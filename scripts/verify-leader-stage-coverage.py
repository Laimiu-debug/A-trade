"""Independent coverage-chain and focused original-leadership audit.

No new signal or account is generated. Only frozen raw/rank mathematics from the
old independent auditor is reused; the new extraction builder is never imported.
"""
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import gc
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/leader-stage-coverage-v1"
FAMILY = "leader-stage-coverage-v1"
DEPENDENCIES = {"scripts/verify-leader-lag-followers.py": "3e0244282a82701dcc942f532673c4e17493543cfa335f9e2459da6a7a33333c"}
_REFERENCE = None
FIELDS = ("meanStrengthPercentile20", "minimumStrengthPercentile20", "relativeReturn60Percentile",
          "closePathMaxDrawdown20", "signedPathEfficiency20")
CURRENT_FAILURES = ("unknown", *FIELDS, "breakout", "passed")
FORMATION_FAILURES = ("unknown", *FIELDS, "passed")
DISPOSITIONS = ("currentUnknown", "currentPredicateFailed", "currentNoBreakout", "formationUnknown",
                "formationPredicateFailed", "leaderRankExcluded", "selectedLeader")
COUNT_FIELDS = ("currentCandidateCount", "temporalCandidateCount", "leaderEventCount")
PARENT_FIELDS = ("formationDate", "peerFormationDate", "rankPopulations", "rankReasonCounts",
                 "activationCounts", "formationCounts", "leaderCandidates", "leaderRanking", "leaderEventIds")
BASIS = {
    "protocol": "docs/leader-stage-coverage-v1.json",
    "helper": {"path": "service/research_leader_lag.py", "sha256": "1c6168e7b56af0bff90dcda665ab88e9d4756f5da1b82d8b7b76b1e14bf1fc9e"},
    "priceBasisId": "raw-unadjusted", "benchmarkSymbol": "sh000300",
    "rankScope": "All fixed-cohort symbols; independent exact21/61-slot valid populations; no eligibility prefilter",
    "rankClock": "Earliest focus A minus29 source slots (earliest F minus19), through final focus A; raw windows retain earlier history",
    "formationLag": 10,
    "conditions": [{"field": f, "operator": op, "threshold": v} for f, op, v in
                   zip(FIELDS, (">=", ">=", ">=", "<=", ">="), (.90, .80, .90, .10, .35))],
    "activation": "A five conditions and strict close above preceding5 highs; F five conditions only",
    "firstFailure": "Unknown first, then first false predicate in frozen order, then A breakout; descriptive ordering, not causal importance",
    "masks": "Full true/false/unknown masks overlap across failed conditions; F excludes breakout",
    "formationPopulation": "F evidence is rebuilt for all symbols; formationEvaluatedByParent is true only for original A candidates",
    "rs20Top20": "At most20 known raw RS20 values descending, symbol ascending; no RS60 or leadership prefilter",
    "scope": "Fixed board-cohort historical development diagnostic; no trading account, causal leadership or investor-identity claim",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def same(actual, expected, label="root"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), label + ": keys differ")
        for key, value in expected.items():
            same(actual[key], value, label + "." + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ": length differs")
        for i, (left, right) in enumerate(zip(actual, expected)):
            same(left, right, f"{label}[{i}]")
    elif type(expected) is float:
        require(type(actual) in (int, float) and math.isfinite(actual) and
                math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12), label + ": numeric difference")
    else:
        require(type(actual) is type(expected) and actual == expected, label + ": literal difference")


