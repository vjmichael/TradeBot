# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2 — custody, migration and receipt-provenance continuation

**STATUS: SCOPED INSTALLED VAULT-CUSTODY / MIGRATION-REFUSAL PASS. Full R2B provider-admission and migration-review closeout remains OPEN.**


## Terminal same-SHA qualification result

**[GitHub Actions run #44858741408](https://github.com/vjmichael/TradeBot/actions/runs/44858741408) — SUCCESS**, job `136704574576`, exact tested commit
`cdf2bec8c1fb49ac8edb0b679cb281e67883de5a`. Downloaded
[receipt artifact #14687934914](https://github.com/vjmichael/TradeBot/actions/runs/44858741408/artifacts/14687934914)
was independently verified against GitHub's SHA-256 digest
`bda02512fd67759dbb1f7e7f730e7a7d90798b7725743abb9382b28b671574f4`.
The archive contains source/tree locks, package and dpkg audit, original guard
and new source test transcripts, ten native markers and actual protected
graphical operator screenshots. The `dpkg-audit.txt` receipt is empty.

The source reconstruction retained frozen D2 tree
`a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b`,
R2B overlay `250310b3409dd4c0feffa33eba1fe74d8b4ca54a`,
R2 repaired tree `12a3c46401d96d616de4f88fa8a42588275d22ae`,
and R3 relay tree `89946baeef719d7369887cc7d89a8a4230c7180e`.
Existing production handoff blob `269bdc0b19d8a189496c7ca3b9369c158f85b0f6`
remains unchanged. Independently locked source inputs:
`native_harness.py` `ef29f3ffc11fdda7dac039bfc22aed86eed6ba97`;
`worker_receipt_verifier.py` `6d3dd869086febb21b2c1f8a50cd1fcc8d62e0dc`;
`test_worker_receipt_verifier.py` `10056e511d899c3858dcabcf260165cfca79e4a4`;
original reviewed migration module
`62ca8c18dfc9d26514fc608fb87ea9e5e30d2e53`.
No positive provider RPC was added.

| Verification | Accepted receipt |
|---|---|
| Reconstructed managed vault / native review / reviewed migration / HMAC worker verifier | **71/71 source tests PASS** |
| OS-local, peer-validated bounded readiness witness | **16/16 source tests PASS** |
| Original native handoff security guards | **9/9 source tests PASS** |
| Intent, cancellation, revocation and AF_UNIX adversarial source regressions | **21/21 source tests PASS** |
| **Total independent source checks** | **117/117 PASS** |
| Genuine Debian 13 offline QEMU, root-protected separate systemd keys and service boundary | `R2B_INSTALLED_SYSTEMD_KEY_CUSTODY_PASS` |
| Fresh PAM credential-operator Tk graphical synthetic paired Save | `R2B_INSTALLED_SYNTHETIC_SAVE_PASS` |
| No unreviewed provider/migration/receipt RPC admitted | `R2B_INSTALLED_UNAPPROVED_PROVIDER_REFUSAL_PASS` |
| Protected vault service restart retains original sealed pair | `R2B_INSTALLED_PERSISTENCE_PASS` |
| Missing provider-tag key refused, original key restores | `R2B_INSTALLED_MISSING_TAG_KEY_REFUSAL_PASS` |
| Different random provider tag key refused, original key restores | `R2B_INSTALLED_WRONG_TAG_KEY_REFUSAL_PASS` |
| Different random Fernet venue key refused, original key restores | `R2B_INSTALLED_WRONG_ENCRYPTION_KEY_REFUSAL_PASS` |
| PAPER API stays verification-disabled after restart and graphical exit | `R2B_INSTALLED_PAPER_AUTHORITY_REFUSAL_PASS` |
| Artificial populated legacy state refuses unreviewed startup, ciphertext rows survive | `R2B_INSTALLED_UNREVIEWED_LEGACY_REFUSAL_PASS` |
| Consolidated installed fixture | `R2B_NATIVE_KEY_CUSTODY_PASS` |

All ten native markers were emitted by the actual offline Debian guest and
checked again in the workflow. The tested source never exported credential
plaintext, submitted an external provider test or enabled financial authority.
The synthetic legacy-state conversion was destructive only to the *ephemeral
test VM's* metadata, not any owned or production database.

### Receipt-grounded failed attempts

- [Run #44857991016](https://github.com/vjmichael/TradeBot/actions/runs/44857991016) passed source tests and VM boot, but assumed one textual
  representation of `systemctl show LoadCredential` that Debian 13 did
  not provide. This was a fixture assertion, not a production vault defect.
- [Run #44858273027](https://github.com/vjmichael/TradeBot/actions/runs/44858273027) passed source tests and VM boot, but `systemctl cat` entered
  its pager under the serial harness. It timed out before synthetic save.
- [Run #44858741408](https://github.com/vjmichael/TradeBot/actions/runs/44858741408) verified the exact deployed `LoadCredential` unit lines
  and live systemd credential mount using a **noninteractive** check, then
  completed all native, source and artifact checks. The only changes were
  to the independent acceptance harness. These three runs were sequential;
  no CI was stacked.

### Mandatory unfinished release work

**Native one-use provider-test authority is not implemented or accepted.**
The existing operator graphical review produces local UI intent only, not a
server-verifiable one-use admission. The provider test socket must remain
closed. Required next work: authenticate the dedicated operator's real
PAM/logind session with independent broker and kernel peer identity, bind
role/generation/policy and immutable preview, durably consume a single
explicit approval before adapter execution, and refuse replay/cancel,
seat switch, logout, restart and stale role. Do not treat a browser token
or a caller-supplied `reviewed=True` as valid.

**Positive reviewed migration remains uninstalled.** The 15 source-level
migration tests inject a synthetic `consume` callback. Their pass proves
transaction/inventory behavior, not real operator approval. Populated
legacy stores continue to refuse unattended activation until the same
installed one-use authority explicitly binds the original ciphertext
inventory and the actual owner vault executes the migration under
`BEGIN IMMEDIATE` without rewriting ciphertext.

**Worker receipt provenance remains a source-verified building block.**
The receive-only HMAC verifier rejects tampering, unknown workers,
misbound attempt/generation/nonce/policy and stale results; tests use
synthetic signing keys. A truly independent installed fixed-destination
provider worker with root-provisioned *separate* receipt key, verified
`SO_PEERCRED` and authenticated fixed result transport is not yet present.
Provider permission verification and R2C rollback/nonce-safe recovery
remain separate release gates. No work here authorizes Kraken/Telegram/Grok
requests, funds, orders, paid model calls or LIVE trading.

The next task is `TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION`:
implement/qualify the missing native one-use approval and protected
worker receipt custody, then positively qualify reviewed migration and
fixed-provider sandbox testing without weakening financial protections.

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
