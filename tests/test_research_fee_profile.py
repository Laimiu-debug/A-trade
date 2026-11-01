import ast
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from service.research_fee_profile import (
    DATED_TRANSFER, LEGACY_STATIC, REVIEW_PATH, REVIEW_SHA256, VERSION, fee_breakdown,
)


class ResearchFeeProfileTests(unittest.TestCase):
    def setUp(self):
        self.arguments = {
            "amount": 100_000, "side": "sell", "execution_date": "2022-04-28",
            "market": "SH", "security_type": "ordinary-a-share", "trading_mode": "exchange-auction",
            "profile_id": DATED_TRANSFER, "commission_rate": 0.0003, "min_commission": 5,
            "commission_includes_transfer": False,
        }

    def fee(self, **changes):
        return fee_breakdown(**(self.arguments | changes))

    def test_transfer_boundary_both_markets_and_sides(self):
        for market in ("SH", "SZ"):
            for side in ("buy", "sell"):
                for day, expected in (("2022-01-01", 2), ("2022-04-28", 2),
                                      ("2022-04-29", 1), ("2023-12-31", 1)):
                    with self.subTest(market=market, side=side, day=day):
                        result = self.fee(market=market, side=side, execution_date=day)
                        self.assertEqual(result["breakdown"]["transfer"], expected)
                        self.assertEqual(result["transferDisclosure"]["notionalTransferReference"], expected)

    def test_legacy_profile_retains_static_transfer_on_both_sides(self):
        for day in ("2022-01-01", "2022-04-28", "2022-04-29", "2023-12-31"):
            for side in ("buy", "sell"):
                with self.subTest(day=day, side=side):
                    self.assertEqual(self.fee(profile_id=LEGACY_STATIC, side=side, execution_date=day)
                                     ["breakdown"]["transfer"], 1)

    def test_stamp_boundary_uses_execution_date_and_only_sell_side(self):
        for day, expected in (("2023-08-25", 100), ("2023-08-27", 100), ("2023-08-28", 50)):
            for side in ("buy", "sell"):
                with self.subTest(day=day, side=side):
                    result = self.fee(execution_date=day, side=side)
                    self.assertEqual(result["breakdown"]["stampDuty"], expected if side == "sell" else 0)
                    self.assertFalse(result["rates"]["stampDutyOverrideApplied"])

    def test_explicit_stamp_zero_and_nonzero_override_survive_date_change(self):
        for day in ("2022-04-28", "2023-08-28"):
            for override, expected in ((0, 0), (0.0007, 70)):
                for side in ("buy", "sell"):
                    with self.subTest(day=day, override=override, side=side):
                        result = self.fee(execution_date=day, side=side, stamp_duty_override=override)
                        self.assertTrue(result["rates"]["stampDutyOverrideApplied"])
                        self.assertEqual(result["profileSettings"]["stampDutyOverride"], override)
                        self.assertEqual(result["breakdown"]["stampDuty"], expected if side == "sell" else 0)

    def test_included_transfer_is_disclosed_not_double_charged_or_subtracted(self):
        for day, reference in (("2022-04-28", 2), ("2022-04-29", 1)):
            with self.subTest(day=day):
                excluded = self.fee(execution_date=day)
                included = self.fee(execution_date=day, commission_includes_transfer=True)
                self.assertEqual(included["breakdown"]["commission"], excluded["breakdown"]["commission"])
                self.assertEqual(included["breakdown"]["transfer"], 0)
                self.assertEqual(included["transferDisclosure"]["notionalTransferReference"], reference)
                self.assertEqual(excluded["breakdown"]["total"] - included["breakdown"]["total"], reference)
                self.assertFalse(included["transferDisclosure"]["commissionInternalAllocationKnown"])
        # Explicit gross assumption may be below reference: do not invent a broker floor.
        result = self.fee(commission_rate=0, min_commission=0,
                          commission_includes_transfer=True, stamp_duty_override=0)
        self.assertEqual(result["breakdown"]["total"], 0)
        self.assertEqual(result["transferDisclosure"]["notionalTransferReference"], 2)

    def test_minimum_applies_only_to_commission_and_never_rounded_to_cents(self):
        for amount, expected in ((1000, 5), (16666, 5), (16667, 5.0001)):
            with self.subTest(amount=amount):
                result = self.fee(amount=amount, side="buy", execution_date="2022-04-29")
                self.assertAlmostEqual(result["breakdown"]["commission"], expected, places=12)
                self.assertEqual(result["breakdown"]["transfer"], amount * 0.00001)
        result = self.fee(amount=12345.67, min_commission=0, commission_rate=0.000123456,
                          stamp_duty_override=0, commission_includes_transfer=True)
        self.assertEqual(result["breakdown"]["total"], 12345.67 * 0.000123456)
        self.assertNotEqual(result["breakdown"]["total"], round(result["breakdown"]["total"], 2))

    def test_zero_notional_is_explicit_arithmetic_not_an_inferred_unfilled_order(self):
        result = self.fee(amount=0)
        self.assertEqual(result["breakdown"], {"commission": 5, "transfer": 0, "stampDuty": 0, "total": 5})
        self.assertEqual(self.fee(amount=0, min_commission=0)["breakdown"]["total"], 0)

    def test_commission_minimum_and_inclusion_have_no_silent_defaults(self):
        for missing in ("commission_rate", "min_commission", "commission_includes_transfer"):
            with self.subTest(missing=missing):
                values = dict(self.arguments)
                del values[missing]
                with self.assertRaises(TypeError):
                    fee_breakdown(**values)

    def test_unknown_scope_profile_or_side_rejected(self):
        bad_values = {
            "market": ("BJ", "sh", "SH/SZ", "", None, True),
            "security_type": ("ETF", "B-share", "", None),
            "trading_mode": ("block", "non-trading-transfer", "", None),
            "profile_id": ("dated-transfer", "unknown", "", None),
            "side": ("BUY", "hold", "", None, False),
        }
        for field, values in bad_values.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.fee(**{field: value})

    def test_invalid_or_uncovered_date_rejected_even_with_explicit_override(self):
        for day in (None, True, "", "20220428", "2022-4-28", "2023-02-29", "2022-04-28T00:00:00",
                    "2021-12-31", "2024-01-01", date(2022, 4, 28)):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.fee(execution_date=day, stamp_duty_override=0)

    def test_all_numeric_inputs_reject_bool_nonfinite_negative_and_nonnumeric(self):
        for field in ("amount", "commission_rate", "min_commission", "stamp_duty_override"):
            for value in (True, False, -1, float("inf"), -float("inf"), float("nan"), "0.01", Decimal("0.1"), 10 ** 1000):
                with self.subTest(field=field, value=str(value)[:30]), self.assertRaises(ValueError):
                    self.fee(**{field: value})
        for field in ("amount", "commission_rate", "min_commission"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.fee(**{field: None})

    def test_inclusion_requires_boolean_without_truthiness_conversion(self):
        for value in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.fee(commission_includes_transfer=value)

    def test_finite_inputs_cannot_return_overflow_or_silent_product_underflow(self):
        for changes in ({"amount": 1e308, "commission_rate": 10},
                        {"amount": 1e308, "min_commission": 1e308, "stamp_duty_override": 1},
                        {"amount": 1e-300, "commission_rate": 1e-300},
                        {"amount": 5e-324, "commission_rate": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.fee(**changes)

    def test_legacy_exactly_matches_frozen_fee_method_without_loading_engine(self):
        source = Path(__file__).resolve().parents[1] / "service" / "portfolio_engine.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_fees")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
        for amount in (0, 1000, 16666, 16667, 100000, 12345.67):
            for day in ("2022-04-28", "2022-04-29", "2023-08-25", "2023-08-28"):
                for side in ("buy", "sell"):
                    for override in (None, 0, 0.0007):
                        with self.subTest(amount=amount, day=day, side=side, override=override):
                            result = self.fee(amount=amount, profile_id=LEGACY_STATIC, execution_date=day,
                                              side=side, stamp_duty_override=override)
                            holder = SimpleNamespace(config={"commissionRate": .0003, "minCommission": 5,
                                                              "transferFeeRate": .00001, "stampDutyRate": override})
                            self.assertEqual(result["breakdown"]["total"], namespace["_fees"](holder, amount, side, day))

    def test_profile_identity_captures_assumptions_not_each_execution(self):
        base = self.fee()["profileSettingsSha256"]
        self.assertEqual(base, self.fee(amount=10, side="buy", execution_date="2023-08-28")["profileSettingsSha256"])
        for changes in ({"profile_id": LEGACY_STATIC}, {"commission_includes_transfer": True},
                        {"min_commission": 0}, {"commission_rate": .0002},
                        {"stamp_duty_override": 0}, {"market": "SZ"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(base, self.fee(**changes)["profileSettingsSha256"])

    def test_json_safe_noncertified_evidence_and_no_extra_exchange_charge(self):
        for profile in (LEGACY_STATIC, DATED_TRANSFER):
            with self.subTest(profile=profile):
                result = self.fee(profile_id=profile)
                self.assertEqual(result["version"], VERSION)
                evidence = result["evidence"]
                self.assertEqual(evidence["reviewSha256"], REVIEW_SHA256)
                for field in ("exact2022IssuerNoticeRetrieved", "fullHistoricalCostScheduleVerified",
                              "brokerSpecificFeesVerified", "scopeIndependentlyVerified",
                              "executionCalendarVerified", "exchangeChargeAdded", "roundingApplied"):
                    self.assertIs(evidence[field], False)
                self.assertEqual(set(result["breakdown"]), {"commission", "transfer", "stampDuty", "total"})
                self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
                self.assertTrue(all(math.isfinite(v) for v in result["breakdown"].values()))

    def test_inputs_and_future_calls_not_affected_by_mutating_returned_evidence(self):
        before = deepcopy(self.arguments)
        expected = self.fee()
        modified = self.fee()
        modified["profileSettings"]["supportedDates"].append("2030-01-01")
        modified["evidence"]["sources"][0]["url"] = "wrong"
        modified["evidence"]["limitations"].clear()
        self.assertEqual(self.fee(), expected)
        self.assertEqual(self.arguments, before)

    def test_evidence_pin_matches_actual_review_file_bytes(self):
        path = Path(__file__).resolve().parents[1] / REVIEW_PATH
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), REVIEW_SHA256)


if __name__ == "__main__":
    unittest.main()
