# R2B-INTEGRATE-02-R2-R2-ADMISSION — protected one-use approval and worker custody candidate

**Status: SOURCE QUALIFICATION PASS + READ-ONLY INSTALLED PAM/LOGIND ATTESTATION PASS. Protected broker/agent/worker deployment and full provider-admission task remain OPEN.**

Based on accepted R2B custody ledger head `e0e2fb7e1fcaa9d404f61c87e2cce005d08f9892`.
The exact frozen D2, R2B, R3 and R1–R4 handoff source locks are
preserved. Work is isolated on branch
`qualification/tb-sec-key-01-r2b-admission`. No mainline merge.

## Terminal qualification receipts (2026-11-20 local / 2026-11-21 UTC)

**Consolidated source PASS:** [GitHub Actions #44864093095](https://github.com/vjmichael/TradeBot/actions/runs/44864093095),
job `136723770737`, unchanged accepted SHA
`faf512829c7ee6bd7811bb69a9c2fa246a612d69`.
[Source artifact #14691445622](https://github.com/vjmichael/TradeBot/actions/runs/44864093095/artifacts/14691445622),
downloaded archive SHA-256
`d7c50b9f655b89afabec4dfd036d5adb9ada870540915af6988e5d364914963d`,
independently checked against GitHub's digest. The source transcript shows
**51/51** new ledger, gateway, session and AF_UNIX receipt tests,
**71/71** prior managed-vault, reviewed-migration, local review and
HMAC-receipt tests, **9/9** original native handoff guards, and
**21/21** unchanged intent/refusal/adversarial socket tests:
**152/152 total PASS**. Pinned dashboard npm build and exact production
relay restoration PASS; no production-code mutation.

**Native OS/session PASS:** [GitHub Actions #44863739057](https://github.com/vjmichael/TradeBot/actions/runs/44863739057),
job `136722673714`, unchanged accepted SHA
`c4acb54aca2099025578a6296e7fbba2cf98268d`.
[Native artifact #14691156922](https://github.com/vjmichael/TradeBot/actions/runs/44863739057/artifacts/14691156922),
downloaded SHA-256
`3c50f6c0c1dc677ed19fbede943c310c1a080a7a4494b5541a40b20f88be3d22`,
independently matched GitHub's digest. Offline QEMU Debian 13 built from
the exact restored source, no guest NIC; original installed LightDM/PAM
Account Connections GUI **not replaced or bypassed**.
Native terminal markers:

| Installed native condition | Result |
|---|---|
| `R2B_NATIVE_ORIGINAL_GUI_LOGIND_PASS`: genuine operator GUI process maps through `sd_pid_get_session` to correct active LightDM seat | **PASS** |
| `R2B_NATIVE_NON_GUI_SAME_UID_REFUSAL_PASS`: another operator-UID process without owning the graphical PAM session is denied | **PASS** |
| `R2B_NATIVE_EXPIRED_PAM_SESSION_REFUSED_PASS`: original session refused after actual `loginctl terminate-session` | **PASS** |
| `R2B_NATIVE_READONLY_LOGIND_PASS`: complete same-source installed OS attestation | **PASS** |

The native source suite also passed **51/51**. The archive contains
actual original Account Connections screenshots, before/after LightDM
greeter captures, exact tree/source locks, empty `dpkg-audit.txt`,
package listing and terminal receipts. This **qualifies only the OS
session-attestation foundation**, not an installed one-use broker.

**Exact source lock:** D2, R2B, R3 production source, including
`credential_handoff.py` blob
`269bdc0b19d8a189496c7ca3b9369c158f85b0f6`,
remain unmodified. Admission modules on the source run:
`approval_ledger.py`, `operator_session.py`,
`broker_gateway.py`, `receipt_channel.py`, and their
source tests are locked in workflow `r2b-one-use-admission.yml`.
Native proof uses a separate workflow `r2b-native-logind-readonly.yml`
with the original installed UI and test-only read-only OS introspection.

### Failed acceptance history retained

- [#44861862894](https://github.com/vjmichael/TradeBot/actions/runs/44861862894) source failure: seven test errors, six due a
  new/refrozen refusal exception class mismatch and one incomplete
  mocked operator GID. Reconciled without weakening refusals.
- [#44861971909](https://github.com/vjmichael/TradeBot/actions/runs/44861971909) source PASS (37 admission regressions),
  [#44862196232](https://github.com/vjmichael/TradeBot/actions/runs/44862196232) source PASS (49 new, 150 total).
- [#44862419218](https://github.com/vjmichael/TradeBot/actions/runs/44862419218) native setup failed at an omitted source
  `PYTHONPATH`. [#44862482138](https://github.com/vjmichael/TradeBot/actions/runs/44862482138) was a rejected test-only
  replacement desktop probe: it did not survive the approved installed
  launcher. The test then switched to **read-only inspection of the real,
  unmodified Account Connections desktop**, not a permissive substitute.
- [#44863008254](https://github.com/vjmichael/TradeBot/actions/runs/44863008254) genuine GUI launched but the new
  `loginctl` parser refused Debian's property report; repaired to read
  complete installed logind output while requiring all security-critical
  properties exactly.
- [#44863417257](https://github.com/vjmichael/TradeBot/actions/runs/44863417257) proved the actual GUI session binding
  but then failed on a malformed negative-test inline Python `except`.
  The negative fixture was fixed and precompiled before QEMU.
- [#44863739057](https://github.com/vjmichael/TradeBot/actions/runs/44863739057) passed native 4/4 at one unchanged SHA;
  [#44864093095](https://github.com/vjmichael/TradeBot/actions/runs/44864093095) passed consolidated source 152/152.
  These CI runs were sequential, not stacked.

### Non-negotiable outstanding installation/release gates

**Full `TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION` is not closed.**
The current gateway is an internal candidate callable and has *no*
installed authenticated broker socket, systemd-owned approval database,
independent protected GUI agent, sealed GUI response transport,
independent fixed-destination worker service, root-provisioned separate
receipt key, or positive provider execution. Source tests inject fake
prompt/session behavior; that does **not** authenticate a real user's
approval. Native tests validate existing GUI logind identity only,
not authorization consumption or any provider action.

The next scoped task is
`TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R1` —
install and independently qualify the one-use broker under a distinct
protected service UID, prove a real current-session native approval
cannot be forged or replayed, and durably consume the approved
exact vault generation/policy/inventory **before** any supervised
fixed-provider worker attempt. After that, add independently verified
worker `SO_PEERCRED` and separate-key receipt custody through the
existing owner transaction, cancellation/revocation/recovery negatives,
and safe positive reviewed legacy migration on disposable ciphertext.
Actual provider sandbox/permission checking, Telegram/Grok policy,
R2C rollback/key recovery and financial Gate remain OPEN.
No real keys, provider network request, money, paid model calls or LIVE
activation were used or authorized in this qualification.

## What is implemented

### Durable one-use approval core

`approval_ledger.py` maintains **one protected SQLite ledger** and an
unforgeable random opaque request ID. The owner prepares an exact
`provider_test` context (approved role, authoritative current generation,
policy) or `legacy_migration` context (full sorted role inventory and
private ciphertext commitment). Preparation alone grants **nothing**.

The internal `review_with_native_prompt` transition requires a trusted
native prompt callback, checks the same authenticated operator session
before/after review, and accepts only literal `True`; decline, session
change, expiry, timeout, or race refuses. The later owner-only `consume`
rechecks exact context, current live session, boot and monotonic time and
commits the **consumed** state under `BEGIN IMMEDIATE` with
`PRAGMA synchronous=FULL` before a worker action. Duplicate use,
reboot, broker restart, negative time, context changes and lost session
all refuse; no failed or indeterminate request is automatically retried.
The approval database must live in a 0700 directory and its 0600 file
must be owned by its dedicated service UID, not operator or browser.

### Owner/operator gateway without caller-forged confirmation

`broker_gateway.py` adds a protected entrypoint contract. The vault owner
must present its actual kernel-authenticated Unix socket identity to
`prepare_from_owner`, `consume_from_owner` or `cancel_from_owner`.
Only an operator peer in its real active LightDM/PAM session may request
`review_from_operator`, and **the broker itself** invokes its fixed
native Tk prompt and re-attests the same session before recording approval.
The operator RPC has no `approved`, `destination` or `result` parameter.

The fixed native prompt can preview a Kraken READ-ONLY permission check
or a non-destructive legacy ciphertext inventory. Telegram and Grok remain
refused until their provider-specific recipient/identity and opt-in
contracts are installed; there is no hidden send or inference fallback.
The stored context is read from the protected ledger, not nominated by
the operator/HTML client. All positive `BrokerGateway` test callbacks
are synthetic fixtures; there is **no trusted socket listener or installed
native GUI broker agent yet**, so this gateway must not be deployed
or used to start provider tests.

### Kernel/PAM/LightDM identity attestation

`operator_session.py` requires actual Unix
`SO_PEERCRED` PID/UID/GID from the accepted socket (not request JSON).
It binds that PID through `libsystemd sd_pid_get_session` to its real PAM
session and independently queries `loginctl` for the credential operator
UID/name, `seat0`, `Service=lightdm`, `Type=x11`, local active,
`Class=user`, and unlocked session. Failure to query is refusal;
there is no browser-token or caller-supplied identity fallback.
`require_vault_owner_peer` separately demands the installed vault UID.

### Protected receive-only worker result transport

`receipt_channel.py` validates the worker process's *actual*
`SO_PEERCRED`, enforces an exact bounded ASCII JSON frame, newline and EOF,
rejects duplicate keys, embedded NUL, invalid schema, overlong data,
wrong worker UID and altered results. It then invokes the already
source-qualified role/attempt/generation/policy/nonce/time HMAC verifier
using a **separate independent worker receipt key**.

It returns one sanitized enum to the trusted vault owner; it does **not**
mint a credential, sign a receipt, run a provider adapter, accept a
caller-defined endpoint, or mutate the vault. Actual `OwnerHooks.finish`
remains the owner's existing encrypted SQLite transaction.

## Deliberately missing before install

The native callback seam above is **not a permitted RPC granting a positive
approval**. A test injecting `prompt=lambda:True` is a synthetic
unit-test seam, not evidence a real person approved anything.
To complete this task, install a protected broker service with
kernel-authenticated owner channel and a trusted per-session GUI agent
whose native confirmation response cannot be forged by another
same-UID process. Bind the **real** current PAM/logind session and
one-use reviewed request to the authoritative current vault state.
The broker must commit consumption before `OwnerHooks.begin`; the
worker must be a separately supervised fixed-destination process, with
independently provisioned signing key and authenticated peer/result
transport. Cancellation, logout, seat switch, restart, replay,
same-UID hostile caller, parallel requests and failed/unknown send must
refuse without execution, acceptance or automatic retry.

Existing `native_test_review.review()` only yields local UI intent;
it is **not** a signed approval or native broker. The current
vault RPC correctly exposes no positive provider test, worker-receipt
submission or migration path. Do not enable them based on these source tests.

Positive legacy migration remains blocked until actual installed one-use
operator approval binds exact private ciphertext/roles, and the vault
owner commits inside its existing `BEGIN IMMEDIATE` transaction. Actual
provider adapter permission proofs, network egress, Telegram destination
review and acknowledgement, optional Grok controls, resource limits,
R2C key recovery and financial Gate remain separate obligations.

## Acceptance policy

Source-locked Python tests cover durable one-use, expiry, restart/reboot,
context mismatch, stale operator session, native prompt decline/race,
SQLite ownership, real local AF_UNIX peer credentials, malformed and
misbound worker receipts and protected nonce/result semantics. Re-run
original 9 handoff guards, 21 adversarial handoff/refusal tests and
the approved R2B provider-custody regression suites.

**No native installed endorsement is claimed until** the broker/agent and
worker are actually integrated into the signed/installed Debian service
layout and the same source SHA passes genuine graphical PAM, network-off
synthetic provider transport, cancellation and recovery tests.
No real Kraken/Telegram/Grok key, paid request, money, trade or LIVE action
is permitted.
