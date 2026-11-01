"""Frozen-mask integration and run guards using synthetic data only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.research_account_fees import LegacyFeePolicy
from service.research_fixed_signal_account import FixedSignalAccount
from service.research_volatility_trail import VolatilityTrailAccount
from tests.test_research_volatility_trail import fixture, configuration, schedule

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("all_opportunity_runner", ROOT / "scripts/run-all-opportunity-feedback.py")
R = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)


def fixture_inputs(indexes=(25, 27)):
    data = fixture(); signals = schedule(data, indexes=indexes)
    for signal in signals: signal["strategyId"] = R.SID
    key = {"period": "2022-2023", "board": "main", "exit": "trail10"}
    targets = data["calendar"][25:]
    decisions = [{**key, "sample": "originalEntered", "formationDate": d,
        "asOfDate": data["calendar"][data["calendar"].index(d) - 1],
        "models": {mode: {"status": "fallback", "reason": "insufficientTrainingFormationDays",
                          "prediction": None, "allow": True} for mode in R.MODELS}} for d in targets]
    return data, signals, decisions, {"calendar": data["calendar"], "target_dates": targets,
                                     "key": key, "population": "originalEntered"}


def set_prediction(row, mode, prediction):
    row["models"][mode] = {"status": "fitted", "prediction": prediction, "allow": prediction > 0,
                            "reason": "positivePrediction" if prediction > 0 else "nonPositivePrediction"}


def guard_fixture():
    hashes = {k: "hash-" + k for k in ("plan", "completion", "validation", "tests", "verifier")}
    plan = {"sourcePins": {"example.py": "abc"}, "accountArtifacts": {}, "baselineSchedules": {},
            "sourceData": {}, "decisionArtifacts": {}, "libraryPaths": {}}
    groups = []
    for p in R.PERIODS:
        for field in ("accountArtifacts", "baselineSchedules", "sourceData", "decisionArtifacts", "libraryPaths"):
            plan[field][p] = {}
        for b in R.BOARDS:
            plan["accountArtifacts"][p][b] = {}; plan["decisionArtifacts"][p][b] = {}; plan["libraryPaths"][p][b] = {}
            plan["baselineSchedules"][p][b] = {"path": p + b + "signals", "sha256": "s"}
            plan["sourceData"][p][b] = {"accountData": {"path": p + b + "data", "sha256": "d"}}
            for e in R.EXITS:
                key = {"period": p, "board": b, "exit": e}
                baseline = {e + "." + suffix: {"path": p + b + e + suffix, "sha256": "x"}
                            for suffix in ("result.json.gz", "checkpoint.json.gz", "summary.json")}
                decisions = {pop: {"path": p + b + e + pop, "sha256": "y"} for pop in R.POPULATIONS}
                paths = {"path": p + b + e + "paths", "sha256": "z"}
                plan["accountArtifacts"][p][b][e] = baseline
                plan["decisionArtifacts"][p][b][e] = decisions; plan["libraryPaths"][p][b][e] = paths
                candidates = {c: {"population": R.dimensions(c)[0], "model": R.dimensions(c)[1],
                    "schedule": {"path": p + b + e + c, "sha256": c}} for c in R.CANDIDATES}
                groups.append({**key, "baseline": baseline, "baselineSchedule": plan["baselineSchedules"][p][b],
                    "accountData": plan["sourceData"][p][b]["accountData"], "decisions": decisions,
                    "libraryPaths": paths, "candidates": candidates})
    tests = {"passed": True, "planSha256": hashes["plan"], "sourcePins": deepcopy(plan["sourcePins"]),
             "allChecksBeforeFirstNewAccount": True, "completedAt": "2026-10-17T01:00:00+00:00"}
    completion = {"planSha256": hashes["plan"], "preAccountTestsSha256": hashes["tests"],
        "allSchedulesFrozenBeforeAccounts": True, "newAccountsExecuted": 0, "modelsRefitted": 0, "pathsExecuted": 0,
        "groups": groups, "counts": {"preparedSchedules": 48}, "completedAt": "2026-10-17T01:01:00+00:00"}
    validation = {"passed": True, "planSha256": hashes["plan"], "inputCompletionSha256": hashes["completion"],
                  "preAccountTestsSha256": hashes["tests"], "verifierSha256": hashes["verifier"]}
    return plan, completion, validation, tests, hashes


class AllOpportunityRunnerTests(unittest.TestCase):
    def test_four_masks_preserve_fallback_identity_and_deepcopy(self):
        data, signals, rows, args = fixture_inputs()
        saved = deepcopy(signals)
        for population in R.POPULATIONS:
            daily = deepcopy(rows)
            for row in daily: row["sample"] = population
            for model in R.MODELS:
                result = R.select_schedule(signals, daily, model, **{**args, "population": population})
                self.assertEqual(result, saved)
                result[0]["originalRank"] = 5
                self.assertEqual(signals, saved)

    def test_strict_zero_rejection_keeps_original_rank_and_date_without_backfill(self):
        _, signals, rows, args = fixture_inputs()
        signals[1]["originalRank"] = 4
        set_prediction(rows[0], "ridge", 0.)
        selected = R.select_schedule(signals, rows, "ridge", **args)
        self.assertEqual(selected, [signals[1]])
        counts = R.mask_counts(signals, rows, "ridge")
        self.assertEqual((counts["scheduledSignals"], counts["rejectedSignals"], counts["fallbackAdmittedSignals"]), (1, 1, 1))

    def test_future_rows_cannot_shift_existing_signal_clock(self):
        _, signals, rows, args = fixture_inputs()
        before = R.select_schedule(signals, rows, "ridge", **args)
        for row in rows[4:]: set_prediction(row, "ridge", -100.)
        self.assertEqual(R.select_schedule(signals, rows, "ridge", **args), before)
        bad = deepcopy(rows); bad[0]["asOfDate"] = bad[0]["formationDate"]
        with self.assertRaisesRegex(ValueError, "T-1"):
            R.select_schedule(signals, bad, "ridge", **args)

    def test_bad_population_dates_duplicate_and_nonboolean_allow_rejected(self):
        _, signals, rows, args = fixture_inputs()
        for kind in ("population", "duplicateDay", "missingDay", "reorder", "allow", "fallback", "signalDuplicate", "signalOrder"):
            rs, ss = deepcopy(rows), deepcopy(signals)
            if kind == "population": rs[0]["sample"] = "allExecutable"
            elif kind == "duplicateDay": rs.append(deepcopy(rs[0]))
            elif kind == "missingDay": rs.pop()
            elif kind == "reorder": rs.reverse()
            elif kind == "allow": rs[0]["models"]["ridge"]["allow"] = 1
            elif kind == "fallback": rs[0]["models"]["ridge"]["allow"] = False
            elif kind == "signalDuplicate": ss.append(deepcopy(ss[0]))
            else: ss.reverse()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                R.select_schedule(ss, rs, "ridge", **args)

    def test_preserved_schedule_matches_real_synthetic_account_for_both_exits(self):
        data, signals, rows, args = fixture_inputs(indexes=(25,))
        for exit_name, cls in (("trail10", FixedSignalAccount), ("volatilityTrail", VolatilityTrailAccount)):
            cfg = configuration(data, trailingStopPct=.1 if exit_name == "trail10" else 0.)
            cfg["strategies"][0]["id"] = R.SID; cfg["allocation"]["strategyIds"] = [R.SID]
            baseline = cls(data, signals, deepcopy(cfg), entry_weight=.2, fee_policy=LegacyFeePolicy())
            baseline.step(20000); expected = baseline.result()
            selected = R.select_schedule(signals, rows, "ridge", **args)
            actual, checkpoint = R.execute_account(data, selected, cfg, exit_name, LegacyFeePolicy())
            for field in ("trades", "curve", "closedTrades", "positions"):
                self.assertEqual(actual[field], expected[field])
            self.assertEqual(actual["trades"][0]["date"], data["calendar"][26])
            self.assertEqual(actual["research"]["identity"]["signalSchedule"], signals)
            self.assertEqual(actual["status"], "completed")

    def test_empty_mask_replays_cash_account_instead_of_filtering_old_ledger(self):
        data, signals, rows, args = fixture_inputs(indexes=(25,))
        set_prediction(rows[0], "intercept", -.1)
        selected = R.select_schedule(signals, rows, "intercept", **args)
        self.assertEqual(selected, [])
        for exit_name in R.EXITS:
            cfg = configuration(data, trailingStopPct=.1 if exit_name == "trail10" else 0.)
            cfg["strategies"][0]["id"] = R.SID; cfg["allocation"]["strategyIds"] = [R.SID]
            result, _ = R.execute_account(data, selected, cfg, exit_name, LegacyFeePolicy())
            self.assertEqual(result["trades"], [])
            self.assertTrue(all(r["equity"] == cfg["initialCapital"] and r["marketValue"] == 0 for r in result["curve"]))

    def test_all_48_masks_and_exact_input_proof_are_required_before_any_run(self):
        fixture_args = guard_fixture()
        R.validate_prerequisites(*fixture_args)
        for kind in ("missingGroup", "missingMask", "tests", "proof", "refitted", "dimension", "source", "time"):
            p, c, v, t, h = deepcopy(fixture_args)
            if kind == "missingGroup": c["groups"].pop()
            elif kind == "missingMask": c["groups"][0]["candidates"].pop(R.CANDIDATES[0])
            elif kind == "tests": t["passed"] = False
            elif kind == "proof": v["preAccountTestsSha256"] = "changed"
            elif kind == "refitted": c["modelsRefitted"] = 1
            elif kind == "dimension": c["groups"][0]["candidates"][R.CANDIDATES[0]]["model"] = "ridge"
            elif kind == "source": c["groups"][0]["accountData"] = {"path": "other", "sha256": "other"}
            else: t["completedAt"] = "2026-10-17T02:00:00+00:00"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                R.validate_prerequisites(p, c, v, t, h)

    def test_parallel_receipt_is_complete_and_created_once(self):
        with tempfile.TemporaryDirectory(prefix="all-feedback-test-") as name:
            path = Path(name) / "receipt.json"
            value = {"plan": "fixed", "rows": list(range(100))}
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: R.atomic_once(path, value), range(12)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(R.read(path), value)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["receipt.json"])

    def test_partial_group_requires_inspection_and_group_selection_is_disjoint(self):
        groups = guard_fixture()[1]["groups"]
        chosen = [R.selected_groups(groups, p + "/" + b) for p in R.PERIODS for b in R.BOARDS]
        self.assertEqual(len({R.group_key(g) for collection in chosen for g in collection}), 12)
        with self.assertRaises(ValueError): R.selected_groups(groups, "2024-2025/main/trail10")
        with tempfile.TemporaryDirectory(prefix="all-feedback-test-") as name:
            folder = Path(name)
            R.require_unstarted_group(folder)
            (folder / "allExecutable__ridge.result.json.gz").write_bytes(b"partial")
            with self.assertRaisesRegex(ValueError, "inspection"):
                R.require_unstarted_group(folder)

    def test_terminal_group_reuses_exact_baseline_and_never_accepts_changed_masks(self):
        p, c, v, t, h = guard_fixture(); group = c["groups"][0]
        receipt = {**{k: group[k] for k in ("period", "board", "exit")}, "completed": True,
            "planSha256": h["plan"], "inputCompletionSha256": h["completion"], "inputValidationSha256": h["validation"],
            "preAccountTestsSha256": h["tests"], "runStartSha256": "start", "rows": []}
        for candidate in ("baseline", *R.CANDIDATES):
            population, model = (None, None) if candidate == "baseline" else R.dimensions(candidate)
            result = group["baseline"][group["exit"] + ".result.json.gz"] if candidate == "baseline" else {"path": candidate + "result", "sha256": "x"}
            checkpoint = group["baseline"][group["exit"] + ".checkpoint.json.gz"] if candidate == "baseline" else {"path": candidate + "checkpoint", "sha256": "x"}
            receipt["rows"].append({**{k: group[k] for k in ("period", "board", "exit")}, "candidate": candidate,
                "population": population, "model": model, "reusedAccount": candidate == "baseline", "result": result,
                "checkpoint": checkpoint, "summary": {"path": candidate + "summary", "sha256": "x"},
                "schedule": group["baselineSchedule"] if candidate == "baseline" else group["candidates"][candidate]["schedule"]})
        with patch.object(R, "bound", side_effect=lambda ref: ref["path"]):
            self.assertEqual(len(R.checked_terminal(receipt, group, h, "start")), 5)
            bad = deepcopy(receipt); bad["rows"][0]["result"]["sha256"] = "different"
            with self.assertRaisesRegex(ValueError, "Baseline bytes"):
                R.checked_terminal(bad, group, h, "start")

    def test_intervals_keep_original_year_halves_and_continuous_epoch_boundaries(self):
        atlas = {"phases": [{"id": "phase-a", "startDate": "2024-01-02", "endDate": "2024-09-23", "title": "Synthetic"}]}
        spans = R.intervals("2024-2025", atlas)
        epochs = [r for r in spans if r["kind"] == "descriptiveEpoch"]
        self.assertEqual(epochs, [{"id": "before20240924", "start": "2024-01-02", "end": "2024-09-23", "kind": "descriptiveEpoch"},
                                 {"id": "from20240924", "start": "2024-09-24", "end": "2025-12-31", "kind": "descriptiveEpoch"}])
        self.assertEqual(sum(r["kind"] == "calendarYear" for r in spans), 2)
        self.assertEqual(sum(r["kind"] == "calendarHalfYear" for r in spans), 4)


if __name__ == "__main__": unittest.main()
