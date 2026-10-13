import copy
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from service.portfolio_config import normalize_config
from service.research_pipeline import SinglePlateau, normalize_single_experiment, single_config
from service.research_service import ResearchService


def fixture():
    days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(210)]
    bars = [{"date": day, "open": 10 + i / 100, "close": 10.01 + i / 100,
             "high": 10.03 + i / 100, "low": 9.98 + i / 100, "volume": 1000000., "amount": 12000000.}
            for i, day in enumerate(days)]
    data = {"fingerprint": "pipeline-fixture", "calendar": days, "source": "test-frozen", "warnings": [],
            "securities": [{"symbol": "sh600001", "name": "测试", "bars": bars}]}
    config = {"startDate": days[125], "endDate": days[159], "strategies": [{"id": "m", "kind": "momentum", "params": {"lookback": 3}}],
              "combinations": [], "allocation": {"mode": "single", "strategyIds": ["m"]}}
    return data, config, days


class FakeEngine:
    calls = []

    def __init__(self, dataset, config, state=None):
        self.dataset, self.config = dataset, config
        self.cursor = (state or {}).get("cursor", 0)
        self.__class__.calls.append((copy.deepcopy(dataset), copy.deepcopy(config)))

    @property
    def progress(self):
        return {"progress": self.cursor / 10}

    def step(self, days=5):
        self.cursor = min(10, self.cursor + days)
        return self.cursor == 10

    def checkpoint(self):
        return {"cursor": self.cursor}

    def result(self):
        rate = self.config["strategies"][0]["params"]["lookback"] / 100
        days = [day for day in self.dataset["calendar"] if self.config["startDate"] <= day <= self.config["endDate"]][:10]
        return {"config": self.config, "fingerprint": self.dataset["fingerprint"],
                "curve": [{"date": day, "equity": self.config["initialCapital"] * (1 + rate * (i + 1) / 10),
                           "cash": 0, "marketValue": self.config["initialCapital"] * (1 + rate * (i + 1) / 10)} for i, day in enumerate(days)],
                "trades": [],
                "metrics": {"totalReturnPct": self.config["strategies"][0]["params"]["lookback"], "maxDrawdownPct": 2,
                            "closedTradeCount": 5, "investedDays": len(days), "totalFees": 30, "totalSlippage": 10},
                "quality": {"quarantinedSymbols": [], "unaccountedActionExposure": False, "staleValuationDays": 0}}


def finish(runner):
    for _ in range(100):
        if runner.step(10):
            return runner.result()
    raise AssertionError("runner did not complete")


class SinglePlateauTests(unittest.TestCase):
    def test_strict_single_strategy_and_low_dimension_contract(self):
        _, config, _ = fixture()
        for bad in ({**config, "allocation": {"mode": "equal", "strategyIds": ["m"]}},
                    {**config, "strategies": [*config["strategies"], {"id": "other", "kind": "momentum"}]},
                    {**config, "study": {"testStart": "2024-12-01"}}, {**config, "endDate": None}):
            with self.assertRaises(ValueError):
                single_config(bad)
        for bad in ({"axes": []}, {"axes": [{"path": "rotation.lookbackDays", "values": [5, 10]}]},
                    {"axes": [{"path": "topN", "values": [1, 2, 3, 4, 5]},
                              {"path": "maxPositions", "values": [5, 6, 7, 8, 9]},
                              {"path": "maxHoldingDays", "values": [5, 10]}]},
                    {"axes": [{"path": "topN", "values": [2, True]}]}):
            with self.assertRaises(ValueError):
                normalize_single_experiment(bad, config)

    @patch("service.research_pipeline.PortfolioEngine", FakeEngine)
    def test_no_future_evaluation_resumption_and_no_automatic_selection(self):
        data, config, _ = fixture()
        experiment = {"axes": [{"path": "strategies.m.params.lookback", "values": [2, 3, 4]}]}
        FakeEngine.calls = []
        runner = SinglePlateau(data, config, experiment)
        runner.step(3)
        state = runner.checkpoint()
        resumed = SinglePlateau(data, config, experiment, state)
        result = finish(resumed)
        self.assertEqual(result, finish(SinglePlateau(data, config, experiment)))
        future_changed = copy.deepcopy(data)
        for bar in future_changed["securities"][0]["bars"]:
            if bar["date"] > config["endDate"]:
                bar["close"] *= 100
        self.assertEqual(result, finish(SinglePlateau(future_changed, config, experiment)))
        self.assertTrue(result["trainingOnly"])
        self.assertIsNone(result["selectedCandidateId"])
        self.assertFalse(result["certified"])
        self.assertTrue(any(region["intervalEligible"] for region in result["regions"]))
        for observed, settings in FakeEngine.calls:
            self.assertLessEqual(max(observed["calendar"]), config["endDate"])
            self.assertLessEqual(observed["securities"][0]["bars"][-1]["date"], config["endDate"])
            self.assertEqual(settings["endDate"], config["endDate"])
        corrupt = copy.deepcopy(state)
        corrupt["index"] += 1
        with self.assertRaises(ValueError):
            SinglePlateau(data, config, experiment, corrupt)

    @patch("service.research_pipeline.PortfolioEngine", FakeEngine)
    def test_two_point_scan_and_insufficient_trades_never_form_stable_region(self):
        data, config, _ = fixture()
        result = finish(SinglePlateau(data, config, {"axes": [{"path": "topN", "values": [2, 3]}]}))
        self.assertFalse(any(row["neighborhood"]["passed"] for row in result["candidates"]))
        self.assertEqual(result["regions"], [])
        result = finish(SinglePlateau(data, config, {"axes": [{"path": "topN", "values": [2, 3, 4]}], "minClosedTrades": 6}))
        self.assertFalse(any(row["neighborhood"]["passed"] for row in result["candidates"]))
        self.assertIn("insufficient_closed_trades", result["candidates"][0]["qualityFailures"])


class FrozenWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data, self.config, self.days = fixture()
        self.service = ResearchService(self.temp.name, autostart=False, universe_loader=lambda *_: copy.deepcopy(self.data))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def create(self, kind="singleBacktest", config=None, **extra):
        task = self.service.create({"kind": kind, "config": config or self.config,
                                    "selection": {"mode": "explicit", "symbols": ["sh600001"]}, **extra})
        self.service.run_next()
        report = self.service.get({"id": task["id"]})
        self.assertEqual(report["task"]["status"], "completed", report["task"].get("error"))
        return report

    def freeze(self):
        task = self.create()
        return task, self.service.freeze({"id": task["task"]["id"]})["freeze"]

    def validation(self, frozen, **extra):
        return {"kind": "frozenValidation", "freezeId": frozen["id"], "startDate": self.days[170], "endDate": self.days[190], **extra}

    def test_frozen_configuration_is_immutable_persistent_and_survives_source_deletion(self):
        report, frozen = self.freeze()
        self.assertEqual(frozen["config"], report["task"]["config"])
        self.assertEqual(frozen, self.service.freeze({"id": report["task"]["id"]})["freeze"])
        for extra in ({"config": self.config}, {"selection": {}}, {"startDate": self.config["endDate"]},
                      {"startDate": self.days[190], "endDate": self.days[170]}):
            with self.assertRaises(ValueError):
                self.service.create(self.validation(frozen, **extra))
        self.service.delete({"ids": [report["task"]["id"]]})
        self.service.close()
        self.service = ResearchService(self.temp.name, autostart=False, universe_loader=lambda *_: copy.deepcopy(self.data))
        self.assertEqual(self.service.freezes()["freezes"][0]["id"], frozen["id"])
        task = self.service.create(self.validation(frozen))
        self.assertFalse(next(t for t in self.service.list()["tasks"] if t["id"] == task["id"])["configEditable"])
        with self.assertRaises(ValueError):
            self.service.update({"id": task["id"], "kind": "singleBacktest", "config": self.config, "name": "tamper"})
        self.service.run_next()
        result = self.service.get({"id": task["id"]})["result"]
        self.assertEqual(result["freeze"]["parameterHash"], frozen["parameterHash"])
        self.assertTrue(result["validationAudit"]["independent"])
        self.assertEqual(result["config"]["strategies"], frozen["config"]["strategies"])
        self.assertEqual(result["config"]["poolSettings"], frozen["config"]["poolSettings"])

    def test_repeated_period_is_reproduction_and_prior_development_is_not_fresh(self):
        _, frozen = self.freeze()
        for index in range(2):
            task = self.service.create(self.validation(frozen))
            self.service.run_next()
            result = self.service.get({"id": task["id"]})["result"]
            self.assertEqual(result["validationAudit"]["status"], "fresh" if index == 0 else "reproduction")
            self.assertEqual(result["validationAudit"]["independent"], index == 0)
        self.create(config={**self.config, "startDate": self.days[191], "endDate": self.days[200]})
        task = self.service.create(self.validation(frozen, startDate=self.days[191], endDate=self.days[205]))
        self.service.run_next()
        audit = self.service.get({"id": task["id"]})["result"]["validationAudit"]
        self.assertEqual(audit["status"], "developmentSample")
        self.assertFalse(audit["independent"])
        self.assertTrue(audit["priorDevelopmentIntervals"])

    def test_plateau_requires_manual_point_and_replay_uses_original_snapshot(self):
        report = self.create("singlePlateau", experiment={"axes": [{"path": "topN", "values": [2, 3]}]})
        with self.assertRaises(ValueError):
            self.service.freeze({"id": report["task"]["id"]})
        frozen = self.service.freeze({"id": report["task"]["id"], "candidateId": "c001"})["freeze"]
        self.assertFalse(frozen["evidence"]["developmentStableRegion"])
        self.assertFalse(frozen["evidence"]["certified"])
        self.assertEqual(frozen["config"]["topN"], 2)
        replay = self.service.replay({"id": report["task"]["id"], "candidateId": "c001"})
        self.data["securities"][0]["bars"][-1]["close"] = 999
        self.service.run_next()
        result = self.service.get({"id": replay["id"]})
        self.assertEqual(result["task"]["status"], "completed")
        self.assertEqual(result["result"]["metrics"]["totalReturnPct"], report["result"]["candidates"][0]["metrics"]["totalReturnPct"])

    def test_version_changes_block_validation_and_advanced_features_remain_available(self):
        _, frozen = self.freeze()
        self.service.version = "different-version"
        with self.assertRaises(ValueError):
            self.service.create(self.validation(frozen))
        self.assertFalse(self.service.freezes()["freezes"][0]["versionCompatible"])
        self.assertIn("singleExperimentDefaults", self.service.catalogue()["portfolio"])
        self.assertIn("poolStyles", self.service.catalogue()["portfolio"])

    def test_changed_declared_source_or_universe_cannot_validate_frozen_scheme(self):
        _, frozen = self.freeze()
        for declaration in ({"source": "different-provider", "universe": "local-file-coverage"},
                            {"source": "test-frozen", "universe": "different-historical-universe"}):
            self.data["historyDeclaration"] = declaration
            task = self.service.create(self.validation(frozen))
            self.service.run_next()
            report = self.service.get({"id": task["id"]})
            self.assertEqual(report["task"]["status"], "failed")
            self.assertIn("来源或历史股票范围", report["task"]["error"])
            self.assertIsNone(report["result"])

    def test_same_source_new_fingerprint_and_later_bars_remain_valid(self):
        _, frozen = self.freeze()
        self.data["fingerprint"] = "same-provider-new-daily-update"
        task = self.service.create(self.validation(frozen))
        self.service.run_next()
        report = self.service.get({"id": task["id"]})
        self.assertEqual(report["task"]["status"], "completed")
        self.assertNotEqual(report["task"]["fingerprint"], frozen["sourceFingerprint"])
        self.assertTrue(report["result"]["validationAudit"]["independent"])

    def test_local_settings_switch_is_detected_even_with_same_generic_source_label(self):
        roots = [Path(self.temp.name) / name for name in ("tdx-a", "tdx-b")]
        for root in roots:
            (root / "vipdoc" / "sh" / "lday").mkdir(parents=True)
        cache = Path(self.temp.name) / "market.sqlite3"
        with closing(sqlite3.connect(cache)) as db, db:
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO meta VALUES ('path',?)", (json.dumps(str(roots[0])),))
        self.service.universe_loader = None
        with patch("service.portfolio_universe.snapshot_universe", side_effect=lambda *a, **kw: copy.deepcopy(self.data)):
            _, frozen = self.freeze()
            self.assertTrue(frozen["sourceIdentity"])
            self.assertNotIn(str(roots[0]), json.dumps(frozen))
            with closing(sqlite3.connect(cache)) as db, db:
                db.execute("UPDATE meta SET value=? WHERE key='path'", (json.dumps(str(roots[1])),))
            task = self.service.create(self.validation(frozen))
            self.service.run_next()
            report = self.service.get({"id": task["id"]})
            self.assertEqual(report["task"]["status"], "failed")
            self.assertIn("当前行情来源与冻结方案不一致", report["task"]["error"])
            self.assertIsNone(report["result"])


if __name__ == "__main__":
    unittest.main()
