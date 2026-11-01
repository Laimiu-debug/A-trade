import copy
from datetime import date, timedelta
import unittest

from service.research_market_filter import filter_by_benchmark_ma60


def fixture(count=61):
    days, current = [], date(2023, 1, 2)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return {"calendar": days, "benchmark": {"symbol": "sh000300", "bars": [
        {"date": day, "close": 100.0} for day in days]}}


def signal(day, rank=1):
    return {"signalId": f"opportunity:{rank}", "signalDate": day,
            "symbol": f"stock{rank}", "originalRank": rank,
            "metadata": {"keep": [rank]}}


class ResearchMarketFilterTests(unittest.TestCase):
    def test_exact_calendar_window_includes_signal_day(self):
        data = fixture()
        data["benchmark"]["bars"][0]["close"] = 1000
        data["benchmark"]["bars"][-1]["close"] = 160
        source = signal(data["calendar"][-1])
        result = filter_by_benchmark_ma60(data, [source])
        row = result["evidence"][0]
        self.assertEqual(result["selected"], [source])
        self.assertEqual(row["benchmarkMA60"], 101)
        self.assertEqual(row["benchmarkClose"], 160)
        self.assertEqual(row["windowStart"], data["calendar"][1])
        self.assertEqual(result["counts"], {"included": 1, "excluded": 0, "unknown": 0})

    def test_equal_and_below_mean_are_excluded(self):
        for close in (100, 90):
            with self.subTest(close=close):
                data = fixture(60)
                data["benchmark"]["bars"][-1]["close"] = close
                result = filter_by_benchmark_ma60(data, [signal(data["calendar"][-1])])
                self.assertEqual(result["selected"], [])
                self.assertEqual(result["evidence"][0]["status"], "excluded")
                self.assertEqual(result["evidence"][0]["reason"], "closeNotAboveMA60")

    def test_missing_invalid_and_duplicate_do_not_shorten_window(self):
        for mutation in ("missingPrior", "missingCurrent", "duplicate", 0, -1,
                         float("nan"), float("inf"), True, "100", None):
            with self.subTest(mutation=mutation):
                data = fixture()
                rows = data["benchmark"]["bars"]
                if mutation == "missingPrior":
                    del rows[10]
                elif mutation == "missingCurrent":
                    rows.pop()
                elif mutation == "duplicate":
                    rows.append(copy.deepcopy(rows[10]))
                else:
                    rows[10]["close"] = mutation
                result = filter_by_benchmark_ma60(data, [signal(data["calendar"][-1])])
                self.assertEqual(result["selected"], [])
                self.assertEqual(result["evidence"][0]["status"], "unknown")
                self.assertIsNone(result["evidence"][0]["benchmarkMA60"])

    def test_insufficient_or_absent_evidence_is_unknown(self):
        data = fixture(59)
        row = filter_by_benchmark_ma60(data, [signal(data["calendar"][-1])])["evidence"][0]
        self.assertEqual(row["reason"], "insufficientMarketHistory")
        data = fixture()
        day = data["calendar"][-1]
        data.pop("benchmark")
        self.assertEqual(filter_by_benchmark_ma60(data, [signal(day)])["evidence"][0]["reason"],
                         "benchmarkUnavailable")
        self.assertEqual(filter_by_benchmark_ma60(data, [signal("2022-12-30")])["evidence"][0]["reason"],
                         "signalDateOutsideCalendar")
        self.assertEqual(filter_by_benchmark_ma60(data, [])["counts"],
                         {"included": 0, "excluded": 0, "unknown": 0})

    def test_future_append_invariance_and_input_output_isolation(self):
        data = fixture()
        data["benchmark"]["bars"][-1]["close"] = 110
        source = [signal(data["calendar"][-1])]
        before = copy.deepcopy((data, source))
        expected = filter_by_benchmark_ma60(data, source)
        self.assertEqual((data, source), before)
        future = fixture(90)
        future["benchmark"]["bars"][:61] = copy.deepcopy(data["benchmark"]["bars"])
        for row in future["benchmark"]["bars"][61:]:
            row["close"] = float("nan")
        self.assertEqual(filter_by_benchmark_ma60(future, source), expected)
        expected["selected"][0]["metadata"]["keep"].append(999)
        self.assertEqual((data, source), before)

    def test_rank_gaps_input_order_and_identity_are_preserved(self):
        data = fixture()
        data["benchmark"]["bars"][-1]["close"] = 110
        sources = [signal(data["calendar"][-1], 5), signal(data["calendar"][-2], 3),
                   signal(data["calendar"][-1], 2)]
        result = filter_by_benchmark_ma60(data, sources)
        self.assertEqual([s["originalRank"] for s in result["selected"]], [5, 2])
        self.assertEqual([s["originalRank"] for s in result["evidence"]], [5, 3, 2])
        self.assertEqual(result["selected"], [sources[0], sources[2]])
        with self.assertRaises(ValueError):
            invalid = copy.deepcopy(data)
            invalid["calendar"].append(invalid["calendar"][-1])
            filter_by_benchmark_ma60(invalid, sources)
        with self.assertRaises(ValueError):
            invalid = copy.deepcopy(data)
            invalid["benchmark"]["symbol"] = "sh000001"
            filter_by_benchmark_ma60(invalid, sources)


if __name__ == "__main__":
    unittest.main()
