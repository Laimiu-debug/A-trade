"""Independent raw-source, selection and outcome audit for the fixed VCP trial.

Does not call the contraction detector or reselect a strategy from results.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean, median
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/strategy-search/signal-contraction-price-v1"
sys.path.insert(0, str(ROOT))
from service.portfolio_signals import DailyScorer
from service.portfolio_universe import snapshot_universe


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(a, b):
    if a is None or b is None:
        return a is b
    return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


def save(report):
    path = OUTPUT / "independent-result-verification.json"
    if path.exists() or not report.get("passed"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = OUTPUT / f"independent-result-verification-{stamp}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path


def main():
    issues, checks = [], 0

    def check(ok, label, **details):
        nonlocal checks
        checks += 1
        if not ok:
            issues.append({"check": label, **details})

    protocol = read(OUTPUT / "protocol.json")
    attempt = OUTPUT / "attempts/attempt-0001"
    receipt = read(attempt / "completion.json")
    snapshot = read(OUTPUT / "snapshot.json")
    ph = hashlib.sha256(json.dumps(protocol, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    check(receipt["protocolHash"] == ph, "protocol-hash")
    check(set(receipt["files"]) == {"candidates", "coverage", "signals", "result", "summary", "selections"}, "six-artifacts")
    names = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
             "result": "result.json.gz", "summary": "summary.json", "selections": "selection.jsonl.gz"}
    for key, row in receipt["files"].items():
        p = (OUTPUT / row["path"]).resolve()
        check(p == (attempt / names[key]).resolve() and p.is_relative_to(OUTPUT) and sha(p) == row["sha256"], "artifact-path-and-hash", key=key)
    for name, expected in protocol["implementation"].items():
        check(sha(ROOT / name) == expected, "implementation-hash", name=name)
    with gzip.open(attempt / "result.json.gz", "rt", encoding="utf-8") as stream:
        result = json.load(stream)
    summary = read(attempt / "summary.json")
    coverage, selections, candidates, signals = [lines(attempt / name) for name in
        ("coverage.jsonl.gz", "selection.jsonl.gz", "candidates.jsonl.gz", "signals.jsonl.gz")]
    check(result["groups"] == summary["groups"] and result["pathRiskSummary"] == summary["pathRiskSummary"], "summary-result-match")
    dates = [r["date"] for r in coverage]
    check(dates == sorted(set(dates)) and len(dates) == 484, "484-unique-ordered-sessions")
    check(Counter(d[:4] for d in dates) == {"2022": 242, "2023": 242}, "development-year-counts")
    index = {d: i for i, d in enumerate(dates)}
    sid = protocol["strategyOrder"][0]
    identity = lambda r: (r["strategyId"], r["symbol"], r["signalDate"])
    by_day = defaultdict(list)
    for row in candidates:
        by_day[row["signalDate"]].append(row)
    check(Counter(identity(r) for r in signals if r["strategyId"] == sid) ==
          Counter(identity(r) for r in candidates if r["selectedTop5"]), "selected-equals-raw-top-five")
    check(Counter(identity(r) for r in signals) == Counter(identity(r) for r in result["events"] + result["overlapSuppressed"]), "all-signal-dispositions")
    check(len(signals) == len({identity(r) for r in signals}), "no-duplicate-signal-identity")
    check([r["date"] for r in selections] == dates, "daily-selection-coverage")

    spec = importlib.util.spec_from_file_location("_independent_vcp_source", ROOT / "scripts/run-strategy-search.py")
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    selection = read(OUTPUT / "selection.json")
    dataset = source.bound_snapshot(snapshot_universe(Path(protocol["workspace"]), selection,
        protocol["signalEnd"], protocol["signalStart"]), protocol["signalStart"], protocol["signalEnd"])
    check(dataset["fingerprint"] == snapshot["fingerprint"] == receipt["snapshotFingerprint"] == protocol["registration"]["sample"]["expectedFullSnapshotFingerprint"], "independent-source-fingerprint")
    check(len(dataset["securities"]) == 3141 and sum(len(s["bars"]) for s in dataset["securities"]) == 1796835, "independent-source-counts")
    raw = {s["symbol"]: {b["date"]: b for b in s["bars"]} for s in dataset["securities"]}
    securities = {s["symbol"]: s for s in dataset["securities"]}
    calendar, config = dataset["calendar"], protocol["config"]
    ci = {d: i for i, d in enumerate(calendar)}
    scorer = DailyScorer(dataset, config, pools_only=True)
    for cover, selected in zip(coverage, selections):
        day = cover["date"]
        rows = by_day[day]
        check(cover["evaluatedSecurities"] == sum(cover["evaluatedKinds"].values()) == sum(cover["evaluatedReasons"].values()) == 3141, "daily-all-security-accounting", date=day)
        check(cover["evaluatedKinds"].get("signaled", 0) == len(rows) == cover["candidateCounts"][sid], "daily-raw-count", date=day)
        expected = sorted(rows, key=lambda r: (-r["prior20MeanAmount"], r["symbol"]))
        check(rows == expected and [r["rank"] for r in rows] == list(range(1, len(rows) + 1)), "full-ranking", date=day)
        check(all(r["selectedTop5"] == (i < 5) for i, r in enumerate(rows)), "top-five-before-active", date=day)
        check(selected["fullRanking"] == [{k: r[k] for k in ("symbol", "rank", "prior20MeanAmount", "selectedTop5")} for r in rows], "selection-candidate-lineage", date=day)
        top = [r["symbol"] for r in rows[:5]]
        check(selected["selectedTop5Symbols"] == top and selected["activeExclusionApplied"] is False, "selection-no-active-backfill")
        qualified = scorer._qualified_universe(day)
        eligible = {s for s, state in qualified["eligibilityBySymbol"].items() if state.get("eligible") is True}
        pool = eligible - set(top)
        controls = sorted(pool, key=lambda s: (hashlib.sha256(f"{protocol['controlSeed']}:{day}:{sid}:{s}".encode()).hexdigest(), s))[:len(top)]
        matching = {"baseEligibleCount": len(eligible), "eligibleControlPoolSize": len(pool), "requestedCount": len(top),
                    "matchedCount": len(controls), "shortfall": len(top) - len(controls), "selectedSymbols": controls}
        check(matching == cover["controlMatching"][sid] == selected["controlMatching"], "source-control-pool-hash", date=day)
        check(controls == [r["symbol"] for r in signals if r["signalDate"] == day and r["strategyId"] == "control_" + sid], "control-signal-lineage")
        for row in rows:
            symbol = row["symbol"]
            history = [raw[symbol].get(d) for d in calendar[ci[day]-80:ci[day]+1]]
            check(symbol in eligible and len(history) == 81 and all(history), "raw-candidate-full-causal-history", symbol=symbol, date=day)
            if len(history) != 81 or not all(history):
                continue
            check(all(all(type(b.get(k)) in (int, float) and math.isfinite(b[k]) and b[k] > 0 for k in ("open", "high", "low", "close", "volume")) for b in history), "positive-finite-ohlcv")
            last, prior = history[-1], history[-41:-1]
            check(last["close"] > max(b["high"] for b in prior) and last["close"] > mean(b["close"] for b in history[1:]) > mean(b["close"] for b in history[:-1]), "independent-raw-breakout-and-MA")
            check(last["volume"] / mean(b["volume"] for b in prior) >= 1.2, "independent-breakout-volume")
            blocks = (history[-31:-21], history[-21:-11], history[-11:-1])
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                ds = [Decimal(str(max(b["high"] for b in part))) / Decimal(str(min(b["low"] for b in part))) - 1 for part in blocks]
                check(ds[0] > ds[1] > ds[2] and ds[2] <= Decimal(".08"), "independent-range-contraction")
            check(mean(b["volume"] for b in blocks[2]) <= .70 * mean(b["volume"] for b in blocks[0]), "independent-volume-contraction")
            amounts = [b.get("amount") for b in history[-21:-1]]
            check(all(type(a) in (int, float) and math.isfinite(a) and a > 0 for a in amounts), "raw-ranking-amount-coverage")
            expected_amount = mean(amounts)
            check(same(expected_amount, row["prior20MeanAmount"]) and same(row["score"], expected_amount) and same(row["rawScore"], expected_amount), "raw-prior20-ranking-excludes-T")
            facts = row["features"]
            expected_values = {"prior40High": max(b["high"] for b in prior), "ma80": mean(b["close"] for b in history[1:]),
                "previousMa80": mean(b["close"] for b in history[:-1]), "prior40MeanVolume": mean(b["volume"] for b in prior),
                "volumeRatio40": last["volume"] / mean(b["volume"] for b in prior), "prior20MeanAmount": expected_amount}
            check(all(same(facts[k], v) for k, v in expected_values.items()), "logged-numeric-facts-from-source")
            check(facts["current"] == {k: last[k] for k in ("date", "open", "high", "low", "close", "volume")} and facts["historyStart"] == history[0]["date"] and facts["historyEnd"] == day and facts["observations"] == 81, "logged-source-clock")
            check(facts["rankingWindow"] == {"startDate": history[-21]["date"], "endDate": history[-2]["date"], "sessions": 20} and
                  facts["prior40Window"] == {"startDate": prior[0]["date"], "endDate": prior[-1]["date"], "sessions": 40}, "logged-window-endpoints")
            check(len(facts["contractionBlocks"]) == 3, "exactly-three-blocks")
            for j, (block, logged) in enumerate(zip(blocks, facts["contractionBlocks"])):
                check(logged == {"id": j+1, "startDate": block[0]["date"], "endDate": block[-1]["date"], "sessions": 10,
                    "maxHigh": max(b["high"] for b in block), "minLow": min(b["low"] for b in block),
                    "rangeRatio": float(ds[j]), "rangeDecimal": str(ds[j]), "meanVolume": mean(b["volume"] for b in block)}, "logged-block-facts-from-source")
            check(len(facts["conditions"]) == 7 and all(v is True for v in facts["conditions"].values()) and facts["failedConditions"] == [], "all-seven-gates-recorded")
    check(sum(c["evaluatedSecurities"] for c in coverage) == summary["performance"]["evaluatedSecuritySessions"] == 1520244, "full-evaluation-total")

    event_by_id = {e["eventId"]: e for e in result["events"]}
    check(len(event_by_id) == len(result["events"]), "unique-events")
    event_by_signal = {identity(e): e for e in result["events"]}
    suppressed_by_signal = {identity(e): e for e in result["overlapSuppressed"]}
    active = {}
    check([s["signalDate"] for s in signals] == sorted(s["signalDate"] for s in signals), "chronological-signals")
    for signal in signals:
        key, ident = (signal["strategyId"], signal["symbol"]), identity(signal)
        prior = active.get(key)
        hit = prior["outcomes"]["nextOpenNet30"]["hitDate"] if prior else None
        blocked = prior is not None and (hit is None or signal["signalDate"] < hit)
        check((ident in suppressed_by_signal) == blocked and (ident in event_by_signal) != blocked, "complete-active-disposition-replay", signal=ident)
        if blocked:
            if ident in suppressed_by_signal:
                check(suppressed_by_signal[ident]["blockedByEventId"] == prior["eventId"], "active-blocker-identity")
        elif ident in event_by_signal:
            event = event_by_signal[ident]
            if event["entry"]["status"] == "filled":
                active[key] = event
            else:
                active.pop(key, None)
    for row in result["overlapSuppressed"]:
        prior = event_by_id[row["blockedByEventId"]]
        hit = prior["outcomes"]["nextOpenNet30"]["hitDate"]
        check(prior["entry"]["status"] == "filled" and prior["strategyId"] == row["strategyId"] and prior["symbol"] == row["symbol"] and
              prior["signalDate"] < row["signalDate"] and (hit is None or row["signalDate"] < hit), "suppression-active-before-close")
    for event in result["events"]:
        entry = event["entry"]
        if entry["date"]:
            check(index[entry["date"]] == index[event["signalDate"]] + 1, "single-next-session-entry")
        if entry["status"] != "filled":
            continue
        check(same(entry["price"] * entry["quantity"] + entry["fee"], entry["investedCapital"]), "entry-cost-arithmetic")
        hit = event["outcomes"]["nextOpenNet30"]
        if hit["hitDate"]:
            check(hit["tradingDays"] == index[hit["hitDate"]] - index[entry["date"]] + 1 >= 2, "target-T-plus-one")
            net = event["terminal"]["proceeds"] / entry["investedCapital"] - 1
            check(net >= .3-1e-10 and same(net * 100, event["terminal"]["netReturnPct"]), "net-target-arithmetic")
    for group in result["groups"]:
        events = [e for e in result["events"] if e["strategyId"] == group["strategyId"]]
        entered = [e for e in events if e["entry"]["status"] == "filled"]
        check(Counter(e["entry"]["status"] for e in events) == Counter(group["entryStatusCounts"]) and len(entered) == group["filledEntryCount"], "group-entry-counts")
        for h in group["horizons"]:
            horizon = h["tradingDays"]
            mature = [e for e in entered if len(dates)-index[e["entry"]["date"]] >= horizon]
            valid = []
            for e in mature:
                stop = min(horizon, e["outcomes"]["nextOpenNet30"]["tradingDays"] or horizon)
                if e["entry"]["executionValid"] is True and not any(g["tradingDay"] <= stop for g in e["observation"]["missingDates"]) and not any(q.get("tradingDay", 1) <= stop for q in e["quality"]["executionCapacityIssues"]):
                    valid.append(e)
            unknown = len(mature)-len(valid)
            check(len(mature) == h["completeCalendarCohort"] and len(valid) == h["eligibleEntryCohort"] and unknown == h["missingOrInvalidDataExcluded"] and len(entered)-len(mature) == h["rightCensoredBeforeHorizon"], "horizon-denominators")
            for proxy, values in h["proxies"].items():
                times = [e["outcomes"][proxy]["tradingDays"] for e in valid if e["outcomes"][proxy]["tradingDays"] is not None and e["outcomes"][proxy]["tradingDays"] <= horizon]
                check(len(times) == values["hits"] and len(valid) == values["denominator"] and same(values["hitRate"], len(times)/len(valid) if valid else None), "target-fraction")
                check(values["medianHitTradingDays"] == (median(times) if times else None), "hit-time-median")
                bounds = values["missingOutcomeBounds"]
                check(bounds["denominator"] == len(mature) and bounds["unresolved"] == unknown and same(bounds["lower"], len(times)/len(mature) if mature else None) and same(bounds["upper"], (len(times)+unknown)/len(mature) if mature else None), "missing-bounds")

    paths = result["pathRiskEvents"]
    check(len(paths) == len({p["eventId"] for p in paths}) and {p["eventId"] for p in paths} == {e["eventId"] for e in result["events"] if e["entry"]["status"] == "filled"}, "all-unique-filled-paths")
    for p in paths:
        e = event_by_id[p["eventId"]]
        hit = e["outcomes"]["nextOpenNet30"]
        check(p["strategyId"] == e["strategyId"] and p["symbol"] == e["symbol"] and p["entryDate"] == e["entry"]["date"] and
              p["throughDate"] == (hit["hitDate"] or calendar[-1]) == e["activeUntil"] and
              p["targetTradingDays"] == hit["tradingDays"] and p["targetDate"] == hit["hitDate"], "path-bound-to-original-event")
        start, stop = ci[e["entry"]["date"]], p["throughDate"]
        boundaries, gaps, mae = {"-10": None, "-20": None}, [], 0.
        for offset, day in enumerate(calendar[start:], 1):
            if day > stop:
                break
            if day == p["targetDate"]:
                low = (e["terminal"]["price"]/e["entry"]["price"]-1)*100
            else:
                status = securities[e["symbol"]].get("tradeStatusByDate", {}).get(day)
                if status in (False, 0, "0"):
                    continue
                bar = raw[e["symbol"]].get(day)
                if bar is None or bar["volume"] <= 0:
                    gaps.append({"date": day, "tradingDay": offset, "reason": "missingBar" if bar is None else "unexplainedZeroVolume"})
                    continue
                low = (bar["low"]/e["entry"]["price"]-1)*100
            mae = min(mae, low)
            for level in boundaries:
                if boundaries[level] is None and low <= int(level)+1e-10:
                    boundaries[level] = {"date": day, "tradingDay": offset, "entryDayTPlusOneLocked": offset == 1}
        check(boundaries == p["firstObservedBoundary"] and gaps == p["missing"] and same(mae, p["grossMaePct"]), "independent-raw-path", eventId=e["eventId"])
        invalid = {q.get("tradingDay", 1) for q in e["quality"]["executionCapacityIssues"]}
        if e["entry"]["executionValid"] is not True:
            invalid.add(1)
        check(sorted(invalid) == p["invalidExecutionOffsets"] and p["potentialFollowupSessions"] == len(calendar)-start and
              p["observedFollowupSessions"] == ci[stop]-start+1 and p["targetDate"] == e["outcomes"]["nextOpenNet30"]["hitDate"], "path-evidence-timing")
    for group in result["pathRiskSummary"]["groups"]:
        gp = [p for p in paths if p["strategyId"] == group["strategyId"]]
        keys = [(o["horizonSessions"], o["rawPriceBoundaryPct"]) for o in group["observations"]]
        check(len(keys) == 16 and set(keys) == {(h, b) for h in (None, 5, 10, 20, 40, 60, 120, 250) for b in (-10, -20)}, "complete-unique-risk-observation-grid")
        for observation in group["observations"]:
            horizon, level = observation["horizonSessions"], observation["rawPriceBoundaryPct"]
            cohort = [p for p in gp if horizon is None or p["potentialFollowupSessions"] >= horizon]
            counts = Counter()
            for p in cohort:
                through = min(horizon or p["potentialFollowupSessions"], p["observedFollowupSessions"])
                if any(g["tradingDay"] <= through for g in p["missing"]) or any(i <= through for i in p["invalidExecutionOffsets"]):
                    category = "dataUnresolved"
                else:
                    hit = p["targetTradingDays"] is not None and p["targetTradingDays"] <= through
                    boundary = p["firstObservedBoundary"][str(level)]
                    risk = boundary is not None and boundary["tradingDay"] <= through
                    category = ("boundaryThenTarget" if risk else "targetWithoutObservedBoundary") if hit else ("boundaryStillWaiting" if risk else "neitherObserved")
                counts[category] += 1
            expected = {k: counts[k] for k in ("targetWithoutObservedBoundary", "boundaryThenTarget", "boundaryStillWaiting", "neitherObserved", "dataUnresolved")}
            check(observation["counts"] == expected and observation["cohortCount"] == len(cohort) and observation["insufficientPotentialFollowupCount"] == len(gp)-len(cohort), "independent-risk-cohort-classification")
            bounds = observation["targetWithoutBoundaryMissingOutcomeBounds"]
            check(same(bounds["lower"], counts["targetWithoutObservedBoundary"]/len(cohort) if cohort else None) and
                  same(bounds["upper"], (counts["targetWithoutObservedBoundary"]+counts["dataUnresolved"])/len(cohort) if cohort else None), "independent-risk-missing-bounds")
    check(result["certified"] is False and result["targetAchieved"] is False, "no-certification")
    report = {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(), "passed": not issues,
        "checks": checks, "issues": issues, "candidateCount": len(candidates), "events": len(result["events"]),
        "sourceFingerprint": dataset["fingerprint"], "resultSha256": sha(attempt / "result.json.gz"),
        "verifierSha256": sha(Path(__file__)), "scope": "Raw-source replay of every emitted candidate and filled adverse path, every day's control pool/hash, all rankings/dispositions/cohort arithmetic and frozen hashes",
        "limitations": ["Uses the frozen source loader and legacy base eligibility implementation; does not independently replay every rejected candidate predicate.",
                        "Does not certify corporate actions, actual opening fills, exhaustive historical membership or independent future target probability."],
        "certified": False, "targetAchieved": False}
    path = save(report)
    print(json.dumps({"passed": not issues, "checks": checks, "issues": issues[:8], "report": str(path)}))
    return int(bool(issues))


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:
        path = save({"passed": False, "verificationCompleted": False, "errorType": type(exc).__name__,
                     "error": str(exc), "checkedAt": datetime.now(timezone.utc).isoformat(), "certified": False})
        print(json.dumps({"passed": False, "errorType": type(exc).__name__, "report": str(path)}))
        raise
    raise SystemExit(code)
