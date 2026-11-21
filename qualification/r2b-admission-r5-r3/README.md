# R5-R3 — production installer preparation and protected upgrade custody

**Status: PLANNED / NOT ACCEPTED.** R5-R2-R1 is scoped accepted at exact source `162edb29d60abe8e373c2e961333611d74f13717`, [original native run #44913129057](https://github.com/vjmichael/TradeBot/actions/runs/44913129057), with **235/235** source tests, **4/4** offline QEMU scenarios and independently verified artifact #14710224310 (SHA-256 `504df835b71902df69701a532f23ae3fcbb66b4718114c17d2f1bce025471ff6`). This document defines **the next work**, not acceptance of production deployment.

## Engineering execution policy

- **Build on host/ordinary native CI runner, not inside QEMU.** Prepare pinned Debian root filesystem and any Python/JS compiled assets on host before booting. QEMU is reserved for installed kernel/systemd/PAM/IPC tests, truly abrupt host-QEMU SIGKILL and same-overlay recovery, and cross-version lifecycle behavior. Later physical Debian/VPS native acceptance is an independent release gate.
- **No Docker.** Batch source/staging changes with local preflight; do not push a new CI workflow trigger for every subtask. Retain the accepted R5-R2-R1 original matrix and receipts. No default-branch merge until independent release review.
- **Default PAPER, fail-closed and disabled.** Never use production credentials, access real Kraken endpoints, place orders, turn on LIVE, or claim a production installer solely from offline synthetic success.

## R5-R3A — publisher and package trust contract (next)

1. Define a deterministic package manifest: publisher identity, release/version and anti-rollback epoch, permitted file inventory, hashes, exact schema compatibility, target OS/architecture, service identities and installation paths. Cryptographically bind this manifest to a reviewed signed package and a separately preprovisioned publisher trust root; the package must not install or replace its own root of trust.
2. Define protected trust-root enrollment, rotation and revocation under separate authenticated operator/administrator authority. Reject unknown/revoked signers, altered packages, manifest substitution, replay, out-of-order releases, unauthorized downgrades and swapped executable/service identities. An authenticated rollback may restore the prior *approved* release without granting arbitrary downgrade.
3. Implement host-side, read-only manifest/trust validation and deterministic negative unit cases **first**, using ephemeral fixture keys. No production signing private key may enter the repository, CI artifacts, guest disk, logs or installer payload. Retain explicit auditable version/publisher pins and refusal receipts.

**R5-R3A gate:** all exact-source host tests pass, including wrong/revoked/rotated publisher and mismatched signed inventory; no installed guest or CI batch is required to complete initial source work.

## R5-R3B — transactional installer and data-compatibility custody

1. Implement prepare/stage/verify/commit/recover phases with root-protected no-follow paths, durable file and directory fsync, explicit journal/version records and source hash pinning; never mutate live services before verification. Stop/restart only fixed authorized units with bounded readiness and safe interrupted state.
2. Keep credential-bearing encrypted vault data intact and inaccessible to packaging workers. Initially admit metadata-only, backward-compatible migrations under a reviewed schema declaration. **Do not** perform an encrypted-secret migration, key export, provider credential read or destructive rollback without a separate approved custody design and operator authorization.
3. Preserve prior encrypted state on rollback refusal; bind any later authorized migration to an independent, transactional, operator-approved state transition with audit-safe receipts. Never bypass existing one-use approval, revocation, broker/worker isolation, key custody or default-disable gates.

**R5-R3B gate:** immutable source/package, journal, rollback, data-preservation and refusal unit tests; no production secret or live provider.

## R5-R3C — independent installed adverse qualification

Use host-built artifacts on disposable offline Debian 13 QEMU overlays. Exercise fresh install, interrupted stage, incomplete/fsync snapshot, first and repeated power loss in every transaction phase, partial restore, interrupted rollback cleanup, same-release reinstall, compatible signed version upgrade, malformed/incompatible migration, service/key tampering, publisher rotation/revocation and wrong-owner permissions. Require measured same-overlay recovery with **actual host-QEMU SIGKILL**, bounded service startup and no unintended authority. Preserve historical R3/R4/R5/R5-R1/R5-R2 acceptance cohorts; report exact SHA and independent artifacts. Only batch one original CI qualification after local source preflight.

## R5-R3D — physical Debian/VPS release admission (separate gate)

Qualify a non-trading staging installation on actual Debian/VPS with OS-backed operator authorization, verified package provenance, installer/upgrade/rollback, reboots, cancellation, credential-store access refusal and restart durability. Assess protected service permissions, tamper resistance, signed publisher custody, revocation, logging redaction, restore/rollback operator interaction and disaster recovery. **No LIVE admission or real trading follows automatically.** Provider-specific least-privilege, R2C browser/notification/KEK, migration and financial safeguards retain their own release gates.

## Acceptance / remaining-risk boundary

R5-R2-R1's proven Ed25519 signed *test* v1→v2 upgrade is a narrowly compatible, offline synthetic code-only change. It does not prove production trust enrollment, rotation/revocation, credential-bearing schema migration, physical installed release, real Kraken privileges, X11 physical-user provenance or LIVE trading. R5-R3 remains **OPEN** pending separately verified receipts.

**Immediate next coding task: `TB-SEC-KEY-01-R2B-ADMISSION-R5-R3A` — implement the independent host-side publisher manifest/trust verifier and refusal tests without introducing a CI trigger.**
