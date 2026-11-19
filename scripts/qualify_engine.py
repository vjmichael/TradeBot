"""Developer/CI qualification, not a VPS installer or an HTTP-accessible launcher."""
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import IMAGE, PLATFORM, candles, write_config
from tradebot import InstanceSpec, PAIRS

ROOT = Path(__file__).resolve().parents[1]


def docker(work, name, args):
    return ["docker", "run", "--rm", "--platform", PLATFORM, "--name", name, "--user", f"{os.getuid()}:{os.getgid()}",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=2", "--memory=3g", "--pids-limit=256",
            "--env", "HOME=/tmp", "--mount", f"type=bind,source={work},target=/work", IMAGE, *args]


def cleanup(name):
    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)


def run(work, args, timeout=240):
    name = "tb02-" + uuid4().hex
    try:
        return subprocess.run(docker(work, name, args), check=True, timeout=timeout, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        print(exc.stdout, exc.stderr, file=sys.stderr)
        raise
    finally:
        cleanup(name)


def setup(work, qualification=False):
    spec = InstanceSpec(name="Qualification BTC", pair="BTC/USD", virtual_cents=100000)
    write_config(work, spec, qualification=qualification)
    strategies = work / "strategies"
    strategies.mkdir()
    name = "FreqAIQualification.py" if qualification else "NoEntry.py"
    shutil.copyfile(ROOT / "engine_strategies" / name, strategies / name)


def stopped_runtime(work):
    setup(work)
    name = "tb02-" + uuid4().hex
    cmd = docker(work, name, ["trade", "--config", "/work/config.json", "--userdir", "/work", "--strategy-path", "/work/strategies"])
    with (work / "runtime.log").open("w") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError("Freqtrade exited before stopped initialization")
                if (work / "paper.sqlite").exists() and "STOPPED" in (work / "runtime.log").read_text():
                    break
                time.sleep(1)
            else:
                raise TimeoutError("no stopped-state evidence")
        finally:
            cleanup(name)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            print((work / "runtime.log").read_text())
    with sqlite3.connect(work / "paper.sqlite") as db:
        if db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] != 0:
            raise AssertionError("stopped engine created a trade")
    print("RECEIPT: real Freqtrade stopped dry-run, zero trades")


def backtest(work):
    setup(work, qualification=True)
    directory = work / "data" / "kraken"
    directory.mkdir(parents=True)
    rows = []
    start = 1735689600000  # 2025-01-01 UTC
    for i in range(24 * 32):
        price = 90000 + 4000 * math.sin(i / 7) + 1800 * math.cos(i / 19) + 12 * i
        close = price + 250 * math.sin(i / 3)
        rows.append([start + i * 3600000, price, max(price, close) + 500, min(price, close) - 500, close, 10 + i % 17])
    (directory / "BTC_USD-1h.json").write_text(json.dumps(rows))
    result = run(work, ["backtesting", "--config", "/work/config.json", "--userdir", "/work", "--strategy-path", "/work/strategies", "--freqaimodel", "LightGBMRegressor", "--timerange", "20250112-20250122", "--cache", "none", "--export", "trades"], timeout=600)
    print(result)
    models = list((work / "models").rglob("*_model.*"))
    if not models:
        raise AssertionError("FreqAI produced no saved model")
    trades = []
    for archive in (work / "backtest_results").glob("*.zip"):
        with ZipFile(archive) as bundle:
            for member in bundle.namelist():
                if member.endswith(".json") and not member.endswith("_config.json"):
                    data = json.loads(bundle.read(member))
                    trades.extend(data.get("strategy", {}).get("FreqAIQualification", {}).get("trades", []))
    if not trades or not all(t.get("pair") == "BTC/USD" and t.get("close_date") and t.get("fee_open", 0) > 0 and t.get("fee_close", 0) > 0 for t in trades):
        raise AssertionError("no valid completed simulated trades with fees")
    print(f"RECEIPT: synthetic FreqAI trained {len(models)} model artifacts, {len(trades)} completed backtest trades; not performance evidence")


def main():
    subprocess.run(["docker", "pull", "--platform", PLATFORM, IMAGE], check=True, timeout=600)
    for pair in PAIRS:
        snapshot = candles(pair)
        print(f"RECEIPT: Kraken public {pair}: {len(snapshot['candles'])} closed candles, {snapshot['gap_count']} gaps")
    with tempfile.TemporaryDirectory(prefix="tb02-") as tmp:
        base = Path(tmp)
        listing = run(base, ["list-pairs", "--exchange", "kraken", "--base", "BTC", "ETH", "SOL", "--quote", "USD", "--print-json"])
        if not set(PAIRS).issubset(json.loads(listing)):
            raise AssertionError("Kraken pair qualification failed")
        print("RECEIPT: Freqtrade Kraken market listing confirms all three pairs")
        for folder, check in (("stopped", stopped_runtime), ("backtest", backtest)):
            work = base / folder
            work.mkdir(mode=0o700)
            check(work)


if __name__ == "__main__":
    main()
