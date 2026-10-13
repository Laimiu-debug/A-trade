"""Dated, full-universe pool screening; no account simulation or orders."""
from copy import deepcopy
from .portfolio_config import normalize_screen_config
from .portfolio_engine import _digest
from .portfolio_signals import DailyScorer, _combine_conditions, _condition, _pool_evidence

VERSION = "portfolio-pool-screen-v2"
POOL_KEYS = ("base", "trend", "structure", "volume")


def _row_order(row):
    return (-(row.get("amount") or 0), row["symbol"])


def paginate_screen(report, payload, names=None):
    """Combine saved complete pools before search/paging; never rerun strategy scoring."""
    source = report["screenPools"]
    names = names or {}
    combined = "pools" in payload
    pool, offset, size, query = (payload.get("pool", "combined" if combined else "base"), payload.get("poolOffset", 0),
                                 payload.get("poolPageSize", 100), payload.get("poolQuery", ""))
    mode = payload.get("poolMode", "intersection")
    requested = payload.get("pools", [])
    if (type(offset) is not int or offset < 0 or type(size) is not int or not 1 <= size <= 200
            or not isinstance(query, str) or len(query) > 100
            or mode not in ("intersection", "union") or not isinstance(pool, str)
            or pool not in source and not (combined and pool == "combined")):
        raise ValueError("股票池分页或搜索条件无效")
    if combined and (not isinstance(requested, list) or len(requested) > len(POOL_KEYS)
                     or any(not isinstance(key, str) or key not in POOL_KEYS for key in requested)):
        raise ValueError("叠加股票池须为基础、趋势、结构或量能池列表")
    selected = [key for key in POOL_KEYS if key in requested] if combined and requested else ["base"]
    available = {key for key, values in source.items() if isinstance(values, list)}
    members = {key: {row["symbol"] for row in values} for key, values in source.items() if key in available}
    stock_rows = {}
    for key in (*POOL_KEYS, "excluded"):
        for row in source.get(key, []) or []:
            if row["symbol"] in stock_rows:
                continue
            item = deepcopy(row)
            for field in ("matchedStrategies", "score", "rawScore"):
                item.pop(field, None)
            if not item.get("name") or item["name"] in (item["symbol"], item["symbol"][2:]):
                item["name"] = names.get(item["symbol"])
            item["memberships"] = [key for key in (*POOL_KEYS, "excluded") if item["symbol"] in members.get(key, set())]
            item["membershipStatus"] = {key: item["symbol"] in members[key] if key in available else None for key in POOL_KEYS}
            for key, detail in item.get("poolEvidence", {}).items():
                if key in POOL_KEYS and detail.get("status") == "unknown" and not item["membershipStatus"][key]:
                    item["membershipStatus"][key] = None
            stock_rows[item["symbol"]] = item

    def match(symbol, keys, operation):
        statuses = [stock_rows[symbol]["membershipStatus"][key] for key in keys]
        if operation == "intersection":
            return False if False in statuses else None if None in statuses else True
        return True if True in statuses else None if None in statuses else False

    if combined:
        for symbol, row in stock_rows.items():
            status = row["membershipStatus"]
            matched = match(symbol, selected, mode)
            row["poolMatch"] = {"matched": _combine_conditions([status["base"], matched]),
                                "missingPools": [key for key in selected if status[key] is False],
                                "unknownPools": [key for key in selected if status[key] is None]}
            if row.get("poolEvidence"):
                evidence = _pool_evidence([
                    _condition(key, {"base": "基础资格", "trend": "趋势", "structure": "回调结构", "volume": "量能"}[key] + "条件通过",
                               row["poolEvidence"].get(key, {}).get("passed") if key in available else None, "==", True)
                    for key in selected], mode=mode)
                evidence.update(pools=selected[:], mode=mode, inPool=row["poolMatch"]["matched"] is True,
                                baseEligible=status["base"] is True)
                row["poolEvidence"]["combined"] = evidence
    paged, pagination = {}, {}
    query = query.casefold()

    def page(key, values, position=0, apply_query=False):
        filtered = [row for row in values if not query or query in (row["symbol"] + " " + (row.get("name") or "")).casefold()] if apply_query else values
        paged[key] = filtered[position:position + size]
        pagination[key] = {"offset": position, "total": len(filtered), "unfilteredTotal": len(values),
                           "pageSize": size, "hasMore": position + size < len(filtered)}

    for key, values in source.items():
        if not isinstance(values, list):
            continue
        rows = sorted((stock_rows[row["symbol"]] for row in values if row["symbol"] in stock_rows), key=_row_order)
        page(key, rows, offset if key == pool else 0, key == pool)
    output = {"screenPools": paged, "poolPagination": pagination,
              "protocol": {**report.get("protocol", {}), "sortOrder": "amount_desc_symbol_asc", "strategyScoring": False}}
    if combined:
        base = members.get("base", set())
        matched = sorted((stock_rows[symbol] for symbol in base if stock_rows[symbol]["poolMatch"]["matched"] is True), key=_row_order)
        unknown = sum(stock_rows[symbol]["poolMatch"]["matched"] is None for symbol in base)
        page("combined", matched, offset if pool == "combined" else 0, pool == "combined")
        total = report.get("coverage", {}).get("totalSecurities", len(stock_rows))
        stages, previous, preceding_unknown = [], total, 0
        for index, key in enumerate(POOL_KEYS):
            statuses = [match(symbol, POOL_KEYS[:index + 1], "intersection") for symbol in base]
            count, unknown_count = statuses.count(True), statuses.count(None)
            after = count if not unknown_count else None
            before = previous if not preceding_unknown else None
            stages.append({"pool": key, "before": before, "after": after,
                           "removed": before - after if before is not None and after is not None else None,
                           "matchedCount": count, "unknownCount": unknown_count, "available": key in available})
            previous, preceding_unknown = after, unknown_count
        output["poolSelection"] = {"pools": selected, "mode": mode, "baseCount": len(base),
            "matchedCount": len(matched), "rejectedCount": len(base) - len(matched) - unknown,
            "unknownCount": unknown, "filteredCount": pagination["combined"]["total"],
            "missingCounts": {key: sum(stock_rows[symbol]["membershipStatus"][key] is False for symbol in base)
                              if key in available else None for key in selected},
            "unavailablePools": [key for key in selected if key not in available],
            "funnelStages": stages, "funnelMode": "cumulativeIntersection",
            "scope": "parallel pool conditions after base eligibility; no strategy scoring",
            "missingCountsOverlap": True, "fullUniverseBeforePaging": True}
    return output


