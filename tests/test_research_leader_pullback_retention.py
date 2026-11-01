"""Synthetic boundaries for first-decline/member-retention and account scheduling."""
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import tempfile
import unittest

from service.research_leader_amount_handoff import build_leader_amount_handoff
from service.research_leader_pullback_retention import build_leader_pullback_retention, first_down, retention_path, STRATEGY_ID, _digest, _Series


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
    return build_leader_pullback_retention(data, saved["formations"], saved["opportunities"],
        start=data["calendar"][30], end=data["calendar"][last], **kwargs)


def set_price(data, security, at, close):
    data["securities"][security]["bars"][at].update(open=close, high=close+1, low=close-1, close=close)


def series(data, security=0):
    return _Series(data["securities"][security], data["calendar"], data["calendar"][-1])


class PullbackRetentionTests(unittest.TestCase):
    def test_proven_prior_decline_and_one_day_retention_clock(self):
        data = fixture(); set_price(data, 0, 31, 99); breakout(data, at=32)
        row = build(data)["opportunities"][0]
        self.assertEqual(row["commonStatus"], "commonKnown")
        self.assertTrue(row["control"]); self.assertTrue(row["retained"])
        self.assertEqual(row["firstDown"]["firstDownDate"], data["calendar"][31])
        self.assertEqual(row["retention"]["referenceDate"], data["calendar"][30])
        self.assertEqual(row["retention"]["windowDates"], data["calendar"][30:32])
        self.assertEqual(row["diagnostics"], {"formationToDownSlots": 1, "downToDecisionSlots": 1, "retentionObservationCount": 1})

    def test_same_day_down_is_known_nonadmission_not_prior(self):
        data = fixture(); set_price(data, 0, 31, 99); breakout(data)
        row = build(data)["opportunities"][0]
        self.assertEqual(row["commonStatus"], "sameDayDown")
        self.assertIs(row["control"], False); self.assertIs(row["retained"], False)
        self.assertEqual(row["retention"]["status"], "notApplicable")

    def test_equal_closes_are_not_down_and_no_down_is_known(self):
        data = fixture(); breakout(data, at=35)
        row = build(data)["opportunities"][0]
        self.assertEqual(row["commonStatus"], "noDownThroughT")
        self.assertEqual(len(row["firstDown"]["evaluatedPairs"]), 5)
        self.assertTrue(all(r["down"] is False for r in row["firstDown"]["evaluatedPairs"]))
        self.assertIs(row["control"], False)

    def test_unknown_prefix_cannot_be_skipped_for_later_decline(self):
        data = fixture(); breakout(data, at=34); data["securities"][0]["bars"][31]["volume"] = 0
        set_price(data, 0, 32, 99)
        row = build(data)["opportunities"][0]
        self.assertEqual(row["commonStatus"], "firstDownUnknown")
        self.assertIsNone(row["firstDown"]["firstDownDate"])
        self.assertEqual(row["firstDown"]["unknownDate"], data["calendar"][31])
        self.assertEqual(len(row["firstDown"]["evaluatedPairs"]), 1)
        self.assertIsNone(row["control"]); self.assertIsNone(row["retained"])

    def test_anchor_unknown_after_proven_down_does_not_cancel_or_require_amount(self):
        data = fixture(); set_price(data, 0, 31, 99); breakout(data, at=34)
        data["securities"][0]["bars"][32]["close"] = None
        data["securities"][1]["bars"][34]["amount"] = None
        row = build(data)["opportunities"][0]
        self.assertEqual(row["parentAmountStatus"], "unknown")
        self.assertEqual(row["firstDown"]["windowDates"], data["calendar"][30:32])
        self.assertEqual(row["commonStatus"], "commonKnown"); self.assertTrue(row["retained"])

    def test_opportunity_prefix_never_borrows_later_down(self):
        data = fixture(); breakout(data, member=1, at=31); breakout(data, member=2, at=33)
        set_price(data, 0, 32, 99)
        rows = build(data)["opportunities"]
        self.assertEqual([r["commonStatus"] for r in rows], ["noDownThroughT", "commonKnown"])
        self.assertIsNone(rows[0]["firstDown"]["firstDownDate"])
        self.assertEqual(rows[1]["firstDown"]["firstDownDate"], data["calendar"][32])

    def test_fixed_reference_equality_passes_breach_then_recovery_stays_failed(self):
        data = fixture(); set_price(data, 0, 31, 99); set_price(data, 1, 31, 99); breakout(data, at=33)
        row = build(data)["opportunities"][0]
        self.assertTrue(row["control"]); self.assertFalse(row["retained"])
        self.assertEqual(row["retention"]["referenceClose"], 100.)
        self.assertEqual(row["retention"]["breachDates"], [data["calendar"][31]])
        self.assertEqual([r["retained"] for r in row["retention"]["observations"]], [False, True])
        self.assertNotIn(data["calendar"][33], row["retention"]["windowDates"])

    def test_unknown_path_preserves_independent_known_breach(self):
        data = fixture(); set_price(data, 1, 31, 99); data["securities"][1]["bars"][32]["volume"] = 0
        value = retention_path(series(data, 1), data["calendar"], 31, 34)
        self.assertEqual(value["status"], "unknown"); self.assertIsNone(value["retained"])
        self.assertEqual(value["breachDates"], [data["calendar"][31]])
        self.assertEqual(len(value["observations"]), 3)
        self.assertEqual(value["observations"][1]["close"], 100.)
        self.assertIsNone(value["observations"][1]["breached"])

    def test_unknown_reference_does_not_impute_breach_from_readable_close(self):
        data = fixture(); data["securities"][1]["bars"][30]["volume"] = 0; set_price(data, 1, 31, 99)
        value = retention_path(series(data, 1), data["calendar"], 31, 33)
        self.assertEqual(value["referenceClose"], 100.)
        self.assertIsNotNone(value["referenceReason"]); self.assertEqual(value["breachDates"], [])
        self.assertTrue(all(r["breached"] is None for r in value["observations"]))

    def test_first_pair_quality_preserves_zero_halt_and_missing_slots(self):
        for kind in ("missing", "halt", "envelope", "nonfinite"):
            data = fixture(); breakout(data, at=34)
            stock = data["securities"][0]
            if kind == "missing": del stock["bars"][31]
            elif kind == "halt": stock["tradeStatusByDate"] = {data["calendar"][31]: 0.0}
            elif kind == "envelope": stock["bars"][31]["high"] = 1
            else: stock["bars"][31]["close"] = float("nan")
            with self.subTest(kind=kind):
                row = build(data)["opportunities"][0]
                self.assertEqual(row["commonStatus"], "firstDownUnknown")
                self.assertEqual(row["firstDown"]["unknownDate"], data["calendar"][31])

    def test_initial_amount_parent_selection_remains_inherited(self):
        data = fixture(); data["securities"][1]["bars"][30]["amount"] = 1e9; breakout(data, at=33)
        out = build(data)
        self.assertEqual(out["counts"]["originalTop5"], 0)
        self.assertEqual(out["counts"]["parentFormations"], 1)
        self.assertEqual(len(out["formations"][0]["members"]), 20)

    def test_retention_rank_hole_never_backfills_sixth_owner(self):
        data = fixture(); set_price(data, 0, 31, 99); set_price(data, 1, 31, 99)
        for member in range(1, 7): breakout(data, member=member, at=33)
        out = build(data)
        self.assertEqual([s["originalRank"] for s in out["schedules"]["control"]], [1, 2, 3, 4, 5])
        self.assertEqual([s["originalRank"] for s in out["schedules"]["retained"]], [2, 3, 4, 5])
        self.assertEqual(len(out["opportunities"]), 5)

    def test_future_tail_and_events_leave_existing_prefix_unchanged(self):
        data = fixture(); set_price(data, 0, 31, 99); breakout(data, at=33)
        saved = parent(data, [event(data), event(data, at=36)], last=41)
        before = build(data, saved, last=35); changed = deepcopy(data)
        for stock in changed["securities"]:
            for bar in stock["bars"][36:]: bar.update(close=-1, amount=float("nan"))
        self.assertEqual(before, build(changed, saved, last=35))
        after = build(data, saved, last=41)
        self.assertEqual(before["opportunities"], [r for r in after["opportunities"] if r["decisionDate"] <= data["calendar"][35]])

    def test_parent_raw_breakout_and_identity_corruption_rejected(self):
        data = fixture(); breakout(data, at=33); saved = parent(data)
        for corruption in ("raw", "priceEvidence", "rank", "duplicate", "F", "calendar"):
            d, s = deepcopy(data), deepcopy(saved)
            if corruption == "raw": d["securities"][1]["bars"][32]["volume"] = 0
            elif corruption == "priceEvidence": s["opportunities"][0]["priceEvidence"]["prior5High"] += 1
            elif corruption == "rank": s["opportunities"][0]["originalRank"] = 6
            elif corruption == "duplicate": s["opportunities"] *= 2
            elif corruption == "F": s["formations"][0]["peerFormationDate"] = d["calendar"][21]
            else: d["calendar"][10] = d["calendar"][9]
            with self.subTest(corruption=corruption), self.assertRaises(ValueError): build(d, s)

    def test_final_day_signal_preserved_without_assuming_next_open(self):
        data = fixture(); set_price(data, 0, 31, 99); breakout(data, at=32)
        out = build(data, last=32)
        self.assertEqual(out["schedules"]["retained"][0]["signalDate"], data["calendar"][32])
        self.assertEqual(out["calendar"][-1], data["calendar"][32])

    def test_input_separation_and_full_status_conservation(self):
        data = fixture(); set_price(data, 0, 31, 99); breakout(data, at=33)
        saved = parent(data, [event(data), event(data, at=31)]); before = deepcopy((data, saved))
        out = build(data, saved); self.assertEqual((data, saved), before)
        self.assertEqual(out["counts"]["formed"], 1); self.assertEqual(out["counts"]["inheritedOther"], 1)
        self.assertEqual(out["counts"]["originalTop5"], sum(out["counts"]["commonStatusCounts"].values()))
        self.assertEqual(out["counts"]["commonStatusCounts"]["commonKnown"], out["counts"]["retainedKnown"] + out["counts"]["lostKnown"])
        for mode in out["modes"]:
            signal = out["schedules"][mode][0]
            self.assertEqual(set(signal), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})
            self.assertEqual(signal["strategyId"], STRATEGY_ID)
        out["formations"][0]["members"].clear(); self.assertEqual(len(saved["formations"][0]["members"]), 20)


