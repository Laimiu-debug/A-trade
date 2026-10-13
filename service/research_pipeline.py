"""Single-strategy development scans. No holdout is opened by this runner."""
from copy import deepcopy
from itertools import product

from .portfolio_config import normalize_config, number
from .portfolio_engine import PortfolioEngine
from .portfolio_experiment import apply_parameters, parameter_location, neighborhood_statistics
from .portfolio_regions import connected_regions
from .portfolio_study import _digest, _number, _quality_failures, _slice, _summary
from .plateau_validation import (risk_failures, behavior_fingerprint, behavior_diagnostics,
                                 check_identifiability)

VERSION = "single-research-pipeline-v2-identifiable-stability"
KINDS = {"singleBacktest", "singlePlateau", "frozenValidation"}


def single_config(raw):
    config = normalize_config(raw)
    if (len(config["strategies"]) != 1 or config["combinations"] or
            config["allocation"] != {"mode": "single", "strategyIds": [config["strategies"][0]["id"]]}):
        raise ValueError("单策略研究只允许一个基础策略、单策略配权，组合与轮动请在高级研究中设置")
    if not config["startDate"] or not config["endDate"]:
        raise ValueError("请明确设置开发区间开始与结束日期")
    if config.get("study"):
        raise ValueError("单策略开发不自动读取样本外区间；请先冻结方案，再验证新时段")
    config.pop("study", None)
    return config


