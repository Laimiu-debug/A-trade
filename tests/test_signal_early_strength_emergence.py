"""Synthetic-only boundary tests for the preregistered first-entry clock."""
from copy import deepcopy
from datetime import date, timedelta
from statistics import mean
import unittest

from service.signal_early_strength_emergence import (
    VERSION, CANDIDATE_ID, CONTROL_ID, EarlyStrengthEmergenceStudy,
    project_panel, evaluate_advance,
)


def dates(n=100):
    out, day = [], date(2021, 8, 2)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def coverage(window):
    size = len(window)
    return {"requiredSessions": size, "availableCalendarSessions": size,
        "observedSessions": size, "validSessions": size, "complete": True,
        "missingDates": [], "invalidDates": [], "haltDates": [],
        "unknownTradeStatusDates": list(window), "reasons": []}


def panel(symbol, day, calendar, rank=.95, *, rank60=.7, history=.2, amount=100000000,
          eligible=True, reason="eligible"):
    pos = calendar.index(day)
    days = calendar[max(0, pos-60):pos+1]
    values = [history]*19+[rank]
    row = {"featureVersion": "daily-leadership-raw-v1", "symbol": symbol, "asOfDate": day,
        "rawPriceBasis": "raw-unadjusted", "featureReasons": {},
        "baseEligibility": {"eligible": eligible, "reason": reason, "unknown": [],
                            "certified": False, "affectsFeaturesOrRanks": False},
        "return20": .1, "relativeReturn20": .05, "return60": .3, "relativeReturn60": .2,
        "relativeReturn20Percentile": rank, "relativeReturn60Percentile": rank60,
        "relativeReturn20CrossSectionCount": 100, "relativeReturn60CrossSectionCount": 100,
        "trailing20DailyStrengthPercentiles": values, "trailing20StrengthCrossSectionCounts": [100]*20,
        "strengthRankSessionDates20": days[-20:], "knownStrengthRankSessions20": 20,
        "availableStrengthRankSessions20": 20, "meanStrengthPercentile20": mean(values),
        "minimumStrengthPercentile20": min(values), "prior20MeanAmount": amount,
        "knownAmountSessions20": 20,
        "completeness": {key: coverage(days[-size:]) for key, size in
            (("security20", 21), ("benchmark20", 21), ("security60", 61), ("benchmark60", 61))}}
    row["completeness"]["amountPrior20"] = coverage(days[-21:-1])
    return row


def bars(days, start=100, end=110):
    values = [start+(end-start)*i/max(1, len(days)-1) for i in range(len(days))]
    return [{"date": d, "open": c, "close": c, "high": c+1, "low": c-1,
             "volume": 1000, "amount": 100000, "priceBasisId": "raw-unadjusted",
             "knownSuspended": False, "tradeStatusState": "unknown"} for d, c in zip(days, values)]


