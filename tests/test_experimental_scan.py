"""Current-screen boundary tests: local synthetic TDX, original detectors, no accounts."""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service import experimental_scan as scan
from service.experimental_scan_service import ExperimentalScanService
from service.experimental_strategies import ExecutionStopped, IDS, build_signals
from service import tdx, trading_calendar

ROOT = Path(__file__).resolve().parents[1]


def fixture_module(name):
    spec = importlib.util.spec_from_file_location("scan_fixture_" + name, ROOT / "tests" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_fixture(size=130, *, all_boards=True):
    days = trading_calendar.trading_sessions("2026-01-05", "2026-10-16")[:size]
    def bars(multiplier=1.):
        return [{"date": day, "open": (10 + i * .01) * multiplier, "close": (10 + i * .01) * multiplier,
            "high": (10.1 + i * .01) * multiplier, "low": (9.9 + i * .01) * multiplier,
            "volume": 1000000, "amount": 80000000.} for i, day in enumerate(days)]
    identities = [("sh600001", "main"), ("sz300001", "chinext"), ("sh688001", "star")]
    if not all_boards: identities = identities[:1]
    data = {"calendar": days, "calendarSource": "synthetic-independent-exchange", "priceBasisId": "raw-unadjusted",
        "securities": [{"symbol": symbol, "name": symbol, "board": board, "bars": bars()} for symbol, board in identities],
        "benchmark": {"symbol": "sh000300", "bars": bars(10)}}
    metadata = {"requestedDate": days[-1], "expectedAsOfDate": days[-1], "asOfDate": days[-1],
        "stale": False, "lagSessions": 0, "failures": [], "warnings": [], "source": {"kind": "synthetic"}}
    return data, metadata


def empty_builder(strategy, data, **kwargs):
    n = len(data["securities"])
    row = {"counts": {"rankPopulationCount": n, "amountUnknownCount": 0, "formationPriceUnknownCount": 0},
        "parentActivationCounts": {"candidate": 0, "notTriggered": n, "unknown": 0},
        "benchmarkPrior60": {"complete": True}, "ranking": []}
    if strategy == IDS[0]: row["counts"] = {"notTriggered": n}
    return {"schedule": [], "evidence": {"dailyRows": [row]}}


def write_daily(root, symbol, bars):
    folder = root / "vipdoc" / symbol[:2] / "lday"
    folder.mkdir(parents=True, exist_ok=True)
    payload = b"".join(tdx.DAY.pack(int(b["date"].replace("-", "")),
        *(round(b[k] * 100) for k in ("open", "high", "low", "close")),
        b["amount"], b["volume"], 0) for b in bars)
    path = folder / (symbol + ".day")
    path.write_bytes(payload)
    return path


class CurrentScreenTests(unittest.TestCase):
    def test_clock_excludes_partial_session_and_respects_holidays_and_coverage(self):
        for clock, expected in (("2026-10-16T15:14:00+08:00", "2026-10-15"),
                ("2026-10-16T15:15:00+08:00", "2026-10-16"),
                ("2026-10-17T12:00:00+08:00", "2026-10-16"),
                ("2026-10-06T16:00:00+08:00", "2026-09-30")):
            self.assertEqual(scan.scan_clock(datetime.fromisoformat(clock))["expectedAsOfDate"], expected)
        with self.assertRaises(ValueError): scan.scan_clock(datetime(2026, 10, 16))
        with self.assertRaises(ValueError): scan.scan_clock(datetime.fromisoformat("2028-01-10T16:00:00+08:00"))

    def test_loader_uses_stock_modal_date_and_discloses_stale_benchmark(self):
        data, _ = source_fixture()
        days = data["calendar"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for s in data["securities"]: write_daily(root, s["symbol"], s["bars"])
            write_daily(root, "sh000300", data["benchmark"]["bars"][:-2])
            clock = {"requestedDate": days[-1], "expectedAsOfDate": days[-1], "requestedAt": "synthetic"}
            with patch.object(scan, "_configured_source", return_value=(root, {}, [])):
                result, metadata = scan.load_current_dataset(root, clock=clock)
            self.assertEqual(metadata["source"]["stockModalLatestDate"], days[-1])
            self.assertEqual(metadata["source"]["benchmarkLatestDate"], days[-3])
            self.assertEqual(metadata["asOfDate"], days[-3])
            self.assertTrue(metadata["stale"])
            self.assertEqual(metadata["lagSessions"], 2)
            self.assertFalse(result["calendarSource"].startswith("observed"))
            self.assertTrue(all(b["date"] <= days[-3] for s in result["securities"] for b in s["bars"]))

    def test_loader_prefix_does_not_use_future_price_values(self):
        data, _ = source_fixture()
        end = data["calendar"][-2]
        clock = {"requestedDate": end, "expectedAsOfDate": end, "requestedAt": "synthetic"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for s in data["securities"] + [data["benchmark"]]: write_daily(root, s["symbol"], s["bars"])
            with patch.object(scan, "_configured_source", return_value=(root, {}, [])):
                first, first_meta = scan.load_current_dataset(root, clock=clock)
                for s in data["securities"] + [data["benchmark"]]:
                    for key in ("open", "high", "low", "close"): s["bars"][-1][key] *= 5
                    s["bars"][-1]["amount"] *= 100
                    write_daily(root, s["symbol"], s["bars"])
                second, second_meta = scan.load_current_dataset(root, clock=clock)
            self.assertEqual(first, second)
            self.assertEqual(first_meta, second_meta)

    def test_loader_preserves_failed_security_in_cohort_and_cancellation(self):
        data, _ = source_fixture()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for s in data["securities"] + [data["benchmark"]]: write_daily(root, s["symbol"], s["bars"])
            (root / "vipdoc/sh/lday/sh600999.day").write_bytes(b"broken")
            with patch.object(scan, "_configured_source", return_value=(root, {}, [])):
                got, meta = scan.load_current_dataset(root, clock={"expectedAsOfDate": data["calendar"][-1]})
                with self.assertRaises(ExecutionStopped):
                    scan.load_current_dataset(root, clock={"expectedAsOfDate": data["calendar"][-1]}, cancelled=lambda: True)
            bad = next(s for s in got["securities"] if s["symbol"] == "sh600999")
            self.assertEqual(bad["bars"], [])
            self.assertEqual(meta["failures"][0]["symbol"], "sh600999")

    def test_environment_zero_denominator_is_unknown_not_zero(self):
        data, _ = source_fixture()
        got = scan.market_context(data, [])
        self.assertEqual(got["label"], "数据不足")
        self.assertTrue(all(m["value"] is None and m["denominator"] == 0 for m in got["metrics"]))
        data["securities"][0]["bars"] = []
        self.assertEqual(scan.market_context(data, data["securities"][:1])["asOfCount"], 0)

    def test_environment_exact_slots_matched_denominator_and_amount_basis(self):
        data, _ = source_fixture()
        a, b, c = data["securities"]
        a["bars"][-1]["amount"] = 160000000.
        b["bars"].pop(-24)  # known current21 but not prior21 at lag5.
        c["bars"].pop(-10)  # current price and amount unavailable; cannot compress.
        got = scan.market_context(data, data["securities"])
        by_label = {m["label"]: m for m in got["metrics"]}
        self.assertEqual(by_label["20日正收益参与度"]["denominator"], 2)
        self.assertEqual(by_label["同股参与度较5日前变化"]["denominator"], 1)
        self.assertEqual(by_label["成交额/前20日均额中位数"]["denominator"], 2)
        self.assertEqual(by_label["成交额/前20日均额中位数"]["value"], 1.5)
        self.assertIn("共同子集", got["amountBasis"])

    def test_nonfinite_or_boolean_volume_is_unknown_and_no_nan_json(self):
        for value in (True, float("nan"), float("inf"), "1", 0):
            data, _ = source_fixture(all_boards=False)
            data["securities"][0]["bars"][-1]["volume"] = value
            got = scan.market_context(data, data["securities"])
            self.assertTrue(all(m["value"] is None for m in got["metrics"]))
            json.dumps(got, allow_nan=False)

    def test_boards_are_complete_and_separately_sent_to_original_builder(self):
        data, metadata = source_fixture()
        calls = []
        before = deepcopy(data)
        def builder(strategy, view, **kwargs):
            calls.append((strategy, tuple(s["board"] for s in view["securities"]), kwargs["start"], kwargs["end"]))
            return empty_builder(strategy, view, **kwargs)
        result = scan.analyze_dataset(data, metadata, builder=builder)
        self.assertEqual(len(calls), 7)
        self.assertEqual([r[1] for r in calls], [("main",), ("main",), ("chinext",), ("star",), ("main",), ("chinext",), ("star",)])
        self.assertTrue(all(r[2:] == (metadata["asOfDate"], metadata["asOfDate"]) for r in calls))
        self.assertEqual(result["coverage"]["boardCounts"], {"main": 1, "chinext": 1, "star": 1})
        self.assertEqual(data, before)

    def test_environment_preserves_known_direction_with_incomplete_coverage(self):
        data, metadata = source_fixture()
        for s in data["securities"]: s["bars"] = []
        result = scan.analyze_dataset(data, metadata, builder=empty_builder)
        self.assertTrue(all(s["environmentFit"]["label"] == "环境数据不足" for s in result["strategies"]))
        data, metadata = source_fixture()
        data["securities"].append({"symbol": "sh600002", "board": "main", "bars": []})
        result = scan.analyze_dataset(data, metadata, builder=empty_builder)
        self.assertTrue(all(s["environmentFit"]["label"] == "已知样本趋势扩散（覆盖不完整）" for s in result["strategies"]))
        for s in data["securities"]:
            for i, bar in enumerate(s["bars"]):
                close = 20. - i * .01
                bar.update(open=close, high=close * 1.01, low=close * .99, close=close)
        result = scan.analyze_dataset(data, metadata, builder=empty_builder)
        self.assertTrue(all(s["environmentFit"]["label"] == "已观察样本偏弱，谨慎观察" for s in result["strategies"]))
        # Known mixed board states keep their description even with an unknown member.
        data, metadata = source_fixture()
        data["securities"][0]["bars"][-1]["close"] = 11.1  # Below MA20, above MA60.
        data["securities"][0]["bars"][-1].update(open=11.1, low=11.0)
        data["securities"].append({"symbol": "sh600002", "board": "main", "bars": []})
        result = scan.analyze_dataset(data, metadata, builder=empty_builder)
        self.assertTrue(all(s["environmentFit"]["label"] == "已观察样本震荡分化（覆盖不完整）" for s in result["strategies"]))

    def test_missing_benchmark_preserves_contraction_but_not_relative_strategies(self):
        data, metadata = source_fixture()
        data["benchmark"] = None
        calls = []
        def builder(strategy, view, **kwargs):
            calls.append(strategy)
            return empty_builder(strategy, view, **kwargs)
        got = scan.analyze_dataset(data, metadata, builder=builder)
        self.assertEqual(calls, [IDS[0]])
        self.assertTrue(all(b["status"] == "unavailable" for s in got["strategies"][1:] for b in s["boards"]))

    def test_future_data_wrong_board_and_observed_calendar_rejected_before_builder(self):
        base, metadata = source_fixture()
        for kind in ("future", "board", "calendar", "duplicate"):
            data = deepcopy(base)
            if kind == "future":
                data["securities"][0]["bars"].append({**data["securities"][0]["bars"][-1], "date": "2029-01-02"})
            if kind == "board": data["securities"][0]["board"] = "star"
            if kind == "calendar": data["calendarSource"] = "observed-union"
            if kind == "duplicate": data["securities"].append(deepcopy(data["securities"][0]))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                scan.analyze_dataset(data, metadata, builder=lambda *a, **k: self.fail("invalid input reached builder"))

    def test_stale_signal_warning_and_cancelled_scan(self):
        data, metadata = source_fixture()
        metadata.update(stale=True, expectedAsOfDate="2026-10-16")
        got = scan.analyze_dataset(data, metadata, builder=empty_builder)
        self.assertIn("不是今天的新信号", got["warnings"][0])
        with self.assertRaises(ExecutionStopped):
            scan.analyze_dataset(data, metadata, builder=empty_builder, cancelled=lambda: True)

    def test_real_persistent_detector_and_candidate_evidence(self):
        fixture = fixture_module("test_research_leader_lag")
        data = fixture.source(size=100)
        data["calendarSource"] = "synthetic-independent"
        for s in data["securities"]: s.update(board="main", name=s["symbol"])
        metadata = {"asOfDate": data["calendar"][-1], "stale": False}
        got = scan.analyze_dataset(data, metadata)
        expected = build_signals(IDS[1], data, start=metadata["asOfDate"], end=metadata["asOfDate"])
        candidates = got["strategies"][1]["candidates"]
        self.assertEqual([(c["symbol"], c["rank"]) for c in candidates], [(s["symbol"], s["originalRank"]) for s in expected["schedule"]])
        self.assertTrue(candidates)
        self.assertEqual(len(candidates[0]["evidence"]), 6)

    def test_real_reclaim_detector_next_session_semantics_and_no_account(self):
        fixture = fixture_module("test_research_undercut_reclaim")
        data = fixture.fixture(length=62)
        fixture.reclaim(data)
        data["benchmark"]["symbol"] = "sh000300"
        for i, s in enumerate(data["securities"]): s.update(symbol=f"sh{600001+i}", board="main")
        metadata = {"asOfDate": data["calendar"][-1], "stale": False}
        with patch("service.experimental_strategies.make_account", side_effect=AssertionError("scan created account")):
            got = scan.analyze_dataset(data, metadata)
        candidates = got["strategies"][2]["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["signalDate"], metadata["asOfDate"])
        self.assertIn("下一交易日", got["executionNotice"])

    def test_reclaim_evaluated_count_excludes_amount_and_formation_price_unknown(self):
        fixture = fixture_module("test_research_undercut_reclaim")
        data = fixture.fixture(length=62)
        data["benchmark"]["symbol"] = "sh000300"
        for i, s in enumerate(data["securities"]): s.update(symbol=f"sh{600001+i}", board="main")
        data["securities"][39]["bars"][-2]["amount"] = None
        data["securities"][38]["bars"][-1]["volume"] = 0
        day = data["calendar"][-1]
        direct = build_signals(IDS[2], data, start=day, end=day)
        result = scan.analyze_dataset(data, {"asOfDate": day, "stale": False})
        board = result["strategies"][2]["boards"][0]
        self.assertEqual(board["counts"]["rankPopulationCount"], 40)
        self.assertEqual(board["counts"]["amountUnknownCount"], 1)
        self.assertEqual(board["counts"]["formationPriceUnknownCount"], 1)
        self.assertEqual(board["evaluatedCount"], 38)
        self.assertEqual(result["strategies"][2]["counts"]["signals"], len(direct["schedule"]))

    def test_real_contraction_candidate_preserves_prior40_volume(self):
        fixture = fixture_module("test_signal_contraction_price_proxy")
        dates, old = fixture.fixture(length=121)
        bars = [deepcopy(old[0]) for _ in range(40)] + deepcopy(old[:81])
        for day, bar in zip(dates, bars): bar["date"] = day
        data = {"calendar": dates, "calendarSource": "synthetic-independent", "priceBasisId": "raw-unadjusted",
            "securities": [{"symbol": "sh600001", "board": "main", "bars": bars}], "benchmark": None}
        got = scan.analyze_dataset(data, {"asOfDate": dates[-1], "stale": False})
        candidate = got["strategies"][0]["candidates"][0]
        metric = next(x for x in candidate["evidence"] if x["label"] == "突破量/前40日均量")
        self.assertAlmostEqual(metric["value"], 200 / 87.5)


class CurrentScanQueueTests(unittest.TestCase):
    def test_request_is_idempotent_one_active_and_never_auto_executes(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            service = ExperimentalScanService(folder, autostart=False, executor=lambda *a, **k: calls.append(1))
            one = service.create({"requestId": "one"})
            self.assertEqual(service.create({"requestId": "one"}), one)
            self.assertEqual(service.create({"requestId": "two"}), one)
            self.assertEqual(len(service.dispatch("research.experimental.scan.list")["tasks"]), 1)
            self.assertNotIn("result", service.get({"id": one["id"]}))
            self.assertEqual(calls, [])
            with self.assertRaises(ValueError): service.create({"requestId": "override", "asOfDate": "2025-01-02"})
            service.close()

    def test_complete_persisted_result_is_readable_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ExperimentalScanService(folder, autostart=False, executor=lambda *a, **k: {"synthetic": True, "stale": True})
            row = service.create({"requestId": "one"}); service._run(row["id"]); service.close()
            restarted = ExperimentalScanService(folder, autostart=False, executor=lambda *a, **k: self.fail("read executed"))
            got = restarted.get({"id": row["id"]})
            self.assertEqual(got["task"]["status"], "completed")
            self.assertEqual(got["result"], {"synthetic": True, "stale": True})
            self.assertTrue(restarted.pending.empty())
            restarted.close()

    def test_saved_scan_freshness_updates_across_days_without_rewriting_result(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = {"asOfDate": "2026-10-15", "stale": False, "signals": ["unchanged"]}
            service = ExperimentalScanService(folder, autostart=False, executor=lambda *a, **k: saved)
            row = service.create({"requestId": "one"}); service._run(row["id"])
            path = service.directory / row["id"] / "result.json"
            original = path.read_bytes()
            for expected, stale, lag in (("2026-10-15", False, 0), ("2026-10-16", True, 1)):
                clock = {"expectedAsOfDate": expected, "requestedAt": expected + "T16:00:00+08:00"}
                with patch("service.experimental_scan_service.scan_clock", return_value=clock):
                    result = service.get({"id": row["id"]})
                self.assertEqual(result["freshness"], {"checkedAt": clock["requestedAt"],
                    "expectedAsOfDate": expected, "stale": stale, "lagSessions": lag, "available": True})
                self.assertEqual(result["result"], saved)
                self.assertEqual(path.read_bytes(), original)
            with patch("service.experimental_scan_service.scan_clock", side_effect=ValueError("calendar unavailable")):
                result = service.get({"id": row["id"]})
            self.assertEqual(result["freshness"]["available"], False)
            self.assertIsNone(result["freshness"]["stale"])
            self.assertEqual(result["freshness"]["reason"], "当前日历无法核验")
            self.assertEqual(path.read_bytes(), original)
            service.close()

    def test_stop_after_executor_returns_prevents_completed_and_close_is_interrupted(self):
        for action, terminal in (("stop", "stopped"), ("close", "interrupted")):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                service = ExperimentalScanService(folder, autostart=False)
                row = service.create({"requestId": "one"})
                def executor(*args, **kwargs):
                    service.stop({"id": row["id"]}) if action == "stop" else service.close()
                    return {"mustNotPublish": True}
                service.executor = executor; service._run(row["id"])
                got = service.get({"id": row["id"]})
                self.assertEqual(got["task"]["status"], terminal)
                self.assertNotIn("result", got)
                service.close()

    def test_queued_stop_and_interrupted_restart_do_not_run(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ExperimentalScanService(folder, autostart=False, executor=lambda *a, **k: self.fail("stopped executed"))
            row = service.create({"requestId": "one"}); service.stop({"id": row["id"]}); service._run(row["id"])
            pending = service.create({"requestId": "two"})
            second = ExperimentalScanService(folder, autostart=False)
            self.assertEqual(second.get({"id": row["id"]})["task"]["status"], "stopped")
            self.assertEqual(second.get({"id": pending["id"]})["task"]["status"], "interrupted")
            self.assertTrue(second.pending.empty())
            second.close(); service.close()


if __name__ == "__main__":
    unittest.main()
