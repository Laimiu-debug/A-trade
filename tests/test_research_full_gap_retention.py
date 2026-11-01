from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import tempfile
import unittest

from service.research_full_gap_retention import build_full_gap_retention, STRATEGY_ID, _digest


def fixture(length=130, size=40):
    days = [(date(2022, 1, 1) + timedelta(days=i)).isoformat() for i in range(length)]
    securities = []
    for s in range(size):
        bars = []
        for i, day in enumerate(days):
            close = 100 + i * (s + 1) * .01
            bars.append(dict(date=day, open=close, high=close + .2, low=close - .2,
                             close=close, volume=1_000_000, amount=80_000_000 + s * 100_000))
        securities.append({"symbol": "sz" + str(s + 1).zfill(6), "bars": bars})
    benchmark = deepcopy(securities[0]); benchmark["symbol"] = "sh000300"
    return {"priceBasisId": "raw-unadjusted", "calendar": days, "securities": securities, "benchmark": benchmark}


def gap(data, symbol_index=39, at=120, confirm=True):
    rows = data["securities"][symbol_index]["bars"]
    boundary = rows[at - 1]["high"]
    for i in range(at, min(len(rows), at + (3 if confirm else 1))):
        low = boundary + 1 + .1 * (i - at)
        rows[i].update(open=low + .1, low=low, high=low + .5, close=low + .3,
                       volume=2_000_000 if i == at else 1_000_000)
    return boundary


def build(data, first=120, last=None, **kwargs):
    return build_full_gap_retention(data, start=data["calendar"][first],
                                   end=data["calendar"][last if last is not None else -1], **kwargs)


