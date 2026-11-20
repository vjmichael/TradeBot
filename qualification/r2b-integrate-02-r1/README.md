# TB-SEC-KEY-01-R2B-INTEGRATE-02-R1 — protected vault bridge and offline-responsive diagnostics

2026-11-20. **Scoped engineering candidate; not installed or active.** Frozen D2 candidate production source tree `a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b` unchanged. No real credential, provider request, money movement, LIVE authority, new daemon, or CI dispatch.

## 1. Transactional vault-owner bridge — staged integration seam

`vault_bridge.py` is designed to be imported by the existing isolated `maj-vaj-vault` service; it refuses construction if the current real/effective UID is not that vault principal. It relies on the existing `PaperVault.transaction()`, `_verify(db)`, `cipher` and the previous `OwnerHooks` metadata implementation, not a second vault or API.

- **Explicit fresh-install migration** only, refused if any credentials exist; saved-credential migration needs a separate reviewed disabled/unverified transition.
- **Same-transaction** paired role enrollment and removal: validate exact role/slots and permitted credential form, encrypt inside the owner, update the existing `secrets` table, verify resulting pair completeness, then atomically replace or remove bound version metadata. Rollback leaves neither partial credential nor metadata mutation. The initial role set remains Kraken read, Telegram Desk, Telegram Recovery and optional Grok. Trading credentials remain unsupported; no automatic live activation.
- **Authenticated admission seam:** verify local Unix `SO_PEERCRED` against the dedicated `maj-vaj-credential-operator` UID before accepting a separately reviewed native GUI decision. Reject raw booleans without a real local peer; no IPC command endpoint is added. Actual local PAM/session provenance, OS role isolation, UI confirmation and one-use authorization persistence still need installed qualification. The current in-process `reviewed` argument is not a safe exposed API.
- **Durable attempts/receipt invalidation:** reuse `OwnerHooks.begin/finish/recover_interrupted`, requiring a committed attempt before invoking a fixed provider-specific adapter. No arbitrary provider callback or public receipt submission is introduced. Native isolated adapter execution, trusted result provenance and database anti-rollback are **not** implemented by this bridge.
- No plaintext secret read/export method, model access, order endpoint, external URLs or provider network calls. Existing status-only dashboard path remains unchanged until separate explicit integration.

**Local evidence:** `test_vault_bridge.py` **15/15 tests pass** with synthetic temporary SQLite, encrypted rows, transaction rollback and a **fake OwnerHooks interface**. This verifies the bridge calling contract, not the real OwnerHooks integrated into the installed `PaperVault`. Positive Unix peer admission is simulated; a genuine current-UID mismatch is refused. Do not report production authentication acceptance. Earlier independent owner hooks passed 32 tests but have not been rerun with this bridge.

## 2. Internet-independent diagnosis — two distinct environments

`offline_health.py` queries only local `systemctl show ... ActiveState` for known installed dashboard/backend/vault units (one-second ceiling each), plus a **kernel-only route lookup** (`ip route get` without sending packets). It cannot conclude internet access from a route and never exposes service credentials. Missing route can be identified; route present means internet **unknown** until trusted actual observations exist. Active services are labeled **Local services running**, not **Services available**: service responsiveness, financial Gate and reconciliation readiness are separately required. It does not inspect or change orders, processes, financial settings or credentials.

`offline_diagnostics.py` is an independent local Tk GUI, so the user can inspect local service process status without the dashboard backend or internet access. A bounded worker thread keeps the GUI event loop responsive if a health probe is slow; repeated clicks cannot create parallel probes. No terminal instruction or external API call is required for the end user. An unmodified VPS without a graphical display will not show this window to a remote disconnected client. Desktop packaging/start menu support remains in existing TB-10-GUI-RECOVERY.

`offline_connection_client.cjs` provides a lightweight, bounded observation controller for an **already-open** browser dashboard. An unreachable health call promptly changes status to server unreachable; a hung request has a hard 3.5-second timeout even if its caller ignores abort, then retries at a bounded 15-second cadence. Authentication failure and known server failure have distinct fixed categories; untrusted exception text is never displayed. It uses a caller-supplied existing authenticated **status-only** health read, not a provider request, new endpoint, token store or synthetic server response. Stop cancels/invalidates pending work; results arriving after stop cannot overwrite state. The actual dashboard must still wire this to the previously tested adjacent Wi-Fi / Services available status strip. A newly opened, **uncached** remote dashboard cannot load when the server is unreachable; an already-open page can remain responsive. Offline cache or separately packaged local manager requires its own explicit security and native acceptance.

**Actual local checks in this task:**
- Vault bridge: **15/15** Python unittest pass, fake owner-hook integration fixture.
- Local health collector: **8/8** Python unittest pass, no packets or provider calls.
- Tk graphical fallback: **3/3** genuine Tk tests passed under Xvfb, including delayed probe responsive UI, error redaction and duplicate-Refresh refusal; no installed OS desktop acceptance.
- Existing-browser connection observer: **17/17** Node.js assertions pass, including timeout, stale late result rejection, callback failure category and credential error non-disclosure.

**Total this task: 26 Python unittest tests + 17 JavaScript assertions passed**. Source was published on the existing qualification branch and re-fetch compared byte-for-byte to staged files. No real provider calls, LIVE activation, paid infrastructure, new CI or original candidate modification.

## Binding UX requirement

Keep Wi-Fi icon + **Online / No internet / Server unreachable** + **Services available / need attention / checking** together, as an accessible persistent status group, not dispersed across the footer. Green requires **fresh, positive** independent health evidence of all required services. A stale cached success is not current success. The support UX should show what is known locally even when the VPS is unreachable; it must not attribute the outage to the user without evidence.

See [resource design refinement](../resource/requirements.md) and [R2B-INTEGRATE-02 adjacent-strip candidate](../r2b-integrate-02/README.md). Status information is not authority to trade, stop, restart or revoke anything.

## Mandatory unfinished R1 integration and release gates

- Real `PaperVault` source hook, versioned schema migration including existing stored credentials and separately protected tag-key custody; no independent mutable duplicate code.
- Native actual `SO_PEERCRED`/PAM/session/confirmation acceptance, no direct programmatic bypass; protect against replay and forged receipt source, and prove permit/attempt consumption across concurrency/restart.
- Restrict provider-specific adapter network, credentials and return provenance through distinct service principals; isolate transport; authenticated full GUI test connection with synthetic provider responses and no real orders.
- Trusted status-only service readiness and network observations from installed supervisor, financial Gate/reconciliation; wire the UI/controller and local support launcher with real timed failures, 401/403, stale auth, browser teardown, host/Internet separation and accessibility. Headless VPS vs remote desktop topology must be verified.
- Backup/restore rollback safety and Kraken nonce continuity; real installed cross-UID, canary, crash, native browser handoff D2, recovery and full-journey acceptance. D2 is still open and blocks installed release.

No claim of completed installed security or end-to-end offline diagnostic operation.
