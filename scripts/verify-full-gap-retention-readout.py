"""Independent full-gap readout audit; never imports its new summarizer.

Cash episodes and continuous interval arithmetic use frozen independent
references. New glue verifies formation/decision clocks and complete coverage.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/full-gap-retention-v1"
MAIN = "scripts/verify-full-gap-retention.py"
OLD = "scripts/verify-online-feedback-readout.py"
OLD_SHA = "dd2f467f25d91ecbd5712b40dc8d7c02717ab4ac82a6d755dd51c306c9c233b2"
EPISODES = "scripts/verify-reclaim-exit-mechanism.py"
EPISODES_SHA = "96ef23022f1a8a515ecf94eabdab8696057bcb166a2cd309fe43f101a507cd89"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10", "volatilityTrail")
POLICIES = ("control", "retained")
KEYS = ("period", "board", "exit", "policy")


def require(value, message):
    if not value: raise AssertionError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz") else Path(path).open(encoding="utf-8")) as stream:
        return json.load(stream)


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def pinned(ref):
    path = (ROOT / ref["path"]).resolve()
    require(not Path(ref["path"]).is_absolute() and path.is_relative_to(ROOT)
        and sha(path) == ref["sha256"], "Changed or nonrepository reference: " + ref["path"])
    return path


def reference(path):
    return {"path": Path(path).resolve().relative_to(ROOT).as_posix(), "sha256": sha(path)}


def load_file(name, expected=None):
    if expected: require(sha(ROOT / name) == expected, "Frozen independent helper changed")
    spec = importlib.util.spec_from_file_location("gap_readout_independent_" + Path(name).stem.replace("-", "_"), ROOT / name)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def same(actual, expected, label="value"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + " keys")
        for key, value in expected.items(): same(actual[key], value, label + "." + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + " length")
        for index, value in enumerate(expected): same(actual[index], value, label + ":" + str(index))
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), label + " number")
    else:
        require(type(actual) is type(expected) and actual == expected, label + " value")


MATH = SimpleNamespace(require=require, finite=finite, same=same, near=same)


def formation_reference(key, opportunities, counts, schedules):
    require(len({r["formationId"] for r in opportunities}) == len(opportunities) == counts["opportunities"], "Unique original Top5")
    known = [r for r in opportunities if r["confirmation"]["status"] == "known"]
    expected_confirmation = {s: sum(r["confirmation"]["status"] == s for r in opportunities) for s in ("known", "unknown", "censored")}
    same(counts["confirmation"], expected_confirmation, "Confirmation population")
    same(counts["outcomes"], {"retained": sum(r["retained"] is True for r in known),
        "filled": sum(r["retained"] is False for r in known),
        "unknown": expected_confirmation["unknown"], "censored": expected_confirmation["censored"]}, "Outcome population")
    require(counts["rawSetups"] - counts["lockedSetups"] - counts["rankExcludedSetups"] == len(opportunities), "Setup conservation")
    for policy in POLICIES:
        expected = [r["signals"][policy] for r in opportunities if r["signals"][policy] is not None]
        same(schedules[policy], expected, "Original-order same-clock schedule")
        require(len(expected) == counts["signals"][policy], "Signal count")
    return {**key, **deepcopy(counts), "distinctFormationDays": len(set(r["formationDate"] for r in opportunities)),
        "distinctSymbols": len(set(r["symbol"] for r in opportunities)),
        "meaning": "Raw setups include locked and rank-excluded events; only original Top5 formations are account opportunities. Unknown and censored confirmation excludes both policies."}


def episodes_reference(key, opportunities, schedule, result, old, episodes):
    originals = [r["signals"]["control"] for r in opportunities if r["signals"]["control"] is not None]
    details, entries = old.episodes_reference(key, originals, schedule, result, set(), MATH, episodes)
    by_id = {r["formationId"]: r for r in details}
    original_by_id = {r["formationId"]: r for r in opportunities}
    output = []
    for opportunity in opportunities:
        fid = opportunity["formationId"]
        row = deepcopy(by_id.get(fid, {**key, "formationId": fid, "symbol": opportunity["symbol"],
            "originalRank": opportunity["originalRank"], "scheduled": False,
            "accountDisposition": None, "positionState": "notScheduled"}))
        row.pop("shadowEverEntered", None)
        state = opportunity["confirmation"]["status"]
        row.update(formationDate=opportunity["formationDate"], decisionDate=opportunity["decisionDate"],
            signalDate=opportunity["signals"][key["policy"]]["signalDate"] if row["scheduled"] else None,
            confirmationStatus=state, gapRetained=opportunity["retained"],
            admissionStatus="included" if row["scheduled"] else "excluded" if state == "known" else state)
        output.append(row)
    for row in entries:
        event = original_by_id[row["formationId"]]
        row.pop("shadowEverEntered"); row.pop("candidateOnlyEntry")
        row.update(formationDate=event["formationDate"], decisionDate=event["decisionDate"],
            signalDate=event["signals"][key["policy"]]["signalDate"])
        require(row["formationDate"] < row["signalDate"] < row["entryDate"], "Three original clocks")
    return output, entries


def intervals_reference(key, result, summary, opportunities, schedule, old, phases):
    originals = [r["signals"]["control"] for r in opportunities if r["signals"]["control"] is not None]
    measured, partition = old.intervals_reference({**key, "candidate": "baseline"}, result, summary,
        originals, schedule, [], MATH, phases)
    partition.pop("candidate")
    for row in measured:
        for name in ("candidate", "modelStatusCounts", "modelReasonCounts"): row.pop(name)
        begin, end = row["requestedStart"], row["requestedEnd"]
        formed = [r for r in opportunities if begin <= r["formationDate"] <= end]
        accepted = sum(r["signals"][key["policy"]] is not None for r in formed)
        row.update(originalFormations=len(formed), scheduledFromFormations=accepted,
            notScheduledFromFormations=len(formed) - accepted,
            decisionSignals=sum(begin <= r["signalDate"] <= end for r in schedule),
            confirmationStatusCounts=old.labels(r["confirmation"]["status"] for r in formed),
            queuedExitReasons=old.labels(r.get("reason") for r in result.get("decisions", [])
                if begin <= r["date"] <= end and r.get("action") == "sell" and r.get("status") == "queued"),
            attributionMeaning="Continuous actual equity, including carried positions. Whole closed episodes follow final exitDate; formation counts use F and decisionSignals use F+2. Neither is an interval reset account.")
    return measured, partition


def comparisons_reference(flat):
    indexed = {tuple(r[k] for k in KEYS): r["metrics"] for r in flat}
    require(len(indexed) == len(flat) == 24, "All source/exit/policy accounts")
    output = []
    for period in PERIODS:
        for board in BOARDS:
            for exit_policy in EXITS:
                a, b = [indexed[period, board, exit_policy, p] for p in POLICIES]
                output.append(dict(period=period, board=board, exit=exit_policy, id="retainedVsControl",
                    control="control", candidate="retained", controlMetrics=a, candidateMetrics=b,
                    candidateMinusControl={k: b[k] - a[k] if finite(a[k]) and finite(b[k]) else None for k in a},
                    meaning="Separately replayed complete accounts. Retention changes later cash, capacity and entries; this difference is not a paired-trade causal effect."))
    return output


def verify(directory):
    directory = Path(directory).resolve()
    destination = directory / "readout-validation.json"
    require(not destination.exists(), "Never overwrite readout proof")
    plan, comparison, completion, proof, report = [read(directory / n) for n in (
        "plan.json", "comparison.json", "input-completion.json", "validation.json", "readout.json")]
    plan_sha = sha(directory / "plan.json")
    require(proof["passed"] is True and proof["planSha256"] == report["planSha256"] == plan_sha
        and proof["comparisonSha256"] == report["comparisonSha256"] == sha(directory / "comparison.json")
        and proof["inputCompletionSha256"] == sha(directory / "input-completion.json")
        and proof["inputValidationSha256"] == sha(directory / "input-validation.json")
        and proof["verifierSha256"] == sha(ROOT / MAIN) == plan["sourcePins"][MAIN], "Exact audited input/account chain")
    require(report["verification"]["passed"] is True and pinned(report["verification"]) == directory / "validation.json", "Report audit binding")
    for name, value in report["inputPins"].items(): pinned({"path": name, "sha256": value})
    same([r["artifactRefs"] for r in proof["accounts"]], comparison["rows"], "Exactly audited account descriptors")
    old, episodes = load_file(OLD, OLD_SHA), load_file(EPISODES, EPISODES_SHA)
    phases = {r["id"]: r for r in read(pinned(plan["atlas"]))["phases"]}
    descriptors = {tuple(r[k] for k in KEYS): r for r in comparison["rows"]}
    require(len(descriptors) == len(comparison["rows"]) == 24, "Unique account matrix")
    groups = {(g["period"], g["board"]): g for g in completion["groups"]}
    accounts, flat, formations, coverages, intervals, partitions, details, entries = ([] for _ in range(8))
    source_count = 0
    for period in PERIODS:
        for board in BOARDS:
            group = groups[period, board]
            opportunities = records(pinned(group["opportunities"]))
            dates = read(pinned(group["metadata"]))["targetDates"]
            schedules = {p: read(pinned(group["schedules"][p])) for p in POLICIES}
            source_count += len(opportunities)
            formations.append(formation_reference(dict(period=period, board=board), opportunities, group["counts"], schedules))
            for exit_policy in EXITS:
                for policy in POLICIES:
                    key = dict(period=period, board=board, exit=exit_policy, policy=policy)
                    item = descriptors[period, board, exit_policy, policy]
                    result, summary = read(pinned(item["result"])), read(pinned(item["summary"]))
                    pinned(item["checkpoint"])
                    same(read(pinned(item["schedule"])), schedules[policy], "Frozen exact schedule")
                    same([r["date"] for r in result["curve"]], dates, "Whole source timeline")
                    detail, actual = episodes_reference(key, opportunities, schedules[policy], result, old, episodes)
                    details.extend(detail); entries.extend(actual)
                    metrics = old.metric_reference(summary, len(schedules[policy]), len(opportunities))
                    metrics.update({k: summary["closedEpisodes"][v] for k, v in (
                        ("medianWinPct", "medianWinReturnPct"), ("medianLossPct", "medianLossReturnPct"),
                        ("expectancyReturnPct", "expectancyReturnPct"), ("expectancyPnlAmount", "expectancyPnlAmount"))})
                    flat.append({**key, "metrics": metrics})
                    accounts.append({**key, "artifacts": item, "metrics": metrics,
                        **{k: summary[k] for k in ("account", "closedEpisodes", "concentration", "openPositions",
                            "rights", "unresolvedExits", "reconciliation", "quality", "warnings")},
                        "sourceQualityScalarFlags": {k: v for k, v in summary["sourceQuality"].items() if not isinstance(v, (dict, list))},
                        "fullSourceQuality": {"summaryRef": item["summary"], "field": "sourceQuality"}})
                    states, admissions = Counter(r["positionState"] for r in detail), Counter(r["admissionStatus"] for r in detail)
                    coverages.append({**key, "originalOpportunities": len(opportunities), "scheduledSignals": len(schedules[policy]),
                        "notScheduled": states["notScheduled"], "notEntered": states["notEntered"], "entered": len(actual),
                        "closed": states["closed"], "openAtCutoff": states["openAtCutoff"],
                        "admissionCounts": {s: admissions[s] for s in ("included", "excluded", "unknown", "censored")},
                        "accountOpportunityReasons": old.labels(r["accountDisposition"]["reason"] for r in detail if r["scheduled"]),
                        "valuationUnreliableDays": metrics["unreliableValuationDays"], "openStaleMarks": metrics["openStaleMarks"]})
                    measured, part = intervals_reference(key, result, summary, opportunities, schedules[policy], old, phases)
                    intervals.extend(measured); partitions.append(part)
            print(json.dumps({"verifiedReadout": [period, board], "originalTop5": len(opportunities)}), flush=True)
    contrasts = comparisons_reference(flat)
    expected = dict(accounts=accounts, accountRows=flat, comparisons=contrasts, primaryComparisons=contrasts,
        formationCoverageRows=formations, coverageRows=coverages, partitionReconciliation=partitions)
    for name, kind in (("yearRows", "calendarYear"), ("halfYearRows", "calendarHalfYear"),
                      ("phaseRows", "retrospectiveNarrativePhase"), ("curveEpochRows", "descriptiveEpoch")):
        expected[name] = [r for r in intervals if r["kind"] == kind]
    for name, value in expected.items(): same(report[name], value, "Readout table " + name)
    for name, rows in (("opportunityRowsArtifact", details), ("entryRowsArtifact", entries)):
        ref = report[name]
        require(ref["count"] == len(rows) and ref["format"] == "jsonl+gzip" and ref["identity"] == [*KEYS, "formationId"], "Full sidecar descriptor")
        require(len({tuple(r[k] for k in (*KEYS, "formationId")) for r in rows}) == len(rows), "Unique sidecar identity")
        same(records(pinned(ref)), rows, name)
    expected_counts = dict(accounts=24, newAccounts=24, reusedAccounts=0, comparisons=12,
        yearRows=48, halfYearRows=96, phaseRows=192, curveEpochRows=24, formationCoverageRows=6,
        coverageRows=24, partitionReconciliation=24, sourceFormationCount=source_count,
        policyDispositions=source_count * 4, actualEntryEpisodes=len(entries), fullCurveIntervals=24, subIntervals=360)
    same(report["counts"], expected_counts, "Complete matrix counts")
    require(source_count == proof["counts"]["sourceFormationCount"] and report["accountExecutionsBySummarizer"] == 0
        and report["holdoutClaim"] is report["certified"] is report["targetAchieved"] is False, "Scope remains descriptive")
    validation = dict(passed=True, schema="full-gap-retention-readout-independent-validation-v1",
        completedAt=datetime.now(timezone.utc).isoformat(), planSha256=plan_sha,
        comparisonSha256=sha(directory / "comparison.json"), readoutSha256=sha(directory / "readout.json"),
        accountValidationSha256=sha(directory / "validation.json"), verifierSha256=sha(__file__),
        opportunityRowsArtifactSha256=report["opportunityRowsArtifact"]["sha256"],
        entryRowsArtifactSha256=report["entryRowsArtifact"]["sha256"], counts=expected_counts,
        sourcePins={MAIN: sha(ROOT / MAIN), OLD: OLD_SHA, EPISODES: EPISODES_SHA},
        checks=["Independent raw-fill episode joins", "Original F and decision T denominators",
            "All full-account scalars and open/closed amounts", "Continuous carried-equity intervals and partitions",
            "Every original Top5 disposition, including unknown/censored", "All twelve signed retained-minus-control contrasts"])
    destination.write_text(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"passed": True, "validation": reference(destination), "counts": expected_counts}


def self_test():
    old = load_file(OLD, OLD_SHA)
    episode_math = load_file(EPISODES, EPISODES_SHA)
    signals = [{"signalId": "a", "signalDate": "2024-01-03", "strategyId": "fullGapRetentionV1", "symbol": "s", "originalRank": 1}]
    opportunities = [{"formationId": "a", "symbol": "s", "formationDate": "2024-01-01", "decisionDate": "2024-01-03",
        "originalRank": 1, "confirmation": {"status": "known"}, "retained": False,
        "signals": {"control": signals[0], "retained": None}}]
    for state in ("unknown", "censored"):
        opportunities.append({"formationId": state, "symbol": state, "formationDate": "2024-01-03", "decisionDate": None,
            "originalRank": 1, "confirmation": {"status": state}, "retained": None, "signals": {"control": None, "retained": None}})
    counts = dict(opportunities=3, rawSetups=5, lockedSetups=1, rankExcludedSetups=1,
        confirmation=dict(known=1, unknown=1, censored=1), outcomes=dict(retained=0, filled=1, unknown=1, censored=1),
        signals=dict(control=1, retained=0))
    result = formation_reference({}, opportunities, counts, dict(control=signals, retained=[]))
    require(result["distinctFormationDays"] == 2 and result["opportunities"] == 3, "Independent formation populations")
    changed = deepcopy(counts); changed["rawSetups"] = 6
    try: formation_reference({}, opportunities, changed, dict(control=signals, retained=[]))
    except AssertionError: pass
    else: raise AssertionError("Broken formation conservation accepted")
    rows = [dict(period=p, board=b, exit=e, policy=m, metrics=dict(accountReturnPct=-2. if m == "control" else -5., profitFactor=None))
        for p in PERIODS for b in BOARDS for e in EXITS for m in POLICIES]
    comparisons = comparisons_reference(rows)
    require(len(comparisons) == 12 and all(r["candidateMinusControl"] == dict(accountReturnPct=-3., profitFactor=None) for r in comparisons), "Signed complete contrast matrix")
    require(old.distribution([])["mean"] is None and old.distribution([1., None])["mean"] is None, "Unknown/empty not zero-filled")
    # A frozen independent raw-fill fixture has a partial sell, final sell,
    # same-symbol reentry and a stale terminal mark. No production summary is
    # imported to construct the expected money or maturity.
    raw = episode_math.synthetic_fixture()
    buys = [r for r in raw["trades"] if r["side"] == "buy"]
    schedule = [dict(signalId=r["signalId"], strategyId="fullGapRetentionV1", symbol=r["symbol"],
        signalDate=r["signalDate"], originalRank=1) for r in buys]
    raw["research"] = {"opportunities": [{**s, "status": "filled", "reason": "filled",
        "filledQuantity": b["quantity"], "attemptDate": b["date"]} for s, b in zip(schedule, buys)]}
    events = [{"formationId": s["signalId"], "symbol": s["symbol"], "originalRank": 1,
        "formationDate": f, "decisionDate": s["signalDate"], "confirmation": {"status": "known"},
        "retained": True, "signals": {"control": deepcopy(s), "retained": deepcopy(s)}}
        for s, f in zip(schedule, ("2023-12-29", "2024-01-03"))]
    events.append(opportunities[-1])
    key = dict(period="2024-2025", board="main", exit="trail10", policy="retained")
    frozen = deepcopy((events, schedule, raw))
    detail, entries = episodes_reference(key, events, schedule, raw, old, episode_math)
    require(len(detail) == 3 and len(entries) == 2 and detail[-1]["admissionStatus"] == "censored", "Full original denominator after fill joins")
    require(entries[0]["closedPnlAmount"] == 16. and entries[1]["openUnrealizedPnlAmount"] == 48.
        and entries[1]["closedReturnPct"] is None and entries[1]["openMarkStale"] is True, "Independent partial/final/open cash")
    require(entries[0]["formationDate"] == "2023-12-29" and entries[0]["decisionDate"] == "2024-01-02"
        and entries[0]["entryDate"] == "2024-01-03", "Independent F/T/open clocks")
    same((events, schedule, raw), frozen, "Independent reference must not mutate inputs")
    for fill in raw["trades"]: fill["slippage"] = 0.
    raw["decisions"] = [{"date": "2024-01-04", "action": "sell", "status": "queued", "reason": "stopLoss"},
                        {"date": "2024-01-04", "action": "exit", "status": "queued", "reason": "wrongAction"}]
    saved = {"periods": [{"id": "overall"}], "account": {"netPnlAmount": 64., "netReturnPct": .64, "feesPaid": 6.},
             "closedEpisodes": {"count": 1}}
    phase_map = {}
    for name, begin, end, pnl, closed in (("a", "2024-01-02", "2024-01-04", 0., 0), ("b", "2024-01-05", "2024-01-07", 64., 1)):
        saved["periods"].append(dict(id=name, kind="retrospectiveNarrativePhase", requestedStart=begin, requestedEnd=end,
            enteringEquity=10000., endingEquity=10000. + pnl, netPnlAmount=pnl, returnPct=pnl / 100., maxDrawdownPct=0.,
            closedEpisodes={"count": closed}))
        phase_map[name] = {"title": name}
    intervals, partition = intervals_reference(key, raw, saved, events, schedule, old, phase_map)
    require(partition["passed"] and intervals[1]["enteringEquity"] == intervals[0]["endingEquity"]
        and intervals[0]["queuedExitReasons"] == {"stopLoss": 1}, "Independent continuous equity and sell-action evidence")
    require(intervals[0]["originalFormations"] == 2 and intervals[0]["scheduledFromFormations"] == 1
        and intervals[0]["decisionSignals"] == 1, "F cohorts are separate from T decisions")
    changed = deepcopy(saved); changed["periods"][2]["enteringEquity"] = 9999.
    try: intervals_reference(key, raw, changed, events, schedule, old, phase_map)
    except AssertionError: pass
    else: raise AssertionError("Reset or altered interval capital accepted")
    return dict(passed=True, syntheticGroups=8, filesWritten=0, realSourceReads=0, accountExecutions=0)


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--out", type=Path, default=DEFAULT); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else verify(args.out), ensure_ascii=False))
