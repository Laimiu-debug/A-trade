"""Independent matched-anchor readout audit; never imports its new summarizer.

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
DEFAULT = ROOT / "artifacts/strategy-search/leader-matched-anchor-v1"
MAIN = "scripts/verify-leader-matched-anchor.py"
OLD = "scripts/verify-online-feedback-readout.py"
OLD_SHA = "dd2f467f25d91ecbd5712b40dc8d7c02717ab4ac82a6d755dd51c306c9c233b2"
EPISODES = "scripts/verify-reclaim-exit-mechanism.py"
EPISODES_SHA = "96ef23022f1a8a515ecf94eabdab8696057bcb166a2cd309fe43f101a507cd89"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
EXITS = ("trail10", "volatilityTrail")
POLICIES = ("control", "realCross", "pseudoCross")
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


STATUSES = ("matchUnavailable", "initialUnknown", "initialAbove", "decisionUnknown", "commonKnown")
OVERLAPS = ("both", "realOnly", "pseudoOnly", "neither")


def known_values_reference(values, old):
    original = list(values)
    present = [x for x in original if finite(x)]
    return dict(knownCount=len(present), unknownCount=len(original)-len(present), distribution=old.distribution(present))


def matching_reference(key, matches, opportunities, counts, schedules, old):
    require(len({r["parentEventId"] for r in matches}) == len(matches), "Unique inherited formation")
    require(len({r["formationId"] for r in opportunities}) == len(opportunities)
        == len({r["parentOpportunityId"] for r in opportunities}), "Original Top5 identities")
    matching_states = Counter(r["matchStatus"] for r in matches)
    status = Counter(r["commonStatus"] for r in opportunities)
    match_index = {r["parentEventId"]: r for r in matches}
    chosen = [r for r in matches if r["matchStatus"] == "matched"]
    assignments = Counter(r["pseudoSymbol"] for r in chosen)
    expected_matches = {name: matching_states[name] for name in ("matched","anchorScaleUnknown","noEligibleCandidate","inheritedOther")}
    require(sum(expected_matches.values()) == len(matches), "Every match outcome retained")
    for r in matches:
        require((r["parentStatus"] == "formed") == (r["matchStatus"] != "inheritedOther"), "Only formed events matched")
    flag_counts = Counter()
    for row in opportunities:
        f = row["flags"]
        causes = [("matchUnavailable",f["matchUnavailable"]),("initialUnknown",bool(f["initialUnknownReasons"])),
                  ("initialAbove",bool(f["initialAboveAnchors"])),("decisionUnknown",bool(f["decisionUnknownReasons"]))]
        expected_status = next((name for name, active in causes if active), "commonKnown")
        require(row["commonStatus"] == expected_status, "Exclusive status and independent reasons")
        require(row["pseudoSymbol"] == match_index[row["parentEventId"]]["pseudoSymbol"], "Same A-frozen match")
        for name, value in f.items(): flag_counts[name] += int(bool(value))
        if expected_status == "commonKnown":
            x, y = row["crossing"]["real"], row["crossing"]["pseudo"]
            require(type(x) is type(y) is bool, "Known two-anchor crossing")
            cell = {(True,True):"both",(True,False):"realOnly",(False,True):"pseudoOnly",(False,False):"neither"}[x,y]
            require(row["overlap"] == cell, "Exact overlap")
        else:
            require(row["overlap"] is None, "Outside common support has no crossing cell")
        for policy in POLICIES:
            allowed = expected_status == "commonKnown" and (policy == "control" or row["crossing"]["real" if policy == "realCross" else "pseudo"])
            require((row["signals"][policy] is not None) == allowed, "Policy population")
    for policy in POLICIES:
        same(schedules[policy],[r["signals"][policy] for r in opportunities if r["signals"][policy] is not None],"Exact original-order schedule")
    common = {name:status[name] for name in STATUSES}
    require(sum(common.values()) == len(opportunities), "All common outcomes")
    calculated = dict(parentFormations=len(matches),formed=len(matches)-matching_states["inheritedOther"],**expected_matches,
        originalTop5=len(opportunities),opportunities=len(opportunities),commonStatusCounts=common,
        overlapCounts={k:sum(r["overlap"]==k for r in opportunities) for k in OVERLAPS},
        distinctPseudoAnchors=len(assignments),reusedPseudoAssignments=len(chosen)-len(assignments),
        signals={p:len(schedules[p]) for p in POLICIES},notScheduled={p:len(opportunities)-len(schedules[p]) for p in POLICIES})
    for name,value in calculated.items():same(counts[name],value,"Source count "+name)
    diagnostics={name:known_values_reference([r["diagnostics"][name] for r in opportunities],old) for name in (
        "prior20ScaleRatio","matchDistance","initialPseudoRealM3Ratio","initialMemberRealM3Ratio","initialMemberPseudoM3Ratio")}
    return {**key,"parentFormations":len(matches),"formed":len(matches)-matching_states["inheritedOther"],**expected_matches,
        "originalOpportunities":len(opportunities),"commonStatusCounts":common,"signals":calculated["signals"],"notScheduled":calculated["notScheduled"],
        "distinctPseudoSymbols":len(assignments),"reusedPseudoSymbols":sum(n>1 for n in assignments.values()),
        "maxFormationsPerPseudo":max(assignments.values(),default=0),"pseudoFormationUsage":dict(sorted(assignments.items())),
        "matchingDistance":known_values_reference([r["distance"] for r in chosen],old),
        "matchingScaleRatio":known_values_reference([r["pseudoScale"]["meanAmount20"]/r["anchorScale"]["meanAmount20"] for r in chosen],old),
        "opportunityDiagnostics":diagnostics,"independentFlagCounts":dict(sorted(flag_counts.items())),"sourceCounts":deepcopy(counts),"conservationPassed":True,
        "meaning":"Every inherited formation is retained, including formed events with no later opportunity. Match diagnostics cover matched formations; opportunity diagnostics retain separate known/unknown denominators. No diagnostic creates a gate."}


def overlap_reference(key, opportunities, schedules):
    counts = Counter(r["overlap"] for r in opportunities)
    known = sum(r["commonStatus"]=="commonKnown" for r in opportunities)
    require(sum(counts[x] for x in OVERLAPS) == known == len(schedules["control"])
        and len(schedules["realCross"]) == counts["both"]+counts["realOnly"]
        and len(schedules["pseudoCross"]) == counts["both"]+counts["pseudoOnly"],"Overlap and all policy schedules")
    return {**key,"originalOpportunities":len(opportunities),"commonKnown":known,"outsideCommon":len(opportunities)-known,
        **{x:counts[x] for x in OVERLAPS},"meaning":"Overlap is measured on original common-known opportunities, before account capacity, fills or outcomes."}


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
            parentOpportunityId=opportunity["parentOpportunityId"], pseudoSymbol=opportunity["pseudoSymbol"],
            commonStatus=state, crossing=deepcopy(opportunity["crossing"]), overlap=opportunity["overlap"], independentFlags=deepcopy(opportunity["flags"]),
            admissionStatus="included" if row["scheduled"] else "excluded" if state in ("commonKnown","initialAbove") else "unknown")
        output.append(row)
    for row in entries:
        event = original_by_id[row["formationId"]]
        row.pop("shadowEverEntered"); row.pop("candidateOnlyEntry")
        row.update(formationDate=event["formationDate"], decisionDate=event["decisionDate"],
            signalDate=event["signals"][key["policy"]]["signalDate"], peerFormationDate=event["peerFormationDate"],
            parentEventId=event["parentEventId"], anchorSymbol=event["anchorSymbol"], frozenMemberRank=event["frozenMemberRank"],
            parentOpportunityId=event["parentOpportunityId"], pseudoSymbol=event["pseudoSymbol"])
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
    require(len(indexed)==len(flat)==36,"All thirty-six account cells")
    output=[]
    for period in PERIODS:
        for board in BOARDS:
            for exit_policy in EXITS:
                for name, control, candidate, role in (("realCrossVsPseudoCross","pseudoCross","realCross","primary"),
                        ("realCrossVsControl","control","realCross","secondary"),("pseudoCrossVsControl","control","pseudoCross","secondary")):
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
        "reusedAccount":False,"meaning":"Old43 control is descriptive population context only. New common control is newly replayed after adding pseudo-anchor matching/completeness and dual initial eligibility. This difference is not an anchor increment or reused account."}


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
    require(len(descriptors) == len(comparison["rows"]) == 36, "Unique account matrix")
    groups = {(g["period"], g["board"]): g for g in completion["groups"]}
    accounts, flat, matching, coverages, intervals, partitions, details, entries, overlaps, population = ([] for _ in range(10))
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
            matching.append(matching_reference(source_key, records(pinned(group["matches"])), opportunities, group["counts"], schedules, old))
            overlaps.append(overlap_reference(source_key, opportunities, schedules))
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
        primaryComparisons=[r for r in contrasts if r["role"]=="primary"],secondaryComparisons=[r for r in contrasts if r["role"]=="secondary"],
        matchingCoverageRows=matching, coverageRows=coverages, overlapRows=overlaps, populationContextRows=population, partitionReconciliation=partitions)
    for name, kind in (("yearRows", "calendarYear"), ("halfYearRows", "calendarHalfYear"),
                      ("phaseRows", "retrospectiveNarrativePhase"), ("curveEpochRows", "descriptiveEpoch")):
        expected[name] = [r for r in intervals if r["kind"] == kind]
    for name, value in expected.items(): same(report[name], value, "Readout table " + name)
    for name, rows in (("opportunityRowsArtifact", details), ("entryRowsArtifact", entries)):
        ref = report[name]
        require(ref["count"] == len(rows) and ref["format"] == "jsonl+gzip" and ref["identity"] == [*KEYS, "formationId"], "Full sidecar descriptor")
        require(len({tuple(r[k] for k in (*KEYS, "formationId")) for r in rows}) == len(rows), "Unique sidecar identity")
        same(records(pinned(ref)), rows, name)
    expected_counts = dict(accounts=36, newAccounts=36, reusedAccounts=0, comparisons=36,primaryComparisons=12,secondaryComparisons=24,
        yearRows=72, halfYearRows=144, phaseRows=288, curveEpochRows=36, matchingCoverageRows=6,overlapRows=6,
        coverageRows=36, populationContextRows=12, partitionReconciliation=36, sourceOpportunityCount=source_count,
        parentFormations=sum(r["parentFormations"] for r in matching),formed=sum(r["formed"] for r in matching),matched=sum(r["matched"] for r in matching),
        policyDispositions=source_count * 6, actualEntryEpisodes=len(entries), fullCurveIntervals=36, subIntervals=540)
    same(report["counts"], expected_counts, "Complete matrix counts")
    require(source_count == proof["counts"]["sourceOpportunityCount"] and report["accountExecutionsBySummarizer"] == 0
        and report["holdoutClaim"] is report["certified"] is report["targetAchieved"] is False, "Scope remains descriptive")
    validation = dict(passed=True, schema="leader-matched-anchor-readout-independent-validation-v1",
        completedAt=datetime.now(timezone.utc).isoformat(), planSha256=plan_sha,
        comparisonSha256=sha(directory / "comparison.json"), readoutSha256=sha(directory / "readout.json"),
        accountValidationSha256=sha(directory / "validation.json"), verifierSha256=sha(__file__),
        opportunityRowsArtifactSha256=report["opportunityRowsArtifact"]["sha256"],
        entryRowsArtifactSha256=report["entryRowsArtifact"]["sha256"], counts=expected_counts,
        sourcePins={MAIN: sha(ROOT / MAIN), OLD: OLD_SHA, EPISODES: EPISODES_SHA},
        checks=["Independent raw-fill episode joins", "Original activation A and decision T denominators",
            "All full-account scalars and open/closed amounts", "Continuous carried-equity intervals and partitions",
            "Every original Top5 disposition and exclusive-status priority", "All twelve primary and twenty-four secondary contrasts", "All inherited formations including no-opportunity matches and repeated pseudo stocks", "Old control population context is not account reuse or an anchor effect"])
    destination.write_text(json.dumps(validation, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"passed": True, "validation": reference(destination), "counts": expected_counts}


def self_test():
    old=load_file(OLD,OLD_SHA); episodes=load_file(EPISODES,EPISODES_SHA)
    signal=dict(signalId="a",strategyId="leaderMatchedAnchorV1",symbol="s",signalDate="2024-01-03",originalRank=1)
    def event(fid, state, scheduled=False):
        return dict(formationId=fid,parentOpportunityId="old:"+fid,parentEventId="p",formationDate="2024-01-01",
            peerFormationDate="2023-12-01",decisionDate="2024-01-03",symbol="s",anchorSymbol="anchor",pseudoSymbol="pseudo",
            frozenMemberRank=1,originalRank=1,commonStatus=state,
            flags=dict(matchUnavailable=False,initialUnknownReasons=[],initialAboveAnchors=["pseudo"] if state=="initialAbove" else [],
                decisionUnknownReasons=["missing"] if state!="commonKnown" else []),
            crossing=dict(real=False,pseudo=False),overlap="neither" if state=="commonKnown" else None,
            signals=dict(control=deepcopy(signal) if scheduled else None,realCross=None,pseudoCross=None),
            diagnostics={k:1. if state=="commonKnown" else None for k in ("prior20ScaleRatio","matchDistance",
                "initialPseudoRealM3Ratio","initialMemberRealM3Ratio","initialMemberPseudoM3Ratio")})
    opportunities=[event("a","commonKnown",True),event("b","initialAbove"),event("c","decisionUnknown")]
    matches=[dict(parentEventId=e,parentStatus="formed",matchStatus="matched",pseudoSymbol="pseudo",distance=.2,
        anchorScale=dict(meanAmount20=10.),pseudoScale=dict(meanAmount20=12.)) for e in ("p","noFutureOpportunity")]
    schedules=dict(control=[signal],realCross=[],pseudoCross=[])
    counts=dict(parentFormations=2,formed=2,matched=2,anchorScaleUnknown=0,noEligibleCandidate=0,inheritedOther=0,
        originalTop5=3,opportunities=3,commonStatusCounts=dict(matchUnavailable=0,initialUnknown=0,initialAbove=1,decisionUnknown=1,commonKnown=1),
        overlapCounts=dict(both=0,realOnly=0,pseudoOnly=0,neither=1),distinctPseudoAnchors=1,reusedPseudoAssignments=1,
        signals=dict(control=1,realCross=0,pseudoCross=0),notScheduled=dict(control=2,realCross=3,pseudoCross=3))
    covered=matching_reference({},matches,opportunities,counts,schedules,old)
    require(covered["matched"]==2 and covered["maxFormationsPerPseudo"]==2
        and covered["independentFlagCounts"]["decisionUnknownReasons"]==2,"No-future-opportunity matches and independent flags")
    require(covered["opportunityDiagnostics"]["matchDistance"]["unknownCount"]==2
        and covered["opportunityDiagnostics"]["matchDistance"]["distribution"]["mean"]==1.,"Explicit known-only diagnostic distribution")
    changed=deepcopy(opportunities);changed[1]["commonStatus"]="decisionUnknown"
    try:matching_reference({},matches,changed,counts,schedules,old)
    except AssertionError:pass
    else:raise AssertionError("InitialAbove priority lost")
    changed=deepcopy(opportunities);changed[0]["pseudoSymbol"]="replacement"
    try:matching_reference({},matches,changed,counts,schedules,old)
    except AssertionError:pass
    else:raise AssertionError("Changed frozen pseudo accepted")
    overlap=overlap_reference({},opportunities,schedules)
    require(overlap["neither"]==1 and overlap["outsideCommon"]==2,"Common support is separate from all original Top5")
    cells=[]
    for cell,(a,b) in zip(OVERLAPS,((True,True),(True,False),(False,True),(False,False))):
        row=event(cell,"commonKnown");row["crossing"]=dict(real=a,pseudo=b);row["overlap"]=cell
        row["signals"]={p:dict(signal,signalId=cell) if p=="control" or (a if p=="realCross" else b) else None for p in POLICIES};cells.append(row)
    cell_schedules={p:[r["signals"][p] for r in cells if r["signals"][p] is not None] for p in POLICIES}
    require(all(overlap_reference({},cells,cell_schedules)[name]==1 for name in OVERLAPS),"All four fixed overlap cells")
    rows=[dict(period=p,board=b,exit=e,policy=q,metrics=dict(accountReturnPct={"control":-5.,"realCross":-2.,"pseudoCross":1.}[q],profitFactor=None))
        for p in PERIODS for b in BOARDS for e in EXITS for q in POLICIES]
    contrasts=comparisons_reference(rows)
    require(len(contrasts)==36 and sum(r["role"]=="primary" for r in contrasts)==12
        and all(r["candidateMinusControl"]["accountReturnPct"]==-3. for r in contrasts if r["role"]=="primary"),"All signed primary/secondary contrasts")
    require(known_values_reference([None],old)["distribution"]["mean"] is None,"Missing diagnostic not zero-filled")
    raw=episodes.synthetic_fixture();buys=[r for r in raw["trades"] if r["side"]=="buy"]
    schedule=[dict(signalId=r["signalId"],strategyId="leaderMatchedAnchorV1",symbol=r["symbol"],signalDate=r["signalDate"],originalRank=1) for r in buys]
    raw["research"]={"opportunities":[{**sig,"status":"filled","reason":"filled","filledQuantity":buy["quantity"],"attemptDate":buy["date"]} for sig,buy in zip(schedule,buys)]}
    events=[]
    for sig,day in zip(schedule,("2023-12-29","2024-01-03")):
        row=event(sig["signalId"],"commonKnown");row.update(formationDate=day,decisionDate=sig["signalDate"],crossing=dict(real=True,pseudo=True),overlap="both",
            signals={p:deepcopy(sig) for p in POLICIES});events.append(row)
    events.append(opportunities[-1]);key=dict(period="2024-2025",board="main",exit="trail10",policy="realCross")
    before=deepcopy((events,schedule,raw));detail,entries=episodes_reference(key,events,schedule,raw,old,episodes)
    require(len(detail)==3 and len(entries)==2 and detail[-1]["admissionStatus"]=="unknown"
        and entries[0]["closedPnlAmount"]==16. and entries[1]["openUnrealizedPnlAmount"]==48.
        and entries[1]["closedReturnPct"] is None and entries[1]["openMarkStale"] is True,"Independent partial/final sell and open maturity")
    require(entries[0]["parentOpportunityId"]=="old:first" and entries[0]["formationDate"]=="2023-12-29"
        and entries[0]["entryDate"]=="2024-01-03","Parent identity and A/T/open clocks")
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
    return dict(passed=True,syntheticGroups=11,filesWritten=0,realSourceReads=0,accountExecutions=0)


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--out", type=Path, default=DEFAULT); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else verify(args.out), ensure_ascii=False))
