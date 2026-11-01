"""Independent full seed oracle plus a forward, per-seed retest reference.

Never imports the new strategy module. No account execution or parameters are
fitted here. A seed's first qualifying retest is consumed before liquidity/rank.
"""
import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_retest_prior_seed_oracle", ROOT / "scripts/verify-liquid-trend-breakout.py")
SEED = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SEED)
SID = "trendBreakoutRetestV1"


def price_ok(row):
    return (isinstance(row, dict) and not row.get("suspended") and
        all(type(row.get(k)) in (int, float) and math.isfinite(row[k]) and row[k] > 0 for k in ("open", "high", "low", "close", "volume", "amount")) and
        row["low"] <= row["open"] <= row["high"] and row["low"] <= row["close"] <= row["high"])


def liquid(calendar, at, market):
    raw = [market.get(d) for d in calendar[max(0, at - 20):at + 1]]
    if len(raw) != 21 or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in raw):
        return "unknown"
    return "passed" if raw[-1] >= 1e12 and math.fsum(raw[-5:]) / 5 >= .8 * math.fsum(raw[:20]) / 20 else "failed"


def oracle(dataset, market, start, end):
    calendar = [d for d in dataset["calendar"] if d <= end]
    ix = {d: i for i, d in enumerate(calendar)}
    seed_start = calendar[max(0, ix[start] - 20)]
    old, daily = SEED.oracle(dataset, market, seed_start, end)
    queues = {}
    result = {p: [] for p in ("immediate", "immediateLiquid", "retestLiquid")}
    for row in old["trendEarly"]:
        d, symbol = row["signalDate"], row["symbol"]
        queues.setdefault(symbol, []).append(row)
        if d >= start:
            signal = {**row, "strategyId": SID, "signalId": f"{SID}:immediate:{d}:{symbol}"}
            result["immediate"].append(signal)
            if liquid(calendar, ix[d], market) == "passed":
                result["immediateLiquid"].append(dict(signal))
    candidates, setups = {}, []
    for security in dataset["securities"]:
        symbol = security["symbol"]
        if symbol not in queues:
            continue
        by_date = {row["date"]: row for row in security["bars"] if row["date"] <= end}
        rows = [by_date.get(d) for d in calendar]
        occupied_through = -1
        for seed in queues[symbol]:
            seed_at = ix[seed["signalDate"]]
            if seed_at <= occupied_through:
                continue
            level = max(row["high"] for row in rows[seed_at - 120:seed_at])
            status, ending = "activeAtSourceEnd", None
            for t in range(seed_at + 1, min(seed_at + 20, len(calendar) - 1) + 1):
                row = rows[t]
                if not price_ok(row):
                    status, ending = "unknownAlignedBar", t
                    break
                if row["close"] < level:
                    status, ending = "closeBelowBreakoutLevel", t
                    break
                if t - seed_at >= 3:
                    closes = [r["close"] for r in rows[t - 139:t + 1]]
                    recent60 = math.fsum(closes[-60:]) / 60
                    recent120 = math.fsum(closes[-120:]) / 120
                    prior120 = math.fsum(closes[:120]) / 120
                    qualifies = (row["low"] <= 1.03 * level and level <= row["close"] <= 1.05 * level and
                        row["close"] > row["open"] and row["close"] > rows[t - 1]["close"] and
                        row["close"] > recent60 > recent120 > prior120)
                    if qualifies:
                        status, ending = "firstConstructiveRetest", t
                        if calendar[t] >= start:
                            amount = math.fsum(r["amount"] for r in rows[t - 20:t]) / 20
                            candidates.setdefault(calendar[t], []).append((symbol, seed["signalDate"], amount))
                        break
                if t == seed_at + 20:
                    status, ending = "expired20Sessions", t
            occupied_through = ending if ending is not None else len(calendar) - 1
            setups.append((symbol, seed["signalDate"], status, calendar[ending] if ending is not None else None))
    for d, values in sorted(candidates.items()):
        for rank, (symbol, seed_date, _) in enumerate(sorted(values, key=lambda row: (-row[2], row[0]))[:5], 1):
            if liquid(calendar, ix[d], market) == "passed":
                result["retestLiquid"].append({"signalId": f"{SID}:retest:{d}:{symbol}:{seed_date}",
                    "strategyId": SID, "symbol": symbol, "signalDate": d, "originalRank": rank})
    for values in result.values():
        values.sort(key=lambda row: (row["signalDate"], row["originalRank"], row["symbol"]))
    return result, sorted(setups), daily
