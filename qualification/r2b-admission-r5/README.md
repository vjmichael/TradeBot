# Maj Vaj R5 — protected offline installer and installed lifecycle

**Scoped installed PASS, not production release.** Source branch: `qualification/tb-sec-key-01-r2b-admission-r5`.

## Evidence

- **Accepted exact SHA:** `495e8d96ad13703f86d5951b5be7c05ddadd3394`.
- **Native CI:** [#44878841752](https://github.com/vjmichael/TradeBot/actions/runs/44878841752), completed **SUCCESS**; 216/216 source and historical regressions passed. Original Debian 13 package audit empty.
- **Installed lifecycle:** Two isolated original Debian/LightDM/PAM/logind QEMU guests, one full native GUI→real encrypted-vault→one-use broker→HMAC-signed *negative-only* worker→disable/reenable→reboot, one independent session-lock refusal.
- **Mandatory proof:** `R5_INSTALLER_DISABLED_DEFAULT_NATIVE_PASS`, `R5_INSTALLER_ROOT_ACTIVATION_NATIVE_PASS`, `R5_INSTALLED_DISABLED_CUSTODY_REFUSAL_PASS`, `R5_INSTALLED_REENABLE_NO_REPLAY_PASS`, `R4_INSTALLED_COLD_REBOOT_RECOVERY_PASS`, `R4_INSTALLED_SESSION_LOCK_REFUSAL_PASS`, `R5_INSTALLED_PROTECTED_LIFECYCLE_ALL_PASS`.
- **Artifact:** `r2b-r5-installed-lifecycle-evidence`, GitHub artifact ID `14697019389`, independently verified ZIP SHA-256 `3ef3a1623e412cbc85f0c8817fc8f5e04eeb3cd835222a06d42ee2f639bf26b0`.

## Installer behavior

`qualification/r2b-admission-r5/install-offline.sh` consolidates the accepted R3/R4 systemd and Python modules in a fixed source bundle, accepting only `install|verify|enable|disable`. It requires genuine QEMU and loopback-only networking, verifies source Git blob IDs, ownership and private directories, preflights the frozen original vault patch before modifying installed code, provisions a dedicated worker principal and root-controlled worker signing key, and installs a QEMU-only systemd condition. Broker/worker remain **disabled by default**. Positive network/provider/permission results are impossible; the installed synthetic worker signs only `provider_refused` using a distinct domain. Revoking the test marker stops both protected services, and reactivation does not reissue consumed approvals.

## Original failure and repair

[First R5 native run #44878500050](https://github.com/vjmichael/TradeBot/actions/runs/44878500050) passed source/build gates but failed the immediate vault-socket assertion after `systemctl restart`. Systemd `Type=simple` service startup does not guarantee the application has bound its Unix socket. The source-locked installer now uses bounded active-state **and real socket** readiness for vault, broker and worker, with failure/timeout still fatal. The source and native security acceptance criteria were retained unchanged. [Successor #44878841752](https://github.com/vjmichael/TradeBot/actions/runs/44878841752) passed.

## Not authorized / remaining

This installation is **only a disposable QEMU qualification variant**. No real Kraken, Telegram, Grok, API secrets, wallet, provider permission verification, external egress, live order/trade/withdrawal or financial authority. The native UI uses controlled virtual keyboard/pointer input, not cryptographic physical-human presence. Nothing is deployed to the user's installed machine/VPS or merged into default `main`.

Before a real installed release: qualify signed package provenance, idempotent upgrade and rollback after interruption, tampered source/key/unit denials, encrypted legacy migration and recovery, R2C browser/notification/KEK, separately consented provider-specific read-only sandbox permission verification, and all financial/LIVE approval gates. Prior R3 and R4 native evidence remains independently accepted and unmodified.
