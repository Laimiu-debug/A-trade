"""Opt-in strategy integration checks; synthetic accounts and temporary workspaces only."""
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service import experimental_strategies as model
from service.experimental_strategy_service import ExperimentalStrategyService

ROOT = Path(__file__).resolve().parents[1]


def fixture_module(name):
    spec = importlib.util.spec_from_file_location("experiment_fixture_" + name, ROOT / "tests" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_account(strategy_id=model.IDS[1]):
    fixture = fixture_module("test_research_volatility_trail")
    data = fixture.fixture(36)
    sid = model.SIDS[model.IDS.index(strategy_id)]
    signals = [{**fixture.schedule(data)[0], "strategyId": sid, "signalId": sid + ":synthetic"}]
    config = fixture.configuration(data, trailingStopPct=0 if strategy_id == model.IDS[2] else .1,
        strategies=[{"id": sid, "kind": "momentum", "pool": "base"}],
        allocation={"mode": "single", "strategyIds": [sid]})
    return data, signals, config, {"profile": "legacy-engine-delegate-v1"}


def fake_catalog():
    return {"strategies": [{"id": "synthetic", "name": "合成实验", "sources": [
        {"id": "fixture", "label": "合成来源", "available": True}]}]}


def fake_result():
    return {"result": {"curve": [{"date": "2024-01-02", "equity": 101.}], "trades": [{"id": 1}]},
        "checkpoint": {}, "schedule": [], "provenance": {"synthetic": True},
        "summary": {"account": {"netReturnPct": 1., "maxDrawdownPct": 0., "finalEquity": 101., "feesPaid": .1},
            "closedEpisodes": {"count": 0}, "openPositions": {"count": 1},
            "periods": [{"exposurePct": {"mean": 19.}}], "reconciliation": {"openPositionUnrealizedPnl": 1.}}}


class OriginalStrategyIntegrationTests(unittest.TestCase):
    def test_catalogue_has_three_exact_families_and_all_research_sources_without_execution(self):
        with patch.object(model, "execute_source", side_effect=AssertionError("catalogue executed")):
            result = model.catalogue()
        self.assertEqual(tuple(r["id"] for r in result["strategies"]), model.IDS)
        self.assertEqual([len(r["sources"]) for r in result["strategies"]], [2, 6, 6])
        self.assertEqual([len(r["researchResults"]) for r in result["strategies"]], [2, 6, 6])
        self.assertFalse(result["automaticExecution"])
        self.assertEqual({s["board"] for s in result["strategies"][0]["sources"]}, {"main"})
        manifest = model._manifest()
        for path, digest in manifest["sourcePins"].items():
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest)
        for strategy in manifest["strategies"]:
            for source in strategy["sources"]:
                self.assertEqual(source["config"]["allocation"]["strategyIds"], [model.SIDS[model.IDS.index(strategy["id"])]])
                self.assertEqual(source["initialCapital"], source["config"]["initialCapital"])
                self.assertIn(source["feeSpec"]["profile"], ("dated-transfer-assumption-v1", "legacy-engine-delegate-v1"))

    def test_ur_rebuild_exact_original_schedule_and_no_mutation(self):
        fixture = fixture_module("test_research_undercut_reclaim")
        data = fixture.fixture()
        fixture.reclaim(data)
        before = deepcopy(data)
        expected = fixture.run(data)
        actual = model.build_signals(model.IDS[2], data, start=data["calendar"][61], end=data["calendar"][-1])
        self.assertEqual(actual["schedule"], expected["schedules"]["immediate"])
        self.assertGreater(len(actual["schedule"]), 0)
        self.assertEqual(data, before)

    def test_persistence_rebuild_uses_original_full_cohort_and_top5_masks(self):
        fixture = fixture_module("test_research_leader_lag")
        from service.research_leader_persistence_admission import build_leader_persistence_admission
        data = fixture.source(size=89)
        data["calendarSource"] = "synthetic-independent"
        before = deepcopy(data)
        parent = fixture.build(data, first=85, last=88)
        metadata = {k: parent[k] for k in ("strategyId", "calendar", "targetDates", "counts")}
        metadata["symbols"] = sorted(s["symbol"] for s in data["securities"])
        expected = build_leader_persistence_admission(metadata, parent["dailyRows"], start=data["calendar"][85], end=data["calendar"][-1])
        actual = model.build_signals(model.IDS[1], data, start=data["calendar"][85], end=data["calendar"][-1])
        self.assertEqual(actual["schedule"], expected["schedules"]["persistent"])
        self.assertGreater(len(actual["schedule"]), 0)
        self.assertEqual(data, before)

    def test_contraction_original_pattern_qualification_and_ranking(self):
        fixture = fixture_module("test_signal_contraction_price_proxy")
        days, old = fixture.fixture(length=130)
        bars = [deepcopy(old[0]) for _ in range(40)] + deepcopy(old[:90])
        for day, bar in zip(days, bars):
            bar["date"] = day
        data = {"calendar": days, "calendarSource": "synthetic-independent", "priceBasisId": "raw-unadjusted",
            "securities": [{"symbol": "sh600001", "name": "合成", "bars": bars}]}
        before = deepcopy(data)
        got = model.build_signals(model.IDS[0], data, start=days[120], end=days[-1])
        self.assertEqual(got["schedule"], [{"signalId": f"{model.SIDS[0]}:{days[120]}:sh600001",
            "strategyId": model.SIDS[0], "symbol": "sh600001", "signalDate": days[120], "originalRank": 1}])
        self.assertEqual(data, before)
        # Same price pattern without the original observed-history eligibility.
        short_days, short_bars = fixture.fixture()
        data.update(calendar=short_days, securities=[{"symbol": "sh600001", "bars": short_bars}])
        self.assertEqual(model.build_signals(model.IDS[0], data, start=short_days[80], end=short_days[-1])["schedule"], [])

    def test_original_three_exit_engines_next_open_and_exact_economic_results(self):
        from service.research_fixed_signal_account import FixedSignalAccount
        from service.research_structural_exits import ResearchExitAccount
        from service.research_volatility_trail import VolatilityTrailAccount
        from service.research_account_fees import LegacyFeePolicy
        for strategy_id, cls in zip(model.IDS, (ResearchExitAccount, FixedSignalAccount, VolatilityTrailAccount)):
            with self.subTest(strategy_id=strategy_id):
                data, signals, config, fees = synthetic_account(strategy_id)
                before = deepcopy((data, signals, config))
                kwargs = {"entry_weight": .2, "fee_policy": LegacyFeePolicy()}
                if strategy_id == model.IDS[0]:
                    kwargs["exit_rule"] = "noProgress5"
                expected_engine = cls(data, signals, deepcopy(config), **kwargs)
                expected_engine.step(999)
                actual = model._run(strategy_id, data, signals, config, fees)
                self.assertEqual(actual["result"], expected_engine.result())
                self.assertEqual(actual["checkpoint"], expected_engine.checkpoint())
                self.assertEqual(actual["result"]["trades"][0]["date"], data["calendar"][26])
                self.assertEqual(actual["summary"]["quality"]["integrityIssues"], [])
                self.assertEqual((data, signals, config), before)
                if strategy_id == model.IDS[0]:
                    self.assertIn("noProgress5", [t.get("reason") for t in actual["result"]["trades"]])

    def test_explicit_inputs_reject_observed_calendar_future_duplicate_and_retagging(self):
        fixture = fixture_module("test_research_undercut_reclaim")
        base = fixture.fixture()
        for change in ("observed", "duplicateSymbol", "duplicateBar", "future", "basis"):
            data = deepcopy(base)
            start, end = data["calendar"][61], data["calendar"][-1]
            if change == "observed": data["calendarSource"] = "observed-local-union-not-exchange-calendar"
            if change == "duplicateSymbol": data["securities"].append(deepcopy(data["securities"][0]))
            if change == "duplicateBar": data["securities"][0]["bars"].append(deepcopy(data["securities"][0]["bars"][-1]))
            if change == "future": end = data["calendar"][-2]
            if change == "basis": data.pop("priceBasisId")
            with self.subTest(change=change), self.assertRaises(ValueError):
                model.build_signals(model.IDS[2], data, start=start, end=end)

    def test_fee_types_and_exit_rules_cannot_silently_change(self):
        data, signals, config, fees = synthetic_account()
        config["takeProfitPct"] = .3
        with self.assertRaises(ValueError): model.make_account(model.IDS[1], data, signals, config, fees)
        config["takeProfitPct"] = 0
        signals[0]["strategyId"] = "other"
        with self.assertRaises(ValueError): model.make_account(model.IDS[1], data, signals, config, fees)
        with self.assertRaises(ValueError): model._fees({"profile": "new-current-fees"})
        manifest = model._manifest()
        old = manifest["strategies"][0]["sources"][0]["feeSpec"]
        new = manifest["strategies"][0]["sources"][1]["feeSpec"]
        self.assertEqual(old["profile"], "dated-transfer-assumption-v1")
        self.assertEqual(new["profile"], "legacy-engine-delegate-v1")
        self.assertIs(type(old["parameters"]["min_commission"]), int)

    def test_bound_reference_rejects_missing_keys_escape_and_sha_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "source.json"; path.write_text("{}", encoding="utf-8")
            good = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertEqual(model._bound(good, root=root), path)
            for bad in ({"path": path.name, "extra": 1}, {"sha256": "x", "extra": 1}, {**good, "path": "../outside"}, {**good, "sha256": "0" * 64}):
                with self.assertRaises(ValueError): model._bound(bad, root=root)

    def test_frozen_source_binds_data_and_schedule_before_account(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data, signals, config, fees = synthetic_account()
            def save(name, value):
                path = root / name
                with (gzip.open(path, "wt", encoding="utf-8") if name.endswith(".gz") else path.open("w", encoding="utf-8")) as out:
                    json.dump(value, out)
                return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            source = {"id": "fixture", "accountData": save("data.json.gz", data), "schedule": save("signals.json", signals),
                "evidenceRefs": [save("proof.json", {"passed": True})], "signalCount": len(signals),
                "accountDatasetDigest": model._digest(data), "config": config, "feeSpec": fees,
                "start": config["startDate"], "end": config["endDate"]}
            manifest = {"schema": model.VERSION, "sourcePins": {}, "strategies": [{"id": sid, "sources": [source] if sid == model.IDS[1] else []} for sid in model.IDS]}
            path = root / "manifest.json"; path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(model, "_run", return_value={"sentinel": True}) as run:
                self.assertEqual(model.execute_source(model.IDS[1], "fixture", manifest_path=path, root=root), {"sentinel": True})
                self.assertEqual(run.call_args.args[2], signals)
                self.assertFalse(run.call_args.kwargs["provenance"]["newSignalGeneration"])
            (root / "signals.json").write_text("[]", encoding="utf-8")
            with patch.object(model, "_run", side_effect=AssertionError("bad source executed")), self.assertRaises(ValueError):
                model.execute_source(model.IDS[1], "fixture", manifest_path=path, root=root)


class ExperimentalQueueTests(unittest.TestCase):
    def create(self, service, request_id="one"):
        return service.dispatch("experimental.create", {"strategyId": "synthetic", "sourceId": "fixture", "requestId": request_id})

    def test_create_is_idempotent_lazy_and_rejects_parameter_override(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            service = ExperimentalStrategyService(folder, autostart=False, executor=lambda *a, **k: calls.append(1), catalog_provider=fake_catalog)
            first = self.create(service)
            self.assertEqual(self.create(service), first)
            service.dispatch("experimental.catalogue"); service.list(); service.get({"id": first["id"]})
            self.assertEqual(calls, [])
            with self.assertRaises(ValueError): service.create({"strategyId": "synthetic", "sourceId": "fixture", "requestId": "one", "name": "changed"})
            with self.assertRaises(ValueError): service.create({"strategyId": "synthetic", "sourceId": "fixture", "requestId": "new", "config": {}})
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["experimental-strategies"])
            service.close()

    def test_complete_result_has_inspectable_summary_and_paging(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ExperimentalStrategyService(folder, autostart=False, executor=lambda *a, **k: fake_result(), catalog_provider=fake_catalog)
            task = self.create(service); service._run_one(task["id"])
            result = service.get({"id": task["id"], "offset": 0, "limit": 1})
            self.assertEqual(result["task"]["status"], "completed")
            self.assertEqual(result["summary"]["meanExposurePct"], 19.)
            self.assertIsNone(result["summary"]["meanWinPct"])
            self.assertEqual(result["curve"]["total"], 1)
            self.assertEqual(model._read(Path(result["resultRef"]["path"])), fake_result()["result"])
            with self.assertRaises(ValueError): service.get({"id": task["id"], "limit": 501})
            service.close()

    def test_queued_stop_never_executes(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ExperimentalStrategyService(folder, autostart=False,
                executor=lambda *a, **k: self.fail("stopped queue executed"), catalog_provider=fake_catalog)
            task = self.create(service); service.stop({"id": task["id"]}); service._run_one(task["id"])
            self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "stopped")
            service.close()

    def test_stop_during_output_publication_never_becomes_completed(self):
        from service import experimental_strategy_service as queue_module
        with tempfile.TemporaryDirectory() as folder:
            service = ExperimentalStrategyService(folder, autostart=False, executor=lambda *a, **k: fake_result(), catalog_provider=fake_catalog)
            task = self.create(service); real_write = queue_module._write
            def write(path, value):
                real_write(path, value)
                if Path(path).name == "trades.json": service.stop({"id": task["id"]})
            with patch.object(queue_module, "_write", side_effect=write): service._run_one(task["id"])
            result = service.get({"id": task["id"]})
            self.assertEqual(result["task"]["status"], "stopped")
            self.assertNotIn("summary", result)
            service.close()

    def test_close_while_running_preserves_interrupted_not_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ExperimentalStrategyService(folder, autostart=False, catalog_provider=fake_catalog)
            def executor(*args, **kwargs):
                service.close()
                kwargs["progress"]({"phase": "account", "completed": 1, "total": 2})
            service.executor = executor
            task = self.create(service); service._run_one(task["id"])
            self.assertEqual(service.get({"id": task["id"]})["task"]["status"], "interrupted")

    def test_reopen_pending_marks_interrupted_and_never_resumes(self):
        with tempfile.TemporaryDirectory() as folder:
            first = ExperimentalStrategyService(folder, autostart=False, catalog_provider=fake_catalog)
            task = self.create(first)
            second = ExperimentalStrategyService(folder, autostart=False, catalog_provider=fake_catalog)
            self.assertEqual(second.get({"id": task["id"]})["task"]["status"], "interrupted")
            self.assertTrue(second.pending.empty())
            second.close(); first.close()

    def test_failure_preserves_private_detail_but_public_error_omits_path(self):
        with tempfile.TemporaryDirectory() as folder:
            def fail(*args, **kwargs): raise ValueError("private source C:/user/private/history")
            service = ExperimentalStrategyService(folder, autostart=False, executor=fail, catalog_provider=fake_catalog)
            task = self.create(service); service._run_one(task["id"])
            status = service.get({"id": task["id"]})["task"]
            self.assertEqual(status["status"], "failed")
            self.assertNotIn("C:/user", status["error"])
            detail = model._read(service.directory / task["id"] / "error-detail.json")
            self.assertIn("C:/user", detail["message"])
            service.close()


if __name__ == "__main__":
    unittest.main()
