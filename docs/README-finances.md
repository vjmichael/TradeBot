# Maj Vaj — Money, trading capital and protected profit

This guide explains the intended financial controls in everyday language. Maj Vaj is currently a **paper-only, paused development application**. Examples use hypothetical dollars, not promised returns. Actual trading, the production dashboard approval interface, and independent exchange reconciliation remain separately qualified release gates.

## Your money and your goals

| Term | Meaning |
|---|---|
| Trading capital / bankroll | The maximum principal and explicitly released profit that you authorize to be put at risk. |
| Daily profit goal | A reporting or optional entry-pause target, not an order size or trading quota. |
| Active cash | Uncommitted settled trading cash; open positions and pending orders may consume it. |
| Protected profit | Positive settled net trading gains placed outside ordinary trading authority. |
| Realized net profit | Sale proceeds minus matched purchase cost and trading fees. |
| Settled net profit | Realized profit after the proceeds and obligations have actually been reconciled. |
| Unrealized profit | A mark-to-market estimate of an open position, not available cash or protected profit. |
| Daily trading volume (turnover) | Total trading activity during a day; a $100 bankroll can be reused, so turnover can exceed $100. A separate turnover limit would be necessary to restrict aggregate daily activity. |

### Can I keep trading with only $100 even after making hundreds of dollars?

Yes. Authorize a $100 bankroll, leave reinvestment OFF (the default), and use a $100 maximum authorized capital. This limit is an upper bound, not a promise to maintain exactly $100 after losses, nor an instruction to trade $100 every day.

| Hypothetical event | Active capital | Protected profit | Total settled balance (no open positions) |
|---|---:|---:|---:|
| Start | $100 | $0 | $100 |
| $500 settled net profit accumulated over five days | $100 | $500 | $600 |
| If total settled balance later reaches $1,000 | $100 | $900 | $1,000 |
| A subsequent $20 settled loss | $80 | $900 | $980 |

The last row assumes the preceding $1,000 balance. Protected profits do not automatically replenish losses. If $100 is a daily **profit goal** rather than trading capital, the goal does not affect the authorized bankroll or reinvestment. Actual results can be negative; no target forces trades. Goal reporting and the optional daily entry-pause control remain planned, not implemented by this reserve engine.

### Will a larger account increase my risk automatically?

No. Account value, cumulative profits, or a reached goal are not permission to increase capital. Until explicitly released, protected funds cannot be used for new orders. Unrealized gains and pending proceeds are also unavailable. Risk limits and pending reservations can make the permitted order size much smaller than the bankroll.

### What is the difference between OFF, saving settings, and ON?

- OFF: no new standing reserve releases. Positive settled gains stay protected. OFF does not liquidate positions or reverse previously authorized releases, and is not a trading pause switch.
- Experiment: a read-only preview. Change example rate, floor and ceiling without moving money.
- Apply/Update: save a version of a policy, without authorizing it. If an earlier version is approved, it remains unchanged.
- ON: a separate explicit review and authentication activates an exact saved version. It is finite, scope-bound and can be revoked. Reusing an already activated version is forbidden; renewal requires a new review.

The optional standing policy evaluates at most once per approved 24-hour UTC cycle, not at each trade or market session. Missed cycles are not automatically caught up. If actual financial evidence is missing or stale, the release is zero. After a loss the current conservative release foundation locks new releases pending a separately reviewed recovery; there is no reserve top-up.

### How much would be released if I choose to reinvest?

Illustration only: start with $100, approve a 10% rate, $50 fixed protected floor and at least $105 maximum trading capital. Assume the policy is approved before the new gain, funds are settled, and all other risk and approval budgets allow the release. With $100 eligible new settled gain:

- $100 profit is protected first.
- $50 newly eligible above the floor times 10% = $5 potential release.
- Final active capital $105; protected profit $95.
- If the authorized ceiling is $100, release $0, even with the 10% rate.

Earlier already protected gains are excluded from a new standing policy by default. A one-time exact amount release is separately reviewed. Tax, household, emergency and savings earmarks remain protected regardless of rate. Per-cycle and lifetime caps, approval expiry, available settled reserve, losses, reconciled balance and shared account cap can reduce a release to zero. The $100 goal and 10% are examples, not suggested settings.

### Does earmarking or withdrawing profit count as a trading gain?

No. Changing reserve buckets is internal accounting, not a new profit, loss, deposit, or external transfer. The bot has no withdrawal authority. External exchange custody is not made safe merely by a ledger label; manually initiated withdrawals and actual transfer reconciliation require separate handling.

### Who controls my money?

The research and strategy layers may recommend or evaluate trades. They do not authorize reinvestment or set the financial risk limits. The deterministic Gate checks the ledger, exposure, market freshness and explicit authority. The reserve scheduler may only evaluate a previously approved policy. A local approval passphrase and exact review are separate from background scheduling. Production operator authentication, OS process isolation, actual independent receipt collection, and installation have not yet been qualified. No real-money mode is implied.

See [binding financial design](tb-fin-01.md) and [reserve engine](tb-fin-01c.md) for technical requirements. The graphical Experiment → Apply/Update → ON/OFF experience is still a later acceptance gate.


### What happens when I switch between paper and live trading?

The planned Paper Trading switch defaults ON. The current build cannot trade real money. In the future, turning it OFF will display a separate real-money loss/no-guaranteed-profit warning and require explicit authenticated approval, exact capital and risk limits, and all readiness checks. Once live and authorized to RUN, Maj Vaj may trade autonomously within those limits only. Turning it ON will warn that new real-money entries will stop and unresolved positions may incur losses or fees. Real positions are not silently abandoned or liquidated: the system retains live-exit-only protection until verified resolution or authorized manual handoff. Cancel leaves the current state unchanged. Paper and live accounts remain separate. See [TB-LIVE-01](tb-live-01.md) for the full warning and release requirements.


### How do my monthly expenses and daily goals relate?

The Financial Goals candidate separates monthly expenses, desired savings, other take-home income, an estimated tax reserve and any additional costs not already counted. For example, $2,500 expenses + $500 savings - $1,500 income leaves a $1,500 after-tax gap. A 20% estimated tax reserve implies a $1,875 pre-tax planning target (about $62.50 per day in a 30-day month). A personal daily target can instead be dollars or return points; 2 points on $100 verified opening active capital means $2. Daily points are unavailable if an authenticated day-start baseline is missing; a $100 principal or daily target never changes capital authority. Paper results are not actual income. Goal settings remain reporting-only. See [TB-FIN-01A](tb-fin-01a.md).


### Can Maj Vaj stop entering trades when I reach my daily target?

The optional daily entry pause is OFF by default. A verified positive target can pause only new entries for the rest of the local reporting day; protection and settlement of existing trades continue. A later downward correction does not undo a recorded daily pause. At the next local day only the goal restriction expires, not manual or risk pauses. If you choose a point-based pause and an authenticated opening baseline is missing, new entries remain blocked rather than guessing. A goal reached notification is retained in the local dashboard after verified financial reconciliation. This is not a quota or permission to increase capital. See [TB-FIN-01D](tb-fin-01d.md); integrated acceptance is pending.


### Can I continue after reaching a daily profit goal, but with more caution?

Stop after goal and ordinary continuation within the existing independent limits remain the two candidate behaviors. A third optional conservative continuation is a deferred design: once a verified target is reached, a narrower approved risk budget may allow qualified opportunities without automatically ending the day. It does not guarantee keeping the day's profit or finding/executing every opportunity. The new setting must never increase exposure, reinvest protected proceeds, disable protective exits or override other risk/manual pauses. This third mode is not implemented yet.
