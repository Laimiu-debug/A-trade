"""Summarize audited leader-lag follower accounts without creating signals.

The four input layers and original member Top5 are retained independently;
all trade cohorts use activation A, while F remains the relationship cutoff.
"""
from __future__ import annotations
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/leader-lag-followers-v1"
HELPER = ROOT / "scripts/summarize-volume-anchor-acceptance.py"
ATLAS = ROOT / "artifacts/strategy-search/market-phase-atlas-v1/atlas.json"
ATLAS_SHA = "ddf55f2741dca3793923e5da782690aad500510848077f43492fff41af1a44d8"
PROTOCOL = ROOT / "docs/signal-leader-lag-followers-v1.json"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODES = ("control", "direction")
CONTRASTS = (("primary", "control", "direction"),)

spec = importlib.util.spec_from_file_location("leader_lag_readout_base", HELPER)
PRIOR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PRIOR)
BOARD, BASE = PRIOR.BOARD, PRIOR.BASE
require, finite, close = PRIOR.require, PRIOR.finite, PRIOR.close
sha, location, read, records = PRIOR.sha, PRIOR.location, PRIOR.read, PRIOR.records
total, labels, distribution = PRIOR.total, PRIOR.labels, PRIOR.distribution
EPOCHS = PRIOR.EPOCHS
partition_check, metric_values = PRIOR.partition_check, PRIOR.metric_values

def inside(day, start=None, end=None):
    return day is not None and (start is None or start <= day <= end)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def stream_records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def sum_counts(rows):
    result = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, dict):
                result[key] = sum_counts([result.get(key, {}), value])
            else:
                require(type(value) is int and value >= 0, "Count must be a nonnegative integer")
                result[key] = result.get(key, 0) + value
    return result


def edge_key(row):
    return (row["frozenMemberRank"], row["originalLeaderRank"], row["anchorSymbol"], row["symbol"])


def check_layers(daily, events, edges, original, counts, targets):
    require([r["formationDate"] for r in daily] == targets, "Every activation day required")
    by_event = {r["eventId"]: r for r in events}
    by_edge = {r["formationId"]: r for r in edges}
    by_opp = {r["formationId"]: r for r in original}
    edges_by_event = {}
    for edge in edges:
        require(edge["eventId"] in by_event, "Edge references absent leader event")
        edges_by_event.setdefault(edge["eventId"], []).append(edge)
    require(len(by_event) == len(events) and len(by_edge) == len(edges) and len(by_opp) == len(original), "Duplicate layer identity")
    require(len({(r["formationDate"], r["symbol"]) for r in original}) == len(original), "Duplicate same-day member opportunity")
    require([x for r in daily for x in r["leaderEventIds"]] == list(by_event)
        and [x for r in daily for x in r["memberEdgeIds"]] == list(by_edge)
        and [x for r in daily for x in r["selectedFormationIds"]] == list(by_opp), "Daily/layer ordering differs")
    for event in events:
        group = event["groupSummary"]
        members = group["members"]
        require(group["kind"] in ("formed", "incomplete", "unknown") and group["requiredCount"] == 20
            and group["selectedCount"] == len(members) <= 20
            and len({r["symbol"] for r in members}) == len(members)
            and [r["frozenMemberRank"] for r in members] == list(range(1, len(members)+1)), "Frozen group members differ")
        require((group["kind"] == "formed") == (len(members) == 20), "Partial group is actionable")
        actual = edges_by_event.get(event["eventId"], [])
        require(len(actual) == (20 if group["kind"] == "formed" else 0), "Group-to-edge conservation differs")
        members_by_symbol = {r["symbol"]: r for r in members}
        for edge in actual:
            member = members_by_symbol[edge["symbol"]]
            require(edge["anchorSymbol"] != edge["symbol"] and all(edge[k] == event[k] for k in
                ("eventId", "formationDate", "peerFormationDate", "anchorSymbol", "originalLeaderRank", "groupSha256"))
                and edge["frozenMemberRank"] == member["frozenMemberRank"], "Edge belongs to another frozen group")
    for day in daily:
        date, c = day["formationDate"], day["counts"]
        es, qs, selected = ([by_event[x] for x in day["leaderEventIds"]],
            [by_edge[x] for x in day["memberEdgeIds"]], [by_opp[x] for x in day["selectedFormationIds"]])
        require(all(r["formationDate"] == date and r["peerFormationDate"] == day["peerFormationDate"] for r in es+qs), "Event F/A differs")
        require(qs == sorted(qs, key=edge_key), "Peer-first interleave changed")
        owners = {}
        for edge in qs:
            first = edge["symbol"] not in owners
            if first:
                owners[edge["symbol"]] = edge
            owner = owners[edge["symbol"]]
            rank = len(owners) if first else owner["originalRank"]
            require(edge["ownerFormationId"] == owner["formationId"] and edge["ownerOriginalRank"] == rank,
                "Owner changed after relationship mask")
            require(edge["ownershipStatus"] == ("owner" if first else "duplicateSuppressed")
                and edge["originalRank"] == (rank if first else None), "Ownership/rank differs")
            is_selected = first and rank <= 5
            require(edge["selectedTop5"] is is_selected and edge["disposition"] ==
                ("selectedTop5" if is_selected else "dailyRankExcluded" if first else "duplicateSuppressed"), "Top5 selection differs")
            if not is_selected:
                require(edge["relationshipStatus"] == "notEvaluated", "Unselected relation mislabeled unknown/false")
        require([r["formationId"] for r in list(owners.values())[:5]] == day["selectedFormationIds"], "Original memberTop5 changed")
        states = Counter()
        for opportunity in selected:
            edge = by_edge[opportunity["formationId"]]
            require(all(opportunity[k] == v for k, v in edge.items()), "Selected opportunity lost edge evidence")
            state = relationship_state(opportunity)
            states[state] += 1
            require(opportunity["relationship"]["status"] == ("unknown" if state == "unknown" else "known")
                and opportunity["relationship"]["directionPassed"] == (None if state == "unknown" else state == "passed"),
                "Relation and policy admission differ")
            for mode in MODES:
                expected_signal(opportunity, mode)
        require(sum(day["activationCounts"].values()) == counts["sampleCount"]
            and day["activationCounts"]["candidate"] == len(day["leaderCandidates"]) == c["currentCandidateCount"]
            and sum(day["formationCounts"].values()) == c["currentCandidateCount"]
            and day["formationCounts"]["passed"] == len(day["leaderRanking"]) == c["temporalCandidateCount"]
            and len(es) == c["leaderEventCount"] == min(5, c["temporalCandidateCount"]), "Leader population denominators differ")
        group_kinds = {k: sum(r["groupSummary"]["kind"] == k for r in es) for k in ("formed", "incomplete", "unknown")}
        require(c["leaderGroupKinds"] == group_kinds and sum(group_kinds.values()) == len(es), "Group states differ")
        require(c["rawEdges"] == len(qs) == group_kinds["formed"]*20
            and c["uniqueOwners"] == len(owners) and c["duplicateSuppressed"] == len(qs)-len(owners)
            and c["selectedTop5"] == len(selected) == min(5, len(owners))
            and c["dailyRankExcluded"] == len(owners)-len(selected), "Event/edge/owner/Top5 conservation differs")
        require(c["knownRelations"] == states["passed"]+states["failed"]
            and c["unknownRelations"] == states["unknown"]
            and c["directionPass"] == states["passed"] and c["directionFail"] == states["failed"], "Relation population differs")
        for mode in MODES:
            decisions = Counter(r["decisions"][mode]["status"] for r in selected)
            require(c["policyCounts"][mode] == {k: decisions[k] for k in ("included", "excluded", "unknown")}
                and c["signals"][mode] == decisions["included"]
                and c["notScheduled"][mode] == len(selected)-decisions["included"], "Policy denominator differs")
    aggregate = sum_counts(r["counts"] for r in daily)
    require(all(counts[k] == v for k, v in aggregate.items()) and counts["opportunityCount"] == len(original), "Daily totals differ")
    return {"passed": True, "counts": aggregate, "leaderEventCount": len(events), "rawEdgeCount": len(edges),
        "uniqueOwnershipCount": aggregate.get("uniqueOwners", 0), "formationTop5": len(original), "selectedMemberTop5": len(original),
        "relationshipStateCounts": {k: sum(relationship_state(r) == k for r in original) for k in ("passed", "failed", "unknown")},
        "leaderGroupKinds": aggregate.get("leaderGroupKinds", {}),
        "uniqueFormationDays": len({r["formationDate"] for r in original}), "uniqueSymbols": len({r["symbol"] for r in original}),
        "ownerEventSelectionCounts": dict(sorted(Counter(r["eventId"] for r in original).items())),
        "activationLayerRows": [{"formationDate": r["formationDate"], "counts": deepcopy(r["counts"])} for r in daily],
        "meaning": "Four separate denominators; activation A is trade cohort, F only relationship cutoff. Incomplete groups preserved, no synthetic member edges; overlapping edges/events are not independent samples."}


