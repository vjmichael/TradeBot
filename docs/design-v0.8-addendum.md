# TradeBot design v0.8 — Independent data, research specialist and market-session intelligence

**Date:** 2026-11-18 (Arizona)  
**Status:** Binding design amendment; implementation and evidence remain separately tracked.  
**Authority:** Read with the preserved [v0.7 design](design-v0.7.md), [binding decisions](decisions.md) and [master ledger](tasks.md). This amendment supersedes conflicting v0.7 requirements only on the matters below, including the former hourly AI synthesis cadence. All original security, paper-first, historical readiness, evaluation, deployment and deterministic authorization gates survive. No live trading, new paid subscription or VPS provisioning is authorized here.

## 1. Architecture

TradeBot operates independently of LLMs for data acquisition, normalization, storage, numerical feature calculation, training of numerical models, paper order simulation, reconciliation, risk protection and reporting. Start with Kraken spot BTC/USD, ETH/USD and SOL/USD; Python collectors, FreqAI with LightGBM, deterministic rules, independent Gate, Freqtrade and a DigitalOcean Linux deployment target. Grok is an optional fundamental and linguistic research specialist, capable of independently originating hypotheses within the permitted scope; it is not an execution engine or a prerequisite to numerical-only paper research.

```text
Historical public archives + incremental public market observations
                              |
                     Independent ingestion
                              |
                  Shared intelligence memory
                     /                   \
       Numerical features          Optional Grok research
                 |                       |
          Rules / FreqAI           Fundamental features
                     \                   /
                      Candidate proposals
                              |
                      Deterministic Gate
                              |
                    Freqtrade paper execution
                              |
               Recorded decisions/fills/outcomes
```

## 2. TB-04: AI-independent shared data service

Collect each observation once; make it available to authorized strategies/instances via bounded, read-only queries with explicit venue, instrument, product, timeframe, timestamps, provenance, coverage and last observation. Never expose execution credentials or private account records through this service.

- Bootstrap from permitted official Kraken spot OHLCVT/trade archives and actual available historical observations; target at least five years of price/volume history and retain original v0.7 source coverage targets. Record actual coverage, gaps, licenses, checksums, acquisition and revision timestamps; no invented backfill or silent alternate-venue substitution.
- Continue through public WebSocket feeds and bounded REST reconciliation. Derive closed candles deterministically, exclude forming candles from closed-candle features, use available archives/trade history to repair gaps, expose stale/incomplete data. Kraken's limited recent REST OHLC window cannot supply five years of backfill.
- Import permitted structured macro series, event calendars, derivative/on-chain context and official announcements/RSS/news metadata without an LLM. Distinguish source observation, revision, interpretation and forecast/actual values.
- Durable resumable checkpoints, bounded batches, rate limits, retries/backoff, deduplication, checksum/manifest validation, restart and reconnect recovery; share collectors/cache rather than duplicate data per instance.
- Public prices are real observed market data. Paper fills, fees and returns are simulated locally and labelled as such; no public market-data feed supplies actual simulated account outcomes.
- Collection incurs zero Grok API charges. Public availability does not imply unrestricted licensing or zero hosting, storage, compute or source costs. No unapproved paid service.

Acceptance: actual historical import and incremental public observations for all three pairs; measured coverage rather than claims; gap, stale, revision and overlap handling, restart and read-only query boundaries. Demonstrate collection and deterministic feature calculation with all LLM credentials absent and zero calls. Real-time supervised paper trading remains a later gate.

## 3. Database as persistent trading intelligence memory

| Domain | Records |
|---|---|
| Market history | Source-provenanced closed candles, trades, permitted live observations and liquidity/spread summaries |
| Fundamental evidence | Official releases, news/social observations where permitted, revisions and separately identified expiring AI assessments |
| Feature library | Versioned technical, temporal, macro and optional linguistic features with immutable snapshot manifests |
| Trading experience | Proposals including rejects and no-trade, Gate decisions, simulated fills, separately verified actual fills, fees and outcomes |
| Learned knowledge | Model files, feature/pipeline versions, evaluations, comparable cases and reviewed research findings |

Use SQLite WAL for transactional metadata/evidence/decision ledgers, partitioned compressed Parquet for market history and immutable bulk feature datasets, and DuckDB for bounded read-only analytical queries over published partitions. Keep versioned native model artifacts outside the database with checksummed references. Use explicit writer ownership and atomic publication of validated data manifests, recoverable migrations, consistent backup/restore and storage monitoring. Preserve exact records as authoritative; no HDC vectors as transaction truth. Secrets remain in the separate encrypted vault.

Retain long-duration closed candles and necessary evidence/outcome lineage where permitted; avoid indefinite full-depth order-book/tick retention by default. Bound high-rate raw buffers and compute useful aggregates while maintaining replay provenance. Measure actual storage/retention and I/O before sizing the VPS; configure disk budgets and low-space alerts. Disk-full, missing or corrupt data never count as valid. Do not delete artifacts needed by pinned evaluations or protection obligations. Distinguish event time from availability time so an as-of query cannot see future returns or later revisions.

