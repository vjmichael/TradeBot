# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R1

**State: SCOPED installed broker custody and owner execution source PASS; trusted native approval agent and fixed-provider execution remain OPEN.**

Branch `qualification/tb-sec-key-01-r2b-admission-r1` forks accepted
`qualification/tb-sec-key-01-r2b-admission` at
`3506bf6af91cdb71e52f4e86fbd956e043c1c07e`.
Frozen D2, R2B, R3 vault, native LightDM and accepted R1–R4
security source are unchanged. No mainline merge, real credentials,
provider internet access, money, paid model call or LIVE trading.

## Terminal R1 qualification evidence — 2026-11-20 local / 2026-11-21 UTC

**[Successful GitHub Actions #44865671640](https://github.com/vjmichael/TradeBot/actions/runs/44865671640)**,
job `136728657210`, exact accepted test SHA
`dc71102a9521214dcaa03cc858ae9fe0504388ec`. The
[nonsecret native receipt artifact #14691706891](https://github.com/vjmichael/TradeBot/actions/runs/44865671640/artifacts/14691706891)
was downloaded and independently hashed, SHA-256
`4aadf6f3e46cf71cd013f5ddef7c56ecea316d281338cd7e35e13477ee413192`,
matching GitHub's archive digest. The artifact includes original
source locks, zero-error `dpkg-audit.txt`, package inventory, all
source-test transcripts, eight native test markers and genuine screenshot
of the original, **unchanged** Account Connections operator GUI.

| Same-source qualification | Result |
|---|---|
| New installed broker frame/real AF_UNIX and authoritative owner bridge SQLite tests | **22/22 PASS** |
| Prior one-use approval/PAM/worker-receipt source tests | **51/51 PASS** |
| Existing managed vault, reviewed migration, HMAC receipt source tests | **71/71 PASS** |
| Original native handoff guards | **9/9 PASS** |
| Original intent/refusal/adversarial AF_UNIX regressions | **21/21 PASS** |
| **Total source suite** | **174/174 PASS** |
| Disabled-by-default independent broker root enable marker | `R2B_R1_DISABLED_DEFAULT_PASS` |
| Actual systemd broker UID, 0700/0600 state, socket group/DAC, PrivateNetwork/AF_UNIX | `R2B_R1_INSTALLED_PROTECTED_BROKER_PASS` |
| Root, console, operator, dashboard, native non-owner principals refused | `R2B_R1_KERNEL_PRINCIPAL_ISOLATION_PASS` |
| Inert prepared request cannot be forged into an approved worker/provider action | `R2B_R1_NO_FORGED_NATIVE_APPROVAL_PASS` |
| Owner cancellation is one-use; duplicate/consume replay refused | `R2B_R1_CANCEL_REPLAY_REFUSAL_PASS` |
| Actual service restart revokes pending request, private SQLite read-only confirmation | `R2B_R1_RESTART_REVOKES_PENDING_PASS` |
| Genuine original operator LightDM Account Connections GUI still works | `R2B_R1_ORIGINAL_GUI_UNCHANGED_PASS` |
| Full offline Debian 13 no-NIC installed broker acceptance | `R2B_R1_INSTALLED_NEGATIVE_BROKER_PASS` |

All eight installed markers emitted by the genuine Debian 13 guest and
independently checked at the same workflow SHA. The unchanged installed GUI
still explicitly says PAPЕR qualification only, no connection test or trading;
no positive approval agent, real worker or provider test is available.

**Failed predecessor preserved:** [#44865248533](https://github.com/vjmichael/TradeBot/actions/runs/44865248533)
passed **174/174** source tests and the first five installed native gates,
but an independent diagnostic script imported `approval_ledger` without
the authoritative `vault_owner_hooks` module path. The diagnostic was
replaced with a *read-only SQLite* query of the dedicated approval UID's
private ledger, preserving exact restart-revocation requirements without
altering broker, vault, GUI, worker or production source. One subsequent,
sequential run [#44865671640](https://github.com/vjmichael/TradeBot/actions/runs/44865671640)
passed. No stacked CI, secret, provider networking, main merge, financial
operation or LIVE permission.

**Acceptance boundary:** the broker is truly installed, but deliberately
**negative-only**. It will not admit positive native review until a trusted
per-session operator GUI agent with an authentic unforgeable reply channel
is built. The owner execution bridge is proven with synthetic approval
and HMAC fixtures, but is not yet wired as an executable action in the
installed managed vault service. No independently supervised worker or
separate service-provisioned receipt signing key was installed in R1.
The full `TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R1`
contract therefore remains **PARTIAL**, not releasable.

**Next focused task:** `TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R2`
— implement the trusted native operator review agent and broker-owned
session-bound reply path; independently prove actual yes/no/cancel,
same-UID forgery refusal, revoke/seat/logout/restart/races on installed
Debian, then wire exact one-use consumption into the authoritative
vault bridge and a separate fixed-destination receipt-authenticated
synthetic worker with independently provisioned signing key.
Only after installed end-to-end PASS should sandbox provider
permission tests or reviewed migration be considered.

## Installed independent protected broker

`broker_service.py` and `maj-vaj-r2b-approval.service` implement a
separate, disabled-by-default `maj-vaj-approval` systemd service with:

- Distinct OS UID, root-installed non-writable Python source, 0700
  `StateDirectory`/0600 SQLite, root-controlled enable marker;
- AF_UNIX only, `PrivateNetwork=yes`, `NoNewPrivileges=yes`,
  strict filesystem/device/home protections, no inherited key files,
  provider data, network egress or worker execution;
- Exact 1024-byte, single-line ASCII JSON + EOF frame, rejection of
  duplicate keys, NUL, oversized data, unknown commands and client
  `approved`/target fields;
- Unix socket protected by group/DAC plus independently checked
  `SO_PEERCRED` owner identity; only the existing `maj-vaj-vault`
  principal can prepare/cancel/attempt consumption;
- All positive operator `review` requests **refused**, even from a
  genuine operator session, because the separate, trusted graphical
  confirmation/return channel has NOT been installed. The native GUI
  service remains unchanged and the operator is not added to broker
  IPC group;
- Requests are bound to the preexisting durable one-use SQLite ledger.
  Prepared requests grant no authority. Cancellation consumes
  the opportunity, restart revokes pending/approved requests,
  duplicate or stale requests cannot resurrect approval.

The installed service is **qualification-only**. It is a real socket
and real systemd sandbox, not a provider-capable production service.
Its `prepare` command can be tested with synthetic owner-supplied
context, but the deployed broker is not yet connected to current
vault generation/inventory or authenticated native review. Consequently
it cannot create an approved grant or launch any worker.

## Authoritative owner transaction and signed worker bridge

`owner_execution_bridge.py` is a source-only, owner-internal candidate:

1. Read and verify the authoritative owner-encrypted ciphertext bindings,
   role generation/policy, disabled flag and pending attempt status.
   Only Kraken's **read-only permission check** is represented so far.
2. Require a trusted internal `consume(context)` callback to perform
   the broker's durable one-use transaction. The callback must return
   literal `True`, never a browser token or caller boolean.
3. Recheck the vault generation/policy/binding after consumption. Any
   concurrent re-enroll/change invalidates the grant, and the grant is
   *not* restored.
4. Commit `OwnerHooks.begin()` under the authoritative encrypted
   vault `BEGIN IMMEDIATE` transaction. This reserves one attempt,
   increments the existing Kraken nonce and never exports credential
   plaintext.
5. Accept results only from `read_authenticated_receipt`, requiring
   the configured worker's actual Unix peer UID, bounded framing and
   separate-key HMAC tied to exact generation, attempt, nonce and policy.
   Then commit `OwnerHooks.finish()` under a new vault transaction.
   Duplicate, stale, unknown-worker or tampered results are refused.
   Startup recovery refuses interrupted attempts without retry.

The source tests exercise all of this with **synthetic** approvals,
synthetic HMAC signer and disposable encrypted SQLite. That evidence
is not an installed legitimate approval or actual provider permission.

## R1 acceptance requirements

`r2b-r1-broker-source.yml` requalifies this broker's framing, Unix
socket cleanup, durable cancellation/restart, owner-only boundaries,
the existing admission ledger and the original native security suites.

`r2b-r1-installed-broker.yml` reconstructs the original immutable
installed source and copies broker candidates into a disposable,
network-isolated QEMU Debian 13 guest. Source tests must pass *before*
the Debian guest builds. The installed native harness tests:

1. Broker cannot start without its separate root-owned qualification marker.
2. Real systemd UID/group/DAC/state/database/sandbox custody.
3. Root, console, operator, dashboard and native UIDs cannot prepare.
4. Real vault UID can prepare one inert request; it cannot forge
   `review`, `approve`, `consume` or worker execution.
5. Owner cancellation is single-use and replay refused.
6. Genuine systemd service restart revokes a second pending request;
   private SQLite shows `revoked`.
7. Original installed Account Connections LightDM operator GUI remains
   accessible, with unchanged empty PAPER-only vault status and no
   provider authority.

All native checks must emit their fixed terminal markers from the
same built guest; the workflow must independently require every marker.

## Outstanding authorization and release gates

**The full R1 task is NOT complete with a negative-only broker.**
A trusted native GUI agent still must be installed under the genuine
operator PAM/logind session with a return path that cannot be forged by
another same-UID process or a browser. Only then may the broker create
approved grants. Next the installed vault owner must bind and consume
the exact current generation/inventory and reserve the attempt using the
tested bridge, before any separately supervised fixed-destination
provider worker is started. Root-provisioned independent worker receipt
key custody, actual worker `SO_PEERCRED`, transport replay/cancellation,
unexpected restart/unknown delivery and native approve/refuse cases need
physical installed acceptance before positive operation is exposed.

Reviewed legacy migration remains blocked until the trusted GUI and owner
one-use consumption exist. Telegram and Grok destination/opt-in contracts,
R2C key restoration, external provider sandbox/permission proofs and
final financial authorization are separate gates. PAPER-only status remains;
no real provider test, account execution or trading is permitted.
