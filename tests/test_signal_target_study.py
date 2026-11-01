import datetime as dt
import json
import unittest
from copy import deepcopy

from service.signal_target_study import SignalTargetStudy, summarize_events
from service.portfolio_engine import PortfolioEngine


SYMBOL = "sh600001"


def fixture(forward=None, length=20):
    prices = [10.] * 6 + (forward if forward is not None else [10.] * length)
    day, rows = dt.date(2024, 1, 2), []
    for price in prices:
        while day.weekday() > 4:
            day += dt.timedelta(days=1)
        rows.append(dict(date=day.isoformat(), open=price, high=price, low=price,
                         close=price, volume=1_000_000, amount=price * 1_000_000))
        day += dt.timedelta(days=1)
    security = dict(symbol=SYMBOL, bars=rows, listingDate="2010-01-01",
                    stByDate={r["date"]: False for r in rows},
                    tradeStatusByDate={r["date"]: True for r in rows},
                    precloseByDate={r["date"]: rows[max(0, i - 1)]["close"] for i, r in enumerate(rows)})
    data = dict(securities=[security], calendar=[r["date"] for r in rows], fingerprint="synthetic-signal-target")
    settings = dict(commissionRate=0, minCommission=0, transferFeeRate=0, stampDutyRate=0,
                    slippageBps=0, maxParticipationRate=.1,
                    universe=dict(strictHistory=False, excludeSt=True, minListingDays=0, minAmount20=0))
    signal = dict(strategyId="control_momentum", symbol=SYMBOL, signalDate=data["calendar"][5], rank=1, score=.8)
    return data, settings, signal


