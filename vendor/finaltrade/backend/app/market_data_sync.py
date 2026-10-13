from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timedelta
from pathlib import Path


def resolve_user_path(path_text: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(path_text).strip()))
    return Path(expanded)


def normalize_symbol(raw: str) -> str | None:
    text = str(raw).strip().lower()
    if not text:
        return None
    if text.startswith(("sh", "sz", "bj")) and len(text) >= 8:
        code = text[2:8]
        market = text[:2]
        if len(code) == 6 and code.isdigit():
            return f"{market}{code}"
    if "." in text:
        market, code = text.split(".", 1)
        market = market.strip()
        code = code.strip()
        if market in {"sh", "sz", "bj"} and len(code) == 6 and code.isdigit():
            return f"{market}{code}"
    code = text[:6]
    if len(code) == 6 and code.isdigit():
        if code.startswith("6"):
            return f"sh{code}"
        if code.startswith(("4", "8")):
            return f"bj{code}"
        return f"sz{code}"
    return None


def normalize_date_text(text: str) -> str:
    raw = str(text).strip()
    if not raw:
        raise ValueError("empty date")
    if "-" in raw:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    else:
        dt = datetime.strptime(raw, "%Y%m%d")
    return dt.strftime("%Y%m%d")


def yyyymmdd_to_dash(date_text: str) -> str:
    return datetime.strptime(date_text, "%Y%m%d").strftime("%Y-%m-%d")


def read_existing(file_path: Path) -> dict[str, dict[str, str]]:
    if not file_path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with file_path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            date_text = str(row.get("date") or "").strip()
            if len(date_text) < 10:
                continue
            date_key = date_text[:10]
            rows[date_key] = {
                "date": date_key,
                "open": str(row.get("open") or ""),
                "high": str(row.get("high") or ""),
                "low": str(row.get("low") or ""),
                "close": str(row.get("close") or ""),
                "volume": str(row.get("volume") or "0"),
                "amount": str(row.get("amount") or "0"),
                "symbol": str(row.get("symbol") or ""),
            }
    return rows


def write_rows(file_path: Path, rows: dict[str, dict[str, str]]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [rows[date] for date in sorted(rows.keys())]
    with file_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["date", "open", "high", "low", "close", "volume", "amount", "symbol"],
        )
        writer.writeheader()
        writer.writerows(ordered)


def latest_existing_yyyymmdd(existing: dict[str, dict[str, str]]) -> str | None:
    latest: datetime | None = None
    for key in existing.keys():
        text = str(key).strip()
        if len(text) < 10:
            continue
        try:
            current = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if latest is None or current > latest:
            latest = current
    if latest is None:
        return None
    return latest.strftime("%Y%m%d")


def resolve_symbol_start_date(
    *,
    explicit_start: str,
    full_history: bool,
    default_start: str,
    end_date: str,
    existing: dict[str, dict[str, str]],
) -> str | None:
    if explicit_start:
        return explicit_start if explicit_start <= end_date else None
    if full_history:
        return default_start if default_start <= end_date else None
    latest = latest_existing_yyyymmdd(existing)
    if latest:
        # Incremental mode still refreshes recent rows so latest-day corrections can be picked up.
        backfill_start = (datetime.strptime(latest, "%Y%m%d") - timedelta(days=2)).strftime("%Y%m%d")
        candidate = max(backfill_start, default_start)
        return candidate if candidate <= end_date else None
    return default_start if default_start <= end_date else None


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _is_a_share(symbol: str) -> bool:
    if len(symbol) != 8:
        return False
    market = symbol[:2]
    code = symbol[2:]
    if not code.isdigit():
        return False
    if market == "sh":
        return code.startswith("6")
    if market == "sz":
        return code.startswith(("0", "2", "3"))
    if market == "bj":
        return code.startswith(("4", "8"))
    return False


def _collect_symbols_from_text(symbols_text: str) -> list[str]:
    tokens = [item.strip() for item in str(symbols_text).split(",") if item.strip()]
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        symbol = normalize_symbol(token)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _collect_baostock_symbols(*, bs, end_date_dash: str, limit: int) -> list[str]:
    rs = bs.query_all_stock(day=end_date_dash)
    if rs.error_code != "0":
        raise RuntimeError(f"query_all_stock failed: {rs.error_code} {rs.error_msg}")
    symbols: list[str] = []
    seen: set[str] = set()
    while rs.next():
        row = rs.get_row_data()
        if not row:
            continue
        normalized = normalize_symbol(row[0])
        if not normalized or normalized in seen:
            continue
        if not _is_a_share(normalized):
            continue
        seen.add(normalized)
        symbols.append(normalized)
        if limit > 0 and len(symbols) >= limit:
            break
    return symbols


