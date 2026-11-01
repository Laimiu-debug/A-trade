"""Offline coverage evidence, never an eligibility or corporate-action ledger.

All filesystem reads are explicit and hash-checked. Normalization does not use
the current basic name/status, fill calendar gaps, interpolate membership, or
infer absence of actions from an empty provider response.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re


VERSION = "signal-history-coverage-v1"
DAILY = "query_history_k_data_plus"
BASIC = "query_stock_basic"
FACTOR = "query_adjust_factor"
DIVIDEND = "query_dividend_data"
APIS = {DAILY, BASIC, FACTOR, DIVIDEND}
LIMITATIONS = [
    "Coverage is retrospective provider evidence, not point-in-time eligibility certification.",
    "Current basic name/status do not establish historical ST, membership or trading status.",
    "Reported outDate is not translated into an engine delistDate; its boundary rows remain explicit.",
    "Four dated member snapshots apply only to those dates; their union is not a complete historical universe.",
    "An empty factor/dividend response does not prove no corporate actions or a complete economic ledger.",
    "Daily tradestatus and volume do not establish auction execution capacity.",
    "This package does not repair identity/action evidence for the original 3141 local securities.",
    "No returns, strategy selection, user database writes or frozen-sample changes are performed.",
]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _day(value, optional=False):
    if optional and value in (None, ""):
        return None
    _require(isinstance(value, str), "Date must be a YYYY-MM-DD string")
    _require(date.fromisoformat(value).isoformat() == value, "Invalid date")
    return value


def _symbol(value):
    _require(isinstance(value, str) and re.fullmatch(r"(?:sh|sz)\.?\d{6}", value),
             "Invalid SH/SZ symbol")
    return value.replace(".", "")


def read_hashed_json(path, expected_sha256):
    """Require an independently supplied byte hash; return payload and receipt."""
    _require(isinstance(expected_sha256, str) and re.fullmatch(r"[a-f0-9]{64}", expected_sha256),
             "A pinned SHA256 is required")
    path = Path(path)
    data = path.read_bytes()
    _require(sha256(data) == expected_sha256, f"SHA256 mismatch: {path.name}")
    payload = json.loads(data)
    _require(isinstance(payload, dict), "Expected JSON object")
    return payload, {"sha256": expected_sha256, "byteLength": len(data),
                     "file": path.name}


def _child(root, record):
    name = record.get("path")
    _require(isinstance(name, str) and name, "Missing source-relative path")
    relative = Path(name)
    _require(not relative.is_absolute() and ".." not in relative.parts,
             "Unsafe source-relative path")
    path = (root / relative).resolve()
    _require(path.is_relative_to(root.resolve()), "Source path escapes bundle")
    payload, receipt = read_hashed_json(path, record.get("sha256"))
    receipt["file"] = relative.as_posix()
    return payload, receipt


def _records(payload):
    fields, rows = payload.get("fields"), payload.get("rows")
    _require(isinstance(fields, list) and all(isinstance(f, str) for f in fields)
             and len(fields) == len(set(fields)), "Invalid/duplicate response fields")
    _require(isinstance(rows, list), "Response rows must be an array")
    _require(all(isinstance(row, list) and len(row) == len(fields) for row in rows),
             "Response row width mismatch")
    return [dict(zip(fields, row)) for row in rows]


def _response(payload, api, parameters):
    _require(payload.get("provider") == "baostock" and payload.get("api") == api,
             "Response provider/API mismatch")
    _require(payload.get("parameters") == parameters, "Response parameters mismatch")
    _require(payload.get("errorCode") == "0" and payload.get("rowBoundReached") is False,
             "Errored or truncated source response")
    stamp = payload.get("retrievedAt")
    _require(isinstance(stamp, str) and datetime.fromisoformat(stamp).tzinfo is not None,
             "Timezone-aware retrievedAt required")
    return _records(payload)


def load_manifest(path, expected_sha256):
    """Verify one completed v1 or v2 batch, including every immutable raw page."""
    path = Path(path)
    manifest, top = read_hashed_json(path, expected_sha256)
    _require(manifest.get("status") == "completed", "Source manifest is incomplete")
    _require(all(manifest.get("auth", {}).get(key, {}).get("errorCode") == "0"
                 for key in ("login", "logout")), "Source authentication lifecycle incomplete")
    root = path.parent
    protocol, protocol_ref = _child(root, manifest["protocol"])
    requests, requests_ref = _child(root, manifest["requestManifest"])
    _require(requests.get("protocolSha256") == protocol_ref["sha256"],
             "Request/protocol hash mismatch")
    planned = requests.get("requests")
    actual = manifest.get("requests")
    _require(isinstance(planned, list) and isinstance(actual, list), "Missing requests")
    plan = {r["id"]: r for r in planned}
    result = {r["id"]: r for r in actual}
    _require(len(plan) == len(planned) and len(result) == len(actual), "Duplicate request id")
    _require(plan.keys() == result.keys() and len(planned) == protocol.get("requestCount"),
             "Planned/completed request set mismatch")
    audit_hash = requests.get("sourceAuditSha256")
    _require(audit_hash == protocol.get("sourceAuditSha256"), "Source audit hash mismatch")
    sources = [top, protocol_ref, requests_ref]
    responses = []
    for request in actual:
        api, params = request["api"], request["parameters"]
        _require(api in APIS and {k: request[k] for k in ("id", "api", "parameters")} ==
                 {k: plan[request["id"]][k] for k in ("id", "api", "parameters")},
                 "Request differs from frozen request plan")
        _require(request.get("status") == "completed" and request.get("complete") is True
                 and request.get("errorCode") == "0" and request.get("rowBoundReached") is False
                 and request.get("exceptionType") is None, "Incomplete request")
        aggregate, source = _child(root, request)
        _response(aggregate, api, params)
        _require(aggregate.get("complete") is True and aggregate.get("exceptionType") is None,
                 "Incomplete aggregate")
        pages = request.get("pages")
        _require(isinstance(pages, list) and pages and pages == aggregate.get("pages"),
                 "Aggregate/page manifest mismatch")
        _require(request.get("terminalReason") == "shortOrEmptyTerminalPage" ==
                 aggregate.get("terminalReason"), "Unproven pagination termination")
        page_rows, page_sources = [], []
        for number, record in enumerate(pages, 1):
            page, page_ref = _child(root, record)
            _response(page, api, params)
            _require(page.get("fields") == aggregate["fields"], "Page fields drift")
            _require(page.get("pageRowsTruncated") is False, "Truncated raw page")
            _require(str(page.get("sdkPageNumber")) == str(number) == str(record.get("sdkPageNumber")),
                     "Non-contiguous source page numbering")
            count = len(page["rows"])
            _require(page.get("receivedRows") == count == record.get("rowCount"),
                     "Page row count mismatch")
            capacity = int(page.get("sdkPerPageCount", 0))
            _require(capacity > 0 and count <= capacity, "Invalid raw page capacity")
            _require(count == capacity if number < len(pages) else count < capacity,
                     "Missing terminal page or premature page ending")
            page_rows.extend(page["rows"])
            page_sources.append(page_ref)
        _require(page_rows == aggregate["rows"] and request.get("rowCount") == len(page_rows),
                 "Page rows differ from aggregate")
        sources.extend([source, *page_sources])
        responses.append({"payload": aggregate, "sourceHash": source["sha256"],
                          "payloadHash": sha256(encoded(aggregate)),
                          "pageHashes": [s["sha256"] for s in page_sources]})
    selected = sorted({_symbol(r["parameters"]["code"]) for r in actual})
    if "selectedCodes" in manifest:
        _require(sorted(_symbol(s) for s in manifest["selectedCodes"]) == selected,
                 "Selected/requested securities differ")
    if "selected" in requests:
        _require(sorted(_symbol(r.get("symbol", r.get("code"))) for r in requests["selected"]) == selected,
                 "Selected/frozen request securities differ")
    return {"manifestHash": top["sha256"], "sourceAuditHash": audit_hash,
            "sources": sources, "selectedSymbols": selected, "responses": responses}


def load_calendar(path, expected_sha256, *, start, end):
    payload, source = read_hashed_json(path, expected_sha256)
    _day(start); _day(end)
    rows = _response(payload, "query_trade_dates", {"start_date": start, "end_date": end})
    _require(payload["fields"] == ["calendar_date", "is_trading_day"], "Unexpected calendar schema")
    expected = []
    current = date.fromisoformat(start)
    while current <= date.fromisoformat(end):
        expected.append(current.isoformat())
        current += timedelta(days=1)
    _require([r["calendar_date"] for r in rows] == expected, "Calendar has gaps, duplicates or outside dates")
    _require(all(r["is_trading_day"] in ("0", "1") for r in rows), "Unknown calendar flag")
    return {"start": start, "end": end, "dates": [r["calendar_date"] for r in rows
             if r["is_trading_day"] == "1"], "source": source}


def load_universe_audit(path, expected_sha256):
    payload, source = read_hashed_json(path, expected_sha256)
    symbols = [_symbol(s) for s in payload["missingUnionSymbols"]]
    _require(len(set(symbols)) == len(symbols) == payload["missingUnionCount"],
             "Universe count/duplicates mismatch")
    snapshots = payload.get("dailySnapshots", [])
    observations = {s: [] for s in symbols}
    seen_days = set()
    union = set()
    for snapshot in snapshots:
        day = _day(snapshot["date"])
        _require(day not in seen_days, "Duplicate membership observation date")
        seen_days.add(day)
        members = snapshot["missing"]
        _require(len(members) == snapshot["missingFromFrozenSampleCount"], "Membership count mismatch")
        seen = set()
        for row in members:
            symbol = _symbol(row["symbol"])
            _require(symbol in observations and symbol not in seen, "Invalid/duplicate observed member")
            seen.add(symbol); union.add(symbol)
            observations[symbol].append({"date": day, "reportedName": row.get("reportedName"),
                "reportedTradeStatus": row.get("reportedTradeStatus"),
                "sourceHash": snapshot["membershipResponseSha256"], "membershipOnOtherDates": "unknown"})
    _require(union == set(symbols), "Membership observations do not reproduce fixed universe")
    return {"symbols": sorted(symbols), "observations": observations, "source": source,
            "baselineFingerprint": payload.get("baselineFingerprint")}


def _numeric(value):
    if value in (None, ""):
        return None
    _require(not isinstance(value, bool), "Boolean financial value")
    number = float(value)
    _require(math.isfinite(number) and number >= 0, "Invalid financial value")
    return number


def _flag(value):
    if value in ("1", 1) and not isinstance(value, bool):
        return True
    if value in ("0", 0) and not isinstance(value, bool):
        return False
    return None


def normalize_history_coverage(bundles, calendar, audit, *, start, end, require_all=True):
    """Pure evidence merger. Inputs come from the verified loaders above.

    Missing dates between reported IPO/outDate are a coverage classification,
    not proof of legal listing, trading eligibility or a true suspended session.
    """
    _day(start); _day(end)
    _require(start <= end and calendar["start"] == start and calendar["end"] == end,
             "Calendar/coverage interval mismatch")
    days = calendar["dates"]
    _require(days == sorted(set(days)) and all(start <= _day(d) <= end for d in days),
             "Invalid trading dates")
    day_set = set(days)
    expected = set(audit["symbols"])
    responses = {s: {} for s in expected}
    sources, duplicate_responses = [], 0
    for bundle in bundles:
        _require(bundle["sourceAuditHash"] == audit["source"]["sha256"], "Bundle belongs to another universe")
        sources.append({"manifestHash": bundle["manifestHash"], "files": deepcopy(bundle["sources"])})
        for response in bundle["responses"]:
            payload = response["payload"]
            _require(response["payloadHash"] == sha256(encoded(payload)), "Verified response was mutated")
            params, api = payload["parameters"], payload["api"]
            symbol = _symbol(params["code"])
            _require(symbol in expected and api in APIS, "Unexpected security/API")
            rows = _response(payload, api, params)
            _require(all(_symbol(r.get("code")) == symbol for r in rows), "Cross-symbol response row")
            if api in (DAILY, FACTOR):
                _require(params.get("start_date") == start and params.get("end_date") == end,
                         "Request does not cover registered interval")
            if api == DAILY:
                _require(params.get("frequency") == "d" and params.get("adjustflag") == "3",
                         "Only raw daily bars are accepted")
                _require(payload["fields"] == params.get("fields", "").split(",") and
                         {"date", "code", "open", "high", "low", "close", "preclose", "volume",
                          "amount", "adjustflag", "tradestatus", "isST"} <= set(payload["fields"]),
                         "Daily field schema mismatch")
            if api == BASIC:
                _require({"code", "code_name", "ipoDate", "outDate", "type", "status"} <= set(payload["fields"]),
                         "Basic field schema mismatch")
            if api in (FACTOR, DIVIDEND):
                _require({"code", "dividOperateDate"} <= set(payload["fields"]), "Rights field schema mismatch")
            if api == DIVIDEND:
                _require(params.get("yearType") == "operate" and str(params.get("year")) in
                         {str(y) for y in range(int(start[:4]), int(end[:4]) + 1)},
                         "Dividend year lies outside registered scope")
            key = (api, str(params.get("year", "")))
            old = responses[symbol].get(key)
            content = {"fields": payload["fields"], "rows": payload["rows"], "parameters": params}
            if old:
                _require(old["content"] == content, "Conflicting duplicate request response")
                old["sourceHashes"].append(response["sourceHash"])
                duplicate_responses += 1
            else:
                responses[symbol][key] = {"content": content, "rows": rows,
                    "retrievedAt": payload["retrievedAt"], "sourceHashes": [response["sourceHash"]]}
    selected = sorted(s for s, entries in responses.items() if entries)
    _require(selected and (not require_all or set(selected) == expected), "Missing fixed-universe securities")
    required = {(BASIC, ""), (DAILY, ""), (FACTOR, "")} | {
        (DIVIDEND, str(y)) for y in range(int(start[:4]), int(end[:4]) + 1)}
    securities, totals = [], Counter()
    for symbol in selected:
        entries = responses[symbol]
        _require(set(entries) == required, f"Incomplete endpoint evidence for {symbol}")
        basic = entries[(BASIC, "")]
        unique_basic = {encoded(r): r for r in basic["rows"]}
        _require(len(unique_basic) <= 1, "Conflicting basic records")
        raw_basic = next(iter(unique_basic.values()), {})
        ipo, out = _day(raw_basic.get("ipoDate"), True), _day(raw_basic.get("outDate"), True)
        _require(not (ipo and out) or ipo <= out, "Reported outDate precedes IPO")
        daily = entries[(DAILY, "")]
        by_date, duplicate_rows, issues = {}, 0, []
        for raw in daily["rows"]:
            day = _day(raw.get("date"))
            _require(start <= day <= end, "Daily row outside cutoff interval")
            _require(day in day_set, "Daily row outside verified exchange calendar")
            _require(raw.get("adjustflag") == "3", "Adjusted daily row")
            if day in by_date:
                _require(by_date[day]["rawFields"] == raw, "Conflicting duplicate symbol/date")
                duplicate_rows += 1
                continue
            row = {"date": day, **{k: _numeric(raw.get(k)) for k in
                   ("open", "high", "low", "close", "preclose", "volume", "amount")},
                   "isST": _flag(raw.get("isST")), "tradeStatus": _flag(raw.get("tradestatus")),
                   "rawFields": deepcopy(raw), "sourceHashes": sorted(set(daily["sourceHashes"]))}
            row["tradingEvidence"] = ("haltedPlaceholder" if row["tradeStatus"] is False and
                row["volume"] in (None, 0) and row["amount"] in (None, 0) else
                "observedPositiveVolume" if (row["volume"] or 0) > 0 else "unknownOrZeroVolume")
            row["listingBoundary"] = ("beforeReportedIPO" if ipo and day < ipo else
                "atReportedOutDate" if out and day == out else "afterReportedOutDate" if out and day > out
                else "betweenReportedBounds" if ipo and out else
                "onOrAfterReportedIPOWithoutOutDate" if ipo else "unknownListingBoundary")
            reason = []
            o, h, low, close = [row[k] for k in ("open", "high", "low", "close")]
            if all(v is not None for v in (o, h, low, close)) and not low <= min(o, close) <= max(o, close) <= h:
                reason.append("inconsistentOHLC")
            if row["tradeStatus"] is False and ((row["volume"] or 0) > 0 or (row["amount"] or 0) > 0):
                reason.append("haltWithPositiveVolumeOrAmount")
            if row["tradeStatus"] is True and any(v is None or v <= 0 for v in (o, h, low, close)):
                reason.append("tradingFlagWithoutPositiveOHLC")
            if row["listingBoundary"] in ("beforeReportedIPO", "afterReportedOutDate"):
                reason.append("dailyOutsideReportedListingBounds")
            if raw.get("isST") not in ("0", "1", "", None) or raw.get("tradestatus") not in ("0", "1", "", None):
                reason.append("unrecognizedProviderFlag")
            row["qualityIssues"] = reason
            issues.extend({"date": day, "reason": r} for r in reason)
            by_date[day] = row
        missing = [d for d in days if d not in by_date]
        classes = {k: [] for k in ("missingBeforeReportedIPO", "missingAtReportedOutDate",
            "missingAfterReportedOutDate", "missingBetweenReportedBounds",
            "missingAfterReportedIPOWithoutOutDate", "missingListingBoundaryUnknown")}
        for day in missing:
            key = ("missingBeforeReportedIPO" if ipo and day < ipo else
                   "missingAtReportedOutDate" if out and day == out else
                   "missingAfterReportedOutDate" if out and day > out else
                   "missingBetweenReportedBounds" if ipo and out else
                   "missingAfterReportedIPOWithoutOutDate" if ipo else "missingListingBoundaryUnknown")
            classes[key].append(day)
        observed = [by_date[d] for d in sorted(by_date)]
        rights, future_terms, outside_window_rights = [], [], []
        for (api, year), entry in sorted(entries.items()):
            if api not in (FACTOR, DIVIDEND):
                continue
            seen = {}
            for raw in entry["rows"]:
                effective = _day(raw.get("dividOperateDate"), True)
                _require(effective is None or effective <= end and (api == DIVIDEND or effective >= start),
                         "Rights effective event outside cutoff interval")
                if api == DIVIDEND:
                    _require(effective is None or effective[:4] == year, "Dividend operate year mismatch")
                identity = effective if effective else encoded(raw)
                if identity in seen:
                    _require(seen[identity] == raw, "Conflicting duplicate rights effective date")
                    continue
                seen[identity] = raw
                if api == DIVIDEND and effective and effective < start:
                    outside_window_rights.append({"api": api, "effectiveDate": effective,
                        "rawFields": deepcopy(raw), "sourceHashes": sorted(set(entry["sourceHashes"])),
                        "reason": "annualOperateYearQueryIncludesDatesBeforeAnalysisStart",
                        "usedForHistoricalEligibilityOrWindowActions": False})
                    continue
                for field, value in raw.items():
                    if field.endswith("Date") and value not in (None, ""):
                        value = _day(value)
                        if value > end:
                            future_terms.append({"api": api, "effectiveDate": effective,
                                "field": field, "reportedDate": value, "usedForHistoricalEligibility": False})
                rights.append({"api": api, "effectiveDate": effective, "rawFields": deepcopy(raw),
                               "sourceHashes": sorted(set(entry["sourceHashes"]))})
        coverage = {"calendarTradingDays": len(days), "observedDates": [r["date"] for r in observed],
            "observedDailyRowCount": len(observed), "noDailyRows": not observed,
            "firstObservedDate": observed[0]["date"] if observed else None,
            "lastObservedDate": observed[-1]["date"] if observed else None,
            "missingDates": missing, **classes, "exactDuplicateRowsCollapsed": duplicate_rows,
            "observedStrictlyBeforeOutDate": [r["date"] for r in observed if out and r["date"] < out],
            "observedAtOutDate": [r for r in observed if out and r["date"] == out],
            "observedAfterOutDate": [r["date"] for r in observed if out and r["date"] > out],
            "dailyStatusCounts": dict(Counter("unknown" if r["tradeStatus"] is None else
                "trading" if r["tradeStatus"] else "halted" for r in observed)),
            "dailySTCounts": dict(Counter("unknown" if r["isST"] is None else
                "ST" if r["isST"] else "notST" for r in observed)),
            "unknownPrecloseCount": sum(r["preclose"] is None for r in observed),
            "boundarySemantics": "reported metadata only; outDate boundary never implies execution eligibility"}
        security = {"symbol": symbol, "reportedBasic": {"ipoDate": ipo, "outDate": out,
            "currentName": raw_basic.get("code_name"), "currentStatus": raw_basic.get("status"),
            "rawFields": deepcopy(raw_basic), "sourceHashes": sorted(set(basic["sourceHashes"])),
            "retrievedAt": basic["retrievedAt"], "historicalEligibilityUse": False,
            "listingBoundsBasis": "retrospective-provider-metadata;not-eligibility-proof"},
            "coverage": coverage, "daily": observed, "membershipObservations": deepcopy(audit["observations"][symbol]),
            "corporateActions": {"status": "unknown", "noEventsProven": False,
                "pointInTimeTermsVerified": False, "rows": rights, "futureReportedTerms": future_terms,
                "outsideAnalysisWindowReportedRows": outside_window_rights,
                "factorRowCount": sum(r["api"] == FACTOR for r in rights),
                "dividendRowCount": sum(r["api"] == DIVIDEND for r in rights),
                "outsideAnalysisWindowDividendRowCount": len(outside_window_rights)}, "qualityIssues": issues}
        securities.append(security)
        totals.update({"observedDailyRows": len(observed), "missingDates": len(missing),
            "noDailySecurities": int(not observed), "qualityIssues": len(issues),
            "observedAtOutDate": len(coverage["observedAtOutDate"]),
            "haltedPlaceholdersAtOutDate": sum(r["tradingEvidence"] == "haltedPlaceholder" for r in coverage["observedAtOutDate"]),
            "exactDuplicateRowsCollapsed": duplicate_rows})
        totals.update({k: len(v) for k, v in classes.items()})
    package = {"schema": VERSION, "range": {"start": start, "end": end},
        "fixedUniverseCount": len(expected), "includedSecurityCount": len(securities),
        "notYetSuppliedSymbols": sorted(expected - set(selected)), "partialEvidenceOnly": set(selected) != expected,
        "sourceAudit": deepcopy(audit["source"]), "calendar": deepcopy(calendar), "sources": sources,
        "baselineFingerprint": audit.get("baselineFingerprint"), "totals": dict(totals),
        "exactDuplicateResponsesCollapsed": duplicate_responses, "securities": securities,
        "returnsCalculated": False, "certified": False, "targetAchieved": False,
        "limitations": LIMITATIONS}
    package["fingerprint"] = sha256(encoded(package))
    return package


def coverage_summary(package):
    result = {k: deepcopy(v) for k, v in package.items() if k not in ("securities", "sources")}
    result["sources"] = [{"manifestHash": s["manifestHash"], "verifiedFileCount": len(s["files"])}
                         for s in package["sources"]]
    result["securities"] = [{"symbol": s["symbol"], "reportedBasic": s["reportedBasic"],
        "coverage": s["coverage"], "qualityIssues": s["qualityIssues"],
        "membershipObservedDates": [r["date"] for r in s["membershipObservations"]],
        "corporateActions": {k: v for k, v in s["corporateActions"].items() if k != "rows"}}
        for s in package["securities"]]
    return result
