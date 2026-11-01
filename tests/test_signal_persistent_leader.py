import copy
from datetime import date, timedelta
import json
import math
from statistics import mean
import unittest

from service.signal_leadership_features import daily_features
from service.signal_persistent_leader import (
    CANDIDATE_ID, THRESHOLDS, evaluate_persistent_leader, rank_persistent_leader_signals,
)


def fixture():
    dates, day = [], date(2021, 8, 2)
    while len(dates) < 61:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    bars = [{"date": d, "priceBasisId": "raw-unadjusted", "open": 100.+i,
             "high": 100.+i, "low": 100.+i, "close": 100.+i,
             "volume": 1000., "amount": 100000000.} for i, d in enumerate(dates)]
    benchmark = [{**bar, "open": 100., "high": 100., "low": 100., "close": 100.} for bar in bars]
    row = daily_features("sh600001", dates[-1], dates, bars, benchmark)
    row.update(baseEligibility={"eligible": True, "reason": "eligible", "unknown": ["historicalSt", "membership"]},
        relativeReturn20Percentile=.9, relativeReturn60Percentile=.9,
        relativeReturn20CrossSectionCount=100, relativeReturn60CrossSectionCount=100,
        trailing20DailyStrengthPercentiles=[.9]*20, trailing20StrengthCrossSectionCounts=[100]*20,
        strengthRankSessionDates20=dates[-20:], knownStrengthRankSessions20=20,
        availableStrengthRankSessions20=20, meanStrengthPercentile20=.9, minimumStrengthPercentile20=.9,
        topQuantileSessionCount20=None, closePathMaxDrawdown20=.1, signedPathEfficiency20=.35)
    return dates, bars, row


def evaluate(dates, bars, row):
    return evaluate_persistent_leader("sh600001", dates[-1], dates, bars, row)


def set_history(row, values):
    row["trailing20DailyStrengthPercentiles"] = list(values)
    row["meanStrengthPercentile20"] = mean(values)
    row["minimumStrengthPercentile20"] = min(values)
    row["relativeReturn20Percentile"] = values[-1]


