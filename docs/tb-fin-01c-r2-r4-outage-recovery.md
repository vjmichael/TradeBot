# TB-FIN-01C / TB-08-R2-R4 — independent partial-fill settlement and snapshot-outage protection

Status: locally qualified bounded PAPER paths; production/installed closeout remains open. No actual desk activation or main changes.

## Implementation

Accepted synchronous entry authority now persists a native-private authenticated immutable reduce-only record before the side effect, after the desk's current final authorization and transfer of the kernel execution fence. Recheck short permit and guardian immediately after fsync. This record cannot authorize a second entry. Missing, tampered, mismatched, future-dated, symlinked or wrongly permissioned records refuse. Root-signed rebind identity validation is shared with existing ledger-prefix verification; only the latter permits ledger-dependent actions.

When protected native terminal readback encounters a missing, expired or checkpoint-changed snapshot, the separate local reduction path verifies root-pinned rebind identity, local native DB/key binding, exclusive lease, actual persisted open exposure and its exact accepted grant. It may cancel an existing pending buy with observed native simulator state, recover previously frozen authenticated cancellation, or reduce only the actual tagged holding under the preapproved stop/target and fresh public quote. It never polls pending buys into additional exposure. It records exit attempts durably via the existing exit custody journal, never signs settlement based on outage state or retries an ambiguous effect. Fresh authenticated snapshot, exact signed terminal receipt, desk import and signed acknowledgement are required before release. No open trade is not evidence of no fill. Invalid signature or other unexplained snapshot errors fail closed. Single migrated workspace only. Older open positions without the new accepted grant require explicit recovery review.

## Local evidence

Pinned Python 3.12.16/Freqtrade 2026.10 and actual financial ledger, separate OS identities, controlled synthetic market book. Actual Gate admission/claim, actual native PAPER methods and persisted ORM. Partial-fill quantity is a deliberately injected cumulative simulator observation because pinned simulator normally fills all-or-none. Subsequent authenticated freeze, SIGKILL, cancellation import, actual native exit, ledger settlement and acknowledgement are genuine. No live credentials, public venue orders or production state.

- Partial-fill case: 5/5 checkpoints, exit 0. Actual authorized pending buy, observed partial 0.2 BTC ($20 synthetic notional), durable frozen cancellation followed by SIGKILL, no snapshot reduce-only exit, $3.78 net simulated profit protected; $100 active principal, no remaining position/pending funds/reservation; signed import, duplicate nonmutation, exact acknowledgement. SHA256 d5a8278e5a8149350d05896dd57523b5375186026550911a665cf3269dd456d9.
- Full-fill outage: 5/5 checkpoints, exit 0. Actual independent entry, snapshot removed, reduction under durable grant, no pre-recovery release, fresh snapshot settlement, $4.8951 simulated protected gain. SHA256 18afc5d6cb4ec76dfdc659a9d623e23ba37e1ad3b72065169daf22ad9c99b59e.
- Post-persistence SIGKILL: 5/5 checkpoints, exit 0. Desk UNKNOWN, exactly one native order, no repeat authorization, no-snapshot reduction, restored snapshot and verified terminal settlement. SHA256 09bd8becddd77aebfb3b994a39de462ade49d7af5a6c4e11882f7e0c8d10bd3d.
- Guardian/native pre-effect SIGKILL: 4/4 checkpoints, exit 0. Existing kernel fence persists until native death; unresolved claim and reserved principal preserved. No persisted open exposure during outage creates no zero-fill proof or settlement; no automatic retry. SHA256 26a52384a1c5fb25c46e6569a4bd50f917321f81f14c6038f0be827f8b322200.
- 33/33 focused tests PASS; broader 137/137 regression suite PASS, 13.839s, exit 0, including four new grant tests (not additive). SHA256 c54e6af97833a35cf9dd8c3e39d7bc0a29ab5edb6deb53ab4be6289be5718df6 and f83a088177ce0cec6efa24c914df58f13be7bbda10a434d8658e12a80a1202db.
- Initial test fixture source-directory setgid bit rejected; corrected fixture permissions. Initial settlement assertions incorrectly inspected raw journal state instead of observed projection; corrected, full independent reruns passed. Exact tested code/test blobs matched local Git hashes.

Reproduce on disposable Linux as root with setpriv and pinned runtime; PYTHONPATH includes project, scripts, tests and engine_strategies; run scripts/qualify_independent_entry.py with --partial, --outage, --after and --kill.

## Remaining requirements

Unfrozen pending orders whose actual simulator cache is lost and pre-effect ambiguous exits/entries without positive execution proof retain their holds. Need explicit operator-reviewed recovery/disposition without inferring no-fill from absence; do not claim these automatically recovered. Protection requires a fresh market quote and a functioning native worker even during relay loss. Only removed-snapshot real integration exercised; timed expiry/checkpoint-change branches implemented but not independently exercised in native integration. Actual systemd supervision/installed restart and timeout paths not executed. Complete independently authenticated wallet/approval delivery, migrated goal evidence, full installed entry-to-settlement and operator recovery UX, prior failed public PAPER settlement/sustained forward validation, and TB-SEC-KEY-01 release audit. No new CI, production changes, live authority or merge.

Exact changed source:
- reserve_native_rebind.py: 64e3d75c4b3cb83e4eb60d3bbebccb8404db2334
- reserve_entry_broker.py: c22bdc47718a68b085782f4d4d2d91c52c087df0
- reserve_terminal_worker.py: 9003a455d50889761997000d8109a43910d13e5a
- reserve_outage_protection.py: 989cf9373ce6f6f9936892dcd03ef4e197a769bb
- tests/test_outage_protection.py: 7113a607776c81910d673f15873a2a086c594039
- scripts/qualify_independent_entry.py: 6c1a3b3c6c8cc554ed048ee6ec1a3aa215f3a85c
