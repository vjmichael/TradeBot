# TB-SEC-KEY-01-R2B-INTEGRATE-02 — Transactional owner hooks and service-status strip
2026-11-20. **Standalone qualification candidate; not installed.** Frozen D2 source tree `a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b` remains untouched. No real credentials, network calls, money, trading, or LIVE authorization.

## Implemented owner-side integration foundation
`vault_owner_hooks.py` binds role metadata to the **actual encrypted credential rows** held under the existing `PaperVault` SQLite transaction boundary. Encrypted, domain-separated state lives in a single explicit-migration row, not a second vault service. Role mapping currently covers the existing Kraken **read** pair, Telegram Desk, Telegram Recovery, and optional Grok. Trading credentials intentionally remain unsupported until the independently reviewed trading-pair migration and full safety release.

- Requires the existing SQLite `BEGIN IMMEDIATE` transaction, existing Fernet cipher and a **distinct** at-least-32-byte HMAC key. HMAC commitments to exact encrypted pair bytes and vault-decrypted key identity stay within the owner; no plaintext read/export operation or browser credential access exists here.
- Replacing or removing a role changes current generation/verification atomically when invoked **inside the same transaction as the existing vault mutation**, including collision refusal across roles. A separate write cannot be considered atomic. Existing read-only status boundary is unchanged.
- Verification attempt is reserved and durably committed **before** any provider adapter is invoked; one pending attempt per version, review policy fingerprint, opaque attempt ID and role. Kraken high-water nonces remain stored after removal/replacement. Unknown result codes are refused. Late verification results after replacement/disable/role or policy changes cannot restore a trusted status. Results expire within five minutes and previous successful results clear on new attempt/failure.
- Interrupted Kraken/Grok inspections become unverified; interrupted Telegram tests become **delivery unknown** rather than automatically resending a potentially delivered message. This module never sends messages, invokes APIs, authorizes trading or restores LIVE.
- Denies backward clock movement within recorded state, ciphertext tampering and implicit recreation of missing metadata. Privileged restore of a valid older encrypted database remains unmitigated. Actual secure time, backup rollback/nonce continuity, service principal isolation, authenticated operator authorization, hardened outbound adapter, receipt provenance and OS egress remain release gates.

`test_vault_owner_hooks.py`: **32/32 local Python unittest passed** with temporary SQLite transactions, actual Fernet encryption, separate tag key and synthetic data; tests cover atomic write/rollback, expiration and non-resurrection, unique roles, nonce retention, crash recovery, stale reply, policy changes, refusal of missing/corrupt state and no plaintext canary in the database. No installed PaperVault end-to-end acceptance is claimed. No new API or IPC was added.

## Explicit UI refinement: icon and words together
`service_status_strip.cjs` + `service_status_strip.css` render one persistent, compact strip with icon, **Online** and **Services available** **adjacent in the same element**. They do not occupy opposite sides of the footer. A keyboard-accessible Details control opens a caller-defined, separately authenticated detail view when wired. Plain labels are always available independently of color or icon.

The UI renders **Services available** only if the backend supplies a **fresh** validated, authenticated view of each required service: dashboard, vault, risk gate, reconciliation and market data, plus online host evidence. It shows **Services need attention**, **Checking services** or **Server unreachable** otherwise. The UI does not infer that all providers are available merely because a Wi-Fi symbol is shown. The client uses monotonic observation age; the server must translate trusted provider and service observations into a normalized snapshot, never compare unsynchronized raw client/server timestamps.

`test_service_status_strip.cjs`: **24/24 local Node.js UI assertions passed**. Checked adjacent strings/icon, accessible status, working Details callback, successful/unreachable/offline/DNS/TLS states, missing/failing services, stale/future/unknown reports and refusal to display green on a failed network. No screenshots or GUI-installed acceptance is claimed; no actual network collection is yet connected.

## Deferred to installed integration and acceptance
1. **R2B-INTEGRATE-02-R1:** reviewed change to the actual `PaperVault` migration/enroll/remove service and systemd credential custody, protected authenticated operator test action, transactional role mapping, real receipt provenance and current policy binding, safe time and restore anti-rollback. Do not expose generic secret reads or allow commands from dashboard/model.
2. **R2B-DX-NET / R2B-INTEGRATE-03:** authenticated status-only backend read of service health and existing network observations (reuse current collector, no paid/model calls, no new always-on daemon), bounded fresh status and error/last-updated information, accessible persistent frontend integration. Avoid claiming global internet failure from one provider timeout. Add independent Recovery Bot missing-heartbeat classification in TB-09. `ServiceStrip` must default to unknown until wired; a fixture is never a production health claim.
3. Reconcile D2 browser-to-native enrollment separately before installed provider acceptance. Full process/role isolation, secret canaries, concurrent mutation, crash and reboot, real GUI flow, status strip client-server separation and synthetic provider suite remain pending.

## Reproducibility and exact source evidence
- Run `python -m unittest -q test_vault_owner_hooks.py` with Python 3.13 and `cryptography` 46.0.4: **32 passed, 0 failed/skipped**.
- Run `node test_service_status_strip.cjs` with Node 22.16.0: **24 assertions passed**.
- Published files independently re-fetched and matched upload bytes. Blob IDs:
  - `vault_owner_hooks.py` — `fa4651d30732d6d0e5ed9d4c2b920c7a49d2762a`
  - `test_vault_owner_hooks.py` — `b994cc90d19f195bfb5e2d1a2d7586be55886e58`
  - `service_status_strip.cjs` — `5cf42135a2145c947df05b8c4df7b3ddfdee3f06`
  - `service_status_strip.css` — `4af7cb14c6b534f2d375614b1b0cd26c7f9a066b`
  - `test_service_status_strip.cjs` — `a957370d9611fa77b324aee2e63724872913f3c6`

No CI dispatch, production source modification, main merge, paid provider calls or installation. One group at a time: keep D2 security acceptance unresolved; advance only protected owner integration and network-health wiring within their existing tasks.