def layer_coverage(inputs, metadata, counts, original):
    daily = records(inputs["daily-evidence.jsonl.gz"])
    events, edges = records(inputs["leader-events.jsonl.gz"]), records(inputs["member-edges.jsonl.gz"])
    result = check_layers(daily, events, edges, original, counts, metadata["targetDates"])
    expected = {r["eventId"]: r for r in events}
    seen, candidate_count, group_unknowns = set(), 0, Counter()
    for row in stream_records(inputs["group-evidence.jsonl.gz"]):
        event = expected.get(row["eventId"])
        require(event is not None and row["eventId"] not in seen, "Unmatched/duplicate full group evidence")
        seen.add(row["eventId"])
        group = row["group"]
        require(digest(group) == row["groupSha256"] == event["groupSha256"]
            and {k:v for k,v in group.items() if k != "candidateEvaluations"} == event["groupSummary"], "Full group/summary binding differs")
        evaluations = group["candidateEvaluations"]
        require(digest(evaluations) == group["candidateEvaluationsSha256"]
            and len(evaluations) == group["counts"]["candidateCount"] == counts["sampleCount"]-1,
            "Full same-board peer candidates missing")
        candidate_count += len(evaluations)
        group_unknowns.update(r["reason"] for r in evaluations if r["status"] == "unknown")
    require(seen == set(expected), "Partial/unknown leader group evidence omitted")
    ranks = records(inputs["rank-evidence.jsonl.gz"])
    require(len(ranks) == counts["rankDayCount"] and [r["date"] for r in ranks] ==
        [d for d in metadata["calendar"] if d >= metadata["basis"]["rankClockStart"]], "Warmup rank day coverage differs")
    result.update(layerCounts=deepcopy(result["counts"]), counts=deepcopy(counts), basis=deepcopy(metadata["basis"]),
        rankDayCount=len(ranks), groupCandidateEvaluationCount=candidate_count,
        groupCandidateUnknownReasonCounts=dict(sorted(group_unknowns.items())), fullGroupRecords=len(seen),
        evidenceArtifacts={name: {"path": location(path), "sha256": sha(path)} for name,path in inputs.items()
            if name.endswith(".jsonl.gz")})
    return result


def relationship_state(row):
    control, direction = (row["decisions"][m]["status"] for m in MODES)
    require((control, direction) in (("included", "included"), ("included", "excluded"), ("unknown", "unknown")),
        "Control/direction common relationship eligibility differs")
    return "unknown" if control == "unknown" else "passed" if direction == "included" else "failed"


def expected_signal(row, mode):
    relationship_state(row)
    signal = row["signals"][mode]
    require((signal is not None) == (row["decisions"][mode]["status"] == "included"), "Relationship admission differs")
    if signal is not None:
        require(set(signal) == {"signalId", "strategyId", "symbol", "signalDate", "originalRank"}
            and signal["signalId"] == row["formationId"] and signal["symbol"] == row["symbol"]
            and signal["originalRank"] == row["originalRank"] and signal["signalDate"] == row["formationDate"],
            "Original member signal identity/rank/activation changed")
    return signal


def ordered_schedule(original, mode):
    signals = [s for r in original if (s := expected_signal(r, mode)) is not None]
    return sorted(signals, key=lambda s: (s["signalDate"], s["originalRank"], s["symbol"], s["signalId"]))


def formation_counts(original, schedule, account_opportunities, mode, start=None, end=None):
    require(len({r["formationId"] for r in original}) == len(original), "Duplicate member opportunity")
    require(list(schedule) == ordered_schedule(original, mode), "Schedule changed")
    scheduled = {r["signalId"] for r in schedule}
    executed = {r["signalId"]: r for r in account_opportunities}
    require(len(executed) == len(account_opportunities) and set(executed) == scheduled, "Account/schedule coverage differs")
    cohort = [r for r in original if inside(r["formationDate"], start, end)]
    observed = [executed[r["formationId"]] for r in cohort if r["formationId"] in scheduled]
    entered = sum(finite(r.get("filledQuantity")) and r["filledQuantity"] > 0 for r in observed)
    statuses = Counter("scheduled" if r["formationId"] in scheduled else r["decisions"][mode]["status"] for r in cohort)
    return {"formationTop5": len(cohort), "selectedMemberTop5": len(cohort),
        "relationshipStates": labels(relationship_state(r) for r in cohort),
        "policyDispositionCounts": {k: statuses[k] for k in ("scheduled", "excluded", "unknown")},
        "selectedSignalsFromFormation": len(observed), "notScheduledFromFormation": len(cohort) - len(observed),
        "enteredFromFormation": entered, "notEnteredFromFormation": len(observed) - entered,
        "uniqueFormationDays": len({r["formationDate"] for r in cohort}), "uniqueSymbols": len({r["symbol"] for r in cohort}),
        "uniqueOwnerEvents": len({r["eventId"] for r in cohort}),
        "terminalStatusCounts": labels(r.get("status", "missing") for r in observed),
        "terminalReasonCounts": labels(r.get("reason", "missing") for r in observed),
        "meaning": "Original member Top5 cohort uses activation A; F is historical relation cutoff. Terminal dispositions are measured at account cutoff, not interval end."}


