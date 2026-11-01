"""Posthoc label-era composition; no model, schedule or account is changed."""
from argparse import ArgumentParser
from collections import Counter
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
from service.research_online_feedback import build_online_feedback

HELPER = ROOT / "scripts/summarize-independent-shadow-labels.py"
SPEC = importlib.util.spec_from_file_location("independent_labels_frozen_readout", HELPER)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
require, digest, distribution = BASE.require, BASE.digest, BASE.distribution
DEFAULT = ROOT / "artifacts/strategy-search/independent-shadow-labels-v1"
CUT = "2024-09-24"
SAMPLES = ("oldActual", "originalEntered", "allExecutable")
VERSION = "independent-shadow-regime-maturity-v1"


def describe_day(row, reference, sample):
    """Verify the unchanged decision, then describe its already-eligible labels."""
    require(sample in SAMPLES, "Unknown label sample")
    for key in ("formationDate", "asOfDate", "window", "currentFeatures", "currentFeatureStatus",
                "currentFeatureReasons", "support", "trainingSummary", "models"):
        require(row[key] == reference[key], "Replayed feedback differs: " + key)
    labels = row["trainingLabels"]
    label_hash = digest(labels)
    if sample == "oldActual":
        require(label_hash == digest(reference["trainingLabels"]), "Old actual training labels differ")
        require(row["immature"] == reference["immature"] and row["labelXUnknown"] == reference["labelXUnknown"],
                "Old actual rejected/immature evidence differs")
    else:
        require(label_hash == reference["trainingPopulation"]["labelsDigest"], "Compact training label digest differs")
        require(digest(row["immature"]) == reference["immaturePopulation"]["digest"]
                and digest(row["labelXUnknown"]) == reference["labelXUnknownPopulation"]["digest"],
                "Compact rejected/immature evidence differs")
    by_day = Counter(label["formationDate"] for label in labels)
    require(len(labels) == row["support"]["eligibleEpisodeCount"] == row["trainingSummary"]["episodeCount"]
            and len(by_day) == row["support"]["eligibleFormationDayCount"] == row["trainingSummary"]["formationDayCount"],
            "Eligible label denominator differs")
    for label in labels:
        require(label["exitDate"] <= row["asOfDate"] and row["window"]["startDate"] <= label["exitDate"],
                "Label outside prior-close final-exit window")
        require(label["formationDayCount"] == by_day[label["formationDate"]]
                and label["weight"] == 1. / by_day[label["formationDate"]], "Formation-day weight changed")
    weight = math.fsum(label["weight"] for label in labels)
    post = [label for label in labels if label["formationDate"] >= CUT]
    post_days = {label["formationDate"] for label in post}
    post_weight = math.fsum(label["weight"] for label in post)
    require(weight == row["trainingSummary"]["weightSum"] and math.isclose(weight, len(by_day), abs_tol=1e-12)
            and math.isclose(post_weight, len(post_days), abs_tol=1e-12), "Formation-day weight totals differ")
    fraction = len(post_days) / len(by_day) if by_day else None
    require(fraction is None or math.isclose(fraction, post_weight / weight, abs_tol=1e-12), "Integer/day-weight composition differs")
    return {"formationDate": row["formationDate"], "asOfDate": row["asOfDate"], "window": row["window"],
        "trainingLabelsDigest": label_hash, "eligibleEpisodeCount": len(labels), "eligibleFormationDayCount": len(by_day),
        "weightSum": weight, "preRegimeEpisodeCount": len(labels) - len(post), "postRegimeEpisodeCount": len(post),
        "preRegimeFormationDayCount": len(by_day) - len(post_days), "postRegimeFormationDayCount": len(post_days),
        "preRegimeWeight": weight - post_weight, "postRegimeWeight": post_weight,
        "postRegimeWeightShare": fraction, "hasPostRegimeMaturedLabels": bool(post_days),
        "labelXUnknownCount": row["support"]["labelXUnknownCount"], "immatureCount": row["support"]["immatureCount"],
        "currentFeatureStatus": row["currentFeatureStatus"],
        "modelStatus": row["models"]["ridge"]["status"], "modelReason": row["models"]["ridge"]["reason"],
        "modelAllow": {mode: row["models"][mode]["allow"] for mode in ("intercept", "ridge")}}


