"""Bounded, resumable walk-forward and full-system neighborhood experiments.

Every fold selects from training data before any holdout result exists. Folds
are independent funded experiments, not a cost-free stitched trading ledger.
The predeclared grid is also evaluated on holdout for diagnostics, never ranked
or selected using those holdout returns.
"""
from __future__ import annotations

from copy import deepcopy
from itertools import product
import math
from statistics import mean, pstdev

from .portfolio_config import normalize_config, number
from .portfolio_engine import PortfolioEngine
from .portfolio_study import (_digest, _number, _quality_failures, _slice, _summary,
                              normalize_study, persistence_diagnostic, rotation_comparison)
from .portfolio_regions import connected_regions, verified_ranges
from .portfolio_prepared import PreparedCache

VERSION = "portfolio-experiment-connected-regions-v2"
MAX_ENGINE_RUNS = 4000
HOLDING_PATHS = {"topN", "maxPositions", "maxStockWeight", "switchThreshold", "rebalanceEvery",
                 "stopLossPct", "takeProfitPct", "trailingStopPct", "maxHoldingDays"}
ROTATION_PATHS = {"lookbackDays", "cooldownDays", "minWeightChange", "temperature", "regimeBlend",
                  "regimeMinObservations", "warmupDays"}


def parameter_location(config, path):
    """Resolve identifiers, never arbitrary object paths, to one scan parameter."""
    if not isinstance(path, str):
        raise ValueError("扫描 path 须为字符串")
    parts = path.split(".")
    if len(parts) == 1 and path in HOLDING_PATHS:
        return config, path, "holdings"
    if len(parts) == 2 and parts[0] == "rotation" and parts[1] in ROTATION_PATHS:
        return config["rotation"], parts[1], "rotation"
    if len(parts) == 4 and parts[0] == "strategies" and parts[2] == "params":
        row = next((row for row in config["strategies"] if row["id"] == parts[1]), None)
        if row and parts[3] in row["params"]:
            return row["params"], parts[3], "strategy"
    if parts[0] == "combinations" and len(parts) in (3, 5):
        row = next((row for row in config["combinations"] if row["id"] == parts[1]), None)
        if row and len(parts) == 3 and parts[2] in ("method", "threshold"):
            return row, parts[2], "combination"
        if row and len(parts) == 5 and parts[2] == "children" and parts[4] == "weight":
            child = next((child for child in row["children"] if child["id"] == parts[3]), None)
            if child:
                return child, "weight", "combination"
    raise ValueError("不支持的扫描参数: " + path)


def apply_parameters(config, parameters):
    result = deepcopy(config)
    result.pop("study", None)
    for path, value in parameters.items():
        target, key, _ = parameter_location(result, path)
        target[key] = value
    return normalize_config(result)


