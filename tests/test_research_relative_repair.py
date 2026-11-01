"""Synthetic event-clock checks only; no market files, returns or accounts."""
from copy import deepcopy
from datetime import date, timedelta
import json
import unittest

from service.research_relative_repair import build_relative_repair, STRATEGY_ID


def fixture(count=50, sessions=150):
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(sessions)]
    benchmark = [{"date": d, "close": 100 if i < 80 else (95 if i == 80 else 96)}
                 for i, d in enumerate(days)]
    securities = []
    for s in range(count):
        anchor = 100 + s
        ret = 1 - s / 100 if s < (count + 4) // 5 else .2 - s / 1000
        bars = [{"date": d, "close": anchor / (1 + ret) if i < 79 else (anchor if i == 79 else anchor * .9),
                 "volume": 100, "amount": (count - s) * 1000} for i, d in enumerate(days)]
        securities.append({"symbol": f"s{s:03}", "bars": bars})
    return {"calendar": days, "benchmark": {"symbol": "sh000300", "bars": benchmark},
            "securities": securities}


def set_bar(data, symbol, index, **fields):
    day = data["calendar"][index]
    source = data["benchmark"] if symbol is None else data["securities"][symbol]
    next(row for row in source["bars"] if row["date"] == day).update(fields)


def remove_bar(data, symbol, index):
    day = data["calendar"][index]
    source = data["benchmark"] if symbol is None else data["securities"][symbol]
    source["bars"] = [r for r in source["bars"] if r["date"] != day]


def run(data, start=80, end=120, **kwargs):
    return build_relative_repair(data, start=data["calendar"][start], end=data["calendar"][end], **kwargs)


