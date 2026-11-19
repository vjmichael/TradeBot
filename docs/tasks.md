# Master development ledger

No automatic progression to real money. Design v0.7 plus decisions.md governs every group. Acceptance must demonstrate behavior, not just source presence. Consolidate tests per task group. No live keys in CI; no paid resources without authorization.

| ID | Scope / acceptance | State |
|---|---|---|
| TB-01 | Repository, design, ledger, local authenticated API, SQLite journal, paper-only instances and bounded virtual allocations, internal encrypted credentials, dry-run contract, negative/restart tests | Local 8/8 + exact GitHub publication 7d483dbb; foundation checks passed on d2064b8a (44455535003) |
| TB-02 | Pin and qualify Freqtrade/FreqAI upstream images, Kraken public spot data and engine dry-run contract; no order capability; verified simulated lifecycle | Foundation run 44456262780 passed at 8e915931; native run 44456262723 pulled pinned image, failed at Kraken public OHLC; bounded retry/diagnostic and independent-stage repair candidate; native acceptance pending |
| TB-03 | React responsive dashboard, authenticated setup, add-instance progression, economic calendar, privacy-safe health, HTTPS/access controls and secure credential entry | Pending |
| TB-04 | Point-in-time historical ingestion, source licenses, coverage/gaps (5+ years where available), venue/product identity, resumable jobs, shared evidence, costs | Pending |
| TB-05 | Grok Scout/Researcher/Analyst/Strategist/Reviewer, structured evidence and proposals, model isolation, budgets, stale/conflict/injection tests | Pending |
| TB-06 | FreqAI numerical and AI-derived features, chronological purged splits, leakage controls, frozen baseline/ablation and fee-aware replay | Pending |
| TB-07 | Independent deterministic Gate, intent ledger, idempotent reconciliation, ownership, exposure limits, protection, no AI execution authority; non-money drills | Pending |
| TB-08 | Multi-instance shared data and isolated portfolios, allocation/reservations, preparation lifecycle, paper switch with separate live confirmation and protected exits | Pending |
| TB-09 | Central Telegram Desk, external Recovery bot, allowlisted chats, test messages, durable controls and secondary email | Pending |
| TB-10 | Signed one-click supported Linux VPS deployment, private APIs, HTTPS, external heartbeat/recovery, backups, upgrade/rollback; graphical fresh-host acceptance | Pending |
| TB-11 | Consolidated functional/security and failure drills per design section 9; real venue eligibility and stop/ownership qualification; 24h laptop-off check | Pending |
| TB-12 | At least seven consecutive days real-time simulated trading after historical readiness; freeze evaluation and extend as needed, no automatic live promotion | Pending |

The design's Phase 0 secure deployment gate precedes operational Phase 1 research and Phase 2 paper-loop acceptance. Code development of later modules does not waive that gate. Two-week research-quality evaluation and longer frozen forward tests remain required. Live canary is a separate explicitly authorized future decision, never a task completion side effect.
