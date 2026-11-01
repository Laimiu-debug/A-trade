from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import tempfile
import unittest

from service.research_leader_amount_handoff import build_leader_amount_handoff, STRATEGY_ID, _digest


def fixture(length=44, size=22):
    days = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(length)]
    securities = [{"symbol": "sz" + str(s + 1).zfill(6), "bars": [
        {"date": day, "open": 100., "high": 101., "low": 99., "close": 100.,
         "volume": 1_000_000, "amount": 100_000_000 if s in (0, 21) else 80_000_000}
        for day in days]} for s in range(size)]
    benchmark = deepcopy(securities[0]); benchmark["symbol"] = "sh000300"
    return {"priceBasisId": "raw-unadjusted", "calendar": days, "securities": securities, "benchmark": benchmark}


def event(data, at=30, anchor=0, rank=1, count=20):
    day, symbol = data["calendar"][at], data["securities"][anchor]["symbol"]
    peers = [s["symbol"] for s in data["securities"] if s["symbol"] != symbol][:count]
    return {"eventId": "leaderLagFollowersV1:" + day + ":" + symbol, "formationDate": day,
            "peerFormationDate": data["calendar"][at - 10], "anchorSymbol": symbol,
            "originalLeaderRank": rank, "groupSha256": "a" * 64,
            "groupSummary": {"anchorSymbol": symbol, "peerFormationDate": data["calendar"][at - 10],
                             "kind": "formed" if count == 20 else "incomplete", "requiredCount": 20,
                             "selectedCount": count, "members": [{"symbol": p, "frozenMemberRank": i + 1,
                             "correlation60": .5, "rawCorrelation60": .5} for i, p in enumerate(peers)]}}


def breakout(data, member=1, at=31, amount=150_000_000, close=102.):
    data["securities"][member]["bars"][at].update(open=close, close=close, high=close + 1,
                                                   low=close - 1, amount=amount)


def build(data, events=None, last=38, **kwargs):
    return build_leader_amount_handoff(data, [event(data)] if events is None else events,
                                      start=data["calendar"][30], end=data["calendar"][last], **kwargs)