def old_reference():
    global _REFERENCE
    if _REFERENCE is None:
        name, expected = next(iter(DEPENDENCIES.items()))
        path = ROOT / name
        require(sha(path) == expected, "Frozen independent rank/leadership helper changed")
        spec = importlib.util.spec_from_file_location("stage_coverage_old_independent_math", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _REFERENCE = module
    return _REFERENCE


def focus_states_reference(dataset, *, start, end):
    """Full-cohort causal ranks for A-29..A, retaining only required A/F states."""
    old = old_reference()
    source = old.source_reference(dataset, start, end)
    calendar, targets, symbols, items = (source[key] for key in ("calendar", "targetDates", "symbols", "indexed"))
    positions = {d: i for i, d in enumerate(calendar)}
    wanted = {positions[d] for d in targets}
    wanted.update(i - 10 for i in list(wanted) if i >= 10)
    first = max(0, positions[targets[0]] - 29)
    histories = {s: deque(maxlen=20) for s in symbols}
    saved, ranked = {}, []
    benchmark = items["__benchmark__"]
    for i in range(first, len(calendar)):
        rankmaps, values, populations, reasons = {}, {}, {}, {}
        for horizon in (20, 60):
            values[horizon] = {s: old.relative_value(items[s], benchmark, i, horizon) for s in symbols}
            rankmaps[horizon], size = old.rank_reference(values[horizon])
            population = [{"symbol": s, "value": values[horizon][s], "percentile": rankmaps[horizon][s]}
                          for s in symbols if s in rankmaps[horizon]]
            populations[str(horizon)] = {"count": size, "sha256": digest(population)}
            reasons[str(horizon)] = dict(sorted(Counter(reason for s in symbols
                if (reason := old.relative_reason(items[s], benchmark, i, horizon)) is not None).items()))
        ranked.append({"date": calendar[i], "populations": populations, "reasonCounts": reasons})
        states = {}
        for s in symbols:
            histories[s].append(rankmaps[20].get(s))
            if i in wanted:
                features, passed, breakout = old.strength_reference(items[s], benchmark, i, histories[s], rankmaps[60].get(s))
                state = {"features": features, "passed": passed, "breakout": breakout, "history": list(histories[s])}
                states[s] = {"relativeReturn20": values[20][s], "relativeReturn60": values[60][s],
                             "evidence": old.leadership_record(state, items[s], benchmark, i, calendar)}
        if i in wanted:
            saved[calendar[i]] = states
    return {"calendar": calendar, "targetDates": targets, "symbols": symbols, "rankRows": ranked, "states": saved}


def failure_reference(evidence, *, include_breakout):
    names = FIELDS + (("breakout",) if include_breakout else ())
    values = [None if evidence is None else evidence["breakout"] if n == "breakout"
              else evidence["predicates"][n] for n in names]
    require(all(v is None or type(v) is bool for v in values), "Invalid predicate type")
    mask = {"true": [n for n, v in zip(names, values) if v is True],
            "false": [n for n, v in zip(names, values) if v is False],
            "unknown": [n for n, v in zip(names, values) if v is None]}
    if evidence is None or evidence["status"] == "unknown" or any(v is None for v in values):
        return "unknown", mask
    return next((n for n, v in zip(names, values) if v is False), "passed"), mask


def counted(rows, key, domain):
    counts = Counter(r[key] for r in rows)
    require(not (counts.keys() - set(domain)), "Unexpected categorical value: " + key)
    return {name: counts[name] for name in domain}


def project_focus_reference(focus):
    """Project independently computed per-symbol predicates into the full funnel."""
    calendar, targets, symbols, states = (focus[k] for k in ("calendar", "targetDates", "symbols", "states"))
    position = {day: i for i, day in enumerate(calendar)}
    ranks = {r["date"]: r for r in focus["rankRows"]}
    daily, all_symbols = [], []
    for day in targets:
        f = calendar[position[day] - 10] if position[day] >= 10 else None
        current, prior = states[day], states.get(f)
        symbol_rows, candidates = [], []
        for symbol in symbols:
            a = current[symbol]["evidence"]
            p = prior[symbol]["evidence"] if prior is not None else None
            activation = "unknown" if a["status"] == "unknown" else "candidate" if a["status"] == "passed" and a["breakout"] else "notTriggered"
            evaluated = activation == "candidate"
            status = (p["status"] if p else "unknown") if evaluated else None
            reason = ("formationStateUnavailable" if p is None else
                      {"passed": "formationFiveConditionsPassed", "failed": "formationFiveConditionsFailed",
                       "unknown": "formationEvidenceUnknown"}[p["status"]]) if evaluated else None
            fa, ma = failure_reference(a, include_breakout=True)
            fp, mp = failure_reference(p, include_breakout=False)
            identity = {"formationDate": day, "peerFormationDate": f, "symbol": symbol}
            if evaluated:
                candidates.append({**identity, "currentEvidence": deepcopy(a), "formationEvidence": deepcopy(p),
                                   "formationStatus": status, "formationReason": reason,
                                   "originalLeaderRank": None, "selectedTop5": False})
            symbol_rows.append({**identity, "currentEvidence": deepcopy(a), "formationEvidence": deepcopy(p),
                "formationEvaluatedByParent": evaluated, "activation": activation,
                "formationStatus": status, "formationReason": reason, "originalLeaderRank": None,
                "selectedTop5": False, "currentFirstFailure": fa, "formationFirstFailure": fp,
                "failureMask": {"current": ma, "formation": mp}})
        ordered = sorted((r for r in candidates if r["formationStatus"] == "passed"),
                         key=lambda r: (-r["currentEvidence"]["values"][FIELDS[0]],
                                        -r["currentEvidence"]["values"][FIELDS[2]], r["symbol"]))
        rank_map = {r["symbol"]: i for i, r in enumerate(ordered, 1)}
        for row in candidates + symbol_rows:
            rank = rank_map.get(row["symbol"])
            row.update(originalLeaderRank=rank, selectedTop5=rank is not None and rank <= 5)
        for row in symbol_rows:
            a, p = row["currentFirstFailure"], row["formationFirstFailure"]
            if a == "unknown": disposition = "currentUnknown"
            elif a in FIELDS: disposition = "currentPredicateFailed"
            elif a == "breakout": disposition = "currentNoBreakout"
            elif p == "unknown": disposition = "formationUnknown"
            elif p != "passed": disposition = "formationPredicateFailed"
            elif row["selectedTop5"]: disposition = "selectedLeader"
            else: disposition = "leaderRankExcluded"
            row["endToEndDisposition"] = disposition
        top = sorted((s for s in symbols if current[s]["relativeReturn20"] is not None),
                     key=lambda s: (-current[s]["relativeReturn20"], s))[:20]
        daily.append({"formationDate": day, "peerFormationDate": f,
            "rankPopulations": deepcopy(ranks[day]["populations"]), "rankReasonCounts": deepcopy(ranks[day]["reasonCounts"]),
            "activationCounts": counted(symbol_rows, "activation", ("candidate", "notTriggered", "unknown")),
            "formationCounts": counted(candidates, "formationStatus", ("passed", "failed", "unknown")),
            "leaderCandidates": candidates,
            "leaderRanking": [{"symbol": r["symbol"], "originalLeaderRank": i, "selectedTop5": i <= 5} for i, r in enumerate(ordered, 1)],
            "leaderEventIds": [f"leaderLagFollowersV1:{day}:{r['symbol']}" for r in ordered[:5]],
            "counts": dict(zip(COUNT_FIELDS, (len(candidates), len(ordered), min(5, len(ordered))))),
            "rs20Top20": [{"symbol": s, "relativeReturn20": current[s]["relativeReturn20"],
                            "percentile20": current[s]["evidence"]["rankHistory20"][-1],
                            "currentEvidence": deepcopy(current[s]["evidence"]),
                            "formationEvidence": deepcopy(prior[s]["evidence"]) if prior else None} for s in top],
            "firstFailureCounts": {"current": counted(symbol_rows, "currentFirstFailure", CURRENT_FAILURES),
                "formationAll": counted(symbol_rows, "formationFirstFailure", FORMATION_FAILURES),
                "formationEvaluatedByParent": counted([r for r in symbol_rows if r["formationEvaluatedByParent"]], "formationFirstFailure", FORMATION_FAILURES)},
            "endToEndCounts": counted(symbol_rows, "endToEndDisposition", DISPOSITIONS)})
        all_symbols.extend(symbol_rows)
    return daily, all_symbols


def reference_build(dataset, saved_daily_rows, *, start, end):
    require(isinstance(saved_daily_rows, list), "Parent daily must be a list")
    dates = [r["formationDate"] for r in saved_daily_rows]
    require(dates == sorted(set(dates)) and all(d in set(dataset["calendar"]) for d in dates), "Invalid parent daily dates")
    focus = focus_states_reference(dataset, start=start, end=end)
    daily, symbols = project_focus_reference(focus)
    parent = {r["formationDate"]: r for r in saved_daily_rows}
    checks = []
    for row in daily:
        day = row["formationDate"]
        require(day in parent, "Missing focus parent day")
        for field in PARENT_FIELDS:
            require(digest(row[field]) == digest(parent[day][field]), "Parent daily differs: " + day + "." + field)
        for field in COUNT_FIELDS:
            require(row["counts"][field] == parent[day]["counts"][field], "Parent count mismatch")
        checks.append({"formationDate": day, "passed": True, "parentDailySha256": digest(parent[day]),
                       "checkedFields": list(PARENT_FIELDS) + ["counts." + k for k in COUNT_FIELDS]})
    counts = {"sampleCount": len(focus["symbols"]), "focusDayCount": len(daily),
              "rankDayCount": len(focus["rankRows"]), "symbolDayCount": len(symbols), "parentReconciledDayCount": len(checks),
              **{key: sum(r["counts"][key] for r in daily) for key in COUNT_FIELDS},
              "activationCounts": counted(symbols, "activation", ("candidate", "notTriggered", "unknown")),
              "formationCounts": counted([r for r in symbols if r["formationEvaluatedByParent"]], "formationStatus", ("passed", "failed", "unknown")),
              "firstFailureCounts": {"current": counted(symbols, "currentFirstFailure", CURRENT_FAILURES),
                  "formationAll": counted(symbols, "formationFirstFailure", FORMATION_FAILURES),
                  "formationEvaluatedByParent": counted([r for r in symbols if r["formationEvaluatedByParent"]], "formationFirstFailure", FORMATION_FAILURES)},
              "endToEndCounts": counted(symbols, "endToEndDisposition", DISPOSITIONS),
              "top20RowCount": sum(len(r["rs20Top20"]) for r in daily)}
    return {"metadata": {"version": FAMILY, "calendar": focus["calendar"], "targetDates": focus["targetDates"], "symbols": focus["symbols"],
                "focusStart": start, "focusEnd": end, "rankClockStart": focus["rankRows"][0]["date"],
                "rankClockDates": [r["date"] for r in focus["rankRows"]], "basis": deepcopy(BASIS),
                "newSignals": 0, "accountExecutions": 0, "targetAchieved": False},
            "rankRows": focus["rankRows"], "dailyRows": daily, "symbolRows": symbols, "counts": counts,
            "verification": {"passed": True, "parentDailyChecks": checks,
                "rankStreamComparison": "Required externally against frozen parent rank-evidence; complete rebuilt rows supplied"}}


def coverage_chain_reference(calendar, target_dates, sample_count, daily34, events34, formations43, rows46):
    """Check immutable full-period admission, locks and forecast identities.

    This does not recompute prices outside the focus. Prior independently audited
    bytes supply the upstream conditions; every downstream event/slot is joined.
    """
    old = old_reference()
    old.calendar_check(calendar)
    require(target_dates == sorted(set(target_dates)) and all(d in calendar for d in target_dates), "Target calendar changed")
    require(type(sample_count) is int and sample_count > 0, "Fixed cohort denominator required")
    require([r["formationDate"] for r in daily34] == target_dates, "Missing/duplicate full-period daily evidence")
    positions = {d: i for i, d in enumerate(calendar)}
    expected_events = []
    candidate_by_id = {}
    for row in daily34:
        a, at = row["formationDate"], positions[row["formationDate"]]
        f = calendar[at - 10] if at >= 10 else None
        require(row["peerFormationDate"] == f, "Daily F differs from A-10")
        require(set(row["activationCounts"]) == {"candidate", "notTriggered", "unknown"}
                and all(type(v) is int and v >= 0 for v in row["activationCounts"].values())
                and sum(row["activationCounts"].values()) == sample_count, "Activation denominator mismatch")
        candidates = row["leaderCandidates"]
        require(len({c["symbol"] for c in candidates}) == len(candidates) == row["activationCounts"]["candidate"], "Candidate identity/count mismatch")
        require(Counter(c["formationStatus"] for c in candidates) == Counter({k: v for k, v in row["formationCounts"].items() if v}),
                "Conditional F denominator mismatch")
        temporal = sorted((c for c in candidates if c["formationStatus"] == "passed"),
                          key=lambda c: (-c["currentEvidence"]["values"]["meanStrengthPercentile20"],
                                         -c["currentEvidence"]["values"]["relativeReturn60Percentile"], c["symbol"]))
        ranking = [{"symbol": c["symbol"], "originalLeaderRank": i, "selectedTop5": i <= 5}
                   for i, c in enumerate(temporal, 1)]
        same(row["leaderRanking"], ranking, "Original leader ranking")
        selected = temporal[:5]
        ids = [f"leaderLagFollowersV1:{a}:{c['symbol']}" for c in selected]
        same(row["leaderEventIds"], ids, "Original selected event identities")
        for name, count in (("currentCandidateCount", len(candidates)), ("temporalCandidateCount", len(temporal)), ("leaderEventCount", len(selected))):
            require(row["counts"][name] == count, "Daily upstream count mismatch")
        for rank, candidate in enumerate(selected, 1):
            require(candidate["originalLeaderRank"] == rank and candidate["selectedTop5"] is True, "Candidate rank drift")
            candidate_by_id[ids[rank - 1]] = candidate
        expected_events.extend(ids)
    require([e["eventId"] for e in events34] == expected_events and len(set(expected_events)) == len(expected_events), "Parent events not exact daily Top5")
    require([f["parentEventId"] for f in formations43] == expected_events, "43 formation join is not one-to-one")
    locks, formed = {}, []
    for event, formation in zip(events34, formations43):
        candidate = candidate_by_id[event["eventId"]]
        same(event["currentEvidence"], candidate["currentEvidence"], "Event current evidence")
        same(event["formationEvidence"], candidate["formationEvidence"], "Event F evidence")
        require(event["formationDate"] == candidate["formationDate"] and event["peerFormationDate"] == candidate["peerFormationDate"]
                and event["anchorSymbol"] == candidate["symbol"] and event["originalLeaderRank"] == candidate["originalLeaderRank"], "Event identity mismatch")
        group = event["groupSummary"]
        require(group["kind"] in ("formed", "incomplete", "unknown") and group["selectedCount"] == len(group["members"]), "Group kind/member denominator mismatch")
        require(len(group["members"]) == 20 if group["kind"] == "formed" else len(group["members"]) < 20, "Incomplete group invented members")
        anchor, at = event["anchorSymbol"], positions[event["formationDate"]]
        previous = locks.get(anchor)
        locked = previous is not None and at <= previous[1]
        state = "locked" if locked else "formed" if group["kind"] == "formed" else "notFormed"
        owner, through = previous if locked else (event["eventId"], at + 5)
        expected = {"parentEventId": event["eventId"], "formationDate": event["formationDate"],
                    "peerFormationDate": event["peerFormationDate"], "anchorSymbol": anchor,
                    "originalLeaderRank": event["originalLeaderRank"], "groupSha256": event["groupSha256"],
                    "groupKind": group["kind"], "members": group["members"], "status": state,
                    "lockOwnerEventId": owner, "lockThroughIndex": through, "createdMemberEpisodes": 20 if state == "formed" else 0}
        same(formation, expected, "Original43 lock and group chronology")
        if not locked:
            locks[anchor] = (owner, through)
        if state == "formed":
            formed.append(formation)
    by_row = {r["rowId"]: r for r in rows46}
    require(len(by_row) == len(rows46) == len(formed) * 100, "Prediction planned-slot conservation failed")
    expected_rows = []
    for event in formed:
        at = positions[event["formationDate"]]
        for member in event["members"]:
            fit_id = event["parentEventId"] + ":" + member["symbol"]
            fit_status = None
            for offset in range(5):
                row_id = fit_id + ":" + str(offset)
                require(row_id in by_row, "Missing member/offset slot")
                r = by_row[row_id]
                for field in ("parentEventId", "formationDate", "peerFormationDate", "anchorSymbol", "originalLeaderRank"):
                    require(r[field] == event[field], "Prediction event ancestry mismatch")
                require(r["fitId"] == fit_id and r["memberSymbol"] == member["symbol"] and r["frozenMemberRank"] == member["frozenMemberRank"]
                        and r["offset"] == offset, "Prediction member identity mismatch")
                forecast = calendar[at + offset] if at + offset < len(calendar) else None
                target = calendar[at + offset + 1] if at + offset + 1 < len(calendar) else None
                require(r["forecastDate"] == forecast and r["targetDate"] == target, "Forecast clock is not A..A+4")
                require(fit_status is None or fit_status == r["fitStatus"], "Fit changed after F")
                fit_status = r["fitStatus"]
                expected_rows.append(row_id)
    require([r["rowId"] for r in rows46] == expected_rows, "Prediction immutable order changed")
    return {"sourceSymbols": sample_count, "targetDays": len(target_dates), "parentLeaderEvents": len(events34),
            "formation43StatusCounts": {s: sum(f["status"] == s for f in formations43) for s in ("formed", "locked", "notFormed")},
            "formedEvents": len(formed), "memberEpisodes": len(formed) * 20, "plannedSlots": len(rows46),
            "unassignedForecastSlots": sum(r["forecastDate"] is None for r in rows46)}


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def ref(path):
    return {"path": relative(path), "sha256": sha(path)}


def bound(item):
    require(isinstance(item, dict) and isinstance(item.get("path"), str), "Artifact descriptor required")
    path = (ROOT / item["path"]).resolve()
    require(path.is_relative_to(ROOT) and sha(path) == item["sha256"], "Changed artifact: " + item["path"])
    return path


def write_once(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def pinned(plan, item):
    path = bound(item)
    require(plan["inputPins"].get(relative(path)) == item["sha256"], "Unregistered input: " + relative(path))
    return path


def registered_plan():
    plan = read(OUT / "plan.json")
    require(plan.get("schema") == FAMILY and plan.get("newAccounts") == plan.get("reusedAccounts") ==
            plan.get("newTradingCandidates") == 0 and plan.get("holdoutClaim") is False and
            plan.get("targetAchieved") is False, "Unexpected scope")
    for name in ("sourcePins", "inputPins"):
        require(isinstance(plan.get(name), dict), "Missing frozen hash table")
        for path, expected in plan[name].items():
            bound({"path": path, "sha256": expected})
    for path, expected in {**DEPENDENCIES, relative(__file__): sha(__file__),
                           "tests/test_leader_stage_coverage_verifier.py": sha(ROOT / "tests/test_leader_stage_coverage_verifier.py")}.items():
        require(plan["sourcePins"].get(path) == expected, "Auditor dependency not frozen: " + path)
    protocol = read(bound(plan["protocol"]))
    require(plan["sourcePins"].get(plan["protocol"]["path"]) == plan["protocol"]["sha256"] and
            protocol["family"] == FAMILY and protocol["previousCompletedBatch"] == 46, "Protocol identity mismatch")
    same(protocol["focalReconstruction"]["originalConditions"],
         [[r["field"], r["operator"], r["threshold"]] for r in BASIS["conditions"]], "Frozen predicates")
    for field in ("newTradingCandidates", "newAccounts", "reusedAccounts"):
        require(protocol["trialAccounting"][field] == 0, "Unexpected trial increment")
    bound(plan["previousProgram"])
    require(sha(OUT / "program-before.json") == plan["previousProgram"]["sha256"], "Program snapshot changed")
    focus = plan["focus"]
    same(focus, {"phaseId": "2024-09-policy-rally", "start": "2024-09-24", "end": "2024-10-08"}, "Fixed focus")
    atlas = read(pinned(plan, plan["atlas"]))
    phases = [p for p in atlas["phases"] if p["id"] == focus["phaseId"]]
    require(len(phases) == 1 and (phases[0]["startDate"], phases[0]["endDate"]) == (focus["start"], focus["end"]), "Atlas focus differs")
    expected = {(p, b) for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star")}
    require(len(plan["groups"]) == 6 and {(g["period"], g["board"]) for g in plan["groups"]} == expected, "Six distinct source groups required")
    for group in plan["groups"]:
        first, last = group["period"].split("-")
        require((group["start"], group["end"]) == (first + "-01-01", last + "-12-31"), "Period changed")
        for name in ("accountData", "lagMetadata", "lagDaily", "lagRanks", "lagEvents", "handoffFormations", "predictionRows", "environmentDaily"):
            pinned(plan, group[name])
    return plan


def inherited_reference(plan):
    """Bind each reused stream to its prior passing proof, not just a fresh hash."""
    base = ROOT / "artifacts/strategy-search"
    families = {"lag": "leader-lag-followers-v1", "handoff": "leader-amount-handoff-v1",
                "prediction": "leader-incremental-prediction-v1", "environment": "board-environment-context-v1"}
    receipts = {}
    def get(role, name):
        path = base / families[role] / name
        item = ref(path)
        pinned(plan, item)
        receipts[item["path"]] = item["sha256"]
        return read(path)
    lp, lc, lv = (get("lag", n) for n in ("plan.json", "input-completion.json", "input-validation.json"))
    hp, hc, hv, hd = (get("handoff", n) for n in ("plan.json", "input-completion.json", "input-validation.json", "delivery.json"))
    pp, pc, pv, pd, pr, prv = (get("prediction", n) for n in ("plan.json", "completion.json", "validation.json", "delivery.json", "readout.json", "readout-validation.json"))
    ep, ev = (get("environment", n) for n in ("plan.json", "validation.json"))
    for role, proof, completion in (("lag", lv, lc), ("handoff", hv, hc), ("prediction", pv, pc)):
        directory = base / families[role]
        require(proof["passed"] is True and proof["planSha256"] == sha(directory / "plan.json") == completion["planSha256"], "Prior proof plan mismatch")
        key, filename = ("completionSha256", "completion.json") if role == "prediction" else ("inputCompletionSha256", "input-completion.json")
        require(proof[key] == sha(directory / filename), "Prior completion proof mismatch")
    require(ev["passed"] is True and ev["planSha256"] == sha(base / families["environment"] / "plan.json"), "Environment proof changed")
    require(prv["passed"] is True and prv["sourceValidationSha256"] == sha(base / families["prediction"] / "validation.json") and
            prv["readoutSha256"] == sha(base / families["prediction"] / "readout.json") and
            prv["planSha256"] == pv["planSha256"] and prv["completionSha256"] == pv["completionSha256"], "Prediction readout proof mismatch")
    same(plan["previousDelivery"], ref(base / families["prediction"] / "delivery.json"), "Previous sealed delivery")
    inventories = {}
    for role, delivery, batch in (("prediction", pd, 46), ("handoff", hd, 43)):
        require(delivery["candidateTrialsAfter"] == batch, "Wrong parent batch number")
        inventory = {r["path"]: r["sha256"] for r in delivery["files"]}
        require(len(inventory) == len(delivery["files"]), "Duplicate sealed inventory")
        for name in (("plan.json", "completion.json", "validation.json", "readout.json", "readout-validation.json") if role == "prediction" else ("plan.json", "input-completion.json", "input-validation.json")):
            path = base / families[role] / name
            require(inventory.get(relative(path)) == sha(path), "Prior receipt not sealed")
        inventories[role] = inventory
    def index(rows):
        result = {(r["period"], r["board"]): r for r in rows}
        require(len(result) == len(rows) == 6, "Parent group domain changed")
        return result
    handoff, prediction, prediction_proofs, prediction_sources = map(index, (hc["groups"], pc["groups"], pv["groups"], pp["groups"]))
    for group in plan["groups"]:
        p, b = key = group["period"], group["board"]
        old_receipt = read(pinned(plan, lc["outputs"][p][b]))
        h, pred, predproof, predsource = handoff[key], prediction[key], prediction_proofs[key], prediction_sources[key]
        same(group["accountData"], old_receipt["accountData"], "Original cache")
        for expected in (h["accountData"], predsource["accountData"], predproof["accountData"], lp["sourceData"][p][b]["accountData"], ep["sourceData"][p][b]["accountData"]):
            same(group["accountData"], expected, "All sources share the same fixed cohort")
        for field, filename in (("lagMetadata", "signal-metadata.json"), ("lagDaily", "daily-evidence.jsonl.gz"),
                                ("lagRanks", "rank-evidence.jsonl.gz"), ("lagEvents", "leader-events.jsonl.gz")):
            same(group[field], old_receipt["files"][filename], "Parent34 receipt artifact")
            same(group[field], lv["inputArtifacts"][p][b][filename], "Parent34 audited artifact")
        require(lv["datasetPins"].get(group["accountData"]["path"]) == group["accountData"]["sha256"] and
                hv["datasetPins"].get(group["accountData"]["path"]) == group["accountData"]["sha256"], "Parent dataset proof mismatch")
        same(group["lagEvents"], h["parentLeaderEvents"], "Handoff leader ancestry")
        same(group["handoffFormations"], h["formations"], "Original handoff formations")
        for expected in (predsource["parentFormations"], predproof["parentFormations"]):
            same(group["handoffFormations"], expected, "Prediction consumes original43 formations")
        same(group["predictionRows"], pred["rows"], "Prediction completion rows")
        same(group["predictionRows"], predproof["rows"], "Prediction audited rows")
        same(group["environmentDaily"], ev["artifacts"][p][b]["daily.jsonl.gz"], "Audited environment artifact")
        for role, item in (("handoff", group["handoffFormations"]), ("prediction", group["predictionRows"])):
            require(inventories[role].get(item["path"]) == item["sha256"], "Inherited stream not sealed")
    return receipts


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Timezone-aware provenance required")
    return result


def lifecycle_reference(plan, tests, run, completion, hashes):
    require(tests.get("passed") is True and completion.get("passed") is True and
            completion.get("schema") == "leader-stage-coverage-completion-v1", "Failed extraction receipt")
    require(all(obj.get("planSha256") == hashes["plan"] for obj in (tests, run, completion)), "Receipt plan mismatch")
    require(run.get("preExecutionTestsSha256") == completion.get("preExecutionTestsSha256") == hashes["tests"] and
            completion.get("runStartSha256") == hashes["run"], "Execution lineage mismatch")
    require(plan.get("newAccounts") == plan.get("reusedAccounts") == run.get("newAccounts") ==
            completion.get("newAccounts") == completion.get("reusedAccounts") == 0, "Unexpected account execution")
    require(tests.get("realDataReads") == tests.get("accountExecutions") == 0, "Non-synthetic preregistration tests")
    checks = tests.get("checks")
    names = ("test_research_leader_stage_coverage.py", "test_leader_stage_coverage_verifier.py", "test_leader_stage_coverage_readout.py")
    require(isinstance(checks, list) and len(checks) == len(names), "Three actual pretest commands required")
    for check, name in zip(checks, names):
        require(check["exitCode"] == 0 and check["argv"][1:] == ["-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", name, "-v"]
                and "\nOK\n" in check["stderr"], "Unsuccessful or wrong pretest command")
    dates = [timestamp(obj[key]) for obj, key in ((plan, "registeredAt"), (tests, "completedAt"), (run, "startedAt"), (completion, "completedAt"))]
    require(dates == sorted(dates), "Tests/execution chronology mismatch")


def verify_group(dataset, saved_daily_rows, actual, *, start, end):
    expected = reference_build(dataset, saved_daily_rows, start=start, end=end)
    same(actual, expected, "Complete focus reconstruction")
    n = expected["counts"]["symbolDayCount"]
    require(n == expected["counts"]["sampleCount"] * expected["counts"]["focusDayCount"] and
            sum(expected["counts"]["endToEndCounts"].values()) == n, "Focus denominator mismatch")
    return expected["counts"]


def audit():
    require(not (OUT / "validation.json").exists(), "Never overwrite validation")
    plan = registered_plan()
    inherited = inherited_reference(plan)
    completion, tests, run = (read(OUT / name) for name in ("completion.json", "pre-execution-tests.json", "run-start.json"))
    hashes = {key: sha(OUT / name) for key, name in (("plan", "plan.json"), ("tests", "pre-execution-tests.json"), ("run", "run-start.json"), ("completion", "completion.json"))}
    lifecycle_reference(plan, tests, run, completion, hashes)
    focus_sources = [g for g in plan["groups"] if g["period"] == "2024-2025"]
    require([(g["period"], g["board"]) for g in completion["focusGroups"]] ==
            [(g["period"], g["board"]) for g in focus_sources], "Three focal groups/order required")
    completed_by_key = {(g["period"], g["board"]): g for g in completion["focusGroups"]}
    source_proofs, focus_proofs = [], []
    counts = {"sourceGroups": 0, "sourceDays": 0, "parentLeaderEvents": 0, "formedEvents": 0,
              "memberEpisodes": 0, "plannedSlots": 0, "focusGroups": 0, "focusDays": 0,
              "focusSymbolDays": 0, "focusRankDays": 0, "newAccounts": 0, "reusedAccounts": 0,
              "newTradingCandidates": 0}
    for source in plan["groups"]:
        key = source["period"], source["board"]
        meta = read(bound(source["lagMetadata"]))
        calendar, targets = meta["calendar"], meta["targetDates"]
        require(calendar == sorted(set(calendar)) and all(d <= source["end"] for d in calendar) and
                targets == [d for d in calendar if source["start"] <= d <= source["end"]], "Parent calendar/range mismatch")
        daily, events, forms, predictions, environment = (records(bound(source[name])) for name in
            ("lagDaily", "lagEvents", "handoffFormations", "predictionRows", "environmentDaily"))
        chain = coverage_chain_reference(calendar, targets, meta["counts"]["sampleCount"], daily, events, forms, predictions)
        require([r["formationDate"] for r in environment] == targets, "Environment target join mismatch")
        positions = {day: i for i, day in enumerate(calendar)}
        require(all(r["asOfDate"] == (calendar[positions[r["formationDate"]] - 1] if positions[r["formationDate"]] else None)
                    for r in environment), "Environment is not A-1")
        source_proofs.append({"period": key[0], "board": key[1], "counts": chain,
                              "sourceBindings": {name: source[name] for name in source if isinstance(source[name], dict)}})
        counts["sourceGroups"] += 1
        counts["sourceDays"] += chain["targetDays"]
        for name in ("parentLeaderEvents", "formedEvents", "memberEpisodes", "plannedSlots"):
            counts[name] += chain[name]
        if key in completed_by_key:
            group = completed_by_key[key]
            require((group["start"], group["end"]) == (plan["focus"]["start"], plan["focus"]["end"]), "Focus range changed")
            directory = OUT / key[0] / key[1]
            same(read(directory / "completion.json"), group, "Focal group receipt")
            files = {name: bound(group[name]) for name in ("metadata", "verification", "rankRows", "dailyRows", "symbolRows")}
            for name, path in files.items():
                require(path == directory / (name + (".json" if name in ("metadata", "verification") else ".jsonl.gz")), "Unexpected focal path")
            data = read(bound(source["accountData"]))
            require(data["calendar"] == calendar and len(data["securities"]) == chain["sourceSymbols"], "Raw/parent source identity mismatch")
            actual = {"counts": group["counts"], **{name: read(path) if name in ("metadata", "verification") else records(path) for name, path in files.items()}}
            focal_counts = verify_group(data, daily, actual, start=group["start"], end=group["end"])
            archived_ranks = records(bound(source["lagRanks"]))
            rank_dates = [r["date"] for r in archived_ranks]
            require(rank_dates == sorted(set(rank_dates)), "Duplicate parent rank dates")
            index = {r["date"]: r for r in archived_ranks}
            for row in actual["rankRows"]:
                require(row["date"] in index and digest(row) == digest(index[row["date"]]), "Exact original rank population digest differs")
            focus_proofs.append({"period": key[0], "board": key[1], "start": group["start"], "end": group["end"],
                                 "counts": focal_counts, "artifacts": {name: group[name] for name in files},
                                 "allSymbolFieldsRebuilt": True, "parentRanksExact": True})
            counts["focusGroups"] += 1
            counts["focusDays"] += focal_counts["focusDayCount"]
            counts["focusSymbolDays"] += focal_counts["symbolDayCount"]
            counts["focusRankDays"] += focal_counts["rankDayCount"]
            for name, path in files.items():
                require(sha(path) == group[name]["sha256"], "Focal output changed during audit")
            del data, actual, archived_ranks, index
        print(json.dumps({"verifiedGroup": list(key), "coverage": chain, "rawFocusRebuilt": key in completed_by_key}, ensure_ascii=False), flush=True)
        del daily, events, forms, predictions, environment
        gc.collect()
    require(counts["sourceGroups"] == 6 and counts["focusGroups"] == 3 and counts["sourceDays"] == 2907 and
            counts["parentLeaderEvents"] == 575 and counts["formedEvents"] == 316 and counts["memberEpisodes"] == 6320 and
            counts["plannedSlots"] == 31600, "Inherited whole-population conservation changed")
    registered_plan()
    for key, name in (("plan", "plan.json"), ("tests", "pre-execution-tests.json"), ("run", "run-start.json"), ("completion", "completion.json")):
        require(sha(OUT / name) == hashes[key], "Execution receipt changed")
    proof = {"passed": True, "verifiedAt": datetime.now(timezone.utc).isoformat(), "planSha256": hashes["plan"],
             "completionSha256": hashes["completion"], "preExecutionTestsSha256": hashes["tests"], "runStartSha256": hashes["run"],
             "verifierSha256": sha(__file__), "sourcePins": plan["sourcePins"], "inputPins": plan["inputPins"],
             "inheritedProofPins": inherited, "counts": counts, "sourceGroups": source_proofs, "focusGroups": focus_proofs,
             "checks": {"allOriginalDailyEventJoins": True, "inclusiveLocksNotExtended": True,
                        "allFormed20Times5DesignSlots": True, "allFocalRawSymbolPredicates": True,
                        "originalRankAndDailyReconciliation": True, "fullOverlappingFailureMasks": True,
                        "top20BeforeLeadershipEligibility": True, "environmentAsOfPreviousSession": True,
                        "zeroAccountsAndNoNewSignals": True},
             "numericTolerance": {"relative": 1e-10, "absolute": 1e-12}, "holdoutClaim": False, "targetAchieved": False}
    write_once(OUT / "validation.json", proof)
    return {"passed": True, "validation": ref(OUT / "validation.json"), "counts": counts}


def self_test():
    path = ROOT / "tests/test_leader_stage_coverage_verifier.py"
    spec = importlib.util.spec_from_file_location("stage_coverage_test_suite", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
    require(result.wasSuccessful(), "Independent synthetic tests failed")
    return {"passed": True, "syntheticTests": result.testsRun, "realSourceReads": 0, "accountExecutions": 0,
            "verifierSha256": sha(__file__), "testsSha256": sha(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False))
        return
    try:
        print(json.dumps(audit(), ensure_ascii=False))
    except Exception as error:
        if OUT.exists():
            attempts = OUT / "validation-attempts"
            attempts.mkdir(exist_ok=True)
            write_once(attempts / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"),
                       {"passed": False, "at": datetime.now(timezone.utc).isoformat(), "errorType": type(error).__name__,
                        "message": str(error), "traceback": traceback.format_exc(), "verifierSha256": sha(__file__)})
        raise


if __name__ == "__main__":
    main()
