# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2 — Authoritative vault contract, native review and health route

Date: 2026-11-20. **Staged qualification implementation only. Not installed, not merged, not a credential/provider release.** D2 exact production candidate Git tree remains `a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b`. No source change to that tree or mainline.

## Implemented source and contract

**ManagedPaperVault** (`managed_paper_vault.py`) wraps the single existing `PaperVault` and relies on its actual `transaction(), _verify(db), _status(db), cipher` interface and the earlier `OwnerHooks` module, which is imported from `qualification/r2b-integrate-02` when that directory is present. It does **not** create a second vault, socket or credential export path. Only the verified `maj-vaj-vault` service UID can instantiate/operate the wrapper. Strictly configured, immutable role-to-policy fingerprints and a separate HMAC tag key must come from trusted protected owner configuration; this task does not provision those keys.

- Explicit migration on **empty** existing vault only. Preexisting credentials require a separately approved native migration before this wrapper may be deployed. No silent migration, overwrite or reset.
- Single BEGIN IMMEDIATE transaction encapsulates encrypted, paired credential enrollment/removal and encrypted generation/role metadata update. Status-only response retains the existing `stored_unverified/not_configured`, `connections_verified=false` and `deployment_ready=false` contract. Supports Kraken read, Telegram Desk, Telegram Recovery and optional Grok; no trading credential role or LIVE authorization is exposed.
- Refuses previous/parallel writes made outside the new lifecycle by comparing *all* existing credential ciphertext bindings and tracked roles before mutation. Refuses wrong role, partial pair, metadata orphan, changed key identity, missing migration or unexpected policy. Nonce high-water marks persist on removal. Replacements invalidate prior verification; startup invalidates previously successful receipts, and interrupted Telegram sends remain delivery-unknown without retry. The underlying staged OwnerHooks code supplies encrypted metadata and bounded generation/receipt lifecycle.
- **No provider test execution method is exported.** The security-critical authorization and provenance path cannot be inferred from a caller-controlled boolean. Real test execution is held back until independently authenticated native UI/session, server-side SO_PEERCRED/session admission and fixed protected adapter results are proven.

**Native review guard** (`native_test_review.py`) reuses the **existing** `credential_graphical.current_identity()` and `SessionEvents` to check the dedicated operator desktop before and after a fixed, explicit native connection-test prompt. It only returns an in-process, non-secret expression of intent; **not** a signed proof or server-side authorization. Grok requires strict opt-in. There is no browser-supplied test endpoint or positive provider dispatch, and Telegram sends no messages in this candidate.

**Actual application HTTP integration module** (`health_endpoint.py`) installs a status-only `GET /api/connection-health` route on the existing FastAPI app with the app's **same `authorize` dependency**, no new bearer or client credential. Responses carry bounded, fixed service/internet categories, never secrets. A missing/stale/failed source returns `unknown`, not a false-green result; `Cache-Control: no-store`. `health_backend.py` needs *independent fresh positive readiness* for all required services (dashboard, vault, risk Gate, reconciliation and market data) and separate VPS internet evidence; process-active alone is not readiness. `health_cache.py` stores observations from existing fixed local supervision. It offers nonblocking `latest()` and **demand-triggered, at-most-one** bounded sample worker; no continuously polling new daemon, provider beacon, paid inference or arbitrary destination. Health sampler yields internet unknown until separate valid network evidence is available.

At the exact authoritative `tradebot.py` base, `create_app` defines its `authorize` function and a separate unauthenticated `/health` route. During final convergence, the existing `create_app` must mount the new authenticated route and use the demand-sampled local cache; source registration was **not performed**, to avoid altering the D2 tree. The existing prior `service_status_strip.cjs` and `offline_connection_client.cjs` must likewise be wired into the actual React `Desk` shell. Only after that may the UI display **Wi-Fi + Online + Services available** together based on verified backend evidence. An already-open browser can still report loss of host reachability; an uncached remote browser cannot load a dead VPS. The separately staged local support GUI is a different workflow.

## Local tests — scope and limits

| Suite | Passed |
|---|---:|
| Managed vault and real SQLite/Fernet transactions | 25 |
| Separate business-readiness projection | 18 |
| Native session-review guard (injected session mocks) | 14 |
| FastAPI authenticated health route with TestClient | 14 |
| Nonblocking, bounded health cache | 8 |
| **Total** | **79/79** |

All tests are local, synthetic and offline, zero failures/skips. Test fixture uses the *published PaperVault table and transaction contract*, with real temporary SQLite and Fernet, and a locally transcribed prior OwnerHooks implementation; it is **not** a source-identical native installed PaperVault/OwnerHooks test. The published tests import the **actual earlier GitHub OwnerHooks module** when executed in the repository. Run these tests again from the exact consolidated checkout, then execute actual installed UID/PAM/seat, socket, systemd, process/network custody and restart/recovery acceptance before qualification. No provider call, real token, financial action or installed service was exercised.

## Outstanding mandatory integration

1. Mount the managed wrapper inside the **one authoritative** `maj-vaj-vault` service; replace old mutation dispatch paths atomically, provision separate protected tag key, review saved-credential migration safely and test same-source existing encrypted databases. Do not leave parallel legacy writer enabled. Prove cross-role consistency and durable nonce/rollback behavior under full crashes and backups.
2. Bind native operator review to *server-verifiable* session/intent via kernel peer identity, same active local OS session, one-use reviewed request and protected adapter receipt provenance. Current in-process UI acceptance alone is insufficient; keep test execution unavailable until proved. No provider credentials exposed to browser, AI, research, argv or logs.
3. Mount the authenticated status-only route on the installed dashboard FastAPI app; feed it real fixed local service-health samples through existing supervision, independent qualified business-readiness checks and separately evidenced internet observations. Wire the previous adjacent service strip and browser fallback; verify offline/401/missing VPS, stale/contradictory signals, OS visibility/accessibility, no green on unknown. No new unconditional long-running poller.
4. Reconcile D2's still-failing browser-to-native enrollment handoff. Only then run consolidated native Debian/GUI/credential transport canary and negative tests, including operator revocation, restart, concurrent replacement, duplicate messages, receipt replay and resource envelope. Keep LIVE and paid Grok closed; retain independent financial Gate.

File contents published on existing non-merge qualification branch. All ten files re-fetched and compared byte-for-byte with the locally tested snapshots. No additional CI, main merge, installed production activation, real credentials, paid provider use, or new hosting.
