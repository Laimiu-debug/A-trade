"""Compare label membership and maturity without running another portfolio.

The two new feedback inputs share exactly the same isolated path labels. The
old actual-account bridge is deliberately separate: it changes execution as
well as membership and is not an identified causal capacity effect.
"""
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.research_online_feedback import build_online_feedback

DEFAULT = ROOT / "artifacts/strategy-search/independent-shadow-labels-v1"
SAMPLES = ("allExecutable", "originalEntered")
MODES = ("intercept", "ridge")
KEYS = ("period", "board", "exit")
LABEL_KEYS = ("formationId", "formationDate", "entryDate", "exitDate", "netReturn")
VERSION = "independent-shadow-labels-readout-v1"
CUT = "2024-09-24"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    with (gzip.open if str(path).endswith(".gz") else open)(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def records(path):
    with (gzip.open if str(path).endswith(".gz") else open)(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def distribution(values):
    values = sorted(v for v in values if v is not None)
    require(all(finite(v) for v in values), "Finite distribution values required")
    n = len(values)
    return {"count": n, "minimum": values[0] if n else None,
        "mean": math.fsum(v / n for v in values) if n else None,
        "median": (values[n // 2] if n % 2 else math.fsum((values[n // 2 - 1] / 2, values[n // 2] / 2))) if n else None,
        "maximum": values[-1] if n else None}


def population(labels, symbols):
    return {"episodes": len(labels), "formationDays": len({e["formationDate"] for e in labels}),
        "symbols": len({symbols[e["formationId"]] for e in labels}),
        "closed": sum(e["exitDate"] is not None for e in labels),
        "immatureAtCutoff": sum(e["exitDate"] is None for e in labels),
        "closedNetReturnPct": distribution(e["netReturn"] * 100 if e["netReturn"] is not None else None for e in labels)}


def normalize_paths(rows, schedule, old_labels, key):
    """Stream away heavy path evidence; retain every original opportunity."""
    expected = {s["signalId"]: s for s in schedule}
    require(len(expected) == len(schedule), "Duplicate original formation ID")
    old = {e["formationId"]: e for e in old_labels}
    require(len(old) == len(old_labels) and set(old) <= set(expected), "Old labels must be unique original entries")
    output, seen = [], set()
    for row in rows:
        fid = row["formationId"]
        require(fid in expected and fid not in seen, "Unknown or duplicate library formation")
        seen.add(fid)
        signal, path = expected[fid], row["path"]
        require(all(row[k] == key[k] for k in KEYS), "Library source/exit mismatch")
        require(row["formationDate"] == signal["signalDate"] and row["symbol"] == signal["symbol"]
                and path["signal"] == signal, "Original signal changed")
        require(path["exitPolicy"] == key["exit"], "Path exit policy changed")
        outcome, status, label = path["outcome"], path["status"], path["label"]
        require(status in ("closed", "open", "unfilled", "unresolved"), "Unknown path status")
        require(type(outcome["entered"]) is bool and type(row["originalEntered"]) is bool,
                "Explicit entered flags required")
        require(row["originalEntered"] == (fid in old) == (row["originalDisposition"]["filledQuantity"] > 0),
                "Original-entry mask must match audited actual shadow labels")
        require(outcome["labelEligible"] == outcome["fullyClosed"] == (status == "closed"),
                "Path closed status and maturity disagree")
        require(status != "unfilled" or not outcome["entered"], "Unfilled path cannot have an entry")
        require(status not in ("closed", "open") or outcome["entered"], "Closed/open path needs entry")
        if outcome["entered"]:
            require(isinstance(label, dict) and set(label) == set(LABEL_KEYS), "Exact five-field path label required")
            require(label["formationId"] == fid and label["formationDate"] == row["formationDate"]
                    and label["entryDate"] == outcome["entryDate"], "Label identity differs")
            require(finite(outcome["investedAmount"]) and outcome["investedAmount"] > 0,
                    "Entered path must have positive committed cash")
            if status == "closed":
                require(outcome["labelAvailableDate"] == outcome["finalExitDate"] == label["exitDate"],
                        "Delayed settlement is outside this final-exit-clock diagnostic")
                require(finite(label["netReturn"]) and label["netReturn"] == outcome["netReturn"]
                        and finite(outcome["netPnlAmount"])
                        and math.isclose(label["netReturn"], outcome["netPnlAmount"] / outcome["investedAmount"],
                                         rel_tol=1e-10, abs_tol=1e-12), "Closed decimal label does not reconcile")
            else:
                require(label["exitDate"] is None and label["netReturn"] is None
                        and outcome["netReturn"] is None, "Unmatured path cannot carry final label")
        else:
            require(label is None and outcome["entryDate"] is None, "Non-entry must not be a zero label")
        if fid in old:
            require(old[fid]["formationDate"] == row["formationDate"]
                    and old[fid]["entryDate"] == row["originalDisposition"]["entryDate"], "Old entry identity differs")
        output.append({"formationId": fid, "formationDate": row["formationDate"], "symbol": row["symbol"],
            "originalEntered": row["originalEntered"], "status": status, "label": deepcopy(label),
            "outcome": deepcopy(outcome)})
    require([r["formationId"] for r in output] == [s["signalId"] for s in schedule],
            "Library must preserve all original opportunities in order")
    return output


def compact_decision(row, symbols):
    labels, unknown, immature = (row[k] for k in ("trainingLabels", "labelXUnknown", "immature"))
    total = row["trainingSummary"]["weightSum"]
    symbol_weights = {}
    for label in labels:
        symbol_weights.setdefault(symbols[label["formationId"]], []).append(label["weight"] / total)
    result = {k: deepcopy(v) for k, v in row.items() if k not in ("trainingLabels", "labelXUnknown", "immature")}
    result["trainingPopulation"] = {"episodeCount": len(labels),
        "formationDayCount": len({r["formationDate"] for r in labels}), "symbolCount": len(symbol_weights),
        "netReturnPct": distribution(r["netReturn"] * 100 for r in labels),
        "normalizedEpisodeWeight": distribution(r["weight"] / total for r in labels),
        "maximumSymbolWeightShare": max((math.fsum(v) for v in symbol_weights.values()), default=None),
        "maximumFormationDayWeightShare": 1 / row["trainingSummary"]["formationDayCount"] if labels else None,
        "labelsDigest": digest(labels)}
    result["labelXUnknownPopulation"] = {"episodeCount": len(unknown),
        "formationDayCount": len({r["formationDate"] for r in unknown}),
        "symbolCount": len({symbols[r["formationId"]] for r in unknown}), "digest": digest(unknown)}
    result["immaturePopulation"] = {"episodeCount": len(immature),
        "formationDayCount": len({r["formationDate"] for r in immature}),
        "symbolCount": len({symbols[r["formationId"]] for r in immature}),
        "ageSlots": distribution(r["ageSlots"] for r in immature), "digest": digest(immature)}
    return result


def intervals(target_dates):
    require(bool(target_dates), "Nonempty target calendar required")
    spans = [{"id": "overall", "kind": "overall", "startDate": target_dates[0], "endDate": target_dates[-1]}]
    for year in sorted({d[:4] for d in target_dates}):
        dates = [d for d in target_dates if d[:4] == year]
        spans.append({"id": year, "kind": "calendarYear", "startDate": dates[0], "endDate": dates[-1]})
    if target_dates[0] < CUT <= target_dates[-1]:
        for name, dates in (("before20240924", [d for d in target_dates if d < CUT]),
                            ("from20240924", [d for d in target_dates if d >= CUT])):
            spans.append({"id": name, "kind": "descriptiveEpoch", "startDate": dates[0], "endDate": dates[-1]})
    return spans


def coverage(key, sample, daily, opportunity_counts):
    output = []
    for span in intervals([r["formationDate"] for r in daily]):
        rows = [r for r in daily if span["startDate"] <= r["formationDate"] <= span["endDate"]]
        fitted = [r for r in rows if r["models"]["ridge"]["status"] == "fitted"]
        nopp = sum(opportunity_counts[r["formationDate"]] for r in rows)
        fopp = sum(opportunity_counts[r["formationDate"]] for r in fitted)
        output.append({**key, "sample": sample, **span, "dayCount": len(rows),
            "fittedDays": len(fitted), "fallbackDays": len(rows) - len(fitted),
            "fittedDayPct": len(fitted) / len(rows) * 100,
            "firstFittedDate": fitted[0]["formationDate"] if fitted else None,
            "originalOpportunities": nopp, "fittedOriginalOpportunities": fopp,
            "fittedOriginalOpportunityPct": fopp / nopp * 100 if nopp else None,
            "fallbackReasons": dict(sorted(Counter(r["models"]["ridge"]["reason"] for r in rows
                if r["models"]["ridge"]["status"] == "fallback").items())),
            "models": {mode: {"fittedAllowedDays": sum(r["models"][mode]["allow"] for r in fitted),
                "fittedRejectedDays": sum(not r["models"][mode]["allow"] for r in fitted),
                "fittedPredictionPct": distribution(r["models"][mode]["prediction"] * 100 for r in fitted)} for mode in MODES},
            "support": {field: distribution(r["support"][field] for r in rows) for field in (
                "enteredByAsOfCount", "closedByAsOfCount", "closedBeforeWindowCount", "closedInWindowCount",
                "labelXUnknownCount", "eligibleEpisodeCount", "eligibleFormationDayCount", "immatureCount")},
            "eligibleSymbols": distribution(r["trainingPopulation"]["symbolCount"] for r in rows),
            "eligibleWeightedMeanReturnPct": distribution(r["trainingSummary"]["labelMean"] * 100
                if r["trainingSummary"]["labelMean"] is not None else None for r in rows),
            "maximumSymbolWeightShare": distribution(r["trainingPopulation"]["maximumSymbolWeightShare"] for r in rows),
            "maximumFormationDayWeightShare": distribution(r["trainingPopulation"]["maximumFormationDayWeightShare"] for r in rows),
            "maximumEpisodeWeight": distribution(r["trainingPopulation"]["normalizedEpisodeWeight"]["maximum"] for r in rows),
            "immatureFormationDays": distribution(r["immaturePopulation"]["formationDayCount"] for r in rows),
            "immatureSymbols": distribution(r["immaturePopulation"]["symbolCount"] for r in rows),
            "dailyMeanImmatureAgeSlots": distribution(r["immaturePopulation"]["ageSlots"]["mean"] for r in rows),
            "maximumImmatureAgeSlots": max((r["immaturePopulation"]["ageSlots"]["maximum"] or 0 for r in rows), default=0),
            "basis": "Days and original opportunities are separate denominators; distributions are daily snapshots, not independent observations. Epochs use formation T, features use T-1."})
    return output


def bridge_rows(paths, old_labels, calendar):
    old = {r["formationId"]: r for r in old_labels}
    indexes = {d: i for i, d in enumerate(calendar)}
    result = []
    for path in paths:
        fid = path["formationId"]
        if fid not in old:
            continue
        before, after = old[fid], path["label"]
        old_closed = before["exitDate"] is not None
        new_closed = after is not None and after["exitDate"] is not None
        category = ("oldEnteredNewNotEntered" if after is None else
                    ("oldClosed" if old_closed else "oldOpen") + ("NewClosed" if new_closed else "NewUnmatured"))
        result.append({"formationId": fid, "formationDate": path["formationDate"], "symbol": path["symbol"],
            "category": category, "pathStatus": path["status"], "oldActualLabel": deepcopy(before),
            "independentLabel": deepcopy(after), "sameEntryDate": before["entryDate"] == after["entryDate"] if after else None,
            "entryDeltaSlots": indexes[after["entryDate"]] - indexes[before["entryDate"]] if after else None,
            "sameFinalExitDate": before["exitDate"] == after["exitDate"] if old_closed and new_closed else None,
            "finalExitDeltaSlots": indexes[after["exitDate"]] - indexes[before["exitDate"]] if old_closed and new_closed else None,
            "netReturnDeltaPct": (after["netReturn"] - before["netReturn"]) * 100 if old_closed and new_closed else None,
            "independentInvestedAmount": path["outcome"]["investedAmount"]})
    require(len(result) == len(old), "Every old actual entry must remain in the bridge")
    return result


def daily_compare(key, a, b, old, opportunity_count):
    require(a["formationDate"] == b["formationDate"] == old["formationDate"]
            and a["asOfDate"] == b["asOfDate"] == old["asOfDate"], "Decision clock mismatch")
    require(a["currentFeatures"] == b["currentFeatures"] == old["currentFeatures"], "Current features changed")
    require(a["support"]["eligibleEpisodeCount"] >= b["support"]["eligibleEpisodeCount"]
            and a["support"]["eligibleFormationDayCount"] >= b["support"]["eligibleFormationDayCount"],
            "Independent originalEntered must be a subset of allExecutable")
    def contrast(candidate, control):
        cf, bf = (r["models"]["ridge"]["status"] == "fitted" for r in (candidate, control))
        return {"fitState": "bothFitted" if cf and bf else "candidateOnlyFitted" if cf else "controlOnlyFitted" if bf else "neitherFitted",
            "eligibleEpisodeDelta": candidate["support"]["eligibleEpisodeCount"] - control["support"]["eligibleEpisodeCount"],
            "eligibleFormationDayDelta": candidate["support"]["eligibleFormationDayCount"] - control["support"]["eligibleFormationDayCount"],
            "immatureDelta": candidate["support"]["immatureCount"] - control["support"]["immatureCount"],
            "models": {m: {"allowDiffers": candidate["models"][m]["allow"] != control["models"][m]["allow"],
                "bothFittedPredictionDeltaPct": (candidate["models"][m]["prediction"] - control["models"][m]["prediction"]) * 100 if cf and bf else None}
                for m in MODES}}
    same = contrast(a, b)
    require(same["fitState"] != "controlOnlyFitted", "Superset cannot lose the unchanged fitting support condition")
    return {**key, "formationDate": a["formationDate"], "asOfDate": a["asOfDate"],
        "originalOpportunities": opportunity_count,
        "sameLibrarySelection": same, "independentSubsetVsOldActual": contrast(b, old)}


def summarize_group(key, calendar, target_dates, environment_rows, path_rows, schedule, old_labels, old_decisions):
    """Pure group analysis. No source prices, accounts, files or signals."""
    require([r["formationDate"] for r in old_decisions] == target_dates, "Old decision days differ")
    paths = normalize_paths(path_rows, schedule, old_labels, key)
    symbols = {r["formationId"]: r["symbol"] for r in paths}
    opportunity_counts = Counter(r["formationDate"] for r in paths)
    require(set(opportunity_counts) <= set(target_dates), "Opportunity outside target dates")
    samples = {"allExecutable": [r["label"] for r in paths if r["label"] is not None],
               "originalEntered": [r["label"] for r in paths if r["label"] is not None and r["originalEntered"]]}
    daily, parameters = {}, None
    for sample in SAMPLES:
        value = build_online_feedback(calendar, target_dates, environment_rows, samples[sample])
        parameters = value["parameters"]
        daily[sample] = [compact_decision(r, symbols) for r in value["dailyRows"]]
        del value
    bridges = bridge_rows(paths, old_labels, calendar)
    comparisons = [daily_compare(key, a, b, old, opportunity_counts[a["formationDate"]])
        for a, b, old in zip(daily[SAMPLES[0]], daily[SAMPLES[1]], old_decisions)]
    cov = [r for sample in SAMPLES for r in coverage(key, sample, daily[sample], opportunity_counts)]
    bridge_summary = {"oldActualEntryCount": len(old_labels), "matchedIndependentEntries": len(samples["originalEntered"]),
        "missingIndependentEntries": len(old_labels) - len(samples["originalEntered"]),
        "categories": dict(sorted(Counter(r["category"] for r in bridges).items())),
        "sameEntryDate": sum(r["sameEntryDate"] is True for r in bridges),
        "differentEntryDate": sum(r["sameEntryDate"] is False for r in bridges),
        "bothClosed": sum(r["sameFinalExitDate"] is not None for r in bridges),
        "sameFinalExitDate": sum(r["sameFinalExitDate"] is True for r in bridges),
        "differentFinalExitDate": sum(r["sameFinalExitDate"] is False for r in bridges),
        "entryDeltaSlots": distribution(r["entryDeltaSlots"] for r in bridges),
        "finalExitDeltaSlots": distribution(r["finalExitDeltaSlots"] for r in bridges),
        "netReturnDeltaPct": distribution(r["netReturnDeltaPct"] for r in bridges)}
    summary = {**key, "originalOpportunities": len(paths), "pathStatusCounts": dict(sorted(Counter(r["status"] for r in paths).items())),
        "populations": {s: population(labels, symbols) for s, labels in samples.items()},
        "oldActualPopulation": population(old_labels, symbols), "bridge": bridge_summary,
        "dailyComparisons": {name: {"fitStates": dict(sorted(Counter(r[name]["fitState"] for r in comparisons).items())),
            "models": {m: {"allowDiffersDays": sum(r[name]["models"][m]["allowDiffers"] for r in comparisons),
                "bothFittedPredictionDeltaPct": distribution(r[name]["models"][m]["bothFittedPredictionDeltaPct"] for r in comparisons)}
                for m in MODES}} for name in ("sameLibrarySelection", "independentSubsetVsOldActual")}}
    old_compact = [compact_decision(r, symbols) for r in old_decisions]
    return {"groupReadout": summary, "coverageRows": cov,
        "oldActualCoverageRows": coverage(key, "oldActual", old_compact, opportunity_counts), "decisionLibraries": daily,
        "dailyComparisonRows": comparisons, "bridgeRows": [{**key, **r} for r in bridges], "parameters": parameters}


def build(directory=DEFAULT):
    directory = Path(directory).resolve()
    outputs = [directory / name for name in ("readout.json", "daily-comparison.jsonl.gz", "old-actual-bridge.jsonl.gz")]
    require(not any(p.exists() for p in outputs), "Readout outputs already exist; never overwrite evidence")
    inputs = {}
    def bind(item):
        path = ROOT / item["path"]
        require(sha(path) == item["sha256"], "Changed input: " + item["path"])
        inputs[item["path"]] = item["sha256"]
        return path
    def local(name):
        path = directory / name
        return bind({"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)})
    plan_path = local("plan.json"); plan = read(plan_path); plan_sha = sha(plan_path)
    for name, expected in plan["sourcePins"].items():
        bind({"path": name, "sha256": expected})
    self_name = Path(__file__).relative_to(ROOT).as_posix()
    if plan["sourcePins"].get(self_name) != sha(__file__):
        post_pins = read(local("readout-source-pins.json"))
        require(post_pins["planSha256"] == plan_sha and post_pins["sourcePins"].get(self_name) == sha(__file__),
                "Postprocessing code must be frozen before readout")
        for name, expected_sha in post_pins["sourcePins"].items():
            bind({"path": name, "sha256": expected_sha})
    parent_path = bind(plan["parentPlan"]); parent = read(parent_path)
    bind(plan["parentDelivery"])
    old_input_path = bind(plan["parentInputCompletion"]); old_inputs = read(old_input_path)
    old_proof = read(bind(plan["parentInputValidation"]))
    require(old_proof["passed"] is True and old_proof["planSha256"] == sha(parent_path)
            and old_proof["inputCompletionSha256"] == sha(old_input_path), "Parent inputs have no exact passing audit")
    library_path = local("library-completion.json"); library = read(library_path)
    proof_path = local("validation.json"); proof = read(proof_path)
    require(proof["passed"] is True and proof["planSha256"] == plan_sha
            and proof["libraryCompletionSha256"] == sha(library_path), "Independent path audit must pass first")
    require(library["planSha256"] == plan_sha and library["newCandidateAccounts"] == 0, "Library scope mismatch")
    require(old_inputs["planSha256"] == sha(parent_path), "Parent completion plan mismatch")
    for field in ("environment", "metadata", "baselineSchedules", "opportunities"):
        require(plan[field] == parent[field], "Parent input binding changed: " + field)
    old_groups = {tuple(g[k] for k in KEYS): g for g in old_inputs["groups"]}
    groups = library["groups"]
    identities = [tuple(g[k] for k in KEYS) for g in groups]
    expected = {(p, b, e) for p in ("2022-2023", "2024-2025") for b in ("main", "chinext", "star") for e in ("trail10", "volatilityTrail")}
    require(len(identities) == len(set(identities)) == 12 and set(identities) == set(old_groups) == expected, "Complete twelve groups required")
    totals, coverage_rows, old_coverage, comparisons, bridges, decisions, pending = [], [], [], [], [], [], []
    parameters = None
    for g in groups:
        key = {k: g[k] for k in KEYS}; p, b, e = (g[k] for k in KEYS); old = old_groups[(p, b, e)]
        md = read(bind(plan["metadata"][p][b])); env = list(records(bind(plan["environment"][p][b])))
        schedule = read(bind(plan["baselineSchedules"][p][b]))
        require(old["baselineSchedule"] == plan["baselineSchedules"][p][b], "Original schedule differs")
        labels = read(bind(old["shadow"])); old_daily = list(records(bind(old["decisions"])))
        value = summarize_group(key, md["calendar"], md["targetDates"], env, records(bind(g["paths"])), schedule, labels, old_daily)
        require(value["groupReadout"]["originalOpportunities"] == g["pathCount"], "Group path count differs")
        require(value["groupReadout"]["pathStatusCounts"] == g["counts"], "Group status counts differ")
        totals.append(value["groupReadout"]); coverage_rows.extend(value["coverageRows"])
        old_coverage.extend(value["oldActualCoverageRows"])
        comparisons.extend(value["dailyComparisonRows"]); bridges.extend(value["bridgeRows"])
        parameters = value["parameters"]
        artifacts = {**key, "samples": {}}
        for sample in SAMPLES:
            path = directory / p / b / e / (sample + ".decisions.jsonl.gz")
            require(not path.exists(), "Decision artifact exists; never overwrite")
            pending.append((path, [{**key, "sample": sample, **r} for r in value["decisionLibraries"][sample]]))
            artifacts["samples"][sample] = path
        decisions.append(artifacts)
    npaths = sum(r["originalOpportunities"] for r in totals)
    require(npaths == plan["expectedPaths"] == library["independentPathExecutions"] == proof["counts"]["paths"], "Complete path denominator differs")
    require(npaths == plan["expectedFormations"] * 2 and len(coverage_rows) == 96 and len(old_coverage) == 48,
            "Unexpected coverage matrix")
    require(all(sha(ROOT / name) == value for name, value in inputs.items()), "Inputs changed during summary")
    def artifact(path):
        return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}
    def lines(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "xt", encoding="utf-8", compresslevel=6) as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        return {**artifact(path), "count": len(rows), "format": "jsonl+gzip"}
    for path, rows in pending:
        lines(path, rows)
    for group in decisions:
        group["samples"] = {s: {**artifact(path), "count": next(len(rows) for file, rows in pending if file == path), "format": "jsonl+gzip"}
                             for s, path in group["samples"].items()}
    compare_ref = lines(outputs[1], comparisons); bridge_ref = lines(outputs[2], bridges)
    report = {"version": VERSION, "createdAt": datetime.now(timezone.utc).isoformat(), "planSha256": plan_sha,
        "libraryCompletionSha256": sha(library_path), "pathValidationSha256": sha(proof_path), "builderSha256": sha(__file__),
        "inputRefs": [{"path": k, "sha256": v} for k, v in sorted(inputs.items())], "parameters": parameters,
        "groupReadout": totals, "coverageRows": coverage_rows, "oldActualCoverageRows": old_coverage,
        "decisionArtifacts": decisions,
        "dailyComparisonRowsArtifact": compare_ref, "oldActualBridgeRowsArtifact": bridge_ref,
        "counts": {"groups": 12, "decisionLibraries": 24, "originalFormations": plan["expectedFormations"],
            "independentPaths": npaths, "feedbackDays": len(comparisons) * 2, "coverageRows": len(coverage_rows),
            "oldActualCoverageRows": len(old_coverage),
            "oldActualEntries": len(bridges), "newCandidateAccounts": 0, "newSignals": 0},
        "basis": {"mainComparison": "Same independent path library; only original actual-entry membership changes. Formation-day equal weighting is recalculated within each sample and exit window.",
            "oldActualBridge": "Old actual versus independent originalEntered changes executed capital, fees, quantities, capacity and sometimes exits; it is not a pure capacity or causal effect.",
            "clocks": "Formation T uses only A=T-1 and final exits in the last 126 source slots. Twenty complete-feature formation days and lambda=1 are unchanged. Descriptive 9.24 intervals use T.",
            "missing": "Original entries missing a new executable path stay in the bridge. Open/unresolved entered paths remain immature; non-entries never become zero labels.",
            "dependence": "Overlapping paths and repeat symbols are dependent; episode, formation-day and symbol counts are separate. Daily distribution snapshots repeatedly contain the same labels.",
            "notPerformance": "No candidate portfolio is run and overlapping path PnLs are not added into an investment return.",
            "scope": "2022-2025 frozen observed samples, repeatedly viewed development; no 2026, holdout or promotion claim."},
        "signalsGenerated": False, "candidateAccountExecutions": 0, "certified": False, "targetAchieved": False}
    with outputs[0].open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write("\n")
    return {"passed": True, "readout": artifact(outputs[0]), "counts": report["counts"]}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_independent_shadow_labels_readout.py")
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    require(result.wasSuccessful(), "Synthetic readout checks failed")
    return {"passed": True, "syntheticTests": result.testsRun, "realSourceReads": 0, "accountExecutions": 0, "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False, allow_nan=False), flush=True)
