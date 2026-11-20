# TB-FIN-01C / TB-08-R2-R3 — synchronous independent native entry

Status: independent broker and bounded local interruption acceptance PASS; staged installed operational acceptance remains OPEN. PAPER-only, no actual desk change, no LIVE activation.

## Implemented
- Independent native worker discovers only an already authorized candidate from a root-signed minimized snapshot, confirms rebound workspace/key and exclusive custody, and connects to the desk broker as the expected OS principal. Snapshot alone grants no order authority. The desk obtains the actual current authorization via Gate.claim and verifies binding, market, scope, ownership, policy, goal restrictions, expiry, journal witness and unchanged claim inside serialized transactions. Exact minimized order offer excludes claim token and account/source secrets. Native fsyncs exclusive per-intent attempt before requesting the final permit; desk commits native_bound, native_attempt and exact ready digest before issuing a nonce/receipt-bound two-second permit while holding the final transaction.
- Cooperative desk writers (Store transactions/boot journal mode and reserve_runtime desk transactions) honor a root-owned immutable kernel fence. Desk passes the locked open-file description via authenticated Unix socket at the final boundary; native verifies the file identity and lock and retains it through the actual Freqtrade entry/persistence and acknowledgment. Closing the desk descriptor alone cannot drop the fence. Peer disconnect before the final check refuses; death immediately afterward may leave one already-authorized effect in flight under the retained fence, never a new authority. Uncertain outcomes retain claim/reservation, and subsequent claims cannot repeat. This is a cooperative installed-writer fence, not protection against arbitrary malicious code with desk UID/raw DB access.
- Staged desk gate validates public native wallet evidence and independent witness rather than accessing native HMAC keys. Missing compatible goal evidence fails closed; migrated point-baseline attribution remains a separate qualification. Root snapshot relay admits only the pinned transfer group for its fixed desk-UID extractor. Legacy installed worker refuses startup after the new fence exists; cutover must occur with all old services stopped and a reviewed pristine migration, never an in-flight legacy position.
- Three opt-in service/timer definitions. Root must separately provision and pin the permanent lock (root-owned 0640, single link, transfer group only), roles, keys, directories, code/venv and marker; never replace/remove the lock during service operation. Neither marker nor any actual service was enabled. Existing Store schema still enforces PAPER/PAUSED; no RUN schema migration or strategy authorization supplied. No external API.

## Verified local acceptance
Pinned offline Python 3.12.16/Freqtrade 2026.10, isolated Linux separate desk/native/authority UIDs, authenticated root snapshot, actual native wallet, real Gate/financial ledger and actual native Freqtrade simulator with controlled synthetic book. The test does not insert a synthetic claimed order; it invokes actual Gate.admit/claim. Logical clock, synthetic market and isolated RUN fixture are explicit, not actual public market or production authority.
- Normal independent entry: actual authorized native order persisted, fresh process verifies exactly one tagged trade/order, duplicate claim refused, principal reserved. Three checkpoints PASS, exit 0. Log SHA256 474f5e3aa00e44429e5dc9b4085a4d43e9122112b35e433204e5bcfec3e6d04f.
- Guardian SIGKILL at actual native effect boundary: lock remains held while native process lives; native SIGKILL releases lock but leaves durable claim/attempt, no persisted order and no repeat authority. Three checkpoints PASS, exit 0. Log SHA256 ccbf456e823d45a0a5e8de3a1daab9ffcd5d3e4c51431904e68079781403d45b.
- Native SIGKILL after actual ORM persistence and before desk acknowledgment: desk records UNKNOWN, independent reopen verifies exactly one tagged order, no replay or reservation release. Three checkpoints PASS, exit 0. Log SHA256 f14c96734f82a2c91a22dd5e4d1a088ee9b85b0214717944a6c988aef9be16f8.
- 112/112 existing regression tests PASS (8+104; actual Gate/bridge/recovery/ledger/reserve/Store/dashboard). Log SHA256 a8f56e6860828b12eaadf9380cdc9a4f3758de25a651b29478772f4e36117b85. Five new focused kernel/protocol tests PASS: passed lock lifetime, unlocked/wrong inode refusal, fence persists despite marker absence, immutable minimized attempt/scope, wrong peer refusal. Log SHA256 bd5b1ed402fc541a1891ca07e0ef7b83c2b5bda2b1ebfe762ef7410e4d1ea676.
- Three staged unit definitions systemd-analyze verify PASS (interpreter path substituted for disposable host). Host PID 1 is supervisord; actual installed service start/stop/restart/timeouts not executed. Early fixture setup errors: root source permission mode, wrong fixture identity for admitted proposal, name shadowing; corrected. All three final invocations exit 0. No production credentials; no network beyond local Unix sockets in controlled runs.

Exact tested source blobs:
- reserve_entry_fence.py: f11a4e849d1f88b2a9188a231ab750acc421f48a
- reserve_entry_broker.py: be8122e935e9feeb00939b113bb105bd3590eaa3
- reserve_entry_worker.py: 93e33cb24b8a52f65458de045ce9c34b762e847c
- reserve_runtime.py: 221ad9c90a0c6220066b19e99646bbf2793ba337
- tradebot.py: bac1b2473257e5c2ed21e8f784a26f6fb05d3345
- reserve_live_relay.py: 30b4118b928283c5e9eb39e09e9b31d4537e01ae
- scripts/run_paper_worker.py: 3d9fab6922288088cbdda29197bda102dfef7123
- scripts/qualify_independent_entry.py: d7bdbb73ba4273fe9a677f75dff714e6a2434390
- deploy/maj-vaj-entry-desk.service: dffcdf9beb3e9ea7a8006e5e1177323dfc31ea24
- deploy/maj-vaj-entry-native.service: 6e2644c1146e35889fa44c11526c2ba6e28cef4b
- deploy/maj-vaj-entry-native.timer: b6bbf713a6517d7d5b242564ac7b2bf2ac23b6bd
- tests/test_reserve_entry_broker.py: 666b0c7f49542222d0981a1b84c08d3616466797

## Remaining
Qualify actual installed service manager/cgroup supervision and complete the same broker-to-terminal path, including protection through snapshot unavailability, independent partial-fill cancellation and terminal import, and explicit reviewed disposition of pre-effect ambiguity rather than inferred no-fill. Complete migrated goal evidence, authenticated wallet and approval delivery, genuine public-market sustained PAPER settlement and full TB-SEC-KEY-01 installed canary audit. All cooperative writer paths must be audited again at cutover; no arbitrary raw DB writes. No main merge, actual desk change, new CI or LIVE authorization.
