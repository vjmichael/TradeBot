# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R3

**Status: SCOPED INSTALLED PASS — offline synthetic denial only.** No release or LIVE authorization.

## Exact accepted evidence

- Source branch: `qualification/tb-sec-key-01-r2b-admission-r3`.
- Accepted application and harness commit: `f4b513480f948c638f450d2aeccb2acdfe6201af`.
- [Native installed acceptance #44870913193](https://github.com/vjmichael/TradeBot/actions/runs/44870913193): **SUCCESS**, 9/9 mandatory end-to-end markers.
- [Prior native broker #44868873434](https://github.com/vjmichael/TradeBot/actions/runs/44868873434): **SUCCESS**, 8/8 markers.
- [Prior installed synthetic worker #44869628432](https://github.com/vjmichael/TradeBot/actions/runs/44869628432): **SUCCESS**, 7/7 markers.
- Source regressions on R3 acceptance: 31 + 22 + 51 + 71 + 9 + 21 = **205 passed, zero failed**. Debian dpkg audit empty.
- Artifact: `r2b-r3-native-end-to-end-evidence`, GitHub artifact ID `14694370050`. Downloaded archive digest matches `sha256:9925040c45d6226ed1167c6bd32276df4d0b7dee90b99ab61ee6af368c49c02d`.

## Original failure and correction

The original [R2 integrated run #44870100466](https://github.com/vjmichael/TradeBot/actions/runs/44870100466) and [R3 independent diagnosis #44870526160](https://github.com/vjmichael/TradeBot/actions/runs/44870526160) failed. The captured original Debian service state proved that both the independent approval broker and synthetic worker were active, but `maj-vaj-paper-vault.service` exited status 1 before creating `enroll.sock`. The source test PYTHONPATH included the R1 owner bridge, whereas the installed vault process in Python `-I` mode did not. R3 added only the exact root-owned `/opt/maj-vaj/r2b-admission-r1` directory to the overlay's fixed owner bootstrap, locked its blob SHA, and reran the unchanged native matrix. A fixture-only diagnostic restart never counted as acceptance.

## Installed transaction verified

1. Actual root-protected paper vault stores a synthetic Kraken read-only credential pair in encrypted SQLite; its status stays `stored_unverified`.
2. The fixed owner coordinator obtains authoritative current generation and policy; with no credential pair the request is refused before worker use.
3. The original graphical operator UI enters the trusted LightDM session and passes a fixed one-use review to an independent approval broker. No caller chooses request ID, endpoint, key or result.
4. Broker pins the actual kernel/socket peer and PAM/logind session, records the user's choice, and durably consumes the specific grant before the owner vault reserves its bounded attempt. A cancelled request never reaches worker.
5. The separate `maj-vaj-r2b-worker` systemd account with `PrivateNetwork=yes` and a distinct LoadCredential signing key issues only the signed, bound `provider_refused` result. The owner validates worker kernel UID, attempt identity, current policy, timing and HMAC before authoritative finish.
6. The GUI retains unverified status. Replay is denied; after actual logout and service restart, spent approval cannot be reused. All proof is in disposable Debian 13 QEMU without a guest network device.

## Explicit limitations and next tasks

- This is a source-locked **qualification overlay** requiring the root-owned `r2b-approval-test-enabled` marker, not production deployment or provider permission verification.
- The X11 desktop's virtual input demonstrates the GUI path, not unforgeable physical user intent on a compromised host. No real API keys, real Kraken connection, Telegram/Grok notification, withdrawal, trade, financial movement or LIVE execution.
- Additional R3 hardening may independently test lock/switch during modal, key loss during receipt transport, approved-but-unconsumed restart, concurrent credential generation changes, interrupted worker finish and reboot/rollback. No automatic retry of a spent grant.
- R2B real provider sandbox-specific scopes, reviewed migration and R2C browser/notification/KEK/recovery gates remain separate; release gate OPEN.
