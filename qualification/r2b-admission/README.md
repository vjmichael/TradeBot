# R2B-INTEGRATE-02-R2-R2-ADMISSION — protected one-use approval and worker custody candidate

**Status: candidate implementation, NOT installed or authorized to run provider tests. Full task remains OPEN.**

Based on accepted R2B custody ledger head `e0e2fb7e1fcaa9d404f61c87e2cce005d08f9892`.
The exact frozen D2, R2B, R3 and R1–R4 handoff source locks are
preserved. Work is isolated on branch
`qualification/tb-sec-key-01-r2b-admission`. No mainline merge.

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