def summarize_days(key, rows):
    require(bool(rows) and [r["formationDate"] for r in rows] == sorted({r["formationDate"] for r in rows}),
            "Unique chronological decision days required")
    spans = [("overall", "overall", rows),
             ("before20240924", "descriptiveEpoch", [r for r in rows if r["formationDate"] < CUT]),
             ("from20240924", "descriptiveEpoch", [r for r in rows if r["formationDate"] >= CUT])]
    result = []
    for identity, kind, daily in spans:
        if not daily:
            continue
        post = [r for r in daily if r["hasPostRegimeMaturedLabels"]]
        fitted = [r for r in daily if r["modelStatus"] == "fitted"]
        result.append({**key, "id": identity, "kind": kind, "startDate": daily[0]["formationDate"],
            "endDate": daily[-1]["formationDate"], "dayCount": len(daily),
            "fittedDays": len(fitted), "fallbackDays": len(daily) - len(fitted),
            "daysWithAnyEligibleLabels": sum(r["eligibleEpisodeCount"] > 0 for r in daily),
            "daysWithPostRegimeMaturedLabels": len(post),
            "firstPostRegimeMaturedLabelDate": post[0]["formationDate"] if post else None,
            "firstPostRegimeMaturedLabelAsOfDate": post[0]["asOfDate"] if post else None,
            "fittedDaysWithPostRegimeMaturedLabels": sum(r["hasPostRegimeMaturedLabels"] for r in fitted),
            "fittedDaysWithoutPostRegimeMaturedLabels": sum(not r["hasPostRegimeMaturedLabels"] for r in fitted),
            "fallbackDaysWithPostRegimeMaturedLabels": sum(r["modelStatus"] == "fallback" for r in post),
            "postRegimeWeightShare": distribution(r["postRegimeWeightShare"] for r in daily),
            "postRegimeWeightShareOnFittedDays": distribution(r["postRegimeWeightShare"] for r in fitted),
            "eligibleFormationDays": distribution(r["eligibleFormationDayCount"] for r in daily),
            "postRegimeFormationDays": distribution(r["postRegimeFormationDayCount"] for r in daily),
            "eligibleEpisodes": distribution(r["eligibleEpisodeCount"] for r in daily),
            "postRegimeEpisodes": distribution(r["postRegimeEpisodeCount"] for r in daily),
            "basis": "Decision-day snapshots; share is eligible post-9.24 formation days divided by all eligible formation days. Null means no usable matured labels. Not an adaptation-success threshold."})
    return result