def normalize_experiment(experiment, config):
    allowed = {"kind", "trainDays", "testDays", "stepDays", "expanding", "maxFolds", "axes", "maxTrials",
               "minNeighborPositiveFraction", "minNeighborhoodSize", "maxNeighborhoodStdPct",
               "minOosYears", "minOosRegimes", "capitalMode"}
    if not isinstance(experiment, dict) or set(experiment) - allowed:
        raise ValueError("experiment 必须是对象且只包含支持的参数")
    kind = experiment.get("kind")
    if kind not in ("walkForward", "systemPlateau"):
        raise ValueError("experiment.kind 须为 walkForward 或 systemPlateau")
    normalized = normalize_config(config)
    if not normalized.get("startDate") or not normalized.get("endDate"):
        raise ValueError("滚动/平原研究须明确设置开始和结束日期")
    output = {"kind": kind}
    output["capitalMode"] = experiment.get("capitalMode", "independent")
    if output["capitalMode"] not in ("independent", "continuous"):
        raise ValueError("capitalMode 须为 independent/continuous")
    specs = {"trainDays": (140, 14, 2500, True), "testDays": (39, 2, 1000, True),
             "stepDays": (experiment.get("testDays", 39), 2, 2500, True), "maxFolds": (5, 1, 8, True),
             "maxTrials": (81, 1, 81, True), "minNeighborPositiveFraction": (.7, .5, 1, False),
             "minNeighborhoodSize": (3, 2, 81, True), "maxNeighborhoodStdPct": (10, 0, 1000, False),
             "minOosYears": (2, 2, 10, True), "minOosRegimes": (2, 2, 8, True)}
    for key, (default, low, high, integer) in specs.items():
        output[key] = number(experiment.get(key, default), "experiment." + key, low, high, integer)
    if output["stepDays"] < output["testDays"]:
        raise ValueError("stepDays 不得小于 testDays；样本外窗口禁止重叠")
    if type(experiment.get("expanding", False)) is not bool:
        raise ValueError("expanding 须为布尔值")
    output["expanding"] = experiment.get("expanding", False)
    axes = experiment.get("axes", [])
    if not isinstance(axes, list) or len(axes) > 8:
        raise ValueError("扫描轴必须是列表且最多 8 个")
    output["axes"], paths, layers, count = [], set(), set(), 1
    reachable = set(normalized["allocation"]["strategyIds"])
    for combination in reversed(normalized["combinations"]):
        if combination["id"] in reachable:
            reachable.update(child["id"] for child in combination["children"])
    for axis in axes:
        if not isinstance(axis, dict) or set(axis) != {"path", "values"}:
            raise ValueError("每个扫描轴只允许 path 和 values")
        path, values = axis["path"], axis["values"]
        _, _, layer = parameter_location(normalized, path)
        if layer in ("strategy", "combination") and path.split(".")[1] not in reachable:
            raise ValueError("扫描参数不影响已选策略，不能作为系统扫描轴: " + path)
        if path in paths or not isinstance(values, list) or not 2 <= len(values) <= 5:
            raise ValueError("扫描轴不可重复，每轴须有 2 至 5 个值")
        if any(not (isinstance(value, str) or _number(value)) for value in values):
            raise ValueError("扫描值须为有限数字或组合方法字符串")
        if len({_digest(value) for value in values}) != len(values) or (all(_number(v) for v in values) and len(set(values)) != len(values)):
            raise ValueError("扫描值不可重复")
        if not (all(_number(v) for v in values) or all(isinstance(v, str) for v in values)):
            raise ValueError("同一轴扫描值类型须一致")
        values = sorted(values)
        for value in values:
            apply_parameters(normalized, {path: value})
        output["axes"].append({"path": path, "values": values})
        paths.add(path)
        layers.add(layer)
        count *= len(values)
    if count > output["maxTrials"]:
        raise ValueError(f"参数组合 {count} 组超过 maxTrials={output['maxTrials']}，请精简网格")
    if kind == "systemPlateau" and layers != {"strategy", "combination", "rotation", "holdings"}:
        raise ValueError("整套系统平原必须同时扫描策略、组合、轮动、持仓四层参数")
    # Validate interactions, e.g. a min/max pair valid alone but invalid jointly.
    for values in product(*(axis["values"] for axis in output["axes"])):
        apply_parameters(normalized, dict(zip((axis["path"] for axis in output["axes"]), values)))
    return output


def neighborhood_statistics(values, settings):
    valid = [value for value in values if _number(value)]
    result = {"size": len(values), "validCount": len(valid), "meanNetReturnPct": mean(valid) if valid else None,
              "stdNetReturnPct": pstdev(valid) if valid else None,
              "positiveFraction": sum(value > 0 for value in valid) / len(valid) if valid else None, "passed": False}
    result["passed"] = (len(valid) == len(values) and len(valid) >= settings["minNeighborhoodSize"]
                        and result["meanNetReturnPct"] > 0
                        and result["positiveFraction"] >= settings["minNeighborPositiveFraction"]
                        and result["stdNetReturnPct"] <= settings["maxNeighborhoodStdPct"])
    return result


def _calendar(dataset):
    values = dataset.get("calendar") or sorted({row["date"] for item in dataset["securities"] for row in item["bars"]})
    return sorted(set(values))


def _return_groups(result):
    """Observed daily net returns grouped by year and previous-close regime."""
    years, regimes = {}, {}
    curve = result.get("curve", [])
    prior = result.get("config", {}).get("initialCapital")
    for row in curve:
        value = row.get("dailyReturn")
        if not _number(value) and _number(prior) and prior > 0 and _number(row.get("equity")):
            value = row["equity"] / prior - 1
        prior = row.get("equity", prior)
        if not _number(value):
            continue
        years.setdefault(row["date"][:4], []).append(value)
        regime = row.get("regimeForReturn") or {}
        if regime.get("status") == "observed":
            label = str(regime.get("trend", "unknown")) + "/" + str(regime.get("volatility", "unknown"))
            if "unknown" not in label:
                regimes.setdefault(label, []).append(value)
    def summarize(groups):
        return {key: {"sampleDays": len(values), "netReturnPct": (math.prod(1 + value for value in values) - 1) * 100}
                for key, values in groups.items()}
    return {"years": summarize(years), "regimes": summarize(regimes)}