def normalize_single_experiment(raw, config):
    allowed = {"axes", "minNeighborPositiveFraction", "minNeighborhoodSize", "maxNeighborhoodStdPct", "minClosedTrades", "maxDrawdownPct",
               "minInvestedDays", "maxNeighborhoodRelativeStd", "minNeighborhoodReturnRetention"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("单策略平原仅支持关键参数扫描与开发期邻域设置")
    config = single_config(config)
    axes = raw.get("axes", [])
    if not isinstance(axes, list) or not 1 <= len(axes) <= 3:
        raise ValueError("单策略平原请选择 1 至 3 个关键参数")
    result = {"axes": [],
              "minNeighborPositiveFraction": number(raw.get("minNeighborPositiveFraction", .7), "邻域盈利比例", .5, 1),
              "minNeighborhoodSize": number(raw.get("minNeighborhoodSize", 3), "最小邻域点数", 3, 27, True),
              "maxNeighborhoodStdPct": number(raw.get("maxNeighborhoodStdPct", 10), "邻域收益标准差", 0, 1000),
              "minClosedTrades": number(raw.get("minClosedTrades", 3), "最低已关闭持仓波段", 1, 10000, True),
              "maxDrawdownPct": number(raw.get("maxDrawdownPct", 30), "最大回撤上限", 0, 100),
              "minInvestedDays": number(raw.get("minInvestedDays", 3), "最少持仓交易日", 1, 10000, True),
              "maxNeighborhoodRelativeStd": number(raw.get("maxNeighborhoodRelativeStd", 1), "邻域相对标准差", 0, 10),
              "minNeighborhoodReturnRetention": number(raw.get("minNeighborhoodReturnRetention", .5), "邻域中位收益相对峰值下限", .1, 1)}
    paths, count = set(), 1
    for axis in axes:
        if not isinstance(axis, dict) or set(axis) != {"path", "values"}:
            raise ValueError("扫描轴只允许 path 和 values")
        path, values = axis["path"], axis["values"]
        _, _, layer = parameter_location(config, path)
        if layer not in {"strategy", "holdings"}:
            raise ValueError("单策略平原只扫描策略或持仓参数，漏斗和费用在本次实验中固定")
        if path in paths or not isinstance(values, list) or not 2 <= len(values) <= 5:
            raise ValueError("每个关键参数需 2 至 5 个不同取值，扫描参数不得重复")
        if any(not _number(value) for value in values) or len(set(values)) != len(values):
            raise ValueError("扫描取值须为互不重复的有限数字")
        values = sorted(values)
        result["axes"].append({"path": path, "values": values})
        paths.add(path)
        count *= len(values)
    if count > 27:
        raise ValueError("关键参数平原最多 27 组，请减少轴数或取值")
    for values in product(*(axis["values"] for axis in result["axes"])):
        single_config(apply_parameters(config, dict(zip((axis["path"] for axis in result["axes"]), values))))
    return result


class SinglePlateau:
    """Resumable one-account-per-point scan over the declared development dates."""
    def __init__(self, dataset, config, experiment, state=None):
        self.config = single_config(config)
        self.settings = normalize_single_experiment(experiment, self.config)
        # Defense in depth: even a loader returning extra bars cannot expose future
        # observations to parameter comparison or the prepared engine.
        self.dataset = _slice(dataset, self.config["endDate"])
        self.digest = _digest(dataset)
        self.fingerprint = str(dataset.get("fingerprint") or self.digest)
        self.dataset["fingerprint"] = self.fingerprint
        self.candidates = []
        for index, values in enumerate(product(*(axis["values"] for axis in self.settings["axes"]))):
            params = dict(zip((axis["path"] for axis in self.settings["axes"]), values))
            self.candidates.append({"candidateId": "c" + str(index + 1).zfill(3), "params": params,
                                    "coordinates": [axis["values"].index(value) for axis, value in zip(self.settings["axes"], values)],
                                    "config": apply_parameters(self.config, params)})
        self.signature = _digest({"version": VERSION, "config": self.config, "settings": self.settings, "data": self.digest})
        self.state = {"version": VERSION, "signature": self.signature, "index": 0, "active": None, "completed": {}}
        if state is not None:
            restored = deepcopy(state)
            checksum = restored.pop("checkpointDigest", None)
            if checksum != _digest(restored) or restored.get("signature") != self.signature or restored.get("version") != VERSION:
                raise ValueError("单策略平原保存点与配置、行情或版本不一致")
            if type(restored.get("index")) is not int or not 0 <= restored["index"] <= len(self.candidates):
                raise ValueError("单策略平原保存点阶段无效")
            self.state = restored
        self.engine = None

    def checkpoint(self):
        result = deepcopy(self.state)
        if self.engine:
            result["active"] = self.engine.checkpoint()
        result["checkpointDigest"] = _digest(result)
        return result

    @property
    def progress(self):
        fraction = 0
        if self.engine:
            fraction = self.engine.progress.get("progress", 0)
        return {"progress": min(1, (self.state["index"] + fraction) / len(self.candidates)),
                "phase": f"开发区间关键参数：{self.state['index']} / {len(self.candidates)} 组；不读取验证区间"}

    def step(self, days=5):
        if self.state["index"] >= len(self.candidates):
            return True
        candidate = self.candidates[self.state["index"]]
        if self.engine is None:
            self.engine = PortfolioEngine(self.dataset, candidate["config"], state=self.state["active"])
        if self.engine.step(days=days):
            report = self.engine.result()
            failures = _quality_failures(report, self.fingerprint)
            failures.extend(risk_failures(report, self.settings))
            self.state["completed"][candidate["candidateId"]] = {
                "metrics": _summary(report, self.config["initialCapital"]),
                "qualityFailures": failures,
                "qualityCertified": report.get("quality", {}).get("certified") is True,
                "behaviorFingerprint": behavior_fingerprint(report)}
            self.state["index"] += 1
            self.state["active"] = None
            self.engine = None
        return self.state["index"] == len(self.candidates)

    def result(self):
        completed, rows, training = self.state["completed"], [], {}
        fingerprints = {key: row.get("behaviorFingerprint") for key, row in completed.items()}
        diagnostics = behavior_diagnostics(self.candidates, fingerprints, self.settings["axes"])
        for candidate in self.candidates:
            point = completed.get(candidate["candidateId"])
            if not point:
                continue
            # Up to three adjacent sampled values per axis. At an edge, slide
            # the window inward instead of giving every edge only two points;
            # otherwise a one-axis, three-value experiment can never form a
            # three-point connected region, however flat its results are.
            windows = []
            for coordinate, axis in zip(candidate["coordinates"], self.settings["axes"]):
                width = min(3, len(axis["values"]))
                left = min(max(coordinate - 1, 0), len(axis["values"]) - width)
                windows.append(range(left, left + width))
            neighbors = [other["candidateId"] for other in self.candidates
                         if all(value in window for value, window in zip(other["coordinates"], windows))]
            values = [completed.get(key, {}).get("metrics", {}).get("totalReturnPct")
                      if not completed.get(key, {}).get("qualityFailures") else None for key in neighbors]
            stats = neighborhood_statistics(values, self.settings)
            check_identifiability(stats, [fingerprints.get(key) for key in neighbors], self.settings)
            if diagnostics["inactiveAxes"]:
                stats["passed"] = False
                stats["failures"].append("inactive_parameter_axes")
            stats["candidateIds"] = neighbors
            stats["definition"] = "每轴至多三个相邻已测取值的笛卡尔邻域；边界向内平移；不补造未测点"
            row = {**deepcopy(candidate), **deepcopy(point), "neighborhood": stats}
            rows.append(row)
            training[candidate["candidateId"]] = {"neighborhood": stats,
                "fullSystem": {"metrics": point["metrics"], "behaviorFingerprint": point.get("behaviorFingerprint")}}
        complete = len(rows) == len(self.candidates)
        regions = connected_regions(self.candidates, training, self.settings) if complete else []
        return {"status": "completed" if complete else "running", "config": deepcopy(self.config),
                "experiment": deepcopy(self.settings), "axes": deepcopy(self.settings["axes"]), "candidates": rows,
                "regions": regions, "trainingOnly": True, "selectedCandidateId": None,
                "parameterIdentifiability": diagnostics,
                "developmentPeriod": {"startDate": self.config["startDate"], "endDate": self.config["endDate"]},
                "plannedTrials": len(self.candidates), "completedTrials": len(rows), "engineRunCount": len(rows),
                "certified": False, "fingerprint": self.fingerprint,
                "warnings": ["全部参数点只使用开发区间；邻域稳定是开发期诊断，不代表独立样本外验证通过。",
                             "不自动选择最高收益点。请明确选择参数，冻结后再用未使用的新时段验证。",
                             "两点扫描只能比较敏感性；不足三个合格邻域点时不标记稳定区。",
                             "相同成交与净值路径不重复计作有效参数证据；未生效参数轴不形成稳定宽度。"]}
