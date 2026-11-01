"""Synthetic admission boundaries, temporal identity and schedule conservation."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from service.research_reclaim_breadth import MODES, STRATEGY_ID, filter_reclaim_breadth


def calendar(length=20):
    day, days = date(2024, 1, 2), []
    while len(days) < length:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def signal(day, symbol="stockA", rank=1):
    return {"signalId": f"{STRATEGY_ID}:{day}:{symbol}", "strategyId": STRATEGY_ID,
            "symbol": symbol, "signalDate": day, "originalRank": rank}


def context(days, at=8, today=.6, earlier=.4):
    change = 100 * (today - earlier)
    return {"formationDate": days[at], "asOfDate": days[at - 1] if at else None,
            "earlierDate": days[at - 6] if at >= 6 else None,
            "state": ("majority" if today > .5 else "notMajority") + ("Improving" if change > 0 else "NotImproving"),
            "status": "known", "reason": "classified", "pairedCount": 3,
            "positiveBreadthToday": today, "positiveBreadthEarlier": earlier,
            "matchedBreadthChange5": change, "pairedCoverageFixed": .001}


class ReclaimBreadthTests(unittest.TestCase):
    def test_all_four_combinations_and_strict_equalities(self):
        days = calendar()
        cases = ((.6, .4, (1, 1, 1, 1)), (.6, .6, (1, 1, 0, 0)),
                 (.5, .4, (1, 0, 1, 0)), (.5, .5, (1, 0, 0, 0)),
                 (.4, .6, (1, 0, 0, 0)))
        for today, earlier, expected in cases:
            with self.subTest(today=today, earlier=earlier):
                result = filter_reclaim_breadth([signal(days[8])], [context(days, today=today, earlier=earlier)], calendar=days)
                self.assertEqual(tuple(result["schedules"]), MODES)
                self.assertEqual(tuple(len(result["schedules"][m]) for m in MODES), expected)
                for mode, included in zip(MODES, expected):
                    self.assertEqual(result["counts"][mode], {"included": included, "excluded": 1-included, "unknown": 0})

    def test_missing_and_unknown_never_become_known_control(self):
        days = calendar()
        unknown = context(days)
        unknown.update(state="unknown", status="unknown", reason="matchedComparisonUnavailable")
        for rows, reason in (([], "missingContextRow"), ([unknown], "matchedComparisonUnavailable")):
            result = filter_reclaim_breadth([signal(days[8])], rows, calendar=days)
            for mode in MODES:
                self.assertEqual(result["schedules"][mode], [])
                self.assertEqual(result["counts"][mode], {"included": 0, "excluded": 0, "unknown": 1})
                item = result["evidence"][mode][0]
                self.assertEqual((item["state"], item["reason"], item["asOfDate"]), ("unknown", reason, days[7]))
        first = filter_reclaim_breadth([signal(days[0])], [], calendar=days)
        self.assertIsNone(first["evidence"][MODES[0]][0]["asOfDate"])

    def test_preserves_input_order_rank_gaps_and_repeat_setups(self):
        days = calendar()
        signals = [signal(days[9], "B", 4), signal(days[8], "A", 2), signal(days[9], "A", 1)]
        result = filter_reclaim_breadth(signals, [context(days, 8, .4, .6), context(days, 9)], calendar=days)
        self.assertEqual(result["schedules"]["knownContext"], signals)
        self.assertEqual(result["schedules"]["breadthBoth"], [signals[0], signals[2]])
        for mode in MODES:
            self.assertEqual([r["signalId"] for r in result["evidence"][mode]], [r["signalId"] for r in signals])
            self.assertEqual(sum(result["counts"][mode].values()), len(signals))

    def test_future_numeric_append_cannot_revise_past(self):
        days = calendar()
        original = context(days)
        prior = filter_reclaim_breadth([signal(days[8])], [original], calendar=days[:9])
        future = context(days, 12)
        future.update(positiveBreadthToday=float("nan"), pairedCount=-100, matchedBreadthChange5=12345)
        extended = filter_reclaim_breadth([signal(days[8])], [original, future], calendar=days)
        self.assertEqual(prior, extended)
        with self.assertRaises(ValueError):
            filter_reclaim_breadth([signal(days[12])], [original, future], calendar=days)

    def test_calendar_cutoff_retains_final_signal_and_rejects_outside(self):
        days = calendar()
        last = signal(days[-1])
        result = filter_reclaim_breadth([last], [context(days, len(days)-1)], calendar=days)
        self.assertTrue(all(result["schedules"][mode] == [last] for mode in MODES))
        with self.assertRaises(ValueError):
            filter_reclaim_breadth([last], [], calendar=days[:-1])
        with self.assertRaises(ValueError):
            filter_reclaim_breadth([], [context(days, len(days)-1)], calendar=days[:-1])

    def test_strict_dates_no_neighbor_or_current_day_substitution(self):
        days = calendar()
        base = context(days)
        for field, value in (("asOfDate", days[8]), ("asOfDate", days[6]),
                             ("earlierDate", days[1]), ("earlierDate", None)):
            changed = {**base, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                filter_reclaim_breadth([signal(days[8])], [changed], calendar=days)
        for bad_calendar in (days[::-1], days + [days[-1]], ["2024-1-2"]):
            with self.assertRaises(ValueError):
                filter_reclaim_breadth([], [], calendar=bad_calendar)

    def test_duplicate_or_changed_immediate_identity_rejected(self):
        days = calendar()
        sig, ctx = signal(days[8]), context(days)
        invalid = ([sig, sig], [sig, signal(days[8], "B", 1)],
                   [{**sig, "signalDate": days[10]}], [{**sig, "originalRank": 6}],
                   [{**sig, "formationDate": days[8]}])
        for signals in invalid:
            with self.assertRaises(ValueError):
                filter_reclaim_breadth(signals, [ctx], calendar=days)
        with self.assertRaises(ValueError):
            filter_reclaim_breadth([sig], [ctx, ctx], calendar=days)

    def test_known_state_and_same_population_arithmetic_rechecked(self):
        days = calendar()
        base = context(days)
        changes = (("state", "majorityNotImproving"), ("status", "unknown"),
                   ("pairedCount", 0), ("pairedCount", True),
                   ("positiveBreadthToday", float("inf")), ("positiveBreadthEarlier", -1),
                   ("matchedBreadthChange5", 3), ("matchedBreadthChange5", None))
        for field, value in changes:
            with self.subTest(field=field), self.assertRaises(ValueError):
                filter_reclaim_breadth([signal(days[8])], [{**base, field: value}], calendar=days)
        # No integer-positive-count inference or minimum-coverage gate is added.
        result = filter_reclaim_breadth([signal(days[8])], [base], calendar=days)
        self.assertEqual(result["counts"]["breadthBoth"]["included"], 1)

    def test_input_immutability_and_output_schedule_isolation(self):
        days = calendar()
        signals, rows = [signal(days[8])], [context(days)]
        before = deepcopy((signals, rows, days))
        result = filter_reclaim_breadth(signals, rows, calendar=days)
        self.assertEqual((signals, rows, days), before)
        result["schedules"]["knownContext"][0]["symbol"] = "changed"
        result["evidence"]["knownContext"][0]["symbol"] = "changed"
        self.assertEqual((signals, rows, days), before)
        self.assertEqual(result["schedules"]["breadthBoth"], signals)
        empty = filter_reclaim_breadth([], rows, calendar=days)
        self.assertEqual(empty["inputSignalCount"], 0)
        self.assertTrue(all(empty["counts"][mode] == {"included": 0, "excluded": 0, "unknown": 0} for mode in MODES))


if __name__ == "__main__":
    unittest.main()
