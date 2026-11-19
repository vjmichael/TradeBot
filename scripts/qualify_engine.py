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
import traceback
from uuid import uuid4
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import IMAGE, PLATFORM, candles, write_config
from tradebot import InstanceSpec, PAIRS

ROOT = Path(__file__).resolve().parents[1]
# Freqtrade 2026.10 installs its entrypoint and dependencies as ftuser.
ENGINE_UID = 1000
ENGINE_GID = 1000
ENGINE_HOME = "/home/ftuser"
ENGINE_ENTRYPOINT = ENGINE_HOME + "/.local/bin/freqtrade"


def workspace_acl(work):
    """Share only the isolated scratch tree with upstream ftuser and the host operator."""
    host = os.getuid()
    rules = ["g::---", "o::---", "m::rwx", "d:u::rwx", "d:g::---", "d:o::---", "d:m::rwx"]
    if host != ENGINE_UID:
        rules.extend([f"u:{ENGINE_UID}:rwx", f"d:u:{ENGINE_UID}:rwx", f"d:u:{host}:rwx"])
    return ["setfacl", "-m", ",".join(rules), str(work)]


def prepare_workspace(work):
    subprocess.run(workspace_acl(work), check=True, timeout=15)


def docker(work, name, args):
    return ["docker", "run", "--rm", "--platform", PLATFORM, "--name", name, "--pull=never", "--user", f"{ENGINE_UID}:{ENGINE_GID}",
            "--entrypoint", ENGINE_ENTRYPOINT,
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=2", "--memory=3g", "--pids-limit=256",
            "--env", f"HOME={ENGINE_HOME}", "--mount", f"type=bind,source={work},target=/work", IMAGE, *args]


def cleanup(name):
    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)


def run(work, args, timeout=240):
    name = "tb02-" + uuid4().hex
    try:
        result = subprocess.run(docker(work, name, args), check=True, timeout=timeout, capture_output=True, text=True)
        print(result.stderr, file=sys.stderr, flush=True)
        return result.stdout
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


def qualify_stages(stages):
    """Collect every independent result without converting failures to passes."""
    failures = []
    for label, check in stages:
        print(f"STAGE: {label}", flush=True)
        try:
            check()
        except Exception:
            failures.append(label)
            traceback.print_exc()
            print(f"FAILED: {label}", flush=True)
        else:
            print(f"PASSED: {label}", flush=True)
    if failures:
        raise RuntimeError("Required acceptance stages failed: " + ", ".join(failures))


def public_market(pair):
    snapshot = candles(pair)
    print(f"RECEIPT: Kraken public {pair}: {snapshot['raw_candle_count']} raw rows, {len(snapshot['candles'])} closed candles, {snapshot['gap_count']} gaps", flush=True)


def runtime_version(base):
    version = run(base, ["--version"])
    print(version, flush=True)
    if "2026.10" not in version:
        raise AssertionError("unexpected Freqtrade version")


def market_listing(base):
    listing = run(base, ["list-pairs", "--exchange", "kraken", "--base", "BTC", "ETH", "SOL", "--quote", "USD", "--print-json"])
    if not set(PAIRS).issubset(json.loads(listing)):
        raise AssertionError("Kraken pair qualification failed")
    print("RECEIPT: Freqtrade Kraken market listing confirms all three pairs", flush=True)


def main():
    # Image is a prerequisite for container tests. If it fails, still test the
    # direct public adapter but do not implicitly pull or run a different image.
    with tempfile.TemporaryDirectory(prefix="tb02-") as tmp:
        base = Path(tmp)
        stopped = base / "stopped"
        replay = base / "backtest"
        for work in (stopped, replay):
            work.mkdir(mode=0o700)
        for work in (base, stopped, replay):
            prepare_workspace(work)
        image_ready = False

        def pull():
            nonlocal image_ready
            subprocess.run(["docker", "pull", "--platform", PLATFORM, IMAGE], check=True, timeout=600)
            image_ready = True

        def native(check, work):
            if not image_ready:
                raise RuntimeError("Pinned image unavailable; native stage blocked")
            check(work)

        qualify_stages([
            ("pinned image", pull),
            *((f"Kraken public {pair}", lambda pair=pair: public_market(pair)) for pair in PAIRS),
            ("Freqtrade runtime version", lambda: native(runtime_version, base)),
            ("Freqtrade market listing", lambda: native(market_listing, base)),
            ("Freqtrade stopped runtime", lambda: native(stopped_runtime, stopped)),
            ("FreqAI synthetic backtest", lambda: native(backtest, replay)),
        ])


if __name__ == "__main__":
    main()
