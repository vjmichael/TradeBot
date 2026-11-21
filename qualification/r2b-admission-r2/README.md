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

**Still OPEN:** independently accepted native yes/decline/cancel, PIN
or GUI-session lock / logout revocation, replay and restart; trusted
real owner-prepared current credential state; installed vault service
owner-side consume/reserve/finish bridge; installed fixed synthetic
worker plus authenticated receipt custody; positive non-destructive
reviewed legacy migration; actual optional provider sandbox checks;
and all R2C/financial/LIVE gates. As X11 permits other authorized
clients to synthesize UI input, pinned IPC identity alone cannot
certify that a physical person clicked the confirmation. Dedicated
session isolation and its limits require explicit security review;
do not claim physical input provenance from a QEMU keyboard fixture.
