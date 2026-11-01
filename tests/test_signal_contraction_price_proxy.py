from copy import deepcopy
import datetime as dt
from decimal import Inexact, ROUND_DOWN, localcontext
import json
import math
from statistics import mean
import unittest

from service.signal_contraction_price_proxy import (
    CANDIDATE_ID, evaluate_contraction, rank_contraction_signals)


def fixture(length=90):
    dates, day = [], dt.date(2021, 8, 2)
    while len(dates) < length:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += dt.timedelta(days=1)
    bars = [{"date": day, "open": 10., "high": 10., "low": 10., "close": 10.,
             "volume": 100., "amount": 80_000_000. + i * 1000,
             "priceBasisId": "raw-unadjusted"} for i, day in enumerate(dates)]
    for start, end, high, low, volume in ((50, 60, 12., 8., 100.),
                                         (60, 70, 11., 9., 80.),
                                         (70, 80, 10.8, 10., 70.)):
        for bar in bars[start:end]:
            bar.update(high=high, low=low, volume=volume)
    bars[80].update(open=12.4, high=12.6, low=12.3, close=12.5, volume=200., amount=1e12)
    return dates, bars


class RawContractionPriceTests(unittest.TestCase):
    def setUp(self):
        self.dates, self.bars = fixture()
        self.eligibility = {"eligible": True, "reason": "eligible", "unknown": [
            "historicalMembership", "historicalSt", "listingDate", "historicalTradeStatus"]}

    def evaluate(self, *, bars=None, calendar=None, eligible=None, suspended=False, symbol="sh600001"):
        return evaluate_contraction(symbol, self.dates[80], self.dates[:81] if calendar is None else calendar,
            self.bars[:81] if bars is None else bars, self.eligibility if eligible is None else eligible,
            known_suspended=suspended)

    def test_exact_causal_features_and_eight_percent_equality_trigger_without_certification(self):
        result = self.evaluate()
        self.assertEqual(result["kind"], "signaled")
        signal, f = result["signal"], result["features"]
        self.assertEqual(signal["strategyId"], CANDIDATE_ID)
        self.assertEqual(f["observations"], 81)
        self.assertEqual(f["prior40High"], 12.)
        self.assertEqual(f["ma80"], mean(b["close"] for b in self.bars[1:81]))
        self.assertEqual(f["previousMa80"], 10.)
        self.assertEqual(f["prior40MeanVolume"], 87.5)
        self.assertEqual(f["volumeRatio40"], 200 / 87.5)
        self.assertEqual([b["rangeRatio"] for b in f["contractionBlocks"]], [.5, 2 / 9, .08])
        self.assertEqual([(b["startDate"], b["endDate"]) for b in f["contractionBlocks"]],
                         [(self.dates[50], self.dates[59]), (self.dates[60], self.dates[69]),
                          (self.dates[70], self.dates[79])])
        self.assertEqual(f["prior20MeanAmount"], mean(b["amount"] for b in self.bars[60:80]))
        self.assertEqual(signal["prior20MeanAmount"], f["prior20MeanAmount"])
        self.assertEqual(f["failedConditions"], [])
        self.assertFalse(signal["certified"])
        self.assertFalse(signal["targetAchieved"])
        self.assertFalse(signal["quality"]["historicalEligibilityVerified"])
        self.assertFalse(signal["quality"]["comparableRightsVerified"])
        self.assertEqual(signal["quality"]["unknownHistoricalFields"], self.eligibility["unknown"])
        self.assertEqual(signal["actionEvidence"], "unverified")
        self.assertIn("precision 28", f["numericComparisonBasis"]["range"])
        json.dumps(result, allow_nan=False)

    def test_contraction_strictness_and_range_threshold_not_rounded(self):
        for kind in ("D1equalsD2", "D2equalsD3", "D3overLimit"):
            with self.subTest(kind=kind):
                bars = deepcopy(self.bars[:81])
                if kind == "D1equalsD2":
                    for bar in bars[50:60]:
                        bar.update(high=11., low=9.)
                elif kind == "D2equalsD3":
                    for bar in bars[60:70]:
                        bar.update(high=10.8, low=10.)
                else:
                    for bar in bars[70:80]:
                        bar["high"] = 10.800000001
                result = self.evaluate(bars=bars)
                self.assertEqual(result["kind"], "notTriggered")
                condition = "lastRangeAtMost0_08" if kind == "D3overLimit" else "rangesStrictlyDecreasing"
                self.assertFalse(result["features"]["conditions"][condition])
                self.assertIsNone(result["signal"])

    def test_volume_contraction_equality_passes_above_limit_fails(self):
        self.assertTrue(self.evaluate()["features"]["conditions"]["lastMeanVolumeAtMost0_70First"])
        for bar in self.bars[70:80]:
            bar["volume"] = 70.000001
        result = self.evaluate()
        self.assertEqual(result["kind"], "notTriggered")
        self.assertEqual(result["features"]["failedConditions"], ["lastMeanVolumeAtMost0_70First"])

    def test_original_breakout_volume_ratio_includes_equality(self):
        self.bars[80]["volume"] = 105.
        result = self.evaluate()
        self.assertEqual(result["features"]["volumeRatio40"], 1.2)
        self.assertEqual(result["kind"], "signaled")
        self.bars[80]["volume"] = 104.999
        result = self.evaluate()
        self.assertEqual(result["features"]["failedConditions"], ["breakoutVolumeRatioAtLeast1_2"])

    def test_prior40_high_window_has_exact_exclusive_t_boundary(self):
        self.bars[39]["high"] = 999.
        self.assertEqual(self.evaluate()["kind"], "signaled")
        self.bars[40]["high"] = 13.
        result = self.evaluate()
        self.assertEqual(result["features"]["prior40High"], 13.)
        self.assertFalse(result["features"]["conditions"]["closeAbovePrior40High"])

    def test_breakout_and_moving_average_comparisons_are_strict(self):
        bars = deepcopy(self.bars[:81])
        bars[-1].update(open=12., high=12., low=12., close=12.)
        result = self.evaluate(bars=bars)
        self.assertFalse(result["features"]["conditions"]["closeAbovePrior40High"])
        bars = deepcopy(self.bars[:81])
        bars[0].update(open=12.5, high=12.5, low=12.5, close=12.5)
        result = self.evaluate(bars=bars)
        self.assertEqual(result["features"]["ma80"], result["features"]["previousMa80"])
        self.assertEqual(result["features"]["failedConditions"], ["ma80Rising"])
        bars = deepcopy(self.bars[:81])
        for bar in bars[1:40]:
            bar.update(open=100., high=100., low=100., close=100.)
        result = self.evaluate(bars=bars)
        self.assertEqual(result["features"]["failedConditions"], ["closeAboveMA80"])

    def test_t_extreme_high_low_volume_amount_never_enters_contraction_or_ranking(self):
        original = self.evaluate()["features"]
        self.bars[80].update(open=12., close=13., high=1000., low=1., volume=1e9, amount=1e30)
        result = self.evaluate()
        self.assertEqual(result["kind"], "signaled")
        for key in ("contractionBlocks", "prior20MeanAmount", "rankingWindow", "prior40High", "prior40MeanVolume"):
            self.assertEqual(result["features"][key], original[key])

    def test_amount_gate_only_uses_explicit_prior_twenty_sessions(self):
        for value in (None, 0, -1, math.nan, math.inf, "80000000", True):
            with self.subTest(value=value):
                bars = deepcopy(self.bars[:81]); bars[60]["amount"] = value
                result = self.evaluate(bars=bars)
                self.assertEqual((result["kind"], result["reason"]), ("unevaluable", "prior20AmountUnknown"))
        bars = deepcopy(self.bars[:81])
        for bar in [*bars[:60], bars[-1]]:
            bar.pop("amount")
        self.assertEqual(self.evaluate(bars=bars)["kind"], "signaled")

    def test_missing_session_does_not_become_a_shorter_complete_window(self):
        for index in (0, 40, 50, 60, 70, 80):
            with self.subTest(index=index):
                bars = deepcopy(self.bars[:81]); bars[index] = None
                result = self.evaluate(bars=bars)
                self.assertEqual((result["kind"], result["reason"]), ("unevaluable", "missingAlignedRawBar"))
                self.assertEqual(result["features"]["expectedDate"], self.dates[index])
        result = self.evaluate(bars=self.bars[1:81], calendar=self.dates[1:81])
        self.assertEqual((result["kind"], result["reason"]), ("unevaluable", "insufficient81SessionHistory"))

    def test_dates_must_match_every_exchange_slot_and_end_at_t(self):
        for kind in ("missingDate", "duplicate", "future", "wrongFinalDate"):
            with self.subTest(kind=kind):
                bars, calendar = deepcopy(self.bars[:81]), list(self.dates[:81])
                if kind == "missingDate":
                    bars[0].pop("date")
                elif kind == "duplicate":
                    calendar[10] = calendar[9]
                elif kind == "future":
                    bars[20]["date"] = self.dates[88]
                else:
                    calendar[-1] = self.dates[81]
                result = self.evaluate(bars=bars, calendar=calendar)
                self.assertEqual(result["kind"], "unevaluable")
                self.assertIsNone(result["signal"])

    def test_raw_inputs_require_positive_finite_ohlcv_and_valid_envelopes(self):
        for key in ("open", "high", "low", "close", "volume"):
            for value in (None, 0, -1, math.nan, math.inf, "10", True):
                with self.subTest(key=key, value=value):
                    bars = deepcopy(self.bars[:81]); bars[20][key] = value
                    result = self.evaluate(bars=bars)
                    self.assertEqual(result["reason"], "nonpositiveOrUnknownRawOHLCV")
                    self.assertEqual(result["kind"], "unevaluable")
        bars = deepcopy(self.bars[:81]); bars[40]["close"] = 999.
        self.assertEqual(self.evaluate(bars=bars)["reason"], "invalidRawOHLCEnvelope")
        bars = deepcopy(self.bars[:81]); bars[40].pop("priceBasisId")
        self.assertEqual(self.evaluate(bars=bars)["reason"], "rawPriceBasisUnknown")

    def test_hard_rejection_precedes_unknown_inputs_and_halt_contradiction(self):
        for reason in ("delisted", "notListed", "notHistoricalMember", "historicalSt", "insufficientListingHistory"):
            with self.subTest(reason=reason):
                result = self.evaluate(bars=[None], calendar=[], eligible={"eligible": False, "reason": reason})
                self.assertEqual((result["kind"], result["reason"]), ("ineligible", "baseEligibilityRejected"))
        result = self.evaluate(eligible={"eligible": False, "reason": "amount20BelowMinimum", "amount20": 1}, suspended=True)
        self.assertEqual(result["kind"], "ineligible")
        self.assertEqual(result["reason"], "baseEligibilityRejected")

    def test_missing_base_inputs_are_unknown_not_observed_ineligibility(self):
        for reason in ("strictHistoryMissing", "invalidHistory", "missingDailyBar", "notObservedYet"):
            with self.subTest(reason=reason):
                result = self.evaluate(eligible={"eligible": False, "reason": reason})
                self.assertEqual((result["kind"], result["reason"]), ("unevaluable", reason))
        for amount in (None, math.nan, math.inf, "1", True):
            with self.subTest(amount=amount):
                result = self.evaluate(eligible={"eligible": False, "reason": "amount20BelowMinimum", "amount20": amount})
                self.assertEqual((result["kind"], result["reason"]), ("unevaluable", "baseAmountUnknown"))
        self.assertEqual(self.evaluate(eligible={"eligible": False, "reason": "amount20BelowMinimum", "amount20": 0})["kind"], "ineligible")
        self.assertEqual(self.evaluate(eligible={"eligible": None})["reason"], "baseEligibilityUnknown")
        self.assertEqual(self.evaluate(eligible={"eligible": False, "reason": "unrecognized"})["kind"], "unevaluable")

    def test_known_halt_positive_volume_is_unknown_and_zero_does_not_prove_halt(self):
        result = self.evaluate(suspended=True)
        self.assertEqual((result["kind"], result["reason"]), ("unevaluable", "suspensionContradictsPositiveVolume"))
        self.bars[80]["volume"] = 0
        result = self.evaluate(suspended=True, eligible={"eligible": False, "reason": "suspendedOrZeroVolume"})
        self.assertEqual((result["kind"], result["reason"]), ("ineligible", "knownSuspension"))
        self.assertEqual(self.evaluate()["reason"], "nonpositiveOrUnknownRawOHLCV")
        self.assertEqual(self.evaluate(eligible={"eligible": False, "reason": "suspendedOrZeroVolume"})["reason"],
                         "suspensionStatusUnknown")

    def test_decimal_context_and_float_display_never_change_range_gate(self):
        reference = self.evaluate()
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            self.assertEqual(self.evaluate(), reference)
        # Float arithmetic alone gives > .08 for this exact decimal source pair.
        self.assertGreater(10.8 / 10. - 1, .08)
        self.assertTrue(reference["features"]["conditions"]["lastRangeAtMost0_08"])
        self.assertEqual(reference["features"]["contractionBlocks"][2]["rangeDecimal"], "0.08")

    def test_future_append_and_mutation_cannot_change_t_or_mutate_inputs(self):
        initial = self.evaluate()
        before = deepcopy(self.bars)
        for bar in self.bars[81:]:
            bar.update(open=1., high=1e6, low=.001, close=1., volume=1e12, amount=1e30)
        self.assertEqual(self.evaluate(), initial)
        self.assertEqual(self.bars[:81], before[:81])
        initial["signal"]["features"]["contractionBlocks"][0]["maxHigh"] = -99
        initial["quality"]["unknownHistoricalFields"].clear()
        self.assertEqual(self.evaluate()["kind"], "signaled")
        self.assertEqual(self.eligibility["unknown"], ["historicalMembership", "historicalSt", "listingDate", "historicalTradeStatus"])

    def test_real_series_raw_source_differs_from_reference_adjusted_indicator_view(self):
        from service.portfolio_signals import DailyScorer
        security = {"symbol": "sh600001", "bars": deepcopy(self.bars),
                    "precloseByDate": {self.dates[65]: 5.}}
        snapshot = deepcopy(security)
        scorer = DailyScorer({"securities": [security]}, {}, pools_only=True)
        series = scorer.series["sh600001"]
        self.assertNotEqual(series.bars[70]["high"], series.signal_bars[70]["high"])
        raw = [{**b, "priceBasisId": "raw-unadjusted"} for b in series.bars[:81]]
        adjusted = [{**b, "priceBasisId": "reference-adjusted"} for b in series.signal_bars[:81]]
        self.assertEqual(self.evaluate(bars=raw)["kind"], "signaled")
        self.assertEqual(self.evaluate(bars=adjusted)["reason"], "rawPriceBasisUnknown")
        self.assertEqual(security, snapshot)

    def test_rank_full_cross_section_by_prior_amount_then_symbol_before_active_exclusion(self):
        signal = self.evaluate()["signal"]
        signals = [{**deepcopy(signal), "symbol": f"sh600{i:03}", "prior20MeanAmount": amount}
                   for i, amount in enumerate([80., 90., 100., 100., 70., 60., 50.])]
        before = deepcopy(signals)
        ranked = rank_contraction_signals(list(reversed(signals)))
        self.assertEqual([s["symbol"] for s in ranked], ["sh600002", "sh600003", "sh600001", "sh600000", "sh600004", "sh600005", "sh600006"])
        self.assertEqual([s["rank"] for s in ranked], list(range(1, 8)))
        self.assertEqual(sum(s["selectedTop5"] for s in ranked), 5)
        self.assertTrue(all(s["score"] == s["rawScore"] == s["prior20MeanAmount"] for s in ranked))
        survivors = [s for s in ranked if s["selectedTop5"] and s["symbol"] != "sh600002"]
        self.assertEqual(len(survivors), 4)
        self.assertNotIn("sh600005", [s["symbol"] for s in survivors])
        self.assertEqual(signals, before)

    def test_ranking_rejects_duplicates_mixed_days_bad_scores_or_other_candidates(self):
        signal = self.evaluate()["signal"]
        with self.assertRaises(ValueError):
            rank_contraction_signals([signal, signal])
        for change in ({"signalDate": self.dates[81], "symbol": "sz000001"},
                       {"prior20MeanAmount": math.nan, "symbol": "sz000001"},
                       {"strategyId": "other", "symbol": "sz000001"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                rank_contraction_signals([signal, {**signal, **change}])

    def test_stateless_emission_has_no_first_trigger_cooldown_or_position_input(self):
        first = self.evaluate()
        second = self.evaluate()
        self.assertEqual(first, second)
        calendar, bars = fixture()
        shifted = deepcopy(bars[:81])
        for i, bar in enumerate(shifted):
            bar["date"] = calendar[i + 1]
        result = evaluate_contraction("sh600001", calendar[81], calendar[1:82], shifted, self.eligibility)
        self.assertEqual(result["kind"], "signaled")
        self.assertNotEqual(first["signal"]["signalDate"], result["signal"]["signalDate"])


if __name__ == "__main__":
    unittest.main()