class HandoffTests(unittest.TestCase):
    def test_sum_crossover_and_first_close_clock(self):
        data = fixture(); breakout(data)
        result = build(data); opp = result["opportunities"][0]
        self.assertEqual(result["schedules"]["control"], result["schedules"]["handoff"])
        self.assertEqual(opp["amountEvidence"]["previous"]["member"]["total"], 240_000_000)
        self.assertEqual(opp["amountEvidence"]["current"]["member"]["total"], 310_000_000)
        self.assertEqual(opp["formationDate"], data["calendar"][30])
        self.assertEqual(opp["decisionDate"], data["calendar"][31])
        self.assertEqual(opp["diagnostics"]["member"]["ownRelativeAmount"], 310 / 240)
        self.assertEqual(set(opp["signals"]["control"]), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})

    def test_initial_and_previous_equal_pass_current_equal_fails(self):
        data = fixture()
        for bar in data["securities"][1]["bars"]: bar["amount"] = 100_000_000
        breakout(data, amount=100_000_000)
        out = build(data)
        self.assertEqual(out["memberEpisodes"][0]["initialStatus"], "observing")
        self.assertIs(out["opportunities"][0]["handoff"], False)
        breakout(data, amount=100_000_001)
        self.assertIs(build(data)["opportunities"][0]["handoff"], True)

    def test_earlier_cross_is_not_a_current_cross(self):
        data = fixture(); data["securities"][1]["bars"][31]["amount"] = 180_000_000
        breakout(data, at=32, amount=170_000_000)
        opp = build(data)["opportunities"][0]
        self.assertGreater(opp["amountEvidence"]["previous"]["member"]["total"], 300_000_000)
        self.assertIs(opp["handoff"], False)

    def test_first_breakout_consumed_before_a_later_handoff(self):
        data = fixture(); breakout(data, amount=80_000_000); breakout(data, at=32, amount=500_000_000, close=105)
        out = build(data)
        self.assertEqual(len(out["opportunities"]), 1)
        self.assertEqual(out["schedules"]["handoff"], [])
        self.assertEqual(out["memberEpisodes"][0]["terminalDate"], data["calendar"][31])

    def test_unknown_price_terminates_without_compressing_missing_slot(self):
        data = fixture(); del data["securities"][1]["bars"][31]; breakout(data, member=2, at=32)
        rows = []; out = build(data, on_evidence=rows.append)
        self.assertEqual(out["memberEpisodes"][0]["terminalStatus"], "priceUnknown")
        self.assertEqual(out["memberEpisodes"][0]["observedSessions"], 1)
        self.assertEqual(rows[1]["memberObservations"][0]["priceEvidence"]["reason"], "missingAlignedRawBar")

    def test_amount_unknown_during_observation_does_not_cancel_price(self):
        data = fixture(); data["securities"][1]["bars"][31]["amount"] = None
        breakout(data, at=35)
        opp = build(data)["opportunities"][0]
        self.assertEqual(opp["decisionDate"], data["calendar"][35])
        self.assertIs(opp["control"], True)

    def test_joint_unknown_consumes_and_never_becomes_known_failure(self):
        data = fixture(); breakout(data); data["securities"][0]["bars"][31]["amount"] = None
        breakout(data, at=32, amount=500_000_000, close=105)
        out = build(data); opp = out["opportunities"][0]
        self.assertEqual(out["counts"]["firstBreakout"], 1)
        self.assertIsNone(opp["control"]); self.assertIsNone(opp["handoff"])
        self.assertEqual(opp["signals"], {"control": None, "handoff": None})

    def test_initial_unknown_and_above_are_terminal(self):
        for value, terminal in ((0, "initialUnknown"), (1e9, "initialAbove")):
            data = fixture(); data["securities"][1]["bars"][30]["amount"] = value; breakout(data)
            out = build(data)
            self.assertEqual(out["memberEpisodes"][0]["terminalStatus"], terminal)
            self.assertEqual(out["memberEpisodes"][0]["observedSessions"], 0)
            self.assertEqual(out["counts"]["firstBreakout"], 0)

    def test_incomplete_group_owns_lock_and_suppressed_events_do_not_extend(self):
        data = fixture()
        events = [event(data, 30, count=19), event(data, 31), event(data, 35), event(data, 36)]
        out = build(data, events, last=41)
        self.assertEqual([f["status"] for f in out["formations"]], ["notFormed", "locked", "locked", "formed"])
        self.assertEqual([f["lockThroughIndex"] for f in out["formations"]], [35, 35, 35, 41])
        self.assertEqual(out["counts"]["memberEpisodes"], 20)

    def test_fifth_session_assessed_before_expiry_and_short_end_censored(self):
        data = fixture(); breakout(data, at=35)
        out = build(data)
        self.assertEqual(out["memberEpisodes"][0]["terminalStatus"], "firstBreakout")
        self.assertEqual(out["counts"]["expired"], 19)
        before = build(data, last=34)
        self.assertEqual(before["counts"]["censored"], 20)
        self.assertEqual(before["counts"]["firstBreakout"], 0)

    def test_duplicate_owner_is_not_chosen_by_amount_gate(self):
        data = fixture(); breakout(data)
        data["securities"][0]["bars"][31]["amount"] = None
        events = [event(data, anchor=0), event(data, anchor=21, rank=2)]
        out = build(data, events)
        raw = [r for r in out["rawConfirmations"] if r["memberSymbol"] == data["securities"][1]["symbol"]]
        self.assertEqual([r["ownershipStatus"] for r in raw], ["owner", "duplicateSuppressed"])
        self.assertEqual(raw[1]["amountEvidence"]["status"], "known")
        self.assertEqual(out["counts"]["opportunities"], 1)
        self.assertEqual(out["schedules"]["control"], [])

    def test_unknown_top_slot_does_not_backfill_rank_six(self):
        data = fixture()
        for member in range(1, 7): breakout(data, member=member)
        data["securities"][1]["bars"][31]["amount"] = None
        out = build(data)
        self.assertEqual(out["counts"]["rankExcluded"], 1)
        self.assertEqual([s["originalRank"] for s in out["schedules"]["control"]], [2, 3, 4, 5])
        self.assertEqual(len(out["opportunities"]), 5)

    def test_price_equality_is_not_breakout(self):
        data = fixture(); breakout(data, close=101.)
        out = build(data)
        self.assertEqual(out["counts"]["firstBreakout"], 0)
        self.assertEqual(out["counts"]["expired"], 20)

    def test_known_suspension_and_nonfinite_amount_are_unknown(self):
        data = fixture(); breakout(data)
        data["securities"][1]["tradeStatusByDate"] = {data["calendar"][31]: 0.0}
        self.assertEqual(build(data)["memberEpisodes"][0]["terminalStatus"], "priceUnknown")
        data = fixture(); breakout(data, amount=float("nan"))
        self.assertEqual(build(data)["counts"]["jointUnknown"], 1)

    def test_final_session_signal_kept_and_unresolved_members_censored(self):
        data = fixture(); breakout(data)
        out = build(data, last=31)
        self.assertEqual(out["schedules"]["handoff"][0]["signalDate"], data["calendar"][31])
        self.assertEqual(out["counts"]["censored"], 19)

    def test_future_numeric_and_event_append_does_not_change_prior_decisions(self):
        data = fixture(); breakout(data)
        before = build(data, last=35)
        changed = deepcopy(data)
        for security in changed["securities"]:
            for bar in security["bars"][36:]: bar.update(amount=float("nan"), close=-1)
        self.assertEqual(before, build(changed, [event(data), event(data, at=36)], last=35))
        after = build(data, [event(data), event(data, at=36)], last=41)
        self.assertEqual(before["formations"], [f for f in after["formations"] if f["formationDate"] <= data["calendar"][35]])
        self.assertEqual(before["opportunities"], [o for o in after["opportunities"] if o["decisionDate"] <= data["calendar"][35]])

    def test_identity_calendar_and_frozen_member_corruption_rejected(self):
        for change in ("F", "duplicateMember", "rank", "calendar", "eventOrder"):
            data = fixture(); events = [event(data)]
            if change == "F": events[0]["peerFormationDate"] = data["calendar"][21]
            elif change == "duplicateMember": events[0]["groupSummary"]["members"][1]["symbol"] = events[0]["groupSummary"]["members"][0]["symbol"]
            elif change == "rank": events[0]["originalLeaderRank"] = 6
            elif change == "calendar": data["calendar"][10] = data["calendar"][9]
            else: events = [event(data, at=31), event(data)]
            with self.subTest(change=change), self.assertRaises(ValueError): build(data, events)

    def test_inputs_and_streams_are_separate_and_reconciliation_complete(self):
        data = fixture(); breakout(data); events = [event(data)]; before = deepcopy((data, events)); logs = []
        out = build(data, events, on_evidence=logs.append)
        self.assertEqual((data, events), before)
        self.assertEqual([d["evidenceSha256"] for d in out["dailyRows"]], [_digest(d) for d in logs])
        logs[0]["formations"][0]["members"].clear()
        self.assertEqual(len(out["formations"][0]["members"]), 20)
        c = out["counts"]
        self.assertEqual(c["sourceEvents"], c["lockedEvents"] + c["unlockedFormed"] + c["unlockedNotFormed"])
        self.assertEqual(c["memberEpisodes"], c["initialUnknown"] + c["initialAbove"] + c["observedMembers"])
        self.assertEqual(c["observedMembers"], sum(c[k] for k in ("priceUnknown", "firstBreakout", "expired", "censored")))
        self.assertEqual(c["firstBreakout"], c["duplicateSuppressed"] + c["uniqueOwners"])
        self.assertEqual(c["uniqueOwners"], c["selectedTop5"] + c["rankExcluded"])