class RelativeRepairTests(unittest.TestCase):
    def test_trigger_freeze_latest_peak_and_equal_price_first_cross(self):
        data = fixture()
        set_bar(data, 0, 81, close=100)
        out = run(data)
        event = out["events"][0]
        self.assertEqual(event["peakDate"], data["calendar"][79])
        self.assertEqual(event["triggerDate"], data["calendar"][80])
        self.assertEqual(event["candidates"][0]["return60"], 1)
        self.assertEqual(event["population"]["validPopulationCount"], 50)
        self.assertEqual(event["population"]["top20Count"], 10)
        self.assertEqual(event["candidates"][0]["return60Percentile"], 49.5 / 50)
        self.assertEqual(out["dailyEvidence"][0]["firstCrosses"], [])
        for group in ("allRepaired", "repaired", "matchedControl"):
            signals = out["schedules"][group]
            self.assertEqual(len(signals), 1)
            self.assertEqual(set(signals[0]), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})
            self.assertEqual(signals[0]["strategyId"], STRATEGY_ID)
            self.assertEqual(signals[0]["signalDate"], data["calendar"][81])
        self.assertEqual(out["schedules"]["matchedControl"][0]["symbol"], "s001")

    def test_trigger_at_or_above_anchor_is_not_retroactive_cross(self):
        data = fixture()
        for i in (80, 81):
            set_bar(data, 0, i, close=100)
        set_bar(data, 0, 83, close=100)
        out = run(data)
        self.assertEqual([s["signalDate"] for s in out["schedules"]["allRepaired"]], [data["calendar"][83]])

    def test_first_cross_all_seen_before_top5_and_control_can_later_repair(self):
        data = fixture(60)
        for s in range(6):
            set_bar(data, s, 81, close=100 + s)
        set_bar(data, 5, 83, close=105)  # Rank 6 was consumed, never deferred.
        set_bar(data, 6, 82, close=106)  # Prior day's control is allowed to repair.
        out = run(data)
        first_day = out["dailyEvidence"][1]
        self.assertEqual(len(first_day["firstCrosses"]), 6)
        self.assertEqual([s["symbol"] for s in first_day["selectedTop5"]], [f"s{s:03}" for s in range(5)])
        self.assertEqual(len(first_day["pairs"]), 5)
        self.assertEqual(len({p["controlSymbol"] for p in first_day["pairs"]}), 5)
        self.assertEqual([s["symbol"] for s in out["schedules"]["allRepaired"]],
                         [f"s{s:03}" for s in range(5)] + ["s006"])
        self.assertEqual([s["symbol"] for s in out["schedules"]["matchedControl"]][-1], "s011")

    def test_top5_then_matching_no_backfill_and_complete_group_retained(self):
        data = fixture(35)  # Seven candidates; six repair and one control.
        for s in range(6):
            set_bar(data, s, 81, close=100 + s)
        out = run(data)
        self.assertEqual(len(out["schedules"]["allRepaired"]), 5)
        self.assertEqual(len(out["schedules"]["repaired"]), 1)
        self.assertEqual(len(out["schedules"]["matchedControl"]), 1)
        self.assertEqual(out["schedules"]["repaired"][0]["originalRank"], 1)
        self.assertEqual(len(out["dailyEvidence"][1]["unmatched"]), 4)
        self.assertEqual(out["events"][0]["seenSymbols"], [f"s{s:03}" for s in range(6)])

    def test_zero_unknown_and_negative_volume_crosses_are_consumed(self):
        data = fixture()
        for s, volume in ((0, 0), (1, None), (2, -1), (3, 100)):
            set_bar(data, s, 81, close=100 + s, volume=volume)
            set_bar(data, s, 83, close=100 + s)
        out = run(data)
        self.assertEqual([s["symbol"] for s in out["schedules"]["allRepaired"]], ["s003"])
        self.assertEqual(out["schedules"]["allRepaired"][0]["originalRank"], 1)
        self.assertEqual([r["status"] for r in out["dailyEvidence"][1]["firstCrosses"]],
                         ["excluded", "unknown", "excluded", "eligibleFirstCross"])
        self.assertEqual(out["events"][0]["seenSymbols"], ["s000", "s001", "s002", "s003"])

    def test_missing_close_from_trigger_or_later_permanently_ends_stock(self):
        data = fixture()
        remove_bar(data, 0, 80)
        remove_bar(data, 1, 81)
        set_bar(data, 2, 81, close=float("nan"))
        for s in (0, 1, 2):
            set_bar(data, s, 83, close=100 + s)
        out = run(data)
        self.assertEqual(out["schedules"]["allRepaired"], [])
        self.assertEqual(out["events"][0]["unknownSymbols"],
                         {"s000": data["calendar"][80], "s001": data["calendar"][81], "s002": data["calendar"][81]})
        json.dumps(out, allow_nan=False)

    def test_index_missing_ends_unknown_requires_complete_strict_high_rearm(self):
        data = fixture(sessions=170)
        remove_bar(data, None, 81)
        set_bar(data, 0, 81, close=100)
        for i in range(82, 103):
            set_bar(data, None, i, close=100)
        set_bar(data, None, 103, close=101)
        set_bar(data, None, 104, close=95)
        out = run(data, end=106)
        self.assertEqual(out["events"][0]["endReason"], "benchmarkCloseUnavailable")
        self.assertEqual(out["events"][0]["observedSessions"], 1)
        self.assertEqual(out["dailyEvidence"][1]["firstCrosses"], [])
        self.assertNotIn("rearmed", out["dailyEvidence"][102 - 80])  # Equality is insufficient.
        self.assertTrue(out["dailyEvidence"][103 - 80]["rearmed"])
        self.assertEqual([e["triggerDate"] for e in out["events"]],
                         [data["calendar"][80], data["calendar"][104]])

    def test_recovery_precedes_stock_signals_and_same_day_strict_rearm(self):
        data = fixture()
        set_bar(data, None, 81, close=101)
        set_bar(data, 0, 81, close=100)
        set_bar(data, None, 82, close=95)
        out = run(data, end=83)
        self.assertEqual(out["events"][0]["endReason"], "benchmarkRecovered")
        self.assertTrue(out["dailyEvidence"][1]["rearmed"])
        self.assertEqual(len(out["events"]), 2)
        self.assertEqual(out["events"][1]["triggerDate"], data["calendar"][82])
        self.assertEqual(out["schedules"]["allRepaired"], [])

    def test_equal_recovery_terminates_but_does_not_rearm(self):
        data = fixture()
        set_bar(data, None, 81, close=100)
        out = run(data, end=83)
        self.assertEqual(out["events"][0]["endReason"], "benchmarkRecovered")
        self.assertFalse(out["dailyEvidence"][1]["armedAfter"])
        self.assertEqual(len(out["events"]), 1)

    def test_day40_inclusive_then_expire_and_cutoff_censor(self):
        data = fixture()
        set_bar(data, 0, 120, close=100)
        set_bar(data, 1, 121, close=101)
        out = run(data, end=125)
        self.assertEqual([s["signalDate"] for s in out["schedules"]["allRepaired"]], [data["calendar"][120]])
        self.assertEqual(out["events"][0]["endReason"], "observationWindowExpired")
        self.assertEqual(out["events"][0]["observedSessions"], 40)
        self.assertFalse(out["events"][0]["censored"])
        cut = run(data, end=100)
        self.assertEqual(cut["events"][0]["endReason"], "periodCutoff")
        self.assertTrue(cut["events"][0]["censored"])
        self.assertEqual(cut["events"][0]["observedSessions"], 20)

    def test_amount_window_includes_peak_features_frozen_and_rank_ties(self):
        data = fixture(10)
        # Equal returns/amounts use equal percentiles and stable symbol rank.
        a, b = data["securities"][:2]
        b["bars"] = deepcopy(a["bars"])
        set_bar(data, 0, 79, amount=21000)
        set_bar(data, 1, 79, amount=21000)
        set_bar(data, 0, 81, close=100, amount=1)
        set_bar(data, 1, 81, amount=1e12)
        out = run(data)
        frozen = out["events"][0]["candidates"]
        self.assertEqual([s["symbol"] for s in frozen], ["s000", "s001"])
        self.assertEqual(frozen[0]["amount20"], (19 * 10000 + 21000) / 20)
        self.assertEqual(frozen[0]["return60Percentile"], frozen[1]["return60Percentile"])
        self.assertEqual(frozen[0]["amount20Percentile"], frozen[1]["amount20Percentile"])
        self.assertEqual(out["dailyEvidence"][1]["pairs"][0]["distance"], 0)

    def test_full_valid_denominator_positive_filter_and_exact61_slots(self):
        data = fixture(11)
        remove_bar(data, 0, 19)  # p-60 is required, no observed-row compression.
        set_bar(data, 1, 60, amount=0)  # p-19, part of inclusive amount20.
        set_bar(data, 2, 59, amount=0)  # p-20 is irrelevant.
        out = run(data)
        event = out["events"][0]
        self.assertEqual(event["population"]["validPopulationCount"], 9)
        self.assertEqual(event["population"]["top20Count"], 2)
        self.assertEqual([r["symbol"] for r in event["candidates"]], ["s002", "s003"])
        negative = fixture(5)
        for sec in negative["securities"]:
            sec["bars"][19]["close"] = 1000
        self.assertEqual(run(negative)["events"][0]["candidates"], [])

    def test_empty_pool_short_history_and_empty_target(self):
        data = fixture(0)
        out = run(data)
        self.assertEqual(len(out["events"]), 1)
        self.assertEqual(out["schedules"], {"allRepaired": [], "repaired": [], "matchedControl": []})
        self.assertEqual(out["events"][0]["endReason"], "observationWindowExpired")
        short = fixture(1)
        for i in range(80):
            set_bar(short, None, i, close=100 if i < 19 else 90)
        out = run(short, start=19, end=20)
        self.assertEqual(out["dailyEvidence"][0]["status"], "unknown")
        self.assertEqual(out["events"][0]["candidates"], [])
        self.assertEqual(out["events"][0]["population"]["invalidPopulation"][0]["reasons"],
                         ["insufficient61CalendarSlots", "missingOrInvalidAmount20"])
        empty = build_relative_repair(data, start="2021-01-01", end="2021-01-10")
        self.assertEqual(empty["events"], [])

    def test_no_mutation_future_prefix_and_period_reset(self):
        data = fixture()
        set_bar(data, 0, 81, close=100)
        original = deepcopy(data)
        progress = []
        out = run(data, end=90, progress=progress.append)
        self.assertEqual(data, original)
        self.assertEqual(len(progress), 11)
        future = deepcopy(data)
        for source in [future["benchmark"], *future["securities"]]:
            for bar in source["bars"]:
                if bar["date"] > data["calendar"][90]:
                    bar.update(close=1e10, volume=1e10, amount=1e10)
        self.assertEqual(out, run(future, end=90))
        short = deepcopy(data)
        short["calendar"] = short["calendar"][:91]
        for source in [short["benchmark"], *short["securities"]]:
            source["bars"] = source["bars"][:91]
        self.assertEqual(out, run(short, end=90, progress=lambda _: None))
        longer = run(data, end=110)
        self.assertEqual(out["dailyEvidence"], longer["dailyEvidence"][:11])
        self.assertEqual(out["schedules"], longer["schedules"])
        # Starting after the drawdown does not inherit the earlier active event.
        reset = run(data, start=81, end=90)
        self.assertEqual(reset["events"], [])

    def test_bad_input_hard_fails_and_nonfinite_evidence_json_safe(self):
        data = fixture()
        for mutate in (lambda d: d["calendar"].append(d["calendar"][-1]),
                       lambda d: d["securities"].append(deepcopy(d["securities"][0])),
                       lambda d: d["benchmark"]["bars"].append(deepcopy(d["benchmark"]["bars"][0])),
                       lambda d: d["benchmark"].update(symbol="other")):
            bad = deepcopy(data)
            mutate(bad)
            with self.assertRaises(ValueError):
                run(bad)
        set_bar(data, 0, 60, amount=float("inf"))
        set_bar(data, 1, 81, volume=float("nan"), close=101)
        json.dumps(run(data), allow_nan=False)

    def test_completed_cross_is_not_reclassified_by_later_missing_price(self):
        data = fixture()
        set_bar(data, 0, 81, close=100)
        remove_bar(data, 0, 82)
        out = run(data)
        self.assertNotIn("s000", out["events"][0]["unknownSymbols"])
        self.assertEqual(out["dailyEvidence"][2]["observations"][0]["crossReason"], "firstCrossAlreadySeen")

    def test_matching_equal_distance_uses_symbol_and_stable_greedy_order(self):
        data = fixture()
        common = deepcopy(data["securities"][0]["bars"])
        for sec in data["securities"]:
            sec["bars"] = deepcopy(common)
        set_bar(data, 0, 81, close=100)
        set_bar(data, 1, 81, close=100)
        out = run(data)
        pairs = out["dailyEvidence"][1]["pairs"]
        self.assertEqual([(p["repairedSymbol"], p["controlSymbol"], p["distance"])
                          for p in pairs], [("s000", "s002", 0), ("s001", "s003", 0)])
        self.assertEqual([r["return60Percentile"] for r in out["events"][0]["candidates"]], [.5] * 10)

    def test_expiry_can_rearm_after_last_allowed_signals_but_not_trigger_same_day(self):
        data = fixture()
        set_bar(data, None, 119, close=97)
        set_bar(data, None, 120, close=98)
        set_bar(data, None, 121, close=90)
        set_bar(data, 0, 120, close=100)
        out = run(data, end=122)
        last = out["dailyEvidence"][40]
        self.assertEqual(last["endReason"], "observationWindowExpired")
        self.assertTrue(last["rearmed"])
        self.assertEqual(out["schedules"]["allRepaired"][0]["signalDate"], data["calendar"][120])
        self.assertEqual(out["events"][1]["triggerDate"], data["calendar"][121])
        remove_bar(data, None, 120)
        unknown = run(data, end=122)
        self.assertEqual(unknown["events"][0]["endReason"], "benchmarkCloseUnavailable")
        self.assertEqual(unknown["schedules"]["allRepaired"], [])


if __name__ == "__main__":
    unittest.main()
