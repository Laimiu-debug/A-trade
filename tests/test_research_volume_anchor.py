"""Synthetic protocol boundaries; no frozen sources, prices or accounts are read."""
from copy import deepcopy
from datetime import date, timedelta
import math
import unittest

from service.research_volume_anchor import build_volume_anchor, MODES, SID


def fixture(size=1, length=27):
    days, day = [], date(2024, 1, 2)
    while len(days) < length:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    securities = []
    for number in range(size):
        volume = 100. * (number + 1)
        bars = []
        for index, day in enumerate(days):
            close = 10. if index < 20 else 12.
            current_volume = 2 * volume if index == 20 else volume
            bars.append({"date": day, "open": close, "high": close, "low": close - 1,
                "close": close, "volume": current_volume, "amount": close * current_volume})
        securities.append({"symbol": f"s{number:03d}", "volumeUnit": "shares", "amountUnit": "CNY", "bars": bars})
    return {"priceBasisId": "raw-unadjusted", "calendar": days, "securities": securities}


def run(data, start=20, end=None):
    return build_volume_anchor(data, data["calendar"][start], data["calendar"][end if end is not None else -1])


def bar_at(data, index, *, number=0, close=12., volume=100., average=None, low=None, high=None):
    average = close if average is None else average
    bar = data["securities"][number]["bars"][index]
    bar.update(open=close, close=close, low=min(close, average) if low is None else low,
        high=max(close, average) if high is None else high, volume=volume, amount=volume * average)
    return bar


