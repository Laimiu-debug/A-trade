"""Independent pullback-retention readout audit; never imports its new summarizer.

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
DEFAULT = ROOT / "artifacts/strategy-search/leader-pullback-retention-v1"
MAIN = "scripts/verify-leader-pullback-retention.py"
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
        for key, value in expected.items(): same(actual[key], value, label + "." + str(key))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + " length")
        for index, value in enumerate(expected): same(actual[index], value, label + ":" + str(index))
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), label + " number")
    else:
        require(type(actual) is type(expected) and actual == expected, label + " value")


MATH = SimpleNamespace(require=require, finite=finite, same=same, near=same)


STATUSES = ("firstDownUnknown", "noDownThroughT", "sameDayDown", "memberPathUnknown", "commonKnown")


def known_values_reference(values, old):
    original = list(values)
    present = [x for x in original if finite(x)]
    return dict(knownCount=len(present), unknownCount=len(original)-len(present), distribution=old.distribution(present))


def sequence_reference(key, formations, opportunities, counts, schedules, old):
    ids = {r["parentEventId"]: r for r in formations}
    require(len(ids) == len(formations), "Every inherited formation once")
    require(len(set(r["formationId"] for r in opportunities)) == len(opportunities)
        == len(set(r["parentOpportunityId"] for r in opportunities)), "Every original Top5 once")
    lifecycle = {s: sum(r["status"] == s for r in formations) for s in ("formed", "locked", "notFormed")}
    require(sum(lifecycle.values()) == len(formations), "Known inherited lifecycle")
    common = dict.fromkeys(STATUSES, 0)
    down_counts = dict.fromkeys(("priorDown", "sameDayDown", "noDownThroughT", "firstDownUnknown"), 0)
    retained_count = lost_count = 0
    evidence = dict(firstDownUnknown=0, memberUnknown=0, knownBreach=0, memberUnknownWithKnownBreach=0)
    expected_schedules = {p: [] for p in POLICIES}
    for row in opportunities:
        require(ids[row["parentEventId"]]["status"] == "formed", "Top5 belongs to formed parent")
        first, member = row["firstDown"], row["retention"]
        require(first["formationDate"] == row["formationDate"] and first["decisionDate"] == row["decisionDate"], "Per-opportunity prefix")
        require(first["status"] in down_counts, "Valid first-down status")
        down_counts[first["status"]] += 1
        if first["status"] == "priorDown":
            require(row["formationDate"] < first["firstDownDate"] < row["decisionDate"], "Strict A/P/T ordering")
            require(member["status"] in ("known", "unknown"), "Member support evaluated only for prior P")
            state = {"known": "commonKnown", "unknown": "memberPathUnknown"}[member["status"]]
        else:
            state = first["status"]
            require(member["status"] == "notApplicable", "No member retention without prior P")
            if state == "sameDayDown": require(first["firstDownDate"] == row["decisionDate"], "P=T remains excluded")
        require(state == row["commonStatus"], "Exclusive original-opportunity status")
        common[state] += 1
        if state == "commonKnown":
            require(type(member["retained"]) is bool and not member["unknownReasons"], "Known complete member path")
            require(member["retained"] == (len(member["breachDates"]) == 0), "No breach means retained")
            retained_count += int(member["retained"]); lost_count += int(not member["retained"])
        else:
            require(member["retained"] is None, "Missing or inapplicable retention stays null")
        same(member["firstBreachDate"], next(iter(member["breachDates"]), None), "First known breach")
        same(row["flags"]["memberBreachDates"], member["breachDates"], "Independent breach evidence")
        f = row["flags"]
        evidence["firstDownUnknown"] += int(bool(f["firstDownUnknownReasons"]))
        evidence["memberUnknown"] += int(bool(f["memberUnknownReasons"]))
        evidence["knownBreach"] += int(bool(member["breachDates"]))
        evidence["memberUnknownWithKnownBreach"] += int(bool(f["memberUnknownReasons"]) and bool(member["breachDates"]))
        for policy in POLICIES:
            accept = state == "commonKnown" and (policy == "control" or member["retained"] is True)
            require((row["signals"][policy] is not None) == accept, "Exact policy mask")
            if accept: expected_schedules[policy].append(row["signals"][policy])
    same(schedules, expected_schedules, "Original ordered two-policy schedules")
    required = dict(parentFormations=len(formations), formed=lifecycle["formed"], inheritedOther=len(formations)-lifecycle["formed"],
        originalTop5=len(opportunities), opportunities=len(opportunities), commonStatusCounts=common, retainedKnown=retained_count,
        lostKnown=lost_count, signals={p:len(schedules[p]) for p in POLICIES}, notScheduled={p:len(opportunities)-len(schedules[p]) for p in POLICIES})
    for name,value in required.items(): same(counts[name], value, "Input count "+name)
    require(common["commonKnown"] == retained_count+lost_count == len(schedules["control"])
        and retained_count == len(schedules["retained"]), "Sequence/schedule conservation")
    return {**key, **{k:required[k] for k in ("parentFormations", "formed", "inheritedOther", "commonStatusCounts", "retainedKnown", "lostKnown", "signals", "notScheduled")},
        "originalOpportunities":len(opportunities), "parentFormationStatusCounts":lifecycle, "firstDownStatusCounts":down_counts,
        "parentAmountStatusCounts":old.labels(r["parentAmountStatus"] for r in opportunities),
        "diagnostics":{k:known_values_reference([r["diagnostics"][k] for r in opportunities],old)
            for k in ("formationToDownSlots", "downToDecisionSlots", "retentionObservationCount")},
        "independentEvidenceCounts":evidence, "sourceCounts":deepcopy(counts), "conservationPassed":True,
        "meaning":"All parent formations and original Top5 remain. A is leader activation, P is the first proven adjacent decline within each opportunity prefix, T is its inherited breakout. Diagnostics retain unknown and varying observation lengths; none is an extra gate. Parent A amount eligibility remains inherited, while T amount status is descriptive only."}


def sequence_copy(opportunity):
    return {k: deepcopy(opportunity[k]) for k in ("firstDown", "retention", "flags", "diagnostics", "parentAmountStatus")}


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
        state = opportunity["commonStatus"]
        row.update(formationDate=opportunity["formationDate"], decisionDate=opportunity["decisionDate"],
            signalDate=opportunity["signals"][key["policy"]]["signalDate"] if row["scheduled"] else None,
            peerFormationDate=opportunity["peerFormationDate"], parentEventId=opportunity["parentEventId"],
            anchorSymbol=opportunity["anchorSymbol"], frozenMemberRank=opportunity["frozenMemberRank"],
            parentOpportunityId=opportunity["parentOpportunityId"],
            commonStatus=state, sequenceEvidence=sequence_copy(opportunity),
            admissionStatus="included" if row["scheduled"] else "excluded" if state in ("commonKnown","noDownThroughT","sameDayDown") else "unknown")
        output.append(row)
    for row in entries:
        event = original_by_id[row["formationId"]]
        row.pop("shadowEverEntered"); row.pop("candidateOnlyEntry")
        row.update(formationDate=event["formationDate"], decisionDate=event["decisionDate"],
            signalDate=event["signals"][key["policy"]]["signalDate"], peerFormationDate=event["peerFormationDate"],
            parentEventId=event["parentEventId"], anchorSymbol=event["anchorSymbol"], frozenMemberRank=event["frozenMemberRank"],
            parentOpportunityId=event["parentOpportunityId"], sequenceEvidence=sequence_copy(event))
        require(row["peerFormationDate"] < row["formationDate"] < row["signalDate"] < row["entryDate"], "Four original clocks")
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
            commonStatusCounts=old.labels(r["commonStatus"] for r in formed),
            queuedExitReasons=old.labels(r.get("reason") for r in result.get("decisions", [])
                if begin <= r["date"] <= end and r.get("action") == "sell" and r.get("status") == "queued"),
            attributionMeaning="Continuous actual equity, including carried positions. Whole closed episodes follow final exitDate; formation counts use activation A and decisionSignals use first-breakout T. Neither is an interval reset account.")
    return measured, partition


def comparisons_reference(flat):
    indexed = {tuple(r[k] for k in KEYS): r["metrics"] for r in flat}
    require(len(indexed)==len(flat)==24,"All twenty-four account cells")
    output=[]
    for period in PERIODS:
        for board in BOARDS:
            for exit_policy in EXITS:
                for name, control, candidate, role in (("retainedVsControl","control","retained","primary"),):
                    a,b=[indexed[period,board,exit_policy,p] for p in (control,candidate)]
                    output.append(dict(period=period,board=board,exit=exit_policy,id=name,role=role,control=control,candidate=candidate,
                        controlMetrics=a,candidateMetrics=b,candidateMinusControl={k:b[k]-a[k] if finite(a[k]) and finite(b[k]) else None for k in a},
                        meaning="Complete independent accounts on shared original slots and common support; differences include cash, capacity and later entries, not paired-trade causal effects."))
    return output


def metric_reference(summary, scheduled, original_count, old):
    value=old.metric_reference(summary,scheduled,original_count)
    for target,source in (("medianWinPct","medianWinReturnPct"),("medianLossPct","medianLossReturnPct"),
        ("expectancyReturnPct","expectancyReturnPct"),("expectancyPnlAmount","expectancyPnlAmount")):
        value[target]=summary["closedEpisodes"][source]
    return value


def population_reference(key, old_summary, old_signals, original_count, new_metrics, old_ref, old):
    previous=metric_reference(old_summary,old_signals,original_count,old)
    return {**key,"id":"commonControlVsOldControl","oldControlSummary":deepcopy(old_ref),"oldMetrics":previous,"newMetrics":deepcopy(new_metrics),
        "newMinusOld":{name:new_metrics[name]-value if finite(value) and finite(new_metrics[name]) else None for name,value in previous.items()},
        "reusedAccount":False,"meaning":"Old43 control is descriptive population context only. New control is newly replayed on proven prior anchor declines and complete member paths. Parent A initial amount selection remains inherited; parent T amount masks do not. This is population/sequence selection, not a causal retention increment or reused account."}


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
    accounts, flat, sequences, coverages, intervals, partitions, details, entries, population = ([] for _ in range(9))
    parent_comparison = read(pinned(plan["parentComparison"]))
    old_summary_refs = {(r["period"],r["board"],r["exit"]):r["summary"] for r in plan["parentControlSummaries"]}
    require(len(old_summary_refs)==len(plan["parentControlSummaries"])==12,"Twelve old controls as context")
    same(old_summary_refs,{(r["period"],r["board"],r["exit"]):r["summary"] for r in parent_comparison["rows"] if r["policy"]=="control"},"Old control summary bindings")
    source_count = 0
    for period in PERIODS:
        for board in BOARDS:
            group = groups[period, board]
            opportunities = records(pinned(group["opportunities"]))
            dates = read(pinned(group["metadata"]))["targetDates"]
            schedules = {p: read(pinned(group["schedules"][p])) for p in POLICIES}
            source_count += len(opportunities)
            source_key = dict(period=period, board=board)
            sequences.append(sequence_reference(source_key, records(pinned(group["formations"])), opportunities, group["counts"], schedules, old))
            parent_opportunities=records(pinned(group["parentOpportunities"]))
            same([r["parentOpportunityId"] for r in opportunities],[r["formationId"] for r in parent_opportunities],"Complete parent Top5 order")
            old_signal_count=sum(r["signals"]["control"] is not None for r in parent_opportunities)
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
                    metrics = metric_reference(summary, len(schedules[policy]), len(opportunities), old)
                    flat.append({**key, "metrics": metrics})
                    if policy=="control":
                        old_ref=old_summary_refs[period,board,exit_policy]
                        population.append(population_reference(dict(period=period,board=board,exit=exit_policy),
                            read(pinned(old_ref)),old_signal_count,len(opportunities),metrics,old_ref,old))
                    accounts.append({**key, "artifacts": item, "metrics": metrics,
                        **{k: summary[k] for k in ("account", "closedEpisodes", "concentration", "openPositions",
                            "rights", "unresolvedExits", "reconciliation", "quality", "warnings")},
                        "sourceQualityScalarFlags": {k: v for k, v in summary["sourceQuality"].items() if not isinstance(v, (dict, list))},
                        "fullSourceQuality": {"summaryRef": item["summary"], "field": "sourceQuality"}})
                    states, admissions = Counter(r["positionState"] for r in detail), Counter(r["admissionStatus"] for r in detail)
                    coverages.append({**key, "originalOpportunities": len(opportunities), "scheduledSignals": len(schedules[policy]),
                        "notScheduled": states["notScheduled"], "notEntered": states["notEntered"], "entered": len(actual),
                        "closed": states["closed"], "openAtCutoff": states["openAtCutoff"],
                        "admissionCounts": {s: admissions[s] for s in ("included", "excluded", "unknown")},
                        "accountOpportunityReasons": old.labels(r["accountDisposition"]["reason"] for r in detail if r["scheduled"]),
                        "valuationUnreliableDays": metrics["unreliableValuationDays"], "openStaleMarks": metrics["openStaleMarks"]})
                    measured, part = intervals_reference(key, result, summary, opportunities, schedules[policy], old, phases)
                    intervals.extend(measured); partitions.append(part)
            print(json.dumps({"verifiedReadout": [period, board], "originalTop5": len(opportunities)}), flush=True)
    contrasts = comparisons_reference(flat)
    expected = dict(accounts=accounts, accountRows=flat, comparisons=contrasts,
        primaryComparisons=contrasts,
        sequenceCoverageRows=sequences, coverageRows=coverages, populationContextRows=population, partitionReconciliation=partitions)
    for name, kind in (("yearRows", "calendarYear"), ("halfYearRows", "calendarHalfYear"),
                      ("phaseRows", "retrospectiveNarrativePhase"), ("curveEpochRows", "descriptiveEpoch")):
        expected[name] = [r for r in intervals if r["kind"] == kind]
    for name, value in expected.items(): same(report[name], value, "Readout table " + name)
    for name, rows in (("opportunityRowsArtifact", details), ("entryRowsArtifact", entries)):
        ref = report[name]
        require(ref["count"] == len(rows) and ref["format"] == "jsonl+gzip" and ref["identity"] == [*KEYS, "formationId"], "Full sidecar descriptor")
        require(len({tuple(r[k] for k in (*KEYS, "formationId")) for r in rows}) == len(rows), "Unique sidecar identity")
        same(records(pinned(ref)), rows, name)
    expected_counts = dict(accounts=24, newAccounts=24, reusedAccounts=0, comparisons=12, primaryComparisons=12,
        yearRows=48, halfYearRows=96, phaseRows=192, curveEpochRows=24, sequenceCoverageRows=6,
        coverageRows=24, populationContextRows=12, partitionReconciliation=24, sourceOpportunityCount=source_count,
        parentFormations=sum(r["parentFormations"] for r in sequences), formed=sum(r["formed"] for r in sequences),
        policyDispositions=source_count*4, actualEntryEpisodes=len(entries), fullCurveIntervals=24, subIntervals=360)
    same(report["counts"], expected_counts, "Complete matrix counts")
    require(source_count == proof["counts"]["sourceOpportunityCount"] and report["accountExecutionsBySummarizer"] == 0
        and report["holdoutClaim"] is report["certified"] is report["targetAchieved"] is False, "Scope remains descriptive")
    validation = dict(passed=True, schema="leader-pullback-retention-readout-independent-validation-v1",
        completedAt=datetime.now(timezone.utc).isoformat(), planSha256=plan_sha,
        comparisonSha256=sha(directory / "comparison.json"), readoutSha256=sha(directory / "readout.json"),
        accountValidationSha256=sha(directory / "validation.json"), verifierSha256=sha(__file__),
        opportunityRowsArtifactSha256=report["opportunityRowsArtifact"]["sha256"],
        entryRowsArtifactSha256=report["entryRowsArtifact"]["sha256"], counts=expected_counts,
        sourcePins={MAIN: sha(ROOT / MAIN), OLD: OLD_SHA, EPISODES: EPISODES_SHA},
        checks=["Independent raw-fill episode joins", "Original activation A and decision T denominators",
            "All full-account scalars and open/closed amounts", "Continuous carried-equity intervals and partitions",
            "Every original Top5 disposition and exclusive sequence status", "All twelve retained-minus-control contrasts", "All inherited formations and prior-down/member-path evidence, including unknown plus known breach", "Old control population context is not account reuse or a causal sequence effect"])
    destination.write_text(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"passed": True, "validation": reference(destination), "counts": expected_counts}


def self_test():
    old = load_file(OLD, OLD_SHA); episodes = load_file(EPISODES, EPISODES_SHA)
    tuple_key = ("2024-2025", "main", "trail10")
    same({tuple_key: {"path":"old", "sha256":"hash"}}, {tuple_key: {"path":"old", "sha256":"hash"}}, "Tuple-key provenance")
    try: same({tuple_key:1.}, {tuple_key:2.}, "Tuple mismatch")
    except AssertionError: pass
    else: raise AssertionError("Tuple mismatch accepted")
    signal = dict(signalId="a", strategyId="leaderPullbackRetentionV1", symbol="s", signalDate="2024-01-03", originalRank=1)
    def event(fid, state="commonKnown", kept=True):
        sig = dict(signal, signalId=fid)
        prior = state in ("commonKnown", "memberPathUnknown")
        first = dict(status="priorDown" if prior else state, formationDate="2024-01-01", decisionDate="2024-01-03",
            firstDownDate="2024-01-02" if prior else "2024-01-03" if state=="sameDayDown" else None,
            unknownDate="2024-01-02" if state=="firstDownUnknown" else None,windowDates=["2024-01-01","2024-01-02"],
            evaluatedPairs=[],reasons=["unknown"] if state=="firstDownUnknown" else [])
        breaches = ["2024-01-02"] if prior and not kept else []
        member = dict(status="known" if state=="commonKnown" else "unknown" if prior else "notApplicable",
            referenceDate="2024-01-01" if prior else None,referenceClose=10. if prior else None,referenceReason=None,
            windowDates=["2024-01-01","2024-01-02"] if prior else [], observations=[dict(date="2024-01-02",close=10.)] if prior else [],
            unknownReasons=["missingMember"] if state=="memberPathUnknown" else [],breachDates=breaches,
            firstBreachDate=next(iter(breaches),None),retained=kept if state=="commonKnown" else None)
        return dict(formationId=fid,parentOpportunityId="old:"+fid,parentEventId="p",formationDate="2024-01-01",
            peerFormationDate="2023-12-01",decisionDate="2024-01-03",symbol="s",anchorSymbol="anchor",frozenMemberRank=1,originalRank=1,
            commonStatus=state,firstDown=first,retention=member,parentAmountStatus="unknown" if fid=="a" else "known",
            flags=dict(firstDownUnknownReasons=first["reasons"][:],memberUnknownReasons=member["unknownReasons"][:],memberBreachDates=breaches[:]),
            diagnostics=dict(formationToDownSlots=1 if prior else None,downToDecisionSlots=1 if prior else None,retentionObservationCount=len(member["observations"])),
            signals={p:deepcopy(sig) if state=="commonKnown" and (p=="control" or kept) else None for p in POLICIES})
    opportunities=[event("a"),event("b",kept=False)]+[event(name,name,False) for name in STATUSES[:-1]]
    forms=[dict(parentEventId=i,status=s) for i,s in (("p","formed"),("noTop5","formed"),("locked","locked"),("notFormed","notFormed"))]
    schedules={p:[r["signals"][p] for r in opportunities if r["signals"][p] is not None] for p in POLICIES}
    counts=dict(parentFormations=4,formed=2,inheritedOther=2,originalTop5=6,opportunities=6,
        commonStatusCounts=dict(firstDownUnknown=1,noDownThroughT=1,sameDayDown=1,memberPathUnknown=1,commonKnown=2),retainedKnown=1,lostKnown=1,
        signals=dict(control=2,retained=1),notScheduled=dict(control=4,retained=5))
    covered=sequence_reference({},forms,opportunities,counts,schedules,old)
    require(covered["formed"]==2 and covered["retainedKnown"]==1 and covered["independentEvidenceCounts"]["memberUnknownWithKnownBreach"]==1,"Complete source populations and independent unknown/breach")
    require(covered["parentAmountStatusCounts"]["unknown"]==1 and schedules["retained"][0]["signalId"]=="a","Parent T amount does not mask price gate")
    changed=deepcopy(opportunities);changed[0]["firstDown"]["firstDownDate"]="2024-01-03"
    try: sequence_reference({},forms,changed,counts,schedules,old)
    except AssertionError: pass
    else: raise AssertionError("P=T admitted")
    changed=deepcopy(opportunities);changed[-1]["commonStatus"]="commonKnown"
    try: sequence_reference({},forms,changed,counts,schedules,old)
    except AssertionError: pass
    else: raise AssertionError("Unknown promoted to common")
    changed=deepcopy(schedules);changed["control"].reverse()
    try: sequence_reference({},forms,opportunities,counts,changed,old)
    except AssertionError: pass
    else: raise AssertionError("Schedule reorder accepted")
    require(known_values_reference([None],old)["distribution"]["mean"] is None,"Missing diagnostic stays unknown")
    flat=[dict(period=p,board=b,exit=e,policy=q,metrics=dict(accountReturnPct=-2. if q=="retained" else -5.,profitFactor=None))
        for p in PERIODS for b in BOARDS for e in EXITS for q in POLICIES]
    contrasts=comparisons_reference(flat)
    require(len(contrasts)==12 and all(r["id"]=="retainedVsControl" and r["candidateMinusControl"]["accountReturnPct"]==3. for r in contrasts),"All signed complete-account differences")
    raw=episodes.synthetic_fixture();buys=[r for r in raw["trades"] if r["side"]=="buy"]
    schedule=[dict(signalId=r["signalId"],strategyId="leaderPullbackRetentionV1",symbol=r["symbol"],signalDate=r["signalDate"],originalRank=1) for r in buys]
    raw["research"]={"opportunities":[{**sig,"status":"filled","reason":"filled","filledQuantity":buy["quantity"],"attemptDate":buy["date"]} for sig,buy in zip(schedule,buys)]}
    events=[]
    for sig,day,pday in zip(schedule,("2023-12-29","2024-01-03"),("2024-01-01","2024-01-04")):
        row=event(sig["signalId"]);row.update(formationDate=day,decisionDate=sig["signalDate"],signals={p:deepcopy(sig) for p in POLICIES})
        row["firstDown"].update(formationDate=day,decisionDate=sig["signalDate"],firstDownDate=pday)
        events.append(row)
    events.append(opportunities[-1]);key=dict(period="2024-2025",board="main",exit="trail10",policy="retained")
    before=deepcopy((events,schedule,raw));detail,entries=episodes_reference(key,events,schedule,raw,old,episodes)
    require(len(detail)==3 and len(entries)==2 and detail[-1]["admissionStatus"]=="unknown"
        and entries[0]["closedPnlAmount"]==16. and entries[1]["openUnrealizedPnlAmount"]==48.
        and entries[1]["closedReturnPct"] is None and entries[1]["openMarkStale"] is True,"Independent partial/final sell and open maturity")
    require(entries[0]["parentOpportunityId"]=="old:first" and entries[0]["formationDate"]=="2023-12-29"
        and entries[0]["sequenceEvidence"]["firstDown"]["firstDownDate"]=="2024-01-01" and entries[0]["entryDate"]=="2024-01-03","Parent and F/A/P/T/open clocks")
    same((events,schedule,raw),before,"No input mutation")
    for fill in raw["trades"]:fill["slippage"]=0.
    raw["decisions"]=[dict(date="2024-01-04",action=a,status="queued",reason=a) for a in ("sell","exit")]
    saved=dict(periods=[dict(id="overall")],account=dict(netPnlAmount=64.,netReturnPct=.64,feesPaid=6.),closedEpisodes=dict(count=1))
    phase_map={}
    for name,begin,end,pnl,closed in (("a","2024-01-02","2024-01-04",0.,0),("b","2024-01-05","2024-01-07",64.,1)):
        saved["periods"].append(dict(id=name,kind="retrospectiveNarrativePhase",requestedStart=begin,requestedEnd=end,
            enteringEquity=10000.,endingEquity=10000.+pnl,netPnlAmount=pnl,returnPct=pnl/100.,maxDrawdownPct=0.,closedEpisodes=dict(count=closed)))
        phase_map[name]=dict(title=name)
    intervals,partition=intervals_reference(key,raw,saved,events,schedule,old,phase_map)
    require(partition["passed"] and intervals[0]["queuedExitReasons"]==dict(sell=1)
        and intervals[1]["enteringEquity"]==intervals[0]["endingEquity"],"Continuous equity and queued sell")
    changed=deepcopy(saved);changed["periods"][2]["enteringEquity"]=9999.
    try:intervals_reference(key,raw,changed,events,schedule,old,phase_map)
    except AssertionError:pass
    else:raise AssertionError("Reset capital accepted")
    return dict(passed=True,syntheticGroups=12,filesWritten=0,realSourceReads=0,accountExecutions=0)


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--out", type=Path, default=DEFAULT); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else verify(args.out), ensure_ascii=False))
