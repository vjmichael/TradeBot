"""TB-07A: internal, PAPER-only deterministic intent authorization.

No venue adapter, credentials, HTTP routes, model callbacks or RUN control.
Trusted policy/account/market publishers are integration boundaries, NOT
proposal fields. Until those publishers and the execution adapter qualify,
the desk's existing persisted pause remains binding.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import time
from uuid import uuid4

MAX_CENTS = 100_000_000_000
MAX_TIME = 4_102_444_800
PRICE_SCALE = 100_000_000  # USD quote per one whole base unit, eight decimal places
MAX_PRICE = 10**15  # bound; these are not order quantities
PAIRS = ("BTC/USD", "ETH/USD", "SOL/USD")
HELD = ("AUTHORIZED", "CLAIMED", "UNKNOWN", "FILLED_HELD")
UNCERTAIN = ("CLAIMED", "UNKNOWN", "FILLED_HELD")


class GateRefusal(ValueError):
    pass


def integer(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise GateRefusal("invalid_integer")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", value):
        raise GateRefusal("invalid_identifier")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Policy:
    version: str
    owner: str
    epoch: int
    expires: int
    strategy_revision: str
    max_order_cents: int
    max_total_exposure_cents: int
    max_pair_exposure_cents: int
    max_positions: int
    max_loss_per_trade_cents: int
    daily_loss_limit_cents: int
    weekly_loss_limit_cents: int
    drawdown_limit_cents: int
    market_max_age_sec: int
    account_max_age_sec: int
    max_spread_bps: int
    max_volatility_bps: int
    min_depth_cents: int
    cost_bps: int
    min_edge_bps: int

    def check(self, now):
        for value in (self.version, self.owner, self.strategy_revision):
            identifier(value)
        integer(self.epoch, 1, MAX_TIME)
        integer(self.expires, now + 1, MAX_TIME)
        for value in (self.max_order_cents, self.max_total_exposure_cents,
                      self.max_pair_exposure_cents, self.max_loss_per_trade_cents,
                      self.daily_loss_limit_cents, self.weekly_loss_limit_cents,
                      self.drawdown_limit_cents, self.min_depth_cents):
            integer(value, 1, MAX_CENTS)
        integer(self.max_positions, 1, 1000)
        integer(self.market_max_age_sec, 1, 3600)
        integer(self.account_max_age_sec, 1, 300)
        for value in (self.max_spread_bps, self.max_volatility_bps,
                      self.cost_bps, self.min_edge_bps):
            integer(value, 1, 9999)
        if not self.max_order_cents <= self.max_pair_exposure_cents <= self.max_total_exposure_cents:
            raise GateRefusal("inconsistent_policy")


@dataclass(frozen=True)
class RiskState:
    receipt: str
    observed: int
    settled_cash_cents: int  # includes protected cash; never use NAV
    protected_cash_cents: int
    daily_loss_cents: int
    weekly_loss_cents: int
    drawdown_cents: int
    open_risk_cents: int  # trusted remaining downside of open positions
    # Reconciled position cost exposure and remaining active cash per instance.
    # This is a trusted accounting contract, not a substitute for TB-FIN-01B.
    active_by_instance: dict
    exposure_by_instance: dict
    reconciled: bool
    protection_ok: bool

    def check(self, now):
        identifier(self.receipt)
        integer(self.observed, 1, now)
        for value in (self.settled_cash_cents, self.protected_cash_cents,
                      self.daily_loss_cents, self.weekly_loss_cents, self.drawdown_cents, self.open_risk_cents):
            integer(value, 0, MAX_CENTS)
        if self.protected_cash_cents > self.settled_cash_cents:
            raise GateRefusal("unreconciled_reserve")
        for mapping in (self.active_by_instance, self.exposure_by_instance):
            if type(mapping) is not dict or len(mapping) > 1000:
                raise GateRefusal("invalid_account_scope")
            for key, value in mapping.items():
                identifier(key)
                integer(value, 0, MAX_CENTS)
        if type(self.reconciled) is not bool or type(self.protection_ok) is not bool:
            raise GateRefusal("invalid_account_flags")
        if self.open_risk_cents and not any(self.exposure_by_instance.values()):
            raise GateRefusal("unreconciled_open_risk")
        if sum(self.active_by_instance.values()) > self.settled_cash_cents - self.protected_cash_cents:
            raise GateRefusal("unreconciled_active_cash")


@dataclass(frozen=True)
class MarketEvidence:
    receipt: str
    pair: str
    observed: int
    available: int
    source_digest: str
    strategy_revision: str
    entry: int
    stop: int
    target: int
    spread_bps: int
    volatility_bps: int
    depth_cents: int
    min_notional_cents: int
    estimated_cost_bps: int  # published independent cost estimate
    gross_edge_bps: int  # published prevalidated strategy estimate, not confidence
    continuous: bool
    operational: bool
    trigger: bool
    blackout: bool

    def check(self, now):
        identifier(self.receipt)
        identifier(self.strategy_revision)
        if self.pair not in PAIRS or not isinstance(self.source_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.source_digest):
            raise GateRefusal("invalid_market_identity")
        integer(self.observed, 1, now)
        integer(self.available, self.observed, now)
        for value in (self.entry, self.stop, self.target):
            integer(value, 1, MAX_PRICE)
        if not self.stop < self.entry < self.target:
            raise GateRefusal("invalid_levels")
        for value in (self.spread_bps, self.volatility_bps):
            integer(value, 0, 1000000)
        integer(self.estimated_cost_bps, 1, 9999)
        integer(self.gross_edge_bps, -1000000, 1000000)
        if self.estimated_cost_bps < self.spread_bps:
            raise GateRefusal("inconsistent_cost_estimate")
        for value in (self.depth_cents, self.min_notional_cents):
            integer(value, 1, MAX_CENTS)
        for flag in (self.continuous, self.operational, self.trigger, self.blackout):
            if type(flag) is not bool:
                raise GateRefusal("invalid_market_flags")


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    instance_id: str
    pair: str
    evidence_id: str
    max_notional_cents: int  # advisory ceiling, never an executable stake
    expires: int

    def check(self):
        for value in (self.proposal_id, self.instance_id, self.evidence_id):
            identifier(value)
        if self.pair not in PAIRS:
            raise GateRefusal("unsupported_pair")
        integer(self.max_notional_cents, 1, MAX_CENTS)
        integer(self.expires, 1, MAX_TIME)


def initialize(db):
    # Called inside Store's existing BEGIN IMMEDIATE transaction; no executescript.
    db.execute("CREATE TABLE IF NOT EXISTS gate_clock (id INTEGER PRIMARY KEY CHECK(id=1), high_water INTEGER NOT NULL)")
    db.execute("INSERT OR IGNORE INTO gate_clock VALUES(1,0)")
    db.execute("""CREATE TABLE IF NOT EXISTS gate_inputs(
        kind TEXT NOT NULL, scope TEXT NOT NULL, receipt TEXT NOT NULL,
        body TEXT NOT NULL, hash TEXT NOT NULL, observed INTEGER NOT NULL,
        PRIMARY KEY(kind,scope,receipt))""")
    db.execute("""CREATE TABLE IF NOT EXISTS gate_current(
        kind TEXT NOT NULL, scope TEXT NOT NULL, receipt TEXT NOT NULL,
        PRIMARY KEY(kind,scope))""")
    db.execute("""CREATE TABLE IF NOT EXISTS gate_decisions(
        proposal_id TEXT PRIMARY KEY, proposal_hash TEXT NOT NULL, proposal TEXT NOT NULL,
        intent_id TEXT UNIQUE, reason TEXT NOT NULL, created INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS gate_intents(
        id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE,
        instance_id TEXT NOT NULL, pair TEXT NOT NULL, evidence_id TEXT NOT NULL,
        policy_hash TEXT NOT NULL, account_hash TEXT NOT NULL, evidence_hash TEXT NOT NULL,
        owner TEXT NOT NULL, epoch INTEGER NOT NULL, notional INTEGER NOT NULL CHECK(notional>0),
        reserved INTEGER NOT NULL CHECK(reserved>=notional), entry INTEGER NOT NULL,
        stop INTEGER NOT NULL, target INTEGER NOT NULL, expires INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('AUTHORIZED','CLAIMED','UNKNOWN','VOID','NO_FILL','FILLED_HELD')),
        claim_token TEXT, resolution_receipt TEXT UNIQUE,
        UNIQUE(instance_id,evidence_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS gate_events(
        seq INTEGER PRIMARY KEY, intent_id TEXT, action TEXT NOT NULL, at INTEGER NOT NULL,
        detail TEXT NOT NULL)""")


def event(db, action, now, ident=None, detail=None):
    db.execute("INSERT INTO gate_events(intent_id,action,at,detail) VALUES(?,?,?,?)",
               (ident, action, now, canonical(detail or {})))


def restrictive_transition(db, reason, now):
    """Called on desk boot and pause; no external-order inference or release."""
    for row in db.execute("SELECT id,state FROM gate_intents WHERE state IN ('AUTHORIZED','CLAIMED')").fetchall():
        state = "VOID" if row[1] == "AUTHORIZED" else "UNKNOWN"
        db.execute("UPDATE gate_intents SET state=? WHERE id=?", (state, row[0]))
        event(db, state, now, row[0], {"reason": reason})


class Gate:
    """Internal trusted runtime object; do not expose publisher/claim methods to AI."""

    def __init__(self, transaction, *, clock=time.time):
        self.transaction = transaction
        self.clock = clock

    def _now(self, db):
        now = integer(int(self.clock()), 1, MAX_TIME)
        high = db.execute("SELECT high_water FROM gate_clock WHERE id=1").fetchone()[0]
        if now < high:
            raise GateRefusal("clock_rollback")
        db.execute("UPDATE gate_clock SET high_water=? WHERE id=1", (now,))
        return now

    def _current(self, db, kind, scope, cls):
        row = db.execute("""SELECT i.body,i.hash FROM gate_current c JOIN gate_inputs i
            ON i.kind=c.kind AND i.scope=c.scope AND i.receipt=c.receipt
            WHERE c.kind=? AND c.scope=?""", (kind, scope)).fetchone()
        if row is None:
            raise GateRefusal("missing_" + kind)
        body = json.loads(row[0])
        if digest(body) != row[1]:
            raise GateRefusal("input_integrity")
        return cls(**body), row[1]

    def _publish(self, db, kind, scope, receipt, body, observed, now):
        body_hash = digest(body)
        old = db.execute("SELECT hash FROM gate_inputs WHERE kind=? AND scope=? AND receipt=?",
                         (kind, scope, receipt)).fetchone()
        if old:
            if old[0] != body_hash:
                raise GateRefusal("receipt_conflict")
            return body_hash  # replay never makes an old receipt current
        current = db.execute("""SELECT i.observed FROM gate_current c JOIN gate_inputs i
            ON i.kind=c.kind AND i.scope=c.scope AND i.receipt=c.receipt
            WHERE c.kind=? AND c.scope=?""", (kind, scope)).fetchone()
        if current and observed < current[0]:
            raise GateRefusal("out_of_order_receipt")
        db.execute("INSERT INTO gate_inputs VALUES(?,?,?,?,?,?)",
                   (kind, scope, receipt, canonical(body), body_hash, observed))
        db.execute("""INSERT INTO gate_current VALUES(?,?,?) ON CONFLICT(kind,scope)
            DO UPDATE SET receipt=excluded.receipt""", (kind, scope, receipt))
        event(db, "input_published", now, detail={"kind":kind,"scope":scope,"receipt":receipt,"hash":body_hash})
        return body_hash

    def trusted_policy(self, policy):
        if type(policy) is not Policy:
            raise GateRefusal("invalid_policy")
        with self.transaction() as db:
            now = self._now(db)
            policy.check(now)
            try:
                old, _ = self._current(db, "policy", "desk", Policy)
            except GateRefusal as exc:
                if str(exc) != "missing_policy":
                    raise
            else:
                if policy.version == old.version and policy == old:
                    return digest(asdict(policy))
                if policy.epoch <= old.epoch:
                    raise GateRefusal("ownership_epoch_not_advanced")
            # A new policy fences the old approval, but never discards ambiguity.
            result = self._publish(db,"policy","desk",policy.version,asdict(policy),now,now)
            restrictive_transition(db,"policy_changed",now)
            return result

    def trusted_account(self, account):
        if type(account) is not RiskState:
            raise GateRefusal("invalid_account")
        with self.transaction() as db:
            now = self._now(db)
            account.check(now)
            # Until balanced fill reconciliation exists, never let a new snapshot
            # erase a pending debit or unknown outcome.
            if db.execute("SELECT 1 FROM gate_intents WHERE state IN ('CLAIMED','UNKNOWN','FILLED_HELD') LIMIT 1").fetchone():
                raise GateRefusal("unresolved_execution")
            result = self._publish(db,"account","desk",account.receipt,asdict(account),account.observed,now)
            return result

    def trusted_market(self, evidence):
        if type(evidence) is not MarketEvidence:
            raise GateRefusal("invalid_market")
        with self.transaction() as db:
            now = self._now(db)
            evidence.check(now)
            return self._publish(db,"market",evidence.pair,evidence.receipt,asdict(evidence),evidence.available,now)

    def _expire(self, db, now):
        for row in db.execute("SELECT id FROM gate_intents WHERE state='AUTHORIZED' AND expires<=?", (now,)).fetchall():
            db.execute("UPDATE gate_intents SET state='VOID' WHERE id=?", (row[0],))
            event(db,"VOID",now,row[0],{"reason":"expired"})

    def _evaluate(self, db, proposal, now, exclude=None):
        if proposal.expires <= now:
            raise GateRefusal("proposal_expired")
        desk = db.execute("SELECT paused,human_pause,virtual_cap_cents FROM desk WHERE id=1").fetchone()
        if desk is None or desk[0] or desk[1]:
            raise GateRefusal("desk_paused")
        instances = {r["id"]:dict(r) for r in db.execute("SELECT id,pair,virtual_cents,mode,strategy_family,state FROM instances")}
        inst = instances.get(proposal.instance_id)
        if not inst or inst["pair"] != proposal.pair or inst["mode"] != "PAPER":
            raise GateRefusal("instance_scope")
        # This first group supports numerical paper only. Research and actual
        # instance activation are gated separately. No LLM is consulted.
        if inst["strategy_family"] != "numerical-baseline" or inst["state"] != "RUN":
            raise GateRefusal("strategy_or_instance_not_running")
        policy, ph = self._current(db,"policy","desk",Policy)
        if policy.expires <= now:
            raise GateRefusal("policy_expired")
        policy.check(now)
        account, ah = self._current(db,"account","desk",RiskState)
        account.check(now)
        market, mh = self._current(db,"market",proposal.pair,MarketEvidence)
        market.check(now)
        if not account.reconciled or not account.protection_ok or now-account.observed > policy.account_max_age_sec:
            raise GateRefusal("account_stale_or_unreconciled")
        if (market.receipt != proposal.evidence_id or market.pair != proposal.pair
                or now-market.observed > policy.market_max_age_sec
                or market.strategy_revision != policy.strategy_revision):
            raise GateRefusal("market_stale_or_changed")
        if not market.continuous or not market.operational or not market.trigger or market.blackout:
            raise GateRefusal("market_or_trigger_block")
        if market.spread_bps > policy.max_spread_bps or market.volatility_bps > policy.max_volatility_bps or market.depth_cents < policy.min_depth_cents:
            raise GateRefusal("market_quality")
        if market.estimated_cost_bps > policy.cost_bps:
            raise GateRefusal("cost_budget_exceeded")
        if market.gross_edge_bps-policy.cost_bps < policy.min_edge_bps:
            raise GateRefusal("insufficient_validated_edge")
        if account.daily_loss_cents >= policy.daily_loss_limit_cents or account.weekly_loss_cents >= policy.weekly_loss_limit_cents or account.drawdown_cents >= policy.drawdown_limit_cents:
            raise GateRefusal("loss_lock")
        if set(account.active_by_instance) != set(instances) or set(account.exposure_by_instance) != set(instances):
            raise GateRefusal("account_scope_mismatch")
        if sum(account.active_by_instance.values())+sum(account.exposure_by_instance.values()) > desk[2]:
            raise GateRefusal("desk_accounting_cap")
        for k,i in instances.items():
            if account.active_by_instance[k]+account.exposure_by_instance[k] > i["virtual_cents"]:
                raise GateRefusal("instance_accounting_cap")
        held = db.execute("""SELECT * FROM gate_intents WHERE state IN
            ('AUTHORIZED','CLAIMED','UNKNOWN','FILLED_HELD') AND (? IS NULL OR id<>?)""", (exclude,exclude)).fetchall()
        if any(r["state"] in UNCERTAIN for r in held):
            raise GateRefusal("unresolved_execution")
        if any(r["instance_id"] == proposal.instance_id for r in held) or account.exposure_by_instance[proposal.instance_id]:
            raise GateRefusal("existing_instance_exposure")
        if db.execute("""SELECT 1 FROM gate_intents WHERE instance_id=? AND evidence_id=?
            AND (? IS NULL OR id<>?)""", (proposal.instance_id,proposal.evidence_id,exclude,exclude)).fetchone():
            raise GateRefusal("signal_already_authorized")
        exposures = account.exposure_by_instance
        pair_exposure = sum(v for k,v in exposures.items() if instances[k]["pair"] == proposal.pair)
        reserved = sum(r["reserved"] for r in held)
        pending_notional = sum(r["notional"] for r in held)
        pair_pending = sum(r["notional"] for r in held if r["pair"] == proposal.pair)
        total_exposure = sum(exposures.values())+pending_notional
        if sum(v>0 for v in exposures.values())+len(held) >= policy.max_positions:
            raise GateRefusal("position_limit")
        free = min(account.settled_cash_cents-account.protected_cash_cents-reserved,
                   account.active_by_instance[proposal.instance_id],
                   desk[2]-sum(exposures.values())-reserved)
        # Integer arithmetic: round loss UP and allowed stake DOWN. Cost buffer
        # covers configured round-trip costs; no floating-point financial sizing.
        risk_bps = ((market.entry-market.stop)*10000+market.entry-1)//market.entry+policy.cost_bps
        pending_risk = sum((r["notional"] * (((r["entry"]-r["stop"])*10000+r["entry"]-1)//r["entry"]+policy.cost_bps)+9999)//10000 for r in held)
        risk_room = min(policy.max_loss_per_trade_cents,
                        policy.daily_loss_limit_cents-account.daily_loss_cents-account.open_risk_cents-pending_risk,
                        policy.weekly_loss_limit_cents-account.weekly_loss_cents-account.open_risk_cents-pending_risk,
                        policy.drawdown_limit_cents-account.drawdown_cents-account.open_risk_cents-pending_risk)
        stake = min(proposal.max_notional_cents, policy.max_order_cents, market.depth_cents,
                    policy.max_total_exposure_cents-total_exposure,
                    policy.max_pair_exposure_cents-pair_exposure-pair_pending,
                    max(0,free)*10000//(10000+policy.cost_bps),
                    max(0,risk_room)*10000//risk_bps)
        if stake < market.min_notional_cents or stake <= 0:
            raise GateRefusal("insufficient_risk_or_active_cash")
        reserve = stake+(stake*policy.cost_bps+9999)//10000
        expiry = min(proposal.expires,policy.expires,market.observed+policy.market_max_age_sec+1,
                     account.observed+policy.account_max_age_sec+1)
        return policy, market, ph, ah, mh, stake, reserve, expiry

    def admit(self, proposal):
        if type(proposal) is not Proposal:
            raise GateRefusal("invalid_proposal")
        proposal.check()
        body = asdict(proposal)
        fingerprint = digest(body)
        with self.transaction() as db:
            now = self._now(db)
            self._expire(db,now)
            old = db.execute("SELECT * FROM gate_decisions WHERE proposal_id=?", (proposal.proposal_id,)).fetchone()
            if old:
                if old["proposal_hash"] != fingerprint:
                    raise GateRefusal("proposal_id_conflict")
                # Return current state, not an old authorization usable again.
                return self._result(db,old)
            ident = None
            try:
                p,m,ph,ah,mh,stake,reserve,expiry = self._evaluate(db,proposal,now)
            except GateRefusal as exc:
                reason = str(exc)
            else:
                ident = uuid4().hex
                reason = "paper_authorized"
                db.execute("""INSERT INTO gate_intents(id,proposal_id,instance_id,pair,evidence_id,
                    policy_hash,account_hash,evidence_hash,owner,epoch,notional,reserved,entry,stop,target,expires,state)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'AUTHORIZED')""",
                    (ident,proposal.proposal_id,proposal.instance_id,proposal.pair,proposal.evidence_id,
                     ph,ah,mh,p.owner,p.epoch,stake,reserve,m.entry,m.stop,m.target,expiry))
            db.execute("INSERT INTO gate_decisions VALUES(?,?,?,?,?,?)",
                       (proposal.proposal_id,fingerprint,canonical(body),ident,reason,now))
            event(db,reason,now,ident,{"proposal_id":proposal.proposal_id})
            return self._result(db,db.execute("SELECT * FROM gate_decisions WHERE proposal_id=?",(proposal.proposal_id,)).fetchone())

    def _result(self, db, decision):
        result = {"proposal_id":decision["proposal_id"],"reason":decision["reason"],"mode":"PAPER"}
        if decision["intent_id"] is None:
            return result | {"state":"REJECT"}
        row = db.execute("SELECT * FROM gate_intents WHERE id=?",(decision["intent_id"],)).fetchone()
        return result | {"intent_id":row["id"],"state":row["state"],
                         "notional_cents":row["notional"],"reserved_cents":row["reserved"],
                         "expires":row["expires"]}

    def claim(self, ident, owner, epoch):
        """Persist claim before any future adapter action; one winner only."""
        identifier(ident)
        identifier(owner)
        integer(epoch,1,MAX_TIME)
        with self.transaction() as db:
            now = self._now(db)
            self._expire(db,now)
            row = db.execute("SELECT * FROM gate_intents WHERE id=?",(ident,)).fetchone()
            if not row or row["state"] != "AUTHORIZED":
                return {"claimed":False,"reason":"not_authorized"}
            if row["owner"] != owner or row["epoch"] != epoch:
                return {"claimed":False,"reason":"ownership_mismatch"}
            original = db.execute("SELECT proposal,proposal_hash FROM gate_decisions WHERE proposal_id=?",(row["proposal_id"],)).fetchone()
            try:
                body = json.loads(original[0])
                if digest(body) != original[1]:
                    raise GateRefusal("proposal_integrity")
                p,m,ph,ah,mh,stake,reserve,expiry = self._evaluate(db,Proposal(**body),now,exclude=ident)
                if (ph,ah,mh,p.owner,p.epoch,stake,reserve,m.entry,m.stop,m.target,expiry) != (
                    row["policy_hash"],row["account_hash"],row["evidence_hash"],owner,epoch,
                    row["notional"],row["reserved"],row["entry"],row["stop"],row["target"],row["expires"]):
                    raise GateRefusal("authorization_changed")
            except GateRefusal as exc:
                db.execute("UPDATE gate_intents SET state='VOID' WHERE id=?",(ident,))
                event(db,"VOID",now,ident,{"reason":str(exc)})
                return {"claimed":False,"reason":str(exc)}
            token = uuid4().hex
            db.execute("UPDATE gate_intents SET state='CLAIMED',claim_token=? WHERE id=?",(token,ident))
            event(db,"CLAIMED",now,ident)
            return {"claimed":True,"mode":"PAPER","claim_token":token,"intent_id":ident,
                    "notional_cents":stake,"entry":m.entry,"stop":m.stop,"target":m.target,
                    "price_scale":PRICE_SCALE}

    def trusted_resolution(self, ident, token, receipt, outcome):
        """Future trusted reconciler only. NO_FILL requires verified absence.

        A filled/partial outcome stays encumbered; TB-FIN-01B must reconcile
        actual lots/fees/cash before any new entry. No blind resubmission.
        """
        for value in (ident,token,receipt):
            identifier(value)
        if outcome not in ("UNKNOWN","NO_FILL","FILLED_HELD"):
            raise GateRefusal("invalid_resolution")
        with self.transaction() as db:
            now = self._now(db)
            row = db.execute("SELECT * FROM gate_intents WHERE id=?",(ident,)).fetchone()
            if not row or row["claim_token"] != token:
                raise GateRefusal("claim_mismatch")
            if row["resolution_receipt"] == receipt and row["state"] == outcome:
                return outcome
            if row["state"] not in ("CLAIMED","UNKNOWN"):
                raise GateRefusal("terminal_resolution")
            # Record immutable receipt identities, including previous unknowns.
            if db.execute("SELECT 1 FROM gate_events WHERE action='resolution' AND json_extract(detail,'$.receipt')=?",(receipt,)).fetchone():
                raise GateRefusal("resolution_receipt_reused")
            db.execute("UPDATE gate_intents SET state=?,resolution_receipt=? WHERE id=?",(outcome,receipt,ident))
            event(db,"resolution",now,ident,{"receipt":receipt,"outcome":outcome})
            return outcome
