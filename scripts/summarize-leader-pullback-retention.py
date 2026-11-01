"""Describe all pullback-retention accounts after their independent ledger audit.

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
DEFAULT = ROOT / "artifacts/strategy-search/leader-pullback-retention-v1"
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


STATUSES = ("firstDownUnknown", "noDownThroughT", "sameDayDown", "memberPathUnknown", "commonKnown")


def known_distribution(values):
    values = list(values)
    known = [value for value in values if finite(value)]
    return {"knownCount": len(known), "unknownCount": len(values)-len(known), "distribution": MATH.distribution(known)}


def sequence_evidence(opportunity):
    return {name: deepcopy(opportunity[name]) for name in ("firstDown", "retention", "flags", "diagnostics", "parentAmountStatus")}


def sequence_coverage(key, formations, opportunities, counts, schedules):
    require(len({r["parentEventId"] for r in formations}) == len(formations), "All inherited formation identities")
    formation_index = {r["parentEventId"]: r for r in formations}
    require(len({r["formationId"] for r in opportunities}) == len(opportunities)
        == len({r["parentOpportunityId"] for r in opportunities}), "All original Top5 unique")
    lifecycle = {name: sum(r["status"] == name for r in formations) for name in ("formed", "locked", "notFormed")}
    require(sum(lifecycle.values()) == len(formations), "All inherited lifecycle statuses")
    common = {name: sum(r["commonStatus"] == name for r in opportunities) for name in STATUSES}
    require(sum(common.values()) == len(opportunities), "All five exclusive sequence statuses")
    for row in opportunities:
        require(row["parentEventId"] in formation_index and formation_index[row["parentEventId"]]["status"] == "formed", "Top5 lineage")
        down, retention = row["firstDown"], row["retention"]
        require(down["formationDate"] == row["formationDate"] and down["decisionDate"] == row["decisionDate"], "Opportunity-specific A/T prefix")
        if down["status"] == "priorDown":
            require(row["formationDate"] < down["firstDownDate"] < row["decisionDate"], "P strictly between A and T")
            status = "commonKnown" if retention["status"] == "known" else "memberPathUnknown"
            require(retention["status"] in ("known", "unknown"), "Prior down needs member-path evidence")
        else:
            require(down["status"] in STATUSES[:3], "First-down terminal status")
            status = down["status"]
            require(retention["status"] == "notApplicable", "No prior P has no retention test")
            if status == "sameDayDown": require(down["firstDownDate"] == row["decisionDate"], "Same-day P is not prior P")
        require(row["commonStatus"] == status, "Exclusive sequence status")
        if status == "commonKnown":
            require(type(retention["retained"]) is bool and not retention["unknownReasons"], "Complete member support")
            require(retention["retained"] == (not retention["breachDates"]), "Known retention is no strict breach")
        else:
            require(retention["retained"] is None, "No outcome for incomplete/not applicable path")
        require(retention["firstBreachDate"] == (retention["breachDates"][0] if retention["breachDates"] else None), "First breach preserved")
        require(row["flags"]["memberBreachDates"] == retention["breachDates"], "Independent breach evidence")
        for policy in POLICIES:
            allowed = status == "commonKnown" and (policy == "control" or retention["retained"])
            require((row["signals"][policy] is not None) == allowed, "Same price support, retention only difference")
    required = {"parentFormations": len(formations), "formed": lifecycle["formed"],
        "inheritedOther": lifecycle["locked"] + lifecycle["notFormed"], "originalTop5": len(opportunities), "opportunities": len(opportunities),
        "commonStatusCounts": common,
        "retainedKnown": sum(r["commonStatus"] == "commonKnown" and r["retention"]["retained"] is True for r in opportunities),
        "lostKnown": sum(r["commonStatus"] == "commonKnown" and r["retention"]["retained"] is False for r in opportunities),
        "signals": {p: len(schedules[p]) for p in POLICIES}, "notScheduled": {p: len(opportunities)-len(schedules[p]) for p in POLICIES}}
    require(all(counts[name] == value for name, value in required.items()), "Sequence counts match full original population")
    require(common["commonKnown"] == required["retainedKnown"] + required["lostKnown"] == len(schedules["control"])
        and required["retainedKnown"] == len(schedules["retained"]), "Common and retained population conservation")
    for policy in POLICIES:
        require(schedules[policy] == [r["signals"][policy] for r in opportunities if r["signals"][policy] is not None], "Original-order schedule, no refill")
    return {**key, **{name: required[name] for name in ("parentFormations", "formed", "inheritedOther", "commonStatusCounts", "retainedKnown", "lostKnown", "signals", "notScheduled")},
        "originalOpportunities": len(opportunities), "parentFormationStatusCounts": lifecycle,
        "firstDownStatusCounts": {name: sum(r["firstDown"]["status"] == name for r in opportunities)
            for name in ("priorDown", "sameDayDown", "noDownThroughT", "firstDownUnknown")},
        "parentAmountStatusCounts": MATH.labels(r["parentAmountStatus"] for r in opportunities),
        "diagnostics": {name: known_distribution(r["diagnostics"][name] for r in opportunities)
            for name in ("formationToDownSlots", "downToDecisionSlots", "retentionObservationCount")},
        "independentEvidenceCounts": {
            "firstDownUnknown": sum(bool(r["flags"]["firstDownUnknownReasons"]) for r in opportunities),
            "memberUnknown": sum(bool(r["flags"]["memberUnknownReasons"]) for r in opportunities),
            "knownBreach": sum(bool(r["retention"]["breachDates"]) for r in opportunities),
            "memberUnknownWithKnownBreach": sum(bool(r["flags"]["memberUnknownReasons"]) and bool(r["retention"]["breachDates"]) for r in opportunities)},
        "sourceCounts": deepcopy(counts), "conservationPassed": True,
        "meaning": "All parent formations and original Top5 remain. A is leader activation, P is the first proven adjacent decline within each opportunity prefix, T is its inherited breakout. Diagnostics retain unknown and varying observation lengths; none is an extra gate. Parent A amount eligibility remains inherited, while T amount status is descriptive only."}


def admission_status(opportunity, scheduled):
    if scheduled: return "included"
    return "excluded" if opportunity["commonStatus"] in ("commonKnown", "noDownThroughT", "sameDayDown") else "unknown"


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
            "parentOpportunityId": opportunity["parentOpportunityId"],
            "commonStatus": opportunity["commonStatus"], "sequenceEvidence": sequence_evidence(opportunity),
            "admissionStatus": admission_status(opportunity, row["scheduled"])})
        all_detail.append(row)
    for row in entries:
        opportunity = index[row["formationId"]]
        row.pop("shadowEverEntered", None); row.pop("candidateOnlyEntry", None)
        row.update({"formationDate": opportunity["formationDate"], "decisionDate": opportunity["decisionDate"],
                    "signalDate": opportunity["signals"][key["policy"]]["signalDate"],
                    "peerFormationDate": opportunity["peerFormationDate"], "parentEventId": opportunity["parentEventId"],
                    "anchorSymbol": opportunity["anchorSymbol"], "frozenMemberRank": opportunity["frozenMemberRank"],
                    "parentOpportunityId": opportunity["parentOpportunityId"], "sequenceEvidence": sequence_evidence(opportunity)})
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
            "commonStatusCounts": MATH.labels(r["commonStatus"] for r in cohort),
            "attributionMeaning": "Continuous actual equity, including carried positions. Whole closed episodes follow final exitDate; formation counts use activation A and decisionSignals use first-breakout T. Neither is an interval reset account."})
    return output


CONTRASTS = (("retainedVsControl", "control", "retained", "primary"),)

def metric_delta(control, candidate):
    require(control.keys() == candidate.keys(), "Metric columns differ")
    return {name: candidate[name] - value if finite(value) and finite(candidate[name]) else None for name, value in control.items()}


def compare_accounts(rows):
    index = {identity(r): r for r in rows}
    expected = {(p,b,e,m) for p in PERIODS for b in BOARDS for e in EXITS for m in POLICIES}
    require(len(index) == len(rows) == 24 and set(index) == expected, "All twenty-four new accounts required")
    output = []
    for p in PERIODS:
        for b in BOARDS:
            for e in EXITS:
                for name, control, candidate, role in CONTRASTS:
                    a, z = index[p,b,e,control]["metrics"], index[p,b,e,candidate]["metrics"]
                    output.append(dict(period=p, board=b, exit=e, id=name, role=role, control=control, candidate=candidate,
                        controlMetrics=deepcopy(a), candidateMetrics=deepcopy(z), candidateMinusControl=metric_delta(a,z),
                        meaning="Complete independent accounts on shared original slots and common support; differences include cash, capacity and later entries, not paired-trade causal effects."))
    return output


def population_context(key, old_summary, old_signals, original_count, new_metrics, old_ref):
    old_metrics = account_metrics(old_summary, old_signals, original_count)
    return {**key, "id": "commonControlVsOldControl", "oldControlSummary": deepcopy(old_ref),
        "oldMetrics": old_metrics, "newMetrics": deepcopy(new_metrics), "newMinusOld": metric_delta(old_metrics, new_metrics),
        "reusedAccount": False, "meaning": "Old43 control is descriptive population context only. New control is newly replayed on proven prior anchor declines and complete member paths. Parent A initial amount selection remains inherited; parent T amount masks do not. This is population/sequence selection, not a causal retention increment or reused account."}


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
    require(plan["schema"] == "leader-pullback-retention-v1" and
        comparison["planSha256"] == completion["planSha256"] == plan_sha, "Plan/comparison/completion binding")
    require(proof["passed"] is True and input_proof["passed"] is True
        and proof["planSha256"] == input_proof["planSha256"] == plan_sha
        and proof["comparisonSha256"] == sha(directory / "comparison.json")
        and proof["inputCompletionSha256"] == input_proof["inputCompletionSha256"] == sha(directory / "input-completion.json")
        and proof["inputValidationSha256"] == sha(directory / "input-validation.json"), "Passing exact audit chain required")
    verifier = "scripts/verify-leader-pullback-retention.py"
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
    accounts, flat, coverage, sequences, intervals, partitions, details, entries, population = ([] for _ in range(9))
    old_summary_refs = {(r["period"], r["board"], r["exit"]): r["summary"] for r in plan["parentControlSummaries"]}
    require(len(old_summary_refs) == len(plan["parentControlSummaries"]) == 12, "All twelve old controls are context only")
    parent_comparison = read(bound(plan["parentComparison"]))
    require(old_summary_refs == {(r["period"], r["board"], r["exit"]): r["summary"] for r in parent_comparison["rows"] if r["policy"] == "control"}, "Exact old-control summary provenance")
    source_count = 0
    for period in PERIODS:
        for board in BOARDS:
            group = groups[period, board]
            opportunities = records(bound(group["opportunities"]))
            metadata = read(bound(group["metadata"]))
            counts = group["counts"]
            schedules = {m: read(bound(group["schedules"][m])) for m in POLICIES}
            source_key = {"period": period, "board": board}
            sequences.append(sequence_coverage(source_key, records(bound(group["formations"])), opportunities, counts, schedules))
            parent_opportunities = records(bound(group["parentOpportunities"]))
            require([r["parentOpportunityId"] for r in opportunities] == [r["formationId"] for r in parent_opportunities], "All original Top5 in original order")
            old_signal_count = sum(r["signals"]["control"] is not None for r in parent_opportunities)
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
                    if policy == "control":
                        old_ref = old_summary_refs[period, board, exit_policy]
                        population.append(population_context(source_key | {"exit": exit_policy}, read(bound(old_ref)),
                            old_signal_count, len(opportunities), scalar, old_ref))
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

    report = {"schema": "leader-pullback-retention-readout-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "comparisonSha256": sha(directory / "comparison.json"),
        "verification": {"passed": True, **ref(directory / "validation.json")}, "inputPins": pins,
        "accounts": accounts, "accountRows": flat, "comparisons": contrasts,
        "primaryComparisons": deepcopy(contrasts),
        **tables, "sequenceCoverageRows": sequences, "coverageRows": coverage,
        "populationContextRows": population, "partitionReconciliation": partitions,
        "opportunityRowsArtifact": write_lines("opportunity-dispositions.jsonl.gz", details),
        "entryRowsArtifact": write_lines("entry-episodes.jsonl.gz", entries),
        "counts": {"accounts": 24, "newAccounts": 24, "reusedAccounts": 0, "comparisons": 12,
            "primaryComparisons": 12,
            **expected_lengths, "sequenceCoverageRows": 6, "coverageRows": 24,
            "populationContextRows": 12, "partitionReconciliation": 24,
            "parentFormations": sum(r["parentFormations"] for r in sequences),
            "formed": sum(r["formed"] for r in sequences),
            "sourceOpportunityCount": source_count,
            "policyDispositions": len(details), "actualEntryEpisodes": len(entries),
            "fullCurveIntervals": 24, "subIntervals": len(intervals)},
        "method": {"account": "Each policy is a separate complete shared-capital account. Net return includes closed and marked open positions, fees and capital constraints.",
            "opportunities": "Inherit every parent original Top5 and A/F/T clock. For each original T independently scan anchor adjacent closes from A+1 through T, stopping at the first unknown or proven decline P. Only P<T with a complete member P-1..T-1 path is common support. Later anchor gaps after proven P are irrelevant. No new Top5 or later T is selected.",
            "closedDistribution": "Win/loss means and medians, payoff ratio, profit factor and expectancy describe closed episodes, not the complete account; empty or unidentified ratios remain null.",
            "intervals": "Continuous previous-close equity and carried positions. Before/after 2024-09-24 have unequal lengths and are not annualized or restarted accounts. A formation counts, T decision signals and actual fills are separate.",
            "comparison": "Retained minus common control across all six sources and both exits. Old43 control is population context only; no winner selection, account reuse or profit hurdle.",
            "sequence": "Five exclusive statuses cover all original Top5. Known breaches survive simultaneous missing evidence but do not turn an incomplete member path into common support. Observation lengths and lags are diagnostics, never additional gates.",
            "atlas": "All fixed retrospective narrative phases are descriptive evidence, never signal inputs."},
        "limitations": ["2022-2025 is repeated development history, not a holdout; 2026 is not used.",
            "Stock/day and cross-account outcomes are dependent; no causal identification or stable success claim.",
            "Raw prices, historical status availability, stale marks and unresolved positions retain the inherited source/account flags.",
            "A close-triggered stop does not cap realized loss; later tradability and gaps matter.",
            "A first adjacent decline is not sustained weakness or loss of leadership. Retention is of the member own fixed level, not proven leadership transfer or investor identity.",
            "Initial A amount eligibility is inherited from43, so this is not a pure-price full-cohort test. New sequence support changes old43 control population; simultaneous members, varying observation lengths and overlapping positions remain dependent.",
            "The common relative-strength benchmark is CSI 300 (sh000300), not a board-specific or beta-neutral benchmark; boards are not pure technology industries."],
        "newAccounts": 24, "referenceAccountsReused": 0, "accountExecutionsBySummarizer": 0,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    destinations["readout.json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"passed": True, "readout": ref(destinations["readout.json"]), "counts": report["counts"]}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_leader_pullback_retention_readout.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    require(result.wasSuccessful(), "Synthetic readout tests failed")
    return {"passed": True, "tests": result.testsRun, "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(self_test() if arguments.self_test else build(arguments.out), ensure_ascii=False))
