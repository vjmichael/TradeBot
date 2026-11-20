# TB-R01 — Deferred Cognitum data discovery and simulated-market research

Status: documented research proposal only. Finish Maj Vaj Trade Bot's TB-01–TB-12 core and required paper evaluation first. Real-money trading is not a prerequisite. Separately authorize experiments before implementation; no Cognitum dependency, production control, or live enablement is authorized here.

## Research hypotheses

Cognitum may be useful beyond its current application domains wherever permitted data can be represented and meaningful learning/evaluation objectives defined. Investigate using HDC associative representations and retrieval, KAN nonlinear models, and VFA/typed exchange where appropriate, to discover relationships, derive features, generate testable hypotheses and produce new derived mathematical data. Do not present generated data as observed market fact. The capacity to learn, generalize, and improve over baselines is a hypothesis, not an established result.

## Proposed experiments

- **TB-R01A: Read-only data mining.** Consume frozen, versioned market history, macro/evidence where licensed, public observations and deidentified permitted paper-decision/fill/outcome records. Start with trading-data-only versus combined evidence cohorts. Compare exact retrieval and conventional statistics against HDC regime retrieval, KAN modeling and combinations; record provenance, uncertainty and reproducibility. Where needed, fit models on training partitions only; distinguish representations, retrieval and trainable models.
- **TB-R01B: Historical replay.** Give Cognitum only information available at each replay instant, including publication/vintage lags and delayed outcomes; train on strictly earlier data. Evaluate frozen chronological holdouts with purging/embargo and no retrospective parameter selection. Compare rules, FreqAI/LightGBM and Cognitum under identical virtual capital, fees, spread, slippage, universe, clock and risk rules. Measure net performance, drawdown, calibration, robustness, data/model costs and compute use; report negative/null findings too.
- **TB-R01C: Independent simulated markets.** Investigate an isolated crypto replay, equity-market simulator or licensed external paper/simulation venue after separate venue and data authorization. For equity experiments obtain appropriately licensed equity data, corporate actions, delistings, exchange hours and distinct accounting/fees, rather than assume crypto-trained relationships transfer. Use time-accurate prices and realistic simulated order handling where supported. Cognitum may submit experimental intents solely to an independent simulated execution/risk adapter; it cannot place real orders. Synthetic market stress generators may explore counterfactuals, clearly labeled and kept separate from genuine observations. Forward shadow/paper results test out-of-sample robustness, not live profitability.

## Mandatory isolation

One-way data export or read-only scoped access from Maj Vaj to Cognitum; separate accounts, databases, model checkpoints, runtime, virtual ledgers and simulation endpoints. No trading/withdrawal credentials, production mutation APIs, access to the independent Gate, strategy override, capital/reinvestment authority, or automatic feedback into live/paper execution of Maj Vaj. Experimental predictions, generated features, synthetic datasets and reports are separately stored, versioned, traceable and reviewable. Any later consideration of a derived feature in Maj Vaj requires explicit human review, independent validation and a separate design decision; never direct Cognitum control over Maj Vaj. Neither experiment nor resulting artifact modifies the production source database. No paid resources or live exchanges authorized by this document.

## Relationship to core Maj Vaj

Historical acquisition and feature preparation allow local numerical model bootstrap without waiting seven days merely to collect basic historical candles. TB-12 still requires genuine seven-day minimum forward paper evaluation; neither retrospective history nor a simulator substitutes for it. No claim of superiority over AI_Pirate or any particular trading system without controlled comparative evidence. Freqtrade remains the execution engine, FreqAI the numerical-learning component, optional Grok the contextual research specialist, independent deterministic Gate the authority. Cognitum is a later independent research consumer.

## Study-only research priority

The motivating question was whether Cognitum could learn to trade; study-only research is valuable on its own. Begin with TB-R01A read-only market data mining and mathematical learning, where no order intent or trading agent is required. Simulated-market experiments in TB-R01B/C are optional later research, not prerequisites or permission to trade. Cognitum may discover relationships or create derived data without any trading capability. No production integration/control of Maj Vaj is authorized.


### Multi-scale discovery hypothesis (deferred)

Explore bottom-up aggregation of market observations and derived features into regimes, relationships and hierarchies, then top-down decomposition of candidate associations into supporting or contradictory lower-level events. HDC association retrieval and KAN mathematical modeling may expose previously unseen patterns, including null or spurious associations; neither proves causality or profitability. Freeze time-consistent real history as the observed timeline, generate separately labeled counterfactual trajectories with source lineage, seeds and calibrated assumptions, and test discoveries against untouched genuine observations with controls for multiple comparisons and leakage. Study-only first; no production feedback, financial authority or implementation approval.

**Binding secret boundary:** [TB-SEC-KEY-01](tb-sec-key-01.md) applies to all Cognitum experiments. Only explicitly allowlisted non-secret datasets, derived features and simulated results may cross the research boundary; no API keys, wallet/private signing keys, seeds, credential stores, full desk databases, environment, live signing or execution access. Include this in the synthetic-canary acceptance before integration.