class PersistentLeaderTests(unittest.TestCase):
    def test_all_five_inclusive_thresholds_and_strict_breakout(self):
        dates, bars, row = fixture()
        result = evaluate(dates, bars, row)
        self.assertEqual(result["kind"], "signaled")
        self.assertEqual(result["signal"]["strategyId"], CANDIDATE_ID)
        self.assertEqual(len(result["predicates"]), 6)
        self.assertTrue(all(p["passed"] is True for p in result["predicates"].values()))
        self.assertEqual(result["features"]["prior5High"], 159)
        self.assertEqual(result["features"]["prior5Window"], {"startDate": dates[-6], "endDate": dates[-2], "sessions": 5})
        self.assertEqual(len(result["features"]["breakoutBars"]), 6)
        self.assertFalse(result["quality"]["certified"])

    def test_mean_threshold_exact_equality_and_one_float_either_side(self):
        for value, expected in ((.9, "signaled"), (math.nextafter(.9, -math.inf), "notTriggered"),
                                (math.nextafter(.9, math.inf), "signaled")):
            with self.subTest(value=value):
                dates, bars, row = fixture()
                set_history(row, [value]*20)
                result = evaluate(dates, bars, row)
                self.assertEqual(result["kind"], expected)
                self.assertEqual(result["predicates"]["meanStrengthPercentile20"]["passed"], value >= .9)

    def test_minimum_threshold_equality_and_one_float_either_side(self):
        for value, expected in ((.8, "signaled"), (math.nextafter(.8, -math.inf), "notTriggered"),
                                (math.nextafter(.8, math.inf), "signaled")):
            dates, bars, row = fixture()
            set_history(row, [value] + [.91]*19)
            result = evaluate(dates, bars, row)
            self.assertEqual(result["kind"], expected)
            self.assertGreaterEqual(row["meanStrengthPercentile20"], .9)

    def test_other_thresholds_have_no_epsilon_or_display_rounding(self):
        for key, op, boundary in THRESHOLDS[2:]:
            for value in (math.nextafter(boundary, -math.inf), boundary, math.nextafter(boundary, math.inf)):
                with self.subTest(key=key, value=value):
                    dates, bars, row = fixture()
                    row[key] = value
                    passed = value >= boundary if op == ">=" else value <= boundary
                    result = evaluate(dates, bars, row)
                    self.assertEqual(result["kind"], "signaled" if passed else "notTriggered")
                    self.assertEqual(result["predicates"][key]["passed"], passed)

    def test_breakout_equality_fails_one_float_above_passes(self):
        for close, expected in ((159., "notTriggered"), (math.nextafter(159., math.inf), "signaled")):
            dates, bars, row = fixture()
            bars[-1].update(open=close, high=close, low=close, close=close)
            result = evaluate(dates, bars, row)
            self.assertEqual(result["kind"], expected)
            self.assertEqual(result["predicates"]["closeAbovePrior5High"]["passed"], close > 159.)

    def test_current_high_and_day_minus_six_do_not_enter_prior_five(self):
        dates, bars, row = fixture()
        bars[-1]["high"] = 1e9
        bars[-7]["high"] = 1e8
        result = evaluate(dates, bars, row)
        self.assertEqual(result["kind"], "signaled")
        self.assertEqual(result["features"]["prior5High"], 159.)
        bars[-6]["high"] = 1e6
        self.assertEqual(evaluate(dates, bars, row)["kind"], "notTriggered")

    def test_does_not_recalculate_strength_or_require_unregistered_amount_ratio(self):
        dates, bars, row = fixture()
        row["amountRelativeToOwnPrior20"] = None
        row["prior20MeanAmount"] = None
        row["completeness"]["amountPrior20"]["complete"] = False
        row["featureReasons"]["amountRelativeToOwnPrior20"] = ["explicitRawAmountUnknown"]
        for bar in bars:
            bar.pop("amount")
        # These are archived panel inputs, not a request to recalculate them.
        self.assertEqual(evaluate(dates, bars, row)["kind"], "signaled")

    def test_observed_false_conditions_all_remain_visible(self):
        dates, bars, row = fixture()
        set_history(row, [.5]*20)
        row.update(relativeReturn60Percentile=.5, closePathMaxDrawdown20=.11, signedPathEfficiency20=.2)
        bars[-2]["high"] = 200.
        result = evaluate(dates, bars, row)
        self.assertEqual(result["kind"], "notTriggered")
        self.assertTrue(all(p["passed"] is False for p in result["predicates"].values()))
        self.assertEqual(len(result["features"]["failedPredicates"]), 6)
        self.assertIsNone(result["signal"])
        self.assertEqual(rank_persistent_leader_signals([]), [])

    def test_wrong_identity_version_and_missing_sidecar_are_unknown(self):
        for key, value in (("symbol", "sz000001"), ("asOfDate", "2025-01-01"),
                           ("featureVersion", "another-version"), ("baseEligibility", None)):
            dates, bars, row = fixture()
            row[key] = value
            self.assertEqual(evaluate(dates, bars, row)["kind"], "unevaluable")

    def test_legacy_rejection_classification_without_claiming_amount_evidence(self):
        for reason in ("amount20BelowMinimum", "insufficientListingHistory", "historicalSt", "delisted", "suspendedOrZeroVolume"):
            dates, bars, row = fixture()
            row["baseEligibility"].update(eligible=False, reason=reason)
            result = evaluate(dates, bars, row)
            self.assertEqual(result["kind"], "ineligible")
            self.assertEqual(result["reason"], "legacyDiagnosticEligibilityRejected")
            self.assertNotIn("amount20", result["features"]["baseEligibility"])
            self.assertTrue(all(p["passed"] is True for p in result["predicates"].values()))

    def test_base_rejection_keeps_priority_and_partial_independent_evidence(self):
        dates, bars, row = fixture()
        row["baseEligibility"].update(eligible=False, reason="historicalSt")
        bars[0] = None
        result = evaluate(dates, bars, row)
        self.assertEqual(result["kind"], "ineligible")
        self.assertIsNone(result["predicates"]["relativeReturn60Percentile"]["passed"])
        for key in ("meanStrengthPercentile20", "minimumStrengthPercentile20", "closePathMaxDrawdown20",
                    "signedPathEfficiency20", "closeAbovePrior5High"):
            self.assertIs(result["predicates"][key]["passed"], True)

    def test_one_missing_field_preserves_other_known_predicates(self):
        dates, bars, row = fixture()
        row["signedPathEfficiency20"] = None
        result = evaluate(dates, bars, row)
        self.assertEqual(result["kind"], "unevaluable")
        self.assertIsNone(result["predicates"]["signedPathEfficiency20"]["passed"])
        self.assertEqual(sum(p["passed"] is True for p in result["predicates"].values()), 5)
        row["trailing20DailyStrengthPercentiles"][0] = None
        result = evaluate(dates, bars, row)
        self.assertIsNone(result["predicates"]["meanStrengthPercentile20"]["passed"])
        self.assertIsNone(result["predicates"]["minimumStrengthPercentile20"]["passed"])
        self.assertIs(result["predicates"]["relativeReturn60Percentile"]["passed"], True)
        self.assertIs(result["predicates"]["closeAbovePrior5High"]["passed"], True)

    def test_unknown_eligibility_keeps_known_numeric_evidence_without_a_signal(self):
        dates, bars, row = fixture()
        row["baseEligibility"] = None
        result = evaluate(dates, bars, row)
        self.assertEqual(result["kind"], "unevaluable")
        self.assertTrue(all(p["passed"] is True for p in result["predicates"].values()))
        self.assertIsNone(result["signal"])

    def test_unknown_qualification_reasons_and_malformed_states_do_not_reject(self):
        for reason in ("invalidHistory", "missingDailyBar", "notObservedYet"):
            dates, bars, row = fixture()
            row["baseEligibility"].update(eligible=False, reason=reason)
            self.assertEqual(evaluate(dates, bars, row)["kind"], "unevaluable")
        for state in ({}, {"eligible": 1, "reason": "eligible", "unknown": []},
                      {"eligible": True, "reason": "eligible"}, {"eligible": True, "reason": "", "unknown": []}):
            dates, bars, row = fixture()
            row["baseEligibility"] = state
            self.assertEqual(evaluate(dates, bars, row)["kind"], "unevaluable")

    def test_unknown_historical_identity_can_only_pass_as_raw_diagnostic(self):
        dates, bars, row = fixture()
        result = evaluate(dates, bars, row)
        self.assertEqual(result["kind"], "signaled")
        self.assertEqual(result["quality"]["unknownHistoricalFields"], ["historicalSt", "membership"])
        self.assertFalse(result["quality"]["historicalEligibilityVerified"])
        self.assertEqual(result["quality"]["actionEvidence"], "unverified")

    def test_raw_missing_invalid_alignment_and_basis_block_even_if_panel_says_complete(self):
        for change in (None, {"date": "2026-01-01"}, {"priceBasisId": "reference-adjusted"},
                       {"volume": 0}, {"close": float("nan")}, {"high": 1}, {"low": True}):
            dates, bars, row = fixture()
            if change is None:
                bars[0] = None
            else:
                bars[0].update(change)
            result = evaluate(dates, bars, row)
            self.assertEqual(result["kind"], "unevaluable")
            self.assertIsNone(result["signal"])
            json.dumps(result, allow_nan=False)

    def test_short_unsorted_duplicate_and_future_calendars_are_unknown(self):
        dates, bars, row = fixture()
        self.assertEqual(evaluate(dates[1:], bars[1:], row)["kind"], "unevaluable")
        for replacement in (dates[1], "2025-01-01"):
            altered = dates.copy()
            altered[0] = replacement
            self.assertEqual(evaluate(altered, bars, row)["kind"], "unevaluable")
        altered = dates.copy()
        altered[0], altered[1] = altered[1], altered[0]
        self.assertEqual(evaluate(altered, bars, row)["kind"], "unevaluable")

    def test_explicit_halt_and_inconsistent_complete_counts_are_unknown(self):
        for mutate in (
            lambda row, day: row["haltDates"].append(day),
            lambda row, day: row["completeness"]["security20"]["haltDates"].append(day),
            lambda row, day: row["completeness"]["benchmark60"].update(validSessions=60),
            lambda row, day: row["benchmarkQuality"]["haltDates"].append(day),
            lambda row, day: row.update(validSessions=60),
        ):
            dates, bars, row = fixture()
            mutate(row, dates[-1])
            self.assertEqual(evaluate(dates, bars, row)["kind"], "unevaluable")

    def test_required_panel_fields_and_reasons_cannot_fake_known_features(self):
        for key in ("relativeReturn20", "relativeReturn60", "return20", "return60", "signedPathEfficiency20"):
            dates, bars, row = fixture()
            row[key] = None
            self.assertEqual(evaluate(dates, bars, row)["kind"], "unevaluable")
        dates, bars, row = fixture()
        row["featureReasons"]["relativeReturn60"] = ["missingDailyBar"]
        self.assertEqual(evaluate(dates, bars, row)["kind"], "unevaluable")

    def test_history_requires_twenty_slots_dates_counts_and_current_rank_agreement(self):
        for mutate in (
            lambda r: r["trailing20DailyStrengthPercentiles"].pop(),
            lambda r: r["trailing20DailyStrengthPercentiles"].__setitem__(0, None),
            lambda r: r["trailing20StrengthCrossSectionCounts"].__setitem__(0, 0),
            lambda r: r["trailing20StrengthCrossSectionCounts"].__setitem__(0, True),
            lambda r: r["strengthRankSessionDates20"].reverse(),
            lambda r: r.update(knownStrengthRankSessions20=19),
            lambda r: r.update(availableStrengthRankSessions20=19),
            lambda r: r.update(relativeReturn20CrossSectionCount=99),
            lambda r: r.update(relativeReturn20Percentile=.95),
            lambda r: r.update(relativeReturn60CrossSectionCount=0),
        ):
            dates, bars, row = fixture()
            mutate(row)
            self.assertEqual(evaluate(dates, bars, row)["kind"], "unevaluable")

    def test_mean_and_min_reconcile_exactly_without_epsilon(self):
        for key in ("meanStrengthPercentile20", "minimumStrengthPercentile20"):
            dates, bars, row = fixture()
            row[key] = math.nextafter(row[key], math.inf)
            self.assertEqual(evaluate(dates, bars, row)["reason"], "archivedStrengthAggregateMismatch")

    def test_finite_and_domain_boundaries_do_not_pass_bad_panel_values(self):
        cases = [(key, value) for key, _, _ in THRESHOLDS for value in (float("inf"), float("nan"), True, 10**400)]
        cases += [("relativeReturn60Percentile", 1.01), ("closePathMaxDrawdown20", -.01), ("signedPathEfficiency20", -1.01)]
        for key, value in cases:
            dates, bars, row = fixture()
            row[key] = value
            result = evaluate(dates, bars, row)
            self.assertEqual(result["kind"], "unevaluable", key)
            json.dumps(result, allow_nan=False)

    def test_no_input_mutation_no_state_and_no_future_metadata_effect(self):
        dates, bars, row = fixture()
        before = copy.deepcopy((dates, bars, row))
        first = evaluate(dates, bars, row)
        self.assertEqual((dates, bars, row), before)
        self.assertEqual(evaluate(dates, bars, row), first)
        row.update(futureReturn=999, futureDelistDate="2027-01-01", currentName="ST FUTURE")
        bars[0]["futureReferenceClose"] = 99999
        self.assertEqual(evaluate(dates, bars, row), first)
        first["signal"]["features"]["archivedRankPercentiles20"][0] = 0
        self.assertEqual(row["trailing20DailyStrengthPercentiles"][0], .9)

    def test_rank_lexicographic_ties_all_candidates_before_external_active(self):
        dates, bars, row = fixture()
        base = evaluate(dates, bars, row)["signal"]
        values = [("g", .91, .99), ("f", .95, .90), ("e", .95, .95), ("d", .95, .95),
                  ("c", .96, .90), ("b", .96, .91), ("a", .97, .90)]
        signals = [{**copy.deepcopy(base), "symbol": s, "meanStrengthPercentile20": m,
                    "relativeReturn60Percentile": r} for s, m, r in values]
        before = copy.deepcopy(signals)
        ranked = rank_persistent_leader_signals(signals)
        self.assertEqual(signals, before)
        self.assertEqual([r["symbol"] for r in ranked], ["a", "b", "c", "d", "e", "f", "g"])
        self.assertEqual([r["rank"] for r in ranked], list(range(1, 8)))
        self.assertEqual([r["selectedTop5"] for r in ranked], [True]*5 + [False]*2)
        selected = [r for r in ranked if r["selectedTop5"] and r["symbol"] != "a"]
        self.assertEqual([r["symbol"] for r in selected], ["b", "c", "d", "e"])

    def test_rank_rejects_duplicate_mixed_days_and_bad_ranking_fields(self):
        dates, bars, row = fixture()
        signal = evaluate(dates, bars, row)["signal"]
        with self.assertRaises(ValueError):
            rank_persistent_leader_signals([signal, signal])
        other = {**signal, "symbol": "sz000001", "signalDate": "2022-01-04"}
        with self.assertRaises(ValueError):
            rank_persistent_leader_signals([signal, other])
        with self.assertRaises(ValueError):
            rank_persistent_leader_signals([{**signal, "meanStrengthPercentile20": None}])


if __name__ == "__main__":
    unittest.main()
