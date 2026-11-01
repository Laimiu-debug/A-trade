"""Synthetic boundaries for frozen-parent matching and account scheduling."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import tempfile
import unittest

from service.research_leader_amount_handoff import build_leader_amount_handoff
from service.research_leader_matched_anchor import build_leader_matched_anchor, STRATEGY_ID, _digest


def fixture(length=44, size=24):
    days = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(length)]
    securities = [{"symbol": "sz" + str(s + 1).zfill(6), "bars": [
        {"date": day, "open": 100., "high": 101., "low": 99., "close": 100.,
         "volume": 1_000_000, "amount": 100_000_000 if s == 0 or s >= 21 else 80_000_000}
        for day in days]} for s in range(size)]
    benchmark = deepcopy(securities[0]); benchmark["symbol"] = "sh000300"
    return {"priceBasisId": "raw-unadjusted", "calendar": days, "securities": securities, "benchmark": benchmark}


def event(data, at=30, count=20):
    day, symbol = data["calendar"][at], data["securities"][0]["symbol"]
    return {"eventId": "leaderLagFollowersV1:" + day + ":" + symbol, "formationDate": day,
            "peerFormationDate": data["calendar"][at - 10], "anchorSymbol": symbol,
            "originalLeaderRank": 1, "groupSha256": "a" * 64,
            "groupSummary": {"anchorSymbol": symbol, "peerFormationDate": data["calendar"][at - 10],
                "kind": "formed" if count == 20 else "incomplete", "requiredCount": 20, "selectedCount": count,
                "members": [{"symbol": s["symbol"], "frozenMemberRank": i + 1}
                            for i, s in enumerate(data["securities"][1:count + 1])]}}


def breakout(data, member=1, at=31, amount=150_000_000):
    data["securities"][member]["bars"][at].update(open=102., close=102., high=103., low=101., amount=amount)


def parent(data, events=None, last=38):
    return build_leader_amount_handoff(data, events or [event(data)],
        start=data["calendar"][30], end=data["calendar"][last])


def build(data, saved=None, events=None, last=38, **kwargs):
    saved = saved or parent(data, events, last)
    return build_leader_matched_anchor(data, saved["formations"], saved["opportunities"],
        start=data["calendar"][30], end=data["calendar"][last], **kwargs)


class MatchedAnchorTests(unittest.TestCase):
    def test_excludes_full_group_and_exact_tie_uses_symbol(self):
        data = fixture(); breakout(data); logs = []
        out = build(data, on_match_evidence=logs.append)
        self.assertEqual(out["matches"][0]["pseudoSymbol"], data["securities"][21]["symbol"])
        self.assertEqual(out["matches"][0]["excludedCandidateCount"], 21)
        self.assertEqual(out["matches"][0]["eligibleCandidateCount"], 3)
        self.assertEqual(out["matches"][0]["windowDates20"], data["calendar"][10:30])
        self.assertEqual(out["matches"][0]["candidateEvidenceSha256"], _digest(logs[0]["candidates"]))

    def test_log_distance_is_not_absolute_amount_distance(self):
        data = fixture(size=23)
        for i, value in ((21, 50_000_000), (22, 180_000_000)):
            for bar in data["securities"][i]["bars"]: bar["amount"] = value
        self.assertEqual(build(data)["matches"][0]["pseudoSymbol"], data["securities"][22]["symbol"])

    def test_a_and_later_values_never_choose_a_different_pseudo(self):
        data = fixture(); breakout(data); before = build(data)
        data["securities"][21]["bars"][30]["amount"] = None
        data["securities"][21]["bars"][31]["close"] = -1
        after = build(data)
        self.assertEqual(before["matches"], after["matches"])
        self.assertEqual(after["opportunities"][0]["commonStatus"], "initialUnknown")
        self.assertEqual(after["matches"][0]["pseudoSymbol"], data["securities"][21]["symbol"])

    def test_anchor_scale_missing_is_unavailable_even_with_eligible_candidates(self):
        data = fixture(); breakout(data); data["securities"][0]["bars"][10]["amount"] = None
        out = build(data)
        self.assertEqual(out["matches"][0]["matchStatus"], "anchorScaleUnknown")
        self.assertEqual(out["matches"][0]["eligibleCandidateCount"], 3)
        self.assertEqual(out["opportunities"][0]["commonStatus"], "matchUnavailable")
        self.assertTrue(all(out["opportunities"][0][p] is None for p in out["modes"]))

    def test_candidate_scale_requires_all_exact_slots_positive_amount_and_price(self):
        for corruption in ("missing", "zero", "halt", "ohlc"):
            data = fixture(size=22); breakout(data)
            security = data["securities"][21]
            if corruption == "missing": del security["bars"][10]
            elif corruption == "zero": security["bars"][10]["amount"] = 0
            elif corruption == "halt": security["tradeStatusByDate"] = {data["calendar"][10]: 0.0}
            else: security["bars"][10]["high"] = 1
            with self.subTest(corruption=corruption):
                out = build(data)
                self.assertEqual(out["matches"][0]["matchStatus"], "noEligibleCandidate")
                self.assertEqual(out["opportunities"][0]["commonStatus"], "matchUnavailable")

    def test_initial_above_precedes_later_unknown_without_substitution(self):
        data = fixture(); breakout(data)
        data["securities"][21]["bars"][30]["amount"] = 1
        data["securities"][21]["bars"][31]["amount"] = None
        out = build(data); row = out["opportunities"][0]
        self.assertEqual(row["commonStatus"], "initialAbove")
        self.assertEqual(row["flags"]["initialAboveAnchors"], ["pseudo"])
        self.assertTrue(row["flags"]["decisionUnknownReasons"])
        self.assertTrue(all(row[p] is False for p in out["modes"]))
        self.assertEqual(row["pseudoSymbol"], data["securities"][21]["symbol"])

    def test_initial_unknown_precedes_decision_unknown_and_never_is_false(self):
        data = fixture(); breakout(data); data["securities"][21]["bars"][30]["amount"] = None
        out = build(data); row = out["opportunities"][0]
        self.assertEqual(row["commonStatus"], "initialUnknown")
        self.assertTrue(row["flags"]["initialUnknownReasons"])
        self.assertTrue(row["flags"]["decisionUnknownReasons"])
        self.assertTrue(all(row[p] is None for p in out["modes"]))

    def test_decision_unknown_is_shared_even_when_real_cross_known(self):
        data = fixture(); breakout(data); data["securities"][21]["bars"][31]["amount"] = None
        row = build(data)["opportunities"][0]
        self.assertEqual(row["commonStatus"], "decisionUnknown")
        self.assertTrue(row["crossing"]["real"]); self.assertIsNone(row["crossing"]["pseudo"])
        self.assertIsNone(row["control"])

    def test_all_four_overlap_cells_on_one_immutable_clock(self):
        for real_amount, pseudo_amount, overlap in ((100, 100, "both"), (100, 200, "realOnly"),
                                                   (200, 100, "pseudoOnly"), (200, 200, "neither")):
            data = fixture(); breakout(data)
            data["securities"][0]["bars"][31]["amount"] = real_amount * 1_000_000
            data["securities"][21]["bars"][31]["amount"] = pseudo_amount * 1_000_000
            with self.subTest(overlap=overlap):
                out = build(data); row = out["opportunities"][0]
                self.assertEqual(row["overlap"], overlap); self.assertTrue(row["control"])
                self.assertEqual(row["signalDate"], data["calendar"][31])
                self.assertEqual(out["counts"]["overlapCounts"][overlap], 1)

    def test_initial_previous_equality_pass_current_equality_does_not_cross(self):
        data = fixture()
        for bar in data["securities"][1]["bars"]: bar["amount"] = 100_000_000
        breakout(data, amount=100_000_000)
        row = build(data)["opportunities"][0]
        self.assertEqual(row["commonStatus"], "commonKnown"); self.assertEqual(row["overlap"], "neither")
        breakout(data, amount=100_000_001)
        self.assertEqual(build(data)["opportunities"][0]["overlap"], "both")

    def test_formations_without_top5_still_matched_and_other_status_inherited(self):
        data = fixture()
        out = build(data, events=[event(data), event(data, at=31), event(data, at=36, count=19)], last=41)
        self.assertEqual([r["matchStatus"] for r in out["matches"]], ["matched", "inheritedOther", "inheritedOther"])
        self.assertEqual(out["counts"]["originalTop5"], 0)
        self.assertEqual(out["counts"]["parentFormations"], 3)

    def test_one_pseudo_reused_across_formations_is_not_disallowed(self):
        data = fixture(); out = build(data, events=[event(data), event(data, at=36)], last=41)
        self.assertEqual(out["counts"]["matched"], 2)
        self.assertEqual(out["counts"]["distinctPseudoAnchors"], 1)
        self.assertEqual(out["counts"]["reusedPseudoAssignments"], 1)

    def test_parent_rank_holes_preserved_without_sixth_replacement(self):
        data = fixture()
        for member in range(1, 7): breakout(data, member=member)
        data["securities"][1]["bars"][31]["amount"] = None
        out = build(data)
        self.assertEqual([r["originalRank"] for r in out["opportunities"]], [1, 2, 3, 4, 5])
        self.assertEqual([r["originalRank"] for r in out["schedules"]["control"]], [2, 3, 4, 5])
        self.assertEqual(out["counts"]["opportunities"], 5)

    def test_identity_calendar_and_parent_numeric_corruption_rejected(self):
        data = fixture(); breakout(data); saved = parent(data)
        for corruption in ("calendar", "duplicate", "rank", "formation", "member", "amount"):
            d, s = deepcopy(data), deepcopy(saved)
            if corruption == "calendar": d["calendar"][11] = d["calendar"][10]
            elif corruption == "duplicate": s["opportunities"] *= 2
            elif corruption == "rank": s["opportunities"][0]["originalRank"] = 6
            elif corruption == "formation": s["formations"][0]["peerFormationDate"] = d["calendar"][21]
            elif corruption == "member": s["formations"][0]["members"][0]["frozenMemberRank"] = 2
            else: s["opportunities"][0]["amountEvidence"]["current"]["member"]["total"] += 1
            with self.subTest(corruption=corruption), self.assertRaises(ValueError): build(d, s)

    def test_future_numeric_and_events_do_not_change_existing_prefix(self):
        data = fixture(); breakout(data); saved = parent(data, [event(data), event(data, at=36)], last=41)
        before = build(data, saved, last=35)
        changed = deepcopy(data)
        for security in changed["securities"]:
            for bar in security["bars"][36:]: bar.update(amount=float("nan"), close=-1)
        self.assertEqual(before, build(changed, saved, last=35))
        after = build(data, saved, last=41)
        self.assertEqual(before["opportunities"], [r for r in after["opportunities"] if r["decisionDate"] <= data["calendar"][35]])
        self.assertEqual(before["matches"], [r for r in after["matches"] if r["formationDate"] <= data["calendar"][35]])

    def test_input_and_callback_separation_five_field_signals_and_counts(self):
        data = fixture(); breakout(data); saved = parent(data); original = deepcopy((data, saved)); logs = []
        out = build(data, saved, on_match_evidence=logs.append)
        self.assertEqual((data, saved), original)
        logs[0]["candidates"].clear(); self.assertEqual(out["matches"][0]["candidateCount"], 24)
        for mode in out["modes"]:
            signal = out["schedules"][mode][0]
            self.assertEqual(set(signal), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})
            self.assertEqual(signal["strategyId"], STRATEGY_ID)
        self.assertNotEqual(out["opportunities"][0]["signalId"], out["opportunities"][0]["parentOpportunityId"])
        self.assertEqual(sum(out["counts"]["commonStatusCounts"].values()), out["counts"]["opportunities"])
        self.assertEqual(sum(out["counts"]["overlapCounts"].values()), out["counts"]["commonStatusCounts"]["commonKnown"])


def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/run-leader-matched-anchor.py"
    spec = importlib.util.spec_from_file_location("matched_runner_tests", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class RunnerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.runner = runner()

    def test_only_strategy_identity_changes_in_config(self):
        config = {"strategies": [{"id": "leaderAmountHandoffV1", "params": {"keep": 60}}],
                  "allocation": {"strategyIds": ["leaderAmountHandoffV1"]}, "stopLossPct": .05,
                  "trailingStopPct": .1, "preselected": {"strategyId": "leaderAmountHandoffV1"}}
        before = deepcopy(config); out = self.runner.config_for(config)
        self.assertEqual(config, before); self.assertEqual(out["preselected"]["strategyId"], STRATEGY_ID)
        self.assertEqual(out["stopLossPct"], .05); self.assertEqual(out["trailingStopPct"], .1)
        self.assertEqual(self.runner.PERIODS, ("2022-2023", "2024-2025"))

    def test_parent_receipt_requires_exact_verified_group_and_source(self):
        r = self.runner; source = {"accountData": {"path": "raw", "sha256": "h"}, "accountDatasetDigest": "d"}
        group = {"period": "p", "board": "b", **source, "inputReceipt": {"path": "receipt"},
                 "formations": {"path": "forms"}, "opportunities": {"path": "opps"}, "counts": {"sourceEvents": 2, "opportunities": 1}}
        plan = {"sourceData": {"p": {"b": source}}}; proof = {"preparedGroups": [deepcopy(group)]}
        got = r.validate_parent_binding(plan, group, proof, "p", "b", source)
        self.assertEqual(got["sourceFormationCount"], 2); self.assertEqual(got["sourceOpportunityCount"], 1)
        proof["preparedGroups"][0]["opportunities"]["path"] = "wrong"
        with self.assertRaises(ValueError): r.validate_parent_binding(plan, group, proof, "p", "b", source)
        with self.assertRaises(ValueError): r.validate_parent_binding(plan, group, {"preparedGroups": [group]}, "p", "b", {**source, "accountDatasetDigest": "other"})

    def test_atomic_receipts_do_not_overwrite_or_restart_partial_account(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proof.json"
            self.assertTrue(self.runner.atomic_once(path, {"first": 1}))
            self.assertFalse(self.runner.atomic_once(path, {"other": 2}))
            self.assertEqual(self.runner.read(path), {"first": 1})
            (Path(folder) / "control.result.json.gz").write_bytes(b"partial")
            with self.assertRaises(ValueError): self.runner.OLD.require_unstarted_group(folder)

    def test_all_eighteen_schedules_and_36_bindings_must_precede_accounts(self):
        r = self.runner; groups = []; sources = {p: {} for p in r.PERIODS}; parent_data = deepcopy(sources)
        for period in r.PERIODS:
            for board in r.BOARDS:
                source = {"accountData": {"path": period + board, "sha256": "x"}, "accountDatasetDigest": "d"}
                inherited = {"formations": {"path": "forms"}, "opportunities": {"path": "opps"}, "inputCompletion": {"path": "receipt"}}
                sources[period][board] = source; parent_data[period][board] = inherited
                groups.append({"period": period, "board": board, **source, "parentFormations": inherited["formations"],
                    "parentOpportunities": inherited["opportunities"], "parentInputReceipt": inherited["inputCompletion"],
                    "schedules": {p: {} for p in r.POLICIES}})
        hashes = dict(plan="p", tests="t", completion="c", verifier="v")
        plan = {"sourcePins": {}, "sourceData": sources, "parentData": parent_data}
        tests = {"passed": True, "planSha256": "p", "sourcePins": {}, "allChecksBeforeFirstNewAccount": True,
                 "completedAt": "2026-01-01T00:00:00+00:00"}
        completion = {"planSha256": "p", "preAccountTestsSha256": "t", "allSchedulesFrozenBeforeAccounts": True,
                      "newAccountsExecuted": 0, "groups": groups, "counts": {"preparedSchedules": 18, "accountScheduleBindings": 36},
                      "completedAt": "2026-01-01T00:00:01+00:00"}
        proof = {"passed": True, "planSha256": "p", "inputCompletionSha256": "c", "preAccountTestsSha256": "t", "verifierSha256": "v"}
        r.validate_prerequisites(plan, completion, proof, tests, hashes)
        for change in ("source", "schedule", "parent", "proof", "chronology", "count"):
            c, v = deepcopy(completion), deepcopy(proof)
            if change == "source": c["groups"].pop()
            elif change == "schedule": c["groups"][0]["schedules"].pop("pseudoCross")
            elif change == "parent": c["groups"][0]["parentOpportunities"] = {}
            elif change == "proof": v["inputCompletionSha256"] = "wrong"
            elif change == "count": c["counts"]["accountScheduleBindings"] = 24
            else: c["completedAt"] = "2025-12-31T00:00:00+00:00"
            with self.subTest(change=change), self.assertRaises(ValueError): r.validate_prerequisites(plan, c, v, tests, hashes)

    def test_unchanged_t_signal_buys_next_open_and_preserves_fee_type(self):
        from tests.test_research_fixed_signal_account import settings
        from service.research_account_fees import LegacyFeePolicy
        data = fixture(); breakout(data); signals = build(data)["schedules"]["control"]; original = deepcopy(data)
        config = settings(data, startDate=data["calendar"][30], initialCapital=500000,
            maxPositions=5, maxStockWeight=.2, stopLossPct=.05, trailingStopPct=.1,
            strategies=[{"id": STRATEGY_ID, "kind": "momentum", "pool": "base"}],
            allocation={"mode": "single", "strategyIds": [STRATEGY_ID]})
        result, _ = self.runner.execute_account(data, signals, config, "trail10", LegacyFeePolicy())
        buys = [r for r in result["trades"] if r["side"] == "buy"]
        self.assertEqual(len(buys), 1); self.assertEqual(buys[0]["date"], data["calendar"][32]); self.assertEqual(data, original)
        plan = {"feePolicy": {"commissionRate": .0003, "minCommission": 5, "commissionIncludesTransfer": False,
            "stampDutyOverride": None, "securityType": "ordinary-a-share", "tradingMode": "exchange-auction"}}
        config = {"commissionRate": .0003, "minCommission": 5., "stampDutyRate": None, "transferFeeRate": .00001}
        self.assertIs(type(self.runner.fee_policy("2022-2023", plan).settings(config)["minCommission"]), int)


if __name__ == "__main__": unittest.main()
