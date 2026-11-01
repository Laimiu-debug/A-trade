"""Synthetic causal session decomposition and matched-control contracts.

No real source, market features, outcomes or external services are read.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Inexact, ROUND_DOWN, localcontext
import json
import math
from statistics import mean
import unittest

from service.signal_session_decomposition import (
    CANDIDATE_ID, CONTROL_ID, evaluate_session_path, select_session_pairs,
)


def dates(count=21):
    result, day = [], date(2023, 12, 11)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


DATES = dates()
ELIGIBILITY = {"eligible": True, "reason": "eligible", "unknown": ["historicalSt", "historicalMembership"]}


def path(intraday=None, overnight=None, *, initial=10., amount=80_000_000.):
    intraday = [.01] * 20 if intraday is None else list(intraday)
    overnight = [0.] * 20 if overnight is None else list(overnight)
    assert len(intraday) == len(overnight) == 20
    result = [{"date": DATES[0], "open": initial, "high": initial, "low": initial,
               "close": initial, "volume": 100., "amount": amount,
               "priceBasisId": "raw-unadjusted"}]
    for index, (inside, outside) in enumerate(zip(intraday, overnight), 1):
        opening = result[-1]["close"] * math.exp(outside)
        closing = opening * math.exp(inside)
        result.append({"date": DATES[index], "open": opening, "high": max(opening, closing),
                       "low": min(opening, closing), "close": closing, "volume": 100.,
                       "amount": amount + index, "priceBasisId": "raw-unadjusted"})
    return result


def close_path(closes, *, overnight=False, amount=80_000_000.):
    result = path(amount=amount)
    assert len(closes) == 21
    for index, close in enumerate(closes):
        opening = close if overnight or index == 0 else closes[index-1]
        result[index].update(open=opening, close=close, high=max(opening, close), low=min(opening, close))
    return result


def evaluate(raw=None, calendar=None, eligibility=None, *, symbol="sh600001", suspended=False):
    return evaluate_session_path(symbol, DATES[-1], DATES if calendar is None else calendar,
                                 path() if raw is None else raw,
                                 ELIGIBILITY if eligibility is None else eligibility,
                                 known_suspended=suspended)


def independent_values(raw):
    close_steps = [math.log(raw[i]["close"] / raw[i-1]["close"]) for i in range(1, 21)]
    intraday_steps = [math.log(b["close"] / b["open"]) for b in raw[1:]]
    overnight_steps = [math.log(raw[i]["open"] / raw[i-1]["close"]) for i in range(1, 21)]
    peak, drawdowns = raw[0]["close"], []
    for bar in raw:
        peak = max(peak, bar["close"])
        drawdowns.append((peak-bar["close"])/peak)
    total = math.log(raw[-1]["close"] / raw[0]["close"])
    absolute = math.fsum(abs(v) for v in close_steps)
    return {"total": total, "intraday": math.fsum(intraday_steps), "overnight": math.fsum(overnight_steps),
            "efficiency": total / absolute if absolute else None, "drawdown": max(drawdowns),
            "intradayPositive": sum(b["close"] > b["open"] for b in raw[1:]),
            "overnightPositive": sum(raw[i]["open"] > raw[i-1]["close"] for i in range(1, 21)),
            "amount": mean(b["amount"] for b in raw[:-1])}


def classified(symbol, kind="intraday", *, total=.2, drawdown=0., efficiency=1., amount=80_000_000.):
    """A valid feature-level contract for selector-only, non-raw tests."""
    raw = path([.01 if kind == "intraday" else 0.] * 20,
               [.01 if kind == "overnight" else 0.] * 20)
    signal = evaluate(raw, symbol=symbol)["signal"]
    signal["prior20MeanAmount"] = amount
    signal["features"].update(prior20MeanAmount=amount, totalLogReturn20=total,
        endClose=signal["features"]["startClose"] * math.exp(total),
        simpleReturn20=math.expm1(total), closeMaxDrawdown20=drawdown,
        signedEfficiency20=efficiency, intradayLogReturn20=total if kind == "intraday" else 0.,
        overnightLogReturn20=total if kind == "overnight" else 0.,
        intradaySignedShare=1. if kind == "intraday" else 0.,
        overnightSignedShare=1. if kind == "overnight" else 0., decompositionResidual=0.)
    return signal


class SessionDecompositionTests(unittest.TestCase):
    def test_independent_twenty_step_decomposition_both_classes_and_quality(self):
        for kind in ("intraday", "overnight"):
            with self.subTest(kind=kind):
                raw = path([.01 if kind == "intraday" else 0.] * 20,
                           [.01 if kind == "overnight" else 0.] * 20)
                actual, expected = evaluate(raw), independent_values(raw)
                self.assertEqual(actual["kind"], kind)
                f = actual["features"]
                for key, reference in (("totalLogReturn20", "total"), ("intradayLogReturn20", "intraday"),
                        ("overnightLogReturn20", "overnight"), ("signedEfficiency20", "efficiency"),
                        ("closeMaxDrawdown20", "drawdown"), ("prior20MeanAmount", "amount")):
                    self.assertAlmostEqual(f[key], expected[reference], places=13)
                self.assertEqual(f["positiveIntradaySessions"], expected["intradayPositive"])
                self.assertEqual(f["positiveOvernightSessions"], expected["overnightPositive"])
                self.assertEqual((f["historyStart"], f["returnStart"], f["historyEnd"], f["returnSessions"]),
                                 (DATES[0], DATES[1], DATES[-1], 20))
                self.assertLess(abs(f["decompositionResidual"]), 1e-13)
                self.assertEqual(actual["signal"]["strategyId"], CANDIDATE_ID if kind == "intraday" else CONTROL_ID)
                for name in ("certified", "targetAchieved", "historicalEligibilityVerified", "comparableRightsVerified"):
                    self.assertIs(actual["quality"][name], False)
                self.assertEqual(actual["quality"]["unknownHistoricalFields"], ELIGIBILITY["unknown"])
                self.assertEqual(actual["signal"]["quality"]["actionEvidence"], "unverified")
                json.dumps(actual, allow_nan=False)

    def test_first_open_and_current_amount_excluded_but_first_close_included(self):
        raw = path()
        baseline = evaluate(raw)
        raw[0].update(open=1., low=1.)
        raw[-1]["amount"] = float("nan")
        self.assertEqual(evaluate(raw), baseline)
        self.assertEqual(baseline["features"]["priorAmountEnd"], DATES[-2])
        raw[0]["amount"] += 20_000
        self.assertEqual(evaluate(raw)["features"]["prior20MeanAmount"], baseline["features"]["prior20MeanAmount"] + 1000)
        raw[0].update(close=9., high=9.)
        self.assertNotEqual(evaluate(raw)["features"]["totalLogReturn20"], baseline["features"]["totalLogReturn20"])

    def test_ten_percent_decimal_boundary_and_ambient_context_independence(self):
        raw = close_path([10 + i / 20 for i in range(21)])
        baseline = evaluate(raw)
        self.assertEqual(baseline["kind"], "intraday")
        self.assertIs(baseline["features"]["conditions"]["returnAtLeast10Pct"], True)
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            self.assertEqual(evaluate(raw), baseline)
        raw[-1].update(close=10.999999999999, high=10.999999999999)
        actual = evaluate(raw)
        self.assertEqual(actual["kind"], "notTriggered")
        self.assertIs(actual["features"]["conditions"]["returnAtLeast10Pct"], False)

    def test_nominal_fifteen_percent_drawdown_and_just_over(self):
        closes = [10., 8.5] + [8.5 + (12.-8.5)*i/19 for i in range(1, 20)]
        raw = close_path(closes)
        actual = evaluate(raw)
        self.assertEqual(actual["features"]["closeMaxDrawdown20"], .15)
        self.assertIs(actual["features"]["conditions"]["drawdownAtMost15Pct"], True)
        self.assertEqual(actual["kind"], "intraday")
        raw[1].update(close=8.499999, low=8.499999)
        actual = evaluate(raw)
        self.assertEqual(actual["kind"], "notTriggered")
        self.assertIs(actual["features"]["conditions"]["drawdownAtMost15Pct"], False)

    def test_efficiency_observed_failure_is_not_unknown(self):
        raw = path([.08, -.068] * 10)
        actual = evaluate(raw)
        self.assertEqual(actual["kind"], "notTriggered")
        self.assertGreater(actual["features"]["simpleReturn20"], .1)
        self.assertLess(actual["features"]["closeMaxDrawdown20"], .15)
        self.assertIs(actual["features"]["conditions"]["efficiencyAtLeast035"], False)
        self.assertAlmostEqual(actual["features"]["signedEfficiency20"], independent_values(raw)["efficiency"])

    def test_twenty_step_fraction_boundary_and_strict_below(self):
        for kind in ("intraday", "overnight"):
            with self.subTest(kind=kind):
                raw = close_path([10. * 8**i for i in range(21)])
                for i in range(1, 21):
                    opening = raw[i-1]["close"] * (2 if kind == "intraday" else 4)
                    raw[i].update(open=opening, low=opening)
                self.assertEqual(evaluate(raw)["kind"], kind)
                for i in range(1, 21):
                    raw[i]["open"] *= (1.000001 if kind == "intraday" else .999999)
                    raw[i]["low"] = raw[i]["open"]
                self.assertEqual(evaluate(raw)["kind"], "notTriggered")

    def test_twelve_positive_sessions_required_despite_dominant_component(self):
        for kind in ("intraday", "overnight"):
            for count in (11, 12):
                with self.subTest(kind=kind, count=count):
                    major = [.02] * count + [0.] * (20-count)
                    minor = [0.] * count + [.005] * (20-count)
                    raw = path(major if kind == "intraday" else minor, minor if kind == "intraday" else major)
                    actual = evaluate(raw)
                    self.assertEqual(actual["kind"], kind if count == 12 else "notTriggered")
                    key = "positiveIntradaySessions" if kind == "intraday" else "positiveOvernightSessions"
                    self.assertEqual(actual["features"][key], count)
        actual = evaluate(path([0.] * 20, [.2]+[0.] * 19))
        self.assertEqual(actual["kind"], "notTriggered")
        self.assertEqual(actual["features"]["largestPositiveGapShare"], 1.)
        self.assertEqual(actual["features"]["topThreePositiveGapShare"], 1.)

    def test_negative_components_and_shares_are_not_clipped(self):
        for kind in ("intraday", "overnight"):
            raw = path([.02 if kind == "intraday" else -.005] * 20,
                       [.02 if kind == "overnight" else -.005] * 20)
            actual = evaluate(raw)
            self.assertEqual(actual["kind"], kind)
            own, other = ("intradaySignedShare", "overnightSignedShare") if kind == "intraday" else ("overnightSignedShare", "intradaySignedShare")
            self.assertGreater(actual["features"][own], 1.)
            self.assertLess(actual["features"][other], 0.)
            self.assertAlmostEqual(actual["features"][own]+actual["features"][other], 1.)
        flat = evaluate(path([0.] * 20, [0.] * 20))
        self.assertEqual(flat["kind"], "notTriggered")
        self.assertEqual(flat["features"]["signedEfficiency20"], 0.)
        self.assertIsNone(flat["features"]["intradaySignedShare"])
        json.dumps(flat, allow_nan=False)

    def test_calendar_missing_duplicate_future_and_extra_rows_cannot_be_selected(self):
        cases = []
        raw = path(); raw[4] = None; cases.append((DATES, raw))
        raw = path(); raw[4]["date"] = DATES[5]; cases.append((DATES, raw))
        days = list(DATES); days[4] = days[5]; cases.append((days, path()))
        days = list(DATES); days[4] = "2099-01-01"; cases.append((days, path()))
        cases.extend(((DATES[:-1], path()[:-1]), (DATES+["2099-01-01"], path()+[path()[-1]])))
        for calendar, raw in cases:
            with self.subTest(calendar=calendar):
                result = evaluate(raw, calendar)
                self.assertEqual(result["kind"], "unevaluable")
                self.assertIsNone(result["signal"])
        raw = path()
        baseline = evaluate(raw[:21], DATES[:21])
        raw.extend([{"date": "2099-01-01", "close": float("nan")}])
        self.assertEqual(evaluate(raw[:21], DATES[:21]), baseline)

    def test_raw_ohlcv_basis_and_historical_suspension_are_required(self):
        for index in (0, 10, 20):
            for field, value in (("open", 0), ("high", float("inf")), ("low", True), ("close", None),
                                 ("volume", 0), ("volume", -1), ("priceBasisId", "forward-adjusted")):
                with self.subTest(index=index, field=field):
                    raw = path(); raw[index][field] = value
                    self.assertEqual(evaluate(raw)["kind"], "unevaluable")
        raw = path(); raw[8]["low"] = raw[8]["high"]+1
        self.assertEqual(evaluate(raw)["kind"], "unevaluable")
        raw = path(); raw[8]["knownSuspended"] = True
        self.assertEqual(evaluate(raw)["kind"], "unevaluable")
        # Finite source prices can still overflow their ratio. The invalid
        # derived path must remain unknown, never reach fsum(inf, -inf).
        extreme = evaluate(close_path([1e-308] * 20 + [1e308]))
        self.assertEqual((extreme["kind"], extreme["reason"]), ("unevaluable", "nonfiniteDerivedFeature"))
        self.assertIsNone(extreme["signal"])

    def test_explicit_prior_amount_required_without_price_volume_fallback(self):
        for value in (None, 0, -1, True, float("nan"), float("inf"), "10000000"):
            raw = path(); raw[0]["amount"] = value
            actual = evaluate(raw)
            self.assertEqual((actual["kind"], actual["reason"]), ("unevaluable", "prior20AmountUnknown"))
        raw = path(); del raw[10]["amount"]
        self.assertEqual(evaluate(raw)["reason"], "prior20AmountUnknown")

    def test_base_qualification_unknown_and_halt_priority(self):
        cases = [({"eligible": False, "reason": "historicalSt"}, "ineligible"),
                 ({"eligible": False, "reason": "delisted"}, "ineligible"),
                 ({"eligible": False, "reason": "strictHistoryMissing"}, "unevaluable"),
                 ({"eligible": False, "reason": "amount20BelowMinimum"}, "unevaluable"),
                 ({"eligible": False, "reason": "amount20BelowMinimum", "amount20": 0}, "ineligible"),
                 ({"eligible": False, "reason": "amplitude20BelowMinimum"}, "unevaluable"),
                 ({"eligible": 1, "reason": "eligible"}, "unevaluable")]
        for eligibility, kind in cases:
            self.assertEqual(evaluate(eligibility=eligibility)["kind"], kind)
        self.assertEqual(evaluate(suspended=True)["reason"], "suspensionContradictsPositiveVolume")
        raw = path(); raw[-1]["volume"] = 0
        halted = evaluate(raw, suspended=True)
        self.assertEqual((halted["kind"], halted["reason"]), ("unevaluable", "knownSuspension"))
        self.assertEqual(evaluate(raw, eligibility={"eligible": False, "reason": "delisted"}, suspended=True)["reason"], "baseEligibilityRejected")
        self.assertEqual(evaluate(raw, eligibility={"eligible": False, "reason": "strictHistoryMissing"}, suspended=True)["kind"], "unevaluable")
        with self.assertRaises(ValueError):
            evaluate(suspended=1)

    def test_evaluation_and_selection_do_not_mutate_or_alias_inputs(self):
        raw, eligibility = path(), deepcopy(ELIGIBILITY)
        original = deepcopy((raw, eligibility, DATES))
        actual = evaluate(raw, eligibility=eligibility)
        self.assertEqual((raw, eligibility, DATES), original)
        actual["quality"]["unknownHistoricalFields"].append("changed")
        actual["signal"]["features"]["prior20MeanAmount"] = -1
        self.assertEqual((raw, eligibility, DATES), original)
        rows = [classified("sh600001"), classified("sh600002", "overnight")]
        old = deepcopy(rows)
        selected = select_session_pairs(DATES[-1], rows)
        self.assertEqual(rows, old)
        selected["signals"][0]["features"]["totalLogReturn20"] = 999
        self.assertEqual(rows, old)

    def test_top_five_amount_symbol_order_and_unmatched_retained_without_backfill(self):
        rows = [classified(f"sh60000{i}", amount=100_000_000. if i <= 2 else 100_000_000.-i*1_000_000.) for i in range(1, 7)]
        # The sole comparison is outside the distance bounds for every anchor.
        rows += [classified("sz000001", "overnight", total=.5)]
        selected = select_session_pairs(DATES[-1], list(reversed(rows)))
        self.assertEqual(selected, select_session_pairs(DATES[-1], rows))
        self.assertEqual([r["symbol"] for r in selected["rankedIntraday"]], [f"sh60000{i}" for i in range(1, 7)])
        self.assertEqual([r["symbol"] for r in selected["signals"]], [f"sh60000{i}" for i in range(1, 6)])
        self.assertEqual((selected["requestedCount"], selected["matchedCount"], selected["shortfall"]), (5, 0, 5))
        self.assertFalse(selected["activeExclusionApplied"])
        self.assertTrue(all(p["controlSymbol"] is None and p["distance"] is None for p in selected["pairs"]))
        self.assertFalse(selected["rankedIntraday"][-1]["selectedTop5"])
        for rows in ([], [classified("sz000001", "overnight")]):
            empty = select_session_pairs(DATES[-1], rows)
            self.assertEqual(empty["signals"], [])
            self.assertEqual(empty["requestedCount"], 0)

    def test_greedy_no_replacement_and_control_symbol_tie_break(self):
        rows = [classified("sh600001", amount=100_000_000.), classified("sh600002", amount=90_000_000.),
                classified("sz000002", "overnight", amount=100_000_000.),
                classified("sz000001", "overnight", amount=100_000_000.)]
        selected = select_session_pairs(DATES[-1], rows)
        self.assertEqual([p["controlSymbol"] for p in selected["pairs"]], ["sz000001", "sz000002"])
        self.assertEqual([p["feasibleUnusedControls"] for p in selected["pairs"]], [2, 1])
        self.assertEqual(selected["matchedCount"], 2)
        controls = [s for s in selected["signals"] if s["strategyId"] == CONTROL_ID]
        self.assertEqual([s["rank"] for s in controls], [1, 2])
        self.assertEqual([s["pairedSymbol"] for s in controls], ["sh600001", "sh600002"])
        self.assertEqual(len({s["symbol"] for s in controls}), 2)
        self.assertTrue(all(s["selectedTop5"] is False and s["pairedStrategyId"] == CANDIDATE_ID for s in controls))

    def test_matching_calipers_direct_float_boundaries_and_just_outside(self):
        anchor = classified("sh600001", total=.25, drawdown=0., efficiency=1., amount=100_000_000.)
        boundaries = [("totalLogReturn20", .20, .199999999),
                      ("closeMaxDrawdown20", .05, .050000001),
                      ("signedEfficiency20", math.nextafter(.85, 1.), .849999999),
                      ("prior20MeanAmount", 50_000_000., 49_999_999.)]
        for field, within, outside in boundaries:
            for value, matched in ((within, True), (outside, False)):
                with self.subTest(field=field, value=value):
                    params = {"total": .25, "drawdown": 0., "efficiency": 1., "amount": 100_000_000.}
                    params[{"totalLogReturn20": "total", "closeMaxDrawdown20": "drawdown",
                            "signedEfficiency20": "efficiency", "prior20MeanAmount": "amount"}[field]] = value
                    control = classified("sz000001", "overnight", **params)
                    pair = select_session_pairs(DATES[-1], [anchor, control])["pairs"][0]
                    self.assertIs(pair["matched"], matched)
                    if matched:
                        self.assertAlmostEqual(pair["distance"], 1., places=12)
        # The frozen contract deliberately uses direct binary float calipers.
        # 1-.85 is just greater than .15; do not silently substitute epsilon.
        self.assertGreater(1.-.85, .15)
        control = classified("sz000001", "overnight", total=.25, efficiency=.85, amount=100_000_000.)
        self.assertFalse(select_session_pairs(DATES[-1], [anchor, control])["pairs"][0]["matched"])

    def test_normalized_l1_distance_not_amount_or_symbol_priority(self):
        anchor = classified("sh600001", total=.25, drawdown=.05, efficiency=.8, amount=100_000_000.)
        one = classified("sz000001", "overnight", total=.22, drawdown=.04, efficiency=.75, amount=150_000_000.)
        two = classified("sz000002", "overnight", total=.249, drawdown=.05, efficiency=.8, amount=100_000_000.)
        selected = select_session_pairs(DATES[-1], [anchor, one, two])
        pair = selected["pairs"][0]
        self.assertEqual(pair["controlSymbol"], "sz000002")
        self.assertAlmostEqual(pair["distance"], abs(.25-.249)/.05)
        pair = select_session_pairs(DATES[-1], [anchor, one])["pairs"][0]
        expected = math.fsum((abs(.25-.22)/.05, abs(.05-.04)/.05, abs(.8-.75)/.15, math.log(1.5)/math.log(2)))
        self.assertAlmostEqual(pair["distance"], expected)
        self.assertEqual(pair["amountRatio"], 1.5)

    def test_selector_rejects_duplicate_other_day_or_invalid_class_features(self):
        valid = classified("sh600001")
        for row in (None, {}, {**valid, "symbol": ""}, {**valid, "eligible": 1},
                    {**valid, "strategyId": CONTROL_ID}, {**valid, "signalDate": "2099-01-01"},
                    {**valid, "prior20MeanAmount": True}):
            with self.subTest(row=row), self.assertRaises((ValueError, TypeError)):
                select_session_pairs(DATES[-1], [row])
        with self.assertRaises(ValueError):
            select_session_pairs(DATES[-1], [valid, deepcopy(valid)])
        for kind in ("intraday", "overnight"):
            for key, value in (("totalLogReturn20", float("nan")), ("closeMaxDrawdown20", -.01),
                               ("signedEfficiency20", .34), ("prior20MeanAmount", 1.),
                               ("positiveIntradaySessions" if kind == "intraday" else "positiveOvernightSessions", 11),
                               ("intradayLogReturn20" if kind == "intraday" else "overnightLogReturn20", .01)):
                row = classified("sh600001", kind); row["features"][key] = value
                with self.subTest(kind=kind, key=key), self.assertRaises(ValueError):
                    select_session_pairs(DATES[-1], [row])


if __name__ == "__main__":
    unittest.main()
