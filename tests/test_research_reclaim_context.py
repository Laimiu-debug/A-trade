"""Synthetic date boundaries and descriptive-account attribution contracts."""
from copy import deepcopy
from datetime import date, timedelta
import math
import unittest

from service.research_reclaim_context import STATES, LABELS, build_context_rows, summarize_records


def calendar(length=15):
    days, current = [], date(2024, 1, 2)
    while len(days) < length:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def market(days, at=7, today=.6, earlier=.4):
    return {"date": days[at], "fixedPopulation": 100,
            "price20": {"positiveReturn20Breadth": .1, "positiveCount": 10, "evaluableCount": 100},
            "matched5": {"available": True, "earlierDate": days[at - 5] if at >= 5 else None,
                "todayEvaluableCount": 80, "earlierEvaluableCount": 60, "pairedCount": 40,
                "positiveBreadthToday": today, "positiveBreadthEarlier": earlier,
                "matchedBreadthChange5": 100 * (today - earlier),
                "pairedCoverageToday": .5, "pairedCoverageEarlier": 40 / 60,
                "pairedCoverageFixed": .4}}


def record(ident="a", *, period="p1", policy="immediate", formation="2024-01-10", state=STATES[0],
           status="closed", pnl=100., return_pct=10., signal=None, symbol="stockA", **changes):
    selected = status != "notScheduled"
    closed, opened = status == "closed", status == "open"
    row = {"period": period, "policy": policy, "formationId": ident, "formationDate": formation,
           "symbol": symbol, "state": state, "signalDate": (signal or formation) if selected else None,
           "entered": closed or opened, "closed": closed, "open": opened,
           "closedReturnPct": return_pct if closed else None, "closedPnlAmount": pnl if closed else 0.,
           "openUnrealizedPnlAmount": pnl if opened else 0., "openRealizedPnlAmount": 0.,
           "openMarkStale": False, "holdingSessions": 5 if closed or opened else None,
           "reason": status}
    row.update(changes)
    return row


def group(result, *, period="p1", policy="immediate", state=STATES[0], year=None):
    key = "overallRows" if year is None else "formationYearRows"
    return next(row for row in result[key] if row["period"] == period and row["policy"] == policy
                and row["state"] == state and (year is None or row["formationYear"] == year))


