"""Complete enumeration of the user-selected local coverage, never a PIT claim.

All database connections are read-only. Optional external evidence is loaded from
an existing research-history.sqlite3 cache; this module never starts downloads.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from service import tdx
from service.strategies import FORCE_VERSION, prepare_bars

UNIVERSE_VERSION = "local-coverage-v1"
BOARDS = {"main", "chinext", "star", "bse"}


class UniversePreparationInterrupted(Exception):
    """No partial snapshot is committed; resume must freeze the source again."""


def _dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).iterencode(value):
        digest.update(chunk.encode())
    return digest.hexdigest()


def board_of(symbol):
    market, code = tdx.symbol_parts(symbol)
    if tdx.security_kind(market, code) != "stock":
        return None
    if market == "bj":
        return "bse"
    if market == "sh" and code.startswith("68"):
        return "star"
    if market == "sz" and code.startswith("30"):
        return "chinext"
    return "main"


def _date(value, name):
    if not isinstance(value, str) or dt.date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"{name} 必须为 YYYY-MM-DD")
    return value


def normalize_selection(selection=None):
    selection = {} if selection is None else selection
    if not isinstance(selection, dict):
        raise ValueError("股票池配置必须为对象")
    unknown = set(selection) - {"mode", "symbols", "markets", "boards", "batchSize", "lookbackDays", "benchmarkSymbol", "source", "sourceId"}
    if unknown:
        raise ValueError("未知股票池配置: " + ",".join(sorted(unknown)))
    mode = selection.get("mode", "all")
    if mode not in {"all", "explicit"}:
        raise ValueError("股票池模式必须为 all 或 explicit")
    markets = selection.get("markets", ["sh", "sz", "bj"])
    boards = selection.get("boards", ["main", "chinext", "star", "bse"])
    if not isinstance(markets, list) or not markets or any(m not in tdx.MARKETS for m in markets):
        raise ValueError("请选择有效交易市场")
    if not isinstance(boards, list) or not boards or any(b not in BOARDS for b in boards):
        raise ValueError("请选择有效交易板")
    symbols = selection.get("symbols", [])
    if not isinstance(symbols, list):
        raise ValueError("指定证券须为数组")
    normalized = []
    for value in symbols:
        market, code = tdx.symbol_parts(value)
        symbol = market + code
        if board_of(symbol) is None:
            raise ValueError("股票池只支持 A 股证券")
        if symbol in normalized:
            raise ValueError("股票池存在重复证券")
        normalized.append(symbol)
    if mode == "explicit" and not normalized:
        raise ValueError("指定股票池不能为空")
    batch = selection.get("batchSize", 128)
    if type(batch) is not int or not 1 <= batch <= 4096:
        raise ValueError("读取批次必须为 1 至 4096 的整数")
    lookback = selection.get("lookbackDays", 39)
    if type(lookback) is not int or not 1 <= lookback <= 10000:
        raise ValueError("观察交易日数必须为 1 至 10000 的整数")
    benchmark = selection.get("benchmarkSymbol")
    if benchmark:
        market, code = tdx.symbol_parts(benchmark)
        if tdx.security_kind(market, code) != "index":
            raise ValueError("市场基准必须是本地真实指数代码")
        benchmark = market + code
    source = selection.get("source", "local")
    source_id = selection.get("sourceId")
    if source not in ("local", "history") or source == "history" and not isinstance(source_id, str):
        raise ValueError("请选择有效历史数据源")
    return {"mode": mode, "source": source, "sourceId": source_id, "symbols": normalized, "markets": sorted(set(markets)),
            "boards": sorted(set(boards)), "batchSize": batch, "lookbackDays": lookback,
            "benchmarkSymbol": benchmark or None}


def _configured_source(directory):
    path = (Path(directory).resolve() / "market.sqlite3")
    if not path.is_file():
        raise ValueError("请先选择通达信目录并读取行情")
    names, warnings = {}, []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            record = db.execute("SELECT value FROM meta WHERE key='path'").fetchone()
            if not record:
                raise ValueError("行情库尚未保存通达信目录")
            root = tdx.normalize_directory(json.loads(record[0]))
            try:
                for symbol, name in db.execute("SELECT symbol,name FROM securities"):
                    if isinstance(symbol, str) and isinstance(name, str):
                        names[symbol.lower()] = name
            except sqlite3.DatabaseError:
                warnings.append("行情库证券名称表读取失败，已保留原库并回退所选目录 TNF 名称；代码与日线仍直接枚举文件")
    except sqlite3.DatabaseError as exc:
        raise ValueError("行情库配置无法读取，原文件未修改") from exc
    fallback, tnf_warnings = tdx.read_names(root)
    warnings.extend(tnf_warnings)
    for symbol, name in fallback.items():
        names.setdefault(symbol, name)
    return root, names, warnings


def _cached_history(directory, symbols, end_date):
    path = Path(directory).resolve() / "research-history.sqlite3"
    if not path.is_file():
        return {}, []
    results, warnings = {}, []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            for digest, key, encoded in db.execute("SELECT s.digest,s.request_key,s.content FROM snapshots s JOIN current_snapshots c ON c.digest=s.digest"):
                try:
                    request = json.loads(key)
                    if not isinstance(request, list) or len(request) != 6 or request[2] != "history" or request[3] not in symbols:
                        continue
                    if hashlib.sha256(encoded.encode()).hexdigest() != digest:
                        raise ValueError("内容校验失败")
                    payload = json.loads(encoded)
                    symbol = payload.get("symbol")
                    if symbol != request[3] or not isinstance(payload.get("rawBars"), list):
                        raise ValueError("证券身份不匹配")
                    # One immutable provider snapshot per security: do not mix revisions.
                    prior = results.get(symbol)
                    score = sum(isinstance(b, dict) and isinstance(b.get("date"), str) and b["date"] <= end_date for b in payload["rawBars"])
                    if prior is None or score > prior[0]:
                        results[symbol] = (score, payload, digest)
                except (ValueError, TypeError, KeyError):
                    warnings.append("有历史缓存校验失败或身份不符，未用于历史资格判断")
    except sqlite3.DatabaseError:
        warnings.append("历史补充缓存无法只读打开，历史状态保留未知；未创建或修复缓存")
    return {s: (v[1], v[2]) for s, v in results.items()}, sorted(set(warnings))


def _attach_evidence(security, payload, digest, end_date):
    local = {bar["date"]: bar for bar in security["bars"]}
    external = {bar.get("date"): bar for bar in payload["rawBars"] if isinstance(bar, dict)}
    matched, mismatches = set(), []
    for day, bar in local.items():
        reference = external.get(day)
        if reference is None:
            continue
        try:
            agrees = all(abs(float(reference[k]) - bar[k]) <= 0.021 for k in ("open", "high", "low", "close"))
        except (TypeError, ValueError, KeyError):
            agrees = False
        if agrees:
            matched.add(day)
        else:
            mismatches.append(day)
    for key in ("stByDate", "precloseByDate", "tradeStatusByDate", "factorByDate", "memberHistory"):
        values = payload.get(key)
        if isinstance(values, dict):
            security[key] = {day: copy.deepcopy(value) for day, value in values.items() if day in matched}
    security["stHistory"] = dict(security.get("stByDate", {}))
    # A listing date is only accepted if an explicit source supplied it.
    listing = payload.get("listingDate")
    if isinstance(listing, str):
        try:
            security["listingDate"] = _date(listing, "上市日期")
        except ValueError:
            pass
    security["corporateActions"] = [copy.deepcopy(a) for a in payload.get("corporateActions", [])
                                    if isinstance(a, dict) and isinstance(a.get("exDate"), str) and a["exDate"] <= end_date]
    security["corporateActionsComplete"] = payload.get("corporateActionsComplete") is True
    security["provenance"] = copy.deepcopy(payload.get("provenance", []))
    security["historyCache"] = {"digest": digest, "matchedDates": len(matched), "mismatchedDates": mismatches,
                                "policy": "external-state-only-local-raw-prices-preserved"}
    return mismatches


def _retain_causal_window(security, start_date, warmup=500, lookback=39):
    """Carry exact full-prefix EMA state before trimming bulky OHLC dictionaries."""
    bars = security["bars"]
    signal_start = next((i for i, b in enumerate(bars) if b["date"] >= start_date), len(bars)) if start_date else max(0, len(bars) - lookback)
    cut = max(0, signal_start - warmup)
    security["historyWindow"] = {"sourceObservations": len(bars), "sourceFirstDate": bars[0]["date"],
                                  "observationsBefore": cut, "warmupBars": signal_start - cut,
                                  "signalStartDate": start_date or bars[signal_start]["date"],
                                  "retainedFrom": bars[cut]["date"], "forceSeedExact": True}
    if not cut:
        return
    prepared = prepare_bars(bars[:cut], security.get("precloseByDate", {}))["force"]
    last = prepared[-1]
    up, down, up_sum, down_sum = [], [], 0., 0.
    refs = security.get("precloseByDate", {})
    for index in range(cut):
        row = bars[index]
        reference = refs.get(row["date"], bars[index - 1]["close"] if index else row["close"])
        up.append(row["volume"] / 100 if index and row["close"] > reference else 0.)
        down.append(row["volume"] / 100 if index and row["close"] < reference else 0.)
        up_sum += up[-1]
        down_sum += down[-1]
        if len(up) > 3:
            up_sum -= up.pop(0)
            down_sum -= down.pop(0)
    security["forceSeed"] = {"version": FORCE_VERSION, "observationsBefore": cut, "previousDate": bars[cut - 1]["date"],
                             "previousClose": bars[cut - 1]["close"], "main": last["mainForce"],
                             "retail": last["retailForce"], "previousMain": last["prevMainForce"],
                             "upQueue": up, "downQueue": down, "upSum": up_sum, "downSum": down_sum}
    security["bars"] = bars[cut:]
    earliest = security["bars"][0]["date"]
    for key in ("stByDate", "stHistory", "precloseByDate", "openingVolumeByDate", "tradeStatusByDate", "factorByDate", "memberHistory"):
        if isinstance(security.get(key), dict):
            security[key] = {day: value for day, value in security[key].items() if day >= earliest}


def snapshot_universe(directory, selection=None, end_date=None, start_date=None, *, progress=None, cancelled=None):
    """Freeze all selected local securities. batchSize NEVER limits the universe.

    Retain 500 pre-start bars and an exact full-prefix main/retail EMA seed.
    Without start_date the signal window is the latest 39 source observations.
    File/member/name discovery is present-day local coverage, not historical PIT.
    """
    def checkpoint(done=None, total=None):
        if cancelled is not None and cancelled():
            raise UniversePreparationInterrupted("股票池准备已中断；继续时重新冻结完整数据源")
        if done is not None and progress is not None:
            progress(done, total)

    checkpoint()
    selection = normalize_selection(selection)
    if selection.get("source") == "history":
        from .portfolio_history import HistoryStore
        dataset = HistoryStore(directory).snapshot(selection, start_date, end_date)
        checkpoint(len(dataset["securities"]), len(dataset["securities"]))
        return dataset
    end_date = _date(end_date or dt.date.today().isoformat(), "结束日期")
    if start_date is not None:
        _date(start_date, "开始日期")
        if start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")
    root, names, warnings = _configured_source(directory)
    failures, exclusions, discovered = [], [], {}
    for market in selection["markets"]:
        checkpoint()
        try:
            folder = tdx.within(root / "vipdoc" / market / "lday", root)
            if not folder.is_dir():
                failures.append({"symbol": None, "market": market, "reason": "marketDirectoryMissing"})
                continue
            for path in folder.glob("*.day"):
                stem = path.stem.lower()
                symbol = market + stem if re.fullmatch(r"\d{6}", stem) else stem
                try:
                    source_market, _ = tdx.symbol_parts(symbol)
                    if source_market != market or board_of(symbol) is None:
                        continue
                    safe_path = tdx.within(path, root)
                    if symbol in discovered:
                        failures.append({"symbol": symbol, "reason": "duplicateSourceIdentity"})
                        # Canonical prefixed filename wins deterministically, duplicate is reported.
                        if stem == symbol:
                            discovered[symbol] = safe_path
                    else:
                        discovered[symbol] = safe_path
                except ValueError as exc:
                    failures.append({"symbol": symbol, "reason": "invalidSourcePath", "detail": str(exc)})
        except OSError:
            failures.append({"symbol": None, "market": market, "reason": "marketDirectoryUnreadable"})
    requested = set(selection["symbols"]) if selection["mode"] == "explicit" else set(discovered)
    selected = []
    for symbol in sorted(requested):
        if symbol[:2] not in selection["markets"] or board_of(symbol) not in selection["boards"]:
            exclusions.append({"symbol": symbol, "reason": "selectionMarketOrBoard"})
            continue
        if symbol not in discovered:
            failures.append({"symbol": symbol, "reason": "dailyFileMissing"})
        else:
            selected.append(symbol)
    checkpoint(0, len(selected))
    cached, cache_warnings = _cached_history(directory, set(selected), end_date)
    warnings.extend(cache_warnings)
    securities, total_bars, calendar = [], 0, set()
    for offset in range(0, len(selected), selection["batchSize"]):
        checkpoint()
        for symbol in selected[offset:offset + selection["batchSize"]]:
            try:
                source = tdx.read_stable(discovered[symbol])
                bars = [bar for bar in tdx.parse_daily(source) if bar["date"] <= end_date]
                if not bars:
                    exclusions.append({"symbol": symbol, "reason": "noBarsAsOfDate"})
                    continue
                calendar.update(bar["date"] for bar in bars)
                security = {"symbol": symbol, "name": names.get(symbol, symbol), "board": board_of(symbol),
                            "bars": bars, "adjustment": "none", "localCoverageOnly": True,
                            "sourceSha256": hashlib.sha256(_dump(bars).encode()).hexdigest(),
                            "historicalNameKnown": False, "corporateActionsComplete": False}
                if symbol in cached:
                    mismatch = _attach_evidence(security, *cached[symbol], end_date)
                    if mismatch:
                        warnings.append(f"{symbol} 外部原始价与本地在 {len(mismatch)} 日不一致，这些日期的历史状态未合入")
                _retain_causal_window(security, start_date, lookback=selection["lookbackDays"])
                securities.append(security)
                total_bars += len(security["bars"])
            except (ValueError, OSError) as exc:
                failures.append({"symbol": symbol, "reason": "dailyReadFailed", "detail": str(exc)})
        checkpoint(min(offset + selection["batchSize"], len(selected)), len(selected))
    benchmark = None
    for symbol in ([selection["benchmarkSymbol"]] if selection["benchmarkSymbol"] else []):
        path = tdx.daily_path(root, symbol)
        if not path.is_file():
            warnings.append(f"所选真实基准指数 {symbol} 本地缺失，市场状态显示未知")
            continue
        try:
            bars = [bar for bar in tdx.parse_daily(tdx.read_stable(path)) if bar["date"] <= end_date]
            if bars:
                benchmark = {"symbol": symbol, "bars": bars, "source": "TDX local index", "volumeUnit": "lots"}
                break
        except ValueError:
            warnings.append(f"真实基准指数 {symbol} 无法读取，市场状态可能显示未知")
    warnings.extend(["股票池覆盖范围为所选本地文件，不是完整历史时点全市场；退市缺失和选择偏差仍需单独审计。",
                     "当前证券名称不用于历史 ST 判断；缺失的上市日期、历史成员及状态均保留未知。"])
    coverage = {"mode": selection["mode"], "scope": "selected-local-file-coverage", "pointInTimeComplete": False,
                "discoveredCount": len(discovered), "requestedCount": len(requested), "selectedCount": len(selected),
                "loadedCount": len(securities), "barCount": total_bars, "failureCount": len(failures),
                "failures": failures, "excludedCount": len(exclusions), "exclusions": exclusions,
                "boardCounts": dict(Counter(s["board"] for s in securities)), "batchSize": selection["batchSize"],
                "batchSizeAffectsSelection": False, "coverageComplete": not failures}
    dataset = {"securities": securities, "benchmark": benchmark, "calendar": sorted(calendar),
               "calendarSource": "observed-local-union-not-exchange-calendar", "adjustment": "none",
               "source": "TDX local full-coverage frozen snapshot", "startDate": start_date, "endDate": end_date,
               "universeSelection": selection, "universeCoverage": coverage, "warnings": list(dict.fromkeys(warnings)),
               "versions": {"universe": UNIVERSE_VERSION}}
    # Operational batch size is deliberately absent from the research identity.
    dataset["dataQuality"] = {"certified": False, "pointInTimeComplete": False,
        "reasons": ["local-file-membership-is-not-historical-universe", "corporate-action-ledger-not-audited"],
        "sourceCoverageComplete": not failures, "nameSourceIsCurrent": True,
        "historicalStKnownBars": sum(len(s.get("stByDate", {})) for s in securities),
        "historicalMemberKnownBars": sum(len(s.get("memberHistory", {})) for s in securities),
        "listingDatesKnown": sum(bool(s.get("listingDate")) for s in securities)}
    identity = {**dataset, "universeSelection": {k: v for k, v in selection.items() if k != "batchSize"},
                "universeCoverage": {k: v for k, v in coverage.items() if k != "batchSize"}}
    dataset["fingerprint"] = _digest(identity)
    checkpoint()
    return dataset