def interval(result, start, end, initial, original=(), schedule=(), mode="control"):
    view = dict(result)
    view["research"] = {"opportunities": []}
    measured = BOARD.interval(view, start, end, initial)
    opportunities = result.get("research", {}).get("opportunities", [])
    measured["opportunitiesByFormationDate"] = formation_counts(original, schedule, opportunities, mode, start, end)
    measured["opportunitiesByDecisionDate"] = BASE.decision_counts(schedule, opportunities, start, end)
    return measured


def attach_dispositions(original, schedule, result, summary, mode):
    observed = BASE.execution_dates(result)
    require(set(observed) == {s["signalId"] for s in schedule}, "Account dispositions differ from schedule")
    raw = {r["signalId"]: r for r in result["research"]["opportunities"]}
    opened = {(r["record"]["symbol"], r["record"]["entryDate"]): r for r in summary["openPositions"]["positions"]}
    require(len(opened) == summary["openPositions"]["count"], "Duplicate open episode identity")
    for row in original:
        sid, signal = row["formationId"], expected_signal(row, mode)
        detail = observed.get(sid)
        require((detail is not None) == (signal is not None), "Opportunity/schedule/account binding differs")
        if detail is None:
            disposition = {"signalId": sid, "signalDate": None, "entryDate": None, "exitDate": None,
                "positionState": "notScheduled", "status": "notScheduled", "reason": row["decisions"][mode]["reason"],
                "admissionStatus": row["decisions"][mode]["status"], "filledQuantity": 0, "attemptDate": None,
                "closedReturnPct": None, "closedPnlAmount": None, "accountOpportunity": None, "openPositionAtCutoff": None}
        else:
            open_row = opened.get((row["symbol"], detail["entryDate"]))
            require((open_row is not None) == (detail["positionState"] == "openAtCutoff"), "Open episode linkage differs")
            disposition = {**deepcopy(detail), "admissionStatus": "included", "accountOpportunity": deepcopy(raw[sid]),
                "openPositionAtCutoff": deepcopy(open_row)}
        row.setdefault("accountDispositions", {})[mode] = {"formationId": sid, "eventId": row["eventId"],
            "formationDate": row["formationDate"], "peerFormationDate": row["peerFormationDate"],
            "plannedNextEntryDate": row.get("plannedNextEntryDate"), "nextEntryWithinPeriod": row.get("nextEntryWithinPeriod"),
            "decisionDate": signal["signalDate"] if signal else None, **disposition}

def compare_accounts(accounts):
    indexed = {(r["period"], r["board"], r["policy"]): r for r in accounts}
    require(len(indexed) == len(accounts) == 12 and set(indexed) == {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES},
            "Exactly12 unique account policies required")
    rows = []
    for period in PERIODS:
        for board in BOARDS:
            metrics = {mode: metric_values(indexed[(period, board, mode)]) for mode in MODES}
            keys = set(metrics["control"])
            require(all(set(v) == keys for v in metrics.values()), "Contrast metric fields differ")
            for contrast, control, candidate in CONTRASTS:
                rows.append({"period": period, "board": board, "id": contrast, "control": control, "candidate": candidate,
                    "controlMetrics": metrics[control], "candidateMetrics": metrics[candidate],
                    "candidateMinusControl": {k: metrics[candidate][k] - metrics[control][k]
                        if finite(metrics[candidate][k]) and finite(metrics[control][k]) else None for k in sorted(keys)},
                    "meaning": "direction minus control within common original Top5 and jointly evaluable relationships; historical full-account policy difference, not causal influence."})
    return rows