def build(directory=DEFAULT):
    directory = Path(directory).resolve()
    summary_path, daily_path = (directory / name for name in ("regime-maturity-context.json", "regime-maturity-daily.jsonl.gz"))
    require(not summary_path.exists() and not daily_path.exists(), "Keep any existing sidecar; never overwrite")
    pins = {}
    def bind(ref):
        path = ROOT / ref["path"]
        require(BASE.sha(path) == ref["sha256"], "Changed input: " + ref["path"])
        pins[ref["path"]] = ref["sha256"]
        return path
    def local(name):
        path = directory / name
        return bind({"path": path.relative_to(ROOT).as_posix(), "sha256": BASE.sha(path)})
    plan_path = local("plan.json"); plan = BASE.read(plan_path); plan_sha = BASE.sha(plan_path)
    source_receipt = BASE.read(local("regime-maturity-source-pins.json"))
    require(source_receipt["planSha256"] == plan_sha
            and source_receipt["sourcePins"].get(Path(__file__).relative_to(ROOT).as_posix()) == BASE.sha(__file__),
            "Freeze this descriptive extractor before execution")
    for name, value in source_receipt["sourcePins"].items():
        bind({"path": name, "sha256": value})
    for name, value in plan["sourcePins"].items():
        bind({"path": name, "sha256": value})
    readout_path = local("readout.json"); report = BASE.read(readout_path)
    readout_proof_path = local("readout-validation.json"); proof = BASE.read(readout_proof_path)
    path_proof_path = local("validation.json"); path_proof = BASE.read(path_proof_path)
    library_path = local("library-completion.json"); library = BASE.read(library_path)
    require(proof["passed"] is True and proof["planSha256"] == plan_sha
            and proof["readoutSha256"] == BASE.sha(readout_path)
            and proof["pathValidationSha256"] == BASE.sha(path_proof_path)
            and proof["libraryCompletionSha256"] == BASE.sha(library_path), "Exact readout audit must pass before sidecar")
    require(path_proof["passed"] is True and path_proof["planSha256"] == plan_sha
            and path_proof["libraryCompletionSha256"] == BASE.sha(library_path)
            and report["planSha256"] == plan_sha and report["libraryCompletionSha256"] == BASE.sha(library_path)
            and report["pathValidationSha256"] == BASE.sha(path_proof_path), "Audited library chain differs")
    require(report["builderSha256"] == BASE.sha(HELPER), "Frozen summary helper changed")
    bind({"path": HELPER.relative_to(ROOT).as_posix(), "sha256": BASE.sha(HELPER)})
    parent_path = bind(plan["parentPlan"])
    old_completion_path = bind(plan["parentInputCompletion"]); old_completion = BASE.read(old_completion_path)
    old_proof = BASE.read(bind(plan["parentInputValidation"]))
    require(old_proof["passed"] is True and old_proof["planSha256"] == BASE.sha(parent_path)
            and old_proof["inputCompletionSha256"] == BASE.sha(old_completion_path), "Parent training evidence not audited")
    key_of = lambda r: tuple(r[k] for k in BASE.KEYS)
    old_groups = {key_of(r): r for r in old_completion["groups"]}
    compact_groups = {key_of(r): r for r in report["decisionArtifacts"]}
    groups = [g for g in library["groups"] if g["period"] == "2024-2025"]
    require(len(groups) == len({key_of(g) for g in groups}) == 6, "Exactly six new-period source/exit groups required")
    coverage = report["coverageRows"] + report["oldActualCoverageRows"]
    overall, epochs, count = [], [], 0
    with gzip.open(daily_path, "xt", encoding="utf-8", compresslevel=6) as stream:
        for group in groups:
            key = {k: group[k] for k in BASE.KEYS}; p, b, e = key_of(group); old = old_groups[key_of(group)]
            md = BASE.read(bind(plan["metadata"][p][b])); env = list(BASE.records(bind(plan["environment"][p][b])))
            labels = BASE.read(bind(old["shadow"]))
            schedule = BASE.read(bind(plan["baselineSchedules"][p][b]))
            paths = BASE.normalize_paths(BASE.records(bind(group["paths"])), schedule, labels, key)
            samples = {"oldActual": labels,
                "originalEntered": [r["label"] for r in paths if r["originalEntered"] and r["label"] is not None],
                "allExecutable": [r["label"] for r in paths if r["label"] is not None]}
            for sample in SAMPLES:
                reference_ref = old["decisions"] if sample == "oldActual" else compact_groups[key_of(group)]["samples"][sample]
                reference = iter(BASE.records(bind(reference_ref)))
                replay = build_online_feedback(md["calendar"], md["targetDates"], env, samples[sample])
                daily = []
                for row in replay["dailyRows"]:
                    actual = next(reference, None)
                    require(actual is not None, "Missing frozen decision reference")
                    described = {**key, "sample": sample, **describe_day(row, actual, sample)}
                    stream.write(json.dumps(described, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
                    daily.append(described); count += 1
                require(next(reference, None) is None, "Trailing frozen decision reference")
                del replay
                summaries = summarize_days({**key, "sample": sample}, daily)
                for summary in summaries:
                    matches = [r for r in coverage if all(r[k] == summary[k] for k in (*BASE.KEYS, "sample", "id"))]
                    require(len(matches) == 1 and all(matches[0][k] == summary[k] for k in ("dayCount", "fittedDays", "fallbackDays")),
                            "Descriptive intervals must reconcile to audited main coverage")
                    (overall if summary["id"] == "overall" else epochs).append(summary)
    require(len(overall) == 18 and len(epochs) == 36 and count == sum(r["dayCount"] for r in overall), "Complete era-composition matrix required")
    require(all(BASE.sha(ROOT / name) == value for name, value in pins.items()), "Input changed during descriptive replay")
    daily_ref = {"path": daily_path.relative_to(ROOT).as_posix(), "sha256": BASE.sha(daily_path),
                 "count": count, "format": "jsonl+gzip"}
    result = {"version": VERSION, "createdAt": datetime.now(timezone.utc).isoformat(), "posthoc": True,
        "planSha256": plan_sha, "readoutSha256": BASE.sha(readout_path), "readoutValidationSha256": BASE.sha(readout_proof_path),
        "pathValidationSha256": BASE.sha(path_proof_path), "libraryCompletionSha256": BASE.sha(library_path),
        "extractorSha256": BASE.sha(__file__), "inputRefs": [{"path": name, "sha256": value} for name, value in sorted(pins.items())],
        "cutoff": CUT, "summaryRows": overall, "epochRows": epochs, "dailyRowsArtifact": daily_ref,
        "counts": {"sourceExitGroups": 6, "samples": 3, "summaryRows": 18, "epochRows": 36, "dailyRows": count},
        "verification": {"passed": True, "completeFrozenDecisionMatch": True, "trainingLabelDigestMatch": True,
            "integerFormationDaysMatchWeights": True, "auditedCoverageIntervalsMatch": True},
        "basis": {"era": "FormationDate >= 2024-09-24 is a posthoc descriptive partition, never a feature or admission condition.",
            "weight": "Each eligible formation day has total weight one. Share = post-boundary eligible formation days / all eligible formation days; no labels gives null.",
            "availability": "Unchanged prior-close A=T-1, 126 final-exit sessions, original feature filtering, 20-day support, and ridge lambda=1.",
            "clock": "Daily/epoch date is decision formation T; first observed post-era label date is T, with its A separately recorded.",
            "meaning": "Seeing some post-era matured outcomes does not establish successful regime adaptation. Daily snapshots and overlapping paths are dependent.",
            "scope": "2024-2025 frozen observed development samples; no new signals, accounts, model parameters or promotion threshold."},
        "newSignals": 0, "newCandidateAccounts": 0, "parameterChanges": 0, "certified": False, "targetAchieved": False}
    with summary_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write("\n")
    return {"passed": True, "output": {"path": summary_path.relative_to(ROOT).as_posix(), "sha256": BASE.sha(summary_path)},
            "counts": result["counts"]}


def self_test():
    import unittest
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_independent_shadow_regime_maturity.py")
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    require(result.wasSuccessful(), "Regime-maturity synthetic checks failed")
    return {"passed": True, "syntheticTests": result.testsRun, "realSourceReads": 0, "accountExecutions": 0, "filesWritten": 0}


if __name__ == "__main__":
    parser = ArgumentParser(); parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else build(args.directory), ensure_ascii=False, allow_nan=False), flush=True)
