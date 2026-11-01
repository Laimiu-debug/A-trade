"""Read audited full accounts and evaluate frozen forecasts on independent paths.

This module never fits a model, selects a schedule, runs an account, or changes a
decision using a future outcome. Forecast scores are retrospective descriptions.
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
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT = ROOT / "artifacts/strategy-search/all-opportunity-feedback-v1"
HELPER = ROOT / "scripts/summarize-online-feedback-entry.py"
spec = importlib.util.spec_from_file_location("all_opportunity_account_math", HELPER)
ACCOUNT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ACCOUNT)
MATH = ACCOUNT.MATH
require, finite, close, total = ACCOUNT.require, ACCOUNT.finite, ACCOUNT.close, ACCOUNT.total
sha, digest, read = ACCOUNT.sha, ACCOUNT.digest, ACCOUNT.read
PERIODS, BOARDS, EXITS = ACCOUNT.PERIODS, ACCOUNT.BOARDS, ACCOUNT.EXITS
POPULATIONS = ("originalEntered", "allExecutable")
MODELS = ("intercept", "ridge")
CANDIDATES = ("baseline", *(p + "__" + m for p in POPULATIONS for m in MODELS))
PAIR_CONTRASTS = (
    ("allVsOriginalIntercept", "originalEntered__intercept", "allExecutable__intercept", "population"),
    ("allVsOriginalRidge", "originalEntered__ridge", "allExecutable__ridge", "population"),
    ("ridgeVsInterceptOriginal", "originalEntered__intercept", "originalEntered__ridge", "model"),
    ("ridgeVsInterceptAll", "allExecutable__intercept", "allExecutable__ridge", "model"),
)
ACCOUNT_CONTRASTS = (*PAIR_CONTRASTS, *((c + "VsBaseline", "baseline", c, "baseline") for c in CANDIDATES[1:]))
FIT_CELLS = ("bothFitted", "controlOnlyFitted", "candidateOnlyFitted", "neitherFitted")
PATH_STATES = ("closed", "open", "unfilled", "unresolved")
CUT = "2024-09-24"


def records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def candidate_identity(candidate):
    require(candidate in CANDIDATES, "Unknown candidate identity")
    if candidate == "baseline":
        return {"candidate": candidate, "population": None, "model": None}
    population, model = candidate.split("__")
    return {"candidate": candidate, "population": population, "model": model}


def normalize_evaluation_paths(rows, original, key, cutoff):
    """Discard heavy evidence only after preserving every audited opportunity."""
    expected = {r["signalId"]: r for r in original}
    require(len(expected) == len(original), "Duplicate original signal")
    output = []
    for row in rows:
        fid, path = row["formationId"], row["path"]
        require(fid in expected and all(row[k] == key[k] for k in ("period", "board", "exit")), "Path identity mismatch")
        signal = expected[fid]
        require(path["signal"] == signal and path["exitPolicy"] == key["exit"]
                and row["formationDate"] == signal["signalDate"] and row["symbol"] == signal["symbol"], "Path formation changed")
        status, outcome, label = path["status"], path["outcome"], path["label"]
        require(status in PATH_STATES and type(outcome["entered"]) is bool
                and type(row["originalEntered"]) is bool, "Unknown path status or membership")
        require(outcome["labelEligible"] == outcome["fullyClosed"] == (status == "closed"), "Path maturity differs")
        require(status != "unfilled" or not outcome["entered"], "Unfilled cannot be entered")
        require(status not in ("closed", "open") or outcome["entered"], "Closed/open needs entry")
        if outcome["entered"]:
            require(isinstance(label, dict) and label["formationId"] == fid
                    and label["formationDate"] == row["formationDate"]
                    and label["entryDate"] == outcome["entryDate"] > row["formationDate"], "Label entry identity differs")
        else:
            require(label is None and outcome["entryDate"] is None, "Unfilled is not a zero label")
        if status == "closed":
            require(finite(label["netReturn"]) and label["entryDate"] < label["exitDate"] <= cutoff
                    and label["exitDate"] == outcome["finalExitDate"] == outcome["labelAvailableDate"]
                    and outcome["netReturn"] == label["netReturn"]
                    and finite(outcome["investedAmount"]) and outcome["investedAmount"] > 0,
                    "Final label is not settled by source cutoff")
            close(label["netReturn"], outcome["netPnlAmount"] / outcome["investedAmount"], "Cash-normalized label")
        else:
            require(outcome["netReturn"] is None and (label is None or
                (label["exitDate"] is None and label["netReturn"] is None)), "Immature outcome cannot be scored")
        output.append({"formationId": fid, "formationDate": row["formationDate"], "symbol": row["symbol"],
            "originalEntered": row["originalEntered"], "pathStatus": status, "pathEntered": outcome["entered"],
            "entryDate": outcome["entryDate"], "finalExitDate": label["exitDate"] if label else None,
            "labelNetReturn": label["netReturn"] if label else None, "sourceCutoff": cutoff})
    require([r["formationId"] for r in output] == [r["signalId"] for r in original], "Path order/complete denominator differs")
    return output


def model_observation(day, model):
    if day is None or day.get("models", {}).get(model) is None:
        return {"status": "missing", "reason": "missingFrozenPrediction", "prediction": None, "allow": None}
    saved = day["models"][model]
    require(saved["status"] in ("fitted", "fallback") and type(saved["allow"]) is bool, "Invalid model status")
    if saved["status"] == "fitted":
        require(finite(saved["prediction"]) and saved["allow"] == (saved["prediction"] > 0), "Prediction/allow mismatch")
    else:
        require(saved["prediction"] is None and saved["allow"] is True, "Fallback must retain signals without score")
    return {k: deepcopy(saved[k]) for k in ("status", "reason", "prediction", "allow")}


def paired_records(key, paths, decision_rows):
    """All original IDs, including unfilled, for each predeclared comparison."""
    indexes = {}
    for population in POPULATIONS:
        rows = decision_rows[population]
        indexes[population] = {r["formationDate"]: r for r in rows}
        require(len(indexes[population]) == len(rows), "Duplicate decision date")
    require(len({r["formationId"] for r in paths}) == len(paths), "Duplicate evaluation formation")
    output = []
    for contrast_id, control, candidate, family in PAIR_CONTRASTS:
        cp, cm = control.split("__"); rp, rm = candidate.split("__")
        for path in paths:
            day = path["formationDate"]
            left_day, right_day = indexes[cp].get(day), indexes[rp].get(day)
            if left_day is not None and right_day is not None:
                require(left_day["asOfDate"] == right_day["asOfDate"], "Paired forecasts have different information dates")
            a, b = model_observation(left_day, cm), model_observation(right_day, rm)
            flags = a["status"] == "fitted", b["status"] == "fitted"
            cell = "bothFitted" if all(flags) else "controlOnlyFitted" if flags[0] else "candidateOnlyFitted" if flags[1] else "neitherFitted"
            output.append({**key, "contrastId": contrast_id, "comparisonFamily": family,
                "controlCandidate": control, "candidateCandidate": candidate, **deepcopy(path),
                "controlAsOfDate": left_day["asOfDate"] if left_day else None,
                "candidateAsOfDate": right_day["asOfDate"] if right_day else None,
                "control": a, "candidate": b, "fitCell": cell})
    return output


def weighted_score(rows):
    """A new day-weight denominator inside each slice/cell; no trade-weight mean."""
    scored = [r for r in rows if r["pathStatus"] == "closed"]
    require(all(r["pathEntered"] and finite(r["labelNetReturn"]) for r in scored), "Closed scoring label missing")
    per_day = Counter(r["formationDate"] for r in scored)
    weights = [1.0 / per_day[r["formationDate"]] for r in scored]
    weight_sum = total(weights)
    close(weight_sum, len(per_day), "One total weight per formation day")
    def avg(values):
        return total(w * x for w, x in zip(weights, values)) / weight_sum if weight_sum else None
    actual = [r["labelNetReturn"] * 100 for r in scored]
    result = {"episodeCount": len(scored), "formationDayCount": len(per_day), "weightSum": weight_sum,
        "minimumEpisodesPerFormationDay": min(per_day.values(), default=None),
        "maximumEpisodesPerFormationDay": max(per_day.values(), default=None),
        "meanNetReturnPct": avg(actual), "positiveLabelSharePct": avg([100. if y > 0 else 0. for y in actual]),
        "control": {}, "candidate": {}}
    for side in ("control", "candidate"):
        available = sum(r[side]["status"] == "fitted" and finite(r[side]["prediction"]) for r in scored)
        complete = bool(scored) and available == len(scored)
        predictions = [r[side]["prediction"] * 100 for r in scored] if complete else []
        result[side] = {"predictionCount": available, "completeOnScoredLabels": complete,
            "meanPredictionPct": avg(predictions) if complete else None,
            "maePp": avg([abs(p - y) for p, y in zip(predictions, actual)]) if complete else None,
            "msePpSquared": avg([(p - y) ** 2 for p, y in zip(predictions, actual)]) if complete else None}
    result["candidateMinusControl"] = {name: result["candidate"][name] - result["control"][name]
        if finite(result["candidate"][name]) and finite(result["control"][name]) else None
        for name in ("meanPredictionPct", "maePp", "msePpSquared")}
    return result


def evaluate_cell(rows, include_score=True):
    statuses = Counter(r["pathStatus"] for r in rows)
    require(set(statuses) <= set(PATH_STATES), "Unknown evaluation path state")
    result = {"opportunityCount": len(rows), "formationDayCount": len({r["formationDate"] for r in rows}),
        "symbolCount": len({r["symbol"] for r in rows}), "enteredCount": sum(r["pathEntered"] for r in rows),
        "closedCount": statuses["closed"], "unfilledCount": statuses["unfilled"],
        "openCount": statuses["open"], "unresolvedCount": statuses["unresolved"],
        "immatureEnteredCount": sum(r["pathEntered"] and r["pathStatus"] != "closed" for r in rows),
        "pathStatusCounts": {s: statuses[s] for s in PATH_STATES},
        "controlStatusCounts": {s: sum(r["control"]["status"] == s for r in rows) for s in ("fitted", "fallback", "missing")},
        "candidateStatusCounts": {s: sum(r["candidate"]["status"] == s for r in rows) for s in ("fitted", "fallback", "missing")}}
    if include_score:
        result["score"] = weighted_score(rows)
    return result


def evaluation_spans(target_dates):
    require(target_dates and target_dates == sorted(set(target_dates)), "Ordered full target dates needed")
    spans = [{"id": "overall", "kind": "overall", "startDate": target_dates[0], "endDate": target_dates[-1]}]
    for year in sorted({d[:4] for d in target_dates}):
        dates = [d for d in target_dates if d[:4] == year]
        spans.append({"id": year, "kind": "calendarYear", "startDate": dates[0], "endDate": dates[-1]})
    if target_dates[0] < CUT <= target_dates[-1]:
        for name, dates in (("before20240924", [d for d in target_dates if d < CUT]),
                            ("from20240924", [d for d in target_dates if d >= CUT])):
            spans.append({"id": name, "kind": "descriptiveEpoch", "startDate": dates[0], "endDate": dates[-1]})
    return spans


def evaluate_pairs(key, paired, target_dates):
    output = []
    for contrast_id, control, candidate, family in PAIR_CONTRASTS:
        source = [r for r in paired if r["contrastId"] == contrast_id]
        require(len({r["formationId"] for r in source}) == len(source)
                and all(r["formationDate"] in target_dates for r in source), "Paired identity/date invalid")
        for span in evaluation_spans(target_dates):
            rows = [r for r in source if span["startDate"] <= r["formationDate"] <= span["endDate"]]
            cells = {c: evaluate_cell([r for r in rows if r["fitCell"] == c]) for c in FIT_CELLS}
            require(sum(c["opportunityCount"] for c in cells.values()) == len(rows), "Fit coverage partition")
            both = [r for r in rows if r["fitCell"] == "bothFitted"]
            allow_cells = {str(int(a)) + str(int(b)): evaluate_cell([r for r in both
                if r["control"]["allow"] == a and r["candidate"]["allow"] == b])
                for a in (False, True) for b in (False, True)}
            require(sum(c["opportunityCount"] for c in allow_cells.values()) == len(both), "Common-fitted allow partition")
            margins = {side: {"allow" if allow else "reject": evaluate_cell([r for r in both if r[side]["allow"] == allow])
                for allow in (False, True)} for side in ("control", "candidate")}
            output.append({**key, **span, "contrastId": contrast_id, "comparisonFamily": family,
                "controlCandidate": control, "candidateCandidate": candidate,
                "targetDayCount": sum(span["startDate"] <= d <= span["endDate"] for d in target_dates),
                "coverage": evaluate_cell(rows, include_score=False), "fitCells": cells,
                "commonFitted": cells["bothFitted"], "commonFittedAllowCells": allow_cells,
                "commonFittedAllowMargins": margins,
                "meaning": "Final-cutoff independent-path outcomes, attributed to formation T; each slice/cell reweights its own closed labels by formation day. Not portfolio returns or independent observations."})
    return output


def compare_accounts(rows):
    index = {(r["period"], r["board"], r["exit"], r["candidate"]): r for r in rows}
    expected = {(p, b, e, c) for p in PERIODS for b in BOARDS for e in EXITS for c in CANDIDATES}
    require(len(index) == len(rows) == 60 and set(index) == expected, "Complete 60-account matrix required")
    output = []
    for p in PERIODS:
        for b in BOARDS:
            for e in EXITS:
                for cid, control, candidate, family in ACCOUNT_CONTRASTS:
                    a, z = (index[(p, b, e, c)]["metrics"] for c in (control, candidate))
                    require(set(a) == set(z), "Metric fields differ")
                    output.append({"period": p, "board": b, "exit": e, "id": cid, "comparisonFamily": family,
                        "control": control, "candidate": candidate, "controlMetrics": deepcopy(a), "candidateMetrics": deepcopy(z),
                        "candidateMinusControl": {k: z[k] - a[k] if finite(a[k]) and finite(z[k]) else None for k in sorted(a)},
                        "meaning": "Separate complete accounts including changed funding and exposure; not a paired-trade causal effect."})
    return output


def interval_rows(key, summary, result, original, schedule, decisions):
    legacy_key = {**key, "candidate": key["model"] if key["model"] else "baseline"}
    rows = ACCOUNT.interval_rows(legacy_key, summary, result, original, schedule, decisions)
    for row in rows:
        row.update(key)
    return rows


def feedback_coverage(key, population, decisions, original, schedules, target_dates):
    require([r["formationDate"] for r in decisions] == target_dates, "All target-day predictions required")
    by_day = {r["formationDate"]: r for r in decisions}
    require(len(by_day) == len(decisions), "Duplicate decision day")
    output = []
    for model in MODELS:
        cid = population + "__" + model
        observed = [model_observation(r, model) for r in decisions]
        expected = [s for s in original if model_observation(by_day[s["signalDate"]], model)["allow"]]
        require(expected == schedules[cid], "Frozen admission schedule differs")
        output.append({**key, **candidate_identity(cid), "targetDayCount": len(decisions),
            "statusCounts": MATH.labels(m["status"] for m in observed), "reasonCounts": MATH.labels(m["reason"] for m in observed),
            "allowDayCount": sum(m["allow"] for m in observed), "rejectDayCount": sum(not m["allow"] for m in observed),
            "originalOpportunities": len(original), "scheduledSignals": len(expected), "notScheduledSignals": len(original) - len(expected),
            "fittedOriginalOpportunities": sum(by_day[s["signalDate"]]["models"][model]["status"] == "fitted" for s in original),
            "fallbackOriginalOpportunities": sum(by_day[s["signalDate"]]["models"][model]["status"] == "fallback" for s in original),
            "predictionWhenFitted": MATH.distribution(m["prediction"] for m in observed if m["status"] == "fitted"),
            "eligibleEpisodesPerDay": MATH.distribution(r["support"]["eligibleEpisodeCount"] for r in decisions),
            "eligibleFormationDaysPerDay": MATH.distribution(r["support"]["eligibleFormationDayCount"] for r in decisions),
            "labelXUnknownEpisodeDayObservations": sum(r["support"]["labelXUnknownCount"] for r in decisions),
            "immatureEpisodeDayObservations": sum(r["support"]["immatureCount"] for r in decisions),
            "immatureDailyMeanAgeSlots": MATH.distribution(r["support"]["immatureAgeSlots"]["mean"] for r in decisions),
            "currentFeatureStatusCounts": MATH.labels(r["currentFeatureStatus"] for r in decisions),
            "meaning": "Full target-day coverage; immature counts repeat labels across days. Mean ages describe daily populations, not independent episodes."})
    return output


def build(directory=DEFAULT):
    directory = Path(directory).resolve()
    destinations = {name: directory / name for name in ("readout.json", "opportunity-dispositions.jsonl.gz",
        "candidate-entry-episodes.jsonl.gz", "paired-prediction-evaluation.jsonl.gz")}
    require(not any(p.exists() for p in destinations.values()), "Readout artifacts exist; never overwrite")
    pins = {}

    def pin(path):
        path = Path(path).resolve()
        require(path.is_relative_to(ROOT), "Readout input outside repository")
        name, value = path.relative_to(ROOT).as_posix(), sha(path)
        require(pins.setdefault(name, value) == value, "Readout input changed")
        return path

    def bound(item):
        path = pin(ROOT / item["path"])
        require(sha(path) == item["sha256"], "Artifact digest mismatch: " + item["path"])
        return path

    def ref(path):
        return {"path": Path(path).resolve().relative_to(ROOT).as_posix(), "sha256": sha(path)}

    names = ("plan.json", "comparison.json", "input-completion.json", "input-validation.json", "validation.json")
    plan, comparison, completion, input_proof, proof = [read(pin(directory / n)) for n in names]
    plan_sha = sha(directory / "plan.json")
    require(plan["schema"] == "all-opportunity-feedback-v1" and comparison["planSha256"] == completion["planSha256"] == plan_sha,
            "Plan/comparison/completion identity")
    require(proof["passed"] is True and input_proof["passed"] is True
        and proof["planSha256"] == input_proof["planSha256"] == plan_sha
        and proof["comparisonSha256"] == sha(directory / "comparison.json")
        and proof["inputCompletionSha256"] == input_proof["inputCompletionSha256"] == sha(directory / "input-completion.json")
        and proof["inputValidationSha256"] == sha(directory / "input-validation.json"), "Passing exact full audit chain required")
    verifier = "scripts/verify-all-opportunity-feedback.py"
    require(proof["verifierSha256"] == plan["sourcePins"][verifier] == sha(ROOT / verifier), "Independent verifier changed")
    require(proof["counts"]["accounts"] == 60 and proof["counts"]["newAccounts"] == 48
        and proof["counts"]["reusedAccounts"] == 12 and comparison["newAccounts"] == 48
        and comparison["referenceAccountsReused"] == 12, "Full account matrix/trial scope")
    require(completion["allSchedulesFrozenBeforeAccounts"] is True
        and plan["holdoutClaim"] is False and plan["parameterSearch"] is False, "Frozen scope required")
    for name, value in plan["sourcePins"].items():
        bound({"path": name, "sha256": value})
    pin(__file__); pin(HELPER); pin(ACCOUNT.HELPER)
    for name in ("libraryPlan", "libraryDelivery", "libraryReadout", "libraryReadoutValidation", "libraryValidation", "libraryCompletion"):
        bound(plan[name])
    atlas = read(bound(plan["atlas"]))
    phases = {r["id"]: r for r in atlas["phases"]}
    require(len(phases) == len(atlas["phases"]) == 16, "All original atlas phases required")
    groups = {(r["period"], r["board"], r["exit"]): r for r in completion["groups"]}
    expected_groups = {(p, b, e) for p in PERIODS for b in BOARDS for e in EXITS}
    require(len(groups) == len(completion["groups"]) == 12 and set(groups) == expected_groups, "Twelve source/exit groups required")
    index = {(r["period"], r["board"], r["exit"], r["candidate"]): r for r in comparison["rows"]}
    require(len(index) == len(comparison["rows"]) == 60, "Complete account descriptors")
    accounts, flat, intervals, coverage, feedback, shifts, partitions, evaluations = ([] for _ in range(8))
    dispositions, entries_all, pair_records = [], [], []
    decision_refs, original_counts = [], {}
    for period in PERIODS:
        for board in BOARDS:
            original = read(bound(plan["baselineSchedules"][period][board]))
            metadata = read(bound(plan["metadata"][period][board]))
            dates = metadata["targetDates"]
            original_counts[(period, board)] = len(original)
            for exit_policy in EXITS:
                key = {"period": period, "board": board, "exit": exit_policy}
                group = groups[(period, board, exit_policy)]
                require(group["baselineSchedule"] == plan["baselineSchedules"][period][board]
                    and group["libraryPaths"] == plan["libraryPaths"][period][board][exit_policy]
                    and group["decisions"] == plan["decisionArtifacts"][period][board][exit_policy], "Prepared library references differ")
                paths = normalize_evaluation_paths(records(bound(group["libraryPaths"])), original, key, dates[-1])
                original_entered = {r["formationId"] for r in paths if r["originalEntered"]}
                decisions = {p: list(records(bound(group["decisions"][p]))) for p in POPULATIONS}
                schedules = {c: read(bound(group["candidates"][c]["schedule"])) for c in CANDIDATES[1:]}
                for population in POPULATIONS:
                    feedback.extend(feedback_coverage(key, population, decisions[population], original, schedules, dates))
                    decision_refs.append({**key, "population": population, "artifact": deepcopy(group["decisions"][population]),
                        "targetDayCount": len(decisions[population]), "scope": "Byte-reused frozen prediction evidence; no refit by summarizer."})
                paired = paired_records(key, paths, decisions)
                pair_records.extend(paired)
                evaluations.extend(evaluate_pairs(key, paired, dates))
                for candidate in CANDIDATES:
                    account_key = {**key, **candidate_identity(candidate)}
                    descriptor = index[(period, board, exit_policy, candidate)]
                    require(all(descriptor[k] == account_key[k] for k in ("population", "model")), "Candidate metadata differs")
                    schedule = original if candidate == "baseline" else schedules[candidate]
                    require(read(bound(descriptor["schedule"])) == schedule, "Account schedule binding differs")
                    if candidate == "baseline":
                        for kind in ("result", "checkpoint"):
                            require(descriptor[kind] == plan["accountArtifacts"][period][board][exit_policy][exit_policy + "." + kind + ".json.gz"],
                                    "Baseline was not byte-reused")
                    result = read(bound(descriptor["result"]))
                    bound(descriptor["checkpoint"])
                    summary = read(bound(descriptor["summary"]))
                    require(summary["inputResultSha256"] == digest(result) and summary["policyId"] == candidate
                        and result["status"] == "completed" and not summary["quality"]["integrityIssues"], "Summary/account identity mismatch")
                    require([r["date"] for r in result["curve"]] == dates, "Incomplete source account timeline")
                    detail, entries = ACCOUNT.attach_episodes(account_key, original, schedule, result, summary, original_entered)
                    dispositions.extend(detail)
                    if candidate == "baseline":
                        require({r["formationId"] for r in entries} == original_entered, "Original-entry membership differs from baseline")
                    else:
                        entries_all.extend(entries)
                        shifts.append(ACCOUNT.sample_shift(account_key, entries, summary))
                    scalar = ACCOUNT.metrics(summary, len(schedule), len(original))
                    flat.append({**account_key, "reusedAccount": candidate == "baseline", "metrics": scalar})
                    accounts.append({**account_key, "artifacts": deepcopy(descriptor), "metrics": scalar,
                        **{n: deepcopy(summary[n]) for n in ("account", "closedEpisodes", "concentration", "openPositions", "rights", "unresolvedExits", "reconciliation", "quality", "warnings")},
                        "sourceQualityScalarFlags": {n: v for n, v in summary["sourceQuality"].items() if not isinstance(v, (dict, list))},
                        "fullSourceQuality": {"summaryRef": deepcopy(descriptor["summary"]), "field": "sourceQuality"}})
                    states = Counter(r["positionState"] for r in detail)
                    coverage.append({**account_key, "originalOpportunities": len(original), "scheduledSignals": len(schedule),
                        "notScheduled": states["notScheduled"], "entered": len(entries), "closed": states["closed"],
                        "openAtCutoff": states["openAtCutoff"], "notEntered": states["notEntered"],
                        "accountOpportunityReasons": MATH.labels(r["accountDisposition"]["reason"] for r in detail if r["scheduled"]),
                        "valuationUnreliableDays": scalar["unreliableValuationDays"]})
                    measured = interval_rows(account_key, summary, result, original, schedule,
                        decisions[account_key["population"]] if account_key["population"] else [])
                    for row in measured:
                        if row["kind"] == "retrospectiveNarrativePhase":
                            phase = phases[row["id"]]
                            require((row["requestedStart"], row["requestedEnd"]) == (phase["startDate"], phase["endDate"]), "Atlas dates changed")
                            row["phaseTitle"] = phase["title"]
                    intervals.extend(measured)
                    partitions.append({**account_key, "passed": True,
                        "checks": ACCOUNT.partition_intervals(measured, summary, result["curve"])})
    require(sum(original_counts.values()) == 8155 and len(dispositions) == 8155 * 10
            and len(pair_records) == 8155 * 2 * 4, "Complete original opportunity denominators")
    contrasts = compare_accounts(flat)
    tables = {name: [r for r in intervals if r["kind"] == kind] for name, kind in
        (("yearRows", "calendarYear"), ("halfYearRows", "calendarHalfYear"),
         ("phaseRows", "retrospectiveNarrativePhase"), ("curveEpochRows", "descriptiveEpoch"))}
    require([len(tables[n]) for n in tables] == [120, 240, 480, 60], "Full calendar/phase matrices")
    require(len(shifts) == len(feedback) == 48 and len(coverage) == len(partitions) == 60
            and len(evaluations) == 192 and len(decision_refs) == 24, "Full diagnostics matrices")
    require(all(sha(ROOT / n) == value for n, value in pins.items()), "Inputs changed during readout")
    def write_lines(name, values):
        path = destinations[name]
        with gzip.open(path, "xt", encoding="utf-8", compresslevel=1) as stream:
            for row in values:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        return {**ref(path), "count": len(values), "format": "jsonl+gzip"}
    opportunity_ref = write_lines("opportunity-dispositions.jsonl.gz", dispositions)
    entry_ref = write_lines("candidate-entry-episodes.jsonl.gz", entries_all)
    prediction_ref = write_lines("paired-prediction-evaluation.jsonl.gz", pair_records)
    report = {"schema": "all-opportunity-feedback-readout-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planSha256": plan_sha, "comparisonSha256": sha(directory / "comparison.json"),
        "verification": {"passed": True, **ref(directory / "validation.json")}, "inputPins": pins,
        "accounts": accounts, "accountRows": flat, "comparisons": contrasts,
        "primaryComparisons": [r for r in contrasts if r["comparisonFamily"] == "population"],
        "modelComparisons": [r for r in contrasts if r["comparisonFamily"] == "model"],
        "baselineComparisons": [r for r in contrasts if r["comparisonFamily"] == "baseline"], **tables,
        "coverageRows": coverage, "feedbackCoverageRows": feedback, "feedbackDecisionArtifacts": decision_refs,
        "sampleShiftRows": shifts, "partitionReconciliation": partitions, "predictionEvaluationRows": evaluations,
        "predictionPrimaryRows": [r for r in evaluations if r["comparisonFamily"] == "population"],
        "counts": {"accounts": 60, "newAccounts": 48, "reusedAccounts": 12, "comparisons": 96,
            "primaryComparisons": 24, "modelComparisons": 24, "baselineComparisons": 48,
            **{n: len(v) for n, v in tables.items()}, "coverageRows": 60, "feedbackCoverageRows": 48,
            "sampleShiftRows": 48, "originalOpportunities": 8155, "sourceExitOpportunities": 16310,
            "policyDispositions": len(dispositions), "actualCandidateEntryEpisodes": len(entries_all),
            "predictionEvaluationRows": len(evaluations), "pairedPredictionOpportunities": len(pair_records),
            "frozenDecisionLibraries": 24, "frozenDecisionDays": sum(r["targetDayCount"] for r in decision_refs)},
        "opportunityRowsArtifact": {**opportunity_ref, "identity": ["period", "board", "exit", "candidate", "formationId"]},
        "candidateEntryRowsArtifact": {**entry_ref, "identity": ["period", "board", "exit", "candidate", "formationId"]},
        "pairedPredictionRowsArtifact": {**prediction_ref, "identity": ["period", "board", "exit", "contrastId", "formationId"]},
        "method": {"accounts": "60 full cash/position accounts: 12 byte-reused baseline results and 48 new candidates. Account performance is actual continuous equity, not closed-only PnL.",
            "contrasts": "Primary: allExecutable minus originalEntered within each model. Model contrasts: ridge minus intercept within each population. All four candidates also compared with baseline.",
            "predictionScope": "Four paired comparisons over identical original formation IDs and final-cutoff allExecutable independent paths; no original-entry or actual-candidate-entry restriction in evaluation.",
            "predictionWeighting": "In every slice and allow cell, each finally closed label receives 1/count(closed labels on the same formation T); each represented T has weight 1. MAE uses percentage points; MSE percentage points squared. No cap, trimming or error threshold.",
            "predictionPrimary": "Only common fitted forecasts with the same finally closed label receive paired primary errors. Single-sided fitted coverage and errors remain separate; no pooled overall score mixing support regimes.",
            "predictionClocks": "Frozen predictions use only information at A=T-1. Evaluation may observe final exits after the formation slice end, up to the source-period cutoff. This outcome never changes allow/schedules or training.",
            "predictionMissing": "Fallback has allow=true and no prediction. Missing forecasts, unfilled paths, open and unresolved entries remain counted; none become zero outcomes. Forecast gains are not used to stop or select the fixed account experiments.",
            "predictionAllow": "00/01/10/11 and each side's allow/reject cells are common-fitted descriptions, each with its own date-weight denominator. Cell means cannot be added into investment returns.",
            "dependence": "Same-day stocks, repeated symbols and overlapping paths are dependent. Formation-day equal weighting does not establish independent observations or out-of-sample generalization.",
            "sampleShift": "Actual candidate-only entries relative to original baseline admissions are retained with closed/open cash bridges; these are not synthetic counterfactual trades.",
            "intervals": "Years, halves, narrative phases and 9.24 cuts carry the original account equity and positions. Episode PnL is assigned by final exitDate; forecast evaluation and admission counts by formation T.",
            "quality": "Full bound source-quality summaries, stale open marks, cash/fees, unrealized PnL and exposure remain visible; arithmetic audit does not certify prices or realizability.",
            "scope": "2022-2025 repeatedly observed development data; 2026 was exposed but is not used. No holdout, stability, promotion or profit-hurdle claim."},
        "newAccounts": 48, "referenceAccountsReused": 12, "signalsGenerated": False,
        "summarizerAccountExecutions": 0, "summarizerModelFits": 0,
        "holdoutClaim": False, "certified": False, "targetAchieved": False}
    with destinations["readout.json"].open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"passed": True, "readout": ref(destinations["readout.json"]), "counts": report["counts"]}


def self_test():
    import io
    import unittest
    test_file = ROOT / "tests/test_all_opportunity_feedback_readout.py"
    test_spec = importlib.util.spec_from_file_location("all_opportunity_readout_synthetic", test_file)
    test_module = importlib.util.module_from_spec(test_spec)
    test_spec.loader.exec_module(test_module)
    log = io.StringIO()
    outcome = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_module))
    require(outcome.wasSuccessful(), log.getvalue())
    return {"passed": True, "syntheticTests": outcome.testsRun, "realAccountReads": 0,
            "modelFits": 0, "accountRuns": 0, "filesWritten": 0, "details": log.getvalue()}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False, allow_nan=False))

