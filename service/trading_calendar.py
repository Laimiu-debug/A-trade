"""Published mainland exchange sessions, with explicit year coverage.

Government make-up working weekends remain exchange holidays. Unknown years
return None instead of silently treating their weekdays as trading sessions.
These annual schedules do not predict emergency exchange closures.
"""
from __future__ import annotations

import datetime as dt


CALENDAR_SOURCES = {
    2025: [
        "https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml",
        "https://www.szse.cn/disclosure/notice/t20241223_611283.html",
    ],
    2026: [
        "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml",
        "https://www.szse.cn/disclosure/notice/general/t20251222_618087.html",
        "https://www.bse.cn/important_news/200027428.html",
    ],
}
_CLOSURES = {
    2025: [("01-01", "01-01"), ("01-28", "02-04"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("05-31", "06-02"), ("10-01", "10-08")],
    2026: [("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
           ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"),
           ("10-01", "10-07")],
}


def _date(value):
    if not isinstance(value, str):
        raise ValueError("交易日期须为 YYYY-MM-DD")
    day = dt.date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError("交易日期须为 YYYY-MM-DD")
    return day


def trading_sessions(start: str, end: str) -> list[str] | None:
    """Return planned sessions in [start, end], or None outside covered years."""
    first, last = _date(start), _date(end)
    if first > last:
        return []
    if any(year not in _CLOSURES for year in range(first.year, last.year + 1)):
        return None
    sessions = []
    day = first
    while day <= last:
        mmdd = day.strftime("%m-%d")
        if day.weekday() < 5 and not any(begin <= mmdd <= finish for begin, finish in _CLOSURES[day.year]):
            sessions.append(day.isoformat())
        day += dt.timedelta(days=1)
    return sessions
