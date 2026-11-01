"""Separate NumPy raw-window oracle; never imports the signal builder.

The oracle scans every security/session in the source population, not only
retained signals. Account arithmetic is independently recomputed from results.
"""
import math
import numpy as np

SID = "liquidTrendBreakoutV1"
POLICIES = ("breakout120", "trendEarly", "trendEarlyLiquid")


def oracle(dataset, market, start, end):
    calendar = [d for d in dataset["calendar"] if d <= end]
    index = {d: i for i, d in enumerate(calendar)}
    targets = [d for d in calendar if start <= d <= end]
    found = {d: [] for d in targets}
    unknown = {d: 0 for d in targets}
    for security in dataset["securities"]:
        lookup = {r["date"]: r for r in security["bars"] if r["date"] <= end}
        bars = [lookup.get(d) for d in calendar]
        good, highs = [], []
        for r in bars:
            okay = r is not None and not r.get("suspended")
            if okay:
                for key in ("open", "high", "low", "close", "volume", "amount"):
                    v = r.get(key)
                    if type(v) not in (int, float) or not math.isfinite(v) or v <= 0:
                        okay = False
                        break
            if okay:
                okay = r["low"] <= r["open"] <= r["high"] and r["low"] <= r["close"] <= r["high"]
            good.append(okay)
            highs.append(r["high"] if okay else -math.inf)
        if len(calendar) < 141:
            for d in targets:
                unknown[d] += 1
            continue
        windows = np.lib.stride_tricks.sliding_window_view(np.asarray(highs), 120).max(axis=1)
        validity = np.lib.stride_tricks.sliding_window_view(np.asarray(good), 141).all(axis=1)
        for d in targets:
            t = index[d]
            if t < 140 or not validity[t - 140]:
                unknown[d] += 1
                continue
            if not (bars[t]["close"] > windows[t - 120] and bars[t - 1]["close"] <= windows[t - 121]):
                continue
            amount = sum(b["amount"] for b in bars[t - 20:t]) / 20
            if amount < 50e6:
                continue
            a = math.fsum(b["close"] for b in bars[t - 59:t + 1]) / 60
            b = math.fsum(b["close"] for b in bars[t - 119:t + 1]) / 120
            c = math.fsum(b["close"] for b in bars[t - 139:t - 19]) / 120
            growth = bars[t - 1]["close"] / bars[t - 21]["close"] - 1
            admitted = bars[t]["close"] > a > b > c and growth <= .15
            found[d].append((security["symbol"], amount, admitted))
    result = {p: [] for p in POLICIES}
    daily_counts = {}
    for d in targets:
        t = index[d]
        dates = calendar[max(0, t - 20):t + 1]
        values = [market.get(x) for x in dates]
        liquidity = len(values) == 21 and all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in values)
        if liquidity:
            liquidity = values[-1] >= 1e12 and math.fsum(values[-5:]) / 5 >= math.fsum(values[:-1]) / 20
        ranked = sorted(found[d], key=lambda r: (-r[1], r[0]))
        daily_counts[d] = {"unknownWindow": unknown[d], "commonCandidates": len(ranked)}
        for rank, (symbol, _, trend) in enumerate(ranked[:5], 1):
            signal = {"signalId": f"{SID}:{d}:{symbol}", "strategyId": SID, "symbol": symbol, "signalDate": d, "originalRank": rank}
            result["breakout120"].append(signal)
            if trend:
                result["trendEarly"].append(dict(signal))
                if liquidity:
                    result["trendEarlyLiquid"].append(dict(signal))
    return result, daily_counts


def account_numbers(result):
    returns, profits, losses = [], [], []
    for trade in result["closedTrades"]:
        pnl, invested = trade["pnlAmount"], trade["investedAmount"]
        value = pnl / invested * 100
        returns.append(value)
        if pnl > 0:
            profits.append((value, pnl))
        elif pnl < 0:
            losses.append((value, pnl))
    mean_win = math.fsum(r[0] for r in profits) / len(profits) if profits else None
    mean_loss = math.fsum(r[0] for r in losses) / len(losses) if losses else None
    pf = math.fsum(r[1] for r in profits) / -math.fsum(r[1] for r in losses) if losses else None
    payoff = mean_win / -mean_loss if mean_win is not None and mean_loss is not None else None
    initial = result["config"]["initialCapital"]
    peak, dd = initial, 0.
    for row in result["curve"]:
        peak = max(peak, row["equity"])
        dd = max(dd, (1 - row["equity"] / peak) * 100)
    return {"closed": len(returns), "payoffRatio": payoff, "profitFactor": pf, "meanWinReturnPct": mean_win,
            "meanLossReturnPct": mean_loss, "maxDrawdownPct": dd,
            "netReturnPct": (result["curve"][-1]["equity"] / initial - 1) * 100,
            "closedNetPnl": math.fsum(t["pnlAmount"] for t in result["closedTrades"])}
