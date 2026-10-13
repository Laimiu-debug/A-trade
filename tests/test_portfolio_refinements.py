"""Accounting, historical coverage, interval holes and continuous capital tests."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

from service.portfolio_actions import normalize_actions
from service.portfolio_engine import PortfolioEngine
from service.portfolio_history import HistoryStore, normalize_package
from service.portfolio_regions import connected_regions, verified_ranges
from service.portfolio_experiment import PortfolioExperiment
from service.research_service import ResearchService, _pack
from test_portfolio_engine import dataset, config, FixtureScorer, SYMBOLS
from test_portfolio_service import dataset_fixture, CONFIG


def package(data):
    data = deepcopy(data)
    days = data["calendar"]
    data["membershipByDate"] = {day: [security["symbol"] for security in data["securities"]] for day in days}
    for security in data["securities"]:
        security["corporateActionsComplete"] = True
        security.setdefault("corporateActions", [])
        security.setdefault("openingVolumeByDate", {bar["date"]: bar["volume"] for bar in security["bars"]})
    return {"schema": "historical-package-v1", "coverage": {"startDate": days[0], "endDate": days[-1]},
            "declaration": {"universe": "synthetic historical universe including exited members", "source": "isolated fixture",
                            "calendarSource": "fixture-independent-calendar", "completeMembership": True, "completeActions": True},
            "dataset": data}


def action(data, **values):
    days = data["calendar"]
    return {"id": "div1", "announcementDate": days[2], "recordDate": days[7], "exDate": days[8],
            "cashPayDate": days[10], "cashNetPerShare": .5, "source": "isolated announcement fixture", **values}


def finish(engine):
    for _ in range(20000):
        if engine.step(20):
            return engine.result()
    raise AssertionError("not completed")


@patch("service.portfolio_engine.DailyScorer", FixtureScorer)
class RightsLedgerTests(unittest.TestCase):
    def dividend_data(self):
        data = dataset()
        row = action(data)
        security = data["securities"][0]
        security["corporateActions"] = [row]
        security["corporateActionsComplete"] = True
        for i, bar in enumerate(security["bars"]):
            if i >= 8:
                bar.update(open=9.5, high=9.5, low=9.5, close=9.5)
                security["precloseByDate"][bar["date"]] = 9.5
        return data

    def test_dividend_receivable_preserves_nav_but_is_not_available_before_payday(self):
        data = self.dividend_data()
        engine = PortfolioEngine(data, config(data, rebalanceMode="risk"))
        engine.step(4)
        self.assertEqual(engine.state["cash"], 0)
        self.assertEqual(engine.state["cashReceivables"][0]["amount"], 500)
        self.assertEqual(engine.state["curve"][-1]["equity"], 10000)
        self.assertEqual(engine.state["unaccountedActionExposure"], [])
        checkpoint = json.loads(json.dumps(engine.checkpoint()))
        restored = PortfolioEngine(data, config(data, rebalanceMode="risk"), checkpoint)
        result = finish(restored)
        self.assertEqual(result["cashReceivables"], [])
        self.assertEqual(result["curve"][-1]["cash"], 500)
        self.assertEqual(result["metrics"]["finalEquity"], 10000)
        self.assertEqual(sum(row["stage"] == "cashReceived" for row in result["actionLedger"]), 1)
        self.assertEqual(result, finish(engine))

    def test_bonus_shares_have_locked_availability_and_unchanged_total_basis(self):
        data = self.dividend_data()
        data["securities"][0]["corporateActions"] = [action(data, cashNetPerShare=0, bonusSharesPerShare=1,
                                                             shareListingDate=data["calendar"][10])]
        for i, bar in enumerate(data["securities"][0]["bars"]):
            if i >= 8:
                bar.update(open=5, close=5, high=5, low=5)
                data["securities"][0]["precloseByDate"][bar["date"]] = 5
        settings = config(data, rebalanceMode="risk")
        engine = PortfolioEngine(data, settings)
        engine.step(4)
        position = engine.state["positions"][SYMBOLS[0]]
        self.assertEqual(position["quantity"], 2000)
        self.assertEqual(sum(lot["cost"] for lot in position["lots"]), 10000)
        self.assertEqual(position["costBasis"], 10000)
        self.assertEqual(engine.state["curve"][-1]["equity"], 10000)
        order = {"signalDate": data["calendar"][8], "reason": "test", "weights": {}}
        engine._execute_order(data["calendar"][9], SYMBOLS[0], "sell", 2000, order)
        self.assertEqual(engine.state["positions"][SYMBOLS[0]]["quantity"], 1000)
        engine._execute_order(data["calendar"][10], SYMBOLS[0], "sell", 1000, order)
        self.assertNotIn(SYMBOLS[0], engine.state["positions"])
        self.assertEqual(engine.state["cash"], 10000)

    def test_rights_subscription_reserves_cash_and_books_payment_once(self):
        data = self.dividend_data()
        security = data["securities"][0]
        security["corporateActions"] = [action(data, cashNetPerShare=0, rightsSharesPerShare=.5, rightsPrice=2,
                                               rightsPayDate=data["calendar"][10], shareListingDate=data["calendar"][10])]
        for i, bar in enumerate(security["bars"]):
            if i >= 8:
                bar.update(open=22/3, high=22/3, low=22/3, close=22/3)
                security["precloseByDate"][bar["date"]] = 22/3
        engine = PortfolioEngine(data, config(data, maxStockWeight=.5, rebalanceMode="risk", rightsPolicy="subscribe"))
        engine.step(4)
        self.assertEqual(engine.state["positions"][SYMBOLS[0]]["quantity"], 750)
        self.assertEqual(engine.state["cash"], 5000)
        self.assertEqual(engine.state["reservedActionCash"], 500)
        self.assertAlmostEqual(engine.state["curve"][-1]["equity"], 10000)
        result = finish(engine)
        self.assertEqual(result["reservedActionCash"], 0)
        self.assertEqual(result["curve"][-1]["cash"], 4500)
        self.assertEqual(sum(row["stage"] == "cashPaid" for row in result["actionLedger"]), 1)

    def test_fractional_allocation_is_explicit_and_blocks_certification(self):
        data = self.dividend_data()
        data["securities"][0]["corporateActions"] = [action(data, bonusSharesPerShare=.0005, shareListingDate=data["calendar"][10])]
        result = finish(PortfolioEngine(data, config(data, rebalanceMode="risk")))
        self.assertTrue(result["quality"]["unaccountedActionExposure"])
        self.assertFalse(result["quality"]["certified"])

    def test_pre_ex_subscription_payment_and_failure_refund_do_not_look_ahead_or_release_cash_early(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                data = dataset(length=16)
                days = data["calendar"]
                rights = action(data, type="rights_refund" if failed else "distribution", cashNetPerShare=0,
                                exDate=days[11], rightsSharesPerShare=.5, rightsPrice=2, rightsPayDate=days[9],
                                **({"cashPayDate": days[13]} if failed else {"shareListingDate": days[13]}))
                security = data["securities"][0]
                security["corporateActions"] = [rights]
                security["corporateActionsComplete"] = True
                if not failed:
                    for i, bar in enumerate(security["bars"]):
                        if i >= 11:
                            bar.update(open=22/3, high=22/3, low=22/3, close=22/3)
                            security["precloseByDate"][bar["date"]] = 22/3
                engine = PortfolioEngine(data, config(data, maxStockWeight=.5, rebalanceMode="risk", rightsPolicy="subscribe"))
                engine.step(5)
                self.assertEqual(engine.state["cash"], 4500)
                self.assertEqual(engine.state["preExRights"][SYMBOLS[0]+":div1"]["amount"], 500)
                self.assertEqual(engine.state["positions"][SYMBOLS[0]]["quantity"], 500)
                self.assertAlmostEqual(engine.state["curve"][-1]["equity"], 10000)
                engine = PortfolioEngine(data, config(data, maxStockWeight=.5, rebalanceMode="risk", rightsPolicy="subscribe"), json.loads(json.dumps(engine.checkpoint())))
                result = finish(engine)
                self.assertEqual(result["preExRights"], {})
                self.assertEqual(result["curve"][-1]["cash"], 5000 if failed else 4500)
                self.assertAlmostEqual(result["metrics"]["finalEquity"], 10000)
                self.assertEqual(sum(row["stage"] == "subscriptionPaid" for row in result["actionLedger"]), 1)


class HistoricalPackageTests(unittest.TestCase):
    def test_sharded_snapshot_checks_integrity_retains_warmup_and_ignores_batch_size(self):
        from service.portfolio_config import normalize_selection
        raw = package(dataset(length=550))
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            imported = store.import_package(raw)
            selection = normalize_selection({"source": "history", "sourceId": imported["id"]})
            start, end = raw["dataset"]["calendar"][520], raw["dataset"]["calendar"][-1]
            loaded = store.snapshot(selection, start, end)
            self.assertEqual(len(loaded["securities"][0]["bars"]), 530)
            self.assertEqual(loaded["securities"][0]["forceSeed"]["observationsBefore"], 20)
            self.assertEqual(loaded["fingerprint"], store.snapshot({**selection, "batchSize": 1}, start, end)["fingerprint"])
            with closing(sqlite3.connect(store.path)) as db, db:
                db.execute("UPDATE package_parts SET data=? WHERE kind='securities' AND key=?", (b'corrupted', SYMBOLS[0]))
            with self.assertRaisesRegex(ValueError, "校验失败"):
                store.snapshot(selection, start, end)

    def test_jsonl_native_file_import_and_close_only_benchmark(self):
        raw = package(dataset(length=60))
        raw["dataset"]["benchmark"] = {"symbol": "sh000300", "bars": [{"date": day, "close": 3000+i} for i, day in enumerate(raw["dataset"]["calendar"])]}
        header = deepcopy(raw)
        securities = header["dataset"].pop("securities")
        membership = header["dataset"].pop("membershipByDate")
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "fixture.jsonl"
            with file.open("w", encoding="utf-8") as stream:
                for row in [header, *({"security": s} for s in securities), *({"date": day, "members": symbols} for day, symbols in membership.items())]:
                    stream.write(json.dumps(row) + "\n")
            service = ResearchService(directory, autostart=False)
            self.addCleanup(service.close)
            imported = service.import_history_file({"path": str(file)})["imported"]
            from service.portfolio_config import normalize_selection
            selection = normalize_selection({"source": "history", "sourceId": imported["id"], "benchmarkSymbol": "sh000300"})
            data = HistoryStore(directory).snapshot(selection, raw["coverage"]["startDate"], raw["coverage"]["endDate"])
            result = finish(PortfolioEngine(data, config(data, universe={"strictHistory": True, "minListingDays": 0, "minAmount20": 0})))
            self.assertTrue(result["quality"]["certified"])
            self.assertIsNotNone(result["benchmarkMetrics"])

    def test_price_signal_adjustment_is_causal_and_split_does_not_create_a_false_momentum_crash(self):
        from service.portfolio_signals import DailyScorer
        data = dataset_fixture(symbol_count=2, bar_count=100)
        settings = {**deepcopy(CONFIG), "strategies": [{"id": "mom", "kind": "momentum", "params": {"lookback": 10, "trendWindow": 20, "minReturn": 0}}]}
        reference = DailyScorer(data, settings)
        changed = deepcopy(data)
        for security in changed["securities"]:
            prices = [bar["close"] for bar in security["bars"]]
            security["precloseByDate"] = {}
            for i, bar in enumerate(security["bars"]):
                if i >= 60:
                    for key in ("open", "high", "low", "close"):
                        bar[key] /= 2
                    security["precloseByDate"][bar["date"]] = prices[max(0,i-1)] / 2
        adjusted = DailyScorer(changed, settings)
        for i in (40, 59, 60, 75, 99):
            left = reference.daily(data["calendar"][i])["strategies"]["mom"]
            right = adjusted.daily(data["calendar"][i])["strategies"]["mom"]
            self.assertEqual([row["symbol"] for row in left], [row["symbol"] for row in right])
            for a, b in zip(left, right):
                self.assertAlmostEqual(a["rawScore"], b["rawScore"])
        self.assertEqual(changed["securities"][0]["bars"][75]["close"], data["securities"][0]["bars"][75]["close"] / 2)

    def test_complete_package_can_reach_certificate_and_unknown_history_cannot(self):
        raw = package(dataset(length=60))
        data = normalize_package(raw)
        result = finish(PortfolioEngine(data, config(data, universe={"strictHistory": True, "minListingDays": 0, "minAmount20": 0})))
        self.assertTrue(result["quality"]["certified"], result["quality"])
        self.assertEqual(result["quality"]["returnType"], "declaredNetCashAndShareRightsTotalReturn")
        malformed = deepcopy(raw)
        del malformed["dataset"]["securities"][0]["stByDate"][data["calendar"][4]]
        with self.assertRaisesRegex(ValueError, "ST"):
            normalize_package(malformed)

    def test_missing_delisted_member_and_unexplained_adjustment_rejected(self):
        raw = package(dataset())
        raw["dataset"]["membershipByDate"][raw["dataset"]["calendar"][2]].append("sh600099")
        with self.assertRaisesRegex(ValueError, "退市"):
            normalize_package(raw)
        raw = package(dataset())
        raw["dataset"]["securities"][0]["precloseByDate"][raw["dataset"]["calendar"][8]] = 9.5
        with self.assertRaisesRegex(ValueError, "账本"):
            normalize_package(raw)

    def test_immutable_import_deduplication_and_selection_uses_historical_membership(self):
        raw = package(dataset())
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            first = store.import_package(raw)
            self.assertEqual(first, store.import_package(raw))
            self.assertEqual(len(store.list()), 1)
            from service.portfolio_config import normalize_selection
            selection = normalize_selection({"source": "history", "sourceId": first["id"], "mode": "explicit", "symbols": [SYMBOLS[0]]})
            data = store.snapshot(selection, raw["coverage"]["startDate"], raw["coverage"]["endDate"])
            self.assertEqual(len(data["securities"]), 1)
            self.assertTrue(all(members == [SYMBOLS[0]] for members in data["membershipByDate"].values()))
            data["securities"][0]["bars"][0]["close"] = 999
            self.assertEqual(store.snapshot(selection, raw["coverage"]["startDate"], raw["coverage"]["endDate"])["securities"][0]["bars"][0]["close"], 10)


class RegionAndContinuityTests(unittest.TestCase):
    def test_projected_intervals_with_holes_are_never_recommended(self):
        candidates = [{"candidateId": f"c{x}{y}", "coordinates": [x,y], "params": {"topN": x+2, "rotation.lookbackDays": y+5}} for x in range(2) for y in range(2)]
        axes = [{"path": "topN", "values": [2,3]}, {"path": "rotation.lookbackDays", "values": [5,6]}]
        ranges, failure = verified_ranges(candidates, axes, {"c00", "c01", "c10"})
        self.assertEqual(ranges, [])
        self.assertIn("unverified", failure)
        training = {row["candidateId"]: {"neighborhood": {"passed": row["candidateId"] != "c11"},
                    "fullSystem": {"metrics": {"totalReturnPct": 2}}} for row in candidates}
        regions = connected_regions(candidates, training, {"axes": axes, "minNeighborhoodSize": 3,
                    "minNeighborPositiveFraction": .7, "maxNeighborhoodStdPct": 10})
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["holes"], [[1,1]])
        self.assertFalse(regions[0]["intervalEligible"])
        self.assertEqual(len(verified_ranges(candidates, axes, {row["candidateId"] for row in candidates})[0]), 2)

    def test_continuous_walk_forward_preserves_capital_positions_and_exact_restart(self):
        data = dataset_fixture(symbol_count=3, bar_count=85)
        settings = deepcopy(CONFIG)
        settings.update(startDate=data["calendar"][0], endDate=data["calendar"][-1], maxHoldingDays=0,
                        rebalanceMode="risk", stopLossPct=0, takeProfitPct=0, trailingStopPct=0)
        experiment = {"kind": "walkForward", "trainDays": 35, "testDays": 10, "stepDays": 10,
                      "maxFolds": 2, "capitalMode": "continuous"}
        expected = finish(PortfolioExperiment(data, settings, experiment))
        runner = PortfolioExperiment(data, settings, experiment)
        while runner.state["phase"] != "continuous":
            runner.step(5)
        runner.step(5)
        runner = PortfolioExperiment(data, settings, experiment, json.loads(json.dumps(runner.checkpoint())))
        result = finish(runner)
        self.assertEqual(result, expected)
        continuous = result["continuous"]
        self.assertEqual(continuous["status"], "complete")
        self.assertFalse(continuous["protocol"]["capitalResetAtFoldBoundary"])
        self.assertEqual(set(continuous["runs"]), {"softRotation", "hardRotation", "equalWeight", "fixed"})
        for run in continuous["runs"].values():
            self.assertEqual(len(run["curve"]), 20)
            self.assertEqual(len({row["date"] for row in run["curve"]}), 20)
            self.assertGreater(run["metrics"]["totalFees"], 0)
        self.assertTrue(continuous["retrainLog"][0]["positionsCarried"])
        self.assertEqual(continuous["retrainLog"][0]["signalDate"], data["calendar"][44])
        self.assertEqual(continuous["retrainLog"][0]["firstExecutionDate"], data["calendar"][45])

    def test_prepared_inputs_reused_without_reusing_accounts_or_future_cutoffs(self):
        from service.portfolio_prepared import PreparedCache
        from service.portfolio_study import _slice
        data = dataset_fixture(symbol_count=3, bar_count=85)
        cache = PreparedCache()
        first_config = {**deepcopy(CONFIG), "startDate": data["calendar"][30], "endDate": data["calendar"][50]}
        first = cache.create(PortfolioEngine, _slice(data, first_config["endDate"]), first_config)
        first.step(5)
        changed = deepcopy(first_config)
        changed["strategies"][0]["params"]["lookback"] = 5
        second = cache.create(PortfolioEngine, _slice(data, changed["endDate"]), changed)
        self.assertIs(first.scorer.series["sh600000"], second.scorer.series["sh600000"])
        self.assertIsNot(first.scorer, second.scorer)
        self.assertEqual(second.state["positions"], {})
        self.assertEqual(second.state["cash"], second.config["initialCapital"])
        self.assertEqual(finish(second), finish(PortfolioEngine(_slice(data, changed["endDate"]), changed)))
        later_config = {**changed, "endDate": data["calendar"][60]}
        later = cache.create(PortfolioEngine, _slice(data, later_config["endDate"]), later_config)
        self.assertIsNot(second.scorer.series["sh600000"], later.scorer.series["sh600000"])
        self.assertEqual(later.scorer.series["sh600000"].dates[-1], data["calendar"][60])


class ValidationAuditAndExportTests(unittest.TestCase):
    def test_reused_holdout_changes_to_development_and_export_is_unpaginated(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ResearchService(directory, autostart=False, universe_loader=lambda *args: dataset_fixture())
            self.addCleanup(service.close)
            settings = {**deepcopy(CONFIG), "startDate": "2024-01-01", "endDate": "2024-04-30"}
            first = service.create({"kind": "portfolio", "config": settings})
            second = service.create({"kind": "portfolio", "config": {**settings, "topN": 3}})
            run = {"config": {"startDate": "2024-03-01", "endDate": "2024-03-20"}, "curve": [{"date": "2024-03-01"}]}
            report = {"testing": {"equalWeight": run}, "plateau": {"certified": True, "recommendedRanges": [1], "statisticalValidation": {"passed": True, "reasons": []}}}
            first.update(validationFamily="same-family", fingerprint="same-data")
            second.update(validationFamily="same-family", fingerprint="same-data")
            service._validation_audit(first, deepcopy(report))
            changed = deepcopy(report)
            service._validation_audit(second, changed)
            self.assertEqual(changed["validationAudit"]["status"], "developmentSample")
            self.assertFalse(changed["plateau"]["certified"])
            self.assertEqual(changed["plateau"]["recommendedRanges"], [])
            self.assertFalse(changed["plateau"]["statisticalValidation"]["passed"])
            stored = {"daily": [{"date": str(i)} for i in range(250)]}
            with service._db() as db:
                db.execute("UPDATE jobs SET result=? WHERE id=?", (_pack(stored), first["id"]))
            self.assertEqual(len(service.get({"id": first["id"]})["result"]["daily"]), 20)
            exported = service.export_report({"id": first["id"]})
            whole = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(whole["result"]["daily"]), 250)
            self.assertTrue(whole["export"]["completeAudit"])
            self.assertFalse(whole["export"]["paginationApplied"])


if __name__ == "__main__":
    unittest.main()
