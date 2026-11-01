"""One causal market -> industry -> stock opportunity/state system.

Inputs are immutable source matrices, publication-dated cohort joins and broad
index rows. No portfolio outcomes enter selection, ranks, or role history.
"""
from collections import Counter
import numpy as np

VERSION = "market-sector-stock-system-v1"
SID = "marketSectorStockV1"
POLICIES = ("completeSystem", "withoutMarketAdmission", "withoutSectorAdmission", "withoutCatchupEntry", "sectorLiquidRepresentative")


def mean_window(a, width):
    a = np.asarray(a, dtype=float)
    out = np.full(a.shape, np.nan)
    if a.shape[-1] < width:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(a, width, axis=-1)
    known = np.isfinite(windows).all(axis=-1)
    out[..., width - 1:] = np.where(known, np.where(np.isfinite(windows), windows, 0).sum(axis=-1) / width, np.nan)
    return out


def shift(a, n=1):
    out = np.full(a.shape, np.nan)
    if a.shape[-1] > n:
        out[..., n:] = a[..., :-n]
    return out


def return_window(close, width):
    previous = shift(close, width)
    with np.errstate(invalid="ignore", divide="ignore"):
        value = close / previous - 1
    return np.where(np.isfinite(mean_window(close, width + 1)), value, np.nan)


def prior_extreme(a, valid, width, maximum):
    out = np.full(a.shape, np.nan)
    if a.shape[-1] <= width:
        return out
    view = np.lib.stride_tricks.sliding_window_view(np.where(valid, a, np.nan), width, axis=-1)[..., :-1, :]
    known = np.isfinite(view).all(axis=-1)
    filled = np.where(np.isfinite(view), view, -np.inf if maximum else np.inf)
    out[..., width:] = np.where(known, filled.max(axis=-1) if maximum else filled.min(axis=-1), np.nan)
    return out


def stock_features(matrix):
    valid = matrix["priceValid"]
    close = np.where(valid, matrix["close"], np.nan)
    amount = np.where(valid & np.isfinite(matrix["amount"]) & (matrix["amount"] > 0), matrix["amount"], np.nan)
    previous = shift(close)
    with np.errstate(invalid="ignore", divide="ignore"):
        tr = np.maximum(matrix["high"] - matrix["low"], np.maximum(abs(matrix["high"] - previous), abs(matrix["low"] - previous))) / previous
    tr = np.where(valid & np.isfinite(previous), tr, np.nan)
    ntr = np.where(np.isfinite(mean_window(close, 21)), mean_window(tr, 20), np.nan)
    return {"close": close, "amount": amount, "ma20": mean_window(close, 20), "ma60": mean_window(close, 60),
        "return20": return_window(close, 20), "return60": return_window(close, 60), "meanNtr20": ntr,
        "amount5": mean_window(amount, 5), "priorAmount20": shift(mean_window(amount, 20)),
        "priorHigh5": prior_extreme(matrix["high"], valid, 5, True), "priorHigh20": prior_extreme(matrix["high"], valid, 20, True),
        "priorLow10": prior_extreme(matrix["low"], valid, 10, False), "priorLow20": prior_extreme(matrix["low"], valid, 20, False)}


def midranks(values):
    values = np.asarray(values, dtype=float)
    out = np.full(values.shape, np.nan)
    mask = np.isfinite(values)
    known = values[mask]
    if not len(known):
        return out
    ordered = np.sort(known)
    out[mask] = (np.searchsorted(ordered, known, side="left") + np.searchsorted(ordered, known, side="right")) / (2 * len(known))
    return out


def number(value):
    return float(value) if np.isfinite(value) else None


def advance_streak(value, positive, negative):
    # Consecutive independent-calendar observations are required. An unknown
    # interrupts either streak without casting a negative market/industry vote.
    if value is None:
        return 0, 0
    return (positive + 1, 0) if value else (0, negative + 1)


def index_features(indices):
    result = {}
    for symbol, item in indices.items():
        close = np.asarray(item["close"], dtype=float)
        close = np.where(np.isfinite(close) & (close > 0), close, np.nan)
        result[symbol] = {"close": close, "ma20": mean_window(close, 20), "ma60": mean_window(close, 60),
            "return20": return_window(close, 20), "return60": return_window(close, 60)}
    amounts = [np.asarray(indices[s]["amount"], dtype=float) for s in ("sh000001", "sz399106")]
    total = np.where((amounts[0] > 0) & (amounts[1] > 0), amounts[0] + amounts[1], np.nan)
    five, prior = mean_window(total, 5), shift(mean_window(total, 20))
    result["turnover"] = {"total": total, "mean5": five, "prior20": prior, "ratio": five / prior}
    return result


