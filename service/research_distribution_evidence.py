"""Lossless parsing of one BaoStock dividend row, never execution instructions.

The caller maps the response's exact fields to its row after checking equal
lengths/unique fields. This module does not read responses, infer missing zeroes,
resolve tax alternatives, or certify provider dates against issuer documents.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

VERSION = "research-distribution-evidence-v1"
DATE_FIELDS = {
    "preNoticeDate": "dividPreNoticeDate",
    "agmAnnouncementDate": "dividAgmPumDate",
    "planAnnouncementDate": "dividPlanAnnounceDate",
    "planDate": "dividPlanDate",
    "recordDate": "dividRegistDate",
    "exDate": "dividOperateDate",
    "payDate": "dividPayDate",
    "shareListingDate": "dividStockMarketDate",
}
NUMBER_FIELDS = {
    "cashGrossPerShare": "dividCashPsBeforeTax",
    "stockDividendPerShare": "dividStocksPs",
    "capitalizationPerShare": "dividReserveToStockPs",
}
TEXT_FIELDS = {"cashAfterTaxText": "dividCashPsAfterTax", "distributionText": "dividCashStock"}
FIELDS = ("code", *DATE_FIELDS.values(), *NUMBER_FIELDS.values(), *TEXT_FIELDS.values())


def _copy_json(value):
    # Non-JSON objects (including float NaN) cannot be a lossless wire row.
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Evidence must be finite, JSON-compatible data") from None
    return deepcopy(value)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _symbol(value, *, provider=False):
    pattern = r"(sh|sz)\.(\d{6})" if provider else r"(sh|sz)\.?([0-9]{6})"
    match = re.fullmatch(pattern, value) if isinstance(value, str) else None
    if not match:
        raise ValueError("A canonical SH/SZ security identity is required")
    return "".join(match.groups())


def _source(value):
    value = _copy_json(value)
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("Source reference must not be empty")
        return value
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("source must be a reference string or metadata dictionary")
    if not isinstance(value.get("provider"), str) or value["provider"].lower() != "baostock":
        raise ValueError("Source provider must identify BaoStock")
    if "responseHash" in value and (not isinstance(value["responseHash"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value["responseHash"])):
        raise ValueError("responseHash must be a SHA-256 hexadecimal digest")
    if "url" in value:
        url = urlsplit(value["url"]) if isinstance(value["url"], str) else None
        if url is None or url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError("Source URL must be an absolute HTTP(S) URL")
    return value


def _parse_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None, "notNumeric"
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
        return None, "notFiniteDecimal"
    try:
        decimal = Decimal(text)
        number = float(decimal)
    except (InvalidOperation, ValueError, OverflowError):
        return None, "notFiniteDecimal"
    if not decimal.is_finite() or not math.isfinite(number) or (decimal != 0 and number == 0):
        return None, "outsideFiniteFloatRange"
    if decimal < 0:
        return None, "negativeAmountOrRatio"
    return number, None


def normalize_dividend_row(row, *, symbol, source):
    """Parse a JSON-compatible field dictionary; preserve every original value.

    Missing/blank/None fields are unknown. Malformed dates/numbers and temporal
    contradictions retain the row with status ``invalid``. Unverifiable or
    conflicting security identity and malformed source metadata raise ValueError.
    ``parsed`` describes parsing only, never an executable/complete rights event.
    """
    if not isinstance(row, dict) or any(not isinstance(key, str) for key in row):
        raise ValueError("row must be a field-name dictionary")
    row = _copy_json(row)
    canonical = _symbol(symbol)
    if _symbol(row.get("code"), provider=True) != canonical:
        raise ValueError("Dividend row security differs from requested symbol")
    source = _source(source)
    output = {"schema": VERSION, "symbol": canonical, "providerCode": row["code"],
              "rawRow": row, "source": source, "rawRowSha256": _hash(row), "sourceSha256": _hash(source)}
    statuses, unknown, invalid, missing = {}, [], {}, []
    for mapping, kind in ((DATE_FIELDS, "date"), (NUMBER_FIELDS, "number"), (TEXT_FIELDS, "text")):
        for key, field in mapping.items():
            value, reason = row.get(field), None
            if field not in row:
                missing.append(field)
            if value is None or isinstance(value, str) and not value.strip():
                output[key], statuses[key] = None, "unknown"
                unknown.append(key)
                continue
            if kind == "date":
                try:
                    valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
                except ValueError:
                    valid = False
                output[key] = value if valid else None
                reason = None if valid else "notCanonicalCalendarDate"
            elif kind == "number":
                output[key], reason = _parse_number(value)
            else:
                output[key] = value if isinstance(value, str) else None
                reason = None if isinstance(value, str) else "notReportedText"
            statuses[key] = "known" if reason is None else "invalid"
            if reason:
                invalid[key] = {"sourceField": field, "reason": reason}
    chronology = []
    for earlier, later, strict in (("planDate", "recordDate", False), ("recordDate", "exDate", True),
                                   ("exDate", "payDate", False), ("exDate", "shareListingDate", False)):
        left, right = output[earlier], output[later]
        if left is not None and right is not None and (left >= right if strict else left > right):
            chronology.append({"earlierField": earlier, "laterField": later,
                               "requiredRelation": "before" if strict else "notAfter"})
    output.update(fieldStatus=statuses, unknownFields=unknown, invalidFields=invalid,
                  missingSourceFields=missing, unexpectedSourceFields=sorted(set(row) - set(FIELDS)),
                  chronologyIssues=chronology,
                  status="invalid" if invalid or chronology else "incomplete" if unknown else "parsed",
                  statusMeaning="fieldParsingOnly;notEventCompletenessOrExecutionReadiness",
                  cashNetPerShare=None, cashAvailableAt=None, knownByDate=None,
                  taxResolution="unresolved;reportedAfterTaxTextIsNotAppliedEvenIfItContainsOneNumber",
                  shareListingDateMeaning="providerReportedRedShareListingDate;notIndependentCapitalizationUnlockProof",
                  planDateMeaning="providerReportedImplementationAnnouncementDate",
                  agmAnnouncementDateMeaning="shareholderMeetingAnnouncementDate;notMeetingDate",
                  responseHashVerified=False, issuerTermsVerified=False, pointInTimeTermsVerified=False,
                  historicalCompletenessCertified=False, openingCashAvailabilityVerified=False,
                  economicLedgerReady=False, executionInstructionGenerated=False,
                  certified=False, targetAchieved=False)
    output["contentSha256"] = _hash(output)
    return output
