# R2B-INTEGRATE-02-R2-R2 — Native Debian and reviewed migration qualification

Date: 2026-11-20. **One native acceptance run active; protected provider connections remain disabled.**

## Native Debian fixture
[Native workflow](https://github.com/vjmichael/TradeBot/actions/workflows/r2b-installed-r2.yml) reconstructs the exact prior R2B overlay by invoking source-locked `restore-r2b.sh`, asserting tree `250310b3409dd4c0feffa33eba1fe74d8b4ca54a`, then building a disposable Debian 13 VM with actual LightDM, Chromium and per-UID isolated vault and dashboard. The guest creates a **synthetic, independent 32-byte provider-tag-key** through OS randomness, installs it root-only and passes it via the installed systemd `LoadCredential` path. No real API keys, paid provider calls or actual financial account are used.

The prior D2 [run 44810905585](https://github.com/vjmichael/TradeBot/actions/runs/44810905585) reached authenticated Chromium, but the stored screenshots show a browser Find highlight on the instructional phrase, not the real link. The new harness advances browser focus from that phrase to the genuine anchor before keyboard activation and checks for the actual protected native window. It does **not** invoke `xdg-open` or the handler itself as a positive substitute. A successful native result must still include independent credential-operator PAM login, synthetic paired enrollment, status-only observation, and no enabled LIVE/provider authority. If the browser sign-out prevents protocol dispatch, fix the actual source and rerun from a **new locked SHA**, not a fabricated success.

## Existing credential migration foundation
`migration/reviewed_migration.py` stages a **non-destructive, single-transaction** migration from the original Fernet-backed `PaperVault` rows into the versioned `OwnerHooks` metadata table. `inspect_legacy` returns only role names and a private-key HMAC commitment to exact stored ciphertext; it never decrypts or exports credential values to the UI. `migrate_reviewed` rechecks the exact inventory under the existing `BEGIN IMMEDIATE` lock and requires a private one-use, independently authenticated **operator review authority callback**. Only after that source binding does it initialize encrypted metadata and register each installed role as **unverified**; it never rewrites ciphertext or promotes to LIVE. A mismatch, duplicate bot identity, tampered vault, unknown or partial pair, missing approval or existing metadata must fail closed and roll back the whole transaction.

The module deliberately **does not mint or accept a browser approval token**. `consume` is an in-process trusted authority placeholder that is NOT yet backed by the protected PAM/native service, so this code is **not installable/activatable as an approved migration procedure**. The current installed vault still correctly refuses legacy populated stores on unreviewed startup. No migration operation was added to the public vault IPC, account browser, provider adapter or financial Gate.

`migration/test_reviewed_migration.py` contains 15 source-oriented synthetic SQLite/Fernet negative/atomicity cases, pending exact restored-source execution. It verifies byte-preserved ciphertext, stale review refusal, rollback after role collision, refusal after previous migration, empty/partial/unknown data, missing approval and nonce state initialization. A local `py_compile` check is **not** runtime acceptance.

## Service health and provider-testing gates
The actual R2B React dashboard already displays Wi-Fi and service status together with bounded offline observation. No green `Services available` is possible without fresh, independently qualified witnesses from dashboard, vault, financial Gate, reconciliation and market-data supervision plus real VPS internet evidence. Current systemd ActiveState and kernel route alone are not positive evidence. We will not invent endpoint readiness, run a paid beacon or send unapproved Telegram messages.

Kraken/Telegram/Grok restricted standalone adapters and unit checks exist, but protected provider test execution is still **blocked** pending true one-use OS-authenticated operator intent, current vault generation/policy, durable per-provider attempt reservation and isolated fixed-destination worker receipt provenance. Credential vault stays `PrivateNetwork=yes` and `AF_UNIX` only. No secret read/export RPC or browser/AI authority may be added.

## Evidence classification
- Verified in previous task: 77/77 restored-source Python checks plus locked React build in [run 44826388014](https://github.com/vjmichael/TradeBot/actions/runs/44826388014).
- Current native Debian run: [44827704438](https://github.com/vjmichael/TradeBot/actions/runs/44827704438). Terminal result and small screenshot/receipt artifact must be inspected before closing any native gate.
- Migration tests: source staged and syntactically validated; exact restored-source acceptance pending.
- Full installed provider and trusted service readiness checks: **not executed**.
No changes to original D2 source tree `a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b`, no main merge, no real credentials or orders.
