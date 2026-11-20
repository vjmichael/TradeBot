# TB-FIN-01C / TB-08-R1 — minimized native financial snapshot

Parent 3ed08036abf531a38b437af97bc1f03bb07f6dcf. Actual desk untouched, no CI or live activation.

Root-reviewed cold handoff now reconstructs a new bounded SQLite image with explicit tables, columns and row filters, rather than forwarding source database pages. Preserve immutable journal body bytes, current desk risk policy, scoped binding and required settlement events; do not include vault tables, unrelated logs, bearer/claim tokens, deleted pages, arbitrary schemas, noncurrent policy or evidence rows. Check complete financial replay/checkpoint/execution identity before and after projection using the existing verifier; sign only the projected bytes. Receiver verifies strict allowlisted schema and selected-row scope, root signature, exact identity, checkpoint and TTL, using query-only access. Historical financial payloads still need valid upstream schema/admission; this is not a universal secret scrubber.

Local checks: eight passed (five dedicated projector fixtures plus three existing actual Linux UID signed snapshot tests), including synthetic canary exclusion in serialized database and transmitted packet; refuse full database, extra schema/rows, missing source fields, nontransactional source, unauthorized UID, write, wrong scope, expired, tampered and changed checkpoint. Python compilation passed. Replay in UID tests uses fixture identity; actual full repository financial replay and Freqtrade engine remain not qualified. Source or ledger payload contamination must fail the independent installed synthetic-canary matrix under TB-SEC-KEY-01.

Exact tested blobs:
- reserve_snapshot.py 852d9c321d179320350053a8b87ece96ed7f22d4
- reserve_native_handoff.py f074876a1ba167012dc4ec065693e9095b194c2e
- tests/test_reserve_native_migration.py 261cde4af483a7edc4611169c28fa0b23db2a7c6
- tests/test_reserve_snapshot.py 340843f041e76ae2b10d01ed7efdecf19b80a6d2

Next: restore qualified cp312 interpreter and full exact source. Verify projected snapshot against actual real ledger and native wallet output, publish independently authenticated reconciliation, then complete installed reserve operations and recovery without full database transfers. Production execution/settlement handoff and live snapshot broker, and prior 60-minute paper settlement failure remain open. No further dependency wheelhouse required.
