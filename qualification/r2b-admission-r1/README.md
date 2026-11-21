# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R1

**State: installed broker and authoritative vault-bridge qualification candidate; full native one-use approval and protected provider execution still OPEN.**

Branch `qualification/tb-sec-key-01-r2b-admission-r1` forks accepted
`qualification/tb-sec-key-01-r2b-admission` at
`3506bf6af91cdb71e52f4e86fbd956e043c1c07e`.
Frozen D2, R2B, R3 vault, native LightDM and accepted R1–R4
security source are unchanged. No mainline merge, real credentials,
provider internet access, money, paid model call or LIVE trading.

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
