"""Frozen historical readout: entry-owned A/B attribution, capital-days and gates.

Consumes completed accounts only. Never forms signals, reruns accounts, selects
parameters or reads a new market-data segment.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from pathlib import Path
import gzip
import hashlib
import json
import math
import os

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STUDY = ROOT / "artifacts/strategy-search/ignition-selection-review-20261031"
PATTERNS = ("A_ignition", "B_corePullback")
CASES = ("baseline", "selection", "participation")
LABELS = {"baseline": "原双路径", "selection": "低延伸排序", "participation": "成交额参与排序"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    with (gzip.open if str(path).endswith(".gz") else open)(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pin(path):
    return {"path": path.resolve().relative_to(ROOT).as_posix(), "sha256": sha(path)}


def bound(reference):
    path = (ROOT / reference["path"]).resolve()
    require(path.is_relative_to(ROOT), "Evidence path escapes repository")
    require(path.is_file() and sha(path) == reference["sha256"], "Evidence SHA mismatch: " + str(path))
    return path


def mean(values):
    return math.fsum(values) / len(values) if values else None


def ratio(numerator, denominator, scale=1.):
    return numerator / denominator * scale if denominator > 0 else None


def closed_metrics(episodes):
    pnl = [e["pnlAmount"] for e in episodes]
    positive, negative = math.fsum(max(x, 0) for x in pnl), -math.fsum(min(x, 0) for x in pnl)
    invested = math.fsum(e["investedAmount"] for e in episodes)
    return {"count": len(episodes), "netPnl": math.fsum(pnl), "meanNetPnl": mean(pnl),
            "meanNetReturnPct": mean([e["returnPct"] for e in episodes]),
            "investedAmount": invested, "netPnlToInvestedPct": ratio(math.fsum(pnl), invested, 100),
            "grossProfit": positive, "grossLoss": negative, "cashProfitFactor": ratio(positive, negative),
            "profitFactorStatus": "finite" if negative > 0 else "positiveNoLoss" if positive > 0 else "undefinedNoProfitOrLoss",
            "winRatePct": ratio(sum(x > 0 for x in pnl), len(pnl), 100),
            "meanPnlPerPlannedStopRiskBudget": mean([e["pnlAmount"] / e["plannedStopRiskBudget"] for e in episodes if e["plannedStopRiskBudget"] is not None and e["plannedStopRiskBudget"] > 0]),
            "missingPlannedStopRiskBudget": sum(e["plannedStopRiskBudget"] is None or e["plannedStopRiskBudget"] <= 0 for e in episodes)}


def derive_account(result, system, proof):
    """Pure attribution. A symbol's pattern belongs to its current entry, not ticker."""
    require(result["status"] == "completed", "Account is incomplete")
    metadata = system["metadata"]
    proof_rows = proof["rows"]
    slots, by_signal = {}, {}
    for p in proof_rows:
        sid, slot = p["selectedSignalId"], p["originSlotId"]
        require(sid not in by_signal and slot not in slots, "Duplicate proof signal/slot")
        m = metadata[sid]
        expected_slot = f'{m["signalDate"]}|{m["sectorCode"]}|{m["originalRank"]}'
        require(slot == expected_slot and p["pattern"] == m["pattern"] and p["selectedSymbol"] == m["symbol"], "Formation identity mismatch")
        require(m["pattern"] in PATTERNS, "Unknown entry pattern")
        by_signal[sid] = slot
        slots[slot] = {"originSlotId": slot, "originSignalId": p["originSignalId"], "signalId": sid,
                       "symbol": m["symbol"], "signalDate": m["signalDate"], "pattern": m["pattern"],
                       "originalRank": m["originalRank"], "sectorCode": m["sectorCode"],
                       "filled": False, "buyFills": [], "closedEpisodes": [], "closedNetPnl": 0.,
                       "endUnrealizedPnl": 0., "endOpenRealizedPnl": 0., "capitalDays": 0.,
                       "buyAmount": 0., "sellAmount": 0., "fees": 0., "slippage": 0.}
    require(set(metadata) == set(by_signal), "Metadata/proof population mismatch")
    opportunities = {o["signalId"]: o for o in result["research"]["opportunities"]}
    require(set(opportunities) == set(by_signal), "Opportunity/proof population mismatch")
    for sid, slot in by_signal.items():
        op = opportunities[sid]
        slots[slot].update(opportunityStatus=op["status"], opportunityReason=op["reason"],
                           plannedStopRiskBudget=op.get("stopRiskBudget"))

    curve = result["curve"]
    dates = [c["date"] for c in curve]
    require(dates and dates == sorted(set(dates)), "Curve requires unique ordered sessions")
    daily = result["daily"]
    require([d["date"] for d in daily] == dates, "Daily holdings/curve calendar mismatch")
    active, entries, seen_fills = {}, {}, []
    daily_capital = []
    for day, curve_day in zip(daily, curve):
        for fill in day["fills"]:
            require(fill["date"] == day["date"], "Fill date mismatch")
            symbol = fill["symbol"]
            if fill["side"] == "buy":
                sid = fill["signalId"]
                require(sid in by_signal, "Buy signal absent from frozen metadata")
                s = slots[by_signal[sid]]
                require(s["symbol"] == symbol and s["signalDate"] < fill["date"], "Buy identity/causality mismatch")
                if symbol in active:
                    require(active[symbol]["signalId"] == sid, "Concurrent entries mix pattern ownership")
                else:
                    key = (symbol, fill["date"])
                    require(key not in entries, "Ambiguous same-symbol same-day entry")
                    active[symbol] = {"signalId": sid, "entryDate": fill["date"]}
                    entries[key] = sid
                s["filled"] = True
                s["buyFills"].append({k: fill[k] for k in ("date", "quantity", "price", "amount")})
                s["buyAmount"] += fill["amount"]
            else:
                require(fill["side"] == "sell" and symbol in active, "Sell has no active entry owner")
                s = slots[by_signal[active[symbol]["signalId"]]]
                s["sellAmount"] += fill["amount"]
                if fill["quantityAfter"] == 0:
                    del active[symbol]
            s["fees"] += fill["fee"]
            s["slippage"] += fill["slippage"]
            seen_fills.append(fill)
        holdings = day["holdings"]
        require(len({h["symbol"] for h in holdings}) == len(holdings), "Duplicate daily holding")
        require(set(active) == {h["symbol"] for h in holdings}, "Holding has no fill-derived owner")
        values = {p: 0. for p in PATTERNS}
        for h in holdings:
            s = slots[by_signal[active[h["symbol"]]["signalId"]]]
            require(math.isfinite(h["marketValue"]) and h["marketValue"] >= 0, "Invalid daily market value")
            s["capitalDays"] += h["marketValue"]
            values[s["pattern"]] += h["marketValue"]
        require(math.isclose(math.fsum(values.values()), curve_day["marketValue"], abs_tol=1e-5), "Daily capital attribution fails market-value bridge")
        daily_capital.append({"date": day["date"], **values})
    require(seen_fills == result["trades"], "Daily fills differ from account trade ledger")

    joined_closed = set()
    for closed in result["closedTrades"]:
        key = (closed["symbol"], closed["entryDate"])
        require(key in entries and key not in joined_closed, "Closed episode has ambiguous/missing buy entry")
        joined_closed.add(key)
        s = slots[by_signal[entries[key]]]
        e = {**closed, "signalId": entries[key], "plannedStopRiskBudget": s["plannedStopRiskBudget"]}
        s["closedEpisodes"].append(e)
        s["closedNetPnl"] += closed["pnlAmount"]
    open_rows = []
    for position in result["positions"]:
        key = (position["symbol"], position["entryDate"])
        require(key in entries and key not in joined_closed, "Open position entry missing or already closed")
        sid = entries[key]
        require(active.get(position["symbol"]) == {"signalId": sid, "entryDate": position["entryDate"]}, "Terminal entry owner mismatch")
        s = slots[by_signal[sid]]
        floating = position["marketValue"] - position["costBasis"]
        s["endUnrealizedPnl"] += floating
        s["endOpenRealizedPnl"] += position["realizedPnl"]
        open_rows.append({"signalId": sid, "originSlotId": s["originSlotId"], "pattern": s["pattern"],
                          "symbol": position["symbol"], "entryDate": position["entryDate"],
                          "marketValue": position["marketValue"], "unrealizedPnl": floating,
                          "openRealizedPnl": position["realizedPnl"], "priceDate": position.get("priceDate")})
    require(len(open_rows) == len(active), "Terminal positions/holdings mismatch")
    require(set(entries) == joined_closed | {(p["symbol"], p["entryDate"]) for p in result["positions"]}, "Bought entry is neither closed nor open")
    for s in slots.values():
        s["netPnlIncludingEndMtm"] = s["closedNetPnl"] + s["endUnrealizedPnl"] + s["endOpenRealizedPnl"]
        require(s["filled"] or s["netPnlIncludingEndMtm"] == 0, "Unfilled slot must have zero output")

    paths = {}
    for pattern in PATTERNS:
        group = [s for s in slots.values() if s["pattern"] == pattern]
        episodes = [e for s in group for e in s["closedEpisodes"]]
        net = math.fsum(s["netPnlIncludingEndMtm"] for s in group)
        buys, sells, capital_days = (math.fsum(s[key] for s in group) for key in ("buyAmount", "sellAmount", "capitalDays"))
        ordered = sorted(episodes, key=lambda e: (-e["pnlAmount"], e["symbol"], e["entryDate"]))
        paths[pattern] = {"intendedSlots": len(group), "filledEntries": sum(s["filled"] for s in group),
            "buyFillCount": sum(len(s["buyFills"]) for s in group), "buyAmount": buys, "sellAmount": sells,
            "capitalDays": capital_days, "closed": closed_metrics(episodes),
            "netPnlIncludingEndMtm": net,
            "endUnrealizedPnl": math.fsum(s["endUnrealizedPnl"] for s in group),
            "endOpenRealizedPnl": math.fsum(s["endOpenRealizedPnl"] for s in group),
            "fees": math.fsum(s["fees"] for s in group), "slippage": math.fsum(s["slippage"] for s in group),
            "turnoverEfficiencyBps": ratio(net, buys + sells, 10000),
            "capitalDayEfficiencyBps": ratio(net, capital_days, 10000),
            "netPnlPerEvaluationSession": net / len(dates),
            "opportunityStatuses": dict(Counter(s["opportunityStatus"] for s in group)),
            "largestWinnerExclusions": {"excludeTop" + str(n): closed_metrics([e for e in ordered if e not in [e for e in ordered if e["pnlAmount"] > 0][:n]]) for n in (1, 2, 5)}}
    initial, final = result["config"]["initialCapital"], curve[-1]["equity"]
    account_pnl = final - initial
    attributed = math.fsum(p["netPnlIncludingEndMtm"] for p in paths.values())
    peak, drawdown = initial, 0.
    for row in curve:
        peak = max(peak, row["equity"])
        drawdown = max(drawdown, (1 - row["equity"] / peak) * 100)
    return {"account": {"initialCapital": initial, "finalEquity": final, "netPnl": account_pnl,
                         "netReturnPct": (final / initial - 1) * 100,
                         "maxDrawdownPct": drawdown, "evaluationSessions": len(dates)},
            "paths": paths, "slots": list(slots.values()), "endOpenPositions": open_rows,
            "dailyCapitalDays": daily_capital,
            "pnlBridge": {"accountNetPnl": account_pnl, "attributedClosedPnl": math.fsum(p["closed"]["netPnl"] for p in paths.values()),
                          "attributedEndUnrealizedPnl": math.fsum(p["endUnrealizedPnl"] for p in paths.values()),
                          "attributedOpenRealizedPnl": math.fsum(p["endOpenRealizedPnl"] for p in paths.values()),
                          "unallocatedResidual": account_pnl - attributed,
                          "meaning": "Residual is not assigned to A or B; can include rights or other reconciliation gaps."},
            "definitions": {"capitalDays": "Sum of end-of-session marked holdings marketValue by fill-derived current entry ownership; not cost times holdingDays.",
                            "pathNetPnl": "Closed episode PnL plus terminal open realized and unrealized PnL; bridge residual remains unallocated.",
                            "zeroSlots": "Unfilled original slots contribute zero paired-slot PnL but never enter closed expectancy or profit factor."}}