class FullGapTests(unittest.TestCase):
    def test_common_clock_and_frozen_identity(self):
        data = fixture(); gap(data)
        out = build(data); opp = out["opportunities"][0]
        self.assertEqual(opp["formationDate"], data["calendar"][120])
        self.assertEqual(opp["decisionDate"], data["calendar"][122])
        self.assertEqual(out["schedules"]["control"], out["schedules"]["retained"])
        signal = out["schedules"]["control"][0]
        self.assertEqual(signal["signalDate"], data["calendar"][122])
        self.assertEqual(signal["signalId"], STRATEGY_ID + ":" + data["calendar"][120] + ":sz000040")
        self.assertEqual(set(signal), {"signalId", "strategyId", "symbol", "signalDate", "originalRank"})

    def test_strict_gap_volume_and_retention_equalities(self):
        data = fixture(); boundary = gap(data)
        data["securities"][-1]["bars"][121]["low"] = boundary
        out = build(data)
        self.assertEqual(len(out["schedules"]["control"]), 1)
        self.assertEqual(out["schedules"]["retained"], [])
        for field, value in (("low", boundary), ("volume", 1_000_000)):
            changed = fixture(); gap(changed); changed["securities"][-1]["bars"][120][field] = value
            self.assertEqual(build(changed)["counts"]["opportunities"], 0)

    def test_amount_boundary_and_nonpositive_slot(self):
        for amount, expected in ((50_000_000, 1), (49_999_999, 0), (0, 0)):
            data = fixture(); gap(data)
            for bar in data["securities"][-1]["bars"][100:120]: bar["amount"] = amount
            self.assertEqual(build(data)["counts"]["opportunities"], expected)

    def test_history_counts_valid_observations_not_slots(self):
        data = fixture(); gap(data)
        data["securities"][-1]["bars"][10]["volume"] = 0
        rows = []; out = build(data, on_evidence=rows.append)
        self.assertEqual(out["counts"]["opportunities"], 0)
        record = rows[0]["rows"][-1]
        self.assertEqual(record["priorValidObservations"], 119)
        self.assertIsNotNone(record["priorRank60"])
        self.assertIn("insufficientObservedHistory120", record["failureReasons"])

    def test_rank_population_precedes_eligibility(self):
        data = fixture(); gap(data)
        data["securities"][-2]["stByDate"] = {data["calendar"][119]: True}
        data["securities"][-3]["bars"][110]["amount"] = 0
        out = build(data)
        self.assertEqual(out["dailyRows"][0]["rankPopulation"]["count"], 40)
        self.assertEqual(out["opportunities"][0]["priorRank60"], .9875)

    def test_exact_ties_have_midpoint_not_arbitrary_winners(self):
        data = fixture()
        for security in data["securities"]: security["bars"] = deepcopy(data["securities"][0]["bars"])
        gap(data); rows = []; out = build(data, on_evidence=rows.append)
        self.assertEqual({r["priorRank60"] for r in rows[0]["rows"]}, {.5})
        self.assertEqual(out["counts"]["opportunities"], 0)

    def test_top_five_stays_fixed_after_confirmation_unknown(self):
        data = fixture()
        for s in range(32, 40): gap(data, s)
        data["securities"][-1]["bars"][121]["volume"] = 0
        out = build(data)
        self.assertEqual(out["counts"]["rawSetups"], 8)
        self.assertEqual(out["counts"]["rankExcludedSetups"], 3)
        self.assertEqual(len(out["opportunities"]), 5)
        self.assertEqual([s["originalRank"] for s in out["schedules"]["control"]], [2, 3, 4, 5])

    def test_non_top_five_setup_also_locks_without_extension(self):
        data = fixture()
        for s in range(32, 40): gap(data, s)
        # The rank8 symbol has no opportunity but still owns a lock.
        gap(data, 32, 121, False); gap(data, 32, 122, False); gap(data, 32, 123)
        out = build(data)
        forms = [f for f in out["formations"] if f["symbol"] == "sz000033"]
        self.assertEqual([f["selectionStatus"] for f in forms[:4]], ["rankExcluded", "locked", "locked", "selected"])
        self.assertEqual(forms[1]["lockOwner"], forms[0]["formationId"])
        self.assertEqual(forms[2]["lockUntilIndex"], 122)
        self.assertTrue(forms[3]["lockApplied"])

    def test_unknown_has_priority_over_known_retention_failure(self):
        data = fixture(); boundary = gap(data)
        data["securities"][-1]["bars"][121]["low"] = boundary
        del data["securities"][-1]["bars"][122]
        opp = build(data)["opportunities"][0]
        self.assertEqual(opp["confirmation"]["status"], "unknown")
        self.assertIsNone(opp["retained"])
        self.assertEqual(opp["signals"], {"control": None, "retained": None})

    def test_known_float_zero_halt_is_unknown_confirmation(self):
        data = fixture(); gap(data)
        data["securities"][-1]["tradeStatusByDate"] = {data["calendar"][121]: 0.0}
        self.assertIn("knownSuspension", build(data)["opportunities"][0]["confirmation"]["reasons"][0])

    def test_prior_st_only_known_true_excluded(self):
        for value, expected in ((True, 0), (False, 1), (None, 1)):
            data = fixture(); gap(data)
            data["securities"][-1]["stByDate"] = {data["calendar"][119]: value, data["calendar"][120]: True}
            self.assertEqual(build(data)["counts"]["opportunities"], expected)

    def test_censored_preserves_original_opportunity(self):
        data = fixture(); gap(data)
        for last in (120, 121):
            out = build(data, last=last)
            self.assertEqual(out["counts"]["opportunities"], 1)
            self.assertEqual(out["opportunities"][0]["confirmation"]["status"], "censored")
            self.assertIsNone(out["opportunities"][0]["decisionDate"])

    def test_explicit_reference_change_or_invalid_is_shared_unknown(self):
        for value in (0, float("nan"), 1):
            data = fixture(); gap(data)
            data["securities"][-1]["precloseByDate"] = {data["calendar"][121]: value}
            out = build(data)
            self.assertEqual(out["opportunities"][0]["confirmation"]["status"], "unknown")
            self.assertEqual(out["schedules"], {"control": [], "retained": []})
        data = fixture(); gap(data)
        stock = data["securities"][-1]
        stock["precloseByDate"] = {data["calendar"][121]: stock["bars"][120]["close"] + .001}
        self.assertTrue(build(data)["opportunities"][0]["retained"])

    def test_prefix_and_future_numeric_invariance(self):
        data = fixture(); gap(data)
        before = build(data, last=122)
        modified = deepcopy(data)
        for security in [*modified["securities"], modified["benchmark"]]:
            for bar in security["bars"][123:]: bar.update(close=float("nan"), volume=-10)
        self.assertEqual(before, build(modified, last=122))
        after = build(data)
        self.assertEqual(before["formations"], [f for f in after["formations"] if f["formationDate"] <= data["calendar"][122]])
        self.assertEqual(before["opportunities"], [o for o in after["opportunities"] if o["formationDate"] <= data["calendar"][122]])

    def test_streamed_evidence_input_immutable_and_separate(self):
        data = fixture(); gap(data); original = deepcopy(data); evidence = []
        out = build(data, on_evidence=evidence.append)
        self.assertEqual(data, original)
        self.assertNotIn("symbolEvidence", out)
        for row, stream in zip(out["dailyRows"], evidence):
            self.assertEqual(row["symbolEvidenceDigest"], _digest(stream["rows"]))
        evidence[0]["rows"][0]["priorRank60"] = -1
        self.assertEqual(data, original)

    def test_calendar_missing_slot_does_not_compress(self):
        data = fixture(); gap(data)
        del data["securities"][-1]["bars"][110]
        out = build(data)
        self.assertEqual(out["counts"]["opportunities"], 0)
        bad = fixture(); bad["calendar"][3] = bad["calendar"][2]
        with self.assertRaises(ValueError): build(bad)


