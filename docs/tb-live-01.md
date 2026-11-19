# Live/Paper transition warning and custody contract (TB-LIVE-01)

Status: binding design requirement, not implementation or authorization of LIVE. Existing Store and worker remain PAPER/PAUSED. Neither real-money keys nor live order routes are enabled.

## Per-instance Paper Trading switch

Default ON. Display actual mode, running/paused status, pending transition, venue/account and current exposure persistently. OFF is a request for a new, explicitly approved live authorization, not automatic promotion; display the chosen account in masked form, exact market, strategy, authorized capital, loss/exposure limits and unresolved orders. Require separate strongly authenticated confirmation, versioned warning acknowledgement, exact scope and limits, account/venue eligibility, readiness, reconciled exchange balances/orders/positions, qualified protective order capability, exclusive owner and all existing security and seven-day/forward evaluation gates. Cancel, expired review, invalid credentials or failed checks have no effect on mode or authority. Only a qualified, explicitly confirmed RUN authorization may allow autonomous real-money trading. An AI cannot confirm or alter this decision. Live remains unavailable until implementation and separate release acceptance; switch must not imply otherwise.

### OFF — proposed text

**Enable live trading?**

Maj Vaj will be authorized to place actual orders using real money in the selected account, within your approved capital and risk limits. Trading can result in losses, including loss of all capital committed to trading. Maj Vaj does not guarantee profit, returns, or execution at any particular price. Protective orders may fail, be delayed or execute at worse prices. Exchange, market, network and software failures can affect results.

Review the account, authorized amount, risk limits and open obligations before confirming. Paper balances and positions are not real funds. They will not transfer to LIVE.

Actions: Cancel (default); Review and confirm live trading (strong authentication). Acknowledgement is not permission to exceed any limit. Persist exact displayed warning version, authenticated operator, scope, policy version, review digest, decision and outcome, not credentials.

### ON — proposed text

**Return to paper trading?**

New real-money entries will cease on confirmation. Existing live orders and positions may still expose you to financial loss. Canceling pending orders does not necessarily cancel fills that have already occurred. Closing positions may realize losses and incur fees; leaving positions unmanaged can cause further losses. Paper trading will not manage your real assets.

Review open live orders and positions. Existing protective management continues in a visible live-exit-only transition until all orders, fills and positions attributed to Maj Vaj are independently reconciled and resolved or explicitly handed over to authorized manual management. Cancel (default); Review live positions and confirm return to paper.

## Fail-closed transition

On confirmed return, fence new live entries immediately, including queued/claimed entry races, invalidate live entry authority durably and cancel unfilled entry remainders through the verified exchange adapter. Keep protection until a separate explicit disposition: wait for managed exits, separately authorize flattening with reviewed fees/slippage/risk consequences, or verified handoff to manual exchange management (including order custody and acknowledged remaining exposure). Never liquidate everything silently, cancel protective stops without replacement/disposition, assume missing venue data implies flat, or disable existing protective worker with unresolved positions. If exchange unavailable, show LIVE EXIT ONLY / reconciliation required, keep protections where possible, alert operator, do not report successful PAPER transition. Actual mode and locks persist across crash/restart; completing PAPER never reuses LIVE financial capital or converts live balances to virtual balances. New paper execution only after transition commit and paper readiness/authorization. Emergency pause remains independently available and does not depend on accepting a warning or waiting for AI/approval.

Qualification: graphical two-direction warning and authenticated cancellation, policy/venue/account scope, expired/changed review, hard Gate lock and unknown exchange state; concurrent entry/fill race, partial fill, protective stop custody, manual handoff, flatten (independent review), restore/restart, distinct financial ledgers, no model/API bypass, explicit mode labels and pending transition, durable acknowledgements. Use sandbox/mock venue and paper-only fault drills before any separately approved live canary. Legal/product compliance review of disclosures and consent is separate; acknowledging risk does not replace engineering safeguards or remove responsibility for product defects.
