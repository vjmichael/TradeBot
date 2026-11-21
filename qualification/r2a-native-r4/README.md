# TB-SEC-KEY-01-R2A-R1-D2-R3-NATIVE-R4 — installed adversarial intent, replay and principal isolation

**Status: SCOPED INSTALLED ACCEPTANCE PASS — native R2A handoff security qualified; broader product and financial gates OPEN.**

**Scope:** extend the already accepted R1/R2/R3 native Windows-independent Debian
credential-handoff contract with genuinely installed negative checks.
No production-code changes, mainline integration, real credentials, provider
network calls, trading, funds, or LIVE activation.


## Final exact-source acceptance — 2026-11-20 local / 2026-11-21 UTC

**[Successful GitHub Actions run #44856537336](https://github.com/vjmichael/TradeBot/actions/runs/44856537336)**, job `136699960753`, unchanged tested commit
`5d0a63a740a4974bec67e853d965cbf89ce49e60`. Independent downloadable
[receipt artifact #14688037296](https://github.com/vjmichael/TradeBot/actions/runs/44856537336/artifacts/14688037296)
has SHA-256 `8cc474b1bebb82bbd02bfc8c671b869283909ae2c448a2cbd4045505ff80786d`;
the downloaded ZIP bytes were independently SHA-256 checked and exactly matched
the GitHub artifact digest. Receipt includes the terminal source lock, package
inventory, 9 original guard test results, 21 intent/refusal/adversarial tests,
native markers and actual QEMU screenshots.

Frozen D2 source tree `a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b`,
R2B overlay `250310b3409dd4c0feffa33eba1fe74d8b4ca54a`, repaired
R2B R2 tree `12a3c46401d96d616de4f88fa8a42588275d22ae`,
and authoritative R3 native relay production tree
`89946baeef719d7369887cc7d89a8a4230c7180e` restored unchanged.
Production handoff blob `269bdc0b19d8a189496c7ca3b9369c158f85b0f6`
and original accepted R3 harness blob
`78f2e644340649104ae7ea31fd9589541b659bdb` were
reverified; R4 harness blob
`7451e502de346b503158b612f9c8943c0c44c2d3`
and four-case AF_UNIX source test blob
`7b1a64e864a5c95e76bbfceba53262d9991a640a`
were source-locked. Pinned npm dashboard build PASS.

| Exact-source and installed test | Outcome |
|---|---|
| Original handoff guard suite | **9/9 PASS** |
| R2 fixed-intent/refusal + R4 AF_UNIX regression suite | **21/21 PASS** |
| Installed malformed native URI refusal | **PASS** |
| Real Chromium consent, Tk confirmation and native cancellation | **PASS** |
| Actual Linux socket peer/directory ownership and cross-UID rejection | **PASS** |
| Malformed, oversized, trailing, partial/no-EOF IPC requests | **PASS** |
| Valid same-UID fixed intent requires real native Tk confirmation; Escape refuses | **PASS** |
| Old PAM session terminated, listener socket removed; stale socket request refused | **PASS** |
| Fresh distinct PAM login; current X cookie accepted, old saved X cookie refused | **PASS** |
| Root-owned tag/KEK custody and console X-cookie cross-principal denial | **PASS** |
| Distinct operator PAM login after all denials, no vault enrollment | **PASS** |
| Full offline R4 adversarial matrix `R4_NATIVE_ADVERSARIAL_PASS` | **PASS** |

All ten expected native markers were emitted once by the installed QEMU
harness and verified independently by the workflow. The actual Tk screenshot
shows the credential form still in PAPER-only mode: Kraken trading keys,
Telegram pairing and provider verification are not available. No synthetic
or real credentials were enrolled by R4, no privileges were escalated and no
LIVE authority was enabled.

### Security interpretation

The `OPEN\n` socket frame remains **reusable UI intent within the currently
authenticated console UID**. It is *not* a one-use, cryptographically
authenticated transaction and cannot grant enrollment, sign-out or
provider permission without the independent native confirmation and
fresh credential-operator PAM login. The R4 evidence establishes
cross-UID refusal, old socket removal, old Xauth cookie rejection and
native explicit confirmation, **not** global replay prevention for same-UID
requests across time or a general guarantee against arbitrary same-UID
malware. Such stronger threat models would need a separate contract.

The negative invalid-URI shell invocations are intentionally not treated
as positive native handoff acceptance; the real Chromium-to-Tk positive
path was independently exercised in the same VM. Source tests retain
the exact URI validation requirement.

### Remaining gates

The **R2A native handoff-security R1–R4 qualification group is closed**,
but this does **not** release Maj Vaj for production. Protected
provider-test adapters, owner one-use approval and authenticated receipt
custody, legacy stored-credential migration, recovery/KEK rotation,
independent positive internet/service-readiness witnesses and full
PAPER/financial Gate remain open under R2B/R2C and associated
integration work. There were no production-code changes, source
relaxations, mainline merges, real secrets, external provider calls,
orders, financial changes or paid resources in this R4 run.

## Preserved acceptance

- R1 graphical enrollment: [run 44849939959](https://github.com/vjmichael/TradeBot/actions/runs/44849939959), SHA `75baac46aaff9bb46255080793f5034feaf422d2`.
- R2 cancellation and owner revocation: [run 44852521589](https://github.com/vjmichael/TradeBot/actions/runs/44852521589), SHA `2a7eb1a2ca5f432ab52f2fa7681c606ae029bfc4`.
- R3 installed lock/switch/logout and credential lifecycle: [run 44855035644](https://github.com/vjmichael/TradeBot/actions/runs/44855035644), SHA `f4ec1d27ea7eb844afde9faa85e2cbcf4782af82`.
- Frozen D2 source and native source reconstruction unchanged; protected
  `credential_handoff.py` remains blob
  `269bdc0b19d8a189496c7ca3b9369c158f85b0f6`.

## R4 native adversarial matrix

The actual installed Debian 13 VM is built from the identical frozen
source tree, has no network interface, and runs LightDM/PAM, Chromium,
Tk and the protected encrypted vault. Positive UI entry uses the real
Chromium hyperlink, one-time browser external-protocol consent and
native confirmation. The script uses fixed, synthetic command-line
inputs and socket frames **only for negative tests or to prove that a
same-UID fixed-intent frame is not authentication**.

1. **Invalid desktop scheme:** invoke only malformed installed scheme
   requests. Require nonzero refusal, no credential operator, and no
   vault change.
2. **Actual browser baseline:** navigate a genuine Chromium consent
   into the protected Tk confirmation, cancel, retain original PAM
   console session and verify intended socket owner/mode.
3. **SO_PEERCRED/DAC isolation:** send the exact, otherwise valid
   `OPEN\n` frame from root and the dedicated operator, backend,
   native and dashboard-auth UIDs. No `QUEUED\n` ACK or modal.
4. **Adversarial stream framing:** same UID sends incomplete, appended,
   oversized, NUL/invalid, alternate-command and no-EOF frames.
   No valid ACK, no modal, no credential mutation.
5. **UI intent is not authorization:** a correctly framed same-UID
   request may produce `QUEUED\n` and the original native Tk dialog,
   but explicit Escape must prevent sign-out, privileged admission
   and vault changes.
6. **Session-owned stale IPC:** terminate the real old PAM console,
   verify the listener socket has retired, reject stale-frame replay.
7. **Xauthority replay:** perform a fresh, distinct PAM console login;
   prove the current X cookie works, the previous session's saved
   ephemeral cookie cannot open the new session's X display, and
   root still cannot ACK the fresh session-owned socket.
8. **Credential/cookie principal separation:** unauthorized installed
   UIDs cannot read root-owned tag/key custody or the console
   Xauthority cookie, and the console UID cannot read vault status.
9. **Independent operator admission:** after all denials, an actual
   fresh credential-operator PAM/LightDM login still opens the
   existing native Account Connections UI and exits without
   enrollment; vault stays empty.

`test_adversarial_intent.py` adds four genuine local AF_UNIX
source-level cases for half-open EOF, malformed-then-valid listener
continuity, socket inode replacement custody and exact URI denials.
The existing 9 original handoff guards and 17 R2 intent/refusal
tests must be re-qualified unchanged.

**Required markers:** `R4_INSTALLED_INVALID_URI_PASS`,
`R4_INSTALLED_BROWSER_BASELINE_PASS`,
`R4_INSTALLED_FOREIGN_PEER_PASS`,
`R4_INSTALLED_MALFORMED_IPC_PASS`,
`R4_INSTALLED_SAME_UID_REQUIRES_REVIEW_PASS`,
`R4_INSTALLED_RETIRED_IPC_PASS`,
`R4_INSTALLED_STALE_COOKIE_REFUSAL_PASS`,
`R4_INSTALLED_PRINCIPAL_ISOLATION_PASS`,
`R4_INSTALLED_OPERATOR_SEPARATION_PASS`, and
`R4_NATIVE_ADVERSARIAL_PASS`.

## Nonclaims and boundaries

A valid same-UID `OPEN\n` is a **reusable UI intent**, not an
anti-replay capability, ticket or one-use authentication. R4 must
not assert that a valid message cannot ever be repeated by that UID;
it must prove repeated intent cannot bypass the native
confirmation, PAM admission or credential security. A stale
logind session/socket and stale X cookie are separately invalidated.

No real credential key, logout-guard bypass, direct positive
credential helper launch, provider test connection, owner approval,
LIVE order, network egress, paid service or financial activation is
authorized. This is a bounded R2A handoff-security matrix; the
R2B provider/custody, R2C encrypted recovery and financial Gate
remain separate release blockers.
