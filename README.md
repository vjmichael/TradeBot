# TradeBot

A 24/7 cloud trading desk based on [design v0.7](docs/design-v0.7.md), the binding [v0.8 design addendum](docs/design-v0.8-addendum.md) and [implementation decisions](docs/decisions.md). See the [task ledger](docs/tasks.md).

**TB-03 dashboard/browser acceptance passed; reproducible dependency integrity closeout pending. Paper-only, paused on startup. TB-02 native engine mechanism accepted; the dashboard does not start or control a connected worker, and no deployed services or real order capability exist.** The localhost API requires a developer-supplied admin token. Do not expose it publicly or enter production credentials; production HTTPS, first-administrator authentication, provider verification and deployment are still release gates.

Developer checks (not end-user installation): install `requirements.txt`, then run `python -m unittest discover -s tests -v`. For local API development only, set `TRADEBOT_ADMIN_TOKEN` to a random secret of at least 32 characters, and `TRADEBOT_DATA_DIR` to a private directory, then run `python -m tradebot`. The eventual supported VPS deployment will require no user-entered shell commands. Never commit credentials.

Developer-only native acceptance: `python scripts/qualify_engine.py` on a qualified Linux host with Docker, internet and the pinned FreqAI image. This is not the end-user installer. See [TB-02](docs/tb02.md).

Developer-only dashboard build: install the pinned npm dependencies with `npm --prefix dashboard install --ignore-scripts --no-audit --no-fund`, then `npm --prefix dashboard run build`. Open `http://127.0.0.1:8765/` after starting the local service. Set `TRADEBOT_VAULT_KEY` to a separately managed Fernet key for **test** credential setup; if absent the write-only credential endpoint is unavailable. Browser tests: install Playwright 1.57.0 and Chromium, then `python scripts/test_dashboard_browser.py`. `--offline` runs a synthetic browser fixture, not acceptance of the HTTP integration. See [TB-03](docs/tb03.md). The end-user DigitalOcean Linux deployment will use a graphical installation path at TB-10; do not provision a VPS yet merely for these development checks.
