"""Replay the fixed persistent-leader trial against its audited full panel.

Does not call the signal detector or ranker. Shares the source loader and uses
the previously independently replayed panel; no market-influence certification.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from statistics import mean, median
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/strategy-search/signal-persistent-leader-v1"
PANEL = ROOT / "artifacts/strategy-search/leadership-panel-v1"
CANDIDATE_ID = "rawPersistentPriceLeaderBreakoutDiagnostic"
HORIZONS = (5, 10, 20, 40, 60, 120, 250)
sys.path.insert(0, str(ROOT))
from service.portfolio_universe import snapshot_universe

GATES = (("meanStrengthPercentile20", ">=", .90), ("minimumStrengthPercentile20", ">=", .80),
         ("relativeReturn60Percentile", ">=", .90), ("closePathMaxDrawdown20", "<=", .10),
         ("signedPathEfficiency20", ">=", .35))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def iter_lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    result = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        result.update(chunk.encode("utf-8"))
    return result.hexdigest()


def identity(row):
    return row["strategyId"], row["symbol"], row["signalDate"]


def same(a, b):
    if a is None or b is None:
        return a is b
    return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def valid_feature(key, value):
    return finite(value) and (-1 if key == "signedPathEfficiency20" else 0) <= value <= 1


def price_valid(bar, day):
    return (isinstance(bar, dict) and bar.get("date") == day and
            all(finite(bar.get(k)) and bar[k] > 0 for k in ("open", "high", "low", "close", "volume")) and
            bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"])


def expected_kind(row, days, bars):
    """Classify hash-bound, well-formed audited panel rows independently.

    Source/schema corruption is handled by source binding and panel audit,
    not converted to a valid rejection. No new score or rank is constructed.
    """
    base = row["baseEligibility"]
    if base["reason"] in ("invalidHistory", "missingDailyBar", "notObservedYet"):
        return "unevaluable", None
    if base["eligible"] is False:
        return "ineligible", None
    if len(days) != 61 or len(bars) != 61 or not all(price_valid(b, d) for b, d in zip(bars, days)):
        return "unevaluable", None
    if row["haltDates"] or not all(row["completeness"][k]["complete"] for k in
                                 ("security60", "benchmark60", "security20", "benchmark20", "path20")):
        return "unevaluable", None
    history = row["trailing20DailyStrengthPercentiles"]
    if len(history) != 20 or not all(finite(v) for v in history) or row["knownStrengthRankSessions20"] != 20:
        return "unevaluable", None
    if not all(valid_feature(k, row[k]) for k, _, _ in GATES):
        return "unevaluable", None
    passed = {k: row[k] >= v if op == ">=" else row[k] <= v for k, op, v in GATES}
    passed["closeAbovePrior5High"] = bars[-1]["close"] > max(b["high"] for b in bars[-6:-1])
    return ("signaled" if all(passed.values()) else "notTriggered"), passed


def save(report):
    path = OUTPUT / "independent-result-verification.json"
    if path.exists() or not report.get("passed"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = OUTPUT / f"independent-result-verification-{stamp}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return path


def main():
    issues, checks, failed = [], 0, 0
    started = last_notice = time.perf_counter()

    def check(ok, label, **details):
        nonlocal checks, failed
        checks += 1
        if not ok:
            failed += 1
            if len(issues) < 100:
                issues.append({"check": label, **details})

    protocol = read(OUTPUT / "protocol.json")
    successful = list((OUTPUT / "attempts").glob("attempt-*/completion.json"))
    if len(successful) != 1:
        raise ValueError("Exactly one successful immutable attempt is required")
    attempt = successful[0].parent
    receipt = read(successful[0])
    snapshot = read(OUTPUT / "snapshot.json")
    ph = hashlib.sha256(json.dumps(protocol, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    check(receipt["protocolHash"] == ph, "protocol-hash")
    names = {"candidates": "candidates.jsonl.gz", "coverage": "coverage.jsonl.gz", "signals": "signals.jsonl.gz",
             "result": "result.json.gz", "summary": "summary.json", "selections": "selection.jsonl.gz",
             "predicates": "predicates.jsonl.gz", "executionSources": "execution-sources.zip"}
    check(set(receipt["files"]) == set(names), "complete-eight-artifacts")
    for key, item in receipt["files"].items():
        path = (OUTPUT / item["path"]).resolve()
        check(path == (attempt / names[key]).resolve() and path.is_relative_to(OUTPUT) and sha(path) == item["sha256"], "artifact-path-hash", key=key)
    with zipfile.ZipFile(attempt / "execution-sources.zip") as archive:
        for name, expected in protocol["implementation"].items():
            check(sha(ROOT / name) == expected and hashlib.sha256(archive.read(name.replace("\\", "/"))).hexdigest() == expected,
                  "source-archive-and-live-hash", name=name)
    binding = protocol["registration"]["registrationPrerequisites"]
    check(sha(PANEL / "manifest.json") == binding["panelManifestSha256"] and
          sha(PANEL / "completion.json") == binding["panelCompletionSha256"] and
          sha(PANEL / "independent-verification.json") == binding["actualPanelAuditSha256"], "registered-panel-primary-binding")
    for name, expected_hash in binding["panelFilesSha256"].items():
        check(sha(PANEL / name) == expected_hash, "registered-panel-file-hash", name=name)
    panel_manifest = read(PANEL / "manifest.json")
    panel_audit = read(PANEL / "independent-verification.json")
    check(panel_audit["passed"] and panel_audit["failureCount"] == 0 and panel_audit["featureRows"] == 1520244 and
          panel_audit["sampleManifestHash"] == binding["panelManifestSha256"], "actual-panel-replay-passed")
    with gzip.open(attempt / "result.json.gz", "rt", encoding="utf-8") as stream:
        result = json.load(stream)
    summary = read(attempt / "summary.json")
    coverage, selections, candidates, signals = [list(iter_lines(attempt / name)) for name in
        ("coverage.jsonl.gz", "selection.jsonl.gz", "candidates.jsonl.gz", "signals.jsonl.gz")]
    check(result["groups"] == summary["groups"] and result["pathRiskSummary"] == summary["pathRiskSummary"], "summary-result-match")
    dates = [r["date"] for r in coverage]
    check(dates == sorted(set(dates)) and len(dates) == 484 and Counter(d[:4] for d in dates) == {"2022": 242, "2023": 242}, "484-development-dates")
    index = {d: i for i, d in enumerate(dates)}
    sid = protocol["strategyOrder"][0]
    check(protocol["strategyOrder"] == [CANDIDATE_ID], "exact-registered-strategy")
    by_day = defaultdict(list)
    for row in candidates:
        by_day[row["signalDate"]].append(row)
    check(Counter(identity(r) for r in signals if r["strategyId"] == sid) == Counter(identity(r) for r in candidates if r["selectedTop5"]), "selected-equals-raw-top-five")
    check(Counter(identity(r) for r in signals) == Counter(identity(r) for r in result["events"] + result["overlapSuppressed"]), "all-signal-dispositions")
    check(len(signals) == len({identity(r) for r in signals}) and [r["date"] for r in selections] == dates, "signal-identity-selection-date-coverage")

    spec = importlib.util.spec_from_file_location("_independent_leader_source", ROOT / "scripts/run-strategy-search.py")
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    dataset = source.bound_snapshot(snapshot_universe(Path(protocol["workspace"]), read(OUTPUT / "selection.json"),
        protocol["signalEnd"], protocol["signalStart"]), protocol["signalStart"], protocol["signalEnd"])
    check(dataset["fingerprint"] == snapshot["fingerprint"] == receipt["snapshotFingerprint"] == protocol["registration"]["sample"]["expectedFullSnapshotFingerprint"], "source-fingerprint")
    check(len(dataset["securities"]) == 3141 and sum(len(s["bars"]) for s in dataset["securities"]) == 1796835, "source-counts")
    check(digest(dataset) == panel_manifest["inputs"]["fullDatasetContentSha256"], "full-source-content-binding")
    for name, expected in panel_manifest["physicalSourceHashes"].items():
        check(sha(Path(name)) == expected, "physical-source-file-binding")
    raw = {s["symbol"]: {b["date"]: b for b in s["bars"]} for s in dataset["securities"]}
    securities = {s["symbol"]: s for s in dataset["securities"]}
    symbols = sorted(securities)
    calendar, config = dataset["calendar"], protocol["config"]
    ci = {d: i for i, d in enumerate(calendar)}
    panel_rows, predicate_rows = iter_lines(PANEL / "features.jsonl.gz"), iter_lines(attempt / "predicates.jsonl.gz")
    total_rows, total_kinds, total_reasons = 0, Counter(), Counter()
    for cover, selection in zip(coverage, selections):
        day = cover["date"]
        days = calendar[ci[day]-60:ci[day]+1]
        eligible, expected_signals, daily_kinds, daily_reasons = set(), set(), Counter(), Counter()
        candidate_lookup = {r["symbol"]: r for r in by_day[day]}
        for symbol in symbols:
            panel, record = next(panel_rows), next(predicate_rows)
            total_rows += 1
            check((panel["asOfDate"], panel["symbol"]) == (day, symbol), "panel-row-order")
            check((record["date"], record["symbol"]) == (day, symbol), "predicate-row-order")
            check(record["panelManifestSha256"] == binding["panelManifestSha256"] and
                  record["panelFeatureFileSha256"] == binding["panelFilesSha256"]["features.jsonl.gz"] and
                  record["calendarHash"] == panel["calendarHash"] and record["sourceHashes"] == panel["sourceHashes"], "predicate-panel-source-binding")
            bars = [raw[symbol].get(d) for d in days]
            expected, predicates = expected_kind(panel, days, bars)
            check(record["kind"] == expected, "all-row-independent-classification", day=day, symbol=symbol)
            daily_kinds[record["kind"]] += 1
            daily_reasons[record["reason"]] += 1
            if panel["baseEligibility"]["eligible"] is True:
                eligible.add(symbol)
            check(set(record["predicates"]) == {k for k, _, _ in GATES} | {"closeAbovePrior5High"}, "six-predicate-evidence-fields")
            for key, op, threshold in GATES:
                p = record["predicates"][key]
                expected_value = panel[key] if valid_feature(key, panel[key]) else None
                check(p["operator"] == op and p["threshold"] == threshold and same(p["value"], expected_value), "predicate-value-and-rule", field=key)
            if predicates is not None:
                check({k: v["passed"] for k, v in record["predicates"].items()} == predicates, "all-evaluable-predicate-decisions")
                check(same(record["predicates"]["closeAbovePrior5High"]["value"], bars[-1]["close"]) and
                      same(record["predicates"]["closeAbovePrior5High"]["threshold"], max(b["high"] for b in bars[-6:-1])), "breakout-reference-excludes-current")
            if expected == "signaled":
                expected_signals.add(symbol)
                c = candidate_lookup.get(symbol)
                check(c is not None, "no-omitted-raw-candidate", day=day, symbol=symbol)
                if c is not None:
                    facts = c["features"]
                    check(facts["breakoutBars"] == [{k: b[k] for k in ("date", "open", "high", "low", "close", "volume")} for b in bars[-6:]], "candidate-six-raw-bars")
                    check(facts["archivedRankDates20"] == panel["strengthRankSessionDates20"] and
                          facts["archivedRankPercentiles20"] == panel["trailing20DailyStrengthPercentiles"] and
                          facts["archivedRankCrossSectionCounts20"] == panel["trailing20StrengthCrossSectionCounts"] and
                          same(mean(facts["archivedRankPercentiles20"]), c["meanStrengthPercentile20"]) and
                          c["relativeReturn60Percentile"] == panel["relativeReturn60Percentile"], "candidate-archived-rank-lineage")
                    check(c["predicates"] == record["predicates"] and c["features"] == record["features"], "candidate-predicate-log-lineage")
        rows = by_day[day]
        check(set(candidate_lookup) == expected_signals and len(rows) == len(expected_signals), "exact-full-candidate-set")
        ordered = sorted(rows, key=lambda r: (-r["meanStrengthPercentile20"], -r["relativeReturn60Percentile"], r["symbol"]))
        check(rows == ordered and [r["rank"] for r in rows] == list(range(1, len(rows)+1)) and
              all(r["selectedTop5"] == (i < 5) for i, r in enumerate(rows)), "lexicographic-full-rank-top-five")
        top = [r["symbol"] for r in rows[:5]]
        check(selection["fullRanking"] == [{k: r[k] for k in ("symbol", "rank", "meanStrengthPercentile20",
              "relativeReturn60Percentile", "selectedTop5")} for r in rows] and
              all(r["score"] == r["rawScore"] == r["meanStrengthPercentile20"] for r in rows), "selection-full-rank-lineage")
        check(selection["selectedTop5Symbols"] == top and selection["activeExclusionApplied"] is False, "top-five-before-active")
        pool = eligible - set(top)
        controls = sorted(pool, key=lambda s: (hashlib.sha256(f"{protocol['controlSeed']}:{day}:{sid}:{s}".encode()).hexdigest(), s))[:len(top)]
        matching = {"baseEligibleCount": len(eligible), "eligibleControlPoolSize": len(pool), "requestedCount": len(top),
                    "matchedCount": len(controls), "shortfall": len(top)-len(controls), "selectedSymbols": controls}
        check(matching == cover["controlMatching"][sid] == selection["controlMatching"], "base-panel-control-pool-hash")
        check(controls == [r["symbol"] for r in signals if r["signalDate"] == day and r["strategyId"] == "control_"+sid], "control-signal-lineage")
        check(cover["evaluatedKinds"] == dict(daily_kinds) and cover["evaluatedReasons"] == dict(daily_reasons) and
              sum(daily_kinds.values()) == cover["evaluatedSecurities"] == 3141 and cover["candidateCounts"][sid] == len(rows), "daily-all-row-accounting")
        total_kinds.update(daily_kinds)
        total_reasons.update(daily_reasons)
        if time.perf_counter()-last_notice > 30:
            print(f"Persistent-leader independent replay: {total_rows}/1520244 rows", flush=True)
            last_notice = time.perf_counter()
    check(next(panel_rows, None) is None and next(predicate_rows, None) is None and total_rows == 1520244, "complete-panel-and-predicate-EOF")
    check(summary["performance"]["evaluatedSecuritySessions"] == total_rows and
          summary["performance"]["evaluatedKinds"] == dict(total_kinds) and summary["performance"]["evaluatedReasons"] == dict(total_reasons), "total-evaluation-accounting")
    audit_outcomes(check, result, signals, dates, index, calendar, ci, raw, securities, config)
    check(result["certified"] is False and result["targetAchieved"] is False, "no-certification")
    report = {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(), "passed": failed == 0,
        "checks": checks, "failureCount": failed, "issues": issues, "evaluatedRows": total_rows,
        "candidateCount": len(candidates), "events": len(result["events"]), "sourceFingerprint": dataset["fingerprint"],
        "resultSha256": sha(attempt / "result.json.gz"), "verifierSha256": sha(Path(__file__)),
        "elapsedSeconds": time.perf_counter()-started,
        "scope": "All fixed panel rows classified independently; all evaluable six predicates, raw candidates, ranks, controls, active dispositions and cohort arithmetic. Raw adverse paths and fee/slippage arithmetic replayed for filled events. Input/output/source archives hash bound.",
        "limitations": ["Shares the original source loader and trusts the separately hash-bound independently replayed feature panel; does not rederive every rank again or certify original source coverage.",
                       "Partial/unevaluable predicate truth values and exact rejection reason priority are checked through synthetic tests and accounting, not independently reimplemented in full here.",
                       "Does not independently reimplement every execution eligibility/capacity gate or prove first executable opening under actual auction liquidity; arithmetic and lifecycle evidence are narrower than actual fill certification.",
                       "No corporate-action profit, historical whole-market membership, market influence, independent holdout or future-probability certification."],
        "certified": False, "targetAchieved": False}
    path = save(report)
    print(json.dumps({"passed": failed == 0, "checks": checks, "failures": failed, "issues": issues[:8], "report": str(path)}), flush=True)
    return int(failed != 0)


# The following lifecycle/cohort audit is copied from the previously reviewed
# VCP result verifier, without importing its signal detector or runner.


def audit_outcomes(check, result, signals, dates, index, calendar, ci, raw, securities, config):
    expected_groups = {CANDIDATE_ID, "control_" + CANDIDATE_ID}
    group_ids = [g["strategyId"] for g in result["groups"]]
    check(len(group_ids) == 2 and set(group_ids) == expected_groups, "complete-unique-target-groups")
    for group in result["groups"]:
        grid = [h["tradingDays"] for h in group["horizons"]]
        check(len(grid) == len(HORIZONS) and set(grid) == set(HORIZONS), "complete-unique-target-horizon-grid")
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
    risk_ids = [g["strategyId"] for g in result["pathRiskSummary"]["groups"]]
    check(len(risk_ids) == len(set(risk_ids)) and set(risk_ids) == {p["strategyId"] for p in paths},
          "complete-unique-filled-risk-groups")
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

    check(all(config[k] == v for k, v in {"commissionRate": .0003, "minCommission": 5,
          "transferFeeRate": .00001, "slippageBps": 10, "maxParticipationRate": .01,
          "stampDutyRate": None}.items()), "unchanged-registered-cost-configuration")

    def fee(amount, side, day):
        tax = .001 if day < "2023-08-28" else .0005
        return max(5, amount*.0003)+amount*.00001+(amount*tax if side == "sell" else 0)

    for event in result["events"]:
        entry = event["entry"]
        if entry["status"] != "filled":
            continue
        quantity = entry["quantity"]
        original = raw[event["symbol"]][entry["date"]]
        check(same(entry["price"], original["open"]*1.001) and quantity > 0 and quantity % 100 == 0,
              "independent-entry-slippage-and-lot")
        check(same(entry["fee"], fee(quantity*entry["price"], "buy", entry["date"])) and
              same(entry["remainingCash"]+entry["investedCapital"], 100000), "independent-buy-fees-and-capital")
        if event["outcomes"]["nextOpenNet30"]["hitDate"]:
            terminal = event["terminal"]
            day = event["outcomes"]["nextOpenNet30"]["hitDate"]
            price = raw[event["symbol"]][day]["open"]*.999
            check(same(terminal["price"], price) and
                  same(terminal["proceeds"], quantity*price-fee(quantity*price, "sell", day)),
                  "independent-target-slippage-sell-fees")


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:
        path = save({"passed": False, "verificationCompleted": False, "errorType": type(exc).__name__,
                     "error": str(exc), "checkedAt": datetime.now(timezone.utc).isoformat(), "certified": False})
        print(json.dumps({"passed": False, "errorType": type(exc).__name__, "report": str(path)}))
        raise
    raise SystemExit(code)