class PortfolioExperiment:
    def __init__(self, dataset, config, experiment, state=None):
        self.config = normalize_config(config)
        self.settings = normalize_experiment(experiment, self.config)
        if not isinstance(dataset, dict) or not isinstance(dataset.get("securities"), list) or not dataset["securities"]:
            raise ValueError("实验需要非空股票池")
        self.dataset = dict(dataset)
        self.digest = _digest(dataset)
        self.fingerprint = str(dataset.get("fingerprint") or self.digest)
        self.dataset["fingerprint"] = self.fingerprint
        self.ids = list(self.config["allocation"]["strategyIds"])
        self.candidates = []
        axes = self.settings["axes"]
        for index, values in enumerate(product(*(axis["values"] for axis in axes))):
            params = dict(zip((axis["path"] for axis in axes), values))
            self.candidates.append({"candidateId": "c" + str(index + 1).zfill(3), "params": params,
                                    "coordinates": [axis["values"].index(value) for axis, value in zip(axes, values)],
                                    "config": apply_parameters(self.config, params)})
        dates = [day for day in _calendar(dataset) if self.config["startDate"] <= day <= self.config["endDate"]]
        self.folds = []
        for index in range(self.settings["maxFolds"]):
            offset = index * self.settings["stepDays"]
            train_left = 0 if self.settings["expanding"] else offset
            test_left = offset + self.settings["trainDays"]
            test_right = test_left + self.settings["testDays"]
            if test_right > len(dates):
                break
            self.folds.append({"fold": index + 1,
                               "trainStart": dates[train_left], "trainEnd": dates[test_left - 1],
                               "testStart": dates[test_left], "testEnd": dates[test_right - 1],
                               "trainDays": test_left - train_left, "testDays": self.settings["testDays"]})
        if not self.folds:
            raise ValueError("数据区间不足以形成一个完整训练/样本外窗口")
        self.planned_runs = len(self.folds) * (len(self.candidates) * (len(self.ids) + 3) + 3)
        if self.settings["capitalMode"] == "continuous":
            self.planned_runs += 2 * len(self.folds) + 2
        if self.planned_runs > MAX_ENGINE_RUNS:
            raise ValueError(f"预计最多 {self.planned_runs} 次引擎回测，超过 {MAX_ENGINE_RUNS} 次限制")
        self.study_settings = []
        for fold in self.folds:
            study = deepcopy(self.config.get("study", {}))
            study.update(trainEnd=fold["trainEnd"], testStart=fold["testStart"], testEnd=fold["testEnd"])
            normalized = normalize_study(study, {**self.config, "startDate": fold["trainStart"]})
            self.study_settings.append(normalized)
        self.signature = _digest({"version": VERSION, "config": self.config, "experiment": self.settings, "dataDigest": self.digest})
        self.state = {"version": VERSION, "signature": self.signature, "foldIndex": 0, "phase": "training",
                      "candidateIndex": 0, "runIndex": 0, "active": None, "pendingTraining": {},
                      "folds": [], "current": {"training": {}, "oosCandidates": {}, "testing": {}},
                      "engineRunCount": 0, "completedDays": 0, "completedCandidateEvaluations": 0, "continuous": None}
        if state is not None:
            restored = deepcopy(state)
            if not isinstance(restored, dict) or restored.get("signature") != self.signature or restored.get("version") != VERSION:
                raise ValueError("实验恢复状态与配置、数据或版本不匹配")
            check = restored.pop("checkpointDigest", None)
            if check != _digest(restored):
                raise ValueError("实验恢复快照校验不匹配")
            if type(restored.get("foldIndex")) is not int or not 0 <= restored["foldIndex"] <= len(self.folds):
                raise ValueError("实验恢复阶段无效")
            self.state = restored
        self.engine = None
        self.continuous_runner = None
        self.prepared = PreparedCache()

    def _candidate(self):
        return self.candidates[self.state["candidateIndex"]]

    def _fold(self):
        return self.folds[self.state["foldIndex"]]

    def _task(self):
        if self.state["phase"] == "complete":
            return None
        fold = self._fold()
        candidate = self._candidate()
        mode, ids, phase = "equal", self.ids, self.state["phase"]
        run = self.state["runIndex"]
        key = "equalWeight"
        if phase == "training":
            if run < len(self.ids):
                key, mode, ids = "single:" + self.ids[run], "single", [self.ids[run]]
            elif run > len(self.ids):
                key, mode = "softRotation", "soft_rotation"
        elif phase == "oosCandidates":
            passed = self.state["current"]["training"][candidate["candidateId"]]["rotationExecuted"]
            key, mode = ("softRotation", "soft_rotation") if passed else ("equalWeight", "equal")
        else:
            candidate = next(row for row in self.candidates if row["candidateId"] == self.state["current"]["selection"]["candidateId"])
            training = self.state["current"]["training"][candidate["candidateId"]]
            key = ["fixed", "equalWeight", "hardRotation"][run]
            mode = {"fixed": "single", "equalWeight": "equal", "hardRotation": "hard_rotation"}[key]
            ids = [training["selectedStrategyId"]] if key == "fixed" else self.ids
        config = deepcopy(candidate["config"])
        config["allocation"] = {"mode": mode, "strategyIds": ids}
        config["startDate"] = fold["trainStart" if phase == "training" else "testStart"]
        config["endDate"] = fold["trainEnd" if phase == "training" else "testEnd"]
        return candidate, key, config

    def _compact(self, result):
        return {"metrics": _summary(result, self.config["initialCapital"]),
                "quality": deepcopy(result.get("quality", {})),
                "qualityFailures": self._failures(result), "groups": _return_groups(result)}

    def _failures(self, result):
        return _quality_failures(result, self.fingerprint) + result.get("comparisonFailures", [])

    def _complete_training_candidate(self, full_system):
        candidate, pending = self._candidate(), self.state["pendingTraining"]
        singles = {key: pending["single:" + key] for key in self.ids}
        settings = self.study_settings[self.state["foldIndex"]]
        diagnostic = persistence_diagnostic(singles, self.config["initialCapital"], settings)
        failures = sorted({failure for result in pending.values() for failure in self._failures(result)})
        if failures:
            diagnostic["passed"] = False
            diagnostic["failures"].append("training_data_or_comparison_integrity_failed")
        selected = min(self.ids, key=lambda key: (-singles[key].get("metrics", {}).get("totalReturnPct", -math.inf),
                                                  singles[key].get("metrics", {}).get("maxDrawdownPct") or 0, key))
        self.state["current"]["training"][candidate["candidateId"]] = {
            "candidateId": candidate["candidateId"], "params": deepcopy(candidate["params"]),
            "configSignature": _digest(candidate["config"]), "fullSystem": self._compact(full_system),
            "singles": {key: _summary(result, self.config["initialCapital"]) for key, result in singles.items()},
            "equalWeight": _summary(pending["equalWeight"], self.config["initialCapital"]),
            "selectedStrategyId": selected, "persistence": diagnostic,
            "rotationExecuted": "softRotation" in pending, "qualityFailures": failures,
            "trainingDataCertified": all(result.get("quality", {}).get("certified") is True for result in pending.values())}
        self.state["pendingTraining"] = {}
        self.state["candidateIndex"] += 1
        self.state["runIndex"] = 0
        self.state["completedCandidateEvaluations"] += 1
        if self.state["candidateIndex"] == len(self.candidates):
            if self._freeze_selection():
                self.state.update(phase="oosCandidates", candidateIndex=0)

    def _neighbors(self, candidate):
        result = []
        for other in self.candidates:
            differences = [index for index, (left, right) in enumerate(zip(candidate["coordinates"], other["coordinates"])) if left != right]
            if not differences:
                result.append(other["candidateId"])
            elif len(differences) == 1:
                index = differences[0]
                axis = self.settings["axes"][index]
                if isinstance(axis["values"][0], str) or abs(candidate["coordinates"][index] - other["coordinates"][index]) == 1:
                    result.append(other["candidateId"])
        return result

    def _freeze_selection(self):
        training = self.state["current"]["training"]
        for candidate in self.candidates:
            ids = self._neighbors(candidate)
            values = [training[key]["fullSystem"]["metrics"]["totalReturnPct"]
                      if not training[key]["qualityFailures"] else None for key in ids]
            stats = neighborhood_statistics(values, self.settings)
            if self.settings["kind"] == "systemPlateau" and not all(training[key]["rotationExecuted"] for key in ids):
                stats["passed"] = False
            training[candidate["candidateId"]]["neighborhood"] = {**stats, "candidateIds": ids}
        eligible = [row for row in training.values() if row["neighborhood"]["passed"]]
        regions = connected_regions(self.candidates, training, self.settings)
        self.state["current"]["regions"] = regions
        frozen_region = None
        if self.settings["kind"] == "systemPlateau":
            valid = [row for row in regions if row["passed"]]
            if valid:
                frozen_region = min(valid, key=lambda row: (not row["intervalEligible"], -row["meanNetReturnPct"],
                                                           row["stdNetReturnPct"], row["regionId"]))
                eligible = [training[key] for key in frozen_region["candidateIds"]]
            else:
                eligible = []
        selection_pool = eligible or [row for row in training.values() if not row["qualityFailures"]]
        if not selection_pool:
            self.state["current"]["selection"] = {"candidateId": None, "status": "invalid_training", "params": {},
                                                  "testUsedForSelection": False, "neighborhood": None}
            self._complete_fold()
            return False
        selected = min(selection_pool, key=lambda row: (
            -(row["neighborhood"]["meanNetReturnPct"] if row["neighborhood"]["meanNetReturnPct"] is not None else -math.inf),
            row["neighborhood"]["stdNetReturnPct"] or 0, row["candidateId"]))
        self.state["current"]["selection"] = {"candidateId": selected["candidateId"], "params": deepcopy(selected["params"]),
                                              "status": "training_neighborhood" if eligible else "diagnostic_only_no_training_plateau",
                                              "neighborhood": deepcopy(selected["neighborhood"]),
                                              "testUsedForSelection": False}
        if frozen_region:
            self.state["current"]["selection"].update(region=deepcopy(frozen_region),
                neighborhood={"candidateIds": frozen_region["candidateIds"], "size": len(frozen_region["candidateIds"]),
                              "validCount": len(frozen_region["candidateIds"]), "meanNetReturnPct": frozen_region["meanNetReturnPct"],
                              "stdNetReturnPct": frozen_region["stdNetReturnPct"], "positiveFraction": frozen_region["positiveFraction"],
                              "passed": frozen_region["passed"]})
        return True

    def step(self, days=5):
        if type(days) is not int or not 1 <= days <= 1000:
            raise ValueError("days 须为 1..1000 的整数")
        if self.state["phase"] == "continuous":
            from .portfolio_continuous import ContinuousRunner
            if self.continuous_runner is None:
                self.continuous_runner = ContinuousRunner(self.dataset, self.config, self.candidates,
                    self.state["folds"], self.state["continuous"])
            done = self.continuous_runner.step(days)
            self.state["continuous"] = self.continuous_runner.checkpoint()
            if done:
                self.state["phase"] = "complete"
            return done
        task = self._task()
        if task is None:
            return True
        candidate, key, config = task
        if self.engine is None:
            self.engine = self.prepared.create(PortfolioEngine, _slice(self.dataset, config["endDate"]), config, state=self.state["active"])
        done = self.engine.step(days=days)
        self.state["active"] = self.engine.checkpoint()
        if not done:
            return False
        result = self.engine.result()
        if not isinstance(result.get("config"), dict) or _digest(result["config"]) != _digest(config):
            result["comparisonFailures"] = ["capital_cost_pool_or_risk_config_mismatch"]
        self.state["engineRunCount"] += 1
        self.state["completedDays"] += len(result.get("curve", []))
        self.state["active"], self.engine = None, None
        phase = self.state["phase"]
        if phase == "training":
            self.state["pendingTraining"][key] = result
            self.state["runIndex"] += 1
            if key == "equalWeight":
                pending = self.state["pendingTraining"]
                settings = self.study_settings[self.state["foldIndex"]]
                diagnostic = persistence_diagnostic({ident: pending["single:" + ident] for ident in self.ids},
                                                    self.config["initialCapital"], settings)
                failed = any(self._failures(value) for value in pending.values())
                if not diagnostic["passed"] or not settings["rotationEnabled"] or failed:
                    self._complete_training_candidate(result)
            elif key == "softRotation":
                self._complete_training_candidate(result)
        elif phase == "oosCandidates":
            self.state["current"]["oosCandidates"][candidate["candidateId"]] = self._compact(result)
            if candidate["candidateId"] == self.state["current"]["selection"]["candidateId"]:
                self.state["current"]["testing"][key] = result
            self.state["candidateIndex"] += 1
            if self.state["candidateIndex"] == len(self.candidates):
                self.state.update(phase="baselines", candidateIndex=0, runIndex=0)
        else:
            self.state["current"]["testing"][key] = result
            self.state["runIndex"] += 1
            selection = self.state["current"]["selection"]["candidateId"]
            rotation = self.state["current"]["training"][selection]["rotationExecuted"]
            if self.state["runIndex"] == (3 if rotation else 2):
                self._complete_fold()
        return self.state["phase"] == "complete"

    def _complete_fold(self):
        current = self.state["current"]
        selected = current["training"].get(current["selection"]["candidateId"])
        persistence = selected["persistence"] if selected else None
        frozen = (current["selection"].get("neighborhood") or {}).get("candidateIds", [])
        failures = sorted({failure for key in frozen for failure in current["training"][key]["qualityFailures"]}
                          | {failure for key in frozen for failure in current["oosCandidates"].get(key, {}).get("qualityFailures", [])}
                          | {failure for row in current["testing"].values() for failure in self._failures(row)})
        comparison, gate = rotation_comparison(current["testing"], self.config["initialCapital"], persistence,
                                               self.study_settings[self.state["foldIndex"]]["rotationEnabled"], True, failures)
        self.state["folds"].append({**deepcopy(self._fold()), **current, "persistence": persistence,
                                    "comparison": comparison, "rotationGate": gate})
        self.state["foldIndex"] += 1
        self.state.update(candidateIndex=0, runIndex=0, pendingTraining={})
        self.state["current"] = {"training": {}, "oosCandidates": {}, "testing": {}}
        self.state["phase"] = "complete" if self.state["foldIndex"] == len(self.folds) else "training"
        if self.state["phase"] == "complete" and self.settings["capitalMode"] == "continuous" and all(
                fold["selection"].get("candidateId") for fold in self.state["folds"]):
            self.state["phase"] = "continuous"

    @property
    def progress(self):
        complete = self.state["phase"] == "complete"
        if self.state["phase"] == "continuous":
            progress = self.continuous_runner.progress if self.continuous_runner else {"progress": 0., "phase": "continuous ledger preparation"}
            return {**progress, "progress": .9 + .1 * progress["progress"], "completedRuns": self.state["engineRunCount"],
                    "plannedRuns": self.planned_runs, "completedFolds": len(self.state["folds"]), "plannedFolds": len(self.folds)}
        planned_days = sum(fold["trainDays"] * len(self.candidates) * (len(self.ids) + 2)
                           + fold["testDays"] * (len(self.candidates) + 3) for fold in self.folds)
        active = (self.state.get("active") or {}).get("cursor", 0)
        return {"progress": 1. if complete else min(.999, (self.state["completedDays"] + active) / planned_days) * (.9 if self.settings["capitalMode"] == "continuous" else 1),
                "phase": "complete" if complete else f"fold {self.state['foldIndex'] + 1}: {self.state['phase']}",
                "completedRuns": self.state["engineRunCount"], "plannedRuns": self.planned_runs,
                "completedFolds": len(self.state["folds"]), "plannedFolds": len(self.folds)}

    def checkpoint(self):
        state = deepcopy(self.state)
        state["checkpointDigest"] = _digest(state)
        return state

    def _plateau(self):
        folds = self.state["folds"]
        reasons, heatmap = [], []
        if self.state["phase"] != "complete":
            reasons.append("experiment_incomplete")
        if self.settings["kind"] != "systemPlateau":
            reasons.append("four_layer_system_plateau_not_requested")
        if not folds or any(fold["selection"]["status"] != "training_neighborhood" for fold in folds):
            reasons.append("training_neighborhood_not_stable_in_all_folds")
        all_quality = []
        frozen_training, frozen_oos = [], []
        for fold in folds:
            ids = (fold["selection"].get("neighborhood") or {}).get("candidateIds", [])
            train = [fold["training"][key] for key in ids if key in fold["training"]]
            oos = [fold["oosCandidates"][key] for key in ids if key in fold["oosCandidates"]]
            frozen_training.extend(train)
            frozen_oos.extend(oos)
            if not ids or len(train) != len(ids) or len(oos) != len(ids):
                all_quality.append(False)
                reasons.append("frozen_region_evidence_missing")
            all_quality.extend(row["trainingDataCertified"] and row["fullSystem"]["quality"].get("certified") is True and not row["qualityFailures"]
                               for row in train)
            all_quality.extend(row["quality"].get("certified") is True and not row["qualityFailures"]
                               for row in oos)
            all_quality.extend(row.get("quality", {}).get("certified") is True and not self._failures(row)
                               for row in fold["testing"].values())
        if not all_quality or not all(all_quality):
            reasons.append("data_quality_not_certified")
        if any(row["qualityFailures"] for row in frozen_training) or any(
                row["qualityFailures"] for row in frozen_oos) or any(
                self._failures(row) for fold in folds for row in fold["testing"].values()):
            reasons.append("evidence_integrity_failed")
        if any(not row["rotationExecuted"] for row in frozen_training):
            reasons.append("training_persistence_not_passed_for_frozen_region")
        if any(not all(_number(fold["comparison"].get(key)) and fold["comparison"][key] > 1e-12
                       for key in ("softMinusEqualReturnPct", "softMinusFixedReturnPct")) for fold in folds):
            reasons.append("rotation_did_not_beat_both_baselines_in_every_fold")
        # Every fold's region is frozen using training only. Holdout checks only
        # accept or reject these already declared regions; they never shrink one.
        common = None
        validation = []
        for fold in folds:
            neighborhood = fold["selection"].get("neighborhood") or {}
            ids = neighborhood.get("candidateIds", [])
            common = set(ids) if common is None else common & set(ids)
            oos = fold["oosCandidates"]
            stats = neighborhood_statistics([oos.get(key, {}).get("metrics", {}).get("totalReturnPct") for key in ids], self.settings)
            validation.append({"fold": fold["fold"], "candidateIds": ids, "neighborhood": stats})
            if not stats["passed"]:
                reasons.append("frozen_neighborhood_failed_out_of_sample")
        if common is None or len(common) < self.settings["minNeighborhoodSize"]:
            reasons.append("no_common_training_region_across_folds")
        groups = {"years": {}, "regimes": {}}
        frozen_ids = set()
        for fold in folds:
            ids = (fold["selection"].get("neighborhood") or {}).get("candidateIds", [])
            frozen_ids.update(ids)
            for key in ids:
                result = fold["oosCandidates"].get(key, {})
                for category in groups:
                    for label, summary in result.get("groups", {}).get(category, {}).items():
                        grouped = groups[category].setdefault(label, {}).setdefault(key, [])
                        grouped.append(summary)
        group_checks = {"years": [], "regimes": []}
        for category, labels in groups.items():
            for label, candidates in labels.items():
                rows = []
                for key in sorted(frozen_ids):
                    summaries = candidates.get(key, [])
                    sample_days = sum(item["sampleDays"] for item in summaries)
                    # Independent resets are explicitly aggregated by arithmetic
                    # average; no invented concatenated CAGR is reported.
                    rows.append({"candidateId": key, "sampleDays": sample_days,
                                 "meanIndependentNetReturnPct": mean(item["netReturnPct"] for item in summaries) if summaries else None})
                minimum_days = 20 if category == "years" else 10
                values = [row["meanIndependentNetReturnPct"] if row["sampleDays"] >= minimum_days else None for row in rows]
                stats = neighborhood_statistics(values, self.settings)
                group_checks[category].append({"label": label, "candidates": rows, "neighborhood": stats})
                if not stats["passed"]:
                    reasons.append("frozen_neighborhood_failed_" + category)
        if len(group_checks["years"]) < self.settings["minOosYears"]:
            reasons.append("insufficient_out_of_sample_years")
        if len(group_checks["regimes"]) < self.settings["minOosRegimes"]:
            reasons.append("insufficient_observed_market_regimes")
        for candidate in self.candidates:
            key = candidate["candidateId"]
            train = [fold["training"][key]["fullSystem"]["metrics"]["totalReturnPct"] for fold in folds if key in fold["training"]]
            test = [fold["oosCandidates"][key]["metrics"]["totalReturnPct"] for fold in folds if key in fold["oosCandidates"]]
            neighbors = self._neighbors(candidate)
            neighbor_values = []
            for neighbor in neighbors:
                values = [fold["training"][neighbor]["fullSystem"]["metrics"]["totalReturnPct"] for fold in folds if neighbor in fold["training"]]
                neighbor_values.append(mean(values) if values and all(_number(v) for v in values) else None)
            rotated = bool(folds) and all(fold["training"].get(key, {}).get("rotationExecuted") for fold in folds)
            statistics = neighborhood_statistics(neighbor_values, self.settings)
            statistics["validatedAcrossTrainingFolds"] = bool(folds) and all(
                fold["training"].get(key, {}).get("neighborhood", {}).get("passed") is True for fold in folds)
            if not statistics["validatedAcrossTrainingFolds"] or self.settings["kind"] == "systemPlateau" and not rotated:
                statistics["passed"] = False
            heatmap.append({"candidateId": key, "params": deepcopy(candidate["params"]),
                            "trainingMeanNetReturnPct": mean(train) if train and all(_number(v) for v in train) else None,
                            "oosMeanNetReturnPct": mean(test) if test and all(_number(v) for v in test) else None,
                            "neighborhood": statistics,
                            "rotationExecuted": rotated,
                            "isFrozenTrainingRegion": key in (common or set()),
                            "modeByFold": [{"fold": fold["fold"], "mode": "soft_rotation" if fold["training"].get(key, {}).get("rotationExecuted") else "equal_fallback"} for fold in folds]})
        ranges, interval_failure = verified_ranges(self.candidates, self.settings["axes"], common or set())
        if interval_failure and self.settings["kind"] == "systemPlateau":
            reasons.append(interval_failure)
        reasons = sorted(set(reasons))
        recommended = ranges if not reasons else []
        for item in recommended:
            item["layer"] = parameter_location(self.config, item["path"])[2]
        statistical_reasons = [reason for reason in reasons if reason != "data_quality_not_certified"]
        return {"certified": not reasons, "dataCertified": bool(all_quality) and all(all_quality),
                "statisticalValidation": {"passed": not statistical_reasons, "reasons": statistical_reasons},
                "reasons": reasons, "recommendedRanges": recommended,
                "frozenRegionCandidateIds": sorted(common or []), "heatmap": heatmap,
                "foldValidation": validation, "yearValidation": group_checks["years"], "regimeValidation": group_checks["regimes"],
                "conclusion": "训练冻结的系统平原通过样本外、年份和环境检验。" if not reasons else "尚未确认收益平原，不输出推荐参数区间。",
                "neighborhoodDefinition": "self plus one-axis immediate numeric neighbors; all alternatives for categorical method",
                "selectionUsesOos": False, "axisRangeWarning": "推荐区间的全部离散网格交叉组合均已验证；未测试的连续插值不代表已验证。",
                "intervalCoverageFailure": interval_failure, "recommendedCartesianComplete": bool(recommended),
                "regionDefinition": "connected training-qualified components, frozen before OOS; holes and incomplete cross products rejected"}

    def result(self):
        complete = self.state["phase"] == "complete"
        continuous = None
        if self.state["continuous"]:
            from .portfolio_continuous import ContinuousRunner
            if self.continuous_runner is None:
                self.continuous_runner = ContinuousRunner(self.dataset, self.config, self.candidates,
                                                          self.state["folds"], self.state["continuous"])
            continuous = self.continuous_runner.result()
        return {"type": self.settings["kind"], "status": "complete" if complete else "running",
                "versions": {"experiment": VERSION}, "fingerprint": self.fingerprint, "sourceDataDigest": self.digest,
                "config": deepcopy(self.config), "experiment": deepcopy(self.settings), "progress": self.progress,
                "trialCount": len(self.candidates), "engineRunCount": self.state["engineRunCount"] + (continuous or {}).get("engineRunCount", 0),
                "actualCandidateEvaluations": self.state["completedCandidateEvaluations"],
                "plannedCandidateEvaluations": len(self.candidates) * len(self.folds),
                "protocol": {"testUsedForSelection": False, "gridFrozenBeforeExecution": True,
                             "selectionScope": "each fold uses only dates at or before its own trainEnd; elapsed prior holdout dates may enter later training",
                             "overlappingHoldoutWindows": False,
                             "maximumEngineRunCount": self.planned_runs, "foldCount": len(self.folds),
                             "capitalConvention": "independent diagnostic folds reset; optional continuous ledger is separate, with carried cash/positions and costed handoffs",
                             "terminalConvention": "terminal positions marked at close without hypothetical liquidation; entry/exit fills and rotation costs included",
                             "selectionMetric": "training neighborhood mean net return, then dispersion, then candidate ID",
                             "holdoutPolicy": "predeclared candidates validated once; holdout never changes grid, persistence thresholds or selected parameters",
                             "candidateConfigs": [{"candidateId": row["candidateId"], "params": deepcopy(row["params"]),
                                                   "configSignature": _digest(row["config"])} for row in self.candidates]},
                "folds": deepcopy(self.state["folds"]), "currentFold": deepcopy(self.state["current"]),
                "continuous": continuous,
                "plateau": self._plateau(),
                "warnings": ["逐折诊断从相同资金开始；连续账户另行记账并保留持仓，不免费拼接净值。",
                             "本次网格与持续性门槛在运行前冻结；样本外失败不触发自动继续调参。",
                             "缺少历史成分、权益账本或足够年份/环境证据时，只报告模拟诊断，不认证收益平原。"]}
