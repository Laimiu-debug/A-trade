"""Meaningful synthetic switch, inherited-account and publication boundaries."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.research_account_fees import LegacyFeePolicy
from service.research_fixed_signal_account import FixedSignalAccount
from service.research_volatility_trail import VolatilityTrailAccount
from tests.test_research_volatility_trail import fixture, configuration, schedule

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("common_support_runner", ROOT / "scripts/run-common-support-switch.py")
R = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)


def fitted(value):
    return {"status": "fitted", "prediction": value, "allow": value > 0,
            "reason": "positivePrediction" if value > 0 else "nonPositivePrediction"}


def synthetic_fixture(indexes=(25, 26, 27, 28)):
    data = fixture()
    signals = schedule(data, indexes=indexes)
    for item in signals: item["strategyId"] = R.SID
    key = {"period": "2022-2023", "board": "main", "exit": "trail10"}
    targets = data["calendar"][25:]
    libraries = []
    for population in R.POPULATIONS:
        libraries.append([{**key, "sample": population, "formationDate": day,
            "asOfDate": data["calendar"][data["calendar"].index(day) - 1],
            "models": {model: {"status": "fallback", "prediction": None, "allow": True,
                "reason": "insufficientTrainingFormationDays"} for model in R.MODELS}} for day in targets])
    return data, signals, *libraries, {"calendar": data["calendar"], "target_dates": targets, "key": key}


def fake_ref(name):
    return {"path": "synthetic/" + name, "sha256": "hash:" + name}


def guard_fixture():
    hashes = {key: "hash-" + key for key in ("plan", "completion", "validation", "tests", "verifier")}
    plan = {"sourcePins": {"example.py": "abc"}, "referenceRows": [], "baselineSchedules": {},
            "sourceData": {}, "decisionArtifacts": {}, "rules": {"trialAccounting": {
                "newAccounts": 24, "referenceAccountsReused": 60, "comparisonAccounts": 84}}}
    groups = []
    for p in R.PERIODS:
        for field in ("baselineSchedules", "sourceData", "decisionArtifacts"): plan[field][p] = {}
        for b in R.BOARDS:
            plan["baselineSchedules"][p][b] = fake_ref(p + b + "originalSignals")
            plan["sourceData"][p][b] = {"accountData": fake_ref(p + b + "source")}
            plan["decisionArtifacts"][p][b] = {}
            for e in R.EXITS:
                key = {"period": p, "board": b, "exit": e}
                inherited = []
                for candidate in R.REFERENCE_CANDIDATES:
                    population, model = (None, None) if candidate == "baseline" else candidate.split("__")
                    inherited.append({**key, "candidate": candidate, "population": population, "model": model,
                        "reusedAccount": candidate == "baseline",
                        **{kind: fake_ref(p + b + e + candidate + kind) for kind in ("result", "checkpoint", "summary", "schedule")}})
                plan["referenceRows"].extend(deepcopy(inherited))
                decisions = {pop: fake_ref(p + b + e + pop) for pop in R.POPULATIONS}
                plan["decisionArtifacts"][p][b][e] = decisions
                candidates = {c: {"population": "commonSupport", "model": R.dimensions(c)[1],
                    "schedule": fake_ref(p + b + e + c)} for c in R.CANDIDATES}
                groups.append({**key, "inheritedRows": inherited, "baselineSchedule": plan["baselineSchedules"][p][b],
                    "accountData": plan["sourceData"][p][b]["accountData"], "decisions": decisions, "candidates": candidates})
    tests = {"passed": True, "planSha256": hashes["plan"], "sourcePins": deepcopy(plan["sourcePins"]),
             "allChecksBeforeFirstNewAccount": True, "completedAt": "2026-10-17T01:00:00+00:00"}
    completion = {"planSha256": hashes["plan"], "preAccountTestsSha256": hashes["tests"],
        "allSchedulesFrozenBeforeAccounts": True, "newAccountsExecuted": 0, "modelsRefitted": 0, "pathsExecuted": 0,
        "groups": groups, "counts": {"preparedGroups": 12, "originalSchedules": 6, "preparedSchedules": 24,
            "sourceImmediateOpportunities": 8155, "sourceExitOpportunities": 16310, "referenceAccounts": 60},
        "completedAt": "2026-10-17T01:01:00+00:00"}
    validation = {"passed": True, "planSha256": hashes["plan"], "inputCompletionSha256": hashes["completion"],
        "preAccountTestsSha256": hashes["tests"], "verifierSha256": hashes["verifier"]}
    return plan, completion, validation, tests, hashes


def terminal_fixture(group, hashes, output):
    rows = deepcopy(group["inheritedRows"])
    for candidate in R.CANDIDATES:
        refs = {kind: {"path": (output.joinpath(*R.group_key(group)) / (candidate + suffix)).relative_to(ROOT).as_posix(),
                      "sha256": "synthetic"}
                for kind, suffix in (("result", ".result.json.gz"), ("checkpoint", ".checkpoint.json.gz"), ("summary", ".summary.json"))}
        rows.append({**{k: group[k] for k in ("period", "board", "exit")}, "candidate": candidate,
            "population": "commonSupport", "model": R.dimensions(candidate)[1], "reusedAccount": False,
            **refs, "schedule": group["candidates"][candidate]["schedule"]})
    return {**{k: group[k] for k in ("period", "board", "exit")}, "completed": True,
        "planSha256": hashes["plan"], "inputCompletionSha256": hashes["completion"],
        "inputValidationSha256": hashes["validation"], "preAccountTestsSha256": hashes["tests"],
        "runStartSha256": "start", "rows": rows, "newAccounts": 2, "referenceAccountsReused": 5}


class CommonSupportRunnerTests(unittest.TestCase):
    def test_all_four_support_states_keep_original_outside_common_support(self):
        _, signals, original, broad, args = synthetic_fixture()
        original[0]["models"]["ridge"] = fitted(1)
        broad[0]["models"]["ridge"] = fitted(-1)
        original[1]["models"]["ridge"] = fitted(-1)
        broad[2]["models"]["ridge"] = fitted(-1)
        selected, counts = R.select_schedule(signals, original, broad, "ridge", **args)
        self.assertEqual(selected, signals[2:])
        self.assertEqual(counts["supportStateSignals"], dict.fromkeys(R.SUPPORT_STATES, 1))
        self.assertEqual(counts["selectedPopulationSignals"], {"originalEntered": 3, "allExecutable": 1})
        self.assertEqual((counts["changedAllowSignals"], counts["addedSignals"], counts["removedSignals"]), (1, 0, 1))
        self.assertEqual(sum(counts["supportStateDays"].values()), len(args["target_dates"]))

    def test_common_support_can_add_and_remove_and_zero_remains_rejected(self):
        _, signals, original, broad, args = synthetic_fixture()
        for index, (left, right) in enumerate(((-1, 1), (1, 0), (0, -1), (1, 1))):
            original[index]["models"]["intercept"] = fitted(left)
            broad[index]["models"]["intercept"] = fitted(right)
        selected, counts = R.select_schedule(signals, original, broad, "intercept", **args)
        self.assertEqual(selected, [signals[0], signals[3]])
        self.assertEqual((counts["changedAllowSignals"], counts["addedSignals"], counts["removedSignals"]), (2, 1, 1))
        self.assertEqual(counts["supportStateSignals"], {s: 4 if s == "bothFitted" else 0 for s in R.SUPPORT_STATES})

    def test_each_model_has_its_own_common_support_and_mask(self):
        _, signals, original, broad, args = synthetic_fixture(indexes=(25,))
        original[0]["models"]["ridge"] = fitted(1); broad[0]["models"]["ridge"] = fitted(-1)
        broad[0]["models"]["intercept"] = fitted(-1)
        self.assertEqual(R.select_schedule(signals, original, broad, "ridge", **args)[0], [])
        self.assertEqual(R.select_schedule(signals, original, broad, "intercept", **args)[0], signals)

    def test_original_order_rank_identity_and_inputs_unchanged(self):
        _, signals, original, broad, args = synthetic_fixture(indexes=(25, 27))
        signals[1]["originalRank"] = 4
        original[0]["models"]["ridge"] = fitted(1); broad[0]["models"]["ridge"] = fitted(-1)
        saved = deepcopy((signals, original, broad))
        selected, _ = R.select_schedule(signals, original, broad, "ridge", **args)
        self.assertEqual(selected, [signals[1]])
        selected[0]["originalRank"] = 1
        self.assertEqual((signals, original, broad), saved)

    def test_misaligned_days_sources_and_invalid_frozen_decisions_rejected(self):
        _, signals, original, broad, args = synthetic_fixture()
        for kind in ("date", "asof", "missing", "duplicate", "source", "sample", "boolean", "fallback", "order", "nan"):
            a, b, ss = deepcopy(original), deepcopy(broad), deepcopy(signals)
            if kind == "date": b[0]["formationDate"] = b[1]["formationDate"]
            elif kind == "asof": b[0]["asOfDate"] = b[0]["formationDate"]
            elif kind == "missing": b.pop()
            elif kind == "duplicate": a.append(deepcopy(a[0]))
            elif kind == "source": b[0]["exit"] = "volatilityTrail"
            elif kind == "sample": b[0]["sample"] = "originalEntered"
            elif kind == "boolean": b[0]["models"]["ridge"]["allow"] = 1
            elif kind == "fallback": a[0]["models"]["ridge"]["allow"] = False
            elif kind == "order": ss.reverse()
            else: b[0]["models"]["ridge"] = fitted(float("nan"))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                R.select_schedule(ss, a, b, "ridge", **args)

    def test_future_append_cannot_change_earlier_schedule(self):
        _, signals, original, broad, args = synthetic_fixture(indexes=(25, 26))
        prior = R.select_schedule(signals, original[:2], broad[:2], "ridge", **{**args, "target_dates": args["target_dates"][:2]})[0]
        for index in range(2, len(original)):
            original[index]["models"]["ridge"] = fitted(1)
            broad[index]["models"]["ridge"] = fitted(-1)
        self.assertEqual(R.select_schedule(signals, original, broad, "ridge", **args)[0], prior)

    def test_account_replay_keeps_engine_clock_fees_and_empty_mask_is_cash(self):
        data, signals, original, broad, args = synthetic_fixture(indexes=(25,))
        selected, _ = R.select_schedule(signals, original, broad, "ridge", **args)
        for exit_name, cls in (("trail10", FixedSignalAccount), ("volatilityTrail", VolatilityTrailAccount)):
            cfg = configuration(data, trailingStopPct=.1 if exit_name == "trail10" else 0.)
            cfg["strategies"][0]["id"] = R.SID; cfg["allocation"]["strategyIds"] = [R.SID]
            engine = cls(data, signals, deepcopy(cfg), entry_weight=.2, fee_policy=LegacyFeePolicy()); engine.step(20000)
            actual, _ = R.execute_account(data, selected, cfg, exit_name, LegacyFeePolicy())
            expected = engine.result()
            for field in ("trades", "curve", "closedTrades", "positions"):
                self.assertEqual(actual[field], expected[field])
            self.assertEqual(actual["trades"][0]["date"], data["calendar"][26])
            original[0]["models"]["intercept"] = fitted(1); broad[0]["models"]["intercept"] = fitted(-1)
            empty, _ = R.select_schedule(signals, original, broad, "intercept", **args)
            cash, _ = R.execute_account(data, empty, cfg, exit_name, LegacyFeePolicy())
            self.assertEqual(cash["trades"], [])
            self.assertTrue(all(r["equity"] == cfg["initialCapital"] for r in cash["curve"]))

    def test_all_24_masks_60_exact_references_and_audit_must_precede_any_account(self):
        R.validate_prerequisites(*guard_fixture())
        for kind in ("group", "mask", "oldSummary", "oldFlag", "count", "tests", "proof", "refit", "time"):
            p, c, v, t, h = deepcopy(guard_fixture())
            if kind == "group": c["groups"].pop()
            elif kind == "mask": c["groups"][0]["candidates"].pop(R.CANDIDATES[0])
            elif kind == "oldSummary": c["groups"][0]["inheritedRows"][1]["summary"] = fake_ref("changed")
            elif kind == "oldFlag": c["groups"][0]["inheritedRows"][1]["reusedAccount"] = True
            elif kind == "count": c["counts"]["preparedSchedules"] = 23
            elif kind == "tests": t["passed"] = False
            elif kind == "proof": v["preAccountTestsSha256"] = "changed"
            elif kind == "refit": c["modelsRefitted"] = 1
            else: t["completedAt"] = "2026-10-17T02:00:00+00:00"
            with self.subTest(kind=kind), self.assertRaises(ValueError): R.validate_prerequisites(p, c, v, t, h)

    def test_completed_group_retains_parent_historical_flags_and_exact_four_refs(self):
        _, c, _, _, h = guard_fixture(); group = c["groups"][0]
        receipt = terminal_fixture(group, h, R.OUT)
        with patch.object(R, "bound", side_effect=lambda item: item["path"]):
            self.assertEqual(len(R.checked_terminal(receipt, group, h, "start")), 7)
            self.assertEqual(sum(r["reusedAccount"] for r in receipt["rows"]), 1)
            for kind in ("summary", "flag", "newPath", "mask", "count"):
                bad = deepcopy(receipt)
                if kind == "summary": bad["rows"][0]["summary"] = fake_ref("other")
                elif kind == "flag": bad["rows"][1]["reusedAccount"] = True
                elif kind == "newPath": bad["rows"][5]["result"] = fake_ref("parentresult")
                elif kind == "mask": bad["rows"][5]["schedule"] = fake_ref("differentmask")
                else: bad["newAccounts"] = 4
                with self.subTest(kind=kind), self.assertRaises(ValueError): R.checked_terminal(bad, group, h, "start")

    def test_collect_uses_explicit_current_reference_identity_and_is_idempotent(self):
        p, c, _, _, h = guard_fixture()
        # Keep all actual temporary output outside the registered research directory.
        with tempfile.TemporaryDirectory(prefix="common-support-") as temp:
            root, output = Path(temp), Path(temp) / "batch"
            output.mkdir()
            with patch.object(R, "OUT", output), patch.object(R, "bound", side_effect=lambda item: item["path"]), \
                    patch.object(R, "ref", side_effect=lambda path: {"path": str(path), "sha256": R.sha(path)}):
                missing = R.collect_comparison(p, c, h, "start")
                self.assertFalse(missing["completed"])
                self.assertFalse((output / "comparison.json").exists())
                receipts = {}
                for group in c["groups"]:
                    path = output.joinpath(*R.group_key(group)) / "group-completion.json"
                    path.parent.mkdir(parents=True); path.write_text("{}", encoding="utf-8")
                    # Path checks are tested above; here isolate global collection semantics.
                    rows = deepcopy(group["inheritedRows"])
                    rows.extend({**{k: group[k] for k in ("period", "board", "exit")}, "candidate": candidate,
                                 "reusedAccount": False} for candidate in R.CANDIDATES)
                    receipts[str(path)] = rows
                original_read = R.read
                with patch.object(R, "read", side_effect=lambda path: receipts.get(str(path), original_read(path))), \
                        patch.object(R, "checked_terminal", side_effect=lambda rows, *args: rows):
                    self.assertTrue(R.collect_comparison(p, c, h, "start")["completed"])
                    first_sha = R.sha(output / "comparison.json")
                    R.collect_comparison(p, c, h, "start")
                    self.assertEqual(R.sha(output / "comparison.json"), first_sha)
                saved = original_read(output / "comparison.json")
                self.assertEqual((len(saved["rows"]), len(saved["referenceRows"]), len(saved["newRows"])), (84, 60, 24))
                self.assertEqual(saved["referenceRows"], p["referenceRows"])
                self.assertEqual(sum(r["reusedAccount"] for r in saved["rows"]), 12)


if __name__ == "__main__": unittest.main()
