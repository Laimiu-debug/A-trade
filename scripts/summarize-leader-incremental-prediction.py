"""Frozen, zero-account readout of paired next-session prediction errors.

Production aggregation nests means. The --verify path instead rebuilds losses from
predictions and uses explicit row weights; neither path imports the model builder.
"""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "artifacts/strategy-search/leader-incremental-prediction-v1"
PERIODS = ("2022-2023", "2024-2025")
BOARDS = ("main", "chinext", "star")
MODELS = ("baseline", "augmented", "zero")
LOSS_KEYS = tuple((model, loss) for model in MODELS for loss in ("squared", "absolute"))
STATUSES = {
    "fitStatus": ("known", "fitUnknown"),
    "featureStatus": ("known", "featureUnknown", "forecastBeyondEnd"),
    "predictionStatus": ("known", "fitUnknown", "featureUnknown", "forecastBeyondEnd", "numericUnknown"),
    "targetStatus": ("known", "targetUnknown", "targetBeyondEnd"),
    "scoreStatus": ("forecastBeyondEnd", "fitUnknown", "featureUnknown", "targetBeyondEnd", "targetUnknown", "numericUnknown", "scored"),
}
TIE = 1e-15
DEPENDENCIES = {}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def mean(values):
    values = list(values)
    return math.fsum(values) / len(values) if values else None


def vector(row):
    values = [row["errors"][model][loss] for model, loss in LOSS_KEYS]
    require(all(finite(value) and value >= 0 for value in values), "Scored losses must be finite and nonnegative")
    return values


def mean_vector(values):
    values = list(values)
    return [mean(items) for items in zip(*values)] if values else None


def metrics(values):
    out = {}
    for index, model in enumerate(MODELS):
        mse, mae = (values[index * 2:index * 2 + 2] if values is not None else (None, None))
        out.update({model + "MSE": mse, model + "MAE": mae,
                    model + "MSEBpsSquared": None if mse is None else mse * 1e8,
                    model + "MAEBps": None if mae is None else mae * 1e4})
    dmse = None if values is None else values[2] - values[0]
    dmae = None if values is None else values[3] - values[1]
    out.update({"deltaMSE": dmse, "deltaMAE": dmae,
                "improvementMSE": None if dmse is None else -dmse,
                "improvementMAE": None if dmae is None else -dmae,
                "deltaMSEBpsSquared": None if dmse is None else dmse * 1e8,
                "deltaMAEBps": None if dmae is None else dmae * 1e4,
                "improvementMSEBpsSquared": None if dmse is None else -dmse * 1e8,
                "improvementMAEBps": None if dmae is None else -dmae * 1e4,
                "relativeMSEImprovement": None if values is None or values[0] == 0 else -dmse / values[0]})
    return out


def status_counts(rows):
    result = {}
    for field, allowed in STATUSES.items():
        count = Counter(row[field] for row in rows)
        require(not (set(count) - set(allowed)), "Unknown " + field)
        result[field + "Counts"] = {name: count[name] for name in allowed}
    return result


def coverage(rows):
    scored = [r for r in rows if r["scoreStatus"] == "scored"]
    dated = [r for r in rows if r["forecastDate"] is not None]
    def unique(items, names):
        return len({tuple(row[name] for name in names) for row in items})
    return {"plannedSlots": len(rows), "scoredSlots": len(scored),
        "unscoredSlots": len(rows) - len(scored), "unassignedForecastDateSlots": len(rows) - len(dated),
        "eventCount": unique(rows, ("parentEventId",)), "scoredEventCount": unique(scored, ("parentEventId",)),
        "eventDateCount": unique(dated, ("parentEventId", "forecastDate")),
        "scoredEventDateCount": unique(scored, ("parentEventId", "forecastDate")),
        "uniqueMemberDateCount": unique(dated, ("memberSymbol", "forecastDate")),
        "scoredUniqueMemberDateCount": unique(scored, ("memberSymbol", "forecastDate")),
        "memberCount": unique(rows, ("memberSymbol",)), "anchorCount": unique(rows, ("anchorSymbol",)),
        "forecastDateCount": unique(dated, ("forecastDate",)), "scoredForecastDateCount": unique(scored, ("forecastDate",)),
        "crosses20240924Slots": sum(bool(r["crosses20240924"]) for r in rows),
        "scoredCrosses20240924Slots": sum(bool(r["crosses20240924"]) for r in scored),
        **status_counts(rows)}


