"""TB-04F3: versioned public Kraken Spot directory and research selection.

No credentials, private endpoints, execution admission or collector activation.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

URL = "https://api.kraken.com/0/public/AssetPairs"
DEFAULTS = ("BTC/USD", "ETH/USD", "SOL/USD")
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
MAX_SELECTED = 32
MAX_PINNED = 256
FRESH_SECONDS = 86400
REFRESH_SECONDS = 60
PAGE = 50
TOKEN = re.compile(r"[A-Z0-9]{1,32}", re.ASCII)
PAIR = re.compile(r"[A-Z0-9]{1,24}/[A-Z0-9]{1,24}", re.ASCII)


class DirectoryUnavailable(RuntimeError):
    pass


def positive_decimal(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid minimum")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        raise ValueError("invalid minimum")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise ValueError("invalid minimum")
    return value


def parse_directory(payload):
    if (not isinstance(payload, dict) or payload.get("error") != []
            or not isinstance(payload.get("result"), dict)
            or not 0 < len(payload["result"]) <= MAX_ROWS):
        raise DirectoryUnavailable("Invalid Kraken market directory")
    grouped = {}
    for key, row in payload["result"].items():
        if not isinstance(row, dict):
            continue
        ws = row.get("wsname")
        if not isinstance(ws, str) or not PAIR.fullmatch(ws):
            continue
        base, quote = ws.split("/")
        if quote != "USD" or base in ("USD", "ZUSD"):
            continue
        pair = ("BTC" if base == "XBT" else base) + "/USD"
        alt = row.get("altname")
        # Retain all possible canonical collisions, even invalid rows.
        grouped.setdefault(pair, []).append((key, row, alt, ws))
    markets = []
    for pair, entries in sorted(grouped.items()):
        if len(entries) != 1:
            continue
        key, row, alt, ws = entries[0]
        if (not all(isinstance(x, str) and TOKEN.fullmatch(x)
                    for x in (key, alt, row.get("base"), row.get("quote")))
                or row.get("aclass_base") != "currency"
                or row.get("aclass_quote") != "currency"
                or row.get("quote") not in ("USD", "ZUSD")):
            continue
        precision = [row.get(k) for k in ("pair_decimals", "lot_decimals", "cost_decimals")]
        if any(type(x) is not int or not 0 <= x <= 18 for x in precision):
            continue
        try:
            ordermin = positive_decimal(row.get("ordermin"))
            costmin = positive_decimal(row.get("costmin"))
            tick = positive_decimal(row.get("tick_size"))
        except (ValueError, InvalidOperation):
            continue
        status = row.get("status")
        if status not in ("online", "cancel_only", "post_only", "limit_only", "reduce_only", "maintenance", "delisted"):
            status = "unknown"
        markets.append(dict(pair=pair, rest_key=key, rest_symbol=alt,
                            ws_legacy=ws, ws_v2=pair, base_id=row["base"],
                            quote_id=row["quote"], status=status,
                            price_decimals=precision[0], quantity_decimals=precision[1],
                            cost_decimals=precision[2], ordermin=ordermin,
                            costmin=costmin, tick_size=tick,
                            eligible_for_research=status == "online"))
    if not markets or not set(DEFAULTS).issubset({m["pair"] for m in markets}):
        raise DirectoryUnavailable("Incomplete Kraken market directory")
    return markets


def fetch_directory():
    try:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", URL, headers={"Accept": "application/json",
                               "User-Agent": "Maj-Vaj/0.1 public directory"}) as response:
                if response.status_code != 200:
                    raise DirectoryUnavailable("Kraken market directory unavailable")
                body = bytearray()
                for chunk in response.iter_bytes(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise DirectoryUnavailable("Kraken market directory too large")
        return parse_directory(json.loads(body))
    except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
        raise DirectoryUnavailable("Kraken market directory unavailable") from None


class MarketDirectory:
    def __init__(self, directory):
        root = Path(directory) / "market-directory"
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path = root / "directory.sqlite3"
        if root.is_symlink() or self.path.is_symlink() or (os.name == "posix" and root.stat().st_mode & 0o077):
            raise DirectoryUnavailable("Unsafe market directory storage")
        if not self.path.exists():
            catalog=Path(directory)/"market-memory"/"catalog.sqlite3"
            if catalog.is_file():
                from contextlib import closing
                with closing(sqlite3.connect(catalog.resolve().as_uri()+"?mode=ro",uri=True)) as old:
                    for table in ("sources","candles","collector","exports","stream_health","quality_books","quality_attempts","gap_jobs"):
                        if old.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                            if any(pair not in DEFAULTS for (pair,) in old.execute("SELECT DISTINCT pair FROM "+table)):
                                raise DirectoryUnavailable("Market identity registry missing for existing history")
        with self.tx() as db:
            db.execute("CREATE TABLE IF NOT EXISTS snapshots (version INTEGER PRIMARY KEY AUTOINCREMENT, observed INTEGER NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS selections (pair TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), version INTEGER, changed INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS pinned_identity (pair TEXT PRIMARY KEY, identity TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS refresh_state (id INTEGER PRIMARY KEY CHECK(id=1), last_attempt INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO refresh_state VALUES(1,0)")
            db.executemany("INSERT OR IGNORE INTO selections VALUES(?,1,NULL,0)", [(p,) for p in DEFAULTS])
        os.chmod(self.path, 0o600)

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=10)
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

    @staticmethod
    def _latest(db):
        row = db.execute("SELECT * FROM snapshots ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            return None, []
        if hashlib.sha256(row["body"].encode()).hexdigest() != row["digest"]:
            raise DirectoryUnavailable("Stored market directory failed verification")
        return row, json.loads(row["body"])

    @staticmethod
    def _identity(market):
        return json.dumps([market[k] for k in ("rest_key","rest_symbol","ws_legacy","ws_v2","base_id","quote_id")], separators=(",", ":"))

    @staticmethod
    def _fresh(row, now):
        return row is not None and 0 <= now - row["observed"] < FRESH_SECONDS

    def refresh(self, *, fetch=fetch_directory, now=None):
        now = int(time.time()) if now is None else now
        with self.tx() as db:
            last = db.execute("SELECT last_attempt FROM refresh_state WHERE id=1").fetchone()[0]
            if last and now - last < REFRESH_SECONDS:
                raise DirectoryUnavailable("Please wait before refreshing the market directory")
            db.execute("UPDATE refresh_state SET last_attempt=? WHERE id=1", (now,))
        markets = fetch()
        body = json.dumps(markets, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.tx() as db:
            # A delayed earlier response must not supersede a later refresh.
            if db.execute("SELECT last_attempt FROM refresh_state WHERE id=1").fetchone()[0] != now:
                raise DirectoryUnavailable("Market directory refresh superseded")
            db.execute("INSERT INTO snapshots(observed,digest,body) VALUES(?,?,?)", (now, digest, body))
            db.execute("DELETE FROM snapshots WHERE version NOT IN (SELECT version FROM snapshots ORDER BY version DESC LIMIT 8)")
        return self.browse(now=now)

    def browse(self, query="", offset=0, *, now=None):
        if (not isinstance(query, str) or len(query) > 64
                or type(offset) is not int or not 0 <= offset <= MAX_ROWS):
            raise ValueError("Invalid market search")
        now = int(time.time()) if now is None else now
        with self.tx() as db:
            row, markets = self._latest(db)
            selected = [dict(r) for r in db.execute("SELECT * FROM selections WHERE enabled=1 ORDER BY pair")]
            pins = dict(db.execute("SELECT pair,identity FROM pinned_identity"))
        fresh = self._fresh(row, now)
        lookup = {m["pair"]: m for m in markets}
        for s in selected:
            m = lookup.get(s["pair"])
            s["directory_status"] = m["status"] if m else "unverified"
            from engine import SYMBOLS
            unchanged = bool(m and (s["pair"] not in pins or pins[s["pair"]] == self._identity(m)) and (s["pair"] not in SYMBOLS or (m["rest_symbol"],m["rest_key"])==SYMBOLS[s["pair"]]))
            s["selection_valid"] = bool(fresh and m and m["eligible_for_research"] and unchanged)
            s["mapping_changed"] = bool(m and not unchanged)
            s["preparation"] = "selected_for_data" if s["pair"] not in DEFAULTS else "baseline_only"
        matches = [m for m in markets if query.strip().upper() in
                   (m["pair"] + " " + m["rest_symbol"] + " " + m["rest_key"] + " " + m["ws_legacy"]).upper()]
        return dict(source=URL, state="fresh" if fresh else "stale" if row else "unavailable",
                    version=row["version"] if row else None, observed_at=row["observed"] if row else None,
                    sha256=row["digest"] if row else None, total=len(matches),
                    markets=matches[offset:offset+PAGE], selected=selected,
                    next_offset=offset+PAGE if offset+PAGE < len(matches) else None)

    @staticmethod
    def _retained(db):
        # Baseline identities stay unchanged. A removed selection retains its
        # pinned source identity; historical data is never silently rebound.
        from engine import SYMBOLS
        known = dict(SYMBOLS)
        aliases = {alias: pair for pair, values in known.items()
                   for alias in (*values, pair.replace("/", ""))}
        for pair, packed in db.execute("SELECT pair,identity FROM pinned_identity ORDER BY pair"):
            fields = json.loads(packed)
            if (not PAIR.fullmatch(pair) or not pair.endswith("/USD")
                    or not isinstance(fields, list) or len(fields) != 6
                    or not all(isinstance(fields[i], str) and TOKEN.fullmatch(fields[i]) for i in (0,1,4,5))
                    or fields[3] != pair or fields[5] not in ("USD","ZUSD")
                    or fields[2] not in (pair, "XBT/USD" if pair == "BTC/USD" else pair)):
                raise DirectoryUnavailable("Invalid stored market identity")
            mapping = (fields[1], fields[0])
            if pair in known and known[pair] != mapping:
                raise DirectoryUnavailable("Baseline market mapping mismatch")
            for alias in (*mapping, pair.replace("/", "")):
                if alias in aliases and aliases[alias] != pair:
                    raise DirectoryUnavailable("Ambiguous market mapping")
                aliases[alias] = pair
            known[pair] = mapping
        if len(known) > MAX_PINNED + len(DEFAULTS):
            raise DirectoryUnavailable("Stored market identity limit exceeded")
        return known

    def retained_symbols(self):
        with self.tx() as db:
            return self._retained(db)

    def selected_symbols(self):
        with self.tx() as db:
            known = self._retained(db)
            selected = [r[0] for r in db.execute("SELECT pair FROM selections WHERE enabled=1 ORDER BY pair")]
            if not set(selected) <= known.keys():
                raise DirectoryUnavailable("Selection has no retained identity")
            # Preserve the original baseline ordering for existing receipts.
            return {p:known[p] for p in known if p in selected}

    def active_symbols(self, *, now=None, refresh_stale=False):
        """Acquire data-only scope. Additional markets require current exact identity."""
        now = int(time.time()) if now is None else now
        with self.tx() as db:
            selected = self._retained(db)
            enabled = {r[0] for r in db.execute("SELECT pair FROM selections WHERE enabled=1")}
            if not enabled <= selected.keys():
                raise DirectoryUnavailable("Selection has no retained identity")
            active = {p:selected[p] for p in selected if p in enabled}
            row, markets = self._latest(db)
            pins = dict(db.execute("SELECT pair,identity FROM pinned_identity"))
        extras = enabled - set(DEFAULTS)
        if extras or row is not None:
            if not self._fresh(row, now):
                if refresh_stale:
                    self.refresh(now=now)
                    return self.active_symbols(now=now)
                raise DirectoryUnavailable("Refresh the selected market directory")
            lookup = {m["pair"]:m for m in markets}
            for pair in enabled:
                market = lookup.get(pair)
                if (not market or not market["eligible_for_research"]
                        or (market["rest_symbol"],market["rest_key"]) != active[pair]
                        or (pair in pins and pins[pair] != self._identity(market))
                        or (pair in extras and pair not in pins)):
                    raise DirectoryUnavailable("Selected market unavailable or changed; review selection")
        return active

    def select(self, pair, enabled, version, *, now=None):
        if not isinstance(pair, str) or not PAIR.fullmatch(pair) or type(enabled) is not bool:
            raise ValueError("Invalid market selection")
        now = int(time.time()) if now is None else now
        with self.tx() as db:
            row, markets = self._latest(db)
            if enabled:
                if type(version) is not int or not self._fresh(row, now) or version != row["version"]:
                    raise ValueError("Refresh the market directory before selecting")
                market = next((m for m in markets if m["pair"] == pair), None)
                if not market or not market["eligible_for_research"]:
                    raise ValueError("Market not available for research")
                pin = db.execute("SELECT identity FROM pinned_identity WHERE pair=?", (pair,)).fetchone()
                if pin and pin[0] != self._identity(market):
                    raise ValueError("Market identity changed; manual review required")
                if not pin and db.execute("SELECT count(*) FROM pinned_identity").fetchone()[0]>=MAX_PINNED:
                    raise ValueError("Retained market identity limit reached")
                db.execute("INSERT OR IGNORE INTO pinned_identity VALUES(?,?)", (pair, self._identity(market)))
                self._retained(db)
                count = db.execute("SELECT count(*) FROM selections WHERE enabled=1 AND pair<>?", (pair,)).fetchone()[0]
                if count >= MAX_SELECTED:
                    raise ValueError("Research selection limit reached")
            else:
                if pair in DEFAULTS and (type(version) is not int or not self._fresh(row, now) or version != row["version"]):
                    raise ValueError("Refresh the market directory before removing a baseline market")
                if not db.execute("SELECT 1 FROM selections WHERE pair=? AND enabled=1", (pair,)).fetchone():
                    raise ValueError("Market is not selected")
                if db.execute("SELECT count(*) FROM selections WHERE enabled=1").fetchone()[0]<=1:
                    raise ValueError("Keep at least one research market selected")
            db.execute("INSERT INTO selections VALUES(?,?,?,?) ON CONFLICT(pair) DO UPDATE SET enabled=excluded.enabled,version=excluded.version,changed=excluded.changed",
                       (pair, int(enabled), row["version"] if row else None, now))
        return self.browse(now=now)
