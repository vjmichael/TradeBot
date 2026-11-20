# TB-FIN-01C / TB-08 — independent native wallet evidence candidate

Parent 4adf57b996b63de2543e5fb7f501a8f97f4f4a90. PAPER only; actual desk unchanged. No live authority, service activation, new CI batch or merge.

## Verified runtime inputs

Uploaded archive Shared Development Toolchain/FreqTrade/MajVaj-Freqtrade-2026.10-Linux-x86_64-cp312-Wheelhouse.zip, 181108466 bytes, SHA256 a4a11dc52bff8e2556224cba60389512edb045baed8aa38713f26bd10b3a097b: matching sidecar; 88 internal SHA256 manifest entries pass. Engine and client wheel payloads compared against earlier independently verified upstream archive (pinned 4cf0351e08d26e96a6429ef48103b726bb230cf8): 356 + 3 matching files, no missing or different payload. 83 Linux x86_64 runtime distributions resolve with no index and no build against target Python 3.12.16/manylinux_2_39, excluding the recorded pip bootstrap version (not included in wheelhouse). cryptography requires glibc >=2.34. This is resolver metadata qualification, not installation or native ABI execution. Local interpreter is 3.13.5, the uploaded wheels target cp312; uv network restoration unavailable due to DNS, no matching interpreter found in checked shared toolkit. Do not demand a new wheelhouse or change pinned dependencies. Use qualified hosted Python 3.12 or restore existing compatible interpreter. Actual exact project checkout and native Freqtrade test gates not executed.

## Candidate

- Separate public root-signed rebinding verification from native-only private DB/key verification; normalize sqlite3.Row values for production row factories. Current scope, original receipt, original ledger prefix and independent identities required.
- Add domain-separated native Ed25519 flat-wallet observations with exact ledger head, execution anchor, boot, binding, instance, workspace, time, and observed amount. Verify complete scope, freshness, flat wallets and quote reconciliation independently without sharing receipt keys. Legacy HMAC packets not accepted in this public path.
- NativeProducer.observe_from_handoff composes the existing actual native driver under reviewed snapshot and exclusive workspace lease; requires distinct pins, real driver identity and workspace key/inode binding. Uses trusted local driver context, not RPC. Rebound native proposals use public signature verification plus actual native custody validation.
- Provide transactional evidence publication and independent projection; staged installed reserve scheduler requires independent public projection for adoption/tick, without disabling revocation merely because native verification is unavailable. Human review may use the separate projection callback. Staged source, not production service qualification. No live broker, automated delivery/acknowledgment, worker startup or trade/settlement handoff installed.

## Acceptance

12/12 focused tests passed locally: four public signature/SQLite/fixture-replay checks, six rebind/actual UID checks (including production sqlite3.Row and public root receipt under desk/authority UIDs while native key reads fail), two existing native key/mailbox regression checks. Python compilation passed. Native wallet production, actual ledger publish/replay, real Freqtrade operations and installed protected reserve adoption/tick/revoke remain unexecuted. Fixture results are not native execution evidence.

Exact tested source blobs:
- reserve_native_migration.py bdd9b07d4e1f2525a392407d0fafadb464e8ec5d
- reserve_native_rebind.py 523c6b64526cd7f53ba84e2d46d4281a4051a5f0
- reserve_native_wallet.py 736d317b7c6d4c520807aa5e7ae63648b589d9d9
- reserve_native_evidence.py 5f6154655ed2c562899e82c2b50744508c0487b0
- reserve_protected.py 0d566c7de8f53b30166aefd5866a459e5b176beb
- reserve_delivery.py f4e2dbac2948da38d0c51b4d62a534766fce6109
- reserve_protected_worker.py 639321c9d4d499c6bbb3619f45ce9008eb2bdacd
- tests/test_reserve_native_rebind.py f98c24c2d5ee47c7bbfc84721b6afef09fc7b2b5
- tests/test_reserve_native_wallet.py 3eb5037d07ea4b3efb908943e5699a91f5ce4f31

## Remaining

Restore actual pinned Linux cp312 runtime and full authenticated source; qualify real native wallet production and signed publication/projection against actual ledger. Implement narrow independent native execution/settlement and live snapshot/delivery supervision without shared mutable desk database or private key transfer. Complete actual installed protected reserve adoption, tick, revocation, crash/restart, delivery reconciliation, and graphical workflow. Historical/multi-workspace migration remains separate. Then consolidated unchanged-SHA qualification and repair prior 60-minute public settlement failure 44646221309. No acceptance closure yet.
