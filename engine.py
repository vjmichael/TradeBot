"""TB-02: fixed Kraken public data and isolated, paper-only Freqtrade config."""
import json
import math
import time
from pathlib import Path
from typing import Literal

import httpx

# Pin the published Linux/amd64 child manifest, not the broken GHCR index.
IMAGE = "docker.io/freqtradeorg/freqtrade@sha256:deb1853f454761058b75251d4b5d6f6ce441bb89d248c6578d7c0bfee1a6f6c8"
PLATFORM = "linux/amd64"
SYMBOLS = {"BTC/USD": ("XBTUSD", "XXBTZUSD"), "ETH/USD": ("ETHUSD", "XETHZUSD"), "SOL/USD": ("SOLUSD", "SOLUSD")}
INTERVALS = (5, 15, 60, 240, 1440)
MAX_CLOSED_CANDLES = 720


class MarketUnavailable(RuntimeError):
    """Only trusted diagnostic labels, never remote response contents."""


PUBLIC_HEADERS = {"User-Agent": "TradeBot/0.1 (Kraken public market data)", "Accept": "application/json"}
RETRY_DELAYS = (1, 2)
TRANSIENT_EXCHANGE_ERRORS = {"EAPI:Rate limit exceeded", "EService:Unavailable", "EService:Busy", "EGeneral:Temporary lockout"}


def _public_ohlc(client, symbol, minutes):
    for attempt in range(len(RETRY_DELAYS) + 1):
        retry_after = None
        try:
            response = client.get("https://api.kraken.com/0/public/OHLC",
                                  params={"pair": symbol, "interval": minutes}, headers=PUBLIC_HEADERS)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            diagnostic = "transport_" + type(exc).__name__
            retryable = True
        except httpx.HTTPError:
            raise MarketUnavailable("transport_error") from None
        else:
            status = response.status_code
            if status != 200:
                diagnostic = f"http_{status}"
                retryable = status == 429 or 500 <= status <= 599
                retry_after = response.headers.get("Retry-After")
            else:
                try:
                    payload = response.json()
                except ValueError:
                    raise MarketUnavailable("invalid_json") from None
                if not isinstance(payload, dict) or not isinstance(payload.get("error"), list):
                    raise MarketUnavailable("invalid_envelope")
                errors = payload["error"]
                if not errors:
                    return payload
                if all(isinstance(e, str) and e in TRANSIENT_EXCHANGE_ERRORS for e in errors):
                    diagnostic = "exchange_transient"
                    retryable = True
                else:
                    raise MarketUnavailable("exchange_error")
        if not retryable or attempt == len(RETRY_DELAYS):
            raise MarketUnavailable(diagnostic)
        delay = RETRY_DELAYS[attempt]
        if retry_after is not None:
            # Never retry earlier than instructed. Longer or unparseable waits
            # are left for the next scheduled poll, not held in this request.
            if not retry_after.isascii() or not retry_after.isdigit() or len(retry_after) > 3:
                raise MarketUnavailable(diagnostic)
            delay = max(delay, int(retry_after))
            if delay > 5:
                raise MarketUnavailable(diagnostic)
        time.sleep(delay)
    raise AssertionError("unreachable")


def candles(pair: str, minutes: int = 60, *, client=None, now=None, symbols=SYMBOLS):
    """Public GET only; never send the vault, API keys, cookies or private requests."""
    if pair not in symbols or type(minutes) is not int or minutes not in INTERVALS:
        raise ValueError("unsupported market or interval")
    symbol, result_key = symbols[pair]
    if client is None:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as transport:
            return candles(pair, minutes, client=transport, now=now, symbols=symbols)
    payload = _public_ohlc(client, symbol, minutes)
    try:
        result = payload["result"]
        if not isinstance(result, dict) or set(result) != {result_key, "last"}:
            raise MarketUnavailable("unexpected_market_response")
        rows = result[result_key]
        if not isinstance(rows, list):
            raise MarketUnavailable("invalid_candles_type")
        # The current uncommitted interval can be additional to 720 closed rows.
        if not 2 <= len(rows) <= MAX_CLOSED_CANDLES + 1:
            raise MarketUnavailable(f"invalid_candles_length_{len(rows)}")
        observed = int(time.time()) if now is None else now
        step = minutes * 60
        clean = []
        previous = -1
        for row in rows:
            if not isinstance(row, list) or len(row) != 8 or type(row[0]) is not int:
                raise MarketUnavailable("invalid_candle_shape")
            ts = row[0]
            o, h, l, c, v = (float(row[i]) for i in (1, 2, 3, 4, 6))
            if ts <= previous or ts % step or ts > observed:
                raise MarketUnavailable("invalid_candle_timestamp")
            if not all(math.isfinite(x) for x in (o, h, l, c, v)) or not (0 < l <= min(o,c) <= max(o,c) <= h and v >= 0):
                raise MarketUnavailable("invalid_candle_values")
            previous = ts
            clean.append([ts * 1000, o, h, l, c, v])
        # Kraken documents the final row as always uncommitted. Never use it.
        closed = clean[:-1]
        if closed[-1][0] // 1000 + step > observed or observed - closed[-1][0] // 1000 > 2 * step:
            raise MarketUnavailable("stale_or_unfinished_candles")
        gaps = sum(b[0] - a[0] != step * 1000 for a, b in zip(closed, closed[1:]))
        return {"exchange": "kraken", "product": "spot", "pair": pair, "interval_minutes": minutes, "observed_at": observed, "gap_count": gaps, "candles": closed, "raw_candle_count": len(rows), "coverage": "recent_only"}
    except (ValueError, KeyError, TypeError, OverflowError, IndexError):
        raise MarketUnavailable("invalid_market_payload") from None


