# TB-SEC-KEY-01-R2A-R1-D2-R3-NATIVE-R3 — installed negative-session and credential lifecycle qualification

**Status: SCOPED INSTALLED ACCEPTANCE PASS at the unchanged qualification SHA; production release gate OPEN.**
Independent branch `qualification/tb-sec-key-01-r2a-native-r3` from existing R2
ledger head `50db4a515bcc57645a5bb875e8b45f43beb00956`.
Original positive installed acceptance R1 `75baac46aaff9bb46255080793f5034feaf422d2`
([run 44849939959](https://github.com/vjmichael/TradeBot/actions/runs/44849939959))
and installed cancellation/owner-revocation R2 `2a7eb1a2ca5f432ab52f2fa7681c606ae029bfc4`
([run 44852521589](https://github.com/vjmichael/TradeBot/actions/runs/44852521589))
remain unchanged.


## Terminal source-locked acceptance (2026-11-20 local / 2026-11-21 UTC)

- **Authoritative successful run:** [GitHub Actions #44855035644](https://github.com/vjmichael/TradeBot/actions/runs/44855035644), job `136695120403`, exact tested commit `f4ec1d27ea7eb844afde9faa85e2cbcf4782af82`.
- **Independently archived receipt:** [artifact #14687955533](https://github.com/vjmichael/TradeBot/actions/runs/44855035644/artifacts/14687955533), ZIP SHA-256 `b87c8541919947cc16618b0b64008bace9d3c1f437f6b742fa318d3143a058e2` (GitHub and downloaded archive verified equal).
- Locked R3 installed harness blob `78f2e644340649104ae7ea31fd9589541b659bdb`; frozen production credential handoff blob `269bdc0b19d8a189496c7ca3b9369c158f85b0f6`.
- Original frozen D2 tree `a9cd0f3392a573d3c91cc2d8464c4b8de5eefb0b` and reconstructed R2B/R3 production relay tree `89946baeef719d7369887cc7d89a8a4230c7180e` verified in the source-tree receipt.
- Original handoff guard tests **9/9 PASS**, fixed-intent + R2 refusal tests **17/17 PASS**, pinned npm dashboard rebuild PASS.
- Actual QEMU Debian 13 with no guest NIC, installed LightDM/PAM, Chromium, native Tk and protected encrypted PaperVault service completed **all eight required R3 markers** below without weakening or changing production source.

| Installed condition | Terminal evidence |
|---|---|
| Real `loginctl lock-session` during a protected native dialog | `R3_INSTALLED_LOCK_REFUSAL_PASS` |
| Real LightDM `dm-tool switch-to-greeter`, original console becomes inactive | `R3_INSTALLED_SEAT_SWITCH_PASS` |
| Actual PAM/logind owner-session logout during open native dialog | `R3_INSTALLED_LOGOUT_REFUSAL_PASS` |
| Masked, confirmed but unsaved fields; graphical End session | `R3_INSTALLED_UNSAVED_EXIT_PASS` |
| Masked unsaved fields; abrupt operator-session termination | `R3_INSTALLED_UNSAVED_LOGOUT_PASS` |
| Real Tk paired Save; both `stored_unverified` after systemd vault restart | `R3_INSTALLED_PERSISTENCE_RESTART_PASS` |
| Real Tk Remove; both empty after restart and graphical exit | `R3_INSTALLED_GUI_REMOVAL_PASS` |
| Full staged offline scenario | `R3_NATIVE_INSTALLED_PASS` |

The status-only PAPER API remained `connections_verified=false` and
`deployment_ready=false`. All values were synthetic and never verified
against a venue, chat, or paid provider.

### Receipt-grounded retry history

1. [#44853516075](https://github.com/vjmichael/TradeBot/actions/runs/44853516075), SHA `a81dc7e`: session lock **PASS**; fixture attempted `dm-tool switch-to-greeter` from a serial-launched process lacking `XDG_SEAT_PATH` and failed. No Maj Vaj production defect demonstrated.
2. [#44854160149](https://github.com/vjmichael/TradeBot/actions/runs/44854160149), SHA `07d6923`: session lock and genuine seat switch **PASS** after seat path was supplied; next login fixture incorrectly required the old `/run/lightdm/root/:0` X cookie even though the actual LightDM greeter moved to another display. Screenshot and `greeter-readiness.txt` established the mismatch.
3. [#44855035644](https://github.com/vjmichael/TradeBot/actions/runs/44855035644), SHA `f4ec1d2`: the fixture detects the active greeter's actual X `DISPLAY` from its system process and validates its corresponding root-owned cookie, then all native and source checks **PASS**. This is a fixture correction, not an authentication bypass.

These are **three successive, non-overlapping runs**. The two failed
historical receipts are retained. No R1/R2 acceptance SHA, policy guard,
source tree, production code, mainline or financial authorization was changed.

### Remaining parent release obligations

R3 closes the requested native session-lock, seat-switch, logout,
credential-entry interruption and synthetic persistence/removal group,
**not the entire Maj Vaj security release**. Additional installed
adversarial malformed/unauthorized desktop intent and cookie-replay
acceptance, R2B one-use protected provider approval/receipt custody,
provider permission tests, migration/KEK rollback-safe lifecycle,
truthful positive service witnesses, and final PAPER/financial Gate remain OPEN.
No real keys, external connections, orders, funds, paid calls or LIVE trading
authority were exercised or enabled.

## R3 candidate

The new `harness.py` uses the **same unmodified restored production tree**
and genuine offline QEMU Debian 13 guest as R1/R2, installed LightDM,
logind, PAM, Chromium, native Tk and actual protected PaperVault service.
All account values are disposable synthetic strings, no guest NIC.
Serial root is limited to disposable fixture lifecycle/control commands,
not positive browser handoff or credential enrollment.

- **Session lock:** actually open the Chromium-originated native confirmation
  through one-time external-protocol approval. Issue `loginctl lock-session`
  to the original PAM session while the modal is displayed. If still visible,
  send user Return to challenge the expired review. The modal must close,
  there must be no credential-operator login and no vault mutation.
- **Seat switch:** open a new independent console PAM/Chromium native
  confirmation. Use installed LightDM `dm-tool switch-to-greeter` from
  the console UID. Verify original console no longer owns the active seat,
  no credential operator and vault untouched. No direct handler/URI call.
- **Logout:** open a third native confirmation and terminate its actual
  logind owner session. Native modal must disappear and LightDM return
  to the greeter, without credential operator admission.
- **Unsaved input interruption:** independently authenticate as the
  operator via the installed greeter, enter the two masked synthetic
  fields and check explicit consent, then use the actual GUI "End account
  session" button *without saving*. Vault must stay `not_configured`.
  Repeat with an unexpected PAM operator-session termination mid-entry.
- **Persistence/restart/removal:** fresh independent operator login,
  enter a synthetic Kraken read-only credential pair using the actual
  Tk fields, checkbox and Save button; verify both
  `stored_unverified` over genuine protected systemd-vault restart,
  then Remove locally through Tk and verify `not_configured` after
  another restart and ordinary graphical logout. PAPER
  `connections_verified` and `deployment_ready` must remain false.

## Required terminal markers

`R3_INSTALLED_LOCK_REFUSAL_PASS`,
`R3_INSTALLED_SEAT_SWITCH_PASS`,
`R3_INSTALLED_LOGOUT_REFUSAL_PASS`,
`R3_INSTALLED_UNSAVED_EXIT_PASS`,
`R3_INSTALLED_UNSAVED_LOGOUT_PASS`,
`R3_INSTALLED_PERSISTENCE_RESTART_PASS`,
`R3_INSTALLED_GUI_REMOVAL_PASS`,
`R3_NATIVE_INSTALLED_PASS`.

Check all eight in one unchanged-SHA workflow. Any failure remains
a **failed scoped acceptance** until reconciled. Screenshots from the
genuine QEMU display and nonsecret category receipts are mandatory.
Source SHA and new test SHA must be checked before VM provisioning.
Do not use the R1 positive receipt as a substitute for these distinct
negative and restart cases.

## Scope boundary

No production code, mainline, approved R1/R2 tests, encrypted secrets,
live provider probes, funding, trading or automatic LIVE activation are
changed. R2B protected provider verification, migration/approval receipts,
installed health witnesses, R2C key recovery/rollback and overall financial
Gate remain separate. This task can close only the testable R2A installed
session/credential lifecycle matrix, not release authorization.
