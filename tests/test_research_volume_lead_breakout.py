"""Boundary evidence for the fixed price-breakout / prior-volume-lead design."""
from copy import deepcopy
from datetime import date, timedelta
import json
import unittest

from service.research_volume_lead_breakout import build_volume_lead_breakout, POLICIES, STRATEGY_ID


def fixture(symbols=1, sessions=26):
    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(sessions)]
    securities = []
    for n in range(symbols):
        bars = [{"date": day, "open": 10., "high": 11., "low": 9., "close": 10.,
                 "volume": 1000., "amount": (symbols - n) * 100000000.} for day in days]
        bars[20].update(open=9., high=10., low=8., close=9.)
        bars[21]["volume"] = 2000.
        bars[22].update(open=12., high=13., low=11., close=12.)
        securities.append({"symbol": "sz" + str(n + 1).zfill(6), "bars": bars})
    return {"calendar": days, "securities": securities}


def build(data, first=22, last=22, progress=None):
    return build_volume_lead_breakout(data, start=data["calendar"][first], end=data["calendar"][last], progress=progress)


def set_price(data, index, close, symbol=0):
    data["securities"][symbol]["bars"][index].update(open=close, high=close + 1, low=close - 1, close=close)


class VolumeLeadBreakoutTests(unittest.TestCase):
    def test_exact_twenty_three_slots_and_prior_only_obv(self):
        data = fixture()
        result = build(data)
        opportunity = result["opportunities"][0]
        evidence, days = opportunity["evidence"], data["calendar"]
        self.assertEqual(evidence["windowDates"], days[:23])
        self.assertEqual(evidence["priorAmountDates"], days[2:22])
        self.assertEqual(evidence["obv"]["levelDates"], days[:22])
        self.assertEqual(evidence["obv"]["comparisonDates"], days[1:21])
        self.assertEqual(evidence["obv"]["previousLevel"], 1000.)
        self.assertEqual(evidence["obv"]["prior20Maximum"], 0)
        self.assertEqual(opportunity["formationStatus"], "passed")
        self.assertEqual(opportunity["signalDate"], days[22])
        self.assertEqual(opportunity["peerFormationDate"], days[21])
        self.assertEqual(result["schedules"]["priceBreakout"], result["schedules"]["volumeLead"])

    def test_current_high_equality_is_not_breakout(self):
        data = fixture(); set_price(data, 22, 11.)
        result = build(data)
        self.assertFalse(result["opportunities"])
        self.assertEqual(result["counts"]["commonStatusCounts"], {"candidate": 0, "notTriggered": 1, "commonUnknown": 0})
        self.assertEqual(result["counts"]["commonReasonCounts"]["currentBreakoutFalse"], 1)

    def test_previous_high_equality_allows_new_breakout(self):
        data = fixture(); set_price(data, 21, 11.); set_price(data, 22, 13.)
        opportunity = build(data)["opportunities"][0]
        self.assertTrue(opportunity["evidence"]["predicates"]["previousNotBreakout"])
        self.assertEqual(opportunity["evidence"]["previousClose"], opportunity["evidence"]["previousPrior20High"])

    def test_previous_breakout_excluded_even_with_current_breakout(self):
        data = fixture(); set_price(data, 21, 12.); set_price(data, 22, 14.)
        result = build(data)
        self.assertFalse(result["opportunities"])
        self.assertEqual(result["counts"]["commonReasonCounts"]["previousBreakoutTrue"], 1)
        self.assertEqual(result["counts"]["commonStatusCounts"]["commonUnknown"], 0)

    def test_previous_up_is_common_control_not_volume_filter(self):
        for previous_close in (9., 8.5):
            with self.subTest(previous_close=previous_close):
                data = fixture(); set_price(data, 21, previous_close)
                result = build(data)
                self.assertEqual(result["counts"]["commonReasonCounts"]["previousUpFalse"], 1)
                self.assertEqual(result["schedules"], {policy: [] for policy in POLICIES})

    def test_obv_equality_fails_only_volume_arm(self):
        data = fixture(); data["securities"][0]["bars"][21]["volume"] = 1000.
        result = build(data)
        self.assertEqual(result["opportunities"][0]["formationStatus"], "failed")
        self.assertEqual(result["counts"]["signals"], {"priceBreakout": 1, "volumeLead": 0})
        self.assertEqual(result["counts"]["selectedFormationCounts"]["unknown"], 0)

    def test_flat_close_adds_zero_despite_huge_volume(self):
        data = fixture(); data["securities"][0]["bars"][8]["volume"] = 1e200
        obv = build(data)["opportunities"][0]["evidence"]["obv"]
        self.assertEqual(obv["closeDirections"][8], 0)
        self.assertEqual(obv["signedVolumes"][8], 0)
        self.assertEqual(obv["levels"][8], 0)
        self.assertTrue(obv["passed"])

    def test_obv_baseline_is_excluded_from_twenty_comparisons(self):
        data = fixture()
        for at in range(1, 20): set_price(data, at, 9.)
        set_price(data, 20, 8.); set_price(data, 21, 9.)
        data["securities"][0]["bars"][21]["volume"] = 1500.
        obv = build(data)["opportunities"][0]["evidence"]["obv"]
        self.assertEqual((obv["previousLevel"], obv["prior20Maximum"]), (-500., -1000.))
        self.assertTrue(obv["passed"])

    def test_current_volume_and_amount_do_not_enter_prior_features(self):
        data = fixture(); before = build(data)
        data["securities"][0]["bars"][22].update(volume=1e99, amount=1e100)
        self.assertEqual(before, build(data))

    def test_volume_mask_never_backfills_sixth_price_candidate(self):
        data = fixture(6)
        for security in data["securities"][:5]: security["bars"][21]["volume"] = 500.
        result = build(data)
        self.assertEqual(len(result["dailyRows"][0]["currentCandidates"]), 6)
        self.assertEqual([row["originalRank"] for row in result["opportunities"]], [1, 2, 3, 4, 5])
        self.assertEqual(result["counts"]["signals"], {"priceBreakout": 5, "volumeLead": 0})
        self.assertEqual(result["counts"]["rankExcludedCount"], 1)
        self.assertEqual(result["dailyRows"][0]["currentCandidates"][5]["formationStatus"], "passed")

    def test_mean_amount_ties_use_symbol_not_input_order(self):
        data = fixture(3)
        for security in data["securities"]:
            for bar in security["bars"]: bar["amount"] = 100000000.
        data["securities"].reverse()
        result = build(data)
        self.assertEqual([row["symbol"] for row in result["opportunities"]], ["sz000001", "sz000002", "sz000003"])

    def test_missing_slot_not_compressed_and_unknown_remains_distinct(self):
        data = fixture(); missing = data["securities"][0]["bars"].pop(10)["date"]
        result = build(data)
        self.assertFalse(result["opportunities"])
        self.assertEqual(result["counts"]["commonStatusCounts"]["commonUnknown"], 1)
        self.assertEqual(result["dailyRows"][0]["commonUnknowns"][0]["invalidSlots"],
                         [{"date": missing, "reasons": ["missingAlignedBar"]}])
        self.assertEqual(result["counts"]["selectedFormationCounts"], {"passed": 0, "failed": 0, "unknown": 0})

    def test_each_numeric_field_requires_positive_finite_nonbool(self):
        for field in ("open", "high", "low", "close", "volume", "amount"):
            for value in (None, 0, -1, True, float("inf"), float("nan")):
                with self.subTest(field=field, value=value):
                    data = fixture(); data["securities"][0]["bars"][22][field] = value
                    result = build(data)
                    self.assertEqual(result["counts"]["commonStatusCounts"]["commonUnknown"], 1)
                    self.assertEqual(result["counts"]["commonReasonCounts"]["nonpositiveOrUnknown" + field.title()], 1)
                    json.dumps(result, allow_nan=False)

    def test_envelope_defect_and_repeated_reason_count_once_per_symbol_day(self):
        data = fixture(); bars = data["securities"][0]["bars"]
        bars[7]["high"] = 9.5
        bars[8]["amount"] = bars[9]["amount"] = 0
        result = build(data)
        self.assertEqual(result["counts"]["commonReasonCounts"]["invalidOHLCEnvelope"], 1)
        self.assertEqual(result["counts"]["commonReasonCounts"]["nonpositiveOrUnknownAmount"], 1)
        self.assertEqual(len(result["dailyRows"][0]["commonUnknowns"][0]["invalidSlots"]), 3)

    def test_insufficient_history_and_zero_signal_dates_retained(self):
        data = fixture(); result = build(data, first=0, last=23)
        self.assertEqual(len(result["dailyRows"]), 24)
        self.assertEqual(result["metadata"]["targetDates"], data["calendar"][:24])
        self.assertEqual(result["counts"]["commonStatusCounts"], {"candidate": 1, "notTriggered": 1, "commonUnknown": 22})
        self.assertIsNone(result["dailyRows"][0]["peerFormationDate"])

    def test_final_session_keeps_signal_without_future_execution_bar(self):
        data = fixture(sessions=23); result = build(data)
        for policy in POLICIES:
            signal = result["schedules"][policy][0]
            self.assertEqual(set(signal), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})
            self.assertEqual(signal["signalId"], STRATEGY_ID + ":" + data["calendar"][-1] + ":sz000001")

    def test_future_numeric_poison_and_calendar_append_preserve_prefix(self):
        data = fixture(sessions=23); result = build(data)
        extended = fixture(sessions=30)
        for security in extended["securities"]:
            for bar in security["bars"][23:]:
                for field in ("open", "high", "low", "close", "volume", "amount"): bar[field] = None
        self.assertEqual(build(extended), result)
        later = build(extended, last=29)
        self.assertEqual(later["dailyRows"][:1], result["dailyRows"])
        self.assertEqual(later["opportunities"], result["opportunities"])

    def test_duplicate_and_outside_calendar_identities_rejected(self):
        for kind in ("symbol", "bar", "calendar", "outside", "barOrder"):
            with self.subTest(kind=kind):
                data = fixture()
                if kind == "symbol": data["securities"].append(deepcopy(data["securities"][0]))
                elif kind == "bar": data["securities"][0]["bars"].insert(5, deepcopy(data["securities"][0]["bars"][5]))
                elif kind == "calendar": data["calendar"].insert(5, data["calendar"][5])
                elif kind == "outside": data["calendar"].pop(5)
                else: data["securities"][0]["bars"][4:6] = reversed(data["securities"][0]["bars"][4:6])
                with self.assertRaises(ValueError): build(data)

    def test_no_extra_halt_basis_st_or_listing_gate_and_input_immutable(self):
        data = fixture()
        data.update(priceBasisId="unspecified", corporateActions=[{"uninterpreted": True}])
        security = data["securities"][0]
        security.update(tradeStatusByDate={data["calendar"][21]: 0}, stByDate={data["calendar"][21]: True}, listingDate=data["calendar"][21])
        security["bars"][21].update(knownSuspended=True, priceBasisId="another-basis", preclose=0)
        snapshot = deepcopy(data); updates = []
        result = build(data, progress=updates.append)
        self.assertEqual(data, snapshot)
        self.assertEqual(result["counts"]["signals"], {"priceBreakout": 1, "volumeLead": 1})
        result["opportunities"][0]["evidence"]["windowDates"][0] = "changed"
        self.assertEqual(data, snapshot)
        self.assertEqual(updates[-1]["processedTargetDays"], 1)

    def test_nonfinite_derived_values_are_unknown_in_both_arms(self):
        for problem in ("amount", "obv"):
            with self.subTest(problem=problem):
                data = fixture()
                if problem == "amount":
                    for bar in data["securities"][0]["bars"][2:22]: bar["amount"] = 1e308
                    expected = "nonfinitePriorMeanAmount"
                else:
                    set_price(data, 19, 9.5)
                    data["securities"][0]["bars"][19]["volume"] = 1e308
                    data["securities"][0]["bars"][20]["volume"] = 1e308
                    expected = "nonfiniteObvLevel"
                result = build(data)
                self.assertEqual(result["counts"]["commonStatusCounts"]["commonUnknown"], 1)
                self.assertEqual(result["counts"]["commonReasonCounts"][expected], 1)
                self.assertFalse(result["opportunities"])


if __name__ == "__main__":
    unittest.main()
