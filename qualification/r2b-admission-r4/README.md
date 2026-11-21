# TB-SEC-KEY-01-R2B-INTEGRATE-02-R2-R2-ADMISSION-R4-R1

**SCOPED PASS: installed offline synthetic-only adversarial and recovery acceptance.**

The authoritative accepted candidate is `4c4065d110a274b84d9fcc5dad1cf8e46a1b47ec` on `qualification/tb-sec-key-01-r2b-admission-r4`. [GitHub Actions run #44876769051](https://github.com/vjmichael/TradeBot/actions/runs/44876769051) completed **SUCCESS** in original source and installed Debian/QEMU native gates; no production release.

## Native acceptance — seven independently rebooted/isolated guest overlays

| Original preserved scenario | Required exact marker | Verified |
|---|---|---|
| Real operator session lock mid native prompt | `R4_INSTALLED_SESSION_LOCK_REFUSAL_PASS` | PASS |
| LightDM seat switch mid native prompt | `R4_INSTALLED_SEAT_SWITCH_REFUSAL_PASS` | PASS |
| Credential generation replacement mid review | `R4_INSTALLED_GENERATION_RACE_REFUSAL_PASS` | PASS |
| Broker crash/restart revokes unconsumed approval | `R4_INSTALLED_BROKER_RESTART_REVOKES_PASS` | PASS |
| Worker stop after durable one-use consume; no retry | `R4_INSTALLED_WORKER_DEATH_NO_RETRY_PASS` | PASS |
| Worker HMAC key changed independently of owner | `R4_INSTALLED_SIGNING_KEY_MISMATCH_REFUSAL_PASS` | PASS |
| Genuine cold guest reboot, recovery and spent approval refusal | `R4_INSTALLED_COLD_REBOOT_RECOVERY_PASS` | PASS |

All seven `R4_NATIVE_ADVERSARIAL_CASE_PASS <case>` markers, `R4_ALL_NATIVE_ADVERSARIAL_CASES_PASS`, and the reboot scenario's `R2B_R2_INSTALLED_END_TO_END_SYNTHETIC_PASS` were present in the original terminal job log. All 216 source regressions passed: R2=31, R4=11, R1=22, prior admission=51, vault/migration/receipt=71, original handoff=9, adversarial IPC=21. Debian dpkg audit empty.

**Verified artifact:** GitHub Actions artifact `r2b-r4-native-adversarial-evidence` (ID `14696312184`), independent downloaded ZIP SHA-256 `5fd70744cedf68d40994b46a7ec035d0606a1914b8651eb143fadc1e0af5ab96`. Each native case provides stdout markers and QEMU screenshots, plus bounded non-secret broker lineage where relevant.

## Failure reconciliation

- Prior [#44875068293](https://github.com/vjmichael/TradeBot/actions/runs/44875068293): genuine LightDM `Seat0` was present, but the harness used a newline-dependent user assertion and failed. Corrected to an exact anchored user/seat parser, not weaker identity admission.
- Prior [#44875484675](https://github.com/vjmichael/TradeBot/actions/runs/44875484675): seat switching passed but broker restart apparently showed one pending and one revoked. Source was sampling five SQLite states separately. The resulting mixed snapshot did not correspond to two actual outstanding requests.
- [Focused native #44876360678](https://github.com/vjmichael/TradeBot/actions/runs/44876360678): source-locked read-only ledger rows showed the single request was revoked across restart. Later full run counted all states in one SQLite SELECT and independently passed broker restart. Original refused/consumed state constraints remain.
- The cold guest reboot scenario now lets QEMU actually reboot, without `-no-reboot` only in that case; the other cases retain `-no-reboot`.

## Trust boundary and remaining work

The system is an **explicit root-marker-gated, no-network disposable qualification variant** with real separate root-protected service principals, one-use authorization and an authenticated **only-negative** synthetic worker. An operator's X11 UI action is not an unforgeable physical-human attestation on a compromised host. No actual Kraken/Telegram/Grok request, positive permission verification, real credentials, orders, withdrawal, LIVE execution, production release or default-branch merge is authorized. All saved pair statuses remain `stored_unverified`.

Next independent work: production packaging/release integration, separately operator-consented read-only provider sandbox verification with positive least-privilege permission checks, reviewed encrypted credential migration/rotation/recovery, R2C browser/notification/KEK, and financial/LIVE gates. Prior R3 scope and original negative-security gates are retained.
