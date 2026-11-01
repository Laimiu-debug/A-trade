"""Frozen ignition-selection review with a fresh full-source execution projection.

No rule is selected here. A separately registered hypothesis and reviewed
research_ignition_selection module are required before prepare. Run is an
explicit, separate command. Old research, production strategies and the user
workspace are never written. No observations after 2026-10-15 are loaded.
"""
from __future__ import annotations

from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
import gc
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REVIEW = ROOT / "artifacts/strategy-search/ignition-selection-review-20261031"
HYPOTHESIS = REVIEW / "hypothesis-plan.json"
OUT = REVIEW / "experiment"
VERSION = "ignition-selection-review-runner-v1"
CUTOFF = "2026-10-15"
PERIODS = {"development": ("2024-09-24", "2025-12-31"),
           "reusedTemporalCheck": ("2026-01-05", "2026-10-15")}
CASE_IDS = ("baseline", "selection", "participation")


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Read-only utilities and the already tested date-prefix transformation.
# Never call the old module's prepare/run/write functions.
SHARED = load_module("_ignition_review_shared_prefix", "scripts/run-dual-confirmation-review.py")
read, sha, ref, bound, now, emit, require = (
    getattr(SHARED, key) for key in ("read", "sha", "ref", "bound", "now", "emit", "require"))


def output_path(path):
    path = Path(path).resolve()
    require(path.is_relative_to(OUT.resolve()) and path != OUT.resolve(),
            "Writes must stay in the new ignition experiment")
    return path


def write(path, value):
    path = output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = path.name.endswith(".gz")
    opener = gzip.open if compressed else open
    with opener(path, "xt", encoding="utf-8", **({"compresslevel": 1} if compressed else {})) as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False,
                  separators=(",", ":") if compressed else None, indent=None if compressed else 2)
        stream.write("\n")


class JsonStream:
    """Incremental decoder that holds at most one security record plus metadata."""
    def __init__(self, stream, chunk_size=65536):
        self.stream, self.chunk_size = stream, chunk_size
        self.buffer, self.position, self.eof = "", 0, False
        self.decoder = json.JSONDecoder(parse_constant=self.invalid_constant)

    @staticmethod
    def invalid_constant(value):
        raise ValueError("Nonfinite source JSON: " + value)

    def fill(self):
        if self.position:
            self.buffer = self.buffer[self.position:]
            self.position = 0
        chunk = self.stream.read(self.chunk_size)
        self.eof = not chunk
        self.buffer += chunk

    def peek(self):
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position].isspace():
                self.position += 1
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if self.eof:
                return None
            self.fill()

    def take(self, token):
        require(self.peek() == token, "Expected source JSON token " + token)
        self.position += 1

    def value(self):
        require(self.peek() is not None, "Unexpected end of source JSON")
        while True:
            try:
                result, end = self.decoder.raw_decode(self.buffer, self.position)
                # A number/literal may end exactly at a chunk boundary. Require
                # the following delimiter before accepting the decoded value.
                if end == len(self.buffer) and not self.eof:
                    self.fill()
                    continue
                if end < len(self.buffer) and self.buffer[end] not in " \t\r\n,:]}":
                    if self.eof:
                        raise ValueError("Invalid source JSON value delimiter")
                    self.fill()
                    continue
                self.position = end
                return result
            except json.JSONDecodeError:
                if self.eof:
                    raise
                self.fill()


