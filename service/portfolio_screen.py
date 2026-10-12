"""Dated, full-universe pool screening; no account simulation or orders."""
from copy import deepcopy
from .portfolio_config import normalize_config
from .portfolio_engine import _digest
from .portfolio_signals import DailyScorer

VERSION = "portfolio-pool-screen-v1"


class PortfolioScreen:
    def __init__(self, dataset, config, state=None):
        self.dataset, self.config = dataset, normalize_config(config)
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
        scorer = DailyScorer(self.dataset, self.config)
        scored = scorer.daily(day)
        matches = {}
        for sid, rows in scored["strategies"].items():
            for row in rows:
                matches.setdefault(row["symbol"], []).append({"id": sid, "score": row["score"], "rawScore": row["rawScore"]})
        rows = {}
        for symbol, state in scored["eligibilityBySymbol"].items():
            series = scorer.series.get(symbol)
            index = series.by_date.get(day) if series else None
            bar = series.bars[index] if index is not None else None
            row = {"symbol": symbol, "name": series.security.get("name") if series else None,
                   "eligible": state["eligible"], "reason": state["reason"], "unknown": state["unknown"],
                   "close": bar["close"] if bar else None, "amount": bar.get("amount") if bar else None,
                   "matchedStrategies": matches.get(symbol, [])}
            if index is not None:
                p = self.config["poolSettings"]
                close = series.signal_bars[index]["close"]
                window = p["trend"]["window"]
                row["trendReturnPct"] = (close / series.signal_bars[index-window]["close"] - 1) * 100 if index >= window else None
                window = p["structure"]["window"]
                row["drawdownPct"] = (1 - close / series.peak("close", window, index)) * 100 if index >= window-1 else None
                window = p["volume"]["window"]
                volume = series.mean("volume", index-window, index) if index >= window else None
                row["volumeRatio"] = bar["volume"] / volume if volume else None
            rows[symbol] = row
        pools = {key: [rows[symbol] for symbol in symbols] for key, symbols in scored["pools"].items()}
        pools["excluded"] = [row for row in rows.values() if not row["eligible"]]
        for values in pools.values():
            values.sort(key=lambda row: (-max((s["score"] for s in row["matchedStrategies"]), default=-1),
                                         -(row.get("amount") or 0), row["symbol"]))
        self.state["report"] = {"type": "poolPreview", "status": "complete", "asOf": day, "config": self.config,
            "fingerprint": self.dataset.get("fingerprint"), "screenPools": pools,
            "poolCounts": {key: len(values) for key, values in pools.items()}, "coverage": scored["coverage"],
            "warnings": self.dataset.get("warnings", []), "protocol": {"fullUniverseBeforePaging": True,
                "ordersGenerated": False, "informationCutoff": "selected date close"}}
        self.state["done"] = True
        return True

    def checkpoint(self):
        return deepcopy(self.state)

    @property
    def progress(self):
        return {"progress": 1. if self.state["done"] else 0., "phase": "股票池筛选完成" if self.state["done"] else "按截止日筛选完整股票池"}

    def result(self):
        return deepcopy(self.state["report"] or {"type": "poolPreview", "status": "running"})
