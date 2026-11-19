# TB-FIN-01B — durable paper accounting foundation

**Status:** candidate. This is a fixed-point, receipt-based accounting foundation, not a complete venue reconciliation or operational trading system. No live capability, order API, reserve release, automatic paper activation or withdrawal.

## Accounting contract

All quote amounts are integer units of USD × 100,000,000; base asset quantities have eight decimal places. The existing Gate operates in integer cents; one cent equals 1,000,000 ledger units. The ledger consumes typed, trusted internal paper receipts only. Actual execution authentication/provenance and independent balance publishers are the next integration gate.

| Event | Debit | Credit |
|---|---|---|
| Explicit virtual principal funding | Active cash | Contributed capital |
| Settled buy fill | FIFO inventory basis, including entry fee | Active cash |
| Sell fill | Unsettled net proceeds | FIFO inventory basis and realized profit (loss debits P/L instead) |
| Verified sale settlement | Returned principal to active cash; positive net gain to protected cash | Unsettled net proceeds |

On every event, all signed balances must sum to zero and inventory equals exact outstanding lot basis; no negative balances or active principal expansion. FIFO partial allocation floors cost at 1e-8 USD units, remaining residual stays with the lot until its final disposal. Gross less exit fee is sale net, so the cost basis and net proceeds include fees once. All gains are protected on settlement, irrespective of previously incurred losses; no automatic loss replenishment. Realized P/L at fill, settled P/L at confirmed settlement and unrealized P/L at fresh mark are distinct. Unrealized P/L is unliquidated mark minus entry basis, not a prediction or liquidation guarantee. Pending sale cash is never available. Protected balance is bookkeeping, not external custody segregation.

Example (simulated): virtual principal $100, buy $98 plus $2 fee, sell $205 less $5 fee. Before settlement, pending $200, realized net $100, settled profit $0. After verified settlement, $100 active and $100 protected. Total fee $7. A subsequent loss cannot consume protected cash. The example is arithmetic, not performance evidence.

## Custody and reconciliation

The same serialized desk transaction owns the append-only financial event journal, journal head and Gate intent ledger. Principal funding uses the existing paper allocation explicitly, once; plans are not auto-funded. Buy fills require a held matching intent, and cumulative gross and expenditures including entry fee cannot exceed its immutable bounds. Partial sales use existing quantity and an independent order receipt. There is no duplication of the Gate reservation, release of ambiguity or claim-to-order adapter. Until the execution adapter and complete accounting projection are accepted, even reconciled balance snapshots report execution_ready false. Unencumbered amount is only a conservative upper bound; the same reserved amount remains held if filled but not fully reconciled by the Gate.

Replay verifies canonical SHA-256 chaining and a separate local head/count, which detects corruption and tail loss, not a malicious writer with authority to rewrite both. Same receipt ID and identical request are idempotent; different payload quarantines the desk without overwriting history. The latest independent fill gross/fee correction is an append-only replacement, re-evaluated from prior state; dependent or settled corrections are refused pending the full review protocol. Reconciliation compares supplied active/protected/pending balances, quantity and reported held reserve against internal ledger state. The matched receipt expires after 60 seconds, is invalidated by any economic mutation and by restart generation, and cannot clear receipt conflicts. It is a balance check only, not proof of venue order/stop completeness.

Desk schema v4 migrates v0–v3 in a single transaction, preserves Gate and allocations, initializes financial journal only during migration, verifies replay at boot and invalidates prior reconciliation. Existing plans are still PAPER/PAUSED and funding is explicit. Older binaries fail on v4. No HTTP exposure. Tests include real SQLite fixture behavior and separate actual Store migration/restart/corruption tests. Local module/test blob hashes exactly match tested files; 11 focused tests passed. Full checkout/native acceptance pending.

## Remaining qualification

General late/settled fee corrections with discrepancy lock/review, verified non-USD fee conversions, external movements and deposits, reserve subaccounts and release authority, accounting operations/cost/tax reporting, desk-wide consistent independent reconciled publisher (including pending orders and protective positions), actual Freqtrade paper adapter and atomic claim/fill/account agreement, long-history checkpoints and backup/recovery. The current 20,000-event full replay bound is a safety guard, not a production retention policy. No automatic advance to actual paper or live operation.


## TB-FIN-01B2 extension

The whole-desk observation replaces the earlier per-instance balance snapshot as the operational Gate's prerequisite. The accounting journal stores the report without moving money. The report must independently cover the exact enrolled PAPER instance set; each active/protected/pending balance, open quantity and covered quantity must agree with replay, no open orders or unresolved claims, no pending sale settlements, no unreviewed corrections or conflicts. A fresh matching latest observation no older than 60 seconds and bound to the boot generation and execution anchor is mandatory. After any later journal event or execution attempt it must be renewed. An unclaimed reservation is not a submitted order; Gate itself subtracts those reservations. Even a verified no-fill changes the execution anchor. Duplicate observations are idempotent and never refresh timestamps. A missing or unknown instance is a recorded mismatch, not authority to use the previous receipt.

Gate in the actual Store projects RiskState from the financial journal within its own transaction at admission and claim; supplying a separate RiskState is refused. The projection rounds active down, reserves/exposure conservatively, and never allows the protected amount into spendable allocation. Until qualified calendar-period and marked risk data are available, lifetime gross settled loss is a deliberately conservative bound for all three loss limits, and full open inventory cost basis is the downside risk bound. It may block new entries earlier than the final policy. The evidence publisher is a trusted internal interface, not an authenticated venue adapter; actual independently observed Freqtrade order/stop/fee/quantity custody is still required. FILLED_HELD is never cleared by this extension.

Late gross/fee amendments are append-only, preserving source fills and original settlements. Reconstruct effective FIFO history and compare with separately observed resulting balances. A settled sale needs a verified revised net settlement amount. If replay fails, balances disagree, or active cash would increase, preserve the old accounting state and retain a durable correction lock and reason; no automatic unlock, no loss refill, no release of prior protected profit. Negative reserve adjustments reflect independently verified changes to settled facts, not transfers to trading capital. Quantity corrections and broader reversals, external movements and non-USD conversions remain separately gated. 20 focused local tests passed on real SQLite; actual Gate/Store integration tests still require full repository qualification.