class EarlyStrengthEmergenceTests(unittest.TestCase):
    def setUp(self):
        self.full = dates()
        self.target = self.full[65:77]
        self.symbols = ["s"]

    def projection(self, index=0, symbol="s", rank=.95, mutate=None, **kwargs):
        day = self.target[index]
        row = panel(symbol, day, self.full, rank, **kwargs)
        if mutate:
            mutate(row)
        pos = self.full.index(day)
        return project_panel(symbol, day, self.full[pos-60:pos+1], row)

    def step(self, study, index, ranks=.95, specs=None, mutate=None, raw_mutate=None):
        day = self.target[index]
        pos = self.full.index(day)
        window = self.full[pos-5:pos+1]
        projected, raw = {}, {}
        for symbol in self.symbols:
            rank = ranks[symbol] if isinstance(ranks, dict) else ranks
            kwargs = (specs or {}).get(symbol, {})
            projected[symbol] = self.projection(index, symbol, rank, mutate=mutate, **kwargs)
            raw[symbol] = bars(window)
            if raw_mutate:
                raw_mutate(raw[symbol])
        return study.step(day, index, projected, raw, bars(window, end=102))

    def study(self, n=12):
        return EarlyStrengthEmergenceStudy(self.target[:n], self.symbols, formation_calendar=self.full)

    def test_projection_keeps_independently_known_groups(self):
        def alter(r):
            r["completeness"]["security60"]["complete"] = False
            r["prior20MeanAmount"] = None
            r["featureReasons"]["signedPathEfficiency20"] = ["zeroPathDistance"]
        out = self.projection(mutate=alter)
        self.assertTrue(out["rank20"]["known"])
        self.assertTrue(out["history20"]["known"])
        self.assertFalse(out["rank60"]["known"])
        self.assertFalse(out["amountPrior20"]["known"])

    def test_history_requires_exact_dates_counts_last_and_arithmetic(self):
        alterations = [
            lambda r: r["strengthRankSessionDates20"].__setitem__(0, self.full[0]),
            lambda r: r["trailing20StrengthCrossSectionCounts"].__setitem__(0, True),
            lambda r: r["trailing20DailyStrengthPercentiles"].__setitem__(0, None),
            lambda r: r.__setitem__("knownStrengthRankSessions20", True),
            lambda r: r.__setitem__("meanStrengthPercentile20", .5),
            lambda r: r.__setitem__("minimumStrengthPercentile20", .1),
            lambda r: r["trailing20StrengthCrossSectionCounts"].__setitem__(-1, 99),
            lambda r: r["featureReasons"].__setitem__("meanStrengthPercentile20", ["missing"]),
        ]
        for alter in alterations:
            with self.subTest(alter=alter):
                out = self.projection(mutate=alter)
                self.assertFalse(out["history20"]["known"])
                self.assertTrue(out["rank60"]["known"])

    def test_rank_known_bounds_types_and_full_price_evidence(self):
        for value in (True, None, float("nan"), float("inf"), -.01, 1.01):
            with self.subTest(value=value):
                out = self.projection(mutate=lambda r: r.__setitem__("relativeReturn20Percentile", value))
                self.assertFalse(out["rank20"]["known"])
        for alteration in (
            lambda r: r.__setitem__("relativeReturn20CrossSectionCount", False),
            lambda r: r.__setitem__("return20", -1.1),
            lambda r: r["completeness"]["benchmark20"].__setitem__("validSessions", True),
            lambda r: r["completeness"]["security20"].__setitem__("haltDates", [r["asOfDate"]]),
            lambda r: r.__setitem__("rawPriceBasis", "adjusted"),
        ):
            self.assertFalse(self.projection(mutate=alteration)["rank20"]["known"])
        self.assertTrue(self.projection()["rank20"]["known"])

    def test_amount_is_explicit_independent_and_excludes_current_day(self):
        for bad in (None, False, 0, -1, float("nan"), float("inf")):
            self.assertFalse(self.projection(amount=bad)["amountPrior20"]["known"])
        def alter(r):
            r["amountToday"] = None
            r["completeness"]["security20"]["complete"] = False
        self.assertTrue(self.projection(mutate=alter)["amountPrior20"]["known"])

    def test_projection_identity_calendar_and_qualification(self):
        for key, value in (("symbol", "wrong"), ("asOfDate", self.target[1]), ("featureVersion", "other")):
            with self.assertRaises(ValueError):
                self.projection(mutate=lambda r: r.__setitem__(key, value))
        row = panel("s", self.target[0], self.full)
        with self.assertRaises(ValueError):
            project_panel("s", self.target[0], self.full[6:67], row)
        for alter in (
            lambda r: r["strengthRankSessionDates20"].__setitem__(0, self.target[1]),
            lambda r: r["completeness"]["security20"].__setitem__("unknownTradeStatusDates", [self.target[1]]),
        ):
            with self.assertRaises(ValueError):
                self.projection(mutate=alter)
        self.assertEqual(self.projection(eligible=False, reason="historicalSt")["qualification"]["state"], "false")
        for reason in ("missingDailyBar", "strictHistoryMissing", "unexpected"):
            self.assertEqual(self.projection(eligible=False, reason=reason)["qualification"]["state"], "unknown")
        self.assertEqual(self.projection(mutate=lambda r: r["baseEligibility"].__setitem__("unknown", ["historicalSt"]))["qualification"]["state"], "true")
        self.assertEqual(self.projection(mutate=lambda r: r["baseEligibility"].__setitem__("unknown", ["x", "x"]))["qualification"]["state"], "unknown")

    def test_advance_formula_exact_strict_positive_and_independent_unknown(self):
        window = self.full[60:66]
        result = evaluate_advance("s", window[-1], window, bars(window), bars(window, end=102))
        self.assertEqual(result["own5"], 110/100-1)
        self.assertEqual(result["excess5"], (110/100)/(102/100)-1)
        self.assertEqual(result["state"], "true")
        self.assertEqual(result["stockQuality"]["unknownTradeStatusDates"], window)
        absent_status = bars(window)
        for bar in absent_status:
            del bar["tradeStatusState"]
        inferred = evaluate_advance("s", window[-1], window, absent_status, bars(window, end=102))
        self.assertEqual(inferred["stockQuality"]["unknownTradeStatusDates"], window)
        self.assertEqual(inferred["state"], "true")
        same = evaluate_advance("s", window[-1], window, bars(window), bars(window))
        self.assertEqual(same["excess5"], 0)
        self.assertEqual(same["state"], "false")
        missing = evaluate_advance("s", window[-1], window, bars(window, end=90), [None]*6)
        self.assertEqual(missing["predicates"]["own5"]["state"], "false")
        self.assertEqual(missing["state"], "unknown")

    def test_advance_quality_halts_missing_basis_dates_and_envelope(self):
        window = self.full[60:66]
        changes = [lambda b: b.__setitem__(2, None), lambda b: b[2].__setitem__("date", window[1]),
            lambda b: b[2].__setitem__("priceBasisId", "adjusted"), lambda b: b[2].__setitem__("volume", 0),
            lambda b: b[2].__setitem__("low", 999), lambda b: b[2].__setitem__("knownSuspended", True),
            lambda b: b[2].__setitem__("tradeStatusState", "halt"), lambda b: b[2].__setitem__("close", float("nan"))]
        for change in changes:
            raw = bars(window)
            change(raw)
            self.assertEqual(evaluate_advance("s", window[-1], window, raw, bars(window))["state"], "unknown")
        self.assertEqual(evaluate_advance("s", window[-1], window[-5:], bars(window[-5:]), bars(window[-5:]))["state"], "unknown")
        with self.assertRaises(ValueError):
            evaluate_advance("s", window[-2], window, bars(window), bars(window))

    def test_advance_nonfinite_and_underflow_derived_are_unknown(self):
        window = self.full[60:66]
        def extreme(start, end):
            raw = bars(window)
            for index, value in ((0, start), (-1, end)):
                raw[index].update(open=value, high=value, low=value, close=value)
            return raw
        for start, end in ((1e-300, 1e300), (1e300, 1e-300)):
            result = evaluate_advance("s", window[-1], window, extreme(start, end), bars(window))
            self.assertEqual(result["state"], "unknown")
            self.assertIsNone(result["own5"])

    def test_first_target_has_no_invented_previous_row_then_confirm(self):
        study = self.study(4)
        first = self.step(study, 0, .8)
        self.assertEqual(first["evaluations"][0]["crossingReason"], "previousFullPanelUnavailable")
        self.assertEqual(first["formations"], [])
        a = self.step(study, 1, .90)
        self.assertEqual(a["formations"][0]["episode"]["formationDate"], self.target[1])
        self.assertEqual(a["formations"][0]["kind"], "observing")
        self.step(study, 2, .90)
        end = self.step(study, 3, .90)
        self.assertEqual([s["strategyId"] for s in end["signals"]], [CANDIDATE_ID, CONTROL_ID])
        self.assertEqual(end["rawConfirmations"][0]["formationDate"], self.target[1])
        self.assertEqual(study.finish(self.target[3])["episodes"][0]["state"], "confirmed")

    def test_early_mask_inclusive_boundaries_and_unknown_priority(self):
        for history, rank60, expected in ((.5, .8, "true"), (.51, .8, "false"), (.5, .81, "false")):
            study = self.study(2)
            # Equal .5 history including the previous day's rank has exact mean .5.
            self.step(study, 0, .5, {"s": {"history": history}})
            result = self.step(study, 1, .95, {"s": {"rank60": rank60}})
            self.assertEqual(result["formations"][0]["episode"]["earlyMask"], expected)
        study = self.study(2)
        self.step(study, 0, .8, {"s": {"history": .8}})
        out = self.step(study, 1, .95, {"s": {"rank60": None}})
        evidence = out["formations"][0]["episode"]["earlyMaskEvidence"]
        self.assertEqual(evidence["state"], "unknown")
        self.assertEqual(evidence["predicates"]["priorHistoryMean"]["state"], "false")

    def test_mask_and_amount_frozen_at_formation(self):
        study = self.study(4)
        self.step(study, 0, .8)
        self.step(study, 1, .95, {"s": {"amount": 123, "rank60": .7}})
        self.step(study, 2, .95, {"s": {"rank60": .99, "amount": 999}})
        result = self.step(study, 3, .95, {"s": {"rank60": .99, "amount": 999}})
        self.assertEqual(result["selection"]["slots"][0]["prior20MeanAmount"], 123)
        self.assertEqual(result["selection"]["slots"][0]["earlyMask"], "true")

    def test_observation_unknown_and_loss_are_first_terminal(self):
        for unknown, expected in ((True, "rankUnknown"), (False, "rankLost")):
            study = self.study(5)
            self.step(study, 0, .8)
            self.step(study, 1, .95)
            mutate = (lambda r: r.__setitem__("relativeReturn20Percentile", None)) if unknown else None
            out = self.step(study, 2, .8 if not unknown else .95, mutate=mutate)
            self.assertEqual(out["transitions"][0]["toState"], expected)
            self.assertEqual(self.step(study, 3, .95)["signals"], [])
            self.assertEqual(self.step(study, 4, .95)["signals"], [])
            self.assertEqual(study.finish(self.target[4])["episodes"][0]["state"], expected)

    def test_failed_formation_still_locks_and_upcross_inside_is_logged(self):
        study = self.study(6)
        self.step(study, 0, .8)
        a = self.step(study, 1, .95, {"s": {"eligible": False, "reason": "historicalSt"}})
        self.assertEqual(a["formations"][0]["kind"], "formationRejected")
        self.step(study, 2, .8)
        locked = self.step(study, 3, .95)
        self.assertEqual(locked["formations"][0]["kind"], "formationSuppressedByLock")
        self.step(study, 4, .8)
        retry = self.step(study, 5, .95)
        self.assertEqual(retry["formations"][0]["kind"], "observing")
        self.assertEqual(retry["episodeCounts"]["activatedEpisodes"], 2)

    def test_formation_unknown_precedes_known_rejection(self):
        study = self.study(2)
        self.step(study, 0, .8)
        out = self.step(study, 1, .95, {"s": {"eligible": False, "reason": "historicalSt", "amount": None}})
        ep = out["formations"][0]["episode"]
        self.assertEqual(ep["state"], "formationUnknown")
        self.assertEqual(ep["formationCommon"]["predicates"]["qualification"], "false")
        self.assertEqual(ep["lockThroughIndex"], 3)

    def test_no_middle_eligibility_gate_but_final_qualification_consumes(self):
        for reason, expected in (("eligible", "confirmed"), ("historicalSt", "qualificationRejected"),
                                 ("missingDailyBar", "qualificationUnknown")):
            study = self.study(4)
            self.step(study, 0, .8)
            self.step(study, 1, .95)
            self.step(study, 2, .95, {"s": {"eligible": False, "reason": "historicalSt"}})
            out = self.step(study, 3, .95, {"s": {"eligible": reason == "eligible", "reason": reason}})
            self.assertEqual(out["transitions"][0]["toState"], expected)
            self.assertEqual(len(out["rawConfirmations"]), 1)
            self.assertEqual(bool(out["signals"]), expected == "confirmed")

    def test_rank_unknown_priority_over_final_qualification_rejection(self):
        study = self.study(4)
        self.step(study, 0, .8); self.step(study, 1, .95); self.step(study, 2, .95)
        out = self.step(study, 3, .95, {"s": {"eligible": False, "reason": "historicalSt"}},
                        mutate=lambda r: r.__setitem__("relativeReturn20Percentile", None))
        self.assertEqual(out["transitions"][0]["toState"], "rankUnknown")
        self.assertEqual(out["transitions"][0]["evidence"]["qualification"]["state"], "false")
        self.assertEqual(out["rawConfirmations"], [])

    def test_cutoff_censors_only_live_episode_and_finish_once(self):
        study = self.study(3)
        self.step(study, 0, .8); self.step(study, 1, .95); self.step(study, 2, .95)
        done = study.finish(self.target[2])
        self.assertEqual(done["episodes"][0]["state"], "censoredAtStudyCutoff")
        with self.assertRaises(ValueError):
            study.finish(self.target[2])
        final = self.study(2)
        self.step(final, 0, .8); self.step(final, 1, .95)
        self.assertEqual(final.finish(self.target[1])["episodes"][0]["state"], "censoredAtStudyCutoff")

    def test_global_top5_before_mask_no_backfill_and_stable_ties(self):
        self.symbols = ["f", "e", "d", "c", "b", "a"]
        study = self.study(4)
        self.step(study, 0, .8)
        specs = {s: {"amount": 100, "rank60": .7} for s in self.symbols}
        specs["a"]["rank60"] = .9
        specs["b"]["rank60"] = None
        self.step(study, 1, .95, specs)
        self.step(study, 2, .95)
        out = self.step(study, 3, .95)
        self.assertEqual([r["symbol"] for r in out["selection"]["fullRanking"]], list("abcdef"))
        self.assertEqual([r["symbol"] for r in out["signals"] if r["strategyId"] == CANDIDATE_ID], list("cde"))
        self.assertEqual([r["symbol"] for r in out["signals"] if r["strategyId"] == CONTROL_ID], list("abcde"))
        self.assertEqual([r["rank"] for r in out["signals"] if r["strategyId"] == CANDIDATE_ID], [3, 4, 5])
        self.assertEqual([r["earlyMask"] for r in out["selection"]["slots"][:2]], ["false", "unknown"])

    def test_state_inputs_outputs_do_not_alias(self):
        study = self.study(4)
        day = self.target[0]
        pos = self.full.index(day)
        projected = {"s": self.projection(0, rank=.8)}
        raw = {"s": bars(self.full[pos-5:pos+1])}
        bench = bars(self.full[pos-5:pos+1], end=102)
        before = deepcopy((projected, raw, bench))
        out = study.step(day, 0, projected, raw, bench)
        self.assertEqual((projected, raw, bench), before)
        projected["s"]["rank20"]["value"] = .99
        out["evaluations"][0]["rank20"]["value"] = .99
        formation = self.step(study, 1, .95)
        self.assertEqual(formation["formations"][0]["kind"], "observing")
        formation["formations"][0]["episode"]["earlyMask"] = "false"
        self.step(study, 2, .95)
        self.assertEqual(len(self.step(study, 3, .95)["signals"]), 2)

    def test_future_extension_does_not_change_consumed_outputs(self):
        a, b = self.study(4), self.study(8)
        for i, rank in enumerate((.8, .95, .95, .95)):
            self.assertEqual(self.step(a, i, rank), self.step(b, i, rank))
        for i in range(4, 8):
            self.step(b, i, .1)
        self.assertEqual(a.finish(self.target[3])["episodes"], b.finish(self.target[7])["episodes"])

    def test_complete_population_order_calendar_and_compact_contract(self):
        with self.assertRaises(ValueError):
            EarlyStrengthEmergenceStudy([self.target[0], self.target[2]], ["s"], formation_calendar=self.full)
        with self.assertRaises(ValueError):
            EarlyStrengthEmergenceStudy(self.target, ["s", "s"])
        study = self.study()
        with self.assertRaises(ValueError):
            study.step(self.target[0], 0, {}, {}, [])
        with self.assertRaises(ValueError):
            self.step(study, 1, .8)
        projection = self.projection(0)
        projection["history20"]["mean"] = .99
        with self.assertRaises(ValueError):
            study.step(self.target[0], 0, {"s": projection}, {"s": []}, [])
        self.step(study, 0, .8)
        with self.assertRaises(ValueError):
            self.step(study, 0, .8)


if __name__ == "__main__":
    unittest.main()