## 4. TB-04/TB-06: time-of-day and trading-session intelligence

Crypto spot is continuously open (except venue outages/maintenance), but volatility, volume, spread, market depth and slippage vary by time and day. Do not hard-code Tokyo/Japan or the United States as an always-profitable window. Research identifies substantial London–New York overlap activity, including a cross-exchange 16:00–17:00 UTC peak in one study, and changes toward US hours in newer Bitcoin research. JPY pairs may exhibit different Asian-session behavior. These are hypotheses, not measurements of our three current Kraken markets or guaranteed direction.

Evidence for prioritizing this research:
- Brauneis, Mestel and Theissen, [The crypto world trades at tea time](https://doi.org/10.1007/s11156-024-01304-1), cross-exchange intraday volume/volatility/liquidity.
- [The intraday dynamics and intraday price discovery of bitcoin](https://www.sciencedirect.com/science/article/abs/pii/S0275531922000137), USD/EUR versus JPY session differences.
- [Coinbase Institutional: Trading Activity from a US Lens](https://www.coinbase.com/institutional/research-insights/research/market-intelligence/trading-activity-from-a-us-lens), institutional/ETF-era activity and volatility shifts.
- [Bitcoin on Wall Street Time](https://doi.org/10.3390/jrfm19090707), historical Kraken hourly evidence and US daylight-saving relationship.

Introduce a deterministic session profiler, not an AI task. Store timestamps UTC and derive IANA time-zone-aware calendar features (Asia/Tokyo, Asia/Hong_Kong, Europe/London, America/New_York, America/Phoenix for display), local market openings/closures, overlaps, weekend/holiday indicators and scheduled macro-release proximity. Crypto has no universal session boundary; treat these traditional-market windows as research anchors. Maintain 24-hour and 7-by-24 (168 hourly weekday bins) profiles independently for BTC/USD, ETH/USD, SOL/USD. Analyze full historical coverage plus rolling recent windows (initial candidates 30/90/365 days), sample sizes, realized volatility from available sub-hour log returns, high-low ranges, absolute returns, volume, spread/depth and modeled execution costs with gaps and missingness explicit. Never infer liquidity from volatility alone. Separate weekend and weekday behavior, asset, venue and regimes; compare older periods with recent periods and account for DST. Use point-in-time features calculated using only data available before the decision.

TB-04 produces real measured profiles and a dashboard activity/volatility heatmap with UTC/local-time views, units, coverage and last refresh. TB-06 tests time/session features, session-conditioned strategy performance, expected fees/slippage, drawdowns and sample uncertainty against a no-time-filter baseline with chronological purged splits and held-out forward evaluation. Do not confuse high volatility or volume with net tradable edge, optimize on the final test set, or force all assets to trade at the same time. Data collection, risk protection and independently authorized paper strategies run around the clock. No fixed trading restriction or live strategy change is authorized by this amendment.

## 5. TB-05: Grok as bounded fundamental specialist

Replace hourly generative synthesis with normally one to three shared desk-wide research sessions daily: proposed default two, third available for a material event. A session can include bounded role-specific calls and searches; session count is not a billable request count. Suggested initial scheduling hypotheses: pre-US activity (before major economic releases/US equity open) and pre-Asia (before Tokyo open), with the third slot for important European/US developments or material events. Anchor schedules to real market time zones rather than hard-coded UTC offsets; refine via measured session profiles and research utility. Keep a configurable cap and explicit budget; no automatic extra calls when volatility is high. Do not force spending to meet a minimum if disabled, unavailable or exhausted. No extra per-asset, reviewer, fallback, retry or alert-triggered sessions. A separately approved, bounded historical linguistic enrichment experiment is optional, not a numerical bootstrap requirement.

Scout investigates material news, X and economic/project events even without a numeric candidate. Researcher corroborates, Analyst produces typed features (event type, impact, horizon, uncertainty, evidence links, expiry and where available social activity/manipulation observations), Strategist may propose, Reviewer measures outcomes. Batch across three assets, reuse permitted cached results until expiry; routine monitoring/reporting is deterministic. Do not spend model tokens to reproduce structured numerical APIs. Compute scores/rates locally. Preserve exact result, original and observation times, model/prompt/schema, provenance, unknowns, contradictions and costs; historical AI-derived features are exploratory when pretraining could leak later information. Do not transplant the source article's memecoin dip trigger or fixed social weight without evidence.

Enforce one persistent desk-wide session plus call/token/search/retry reservation and accounting budget. Per-instance planning cannot multiply that budget. Record actual and reserved spend, shared versus marginal costs; original alert thresholds; fail closed on uncertain costs, exhaustion or missing required research. No unapproved fallback, charge escalation or dollar ceiling invented here. No account secrets or order tools sent to AI.

Acceptance: controlled clock and fake metered provider verify shared schedule/cap including DST, duplicate suppression, bounded calls, budgets, cache expiry, no-trade and zero calls when disabled; qualify actual permitted research under authorized budget separately.

## 6. TB-06–TB-08: independent learning and comparisons

Train verified numerical-only FreqAI/LightGBM before introducing optional fundamental features. Compare primary paper cohorts A rules-only, B numerical FreqAI, C FreqAI plus Grok using shared time-consistent evidence, independent virtual ledgers, comparable constraints, starting capital, fee/spread/slippage assumptions, versioned models, purged chronological splits and frozen evaluation. Retain original cash/buy-and-hold/random and relevant veto/reduction/research-originated ablations. Report net portfolio value, drawdown, uncertainty, calibration, feature usefulness and actual data/AI costs, including marginal research value and time/session utility. No retrospective selection or profit promise.

Independently approved numerical-only paper strategies may run with no LLM credentials or AI budget if all their own data, health, pause, capital and risk requirements pass. Research-dependent strategy entries require their own fresh required research; no silent strategy substitution. Every intent passes deterministic Gate; protection and recovery never wait on AI. Preserve existing conservative live-entry blocks on AI outage/budget exhaustion; live remains separately authorized. GUI shows measured collector coverage/freshness, session heatmap, storage usage, numerical versus optional research readiness, shared spend and last/next research session. Unknown remains unknown.

## 7. Deferred post-TradeBot Cognitum research — TB-R01

Finish TB-01–TB-12 and original evaluation requirements first, without requiring live money. Cognitum integration is a separately authorized subsequent research effort, not a dependency, substitute for FreqAI or change to Cognitum's current development. Design model-agnostic data/feature interfaces now; no Cognitum implementation in the core backlog.

Later investigate a read-only adapter from frozen permitted point-in-time datasets to versioned experimental predictions/associative results. HDC may retrieve similar market regimes/cases while preserving exact evidence references; KAN may model nonlinear relationships. Neither is authoritative transactional storage nor the risk Gate; no credentials, execution rights or automated policy promotion. Compare HDC-only, KAN-only, combined and optional fundamental-enriched variants against rules/LightGBM on frozen historical splits and forward shadow/paper observations. Measure retrieval against conventional/exact baseline, prediction/calibration, net simulated results, drawdown, regime robustness, latency, resource usage and reproducibility. Promotion would require independent evidence and operator approval. No promised outperformance, FPGA integration or native kernel rewrite now.

## 8. Work mapping

TB-04 independent ingestion, memory and session profiling; TB-05 limited specialist; TB-06 baseline, temporal features and AI ablations; TB-07 unchanged deterministic authority; TB-08 shared infrastructure and isolated portfolios; TB-10 storage sizing, backups and DigitalOcean qualification; TB-11 AI-absence/outage, caps, session correctness and original security/fault drills; TB-12 required real-time paper observation and forward evaluation. Deferred TB-R01 only afterward. No waiver of any existing deployment, recovery, safety, human approval or historical-readiness gate.

### TB-R01 experiment clarification — 2026-11-18

After completing TradeBot, an optional fun, separately scoped Cognitum research project may consume frozen permitted database snapshots, continuous genuine public market observations, and TradeBot paper-decision/fill/outcome streams via a read-only versioned adapter. Connect Cognitum only to independently isolated virtual portfolios and simulated venue adapters, with matched clocks, fees, spreads, slippage, capital and deterministic authorization. Keep observations and synthetic fills explicitly distinct; use delayed outcome labels and prevent future-outcome leakage. No connection to a live-order endpoint, trading key or real-money account. The adapter is deferred, not part of TB-04 or a replacement of the accepted database/FreqAI/Gate. Establish reproducible baseline comparisons before claiming any improvement.

## Financial goals and profit protection amendment

See binding [TB-FIN-01](tb-fin-01.md). Profit harvesting defaults to immediate protection of 100% of verified settled realized net profit after trading fees. No profits are reinvested without explicit amount-specific human authorization; no reserve replenishment of losses. Daily dollar and normalized return-point goals and monthly household planning are reporting metrics, never trade quotas. The risk Gate excludes reserves even if they remain on exchange. No bot withdrawal permissions; manual external transfer distinct from ledger protection. Financial accounting, UI, paper evaluation and release acceptance are mapped to TB-07–12. The amendment preserves all existing risk/security/AI-independence gates.

### Optional reinvestment refinement

TB-FIN-01C explicitly allows a human to authorize a bounded expiring recurring profit-only policy through a separate widget, default disabled and rate 0%; never a percentage of account value. Fixed-dollar/percentage/combined protected-profit floor and hard trading bankroll ceiling are required. Profit protected immediately, release evaluated once per anchored 24 hours using only new eligible surplus, no repeat/catch-up or automatic loss refill; continuous market sessions share the same capital limit. See [financial design](tb-fin-01.md) for details. The existing prohibition on unauthorized compounding remains binding.
