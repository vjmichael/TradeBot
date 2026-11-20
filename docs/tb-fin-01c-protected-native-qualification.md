# TB-FIN-01C / TB-08-R1 — real native protected reserve qualification

## Reproduction and evidence

- Exact source parent: 4d9c2e897587368113ac4a2539871cc82e7e8764, 221/221 tracked Git blobs verified. Source restoration workflow [44667205065](https://github.com/vjmichael/TradeBot/actions/runs/44667205065), artifact 14598057155 SHA256 a4010a7cc51772346302272428f3e438a5d0419f4a9b34b3989a26bad68a980e. This is source restoration, not candidate CI acceptance.
- Interpreter Python 3.12.16; Freqtrade 2026.10 offline wheelhouse with 83 packages. Python 3.11.18 also verified. No additional dependency needed.
- Regression command: PYTHONPATH="$PWD:$PWD/tests:$PWD/engine_strategies" python -m unittest -v test_reserve_migration_engine test_reserve_native_wallet test_reserve_snapshot test_financial_ledger test_financial_reconciliation test_reserve_policy test_paper_provision test_paper_custody test_reserve_native_rebind. 74/74 PASS, 17.927s, no skips. Log SHA256 21004b4f83d6033504f6353c24bacad1521493ff63eac9255629cba74f84718d.
- Native wallet runtime 5/5 PASS, 0.719s. Log SHA256 066a9061762d036bc2d9e691349806a2831746373f41139887ac1f2731e3821b.
- New exact tested script scripts/qualify_protected_native_reserve.py Git blob 2c9001d4bd9f96526a6c11462527f3f94446fef6; SHA256 87c6701eb8378242eb7c4ef14ac5e4a0391f1178f763ff81df1afcdc35e3e269. Run as root in disposable Linux environment with setpriv, PYTHONPATH="$PWD:$PWD/scripts:$PWD/engine_strategies" python -B scripts/qualify_protected_native_reserve.py. Uses real independent Linux identities and actual authority socket, native driver/ORM, signed wallet publication and real ledger. Six checkpoints passed in complete invocation, exit 0. Log SHA256 83ce2f3917207204718fc182cc163deb1017bd9896943f60ad73e9d922f376a5.

## Results

1. Actual pristine provision, migration, rebinding, native producer, authenticated wallet reconciliation.
2. Native private signing key denied to desk and human authority; vault canary absent from projected snapshots.
3. Independent human exact review and authenticated authority adoption.
4. Native signed zero-surplus tick.
5. Actual dry-run fills and terminal fee journal, real ProtectionWorker reconciliation and idempotence; $9.45 gain, $4.72 release, $4.73 protected, $104.72 active, $109.45 total; reobservation after release.
6. Stale-wallet revocation, durable ledger and independent witness agreement.

## Boundaries

Isolated PAPER fixture only. Synthetic order book and fees; test-only claimed order intent/exit attempt and observation transfer, controlled root-reviewed cold snapshots and synthetic automatic human confirmation; logical clock advances. No live endpoint, private account keys, withdrawals or actual desk changes. Production narrow execution/settlement handoff, installed live snapshot relay, protected worker lifecycle and acknowledgments, graphical launcher, scheduler/race/restart acceptance remain open. TB-SEC-KEY-01 full installed synthetic-canary matrix and prior public one-hour settlement failure remain mandatory. No merge. Local R1 scope passes; not a production authorization.
