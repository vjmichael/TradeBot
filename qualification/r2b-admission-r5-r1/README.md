# Maj Vaj R5-R1 — installer tamper, interruption, rollback and upgrade qualification

**Scoped native PASS — offline QEMU-only qualification, NOT a production installer release.**

**Exact accepted commit:** `89f4faa5f59ecaac8add555279a902c868588425` on `qualification/tb-sec-key-01-r2b-admission-r5-r1`. [GitHub Actions original success #44881435999](https://github.com/vjmichael/TradeBot/actions/runs/44881435999). **226/226 source tests**, four independent native Debian 13 guests, empty dpkg audit, no external network or real credentials.

| Native case | Hard guarantee verified |
|---|---|
| `interrupt` | Root-only marker triggers SIGKILL after first patched file; pending journal blocks reuse and service enable; explicit recovery verifies original bytes and restarts only original vault; fresh install and verification succeed |
| `tamper` | Patched source, independently installed systemd unit, 32-byte signing key replacement, and symlinked protected source bundle are refused before a marker is created |
| `rollback` | An encrypted stored test credential forces rollback refusal without loss of data; only separate operator-authorized removal permits restoration, with accepted baseline files and no broker/worker activity |
| `upgrade` | Same source with disabled services is idempotently admitted without altering the key or files; active-service upgrade and changed unreviewed module version are refused |

The implementation uses a root-owned `r5_transaction.py` helper and a private `r2b-r5-rollback-snapshot` plus durable `r2b-r5-transaction-pending` marker. The identity manifest binds the installed source, service units/dropins, and signing-key bytes without publishing a key. All source entrypaths are pinned to accepted Git blob IDs; the helper CLI cannot accept alternate filesystem roots. The existing `install-offline.sh` supports `install`, `verify`, `enable`, `disable`, `recover`, `rollback`, and same-version `upgrade`. No automatic version migration, network adapter, credential export, trading or LIVE dispatch is implemented.

## Historical failure and unchanged-SHA acceptance

Initial [run #44880742569](https://github.com/vjmichael/TradeBot/actions/runs/44880742569) passed new source tests and reached the *real* killed-install, detecting that recovery attempted `systemctl stop` for units that had not yet been installed. The repair treats nonexistent broker/worker units as acceptable **only when confirmed not active**, without bypassing the vault stop or transaction checksum. The next [run #44881435999](https://github.com/vjmichael/TradeBot/actions/runs/44881435999) passed all original cases at one SHA.

**Artifact:** `r2b-r5-r1-installer-security-evidence`, ID `14698396762`, independently downloaded SHA-256 `10dae7df18dc0fe4e6cf2bf2e3737641af012b1cc3c966de52186cc155ae7212`; ZIP integrity clean. Raw `r5-r1-interrupt.txt`, `r5-r1-tamper.txt`, `r5-r1-rollback.txt`, `r5-r1-upgrade.txt` and source-test receipts are included.

## Explicit scope limits

R5-R1 is only a **disposable Debian QEMU** no-network and synthetic-denial acceptance path. It is not equivalent to production Debian/VPS packaging, a migration-aware cross-version upgrade or verified physical human intent. Root compromise is not within the threat model. A credential-bearing rollback is deliberately refused; encrypted credentials are never deleted by the installer. Interruption during backup preparation or during rollback itself, repeated hard power loss and encrypted-schema upgrades require separate negative and recovery acceptance. Production release, migration, R2C, provider verification and all financial/LIVE gates remain open. No branch merge into `main`.
