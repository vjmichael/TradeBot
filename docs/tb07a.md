# TB-07A — Deterministic paper authorization boundary

**Status:** initial implementation candidate. No order submission, live account, credentials, automatic paper activation or profitability claim.

## Contract

The existing desk owns the SQLite database and single transaction writer. Its production state is still PAPER and PAUSED. The Gate is internal; no HTTP route or research worker can create policies, publish trusted account or market receipts, claim intents or resolve execution. An isolated qualification fixture provides RUN and deterministic test inputs to exercise the positive path without changing the actual desk.

* The **proposal** is untrusted, typed and bounded: stable ID, instance, approved pair, referenced evidence ID, proposed notional ceiling and expiry. No model confidence, price, risk-policy amendment or reserve-reinvestment field.
* The **policy** is a separately authorized runtime input: version, monotonically increasing ownership epoch, expiry, strategy revision, order/portfolio/pair limits, position count, per-trade and aggregate remaining risk/loss limits, evidence age, spread, volatility, depth, cost and edge limits. A new policy invalidates unclaimed old intents and preserves ambiguity on prior claims. The future operator approval and publisher access-control integration is required before operational use.
* **RiskState** is an independently reconciled accounting receipt: settled cash including reserves, protected component, instance cash and exposure, open downside risk, daily/weekly loss and drawdown, reconciliation and protection flags. Until TB-FIN-01B supplies real balanced accounting, the existing desk has no such qualified publisher.
* **MarketEvidence** is an independently qualified signal/market receipt: instrument, observation and local availability time, source digest, approved strategy revision, verified trigger and market health flags, deterministic levels, depth/spread/volatility, venue minimum, prevalidated gross edge and independent cost estimate. Quote levels are integer units of 1e-8 USD per whole base unit. Actual source selection, price precision and quantity qualification belong to the execution integration; a digest supplied by a trusted test publisher is not proof of public-source provenance. Bound numerical-only scope; research-dependent entries stay blocked.

## Decision, capital and custody

All amounts are integer USD cents, not floats. Candidate stake cannot exceed the advisory ceiling, policy order limit, observed depth, available instance/desk cash, total/pair exposure residuals or remaining risk capacity. Available active funds exclude protected settled cash and all other pending reserves; existing position cost exposure and pending notional count toward exposure limits. Risk includes current stop-distance estimate and conservatively reserved round-trip costs; pending and existing open downside consumes remaining daily/weekly/drawdown capacity. Round allowed stake down and cost/risk liability up. Refuse insufficient stake; never enlarge an order to reach a venue minimum. This is a sizing bound, not a guaranteed loss cap, stop fill or actual slippage model.

Immutable receipt bodies/hashes and append-only events retain decision reasons, proposal identity, input/policy/ownership binding, expiry, level bounds, reserved cents and lifecycle. Exact duplicate proposal IDs return current disposition; conflicting IDs fail closed. A distinct ID cannot authorize the same instance/evidence twice. The Gate rechecks current policy, account, evidence, freshness, capacity and exact level/amount bounds on claim inside a serialized transaction. Only one owner/epoch claim succeeds, and claim precedes any future adapter effect.

```text
AUTHORIZED -> CLAIMED -> UNKNOWN -> NO_FILL
     |           |          |
     |           +----------+--> FILLED_HELD
     +--> VOID (unclaimed only)
```

Expiry or pause voids only unclaimed intents. On restart, claim ambiguity is retained as UNKNOWN and keeps its reserved amount. The trusted reconciler alone can record a unique verified no-fill/absence-of-obligations receipt. Any fill or partial fill holds the full conservative reservation and blocks fresh entry until TB-FIN-01B provides exact reconciliation. No blind retry, no model or human override of a hard limit, no automatic reserve release. A positive authorization is not an exchange order or execution receipt. The current implementation intentionally serializes outstanding uncertain claims across the desk.

## Acceptance and limitations

Real SQLite test fixtures cover policy, capital, reserves, cost and loss bounds, stale/future/changed evidence, snapshot replay, duplicate and concurrent admission/claim, owner epoch, unknown/no-fill/filled-held and restart, paper/research restrictions, and schema migration with existing allocations. Actual Store integration checks inherited pause/no-live and restrictive transition on pause/startup. The desk database version advances to 3; a previous binary rejects the version instead of disregarding intent records. Established missing ledger tables/clock row cause startup refusal, not reconstruction.

Not yet qualified: publisher authentication/source derivation from actual market memory and reconciled funds, venue adapter revalidation and unique client-order lookups, partial fill quantity/lot accounting, stop and protected exit operations, external worker/host fencing, risk-lock lifecycle and forward paper integration. No order path is exposed. TB-FIN-01B/01C and operational TB-07 acceptance remain mandatory. All default safeguards and v0.8/financial amendments continue to apply.
