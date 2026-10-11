import unittest

from service.analysis_service import analyse_universe, parameters
from service.leader_research import Leadership, limit_flags
from service.market_research import build_snapshot
from tests.test_analysis_service import trading_days


def security(symbol, prices, *, dates=None, name="样本", volumes=None):
    dates = dates or trading_days(len(prices))
    return {"symbol": symbol, "code": symbol[2:], "name": name, "market": symbol[:2], "kind": "stock",
            "rows": [(day, close, volumes[i] if volumes else 1000, close * 1000) for i, (day, close) in enumerate(zip(dates, prices))]}


class Inputs:
    @staticmethod
    def iter_inputs(universe, records):
        for item in universe["inputs"]:
            yield {**item, "rows": item["rows"][-records:]}


def analyse(inputs, records=180):
    days = sorted({row[0] for item in inputs for row in item["rows"]})
    return analyse_universe(Inputs(), {"inputs": inputs, "dates": days, "total": len(inputs)}, records=records)


class LeaderCalculations(unittest.TestCase):
    def test_nominal_limits_use_half_up_tick_and_reject_unlimited_moves(self):
        previous = ("2026-07-03", 2.35, 1000, 1)
        self.assertEqual(limit_flags(previous, ("2026-07-06", 2.59, 1000, 1), "main", "普通"), (True, False, True))
        self.assertFalse(limit_flags(previous, ("2026-07-06", 2.58, 1000, 1), "main", "普通")[0])
        self.assertEqual(limit_flags(previous, ("2026-07-06", 3.50, 1000, 1), "main", "新股"), (False, False, False))
        self.assertEqual(limit_flags(previous, ("2026-07-06", 2.59, 0, 1), "main", "普通"), (False, False, False))

    def test_date_specific_main_st_and_chinext_bands(self):
        previous = ("2026-07-02", 10, 1000, 1)
        self.assertTrue(limit_flags(previous, ("2026-07-03", 10.5, 1000, 1), "main", "*ST样本")[0])
        self.assertFalse(limit_flags(previous, ("2026-07-06", 10.5, 1000, 1), "main", "*ST样本")[0])
        self.assertTrue(limit_flags(previous, ("2026-07-06", 11, 1000, 1), "main", "*ST样本")[0])
        self.assertTrue(limit_flags(previous, ("2020-08-21", 11, 1000, 1), "chinext", "普通")[0])
        self.assertTrue(limit_flags(previous, ("2020-08-24", 12, 1000, 1), "chinext", "普通")[0])
        self.assertFalse(limit_flags(previous, ("2020-08-24", 11, 1000, 1), "chinext", "普通")[0])

    def test_promotion_denominator_excludes_missing_suspension_and_unknown_band(self):
        prefix = [100] * 80
        inputs = [security("sh600001", prefix + [100, 110, 121, 133.1]),
                  security("sh600002", prefix + [100, 110, 121, 120]),
                  security("sh600003", prefix + [100, 110, 121]),
                  security("sh600004", prefix + [100, 100, 110, 121]),
                  security("sh600005", prefix + [110, 121, 133.1, 180]),
                  security("sh600006", prefix + [100, 110, 121, 121], volumes=[1000] * 83 + [0])]
        result = analyse(inputs)
        current = result["timeline"][-1]["metrics"]
        self.assertEqual(current["promotion"]["all"], {"eligible": 5, "observed": 2, "promoted": 1, "broken": 1, "missing": 3, "rate": .5})
        self.assertEqual(current["promotion"]["firstToSecond"]["rate"], 1)
        self.assertIsNone(current["promotion"]["high"]["rate"])
        self.assertEqual(current["brokenCount"], 1)
        self.assertEqual(current["leaderBreakCount"], 0)
        self.assertEqual(current["premium"]["eligible"], 6)
        self.assertEqual(current["premium"]["observed"], 4)
        self.assertEqual(current["premium"]["missing"], 2)
        yesterday = result["leadership"]["yesterdayLeaders"]
        self.assertEqual(len(yesterday), 1)
        self.assertEqual(yesterday[0]["symbol"], "sh600005")
        self.assertEqual(yesterday[0]["status"], "missing")
        self.assertEqual(yesterday[0]["missingReason"], "涨跌幅规则待核验")
        self.assertTrue(result["timeline"][-1]["rotationEvent"]["incomplete"])
        candidate = next(item for item in result["items"] if item["symbol"] == "sh600001")
        self.assertEqual(candidate["boardStartDate"], trading_days(84)[81])
        self.assertEqual(candidate["boardRunReturn"], 33.1)
        self.assertEqual(candidate["previousHeight"], 2)
        self.assertTrue(candidate["sequenceComplete"])

    def test_gap_does_not_join_streak_or_invent_break_and_start_is_qualified(self):
        days = trading_days(85)
        inputs = [security("sh600001", [100] * 80 + [110, 121, 133.1, 146.41], dates=days[:82] + days[83:]),
                  security("sh600002", [100] * 85)]
        result = analyse(inputs)
        current = next(item for item in result["items"] if item["symbol"] == "sh600001")
        self.assertEqual(current["boardHeight"], 1)
        self.assertFalse(current["sequenceComplete"])
        missing = result["timeline"][-3]["metrics"]["promotion"]["all"]
        self.assertEqual((missing["missing"], missing["broken"]), (1, 0))

    def test_zero_denominators_unknown_and_no_future_effect(self):
        input_data = [security("sh600001", [100] * 80 + [110, 121, 133.1])]
        result = analyse(input_data)
        early = analyse([{**input_data[0], "rows": input_data[0]["rows"][:-1]}])
        self.assertEqual(early["timeline"][-1], result["timeline"][-2])
        empty = analyse([security("sh600001", [100] * 80)])
        self.assertIsNone(empty["timeline"][-1]["metrics"]["promotion"]["all"]["rate"])
        self.assertIsNone(empty["timeline"][-1]["metrics"]["premium"]["mean"])
        self.assertEqual(empty["timeline"][-1]["metrics"]["trend"]["amountShare"], 0)
        no_trade = analyse([security("sh600001", [100] * 80, volumes=[0] * 80)])
        self.assertIsNone(no_trade["timeline"][-1]["metrics"]["trend"]["amountShare"])

    def test_market_and_ladder_share_evidence_and_overlapping_windows(self):
        prices = [100 + i / 10 for i in range(1050)]
        prices[-3:] = [round(prices[-4] * 1.1, 2), 0, 0]
        prices[-2] = round(prices[-3] * 1.1, 2)
        prices[-1] = round(prices[-2] * 1.1, 2)
        inputs = [security("sh600001", prices), security("sh600002", [200] * len(prices))]
        full = analyse(inputs, records=1080)
        short = analyse(inputs, records=330)
        self.assertEqual(full["timeline"][-250:], short["timeline"][-250:])
        market = build_snapshot(inputs, total=2, history_days=1000, history_bars=1080)
        self.assertEqual(market["style"]["metrics"]["leadership"], full["timeline"][-1]["metrics"])
        self.assertEqual(market["style"]["metrics"]["maxBoardHeight"], full["timeline"][-1]["maxHeight"])
        historical = build_snapshot([{**item, "rows": item["rows"][:-1]} for item in inputs], total=2, history_days=250, history_bars=330)
        self.assertEqual(historical["style"]["metrics"]["leadership"], full["timeline"][-2]["metrics"])

    def test_extended_parameters_have_strict_bounds(self):
        params, _ = parameters("ladder", {"historyDays": 1000, "board": "chinext", "minHeight": 5, "excludeST": False})
        self.assertEqual(params["board"], "chinext")
        for payload in ({"historyDays": 1001}, {"minHeight": 1}, {"minHeight": True}, {"board": []}, {"board": "industry"}, {"excludeST": 1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parameters("ladder", payload)


if __name__ == "__main__":
    unittest.main()
