# TB-SEC-KEY-01-R2A-R1-D2-R3-NATIVE-R4 — installed adversarial intent, replay and principal isolation

**Status: staged, NOT accepted until same-SHA native run completes.**

**Scope:** extend the already accepted R1/R2/R3 native Windows-independent Debian
credential-handoff contract with genuinely installed negative checks.
No production-code changes, mainline integration, real credentials, provider
network calls, trading, funds, or LIVE activation.

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
