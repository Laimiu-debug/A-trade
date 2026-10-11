"""Chronological, holdout-isolated parameter plateau research.

Scores are percentages of capital, not percentile ranks. A large isolated peak
is not a plateau; holdout results never participate in parameter selection.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from typing import Any

from service.backtest_engine import BacktestEngine, normalize_config


VERSION = "chronological-grid-plateau-v2"
RISK_AXES = {"stopLossPct", "takeProfitPct", "partialTakeProfitPct", "partialSellFraction",
             "trailingStopPct", "maxHoldingDays", "maxPositions", "positionFraction", "maxParticipationRate"}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _bounded(value: Any, name: str, low: float, high: float, integer: bool = False):
    parsed = _number(value, name)
    if parsed < low or parsed > high or (integer and not parsed.is_integer()):
        raise ValueError(f"{name} must be {'an integer ' if integer else ''}in [{low}, {high}]")
    return int(parsed) if integer else parsed


def _get_path(config: dict, path: str):
    item = config
    for part in path.split("."):
        if not isinstance(item, dict) or part not in item:
            raise ValueError(f"Unknown parameter axis: {path}")
        item = item[part]
    return item


def _set_path(config: dict, path: str, value: Any):
    parts = path.split(".")
    item = config
    for part in parts[:-1]:
        item = item[part]
    item[parts[-1]] = value


def normalize_scan(scan: dict | None, config: dict) -> dict:
    if scan is not None and not isinstance(scan, dict):
        raise ValueError("Plateau scan must be an object")
    raw = scan or {}
    unknown = set(raw) - {"axes", "holdoutFraction", "folds", "minTrades", "maxDrawdownPct", "minRegionSize",
                          "minBlockDays", "minHoldoutDays", "neighborPassRate"}
    if unknown:
        raise ValueError("Unknown scan fields: " + ", ".join(sorted(unknown)))
    axes = raw.get("axes", [{"path": "stopLossPct", "values": [.05, .08, .11]},
                            {"path": "maxHoldingDays", "values": [10, 20, 30]}])
    if not isinstance(axes, list) or not 1 <= len(axes) <= 3:
        raise ValueError("Plateau requires 1 to 3 numeric axes")
    normalized, paths = [], set()
    for axis in axes:
        if not isinstance(axis, dict) or set(axis) - {"path", "values"}:
            raise ValueError("Each plateau axis must be an object")
        path = str(axis.get("path", ""))
        if path in paths or not (path in RISK_AXES or path.startswith("strategyParams.")):
            raise ValueError(f"Unsupported or duplicate parameter axis: {path}")
        if path.startswith("strategyParams.") and (len(path.split(".")) != 3
                or path.split(".")[1] not in config["strategyIds"]):
            raise ValueError(f"Axis must belong to an enabled strategy: {path}")
        reference = _get_path(config, path)
        _number(reference, path)
        values = axis.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError(f"{path} requires a nonempty values list")
        values = sorted({_number(value, path) for value in values})
        if len(values) < 2:
            raise ValueError(f"{path} requires at least 2 distinct values")
        normalized.append({"path": path, "values": [int(v) if v.is_integer() else v for v in values]})
        paths.add(path)
    combinations = math.prod(len(axis["values"]) for axis in normalized)
    if not 3 <= combinations <= 125:
        raise ValueError("Plateau requires 3 to 125 parameter combinations")
    return {
        "axes": normalized,
        "holdoutFraction": _bounded(raw.get("holdoutFraction", .2), "holdoutFraction", .1, .4),
        "folds": _bounded(raw.get("folds", 3), "folds", 2, 6, True),
        "minTrades": _bounded(raw.get("minTrades", 3), "minTrades", 1, 10000, True),
        "maxDrawdownPct": _bounded(raw.get("maxDrawdownPct", 30), "maxDrawdownPct", .1, 100),
        "minRegionSize": _bounded(raw.get("minRegionSize", 3), "minRegionSize", 3, 125, True),
        "minBlockDays": _bounded(raw.get("minBlockDays", 20), "minBlockDays", 5, 2520, True),
        "minHoldoutDays": _bounded(raw.get("minHoldoutDays", 20), "minHoldoutDays", 5, 2520, True),
        "neighborPassRate": _bounded(raw.get("neighborPassRate", .5), "neighborPassRate", .5, 1),
    }


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    lower, upper = math.floor(pos), math.ceil(pos)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower)


def _metrics_gate(metrics: dict, scan: dict, quality: dict | None = None) -> tuple[bool, list[str], float]:
    failures = []
    try:
        ret = _number(metrics.get("totalReturnPct"), "totalReturnPct")
        dd = abs(_number(metrics.get("maxDrawdownPct"), "maxDrawdownPct"))
        count = _number(metrics.get("closedTradeCount", metrics.get("tradeCount")), "closedTradeCount")
    except ValueError:
        return False, ["invalid_metrics"], -1e6
    if ret <= 0:
        failures.append("nonpositive_return")
    if count < scan["minTrades"]:
        failures.append("insufficient_closed_trades")
    if dd > scan["maxDrawdownPct"]:
        failures.append("drawdown_limit")
    if (quality or {}).get("quarantinedSymbols"):
        failures.append("dataQuality:quarantined_symbols")
    if (quality or {}).get("unaccountedActionExposure"):
        failures.append("dataQuality:unaccounted_corporate_action")
    if metrics.get("staleValuationDays", 0) > 0:
        failures.append("dataQuality:stale_valuation")
    # No win-rate preference: positive-skew trend strategies need not win often.
    score = ret - .75 * dd
    if score <= 0:
        failures.append("nonpositive_risk_adjusted_return")
    return not failures, failures, round(score, 8)


def _neighbors(coordinates: list[int], lengths: list[int]) -> list[tuple[int, ...]]:
    result = []
    for dimension, length in enumerate(lengths):
        for delta in (-1, 1):
            neighbor = list(coordinates)
            neighbor[dimension] += delta
            if 0 <= neighbor[dimension] < length:
                result.append(tuple(neighbor))
    return result


def _behavior_fingerprint(result: dict) -> str:
    """Exclude parameter labels: compare observed fills and equity, not configs."""
    behavior = {key: result.get(key, []) for key in ("trades", "curve")}
    packed = json.dumps(behavior, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _behavior_diagnostics(candidates: list[dict], axes: list[dict], folds: int) -> dict:
    completed = [row for row in candidates if len(row["blocks"]) == folds
                 and all(block.get("behaviorFingerprint") for block in row["blocks"])]
    result = {"status": "complete" if len(completed) == len(candidates) else "incomplete",
              "observedCandidateCount": len(completed), "distinctBehaviorCount": None,
              "inactiveAxes": [], "uninformative": None,
              "comparison": "exact SHA-256 of trades and equity curve in each training block; excludes configuration labels"}
    if not completed:
        return result
    signatures = {row["id"]: tuple(block["behaviorFingerprint"] for block in row["blocks"]) for row in completed}
    result["distinctBehaviorCount"] = len(set(signatures.values()))
    if result["status"] != "complete":
        return result
    result["uninformative"] = result["distinctBehaviorCount"] == 1
    for dimension, axis in enumerate(axes):
        fibers = {}
        for row in completed:
            fixed_coordinates = tuple(value for d, value in enumerate(row["coordinates"]) if d != dimension)
            fibers.setdefault(fixed_coordinates, set()).add(signatures[row["id"]])
        if all(len(values) == 1 for values in fibers.values()):
            result["inactiveAxes"].append(axis["path"])
    return result


def score_plateaus(candidates: list[dict], axes: list[dict], scan: dict) -> tuple[list[dict], dict | None, dict | None]:
    """Two-pass neighbor scoring on the complete grid, independent of list order."""
    by_coordinate = {tuple(row["coordinates"]): row for row in candidates}
    lengths = [len(axis["values"]) for axis in axes]
    # All point scores must exist before ANY neighbor is inspected.
    for row in candidates:
        blocks = row["blocks"]
        scores = [float(block["score"]) for block in blocks]
        row["passes"] = len(blocks) == scan["folds"] and all(block["passes"] for block in blocks)
        row["failures"] = sorted({failure for block in blocks for failure in block["failures"]})
        if len(blocks) != scan["folds"]:
            row["failures"].append("incomplete_blocks")
        row["pointScore"] = round(_quantile(scores, .25) - .25 * (_quantile(scores, .75) - _quantile(scores, .25)), 8)
        row["regionId"] = None
    for row in candidates:
        neighbors = [by_coordinate[coord] for coord in _neighbors(row["coordinates"], lengths)
                     if coord in by_coordinate]
        neighborhood = neighbors + [row]
        scores = [r["pointScore"] for r in neighborhood]
        median = _quantile(scores, .5)
        roughness = _quantile([abs(value - median) for value in scores], .5)
        row["neighborIds"] = [r["id"] for r in neighbors]
        row["neighborPassRate"] = round(sum(r["passes"] for r in neighbors) / len(neighbors), 8) if neighbors else 0
        row["localScore"] = round(_quantile(scores, .25) - .25 * roughness, 8)
        row["plateauScore"] = round(.35 * row["pointScore"] + .65 * row["localScore"], 8)
    qualified = {row["id"]: row for row in candidates if row["passes"] and row["localScore"] > 0
                 and row["neighborPassRate"] >= scan["neighborPassRate"]}
    visited, regions = set(), []
    for start in sorted(qualified):
        if start in visited:
            continue
        stack, members = [start], []
        visited.add(start)
        while stack:
            item = qualified[stack.pop()]
            members.append(item)
            for neighbor in sorted(item["neighborIds"]):
                if neighbor in qualified and neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        if len(members) < scan["minRegionSize"]:
            continue
        # Manhattan grid medoid: center first, robustness only as tie breaker.
        representative = min(members, key=lambda row: (
            sum(sum(abs(a - b) for a, b in zip(row["coordinates"], other["coordinates"])) for other in members),
            -row["plateauScore"], row["id"]))
        scores = [row["plateauScore"] for row in members]
        regions.append({
            "id": "", "pointIds": sorted(row["id"] for row in members), "size": len(members),
            "score": round(_quantile(scores, .25), 8), "representativeId": representative["id"],
            "parameterRanges": {axis["path"]: {"min": min(row["params"][axis["path"]] for row in members),
                                                     "max": max(row["params"][axis["path"]] for row in members)}
                                for axis in axes},
        })
    regions.sort(key=lambda region: (-region["score"], -region["size"], region["representativeId"]))
    by_id = {row["id"]: row for row in candidates}
    for i, region in enumerate(regions):
        region["id"] = f"region-{i + 1}"
        for candidate_id in region["pointIds"]:
            by_id[candidate_id]["regionId"] = region["id"]
    recommendation = None
    if regions:
        region = regions[0]
        representative = by_id[region["representativeId"]]
        recommendation = {"candidateId": representative["id"], "params": copy.deepcopy(representative["params"]),
                          "config": copy.deepcopy(representative["config"]), "regionId": region["id"],
                          "selectionScore": representative["plateauScore"]}
    peak_row = max(candidates, key=lambda row: (row["pointScore"], row["id"])) if candidates else None
    peak = {"candidateId": peak_row["id"], "params": copy.deepcopy(peak_row["params"]),
            "pointScore": peak_row["pointScore"]} if peak_row else None
    return regions, recommendation, peak


class PlateauRunner:
    """Small resumable state machine; each step advances at most one engine."""

    def __init__(self, dataset: dict, config: dict | None = None, scan: dict | None = None, state: dict | None = None):
        self.dataset = dataset
        self.config = normalize_config(config)
        self.scan = normalize_scan(scan, self.config)
        stress_config = copy.deepcopy(self.config)
        stress_config["costMultiplier"] = stress_config.get("costMultiplier", 1) * 2
        try:
            normalize_config(stress_config)
        except ValueError as exc:
            raise ValueError("双倍成本配置超出引擎支持范围，请降低基础 costMultiplier") from exc
        self.axes = self.scan["axes"]
        canonical = {"dataset": dataset, "config": self.config, "scan": self.scan, "version": VERSION}
        self._signature = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                                   separators=(",", ":")).encode("utf-8")).hexdigest()
        self._active = None
        self._active_state = None
        self._candidate_index = 0
        self._block_index = 0
        self._completed = 0
        self._stage = "training"
        self._validation = None
        self._regions, self._recommendation, self._peak = [], None, None
        self._diagnostics = {"status": "incomplete", "observedCandidateCount": 0,
                             "distinctBehaviorCount": None, "inactiveAxes": [], "uninformative": None}
        self._warnings = list(dataset.get("warnings", []))
        self._warnings.extend([
            "训练区间按时间分块评分；这不是滚动重训练的独立样本外业绩。",
            "最终留出只检验已冻结代表点，失败不会改选其他参数。反复查看同一留出再调参会使其失去独立性。",
            "多参数搜索仍有选择偏差；试验计数只覆盖本次扫描，不代表跨任务累计试验数，也不构成显著性证明。",
            "平原只描述已采样参数网格；轴间隔不等时不代表连续参数空间，未测试的中间参数不作收益推断。",
        ])
        self._candidates = self._build_candidates()
        dates = sorted({str(bar["date"]) for security in dataset.get("securities", []) for bar in security.get("bars", [])
                        if (not self.config.get("startDate") or str(bar["date"]) >= self.config["startDate"])
                        and (not self.config.get("endDate") or str(bar["date"]) <= self.config["endDate"])})
        holdout_count = max(self.scan["minHoldoutDays"], math.ceil(len(dates) * self.scan["holdoutFraction"]))
        training_dates = dates[:-holdout_count] if len(dates) > holdout_count else []
        holdout_dates = dates[-holdout_count:] if training_dates else []
        self._split = {"training": self._range(training_dates), "holdout": self._range(holdout_dates), "blocks": []}
        if len(training_dates) >= self.scan["folds"] * self.scan["minBlockDays"]:
            for i in range(self.scan["folds"]):
                block = training_dates[len(training_dates) * i // self.scan["folds"]:
                                       len(training_dates) * (i + 1) // self.scan["folds"]]
                self._split["blocks"].append(self._range(block))
        else:
            self._stage = "insufficient-data"
            self._warnings.append("数据不足以分成训练时间块和独立最终留出；未运行参数搜索，也没有推荐参数。")
        if state is not None:
            self._restore(state)

    @staticmethod
    def _range(dates: list[str]) -> dict:
        return {"startDate": dates[0] if dates else None, "endDate": dates[-1] if dates else None, "days": len(dates)}

    def _build_candidates(self) -> list[dict]:
        candidates = []
        for index, coordinate in enumerate(itertools.product(*(range(len(axis["values"])) for axis in self.axes))):
            config = copy.deepcopy(self.config)
            params = {}
            for dimension, axis in enumerate(self.axes):
                value = axis["values"][coordinate[dimension]]
                _set_path(config, axis["path"], value)
                params[axis["path"]] = value
            config = normalize_config(config)
            for path, value in params.items():
                if _get_path(config, path) != value:
                    raise ValueError(f"Axis value outside supported range: {path}={value}")
            candidates.append({"id": f"point-{index:03d}", "index": index, "coordinates": list(coordinate),
                               "params": params, "config": config, "blocks": []})
        return candidates

    def _restore(self, state: dict):
        if state.get("version") != VERSION or state.get("signature") != self._signature:
            raise ValueError("Plateau checkpoint does not match dataset/config/scan/version")
        self._stage = state["stage"]
        self._candidate_index = state["candidateIndex"]
        self._block_index = state["blockIndex"]
        self._completed = state["completedTrials"]
        self._candidates = copy.deepcopy(state["candidates"])
        self._regions = copy.deepcopy(state["regions"])
        self._recommendation = copy.deepcopy(state["recommendation"])
        self._peak = copy.deepcopy(state["peak"])
        self._validation = copy.deepcopy(state["validation"])
        self._diagnostics = copy.deepcopy(state["diagnostics"])
        self._warnings = list(state["warnings"])
        self._active_state = copy.deepcopy(state.get("activeEngine"))

    @property
    def done(self) -> bool:
        return self._stage in ("complete", "insufficient-data")

    @property
    def progress(self) -> dict:
        planned = len(self._candidates) * len(self._split["blocks"])
        if self._stage == "training" or self._recommendation:
            planned += 2
        active_fraction = 0.0
        if self._active is not None:
            active_progress = getattr(self._active, "progress", {})
            if isinstance(active_progress, dict):
                active_fraction = float(active_progress.get("progress", 0))
        fraction = 1 if self.done else (self._completed + active_fraction) / max(1, planned)
        phases = {"training": "训练区间分块与参数邻域评分", "holdout": "冻结参数后的独立留出检验",
                  "cost-stress": "独立留出双倍成本压力测试", "complete": "研究完成", "insufficient-data": "数据不足"}
        return {"stage": self._stage, "phase": phases[self._stage], "progress": fraction,
                "completedTrials": self._completed, "totalTrials": planned,
                "candidateIndex": self._candidate_index, "candidateCount": len(self._candidates),
                "percent": round(100 * fraction, 2)}

    def checkpoint(self) -> dict:
        return copy.deepcopy({"version": VERSION, "signature": self._signature, "stage": self._stage,
                              "candidateIndex": self._candidate_index, "blockIndex": self._block_index,
                              "completedTrials": self._completed, "candidates": self._candidates,
                              "regions": self._regions, "recommendation": self._recommendation, "peak": self._peak,
                              "validation": self._validation, "warnings": self._warnings,
                              "diagnostics": self._diagnostics,
                              "activeEngine": self._active.checkpoint() if self._active else self._active_state})

    def _engine(self):
        if self._active is not None:
            return self._active
        if self._stage == "training":
            config = copy.deepcopy(self._candidates[self._candidate_index]["config"])
            bounds = self._split["blocks"][self._block_index]
        else:
            config = copy.deepcopy(self._recommendation["config"])
            bounds = self._split["holdout"]
            if self._stage == "cost-stress":
                config["costMultiplier"] = float(config.get("costMultiplier", 1)) * 2
        config.update({"startDate": bounds["startDate"], "endDate": bounds["endDate"]})
        # Physical truncation prevents a vectorized implementation seeing future bars.
        # Earlier bars are retained solely as indicator warmup, with fresh capital.
        securities = []
        for security in self.dataset.get("securities", []):
            bars = [bar for bar in security.get("bars", []) if str(bar["date"]) <= bounds["endDate"]]
            if bars:
                securities.append({**security, "bars": bars})
        subset = {**self.dataset, "securities": securities}
        self._active = BacktestEngine(subset, config, state=self._active_state)
        self._active_state = None
        return self._active

    def step(self, days: int = 5) -> bool:
        if self.done:
            return True
        engine = self._engine()
        if not engine.step(days=days):
            return False
        result = engine.result()
        self._active = None
        self._completed += 1
        if self._stage == "training":
            passed, failures, score = _metrics_gate(result.get("metrics", {}), self.scan, result.get("quality"))
            row = self._candidates[self._candidate_index]
            row["blocks"].append({"index": self._block_index, **self._split["blocks"][self._block_index],
                                  "metrics": copy.deepcopy(result.get("metrics", {})), "score": score,
                                  "passes": passed, "failures": failures,
                                  "quality": copy.deepcopy(result.get("quality", {})),
                                  "behaviorFingerprint": _behavior_fingerprint(result),
                                  "warnings": list(result.get("warnings", [])), "versions": result.get("versions", {})})
            self._block_index += 1
            if self._block_index == self.scan["folds"]:
                self._candidate_index += 1
                self._block_index = 0
            if self._candidate_index == len(self._candidates):
                self._diagnostics = _behavior_diagnostics(self._candidates, self.axes, self.scan["folds"])
                if self._diagnostics["uninformative"]:
                    self._warnings.append("参数网格的全部训练成交与净值路径完全相同（uninformative）；可保留固定代表配置，但该平原不能证明参数敏感性低或参数已被有效优化。")
                elif self._diagnostics["inactiveAxes"]:
                    self._warnings.append("部分参数轴未改变任何已观测训练成交与净值路径：" + ", ".join(self._diagnostics["inactiveAxes"]) + "；这些轴的有效取值未被本次数据区分。")
                self._regions, self._recommendation, self._peak = score_plateaus(self._candidates, self.axes, self.scan)
                if self._recommendation is None:
                    self._stage = "complete"
                    self._warnings.append("没有同时满足交易数、回撤、跨时段盈利和邻域稳定性的连通平原；不回退推荐单点峰值。")
                else:
                    self._validation = {"holdout": None, "costStress": None, "passed": None, "failures": []}
                    self._validation["parameterIdentifiability"] = copy.deepcopy(self._diagnostics)
                    self._validation["interpretationWarnings"] = (["训练网格行为完全一致；留出通过也不能据此证明参数已被有效优化。"]
                                                                  if self._diagnostics["uninformative"] else [])
                    self._stage = "holdout"
        elif self._stage == "holdout":
            self._validation["holdout"] = result
            self._stage = "cost-stress"
        else:
            self._validation["costStress"] = result
            failures = []
            for key in ("holdout", "costStress"):
                _, stage_failures, _ = _metrics_gate(self._validation[key].get("metrics", {}), self.scan,
                                                    self._validation[key].get("quality"))
                failures.extend(f"{key}:{failure}" for failure in stage_failures)
            self._validation["failures"] = failures
            self._validation["passed"] = not failures
            self._validation["costMultiplier"] = 2
            self._validation["selectionFrozen"] = True
            if failures:
                self._warnings.append("训练平原代表点未通过最终留出或双倍成本检验；保留失败结果，不将其标记为可用策略。")
            self._stage = "complete"
        return self.done

    def result(self) -> dict:
        return copy.deepcopy({
            "kind": "plateau", "status": self._stage if self.done else "running", "progress": self.progress,
            "config": self.config, "scan": self.scan, "axes": self.axes, "split": self._split,
            "candidates": self._candidates, "regions": self._regions, "recommendation": self._recommendation,
            "peak": self._peak, "validation": self._validation,
            "diagnostics": self._diagnostics,
            "trialCount": len(self._candidates), "completedTrials": self._completed,
            "parameterTrialCount": len(self._candidates), "backtestRunCount": self._completed,
            "evaluatedParameterTrialCount": sum(len(row["blocks"]) == self.scan["folds"] for row in self._candidates),
            "warnings": self._warnings, "dataFingerprint": self.dataset.get("fingerprint"),
            "versions": {"plateau": VERSION},
            "methodology": {
                "selectionData": "training_chronological_blocks_only", "finalHoldoutUsedForSelection": False,
                "pointScore": "Q25(block net return pct - 0.75 * drawdown pct) - 0.25 * IQR(block score)",
                "localScore": "Q25(self + axis-adjacent point scores) - 0.25 * median absolute deviation",
                "plateauScore": "0.35 * pointScore + 0.65 * localScore",
                "regionScore": "Q25(member plateauScore)", "representative": "grid Manhattan medoid; plateauScore tie breaker",
                "adjacency": "one sorted grid step along exactly one parameter axis",
                "sampledGridOnly": True,
                "behaviorIdentifiability": "identical training fills/curves are reported as uninformative, without changing recommendation or pass gates",
                "minimumRegionSize": self.scan["minRegionSize"], "costStressMultiplier": 2,
                "dataQualityGate": "quarantined symbols, stale valuation days, or unaccounted strategy corporate-action exposure reject a block or final validation; benchmark-only exposure is separately disclosed",
                "blockPortfolioPolicy": "fresh capital per block; prior bars warm indicators only; no carried positions",
                "statisticalSignificance": "not established; no multiple-testing correction or return guarantee",
            },
        })