class VolumeAnchorTests(unittest.TestCase):
    def test_twenty_prior_slots_no_benchmark_or_strength_requirement_and_three_clocks(self):
        data = fixture()
        result = run(data)
        opportunity = result["opportunities"][0]
        self.assertEqual(result["strategyId"], SID)
        self.assertEqual(result["modes"], list(MODES))
        self.assertEqual(result["dailyRows"][0]["priorWindowDates"], data["calendar"][:20])
        self.assertEqual(opportunity["prior20High"], 10.)
        self.assertEqual(opportunity["prior20VolumeHigh"], 100.)
        self.assertEqual(opportunity["priorAmount20"], 1000.)
        self.assertEqual(opportunity["acceptance"]["status"], "passed")
        self.assertEqual(result["counts"]["signals"], dict.fromkeys(MODES, 1))
        ident = f'{SID}:{data["calendar"][20]}:s000'
        for mode in MODES:
            signal = opportunity["signals"][mode]
            self.assertEqual(set(signal), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})
            self.assertEqual(signal["signalId"], ident)
            self.assertEqual(signal["signalDate"], data["calendar"][20 if mode == "immediate" else 23])
            self.assertEqual(result["schedules"][mode], [signal])
        for step in opportunity["acceptance"]["steps"]:
            self.assertEqual(step["closeTimesCumulativeShares"], step["cumulativeAmount"])
            self.assertTrue(step["accepted"])
        self.assertFalse(result["certified"])
        self.assertFalse(result["targetAchieved"])

    def test_both_anchor_comparisons_strict_and_prior_window_excludes_current(self):
        for field in ("price", "volume"):
            data = fixture()
            if field == "price":
                bar_at(data, 20, close=10., volume=200.)
            else:
                bar_at(data, 20, close=12., volume=100.)
            result = run(data)
            with self.subTest(field=field):
                self.assertEqual(result["dailyRows"][0]["counts"]["anchorKnownFalseCount"], 1)
                self.assertEqual(result["dailyRows"][0]["candidates"], [])
        self.assertEqual(run(fixture())["dailyRows"][0]["counts"]["setupCount"], 1)

    def test_cumulative_shares_weighting_not_mean_daily_average_or_anchor_only(self):
        data = fixture()
        bar_at(data, 20, close=12., volume=900.)
        bar_at(data, 21, close=11.5, volume=100., average=10.)
        bar_at(data, 22, close=11.1, volume=9000., average=11.)
        bar_at(data, 23, close=11.2, volume=100.)
        opportunity = run(data)["opportunities"][0]
        steps = opportunity["acceptance"]["steps"]
        self.assertEqual(steps[0]["cumulativeAmount"], 11800.)
        self.assertEqual(steps[0]["cumulativeShares"], 1000.)
        self.assertEqual(steps[0]["q"], 11.8)
        self.assertFalse(steps[0]["accepted"])
        self.assertGreater(steps[0]["close"], (12. + 10.) / 2)
        self.assertAlmostEqual(steps[1]["q"], 11.08)
        self.assertTrue(steps[1]["accepted"])
        self.assertLess(steps[1]["close"], 12.)
        self.assertEqual(opportunity["acceptance"]["status"], "failed")
        self.assertIsNotNone(opportunity["signals"]["delay3"])
        self.assertIsNone(opportunity["signals"]["accept3"])

    def test_acceptance_has_no_epsilon_and_all_three_closes_must_pass(self):
        data = fixture()
        # One cent is a data-envelope tolerance, never an acceptance tolerance.
        bar_at(data, 21, close=12., volume=100., average=12.000000001, low=12., high=12.)
        opportunity = run(data)["opportunities"][0]
        self.assertEqual(opportunity["acceptance"]["steps"][0]["barStatus"], "valid")
        self.assertFalse(opportunity["acceptance"]["steps"][0]["accepted"])
        self.assertEqual(opportunity["acceptance"]["status"], "failed")
        self.assertIsNotNone(opportunity["signals"]["delay3"])
        self.assertIsNone(opportunity["signals"]["accept3"])

    def test_original_top5_before_later_failure_unknown_and_no_backfill(self):
        data = fixture(size=8)
        bar_at(data, 21, number=7, close=10., volume=800.)
        data["securities"][6]["bars"] = [b for b in data["securities"][6]["bars"] if b["date"] != data["calendar"][22]]
        result = run(data)
        candidates = result["dailyRows"][0]["candidates"]
        self.assertEqual([c["symbol"] for c in candidates], [f"s{i:03d}" for i in range(7, -1, -1)])
        self.assertEqual([c["selected"] for c in candidates], [True] * 5 + [False] * 3)
        formed = [o for o in result["opportunities"] if o["formationDate"] == data["calendar"][20]]
        self.assertEqual([o["symbol"] for o in formed], ["s007", "s006", "s005", "s004", "s003"])
        self.assertEqual([o["originalRank"] for o in formed if o["signals"]["delay3"]], [1, 3, 4, 5])
        self.assertEqual([o["originalRank"] for o in formed if o["signals"]["accept3"]], [3, 4, 5])
        self.assertNotIn("s002", [s["symbol"] for s in result["schedules"]["accept3"]])

    def test_equal_prior_amount_uses_symbol_and_input_order_is_irrelevant(self):
        data = fixture(size=7)
        for security in data["securities"]:
            security["bars"] = deepcopy(data["securities"][0]["bars"])
        first = run(data)
        data["securities"].reverse()
        self.assertEqual(first, run(data))
        self.assertEqual([s["symbol"] for s in first["schedules"]["immediate"]], [f"s{i:03d}" for i in range(5)])

    def test_repeated_anchors_do_not_replace_an_earlier_anchor(self):
        data = fixture()
        bar_at(data, 21, close=13., volume=300.)
        bar_at(data, 22, close=14., volume=400.)
        result = run(data)
        self.assertEqual([o["formationDate"] for o in result["opportunities"]], data["calendar"][20:23])
        first = result["opportunities"][0]
        self.assertEqual(first["formationVolume"], 200.)
        self.assertEqual(first["acceptance"]["steps"][0]["cumulativeShares"], 500.)
        self.assertEqual(first["acceptance"]["steps"][0]["q"], 12.6)
        self.assertEqual(first["acceptance"]["steps"][1]["cumulativeAmount"], 11900.)
        self.assertEqual(first["decisionDates"]["delay3"], data["calendar"][23])
        self.assertEqual(len({o["formationId"] for o in result["opportunities"]}), 3)

    def test_known_failure_then_missing_is_unknown_for_both_delayed_modes(self):
        data = fixture()
        bar_at(data, 21, close=10., volume=100.)
        data["securities"][0]["bars"] = [b for b in data["securities"][0]["bars"] if b["date"] != data["calendar"][22]]
        opportunity = run(data)["opportunities"][0]
        acceptance = opportunity["acceptance"]
        self.assertEqual(acceptance["status"], "unknown")
        self.assertEqual(acceptance["dates"], data["calendar"][21:24])
        self.assertEqual([s["accepted"] for s in acceptance["steps"]], [False, None, None])
        self.assertIn("incompleteCumulativeEvidence", acceptance["steps"][2]["reasons"])
        self.assertEqual(acceptance["knownFailCount"], 1)
        self.assertEqual(acceptance["unknownStepCount"], 2)
        self.assertIsNotNone(opportunity["signals"]["immediate"])
        self.assertTrue(all(opportunity["signals"][m] is None for m in ("delay3", "accept3")))
        self.assertEqual(opportunity["decisionDates"]["delay3"], data["calendar"][23])

    def test_censoring_preserves_partial_failure_and_unknown_without_delayed_signal(self):
        data = fixture()
        bar_at(data, 21, close=10., volume=100.)
        data["securities"][0]["bars"][22]["amount"] = None
        opportunity = run(data, end=22)["opportunities"][0]
        acceptance = opportunity["acceptance"]
        self.assertEqual(acceptance["status"], "censored")
        self.assertEqual(acceptance["availableSessions"], 2)
        self.assertEqual(acceptance["knownFailCount"], 1)
        self.assertEqual(acceptance["unknownStepCount"], 1)
        self.assertEqual(acceptance["reasons"][0], "decisionOutsideTarget")
        self.assertIn(data["calendar"][21] + ":closeBelowAnchoredAverage", acceptance["reasons"])
        self.assertIsNone(opportunity["decisionDates"]["delay3"])
        self.assertIsNotNone(opportunity["signals"]["immediate"])
        for end in (20, 21, 22):
            item = run(fixture(), end=end)["opportunities"][0]
            self.assertEqual(item["acceptance"]["availableSessions"], end - 20)
            self.assertTrue(all(item["signals"][m] is None for m in ("delay3", "accept3")))

    def test_future_numeric_changes_ignored_for_same_end_and_append_only_resolves_censor(self):
        data = fixture()
        expected = run(data, end=22)
        future = deepcopy(data)
        for bar in future["securities"][0]["bars"][23:]:
            bar.update(close=math.nan, volume="bad", amount=math.inf)
        future["securities"][0]["tradeStatusByDate"] = {future["calendar"][24]: False}
        self.assertEqual(expected, run(future, end=22))
        full = run(data)
        self.assertEqual(expected["dailyRows"], full["dailyRows"][:3])
        self.assertEqual(expected["schedules"]["immediate"], full["schedules"]["immediate"])
        self.assertEqual(expected["opportunities"][0]["acceptance"]["steps"], full["opportunities"][0]["acceptance"]["steps"][:2])
        self.assertEqual(full["opportunities"][0]["acceptance"]["status"], "passed")
        beyond = deepcopy(data)
        beyond["calendar"].append("2025-01-02")
        beyond["securities"][0]["bars"].append({"date": "2025-01-02", "amount": math.nan})
        self.assertEqual(full, run(beyond, end=26))

    def test_prior_missing_slot_not_compressed_and_unknown_precedes_false_pattern(self):
        data = fixture(length=30)
        missing = data["calendar"][10]
        data["securities"][0]["bars"] = [b for b in data["securities"][0]["bars"] if b["date"] != missing]
        daily = run(data)["dailyRows"][0]
        self.assertEqual(daily["priorWindowDates"], data["calendar"][:20])
        self.assertEqual(daily["counts"]["priorWindowUnknownCount"], 1)
        self.assertEqual(daily["counts"]["anchorUnknownCount"], 1)
        self.assertEqual(daily["candidates"], [])
        data = fixture()
        data["securities"][0]["bars"][5]["volume"] = 0.
        data["securities"][0]["bars"][20]["amount"] = None
        bar_at(data, 20, close=9., volume=100.)["amount"] = None
        daily = run(data)["dailyRows"][0]
        self.assertEqual(daily["counts"]["priorWindowUnknownCount"], 1)
        self.assertEqual(daily["counts"]["formationBarUnknownCount"], 1)
        self.assertEqual(daily["counts"]["anchorUnknownCount"], 1)
        self.assertEqual(daily["counts"]["anchorKnownFalseCount"], 0)

    def test_explicit_stock_units_and_bar_conflicts_are_required_without_conversion(self):
        changes = (("source", "volumeUnit", None), ("source", "volumeUnit", "lots"),
                   ("source", "amountUnit", "USD"), ("bar", "volumeUnit", "lots"), ("bar", "amountUnit", "USD"))
        for where, key, value in changes:
            data = fixture()
            target = data["securities"][0] if where == "source" else data["securities"][0]["bars"][21]
            target[key] = value
            result = run(data)
            with self.subTest(where=where, key=key, value=value):
                if where == "source":
                    self.assertEqual(result["opportunities"], [])
                    self.assertEqual(result["dailyRows"][0]["counts"]["anchorUnknownCount"], 1)
                else:
                    item = result["opportunities"][0]
                    self.assertEqual(item["acceptance"]["status"], "unknown")
                    self.assertIsNone(item["signals"]["delay3"])

    def test_one_cent_data_envelope_is_inclusive_but_outside_is_unknown(self):
        for average, known in ((11.99, True), (12.01, True), (11.98999, False), (12.01001, False), (1200., False)):
            data = fixture()
            bar_at(data, 21, close=12., volume=100., average=average, low=12., high=12.)
            step = run(data)["opportunities"][0]["acceptance"]["steps"][0]
            with self.subTest(average=average):
                self.assertEqual(step["barStatus"], "valid" if known else "unknown")
                if not known:
                    self.assertIn("tradedAverageOutsideOHLC", step["barReasons"])

    def test_zero_missing_nonfinite_amount_and_volume_are_unknown_not_zero_activity(self):
        for field in ("amount", "volume"):
            for value in (0, -1., None, math.nan, math.inf, True):
                data = fixture()
                data["securities"][0]["bars"][21][field] = value
                opportunity = run(data)["opportunities"][0]
                with self.subTest(field=field, value=value):
                    self.assertEqual(opportunity["acceptance"]["status"], "unknown")
                    self.assertIsNone(opportunity["signals"]["delay3"])
                    self.assertIsNone(opportunity["signals"]["accept3"])
        data = fixture()
        del data["securities"][0]["bars"][21]["amount"]
        self.assertEqual(run(data)["opportunities"][0]["acceptance"]["status"], "unknown")

    def test_historical_halt_raw_basis_envelope_and_multiple_reasons_are_retained(self):
        mutations = ({"knownSuspended": True}, {"knownSuspended": None},
                     {"priceBasisId": "adjusted"}, {"low": 13.})
        for mutation in mutations:
            data = fixture()
            data["securities"][0]["bars"][21].update(mutation)
            with self.subTest(mutation=mutation):
                self.assertEqual(run(data)["opportunities"][0]["acceptance"]["status"], "unknown")
        data = fixture()
        data["securities"][0]["tradeStatusByDate"] = {data["calendar"][21]: "0"}
        data["securities"][0]["bars"][21].update(volume=0., amount=0., amountUnit="USD")
        reasons = run(data)["opportunities"][0]["acceptance"]["steps"][0]["barReasons"]
        self.assertEqual(reasons, ["knownSuspension", "amountUnitNotCNY", "nonpositiveOrUnknownRawOHLCV", "nonpositiveOrUnknownAmount"])
        # Unknown historical status and unrelated current metadata are not extra gates.
        data = fixture()
        data["securities"][0].update(st=True, delisted=True, eligible=False, tradeStatusByDate={})
        self.assertEqual(run(data)["counts"]["signals"], dict.fromkeys(MODES, 1))

    def test_nonfinite_cumulative_arithmetic_is_unknown_and_serializable(self):
        data = fixture()
        bar_at(data, 20, close=1e100, volume=1e208)
        bar_at(data, 21, close=1e100, volume=1e208)
        item = run(data)["opportunities"][0]
        self.assertEqual(item["acceptance"]["status"], "unknown")
        self.assertIn("nonfiniteCumulativeTotals", item["acceptance"]["steps"][0]["reasons"])
        self.assertIsNone(item["signals"]["delay3"])
        import json
        json.dumps(item, allow_nan=False)

    def test_warmup_never_creates_carried_opportunities_and_schedules_are_sorted(self):
        data = fixture(size=7)
        self.assertEqual(run(data, start=24)["opportunities"], [])
        for number in range(7):
            bar_at(data, 21, number=number, close=13., volume=1000. * (number + 1))
        result = run(data)
        for mode in MODES:
            values = result["schedules"][mode]
            self.assertEqual(values, sorted(values, key=lambda r: (r["signalDate"], r["originalRank"], r["symbol"], r["signalId"])))
        self.assertEqual(result["counts"]["opportunityCount"], 10)

    def test_no_mutation_structural_rejections_and_insufficient_history(self):
        data = fixture()
        before = deepcopy(data)
        notices = []
        result = build_volume_anchor(data, data["calendar"][20], data["calendar"][-1], notices.append)
        self.assertEqual(data, before)
        self.assertTrue(notices)
        result["opportunities"][0]["signals"]["immediate"]["symbol"] = "changed"
        self.assertEqual(data, before)
        self.assertEqual(result["schedules"]["immediate"][0]["symbol"], "s000")
        for mutate in (lambda d: d["calendar"].insert(1, d["calendar"][0]),
                       lambda d: d["securities"].append(deepcopy(d["securities"][0])),
                       lambda d: d["securities"][0]["bars"].insert(1, deepcopy(d["securities"][0]["bars"][0])),
                       lambda d: d.update(priceBasisId="adjusted")):
            changed = deepcopy(data)
            mutate(changed)
            with self.assertRaises(ValueError):
                run(changed)
        early = run(fixture(), start=5, end=5)["dailyRows"][0]
        self.assertEqual(early["counts"]["anchorUnknownCount"], 1)
        self.assertEqual(early["unknownReasonCounts"], {"insufficientPriorCalendarHistory": 1})


if __name__ == "__main__":
    unittest.main()
