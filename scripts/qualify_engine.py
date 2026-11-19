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


def docker(work, name, args, *, entrypoint=ENGINE_ENTRYPOINT, network_none=False):
    return ["docker", "run", "--rm", "--platform", PLATFORM, "--name", name, "--pull=never", "--user", f"{ENGINE_UID}:{ENGINE_GID}",
            "--entrypoint", entrypoint,
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=2", "--memory=3g", "--pids-limit=256",
            *(["--network", "none"] if network_none else []),
            "--env", f"HOME={ENGINE_HOME}", "--mount", f"type=bind,source={work},target=/work", IMAGE, *args]


def cleanup(name):
    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)


def run(work, args, timeout=240, *, entrypoint=ENGINE_ENTRYPOINT, network_none=False):
    name = "tb02-" + uuid4().hex
    try:
        result = subprocess.run(docker(work, name, args, entrypoint=entrypoint, network_none=network_none), check=True, timeout=timeout, capture_output=True, text=True)
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


NATIVE_CONFIG_CHECK = """import json, sys
from freqtrade.configuration.config_validation import validate_config_consistency
from freqtrade.enums import RunMode
with open('/work/config.json') as file:
    config = json.load(file)
backtest = sys.argv[1] == 'backtest'
assert config['dry_run'] is True and config['trading_mode'] == 'spot'
assert config['initial_state'] == 'stopped' and config['force_entry_enable'] is False
assert not config['exchange']['key'] and not config['exchange']['secret']
assert config['telegram']['enabled'] is False and config['api_server']['enabled'] is False
assert config['freqai']['enabled'] is backtest
for key in ('override_exchange_check', 'override_exchange_checks'):
    assert config['freqai'].get(key, False) is backtest
assert config['strategy'] == ('FreqAIQualification' if backtest else 'NoEntry')
config['runmode'] = RunMode.BACKTEST if backtest else RunMode.DRY_RUN
if backtest:
    config['timerange'] = '20250112-20250122'
validate_config_consistency(config)
print('RECEIPT: upstream config validated: ' + sys.argv[1])
"""


def validate_native_config(work, *, qualification=False):
    mode = "backtest" if qualification else "paper"
    result = run(work, ["-c", NATIVE_CONFIG_CHECK, mode], entrypoint="/usr/local/bin/python")
    print(result, flush=True)
    if f"RECEIPT: upstream config validated: {mode}" not in result.splitlines():
        raise AssertionError("native config validation produced no receipt")


def stopped_runtime(work):
    setup(work)
    validate_native_config(work)
    name = "tb02-" + uuid4().hex
    cmd = docker(work, name, ["trade", "--config", "/work/config.json", "--userdir", "/work", "--strategy-path", "/work/strategies"])
    with (work / "runtime.log").open("w") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError("Freqtrade exited before stopped initialization")
                if (work / "paper.sqlite").exists() and "state='STOPPED'" in (work / "runtime.log").read_text():
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


def synthetic_rows():
    """Exact deterministic fixture; never claim this is Kraken history."""
    rows = []
    start = 1735689600000  # 2025-01-01 UTC
    for i in range(24 * 32):
        price = 90000 + 4000 * math.sin(i / 7) + 1800 * math.cos(i / 19) + 12 * i
        close = price + 250 * math.sin(i / 3)
        rows.append([start + i * 3600000, price, max(price, close) + 500, min(price, close) - 500, close, 10 + i % 17])
    return rows


def verify_synthetic_fixture(path):
    """Reject missing, modified or truncated replay input before enabling the override."""
    if json.loads(path.read_text()) != synthetic_rows():
        raise AssertionError("synthetic history fixture mismatch")


def backtest(work):
    directory = work / "data" / "kraken"
    directory.mkdir(parents=True)
    fixture = directory / "BTC_USD-1h.json"
    with fixture.open("x") as file:
        json.dump(synthetic_rows(), file, allow_nan=False)
    verify_synthetic_fixture(fixture)
    setup(work, qualification=True)
    validate_native_config(work, qualification=True)
    print("RECEIPT: verified 768 synthetic hourly rows for offline replay; not Kraken history", flush=True)
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


def gate_native_bridge(work):
    # Copy the exact candidate modules into the pinned native environment.
    # Synthetic book and isolated database only; no public or private requests.
    for path in ("financial_ledger.py", "reserve_policy.py", "risk_gate.py",
                 "paper_execution.py", "tests/test_risk_gate.py",
                 "engine_strategies/GatePaper.py", "scripts/qualify_gate_native.py"):
        shutil.copyfile(ROOT / path, work / Path(path).name)
    result = run(work, ["/work/qualify_gate_native.py"], timeout=180,
                 entrypoint="/usr/local/bin/python", network_none=True)
    print(result, flush=True)
    if "RECEIPT: native Freqtrade method-level paper round trip, actual native ORM and wallet, synthetic book, held after exact ledger import" not in result.splitlines():
        raise AssertionError("missing native bridge receipt")


def main():
    # Image is a prerequisite for container tests. If it fails, still test the
    # direct public adapter but do not implicitly pull or run a different image.
    with tempfile.TemporaryDirectory(prefix="tb02-") as tmp:
        base = Path(tmp)
        stopped = base / "stopped"
        replay = base / "backtest"
        bridge = base / "gate-bridge"
        for work in (stopped, replay, bridge):
            work.mkdir(mode=0o700)
        for work in (base, stopped, replay, bridge):
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
            ("TB-07A2 native Gate accounting bridge (offline fixture)", lambda: native(gate_native_bridge, bridge)),
            ("FreqAI synthetic backtest", lambda: native(backtest, replay)),
        ])


if __name__ == "__main__":
    main()