def paired_slots(candidate, comparator):
    a = {s["originSlotId"]: s for s in candidate["slots"] if s["pattern"] == PATTERNS[0]}
    b = {s["originSlotId"]: s for s in comparator["slots"] if s["pattern"] == PATTERNS[0]}
    require(set(a) == set(b), "Matched A slot populations differ")
    rows = []
    for slot in sorted(a):
        x, y = a[slot], b[slot]
        changed = x["symbol"] != y["symbol"]
        execution_changed = (x["opportunityStatus"], x["buyFills"]) != (y["opportunityStatus"], y["buyFills"])
        rows.append({"originSlotId": slot, "candidateSymbol": x["symbol"], "comparatorSymbol": y["symbol"],
                     "changedSymbol": changed, "changedExecution": execution_changed,
                     "candidateFilled": x["filled"], "comparatorFilled": y["filled"],
                     "candidateNetPnl": x["netPnlIncludingEndMtm"], "comparatorNetPnl": y["netPnlIncludingEndMtm"],
                     "netPnlDifference": x["netPnlIncludingEndMtm"] - y["netPnlIncludingEndMtm"]})
    ab = {s["originSlotId"]: s for s in candidate["slots"] if s["pattern"] == PATTERNS[1]}
    bb = {s["originSlotId"]: s for s in comparator["slots"] if s["pattern"] == PATTERNS[1]}
    require(set(ab) == set(bb) and all(ab[k]["symbol"] == bb[k]["symbol"] and ab[k]["signalId"] == bb[k]["signalId"] for k in ab), "B frozen signal slots changed")
    return {"slots": len(rows), "changedSymbols": sum(r["changedSymbol"] for r in rows),
            "sameSymbols": sum(not r["changedSymbol"] for r in rows),
            "changedExecution": sum(r["changedExecution"] for r in rows),
            "changedExecutionOnSameSymbol": sum(r["changedExecution"] and not r["changedSymbol"] for r in rows),
            "bothUnfilled": sum(not r["candidateFilled"] and not r["comparatorFilled"] for r in rows),
            "candidateOnlyFilled": sum(r["candidateFilled"] and not r["comparatorFilled"] for r in rows),
            "comparatorOnlyFilled": sum(not r["candidateFilled"] and r["comparatorFilled"] for r in rows),
            "meanNetPnlDifferencePerIntendedSlot": mean([r["netPnlDifference"] for r in rows]),
            "totalNetPnlDifference": math.fsum(r["netPnlDifference"] for r in rows),
            "symbolChangeAttribution": {name: {"slots": sum(r["changedSymbol"] == changed for r in rows),
                "netPnlDifference": math.fsum(r["netPnlDifference"] for r in rows if r["changedSymbol"] == changed)}
                for name, changed in (("changedSymbol", True), ("sameSymbol", False))}, "rows": rows}


