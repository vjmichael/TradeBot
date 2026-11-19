"""TB-02: fixed Kraken public data and isolated, paper-only Freqtrade config."""
import json
import math
import time
from pathlib import Path
from typing import Literal

import httpx

IMAGE = "ghcr.io/freqtrade/freqtrade@sha256:8ab9efee447bedb5b9e31eeaaca0b11252168d6d86c57404c3494d5b43f5538c"
SYMBOLS = {"BTC/USD": ("XBTUSD", "XXBTZUSD"), "ETH/USD": ("ETHUSD", "XETHZUSD"), "SOL/USD": ("SOLUSD", "SOLUSD")}
INTERVALS = (5, 15, 60, 240, 1440)


class MarketUnavailable(RuntimeError):
    pass


def candles(pair: str, minutes: int = 60, *, client=None, now=None):
    """Public GET only; never send the vault, API keys, cookies or private requests."""
    if pair not in SYMBOLS or type(minutes) is not int or minutes not in INTERVALS:
        raise ValueError("unsupported market or interval")
    symbol, result_key = SYMBOLS[pair]
    try:
        if client is None:
            with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as transport:
                return candles(pair, minutes, client=transport, now=now)
        response = client.get("https://api.kraken.com/0/public/OHLC", params={"pair": symbol, "interval": minutes})
        response.raise_for_status()
        payload = response.json()
        if payload["error"]:
            raise ValueError("exchange error")
        result = payload["result"]
        if set(result) != {result_key, "last"}:
            raise ValueError("unexpected market response")
        rows = result[result_key]
        if not 2 <= len(rows) <= 720:
            raise ValueError("invalid candles length")
        observed = int(time.time()) if now is None else now
        step = minutes * 60
        clean = []
        previous = -1
        for row in rows:
            if len(row) != 8 or type(row[0]) is not int:
                raise ValueError("invalid candle shape")
            ts = row[0]
            o, h, l, c, v = (float(row[i]) for i in (1, 2, 3, 4, 6))
            if ts <= previous or ts % step or ts > observed or not all(math.isfinite(x) for x in (o, h, l, c, v)) or not (0 < l <= min(o,c) <= max(o,c) <= h and v >= 0):
                raise ValueError("invalid candle values")
            previous = ts
            clean.append([ts * 1000, o, h, l, c, v])
        # Kraken documents the final row as always uncommitted. Never use it.
        closed = clean[:-1]
        if closed[-1][0] // 1000 + step > observed or observed - closed[-1][0] // 1000 > 2 * step:
            raise ValueError("stale or unfinished candles")
        gaps = sum(b[0] - a[0] != step * 1000 for a, b in zip(closed, closed[1:]))
        return {"exchange": "kraken", "product": "spot", "pair": pair, "interval_minutes": minutes, "observed_at": observed, "gap_count": gaps, "candles": closed, "coverage": "recent_only"}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, IndexError) as exc:
        raise MarketUnavailable("Kraken public market data unavailable or invalid") from None


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
        "cancel_open_orders_on_exit": True,
        "exchange": {"name": "kraken", "key": "", "secret": "", "pair_whitelist": [spec.pair], "pair_blacklist": []},
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "exit_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "unfilledtimeout": {"entry": 10, "exit": 10, "exit_timeout_count": 0, "unit": "minutes"},
        "order_types": {"entry": "market", "exit": "market", "stoploss": "market", "stoploss_on_exchange": False},
        "order_time_in_force": {"entry": "GTC", "exit": "GTC"},
        "telegram": {"enabled": False}, "api_server": {"enabled": False}, "dataformat_ohlcv": "json",
        "freqai": {"enabled": False},
    }
    if qualification:
        # Backtesting only, never used by the paused runtime. Synthetic data, not a strategy recommendation.
        config.update(strategy="FreqAIQualification", fee=0.008, freqaimodel="LightGBMRegressor", freqai={
            "enabled": True, "identifier": "tb02-smoke", "train_period_days": 7, "backtest_period_days": 2,
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
