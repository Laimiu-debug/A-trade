"""Versioned configuration for whole-universe portfolio research.

Dates may be omitted in the editor; the service resolves recent trading days
against the frozen source calendar before accepting a task. Percentages are
fractions in this contract, not percentage points.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import re

from service.strategies import catalogue as legacy_catalogue

CONFIG_VERSION = "portfolio-config-v1"
FILTER_POOLS = ("trend", "structure", "volume")
POOL_LABELS = {"base": "基础可交易池", "trend": "趋势池", "structure": "回调结构池",
               "volume": "量能活跃池", "combined": "叠加筛选池"}


def pool_catalogue():
    return [{"id": key, "name": label} for key, label in POOL_LABELS.items()]


def pool_style_catalogue():
    """Complete condition snapshots for explicit, style-specific editor actions.

    Presets do not change normalization defaults or strategy entry rules. A
    complete snapshot also clears optional filters left by a previous preset.
    Saving the actual conditions rather than a style ID preserves custom and
    old configurations if a later catalogue changes its starting hypotheses.
    """
    from service.portfolio_pool_styles import definitions

    rows = definitions()
    for row in rows:
        row["poolSettings"] = normalize_config({"poolSettings": row["poolSettings"]})["poolSettings"]
    return rows


def apply_pool_style(raw, style_id):
    """Replace only candidate-pool conditions; leave all other inputs untouched."""
    raw = {} if raw is None else raw
    _object(raw, "风格漏斗设置", CONFIG_FIELDS)
    styles = {row["id"]: row for row in pool_style_catalogue()}
    _choice(style_id, tuple(styles), "风格漏斗")
    return {**deepcopy(raw), "poolSettings": styles[style_id]["poolSettings"]}


def catalogue():
    rows = legacy_catalogue()
    rows.insert(4, {
        "id": "forcePurpleToYellow", "name": "主散量能紫转黄",
        "description": "主力量能线由下降转为上升；按主力回升幅度和主散主导强度排序。",
        "rationale": "沿用 FINAL-TRADE 主散公式，检验量能转折信号。",
        "caveats": "属于单日入场事件；风险退出模式下，信号次日消失不会自动平仓。量能线不是真实资金身份。",
        "parameters": [{"key": "minDominance", "label": "最低主导强度", "default": -1., "min": -1., "max": .9, "step": .05, "type": "number"}],
    })
    return rows


NUMBERS = {
    "initialCapital": (100000, 1000, 1e10, False),
    "lookbackDays": (39, 2, 10000, True),
    "topN": (5, 1, 100, True),
    "maxPositions": (10, 1, 200, True),
    "maxStockWeight": (.2, .001, 1, False),
    "rebalanceEvery": (1, 1, 252, True),
    "switchThreshold": (.05, 0, 1, False),
    "stopLossPct": (.08, 0, .99, False),
    "takeProfitPct": (.2, 0, 10, False),
    "trailingStopPct": (.1, 0, .99, False),
    "maxHoldingDays": (0, 0, 10000, True),
    "commissionRate": (.0003, 0, .02, False),
    "minCommission": (5, 0, 1000, False),
    "transferFeeRate": (.00001, 0, .01, False),
    "slippageBps": (10, 0, 500, False),
    "maxParticipationRate": (.01, .0001, .1, False),
}
CONFIG_FIELDS = set(NUMBERS) | {
    "startDate", "endDate", "stampDutyRate", "weighting", "rebalanceMode",
    "strategies", "combinations", "allocation", "universe", "poolSettings", "study", "rotation", "rightsPolicy",
}
SCREEN_FIELDS = ("startDate", "endDate", "lookbackDays", "universe", "poolSettings")


def number(value, label, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} 必须为有限数字")
    if value < low or value > high or integer and int(value) != value:
        raise ValueError(f"{label} 必须在 {low} 至 {high} 范围内" + ("且为整数" if integer else ""))
    return int(value) if integer else float(value)


def optional_number(value, label, low, high, integer=False):
    return None if value is None else number(value, label, low, high, integer)


def _object(value, label, keys):
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    if set(value) - set(keys):
        raise ValueError(f"{label} 含未知字段：" + ", ".join(sorted(set(value) - set(keys))))
    return value


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{label} 必须为 " + " / ".join(choices))
    return value


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} 必须为布尔值")
    return value


def _date(value, label):
    if value in (None, ""):
        return None
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError(f"{label} 必须为 YYYY-MM-DD") from None
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("策略编号须为字母开头的 1 至 64 位字母、数字、下划线或短横线")
    return value


def normalize_config(raw=None):
    raw = {} if raw is None else raw
    _object(raw, "组合回测参数", CONFIG_FIELDS)
    config = {key: number(raw.get(key, default), key, low, high, integer)
              for key, (default, low, high, integer) in NUMBERS.items()}
    config["startDate"] = _date(raw.get("startDate"), "开始日期")
    config["endDate"] = _date(raw.get("endDate"), "结束日期")
    if config["startDate"] and config["endDate"] and config["startDate"] > config["endDate"]:
        raise ValueError("开始日期不能晚于结束日期")
    config["weighting"] = _choice(raw.get("weighting", "equal"), ("equal", "rank"), "股票权重方式")
    config["rebalanceMode"] = _choice(raw.get("rebalanceMode", "risk"), ("risk", "rank", "hybrid"), "调仓方式")
    config["rightsPolicy"] = _choice(raw.get("rightsPolicy", "skip"), ("skip", "subscribe"), "配股处理")
    tax = raw.get("stampDutyRate")
    config["stampDutyRate"] = None if tax is None else number(tax, "印花税率", 0, .02)
    definitions = {row["id"]: row for row in catalogue()}
    strategies = raw.get("strategies", [{"id": kind, "kind": kind, "pool": "base"}
                                         for kind in ("forceGoldenCross", "forcePurpleToYellow", "momentum")])
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= 16:
        raise ValueError("请选择 1 至 16 个基础策略实例")
    config["strategies"], ids = [], set()
    for item in strategies:
        _object(item, "策略实例", {"id", "kind", "params", "pool"})
        ident = _identifier(item.get("id"))
        kind = _choice(item.get("kind"), tuple(definitions), "策略类型")
        if ident in ids:
            raise ValueError("策略编号不可重复")
        ids.add(ident)
        definition = definitions[kind]
        values = _object(item.get("params", {}), ident + " 参数", [p["key"] for p in definition["parameters"]])
        params = {p["key"]: number(values.get(p["key"], p["default"]), ident + "." + p["key"], p["min"], p["max"], p["type"] == "integer")
                  for p in definition["parameters"]}
        if kind == "trendPullback" and params["minDrawdown"] >= params["maxDrawdown"]:
            raise ValueError("回调最低幅度必须小于最高幅度")
        config["strategies"].append({"id": ident, "kind": kind, "params": params,
                                    "pool": _choice(item.get("pool", "base"), tuple(POOL_LABELS), "策略股票池")})
    combinations = raw.get("combinations", [])
    if not isinstance(combinations, list) or len(combinations) > 16:
        raise ValueError("组合策略最多 16 个")
    config["combinations"] = []
    for item in combinations:
        _object(item, "组合策略", {"id", "method", "children", "threshold", "normalization"})
        ident = _identifier(item.get("id"))
        if ident in ids:
            raise ValueError("策略编号不可重复")
        children = item.get("children")
        if not isinstance(children, list) or len(children) < 2:
            raise ValueError("组合策略至少包含两个子策略")
        normalized, used = [], set()
        for child in children:
            _object(child, "子策略", {"id", "weight"})
            child_id = child.get("id")
            if not isinstance(child_id, str) or child_id not in ids or child_id in used:
                raise ValueError("子策略须引用此前定义的不同策略，不可循环依赖")
            used.add(child_id)
            normalized.append({"id": child_id, "weight": number(child.get("weight", 1), "子策略权重", .000001, 1e6)})
        config["combinations"].append({"id": ident,
            "method": _choice(item.get("method", "weighted"), ("intersection", "union", "weighted"), "组合方法"),
            "normalization": _choice(item.get("normalization", "rank"), ("rank",), "分数标准化"),
            "children": normalized, "threshold": number(item.get("threshold", 0), "组合最低得分", 0, 1)})
        ids.add(ident)
    allocation = _object(raw.get("allocation", {}), "策略配权", {"mode", "strategyIds"})
    chosen = allocation.get("strategyIds", [row["id"] for row in config["strategies"]])
    if not isinstance(chosen, list) or not chosen or any(not isinstance(s, str) or s not in ids for s in chosen) or len(set(chosen)) != len(chosen):
        raise ValueError("配权策略须为已定义且不重复的策略编号")
    mode = _choice(allocation.get("mode", "equal"), ("single", "equal", "soft_rotation", "hard_rotation"), "策略配权模式")
    if mode == "single" and len(chosen) != 1:
        raise ValueError("固定策略模式须选择一个策略实例，可选择一个组合策略")
    config["allocation"] = {"mode": mode, "strategyIds": chosen[:]}
    if mode in ("soft_rotation", "hard_rotation") and len(chosen) < 2:
        raise ValueError("轮动至少选择两个策略实例")
    rotation_defaults = {
        "lookbackDays": (20, 2, 252, True),
        "cooldownDays": (5, 0, 252, True),
        "minWeightChange": (.1, 0, 1, False),
        "temperature": (.05, .0001, 10, False),
        "regimeBlend": (.25, 0, 1, False),
        "regimeMinObservations": (5, 2, 252, True),
        "warmupDays": (120, 0, 500, True),
    }
    rotation = _object(raw.get("rotation", {}), "策略轮动", rotation_defaults)
    config["rotation"] = {key: number(rotation.get(key, default), "rotation." + key, low, high, integer)
                          for key, (default, low, high, integer) in rotation_defaults.items()}
    universe = _object(raw.get("universe", {}), "基础资格", {"strictHistory", "excludeSt", "minListingDays", "minAmount20", "minAmplitude20"})
    config["universe"] = {
        "strictHistory": _boolean(universe.get("strictHistory", False), "严格历史资格"),
        "excludeSt": _boolean(universe.get("excludeSt", True), "剔除 ST"),
        "minListingDays": number(universe.get("minListingDays", 60), "最少上市交易日", 0, 2500, True),
        "minAmount20": number(universe.get("minAmount20", 10000000), "20 日平均成交额下限", 0, 1e13),
        "minAmplitude20": optional_number(universe.get("minAmplitude20"), "20 日平均振幅下限", 0, 10),
    }
    pool_settings = _object(raw.get("poolSettings", {}), "分层股票池", {*FILTER_POOLS, "combined"})
    pool_defaults = {
        "trend": {"window": (60, 2, 500, True), "minReturn": (0, -1, 10, False),
                  "rankWindow": (40, 2, 500, True), "rankStart": (1, 1, 100000, True), "rankEnd": (500, 1, 100000, True)},
        "structure": {"window": (20, 2, 500, True), "minDrawdown": (.02, 0, .99, False), "maxDrawdown": (.25, .001, 1, False)},
        "volume": {"window": (20, 2, 500, True), "minRatio": (1.2, .01, 20, False)},
    }
    pool_optional = {
        "structure": {"minMa10Above20Days": (0, 20, True), "minMa5Above10Days": (0, 20, True),
                      "maxPeakAgeDays": (0, 499, True), "maxPriceVsMa20": (0, 5, False)},
        "volume": {"minVolumeChange20": (-1, 20, False), "minUpDownVolumeRatio": (0, 100, False),
                   "maxDownVolumeRatio": (0, 100, False)},
    }
    pool_booleans = {"trend": {"rankEnabled": False}, "structure": {"requireAboveMa20": False},
                     "volume": {"excludeBlowoff": False, "excludeDivergence": False, "excludeUpperShadow": False}}
    pool_choices = {"structure": {"peakBasis": ("close", ("close", "high")),
                                  "maDaysMode": ("count", ("count", "consecutive"))}}
    config["poolSettings"] = {}
    for pool, defaults in pool_defaults.items():
        optional, booleans, choices = pool_optional.get(pool, {}), pool_booleans.get(pool, {}), pool_choices.get(pool, {})
        values = _object(pool_settings.get(pool, {}), pool, set(defaults) | set(optional) | set(booleans) | set(choices))
        config["poolSettings"][pool] = {key: number(values.get(key, default), pool + "." + key, low, high, integer)
                                         for key, (default, low, high, integer) in defaults.items()}
        config["poolSettings"][pool].update({key: optional_number(values.get(key), pool + "." + key, low, high, integer)
                                             for key, (low, high, integer) in optional.items()})
        config["poolSettings"][pool].update({key: _boolean(values.get(key, default), pool + "." + key)
                                             for key, default in booleans.items()})
        config["poolSettings"][pool].update({key: _choice(values.get(key, default), options, pool + "." + key)
                                             for key, (default, options) in choices.items()})
    if config["poolSettings"]["trend"]["rankStart"] > config["poolSettings"]["trend"]["rankEnd"]:
        raise ValueError("趋势池排名起点须小于或等于终点")
    if config["poolSettings"]["structure"]["minDrawdown"] >= config["poolSettings"]["structure"]["maxDrawdown"]:
        raise ValueError("结构池最低回撤须小于最高回撤")
    structure = config["poolSettings"]["structure"]
    if structure["maxPeakAgeDays"] is not None and structure["maxPeakAgeDays"] >= structure["window"]:
        raise ValueError("距窗口峰值最多交易日须小于结构窗口")
    combined = _object(pool_settings.get("combined", {}), "叠加筛选池", {"pools", "mode"})
    selected = combined.get("pools", list(FILTER_POOLS))
    if (not isinstance(selected, list) or any(not isinstance(pool, str) or pool not in FILTER_POOLS for pool in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("叠加筛选池请选择不重复的趋势、结构或量能条件；空列表表示基础池")
    config["poolSettings"]["combined"] = {
        "pools": [pool for pool in FILTER_POOLS if pool in selected],
        "mode": _choice(combined.get("mode", "intersection"), ("intersection", "union"), "股票池叠加方式"),
    }
    if "study" in raw:
        if not isinstance(raw["study"], dict):
            raise ValueError("验证设置必须是对象")
        config["study"] = deepcopy(raw["study"])
    return config


def normalize_screen_config(raw=None):
    """Validate only pool/date inputs; unrelated editor settings never gate screening.

    The fixed strategy/allocation exists solely for legacy storage/date consumers.
    Pool screening must not evaluate it or use it for membership or ordering.
    """
    raw = {} if raw is None else raw
    _object(raw, "股票池筛选参数", CONFIG_FIELDS)
    settings = {key: deepcopy(raw[key]) for key in SCREEN_FIELDS if key in raw}
    settings.update(strategies=[{"id": "momentum", "kind": "momentum", "pool": "base"}],
                    combinations=[], allocation={"mode": "equal", "strategyIds": ["momentum"]})
    return normalize_config(settings)


def default_config():
    return normalize_config({})


def normalize_selection(raw=None):
    raw = {} if raw is None else raw
    _object(raw, "股票池来源", {"mode", "symbols", "markets", "boards", "batchSize", "benchmarkSymbol", "lookbackDays", "source", "sourceId"})
    mode = _choice(raw.get("mode", "all"), ("all", "explicit"), "股票池来源")
    result = {"mode": mode, "symbols": [], "batchSize": number(raw.get("batchSize", 128), "读取批次", 1, 1000, True),
              "lookbackDays": number(raw.get("lookbackDays", 39), "最近交易日", 2, 10000, True)}
    result["source"] = _choice(raw.get("source", "local"), ("local", "history"), "研究数据来源")
    source_id = raw.get("sourceId")
    if source_id is not None and (not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{64}", source_id)):
        raise ValueError("历史数据源编号无效")
    if result["source"] == "history" and source_id is None:
        raise ValueError("请选择已导入的历史数据源")
    result["sourceId"] = source_id
    for key, choices in (("markets", ("sh", "sz", "bj")), ("boards", ("main", "chinext", "star", "bse"))):
        values = raw.get(key, list(choices))
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or v not in choices for v in values) or len(values) != len(set(values)):
            raise ValueError(f"{key} 请选择支持且不重复的选项")
        result[key] = values[:]
    symbols = raw.get("symbols", [])
    if not isinstance(symbols, list) or any(not isinstance(s, str) or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", s) for s in symbols):
        raise ValueError("指定证券须为 sh / sz / bj 加六位代码")
    if len(set(symbols)) != len(symbols):
        raise ValueError("股票池不可包含重复证券")
    if mode == "explicit" and not symbols:
        raise ValueError("指定股票池不能为空")
    result["symbols"] = symbols[:] if mode == "explicit" else []
    benchmark = raw.get("benchmarkSymbol") or None
    if benchmark is not None and (not isinstance(benchmark, str) or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", benchmark)):
        raise ValueError("基准须为市场前缀加六位指数代码")
    result["benchmarkSymbol"] = benchmark
    return result
