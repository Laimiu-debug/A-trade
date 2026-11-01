"""Synthetic fixed-peer and causal breadth tests, without real market data."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
import unittest

from service.signal_leader_peer_expansion import (
    VERSION, CANDIDATE_ID, CONTROL_ID, PARENT_ID, GROUP_SIZE, HISTORY_SLOTS,
    form_peer_group, evaluate_peer_expansion, select_peer_signals,
)


def calendar(count=71):
    result, day = [], date(2023, 10, 2)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


DAYS = calendar()
F, T = DAYS[60], DAYS[70]
ANCHOR = "sh600000"
PEERS = [f"sh600{i:03d}" for i in range(1, 25)]
ELIGIBLE = {"eligible": True, "reason": "eligible", "unknown": ["historicalSt", "historicalMembership"]}


def from_closes(days, closes):
    rows = []
    for index, (day, close) in enumerate(zip(days, closes)):
        opening = closes[index-1] if index else close
        rows.append({"date": day, "open": opening, "high": max(opening, close), "low": min(opening, close),
            "close": close, "volume": 100_000., "priceBasisId": "raw-unadjusted", "knownSuspended": False})
    return rows


def from_returns(days, returns, start=10.):
    closes = [start]
    for value in returns:
        closes.append(closes[-1]*math.exp(value))
    return from_closes(days, closes)


def formation_fixture(peer_count=24):
    benchmark_log = [.004 if i % 3 == 0 else -.002 for i in range(60)]
    excess = [.01 if i % 2 == 0 else -.01 for i in range(60)]
    benchmark = from_returns(DAYS[:61], benchmark_log, 100.)
    own = from_returns(DAYS[:61], [a+b for a, b in zip(benchmark_log, excess)])
    raw = {ANCHOR: own, **{s: deepcopy(own) for s in PEERS[:peer_count]}}
    eligibility = {s: deepcopy(ELIGIBLE) for s in raw}
    return raw, benchmark, eligibility


def formed(peer_count=24):
    raw, benchmark, eligibility = formation_fixture(peer_count)
    return form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)


def expansion_fixture(group=None, before=8, now=12):
    group = formed() if group is None else group
    raw = {}
    for i, symbol in enumerate(group["members"]):
        middle = 11. if i < before else 10.
        # Reverse order ensures the successful half need not contain the same peers.
        end = middle*1.1 if i >= len(group["members"])-now else middle
        closes = [10.+(middle-10.)*j/5 for j in range(6)] + [middle+(end-middle)*j/5 for j in range(1, 6)]
        raw[symbol] = from_closes(DAYS[60:], closes)
    benchmark = from_closes(DAYS[60:], [100.] * 11)
    return group, raw, benchmark


def expansion(before=8, now=12):
    group, raw, benchmark = expansion_fixture(before=before, now=now)
    return evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)


def parent_rows(count=5):
    return [{"strategyId": PARENT_ID, "symbol": f"leader{i}", "signalDate": T, "rank": i+1,
             "eligible": True, "selectedTop5": True, "score": .99-i*.01,
             "features": {"meanStrengthPercentile20": .99-i*.01}} for i in range(count)]


def outcome_for(symbol, kind="expanded"):
    outcome = expansion(now=12 if kind == "expanded" else 11)
    outcome["anchor"] = symbol
    if kind == "unevaluable":
        outcome.update(kind=kind, reason="memberPathUnevaluable", expanded=None,
                       beforeCount=None, nowCount=None, countChange=None, observedMembers=19,
                       conditions={"nowAtLeast12": None, "increaseAtLeast4": None})
    return outcome


def pearson_reference(anchor, peer, benchmark):
    def excess(raw):
        return [math.log(raw[i]["close"]/raw[i-1]["close"])-math.log(benchmark[i]["close"]/benchmark[i-1]["close"])
                for i in range(1, 61)]
    x, y = excess(anchor), excess(peer)
    mx, my = math.fsum(x)/60, math.fsum(y)/60
    dx, dy = [v-mx for v in x], [v-my for v in y]
    return math.fsum(a*b for a, b in zip(dx, dy))/(math.sqrt(math.fsum(a*a for a in dx))*math.sqrt(math.fsum(b*b for b in dy)))


class LeaderPeerExpansionTests(unittest.TestCase):
    def test_fixed_twenty_group_independent_pearson_projection_and_ties(self):
        raw, benchmark, eligibility = formation_fixture()
        group = form_peer_group(ANCHOR, F, DAYS[:61], dict(reversed(list(raw.items()))), benchmark, eligibility)
        self.assertEqual(group["kind"], "formed")
        self.assertEqual(group["members"], PEERS[:20])
        self.assertEqual((HISTORY_SLOTS, GROUP_SIZE, group["selectedCount"]), (71, 20, 20))
        self.assertEqual(group["window"], {"startDate": DAYS[0], "endDate": F, "closeSessions": 61,
                         "returnSessions": 60, "providedCalendarSessions": 61})
        rows = {r["symbol"]: r for r in group["candidateEvaluations"]}
        self.assertEqual(rows[ANCHOR]["reason"], "excludedSelf")
        self.assertIsNone(rows[ANCHOR]["correlation"])
        expected = pearson_reference(raw[ANCHOR], raw[PEERS[0]], benchmark)
        self.assertEqual(rows[PEERS[0]]["rawCorrelation"], expected)
        self.assertEqual(rows[PEERS[0]]["correlation"], max(-1., min(1., expected)))
        self.assertEqual([rows[s]["rank"] for s in PEERS], list(range(1, 25)))
        self.assertEqual(sum(r["selected"] for r in rows.values()), 20)
        self.assertEqual(group, form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility))
        self.assertFalse(group["quality"]["certified"])
        self.assertFalse(group["quality"]["historicalTradeStatusVerified"])
        self.assertIn("not beta-adjusted", group["quality"]["interpretation"])
        json.dumps(group, allow_nan=False)

    def test_benchmark_common_movement_and_zero_variance_remain_unknown(self):
        raw, benchmark, eligibility = formation_fixture()
        raw[PEERS[0]] = deepcopy(benchmark)
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        row = next(r for r in group["candidateEvaluations"] if r["symbol"] == PEERS[0])
        self.assertEqual(row["reason"], "zeroOrNonfiniteExcessVariance")
        self.assertIsNone(row["correlation"])
        self.assertNotIn(PEERS[0], group["members"])
        raw[ANCHOR] = deepcopy(benchmark)
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        self.assertEqual(group["kind"], "unevaluable")
        self.assertEqual(group["reason"], "anchor:zeroOrNonfiniteExcessVariance")
        self.assertEqual(len(group["candidateEvaluations"]), 25)
        self.assertEqual(group["members"], [])

    def test_less_than_twenty_preserves_partial_evidence_but_cannot_expand(self):
        group = formed(19)
        self.assertEqual((group["kind"], group["reason"], group["selectedCount"]),
                         ("unevaluable", "insufficientCorrelatedPeers", 19))
        self.assertEqual(group["members"], PEERS[:19])
        _, raw, benchmark = expansion_fixture(group)
        outcome = evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)
        self.assertEqual(outcome["kind"], "unevaluable")
        self.assertIsNone(outcome["expanded"])
        self.assertEqual(outcome["fixedDenominator"], 20)
        self.assertIsNone(outcome["nowCount"])

    def test_formation_eligibility_and_anchor_never_upgrade_unknown(self):
        raw, benchmark, eligibility = formation_fixture()
        variants = [(True, "eligible", [], "qualified"), (False, "historicalSt", [], "ineligible"),
                    (None, "eligible", [], "unevaluable"), (True, "invalidHistory", [], "unevaluable"),
                    (True, "eligible", ["x", "x"], "unevaluable"), (True, "eligible", [""], "unevaluable"),
                    (True, "eligible", "unknown", "unevaluable")]
        for eligible, reason, unknown, status in variants:
            with self.subTest(eligible=eligible, reason=reason, unknown=unknown):
                changed = deepcopy(eligibility)
                changed[PEERS[0]] = {"eligible": eligible, "reason": reason, "unknown": unknown}
                group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, changed)
                self.assertEqual(next(r for r in group["candidateEvaluations"] if r["symbol"] == PEERS[0])["status"], status)
        eligibility[ANCHOR] = {"eligible": False, "reason": "historicalSt", "unknown": []}
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        self.assertEqual(group["reason"], "anchor:baseEligibilityRejected")
        self.assertEqual(group["members"], [])
        self.assertEqual(next(r for r in group["candidateEvaluations"] if r["symbol"] == ANCHOR)["reason"], "excludedSelf")

    def test_formation_all_raw_failures_are_recorded_and_not_zero_correlations(self):
        original, benchmark, eligibility = formation_fixture()
        cases = [("missing", lambda r: r.__setitem__(20, None)),
                 ("zero", lambda r: r[20].update(volume=0)),
                 ("envelope", lambda r: r[20].update(low=r[20]["high"]+1)),
                 ("basis", lambda r: r[20].update(priceBasisId="forward-adjusted")),
                 ("date", lambda r: r[20].update(date=DAYS[21])),
                 ("halt", lambda r: r[20].update(knownSuspended=True)),
                 ("flag", lambda r: r[20].update(knownSuspended="false")),
                 ("nan", lambda r: r[20].update(open=float("nan")))]
        for name, change in cases:
            with self.subTest(case=name):
                raw = deepcopy(original); change(raw[PEERS[0]])
                group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
                row = next(r for r in group["candidateEvaluations"] if r["symbol"] == PEERS[0])
                self.assertEqual(row["status"], "unevaluable")
                self.assertIsNone(row["correlation"])
                self.assertEqual(row["invalidSlot"], 20)
                self.assertEqual(len(group["candidateEvaluations"]), 25)
        raw = deepcopy(original); raw[PEERS[0]] = from_closes(DAYS[:61], [1e-308]*60+[1e308])
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        self.assertEqual(next(r for r in group["candidateEvaluations"] if r["symbol"] == PEERS[0])["reason"], "nonfiniteLogReturn")

    def test_exact_calendar_and_no_future_suffix_truncation(self):
        raw, benchmark, eligibility = formation_fixture()
        bad = [DAYS[:60], DAYS[:62], list(reversed(DAYS[:61])), DAYS[:30]+[DAYS[29]]+DAYS[31:61],
               DAYS[:30]+["2099-01-01"]+DAYS[31:61]]
        for days in bad:
            outcome = form_peer_group(ANCHOR, F, days, raw, benchmark, eligibility)
            self.assertEqual(outcome["kind"], "unevaluable")
            self.assertEqual(len(outcome["candidateEvaluations"]), len(raw))
            self.assertEqual(outcome["selectedCount"], 0)
        raw[PEERS[0]].append(deepcopy(raw[PEERS[0]][-1]))
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        self.assertEqual(next(r for r in group["candidateEvaluations"] if r["symbol"] == PEERS[0])["reason"], "insufficientAlignedRawHistory")

    def test_causal_formation_independent_of_future_appended_data(self):
        raw, benchmark, eligibility = formation_fixture()
        extended = {s: bars+from_closes(DAYS[61:], [1000.] * 10) for s, bars in raw.items()}
        group = form_peer_group(ANCHOR, F, DAYS[:61], {s: bars[:61] for s, bars in extended.items()}, benchmark, eligibility)
        for bars in extended.values():
            for bar in bars[61:]:
                bar.update(close=float("nan"), knownSuspended=True, volume=0)
        replay = form_peer_group(ANCHOR, F, DAYS[:61], {s: bars[:61] for s, bars in extended.items()}, benchmark, eligibility)
        self.assertEqual(group, replay)
        self.assertEqual(group["members"], PEERS[:20])

    def test_positive_negative_and_threshold_correlation_use_excess_not_raw(self):
        raw, benchmark, eligibility = formation_fixture()
        bench = [.004 if i % 3 == 0 else -.002 for i in range(60)]
        x = [.01 if i % 2 == 0 else -.01 for i in range(60)]
        y = [.01 if i % 4 < 2 else -.01 for i in range(60)]
        for symbol, rho in zip(PEERS[:4], (.30001, .29999, -.5, .7)):
            raw[symbol] = from_returns(DAYS[:61], [b+rho*a+math.sqrt(1-rho*rho)*z for b, a, z in zip(bench, x, y)])
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        rows = {r["symbol"]: r for r in group["candidateEvaluations"]}
        self.assertEqual([rows[s]["status"] for s in PEERS[:4]], ["qualified", "belowThreshold", "belowThreshold", "qualified"])
        for symbol in PEERS[:4]:
            self.assertAlmostEqual(rows[symbol]["rawCorrelation"], pearson_reference(raw[ANCHOR], raw[symbol], benchmark), places=15)

    def test_expansion_exact_counts_and_independent_half_windows(self):
        for before, now, kind in ((8, 12, "expanded"), (9, 12, "notExpanded"), (7, 11, "notExpanded"),
                                  (16, 20, "expanded"), (0, 0, "notExpanded")):
            with self.subTest(before=before, now=now):
                outcome = expansion(before, now)
                self.assertEqual(outcome["kind"], kind)
                self.assertEqual((outcome["beforeCount"], outcome["nowCount"], outcome["countChange"]), (before, now, now-before))
                self.assertEqual(outcome["fixedDenominator"], 20)
                self.assertEqual(outcome["observedMembers"], 20)
                self.assertEqual(outcome["window"]["middleDate"], DAYS[65])
                self.assertEqual((outcome["formationDate"], outcome["date"]), (F, T))
                self.assertEqual(sum(r["beforePositive"] for r in outcome["memberEvaluations"]), before)
                self.assertEqual(sum(r["nowPositive"] for r in outcome["memberEvaluations"]), now)
        self.assertFalse(any(r["beforePositive"] and r["nowPositive"] for r in expansion()["memberEvaluations"]))

    def test_strict_positive_relative_return_and_benchmark_motion_cancel(self):
        group, raw, benchmark = expansion_fixture(before=0, now=0)
        for symbol in group["members"]:
            raw[symbol] = deepcopy(benchmark)
        result = evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)
        self.assertEqual((result["beforeCount"], result["nowCount"], result["kind"]), (0, 0, "notExpanded"))
        for row in result["memberEvaluations"]:
            self.assertEqual((row["beforeRelativeLogReturn"], row["nowRelativeLogReturn"]), (0., 0.))
        raw[group["members"][0]] = from_closes(DAYS[60:], [100.-i*.1 for i in range(11)])
        result = evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)
        self.assertFalse(result["memberEvaluations"][0]["beforePositive"])
        self.assertFalse(result["memberEvaluations"][0]["nowPositive"])

    def test_missing_halt_and_bad_interior_members_keep_fixed_denominator(self):
        group, original, benchmark = expansion_fixture()
        for change in (lambda r: r.__setitem__(1, None), lambda r: r[8].update(knownSuspended=True),
                       lambda r: r[4].update(volume=0), lambda r: r[4].update(low=r[4]["high"]+1)):
            raw = deepcopy(original); change(raw[group["members"][0]])
            raw["replacement"] = deepcopy(original[group["members"][1]])
            outcome = evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)
            self.assertEqual(outcome["kind"], "unevaluable")
            self.assertEqual(outcome["members"], group["members"])
            self.assertEqual((outcome["fixedDenominator"], outcome["observedMembers"]), (20, 19))
            self.assertIsNone(outcome["beforeCount"])
            self.assertIsNone(outcome["nowCount"])
            self.assertIsNone(outcome["expanded"])
            self.assertEqual(len(outcome["memberEvaluations"]), 20)

    def test_invalid_benchmark_prevents_both_formation_and_expansion(self):
        raw, benchmark, eligibility = formation_fixture()
        benchmark[4] = None
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        self.assertEqual(group["reason"], "benchmark:missingAlignedRawBar")
        self.assertEqual(len(group["candidateEvaluations"]), 25)
        group, raw, benchmark = expansion_fixture(); benchmark[3]["knownSuspended"] = True
        outcome = evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)
        self.assertEqual(outcome["kind"], "unevaluable")
        self.assertEqual(outcome["observedMembers"], 0)
        self.assertIsNone(outcome["nowCount"])

    def test_expansion_does_not_observe_anchor_or_replace_members_on_later_eligibility(self):
        group, raw, benchmark = expansion_fixture()
        expected = evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)
        raw[ANCHOR] = [None] * 11
        raw["outsider"] = [None] * 11
        for bars in raw.values():
            for bar in bars:
                if bar is not None:
                    bar["baseEligibility"] = {"eligible": False, "reason": "historicalSt"}
        self.assertEqual(evaluate_peer_expansion(group, DAYS[60:], raw, benchmark), expected)

    def test_expansion_alignment_duplicate_members_and_correlations_rejected(self):
        group, raw, benchmark = expansion_fixture()
        for days in (DAYS[61:], DAYS[59:70], DAYS[60:65]+[DAYS[64]]+DAYS[66:], DAYS[60:]+["2099-01-01"]):
            self.assertEqual(evaluate_peer_expansion(group, days, raw, benchmark)["kind"], "unevaluable")
        duplicate = deepcopy(group); duplicate["members"][1] = duplicate["members"][0]
        self.assertEqual(evaluate_peer_expansion(duplicate, DAYS[60:], raw, benchmark)["reason"], "invalidFixedMembership")
        malformed = deepcopy(group); malformed["members"][1] = {}
        self.assertEqual(evaluate_peer_expansion(malformed, DAYS[60:], raw, benchmark)["reason"], "invalidFixedMembership")
        changed = deepcopy(group); changed["correlations"][changed["members"][0]] = .2
        self.assertEqual(evaluate_peer_expansion(changed, DAYS[60:], raw, benchmark)["reason"], "invalidFrozenCorrelations")

    def test_selection_keeps_full_parent_top5_then_masks_without_backfill(self):
        parents = parent_rows()
        kinds = ("expanded", "notExpanded", "unevaluable", "expanded", "notExpanded")
        outcomes = {r["symbol"]: outcome_for(r["symbol"], kind) for r, kind in zip(parents, kinds)}
        outcomes["rank6"] = outcome_for("rank6")
        result = select_peer_signals(T, parents, outcomes)
        self.assertEqual([s["rank"] for s in result["candidateSignals"]], [1, 4])
        self.assertEqual([s["rank"] for s in result["baselineSignals"]], [1, 2, 3, 4, 5])
        self.assertEqual([s["strategyId"] for s in result["signals"]], [CANDIDATE_ID]*2+[CONTROL_ID]*5)
        self.assertEqual([s["symbol"] for s in result["baselineSignals"]], [r["symbol"] for r in parents])
        self.assertTrue(all(d["baselineAdmitted"] for d in result["decisions"]))
        self.assertEqual([d["kind"] for d in result["decisions"]], list(kinds))
        self.assertEqual(result["classificationCounts"], {"expanded": 2, "notExpanded": 2, "unevaluable": 1})
        self.assertFalse(result["activeExclusionApplied"])
        self.assertEqual(select_peer_signals(T, [], {})["signals"], [])

    def test_missing_expansion_stays_baseline_and_contradictory_outcome_fails(self):
        parents = parent_rows(1)
        result = select_peer_signals(T, parents, {})
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(result["baselineCount"], 1)
        self.assertEqual(result["decisions"][0]["reason"], "missingPeerExpansion")
        for key, value in (("anchor", "wrong"), ("date", F), ("nowCount", 11), ("expanded", False), ("formationDate", T)):
            outcome = outcome_for(parents[0]["symbol"]); outcome[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                select_peer_signals(T, parents, {parents[0]["symbol"]: outcome})

    def test_selection_identity_order_duplicate_and_rank_six_rejected(self):
        original = parent_rows()
        cases = [list(reversed(original)), original+[deepcopy(original[0])], [deepcopy(original[0])]*2]
        for key, value in (("signalDate", F), ("rank", True), ("rank", 2), ("eligible", 1),
                           ("selectedTop5", False), ("strategyId", CANDIDATE_ID)):
            rows = deepcopy(original); rows[0][key] = value; cases.append(rows)
        for parents in cases:
            with self.assertRaises(ValueError):
                select_peer_signals(T, parents, {})

    def test_all_input_structures_immutable_and_returned_evidence_detached(self):
        raw, benchmark, eligibility = formation_fixture()
        original = deepcopy((raw, benchmark, eligibility))
        group = form_peer_group(ANCHOR, F, DAYS[:61], raw, benchmark, eligibility)
        self.assertEqual((raw, benchmark, eligibility), original)
        group["candidateEvaluations"][1]["eligibility"]["unknown"].append("changed")
        self.assertEqual((raw, benchmark, eligibility), original)
        group, raw, benchmark = expansion_fixture()
        original = deepcopy((group, raw, benchmark))
        result = evaluate_peer_expansion(group, DAYS[60:], raw, benchmark)
        result["members"].pop()
        self.assertEqual((group, raw, benchmark), original)
        parents = parent_rows(); outcomes = {r["symbol"]: outcome_for(r["symbol"]) for r in parents}
        original = deepcopy((parents, outcomes))
        selected = select_peer_signals(T, parents, outcomes)
        selected["baselineSignals"][0]["features"]["meanStrengthPercentile20"] = -1
        self.assertEqual((parents, outcomes), original)


if __name__ == "__main__":
    unittest.main()
