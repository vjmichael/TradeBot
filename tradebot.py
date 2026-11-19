"""Local paper-only foundation; public Kraken market data only."""
import hmac
import os
import sqlite3
from contextlib import closing, contextmanager, asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4
from threading import Lock
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator
from engine import candles, MarketUnavailable
from market_memory import MarketMemory, MemoryUnavailable, session_profile

PAIRS = ("BTC/USD", "ETH/USD", "SOL/USD")
SLOTS = frozenset({"grok", "telegram_desk", "telegram_recovery", "kraken_read_key", "kraken_read_secret"})
MAX_CENTS = 100_000_000_000
DASHBOARD = Path(__file__).resolve().parent / "dashboard"


class InstanceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
    pair: Literal["BTC/USD", "ETH/USD", "SOL/USD"]
    virtual_cents: int = Field(gt=0, le=MAX_CENTS)
    exchange: Literal["kraken"] = "kraken"
    mode: Literal["PAPER"] = "PAPER"
    provider: Literal["grok", "none"] = "grok"
    model: str = Field(default="", max_length=120, pattern=r"^[A-Za-z0-9._:/-]*$")
    strategy_family: Literal["research-assisted", "numerical-baseline"] = "research-assisted"
    history_years: int = Field(default=5, ge=1, le=20)
    monthly_budget_cents: int = Field(default=0, ge=0, le=MAX_CENTS)

    @model_validator(mode="after")
    def no_provider_boundary(self):
        if self.provider == "none" and (self.strategy_family != "numerical-baseline" or self.model or self.monthly_budget_cents):
            raise ValueError("provider-free plans must be numerical-only without a model or research budget")
        return self


