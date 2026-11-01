"""Synthetic-only causal support reclaim and entry-clock contract tests."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
import unittest

from service.research_undercut_reclaim import build_undercut_reclaim, MODES, SID


def fixture(size=40, length=66):
    days, cursor = [], date(2024, 1, 2)
    while len(days) < length:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    securities = []
    for number in range(size):
        bars = []
        for index, day in enumerate(days):
            close = 10 * (1 + (.2 + number * .05) * index / 60)
            bars.append({"date": day, "open": close, "high": close * 1.02,
                         "low": close * .98, "close": close, "volume": 1000.,
                         "amount": 1000. * (number + 1)})
        securities.append({"symbol": f"s{number:03d}", "bars": bars,
                           "tradeStatusByDate": {d: True for d in days}})
    benchmark = {"bars": [{"date": d, "open": 10., "high": 10., "low": 10.,
                           "close": 10., "volume": 1000., "amount": 10000.} for d in days]}
    return {"calendar": days, "securities": securities, "benchmark": benchmark,
            "priceBasisId": "raw-unadjusted", "calendarSource": "synthetic-exchange"}


def reclaim(data, number=39, index=61):
    bars = data["securities"][number]["bars"]
    support = min(b["low"] for b in bars[index - 20:index])
    close = bars[index - 1]["close"]
    # Deliberately bearish: a candle-color requirement would silently erase it.
    bars[index].update(open=close * 1.01, high=close * 1.02,
                       low=support * .99, close=close)
    return support


def close_at(data, number, index, close):
    data["securities"][number]["bars"][index].update(
        open=close, high=close * 1.01, low=close * .99, close=close)


def run(data, start_index=61):
    return build_undercut_reclaim(data, start=data["calendar"][start_index], end=data["calendar"][-1])


def trim(data, length):
    shortened = deepcopy(data)
    shortened["calendar"] = shortened["calendar"][:length]
    allowed = set(shortened["calendar"])
    for source in [*shortened["securities"], shortened["benchmark"]]:
        source["bars"] = [b for b in source["bars"] if b["date"] in allowed]
        if "tradeStatusByDate" in source:
            source["tradeStatusByDate"] = {d: s for d, s in source["tradeStatusByDate"].items() if d in allowed}
    return shortened


class UndercutReclaimTests(unittest.TestCase):
    def test_known_setup_bearish_candle_three_dates_and_exact_signal_fields(self):
        data = fixture()
        support = reclaim(data)
        result = run(data)
        opportunity = result["opportunities"][0]
        self.assertEqual(opportunity["formationDate"], data["calendar"][61])
        self.assertEqual(opportunity["priorDate"], data["calendar"][60])
        self.assertEqual(opportunity["support"], support)
        self.assertAlmostEqual(opportunity["priorOwn60"], 2.15)
        self.assertAlmostEqual(opportunity["priorRelative60"], 2.15)
        self.assertEqual(opportunity["priorRank60"], .9875)
        self.assertEqual(opportunity["priorAmount20"], 40000)
        self.assertEqual(opportunity["confirmation"]["status"], "passed")
        ident = f'{SID}:{data["calendar"][61]}:s039'
        for mode in MODES:
            signal = opportunity["signals"][mode]
            self.assertEqual(set(signal), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})
            self.assertEqual(signal["signalId"], ident)
            self.assertEqual(signal["signalDate"], data["calendar"][61 if mode == "immediate" else 63])
            self.assertIn(signal, result["schedules"][mode])
        daily = result["dailyRows"][0]
        self.assertEqual(daily["priorWindowDates"], data["calendar"][:61])
        self.assertEqual(daily["prior20Dates"], data["calendar"][41:61])
        self.assertEqual(daily["rankPopulationCount"], 40)
        self.assertFalse(result["certified"])
        self.assertFalse(result["targetAchieved"])

    def test_original_top5_sorted_before_confirmation_no_replacement(self):
        data = fixture()
        for number in range(32, 40):
            reclaim(data, number)
        # The most liquid original candidate fails; rank six may not replace it.
        support = min(b["low"] for b in data["securities"][39]["bars"][41:61])
        close_at(data, 39, 62, support * .9)
        result = run(data)
        daily = result["dailyRows"][0]
        self.assertEqual([r["symbol"] for r in daily["candidates"]], [f"s{i:03d}" for i in range(39, 31, -1)])
        self.assertEqual([r["originalRank"] for r in daily["candidates"]], list(range(1, 9)))
        self.assertEqual([r["selected"] for r in daily["candidates"]], [True] * 5 + [False] * 3)
        formed = [r for r in result["opportunities"] if r["formationDate"] == data["calendar"][61]]
        self.assertEqual([o["symbol"] for o in formed], [f"s{i:03d}" for i in range(39, 34, -1)])
        confirmation = [o["signals"]["confirm2"] for o in formed if o["signals"]["confirm2"]]
        self.assertEqual([s["originalRank"] for s in confirmation], [2, 3, 4, 5])
        self.assertEqual(formed[0]["decisionDates"]["confirm2"], data["calendar"][63])
        self.assertEqual(formed[0]["confirmation"]["status"], "failed")

    def test_equal_amount_tie_break_is_symbol_and_not_relative_strength(self):
        data = fixture()
        for number in range(32, 40):
            reclaim(data, number)
            for bar in data["securities"][number]["bars"][:61]:
                bar["amount"] = 1234.
        result = run(data)
        self.assertEqual([r["symbol"] for r in result["dailyRows"][0]["candidates"]],
                         [f"s{i:03d}" for i in range(32, 40)])
        self.assertEqual([o["symbol"] for o in result["opportunities"][:5]],
                         [f"s{i:03d}" for i in range(32, 37)])

    def test_midrank_ties_threshold_inclusive_and_all_equal_half(self):
        data = fixture(size=10)
        for index in range(61):
            copied = dict(data["securities"][7]["bars"][index])
            data["securities"][8]["bars"][index] = copied
        reclaim(data, 7)
        reclaim(data, 8)
        result = run(data)
        self.assertEqual([r["priorRank60"] for r in result["dailyRows"][0]["candidates"]], [.8, .8])
        equal = fixture(size=10)
        for security in equal["securities"]:
            security["bars"] = deepcopy(equal["securities"][0]["bars"])
        for number in range(10):
            reclaim(equal, number)
        all_equal = run(equal)
        self.assertEqual(all_equal["dailyRows"][0]["rankPopulationCount"], 10)
        self.assertEqual(all_equal["dailyRows"][0]["counts"]["strongCount"], 0)
        self.assertEqual(all_equal["dailyRows"][0]["candidates"], [])

    def test_amount_does_not_screen_rank_population_and_every_amount_positive(self):
        for value in (None, 0, -1., math.nan, math.inf, True):
            data = fixture()
            reclaim(data)
            data["securities"][39]["bars"][45]["amount"] = value
            daily = run(data)["dailyRows"][0]
            with self.subTest(value=value):
                self.assertEqual(daily["rankPopulationCount"], 40)
                self.assertEqual(daily["counts"]["amountUnknownCount"], 1)
                self.assertEqual(daily["candidates"], [])
        data = fixture()
        reclaim(data)
        # Older amounts are not used, and current eligibility-like metadata is irrelevant.
        data["securities"][39]["bars"][40]["amount"] = None
        data["securities"][39].update(st=True, delisted=True, eligible=False)
        self.assertEqual(run(data)["dailyRows"][0]["candidates"][0]["symbol"], "s039")

    def test_own_positive_required_but_relative_positive_not_required(self):
        data = fixture()
        reclaim(data)
        for i, bar in enumerate(data["benchmark"]["bars"]):
            close = 10 * (1 + 4 * i / 60)
            bar.update(open=close, high=close, low=close, close=close)
        candidate = run(data)["dailyRows"][0]["candidates"][0]
        self.assertLess(candidate["priorRelative60"], 0)
        self.assertGreater(candidate["priorOwn60"], 0)
        falling = fixture()
        for n, security in enumerate(falling["securities"]):
            for i, bar in enumerate(security["bars"]):
                close = 10 * (1 - (.5 - n * .01) * i / 60)
                bar.update(open=close, high=close * 1.02, low=close * .98, close=close)
        reclaim(falling)
        self.assertEqual(run(falling)["dailyRows"][0]["counts"]["strongCount"], 0)

    def test_strict_support_inequalities(self):
        for field in ("low", "close"):
            data = fixture()
            support = reclaim(data)
            data["securities"][39]["bars"][61][field] = support
            with self.subTest(field=field):
                self.assertEqual(run(data)["dailyRows"][0]["candidates"], [])

    def test_confirmation_equal_support_passes_and_unknown_dominates_failure(self):
        data = fixture()
        support = reclaim(data)
        close_at(data, 39, 62, support)
        close_at(data, 39, 63, support)
        self.assertEqual(run(data)["opportunities"][0]["confirmation"]["status"], "passed")
        for missing_index, fail_index in ((62, 63), (63, 62)):
            changed = deepcopy(data)
            close_at(changed, 39, fail_index, support * .9)
            changed["securities"][39]["bars"] = [b for b in changed["securities"][39]["bars"]
                                                   if b["date"] != changed["calendar"][missing_index]]
            opportunity = run(changed)["opportunities"][0]
            with self.subTest(missing_index=missing_index):
                self.assertEqual(opportunity["confirmation"]["status"], "unknown")
                self.assertIn(None, opportunity["confirmation"]["closes"])
                self.assertIsNone(opportunity["signals"]["confirm2"])
                self.assertEqual(opportunity["signals"]["delay2"]["signalDate"], changed["calendar"][63])
                self.assertEqual(opportunity["signals"]["delay2"]["originalRank"], 1)

    def test_confirmation_full_quality_is_required_and_readable_close_retained(self):
        mutations = ({"volume": 0}, {"high": 1.}, {"priceBasisId": "adjusted"},
                     {"knownSuspended": True}, {"knownSuspended": None})
        for mutation in mutations:
            data = fixture()
            reclaim(data)
            data["securities"][39]["bars"][62].update(mutation)
            opportunity = run(data)["opportunities"][0]
            with self.subTest(mutation=mutation):
                self.assertEqual(opportunity["confirmation"]["status"], "unknown")
                self.assertIsNotNone(opportunity["confirmation"]["closes"][0])
                self.assertIsNotNone(opportunity["signals"]["delay2"])

    def test_lookback_missing_slot_not_compressed_and_no_known_halt(self):
        data = fixture(length=68)
        reclaim(data, index=62)
        missing = data["calendar"][40]
        data["securities"][39]["bars"] = [b for b in data["securities"][39]["bars"] if b["date"] != missing]
        daily = run(data, 62)["dailyRows"][0]
        self.assertEqual(daily["priorWindowDates"], data["calendar"][1:62])
        self.assertEqual(daily["counts"]["priorPriceIncomplete"], 1)
        self.assertEqual(daily["rankPopulationCount"], 39)
        self.assertEqual(daily["candidates"], [])
        for flag in (False, 0, "0"):
            changed = fixture()
            reclaim(changed)
            changed["securities"][39]["tradeStatusByDate"][changed["calendar"][40]] = flag
            with self.subTest(flag=flag):
                self.assertEqual(run(changed)["dailyRows"][0]["rankPopulationCount"], 39)
        unknown = fixture()
        reclaim(unknown)
        unknown["securities"][39]["tradeStatusByDate"] = {}
        self.assertEqual(run(unknown)["dailyRows"][0]["candidates"][0]["symbol"], "s039")

    def test_warmup_and_benchmark_invalid_leave_daily_evidence_without_signals(self):
        data = fixture()
        reclaim(data)
        warm = run(data, 60)["dailyRows"][0]
        self.assertEqual(len(warm["priorWindowDates"]), 60)
        self.assertEqual(warm["benchmarkPrior60"]["reason"], "insufficientPriorCalendarHistory")
        self.assertEqual(warm["rankPopulationCount"], 0)
        data["benchmark"]["bars"][20]["volume"] = 0
        daily = run(data)["dailyRows"][0]
        self.assertEqual(daily["rankPopulationCount"], 0)
        self.assertEqual(daily["benchmarkPrior60"],
                         {"complete": False, "reason": "incompleteBenchmarkPriorPrice", "return60": None})
        self.assertEqual(daily["rankPopulationSha256"], hashlib.sha256(b"[]").hexdigest())

    def test_last_two_days_censored_and_final_day_decision_retained(self):
        data = fixture(length=66)
        for index in (63, 64, 65):
            reclaim(data, index=index)
        result = run(data, 63)
        self.assertEqual([o["confirmation"]["status"] for o in result["opportunities"]],
                         ["passed", "censored", "censored"])
        self.assertEqual(result["opportunities"][0]["signals"]["delay2"]["signalDate"], data["calendar"][-1])
        self.assertEqual(result["opportunities"][1]["confirmation"]["dates"], [data["calendar"][-1]])
        self.assertEqual(result["opportunities"][2]["confirmation"]["dates"], [])
        for opportunity in result["opportunities"][1:]:
            self.assertIsNone(opportunity["decisionDates"]["delay2"])
            self.assertIsNone(opportunity["decisionDates"]["confirm2"])
            self.assertIsNone(opportunity["signals"]["delay2"])
            self.assertIsNone(opportunity["signals"]["confirm2"])
            self.assertIsNotNone(opportunity["signals"]["immediate"])
        self.assertEqual(result["counts"]["signals"], {"immediate": 3, "delay2": 1, "confirm2": 1})
        self.assertEqual(result["counts"]["confirmation"], {"passed": 1, "failed": 0, "unknown": 0, "censored": 2})

    def test_prefix_append_future_change_cannot_alter_formation_or_immediate(self):
        data = fixture(length=68)
        for index in (61, 62, 63):
            reclaim(data, index=index)
        prefix = trim(data, 64)
        before, after = run(prefix), run(data)
        self.assertEqual(before["dailyRows"], after["dailyRows"][:3])
        self.assertEqual(before["schedules"]["immediate"], [s for s in after["schedules"]["immediate"]
                                                           if s["signalDate"] <= prefix["calendar"][-1]])
        altered = deepcopy(data)
        close_at(altered, 39, 64, .5)
        changed = run(altered)
        self.assertEqual(before["dailyRows"], changed["dailyRows"][:3])
        for b, a in zip(before["opportunities"], after["opportunities"]):
            self.assertEqual({k: v for k, v in b.items() if k not in ("decisionDates", "confirmation", "signals")},
                             {k: v for k, v in a.items() if k not in ("decisionDates", "confirmation", "signals")})
        self.assertEqual(len({o["formationId"] for o in before["opportunities"]}), 3)

    def test_input_immutable_progress_and_output_does_not_alias_input(self):
        data = fixture()
        reclaim(data)
        saved, progress = deepcopy(data), []
        result = build_undercut_reclaim(data, start=data["calendar"][61], end=data["calendar"][-1], progress=progress.append)
        self.assertEqual(data, saved)
        self.assertEqual(progress[-1]["stage"], "formation")
        self.assertEqual(progress[-1]["completedDays"], len(result["targetDates"]))
        result["calendar"].clear()
        result["opportunities"][0]["confirmation"]["closes"][0] = 0
        self.assertEqual(data, saved)
        json.dumps(result, allow_nan=False)

    def test_invalid_contract_and_future_evidence_rejected(self):
        base = fixture()
        cases = []
        missing_basis = deepcopy(base)
        missing_basis.pop("priceBasisId")
        cases.append(missing_basis)
        bad_calendar = deepcopy(base)
        bad_calendar["calendar"][2] = bad_calendar["calendar"][1]
        cases.append(bad_calendar)
        duplicate_symbol = deepcopy(base)
        duplicate_symbol["securities"].append(deepcopy(duplicate_symbol["securities"][0]))
        cases.append(duplicate_symbol)
        bad_bar = deepcopy(base)
        bad_bar["securities"][0]["bars"].append({**bad_bar["securities"][0]["bars"][-1], "date": "2099-01-01"})
        cases.append(bad_bar)
        bad_status = deepcopy(base)
        bad_status["securities"][0]["tradeStatusByDate"]["2099-01-01"] = True
        cases.append(bad_status)
        for number, data in enumerate(cases):
            with self.subTest(number=number), self.assertRaises(ValueError):
                run(data)
        with self.assertRaises(ValueError):
            build_undercut_reclaim(base, start=base["calendar"][61], end=base["calendar"][-2])


if __name__ == "__main__":
    unittest.main()