def paper_config(spec, *, qualification=False):
    """Construct from typed paper instance; no arbitrary config overlay or secret input."""
    from tradebot import InstanceSpec
    if type(spec) is not InstanceSpec:
        raise TypeError("validated instance required")
    spec = InstanceSpec.model_validate(spec.model_dump())
    wallet = spec.virtual_cents / 100
    config = {
        "dry_run": True, "trading_mode": "spot", "margin_mode": "", "dry_run_wallet": wallet,
        "initial_state": "stopped", "force_entry_enable": False, "max_open_trades": 1,
        "stake_currency": "USD", "stake_amount": "unlimited", "tradable_balance_ratio": 0.95,
        "timeframe": "1h", "strategy": "NoEntry", "db_url": "sqlite:////work/paper.sqlite",
        "last_stake_amount_min_ratio": 0.5, "stoploss": -0.05, "minimal_roi": {"0": 0.01},
        "internals": {"process_throttle_secs": 5},
        "cancel_open_orders_on_exit": True,
        "exchange": {"name": "kraken", "key": "", "secret": "", "pair_whitelist": [spec.pair], "pair_blacklist": []},
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "exit_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "unfilledtimeout": {"entry": 10, "exit": 10, "exit_timeout_count": 0, "unit": "minutes"},
        "order_types": {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False},
        "order_time_in_force": {"entry": "GTC", "exit": "GTC"},
        # Upstream validates required nested fields even with enabled=false.
        # These are inert placeholders, not credentials; no RPC listener is permitted.
        "telegram": {"enabled": False, "token": "", "chat_id": ""},
        "api_server": {"enabled": False, "listen_ip_address": "127.0.0.1", "listen_port": 8080,
                       "username": "", "password": "", "jwt_secret_key": "disabled-not-a-secret-unused-by-rpc"},
        "dataformat_ohlcv": "json", "dataformat_trades": "feather",
        "freqai": {"enabled": False, "identifier": "tb02-disabled", "train_period_days": 7,
                   "backtest_period_days": 2,
                   "feature_parameters": {"include_timeframes": ["1h"], "include_corr_pairlist": []},
                   "data_split_parameters": {"shuffle": False}},
    }
    if qualification:
        # Backtesting only, never used by the paused runtime. Synthetic data, not a strategy recommendation.
        config.update(strategy="FreqAIQualification", minimal_roi={"0": 0.015}, fee=0.008, freqaimodel="LightGBMRegressor", freqai={
            "enabled": True, "identifier": "tb02-smoke", "train_period_days": 7, "backtest_period_days": 2,
            # 2026.10 schema documents the singular key; Exchange.validate_freqai reads the plural.
            # Only the isolated synthetic replay may bypass the REST-history check.
            "override_exchange_check": True, "override_exchange_checks": True,
            "save_backtest_models": True, "data_kitchen_thread_count": 2,
            "feature_parameters": {"include_timeframes": ["1h"], "include_corr_pairlist": [], "label_period_candles": 3, "include_shifted_candles": 1, "indicator_periods_candles": [6, 12]},
            "data_split_parameters": {"test_size": 0.2, "shuffle": False},
            "model_training_parameters": {"n_estimators": 20, "n_jobs": 2, "verbosity": -1, "random_state": 42},
        })
    return config


def write_config(work: Path, spec, *, qualification=False):
    path = work / "config.json"
    with path.open("x", encoding="utf-8") as file:
        json.dump(paper_config(spec, qualification=qualification), file, allow_nan=False)
    return path