def event_change_counts(event_vectors, planned_events):
    out = {}
    for name, base_index, aug_index in (("mse", 0, 2), ("mae", 1, 3)):
        counts = {"improved": 0, "worse": 0, "tie": 0, "unknown": len(planned_events) - len(event_vectors)}
        for value in event_vectors.values():
            delta = value[aug_index] - value[base_index]
            counts["tie" if abs(delta) <= TIE else "improved" if delta < 0 else "worse"] += 1
        out[name] = counts
    return out


def aggregate(rows):
    """Nested means on saved paired losses; no return, entry or fit selection."""
    rows = list(rows)
    scored = [r for r in rows if r["scoreStatus"] == "scored"]
    event_dates, member_dates = defaultdict(list), defaultdict(list)
    for row in scored:
        require(row["forecastDate"] is not None, "Scored row must have a forecast date")
        event_dates[(row["parentEventId"], row["forecastDate"])].append(vector(row))
        member_dates[(row["memberSymbol"], row["forecastDate"])].append(vector(row))
    by_event, by_date = defaultdict(list), defaultdict(list)
    for (event, _), values in event_dates.items():
        by_event[event].append(mean_vector(values))
    for (_, day), values in member_dates.items():
        by_date[day].append(mean_vector(values))
    event_vectors = {event: mean_vector(values) for event, values in by_event.items()}
    return {"coverage": coverage(rows),
        "primary": metrics(mean_vector(event_vectors.values())),
        "overlap": metrics(mean_vector(mean_vector(values) for values in by_date.values())),
        "rowWeighted": metrics(mean_vector(vector(row) for row in scored)),
        "eventImprovementCounts": event_change_counts(event_vectors, {r["parentEventId"] for r in rows})}


def independent_losses(row):
    """Independent path ignores saved error fields and recomputes six losses."""
    y = row["actualReturn"]
    require(finite(y), "Scored target must be finite")
    result = []
    for model in MODELS:
        prediction = 0.0 if model == "zero" else row["predictions"][model]
        require(finite(prediction), "Scored prediction must be finite")
        error = prediction - y
        result.extend((error * error, abs(error)))
    require(all(finite(v) for v in result), "Independent loss arithmetic must be finite")
    return result


def independent_aggregate(rows):
    """Different algorithm: explicit per-row weights, using original predictions."""
    eligible = [r for r in rows if r["scoreStatus"] == "scored"]
    if not eligible:
        return {name: metrics(None) for name in ("primary", "overlap", "rowWeighted")}
    event_day_n = Counter((r["parentEventId"], r["forecastDate"]) for r in eligible)
    event_days = defaultdict(set)
    date_members = defaultdict(set)
    member_day_n = Counter((r["memberSymbol"], r["forecastDate"]) for r in eligible)
    for row in eligible:
        event_days[row["parentEventId"]].add(row["forecastDate"])
        date_members[row["forecastDate"]].add(row["memberSymbol"])
    weighted = {name: [[] for _ in LOSS_KEYS] for name in ("primary", "overlap", "rowWeighted")}
    weights = defaultdict(list)
    for row in eligible:
        event, date, member = row["parentEventId"], row["forecastDate"], row["memberSymbol"]
        row_weights = {
            "primary": 1 / (len(event_days) * len(event_days[event]) * event_day_n[(event, date)]),
            "overlap": 1 / (len(date_members) * len(date_members[date]) * member_day_n[(member, date)]),
            "rowWeighted": 1 / len(eligible),
        }
        loss = independent_losses(row)
        for name, weight in row_weights.items():
            weights[name].append(weight)
            for index, value in enumerate(loss):
                weighted[name][index].append(weight * value)
    for name in weights:
        require(abs(math.fsum(weights[name]) - 1) < 1e-12, "Independent row weights sum to one")
    return {name: metrics([math.fsum(v) for v in columns]) for name, columns in weighted.items()}


