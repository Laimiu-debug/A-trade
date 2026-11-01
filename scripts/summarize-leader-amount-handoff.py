"""Describe all handoff accounts after their independent ledger audit.

Peer formation F, leader activation A, decision T and buy are separate clocks. This file
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
DEFAULT = ROOT / "artifacts/strategy-search/leader-amount-handoff-v1"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10", "volatilityTrail")
POLICIES = ("control", "handoff")
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


def formation_coverage(key, opportunities, counts, schedules, events, members, raw):
    ids = [r["formationId"] for r in opportunities]
    require(len(ids) == len(set(ids)) and len(ids) == counts["opportunities"], "Original Top5 formation identity")
    actual = {"sourceEvents": len(events), "memberEpisodes": len(members), "firstBreakout": len(raw),
        "lockedEvents": sum(r["status"] == "locked" for r in events),
        "unlockedFormed": sum(r["status"] == "formed" for r in events),
        "unlockedNotFormed": sum(r["status"] == "notFormed" for r in events),
        **{s: sum(r["initialStatus"] == s for r in members) for s in ("initialUnknown", "initialAbove")},
        "observedMembers": sum(r["initialStatus"] == "observing" for r in members),
        **{s: sum(r["terminalStatus"] == s for r in members) for s in ("priceUnknown", "expired", "censored")},
        "duplicateSuppressed": sum(r["ownershipStatus"] == "duplicateSuppressed" for r in raw),
        "uniqueOwners": sum(r["ownershipStatus"] == "owner" for r in raw),
        "rankExcluded": sum(r["selectionStatus"] == "rankExcluded" for r in raw),
        "selectedTop5": len(opportunities), "jointKnown": sum(r["control"] is True for r in opportunities),
        "jointUnknown": sum(r["control"] is None for r in opportunities),
        "crossingTrue": sum(r["handoff"] is True for r in opportunities),
        "crossingFalse": sum(r["handoff"] is False for r in opportunities)}
    require(all(counts[k] == v for k, v in actual.items()), "Source layer counts differ from original streams")
    require(len({r["parentEventId"] for r in events}) == len(events) and
        len({r["episodeId"] for r in members}) == len(members) and
        {r["episodeId"] for r in raw} == {r["episodeId"] for r in members if r["terminalStatus"] == "firstBreakout"}, "Layer identity")
    require(ids == [r["formationId"] for r in raw if r["selectionStatus"] == "selected"], "Original Top5 retained in stream order")
    for left, right in (
        ("sourceEvents", ("lockedEvents", "unlockedFormed", "unlockedNotFormed")),
        ("memberEpisodes", ("initialUnknown", "initialAbove", "observedMembers")),
        ("observedMembers", ("priceUnknown", "firstBreakout", "expired", "censored")),
        ("firstBreakout", ("duplicateSuppressed", "uniqueOwners")),
        ("uniqueOwners", ("selectedTop5", "rankExcluded")),
        ("selectedTop5", ("jointKnown", "jointUnknown")),
        ("jointKnown", ("crossingTrue", "crossingFalse"))):
        require(counts[left] == sum(counts[k] for k in right), "Layer conservation: " + left)
    require(counts["memberEpisodes"] == 20 * counts["unlockedFormed"], "Exactly twenty fixed members per formed event")
    for policy in POLICIES:
        expected = [r["signals"][policy] for r in opportunities if r["signals"][policy] is not None]
        require(schedules[policy] == expected and len(expected) == counts["signals"][policy]
            and counts["notScheduled"][policy] == len(ids) - len(expected), "Frozen schedule identity/order")
    return {**key, **deepcopy(counts), "distinctFormationDays": len({r["formationDate"] for r in opportunities}),
        "distinctDecisionDays": len({r["decisionDate"] for r in opportunities}),
        "distinctSymbols": len({r["symbol"] for r in opportunities}),
        "conservationPassed": True,
        "meaning": "Leader events, twenty-member episodes, raw first breakouts and original member Top5 are different populations. Member expiry/censoring remains upstream; only Top5 is the account opportunity denominator."}


def diagnostic_rows(key, opportunities):
    rows = []
    for state, value in (("crossing", True), ("nonCrossing", False), ("jointUnknown", None)):
        cohort = [r for r in opportunities if r["handoff"] is value]
        row = {**key, "group": state, "opportunityCount": len(cohort)}
        for name, path in (("memberOwnActivity", ("member", "ownRelativeAmount")),
            ("anchorOwnActivity", ("anchor", "ownRelativeAmount")),
            ("pairRatioInitial", ("pairRatios", "initial")), ("pairRatioPrevious", ("pairRatios", "previous")),
            ("pairRatioCurrent", ("pairRatios", "current"))):
            values = [r["diagnostics"][path[0]][path[1]] for r in cohort]
            known = [v for v in values if finite(v)]
            row[name] = {"knownCount": len(known), "unknownCount": len(values) - len(known), "distribution": MATH.distribution(known)}
        row["meaning"] = "Known-value distributions only, with every diagnostic unknown counted. Own activity is not a gate or proof of leader-specific capital transfer."
        rows.append(row)
    return rows


def join_episodes(key, opportunities, schedule, result, summary):
    """Reuse proven buy/episode joins, then restore A/F/T for every Top5."""
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
            "peerFormationDate": opportunity["peerFormationDate"], "parentEventId": opportunity["parentEventId"],
            "anchorSymbol": opportunity["anchorSymbol"], "frozenMemberRank": opportunity["frozenMemberRank"],
            "amountStatus": opportunity["amountEvidence"]["status"], "crossing": opportunity["handoff"],
            "admissionStatus": "included" if row["scheduled"] else (
                "excluded" if opportunity["amountEvidence"]["status"] == "known" else "unknown")})
        all_detail.append(row)
    for row in entries:
        opportunity = index[row["formationId"]]
        row.pop("shadowEverEntered", None); row.pop("candidateOnlyEntry", None)
        row.update({"formationDate": opportunity["formationDate"], "decisionDate": opportunity["decisionDate"],
                    "signalDate": opportunity["signals"][key["policy"]]["signalDate"],
                    "peerFormationDate": opportunity["peerFormationDate"], "parentEventId": opportunity["parentEventId"],
                    "anchorSymbol": opportunity["anchorSymbol"], "frozenMemberRank": opportunity["frozenMemberRank"]})
        require(row["peerFormationDate"] < row["formationDate"] < row["signalDate"] < row["entryDate"], "Peer/formation/decision/entry clocks")
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
        "admissionCounts": {s: admissions[s] for s in ("included", "excluded", "unknown")},
        "accountOpportunityReasons": MATH.labels(r["accountDisposition"]["reason"] for r in detail if r["scheduled"]),
        "valuationUnreliableDays": len(summary["quality"]["valuationUnreliableDates"]),
        "openStaleMarks": sum(r["markStaleAtCutoff"] for r in summary["openPositions"]["positions"])}


def interval_rows(key, summary, result, opportunities, schedule):
    """Keep continuous equity; count activation A and decision T separately."""
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
            "amountStatusCounts": MATH.labels(r["amountEvidence"]["status"] for r in cohort),
            "attributionMeaning": "Continuous actual equity, including carried positions. Whole closed episodes follow final exitDate; formation counts use activation A and decisionSignals use first-breakout T. Neither is an interval reset account."})
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
                output.append({"period": period, "board": board, "exit": exit_policy, "id": "handoffVsControl",
                    "control": "control", "candidate": "handoff", "controlMetrics": deepcopy(control),
                    "candidateMetrics": deepcopy(candidate), "candidateMinusControl": delta,
                    "meaning": "Separately replayed complete accounts. Handoff admission changes later cash, capacity and entries; this difference is not a paired-trade causal effect."})
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
    require(plan["schema"] == "leader-amount-handoff-v1" and
        comparison["planSha256"] == completion["planSha256"] == plan_sha, "Plan/comparison/completion binding")
    require(proof["passed"] is True and input_proof["passed"] is True
        and proof["planSha256"] == input_proof["planSha256"] == plan_sha
        and proof["comparisonSha256"] == sha(directory / "comparison.json")
        and proof["inputCompletionSha256"] == input_proof["inputCompletionSha256"] == sha(directory / "input-completion.json")
        and proof["inputValidationSha256"] == sha(directory / "input-validation.json"), "Passing exact audit chain required")
    verifier = "scripts/verify-leader-amount-handoff.py"
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
    accounts, flat, coverage, formations, intervals, partitions, details, entries, diagnostics = ([] for _ in range(9))
    source_count = 0
    for period in PERIODS:
        for board in BOARDS:
            group = groups[period, board]
            opportunities = records(bound(group["opportunities"]))
            metadata = read(bound(group["metadata"]))
            counts = group["counts"]
            schedules = {m: read(bound(group["schedules"][m])) for m in POLICIES}
            source_key = {"period": period, "board": board}
            formations.append(formation_coverage(source_key, opportunities, counts, schedules,
                records(bound(group["formations"])), records(bound(group["memberEpisodes"])), records(bound(group["rawConfirmations"]))))
            diagnostics.extend(diagnostic_rows(source_key, opportunities))
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
    require(source_count == proof["counts"]["sourceOpportunityCount"] and len(details) == source_count * 4,
        "Dynamic original Top5 and four-policy-exit dispositions")
    contrasts = compare_accounts(flat)

    def write_lines(name, rows):
        path = destinations[name]
        with gzip.open(path, "xt", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        return {**ref(path), "count": len(rows), "format": "jsonl+gzip", "identity": [*KEYS, "formationId"]}

    report = {"schema": "leader-amount-handoff-readout-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "comparisonSha256": sha(directory / "comparison.json"),
        "verification": {"passed": True, **ref(directory / "validation.json")}, "inputPins": pins,
        "accounts": accounts, "accountRows": flat, "comparisons": contrasts, "primaryComparisons": contrasts,
        **tables, "formationCoverageRows": formations, "coverageRows": coverage, "diagnosticRows": diagnostics, "partitionReconciliation": partitions,
        "opportunityRowsArtifact": write_lines("opportunity-dispositions.jsonl.gz", details),
        "entryRowsArtifact": write_lines("entry-episodes.jsonl.gz", entries),
        "counts": {"accounts": 24, "newAccounts": 24, "reusedAccounts": 0, "comparisons": 12,
            **expected_lengths, "formationCoverageRows": 6, "coverageRows": 24, "partitionReconciliation": 24,
            "sourceEvents": sum(r["sourceEvents"] for r in formations),
            "memberEpisodes": sum(r["memberEpisodes"] for r in formations),
            "rawBreakouts": sum(r["firstBreakout"] for r in formations),
            "sourceOpportunityCount": source_count, "diagnosticRows": 18,
            "policyDispositions": len(details), "actualEntryEpisodes": len(entries),
            "fullCurveIntervals": 24, "subIntervals": len(intervals)},
        "method": {"account": "Each policy is a separate complete shared-capital account. Net return includes closed and marked open positions, fees and capital constraints.",
            "opportunities": "F freezes peers, A activates the inherited leader event, and T is the member's first breakout in A+1..A+5. Original daily member Top5 precedes both amount masks. Amount-unknown Top5 remains in the denominator; upstream censored/expired members remain in source coverage. Actual entries occur after T through the unchanged engine.",
            "closedDistribution": "Win/loss means and medians, payoff ratio, profit factor and expectancy describe closed episodes, not the complete account; empty or unidentified ratios remain null.",
            "intervals": "Continuous previous-close equity and carried positions. Before/after 2024-09-24 have unequal lengths and are not annualized or restarted accounts. A formation counts, T decision signals and actual fills are separate.",
            "comparison": "Handoff minus same-clock common-known control for all six sources and both exits; no winner selection or profit hurdle.",
            "ownActivity": "M3(T)/(3*mean amount T-20..T-1), reported for original Top5 with separate known/unknown denominators. Unknown diagnostics never change admission. These distributions describe opportunities, not account returns.",
            "atlas": "All fixed retrospective narrative phases are descriptive evidence, never signal inputs."},
        "limitations": ["2022-2025 is repeated development history, not a holdout; 2026 is not used.",
            "Stock/day and cross-account outcomes are dependent; no causal identification or stable success claim.",
            "Raw prices, historical status availability, stale marks and unresolved positions retain the inherited source/account flags.",
            "A close-triggered stop does not cap realized loss; later tradability and gaps matter.",
            "Individual traded-value crossing may reflect own volume expansion or common news; no pseudo-anchor control identifies leader-specific influence, main-force, retail, or quantitative investors.",
            "The common relative-strength benchmark is CSI 300 (sh000300), not a board-specific or beta-neutral benchmark; boards are not pure technology industries."],
        "newAccounts": 24, "referenceAccountsReused": 0, "accountExecutionsBySummarizer": 0,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    destinations["readout.json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"passed": True, "readout": ref(destinations["readout.json"]), "counts": report["counts"]}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_leader_amount_handoff_readout.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    require(result.wasSuccessful(), "Synthetic readout tests failed")
    return {"passed": True, "tests": result.testsRun, "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(self_test() if arguments.self_test else build(arguments.out), ensure_ascii=False))