class PortfolioScreen:
    def __init__(self, dataset, config, state=None):
        self.dataset, self.config = dataset, normalize_screen_config(config)
        self.data_digest = _digest(dataset)
        self.signature = _digest({"data": self.data_digest, "config": self.config, "version": VERSION})
        self.state = {"signature": self.signature, "done": False, "report": None}
        if state is not None:
            if state.get("signature") != self.signature:
                raise ValueError("股票池筛选保存点与来源或参数不一致")
            self.state = deepcopy(state)

    def step(self, days=5):
        if self.state["done"]:
            return True
        dates = self.dataset.get("calendar") or sorted({b["date"] for s in self.dataset["securities"] for b in s["bars"]})
        dates = [day for day in dates if not self.config["endDate"] or day <= self.config["endDate"]]
        if not dates:
            raise ValueError("筛选截止日期前没有有效交易日")
        day = dates[-1]
        scorer = DailyScorer(self.dataset, self.config, pools_only=True)
        scored = scorer.screen(day)
        rows = {}
        for symbol, state in scored["eligibilityBySymbol"].items():
            series = scorer.series.get(symbol)
            index = series.by_date.get(day) if series else None
            bar = series.bars[index] if index is not None else None
            row = {"symbol": symbol, "name": series.security.get("name") if series else None,
                   "eligible": state["eligible"], "reason": state["reason"], "unknown": state["unknown"],
                   "close": bar["close"] if bar else None, "amount": bar.get("amount") if bar else None,
                   **scored["poolValuesBySymbol"][symbol], "poolEvidence": scored["poolEvidenceBySymbol"][symbol]}
            rows[symbol] = row
        pools = {key: [rows[symbol] for symbol in symbols] for key, symbols in scored["pools"].items()}
        pools["excluded"] = [row for row in rows.values() if not row["eligible"]]
        for values in pools.values():
            values.sort(key=_row_order)
        self.state["report"] = {"type": "poolPreview", "status": "complete", "asOf": day, "config": self.config,
            "fingerprint": self.dataset.get("fingerprint"), "screenPools": pools,
            "poolCounts": {key: len(values) for key, values in pools.items()}, "coverage": scored["coverage"],
            "warnings": self.dataset.get("warnings", []), "protocol": {"fullUniverseBeforePaging": True,
                "ordersGenerated": False, "informationCutoff": "selected date close",
                "sortOrder": "amount_desc_symbol_asc", "strategyScoring": False}}
        self.state["done"] = True
        return True

    def checkpoint(self):
        return deepcopy(self.state)

    @property
    def progress(self):
        return {"progress": 1. if self.state["done"] else 0., "phase": "股票池筛选完成" if self.state["done"] else "按截止日筛选完整股票池"}

    def result(self):
        return deepcopy(self.state["report"] or {"type": "poolPreview", "status": "running"})