def _to_baostock_symbol(symbol: str) -> str:
    return f"{symbol[:2]}.{symbol[2:]}"


def sync_baostock_daily(
    *,
    symbols_text: str,
    all_market: bool,
    limit: int,
    mode: str,
    start_date: str,
    end_date: str,
    initial_days: int,
    sleep_sec: float,
    out_dir: str,
) -> dict[str, object]:
    started = datetime.now()
    out_path = resolve_user_path(out_dir)
    end_yyyymmdd = normalize_date_text(end_date) if str(end_date).strip() else datetime.now().strftime("%Y%m%d")
    explicit_start = normalize_date_text(start_date) if str(start_date).strip() else ""
    default_start = (datetime.now() - timedelta(days=max(1, int(initial_days)))).strftime("%Y%m%d")
    full_history = mode == "full"

    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError("baostock not installed. Please run: pip install baostock") from exc

    login = bs.login()
    if str(login.error_code) != "0":
        raise RuntimeError(f"baostock login failed: {login.error_code} {login.error_msg}")

    errors: list[str] = []
    ok_count = 0
    fail_count = 0
    skipped_count = 0
    new_rows_total = 0

    try:
        symbols = _collect_symbols_from_text(symbols_text)
        if not symbols and all_market:
            symbols = _collect_baostock_symbols(
                bs=bs,
                end_date_dash=yyyymmdd_to_dash(end_yyyymmdd),
                limit=max(1, int(limit)),
            )
        if not symbols:
            symbols = ["sh600519", "sz300750", "sh601899", "sz002594", "sz000333"]

        for index, symbol in enumerate(symbols, start=1):
            output_file = out_path / f"{symbol}.csv"
            existing = read_existing(output_file)
            before_count = len(existing)
            resolved_start = resolve_symbol_start_date(
                explicit_start=explicit_start,
                full_history=full_history,
                default_start=default_start,
                end_date=end_yyyymmdd,
                existing=existing,
            )
            if not resolved_start:
                skipped_count += 1
                continue

            rs = bs.query_history_k_data_plus(
                _to_baostock_symbol(symbol),
                "date,open,high,low,close,volume,amount",
                start_date=yyyymmdd_to_dash(resolved_start),
                end_date=yyyymmdd_to_dash(end_yyyymmdd),
                frequency="d",
                adjustflag="2",
            )
            if rs.error_code != "0":
                fail_count += 1
                errors.append(f"{symbol}: {rs.error_code} {rs.error_msg}")
                continue

            while rs.next():
                row = rs.get_row_data()
                if not row or len(row) < 7:
                    continue
                date_text = str(row[0]).strip()
                if len(date_text) < 10:
                    continue
                open_price = _safe_float(row[1])
                high_price = _safe_float(row[2])
                low_price = _safe_float(row[3])
                close_price = _safe_float(row[4])
                volume_val = _safe_float(row[5]) or 0.0
                amount_val = _safe_float(row[6]) or 0.0
                if (
                    open_price is None
                    or high_price is None
                    or low_price is None
                    or close_price is None
                    or open_price <= 0
                    or high_price <= 0
                    or low_price <= 0
                    or close_price <= 0
                ):
                    continue
                if high_price < low_price:
                    high_price, low_price = low_price, high_price
                volume = int(max(0.0, round(volume_val)))
                amount = amount_val if amount_val > 0 else close_price * max(volume, 1)
                existing[date_text[:10]] = {
                    "date": date_text[:10],
                    "open": f"{open_price:.2f}",
                    "high": f"{high_price:.2f}",
                    "low": f"{low_price:.2f}",
                    "close": f"{close_price:.2f}",
                    "volume": str(volume),
                    "amount": f"{amount:.2f}",
                    "symbol": symbol,
                }

            write_rows(output_file, existing)
            new_rows = max(0, len(existing) - before_count)
            new_rows_total += new_rows
            ok_count += 1
            if sleep_sec > 0 and index < len(symbols):
                time.sleep(float(sleep_sec))
    finally:
        try:
            bs.logout()
        except Exception:
            pass

    finished = datetime.now()
    duration_sec = max(0.0, (finished - started).total_seconds())
    return {
        "provider": "baostock",
        "mode": "full" if full_history else "incremental",
        "out_dir": str(out_path),
        "symbol_count": len(symbols),
        "ok_count": ok_count,
        "fail_count": fail_count,
        "skipped_count": skipped_count,
        "new_rows_total": new_rows_total,
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": round(duration_sec, 3),
        "errors": errors,
    }
