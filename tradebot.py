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
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator
from engine import candles, MarketUnavailable
from market_directory import MarketDirectory, DirectoryUnavailable
from market_memory import MarketMemory, MemoryUnavailable, session_profile
from market_lake import HistoricalLake
from market_quality import QualityMemory, MAX_SAMPLES, SAMPLES_PER_MARKET, MAX_CATALOG_BYTES
from economic_memory import EconomicMemory, CALENDAR, MACRO, FED, MACRO_EXT
from data_lifecycle import coverage
from historical_readiness import historical_report
from data_preparation import Preparation
from data_scheduler import Schedule
from gap_schedule import recovery_progress
from recovery_budget import recovery_budget_status
from storage_telemetry import storage_growth
from storage_management import status as maintenance_status
from market_stream import StreamJournal
from risk_gate import Gate, initialize as initialize_gate, restrictive_transition as restrict_gate
from paper_execution import PaperBridge
import financial_goals
import financial_history
import reserve_preview
from goal_control import entry_guard
from paper_custody import authenticated_projection, workspace_key
from financial_ledger import FinancialLedger, initialize as initialize_financial, start as start_financial, gate_projection, replay as replay_financial, CENT as FIN_CENT

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


class GoalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expenses_cents: int = Field(ge=0, le=MAX_CENTS)
    income_cents: int = Field(ge=0, le=MAX_CENTS)
    savings_cents: int = Field(ge=0, le=MAX_CENTS)
    extra_costs_cents: int = Field(ge=0, le=MAX_CENTS)
    tax_bps: int = Field(ge=0, lt=10000)
    zone: str = Field(min_length=1, max_length=100)
    target_kind: Literal["none","usd","points"]
    target_value: int = Field(ge=0, le=MAX_CENTS)
    pause_on_goal: bool = False
    conservative_bps: int = Field(default=0, ge=0, le=9900)
    expected_version: int = Field(ge=0, le=20000)


class ReserveDraftPolicy(BaseModel):
    # Money inputs use integer cents; the server alone converts ledger units.
    model_config = ConfigDict(extra="forbid", strict=True)
    rate_bps: int = Field(gt=0, le=10000)
    floor_mode: Literal["fixed","percent","combined"]
    floor_fixed: int = Field(ge=0, le=MAX_CENTS)
    floor_bps: int = Field(ge=0, le=10000)
    ceiling: int = Field(gt=0, le=MAX_CENTS)
    cycle_cap: int = Field(gt=0, le=MAX_CENTS)
    lifetime_cap: int = Field(gt=0, le=MAX_CENTS)
    anchor: int = Field(ge=0, lt=86400)
    start: int = Field(gt=0, lt=4102444800)
    end: int = Field(gt=0, lt=4102444800)
    expiry: int = Field(gt=0, lt=4102444800)

    def units(self):
        p=self.model_dump()
        for k in reserve_preview.DOLLARS:p[k]*=FIN_CENT
        return p


class ReserveExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy: ReserveDraftPolicy
    hypothetical_gain_cents: int = Field(ge=0, le=MAX_CENTS)


class ReserveDraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy: ReserveDraftPolicy
    expected_version: int = Field(ge=0, le=20000)


class ReserveDraftReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(gt=0, le=20000)


class DataScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class CleanupSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preview_id: str = Field(pattern=r"^[0-9a-f]{32}$")


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
        keys=lambda i:workspace_key(directory,i)
        projection=authenticated_projection(keys)
        self.gate = Gate(self.transaction, account_projection=projection, entry_guard=entry_guard(keys,projection))
        self.paper_execution = PaperBridge(self.transaction,self.gate)
        self.financial = FinancialLedger(self.transaction)

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
            if version not in (0, 1, 2, 3, 4):
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
            if version >= 3:
                # Never silently reconstruct a missing established intent ledger.
                required = {"gate_clock", "gate_inputs", "gate_current", "gate_decisions", "gate_intents", "gate_events"}
                present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required.issubset(present) or db.execute("SELECT 1 FROM gate_clock WHERE id=1").fetchone() is None:
                    raise RuntimeError("risk ledger incomplete")
            else:
                # Initialize only as an atomic versioned migration; established
                # ledgers are never recreated during ordinary startup.
                initialize_gate(db)
            restrict_gate(db, "startup", int(time.time()))
            if version < 4:
                initialize_financial(db)
            # Always verify the existing journal; never recreate a missing v4 ledger.
            start_financial(db)
            financial_goals.initialize(db)
            reserve_preview.initialize(db)
            db.execute("PRAGMA user_version=4")

    def status(self):
        with self.transaction() as db:
            state = dict(db.execute("SELECT paused,human_pause,virtual_cap_cents FROM desk WHERE id=1").fetchone())
            allocated = db.execute("SELECT COALESCE(SUM(virtual_cents),0) FROM instances").fetchone()[0]
            states,_,_,_ = replay_financial(db)
            allocated += sum(s['reserve']['released']//FIN_CENT for s in states.values())
            return {"mode": "PAPER", "paused": bool(state["paused"]), "human_pause": bool(state["human_pause"]), "virtual_cap_cents": state["virtual_cap_cents"], "allocated_cents": allocated, "engine_connected": False, "live_available": False}

    def instances(self):
        with self.transaction() as db:
            return [dict(r) for r in db.execute("SELECT * FROM instances ORDER BY name")]

    def add(self, spec: InstanceSpec):
        with self.transaction() as db:
            allocated = db.execute("SELECT COALESCE(SUM(virtual_cents),0) FROM instances").fetchone()[0]
            states,_,_,_ = replay_financial(db)
            allocated += sum(s['reserve']['released']//FIN_CENT for s in states.values())
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
            states,_,_,_ = replay_financial(db)
            allocated += sum(s['reserve']['released']//FIN_CENT for s in states.values())
            if cents < allocated:
                raise ValueError("capital below existing allocations")
            db.execute("UPDATE desk SET virtual_cap_cents=?,cap_set_by_user=1 WHERE id=1", (cents,))
            db.execute("INSERT INTO journal(event) VALUES ('paper_cap_updated')")
        return self.status()

    def pause(self):
        with self.transaction() as db:
            db.execute("UPDATE desk SET human_pause=1, paused=1 WHERE id=1")
            restrict_gate(db, "human_pause", int(time.time()))
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
        app.state.market_directory = MarketDirectory(directory)
        app.state.market_memory = MarketMemory(directory)
        app.state.market_lake = HistoricalLake(app.state.market_memory)
        app.state.market_quality = QualityMemory(app.state.market_memory)
        app.state.economic_memory = EconomicMemory(directory)
        app.state.preparation = Preparation(directory)
        app.state.schedule = Schedule(directory)
        try:
            yield
        finally:
            app.state.preparation.close()
            app.state.schedule.close()

    market_lock = Lock()
    market_cache = {}
    app = FastAPI(title="Maj Vaj Trade Bot development dashboard", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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

    class ResearchSelection(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        pair: str = Field(max_length=49)
        enabled: bool
        version: int | None

    @app.get("/api/market-directory", dependencies=[Depends(authorize)])
    def market_directory(query: str = "", offset: int = 0):
        try:
            return app.state.market_directory.browse(query, offset)
        except ValueError:
            raise HTTPException(422, "Invalid market search") from None
        except DirectoryUnavailable:
            raise HTTPException(503, "Stored market directory unavailable") from None

    @app.post("/api/market-directory/refresh", dependencies=[Depends(authorize)])
    def refresh_market_directory():
        try:
            with app.state.schedule.command(),market_lock:
                if app.state.schedule.status()['enabled'] or app.state.preparation.status()['state']=='running':
                    raise DirectoryUnavailable('Stop data collection and disable scheduling before refreshing manually')
                return app.state.market_directory.refresh()
        except DirectoryUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @app.post("/api/market-directory/selection", dependencies=[Depends(authorize)])
    def select_research_market(spec: ResearchSelection):
        try:
            with app.state.schedule.command(),market_lock:
                if app.state.schedule.status()['enabled'] or app.state.preparation.status()['state']=='running':
                    raise ValueError('Stop data collection and disable scheduling before changing markets')
                if not spec.enabled and any(x['pair']==spec.pair for x in store.instances()):
                    raise ValueError('A registered paper instance still references this market')
                return app.state.market_directory.select(spec.pair, spec.enabled, spec.version)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except DirectoryUnavailable:
            raise HTTPException(503, "Stored market directory unavailable") from None

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

    @app.get("/api/financial-goals/{instance}", dependencies=[Depends(authorize)])
    def financial_goal_report(instance: str):
        try:
            return financial_goals.report(store.transaction,instance,key_for=lambda i:workspace_key(directory,i))
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(409, "Financial report unavailable; verified records are required.") from None

    @app.get("/api/reserve-drafts/{instance}", dependencies=[Depends(authorize)])
    def reserve_draft_status(instance: str):
        try:
            return reserve_preview.status(store.transaction,instance)
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(409, "Reinvestment records unavailable.") from None

    @app.post("/api/reserve-drafts/{instance}/preview", dependencies=[Depends(authorize)])
    def reserve_experiment(instance: str, spec: ReserveExperiment):
        try:
            return reserve_preview.preview(store.transaction,instance,spec.policy.units(),spec.hypothetical_gain_cents*FIN_CENT,
                verify=authenticated_projection(lambda i:workspace_key(directory,i)))
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(409, "Check the policy limits, dates and financial records.") from None

    @app.post("/api/reserve-drafts/{instance}/save", dependencies=[Depends(authorize)])
    def reserve_save_draft(instance: str, spec: ReserveDraftSave):
        try:
            return reserve_preview.save(store.transaction,instance,spec.policy.units(),spec.expected_version)
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(409, "Settings could not be saved. Check values and refresh the saved version.") from None

    @app.post("/api/reserve-drafts/{instance}/review", dependencies=[Depends(authorize)])
    def reserve_review_draft(instance: str, spec: ReserveDraftReview):
        try:
            return reserve_preview.review(store.transaction,instance,spec.expected_version)
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(409, "Saved policy review unavailable. Refresh and check the expiry.") from None

    @app.get("/api/financial-history/{instance}", dependencies=[Depends(authorize)])
    def financial_history_report(instance: str, days: int = Query(7, ge=1, le=31), end_day: str | None = Query(None, min_length=10, max_length=10)):
        try:
            return financial_history.report(store.transaction,instance,days,end_day)
        except (ValueError, sqlite3.Error, OSError, OverflowError):
            raise HTTPException(409, "Financial history unavailable. Review the reporting dates or financial records.") from None

    @app.post("/api/financial-goals/{instance}", dependencies=[Depends(authorize)])
    def save_financial_goal(instance: str, spec: GoalSettings):
        p=spec.model_dump()
        version=p.pop("expected_version")
        try:
            return financial_goals.save(store.transaction,instance,p,version)
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(409, "Goal settings could not be saved. Check values, reporting time zone, or refresh the saved version.") from None

    @app.post("/api/instances", status_code=201, dependencies=[Depends(authorize)])
    def add(spec: InstanceSpec):
        try:
            with app.state.schedule.command():
                if spec.pair not in app.state.market_directory.selected_symbols():
                    raise ValueError('Market not selected')
                return store.add(spec)
        except (ValueError, sqlite3.IntegrityError, DirectoryUnavailable):
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
    def calendar(as_of: int | None = None, offset: int = 0):
        if (as_of is not None and not 0<as_of<=int(time.time())) or not 0<=offset<=1500:
            raise HTTPException(422,"Invalid evidence window")
        try:
            result=app.state.economic_memory.calendar(now=as_of,offset=offset)
            return result | {"events":result["rows"]}
        except (MemoryUnavailable, sqlite3.Error, ValueError, OSError):
            raise HTTPException(503, "Economic calendar unavailable") from None

    @app.get("/api/economic/{source}/history", dependencies=[Depends(authorize)])
    def economic_history(source: Literal["statistics","extended","monetary","calendar"], as_of: int | None = None, offset: int = 0):
        try:
            return app.state.economic_memory.history({"statistics":MACRO,"extended":MACRO_EXT,"monetary":FED,"calendar":CALENDAR}[source],as_of=as_of,offset=offset)
        except ValueError:
            raise HTTPException(422,"Invalid historical evidence query") from None
        except (MemoryUnavailable,sqlite3.Error,OSError):
            raise HTTPException(503,"Retained historical evidence unavailable") from None

    # Literal path must precede the dynamic source route; Literal validation
    # occurs after Starlette selects its first matching route.
    @app.get("/api/economic/vintage-coverage", dependencies=[Depends(authorize)])
    def vintage_coverage(as_of: int | None = None,offset: int = 0):
        from vintage_memory import coverage
        try:
            if as_of is not None and as_of>int(time.time()):raise ValueError('future as-of')
            return coverage(app.state.economic_memory,as_of=as_of,offset=offset,detail=True)
        except ValueError:raise HTTPException(422,'Invalid vintage coverage query') from None
        except (MemoryUnavailable,sqlite3.Error,OSError):raise HTTPException(503,'Vintage coverage unavailable') from None

    @app.get("/api/economic/{source}", dependencies=[Depends(authorize)])
    def economic(source: Literal["statistics", "monetary", "extended"], as_of: int | None = None, offset: int = 0):
        now=int(time.time())
        if (as_of is not None and not 0<as_of<=now) or not 0<=offset<=1500:
            raise HTTPException(422, "Invalid evidence window")
        try:
            return app.state.economic_memory.view({"statistics":MACRO,"monetary":FED,"extended":MACRO_EXT}[source],as_of=as_of,offset=offset,limit=100)
        except (MemoryUnavailable, sqlite3.Error, ValueError, OSError):
            raise HTTPException(503,"Economic evidence unavailable") from None

    @app.get("/api/evidence/{evidence_id}", dependencies=[Depends(authorize)])
    def evidence(evidence_id: str, as_of: int | None = None):
        if as_of is not None and not 0<as_of<=int(time.time()):raise HTTPException(422,"Invalid evidence window")
        try:result=app.state.economic_memory.evidence(evidence_id,as_of=as_of)
        except ValueError:raise HTTPException(422,"Invalid evidence ID") from None
        except (MemoryUnavailable, sqlite3.Error,OSError):raise HTTPException(503,"Economic evidence unavailable") from None
        if result is None:raise HTTPException(404,"Evidence unavailable at requested time")
        return result

    @app.get("/api/markets/{asset}/session-profile", dependencies=[Depends(authorize)])
    def market_profile(asset: str, days: Literal["30", "90", "365", "1825"] = "90", zone: Literal["UTC", "America/Phoenix", "America/New_York", "Europe/London", "Asia/Tokyo", "Asia/Hong_Kong"] = "UTC"):
        now = int(time.time())
        end = now // 3600 * 3600
        try:
            if asset+"/USD" not in app.state.market_memory.symbols:raise HTTPException(422,'Unknown market')
            profile = session_profile(app.state.market_lake.query(asset + "/USD", 60, end-int(days)*86400, end, now), zone)
        except (DirectoryUnavailable, MemoryUnavailable, sqlite3.Error, ValueError, OSError):
            raise HTTPException(503, "Historical market memory unavailable") from None
        profile["state"] = "observed" if profile["coverage"] else "awaiting_history"
        return profile

    @app.get("/api/markets/{asset}/quality", dependencies=[Depends(authorize)])
    def market_quality(asset: str, days: Literal["1", "3", "7"] = "3", zone: Literal["UTC", "America/Phoenix", "America/New_York", "Europe/London", "Asia/Tokyo", "Asia/Hong_Kong"] = "UTC", fee_bps: int | None = None):
        now = int(time.time()); end = now // 3600 * 3600
        try:
            return app.state.market_quality.profile(asset+"/USD", end-int(days)*86400, end, now, zone, fee_bps)
        except ValueError:
            raise HTTPException(422, "Invalid market quality settings") from None
        except (DirectoryUnavailable, MemoryUnavailable, sqlite3.Error, OSError):
            raise HTTPException(503, "Market quality data unavailable") from None

    @app.get("/api/markets/{asset}/readiness", dependencies=[Depends(authorize)])
    def market_readiness(asset: str, as_of: int | None = None, offset: int = 0, report_id: str | None = None):
        try:
            pair=asset+"/USD"
            if pair not in app.state.market_directory.selected_symbols():
                raise HTTPException(422,"Market not selected")
            return historical_report(app.state.market_memory,app.state.economic_memory,pair,
                                     as_of=as_of,offset=offset,report_id=report_id)
        except ValueError:
            raise HTTPException(409,"History review changed or invalid; refresh the report") from None
        except (DirectoryUnavailable,MemoryUnavailable,sqlite3.Error,OSError):
            raise HTTPException(503,"Historical readiness evidence unavailable") from None

    @app.get("/api/data-preparation", dependencies=[Depends(authorize)])
    def data_preparation():
        try:
            result = coverage(app.state.market_memory)
            result["job"] = app.state.preparation.status()
            result["streams"] = StreamJournal(app.state.market_memory, app.state.market_directory.selected_symbols()).status()
            result["schedule"] = app.state.schedule.public_status()
            result["recovery"] = recovery_progress(app.state.market_memory)
            result["recovery_budget"] = recovery_budget_status(app.state.market_memory)
            return result
        except (DirectoryUnavailable, MemoryUnavailable, sqlite3.Error, OSError, ValueError):
            raise HTTPException(503, "Data preparation status unavailable") from None

    @app.post("/api/data-preparation/{mode}", dependencies=[Depends(authorize)])
    def start_data_preparation(mode: Literal["prepare", "repair", "collect", "cancel"]):
        try:
            with app.state.schedule.command():
                if app.state.schedule.status()['enabled']:
                    raise MemoryUnavailable('disable scheduled collection before manual commands')
                return app.state.preparation.cancel() if mode == "cancel" else app.state.preparation.start(mode)
        except (MemoryUnavailable, sqlite3.Error, OSError, ValueError):
            raise HTTPException(409, "Data preparation operation unavailable") from None

    @app.post("/api/data-schedule", dependencies=[Depends(authorize)])
    def set_data_schedule(spec: DataScheduleSpec):
        try:
            schedule=app.state.schedule
            schedule.enable(spec.enabled,require_idle=True)
            return schedule.public_status()
        except (MemoryUnavailable, sqlite3.Error, OSError, ValueError):
            raise HTTPException(409, "Scheduled collection setting unavailable; stop manual work first") from None

    def maintenance_start(mode, preview_id=None):
        try:
            with app.state.schedule.command():
                if app.state.schedule.status()['enabled']:raise MemoryUnavailable('disable scheduled collection first')
                if mode=='retire':
                    preview=maintenance_status(directory)['cleanup_preview']
                    if not preview or not preview['attempts'] or preview['id']!=preview_id:raise MemoryUnavailable('review cleanup first')
                if mode=='rotate':
                    preview=maintenance_status(directory)['rotation_preview']
                    if not preview or not preview['eligible'] or preview['id']!=preview_id:raise MemoryUnavailable('review rotation first')
                return app.state.preparation.start(mode,preview_id=preview_id)
        except (MemoryUnavailable, sqlite3.Error, OSError, ValueError):
            raise HTTPException(409, "Storage operation unavailable; stop collection and review current storage first") from None

    @app.post("/api/data-maintenance/retire", dependencies=[Depends(authorize)])
    def apply_cleanup(spec: CleanupSpec):
        return maintenance_start('retire',spec.preview_id)

    @app.post("/api/data-maintenance/rotate", dependencies=[Depends(authorize)])
    def apply_rotation(spec: CleanupSpec):
        return maintenance_start('rotate',spec.preview_id)

    @app.post("/api/data-maintenance/{mode}", dependencies=[Depends(authorize)])
    def start_data_maintenance(mode: Literal["backup", "preview-retention", "consolidate", "preview-rotation", "vintages"]):
        return maintenance_start(mode)

    @app.get("/api/economic/vintages/{series}/{vintage}", dependencies=[Depends(authorize)])
    def historical_vintage(series: str,vintage: str,retrieved_by: int | None = None,replay_at: int | None = None):
        from vintage_memory import VintageMemory
        try:return VintageMemory(directory).view(series,vintage,retrieved_by=retrieved_by,replay_at=replay_at)
        except ValueError:raise HTTPException(422,'Invalid vintage query') from None
        except (MemoryUnavailable,sqlite3.Error,OSError):raise HTTPException(503,'Vintage unavailable') from None

    @app.get("/api/market-storage", dependencies=[Depends(authorize)])
    def market_storage():
        try:
            scope=app.state.market_directory.selected_symbols()
            with app.state.market_memory.tx() as db:
                samples=db.execute('SELECT count(*) FROM quality_attempts').fetchone()[0]
                counts=dict(db.execute('SELECT pair,count(*) FROM quality_attempts GROUP BY pair'))
                catalog_bytes=sum(p.stat().st_size for p in app.state.market_memory.root.glob('catalog.sqlite3*'))
            return app.state.market_lake.status() | {"maintenance":maintenance_status(directory),"sample_receipts":samples,"sample_limit":MAX_SAMPLES,"market_sample_capacity":[{"pair":p,"receipts":counts.get(p,0),"limit":SAMPLES_PER_MARKET} for p in scope],"sample_catalog_bytes":catalog_bytes,"sample_catalog_watermark":MAX_CATALOG_BYTES,"growth":storage_growth(directory,scope)}
        except (DirectoryUnavailable, MemoryUnavailable, sqlite3.Error, OSError, ValueError):
            raise HTTPException(503, "Historical storage status unavailable") from None

    @app.get("/api/markets/{asset}/candles", dependencies=[Depends(authorize)])
    def market(asset: str, minutes: Literal["5", "15", "60", "240", "1440"] = "60"):
        # Reject unknown, disabled or stale markets before using a cached response.
        pair=asset+"/USD"
        with market_lock:
            try:
                if pair not in app.state.market_directory.selected_symbols():
                    raise HTTPException(422,"Market not selected")
                symbols=app.state.market_directory.active_symbols()
            except DirectoryUnavailable:
                raise HTTPException(503,"Market directory requires review") from None
            key=(pair,minutes,symbols[pair])
            cached = market_cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < 30:
                return cached[1]
            try:
                data = candles(pair, int(minutes)) if pair in PAIRS else candles(pair,int(minutes),symbols=symbols)
            except (MarketUnavailable,ValueError):
                raise HTTPException(503, "Market data unavailable") from None
            if len(market_cache)>=160:market_cache.clear()
            market_cache[key] = (time.monotonic(), data)
            return data

    return app


if __name__ == "__main__":
    import uvicorn
    os.umask(0o077)
    key = os.environ.get("TRADEBOT_VAULT_KEY")
    app = create_app(Path(os.environ.get("TRADEBOT_DATA_DIR", "data")), os.environ.get("TRADEBOT_ADMIN_TOKEN", ""), int(os.environ.get("TRADEBOT_VIRTUAL_CAP_CENTS", "0")), vault_key=key.encode() if key else None)
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)
