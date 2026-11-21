# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2 — custody, migration and receipt-provenance continuation

**STATUS: QUALIFICATION CANDIDATE. Full R2B closeout remains OPEN until true installed PAM-native one-use review admission and protected provider worker are accepted.**

## Preserved production source and branch policy

Independent branch `qualification/tb-sec-key-01-r2b-closeout-custody`, based on the R4
ledger head `eb752b91687c8c727b9bcee108e39c4773cb151b`. Restore the unchanged
R2B/R3 production source through `qualification/r2b-integrate-02-r2-r3/restore-r3.sh`
and require original credential handoff blob
`269bdc0b19d8a189496c7ca3b9369c158f85b0f6`.
R1 positive installed enrollment, R2 cancellation, R3 lifecycle and R4 adversarial
security acceptance remain independent source-locked successes. No production source,
authoritative owner socket, frozen D2 tree, mainline, provider access, funds, LIVE
trading or approved security baseline is modified.

## New installed Debian 13 custody matrix

`native_harness.py` inherits the screenshot-grounded actual LightDM/PAM graphical
operator controls from accepted R3. The offline QEMU guest has no network interface.
Only disposable synthetic Kraken read-only values are entered through actual
protected Tk controls. The native suite requires:

1. Root-only 0700 credential directory, separate 0600 venue encryption and
   provider HMAC tag keys, unit-owned systemd `LoadCredential` entries,
   `PrivateNetwork=yes`, `AF_UNIX` restriction and dedicated `maj-vaj-vault`.
   Console, operator, dashboard and native service UIDs cannot read key files.
2. Actual independent operator PAM login and real Tk encrypted paired Save,
   with both slots `stored_unverified` and no provider-verified authority.
3. Operator's legacy enrollment Unix socket **refuses** unreviewed provider
   testing, approval, migration and receipt operations. These routes are
   intentionally disabled until a full protected broker is installed.
4. Real systemd vault restart preserves both protected synthetic slots.
5. Missing provider-tag key **refuses service startup**, then restoring the
   exact original key restores the same data.
6. A distinct synthetic random tag key must not adopt existing protected
   role bindings; restoring the original reinstates the same data.
7. A distinct random Fernet venue encryption key must not decrypt or
   silently reset the prior vault; restoration recovers the sealed pair.
8. The PAPER API still reports `connections_verified=false` and
   `deployment_ready=false` after recovery and an actual GUI logout.
9. A fixture-only populated *legacy* vault (the original encrypted rows
   with metadata deliberately removed) must refuse automatic startup,
   preserving the two stored ciphertext rows. This **does not** constitute
   reviewed approval or positive migration.

No real provider key, internet request, model inference, test message or
financial action is ever used. The fixture restores the original key material
between negative runs and leaves an unusable synthetic legacy state only in
the disposable VM.

## Reviewed migration source qualification

The unchanged `reviewed_migration.py` and its original 15 synthetic
SQLite/Fernet acceptance cases are executed in the exact reconstructed source.
The HMAC fingerprint binds exact ciphertext/role inventory, requires explicit
one-use externally authenticated `consume` authority, never exports plaintext,
preserves ciphertext on success, and refuses changed, partial or ambiguous
inventories. **Production `consume` is not backed by an installed native
operator grant yet.** Unit tests use a fixture callback and do not prove this
missing admission. The native fail-closed legacy test above independently
verifies that the current installed service does not migrate without review.

## Receive-only worker receipt provenance

`worker_receipt_verifier.py` accepts only a fully schema-checked,
domain-separated HMAC-SHA256 receipt bound to exact role, fixed adapter
identity, credential generation, attempt ID, policy, nonce, start time,
bounded observation time and a role-specific sanitized result. It requires
a **separate** protected worker signing key (neither vault Fernet key nor
provider-tag key) and has no signing endpoint, dynamic URL or provider API.
`test_worker_receipt_verifier.py` uses a synthetic key and actual
SQLite `OwnerHooks.begin()/finish()` transaction to prove that a
validated result can be committed once and a duplicate cannot be committed.
It also refuses modified, misbound, stale, wrong-worker and malformed receipts.

**These source tests do not authenticate a real worker principal.** Real
`SO_PEERCRED`, protected fixed-destination adapter, root-provisioned
receipt key custody, authenticated local PAM GUI review, durable one-use
operator admission and real provider receipt provenance **remain
installation/release blockers**. No positive verification method is
added to the existing vault RPC. Do not call `OwnerHooks.finish` from
an untrusted client or use the synthetic fixture signing key in production.

## Other reconcilable evidence

Run the prior isolated native-review, managed-vault, migration and trusted
local OS readiness witness suites against the frozen restored source and
original qualification fixtures. Health witness tests are only bounded
local Unix peer observation, not a positive VPS/internet readiness witness.

## Closeout rule and next task

Only mark **this scoped custody qualification** PASS after one terminal
unchanged-source CI with all expected source tests, actual installed key
loss/wrong-key recovery receipts and archived nonsecret screenshots.

**Do not mark full `TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2` closed** until
native PAM-bound, durable one-use operator approval and fixed worker/receipt
transport are actually implemented and accepted with real provider
sandbox/permission proofs and migration/KEK rollback checks. If those remain
unimplemented, the next focused engineering task is
`TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION`.
