# TB-SEC-KEY-01-R2A-R1-D2-R3-NATIVE-R2 — negative native handoff acceptance

**Status: PREPARED, NOT QUALIFIED.** Separate qualification branch
`qualification/tb-sec-key-01-r2a-native-negative`, forked from the passing
R1 positive-acceptance commit `75baac46aaff9bb46255080793f5034feaf422d2`.
There is no production-code change, main-branch merge, new CI run,
real credential, external provider call, approval, or LIVE trading authority.

## Accepted prerequisite (do not inflate its scope)

The original installed positive acceptance run
[44849939959](https://github.com/vjmichael/TradeBot/actions/runs/44849939959)
completed **successfully** at unchanged SHA `75baac46aaff9bb46255080793f5034feaf422d2`.
Job `136678641050` independently recorded:

1. Exact frozen D2/R2B/native-repair reconstruction; original handoff tests
   and synthetic intent tests pass.
2. Genuine disposable Debian 13 QEMU environment, non-networked guest,
   protected vault and real LightDM/PAM console login.
3. Actual Chromium browser click, external-app consent and native Tk handoff
   confirmation (not direct helper invocation).
4. Session termination, return to greeter, no operator autologin.
5. New, distinct PAM credential-operator login; installed Account connections
   window and masked synthetic paired Kraken read-only enrollment.
6. Vault state `stored_unverified` for both read-only slots; status-only
   PAPER dashboard remains `connections_verified=false` and
   `deployment_ready=false`.
7. `R2B_NATIVE_INSTALLED_PASS` and artifact
   [14684453508](https://github.com/vjmichael/TradeBot/actions/runs/44849939959/artifacts/14684453508).

This positive path **does not** prove cancellation, lock/switch/logout race
refusals, restart recovery, legacy migration, genuine provider authorization,
independently witnessed positive service readiness or R2C key rotation.

## Independent source-level refusal candidate

`test_handoff_refusals.py` adds nine synthetic negative/positive
regressions against the exact repaired `credential_handoff.handoff()`
interface. It covers explicit cancel, dismissal/non-Boolean truthiness,
review exception, session identity switch, Xauthority-cookie change,
pre-review and post-review logind event revocation, invalid fixed URI
arguments and origin-session-only termination on valid confirmation.
Each negative test asserts that the termination callback was never called.

To qualify using the separately restored source lock:

```bash
export PYTHONPATH="$RUNNER_TEMP/r2b-r3:$GITHUB_WORKSPACE/qualification/r2a-native-negative"
python -m unittest -v test_handoff_refusals
```

These tests are **not yet run on the restored checkout**. Do not report them
as a pass until the authoritative source-locked receipt exists.

## Independent installed cancellation candidate

`harness.py` is derived from the already passing QEMU native harness,
but it stops after the actual Chromium-to-Tk modal opens. It sends an
actual graphical Escape to cancel and checks: modal closes, original PAM
console session remains active, the actual Chromium window remains, no
credential-operator session is started, and all vault statuses stay exactly
unchanged and `not_configured`.

The native harness must be run against the **same installed non-diagnostic
fixture and immutable R3 source reconstruction** as the positive run, on a
fresh disposable QEMU guest. Do not treat screenshots, mocks, a synthetic
`xdg-open` invocation, or a direct helper start as substitutes for this path.
The sole expected terminal marker is `R2A_NATIVE_NEGATIVE_CANCEL_PASS`.

Further independent installed cases required before full R2A closeout:
lock during prompt, seat/switch/logout during prompt, stale-cookie
revocation, malformed/unauthorized intent, cancellation before vault save,
post-save restart and local disconnect. Each must be independently
observed with the service and principal boundaries preserved.

## Hard boundaries

- This branch has **no workflow trigger** for these new files. Do not
  stack CI; run one source-locked qualification after the current acceptance
  sequence has been reconciled and the negative harness reviewed.
- Preserve all R1 positive files, source SHA and D2 fixture invariants.
- Dedicated credential principal is still separate; browser and dashboard
  cannot receive credential plaintext or claim verified permissions.
- Provider tests, LIVE orders, funds and externally reachable enrollment
  remain disabled. The financial Gate is unchanged.
