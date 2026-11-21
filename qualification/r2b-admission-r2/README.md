# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R2

**Task status: IN PROGRESS / not a release gate.**
All code is isolated on
`qualification/tb-sec-key-01-r2b-admission-r2`, branched from accepted R1
documentation `4802c2199af54a8fddac75e07864875c8057c0d0`.
No default branch merge, provider network operation, real credentials,
financial execution or LIVE authority.

## Immutable source and implemented candidates

The exact restored production tree `89946baeef719d7369887cc7d89a8a4230c7180e`
and the original `credential_graphical.py` SHA-256
`74cd63e6533c237f5222c151c00548dd2631a95b2b69f147bf8c3871f32a5a9f`
are verified before the additive source patch. The user still logs into
the original LightDM `maj-vaj-account-connections.desktop`, through the
original fixed `credential_session_wrapper.sh`, with original
`current_identity`, Xauthority/cookie, session event latch and
`PR_SET_DUMPABLE=0` protections.

`patch_credential_gui.py` inserts one visible
**Review pending read-only check** button and Alt+R into the existing
Tk window without modifying masked secret entry, paired Save/Remove,
status semantics or session guard.

`native_agent_protocol.py` connects from that authenticated operator
GUI to a dedicated AF_UNIX agent socket and refuses untrusted broker UID,
unknown context, malformed preview or state-changing command. It
shows one fixed read-only PAPER synthetic review and returns a decision
only on the original connection after another local session guard.

`agent_broker_channel.py` validates the socket's kernel
`SO_PEERCRED`, the genuine operator PAM/logind identity and seat, and
a kernel-reported *direct-child relationship to LightDM's session
leader*. It does not accept a claimed PID/UID, user-selected request ID,
caller supplied `approved=true`, browser bearer, endpoint, recipient
or provider name. It selects one unambiguous owner-prepared request,
holds a single review mutex, binds the decision to that socket,
and keeps a real libsystemd session event latch alive until
broker consumption. Loss of session, malformed reply, cancellation,
restart or expiry revokes the request; broker restart also revokes
pending/approved SQLite entries. The broker gains no vault key or
provider connection.