def summarize_account(period, board, mode, config, original, schedule, metadata, result, summary, row, phases):
    require((row["period"], row["board"], row["id"]) == (period, board, mode), "Comparison identity differs")
    require(result["status"] == "completed" and summary["policyId"] == mode
            and not summary["quality"]["integrityIssues"], "Incomplete/incorrect account")
    require(schedule == ordered_schedule(original, mode), "Frozen policy schedule differs")
    initial, dates = config["initialCapital"], metadata["targetDates"]
    require(initial == 500000 and dates == sorted(set(dates)) and [r["date"] for r in result["curve"]] == dates,
            "Capital or complete calendar differs")
    attach_dispositions(original, schedule, result, summary, mode)
    overall = interval(result, config["startDate"], config["endDate"], initial, original, schedule, mode)
    saved = summary["account"]
    for key, source in (("returnPct", "netReturnPct"), ("maxDrawdownPct", "maxDrawdownPct"),
        ("netPnlAmount", "netPnlAmount"), ("endingEquity", "finalEquity"), ("feesPaid", "feesPaid"),
        ("buyFillCount", "buyFillCount"), ("sellFillCount", "sellFillCount")):
        close(overall[key], saved[source], f"{period}/{board}/{mode}/{key}")
    closed = overall["closedEpisodesByExitDate"]
    close(closed["netPnlAmount"], summary["closedEpisodes"]["netPnlAmount"], "Closed net PnL")
    close(closed["returnPct"]["mean"], summary["closedEpisodes"]["meanReturnPct"], "Closed mean")
    for key, actual in (("accountReturnPct", overall["returnPct"]), ("maxDrawdownPct", overall["maxDrawdownPct"]),
        ("meanExposurePct", overall["exposurePct"]["mean"]), ("closed", closed["count"]),
        ("signals", len(schedule)), ("openPositions", summary["openPositions"]["count"])):
        close(row[key], actual, "Comparison " + key)
    require(overall["opportunitiesByDecisionDate"]["entered"] == overall["buyFillCount"]
            == closed["count"] + summary["openPositions"]["count"], "Entered/closed/open counts differ")
    opened = deepcopy(summary["openPositions"])
    opened["unrealizedPnlAmount"] = total(r["unrealizedPnlAmount"] for r in opened["positions"])
    opened["realizedPnlWhileOpen"] = total(r["realizedPnlWhileOpen"] for r in opened["positions"])
    bridge = summary["reconciliation"]
    close(opened["unrealizedPnlAmount"], bridge["openPositionUnrealizedPnl"], "Open unrealized PnL")
    close(opened["realizedPnlWhileOpen"], bridge["openPositionRealizedPnl"], "Open realized PnL")
    close(total([closed["netPnlAmount"], opened["unrealizedPnlAmount"], opened["realizedPnlWhileOpen"],
        bridge["rightsAttributionGapAmount"]]), saved["netPnlAmount"], "Closed/open/rights bridge")
    account = {"period": period, "board": board, "id": mode, "policy": mode, "label": summary["policyLabel"],
        "execution": "new-independent-account", "comparisonRow": deepcopy(row), "account": deepcopy(saved),
        "curveAttribution": overall, "closedEpisodes": deepcopy(summary["closedEpisodes"]), "closedDistribution": closed,
        "openPositionsAtCutoff": opened, "reconciliation": deepcopy(bridge),
        **{key: deepcopy(summary[key]) for key in ("unresolvedExits", "concentration", "quality", "rights", "sourceQuality")},
        "sourceQualityScalarFlags": {k: v for k, v in summary["sourceQuality"].items() if not isinstance(v, (list, dict))},
        "warningCount": len(summary.get("warnings", [])),
        "opportunityStatusCounts": deepcopy(summary["opportunityDispositions"]["opportunityCounts"]),
        "configuredUniverseValues": deepcopy(config["universe"]),
        "configuredUniverseMeaning": "Compatibility only: fixed-signal execution does not enforce minListingDays120/minAmount20."}
    coverage = {"period": period, "board": board, "id": mode, "policy": mode,
        **deepcopy(overall["opportunitiesByFormationDate"]), **deepcopy(overall["opportunitiesByDecisionDate"]),
        "buyFillCount": overall["buyFillCount"], "sellFillCount": overall["sellFillCount"],
        "closedCount": closed["count"], "openCountAtCutoff": opened["count"],
        "unresolvedExitCountAtCutoff": summary["unresolvedExits"]["count"], "curveSessions": len(dates),
        "valuationUnreliableDays": len(overall["valuationUnreliableDates"]),
        "qualityScalarFlags": deepcopy(account["sourceQualityScalarFlags"])}
    years, halves, phase_rows, epochs = [], [], [], []
    for saved_period in summary["periods"]:
        if saved_period["kind"] not in ("calendarYear", "calendarHalfYear"):
            continue
        measured = interval(result, saved_period["requestedStart"], saved_period["requestedEnd"], initial, original, schedule, mode)
        for key in ("returnPct", "maxDrawdownPct", "netPnlAmount", "enteringEquity", "endingEquity"):
            close(measured[key], saved_period[key], "Year/halfyear " + key)
        item = BASE.compact_period(saved_period, period, mode)
        item.update(measured)
        item["board"] = board
        (years if saved_period["kind"] == "calendarYear" else halves).append(item)
    relevant = [p for p in phases if p["startDate"] <= config["endDate"] and p["endDate"] >= config["startDate"]]
    require(all(sum(p["startDate"] <= d <= p["endDate"] for p in relevant) == 1 for d in dates), "Atlas coverage differs")
    for phase in relevant:
        measured = interval(result, phase["startDate"], phase["endDate"], initial, original, schedule, mode)
        measured.update(period=period, board=board, policy=mode, phaseId=phase["id"], phaseTitle=phase["title"],
            sentimentSummary=phase["sentimentSummary"], dominantThemes=deepcopy(phase["dominantThemes"]),
            representatives=deepcopy(phase["representatives"]), labelAvailability=phase["labelAvailability"],
            attributionMeaning="Hindsight calendar interval with carried positions/equity; never a signal input.")
        phase_rows.append(measured)
    checks = [partition_check(part, overall, len(original), len(schedule), label)
              for part, label in ((years, "years"), (halves, "halfyears"), (phase_rows, "phases"))]
    if period == "2024-2025":
        for epoch, start, end in EPOCHS:
            measured = interval(result, start, end, initial, original, schedule, mode)
            measured.update(period=period, board=board, policy=mode, phaseId=epoch,
                attributionMeaning="User2024-09-24 boundary; continuous capital and holdings, no reset.")
            epochs.append(measured)
        checks.append(partition_check(epochs, overall, len(original), len(schedule), "userEventEpochs"))
    return {"account": account, "coverage": coverage, "yearRows": years, "halfYearRows": halves,
        "phaseRows": phase_rows, "curveEpochRows": epochs,
        "reconciliation": {"period": period, "board": board, "policy": mode, "passed": True, "checks": checks}}