def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/run-leader-pullback-retention.py"
    spec = importlib.util.spec_from_file_location("pullback_runner_tests", path)
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

    def test_all_twelve_schedules_and_24_bindings_must_precede_accounts(self):
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
                      "newAccountsExecuted": 0, "groups": groups, "counts": {"preparedSchedules": 12, "accountScheduleBindings": 24},
                      "completedAt": "2026-01-01T00:00:01+00:00"}
        proof = {"passed": True, "planSha256": "p", "inputCompletionSha256": "c", "preAccountTestsSha256": "t", "verifierSha256": "v"}
        r.validate_prerequisites(plan, completion, proof, tests, hashes)
        for change in ("source", "schedule", "parent", "proof", "chronology", "count"):
            c, v = deepcopy(completion), deepcopy(proof)
            if change == "source": c["groups"].pop()
            elif change == "schedule": c["groups"][0]["schedules"].pop("retained")
            elif change == "parent": c["groups"][0]["parentOpportunities"] = {}
            elif change == "proof": v["inputCompletionSha256"] = "wrong"
            elif change == "count": c["counts"]["accountScheduleBindings"] = 36
            else: c["completedAt"] = "2025-12-31T00:00:00+00:00"
            with self.subTest(change=change), self.assertRaises(ValueError): r.validate_prerequisites(plan, c, v, tests, hashes)

    def test_unchanged_t_signal_buys_next_open_and_preserves_fee_type(self):
        from tests.test_research_fixed_signal_account import settings
        from service.research_account_fees import LegacyFeePolicy
        data = fixture(); set_price(data, 0, 31, 99); breakout(data, at=32); signals = build(data)["schedules"]["control"]; original = deepcopy(data)
        config = settings(data, startDate=data["calendar"][30], initialCapital=500000,
            maxPositions=5, maxStockWeight=.2, stopLossPct=.05, trailingStopPct=.1,
            strategies=[{"id": STRATEGY_ID, "kind": "momentum", "pool": "base"}],
            allocation={"mode": "single", "strategyIds": [STRATEGY_ID]})
        result, _ = self.runner.execute_account(data, signals, config, "trail10", LegacyFeePolicy())
        buys = [r for r in result["trades"] if r["side"] == "buy"]
        self.assertEqual(len(buys), 1); self.assertEqual(buys[0]["date"], data["calendar"][33]); self.assertEqual(data, original)
        plan = {"feePolicy": {"commissionRate": .0003, "minCommission": 5, "commissionIncludesTransfer": False,
            "stampDutyOverride": None, "securityType": "ordinary-a-share", "tradingMode": "exchange-auction"}}
        config = {"commissionRate": .0003, "minCommission": 5., "stampDutyRate": None, "transferFeeRate": .00001}
        self.assertIs(type(self.runner.fee_policy("2022-2023", plan).settings(config)["minCommission"]), int)


if __name__ == "__main__": unittest.main()