The first installed Debian acceptance run
[GitHub Actions #44867939143](https://github.com/vjmichael/TradeBot/actions/runs/44867939143)
qualified disabled-by-default gating, both broker sockets and refusal
of a non-GUI same-UID process, then failed to show the native modal.
Actual captured LightDM evidence: the PAM session leader is the
LightDM wrapper process, PID 833, and the authenticated fixed GUI
process is its *direct child*, PID 873. The original code incorrectly
required `peer.pid == session.Leader`. The corrected source requires
`kernel_PPId(peer.pid) == loginctl_session_Leader` plus matching real
operator PAM session and role. The broker alone cannot use
`ProtectProc=invisible`, as it needs read-only `/proc/PID/stat`
parent metadata; this is an explicit, narrow process-visibility
tradeoff in a credential-free, no-network broker. The independent vault
and worker protections remain unchanged. Do **not** substitute a
synthetic PAM session, fallback on UID alone, or disable the session
event checks if the corrected native acceptance fails.

`synthetic_worker_protocol.py`, `synthetic_worker_service.py`,
`maj-vaj-r2b-synthetic-worker.service` and
`native_synthetic_worker_harness.py` stage a separate dedicated
`maj-vaj-r2b-worker` account and AF_UNIX endpoint. The worker is
always `PrivateNetwork=yes`, receives a distinct root-provisioned
systemd `LoadCredential` key, accepts only the true vault UID, and
returns only a signed
`maj-vaj-synthetic-no-network-refusal-v1` / `provider_refused`
receipt for a fully bound read-only attempt. Its domain and label
cannot be interpreted as a positive Kraken provider test. Missing or
mismatched key, forged worker, malformed/tampered/replayed result and
unapproved principals are refusals. The native worker acceptance is
a separate gate, not evidence of genuine provider permissions.

`owner_synthetic_bridge.py` stages the private owner-only
consume-before-reserve path. It expects the authoritative
`ManagedPaperVault` generation and policy, existing
`owner_execution_bridge.begin_once`, and an actual broker one-use
consume before `OwnerHooks.begin`. The separately signed negative
worker result must be independently verified before
`OwnerHooks.finish`, inside a fresh vault transaction. It is not yet
exposed as an installed operator command.

## Installed R2 native broker closeout (independently observed)

[Original R2 native run #44868873434](https://github.com/vjmichael/TradeBot/actions/runs/44868873434)
**SUCCESS** at exact SHA `5848013fcfe18b922c2d426d99929b7e3ba9365e`.
All eight required native markers passed: negative-default gate, protected
broker sockets, same-UID outsider refusal, native decline/cancel,
genuine LightDM GUI one-use positive review and consumption,
restart revocation, logout and complete installed agent IPC.
The source-locked acceptance also passed Python regressions and
frozen-root GUI restoration; this is the installed native BROKER
boundary only. The request-scoped consume guard now requires the exact
approval request's live event latch, not an ambiguous session witness.

The earlier #44868453665 run failed after a real negative review when
the second GUI approval stayed pending. A screenshot-grounded explicit
click of the native OK control corrected the acceptance; no
broker-API direct approval, simulated PAM login or protective-session
bypass was substituted. This proves the original QEMU GUI event path
only, not physical keyboard provenance on a hostile X11 session.

The first independent synthetic worker native run
[#44869299133](https://github.com/vjmichael/TradeBot/actions/runs/44869299133)
FAILED during installed service startup after its disabled-by-default
negative gate passed. Inspection found that `operator_session.py` needed
the exact source-locked `approval_ledger.py` dependency in the worker
package. No weakened sandbox or direct positive-result injection was used.

The corrected independent Debian 13 QEMU run
[#44869628432](https://github.com/vjmichael/TradeBot/actions/runs/44869628432)
**SUCCESS** at exact SHA `de7748c7cfa7ae76515256e567434b0a6ad37b7c`:
**7/7 native worker markers passed**. These cover disabled-by-default,
dedicated protected UID and systemd LoadCredential, true vault
SO_PEERCRED, negative-only domain-separated HMAC receipt,
wrong-principal refusal, missing-key restart/recovery and terminal
installed worker acceptance. The earlier explicit fixed Python
module-path repair (`7f3d2f1`) is included in the passing source.
This qualifies the isolated synthetic-denial WORKER only; it is not a
real provider or authoritative vault-to-worker end-to-end receipt.

The owner-only `FixedReadOnlyCoordinator` implementation and source
tests remain staging, not proof of an installed vault handler or
authorized owner → broker → worker complete system transaction.
No real Kraken call or positive permissions authority is admitted.

## Source acceptance and remaining work

[GitHub Actions #44867768044](https://github.com/vjmichael/TradeBot/actions/runs/44867768044)
**SUCCESS** at exact SHA
`819fcbf0a89759e61f1070eca4714c239f3499df`:
**10/10** original R2 source tests, **22/22** R1 broker/owner tests,
**51/51** previous admission tests, **71/71** managed-vault/migration/
worker-verifier tests, **9/9** original handoff guards,
and **21/21** adversarial/cancellation tests:
**184/184 PASS** with the additive GUI patch and dashboard build.
The revised direct-child attestor and separately signed synthetic
worker must pass **new, same-source** acceptance before extension of
the claim.

**Still OPEN:** independently verified GUI-session lock event refusal
(including change during a dialog), installed authoritative vault-owner
prepare/consume/reserve/finish interface, genuine encrypted generation
and policy binding, independently qualified owner-to-worker receipt handoff,
replay/crash and owner-worker restart
cross-boundary qualification, positive non-destructive reviewed legacy
migration, optional real-provider sandbox checks only after separate
authorization, and all R2C/financial/LIVE gates. As X11 permits other authorized
clients to synthesize UI input, pinned IPC identity alone cannot
certify that a physical person clicked the confirmation. Dedicated
session isolation and its limits require explicit security review;
do not claim physical input provenance from a QEMU keyboard fixture.
