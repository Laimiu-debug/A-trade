"""Offline, lossless public-source evidence for signal research.

This is NOT a portfolio-history package or an executable entitlement ledger.
Current basic status is never historical membership; a membership observation
holds only on its query date. Adjustment factors are signal/continuity clues,
not cash or share instructions. No network, database or implicit filesystem I/O
is performed by normalize_evidence or action_evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re


VERSION = "signal-public-evidence-v1"
APIS = {"query_history_k_data_plus", "query_stock_basic", "query_adjust_factor",
        "query_dividend_data", "query_all_stock", "query_trade_dates"}


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _day(value, *, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Evidence dates must use YYYY-MM-DD")
    return value


def _stamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("Source retrievedAt must be a timezone-aware timestamp")
    return value


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:sh|sz|bj)\.?\d{6}", value):
        raise ValueError("Invalid evidence security code")
    return value.replace(".", "")


def _number(value, *, positive=False):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a financial number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("Malformed numeric evidence") from None
    if not math.isfinite(result) or result < 0 or positive and result <= 0:
        raise ValueError("Evidence numbers must be finite and nonnegative")
    return result


def _flag(value):
    if value in (None, ""):
        return None
    if value not in ("0", "1", 0, 1):
        raise ValueError("Unknown provider flag value")
    return str(value) == "1" if isinstance(value, str) else bool(value)


def load_snapshot(path, expected_sha256=None):
    """Read one saved response and verify its original byte hash, without writes."""
    path = Path(path).resolve()
    content = path.read_bytes()
    identity = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and identity != expected_sha256:
        raise ValueError("Source file checksum mismatch")
    payload = json.loads(content)
    return {"payload": payload, "source": {"path": str(path), "sha256": identity,
            "payloadSha256": _hash(payload), "byteLength": len(content), "hashBasis": "file-bytes"}}


def _snapshot(value):
    payload = deepcopy(value.get("payload", value))
    if not isinstance(payload, dict):
        raise ValueError("Snapshot must contain a response object")
    source = deepcopy(value.get("source")) if "payload" in value else None
    payload_hash = _hash(payload)
    if source is None:
        source = {"path": None, "sha256": payload_hash, "payloadSha256": payload_hash,
                  "hashBasis": "canonical-json"}
    if source.get("payloadSha256") != payload_hash or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
        raise ValueError("Snapshot payload changed after its source hash was recorded")
    api = payload.get("api")
    if api not in APIS or payload.get("provider") != "baostock":
        raise ValueError("Unsupported evidence provider/API")
    if str(payload.get("errorCode")) != "0" or payload.get("rowBoundReached") is not False or payload.get("complete") is False:
        raise ValueError("Failed or possibly truncated response cannot establish evidence")
    fields, rows = payload.get("fields"), payload.get("rows")
    if not isinstance(fields, list) or len(fields) != len(set(fields)) or not all(isinstance(x, str) and x for x in fields):
        raise ValueError("Invalid or duplicate source fields")
    if not isinstance(rows, list) or not isinstance(payload.get("parameters"), dict):
        raise ValueError("Invalid response rows/parameters")
    _stamp(payload.get("retrievedAt"))
    records = []
    for row in rows:
        if isinstance(row, dict):
            if any(field not in row for field in fields):
                raise ValueError("Source row is missing a declared field")
            records.append(deepcopy(row))
        elif isinstance(row, list) and len(row) >= len(fields):
            record = dict(zip(fields, row))
            if len(row) > len(fields):
                record["_extraFields"] = deepcopy(row[len(fields):])
            records.append(record)
        else:
            raise ValueError("Source row/field width mismatch")
    source.update(api=api, parameters=deepcopy(payload["parameters"]), retrievedAt=payload["retrievedAt"],
                  provider="baostock", rowCount=len(records), raw=payload)
    return source, records


def normalize_evidence(snapshots, *, start, end, selected_symbols=None):
    """Normalize saved public envelopes; unknowns remain unknown, never zeros.

    Inputs are envelopes or load_snapshot() wrappers. selected_symbols is an
    externally frozen sample, never selected here by future data completeness.
    Source responses remain losslessly embedded, including undocumented fields.
    """
    start, end = _day(start), _day(end)
    if start > end:
        raise ValueError("Evidence period is reversed")
    selected = None if selected_symbols is None else {_symbol(x) for x in selected_symbols}
    sources, securities, members, calendar, issues = {}, {}, {}, {}, []

    def issue(code, symbol=None, day=None, refs=(), **details):
        issues.append({"code": code, "symbol": symbol, "date": day, "sourceRefs": sorted(set(refs)), **details})

    def security(symbol):
        return securities.setdefault(symbol, {"symbol": symbol, "bars": {}, "basicObservations": [],
            "factorEvents": {}, "dividendEvents": {}, "listingDate": None, "delistDate": None,
            "stByDate": {}, "tradeStatusByDate": {}, "precloseByDate": {}, "memberByDate": {},
            "corporateActionsComplete": False, "actionKnownByDate": {}})

    def merge(table, key, value, ref):
        previous = table.get(key)
        if previous is not None:
            if {k: v for k, v in previous.items() if k != "sourceRefs"} != value:
                raise ValueError(f"Conflicting duplicate evidence key: {key}")
            previous["sourceRefs"] = sorted(set(previous["sourceRefs"] + [ref]))
        else:
            table[key] = {**value, "sourceRefs": [ref]}

    for snapshot in snapshots:
        source, rows = _snapshot(snapshot)
        ref, api, params = source["sha256"], source["api"], source["parameters"]
        if ref in sources:
            continue
        sources[ref] = source
        if api == "query_history_k_data_plus" and (params.get("frequency") != "d" or str(params.get("adjustflag")) != "3"):
            raise ValueError("Execution evidence accepts only unadjusted daily prices")
        if api in ("query_all_stock", "query_trade_dates"):
            if api == "query_all_stock":
                day = _day(params.get("day", params.get("date")))
                if not start <= day <= end:
                    raise ValueError("Membership query lies outside evidence period")
                observations = {}
                for row in rows:
                    symbol = _symbol(row.get("code"))
                    if symbol in observations:
                        raise ValueError("Duplicate member in a dated snapshot")
                    observations[symbol] = {"tradeStatus": _flag(row.get("tradeStatus")), "raw": row}
                merge(members, day, {"date": day, "members": sorted(observations),
                      "observations": observations, "scope": "query-date-only", "interpolated": False}, ref)
            else:
                seen = set()
                for row in rows:
                    day = _day(row.get("calendar_date"))
                    if not _day(params.get("start_date")) <= day <= _day(params.get("end_date")):
                        raise ValueError("Calendar row outside query range")
                    if day in seen:
                        raise ValueError("Duplicate calendar date in response")
                    seen.add(day)
                    opened = _flag(row.get("is_trading_day"))
                    if opened is None:
                        raise ValueError("Calendar trading-day state is missing")
                    if start <= day <= end:
                        merge(calendar, day, {"date": day, "isTradingDay": opened}, ref)
            continue
        expected = _symbol(params.get("code"))
        if selected is not None and expected not in selected:
            continue
        sec = security(expected)
        seen = set()
        for row in rows:
            if _symbol(row.get("code")) != expected:
                raise ValueError("Response security does not match its query")
            if api == "query_history_k_data_plus":
                day = _day(row.get("date"))
                if not _day(params.get("start_date")) <= day <= _day(params.get("end_date")) or day > end:
                    raise ValueError("Daily observation is outside request/evidence cutoff")
                if day in seen:
                    raise ValueError("Duplicate daily observation")
                seen.add(day)
                if day < start:
                    continue
                if str(row.get("adjustflag")) != "3":
                    raise ValueError("Adjusted row cannot be raw execution evidence")
                values = {key: _number(row.get(key)) for key in ("open", "high", "low", "close", "volume", "amount", "preclose")}
                trading, st = _flag(row.get("tradestatus")), _flag(row.get("isST"))
                valid = all(values[k] is not None and values[k] > 0 for k in ("open", "high", "low", "close"))
                if valid and (values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"])):
                    raise ValueError("Raw OHLC envelope is inconsistent")
                if trading is True and (not valid or values["volume"] is None or values["amount"] is None):
                    raise ValueError("Trading-day price/volume evidence is incomplete")
                if not valid:
                    issue("unusableOhlc", expected, day, [ref])
                if values["preclose"] in (None, 0):
                    values["preclose"] = None
                    issue("unknownReferencePreclose", expected, day, [ref])
                merge(sec["bars"], day, {"date": day, **values, "validOhlc": valid,
                      "tradeStatus": trading, "isST": st, "priceBasisId": "raw-unadjusted"}, ref)
            elif api == "query_stock_basic":
                listing, delist = _day(row.get("ipoDate"), optional=True), _day(row.get("outDate"), optional=True)
                if listing and delist and delist < listing:
                    raise ValueError("Delisting precedes listing")
                item = {"listingDate": listing, "delistDate": delist, "currentStatus": row.get("status"),
                        "currentType": row.get("type"), "currentName": row.get("code_name"),
                        "retrievedAt": source["retrievedAt"], "sourceRefs": [ref],
                        "historicalStatusInferred": False, "historicalAvailabilityVerified": False}
                sec["basicObservations"].append(item)
                for key, value in (("listingDate", listing), ("delistDate", delist)):
                    if sec[key] is not None and value is not None and sec[key] != value:
                        raise ValueError("Conflicting listing/delisting dates")
                    if value is not None:
                        sec[key] = value
            elif api == "query_adjust_factor":
                day = _day(row.get("dividOperateDate"))
                if not _day(params.get("start_date")) <= day <= _day(params.get("end_date")) or day > end:
                    raise ValueError("Factor event outside request/evidence cutoff")
                if day in seen:
                    raise ValueError("Duplicate factor event")
                seen.add(day)
                values = {key: _number(row.get(key), positive=True) for key in ("foreAdjustFactor", "backAdjustFactor", "adjustFactor")}
                if all(v is None for v in values.values()):
                    raise ValueError("Factor event contains no factors")
                merge(sec["factorEvents"], day, {"date": day, **values, "purpose": "signal-clue-only"}, ref)
            else:
                dates = {key: _day(row.get(field), optional=True) for key, field in (
                    ("preNoticeDate", "dividPreNoticeDate"), ("agmDate", "dividAgmPumDate"),
                    ("planAnnouncementDate", "dividPlanAnnounceDate"), ("reportedPlanDate", "dividPlanDate"),
                    ("recordDate", "dividRegistDate"), ("exDate", "dividOperateDate"),
                    ("cashPayDate", "dividPayDate"), ("shareListingDate", "dividStockMarketDate"))}
                ident = _hash({"symbol": expected, "row": row})
                if ident in seen:
                    raise ValueError("Duplicate dividend row")
                seen.add(ident)
                numeric = {key: _number(row.get(field)) for key, field in (
                    ("cashGrossPerShare", "dividCashPsBeforeTax"), ("stockDividendPerShare", "dividStocksPs"),
                    ("capitalizationPerShare", "dividReserveToStockPs"))}
                chronology = []
                if dates["recordDate"] and dates["exDate"] and dates["recordDate"] >= dates["exDate"]:
                    chronology.append("recordNotBeforeEx")
                for key in ("cashPayDate", "shareListingDate"):
                    if dates[key] and dates["exDate"] and dates[key] < dates["exDate"]:
                        chronology.append(key + "BeforeEx")
                shares = (numeric["stockDividendPerShare"] + numeric["capitalizationPerShare"]
                          if numeric["stockDividendPerShare"] is not None and numeric["capitalizationPerShare"] is not None else None)
                value = {"id": ident, "symbol": expected, **dates, **numeric, "totalBonusSharesPerShare": shares,
                         "cashAfterTaxText": row.get("dividCashPsAfterTax") or None, "cashNetPerShare": None,
                         "knownByDate": None, "pointInTimeTermsVerified": False, "economicLedgerReady": False,
                         "shareRatioSemanticsVerified": False, "reportedDateSemanticsVerified": False,
                         "chronologyIssues": chronology, "raw": row}
                merge(sec["dividendEvents"], ident, value, ref)
                for code in chronology:
                    issue(code, expected, dates["exDate"], [ref], eventId=ident)

    sessions = [day for day, row in sorted(calendar.items()) if row["isTradingDay"]]
    session_index = {day: i for i, day in enumerate(sessions)}
    coverage_days = [(date.fromisoformat(start) + timedelta(days=i)).isoformat()
                     for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)]
    if selected:
        for symbol in selected:
            security(symbol)
    for symbol, sec in securities.items():
        bars = sorted(sec["bars"].values(), key=lambda x: x["date"])
        sec["bars"] = bars
        sec["factorEvents"] = sorted(sec["factorEvents"].values(), key=lambda x: x["date"])
        sec["dividendEvents"] = sorted(sec["dividendEvents"].values(), key=lambda x: (x["exDate"] or "", x["id"]))
        sec["listingInterval"] = {"startInclusive": sec["listingDate"], "endExclusive": sec["delistDate"],
                                  "basis": "retrospective-provider-dated-metadata", "isMembershipProof": False}
        expected_days = ([day for day in sessions if day >= sec["listingDate"]
                          and (sec["delistDate"] is None or day < sec["delistDate"])] if sec["listingDate"] else None)
        observed_days = {bar["date"] for bar in bars}
        sec["dailyCoverage"] = {"observedRows": len(bars),
            "expectedRowsWithinReportedListingInterval": len(expected_days) if expected_days is not None else None,
            "missingCalendarSessions": [day for day in expected_days if day not in observed_days] if expected_days is not None else None,
            "historicalEligibilityProven": False}
        for day, snapshot in sorted(members.items()):
            if symbol in snapshot["members"]:
                sec["memberByDate"][day] = True
                if sec["listingDate"] and day < sec["listingDate"] or sec["delistDate"] and day >= sec["delistDate"]:
                    issue("membershipOutsideListingInterval", symbol, day, snapshot["sourceRefs"])
        action_dates = {a["exDate"] for a in sec["dividendEvents"] if a["exDate"]}
        factor_dates = {a["date"] for a in sec["factorEvents"]}
        previous = None
        for bar in bars:
            day, refs = bar["date"], bar["sourceRefs"]
            sec["stByDate"][day], sec["tradeStatusByDate"][day] = bar["isST"], bar["tradeStatus"]
            sec["precloseByDate"][day] = bar["preclose"]
            sec["actionKnownByDate"][day] = None
            if day in calendar and not calendar[day]["isTradingDay"]:
                issue("dailyBarOnClosedCalendarDate", symbol, day, refs)
            if sec["listingDate"] and day < sec["listingDate"] or sec["delistDate"] and day >= sec["delistDate"]:
                issue("dailyBarOutsideListingInterval", symbol, day, refs)
            adjacent = previous is not None and day in session_index and previous["date"] in session_index and session_index[day] == session_index[previous["date"]] + 1
            if adjacent and previous["close"] and bar["preclose"] is not None:
                changed = abs(bar["preclose"] - previous["close"]) > max(.005, previous["close"] * 1e-7)
                if changed:
                    issue("referencePriceDiscontinuity", symbol, day, refs + previous["sourceRefs"],
                          previousClose=previous["close"], preclose=bar["preclose"],
                          dividendObserved=day in action_dates, factorObserved=day in factor_dates)
            previous = bar
        for day in sorted(factor_dates - action_dates):
            issue("factorWithoutDividendDetails", symbol, day,
                  next(row["sourceRefs"] for row in sec["factorEvents"] if row["date"] == day))
        sec["unknown"] = ["completeCorporateActionCoverage", "pointInTimeActionTerms", "individualDividendTax",
                          "rightsMergerSettlementCoverage", "membershipBetweenSnapshots", "openingAuctionCapacity"]
        if sec["listingDate"] is None:
            sec["unknown"].append("listingDate")
        if sec["delistDate"] is None:
            sec["unknown"].append("delistDateNotReported")
    package = {"schema": VERSION, "coverage": {"startDate": start, "endDate": end},
               "selection": {"symbols": sorted(securities), "mode": "explicit-provided" if selected is not None else "all-provided-responses"},
               "sources": [sources[key] for key in sorted(sources)], "calendar": sessions,
               "calendarObservations": [calendar[key] for key in sorted(calendar)],
               "membershipSnapshots": [members[key] for key in sorted(members)],
               "securities": [securities[key] for key in sorted(securities)], "issues": issues,
               "quality": {"certified": False, "completeMembership": False, "completeActions": False,
                           "calendarCoverageComplete": all(day in calendar for day in coverage_days),
                           "pointInTimeTermsVerified": False, "economicLedgerReady": False},
               "limitations": ["Current basic status/name is not historical status or membership.",
                               "Membership is observed only on each queried date, never interpolated.",
                               "No factor/dividend rows or continuous preclose do not prove absence of all corporate actions.",
                               "Retrospective dated facts do not prove that final terms were available on those dates.",
                               "Cash is gross; net tax and cash availability at an opening are unknown.",
                               "Bonus and capitalization ratios are separate; a missing component is not zero."]}
    package["fingerprint"] = _hash(package)
    return package


def action_evidence(package, symbol, *, as_of, start, end):
    """Conservative query for a sequential detector; never backfills certainty.

    An empty exhaustive ledger with independently verified coverage and historic
    availability would be needed to prove a no-event window. These public APIs
    do not supply that proof, so status remains unknown even if prices appear
    continuous. Observed events are retrospective audit clues, not causal inputs.
    """
    symbol, as_of, start, end = _symbol(symbol), _day(as_of), _day(start), _day(end)
    if start > end or end > as_of:
        raise ValueError("Action query must be a past/current window, never future-conditioned")
    if start < package["coverage"]["startDate"] or end > package["coverage"]["endDate"]:
        raise ValueError("Action query lies outside package coverage")
    sec = next((s for s in package["securities"] if s["symbol"] == symbol), None)
    if sec is None:
        raise ValueError("Security absent from evidence package")
    events = [deepcopy(x) for x in sec["dividendEvents"] if x["exDate"] and start <= x["exDate"] <= end]
    factors = [deepcopy(x) for x in sec["factorEvents"] if start <= x["date"] <= end]
    bars = [x for x in sec["bars"] if start <= x["date"] <= end]
    by_date = {x["date"]: x for x in sec["bars"]}
    sessions = package["calendar"]
    requested_sessions = [day for day in sessions if start <= day <= end]
    gaps = [day for day in requested_sessions if day not in by_date]
    refs = {ref for row in events + factors + bars for ref in row["sourceRefs"]}
    # This is only a raw-reference observation. Equal references cannot rule out
    # offsetting, non-price, or unreported corporate actions.
    continuity, observed_changes = None, False
    calendar_start = start
    if requested_sessions and sessions.index(requested_sessions[0]) > 0:
        calendar_start = sessions[sessions.index(requested_sessions[0]) - 1]
    calendar_rows = {row["date"]: row for row in package["calendarObservations"]}
    wanted_days = [(date.fromisoformat(calendar_start) + timedelta(days=i)).isoformat()
                   for i in range((date.fromisoformat(end) - date.fromisoformat(calendar_start)).days + 1)]
    missing_calendar = [day for day in wanted_days if day not in calendar_rows]
    for day in wanted_days:
        if day in calendar_rows:
            refs.update(calendar_rows[day]["sourceRefs"])
    complete = bool(requested_sessions) and not missing_calendar and not gaps
    for day in requested_sessions:
        i = sessions.index(day)
        previous, current = (by_date.get(sessions[i - 1]) if i else None), by_date.get(day)
        if not previous or not current or not previous["close"] or current["preclose"] is None:
            complete = False
            continue
        refs.update(previous["sourceRefs"])
        changed = abs(current["preclose"] - previous["close"]) > max(.005, previous["close"] * 1e-7)
        observed_changes = observed_changes or changed
    if observed_changes:
        continuity = False
    elif complete:
        continuity = True
    source_map = {source["sha256"]: source for source in package["sources"]}
    for event in events:
        event["evidenceDates"] = {"effectiveDate": event["exDate"], "reportedPlanDate": event["reportedPlanDate"],
                                  "retrievedAt": sorted({source_map[ref]["retrievedAt"] for ref in event["sourceRefs"]})}
    refs = sorted(refs)
    return {"status": "unknown", "start": start, "end": end, "asOf": as_of, "sourceRefs": refs,
            "sourceHashes": refs, "pointInTimeTermsVerified": False, "rawReferenceContinuityObserved": continuity,
            "priceBasisId": "raw-unadjusted", "observedEffectiveEvents": events, "observedFactorEvents": factors,
            "missingCalendarSessions": gaps, "missingCalendarDates": missing_calendar,
            "actionKnownByDate": None, "noEventsProven": False,
            "bridgeVerified": False, "economicLedgerReady": False,
            "reasons": ["complete-action-coverage-unproven", "historical-final-terms-availability-unproven"],
            "use": "audit-only; not an eligibility or no-action certificate"}


def write_evidence(package, path):
    """Write one new independent JSON artifact; never overwrite or open a DB."""
    if package.get("schema") != VERSION or package.get("fingerprint") != _hash({k: v for k, v in package.items() if k != "fingerprint"}):
        raise ValueError("Evidence package fingerprint mismatch")
    path = Path(path).resolve()
    with path.open("xb") as stream:
        stream.write(_encoded(package) + b"\n")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
