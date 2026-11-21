# R5-R2-R1 — installed repeat-power-loss, rollback and offline signed-upgrade qualification

**Scoped native PASS** — [original exact-source GitHub Actions run #44913129057](https://github.com/vjmichael/TradeBot/actions/runs/44913129057); source **`162edb29d60abe8e373c2e961333611d74f13717`**. This report is documentation-only and does not alter the accepted tested source.

**Acceptance:** 235/235 source regressions (31+11+10+9+22+51+71+9+21); empty Debian dpkg audit; all **four independent installed Debian 13 QEMU no-network scenarios** passed on their own isolated guest disk overlays, with the original hard-reset and source-lock checks intact:

| Case | Verified exact original native receipt |
| --- | --- |
| `upgrade_power` | Three actual QEMU process SIGKILL + same-disk reboots; incomplete signed upgrade blocks activation; recovery restores original source and trusted identity; signing key remains unchanged; signed upgrade reapplies successfully (`R5_R2_THREE_HARD_POWER_CUTS_SIGNED_UPGRADE_RECOVERY_PASS` and `R5_R2_SIGNED_UPGRADE_REAPPLIED_AFTER_RECOVERY_PASS`) |
| `backup_power` | Interrupted backup preparation remains disabled, pre-mutation journal abandoned only through explicit recovery, original source verified, reinstall succeeds (`R5_R2_INTERRUPTED_BACKUP_ABANDON_PASS`) |
| `rollback_repeat` | Three actual same-disk QEMU hard resets during interrupted installation, restoration and rollback cleanup; restored bytes, hold removal, isolated services and reinstall accepted (`R5_R2_THREE_HARD_POWER_CUTS_ROLLBACK_COMPLETED_PASS`) |
| `signed_upgrade` | Invalid signatures, modified release payload and altered independent trust root refused; root-provisioned Ed25519 signed compatible v1→v2 release admitted and protected synthetic services only explicitly enabled (`R5_R2_AUTHENTICATED_CROSS_VERSION_LIFECYCLE_PASS`) |

The original run contains `R5_R2_ALL_OFFLINE_POWER_AND_SIGNED_RELEASE_CASES_PASS`. Independently downloaded artifact `r2b-r5-r2-power-and-signed-upgrade-evidence`, ID `14710224310`, SHA-256 **`504df835b71902df69701a532f23ae3fcbb66b4718114c17d2f1bce025471ff6`** (matches GitHub original published digest), ZIP integrity verified and all four `r5-r2-*.txt` native receipts present. Artifact's `acceptance-commit.txt` matches `162edb29d60abe8e373c2e961333611d74f13717`; `dpkg-audit.txt` is empty.

## Genuine failures and corrections

A real hard-power cut exposed a zero-byte original vault systemd unit in the QEMU guest; the installer now requires accepted original service-unit identity and fsyncs its file and parent before transaction mutations. A first interruption exposed an insecure temporary staging parent (fixed with explicit root-only `0700`). Repeated rollback testing corrected an invalid assumption that unflushed copied source bytes always survive a host-killed QEMU process: the test now requires genuine exit 137, durable journal state and exact final restoration.

The R5-R2-R1 diagnostic [run #44911423512](https://github.com/vjmichael/TradeBot/actions/runs/44911423512) confirmed `R5_INSTALL_REFUSED:installation_seal_refused` after the first signed-upgrade power cut. The installed completion seal and protected source/unit/key files were not all durably synced before admission; the fix adds explicit fsync of files and directories followed by strict seal verification without weakening any refusal gate. The next exact-source run [#44912614316](https://github.com/vjmichael/TradeBot/actions/runs/44912614316) reached the next original refusal, `R5_R2_UPGRADE_REFUSED:offline_trust_root_mismatch`: root-owned test trust enrollment had been copied without durable sync. The fixture now checks both original independently provisioned trust files byte-for-byte, fsyncs both and their parent, and keeps the Ed25519 and SHA-256 identity checks. The final unchanged-SHA [run #44913129057](https://github.com/vjmichael/TradeBot/actions/runs/44913129057) passed every original gate. The repair sequence created no parallel overlapping CI.

## Release boundary

**Only an offline disposable QEMU synthetic-denial proof.** The v1→v2 signed package is a fixed compatible code revision (additive version declaration) whose ephemeral Ed25519 private signing key never leaves the runner. Trust provision is a root-authorized **test fixture**, not a production publisher/installer trust process. No encrypted database/schema migration, actual provider key enrollment/permissions, production signed-package revocation/rotation, physical deployment, unsandboxed internet, LIVE or financial execution was accepted. Rollback of credential-bearing stored records still explicitly refuses; production migration needs a separately reviewed contract and native negative tests.

**Next engineering group:** R5-R3 — production package publisher/trust enrollment, authenticated version/data migration custody, repeated interruption and rollback with credential-bearing state, and physical/installed release admission before real provider checks. Preserve paper default and all financial/LIVE gates. No merge to `main`.
