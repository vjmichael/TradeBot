# TB-SEC-KEY-01-R2A-R1-D2-R3-NATIVE-R3 — installed negative-session and credential lifecycle qualification

**Status: candidate staged; no acceptance claimed until terminal unchanged-SHA CI.**
Independent branch `qualification/tb-sec-key-01-r2a-native-r3` from existing R2
ledger head `50db4a515bcc57645a5bb875e8b45f43beb00956`.
Original positive installed acceptance R1 `75baac46aaff9bb46255080793f5034feaf422d2`
([run 44849939959](https://github.com/vjmichael/TradeBot/actions/runs/44849939959))
and installed cancellation/owner-revocation R2 `2a7eb1a2ca5f432ab52f2fa7681c606ae029bfc4`
([run 44852521589](https://github.com/vjmichael/TradeBot/actions/runs/44852521589))
remain unchanged.

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
