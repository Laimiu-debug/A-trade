"""Small synthetic checks; never read user market files or run an account."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_signal_contraction_price_runner as fixtures


SPEC = importlib.util.spec_from_file_location("supplemental_contraction_schedule",
    Path(__file__).resolve().parents[1] / "scripts/build-supplemental-contraction-schedule.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class SupplementalContractionScheduleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContractionRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.data = self.fixture.dataset()
        self.start, self.end = self.fixture.args().start, self.fixture.args().end

    def build(self, data=None, **kwargs):
        return helper.build_schedule(data or self.data, {"identity": {"fixture": "synthetic-only"}},
                                     start=self.start, end=kwargs.pop("end", self.end), **kwargs)

    def test_exact_original_pre_active_top5_and_no_input_changes(self):
        before = deepcopy(self.data)
        old_signals, old_counts = self.fixture.collect(self.data)
        expected = [r for r in old_signals if r["strategyId"] == helper.CANDIDATE_ID]
        result = self.build()
        self.assertEqual(result["schedules"]["baseline"], [helper._project(r) for r in expected])
        old_selection = fixtures.lines(self.fixture.output / "selection.jsonl.gz")
        self.assertEqual([r["fullRanking"] for r in result["selections"]],
                         [r["fullRanking"] for r in old_selection])
        self.assertEqual(result["metadata"]["evaluatedKinds"], old_counts["evaluatedKinds"])
        self.assertEqual(result["metadata"]["rawCandidateCount"], 7)
        self.assertEqual(result["metadata"]["evaluatedSecuritySessions"], 9 * 40)
        self.assertEqual(self.data, before)

    def test_pressure_last40_is_exact_original_full_history(self):
        result = self.build()
        securities = {s["symbol"]: s for s in self.data["securities"]}
        for evidence in result["filterEvidence"]:
            prepared = helper.prepare_force_statistics(securities[evidence["symbol"]]["bars"],
                                                        calendar=self.data["calendar"])
            full = next(r for r in prepared["rows"] if r["date"] == evidence["signalDate"])
            self.assertEqual(evidence["pressure5"], full["pressure5"])
            self.assertEqual(evidence["pressure20"], full["pressure20"])
            self.assertEqual(evidence["source"]["pressureWindowSlots"], 40)
            self.assertEqual(evidence["source"]["pressureWindowEnd"], evidence["signalDate"])

    def test_filter_after_top5_preserves_rank_gaps_unknown_and_no_backfill(self):
        real = helper.prepare_force_statistics
        calls = 0

        def artificial_filter(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = real(*args, **kwargs)
            result["current"].update(pressure20=.1, pressure5=.2)
            if calls == 1:
                result["current"]["pressure5"] = .1  # strict tie excludes
            if calls == 2:
                result["current"]["pressure5"] = None
            return result

        with patch.object(helper, "prepare_force_statistics", side_effect=artificial_filter):
            result = self.build()
        self.assertEqual([r["originalRank"] for r in result["schedules"]["pressure"]], [3, 4, 5])
        self.assertEqual(len(result["schedules"]["baseline"]), 5)
        self.assertEqual(result["metadata"]["filterCounts"], {"included": 3, "excluded": 1, "unknown": 1})
        self.assertEqual(result["selections"][0]["rawCandidateCount"], 7)

    def test_missing_and_halt_keep_daily_coverage_and_gate_unknown_is_not_zero(self):
        a, b = self.data["securities"][:2]
        a["bars"] = [r for r in a["bars"] if r["date"] != self.fixture.dates[120]]
        b["tradeStatusByDate"] = {self.start: False}
        next(r for r in b["bars"] if r["date"] == self.start)["volume"] = 0
        result = self.build()
        self.assertEqual(sum(result["coverage"][0]["evaluatedKinds"].values()), 9)
        self.assertEqual(result["coverage"][0]["evaluatedReasons"]["missingAlignedRawBar"], 1)
        self.assertEqual(result["coverage"][0]["evaluatedReasons"]["knownSuspension"], 1)
        for value in (None, float("nan"), float("inf"), True):
            self.assertEqual(helper.pressure_filter({"pressure5": value, "pressure20": .1})["status"], "unknown")
        self.assertEqual(helper.pressure_filter({"pressure5": .2, "pressure20": 0})["status"], "excluded")

    def test_valid_future_append_or_price_perturbation_does_not_change_past(self):
        end = self.fixture.dates[131]
        first = self.build(end=end)
        later = deepcopy(self.data)
        for security in later["securities"]:
            for row in security["bars"]:
                if row["date"] > end:
                    for key in ("open", "high", "low", "close"):
                        row[key] *= 2
                    row["amount"] *= 7
        second = self.build(later, end=end)
        self.assertEqual(first["schedules"], second["schedules"])
        self.assertEqual(first["filterEvidence"], second["filterEvidence"])
        self.assertEqual(first["coverage"], second["coverage"])
        short = deepcopy(self.data)
        short["calendar"] = [d for d in short["calendar"] if d <= end]
        for security in short["securities"]:
            security["bars"] = [r for r in security["bars"] if r["date"] <= end]
        self.assertEqual(first["schedules"], self.build(short, end=end)["schedules"])

    def test_no_cross_day_cooldown_and_bad_calendar_rejected(self):
        sample = self.build()["top5Signals"][0]
        symbol = sample["symbol"]

        def repeat(s, day, *args, **kwargs):
            signal = {**sample, "signalDate": day} if s == symbol else None
            return {"kind": "signaled" if signal else "notTriggered", "reason": "syntheticRepeatedDailySignal", "signal": signal}

        with patch.object(helper, "evaluate_contraction", side_effect=repeat):
            result = self.build(end=self.fixture.dates[131])
        self.assertEqual(len(result["schedules"]["baseline"]), 2)
        self.assertNotEqual(*[r["signalId"] for r in result["schedules"]["baseline"]])
        for calendar in (list(reversed(self.data["calendar"])), self.data["calendar"] + [self.start]):
            with self.assertRaises(ValueError):
                self.build({**self.data, "calendar": calendar})


if __name__ == "__main__":
    unittest.main()