def project_source(path, wanted_symbols, *, expected_population=4999, progress=None, chunk_size=65536):
    """Select already frozen union symbols; never use source prices to select it.

    Every source security is parsed and its date boundary checked. Unselected
    records are discarded immediately, avoiding a multi-million-bar JSON tree.
    The caller pins the compressed file SHA before calling this transformer.
    """
    from service.portfolio_engine import _digest
    wanted = set(wanted_symbols)
    require(wanted and len(wanted) == len(wanted_symbols), "Unique nonempty frozen symbol union required")
    output, seen, selected = {}, set(), []
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        parser = JsonStream(stream, chunk_size)
        parser.take("{")
        while parser.peek() != "}":
            key = parser.value()
            require(isinstance(key, str) and key not in output, "Duplicate/non-string source key")
            parser.take(":")
            if key == "securities":
                parser.take("[")
                while parser.peek() != "]":
                    security = parser.value()
                    symbol = security["symbol"]
                    require(symbol not in seen, "Duplicate source security")
                    seen.add(symbol)
                    require(all(bar["date"] <= CUTOFF for bar in security["bars"]),
                            "Post-cutoff source observation forbidden")
                    if symbol in wanted:
                        selected.append(security)
                    if progress and len(seen) % 500 == 0:
                        progress({"phase": "projectFullSource", "readSecurities": len(seen),
                                  "selectedSecurities": len(selected), "targetSecurities": len(wanted)})
                    if parser.peek() == "]":
                        break
                    parser.take(",")
                    require(parser.peek() != "]", "Trailing source array comma")
                parser.take("]")
                output[key] = selected
            else:
                output[key] = parser.value()
            if parser.peek() == "}":
                break
            parser.take(",")
            require(parser.peek() != "}", "Trailing source object comma")
        parser.take("}")
        require(parser.peek() is None, "Trailing source content")
    require(len(seen) == expected_population, "Full source population changed")
    require({security["symbol"] for security in selected} == wanted,
            "Full source omitted a newly selected candidate")
    require(output["calendar"] == sorted(set(output["calendar"])) and output["calendar"][-1] == CUTOFF,
            "Full source calendar changed")
    require(all(bar["date"] <= CUTOFF for bar in output.get("benchmark", {}).get("bars", [])),
            "Post-cutoff benchmark forbidden")
    original_fingerprint = output.pop("fingerprint", None)
    output["securities"] = sorted(selected, key=lambda security: security["symbol"])
    output["executionProjection"] = {"sourcePopulation": len(seen), "executionSymbols": len(selected),
        "sourceFingerprint": original_fingerprint, "selectionCompletedBeforeProjection": True,
        "selectionUniverse": "Full frozen pre-ranking A candidates within original complete sector slots; original B slots",
        "neverSelectedFromFillsOrOutcomes": True, "newSegmentConsumed": False}
    output["fingerprint"] = _digest(output)
    return output


def validate_plan(plan):
    require(plan["schema"] == "ignition-selection-profitability-review-v1", "Unexpected hypothesis schema")
    require(plan["newAccountsPlanned"] == 9 and plan["maxNewHypotheses"] == 2,
            "Exactly two hypotheses and nine accounts must be registered")
    require(plan["outcomeBasedParameterChangesAllowed"] is False, "Post-result tuning must stay disabled")
    require({name: (period["start"], period["end"]) for name, period in plan["periods"].items()} == PERIODS,
            "Historical periods changed")
    accounts = plan["accounts"]
    require([case["id"] for case in accounts["cases"]] == list(CASE_IDS), "Frozen case identities changed")
    require(all(case["parentPolicy"] == "complete" for case in accounts["cases"]),
            "All cases must preserve the complete A/B context")
    require(accounts["initialCapitalYuan"] == 500000 and accounts["resetAtPeriodStart"] is True
            and accounts["normalSlippageBpsEachSide"] == 10 and accounts["stressSlippageBpsEachSide"] == 35,
            "Frozen capital/cost convention changed")