class SignalTargetStudyTests(unittest.TestCase):
    def evaluate(self, data, settings, signal):
        return SignalTargetStudy(data, settings).evaluate(signal)

    def test_frozen_budget_quantity_and_input_immutability(self):
        data, settings, signal = fixture()
        original = deepcopy(data)
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["signal"], signal)
        self.assertEqual(event["entry"]["plannedQuantity"], 9000)
        self.assertEqual(event["entry"]["quantity"], 9000)
        self.assertEqual(event["entry"]["investedCapital"], 90000)
        self.assertEqual(event["entry"]["remainingCash"], 10000)
        self.assertEqual(data, original)
        data["securities"][0]["bars"][6]["volume"] = 10_000_000
        data["securities"][0]["bars"][6].update(open=9.5, low=9.5)
        changed = self.evaluate(data, settings, signal)
        self.assertEqual(changed["entry"]["plannedQuantity"], 9000)
        self.assertEqual(changed["entry"]["quantity"], 9000)
        self.assertLess(changed["entry"]["investedCapital"], 90000)

    def test_t_plus_one_entry_is_attempted_once_and_not_deferred(self):
        data, settings, signal = fixture()
        data["securities"][0]["bars"][6].update(open=11., high=11., low=11., close=11.)
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["entry"]["status"], "unfilled")
        self.assertEqual(event["entry"]["reason"], "openAtUpperLimitOrUncertainST")
        self.assertEqual(event["activeUntil"], data["calendar"][6])
        self.assertEqual(event["entry"]["retryPolicy"], "noDeferredEntry")
        self.assertIsNone(event["outcomes"]["nextOpenNet30"]["hitDate"])

    def test_entry_high_is_raw_observation_only_with_t_plus_one_lock(self):
        data, settings, signal = fixture()
        data["securities"][0]["bars"][6]["high"] = 13.
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["outcomes"]["rawHigh30"]["tradingDays"], 1)
        self.assertIsNone(event["outcomes"]["netHigh30"]["hitDate"])
        self.assertIsNone(event["outcomes"]["nextOpenNet30"]["hitDate"])

    def test_raw_thirty_is_not_net_thirty_and_fees_use_portfolio_logic(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.] + [13.] * 5)
        settings.update(commissionRate=.0003, minCommission=5, transferFeeRate=.00001, stampDutyRate=.0005)
        event = self.evaluate(data, settings, signal)
        self.assertIsNotNone(event["outcomes"]["rawHigh30"]["hitDate"])
        self.assertIsNone(event["outcomes"]["netHigh30"]["hitDate"])
        self.assertIsNone(event["outcomes"]["nextOpenNet30"]["hitDate"])
        engine = object.__new__(PortfolioEngine)
        engine.config = SignalTargetStudy(data, settings).config
        self.assertEqual(event["entry"]["fee"], engine._fees(event["entry"]["amount"], "buy", event["entry"]["date"]))

    def test_main_exit_open_excludes_later_high_low_and_stops_excursions(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.5, 13.5, 13.5])
        data["securities"][0]["bars"][10].update(low=1., high=100.)
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["outcomes"]["nextOpenNet30"]["tradingDays"], 5)
        self.assertEqual(event["activeUntil"], data["calendar"][10])
        self.assertAlmostEqual(event["excursions"]["maePct"], 0.)
        self.assertAlmostEqual(event["excursions"]["mfePct"], 35.)
        self.assertEqual(event["excursions"]["observedBarDays"], 5)
        self.assertAlmostEqual(event["terminal"]["netReturnPct"], 35.)
        self.assertAlmostEqual(event["terminal"]["budgetReturnPct"], 31.5)
        self.assertFalse(event["quality"]["targetAchieved"])

    def test_recent_early_winner_does_not_enter_long_horizon_denominator(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.5, 13.5])
        recent = self.evaluate(data, settings, signal)
        older_data, older_settings, older_signal = fixture([10., 10.8, 11.6, 12.5, 13.5] + [13.5] * 20)
        older = self.evaluate(older_data, older_settings, older_signal)
        summary = summarize_events([recent, older], horizons=[5, 10])
        self.assertEqual(summary["horizons"][0]["eligibleEntryCohort"], 2)
        self.assertEqual(summary["horizons"][1]["eligibleEntryCohort"], 1)
        self.assertEqual(summary["horizons"][1]["rightCensoredBeforeHorizon"], 1)
        for proxy in summary["horizons"][1]["proxies"].values():
            self.assertEqual(proxy["denominator"], 1)
            self.assertEqual(proxy["hits"], 1)

    def test_missing_after_exit_does_not_remove_complete_potential_cohort(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.5] + [13.5] * 20)
        missing_day = data["calendar"][15]
        data["securities"][0]["bars"] = [r for r in data["securities"][0]["bars"] if r["date"] != missing_day]
        event = self.evaluate(data, settings, signal)
        self.assertFalse(event["quality"]["dataMissing"])
        self.assertEqual(event["observation"]["missingDates"][0]["date"], missing_day)
        row = summarize_events([event], horizons=[20])["horizons"][0]
        self.assertEqual(row["eligibleEntryCohort"], 1)
        self.assertEqual(row["proxies"]["nextOpenNet30"]["hitRate"], 1)

    def test_missing_while_held_differs_from_dataset_end_censoring(self):
        data, settings, signal = fixture(length=12)
        missing_day = data["calendar"][10]
        data["securities"][0]["bars"] = [r for r in data["securities"][0]["bars"] if r["date"] != missing_day]
        event = self.evaluate(data, settings, signal)
        summary = summarize_events([event], horizons=[3, 10, 20])
        self.assertTrue(event["quality"]["dataMissing"])
        self.assertEqual(summary["horizons"][0]["eligibleEntryCohort"], 1)
        self.assertEqual(summary["horizons"][1]["missingOrInvalidDataExcluded"], 1)
        self.assertEqual(summary["horizons"][2]["rightCensoredBeforeHorizon"], 1)
        self.assertEqual(summary["horizons"][2]["missingOrInvalidDataExcluded"], 0)

    def test_known_suspension_counts_elapsed_time_without_missing_data(self):
        data, settings, signal = fixture(length=10)
        day = data["calendar"][9]
        data["securities"][0]["tradeStatusByDate"][day] = False
        data["securities"][0]["bars"] = [r for r in data["securities"][0]["bars"] if r["date"] != day]
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["observation"]["availableTradingDays"], 10)
        self.assertEqual(event["observation"]["knownSuspensionDays"], 1)
        self.assertFalse(event["quality"]["dataMissing"])
        self.assertEqual(summarize_events([event], horizons=[10])["horizons"][0]["eligibleEntryCohort"], 1)

    def test_eod_volume_audit_does_not_rewrite_causal_open_exit(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.5] + [13.5] * 10)
        data["securities"][0]["bars"][10]["volume"] = 1
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["outcomes"]["nextOpenNet30"]["tradingDays"], 5)
        self.assertEqual(event["quality"]["executionCapacityIssues"][0]["tradingDay"], 5)
        summary = summarize_events([event], horizons=[3, 10])
        self.assertEqual(summary["horizons"][0]["eligibleEntryCohort"], 1)
        self.assertEqual(summary["horizons"][1]["eligibleEntryCohort"], 0)

    def test_opening_capacity_evidence_blocks_full_exit_and_entry_can_be_partial(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.5, 13.5])
        security = data["securities"][0]
        security["openingVolumeByDate"] = {data["calendar"][6]: 50000, data["calendar"][10]: 10000}
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["entry"]["quantity"], 5000)
        self.assertTrue(event["entry"]["partial"])
        self.assertTrue(event["entry"]["openingCapacityVerified"])
        self.assertEqual(event["outcomes"]["nextOpenNet30"]["tradingDays"], 6)
        self.assertEqual(event["quality"]["blockedExitReasons"]["openingCapacityBelowFullPosition"], 1)

    def test_missing_outcome_bounds_keep_excluded_complete_events_in_denominator(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.5] + [13.5] * 10)
        winner = self.evaluate(data, settings, signal)
        data["securities"][0]["bars"][10]["volume"] = 1
        unresolved = self.evaluate(data, settings, signal)
        row = summarize_events([winner, unresolved], horizons=[10])["horizons"][0]
        self.assertEqual(row["completeCalendarCohort"], 2)
        for proxy in row["proxies"].values():
            self.assertEqual(proxy["hitRate"], 1.)
            self.assertEqual(proxy["missingOutcomeBounds"]["denominator"], 2)
            self.assertEqual(proxy["missingOutcomeBounds"]["unresolved"], 1)
            self.assertEqual(proxy["missingOutcomeBounds"]["lower"], .5)
            self.assertEqual(proxy["missingOutcomeBounds"]["upper"], 1.)
            self.assertFalse(proxy["conservativeMissingOutcomeLowerBoundReachesThreshold"])

    def test_terminal_mark_is_not_forced_liquidation_or_certified_return(self):
        data, settings, signal = fixture(length=5)
        data["securities"][0]["bars"][7]["low"] = 7.5
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["terminal"]["status"], "rightCensoredAtDatasetEnd")
        self.assertFalse(event["terminal"]["hypotheticalLiquidationIsExecutable"])
        self.assertFalse(event["quality"]["executionVerified"])
        self.assertFalse(event["quality"]["corporateActionsVerified"])
        self.assertFalse(event["quality"]["historicalIdentityVerified"])
        self.assertFalse(event["observation"]["calendarIndependent"])
        summary = summarize_events([event], horizons=[5])
        self.assertFalse(summary["targetAchieved"])
        self.assertEqual(summary["excursions"]["atOrBelow"][2]["count"], 1)
        json.dumps(event, allow_nan=False)
        json.dumps(summary, allow_nan=False)

    def test_cutoff_signal_is_entry_censored_with_all_outcome_keys(self):
        data, settings, signal = fixture(length=5)
        signal["signalDate"] = data["calendar"][-1]
        event = self.evaluate(data, settings, signal)
        self.assertEqual(event["entry"]["status"], "censored")
        self.assertEqual(set(event["outcomes"]), {"rawHigh30", "netHigh30", "nextOpenNet30"})

    def test_explicit_cutoff_never_observes_future_hit_or_accepts_outside_signal(self):
        data, settings, signal = fixture([10., 10.8, 11.6, 12.5, 13.5, 13.5])
        settings.update(startDate=data["calendar"][5], endDate=data["calendar"][8])
        study = SignalTargetStudy(data, settings)
        event = study.evaluate(signal)
        self.assertEqual(event["observation"]["lastDate"], settings["endDate"])
        self.assertEqual(event["observation"]["availableTradingDays"], 3)
        self.assertIsNone(event["outcomes"]["nextOpenNet30"]["hitDate"])
        for day in (data["calendar"][4], data["calendar"][9]):
            with self.assertRaises(ValueError):
                study.evaluate({**signal, "signalDate": day})

    def test_invalid_numeric_options_fail_and_empty_summary_is_serializable(self):
        data, settings, signal = fixture()
        for budget in (True, float("inf"), float("nan"), 10 ** 1000):
            with self.assertRaises(ValueError):
                SignalTargetStudy(data, settings, budget=budget)
        with self.assertRaises(ValueError):
            summarize_events([], horizons=[5, 5])
        with self.assertRaises(ValueError):
            summarize_events([], precision_target=1.1)
        summary = summarize_events([])
        self.assertIsNone(summary["horizons"][0]["proxies"]["nextOpenNet30"]["hitRate"])
        json.dumps(summary, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