def same(actual, expected, label="value"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label + " keys")
        for key in expected:
            same(actual[key], expected[key], label + "." + str(key))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + " length")
        for index, item in enumerate(expected):
            same(actual[index], item, label + "." + str(index))
    elif finite(expected):
        require(finite(actual) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-14), label + " numeric")
    else:
        require(actual == expected, label + " exact")


def validate_forecasts(formations, rows):
    formed = {r["parentEventId"]: r for r in formations if r["status"] == "formed"}
    require(len({r["parentEventId"] for r in formations}) == len(formations), "Unique inherited formations")
    require(len({r["rowId"] for r in rows}) == len(rows), "Unique planned forecast rows")
    require(len(rows) == len(formed) * 20 * 5, "Exactly 20 members by five planned slots per formed event")
    fits, event_members = defaultdict(list), defaultdict(set)
    for row in rows:
        require(row["parentEventId"] in formed, "Forecast belongs to a formed parent")
        parent = formed[row["parentEventId"]]
        require(all(row[name] == parent[name] for name in ("formationDate", "peerFormationDate", "anchorSymbol")), "F/A/anchor lineage")
        fits[row["fitId"]].append(row)
        event_members[row["parentEventId"]].add(row["memberSymbol"])
        if row["scoreStatus"] == "scored":
            same(vector(row), independent_losses(row), "Saved error vs raw prediction")
            same(row["errors"]["mseDelta"], row["errors"]["augmented"]["squared"] - row["errors"]["baseline"]["squared"], "Paired squared delta")
            same(row["errors"]["maeDelta"], row["errors"]["augmented"]["absolute"] - row["errors"]["baseline"]["absolute"], "Paired absolute delta")
        else:
            require(all(row["errors"][model][loss] is None for model, loss in LOSS_KEYS), "Unscored model losses remain null")
            require(row["errors"]["mseDelta"] is None and row["errors"]["maeDelta"] is None, "Unscored paired deltas remain null")
    require(all(len(v) == 20 for v in event_members.values()), "Twenty original unique members")
    require(len(fits) == len(formed) * 20, "Member episode conservation")
    for fit, items in fits.items():
        require(sorted(r["offset"] for r in items) == list(range(5)), "Five offsets per member episode")
        require(len({(r["parentEventId"], r["memberSymbol"], r["fitStatus"]) for r in items}) == 1, "Fixed fit per episode")
    return formed, fits


def slices(period, start, end, atlas):
    result = [("primaryRows", "overall", "overall", start, end)]
    for year in range(int(start[:4]), int(end[:4]) + 1):
        result.append(("yearRows", "year", str(year), max(start, f"{year}-01-01"), min(end, f"{year}-12-31")))
        for half, a, b in ((1, "01-01", "06-30"), (2, "07-01", "12-31")):
            result.append(("halfYearRows", "halfYear", f"{year}-H{half}", max(start, f"{year}-{a}"), min(end, f"{year}-{b}")))
    for phase in atlas["phases"]:
        a, b = max(start, phase["startDate"]), min(end, phase["endDate"])
        if a <= b:
            result.append(("phaseRows", "retrospectivePhase", phase["id"], a, b))
    if period == "2024-2025":
        result.extend((("epochRows", "descriptiveEpoch", "before20240924", start, "2024-09-23"),
                       ("epochRows", "descriptiveEpoch", "from20240924", "2024-09-24", end)))
    return result


def select_slice(rows, kind, start, end):
    return list(rows) if kind == "overall" else [r for r in rows if r["forecastDate"] is not None and start <= r["forecastDate"] <= end]