def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/run-full-gap-retention.py"
    spec = importlib.util.spec_from_file_location("full_gap_runner_tests", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class RunnerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = runner()

    def test_new_identity_preserves_risk_and_source_config(self):
        before = {"strategies": [{"id": "strongUndercutReclaimV1", "params": {"keep": 60}}],
                  "allocation": {"strategyIds": ["strongUndercutReclaimV1"]}, "stopLossPct": .05,
                  "trailingStopPct": .1, "preselected": {"strategyId": "strongUndercutReclaimV1"}}
        source = deepcopy(before); after = self.runner.config_for(source)
        self.assertEqual(source, before)
        self.assertEqual(after["preselected"]["strategyId"], STRATEGY_ID)
        self.assertEqual(after["stopLossPct"], .05)
        self.assertEqual(after["trailingStopPct"], .1)
        self.assertEqual(after["strategies"][0]["params"], {"keep": 60})

    def test_atomic_once_never_overwrites_and_partial_group_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proof.json"
            self.assertTrue(self.runner.atomic_once(path, {"first": 1}))
            self.assertFalse(self.runner.atomic_once(path, {"second": 2}))
            self.assertEqual(self.runner.read(path), {"first": 1})
            self.runner.OLD.require_unstarted_group(folder)
            (Path(folder) / "control.result.json.gz").write_bytes(b"partial")
            with self.assertRaises(ValueError): self.runner.OLD.require_unstarted_group(folder)

    def test_all_six_source_schedules_and_audit_hashes_required(self):
        r = self.runner
        groups, sources = [], {p: {} for p in r.PERIODS}
        for period in r.PERIODS:
            for board in r.BOARDS:
                source = {"accountData": {"path": period + board, "sha256": "x"}, "accountDatasetDigest": "d"}
                sources[period][board] = source
                groups.append({"period": period, "board": board, **source, "schedules": {p: {} for p in r.POLICIES}})
        hashes = dict(plan="p", tests="t", completion="c", verifier="v")
        plan = {"sourcePins": {}, "sourceData": sources}
        tests = {"passed": True, "planSha256": "p", "sourcePins": {}, "allChecksBeforeFirstNewAccount": True,
                 "completedAt": "2026-01-01T00:00:00+00:00"}
        completion = {"planSha256": "p", "preAccountTestsSha256": "t", "allSchedulesFrozenBeforeAccounts": True,
                      "newAccountsExecuted": 0, "groups": groups, "counts": {"preparedSchedules": 12, "accountScheduleBindings": 24},
                      "completedAt": "2026-01-01T00:00:01+00:00"}
        proof = {"passed": True, "planSha256": "p", "inputCompletionSha256": "c", "preAccountTestsSha256": "t", "verifierSha256": "v"}
        r.validate_prerequisites(plan, completion, proof, tests, hashes)
        for change in ("matrix", "policy", "audit", "chronology"):
            c, v = deepcopy(completion), deepcopy(proof)
            if change == "matrix": c["groups"].pop()
            elif change == "policy": c["groups"][0]["schedules"].pop("retained")
            elif change == "audit": v["inputCompletionSha256"] = "wrong"
            else: c["completedAt"] = "2025-12-31T00:00:00+00:00"
            with self.subTest(change=change), self.assertRaises(ValueError): r.validate_prerequisites(plan, c, v, tests, hashes)

    def test_dated_fee_retains_integer_minimum_identity(self):
        plan = {"feePolicy": {"commissionRate": .0003, "minCommission": 5, "commissionIncludesTransfer": False,
                "stampDutyOverride": None, "securityType": "ordinary-a-share", "tradingMode": "exchange-auction"}}
        config = {"commissionRate": .0003, "minCommission": 5.0, "stampDutyRate": None, "transferFeeRate": .00001}
        policy = self.runner.fee_policy("2022-2023", plan)
        self.assertIs(type(policy.settings(config)["minCommission"]), int)
        self.assertEqual(policy.settings(config)["minCommission"], 5)

    def test_signal_decides_f_plus_two_and_account_buys_next_open(self):
        from tests.test_research_fixed_signal_account import settings
        from service.research_account_fees import LegacyFeePolicy
        data = fixture(); gap(data); original = deepcopy(data)
        signals = build(data)["schedules"]["control"]
        config = settings(data, startDate=data["calendar"][120], initialCapital=500000,
                          maxPositions=5, maxStockWeight=.2, stopLossPct=.05, trailingStopPct=.1,
                          strategies=[{"id": STRATEGY_ID, "kind": "momentum", "pool": "base"}],
                          allocation={"mode": "single", "strategyIds": [STRATEGY_ID]})
        result, _ = self.runner.execute_account(data, signals, config, "trail10", LegacyFeePolicy())
        buys = [row for row in result["trades"] if row["side"] == "buy"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["date"], data["calendar"][123])
        self.assertEqual(buys[0]["signalId"], signals[0]["signalId"])
        self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
