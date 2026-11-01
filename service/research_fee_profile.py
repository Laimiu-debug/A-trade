"""Isolated fee arithmetic for explicitly registered research assumptions.

No existing engine/configuration imports or integration are performed.  Values
are CNY notionals and fractional rates, calculated as binary floats with no
cent rounding.  Each call models one supplied execution, not an order/fill
aggregator or an exchange-calendar check.  Even zero notional applies the
explicit minimum commission, matching the legacy arithmetic contract.

The dated transfer schedule is an ASSUMPTION: the exact 2022 ChinaClear issuer
notice is still missing from our evidence.  Neither profile certifies a broker
tariff, historical cost completeness, or actual trade execution.  An explicit
commission-includes-transfer flag describes the caller's chosen gross tariff;
it is never inferred from its numerical rate or used to reconstruct its parts.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math


VERSION = "research-fee-profile-v1"
LEGACY_STATIC = "legacy-static-v1"
DATED_TRANSFER = "dated-transfer-assumption-v1"
START_DATE = "2022-01-01"
END_DATE = "2023-12-31"
REVIEW_PATH = "artifacts/strategy-search/historical-fee-gap-review-v1.json"
REVIEW_SHA256 = "affc37d6c52b6a4cd9b71cec312d14b2e53e6aad936ba865509cef346d7cd5ec"
_SOURCES = (
    ("mof-2008-single-side", "https://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/200809/t20080919_76432.htm"),
    ("mof-tax-2023-39", "https://jiangsu.chinatax.gov.cn/art/2023/8/27/art_23636_1036.html"),
    ("csrc-2023-17", "https://www.csrc.gov.cn/csrc/c105938/c905d6f8c17234a5e8c17e41de0d05e8f/content.shtml"),
)


def _choice(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of {choices}")
    return value


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative int or float")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite non-negative int or float") from None
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite non-negative int or float")
    return 0.0 if result == 0 else result


def _execution_date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("execution_date must be a canonical YYYY-MM-DD date") from None
    if not START_DATE <= value <= END_DATE:
        raise ValueError(f"execution_date is outside {START_DATE}..{END_DATE}")
    return value


def _product(amount, rate, name):
    value = amount * rate
    if not math.isfinite(value) or (amount > 0 and rate > 0 and value == 0):
        raise ValueError(f"{name} overflowed or underflowed")
    return value


def fee_breakdown(*, amount, side, execution_date, market, security_type,
                  trading_mode, profile_id, commission_rate, min_commission,
                  commission_includes_transfer, stamp_duty_override=None):
    """Return JSON-safe components, applied rates and versioned assumptions.

    Required scope is SH/SZ / ordinary-a-share / exchange-auction; scope fields
    are declarations by the caller, not independent security verification.
    Commission and minimum have no default.  If commission_includes_transfer
    is True, max(minimum, notional * rate) is the entire commission including
    transfer; the transfer reference is disclosed, never added or subtracted.
    stamp_duty_override is a *sell* rate: None chooses the dated schedule, and
    explicit zero disables sell stamp duty.  Buys remain untaxed.

    Unknown scopes/dates, malformed numbers, overflow and positive-product
    underflow raise ValueError.  No commission ceiling, minimum, rounding,
    exchange charge, market calendar or broker contract is guessed here.
    """
    profile_id = _choice(profile_id, (LEGACY_STATIC, DATED_TRANSFER), "profile_id")
    market = _choice(market, ("SH", "SZ"), "market")
    security_type = _choice(security_type, ("ordinary-a-share",), "security_type")
    trading_mode = _choice(trading_mode, ("exchange-auction",), "trading_mode")
    side = _choice(side, ("buy", "sell"), "side")
    execution_date = _execution_date(execution_date)
    amount = _number(amount, "amount")
    commission_rate = _number(commission_rate, "commission_rate")
    min_commission = _number(min_commission, "min_commission")
    if type(commission_includes_transfer) is not bool:
        raise ValueError("commission_includes_transfer must be an explicit bool")
    if stamp_duty_override is not None:
        stamp_duty_override = _number(stamp_duty_override, "stamp_duty_override")

    transfer_rate = 0.00002 if profile_id == DATED_TRANSFER and execution_date < "2022-04-29" else 0.00001
    scheduled_stamp_rate = 0.001 if execution_date < "2023-08-28" else 0.0005
    sell_stamp_rate = scheduled_stamp_rate if stamp_duty_override is None else stamp_duty_override
    charged_stamp_rate = sell_stamp_rate if side == "sell" else 0.0
    commission = max(min_commission, _product(amount, commission_rate, "commission"))
    transfer_reference = _product(amount, transfer_rate, "transfer reference")
    transfer = 0.0 if commission_includes_transfer else transfer_reference
    stamp = _product(amount, charged_stamp_rate, "stamp duty")
    # Deliberately preserve the old engine's operation order; do not quantize.
    total = commission + transfer + stamp
    if not math.isfinite(total):
        raise ValueError("total fee overflowed")

    settings = {
        "version": VERSION, "profileId": profile_id,
        "supportedDates": [START_DATE, END_DATE],
        "market": market, "securityType": security_type, "tradingMode": trading_mode,
        "commissionRate": commission_rate, "minCommission": min_commission,
        "commissionIncludesTransfer": commission_includes_transfer,
        "stampDutyOverride": stamp_duty_override,
        "transferScheduleId": "legacy-fixed-1e-5" if profile_id == LEGACY_STATIC else "reported-2022-04-29-transfer-cut-assumption",
        "stampScheduleId": "seller-only-2023-08-28-half-rate",
        "numericPolicy": "binary-float;left-associated-sum;no-rounding;overflow-underflow-rejected",
        "evidenceReviewSha256": REVIEW_SHA256,
    }
    identity = hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "version": VERSION, "profileId": profile_id, "profileSettingsSha256": identity,
        "profileSettings": settings, "executionDate": execution_date, "side": side,
        "amount": amount, "currency": "CNY",
        "rates": {"commission": commission_rate, "transfer": transfer_rate,
                  "scheduledSellStampDuty": scheduled_stamp_rate,
                  "selectedSellStampDuty": sell_stamp_rate, "chargedStampDuty": charged_stamp_rate,
                  "stampDutyOverrideApplied": stamp_duty_override is not None},
        "breakdown": {"commission": commission, "transfer": transfer,
                      "stampDuty": stamp, "total": total},
        "transferDisclosure": {
            "notionalTransferReference": transfer_reference,
            "chargedSeparately": not commission_includes_transfer,
            "commissionIncludesTransferAssumption": commission_includes_transfer,
            "commissionInternalAllocationKnown": False,
        },
        "evidence": {
            "reviewPath": REVIEW_PATH, "reviewSha256": REVIEW_SHA256,
            "sources": [{"id": key, "url": url} for key, url in _SOURCES],
            "transferBasis": "registered-static-assumption" if profile_id == LEGACY_STATIC else "reported-dated-schedule-assumption",
            "exact2022IssuerNoticeRetrieved": False,
            "fullHistoricalCostScheduleVerified": False, "brokerSpecificFeesVerified": False,
            "scopeIndependentlyVerified": False, "executionCalendarVerified": False,
            "exchangeChargeAdded": False, "roundingApplied": False,
            "limitations": [
                "Exact 2022 ChinaClear issuer notice remains missing; neither profile is historically certified.",
                "Commission, minimum and transfer inclusion are caller assumptions, not a broker tariff certification.",
                "No extra exchange/regulatory charge, fill aggregation, cent rounding or minimum transfer charge is inferred.",
            ],
        },
    }
