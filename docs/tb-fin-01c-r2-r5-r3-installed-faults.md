# TB-FIN-01C / TB-08-R2-R5-R3 — installed in-flight interruption qualification

Status: **PASS — scoped installed controlled-book PAPER qualification**. Production execution code and deployment units unchanged from R5-R2. No actual desk changes, LIVE authorization, main merge or CI dispatch.

## Exact basis and method

- Parent candidate 4efd6ff17196bdf8281d21d4f2cb70719a1ad015; 199/199 deployed Python and service blobs matched the accepted manifest inside a freshly restored offline Debian systemd/cgroup-v2 QEMU guest. Pinned cached Python/Freqtrade runtime installed with dependency check PASS.
- Only the disposable fixture selected the controlled-book factory and injected the fault through a temporary root-owned service override. Actual installed native process, desk authorization broker, signed wallet, kernel fence, relay and settlement workers remained in use. Readiness, 30-second watchdog, 120-second initialization bound and production authorization deadlines unchanged.
- The qualification harness refuses an ordinary desk: isolated loopback-only systemd guest, pristine $100 PAPER fixture, both pause flags set, sole synthetic vault canary, no orders and stopped prior services. Test-only worker fixture and harness retained as scripts/qualify_persistent_faults.py and scripts/qualify_persistent_faults.sh. The harness expects the prior offline qualification mount and reviewed pristine fixture. A new fixture is required for each mode.

## Acceptance

| Case | Result |
|---|---|
| Actual installed watchdog after native buy persisted, before desk acknowledgement | PASS: observed one native attempt and one persisted order, no native return, retained reservation; actual systemd watchdog timeout, replacement readiness, one trade/two orders and one terminal settlement, $100 active principal and $4.8951 protected simulated gain. |
| Actual installed SIGKILL after durable partial cancellation freeze | PASS: one pending order and authenticated frozen cumulative-fill receipt before kill, reservation retained; replacement recovered cancellation, closed only recorded exposure, one trade/two orders and one terminal settlement, $100 active principal and $3.78 protected simulated gain. |
| Replacement stays running through settlement | PASS for both cases. |
| Post-run audit | PASS: both ledgers paused, no outstanding reservation, no open exposure or proceeds, no open orders, one settlement each; test services stopped, timers inactive, override removed. |
| Guest shutdown and disk integrity | PASS: graceful poweroff, qemu-img check zero errors, base SHA unchanged. |

The injected 0.2 BTC partial is a controlled cumulative simulator observation; subsequent durable freeze, interruption, recovery, protective order and ledger settlement are genuine installed PAPER operations, not venue execution. The full-fill and partial-fill test scenarios are independent, not cumulative profits.

Three separate local native regression scenarios on the unchanged parent also passed (15/15 printed checkpoints): after persistence, partial cancellation, and pre-effect ambiguous hold including guardian fence and refusal to infer non-invocation. The first installed harness attempt refused a leftover idle broker from preflight before trading; it was stopped and the watchdog property assertion moved to the active service, then the complete fresh run exited 0. No safety or timing thresholds were relaxed.

## Evidence hashes (SHA-256)

- Final installed run: cdf3db52d55b79cdef89c4dea29c7e7b86b730dd6a1abaee22229e64f5781795
- Final audit: 9be5c75d0f204ba703c07daed840466be48e58b68c45e3f9cd3b593e2517447e
- Guest preparation: 9d8caba3d86174aae72156ae6f1615994e1cf63bad1760c350e45f01d734031c
- Graceful shutdown: 0fb6f565e5cb52e1ea481d9501d16db1b289e5c129e361e215c13f98132b49e1
- Immutable base QCOW2: 2eba10bdfc7f368dde145205e0f3211f5f3a0ce29a5ab60b5ea7b728fd9a1776
- Local after-persistence: 09bd8becddd77aebfb3b994a39de462ade49d7af5a6c4e11882f7e0c8d10bd3d
- Local partial cancellation: d5a8278e5a8149350d05896dd57523b5375186026550911a665cf3269dd456d9
- Local ambiguous hold: 9dee051f9e14f2141a0aa101068659fde9a715ca7416b37171b0ad0efcf635bd

Remaining: installed operator review producer/GUI and repeatable mailbox recovery/archive, installed explicit snapshot-expiry/checkpoint-change cases, production-constructor public-data PAPER sustained execution, migrated goal acceptance, forward validation and credential-security gate. No profitability or live-readiness inference from controlled books.
