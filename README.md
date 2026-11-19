# TradeBot

A 24/7 cloud trading desk based on [design v0.7](docs/design-v0.7.md) and the binding [implementation decisions](docs/decisions.md). See the [task ledger](docs/tasks.md).

**TB-01 development foundation only. Paper-only, paused on startup. No orders, live trading, exchange connectivity, or deployed services.** The localhost API requires a developer-supplied admin token. Do not expose it publicly; production HTTPS, authentication, React dashboard and graphical setup remain release gates.

Developer checks (not end-user installation): install `requirements.txt`, then run `python -m unittest discover -s tests -v`. For local API development only, set `TRADEBOT_ADMIN_TOKEN` to a random secret of at least 32 characters, and `TRADEBOT_DATA_DIR` to a private directory, then run `python -m tradebot`. The eventual supported VPS deployment will require no user-entered shell commands. Never commit credentials.
