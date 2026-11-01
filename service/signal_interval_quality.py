"""Quality-only audit of raw 32-byte TDX daily records over a frozen calendar.

Content classification and source ordering are separate: a valid cell is not
trading eligibility. Structural issues are never repaired by sorting/deduping.
No price, amount, volume value, return or strategy output is returned.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import hashlib
import math
import struct


SCHEMA = "tdx-interval-quality-v1"
RECORD = struct.Struct("<IIIIIfII")
STATES = ("validPositiveActivity", "validZeroVolumeOrAmount", "invalidRecord", "duplicateDate", "missingRecord")
INVALID_FLAGS = {"nonPositiveOhlc", "inconsistentOhlc", "negativeAmount", "nonfiniteAmount", "negativeOrNonintegerVolume"}


def _iso_day(value):
    if not isinstance(value, str):
        raise ValueError("Calendar and interval dates must be canonical ISO strings")
    try:
        valid = date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Calendar and interval dates must be canonical ISO strings")
    return value


def _raw_day(value):
    year, month_day = divmod(value, 10000)
    month, day = divmod(month_day, 100)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _flags(values):
    _, opening, high, low, close, amount, volume, _ = values
    flags = []
    if min(opening, high, low, close) <= 0:
        flags.append("nonPositiveOhlc")
    if not low <= min(opening, close) <= max(opening, close) <= high:
        flags.append("inconsistentOhlc")
    if not math.isfinite(amount):
        flags.append("nonfiniteAmount")
    elif amount < 0:
        flags.append("negativeAmount")
    if type(volume) is not int or volume < 0:
        flags.append("negativeOrNonintegerVolume")
    if volume == 0:
        flags.append("zeroVolume")
    if amount == 0:
        flags.append("zeroAmount")
    return flags


def _counts(states):
    counter = Counter(states)
    return {key: counter[key] for key in STATES}


def scan_daily(raw: bytes, calendar: list[str], *, start: str, end: str) -> dict:
    """Classify every calendar date once; retain all raw structural diagnostics.

    start/end are inclusive. Calendar dates need not include either boundary
    (a boundary may be a nontrading date); every supplied date must be within
    the interval, strictly ordered and unique. An empty calendar is allowed.

    Order counts use adjacent *valid-dated* raw records, skipping unassignable
    dates while recording their zero-based record indices. In-interval order
    flags only involve pairs whose two dates are within the interval. These
    flags never change a date's content state. Duplicate dates take precedence
    over invalid content, then zero activity, then positive activity.
    """
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    start, end = _iso_day(start), _iso_day(end)
    if start > end:
        raise ValueError("start must not follow end")
    if not isinstance(calendar, list):
        raise ValueError("calendar must be a list of frozen ISO dates")
    days = [_iso_day(day) for day in calendar]
    if days != sorted(set(days)) or any(not start <= day <= end for day in days):
        raise ValueError("calendar must be unique, ordered and strictly within start..end")

    day_set, by_date = set(days), defaultdict(list)
    invalid_indices, transitions, off_calendar, decoded_dates = [], [], [], []
    raw_date_counts = Counter()
    interval_order_days, observed_dates = set(), set()
    prior, in_interval_count, outside_count, prefix_records = None, 0, 0, 0
    raw_count, trailing = divmod(len(raw), RECORD.size)
    for index in range(raw_count):
        values = RECORD.unpack_from(raw, index*RECORD.size)
        day = _raw_day(values[0])
        decoded_dates.append(day)
        if day is None:
            invalid_indices.append(index)
            continue
        raw_date_counts[day] += 1
        if day <= end:
            prefix_records = index+1
        if prior is not None and day <= prior["date"]:
            in_interval = start <= day <= end and start <= prior["date"] <= end
            transitions.append({"previousRecordIndex": prior["recordIndex"], "recordIndex": index,
                                "previousDate": prior["date"], "date": day, "withinInterval": in_interval})
            if in_interval:
                interval_order_days.update((prior["date"], day))
        prior = {"recordIndex": index, "date": day}
        if not start <= day <= end:
            outside_count += 1
            continue  # Out-of-interval OHLC/amount/volume are deliberately not examined.
        in_interval_count += 1
        observed_dates.add(day)
        record = {"recordIndex": index, "date": day, "flags": _flags(values)}
        if day in day_set:
            by_date[day].append(record)
        else:
            off_calendar.append(record)

    records, year_states = [], defaultdict(list)
    for day in days:
        matches = by_date.get(day, [])
        flags = set(flag for row in matches for flag in row["flags"])
        if len(matches) > 1:
            state = "duplicateDate"
            flags.add("duplicateDate")
        elif not matches:
            state = "missingRecord"
        elif flags & INVALID_FLAGS:
            state = "invalidRecord"
        elif flags & {"zeroVolume", "zeroAmount"}:
            state = "validZeroVolumeOrAmount"
        else:
            state = "validPositiveActivity"
        if day in interval_order_days:
            flags.add("nonIncreasingDateOrder")
        records.append({"date": day, "state": state, "flags": sorted(flags)})
        year_states[day[:4]].append(state)

    observed_calendar = sorted(by_date)
    observed = sorted(observed_dates)
    duplicate_dates = sorted(day for day, rows in by_date.items() if len(rows) > 1)
    raw_duplicates = {day: count for day, count in sorted(raw_date_counts.items()) if count > 1}
    off_dates = sorted({row["date"] for row in off_calendar})
    structure_reasons = []
    for condition, reason in ((bool(invalid_indices), "invalidDateRecords"), (bool(trailing), "trailingBytes"),
                              (bool(transitions), "nonIncreasingDateTransitions"),
                              (bool(raw_duplicates), "duplicateValidDates"), (bool(off_dates), "offCalendarDates")):
        if condition:
            structure_reasons.append(reason)
    counts = _counts(row["state"] for row in records)
    prefix_bytes = prefix_records*RECORD.size
    prefix_dates = decoded_dates[:prefix_records]
    prefix_reasons = []
    if any(day is None for day in prefix_dates):
        prefix_reasons.append("invalidDateInPrefix")
    if any(day is not None and day > end for day in prefix_dates):
        prefix_reasons.append("dateAfterEndInPrefix")
    if any(row["recordIndex"] < prefix_records for row in transitions):
        prefix_reasons.append("nonIncreasingDateInPrefix")
    if trailing:
        prefix_reasons.append("incompleteRawTailHasUnknownDate")
    return {"schema": SCHEMA, "certified": False, "returnsCalculated": False, "strategyEvaluated": False,
            "recordSize": RECORD.size, "rawRecordCount": raw_count,
            "validDateRecords": raw_count-len(invalid_indices), "invalidDateRecords": len(invalid_indices),
            "invalidDateRecordIndices": invalid_indices, "trailingBytes": trailing,
            "nonIncreasingDateTransitions": len(transitions),
            "intervalNonIncreasingDateTransitions": sum(row["withinInterval"] for row in transitions),
            "dateOrderTransitions": transitions,
            "duplicateDateCounts": raw_duplicates,
            "duplicateValidDateCount": len(raw_duplicates),
            "duplicateValidDateRecords": sum(raw_duplicates.values()),
            "duplicateExcessRecordCount": sum(count-1 for count in raw_duplicates.values()),
            "prefixThroughEndSha256": hashlib.sha256(raw[:prefix_bytes]).hexdigest(),
            "prefixThroughEndBytes": prefix_bytes, "prefixThroughEndRecords": prefix_records,
            "prefixIdentityUsable": not prefix_reasons, "prefixIdentityIssues": prefix_reasons,
            "prefixIdentityMeaning": "Original bytes ending at the last valid date <= end, or empty. Identity-only, not a strategy input or certificate of interval completeness; invalid dates outside this prefix remain in the full diagnostic. Any incomplete raw tail makes this identity unusable.",
            "offCalendarDates": off_dates, "offCalendarRecords": off_calendar,
            "interval": {"start": start, "end": end, "firstObservedDate": observed[0] if observed else None,
                "lastObservedDate": observed[-1] if observed else None,
                "firstObservedCalendarDate": observed_calendar[0] if observed_calendar else None,
                "lastObservedCalendarDate": observed_calendar[-1] if observed_calendar else None,
                "observedCalendarDateCount": len(observed_calendar), "recordCount": in_interval_count,
                "offCalendarRecordCount": len(off_calendar), "outsideIntervalDateRecords": outside_count,
                "duplicateCalendarDateCount": len(duplicate_dates),
                "duplicateCalendarRecordCount": sum(len(by_date[day]) for day in duplicate_dates),
                "duplicateExcessRecordCount": sum(len(by_date[day])-1 for day in duplicate_dates)},
            "counts": counts,
            "summary": {"calendarDates": len(days), "counts": dict(counts),
                "structureIssue": bool(structure_reasons), "structureReasons": structure_reasons,
                "structureCounts": {"invalidDateRecords": len(invalid_indices), "trailingBytes": trailing,
                    "nonIncreasingDateTransitions": len(transitions), "duplicateCalendarDates": len(duplicate_dates),
                    "duplicateValidDates": len(raw_duplicates),
                    "offCalendarDates": len(off_dates), "offCalendarRecords": len(off_calendar)},
                "contentStateMeaning": "Content-only classification; not historical eligibility, repaired source order, execution or certification.",
                "calendarAccountingComplete": sum(counts.values()) == len(days)},
            "byYear": {year: _counts(states) for year, states in sorted(year_states.items())},
            "records": records}