class ReclaimContextTests(unittest.TestCase):
    def test_four_states_and_exact_equal_boundaries(self):
        days = calendar()
        cases = ((.6, .4, STATES[0]), (.6, .7, STATES[1]), (.5, .4, STATES[2]),
                 (.5, .5, STATES[3]), (.6, .6, STATES[1]), (.4, .5, STATES[3]))
        for today, earlier, expected in cases:
            with self.subTest(today=today, earlier=earlier):
                row = build_context_rows(days, [market(days, today=today, earlier=earlier)], [days[8]])[0]
                self.assertEqual((row["state"], row["status"], row["reason"]), (expected, "known", "classified"))
                self.assertEqual((row["formationDate"], row["asOfDate"], row["earlierDate"]),
                                 (days[8], days[7], days[2]))
        self.assertEqual(tuple(LABELS), STATES)

    def test_same_paired_population_and_no_coverage_threshold(self):
        days = calendar()
        source = market(days)
        # Current full price population says minority; matched population says majority.
        source["fixedPopulation"] = 4000
        source["matched5"]["pairedCoverageFixed"] = .01
        row = build_context_rows(days, [source], [days[8]])[0]
        self.assertEqual(row["state"], "majorityImproving")
        self.assertEqual(row["pairedCount"], 40)
        self.assertEqual(row["pairedCoverageFixed"], .01)
        for key in ("pairedCoverageToday", "pairedCoverageEarlier", "pairedCoverageFixed"):
            source["matched5"].pop(key)
        row = build_context_rows(days, [source], [days[8]])[0]
        self.assertEqual(row["status"], "known")
        self.assertIsNone(row["pairedCoverageToday"])

    def test_exact_calendar_clock_no_current_or_neighbor_substitution(self):
        days = calendar()
        row = build_context_rows(days, [market(days, at=6), market(days, at=8)], [days[8]])[0]
        self.assertEqual(row["reason"], "marketRowUnavailable")
        self.assertEqual(row["asOfDate"], days[7])
        changed = market(days)
        changed["matched5"]["earlierDate"] = days[1]
        with self.assertRaises(ValueError):
            build_context_rows(days, [changed], [days[8]])

    def test_calendar_and_panel_warmup_stay_unknown(self):
        days = calendar()
        rows = build_context_rows(days, [], [days[0], days[1]])
        self.assertEqual(rows[0]["reason"], "asOfSessionUnavailable")
        self.assertIsNone(rows[0]["asOfDate"])
        source = market(days, at=3)
        source["matched5"].update(available=False, earlierDate=None)
        row = build_context_rows(days, [source], [days[4]])[0]
        self.assertEqual(row["reason"], "earlierSessionUnavailable")
        # Source calendar has warmup, but the stored panel's early matched row does not.
        source = market(days)
        source["matched5"].update(available=False, earlierDate=None)
        row = build_context_rows(days, [source], [days[8]])[0]
        self.assertEqual(row["reason"], "matchedComparisonUnavailable")
        self.assertEqual(row["earlierDate"], days[2])

    def test_missing_nonfinite_and_empty_required_evidence_are_unknown(self):
        days = calendar()
        cases = ({"available": False}, {"pairedCount": 0}, {"pairedCount": None}, {"earlierDate": None})
        for change in cases:
            source = market(days)
            source["matched5"].update(change)
            with self.subTest(change=change):
                self.assertEqual(build_context_rows(days, [source], [days[8]])[0]["state"], "unknown")
        for field in ("positiveBreadthToday", "positiveBreadthEarlier", "matchedBreadthChange5"):
            for value in (None, math.nan, math.inf, True, "0.5"):
                source = market(days)
                source["matched5"][field] = value
                with self.subTest(field=field, value=value):
                    self.assertEqual(build_context_rows(days, [source], [days[8]])[0]["reason"], "matchedValuesUnavailable")

    def test_known_contradictions_rejected_even_when_comparison_unavailable(self):
        days = calendar()
        cases = ({"positiveBreadthToday": 1.1}, {"positiveBreadthEarlier": -.1},
                 {"matchedBreadthChange5": 101}, {"matchedBreadthChange5": 3},
                 {"pairedCoverageFixed": -1}, {"pairedCount": 101},
                 {"pairedCount": 70}, {"pairedCount": -1}, {"pairedCount": 2.5})
        for change in cases:
            source = market(days)
            source["matched5"].update(available=False, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_context_rows(days, [source], [days[8]])
        source = market(days)
        source["matched5"]["matchedBreadthChange5"] += .5e-9
        self.assertEqual(build_context_rows(days, [source], [days[8]])[0]["state"], STATES[0])
        source["matched5"]["matchedBreadthChange5"] += 2e-9
        with self.assertRaises(ValueError):
            build_context_rows(days, [source], [days[8]])

    def test_date_identity_errors_rejected_and_formation_order_is_sorted(self):
        days = calendar()
        source = market(days)
        for calendar_arg, market_arg, formations in (
            (days + [days[-1]], [source], [days[8]]),
            (days, [source, deepcopy(source)], [days[8]]),
            (days, [{**source, "date": "2099-01-01"}], [days[8]]),
            (days, [source], [days[8], days[8]]),
            (days, [source], ["2099-01-01"]),
        ):
            with self.assertRaises(ValueError):
                build_context_rows(calendar_arg, market_arg, formations)
        rows = build_context_rows(days, [market(days, at=9), source], [days[10], days[8]])
        self.assertEqual([r["formationDate"] for r in rows], [days[8], days[10]])

    def test_future_market_values_are_unread_and_inputs_immutable(self):
        days = calendar()
        source = market(days)
        saved = deepcopy((days, source))
        before = build_context_rows(days[:9], [source], [days[8]])
        future = {"date": days[11], "matched5": {"positiveBreadthToday": 999, "earlierDate": "2099-01-01"}}
        after = build_context_rows(days, [source, future], [days[8]])
        self.assertEqual(before, after)
        after[0]["pairedCount"] = 999
        self.assertEqual((days, source), saved)

    def test_mixed_closed_open_and_unfilled_amount_bridge(self):
        rows = [record("win"), record("loss", pnl=-40, return_pct=-4),
                record("zero", pnl=0, return_pct=0, formation="2024-01-11"),
                record("open", status="open", pnl=20, openRealizedPnlAmount=5, openMarkStale=True,
                       formation="2024-01-12", symbol="stockB"),
                record("unfilled", status="notEntered", formation="2024-01-15"),
                record("no-signal", status="notScheduled", formation="2024-01-15")]
        observed = group(summarize_records(rows))
        self.assertEqual([observed[k] for k in ("formationCount", "selectedSignals", "entered", "closed", "open",
                                               "notScheduled", "notEntered", "uniqueFormationDays", "uniqueSymbols")],
                         [6, 5, 4, 3, 1, 1, 1, 4, 2])
        self.assertEqual(observed["closedMeanPct"], 2)
        self.assertAlmostEqual(observed["winRatePct"], 100 / 3)
        self.assertEqual(observed["meanWinPct"], 10)
        self.assertEqual(observed["meanLossPct"], -4)
        self.assertEqual(observed["profitFactor"], 2.5)
        self.assertEqual(observed["minClosedPct"], -4)
        self.assertEqual(observed["closedPnlAmount"], 60)
        self.assertEqual(observed["openUnrealizedPnlAmount"], 20)
        self.assertEqual(observed["openRealizedPnlAmount"], 5)
        self.assertEqual(observed["totalPnlAmount"], 85)
        self.assertAlmostEqual(observed["contributionPpOfInitialCapital"], .017)
        self.assertNotIn("accountReturnPct", observed)
        self.assertNotIn("maxDrawdownPct", observed)

    def test_top5_is_cash_profit_concentration_and_no_losses_ratio_null(self):
        rows = [record(str(i), pnl=pnl, return_pct=i + 1) for i, pnl in enumerate((60, 50, 40, 30, 20, 10))]
        observed = group(summarize_records(rows))
        self.assertAlmostEqual(observed["top5ShareOfGrossProfitPct"], 200 / 210 * 100)
        self.assertIsNone(observed["profitFactor"])
        self.assertIsNone(observed["meanLossPct"])
        self.assertEqual(observed["winRatePct"], 100)
        only_loss = group(summarize_records([record(pnl=-10, return_pct=-1)]))
        self.assertEqual(only_loss["profitFactor"], 0)
        self.assertIsNone(only_loss["top5ShareOfGrossProfitPct"])

    def test_full_observed_domains_include_empty_policy_state_year_combinations(self):
        rows = [record("a", period="old", policy="immediate", formation="2022-12-30"),
                record("b", period="old", policy="immediate", formation="2023-01-03"),
                record("c", period="new", policy="confirm2", formation="2024-01-03")]
        result = summarize_records(rows)
        self.assertEqual(len(result["overallRows"]), 2 * 2 * 5)
        self.assertEqual(len(result["formationYearRows"]), 3 * 2 * 5)
        empty = group(result, period="old", policy="confirm2", state="unknown")
        self.assertEqual(empty["formationCount"], 0)
        self.assertEqual(empty["totalPnlAmount"], 0)
        self.assertEqual(empty["contributionPpOfInitialCapital"], 0)
        for key in ("closedMeanPct", "winRatePct", "meanWinPct", "meanLossPct", "profitFactor",
                    "minClosedPct", "top5ShareOfGrossProfitPct"):
            self.assertIsNone(empty[key])
        self.assertEqual([r["state"] for r in result["overallRows"][:5]], list(STATES))
        self.assertEqual(summarize_records([]), {"overallRows": [], "formationYearRows": []})

    def test_formation_year_and_relabelled_epochs_do_not_follow_signal_date(self):
        rows = [record("year-boundary", formation="2023-12-29", signal="2024-01-03"),
                record("pre", formation="2024-09-23"), record("post", formation="2024-09-24")]
        observed = summarize_records(rows)
        self.assertEqual(group(observed, year=2023)["formationCount"], 1)
        self.assertEqual(group(observed, year=2024)["formationCount"], 2)
        relabelled = deepcopy(rows)
        for row in relabelled:
            row["period"] = ("old" if row["formationDate"] < "2024-01-01" else
                             "pre20240924" if row["formationDate"] < "2024-09-24" else "post20240924")
        epoch = summarize_records(relabelled)
        self.assertEqual(len(epoch["overallRows"]), 3 * 1 * 5)
        self.assertEqual(sum(r["totalPnlAmount"] for r in epoch["overallRows"]),
                         sum(r["totalPnlAmount"] for r in observed["overallRows"]))
        self.assertTrue(all(r["state"] == STATES[0] for r in relabelled))

    def test_group_amounts_add_and_input_order_does_not_change_statistics(self):
        rows = [record(str(i), state=state, pnl=(i - 2) * 50, return_pct=i - 2,
                       symbol="same-stock", formation="2024-01-10") for i, state in enumerate(STATES)]
        saved = deepcopy(rows)
        result = summarize_records(rows, initial_capital=1000)
        self.assertEqual(result, summarize_records(rows[::-1], initial_capital=1000))
        self.assertEqual(sum(r["totalPnlAmount"] for r in result["overallRows"]), 0)
        self.assertEqual(rows, saved)
        self.assertEqual(group(result, state=STATES[4])["contributionPpOfInitialCapital"], 10)

    def test_duplicate_or_contradictory_episode_records_are_rejected(self):
        base = record()
        cases = ([base, deepcopy(base)], [base, {**base, "policy": "delay2", "state": "unknown"}],
                 [{**base, "entered": False}], [{**base, "open": True}],
                 [{**base, "closedReturnPct": None}], [{**base, "closedPnlAmount": math.nan}],
                 [{**base, "signalDate": None}], [{**base, "signalDate": "2023-01-01"}],
                 [{**base, "openRealizedPnlAmount": 1}], [{**base, "openMarkStale": True}],
                 [{**record(status="notEntered"), "closedReturnPct": 0}])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                summarize_records(rows)
        for initial in (0, -1, math.inf, True):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                summarize_records([], initial_capital=initial)


if __name__ == "__main__":
    unittest.main()