def daily_returns(result):
    previous, values = result["config"]["initialCapital"], []
    for row in result["curve"]:
        require(previous > 0 and math.isfinite(row["equity"]), "Invalid equity for daily return")
        values.append(row["equity"] / previous - 1)
        previous = row["equity"]
    return np.asarray(values, dtype=float)


def block_interval(a, b, settings):
    require([r["date"] for r in a["curve"]] == [r["date"] for r in b["curve"]], "Bootstrap calendars differ")
    delta = daily_returns(a) - daily_returns(b)
    n, block, draws, seed = len(delta), settings["movingBlockSessions"], settings["draws"], settings["seed"]
    if n < block:
        return {"available": False, "pairedSessions": n, "reason": "Fewer sessions than frozen block length"}
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block + 1, size=(draws, math.ceil(n / block)))
    indices = (starts[:, :, None] + np.arange(block)).reshape(draws, -1)[:, :n]
    sampled = delta[indices].mean(axis=1) * 10000
    return {"available": True, "pairedSessions": n, "blockSessions": block, "draws": draws, "seed": seed,
            "meanDailyDifferenceBps": float(delta.mean() * 10000),
            "percentileInterval": settings["percentileInterval"],
            "percentileBoundsBps": np.percentile(sampled, settings["percentileInterval"]).tolist(),
            "meaning": settings["meaning"]}