class PaperCapital(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    virtual_cap_cents: int = Field(ge=0, le=MAX_CENTS)


class CredentialSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    grok: str | None = Field(default=None, min_length=1, max_length=8192)
    telegram_desk: str | None = Field(default=None, min_length=1, max_length=8192)
    telegram_recovery: str | None = Field(default=None, min_length=1, max_length=8192)
    kraken_read_key: str | None = Field(default=None, min_length=1, max_length=8192)
    kraken_read_secret: str | None = Field(default=None, min_length=1, max_length=8192)


class Store:
    def __init__(self, directory: Path, cap: int):
        if type(cap) is not int or not 0 <= cap <= MAX_CENTS:
            raise ValueError("invalid virtual capital cap")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix" and directory.stat().st_mode & 0o077:
            raise PermissionError("data directory must be private")
        self.path = directory / "desk.sqlite3"
        self.cap = cap

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def boot(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError("unsupported schema version")
            db.execute("CREATE TABLE IF NOT EXISTS desk (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL CHECK(paused=1), human_pause INTEGER NOT NULL CHECK(human_pause IN (0,1)))")
            db.execute("CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, pair TEXT NOT NULL CHECK(pair IN ('BTC/USD','ETH/USD','SOL/USD')), virtual_cents INTEGER NOT NULL CHECK(virtual_cents>0), mode TEXT NOT NULL CHECK(mode='PAPER'), state TEXT NOT NULL CHECK(state='PAUSED'))")
            db.execute("CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY, time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), event TEXT NOT NULL, instance_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS secrets (slot TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)")
            db.execute("INSERT OR IGNORE INTO desk (id,paused,human_pause) VALUES (1,1,0)")
            if version < 2:
                db.execute("ALTER TABLE desk ADD COLUMN virtual_cap_cents INTEGER NOT NULL DEFAULT 0 CHECK(virtual_cap_cents>=0)")
                db.execute("ALTER TABLE desk ADD COLUMN cap_set_by_user INTEGER NOT NULL DEFAULT 0 CHECK(cap_set_by_user IN (0,1))")
                for definition in ("provider TEXT NOT NULL DEFAULT 'grok'", "model TEXT NOT NULL DEFAULT ''", "strategy_family TEXT NOT NULL DEFAULT 'research-assisted'", "history_years INTEGER NOT NULL DEFAULT 5", "monthly_budget_cents INTEGER NOT NULL DEFAULT 0"):
                    db.execute("ALTER TABLE instances ADD COLUMN " + definition)
            db.execute("UPDATE desk SET virtual_cap_cents=? WHERE id=1 AND cap_set_by_user=0", (self.cap,))
            db.execute("UPDATE desk SET paused=1 WHERE id=1")
            db.execute("INSERT INTO journal(event) VALUES ('startup_paused')")
            db.execute("PRAGMA user_version=2")

    def status(self):
        with self.transaction() as db:
            state = dict(db.execute("SELECT paused,human_pause,virtual_cap_cents FROM desk WHERE id=1").fetchone())
            allocated = db.execute("SELECT COALESCE(SUM(virtual_cents),0) FROM instances").fetchone()[0]
            return {"mode": "PAPER", "paused": bool(state["paused"]), "human_pause": bool(state["human_pause"]), "virtual_cap_cents": state["virtual_cap_cents"], "allocated_cents": allocated, "engine_connected": False, "live_available": False}

    def instances(self):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM instances ORDER BY name")]

    def add(self, spec: InstanceSpec):
        with self.transaction() as db:
            allocated = db.execute("SELECT COALESCE(SUM(virtual_cents),0) FROM instances").fetchone()[0]
            cap = db.execute("SELECT virtual_cap_cents FROM desk WHERE id=1").fetchone()[0]
            if allocated + spec.virtual_cents > cap:
                raise ValueError("virtual allocation exceeds configured cap")
            ident = uuid4().hex
            db.execute("INSERT INTO instances (id,name,pair,virtual_cents,mode,state,provider,model,strategy_family,history_years,monthly_budget_cents) VALUES (?,?,?,?, 'PAPER','PAUSED',?,?,?,?,?)", (ident, spec.name, spec.pair, spec.virtual_cents, spec.provider, spec.model, spec.strategy_family, spec.history_years, spec.monthly_budget_cents))
            db.execute("INSERT INTO journal(event,instance_id) VALUES ('instance_created',?)", (ident,))
            return dict(db.execute("SELECT * FROM instances WHERE id=?", (ident,)).fetchone())

    def set_cap(self, cents: int):
        if type(cents) is not int or not 0 <= cents <= MAX_CENTS:
            raise ValueError("invalid paper capital")
        with self.transaction() as db:
            allocated = db.execute("SELECT COALESCE(SUM(virtual_cents),0) FROM instances").fetchone()[0]
            if cents < allocated:
                raise ValueError("capital below existing allocations")
            db.execute("UPDATE desk SET virtual_cap_cents=?,cap_set_by_user=1 WHERE id=1", (cents,))
            db.execute("INSERT INTO journal(event) VALUES ('paper_cap_updated')")
        return self.status()

    def pause(self):
        with self.transaction() as db:
            db.execute("UPDATE desk SET human_pause=1, paused=1 WHERE id=1")
            db.execute("INSERT INTO journal(event) VALUES ('human_pause')")


class Vault:
    """Key custody external to SQLite; only authenticated write-only setup is exposed."""
    def __init__(self, store: Store, key: bytes):
        self.store = store
        self.cipher = Fernet(key)

    def put(self, slot: str, value: str):
        if slot not in SLOTS or not isinstance(value, str) or not 0 < len(value) <= 8192:
            raise ValueError("invalid credential slot or value")
        encrypted = self.cipher.encrypt((slot + "\0" + value).encode())
        with self.store.transaction() as db:
            db.execute("INSERT INTO secrets VALUES (?,?) ON CONFLICT(slot) DO UPDATE SET ciphertext=excluded.ciphertext", (slot, encrypted))

    def setup(self, values: dict[str, str]):
        if not values or not set(values).issubset(SLOTS):
            raise ValueError("invalid credential selection")
        if bool("kraken_read_key" in values) != bool("kraken_read_secret" in values):
            raise ValueError("Kraken key and secret must be provided together")
        if any(not isinstance(v, str) or not 0 < len(v) <= 8192 or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in v) for v in values.values()):
            raise ValueError("invalid credential value")
        sealed = [(slot, self.cipher.encrypt((slot + "\0" + value).encode())) for slot, value in values.items()]
        with self.store.transaction() as db:
            for slot, ciphertext in db.execute("SELECT slot,ciphertext FROM secrets"):
                bound_slot, _ = self.cipher.decrypt(ciphertext).decode().split("\0", 1)
                if bound_slot != slot:
                    raise ValueError("credential vault unavailable")
            db.executemany("INSERT INTO secrets VALUES (?,?) ON CONFLICT(slot) DO UPDATE SET ciphertext=excluded.ciphertext", sealed)
            db.execute("INSERT INTO journal(event) VALUES ('credentials_updated')")

    def presence(self):
        result = {}
        for slot in sorted(SLOTS):
            try:
                self.get(slot)
            except KeyError:
                result[slot] = "not_configured"
            except (InvalidToken, ValueError, UnicodeError):
                result[slot] = "unavailable"
            else:
                result[slot] = "stored_unverified"
        return result

    def get(self, slot: str):
        if slot not in SLOTS:
            raise ValueError("invalid credential slot")
        with self.store.transaction() as db:
            row = db.execute("SELECT ciphertext FROM secrets WHERE slot=?", (slot,)).fetchone()
        if row is None:
            raise KeyError(slot)
        bound_slot, value = self.cipher.decrypt(row[0]).decode().split("\0", 1)
        if bound_slot != slot:
            raise ValueError("credential slot mismatch")
        return value


def dry_run_contract(spec: InstanceSpec):
    """Inert Freqtrade configuration fragment, not a runnable strategy/config."""
    return {"dry_run": True, "trading_mode": "spot", "exchange": {"name": "kraken", "key": "", "secret": "", "pair_whitelist": [spec.pair]}, "dry_run_wallet": spec.virtual_cents / 100, "initial_state": "stopped", "force_entry_enable": False, "telegram": {"enabled": False}, "api_server": {"enabled": False}}


def create_app(directory: Path, token: str, cap: int = 0, *, vault_key: bytes | None = None, dashboard_dir: Path = DASHBOARD):
    if not token or len(token) < 32 or not token.isascii():
        raise ValueError("admin token must be at least 32 ASCII characters")
    store = Store(directory, cap)
    vault = Vault(store, vault_key) if vault_key is not None else None

    @asynccontextmanager
    async def lifespan(app):
        store.boot()
        app.state.market_memory = MarketMemory(directory)
        yield

    market_lock = Lock()
    market_cache = {}
    app = FastAPI(title="TradeBot development dashboard", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def browser_security(request: Request, call_next):
        # Local-only service. Authorization remains required on every sensitive API.
        # Block cross-origin browser requests, including simple POSTs and DNS rebinding.
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if origin is not None and origin != expected:
            response = JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "Unauthorized")

    @app.get("/", include_in_schema=False)
    def dashboard():
        if not (dashboard_dir / "index.html").is_file():
            raise HTTPException(503, "Dashboard unavailable")
        return FileResponse(dashboard_dir / "index.html", media_type="text/html")

    @app.get("/assets/{asset}", include_in_schema=False)
    def dashboard_asset(asset: Literal["app.js", "style.css", "react.js", "react-dom.js"]):
        path = dashboard_dir / ("vendor" if asset in ("react.js", "react-dom.js") else "") / asset
        if not path.is_file():
            raise HTTPException(503, "Dashboard assets not built")
        return FileResponse(path, media_type="text/css" if asset.endswith(".css") else "application/javascript")

    @app.get("/health")
    def health():
        return {"service": "tradebot", "stage": "dashboard", "ready_to_trade": False}

    @app.get("/api/status", dependencies=[Depends(authorize)])
    def status():
        return store.status()

    @app.get("/api/instances", dependencies=[Depends(authorize)])
    def instances():
        return store.instances()

    @app.post("/api/instances", status_code=201, dependencies=[Depends(authorize)])
    def add(spec: InstanceSpec):
        try:
            return store.add(spec)
        except (ValueError, sqlite3.IntegrityError):
            raise HTTPException(409, "Instance conflicts with name or virtual capital limit") from None

    @app.post("/api/pause", dependencies=[Depends(authorize)])
    def pause():
        store.pause()
        return store.status()

    @app.get("/api/setup", dependencies=[Depends(authorize)])
    def setup_status():
        presence = vault.presence() if vault else {slot: "unavailable" for slot in sorted(SLOTS)}
        return {"vault_available": vault is not None and "unavailable" not in presence.values(), "credentials": presence, "connections_verified": False, "deployment_ready": False}

    @app.post("/api/setup/credentials", dependencies=[Depends(authorize)])
    def setup_credentials(spec: CredentialSetup):
        if vault is None:
            raise HTTPException(503, "Secure credential storage unavailable")
        try:
            vault.setup(spec.model_dump(exclude_none=True))
        except InvalidToken:
            raise HTTPException(503, "Secure credential storage unavailable") from None
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Invalid credential selection; Kraken key and secret must be saved together") from None
        return setup_status()

    @app.post("/api/setup/paper-capital", dependencies=[Depends(authorize)])
    def paper_capital(spec: PaperCapital):
        try:
            return store.set_cap(spec.virtual_cap_cents)
        except ValueError:
            raise HTTPException(409, "Paper capital cannot be less than existing allocations") from None

    @app.get("/api/calendar", dependencies=[Depends(authorize)])
    def calendar():
        return {"state": "not_connected", "events": [], "source": None}

    @app.get("/api/markets/{asset}/session-profile", dependencies=[Depends(authorize)])
    def market_profile(asset: Literal["BTC", "ETH", "SOL"], days: Literal["30", "90", "365", "1825"] = "90", zone: Literal["UTC", "America/Phoenix", "America/New_York", "Europe/London", "Asia/Tokyo", "Asia/Hong_Kong"] = "UTC"):
        now = int(time.time())
        end = now // 3600 * 3600
        try:
            profile = session_profile(app.state.market_memory.query(asset + "/USD", 60, end-int(days)*86400, end, now), zone)
        except (MemoryUnavailable, sqlite3.Error, ValueError):
            raise HTTPException(503, "Historical market memory unavailable") from None
        profile["state"] = "observed" if profile["coverage"] else "awaiting_history"
        return profile

    @app.get("/api/markets/{asset}/candles", dependencies=[Depends(authorize)])
    def market(asset: Literal["BTC", "ETH", "SOL"], minutes: Literal["5", "15", "60", "240", "1440"] = "60"):
        # Bounded cache and one in-flight upstream call across the three markets.
        key = (asset, minutes)
        with market_lock:
            cached = market_cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < 30:
                return cached[1]
            try:
                data = candles(asset + "/USD", int(minutes))
            except MarketUnavailable:
                raise HTTPException(503, "Market data unavailable") from None
            market_cache[key] = (time.monotonic(), data)
            return data

    return app


if __name__ == "__main__":
    import uvicorn
    os.umask(0o077)
    key = os.environ.get("TRADEBOT_VAULT_KEY")
    app = create_app(Path(os.environ.get("TRADEBOT_DATA_DIR", "data")), os.environ.get("TRADEBOT_ADMIN_TOKEN", ""), int(os.environ.get("TRADEBOT_VIRTUAL_CAP_CENTS", "0")), vault_key=key.encode() if key else None)
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)