def build(directory, validation_path=None):
    directory = Path(directory).resolve()
    validation_path = Path(validation_path).resolve() if validation_path else directory / "validation.json"
    paths = {name: directory / name for name in ("plan.json", "comparison.json", "input-completion.json", "input-validation.json")}
    destination, sidecar = directory / "readout.json", directory / "opportunity-dispositions.jsonl.gz"
    require(not destination.exists() and not sidecar.exists(), "Readout/sidecar already exists; no overwrite")
    require(all(p.is_file() for p in (*paths.values(), validation_path, ATLAS, PROTOCOL)), "Validated frozen evidence required")
    require(sha(ATLAS) == ATLAS_SHA, "Original atlas changed")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        value = sha(path)
        require(pins.setdefault(location(path), value) == value, "Input changed: " + location(path))
        return path

    def bound(reference):
        require(isinstance(reference, dict) and {"path", "sha256"} <= set(reference), "Invalid artifact descriptor")
        path = Path(reference["path"])
        path = pin(path if path.is_absolute() else ROOT / path)
        require(pins[location(path)] == reference["sha256"], "Artifact binding differs: " + reference["path"])
        return path

    for path in (*paths.values(), validation_path, ATLAS, PROTOCOL, HELPER, PRIOR.HELPER, BOARD.HELPER, Path(__file__)):
        pin(path)
    plan, comparison = read(paths["plan.json"]), read(paths["comparison.json"])
    completion, validation, input_validation = read(paths["input-completion.json"]), read(validation_path), read(paths["input-validation.json"])
    plan_sha = pins[location(paths["plan.json"])]
    BOARD.validation_binding(validation, plan_sha, pins[location(paths["comparison.json"])])
    require(validation["inputValidationSha256"] == pins[location(paths["input-validation.json"])]
            and validation["inputCompletionSha256"] == pins[location(paths["input-completion.json"])], "Account input audit chain differs")
    require(input_validation["passed"] is True and input_validation["planSha256"] == plan_sha
            and input_validation["inputCompletionSha256"] == pins[location(paths["input-completion.json"])], "Input proof differs")
    require(plan["schema"] == "leader-lag-followers-v1" and plan["modes"] == list(MODES)
            and plan["boards"] == list(BOARDS) and set(plan["periods"]) == set(PERIODS), "Policy/board/period scope differs")
    require(bound(plan["protocol"]) == PROTOCOL and plan["rules"] == read(PROTOCOL), "Protocol binding differs")
    require(comparison["planSha256"] == completion["planSha256"] == plan_sha
            and completion["allSchedulesFrozenBeforeAccounts"] is True, "Schedules not frozen for this plan")
    for key, expected in (("newAccounts", 12), ("newSetupMechanisms", 1), ("newEntryPolicies", 2),
                          ("newUniverseVariants", 0), ("referenceAccountsReused", 0)):
        require(plan[key] == comparison[key] == expected, "Trial accounting differs: " + key)
    require(plan["parameterSearch"] is False and plan["profitHurdles"] is None and plan["holdoutClaim"] is False,
            "Readout cannot select parameters, profit hurdles or claim holdout")
    for mapping in (plan["sourcePins"], plan["inputPins"]):
        for name, expected in mapping.items():
            bound({"path": name, "sha256": expected})
    rows = deepcopy(comparison["rows"])
    indexed = {(r["period"], r["board"], r["id"]): r for r in rows}
    require(len(rows) == len(indexed) == 12 and set(indexed) == {(p, b, m) for p in PERIODS for b in BOARDS for m in MODES}
            and all(r["policy"] == r["id"] for r in rows), "Exactly12 distinct account rows required")
    phases = read(ATLAS)["phases"]
    require(len(phases) == len({p["id"] for p in phases}) == 16
            and all(p["labelAvailability"] == "retrospectiveResearchOnly" for p in phases), "All original16 hindsight phases required")
    accounts, years, halves, phase_rows, epochs, coverage, formation_coverage, opportunities, partitions = ([] for _ in range(9))
    source_bindings, all_counts = {}, {}
    for period in PERIODS:
        source_bindings[period], all_counts[period] = {}, {}
        for board in BOARDS:
            folder, binding = directory / period / board, plan["sourceData"][period][board]
            receipt_path = bound(completion["outputs"][period][board])
            require(receipt_path == folder / "input-completion.json", "Board receipt path differs")
            receipt = read(receipt_path)
            require(receipt["planSha256"] == plan_sha and receipt["period"] == period and receipt["board"] == board,
                    "Board receipt identity differs")
            require(all(receipt[k] == v for k, v in binding.items()), "Frozen source binding differs")
            for key in ("accountData", "sourceManifest", "sourceValidation"):
                bound(binding[key])
            parent_validation = read(bound(binding["sourceValidation"]))
            require(parent_validation["passed"] is True
                    and validation["parentSourceValidationSha256"] == binding["sourceValidation"]["sha256"]
                    and parent_validation["datasetPins"][binding["accountData"]["path"]] == binding["accountData"]["sha256"],
                    "Previously audited source differs")
            manifest = read(bound(binding["sourceManifest"]))
            require(manifest["passed"] is True and manifest["period"] == period, "Parent source manifest differs")
            cohort = deepcopy(manifest["boards"][board])
            require(cohort["includedCount"] == binding["members"]
                    and cohort["accountData"]["datasetDigest"] == binding["accountDatasetDigest"], "Fixed source cohort differs")
            cohort["accountData"] = {**cohort["accountData"], **deepcopy(binding["accountData"])}
            cohort_records = [r for r in manifest["records"] if r["board"] == board]
            cohort["selectionStatusCounts"] = labels(r["selectionStatus"] for r in cohort_records)
            cohort["selectionReasonCounts"] = labels(r["reason"] for r in cohort_records)
            inputs = {name: bound(reference) for name, reference in receipt["files"].items()}
            require(all(path.parent == folder for path in inputs.values()), "Input escaped registered board folder")
            original = records(inputs["opportunities.jsonl.gz"])
            metadata, counts = read(inputs["signal-metadata.json"]), read(inputs["input-counts.json"])
            require(counts == receipt["counts"] == metadata["counts"] and metadata["modes"] == list(MODES), "Counts/modes differ")
            require(counts["sampleCount"] == binding["members"] and counts["opportunityCount"] == len(original)
                and counts["targetDayCount"] == len(metadata["targetDates"]), "Formation denominators differ")
            layers = layer_coverage(inputs, metadata, counts, original)
            formation_coverage.append({"period": period, "board": board, "sourceCohort": cohort, **layers})
            calendar = metadata["calendar"]
            require(calendar == sorted(set(calendar)), "Source calendar differs")
            day_index = {d: i for i, d in enumerate(calendar)}
            for row in original:
                day, formation = row["formationDate"], row["peerFormationDate"]
                require(day in metadata["targetDates"] and day_index[day] >= 10 and calendar[day_index[day]-10] == formation,
                    "Exact ten-slot F/A clock differs")
                next_index = day_index[day] + 1
                next_date = calendar[next_index] if next_index < len(calendar) else None
                row.update(period=period, board=board, plannedNextEntryDate=next_date,
                    nextEntryWithinPeriod=next_date in metadata["targetDates"])
            for mode in MODES:
                schedule = read(inputs[mode + ".signals.json"])
                require(schedule == ordered_schedule(original, mode) and len(schedule) == counts["signals"][mode], "Frozen schedule differs")
                artifacts = {name: bound(reference) for name, reference in comparison["artifacts"][period][board][mode].items()}
                require(all(path.parent == folder for path in artifacts.values()), "Account artifact escaped board folder")
                result, summary = read(artifacts[mode + ".result.json.gz"]), read(artifacts[mode + ".summary.json"])
                item = summarize_account(period, board, mode, plan["periods"][period]["config"], original, schedule,
                    metadata, result, summary, indexed[(period, board, mode)], phases)
                for key in ("yearRows", "halfYearRows", "phaseRows", "curveEpochRows"):
                    for segment in item[key]:
                        segment["selectionByActivationDate"] = sum_counts(r["counts"] for r in layers["activationLayerRows"]
                            if inside(r["formationDate"], segment["requestedStart"], segment["requestedEnd"]))
                item["account"]["artifactRefs"] = deepcopy(comparison["artifacts"][period][board][mode])
                item["account"]["sourceBinding"] = deepcopy(binding)
                accounts.append(item["account"]); coverage.append(item["coverage"])
                years.extend(item["yearRows"]); halves.extend(item["halfYearRows"])
                phase_rows.extend(item["phaseRows"]); epochs.extend(item["curveEpochRows"])
                partitions.append(item["reconciliation"])
                del result, summary, item
            require(all(set(r["accountDispositions"]) == set(MODES) for r in original), "Every member opportunity needs both dispositions")
            opportunities.extend(original)
            source_bindings[period][board] = {**deepcopy(binding), "inputCompletion": deepcopy(completion["outputs"][period][board]),
                "dailyEvidence": deepcopy(receipt["files"]["daily-evidence.jsonl.gz"]),
                "originalOpportunities": deepcopy(receipt["files"]["opportunities.jsonl.gz"]),
                "evidenceArtifacts": deepcopy(receipt["files"])}
            all_counts[period][board] = deepcopy(counts)
            del original, manifest
    require(tuple(map(len, (accounts, years, halves, phase_rows, epochs, coverage, formation_coverage))) == (12, 24, 48, 96, 12, 12, 6),
            "Complete12-policy and6-source readout matrix required")
    require(len(opportunities) == sum(r["formationTop5"] for r in formation_coverage)
            == len({(r["period"], r["board"], r["formationId"]) for r in opportunities}), "Original anchors lost/duplicated")
    comparisons = compare_accounts(accounts)
    readout = {"schema": "leader-lag-followers-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "inputPins": pins, "sourceBindings": source_bindings,
        "marketContext": deepcopy(plan.get("marketContext")),
        "verification": {"passed": True, "path": location(validation_path), "sha256": sha(validation_path),
            "meaning": "Independent input/account audit plus complete arithmetic/coverage reconciliation; not strategy certification."},
        "method": {
            "setup": "New common persistent-strength F/A setup within each frozen board cohort. A activates a leader; F=A-10 is historical relationship cutoff, not member trade date.",
            "fourLayers": "All original leader Top5 events, full group evidence including partial groups, all20 member edges per formed group, interleaved unique owners and original daily member Top5 remain separately counted.",
            "selection": "Order frozenMemberRank,originalLeaderRank,anchorSymbol,memberSymbol; deduplicate same-day member before memberTop5 and shared relation completeness. No owner switch, rank-six backfill or account-dependent selection.",
            "relationship": "Fixed synchronous60 members; equal-length59 synchronous/forward/reverse correlations before or at F. Direction requires forward>max(0,synchronous59,reverse), not identified causal influence.",
            "primaryContrast": "direction minus control on the same original memberTop5 and common known-relation population; full historical policy difference includes exposure and capital-path changes.",
            "unknown": "Partial groups stay at the event/evidence layer and create no queue edges. Selected Top5 relation unknown occupies its original slot and schedules neither policy. Unselected owner relation is notEvaluated, not unknown.",
            "formationAndExecution": "Opportunity cohorts use activation A. Preserve peerFormationDate F, owner eventId, planned next source session, actual attempt/entry/exit; notScheduled differs from scheduled-but-notEntered. End-period signals remain unexecuted, never zero-return trades.",
            "accounts": "12 new independent two-year500000 accounts, six unchanged board sources and two policies. No account rerun, annualization, four-year chain or best-board promotion.",
            "intervals": "All years, halves,16 retrospective atlas stages and2024-09-24 boundary use previous close equity and carried holdings; initial500000 only when no prior curve point. PnL adds; returns compound; drawdown includes entering equity.",
            "closedAndOpen": "Closed outcomes condition on execution and exit maturity. Terminal open marks, realized PnL while open, fees, exposure, tails and valuation quality remain separate. Opportunity terminal states are as of account cutoff.",
            "quality": "Independent arithmetic audit does not remove inherited valuation/corporate-action/eligibility/opening-capacity flags. Numeric source values are never repaired here.",
            "benchmark": "Feature excess returns and saved account excessReturn use common sh000300/CSI300; neither is industry/beta neutral or a matched board-index return.",
            "scope": "Board-local leadership and related peers are not verified market-wide leaders or industry/theme members. Cross-board transmission and2026 are outside scope; history was already inspected, not pristine holdout.",
            "compatibility": "Old120-session/5000万元 panel gates are not inherited. Retained config minListingDays/minAmount20 are not extra fixed-signal admission rules.",
            "queuedExitCorrection": "Count action=sell AND status=queued separately from sell fills/closed episodes; no original account artifact changes.",
            "parameterSearch": False, "profitHurdles": None, "accountsRerunByThisScript": 0},
        "accounts": accounts, "accountRows": [deepcopy(indexed[(p, b, m)]) for p in PERIODS for b in BOARDS for m in MODES],
        "comparisons": comparisons, "primaryComparisons": [r for r in comparisons if r["id"] == "primary"],
        "secondaryComparisons": [r for r in comparisons if r["id"] == "secondary"],
        "yearRows": years, "halfYearRows": halves, "phaseRows": phase_rows, "curveEpochRows": epochs,
        "coverageRows": coverage, "formationCoverageRows": formation_coverage, "inputCountsByPeriodBoard": all_counts,
        "partitionReconciliation": partitions,
        "trialAccounting": {"experimentBatches": 1, "newSetupMechanisms": 1, "newEntryPolicies": 2,
            "newUniverseVariants": 0, "newAccountExecutions": 12, "referenceAccountsReused": 0, "summarizerAccountExecutions": 0},
        "limitations": list(dict.fromkeys(plan["limitations"] + [
            "Directional filtering changes density, exposure and later capital paths; it does not identify leader causality or investor control.",
            "Closed-trade means omit long-lived open positions and cannot establish stable small-loss/large-win profitability.",
            "Overlapping anchors, repeated members and serial windows are dependent; edge/opportunity counts are not independent sample counts.",
            "Raw correlation response can occur overnight before next-open entry and is not automatically tradable profit.",
            "Incomplete groups, relationship unknown, unselected notEvaluated and execution nonentry remain separate.",
            "No board/policy/date/threshold is automatically selected or promoted."])),
        "decision": {"selectedBoard": None, "selectedPolicy": None, "automaticNextStrategy": None, "profitHurdle": None},
        "certified": False, "targetAchieved": False}
    for name, expected in pins.items():
        path = Path(name)
        require(sha(path if path.is_absolute() else ROOT / path) == expected, "Input changed during readout: " + name)
    with gzip.open(sidecar, "xt", encoding="utf-8", compresslevel=1) as stream:
        for row in opportunities:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    readout["opportunityRowsArtifact"] = {"path": location(sidecar), "sha256": sha(sidecar), "count": len(opportunities),
        "format": "jsonl+gzip", "identity": ["period", "board", "formationId"], "policiesPerRow": list(MODES)}
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(readout, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "path": location(destination), "sha256": sha(destination),
        "opportunityRowsArtifact": readout["opportunityRowsArtifact"], "accounts": len(accounts), "comparisons": len(comparisons),
        "yearRows": len(years), "halfYearRows": len(halves), "phaseRows": len(phase_rows), "curveEpochRows": len(epochs),
        "coverageRows": len(coverage), "formationCoverageRows": len(formation_coverage), "certified": False}


