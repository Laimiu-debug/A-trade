"""Synthetic full-universe scale check. No real data or profit claims."""
import argparse
import ctypes
from datetime import date, timedelta
import gc
import json
import math
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.portfolio_engine import PortfolioEngine


def peak_memory():
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32), ("faults", ctypes.c_uint32)] + [
            (name, ctypes.c_size_t) for name in ("peak", "working", "pagedPeak", "paged", "nonpagedPeak", "nonpaged", "pagefile", "pagefilePeak")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_uint32]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.peak / 1024**2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=5000)
    parser.add_argument("--bars", type=int, default=504)
    args = parser.parse_args()
    if not 1 <= args.symbols <= 9000 or not 80 <= args.bars <= 1000:
        raise ValueError("benchmark bound exceeded")
    dates, current = [], date(2020, 1, 2)
    while len(dates) < args.bars:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    started = time.perf_counter()
    securities = []
    for stock in range(args.symbols):
        bars = []
        for i, day in enumerate(dates):
            close = 20 + stock * .002 + i * .01 + .1 * math.sin(i / 10 + stock * .1)
            bars.append({"date": day, "open": close, "high": close + .01, "low": close - .01,
                         "close": close, "volume": 2000000, "amount": close * 2000000})
        securities.append({"symbol": f"sh{600000+stock}", "bars": bars})
    data = {"securities": securities, "calendar": dates, "source": "synthetic benchmark only",
            "universeCoverage": {"loadedCount": args.symbols, "batchTruncation": False}}
    generation = time.perf_counter() - started
    print(f"generated {args.symbols} securities / {args.symbols*args.bars} bars", flush=True)
    started = time.perf_counter()
    engine = PortfolioEngine(data, {"startDate": dates[60], "endDate": dates[-1], "rebalanceMode": "hybrid"})
    initialization = time.perf_counter() - started
    print(f"initialized in {initialization:.2f}s", flush=True)
    started = time.perf_counter()
    times = []
    while True:
        tick = time.perf_counter()
        done = engine.step(5)
        times.append(time.perf_counter() - tick)
        if done:
            break
    simulation = time.perf_counter() - started
    tick = time.perf_counter()
    result = engine.result()
    report_seconds = time.perf_counter() - tick
    assert len(result["curve"]) == args.bars - 60
    assert all(row["coverage"]["totalSecurities"] == args.symbols for row in result["daily"])
    checkpoint_bytes = sum(len(chunk.encode()) for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(engine.state))
    report = {"scope": "isolated synthetic scale benchmark; not investment evidence", "symbols": args.symbols,
              "barsPerSymbol": args.bars, "totalInputBars": args.symbols*args.bars,
              "simulatedDays": len(result["curve"]), "fullUniverseEveryDay": True,
              "generationSeconds": generation, "initializationSeconds": initialization,
              "simulationSeconds": simulation, "reportSeconds": report_seconds,
              "fiveDayChunkMaxSeconds": max(times), "fiveDayChunkMeanSeconds": sum(times)/len(times),
              "peakWorkingSetMiB": peak_memory(), "checkpointMiB": checkpoint_bytes/1024**2,
              "python": sys.version.split()[0], "mode": "three-strategy equal hybrid shared ledger"}
    target = Path(__file__).resolve().parents[1] / "artifacts" / "portfolio-scale-benchmark.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
