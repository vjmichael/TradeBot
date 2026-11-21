# TB-SEC-KEY-01-R2A-R1-D2-R3-NATIVE-R2 — native refusal and recovery qualification

**SCOPED INSTALLED ACCEPTANCE PASS; broader security release gate OPEN.**

- Branch: `qualification/tb-sec-key-01-r2a-native-negative`.
- **Accepted unchanged source/test SHA:** `2a7eb1a2ca5f432ab52f2fa7681c606ae029bfc4`.
- [Successful installed CI 44852521589](https://github.com/vjmichael/TradeBot/actions/runs/44852521589), job `136686965164`.
- [Artifact 14685294832](https://github.com/vjmichael/TradeBot/actions/runs/44852521589/artifacts/14685294832), reported GitHub SHA-256 `82da9750f88941241776c0fac9ef6bac68f25b9abbabc13995508cad991279b1`.
- Original R1 positive installed qualification remains accepted at `75baac46aaff9bb46255080793f5034feaf422d2`, [run 44849939959](https://github.com/vjmichael/TradeBot/actions/runs/44849939959) / artifact 14684453508.

## Exact-source qualification

The R2 workflow reconstructed the frozen D2/R2B/R3 production source and
confirmed credential handoff blob `269bdc0b19d8a189496c7ca3b9369c158f85b0f6`.
The independently scoped new installed harness was locked at blob
`3d7a3b9c795b2a9bb22984333420b4d5d26d7c28`;
`test_handoff_refusals.py` at
`d2fc1b43c6db299947f2aac999c86d6e230fd566`.
Pinned Node dashboard build, source restoration, original credential-handoff
guards **9/9 PASS**, and synthetic fixed-intent / R2A refusal tests **17/17 PASS**
were all terminal successes.

The original production source, D2 restored source, installed fixture,
R1 positive harness and security principal policies were **not modified**
for R2 negative qualification.

## Actual Debian 13 installed observations

Offline QEMU guest with no NIC; real LightDM/PAM console principal, Chromium
with normal browser protocol consent, original guard and genuine Tk native
confirmation. This does **not** bypass the native protocol handler or
directly start its secure window.

1. The existing browser account link opened the native confirmation.
   Sending the real Tk `Escape` dismissed the dialog; the original PAM
   session remained active, Chromium was still visible, credential-operator
   login never began and all vault slots remained `not_configured`.
   Terminal marker: `R2A_NATIVE_NEGATIVE_CANCEL_PASS`.
2. The same browser subsequently initiated a second protected confirmation
   through its own link and one-time external-protocol consent. The fixture
   administrator then terminated the actual original logind console
   session *while this confirmation window was open*. The modal exited
   with its session, LightDM returned to its greeter, there was no
   credential-operator login and no vault state mutation.
   Terminal marker: `R2A_NATIVE_NEGATIVE_REVOKE_PASS`.
3. The actual systemd vault service was restarted; its status socket
   reappeared and the same empty credential status was observed. A fresh
   console PAM login created a distinct session, Chromium launched, and no
   independent credential session was active.
   Terminal marker: `R2A_NATIVE_NEGATIVE_RECOVERY_PASS`.

All three markers were independently checked in the successful workflow.
They are scoped installed proofs, **not** provider authentication or
authorization to trade.

## Receipt-grounded repair history

The first independent run [44851985905](https://github.com/vjmichael/TradeBot/actions/runs/44851985905)
accepted the unit suite and true installed Escape cancellation but then
failed before revocation: the second automation navigation toggled an
already expanded Settings → Account connections panel closed. The screenshot
`10-second-browser-link.png` proved the secure-link was not visible.
Only the test's second navigation was corrected (commit `7ca4e69`);
the next workflow source lock produced the final pass in 44852521589.
This failure was not evidence of a production security failure.

## Still OPEN / not proved by R2

- True installed lock, seat switch and unexpected logout while native review
  is open, including cookie retirement and event-race refusal, are separate
  negative acceptance cases; synthetic guards alone do not close these.
- Actual cancellation during an unsaved credential entry, paired
  replacement/removal, encrypted persisted-credential restart, legacy
  credential migration review and rollback/nonce-safe key recovery remain
  release obligations.
- Real provider test-connection adapters, one-use OS-authenticated operator
  approvals and receipt-source custody, Kraken permissions, distinct
  Telegram identities/chats, optional Grok and real service/internet health
  witnesses remain unqualified or disabled.
- Production release and financial Gate acceptance are not complete.
  No real secrets, outbound provider calls, orders, funds, paid resources,
  main merge or LIVE/automatic activation were involved.

The next independent task should use this accepted R2 source lock, preserve
R1 positive and R2 negative receipts, and qualify the remaining installed
lock/seat-switch/logout + credential-entry interruption and restart cases
before declaring the parent R2A release contract complete.