def load_formation_sources(plan):
    parent = read(bound(plan["frozenParentPlan"]))
    inputs = read(bound(plan["parentInputs"]))
    require(inputs["passed"] and inputs["plan"] == plan["frozenParentPlan"], "Parent inputs are not bound")
    groups = [row for row in inputs["groups"] if row["period"] == "primary" and row["policy"] == "complete"]
    require(len(groups) == 1, "Ambiguous original complete schedule")
    refs = {"parentPlan": plan["frozenParentPlan"], "parentInputs": plan["parentInputs"],
            "formation": inputs["formation"], "formationValidation": inputs["formationValidation"],
            "fullDataset": parent["sources"]["fullDataset"], "matrix": parent["sources"]["matrix"],
            "suspensions": parent["sources"]["suspensions"],
            "parentSchedule": groups[0]["schedule"]}
    for index, item in enumerate(plan.get("diagnosticSources", [])):
        refs[f"diagnostic{index + 1}"] = item
    for item in refs.values():
        bound(item)
    proof = read(bound(refs["formationValidation"]))
    formation = read(bound(refs["formation"]))
    schedule = read(bound(refs["parentSchedule"]))
    require(proof["passed"] and proof["allCandidateEventsIndependentlyEnumerated"] == len(formation["allCandidates"]),
            "Complete pre-ranking population is not independently enumerated")
    require(all(row["signalDate"] <= CUTOFF for row in formation["allCandidates"]), "Later candidates forbidden")
    return parent, refs, formation, schedule


def validate_slots(parent_system, original, system, schedule):
    """Same daily sector count/rank; unchanged B symbols; no rejected-rank refill."""
    def slots(rows, source):
        return Counter((row["signalDate"], source["metadata"][row["signalId"]]["sectorCode"],
                        source["metadata"][row["signalId"]]["pattern"], row["originalRank"]) for row in rows)
    require(slots(original, parent_system) == slots(schedule, system), "Original sector/rank/pattern slots changed")
    old_b = [row for row in original if parent_system["metadata"][row["signalId"]]["pattern"] == "B_corePullback"]
    new_b = [row for row in schedule if system["metadata"][row["signalId"]]["pattern"] == "B_corePullback"]
    require(old_b == new_b, "Original B stream changed")
    require(all(system["metadata"][row["signalId"]] == parent_system["metadata"][row["signalId"]]
                for row in old_b), "Original B metadata changed")
    require(len(schedule) == len(original), "Candidate count changed during selection")


def formation_prefix(formation, end):
    daily = [row for row in formation["daily"] if row["date"] <= end]
    snapshots = {row["snapshotId"] for row in daily}
    return {**formation, "daily": daily,
        "allCandidates": [row for row in formation["allCandidates"] if row["signalDate"] <= end],
        "metadata": {key: value for key, value in formation["metadata"].items() if value["signalDate"] <= end},
        "schedules": {key: [row for row in rows if row["signalDate"] <= end]
                      for key, rows in formation["schedules"].items()},
        "sectorMaps": {key: value for key, value in formation["sectorMaps"].items() if key in snapshots}}


def matrix_prefix(matrix, end):
    count = sum(str(day) <= end for day in matrix["calendar"])
    require(count > 0, "Empty formation matrix prefix")
    return {key: value[:count] if key == "calendar"
            else value[:, :count] if getattr(value, "ndim", 0) == 2 and value.shape[1] == len(matrix["calendar"])
            else value for key, value in matrix.items()}


def pins(parent, extra_files):
    from service.research_fixed_signal_account import _source_pins
    result = dict(parent["sourcePins"])
    for path, expected in result.items():
        bound({"path": path, "sha256": expected})
    result.update(_source_pins())
    files = ["scripts/run-ignition-selection-review.py", "service/research_ignition_selection.py",
        "scripts/run-dual-confirmation-review.py", "tests/test_ignition_selection_runner.py",
        "tests/test_research_ignition_selection.py", "scripts/verify-guiyin-dual-path-system.py",
        "service/research_account_outcomes.py", "service/research_market_sector_stock.py", *extra_files]
    result.update({path: sha(ROOT / path) for path in files})
    return result