def summarize_group(key, start, end, formations, rows, metadata, source_counts, atlas):
    formed, fits = validate_forecasts(formations, rows)
    tables = defaultdict(list)
    for table, kind, sid, a, b in slices(key["period"], start, end, atlas):
        selected = select_slice(rows, kind, a, b)
        tables[table].append({**key, "kind": kind, "id": sid, "requestedStart": a, "requestedEnd": b, **aggregate(selected)})
    event_rows = []
    by_event = defaultdict(list)
    for row in rows:
        by_event[row["parentEventId"]].append(row)
    for event, formation in formed.items():
        event_rows.append({**key, "parentEventId": event, "formationDate": formation["formationDate"],
            "peerFormationDate": formation["peerFormationDate"], "anchorSymbol": formation["anchorSymbol"],
            **aggregate(by_event[event])})
    lifecycle = Counter(r["status"] for r in formations)
    fit_counts = Counter(items[0]["fitStatus"] for items in fits.values())
    calculated_counts = {"formations": len(formations),
        "formationStatusCounts": {s: lifecycle[s] for s in ("formed", "locked", "notFormed")},
        "fits": len(fits), "plannedRows": len(rows),
        "fitStatusCounts": {s: fit_counts[s] for s in ("known", "fitUnknown")},
        "rowStatusCounts": {field: {state: sum(r[field] == state for r in rows) for state in states}
                            for field, states in STATUSES.items()},
        "unassignedForecastDates": sum(r["forecastDate"] is None for r in rows),
        "crosses20240924": sum(bool(r["crosses20240924"]) for r in rows)}
    same(source_counts, calculated_counts, "Source counts independently conserved")
    cover = {**key, "sourceFormationCount": len(formations),
        "formationStatusCounts": {s: lifecycle[s] for s in ("formed", "locked", "notFormed")},
        "formedEvents": len(formed), "memberEpisodes": len(fits), "designedSlots": len(formed) * 100,
        "fitCounts": {s: fit_counts[s] for s in ("known", "fitUnknown")},
        "targetDayCount": len(metadata["targetDates"]), "coverage": coverage(rows), "sourceCounts": source_counts,
        "conservationPassed": sum(lifecycle.values()) == len(formations) and sum(fit_counts.values()) == len(fits)}
    return dict(tables), event_rows, cover


def inputs(out):
    plan_path, completion_path, proof_path = (out / name for name in ("plan.json", "completion.json", "validation.json"))
    plan, completion, proof = map(read, (plan_path, completion_path, proof_path))
    require(proof["passed"] is True and completion["passed"] is True, "Prediction source audit must pass")
    require(proof["planSha256"] == completion["planSha256"] == sha(plan_path), "Plan binding")
    require(proof["completionSha256"] == sha(completion_path), "Completion binding")
    require(proof["verifierSha256"] == plan["sourcePins"]["scripts/verify-leader-incremental-prediction.py"], "Frozen independent source verifier")
    for path, checksum in plan["sourcePins"].items():
        require(sha(ROOT / path) == checksum, "Frozen source changed: " + path)
    require({(r["period"], r["board"]) for r in completion["groups"]} == {(p, b) for p in PERIODS for b in BOARDS}
            and len(completion["groups"]) == 6, "All six cohorts")
    require(completion["newAccounts"] == completion["reusedAccounts"] == 0, "Zero account diagnostic")
    atlas = read(pinned(plan["atlas"]))
    pinned(plan["protocol"])
    return plan, completion, proof, atlas


