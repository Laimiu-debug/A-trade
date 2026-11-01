"""Describe all fixed full-gap accounts after their independent ledger audit.

Formation F, decision F+2, buy and final exit are separate clocks. This file
never selects a board, changes signals or executes an account.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / "artifacts/strategy-search/full-gap-retention-v1"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10", "volatilityTrail")
POLICIES = ("control", "retained")
KEYS = ("period", "board", "exit", "policy")
SPEC = importlib.util.spec_from_file_location("gap_summary_frozen_math", ROOT / "scripts/summarize-online-feedback-entry.py")
ACCOUNT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCOUNT)
MATH = ACCOUNT.MATH
require, finite, close, total = ACCOUNT.require, ACCOUNT.finite, ACCOUNT.close, ACCOUNT.total
sha, digest, read = ACCOUNT.sha, ACCOUNT.digest, ACCOUNT.read
DEPENDENCIES = {str(Path(path).relative_to(ROOT)).replace("\\", "/"): sha(path)
                for path in (ACCOUNT.__file__, ACCOUNT.HELPER)}


def records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def identity(row):
    return tuple(row[key] for key in KEYS)


def account_metrics(summary, scheduled, original_count):
    metrics = ACCOUNT.metrics(summary, scheduled, original_count)
    closed = summary["closedEpisodes"]
    metrics.update({name: closed[source] for name, source in (
        ("medianWinPct", "medianWinReturnPct"), ("medianLossPct", "medianLossReturnPct"),
        ("expectancyReturnPct", "expectancyReturnPct"), ("expectancyPnlAmount", "expectancyPnlAmount"))})
    return metrics


def formation_coverage(key, opportunities, counts, schedules):
    ids = [r["formationId"] for r in opportunities]
    require(len(ids) == len(set(ids)) and len(ids) == counts["opportunities"], "Original Top5 formation identity")
    status = Counter(r["confirmation"]["status"] for r in opportunities)
    outcomes = Counter(("retained" if r["retained"] else "filled")
        if r["confirmation"]["status"] == "known" else r["confirmation"]["status"] for r in opportunities)
    require({s: status[s] for s in ("known", "unknown", "censored")} == counts["confirmation"], "Confirmation denominator")
    require({s: outcomes[s] for s in ("retained", "filled", "unknown", "censored")} == counts["outcomes"], "Gap outcome denominator")
    for policy in POLICIES:
        expected = [r["signals"][policy] for r in opportunities if r["signals"][policy] is not None]
        require(schedules[policy] == expected and len(expected) == counts["signals"][policy], "Frozen schedule identity/order")
    require(counts["rawSetups"] == counts["lockedSetups"] + counts["rankExcludedSetups"] + len(ids), "Raw setups, lock and Top5 conservation")
    return {**key, **deepcopy(counts), "distinctFormationDays": len({r["formationDate"] for r in opportunities}),
        "distinctSymbols": len({r["symbol"] for r in opportunities}),
        "meaning": "Raw setups include locked and rank-excluded events; only original Top5 formations are account opportunities. Unknown and censored confirmation excludes both policies."}


def join_episodes(key, opportunities, schedule, result, summary):
    """Reuse proven buy/episode joins, then restore F for every original event."""
    original = [r["signals"]["control"] for r in opportunities if r["signals"]["control"] is not None]
    expected = [r["signals"][key["policy"]] for r in opportunities if r["signals"][key["policy"]] is not None]
    require(schedule == expected, "Policy schedule differs from original frozen events")
    detail, entries = ACCOUNT.attach_episodes(key, original, schedule, result, summary, set())
    index = {r["formationId"]: r for r in opportunities}
    require(len(index) == len(opportunities), "Duplicate original formation")
    observed = {r["formationId"]: r for r in detail}
    all_detail = []
    for opportunity in opportunities:
        sid = opportunity["formationId"]
        row = observed.get(sid, {**key, "formationId": sid, "symbol": opportunity["symbol"],
            "originalRank": opportunity["originalRank"], "scheduled": False,
            "accountDisposition": None, "positionState": "notScheduled"})
        row = deepcopy(row)
        row.pop("shadowEverEntered", None)
        row.update({"formationDate": opportunity["formationDate"], "decisionDate": opportunity["decisionDate"],
            "signalDate": opportunity["signals"][key["policy"]]["signalDate"] if row["scheduled"] else None,
            "confirmationStatus": opportunity["confirmation"]["status"], "gapRetained": opportunity["retained"],
            "admissionStatus": "included" if row["scheduled"] else (
                "excluded" if opportunity["confirmation"]["status"] == "known" else opportunity["confirmation"]["status"])})
        all_detail.append(row)
    for row in entries:
        opportunity = index[row["formationId"]]
        row.pop("shadowEverEntered", None); row.pop("candidateOnlyEntry", None)
        row.update({"formationDate": opportunity["formationDate"], "decisionDate": opportunity["decisionDate"],
                    "signalDate": opportunity["signals"][key["policy"]]["signalDate"]})
        require(row["formationDate"] < row["signalDate"] < row["entryDate"], "Formation/decision/entry clocks")
    require(len(all_detail) == len(opportunities), "Original formation coverage")
    return all_detail, entries


def account_coverage(key, opportunities, schedule, detail, entries, summary):
    states = Counter(r["positionState"] for r in detail)
    admissions = Counter(r["admissionStatus"] for r in detail)
    require(set(states) <= {"notScheduled", "notEntered", "closed", "openAtCutoff"}, "Unexpected maturity")
    require(sum(states.values()) == len(opportunities) and len(schedule) == len(entries) + states["notEntered"]
        and len(entries) == states["closed"] + states["openAtCutoff"], "Account opportunity conservation")
    return {**key, "originalOpportunities": len(opportunities), "scheduledSignals": len(schedule),
        "notScheduled": states["notScheduled"], "notEntered": states["notEntered"], "entered": len(entries),
        "closed": states["closed"], "openAtCutoff": states["openAtCutoff"],
        "admissionCounts": {s: admissions[s] for s in ("included", "excluded", "unknown", "censored")},
        "accountOpportunityReasons": MATH.labels(r["accountDisposition"]["reason"] for r in detail if r["scheduled"]),
        "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]),
        "openStaleMarks": sum(r["markStaleAtCutoff"] for r in summary["openPositions"]["positions"])}


def interval_rows(key, summary, result, opportunities, schedule):
    """Keep continuous equity; count formation F and decision T separately."""
    output = []
    for saved in summary["periods"]:
        if saved["id"] == "overall":
            continue
        start, end = saved["requestedStart"], saved["requestedEnd"]
        fills = [r for r in result["trades"] if start <= r["date"] <= end]
        cohort = [r for r in opportunities if start <= r["formationDate"] <= end]
        admitted = [r for r in cohort if r["signals"][key["policy"]] is not None]
        output.append({**key, **deepcopy(saved), "closedEpisodes": ACCOUNT.compact_closed(saved["closedEpisodes"]),
            "feesPaid": total(r["fee"] for r in fills), "slippageAlreadyInFillPrices": total(r["slippage"] for r in fills),
            "buyFillCount": sum(r["side"] == "buy" for r in fills), "sellFillCount": sum(r["side"] == "sell" for r in fills),
            "sellFillReasons": MATH.labels(r.get("reason") for r in fills if r["side"] == "sell"),
            "queuedExitReasons": MATH.labels(r.get("reason") for r in result.get("decisions", [])
                if start <= r["date"] <= end and r.get("action") == "sell" and r.get("status") == "queued"),
            "originalFormations": len(cohort), "scheduledFromFormations": len(admitted),
            "notScheduledFromFormations": len(cohort) - len(admitted),
            "decisionSignals": sum(start <= r["signalDate"] <= end for r in schedule),
            "confirmationStatusCounts": MATH.labels(r["confirmation"]["status"] for r in cohort),
            "attributionMeaning": "Continuous actual equity, including carried positions. Whole closed episodes follow final exitDate; formation counts use F and decisionSignals use F+2. Neither is an interval reset account."})
    return output


def compare_accounts(rows):
    index = {identity(r): r for r in rows}
    expected = {(p, b, e, m) for p in PERIODS for b in BOARDS for e in EXITS for m in POLICIES}
    require(len(index) == len(rows) == 24 and set(index) == expected, "All 24 accounts required")
    output = []
    for period in PERIODS:
        for board in BOARDS:
            for exit_policy in EXITS:
                control, candidate = [index[(period, board, exit_policy, m)]["metrics"] for m in POLICIES]
                require(control.keys() == candidate.keys(), "Metric columns differ")
                delta = {name: candidate[name] - value if finite(value) and finite(candidate[name]) else None
                         for name, value in control.items()}
                output.append({"period": period, "board": board, "exit": exit_policy, "id": "retainedVsControl",
                    "control": "control", "candidate": "retained", "controlMetrics": deepcopy(control),
                    "candidateMetrics": deepcopy(candidate), "candidateMinusControl": delta,
                    "meaning": "Separately replayed complete accounts. Retention changes later cash, capacity and entries; this difference is not a paired-trade causal effect."})
    return output


def build(directory=DEFAULT):
    directory = Path(directory).resolve()
    destinations = {name: directory / name for name in (
        "readout.json", "opportunity-dispositions.jsonl.gz", "entry-episodes.jsonl.gz")}
    require(all(not p.exists() for p in destinations.values()), "Readout outputs already exist; never overwrite")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        require(path.is_relative_to(ROOT), "Input outside repository")
        name, value = path.relative_to(ROOT).as_posix(), sha(path)
        require(pins.setdefault(name, value) == value, "Input changed during readout")
        return path

    def bound(item):
        path = pin(ROOT / item["path"])
        require(sha(path) == item["sha256"], "Artifact hash mismatch: " + item["path"])
        return path

    def ref(path):
        return {"path": Path(path).resolve().relative_to(ROOT).as_posix(), "sha256": sha(path)}

    plan, comparison, completion, input_proof, proof = [read(pin(directory / name)) for name in
        ("plan.json", "comparison.json", "input-completion.json", "input-validation.json", "validation.json")]
    plan_sha = sha(directory / "plan.json")
    require(plan["schema"] == "full-gap-retention-v1" and
        comparison["planSha256"] == completion["planSha256"] == plan_sha, "Plan/comparison/completion binding")
    require(proof["passed"] is True and input_proof["passed"] is True
        and proof["planSha256"] == input_proof["planSha256"] == plan_sha
        and proof["comparisonSha256"] == sha(directory / "comparison.json")
        and proof["inputCompletionSha256"] == input_proof["inputCompletionSha256"] == sha(directory / "input-completion.json")
        and proof["inputValidationSha256"] == sha(directory / "input-validation.json"), "Passing exact audit chain required")
    verifier = "scripts/verify-full-gap-retention.py"
    require(proof["verifierSha256"] == plan["sourcePins"][verifier] == sha(ROOT / verifier), "Frozen main auditor changed")
    require(proof["counts"]["accounts"] == 24 and proof["counts"]["newAccounts"] == 24
        and proof["counts"]["reusedAccounts"] == 0, "Account execution scope")
    for name, value in plan["sourcePins"].items():
        bound({"path": name, "sha256": value})
    pin(__file__); pin(ACCOUNT.__file__); pin(ACCOUNT.HELPER)
    phases = {r["id"]: r for r in read(bound(plan["atlas"]))["phases"]}
    descriptor = {identity(r): r for r in comparison["rows"]}
    expected = {(p, b, e, m) for p in PERIODS for b in BOARDS for e in EXITS for m in POLICIES}
    require(len(comparison["rows"]) == len(descriptor) == 24 and set(descriptor) == expected, "Complete unique account matrix")
    groups = {(r["period"], r["board"]): r for r in completion["groups"]}
    require(len(groups) == len(completion["groups"]) == 6, "Complete source matrix")
    accounts, flat, coverage, formations, intervals, partitions, details, entries = ([] for _ in range(8))
    source_count = 0
    for period in PERIODS:
        for board in BOARDS:
            group = groups[period, board]
            opportunities = records(bound(group["opportunities"]))
            metadata = read(bound(group["metadata"]))
            counts = group["counts"]
            schedules = {m: read(bound(group["schedules"][m])) for m in POLICIES}
            formations.append(formation_coverage({"period": period, "board": board}, opportunities, counts, schedules))
            source_count += len(opportunities)
            for exit_policy in EXITS:
                for policy in POLICIES:
                    key = {"period": period, "board": board, "exit": exit_policy, "policy": policy}
                    item = descriptor[identity(key)]
                    require(item["schedule"] == group["schedules"][policy], "Descriptor schedule differs from audited input")
                    result, summary = read(bound(item["result"])), read(bound(item["summary"]))
                    bound(item["checkpoint"])
                    require(result["status"] == "completed" and summary["inputResultSha256"] == digest(result)
                        and summary["policyId"] == policy and not summary["quality"]["integrityIssues"], "Account summary identity/integrity")
                    require([r["date"] for r in result["curve"]] == metadata["targetDates"], "Full original source timeline")
                    detail, actual = join_episodes(key, opportunities, schedules[policy], result, summary)
                    details.extend(detail); entries.extend(actual)
                    scalar = account_metrics(summary, len(schedules[policy]), len(opportunities))
                    flat.append({**key, "metrics": scalar})
                    accounts.append({**key, "artifacts": deepcopy(item), "metrics": scalar,
                        **{n: deepcopy(summary[n]) for n in ("account", "closedEpisodes", "concentration", "openPositions",
                            "rights", "unresolvedExits", "reconciliation", "quality", "warnings")},
                        "sourceQualityScalarFlags": {n: v for n, v in summary["sourceQuality"].items() if not isinstance(v, (dict, list))},
                        "fullSourceQuality": {"summaryRef": deepcopy(item["summary"]), "field": "sourceQuality"}})
                    coverage.append(account_coverage(key, opportunities, schedules[policy], detail, actual, summary))
                    measured = interval_rows(key, summary, result, opportunities, schedules[policy])
                    for row in measured:
                        if row["kind"] == "retrospectiveNarrativePhase":
                            phase = phases[row["id"]]
                            require((row["requestedStart"], row["requestedEnd"]) == (phase["startDate"], phase["endDate"]), "Atlas dates changed")
                            row["phaseTitle"] = phase["title"]
                    intervals.extend(measured)
                    partitions.append({**key, "passed": True, "checks": ACCOUNT.partition_intervals(measured, summary, result["curve"])})
            print(json.dumps({"summarized": [period, board], "originalTop5": len(opportunities)}, ensure_ascii=False), flush=True)
    tables = {name: [r for r in intervals if r["kind"] == kind] for name, kind in (
        ("yearRows", "calendarYear"), ("halfYearRows", "calendarHalfYear"),
        ("phaseRows", "retrospectiveNarrativePhase"), ("curveEpochRows", "descriptiveEpoch"))}
    expected_lengths = {"yearRows": 48, "halfYearRows": 96, "phaseRows": 192, "curveEpochRows": 24}
    require(all(len(tables[name]) == count for name, count in expected_lengths.items()), "Full predefined time matrices")
    require(source_count == proof["counts"]["sourceFormationCount"] and len(details) == source_count * 4,
        "Dynamic original Top5 and four-policy-exit dispositions")
    contrasts = compare_accounts(flat)

    def write_lines(name, rows):
        path = destinations[name]
        with gzip.open(path, "xt", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        return {**ref(path), "count": len(rows), "format": "jsonl+gzip", "identity": [*KEYS, "formationId"]}

    report = {"schema": "full-gap-retention-readout-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "comparisonSha256": sha(directory / "comparison.json"),
        "verification": {"passed": True, **ref(directory / "validation.json")}, "inputPins": pins,
        "accounts": accounts, "accountRows": flat, "comparisons": contrasts, "primaryComparisons": contrasts,
        **tables, "formationCoverageRows": formations, "coverageRows": coverage, "partitionReconciliation": partitions,
        "opportunityRowsArtifact": write_lines("opportunity-dispositions.jsonl.gz", details),
        "entryRowsArtifact": write_lines("entry-episodes.jsonl.gz", entries),
        "counts": {"accounts": 24, "newAccounts": 24, "reusedAccounts": 0, "comparisons": 12,
            **expected_lengths, "formationCoverageRows": 6, "coverageRows": 24, "partitionReconciliation": 24,
            "sourceFormationCount": source_count, "policyDispositions": len(details), "actualEntryEpisodes": len(entries),
            "fullCurveIntervals": 24, "subIntervals": len(intervals)},
        "method": {"account": "Each policy is a separate complete shared-capital account. Net return includes closed and marked open positions, fees and capital constraints.",
            "opportunities": "F freezes the original Top5. Both policies decide at T=F+2 using the same complete confirmation evidence; unknown/censored original formations remain in the denominator. Actual entries occur later through the unchanged engine.",
            "closedDistribution": "Win/loss means and medians, payoff ratio, profit factor and expectancy describe closed episodes, not the complete account; empty or unidentified ratios remain null.",
            "intervals": "Continuous previous-close equity and carried positions. Before/after 2024-09-24 have unequal lengths and are not annualized or restarted accounts. F formation counts, T decision signals and actual fills are separate.",
            "comparison": "Retained minus same-clock control for all six sources and both exits; no winner selection or profit hurdle.",
            "atlas": "All fixed retrospective narrative phases are descriptive evidence, never signal inputs."},
        "limitations": ["2022-2025 is repeated development history, not a holdout; 2026 is not used.",
            "Stock/day and cross-account outcomes are dependent; no causal identification or stable success claim.",
            "Raw prices, historical status availability, stale marks and unresolved positions retain the inherited source/account flags.",
            "A close-triggered stop does not cap realized loss; later tradability and gaps matter.",
            "A complete upward gap and its retention do not identify main-force, retail, or quantitative investors.",
            "The common relative-strength benchmark is CSI 300 (sh000300), not a board-specific or beta-neutral benchmark; boards are not pure technology industries."],
        "newAccounts": 24, "referenceAccountsReused": 0, "accountExecutionsBySummarizer": 0,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    destinations["readout.json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"passed": True, "readout": ref(destinations["readout.json"]), "counts": report["counts"]}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_full_gap_retention_readout.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    require(result.wasSuccessful(), "Synthetic readout tests failed")
    return {"passed": True, "tests": result.testsRun, "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(self_test() if arguments.self_test else build(arguments.out), ensure_ascii=False))