def pf_value(closed):
    if closed["grossLoss"] > 0:
        return closed["grossProfit"] / closed["grossLoss"]
    return math.inf if closed["grossProfit"] > 0 else -math.inf


def gates(candidate, baseline, rule, *, stress=False):
    a, b = candidate["paths"][PATTERNS[0]], baseline["paths"][PATTERNS[0]]
    ca, cb = candidate["account"], baseline["account"]
    above = lambda x, y: x is not None and y is not None and x > y
    checks = {"netReturnPositive": ca["netReturnPct"] > 0}
    if stress:
        checks.update(beatSameCostBaseline=ca["netReturnPct"] > cb["netReturnPct"],
                      aMeanNetReturnPositive=above(a["closed"]["meanNetReturnPct"], 0),
                      aProfitFactorAboveOne=pf_value(a["closed"]) > 1)
    else:
        checks.update(beatMatchedBaselineNetReturn=ca["netReturnPct"] > cb["netReturnPct"],
            aMeanNetReturnPositiveAndAboveBaseline=above(a["closed"]["meanNetReturnPct"], 0) and above(a["closed"]["meanNetReturnPct"], b["closed"]["meanNetReturnPct"]),
            aProfitFactorAboveOneAndBaseline=pf_value(a["closed"]) > max(1, pf_value(b["closed"])),
            aTurnoverAndCapitalDayEfficiencyPositiveAndAboveBaseline=all(above(a[k], 0) and above(a[k], b[k]) for k in ("turnoverEfficiencyBps", "capitalDayEfficiencyBps")),
            minimumAClosedTrades=a["closed"]["count"] >= rule["minimumAClosedTrades"],
            maximumDrawdownPct=ca["maxDrawdownPct"] <= rule["maximumDrawdownPct"])
        for key, metric in (("minimumFilledAEntryRatioToBaseline", "filledEntries"),
                            ("minimumABuyAmountRatioToBaseline", "buyAmount"),
                            ("minimumACapitalDayRatioToBaseline", "capitalDays")):
            value = ratio(a[metric], b[metric])
            checks[key] = value is not None and value >= rule[key]
    require(set(checks) == set(rule), "Frozen decision gate not implemented or unexpected rule")
    return {"checks": checks, "passed": all(checks.values()),
            "thresholds": rule,
            "participationRatios": {k: ratio(a[k], b[k]) for k in ("filledEntries", "buyAmount", "capitalDays")}}


