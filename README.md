# TradeBot

A 24/7 cloud trading desk based on [design v0.7](docs/design-v0.7.md) and the binding [implementation decisions](docs/decisions.md). See the [task ledger](docs/tasks.md).

**TB-02 development candidate. Paper-only, paused on startup. Public Kraken market data adapter and isolated native qualification harness; no live order capability, no deployed services, no operational trading readiness.** The localhost API requires a developer-supplied admin token. Do not expose it publicly; production HTTPS, authentication, React dashboard and graphical setup remain release gates.

Developer checks (not end-user installation): install `requirements.txt`, then run `python -m unittest discover -s tests -v`. For local API development only, set `TRADEBOT_ADMIN_TOKEN` to a random secret of at least 32 characters, and `TRADEBOT_DATA_DIR` to a private directory, then run `python -m tradebot`. The eventual supported VPS deployment will require no user-entered shell commands. Never commit credentials.

Developer-only native acceptance: `python scripts/qualify_engine.py` on a qualified Linux host with Docker, internet and the pinned FreqAI image. This is not the end-user installer. See [TB-02](docs/tb02.md).