def layer_self_test():
    day, fday, events, edges = "2024-07-01", "2024-06-17", [], []
    for rank, anchor in enumerate(("a", "b", "partial"), 1):
        eid = "event:" + anchor
        members = [{"symbol": f"m{i}" if anchor == "a" or i == 1 else f"{anchor}{i}", "frozenMemberRank": i}
            for i in range(1, 21 if anchor != "partial" else 4)]
        event = {"eventId": eid, "formationDate": day, "peerFormationDate": fday, "anchorSymbol": anchor,
            "originalLeaderRank": rank, "groupSha256": anchor,
            "groupSummary": {"kind": "incomplete" if anchor == "partial" else "formed", "requiredCount": 20,
                "selectedCount": len(members), "members": members}}
        events.append(event)
        if anchor == "partial":
            continue
        for member in members:
            edges.append({k:v for k,v in {**event, **member, "formationId": eid+":"+member["symbol"]}.items() if k != "groupSummary"})
    edges.sort(key=edge_key)
    owners = {}
    for e in edges:
        first = e["symbol"] not in owners
        if first:
            owners[e["symbol"]] = e
            rank = len(owners)
        else:
            rank = owners[e["symbol"]]["originalRank"]
        selected = first and rank <= 5
        e.update(ownerFormationId=owners[e["symbol"]]["formationId"], ownerOriginalRank=rank,
            ownershipStatus="owner" if first else "duplicateSuppressed", originalRank=rank if first else None,
            selectedTop5=selected, disposition="selectedTop5" if selected else "dailyRankExcluded" if first else "duplicateSuppressed",
            relationshipStatus="notEvaluated")
    original = []
    for e in list(owners.values())[:5]:
        state = "unknown" if e["originalRank"] == 2 else "failed" if e["originalRank"] == 3 else "passed"
        e["relationshipStatus"] = "unknown" if state == "unknown" else "known"
        decisions = {"control": {"status": "unknown" if state == "unknown" else "included", "reason": state},
            "direction": {"status": {"unknown": "unknown", "failed": "excluded", "passed": "included"}[state], "reason": state}}
        signal = {"signalId": e["formationId"], "strategyId": "test", "symbol": e["symbol"], "signalDate": day, "originalRank": e["originalRank"]}
        original.append({**deepcopy(e), "relationship": {"status": e["relationshipStatus"], "directionPassed": None if state == "unknown" else state == "passed"},
            "decisions": decisions, "signals": {m: deepcopy(signal) if decisions[m]["status"] == "included" else None for m in MODES}})
    c = {"currentCandidateCount": 3, "temporalCandidateCount": 3, "leaderEventCount": 3,
        "leaderGroupKinds": {"formed": 2, "incomplete": 1, "unknown": 0}, "rawEdges": 40, "uniqueOwners": 39,
        "duplicateSuppressed": 1, "selectedTop5": 5, "dailyRankExcluded": 34, "knownRelations": 4, "unknownRelations": 1,
        "directionPass": 3, "directionFail": 1, "signals": {"control": 4, "direction": 3}, "notScheduled": {"control": 1, "direction": 2},
        "policyCounts": {"control": {"included": 4, "excluded": 0, "unknown": 1}, "direction": {"included": 3, "excluded": 1, "unknown": 1}}}
    daily = [{"formationDate": day, "peerFormationDate": fday, "counts": c, "leaderEventIds": [r["eventId"] for r in events],
        "memberEdgeIds": [r["formationId"] for r in edges], "selectedFormationIds": [r["formationId"] for r in original],
        "activationCounts": {"candidate": 3, "notTriggered": 37, "unknown": 0}, "formationCounts": {"passed": 3, "failed": 0, "unknown": 0},
        "leaderCandidates": [{}, {}, {}], "leaderRanking": [{}, {}, {}]}]
    counts = {**c, "sampleCount": 40, "opportunityCount": 5}
    checked = check_layers(daily, events, edges, original, counts, [day])
    require(checked["rawEdgeCount"] == 40 and checked["uniqueOwnershipCount"] == 39 and checked["formationTop5"] == 5,
        "Four-layer arithmetic differs")
    for failure in ("ownerSwitch", "partialAdded", "unselectedUnknown", "unknownBackfill"):
        changed_edges, changed_original = deepcopy(edges), deepcopy(original)
        if failure == "ownerSwitch": changed_edges[1]["ownerFormationId"] = changed_edges[1]["formationId"]
        elif failure == "partialAdded": changed_edges[0]["eventId"] = events[-1]["eventId"]
        elif failure == "unselectedUnknown": changed_edges[-1]["relationshipStatus"] = "unknown"
        else: changed_original[1]["decisions"]["control"]["status"] = "included"
        try: check_layers(daily, events, changed_edges, changed_original, counts, [day])
        except ValueError: pass
        else: raise AssertionError("Invalid layer evidence accepted: " + failure)


