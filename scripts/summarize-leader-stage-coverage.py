"""Full-calendar coverage readout; no prices, signals, forecasts or accounts are generated.

The production path aggregates joined day records. --verify separately filters
the frozen source records and recomputes slice counters and environment means.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/leader-stage-coverage-v1"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
ACTIVATION = ("candidate", "notTriggered", "unknown")
FORMATION = ("passed", "failed", "unknown")
GROUP_KINDS = ("formed", "incomplete", "unknown")
HANDOFF = ("formed", "locked", "notFormed")
SCORE = ("forecastBeyondEnd", "fitUnknown", "featureUnknown", "targetBeyondEnd",
         "targetUnknown", "numericUnknown", "scored")
DEPENDENCIES = {}
ENV_PATHS = {
    "priorClosePositiveReturn20Breadth": ("price20", "positiveReturn20Breadth"),
    "priorCloseMedianAbsoluteReturn20": ("price20", "medianAbsoluteReturn20"),
    "priorCloseMatchedBreadthChange5": ("matched5", "matchedBreadthChange5"),
    "priorCloseRelativeAmount20": ("amount", "medianRelativeToOwnPrior20"),
    "priorCloseContinuousRetentionLower": ("retention", "continuous", "retentionLower"),
    "priorCloseContinuousRetentionUpper": ("retention", "continuous", "retentionUpper"),
    "priorCloseEndpointRetentionLower": ("retention", "endpoint", "retentionLower"),
    "priorCloseEndpointRetentionUpper": ("retention", "endpoint", "retentionUpper"),
    "price20EvaluableCount": ("price20", "evaluableCount"),
    "price20Coverage": ("price20", "coverage"),
    "amountEvaluableCount": ("amount", "evaluableCount"),
    "amountCoverage": ("amount", "coverage"),
    "matched5PairedCount": ("matched5", "pairedCount"),
    "matched5PairedCoverageFixed": ("matched5", "pairedCoverageFixed"),
    "frozenStrongCohortSize": ("retention", "cohortSize"),
    "continuousUnknownMembers": ("retention", "continuous", "unknownCount"),
}
SUM_FIELDS = ("symbolDayCount", "currentCandidateCount", "temporalCandidateCount", "leaderEventCount",
              "rankExcludedCount", "daysWithCurrentCandidates", "daysWithTemporalCandidates",
              "daysWithLeaderEvents", "zeroLeaderEventDays", "daysWith43Formed", "zero43FormedDays")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def reference(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}


def pinned(ref):
    path = ROOT / ref["path"]
    require(path.is_file() and sha(path) == ref["sha256"], "Artifact pin: " + str(ref["path"]))
    return path


def records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_records(path, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def same(actual, expected, label="value"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + " keys")
        for key, value in expected.items():
            same(actual[key], value, label + "." + str(key))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + " length")
        for i, value in enumerate(expected):
            same(actual[i], value, label + "." + str(i))
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12), label + " numeric")
    else:
        require(type(actual) is type(expected) and actual == expected, label + " exact")


def index_unique(rows, key, label):
    out = {r[key]: r for r in rows}
    require(len(out) == len(rows), "Duplicate " + label)
    return out


def counter(values, allowed):
    values = Counter(values)
    require(not (values.keys() - set(allowed)), "Unknown category: " + str(values.keys() - set(allowed)))
    return {key: values[key] for key in allowed}


def count_map(value, allowed):
    require(set(value) == set(allowed) and all(type(v) is int and v >= 0 for v in value.values()), "Complete nonnegative counter")
    return dict(value)


def cross_counts(formations):
    return {status: {kind: sum(r["status"] == status and r["groupKind"] == kind for r in formations)
                     for kind in GROUP_KINDS} for status in HANDOFF}


def environment_values(row):
    result = {}
    for name, path in ENV_PATHS.items():
        value = row
        for part in path:
            require(isinstance(value, dict) and part in value, "Missing environment field: " + name)
            value = value[part]
        require(value is None or finite(value), "Invalid known environment value: " + name)
        result[name] = value
    return result


def stats(values):
    values = list(values)
    known = [value for value in values if value is not None]
    return {"knownDays": len(known), "unknownDays": len(values) - len(known),
            "mean": math.fsum(known) / len(known) if known else None,
            "minimum": min(known) if known else None, "maximum": max(known) if known else None}


def forecast_counts(rows):
    rows = list(rows)
    dated = [r for r in rows if r["forecastDate"] is not None]
    scored = sum(r["scoreStatus"] == "scored" for r in rows)
    return {"plannedSlots": len(rows), "scoredSlots": scored, "unscoredSlots": len(rows) - scored,
            "unassignedForecastSlots": len(rows) - len(dated),
            "eventCount": len({r["parentEventId"] for r in rows}),
            "eventDateCount": len({(r["parentEventId"], r["forecastDate"]) for r in dated}),
            "uniqueMemberDates": len({(r["memberSymbol"], r["forecastDate"]) for r in dated}),
            "forecastDayCount": len({r["forecastDate"] for r in dated}),
            "scoreStatusCounts": counter((r["scoreStatus"] for r in rows), SCORE)}


def prepare_group(start, end, metadata, daily34, events34, formations43, forecasts46, environment_daily):
    """Validate exact joins and build compact complete-calendar records, never fill missing rows."""
    calendar, targets = metadata["calendar"], metadata["targetDates"]
    require(calendar == sorted(set(calendar)) and targets == [d for d in calendar if start <= d <= end], "Complete source target calendar")
    require(bool(targets), "Nonempty target calendar")
    population = metadata["counts"]["sampleCount"]
    require(type(population) is int and population > 0, "Fixed source population")
    days = index_unique(daily34, "formationDate", "leader day")
    env = index_unique(environment_daily, "formationDate", "environment day")
    require(set(days) == set(targets) == set(env), "All source-calendar days must exist; absent is not zero")
    require([r["formationDate"] for r in daily34] == targets
            and [r["formationDate"] for r in environment_daily] == targets, "Original target-day order")
    events = index_unique(events34, "eventId", "leader event")
    formations = index_unique(formations43, "parentEventId", "43 formation")
    require(set(events) == set(formations), "Every original Top5 event joins one 43 formation")
    by_a, by_d, by_formation = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in formations43:
        require(row["formationDate"] in days, "Formation outside source calendar")
        require(row["status"] in HANDOFF and row["groupKind"] in GROUP_KINDS, "Formation enums")
        parent = events[row["parentEventId"]]
        for name in ("formationDate", "peerFormationDate", "anchorSymbol"):
            require(row[name] == parent[name], "Inherited event clock/identity")
        require(row["groupKind"] == parent["groupSummary"]["kind"], "Inherited group kind")
        if row["status"] == "formed":
            require(row["groupKind"] == "formed" and len(row["members"]) == 20, "Formed fixed group must have 20 members")
        by_formation[row["formationDate"]].append(row)
    require(len({r["rowId"] for r in forecasts46}) == len(forecasts46), "Unique forecast row IDs")
    forecast_design = defaultdict(set)
    target_index = {d: i for i, d in enumerate(targets)}
    for row in forecasts46:
        event = formations.get(row["parentEventId"])
        require(event is not None and event["status"] == "formed", "Only 43 formed events produce forecasts")
        require(row["formationDate"] == event["formationDate"] and row["peerFormationDate"] == event["peerFormationDate"], "Forecast formation identity")
        require(row["anchorSymbol"] == event["anchorSymbol"], "Forecast anchor identity")
        members = {m["symbol"] for m in event["members"]}
        require(len(members) == 20 and row["memberSymbol"] in members and type(row["offset"]) is int and 0 <= row["offset"] < 5, "Member/offset design")
        offset = target_index[row["formationDate"]] + row["offset"]
        expected_d = targets[offset] if offset < len(targets) else None
        expected_y = targets[offset + 1] if offset + 1 < len(targets) else None
        require(row["forecastDate"] == expected_d and row["targetDate"] == expected_y, "Exact source-slot forecast clock including censoring")
        require(row["scoreStatus"] in SCORE, "Forecast score status")
        require(expected_d is not None or row["scoreStatus"] == "forecastBeyondEnd", "Null forecast retained as censored")
        require(row["scoreStatus"] != "scored" or (expected_d is not None and expected_y is not None), "Scored forecast needs target")
        forecast_design[row["parentEventId"]].add((row["memberSymbol"], row["offset"]))
        by_a[row["formationDate"]].append(row)
        if expected_d is not None:
            by_d[expected_d].append(row)
    formed = {eid: f for eid, f in formations.items() if f["status"] == "formed"}
    require(set(forecast_design) == set(formed) and len(forecasts46) == len(formed) * 100
            and all(len(v) == 100 for v in forecast_design.values()), "Every formed event has 20 by 5 planned slots")
    calendar_index = {d: i for i, d in enumerate(calendar)}
    result = []
    all_event_ids = []
    for day in targets:
        raw, e, fs = days[day], env[day], by_formation[day]
        pos = calendar_index[day]
        require(raw["peerFormationDate"] == (calendar[pos - 10] if pos >= 10 else None), "Exact A/F source clock")
        activation = count_map(raw["activationCounts"], ACTIVATION)
        formation = count_map(raw["formationCounts"], FORMATION)
        require(sum(activation.values()) == population, "Activation denominator is all fixed symbols")
        require(sum(formation.values()) == activation["candidate"], "F denominator is only current candidates")
        require(len(raw["leaderCandidates"]) == activation["candidate"], "All current candidates retained")
        same(counter((r["formationStatus"] for r in raw["leaderCandidates"]), FORMATION), formation, "F candidate statuses")
        require(len(raw["leaderRanking"]) == formation["passed"], "Temporal ranking denominator")
        event_ids = raw["leaderEventIds"]
        require(len(event_ids) == len(set(event_ids)) and set(event_ids) == {r["parentEventId"] for r in fs}, "Daily exact Top5 event join")
        require(len(event_ids) == min(5, formation["passed"]), "Leader Top5 without backfill")
        all_event_ids.extend(event_ids)
        kinds = counter((r["groupKind"] for r in fs), GROUP_KINDS)
        lifecycle = counter((r["status"] for r in fs), HANDOFF)
        same(raw["counts"]["leaderGroupKinds"], kinds, "Group kind conservation")
        require(raw["counts"]["currentCandidateCount"] == activation["candidate"]
                and raw["counts"]["temporalCandidateCount"] == formation["passed"]
                and raw["counts"]["leaderEventCount"] == len(event_ids), "Parent funnel counts")
        require(pos >= 1 and e["asOfDate"] == calendar[pos - 1] and e["fixedPopulation"] == population,
                "Environment is same-cohort previous close, not same-day close")
        admission = {"symbolDayCount": population, "activationCounts": activation, "formationCounts": formation,
            "currentCandidateCount": activation["candidate"], "temporalCandidateCount": formation["passed"],
            "leaderEventCount": len(event_ids), "rankExcludedCount": formation["passed"] - len(event_ids),
            "groupKindCounts": kinds, "formation43StatusCounts": lifecycle,
            "formation43StatusByGroupKind": cross_counts(fs),
            "daysWithCurrentCandidates": int(activation["candidate"] > 0),
            "daysWithTemporalCandidates": int(formation["passed"] > 0),
            "daysWithLeaderEvents": int(bool(event_ids)), "zeroLeaderEventDays": int(not event_ids),
            "daysWith43Formed": int(lifecycle["formed"] > 0), "zero43FormedDays": int(lifecycle["formed"] == 0),
            "uniqueAnchors": len({r["anchorSymbol"] for r in fs})}
        result.append({"formationDate": day, "peerFormationDate": raw["peerFormationDate"],
            "environmentAsOfDate": e["asOfDate"], "fixedPopulation": population,
            "rankPopulations": deepcopy(raw["rankPopulations"]), "rankReasonCounts": deepcopy(raw["rankReasonCounts"]),
            "leaderAdmission": admission,
            "lineage": {"leaderEventIds": list(event_ids),
                "formedEventIds": [r["parentEventId"] for r in fs if r["status"] == "formed"],
                "anchorSymbols": sorted({r["anchorSymbol"] for r in fs})},
            "predictionByFormation": forecast_counts(by_a[day]), "predictionByForecast": forecast_counts(by_d[day]),
            "environment": environment_values(e)})
    require(len(all_event_ids) == len(set(all_event_ids)) == len(events), "All event dates accounted for")
    return result


def slices(period, start, end, atlas):
    yield "overallRows", "overall", "overall", "完整期间", start, end
    for phase in atlas["phases"]:
        a, b = max(start, phase["startDate"]), min(end, phase["endDate"])
        if a <= b:
            yield "phaseRows", "marketPhase", phase["id"], phase["title"], a, b
    if period == "2024-2025":
        yield "epochRows", "descriptiveEpoch", "before20240924", "9·24之前", start, "2024-09-23"
        yield "epochRows", "descriptiveEpoch", "from20240924", "9·24起", "2024-09-24", end


def select_forecasts(rows, field, start, end, overall=False):
    return [r for r in rows if overall or (r[field] is not None and start <= r[field] <= end)]


def boundary_counts(rows, start, end, overall=False):
    inside_a = lambda r: overall or start <= r["formationDate"] <= end
    inside_d = lambda r: r["forecastDate"] is not None and (overall or start <= r["forecastDate"] <= end)
    incoming = [r for r in rows if inside_d(r) and not inside_a(r)]
    return {"forecastSlotsFromEarlierFormation": len(incoming),
        "formationSlotsForecastAfterSlice": sum(inside_a(r) and r["forecastDate"] is not None and not inside_d(r) for r in rows),
        "formationSlotsNullForecast": sum(inside_a(r) and r["forecastDate"] is None for r in rows),
        "eventIdsFromEarlierFormation": sorted({r["parentEventId"] for r in incoming})}


def aggregate_slice(daily, forecasts, kind, start, end):
    selected = [d for d in daily if start <= d["formationDate"] <= end]
    admission = {name: sum(d["leaderAdmission"][name] for d in selected) for name in SUM_FIELDS}
    for name, allowed in (("activationCounts", ACTIVATION), ("formationCounts", FORMATION),
                          ("groupKindCounts", GROUP_KINDS), ("formation43StatusCounts", HANDOFF)):
        admission[name] = {key: sum(d["leaderAdmission"][name][key] for d in selected) for key in allowed}
    admission["formation43StatusByGroupKind"] = {status: {group: sum(d["leaderAdmission"]["formation43StatusByGroupKind"][status][group]
        for d in selected) for group in GROUP_KINDS} for status in HANDOFF}
    admission["uniqueAnchors"] = len({a for d in selected for a in d["lineage"]["anchorSymbols"]})
    overall = kind == "overall"
    return {"targetDays": len(selected), "observedStart": selected[0]["formationDate"] if selected else None,
        "observedEnd": selected[-1]["formationDate"] if selected else None,
        "leaderAdmission": admission,
        "predictionByFormation": forecast_counts(select_forecasts(forecasts, "formationDate", start, end, overall)),
        "predictionByForecast": forecast_counts(select_forecasts(forecasts, "forecastDate", start, end, overall)),
        "crossBoundary": boundary_counts(forecasts, start, end, overall),
        "environment": {name: stats(d["environment"][name] for d in selected) for name in ENV_PATHS}}


def reference_slice(metadata, raw_daily, formations, forecasts, environments, kind, start, end):
    """Separate aggregation path: source-level comprehensions, no joined-day counters."""
    ds = [r for r in raw_daily if start <= r["formationDate"] <= end]
    fs = [r for r in formations if start <= r["formationDate"] <= end]
    candidates = [c for d in ds for c in d["leaderCandidates"]]
    day_events = Counter(r["formationDate"] for r in fs)
    day_formed = Counter(r["formationDate"] for r in fs if r["status"] == "formed")
    a = {"symbolDayCount": len(ds) * metadata["counts"]["sampleCount"],
        "activationCounts": {s: sum(r["activationCounts"][s] for r in ds) for s in ACTIVATION},
        "formationCounts": counter((c["formationStatus"] for c in candidates), FORMATION),
        "currentCandidateCount": len(candidates),
        "temporalCandidateCount": sum(c["formationStatus"] == "passed" for c in candidates),
        "leaderEventCount": len(fs),
        "rankExcludedCount": sum(len(d["leaderRanking"]) - len(d["leaderEventIds"]) for d in ds),
        "groupKindCounts": counter((r["groupKind"] for r in fs), GROUP_KINDS),
        "formation43StatusCounts": counter((r["status"] for r in fs), HANDOFF),
        "formation43StatusByGroupKind": {s: {g: len([r for r in fs if r["status"] == s and r["groupKind"] == g]) for g in GROUP_KINDS} for s in HANDOFF},
        "daysWithCurrentCandidates": sum(bool(d["leaderCandidates"]) for d in ds),
        "daysWithTemporalCandidates": sum(bool(d["leaderRanking"]) for d in ds),
        "daysWithLeaderEvents": len(day_events), "zeroLeaderEventDays": len(ds) - len(day_events),
        "daysWith43Formed": len(day_formed), "zero43FormedDays": len(ds) - len(day_formed),
        "uniqueAnchors": len({r["anchorSymbol"] for r in fs})}
    def forecast_reference(selected):
        statuses = {s: len([r for r in selected if r["scoreStatus"] == s]) for s in SCORE}
        dated = [r for r in selected if r["forecastDate"] is not None]
        return {"plannedSlots": len(selected), "scoredSlots": statuses["scored"],
            "unscoredSlots": len(selected) - statuses["scored"], "unassignedForecastSlots": len(selected) - len(dated),
            "eventCount": len(set(r["parentEventId"] for r in selected)),
            "eventDateCount": len(set((r["parentEventId"], r["forecastDate"]) for r in dated)),
            "uniqueMemberDates": len(set((r["memberSymbol"], r["forecastDate"]) for r in dated)),
            "forecastDayCount": len(set(r["forecastDate"] for r in dated)), "scoreStatusCounts": statuses}
    overall = kind == "overall"
    a_rows = [r for r in forecasts if overall or start <= r["formationDate"] <= end]
    d_rows = [r for r in forecasts if overall or r["forecastDate"] is not None and start <= r["forecastDate"] <= end]
    incoming = [r for r in d_rows if not overall and r["formationDate"] < start]
    environment = {}
    es = [r for r in environments if start <= r["formationDate"] <= end]
    for name, path in ENV_PATHS.items():
        vals = []
        for e in es:
            v = e
            for p in path: v = v[p]
            if v is not None: vals.append(v)
        environment[name] = {"knownDays": len(vals), "unknownDays": len(es) - len(vals),
            "mean": math.fsum(vals) / len(vals) if vals else None,
            "minimum": min(vals) if vals else None, "maximum": max(vals) if vals else None}
    return {"targetDays": len(ds), "observedStart": ds[0]["formationDate"] if ds else None,
        "observedEnd": ds[-1]["formationDate"] if ds else None, "leaderAdmission": a,
        "predictionByFormation": forecast_reference(a_rows), "predictionByForecast": forecast_reference(d_rows),
        "crossBoundary": {"forecastSlotsFromEarlierFormation": len(incoming),
            "formationSlotsForecastAfterSlice": sum(r["forecastDate"] is not None and r["forecastDate"] > end for r in a_rows) if not overall else 0,
            "formationSlotsNullForecast": sum(r["forecastDate"] is None for r in a_rows),
            "eventIdsFromEarlierFormation": sorted(set(r["parentEventId"] for r in incoming))}, "environment": environment}


def focus_rows(key, focus_metadata, focus_daily, daily):
    parents = index_unique(daily, "formationDate", "joined focus parent")
    focus = index_unique(focus_daily, "formationDate", "focus day")
    require(set(focus) == set(focus_metadata["targetDates"]), "Full focal source dates")
    result = []
    for day in focus_metadata["targetDates"]:
        raw, parent = focus[day], parents[day]
        for name in ("activationCounts", "formationCounts"):
            same(raw[name], parent["leaderAdmission"][name], "Focused reconstructed parent " + name)
        for name in ("currentCandidateCount", "temporalCandidateCount", "leaderEventCount"):
            same(raw["counts"][name], parent["leaderAdmission"][name], "Focused reconstructed funnel")
        require(sum(raw["endToEndCounts"].values()) == parent["fixedPopulation"], "Complete focal end-to-end denominator")
        require(sum(raw["firstFailureCounts"]["current"].values()) == parent["fixedPopulation"]
                and sum(raw["firstFailureCounts"]["formationAll"].values()) == parent["fixedPopulation"]
                and sum(raw["firstFailureCounts"]["formationEvaluatedByParent"].values()) == parent["leaderAdmission"]["currentCandidateCount"],
                "Focal first-failure denominators are distinct")
        result.append({**key, **deepcopy(parent), "firstFailureCounts": deepcopy(raw["firstFailureCounts"]),
            "endToEndCounts": deepcopy(raw["endToEndCounts"]), "top20": deepcopy(raw["rs20Top20"])})
    return result


def summarize_group(key, start, end, metadata, daily34, events34, formations43, forecasts46, environment_daily, atlas,
                    focus_metadata=None, focus_daily=None):
    daily = prepare_group(start, end, metadata, daily34, events34, formations43, forecasts46, environment_daily)
    tables = defaultdict(list)
    for table, kind, sid, title, a, b in slices(key["period"], start, end, atlas):
        tables[table].append({**key, "kind": kind, "id": sid, "title": title,
            "requestedStart": a, "requestedEnd": b, **aggregate_slice(daily, forecasts46, kind, a, b)})
    cover = {**key, "fixedPopulation": metadata["counts"]["sampleCount"], "targetDays": len(daily),
        "leaderDays": len(daily34), "environmentDays": len(environment_daily), "missingJoinedDays": 0,
        "leaderEvents": len(events34), "formation43StatusCounts": counter((r["status"] for r in formations43), HANDOFF),
        "forecastSlots": len(forecasts46), "unassignedForecastSlots": sum(r["forecastDate"] is None for r in forecasts46),
        "conservationPassed": True}
    focus = focus_rows(key, focus_metadata, focus_daily, daily) if focus_metadata is not None else []
    return dict(tables), [{**key, **d} for d in daily], cover, focus


def inputs(out):
    plan, completion, proof = [read(out / name) for name in ("plan.json", "completion.json", "validation.json")]
    require(completion["passed"] is True and proof["passed"] is True, "Source reconstruction audit must pass")
    require(proof["planSha256"] == completion["planSha256"] == sha(out / "plan.json"), "Plan binding")
    require(proof["completionSha256"] == sha(out / "completion.json"), "Completion binding")
    require(proof["verifierSha256"] == plan["sourcePins"]["scripts/verify-leader-stage-coverage.py"], "Frozen source verifier")
    for name, expected in plan["sourcePins"].items():
        require(sha(ROOT / name) == expected, "Frozen source changed: " + name)
    require(len(plan["groups"]) == 6 and {(g["period"], g["board"]) for g in plan["groups"]}
            == {(p, b) for p in PERIODS for b in BOARDS}, "All six cohorts")
    require(completion["newAccounts"] == completion["reusedAccounts"] == 0, "No account diagnostic")
    require(len(completion["focusGroups"]) == 3 and {(g["period"], g["board"]) for g in completion["focusGroups"]}
            == {("2024-2025", b) for b in BOARDS}, "Three complete focal cohorts")
    pinned(plan["protocol"])
    return plan, completion, proof, read(pinned(plan["atlas"]))


SOURCE_FIELDS = ("lagMetadata", "lagDaily", "lagEvents", "handoffFormations", "predictionRows", "environmentDaily")


def source_group(group):
    values = {name: pinned(group[name]) for name in SOURCE_FIELDS}
    return (read(values["lagMetadata"]), records(values["lagDaily"]), records(values["lagEvents"]),
            records(values["handoffFormations"]), records(values["predictionRows"]), records(values["environmentDaily"]))


def build(out):
    out = Path(out)
    require(not (out / "readout.json").exists() and not (out / "daily-rows.jsonl.gz").exists(), "Do not overwrite completed readout")
    plan, completion, proof, atlas = inputs(out)
    focuses = {(g["period"], g["board"]): g for g in completion["focusGroups"]}
    tables, days, covers, focused = defaultdict(list), [], [], []
    for group in plan["groups"]:
        key = {k: group[k] for k in ("period", "board")}
        f = focuses.get((group["period"], group["board"]))
        fm = read(pinned(f["metadata"])) if f else None
        fd = records(pinned(f["dailyRows"])) if f else None
        t, d, c, fs = summarize_group(key, group["start"], group["end"], *source_group(group), atlas, fm, fd)
        for name, rows in t.items(): tables[name].extend(rows)
        days.extend(d); covers.append(c); focused.extend(fs)
    expected = {"overallRows": 6, "phaseRows": 48, "epochRows": 6}
    require({name: len(tables[name]) for name in expected} == expected, "All fixed phase slices")
    daily_path = out / "daily-rows.jsonl.gz"
    write_records(daily_path, days)
    result = {"schema": "leader-stage-coverage-readout-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "inputPins": {name: reference(out / file) for name, file in
                      (("plan", "plan.json"), ("completion", "completion.json"), ("sourceValidation", "validation.json"))},
        "sourceGroups": deepcopy(plan["groups"]), "focusSources": deepcopy(completion["focusGroups"]),
        "atlas": plan["atlas"], "protocol": plan["protocol"], "summarizerSha256": sha(__file__),
        **dict(tables), "coverageRows": covers, "focusRows": focused, "dailyRowsArtifact": reference(daily_path),
        "counts": {**expected, "coverageRows": len(covers), "focusRows": len(focused), "dailyRows": len(days),
            "leaderEvents": sum(c["leaderEvents"] for c in covers),
            "formedEvents": sum(c["formation43StatusCounts"]["formed"] for c in covers),
            "plannedForecastSlots": sum(c["forecastSlots"] for c in covers),
            "unassignedForecastSlots": sum(c["unassignedForecastSlots"] for c in covers),
            "focusTopRows": sum(len(f["top20"]) for f in focused)},
        "method": {"calendar": "Every complete source target day is retained. Missing input days are rejected, never manufactured as zero-event days.",
            "leaderAdmission": "A activation uses all fixed symbols; F pass/fail/unknown uses only A current candidates. Top5 and group/lock counts are event populations, not additive independent trials.",
            "predictionByFormation": "Design coverage for events formed on A in the slice, including all 20x5 slots and null future dates; not forecast-day coverage.",
            "predictionByForecast": "Forecast dates define coverage. Null dates remain in overall/unassigned only. The same event may occur in multiple phases.",
            "environment": "Original environment formationDate=A joins unchanged. Its observations are known at the previous source close, environmentAsOfDate=A-1. Means use only known daily values and retain known/unknown counts.",
            "environmentUnits": "Breadth, returns, coverage and retention use fractions; relative amount uses a ratio; count fields are numbers of symbols. No field is an account return.",
            "focus": "First-failure order is descriptive software accounting, not causal importance. F for non-current-candidates is reconstructed context; original F evaluation is a separate denominator.",
            "top20": "Focal raw RS20 display ranking only. It does not require full leadership eligibility and is not a new signal or list selected by future gains."},
        "limitations": read(pinned(plan["protocol"]))["limitations"],
        "newAccounts": 0, "reusedAccounts": 0, "newTradingCandidates": 0,
        "holdoutClaim": False, "targetAchieved": False}
    write_json(out / "readout.json", result)
    return {"passed": True, "readout": reference(out / "readout.json"), "counts": result["counts"]}


def verify(out):
    out = Path(out)
    require(not (out / "readout-validation.json").exists(), "Do not overwrite verification")
    plan, completion, proof, atlas = inputs(out)
    report = read(out / "readout.json")
    require(report["summarizerSha256"] == sha(__file__), "Exact summarizer identity")
    for name, file in (("plan", "plan.json"), ("completion", "completion.json"), ("sourceValidation", "validation.json")):
        same(report["inputPins"][name], reference(out / file), "Input pin")
    same(report["sourceGroups"], plan["groups"], "All source refs")
    same(report["focusSources"], completion["focusGroups"], "All focal refs")
    daily = records(pinned(report["dailyRowsArtifact"]))
    require(len({(r["period"], r["board"], r["formationDate"]) for r in daily}) == len(daily), "Unique complete day sidecar")
    checked = 0
    for group in plan["groups"]:
        key = {k: group[k] for k in ("period", "board")}
        md, raw, events, formations, forecasts, env = source_group(group)
        selected_daily = [r for r in daily if all(r[k] == v for k, v in key.items())]
        require([r["formationDate"] for r in selected_daily] == md["targetDates"], "Daily source order and all zeros")
        # Direct field checks bind the day sidecar, independent of its use in slice aggregation.
        raw_index, env_index = index_unique(raw, "formationDate", "raw day"), index_unique(env, "formationDate", "environment")
        for d in selected_daily:
            day = d["formationDate"]
            ref = reference_slice(md, raw, formations, forecasts, env, "day", day, day)
            same(d["leaderAdmission"], ref["leaderAdmission"], "Day admission")
            same(d["predictionByFormation"], ref["predictionByFormation"], "Day A design")
            same(d["predictionByForecast"], ref["predictionByForecast"], "Day forecast")
            same(d["environment"], environment_values(env_index[day]), "Unchanged environment values")
            require(d["environmentAsOfDate"] == env_index[day]["asOfDate"], "Previous-close environment clock")
            require(d["peerFormationDate"] == raw_index[day]["peerFormationDate"]
                    and d["fixedPopulation"] == md["counts"]["sampleCount"], "Daily F/population identity")
            fs = [r for r in formations if r["formationDate"] == day]
            same(d["lineage"], {"leaderEventIds": raw_index[day]["leaderEventIds"],
                 "formedEventIds": [r["parentEventId"] for r in fs if r["status"] == "formed"],
                 "anchorSymbols": sorted(set(r["anchorSymbol"] for r in fs))}, "Complete daily event identities")
            same(d["rankPopulations"], raw_index[day]["rankPopulations"], "Rank population provenance")
            same(d["rankReasonCounts"], raw_index[day]["rankReasonCounts"], "Rank reason provenance")
        for table, kind, sid, title, a, b in slices(group["period"], group["start"], group["end"], atlas):
            actual = [r for r in report[table] if all(r[k] == v for k, v in key.items()) and r["id"] == sid]
            require(len(actual) == 1, "Exactly one fixed slice")
            expected = {**key, "kind": kind, "id": sid, "title": title, "requestedStart": a, "requestedEnd": b,
                        **reference_slice(md, raw, formations, forecasts, env, kind, a, b)}
            same(actual[0], expected, "Independent slice " + sid)
            checked += 1
        cover = next(r for r in report["coverageRows"] if all(r[k] == v for k, v in key.items()))
        same(cover, {**key, "fixedPopulation": md["counts"]["sampleCount"], "targetDays": len(md["targetDates"]),
            "leaderDays": len(raw), "environmentDays": len(env), "missingJoinedDays": 0, "leaderEvents": len(events),
            "formation43StatusCounts": counter((r["status"] for r in formations), HANDOFF),
            "forecastSlots": len(forecasts), "unassignedForecastSlots": sum(r["forecastDate"] is None for r in forecasts),
            "conservationPassed": True}, "Independent full coverage")
    for f in completion["focusGroups"]:
        key = {k: f[k] for k in ("period", "board")}
        raw = records(pinned(f["dailyRows"]))
        selected = [r for r in report["focusRows"] if all(r[k] == v for k, v in key.items())]
        require([r["formationDate"] for r in selected] == read(pinned(f["metadata"]))["targetDates"], "All focal days")
        for actual, expected in zip(selected, raw):
            for name in ("firstFailureCounts", "endToEndCounts"):
                same(actual[name], expected[name], "Focal unchanged " + name)
            same(actual["top20"], expected["rs20Top20"], "Complete raw RS20 display")
            d = next(r for r in daily if all(r[k] == v for k, v in key.items()) and r["formationDate"] == actual["formationDate"])
            for name in d: same(actual[name], d[name], "Focal joins exact daily record")
    expected_counts = {"overallRows": 6, "phaseRows": 48, "epochRows": 6, "coverageRows": 6}
    for name, n in expected_counts.items(): require(len(report[name]) == report["counts"][name] == n, "Full matrix " + name)
    require(report["counts"]["dailyRows"] == len(daily) and report["counts"]["focusRows"] == len(report["focusRows"]), "All daily/focus rows")
    require(report["counts"]["focusTopRows"] == sum(len(r["top20"]) for r in report["focusRows"]), "All top display rows")
    for name, value in (("leaderEvents", sum(c["leaderEvents"] for c in report["coverageRows"])),
                        ("formedEvents", sum(c["formation43StatusCounts"]["formed"] for c in report["coverageRows"])),
                        ("plannedForecastSlots", sum(c["forecastSlots"] for c in report["coverageRows"])),
                        ("unassignedForecastSlots", sum(c["unassignedForecastSlots"] for c in report["coverageRows"]))):
        require(report["counts"][name] == value, "Full coverage total " + name)
    require(report["newAccounts"] == report["reusedAccounts"] == report["newTradingCandidates"] == 0
            and report["holdoutClaim"] is False and report["targetAchieved"] is False, "Descriptive scope")
    result = {"schema": "leader-stage-coverage-readout-validation-v1", "passed": True,
        "completedAt": datetime.now(timezone.utc).isoformat(), "planSha256": sha(out / "plan.json"),
        "completionSha256": sha(out / "completion.json"), "sourceValidationSha256": sha(out / "validation.json"),
        "readoutSha256": sha(out / "readout.json"), "dailyRowsArtifactSha256": sha(pinned(report["dailyRowsArtifact"])),
        "verifierSha256": sha(__file__), "counts": {**report["counts"], "checkedSlices": checked},
        "scope": "Separate source-level aggregation algorithm in this script; focal raw/rank reconstruction is covered by the independently implemented bound source audit."}
    write_json(out / "readout-validation.json", result)
    return result


def self_test():
    import unittest
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_leader_stage_coverage_readout")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    require(result.wasSuccessful(), "Readout synthetic tests")
    return {"passed": True, "checks": result.testsRun, "filesWritten": 0}


def main():
    parser = ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else verify(args.out) if args.verify else build(args.out), ensure_ascii=False))


if __name__ == "__main__":
    main()
