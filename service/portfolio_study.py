"""Resumable, training-gated rotation comparisons with frozen holdout choices.

Only training curves participate in selection and persistence diagnostics. Each
engine receives a point-in-time slice of the same source snapshot, including
prehistory for indicators, and the same capital, costs and risk configuration.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import random
from statistics import mean

from .portfolio_config import normalize_config
from .portfolio_engine import PortfolioEngine
from .portfolio_prepared import PreparedCache

VERSION = "portfolio-study-training-gated-rotation-v2"


def _digest(value):
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":"), allow_nan=False).iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def normalize_study(study, config=None):
    # Public one-argument form accepts the complete portfolio configuration.
    if config is None:
        config = study if isinstance(study, dict) else {}
        study = config.get("study")
    if not isinstance(study, dict):
        raise ValueError("study 须为对象并明确训练和测试日期")
    allowed = {"trainEnd", "testStart", "testEnd", "persistenceWindow", "minWindows",
               "minRankCorrelation", "minPositivePairFraction", "permutationCount", "seed", "alpha", "rotationEnabled"}
    if set(study) - allowed:
        raise ValueError("未知 study 参数: " + ", ".join(sorted(set(study) - allowed)))
    result = {}
    if type(study.get("rotationEnabled", True)) is not bool:
        raise ValueError("study.rotationEnabled 须为布尔值")
    result["rotationEnabled"] = study.get("rotationEnabled", True)
    for key, value in [("trainStart", config.get("startDate")),
                       *((key, study.get(key)) for key in ("trainEnd", "testStart", "testEnd"))]:
        try:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f"{key} 必须明确填写 YYYY-MM-DD 日期") from None
        result[key] = value
    if not result["trainStart"] <= result["trainEnd"] < result["testStart"] <= result["testEnd"]:
        raise ValueError("须满足 startDate <= trainEnd < testStart <= testEnd")
    if config.get("endDate") and result["testEnd"] > config["endDate"]:
        raise ValueError("testEnd 不得晚于配置 endDate")
    specs = {"persistenceWindow": (20, 2, 252, True), "minWindows": (7, 7, 1000, True),
             "minRankCorrelation": (.2, 0, 1, False), "minPositivePairFraction": (.6, .5, 1, False),
             "permutationCount": (999, 99, 4999, True), "seed": (1729, 0, 2**32 - 1, True),
             "alpha": (.05, .001, .1, False)}
    for key, (default, low, high, integer) in specs.items():
        value = study.get(key, default)
        if not _number(value) or not low <= value <= high or integer and int(value) != value:
            raise ValueError(f"study.{key} 须在 {low}..{high}" + (" 且为整数" if integer else ""))
        result[key] = int(value) if integer else float(value)
    return result


def _ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    result, left = [0.] * len(values), 0
    while left < len(order):
        right = left + 1
        while right < len(order) and values[order[right]] == values[order[left]]:
            right += 1
        for index in order[left:right]:
            result[index] = (left + right - 1) / 2 + 1
        left = right
    return result


def spearman(left, right):
    """Average ranks for ties; a constant cross section is uninformative."""
    if len(left) != len(right) or len(left) < 3:
        return None
    x, y = _ranks(left), _ranks(right)
    mx, my = mean(x), mean(y)
    denominator = math.sqrt(sum((v - mx)**2 for v in x) * sum((v - my)**2 for v in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / denominator if denominator else None


def persistence_diagnostic(results, initial_capital, settings):
    """Non-overlapping return windows, with an explicitly conditional null test."""
    ids = sorted(results)
    output = {"status": "insufficient", "strategyIds": ids, "windows": [], "pairs": [],
              "windowDays": settings["persistenceWindow"], "validPairCount": 0,
              "meanRankCorrelation": None, "positivePairFraction": None, "pValue": None,
              "passed": False, "failures": [], "excludedTrailingDays": 0,
              "permutation": {"count": settings["permutationCount"], "seed": settings["seed"],
                              "null": "independently exchange strategy labels within each training window",
                              "alternative": "positive adjacent-window mean cross-sectional rank correlation"},
              "limitations": ["This is a descriptive, conditional exchangeability test, not proof of profitable rotation.",
                              "Adjacent pair statistics share a rank vector; return windows themselves never overlap.",
                              "Few strategies, related signals, stable strategy differences and market regimes limit inference."]}
    if len(ids) < 3:
        output["failures"].append("at_least_three_strategies_required")
        return output
    curves = [results[key].get("curve", []) for key in ids]
    dates = [row.get("date") for row in curves[0]]
    if not dates or dates != sorted(set(dates)) or any([row.get("date") for row in rows] != dates for rows in curves):
        output["failures"].append("missing_or_misaligned_daily_equity")
        return output
    if any(not _number(row.get("equity")) or row["equity"] <= 0 for rows in curves for row in rows):
        output["failures"].append("invalid_daily_equity")
        return output
    window = settings["persistenceWindow"]
    output["excludedTrailingDays"] = len(dates) % window
    matrix = []
    for start in range(0, len(dates) - window + 1, window):
        end = start + window - 1
        values = [rows[end]["equity"] / (rows[start - 1]["equity"] if start else initial_capital) - 1 for rows in curves]
        matrix.append(values)
        output["windows"].append({"startDate": dates[start], "endDate": dates[end],
                                  "returnsPct": dict(zip(ids, [100 * value for value in values]))})
    output["distinctBehaviorCount"] = len({tuple(row["equity"] for row in rows) for rows in curves})
    correlations = []
    for i in range(len(matrix) - 1):
        correlation = spearman(matrix[i], matrix[i + 1])
        output["pairs"].append({"leftWindow": i, "rightWindow": i + 1, "rankCorrelation": correlation})
        if correlation is not None:
            correlations.append(correlation)
    output["validPairCount"] = len(correlations)
    minimum = max(6, settings["minWindows"] - 1)
    if len(matrix) < settings["minWindows"] or len(correlations) < minimum:
        output["failures"].append("insufficient_nonoverlapping_window_pairs")
    if output["distinctBehaviorCount"] < 3:
        output["failures"].append("fewer_than_three_distinct_strategy_behaviors")
    if not correlations or output["failures"]:
        return output
    observed = mean(correlations)
    fraction = sum(value > 0 for value in correlations) / len(correlations)
    rng, exceed = random.Random(settings["seed"]), 0
    for _ in range(settings["permutationCount"]):
        shuffled = [list(values) for values in matrix]
        for values in shuffled:
            rng.shuffle(values)
        samples = [spearman(a, b) for a, b in zip(shuffled, shuffled[1:])]
        samples = [value for value in samples if value is not None]
        if samples and mean(samples) >= observed - 1e-12:
            exceed += 1
    p_value = (exceed + 1) / (settings["permutationCount"] + 1)
    output.update(status="complete", meanRankCorrelation=observed,
                  positivePairFraction=fraction, pValue=p_value)
    if observed < settings["minRankCorrelation"]:
        output["failures"].append("rank_correlation_below_prespecified_threshold")
    if fraction < settings["minPositivePairFraction"]:
        output["failures"].append("positive_pair_fraction_below_prespecified_threshold")
    if p_value > settings["alpha"]:
        output["failures"].append("conditional_permutation_test_not_passed")
    output["passed"] = not output["failures"]
    return output


def _quality_failures(result, fingerprint):
    failures = []
    if result.get("fingerprint", result.get("datasetFingerprint")) != fingerprint:
        failures.append("source_fingerprint_mismatch")
    quality = result.get("quality")
    if not isinstance(quality, dict) or not quality:
        return failures + ["data_quality_missing"]
    if any(key not in quality for key in ("quarantinedSymbols", "unaccountedActionExposure")) or (
            "staleValuationDays" not in quality and "staleValuationDays" not in result.get("metrics", {})):
        failures.append("data_quality_checks_missing")
    if quality.get("quarantinedSymbols"):
        failures.append("quarantined_symbols")
    if quality.get("staleValuationDays", result.get("metrics", {}).get("staleValuationDays", 0)):
        failures.append("stale_valuation")
    if quality.get("unaccountedActionExposure"):
        failures.append("unaccounted_corporate_action_exposure")
    if quality.get("executionCapacityIssues"):
        failures.append("invalid_execution_capacity")
    return failures


def _slice(dataset, end):
    # The service owns this frozen snapshot. Engines read bar rows without
    # modifying them, so date slicing copies references rather than millions
    # of OHLCV dictionaries. No metadata or arrays in the source are changed.
    result = dict(dataset)
    for key in list(result):
        if key.endswith("ByDate") and isinstance(result[key], dict):
            result[key] = {day: value for day, value in result[key].items() if day <= end}
    if isinstance(result.get("calendar"), list):
        result["calendar"] = [day for day in result["calendar"] if day <= end]
    result["securities"] = [dict(security) for security in dataset["securities"]]
    for security in result["securities"]:
        security["bars"] = [bar for bar in security["bars"] if bar["date"] <= end]
        for key in list(security):
            if (key.endswith("ByDate") or key in ("stHistory", "memberHistory")) and isinstance(security[key], dict):
                security[key] = {day: value for day, value in security[key].items() if day <= end}
    result["securities"] = [item for item in result["securities"] if item["bars"]]
    if isinstance(result.get("benchmark"), dict):
        result["benchmark"] = dict(result["benchmark"])
        if isinstance(result["benchmark"].get("bars"), list):
            result["benchmark"]["bars"] = [bar for bar in result["benchmark"]["bars"] if bar["date"] <= end]
    return result


def _summary(result, initial_capital):
    metrics = result.get("metrics", {})
    keys = ("totalReturnPct", "annualizedReturnPct", "maxDrawdownPct", "sharpe", "winRatePct",
            "closedTradeCount", "sellFillWinRatePct", "tradeCount", "investedDays", "turnoverPct", "totalFees", "totalSlippage", "finalEquity", "winRateDefinition")
    output = {key: metrics.get(key) for key in keys}
    output["costRatioPct"] = 100 * (float(metrics.get("totalFees") or 0) + float(metrics.get("totalSlippage") or 0)) / initial_capital
    output["costRatioDefinition"] = "(fees + slippage) / initial capital; already included in equity returns"
    output["regimeMetrics"] = deepcopy(result.get("regimeMetrics", metrics.get("regimeMetrics", [])))
    output["benchmarkReturnPct"] = metrics.get("benchmarkReturnPct")
    output["excessReturnPct"] = metrics.get("excessReturnPct")
    output["benchmarkMetrics"] = deepcopy(result.get("benchmarkMetrics"))
    return output


def rotation_comparison(testing, initial_capital, persistence=None, enabled=True, complete=True, quality_failures=None):
    """Describe held-out evidence without using it to change any allocation rule."""
    comparison = {key: _summary(value, initial_capital) for key, value in testing.items()}
    reasons = list(quality_failures or [])
    if not enabled:
        reasons.append("rotation_disabled")
    if not persistence or not persistence.get("passed"):
        reasons.append("training_persistence_not_passed")
    certified = bool(testing) and all(value.get("quality", {}).get("certified") is True for value in testing.values())
    if not certified:
        reasons.append("data_quality_not_certified")
    if not complete:
        reasons.append("study_incomplete")
    gate = {"passed": False, "status": "running" if not complete else "skipped", "reasons": reasons,
            "estimatedRotationGain": None, "evidenceReadyForReview": False,
            "conclusion": "训练持续性未通过，跳过轮动；不会自动放宽检验门槛。"}
    if not enabled:
        gate["conclusion"] = "已关闭轮动，只比较固定策略和等权组合。"
    if all(key in comparison and _number(comparison[key]["totalReturnPct"]) for key in ("fixed", "equalWeight")):
        comparison["equalMinusFixedReturnPct"] = comparison["equalWeight"]["totalReturnPct"] - comparison["fixed"]["totalReturnPct"]
    if all(key in comparison and _number(comparison[key]["totalReturnPct"]) for key in ("softRotation", "fixed", "equalWeight")):
        soft = comparison["softRotation"]["totalReturnPct"]
        gain = soft - comparison["equalWeight"]["totalReturnPct"]
        fixed_gain = soft - comparison["fixed"]["totalReturnPct"]
        comparison.update(softMinusEqualReturnPct=gain, softMinusFixedReturnPct=fixed_gain)
        gate.update(estimatedRotationGain=gain,
                    evidenceReadyForReview=bool(complete and enabled and persistence and persistence.get("passed") and not quality_failures))
        if not complete:
            gate.update(status="running", conclusion="已生成部分样本外对照，研究尚未完成，结论待定。")
        elif not enabled or not persistence or not persistence.get("passed"):
            gate.update(status="skipped", conclusion="轮动未通过预设训练门控，现有轮动结果不能作为有效证据。")
        elif gain <= 1e-12:
            gate.update(status="no_value", conclusion="轮动没有创造价值：样本外净收益未超过等权组合。")
        elif fixed_gain <= 1e-12:
            gate.update(status="no_value", conclusion="轮动样本外超过等权组合，但未超过样本内选定的固定策略，关键检验未通过。")
        elif not certified or quality_failures:
            gate.update(status="unverified", conclusion="轮动的模拟样本外净收益超过两项对照，但数据质量或完整性不足，不能认证有效。")
        else:
            gate.update(status="passed", passed=True, conclusion="轮动样本外净收益超过等权和训练选定固定策略；仍需跨年份和环境验证。")
        if complete and gate["status"] == "no_value" and (not certified or quality_failures):
            gate["conclusion"] += " 此为模拟结果，数据质量尚不足以认证真实收益。"
    elif enabled and persistence and persistence.get("passed"):
        gate["conclusion"] = "训练持续性已通过，等待完成冻结样本外的软配权与硬切换对照。"
    if comparison:
        comparison["conclusion"] = gate["conclusion"]
    return comparison, gate


class PortfolioStudy:
    def __init__(self, dataset, config, state=None):
        if not isinstance(config, dict):
            raise ValueError("研究配置须为对象")
        raw = deepcopy(config)
        study = raw.pop("study", None)
        self.config = normalize_config(raw)
        self.settings = normalize_study(study, self.config)
        self.ids = list(self.config["allocation"]["strategyIds"])
        if not self.ids:
            raise ValueError("研究至少选择一个策略")
        if not isinstance(dataset, dict) or not isinstance(dataset.get("securities"), list) or not dataset["securities"]:
            raise ValueError("研究需要非空股票池")
        # Dataset ownership belongs to the immutable service snapshot. Keep a
        # separate top-level object only for a generated source fingerprint.
        self.dataset = dict(dataset)
        self.digest = _digest(dataset)
        self.fingerprint = str(dataset.get("fingerprint") or self.digest)
        self.dataset["fingerprint"] = self.fingerprint
        dates = dataset.get("calendar") or sorted({row["date"] for item in dataset["securities"] for row in item["bars"]})
        self.train_dates = [day for day in dates if self.settings["trainStart"] <= day <= self.settings["trainEnd"]]
        self.test_dates = [day for day in dates if self.settings["testStart"] <= day <= self.settings["testEnd"]]
        if not self.train_dates or not self.test_dates:
            raise ValueError("训练段及测试段均须有实际日线")
        self.signature = _digest({"version": VERSION, "config": self.config, "study": self.settings, "dataDigest": self.digest})
        self.state = {"version": VERSION, "signature": self.signature, "cursor": 0, "active": None,
                      "training": {}, "testing": {}, "selectedStrategyId": None, "persistence": None,
                      "selectionFailures": [], "completedDays": 0}
        if state is not None:
            if not isinstance(state, dict) or state.get("signature") != self.signature or state.get("version") != VERSION:
                raise ValueError("研究恢复状态与数据、配置或版本不匹配")
            restored = deepcopy(state)
            integrity = restored.pop("checkpointDigest", None)
            if integrity != _digest(restored):
                raise ValueError("研究恢复快照不完整或校验不匹配")
            if type(restored.get("cursor")) is not int or not 0 <= restored["cursor"] <= len(self.ids) + 5:
                raise ValueError("研究恢复阶段无效")
            if not isinstance(restored.get("training"), dict) or not isinstance(restored.get("testing"), dict):
                raise ValueError("研究恢复结果无效")
            self.state = restored
        self.engine = None
        self.prepared = PreparedCache()
        self.total_days = (len(self.ids) + 1) * len(self.train_dates) + (4 if self.settings["rotationEnabled"] else 2) * len(self.test_dates)

    @staticmethod
    def _shared_config(config):
        value = deepcopy(config)
        for key in ("startDate", "endDate", "study"):
            value.pop(key, None)
        if isinstance(value.get("allocation"), dict):
            value["allocation"].pop("mode", None)
            value["allocation"].pop("strategyIds", None)
        return value

    def _run_failures(self, result):
        failures = _quality_failures(result, self.fingerprint)
        actual = result.get("config")
        if not isinstance(actual, dict) or self._shared_config(actual) != self._shared_config(self.config):
            failures.append("capital_cost_pool_or_risk_config_mismatch")
        return failures

    def _task(self):
        cursor = self.state["cursor"]
        if cursor < len(self.ids):
            return "training", "single:" + self.ids[cursor], "single", [self.ids[cursor]]
        if cursor == len(self.ids):
            return "training", "equalWeight", "equal", self.ids
        if cursor == len(self.ids) + 1:
            return "testing", "fixed", "single", [self.state["selectedStrategyId"]]
        if cursor == len(self.ids) + 2:
            return "testing", "equalWeight", "equal", self.ids
        if self.settings["rotationEnabled"] and (self.state.get("persistence") or {}).get("passed"):
            if cursor == len(self.ids) + 3:
                return "testing", "softRotation", "soft_rotation", self.ids
            if cursor == len(self.ids) + 4:
                return "testing", "hardRotation", "hard_rotation", self.ids
        return None

    def _select(self):
        training = self.state["training"]
        singles = {key: training["single:" + key] for key in self.ids}
        diagnostic = persistence_diagnostic(singles, self.config["initialCapital"], self.settings)
        failures = sorted({failure for value in training.values() for failure in self._run_failures(value)})
        if failures:
            diagnostic["passed"] = False
            diagnostic["failures"] += ["training_data_or_comparison_integrity_failed"]
        self.state["persistence"] = diagnostic
        self.state["selectionFailures"] = failures
        eligible = [key for key in self.ids if not self._run_failures(singles[key])
                    and _number(singles[key].get("metrics", {}).get("totalReturnPct"))]
        if not eligible:
            self.state["selectionFailures"].append("no_valid_training_candidate")
            self.state["cursor"] = len(self.ids) + 5
            return
        # Same dates and starting capital: total net return is the frozen
        # selection criterion. Drawdown and ID are only deterministic tie breaks.
        self.state["selectedStrategyId"] = min(eligible, key=lambda key: (
            -singles[key]["metrics"]["totalReturnPct"],
            singles[key]["metrics"].get("maxDrawdownPct") or 0, key))

    def step(self, days=5):
        if type(days) is not int or not 1 <= days <= 1000:
            raise ValueError("days 须为 1..1000 的整数")
        task = self._task()
        if task is None:
            return True
        phase, key, mode, ids = task
        if self.engine is None:
            config = deepcopy(self.config)
            config["allocation"] = {**config["allocation"], "mode": mode, "strategyIds": ids}
            config["startDate"] = self.settings["trainStart" if phase == "training" else "testStart"]
            config["endDate"] = self.settings["trainEnd" if phase == "training" else "testEnd"]
            self.engine = self.prepared.create(PortfolioEngine, _slice(self.dataset, config["endDate"]), config, state=self.state["active"])
        done = self.engine.step(days=days)
        self.state["active"] = self.engine.checkpoint()
        if done:
            self.state[phase][key] = self.engine.result()
            self.state["completedDays"] += len(self.train_dates if phase == "training" else self.test_dates)
            self.state["cursor"] += 1
            self.state["active"] = None
            self.engine = None
            if phase == "training" and key == "equalWeight":
                self._select()
        return self._task() is None

    @property
    def progress(self):
        task = self._task()
        active = (self.state["active"] or {}).get("cursor", 0)
        if self.engine is not None and isinstance(getattr(self.engine, "progress", None), dict):
            active = self.engine.progress.get("completedDays", active)
        fraction = min(.999, (self.state["completedDays"] + active) / self.total_days) if task else 1.
        return {"progress": fraction, "phase": f"{task[0]}: {task[1]}" if task else "complete",
                "completedRuns": len(self.state["training"]) + len(self.state["testing"]),
                "plannedRuns": len(self.ids) + 3 + (2 if self.settings["rotationEnabled"] and (self.state.get("persistence") or {}).get("passed") else 0)}

    def checkpoint(self):
        result = deepcopy(self.state)
        result["checkpointDigest"] = _digest(result)
        return result

    def result(self):
        training, testing = self.state["training"], self.state["testing"]
        all_results = list(training.values()) + list(testing.values())
        quality_failures = sorted({failure for value in all_results for failure in self._run_failures(value)})
        certified = bool(all_results) and all(value.get("quality", {}).get("certified") is True for value in all_results)
        persistence = self.state["persistence"]
        comparison, gate = rotation_comparison(testing, self.config["initialCapital"], persistence,
                                              self.settings["rotationEnabled"], self._task() is None, quality_failures)
        return {"status": "complete" if self._task() is None else "running", "progress": self.progress,
                "versions": {"study": VERSION}, "fingerprint": self.fingerprint, "sourceDataDigest": self.digest,
                "config": {**deepcopy(self.config), "study": {key: value for key, value in self.settings.items() if key != "trainStart"}},
                "protocol": {"training": {"startDate": self.settings["trainStart"], "endDate": self.settings["trainEnd"]},
                             "testing": {"startDate": self.settings["testStart"], "endDate": self.settings["testEnd"]},
                             "testUsedForSelection": False, "selectionMetric": "training total net return, then lower drawdown, then ID",
                             "sameSourceSnapshot": "source_fingerprint_mismatch" not in quality_failures,
                             "sameInitialCapitalCostsRiskAndPool": "capital_cost_pool_or_risk_config_mismatch" not in quality_failures,
                             "sharedConfigFingerprint": _digest(self._shared_config(self.config)),
                             "trainingTrialCount": len(self.ids), "rotationExecuted": "softRotation" in testing,
                             "systemPlateauExecuted": False},
                "training": {"runs": deepcopy(training), "selectedStrategyId": self.state["selectedStrategyId"],
                             "selectionFailures": list(self.state["selectionFailures"]), "persistence": deepcopy(persistence)},
                "testing": deepcopy(testing), "comparison": comparison,
                "quality": {"certified": certified, "failures": quality_failures},
                "rotationGate": gate,
                "warnings": ["策略排名持续性只在训练段检验；测试段不会改变固定策略或参数。",
                             "全部模拟收益已含引擎配置成本；缺少真实权益账本的数据只能提供诊断，不能认证总回报。",
                             "持续性未通过则跳过轮动；轮动未超过等权组合时直接报告没有创造价值，不使用样本外继续调参。"]}