def prepare():
    require(not OUT.exists(), "Preserve previous experiment; no overwrite")
    hypothesis = read(HYPOTHESIS)
    validate_plan(hypothesis)
    import numpy as np
    from service.research_ignition_selection import build_ignition_inputs
    parent, sources, formation, parent_schedule = load_formation_sources(hypothesis)
    test_modules = ["tests.test_ignition_selection_runner", "tests.test_research_ignition_selection",
                    *hypothesis.get("additionalTestModules", [])]
    testing = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "unittest", *test_modules, "-v"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "TEMP": "H:/codex/tmp", "TMP": "H:/codex/tmp", "PYTHONUTF8": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    require(testing.returncode == 0, "Pre-account synthetic tests failed: " + testing.stderr)
    source_pins = pins(parent, hypothesis.get("additionalSourceFiles", []))
    with np.load(bound(sources["matrix"]), allow_pickle=False) as stored:
        matrix = {key: stored[key] for key in stored.files}
    matrix["explicitSuspended"] = np.load(bound(sources["suspensions"]), allow_pickle=False)
    require(set(matrix) >= {"symbols", "calendar", "priceValid", "open", "high", "low", "close", "volume", "amount"},
            "Matrix schema changed")
    require(len(matrix["symbols"]) == 4999 and str(matrix["calendar"][-1]) == CUTOFF,
            "Full matrix universe/cutoff changed")
    require(matrix["explicitSuspended"].shape == matrix["priceValid"].shape,
            "Suspension evidence is not aligned with the full price matrix")
    OUT.mkdir(parents=True)
    write(OUT / "hypothesis-plan.json", hypothesis)
    write(OUT / "synthetic-tests.json", {"passed": True, "modules": test_modules,
          "stdout": testing.stdout, "stderr": testing.stderr, "completedAt": now(), "marketAccountsRun": 0})
    manifest = {"schema": VERSION, "registeredAt": now(), "hypothesis": ref(HYPOTHESIS),
        "sourceRefs": sources, "sourcePins": source_pins, "syntheticTests": ref(OUT / "synthetic-tests.json"),
        "baseConfig": deepcopy(parent["baseConfig"]), "caseSpecs": deepcopy(hypothesis["accounts"]["cases"]),
        "periodInputs": {}, "jobs": [], "plannedAccounts": 9, "newHypotheses": 2,
        "newAccountsRun": 0, "newSegmentConsumed": False, "pristineHoldout": False}
    for path in source_pins:
        target = output_path(OUT / "frozen-code" / path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write((ROOT / path).read_bytes())
    wanted = set()
    for period, (start, end) in PERIODS.items():
        cases = {}
        originals = [row for row in parent_schedule if start <= row["signalDate"] <= end]
        bounded_formation, bounded_matrix = formation_prefix(formation, end), matrix_prefix(matrix, end)
        bounded_parent = [row for row in parent_schedule if row["signalDate"] <= end]
        for case in manifest["caseSpecs"]:
            emit({"phase": "formCandidates", "period": period, "case": case["id"], "accountsRun": 0})
            system, schedule, proof = build_ignition_inputs(bounded_formation, bounded_matrix, bounded_parent, case, start, end)
            validate_slots(formation, originals, system, schedule)
            require(all(row["date"] <= end for row in system["daily"]), "Future system context escaped")
            if case["id"] == "baseline":
                require(schedule == originals, "Baseline signal identities changed")
            require(all(start <= row["signalDate"] <= end for row in schedule), "Cross-period signal escaped")
            folder = OUT / "inputs" / period / case["id"]
            write(folder / "system-inputs.json.gz", system)
            write(folder / "signals.json", schedule)
            write(folder / "formation-proof.json", proof)
            wanted.update(row["symbol"] for row in schedule)
            cases[case["id"]] = {"system": ref(folder / "system-inputs.json.gz"),
                "schedule": ref(folder / "signals.json"), "formationProof": ref(folder / "formation-proof.json"),
                "signals": len(schedule), "originalSlots": len(originals)}
            for bps in ([10, 35] if period == "reusedTemporalCheck" else [10]):
                manifest["jobs"].append({"id": f"{period}__{case['id']}__{bps}bps", "period": period,
                    "case": case["id"], "start": start, "end": end, "slippageBps": bps,
                    "costCase": "normal" if bps == 10 else "stress", "initialCapital": 500000, "coldStart": True})
        manifest["periodInputs"][period] = {"cases": cases}
    del matrix, formation, parent_schedule, system, schedule, proof, bounded_formation, bounded_matrix, bounded_parent
    gc.collect()
    frozen_union = sorted(wanted)
    write(OUT / "frozen-execution-symbols.json", {"symbols": frozen_union, "count": len(frozen_union),
        "allSchedulesFrozenBeforeSourceProjection": True, "old1120ProjectionUsed": False})
    dataset = project_source(bound(sources["fullDataset"]), frozen_union, progress=emit)
    manifest["projection"] = ref(OUT / "frozen-execution-symbols.json")
    for period, (start, end) in PERIODS.items():
        per = manifest["periodInputs"][period]
        baseline = per["cases"]["baseline"]
        # Prefix transformation is shared, but this project's writer owns outputs.
        context = read(bound(baseline["system"]))
        signals = read(bound(baseline["schedule"]))
        view, _, _, proof = SHARED.prefix_inputs(dataset, context, signals, start, end)
        folder = OUT / "inputs" / period
        write(folder / "account-data.json.gz", view)
        write(folder / "prefix-validation.json", proof)
        per["dataset"], per["prefixValidation"] = ref(folder / "account-data.json.gz"), ref(folder / "prefix-validation.json")
        available = {security["symbol"] for security in view["securities"]}
        for case in per["cases"].values():
            require(all(row["symbol"] in available for row in read(bound(case["schedule"]))),
                    "A new selected candidate lacks prefix history")
        del view, context, signals
        gc.collect()
    require(len(manifest["jobs"]) == 9, "Unexpected registered account count")
    for path, expected in source_pins.items():
        bound({"path": path, "sha256": expected})
    for item in sources.values():
        bound(item)
    require(ref(HYPOTHESIS) == manifest["hypothesis"], "Hypothesis changed during preparation")
    write(OUT / "execution-plan.json", manifest)
    write(OUT / "prepare-completion.json", {"passed": True, "completedAt": now(),
        "plan": ref(OUT / "execution-plan.json"), "allSchedulesFrozenBeforeAccounts": True,
        "newAccountsRun": 0, "oldExecutionProjectionUsed": False, "newSegmentConsumed": False})
    emit({"prepared": True, "plan": ref(OUT / "execution-plan.json"), "plannedAccounts": 9,
          "executionSymbols": len(frozen_union), "accountsRun": 0})


def verified_plan():
    receipt = read(OUT / "prepare-completion.json")
    require(receipt["passed"] and receipt["newAccountsRun"] == 0, "Preparation is incomplete")
    plan = read(bound(receipt["plan"]))
    validate_plan(read(bound(plan["hypothesis"])))
    require(read(bound(plan["syntheticTests"]))["passed"], "Synthetic tests are not bound")
    for path, expected in plan["sourcePins"].items():
        bound({"path": path, "sha256": expected})
    for item in plan["sourceRefs"].values():
        bound(item)
    require(len(plan["jobs"]) == 9, "Frozen account count changed")
    return plan


def account_folder(job):
    return OUT / "accounts" / job["period"] / job["case"] / job["costCase"]


def run(job_id=None):
    from service.research_ignition_selection import make_account
    from service.research_account_fees import LegacyFeePolicy
    from service.research_account_outcomes import summarize_account
    plan, oracle = verified_plan(), SHARED.oracle_module()
    cases = {case["id"]: case for case in plan["caseSpecs"]}
    require(job_id is None or job_id in {job["id"] for job in plan["jobs"]}, "Unknown frozen account")
    jobs = [job for job in plan["jobs"] if job_id is None or job["id"] == job_id]
    for index, job in enumerate(jobs, 1):
        folder = account_folder(job)
        if (folder / "completion.json").exists():
            saved = read(folder / "completion.json")
            require(saved["plan"] == ref(OUT / "execution-plan.json") and saved["job"] == job,
                    "Completed account belongs to another plan")
            for item in saved["artifacts"].values():
                bound(item)
            emit({"phase": "alreadyCompleted", "job": job["id"]})
            continue
        require(not folder.exists(), "Preserve incomplete account: " + job["id"])
        period = plan["periodInputs"][job["period"]]
        case = period["cases"][job["case"]]
        data, system, signals = (read(bound(item)) for item in (period["dataset"], case["system"], case["schedule"]))
        config = deepcopy(plan["baseConfig"])
        config.update(startDate=job["start"], endDate=job["end"], initialCapital=job["initialCapital"], slippageBps=job["slippageBps"])
        write(folder / "run-start.json", {"startedAt": now(), "plan": ref(OUT / "execution-plan.json"), "job": job})
        emit({"phase": "accountStart", "job": job["id"], "index": index, "total": len(jobs), "signals": len(signals)})
        try:
            engine = make_account(data, system, signals, config, cases[job["case"]], fee_policy=LegacyFeePolicy())
            engine.step(20000)
            result, checkpoint = engine.result(), engine.checkpoint()
            summary = summarize_account(result, checkpoint, policy_id=job["case"], policy_label=job["case"])
            require(result["status"] == "completed" and not summary["quality"]["integrityIssues"], "Account/cash bridge failed")
            audit, metrics = oracle.account_audit(result, data, system), oracle.metrics(result)
            artifacts = {}
            for name, value in (("result.json.gz", result), ("checkpoint.json.gz", checkpoint),
                ("summary.json", summary), ("metrics.json", metrics), ("validation.json", audit)):
                write(folder / name, value)
                artifacts[name] = ref(folder / name)
            verified_plan()
            write(folder / "completion.json", {"completedAt": now(), "plan": ref(OUT / "execution-plan.json"),
                "job": job, "artifacts": artifacts, "independentCashAuditPassed": audit["passed"],
                "independentSelectionEntryReviewPending": True, "pristineHoldout": False,
                "checkpointMeaning": "Evidence only; restart restoration is not implemented", "targetAchieved": False})
            emit({"phase": "accountCompleted", "job": job["id"], "index": index, "total": len(jobs),
                  "netReturnPct": metrics["netReturnPct"], "closed": metrics["closed"], "independentCashAuditPassed": True})
            del engine, result, checkpoint, summary, audit, metrics
        except Exception as error:
            write(folder / "failure.json", {"failedAt": now(), "job": job, "type": type(error).__name__, "message": str(error)})
            raise
        finally:
            del data, system, signals
            gc.collect()
    if all((account_folder(job) / "completion.json").exists() for job in plan["jobs"]):
        target = OUT / "all-accounts-completion.json"
        if not target.exists():
            rows = [{"job": job, "metrics": read(account_folder(job) / "metrics.json"),
                **{key: ref(account_folder(job) / filename) for key, filename in (
                    ("metricsRef", "metrics.json"), ("summary", "summary.json"), ("result", "result.json.gz"),
                    ("validation", "validation.json"), ("completion", "completion.json"))}} for job in plan["jobs"]]
            write(OUT / "account-index.json", {"plan": ref(OUT / "execution-plan.json"), "rows": rows})
            write(target, {"completedAt": now(), "plan": ref(OUT / "execution-plan.json"),
                "index": ref(OUT / "account-index.json"), "accounts": [row["completion"] for row in rows],
                "newAccountsRun": 9, "newHypotheses": 2, "independentSelectionEntryReviewPending": True,
                "pristineHoldout": False, "targetAchieved": False})


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify", "run"))
    parser.add_argument("--job", help="One frozen account id; cannot add configurations")
    args = parser.parse_args()
    if args.command == "prepare":
        require(args.job is None, "Cannot prepare a subset")
        prepare()
    elif args.command == "verify":
        emit({"verified": True, "plannedAccounts": len(verified_plan()["jobs"]), "accountsRun": 0})
    else:
        run(args.job)