def build(out):
    out = Path(out)
    require(not (out / "readout.json").exists(), "Do not overwrite a completed readout")
    plan, completion, proof, atlas = inputs(out)
    tables, events, covers = defaultdict(list), [], []
    source_refs = []
    for group in completion["groups"]:
        paths = {name: pinned(group[name]) for name in ("metadata", "formations", "fits", "rows")}
        rows = records(paths["rows"])
        group_tables, group_events, cover = summarize_group({k: group[k] for k in ("period", "board")},
            group["start"], group["end"], records(paths["formations"]), rows, read(paths["metadata"]), group["counts"], atlas)
        for name, values in group_tables.items(): tables[name].extend(values)
        events.extend(group_events); covers.append(cover)
        source_refs.append({"period": group["period"], "board": group["board"], **{name: group[name] for name in paths}})
    expected = {"primaryRows": 6, "yearRows": 12, "halfYearRows": 24, "phaseRows": 48, "epochRows": 6}
    require({k: len(tables[k]) for k in expected} == expected, "Complete fixed segment matrix")
    event_path = out / "event-rows.jsonl.gz"
    require(not event_path.exists(), "Do not overwrite event evidence")
    write_records(event_path, events)
    result = {"schema": "leader-incremental-prediction-readout-v1", "completedAt": datetime.now(timezone.utc).isoformat(),
        "inputPins": {name: reference(out / filename) for name, filename in
            (("plan", "plan.json"), ("completion", "completion.json"), ("sourceValidation", "validation.json"))},
        "atlas": plan["atlas"], "protocol": plan["protocol"], "predictionSources": source_refs,
        "summarizerSha256": sha(__file__), "newAccounts": 0, "reusedAccounts": 0,
        **dict(tables), "coverageRows": covers, "eventRowsArtifact": reference(event_path),
        "counts": {**expected, "coverageRows": len(covers), "eventRows": len(events),
            "sourceFormations": sum(r["sourceFormationCount"] for r in covers),
            "memberEpisodes": sum(r["memberEpisodes"] for r in covers), "designedSlots": sum(r["designedSlots"] for r in covers),
            "scoredSlots": sum(r["coverage"]["scoredSlots"] for r in covers)},
        "method": {"primary": "Equal scored members within event/date, equal available dates within event, equal available events within cohort.",
            "overlap": "Average losses across repeated event views for each member/date; equal unique members within date and equal dates. Predictions are never averaged before losses.",
            "rowWeighted": "Descriptive scored-row means only, not independent replications.",
            "date": "Every slice uses forecastDate, not formationDate or targetDate. Null forecast dates remain only in overall and unassigned counts.",
            "empty": "All planned events/slots retained in coverage; no scored support gives null metrics. Unknowns are never zero outcomes.",
            "units": "MSE/MAE are decimal-return squared/decimal return; explicit BpsSquared/Bps fields multiply by 1e8/1e4. Delta is augmented minus baseline; improvement is its negative. Relative improvement is a fraction, null at baseline MSE zero.",
            "eventTieTolerance": TIE, "eventTieMeaning": "Absolute decimal-loss numerical tie only, not an economic threshold.",
            "sliceWeights": "Weights are recomputed within each slice; event counts and averages are nonadditive across slices."},
        "limitations": read(pinned(plan["protocol"]))["limitations"],
        "holdoutClaim": False, "targetAchieved": False}
    write_json(out / "readout.json", result)
    return {"passed": True, "readout": reference(out / "readout.json"), "counts": result["counts"]}