def build_inputs(matrix, joins, indices, start_date):
    calendar, symbols = matrix["calendar"].tolist(), matrix["symbols"].tolist()
    if calendar != sorted(set(calendar)) or symbols != sorted(set(symbols)) or len(matrix["close"]) != len(symbols):
        raise ValueError("Canonical unique stock and calendar axes required")
    features, broad = stock_features(matrix), index_features(indices)
    joins = sorted(joins, key=lambda r: r["availableFrom"] or "9999")
    daily, schedules, metadata = [], {p: [] for p in POLICIES}, {}
    by_day, role_history, sector_streaks = {}, {}, {}
    join_cursor, current_join, last_identity, market_fail = -1, None, None, 0
    for i, day in enumerate(calendar):
        while join_cursor + 1 < len(joins) and joins[join_cursor + 1]["availableFrom"] is not None and joins[join_cursor + 1]["availableFrom"] <= day:
            join_cursor += 1
            current_join = joins[join_cursor]
        identity = current_join["snapshotId"] if current_join else None
        if identity != last_identity:
            sector_streaks = {}
            last_identity = identity
        observed = np.isfinite(features["ma60"][:, i])
        observed_count = int(observed.sum())
        breadth = float((features["close"][observed, i] > features["ma60"][observed, i]).mean()) if observed_count else None
        turnover = broad["turnover"]
        liquidity = None if not all(np.isfinite(turnover[k][i]) for k in ("total", "ratio")) else bool(turnover["total"][i] >= 1e12 and turnover["ratio"][i] >= .8)
        composite_known = all(np.isfinite(broad[s][k][i]) for s in ("sh000001", "sz399106") for k in ("close", "ma20", "ma60"))
        trend = None if not composite_known else all(broad[s]["close"][i] > broad[s]["ma60"][i] and broad[s]["ma20"][i] > broad[s]["ma60"][i] for s in ("sh000001", "sz399106"))
        weak = None if not composite_known else all(broad[s]["close"][i] < broad[s]["ma60"][i] for s in ("sh000001", "sz399106"))
        market_fail = market_fail + 1 if weak is True else 0
        breadth_gate = None if observed_count / len(symbols) < .95 else breadth > .5
        market_pass = None if any(x is None for x in (liquidity, trend, breadth_gate)) else bool(liquidity and trend and breadth_gate)
        market = {"admission": market_pass, "liquidity": liquidity, "trend": None if trend is None else bool(trend),
            "breadth60": breadth, "breadthObserved": observed_count, "breadthPopulation": len(symbols),
            "turnover": number(turnover["total"][i]), "turnover5ToPrior20": number(turnover["ratio"][i]),
            "crisisCount": market_fail, "riskExit": market_fail >= 3}
        sectors, local = [], {}
        if current_join:
            for group in current_join["sectors"]:
                rows = np.asarray(group["cohortRowIndices"], dtype=int)
                code, n = group["sectorCode"], len(rows)
                if not n:
                    continue
                r20, r60 = features["return20"][rows, i], features["return60"][rows, i]
                m60, closes = features["ma60"][rows, i], features["close"][rows, i]
                masks = [np.isfinite(r20), np.isfinite(r60), np.isfinite(m60), np.isfinite(features["amount5"][rows, i]) & np.isfinite(features["priorAmount20"][rows, i])]
                counts = [int(mask.sum()) for mask in masks]
                avg20 = float(r20[masks[0]].mean()) if counts[0] else np.nan
                avg60 = float(r60[masks[1]].mean()) if counts[1] else np.nan
                sector_breadth = float((closes[masks[2]] > m60[masks[2]]).mean()) if counts[2] else np.nan
                amounts = masks[3]
                participation = float(features["amount5"][rows[amounts], i].sum() / features["priorAmount20"][rows[amounts], i].sum() / turnover["ratio"][i]) if counts[3] and np.isfinite(turnover["ratio"][i]) else np.nan
                rel20, rel60 = avg20 - broad["sh000300"]["return20"][i], avg60 - broad["sh000300"]["return60"][i]
                valid = bool(n >= 5 and min(counts) / n >= .95 and all(np.isfinite(x) for x in (rel20, rel60, sector_breadth, participation)))
                raw = None if not valid else bool(rel20 > 0 and rel60 > 0 and sector_breadth > .5 and participation >= 1)
                yes, no = advance_streak(raw, *sector_streaks.get(code, (0, 0)))
                sector_streaks[code] = yes, no
                ranks20, ranks60 = midranks(r20), midranks(r60)
                leaders = rows[np.isfinite(r60) & (ranks60 >= .8) & (r60 > avg60) & (r60 > 0) & (closes > m60)].tolist()
                role_history[i, code] = identity, leaders
                row = {"sectorCode": code, "sectorName": group["sectorName"], "cohortMembers": n, "observedCounts20_60_breadth_amount": counts,
                    "return20": number(avg20), "return60": number(avg60), "relative20": number(rel20), "relative60": number(rel60),
                    "breadth60": number(sector_breadth), "participation": number(participation), "featureValid": valid,
                    "rawStrong": raw, "confirmCount": yes, "failCount": no, "confirmed": yes >= 3, "riskExit": no >= 3, "leaders": leaders, "score": None}
                sectors.append(row)
                local[code] = rows, ranks20, ranks60, avg20, set(leaders)
        valid_sectors = [s for s in sectors if s["featureValid"]]
        if valid_sectors:
            ranks = [midranks([s[key] for s in valid_sectors]) for key in ("relative20", "relative60", "participation")]
            for k, sector in enumerate(valid_sectors):
                # All three ranks have the same exact2*N denominator. Sum
                # integer numerators so mathematical ties reach the code tie
                # break, without dependence on floating addition order.
                numerator = sum(int(round(float(r[k]) * 2 * len(valid_sectors))) for r in ranks)
                sector["score"] = numerator / (6 * len(valid_sectors))
        ordered = sorted(valid_sectors, key=lambda s: (-s["score"], s["sectorCode"]))
        selected = [s["sectorCode"] for s in ordered if s["confirmed"]][:3]
        context = {"date": day, "index": i, "snapshotId": identity, "market": market, "selectedSectors": selected, "sectors": sectors}
        if day < start_date:
            continue
        daily.append(context)
        by_day[day] = context
        for policy in POLICIES:
            if policy != "withoutMarketAdmission" and market_pass is not True:
                continue
            candidates = []
            for sector in ordered:
                code = sector["sectorCode"]
                if policy != "withoutSectorAdmission" and code not in selected:
                    continue
                rows, ranks20, ranks60, avg20, leaders = local[code]
                old_identity, old_leaders = role_history.get((i - 5, code), (None, []))
                healthy_old = [r for r in old_leaders if old_identity == identity and np.isfinite(features["ma20"][r, i]) and features["close"][r, i] > features["ma20"][r, i]]
                choices = []
                for position, row in enumerate(rows.tolist()):
                    close, ntr, prior_amount = features["close"][row, i], features["meanNtr20"][row, i], features["priorAmount20"][row, i]
                    low10 = features["priorLow10"][row, i]
                    if not all(np.isfinite(x) for x in (close, ntr, prior_amount, low10, features["amount"][row, i])) or prior_amount <= 0:
                        continue
                    floor = max(low10, .95 * close)
                    if not 0 < floor < close:
                        continue
                    pattern = None
                    if policy == "sectorLiquidRepresentative":
                        pattern = "sectorLiquidRepresentative"
                    elif features["return60"][row, i] > 0 and close > features["ma60"][row, i] and close <= features["ma20"][row, i] + 2 * ntr * close:
                        breakout = row in leaders and i > 0 and close > features["priorHigh20"][row, i] and features["close"][row, i - 1] <= features["priorHigh20"][row, i - 1]
                        reclaim = row in leaders and matrix["low"][row, i] < features["priorLow20"][row, i] < close
                        catchup = policy != "withoutCatchupEntry" and features["return20"][row, i] <= avg20 and .2 <= ranks20[position] < .8 and i > 0 and close > features["priorHigh5"][row, i] and features["close"][row, i - 1] <= features["priorHigh5"][row, i - 1] and features["amount"][row, i] > prior_amount and any(r != row for r in healthy_old)
                        pattern = "leaderBreakout" if breakout else "leaderReclaim" if reclaim else "catchup" if catchup else None
                    if pattern:
                        priority = {"leaderBreakout": 0, "leaderReclaim": 1, "catchup": 2, "sectorLiquidRepresentative": 0}[pattern]
                        key = (-prior_amount, symbols[row]) if policy == "sectorLiquidRepresentative" else (priority, -ranks60[position], -prior_amount, symbols[row])
                        choices.append((key, row, {"pattern": pattern, "signalClose": float(close), "failurePrice": float(floor), "meanNtr20": float(ntr),
                            "sectorCode": code, "snapshotId": identity, "sectorScore": sector["score"], "stockReturn60Midrank": number(ranks60[position]),
                            "priorAmount20": float(prior_amount), "priorLeaderRows": healthy_old}))
                if choices:
                    _, row, evidence = min(choices, key=lambda c: c[0])
                    candidates.append((row, evidence))
            for rank, (row, evidence) in enumerate(candidates[:5], 1):
                identifier = f"system-v1:{policy}:{day}:{symbols[row]}"
                signal = {"signalId": identifier, "strategyId": SID, "symbol": symbols[row], "signalDate": day, "originalRank": rank}
                schedules[policy].append(signal)
                metadata[identifier] = {**evidence, "signalId": identifier, "symbol": symbols[row], "signalDate": day, "originalRank": rank}
    return {"version": VERSION, "daily": daily, "schedules": schedules, "metadata": metadata,
        "counts": {"sessions": len(daily), "marketAdmitted": sum(d["market"]["admission"] is True for d in daily),
            "signals": {p: len(v) for p, v in schedules.items()}, "patterns": dict(Counter(m["pattern"] for m in metadata.values()))}}
