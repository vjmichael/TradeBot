# TB-FIN-01C / TB-08-R2-R1 — independent native protective exit custody

Status: partial candidate. Local bounded PAPER exit/recovery acceptance passed; full installed lifecycle remains open.

## Implementation

Native worker resolves an existing claimed/unknown/held PAPER intent from a signed minimized snapshot, verifies the exact rebound workspace, native binding/attempt, exclusive native lease and actual Freqtrade position. An authenticated append-only native journal durably records each exact protective exit before the native action. Missing, ambiguous or conflicting tagged results block retry; terminal partial exits are bounded by verified remaining quantity. Existing open buys are held without polling into new exposure. Signed terminal v2 includes the locally retained exit attempts; keyless desk validates exact sequence, trigger, quantities and existing attempts and atomically invokes real reconciliation. Duplicate import is nonmutating. Native retires the mailbox only after fresh signed acknowledgement. Snapshot transfers four numeric protection fields and only the selected execution-event binding, excluding arbitrary event metadata and claim token. New service path is explicitly opt-in, not activated.

## Tests

Python 3.12.16 / Freqtrade 2026.10, actual separate Linux UIDs and pinned native persistence/exit method/financial ledger; controlled market book and pre-existing synthetic claimed buy, not actual entry broker.
- Before-effect SIGKILL after durable journal: 5 checkpoints passed, exit 0. Retained ambiguous hold, no retry or settlement packet. Position remains unresolved, explicit recovery review required. Result log SHA256 686ba0c8e10b4a955f8e4e9c0d0827166906c0d5aa7d7ab4d6cdcecd3332ec19.
- After-effect SIGKILL after actual native exit/persistence before delivery: 9 checkpoints passed, exit 0. Recovered tagged result from fresh process, authenticated v2 transport, atomic settlement, nonmutating duplicate, acknowledgement, $4.72 reserve release from $9.45 simulated gain, revocation. Log SHA256 4033951cfffd032fecc99f0ced5219e868bb905e9a52113433a45e2222cb746a.
- 13/13 focused tests PASS, 8 exit + 5 snapshot (includes event-metadata canary), log SHA256 81568018de674dfb9147325ffc90f36b545f484fc633776f4ce22c3040ebd5b0. Initial broader snapshot/wallet/migration batch 14/14 PASS, log SHA256 3c1d75a99ece0b38ad8cda0aa7168aa5ff1ffbd4bc9f0b29333ce6594c7c4e16. Initial integrated command timed out, both separately rerun cases passed.

Exact tested blobs matched local Git object hashes. Reproduce with PYTHONPATH including project, scripts, tests, engine_strategies; run scripts/qualify_native_exit_recovery.py as root on disposable Linux, with and without --cut-before.

## Remaining

No new-entry approval solely from snapshots: synchronous entry-side gate/pause/expiry/owner/goal checks and durable native attempt still required. Pending-buy cancellation, zero-fill proof, ambiguous pre-effect exit disposition, protection on snapshot loss, automated wallet/approval delivery, and actual installed systemd lifecycle remain pending (local PID 1 supervisord). Sustained public PAPER settlement and TB-SEC-KEY-01 full installed audit remain blocking. No main merge, actual desk change, new CI or LIVE authority. Keep R2-R1 open.