def verify(out):
    out = Path(out)
    require(not (out / "readout-validation.json").exists(), "Do not overwrite a verification receipt")
    plan, completion, proof, atlas = inputs(out)
    report = read(out / "readout.json")
    require(report["summarizerSha256"] == sha(__file__), "Exact frozen summarizer")
    for name, filename in (("plan", "plan.json"), ("completion", "completion.json"), ("sourceValidation", "validation.json")):
        same(report["inputPins"][name], reference(out / filename), "Readout input pin")
    all_events = records(pinned(report["eventRowsArtifact"]))
    event_index = {(r["period"], r["board"], r["parentEventId"]): r for r in all_events}
    checked = 0
    for group in completion["groups"]:
        key = {k: group[k] for k in ("period", "board")}
        rows, formations = records(pinned(group["rows"])), records(pinned(group["formations"]))
        formed, fits = validate_forecasts(formations, rows)
        for table, kind, sid, start, end in slices(group["period"], group["start"], group["end"], atlas):
            matches = [r for r in report[table] if all(r[k] == v for k, v in key.items()) and r["id"] == sid]
            require(len(matches) == 1, "Unique complete slice")
            selected = select_slice(rows, kind, start, end)
            row = matches[0]
            expected = independent_aggregate(selected)
            for mode in expected: same(row[mode], expected[mode], table + "." + sid + "." + mode)
            same(row["coverage"], coverage(selected), "Slice coverage")
            event_values = {}
            for event in {r["parentEventId"] for r in selected}:
                subset = [r for r in selected if r["parentEventId"] == event]
                event_metrics = independent_aggregate(subset)["primary"]
                if event_metrics["baselineMSE"] is not None:
                    event_values[event] = [event_metrics[model + loss] for model in MODELS for loss in ("MSE", "MAE")]
            same(row["eventImprovementCounts"], event_change_counts(event_values, {r["parentEventId"] for r in selected}), "Event improvement counts")
            checked += 1
        for event in formed:
            row = event_index[(group["period"], group["board"], event)]
            require(all(row[name] == formed[event][name] for name in ("formationDate", "peerFormationDate", "anchorSymbol")), "Event-sidecar F/A/anchor identity")
            selected = [r for r in rows if r["parentEventId"] == event]
            for mode, value in independent_aggregate(selected).items(): same(row[mode], value, "Event metrics")
            same(row["coverage"], coverage(selected), "Event coverage")
        cover = next(r for r in report["coverageRows"] if all(r[k] == v for k, v in key.items()))
        same(cover["coverage"], coverage(rows), "Full coverage")
        require(cover["formedEvents"] == len(formed) and cover["memberEpisodes"] == len(fits)
            and cover["sourceFormationCount"] == len(formations), "Formation/member denominator")
        same(cover["sourceCounts"], group["counts"], "Original source count evidence")
        same(cover["formationStatusCounts"], {s: sum(r["status"] == s for r in formations)
             for s in ("formed", "locked", "notFormed")}, "All formation statuses")
        same(cover["fitCounts"], {s: sum(items[0]["fitStatus"] == s for items in fits.values())
             for s in ("known", "fitUnknown")}, "Unique member fit statuses")
        require(cover["designedSlots"] == len(formed) * 100
             and cover["targetDayCount"] == len(read(pinned(group["metadata"]))["targetDates"])
             and cover["conservationPassed"] is True, "Full source/target denominators")
    expected_counts = {"primaryRows": 6, "yearRows": 12, "halfYearRows": 24, "phaseRows": 48, "epochRows": 6, "coverageRows": 6}
    for key, size in expected_counts.items(): require(len(report[key]) == report["counts"][key] == size, "Full table count " + key)
    require(len(event_index) == len(all_events) == report["counts"]["eventRows"], "Complete unique event sidecar")
    same(report["predictionSources"], [{"period": g["period"], "board": g["board"],
         **{name: g[name] for name in ("metadata", "formations", "fits", "rows")}} for g in completion["groups"]], "All forecast-sidecar refs")
    require(report["newAccounts"] == report["reusedAccounts"] == 0
            and report["holdoutClaim"] is False and report["targetAchieved"] is False, "Zero-account development diagnostic")
    result = {"schema": "leader-incremental-prediction-readout-validation-v1", "passed": True,
        "completedAt": datetime.now(timezone.utc).isoformat(), "planSha256": sha(out / "plan.json"),
        "completionSha256": sha(out / "completion.json"), "sourceValidationSha256": sha(out / "validation.json"),
        "readoutSha256": sha(out / "readout.json"), "eventRowsArtifactSha256": sha(pinned(report["eventRowsArtifact"])),
        "verifierSha256": sha(__file__), "counts": {**report["counts"], "checkedSlices": checked, "newAccounts": 0},
        "checks": ["Saved row losses equal independently recomputed prediction/target losses.",
            "Explicit per-row weights reproduce all three aggregation paths and all fixed slices.",
            "All formed events, planned offsets, missing statuses and empty slices retained.",
            "Tuple-key diagnostic labels use str(key)."],
        "scope": "Independent aggregation algorithm in the same frozen readout script; raw-return/model reconstruction is separately bound by source validation."}
    write_json(out / "readout-validation.json", result)
    return result


def self_test():
    import unittest
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_leader_incremental_prediction_readout")
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