def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/run-leader-amount-handoff.py"
    spec = importlib.util.spec_from_file_location("handoff_runner_tests", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class RunnerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.runner = runner()

    def test_new_identity_preserves_source_config_and_risk(self):
        config = {"strategies": [{"id": "fullGapRetentionV1", "params": {"keep": 60}}],
                  "allocation": {"strategyIds": ["fullGapRetentionV1"]}, "stopLossPct": .05,
                  "trailingStopPct": .1, "preselected": {"strategyId": "fullGapRetentionV1"}}
        original = deepcopy(config); result = self.runner.config_for(config)
        self.assertEqual(config, original)
        self.assertEqual(result["preselected"]["strategyId"], STRATEGY_ID)
        self.assertEqual(result["stopLossPct"], .05); self.assertEqual(result["trailingStopPct"], .1)

    def test_parent_events_require_exact_proof_and_cache_identity(self):
        r = self.runner
        source = {"accountData": {"path": "raw", "sha256": "h"}, "accountDatasetDigest": "d",
                  "sourceManifest": {}, "sourceValidation": {}, "members": 22}
        event_ref = {"path": "events", "sha256": "e"}
        parent = {"sourceData": {"p": {"b": source}}}
        completion = {**deepcopy(source), "period": "p", "board": "b", "planSha256": "plan",
                      "files": {"leader-events.jsonl.gz": event_ref}}
        proof = {"inputArtifacts": {"p": {"b": {"leader-events.jsonl.gz": event_ref}}}, "datasetPins": {"raw": "h"}}
        self.assertEqual(r.validate_event_binding(parent, completion, proof, "p", "b", source, "plan"), event_ref)
        bad = deepcopy(proof); bad["inputArtifacts"]["p"]["b"]["leader-events.jsonl.gz"]["sha256"] = "changed"
        with self.assertRaises(ValueError): r.validate_event_binding(parent, completion, bad, "p", "b", source, "plan")
        bad_source = {**source, "accountDatasetDigest": "other"}
        with self.assertRaises(ValueError): r.validate_event_binding(parent, completion, proof, "p", "b", bad_source, "plan")

    def test_atomic_no_overwrite_and_partial_account_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proof.json"
            self.assertTrue(self.runner.atomic_once(path, {"first": 1}))
            self.assertFalse(self.runner.atomic_once(path, {"other": 2}))
            self.assertEqual(self.runner.read(path), {"first": 1})
            (Path(folder) / "control.result.json.gz").write_bytes(b"partial")
            with self.assertRaises(ValueError): self.runner.OLD.require_unstarted_group(folder)

    def test_all_six_sources_and_both_schedule_bindings_before_accounts(self):
        r = self.runner; groups = []; sources = {p: {} for p in r.PERIODS}; event_data = deepcopy(sources)
        for period in r.PERIODS:
            for board in r.BOARDS:
                source = {"accountData": {"path": period + board, "sha256": "x"}, "accountDatasetDigest": "d"}
                sources[period][board] = source
                event_data[period][board] = {"leaderEvents": {"path": "events"}, "inputCompletion": {"path": "receipt"}}
                groups.append({"period": period, "board": board, **source,
                    "parentLeaderEvents": {"path": "events"}, "parentInputReceipt": {"path": "receipt"},
                    "schedules": {p: {} for p in r.POLICIES}})
        hashes = dict(plan="p", tests="t", completion="c", verifier="v")
        plan = {"sourcePins": {}, "sourceData": sources, "eventData": event_data}
        tests = {"passed": True, "planSha256": "p", "sourcePins": {}, "allChecksBeforeFirstNewAccount": True,
                 "completedAt": "2026-01-01T00:00:00+00:00"}
        completion = {"planSha256": "p", "preAccountTestsSha256": "t", "allSchedulesFrozenBeforeAccounts": True,
                      "newAccountsExecuted": 0, "groups": groups, "counts": {"preparedSchedules": 12, "accountScheduleBindings": 24},
                      "completedAt": "2026-01-01T00:00:01+00:00"}
        proof = {"passed": True, "planSha256": "p", "inputCompletionSha256": "c", "preAccountTestsSha256": "t", "verifierSha256": "v"}
        r.validate_prerequisites(plan, completion, proof, tests, hashes)
        for change in ("source", "schedule", "parent", "proof", "chronology"):
            c, v = deepcopy(completion), deepcopy(proof)
            if change == "source": c["groups"].pop()
            elif change == "schedule": c["groups"][0]["schedules"].pop("handoff")
            elif change == "parent": c["groups"][0]["parentLeaderEvents"] = {}
            elif change == "proof": v["inputCompletionSha256"] = "wrong"
            else: c["completedAt"] = "2025-12-31T00:00:00+00:00"
            with self.subTest(change=change), self.assertRaises(ValueError): r.validate_prerequisites(plan, c, v, tests, hashes)

    def test_breakout_signal_buys_next_source_open_with_frozen_fee_type(self):
        from tests.test_research_fixed_signal_account import settings
        from service.research_account_fees import LegacyFeePolicy
        data = fixture(); breakout(data); signals = build(data)["schedules"]["control"]
        original = deepcopy(data)
        config = settings(data, startDate=data["calendar"][30], initialCapital=500000,
                          maxPositions=5, maxStockWeight=.2, stopLossPct=.05, trailingStopPct=.1,
                          strategies=[{"id": STRATEGY_ID, "kind": "momentum", "pool": "base"}],
                          allocation={"mode": "single", "strategyIds": [STRATEGY_ID]})
        result, _ = self.runner.execute_account(data, signals, config, "trail10", LegacyFeePolicy())
        buys = [r for r in result["trades"] if r["side"] == "buy"]
        self.assertEqual(len(buys), 1); self.assertEqual(buys[0]["date"], data["calendar"][32])
        self.assertEqual(data, original)
        plan = {"feePolicy": {"commissionRate": .0003, "minCommission": 5, "commissionIncludesTransfer": False,
                "stampDutyOverride": None, "securityType": "ordinary-a-share", "tradingMode": "exchange-auction"}}
        fee_config = {"commissionRate": .0003, "minCommission": 5.0, "stampDutyRate": None, "transferFeeRate": .00001}
        self.assertIs(type(self.runner.fee_policy("2022-2023", plan).settings(fee_config)["minCommission"]), int)


if __name__ == "__main__":
    unittest.main()
