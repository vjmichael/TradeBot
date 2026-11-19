"""TB-01 local-only foundation. No exchange or order transport exists."""
import hmac
import os
import sqlite3
from contextlib import closing, contextmanager, asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

PAIRS = ("BTC/USD", "ETH/USD", "SOL/USD")
SLOTS = frozenset({"grok", "telegram_desk", "telegram_recovery", "kraken_read_key", "kraken_read_secret"})
MAX_CENTS = 100_000_000_000


class InstanceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
    pair: Literal["BTC/USD", "ETH/USD", "SOL/USD"]
    virtual_cents: int = Field(gt=0, le=MAX_CENTS)
    exchange: Literal["kraken"] = "kraken"
    mode: Literal["PAPER"] = "PAPER"


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
            if version not in (0, 1):
                raise RuntimeError("unsupported schema version")
            db.execute("CREATE TABLE IF NOT EXISTS desk (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL CHECK(paused=1), human_pause INTEGER NOT NULL CHECK(human_pause IN (0,1)))")
            db.execute("CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, pair TEXT NOT NULL CHECK(pair IN ('BTC/USD','ETH/USD','SOL/USD')), virtual_cents INTEGER NOT NULL CHECK(virtual_cents>0), mode TEXT NOT NULL CHECK(mode='PAPER'), state TEXT NOT NULL CHECK(state='PAUSED'))")
            db.execute("CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY, time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), event TEXT NOT NULL, instance_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS secrets (slot TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)")
            db.execute("INSERT OR IGNORE INTO desk VALUES (1,1,0)")
            db.execute("UPDATE desk SET paused=1 WHERE id=1")
            db.execute("INSERT INTO journal(event) VALUES ('startup_paused')")
            db.execute("PRAGMA user_version=1")

    def status(self):
        with self.transaction() as db:
            state = dict(db.execute("SELECT paused,human_pause FROM desk WHERE id=1").fetchone())
            allocated = db.execute("SELECT COALESCE(SUM(virtual_cents),0) FROM instances").fetchone()[0]
            return {"mode": "PAPER", "paused": bool(state["paused"]), "human_pause": bool(state["human_pause"]), "virtual_cap_cents": self.cap, "allocated_cents": allocated, "engine_connected": False, "live_available": False}

    def instances(self):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM instances ORDER BY name")]

    def add(self, spec: InstanceSpec):
        with self.transaction() as db:
            allocated = db.execute("SELECT COALESCE(SUM(virtual_cents),0) FROM instances").fetchone()[0]
            if allocated + spec.virtual_cents > self.cap:
                raise ValueError("virtual allocation exceeds configured cap")
            ident = uuid4().hex
            db.execute("INSERT INTO instances VALUES (?,?,?,?, 'PAPER','PAUSED')", (ident, spec.name, spec.pair, spec.virtual_cents))
            db.execute("INSERT INTO journal(event,instance_id) VALUES ('instance_created',?)", (ident,))
            return {"id": ident, "name": spec.name, "pair": spec.pair, "virtual_cents": spec.virtual_cents, "mode": "PAPER", "state": "PAUSED"}

    def pause(self):
        with self.transaction() as db:
            db.execute("UPDATE desk SET human_pause=1, paused=1 WHERE id=1")
            db.execute("INSERT INTO journal(event) VALUES ('human_pause')")


class Vault:
    """Internal only. Key custody external to SQLite; no HTTP secret endpoints."""
    def __init__(self, store: Store, key: bytes):
        self.store = store
        self.cipher = Fernet(key)

    def put(self, slot: str, value: str):
        if slot not in SLOTS or not isinstance(value, str) or not 0 < len(value) <= 8192:
            raise ValueError("invalid credential slot or value")
        encrypted = self.cipher.encrypt((slot + "\0" + value).encode())
        with self.store.transaction() as db:
            db.execute("INSERT INTO secrets VALUES (?,?) ON CONFLICT(slot) DO UPDATE SET ciphertext=excluded.ciphertext", (slot, encrypted))

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


def create_app(directory: Path, token: str, cap: int = 0):
    if not token or len(token) < 32 or not token.isascii():
        raise ValueError("admin token must be at least 32 ASCII characters")
    store = Store(directory, cap)

    @asynccontextmanager
    async def lifespan(app):
        store.boot()
        yield

    app = FastAPI(title="TradeBot development foundation", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "Unauthorized")

    @app.get("/health")
    def health():
        return {"service": "tradebot", "stage": "foundation", "ready_to_trade": False}

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

    return app


if __name__ == "__main__":
    import uvicorn
    os.umask(0o077)
    app = create_app(Path(os.environ.get("TRADEBOT_DATA_DIR", "data")), os.environ.get("TRADEBOT_ADMIN_TOKEN", ""), int(os.environ.get("TRADEBOT_VIRTUAL_CAP_CENTS", "0")))
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)