def figure(rows, destination):
    os.environ.setdefault("MPLCONFIGDIR", "H:/codex/cache/matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    plt.rcParams.update({"font.family": "Microsoft YaHei", "axes.unicode_minus": False,
                         "font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    periods = ("development", "reusedTemporalCheck")
    lookup = {(r["period"], r["caseId"]): r["metrics"] for r in rows if r["cost"] == "normal"}
    colors = {"baseline": "#64748b", "selection": "#2764ac", "participation": "#b76b19"}
    fig, axes = plt.subplots(1, 3, figsize=(17.5, 6.3), gridspec_kw={"width_ratios": [1, 1, 1.25]})
    for ax, kind, title, ylabel in ((axes[0], "account", "账户费用后收益", "账户总收益（%）"),
                                   (axes[1], "expectancy", "A 路径单笔净期望", "已闭合 A 交易平均净收益（%）")):
        for i, case in enumerate(CASES):
            vals = [lookup[p, case]["account"]["netReturnPct"] if kind == "account" else lookup[p, case]["paths"][PATTERNS[0]]["closed"]["meanNetReturnPct"] for p in periods]
            require(all(v is not None for v in vals), "Figure has unavailable expectancy")
            bars = ax.bar(np.arange(2) + (i - 1) * .24, vals, width=.22, color=colors[case], label=LABELS[case])
            for bar, value in zip(bars, vals):
                ax.annotate(f"{value:+.2f}", (bar.get_x() + bar.get_width()/2, value), xytext=(0, 4 if value >= 0 else -4), textcoords="offset points", ha="center", va="bottom" if value >= 0 else "top", fontsize=10)
        ax.set_xticks([0, 1], ["开发期\n2024-09—2025-12", "历史复验\n2026-01—2026-10"])
        ax.axhline(0, color="#475569", linewidth=.8)
        ax.set_title(title, pad=15)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=.17)
        ax.set_axisbelow(True)
        ax.margins(y=.23)
    axes[0].legend(loc="best", fontsize=9)
    ax = axes[2]
    markers = {"filledEntries": ("o", "A 成交数"), "buyAmount": ("s", "A 买入金额"), "capitalDays": ("D", "A 资本日")}
    ylabels = []
    for y, (period, case) in enumerate((p, c) for p in periods for c in CASES[1:]):
        a, b = lookup[period, case]["paths"][PATTERNS[0]], lookup[period, "baseline"]["paths"][PATTERNS[0]]
        ylabels.append(("开发 " if period == "development" else "复验 ") + LABELS[case] + f'\nA 成交 {a["filledEntries"]}/{b["filledEntries"]}')
        for j, (key, (marker, _)) in enumerate(markers.items()):
            val = ratio(a[key], b[key], 100)
            require(val is not None, "Figure participation denominator missing")
            ax.scatter(val, y + (j - 1) * .15, marker=marker, s=55, color=colors[case], edgecolors="white", linewidth=.6)
    ax.set_yticks(range(4), ylabels)
    ax.invert_yaxis()
    ax.axvline(100, color="#64748b", linewidth=.8)
    ax.axvline(80, color="#475569", linestyle="--", linewidth=1, label="预先设定 80% 下限")
    ax.set_xlim(left=0)
    ax.set_xlabel("相对同一期原双路径的参与比例（%）")
    ax.set_title("改善是否只来自减少交易", pad=15)
    ax.grid(axis="x", alpha=.17)
    handles = [Line2D([], [], color="#475569", marker=m, linestyle="", label=label) for m, label in markers.values()]
    handles.append(Line2D([], [], color="#475569", linestyle="--", label="80% 下限"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(.5, -.19), ncol=2, fontsize=9)
    fig.suptitle("A 点火选股改进：两段已使用历史，不能当作独立样本外", fontsize=16, y=.98)
    fig.text(.5, .02, "各期独立以 50 万元开始；费用与单边 10 bps 滑点已计入。相同 A 名额不保证相同成交；资本日按每日收盘持仓市值累加。\n35 bps 压力结果、全部冻结门槛、未实现损益及配对区间见 readout.json。", ha="center", fontsize=10, color="#475569")
    fig.subplots_adjust(left=.055, right=.985, top=.85, bottom=.25, wspace=.44)
    fig.savefig(destination, dpi=170)
    plt.close(fig)


def main(study=DEFAULT_STUDY):
    study = study.resolve()
    index_path = study / "experiment/account-index.json"
    require(index_path.exists(), "All frozen accounts must complete before readout")
    plan_path = study / "hypothesis-plan.json"
    plan, index = read(plan_path), read(index_path)
    execution = read(bound(index["plan"]))
    require(execution["hypothesis"] == pin(plan_path), "Execution belongs to a different hypothesis")
    receipt = read(study / "experiment/all-accounts-completion.json")
    require(receipt["index"] == pin(index_path) and receipt["newAccountsRun"] == 9, "All-account completion mismatch")
    require(len(index["rows"]) == len(execution["jobs"]) == plan["newAccountsPlanned"] == 9, "Expected exactly nine frozen accounts")
    for reference in [plan["frozenParentPlan"], plan["parentInputs"], *plan["diagnosticSources"]]:
        bound(reference)
    rows, results, derived = [], {}, {}
    inputs = {}
    for period, per in execution["periodInputs"].items():
        bound(per["dataset"])
        bound(per["prefixValidation"])
        for case, refs in per["cases"].items():
            system, proof, schedule = (read(bound(refs[key])) for key in ("system", "formationProof", "schedule"))
            require({s["signalId"] for s in schedule} == set(system["metadata"]), "Schedule metadata population differs")
            inputs[period, case] = system, proof
    frozen_jobs = {j["id"]: j for j in execution["jobs"]}
    require(len(frozen_jobs) == 9 and len({r["job"]["id"] for r in index["rows"]}) == 9, "Duplicate account job")
    for item in index["rows"]:
        job = item["job"]
        require(job == frozen_jobs[job["id"]], "Index job drift")
        for reference in (v for v in item.values() if isinstance(v, dict) and "path" in v and "sha256" in v):
            bound(reference)
        completion = read(bound(item["completion"]))
        require(completion["independentCashAuditPassed"] is True and completion["job"] == job and completion["plan"] == index["plan"], "Account completion/audit mismatch")
        for reference in completion["artifacts"].values():
            bound(reference)
        for key, filename in (("result", "result.json.gz"), ("summary", "summary.json"), ("validation", "validation.json"), ("metricsRef", "metrics.json")):
            require(item[key] == completion["artifacts"][filename], "Account index/completion artifact mismatch")
        result, summary = read(bound(item["result"])), read(bound(item["summary"]))
        require(not summary["quality"]["integrityIssues"], "Account integrity issues")
        require(result["config"]["startDate"] == job["start"] and result["config"]["endDate"] == job["end"] <= "2026-10-15", "Account period drift")
        require(result["config"]["initialCapital"] == 500000 and result["config"]["slippageBps"] == job["slippageBps"], "Capital/cost drift")
        require(all(job["start"] <= d["date"] <= job["end"] for d in result["curve"]), "Curve outside frozen period")
        key = job["period"], job["case"], job["costCase"]
        metrics = derive_account(result, *inputs[key[:2]])
        require(math.isclose(metrics["account"]["netReturnPct"], item["metrics"]["netReturnPct"], abs_tol=1e-8), "Independent account net mismatch")
        rows.append({"period": key[0], "caseId": key[1], "cost": key[2], "slippageBps": job["slippageBps"], "metrics": metrics,
                     "sources": {k: item[k] for k in ("result", "summary", "completion")}})
        results[key], derived[key] = result, metrics
    expected = {(p, c, cost) for p in plan["periods"] for c in CASES for cost in (("normal", "stress") if p == "reusedTemporalCheck" else ("normal",))}
    require(set(results) == expected, "Frozen account matrix incomplete")
    comparisons, decisions = [], []
    for period in plan["periods"]:
        for cost in (("normal", "stress") if period == "reusedTemporalCheck" else ("normal",)):
            for candidate, comparator in plan["uncertainty"]["pairs"]:
                ka, kb = (period, candidate, cost), (period, comparator, cost)
                a, b = derived[ka], derived[kb]
                comparisons.append({"period": period, "cost": cost, "candidate": candidate, "comparator": comparator,
                    "netReturnDifferencePp": a["account"]["netReturnPct"] - b["account"]["netReturnPct"],
                    "aMeanNetReturnDifferencePp": (a["paths"][PATTERNS[0]]["closed"]["meanNetReturnPct"] - b["paths"][PATTERNS[0]]["closed"]["meanNetReturnPct"]) if all(x["paths"][PATTERNS[0]]["closed"]["meanNetReturnPct"] is not None for x in (a, b)) else None,
                    "matchedASlots": paired_slots(a, b), "blockBootstrap": block_interval(results[ka], results[kb], plan["uncertainty"]),
                    "bCrowding": {**{k + "Difference": a["paths"][PATTERNS[1]][k] - b["paths"][PATTERNS[1]][k] for k in ("intendedSlots", "filledEntries", "netPnlIncludingEndMtm", "capitalDays", "buyAmount")},
                                   "closedCountDifference": a["paths"][PATTERNS[1]]["closed"]["count"] - b["paths"][PATTERNS[1]]["closed"]["count"],
                                   "closedNetPnlDifference": a["paths"][PATTERNS[1]]["closed"]["netPnl"] - b["paths"][PATTERNS[1]]["closed"]["netPnl"]}})
    for case in CASES[1:]:
        normal = {p: gates(derived[p, case, "normal"], derived[p, "baseline", "normal"], plan["decisionRule"]["bothNormalPeriods"]) for p in plan["periods"]}
        stress = gates(derived["reusedTemporalCheck", case, "stress"], derived["reusedTemporalCheck", "baseline", "stress"], plan["decisionRule"]["stress2026"], stress=True)
        decisions.append({"caseId": case, "normalPeriods": normal, "stress2026": stress,
                          "historicalScreenPassed": all(v["passed"] for v in normal.values()) and stress["passed"],
                          "profitabilityProven": False, "pristineOosValidated": False, "productionPromotion": False})
    output = {"schema": "ignition-selection-reviewed-readout-v1", "plan": pin(plan_path), "executionPlan": index["plan"],
              "accountIndex": pin(index_path), "analysisCode": pin(Path(__file__).resolve()), "rows": rows,
              "comparisons": comparisons, "decisions": decisions,
              "historicalScreenPassCount": sum(d["historicalScreenPassed"] for d in decisions),
              "profitabilityProven": False, "pristineOosPerformed": False, "oosStatus": plan["oos"],
              "periodsAreIndependentAccounts": True, "limitations": plan["limitations"]}
    figure(rows, study / "historical-comparison.png")
    output["figure"] = pin(study / "historical-comparison.png")
    (study / "readout.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"accounts": len(rows), "historicalScreenPassCount": output["historicalScreenPassCount"], "readout": pin(study / "readout.json")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    main(parser.parse_args().study)