def synthetic_opportunities(day="2024-07-01"):
    rows = []
    for rank, state in enumerate(("passed", "failed", "unknown"), 1):
        sid = "test:" + state
        signal = {"signalId": sid, "strategyId": "test", "symbol": state, "signalDate": day, "originalRank": rank}
        decisions = {"control": {"status": "unknown" if state == "unknown" else "included", "reason": state},
            "direction": {"status": {"passed": "included", "failed": "excluded", "unknown": "unknown"}[state], "reason": state}}
        rows.append({"formationId": sid, "eventId": "event:owner", "formationDate": day, "peerFormationDate": "2024-06-17",
            "symbol": state, "anchorSymbol": "owner", "originalRank": rank, "decisions": decisions,
            "signals": {m: deepcopy(signal) if decisions[m]["status"] == "included" else None for m in MODES}})
    return rows


def self_test():
    original = synthetic_opportunities()
    for mode, expected in zip(MODES, (2, 1)):
        schedule = ordered_schedule(original, mode)
        opps = [{**s, "filledQuantity": 0, "status": "notPlanned", "reason": "capacity"} for s in schedule]
        counts = formation_counts(original, schedule, opps, mode)
        require(counts["formationTop5"] == 3 and counts["selectedSignalsFromFormation"] == expected
            and counts["notScheduledFromFormation"] == 3-expected and counts["notEnteredFromFormation"] == expected,
            "Original/common-unknown/nonentry denominator lost")
    bad = deepcopy(original)
    bad[2]["decisions"]["control"]["status"] = "included"
    try: ordered_schedule(bad, "control")
    except ValueError: pass
    else: raise AssertionError("Unshared unknown admitted")
    days = ("2024-06-28", "2024-07-01", "2024-07-02", "2024-07-03")
    result = {"curve": [{"date": d, "equity": e, "cash": e, "marketValue": 0., "valuationReliable": True}
        for d,e in zip(days,(100.,90.,95.,99.))], "daily": [{"date": d, "holdings": []} for d in days],
        "trades": [], "closedTrades": [], "decisions": [{"date": days[1], "action": "sell", "status": "queued", "reason": "stop"},
            {"date": days[1], "action": "exit", "status": "queued", "reason": "old"}], "research": {"opportunities": []}}
    whole = interval(result,days[0],days[-1],125.)
    left,right = interval(result,days[0],days[0],125.),interval(result,days[1],days[-1],125.)
    close(right["returnPct"],-1.,"Previous-close boundary")
    close(right["maxDrawdownPct"],10.,"Entering equity drawdown")
    require(right["queuedExitReasons"] == {"stop": 1}, "Queued sell correction lost")
    partition_check([left,right],whole,0,0,"synthetic")
    scheduled = ordered_schedule(original,"control")
    result["research"]["opportunities"] = [{**s,"filledQuantity":0,"status":"notPlanned","reason":"capacity"} for s in scheduled]
    before,after = (interval(result,start,end,125.,original,scheduled,"control")
        for start,end in ((days[0],days[0]),(days[1],days[-1])))
    require(before["opportunitiesByFormationDate"]["formationTop5"] == 0
        and after["opportunitiesByFormationDate"]["formationTop5"] == 3
        and after["opportunitiesByDecisionDate"]["selectedSignals"] == 2,"F mistakenly used as trade-cohort date")
    attached = deepcopy(original)
    attach_dispositions(attached,scheduled,result,{"openPositions":{"count":0,"positions":[]}},"control")
    require(attached[2]["accountDispositions"]["control"]["positionState"] == "notScheduled"
        and attached[0]["accountDispositions"]["control"]["positionState"] == "notEntered","Nonentry/nonschedule conflated")
    require(distribution([None,1.])["mean"] is None,"Unknown metric removed")
    all_accounts, collected = [], Counter()
    for period in PERIODS:
        first, last = map(int, period.split("-"))
        calendar = [f"{year}-{month:02d}-03" for year in (first, last) for month in (1, 4, 7, 10)]
        fixture = {"status": "completed", "curve": [{"date": d, "equity": 500000., "cash": 500000.,
            "marketValue": 0., "valuationReliable": True} for d in calendar],
            "daily": [{"date": d, "holdings": []} for d in calendar], "trades": [], "closedTrades": [],
            "decisions": [], "research": {"opportunities": []}}
        config = {"initialCapital": 500000, "startDate": f"{first}-01-01", "endDate": f"{last}-12-31",
                  "universe": {"minListingDays": 120, "minAmount20": 50000000}}
        saved_periods, phases = [], []
        for year in (first, last):
            for kind, bounds in (("calendarYear", (("01-01", "12-31"),)),
                                 ("calendarHalfYear", (("01-01", "06-30"), ("07-01", "12-31")))):
                for start, end in bounds:
                    saved_periods.append({**interval(fixture, f"{year}-{start}", f"{year}-{end}", 500000), "kind": kind})
            for n, (start, end) in enumerate((("01-01", "03-31"), ("04-01", "06-30"),
                                             ("07-01", "09-30"), ("10-01", "12-31")), 1):
                phases.append({"id": f"{year}-{n}", "title": "synthetic", "startDate": f"{year}-{start}",
                    "endDate": f"{year}-{end}", "sentimentSummary": "synthetic", "dominantThemes": [],
                    "representatives": [], "labelAvailability": "retrospectiveResearchOnly"})
        summary = {"policyId": None, "policyLabel": "synthetic", "quality": {"integrityIssues": []},
            "account": {"netReturnPct": 0., "maxDrawdownPct": 0., "netPnlAmount": 0., "finalEquity": 500000.,
                        "feesPaid": 0., "buyFillCount": 0, "sellFillCount": 0},
            "closedEpisodes": {"netPnlAmount": 0., "meanReturnPct": None}, "openPositions": {"count": 0, "positions": []},
            "reconciliation": {"closedEpisodePnl": 0., "openPositionUnrealizedPnl": 0., "openPositionRealizedPnl": 0.,
                               "rightsAttributionGapAmount": 0.},
            "sourceQuality": {"pointInTimeComplete": False}, "unresolvedExits": {"count": 0},
            "concentration": {"top1ProfitSharePct": None, "top5ProfitSharePct": None}, "rights": {},
            "opportunityDispositions": {"opportunityCounts": {}}, "periods": saved_periods}
        for board in BOARDS:
            for mode in MODES:
                summary["policyId"] = mode
                row = {"period": period, "board": board, "id": mode, "policy": mode, "accountReturnPct": 0.,
                       "maxDrawdownPct": 0., "meanExposurePct": 0., "closed": 0, "signals": 0, "openPositions": 0}
                item = summarize_account(period, board, mode, config, [], [], {"targetDates": calendar}, fixture, summary, row, phases)
                require(item["coverage"]["notEntered"] == 0 and item["account"]["sourceQuality"]["pointInTimeComplete"] is False,
                        "Empty policy/unknown quality lost")
                require(all(c["passed"] for c in item["reconciliation"]["checks"]), "Full account partitions failed")
                for name in ("yearRows", "halfYearRows", "phaseRows", "curveEpochRows"):
                    collected[name] += len(item[name])
                all_accounts.append(item["account"])
    require(dict(collected) == {"yearRows": 24, "halfYearRows": 48, "phaseRows": 96, "curveEpochRows": 12}, "Complete time matrix differs")
    for account in all_accounts:
        account["comparisonRow"]["accountReturnPct"] = float(MODES.index(account["policy"]))
    contrasts = compare_accounts(all_accounts)
    require(len(contrasts) == 6 and all(r["candidateMinusControl"]["accountReturnPct"] == 1. for r in contrasts), "Direction/control contrast orientation")
    require(all(r["candidateMinusControl"]["top1ShareOfGrossProfitPct"] is None for r in contrasts), "Undefined contrast filled")
    layer_self_test()
    return {"passed": True, "checks": ["common unknown and no backfill", "signal identity and admission",
        "prior-close/entering-equity drawdown", "queued sell correction", "PnL/return partition reconciliation",
        "activation A versus relation F cohort", "notScheduled versus notEntered", "unknown metrics preserved",
        "12 complete empty-account adapters with original quality", "24/48/96/12 interval matrix", "6 signed contrasts/null propagation",
        "four-layer ownership/Top5/count conservation"], "filesWritten": 0, "actualResultsRead": False}


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory,args.validation),ensure_ascii=False))
