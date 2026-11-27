"""Shared executable master-trader risk, sizing, scaling, trimming & order-planning engine.

Single source of truth enforced by BOTH:
  - `service/master_backtest.py` (historical causal simulation & end-of-run next-day forecast)
  - `service/master_advisor.py`  (daily live / simulated portfolio predicted delivery slip)

Every rule that was previously prose (capital-tier slot limits, regime total-position caps,
single-stock ceilings, profit-only pyramiding, partial profit-taking trims, decline forced
de-risking, weak-tech rotation, and daily/consecutive-loss throttles) is compiled into
deterministic numerical constraints that directly govern orders and holdings.
"""
from __future__ import annotations

from typing import Any

ACTION_LABELS: dict[str, str] = {
    "open_buy": "首仓建仓",
    "add_win": "浮盈加仓",
    "trim_profit": "冲高减半",
    "trim_cap": "超限压仓",
    "trim_regime": "退潮降仓",
    "cut_stop": "硬止损清",
    "cut_weak": "破位快砍",
    "sell_rotate": "清退旧科技",
    "sell_profit": "止盈清仓",
    "sell_time": "满期换股",
}

EXEC_WINDOWS: dict[str, str] = {
    "cut_stop": "09:15–09:45（硬止损优先砍）",
    "cut_weak": "09:15–09:45（早盘破位纠错）",
    "sell_rotate": "09:26–10:15（旧赛道换仓）",
    "trim_regime": "09:26–10:15（退潮降仓压上限）",
    "trim_cap": "09:26–10:15（单票超限压仓）",
    "trim_profit": "09:26–10:15（冲高不板减半）",
    "sell_profit": "09:26–10:30（达标/移动止盈）",
    "sell_time": "09:30–10:30（满期换股）",
    "add_win": "09:25–10:00（高开承接确认加仓）",
    "open_buy": "09:30–10:30（开盘承接确认首仓）",
}

STYLE_EXECUTION_RULES: dict[str, dict[str, Any]] = {
    "wujiang_dynamic": {
        "maxPositions": 4,
        "baseFirstWeight": 0.18,
        "addWinWeight": 0.15,
        "singleCap": 0.35,
        "stopLossPct": -5.0,
        "addOnWinMinPct": 5.0,
        "trimProfitPct": 8.0,
        "trimRatio": 0.50,
        "takeProfitPct": 18.0,
        "maxHoldDays": 4,
        "useRegimeSwitch": True,
        "useFastWeakCut": True,
        "useAddOnWin": True,
        "usePartialTrim": True,
        "rotateWeakTechInDecline": True,
        "dailyLossBreakerPct": -2.0,
        "consecLossThrottle": 2,
        "stageTotalPosCap": {"advance": 0.88, "trial": 0.68, "chop": 0.58, "decline": 0.30},
        "stageMaxSlots": {"advance": 4, "trial": 4, "chop": 3, "decline": 2},
        "stageSingleCap": {"advance": 0.35, "trial": 0.30, "chop": 0.25, "decline": 0.15},
        "stageFirstScale": {"advance": 1.00, "trial": 0.85, "chop": 0.78, "decline": 0.55},
    },
    "huichanglan_mainline": {
        "maxPositions": 3,
        "baseFirstWeight": 0.20,
        "addWinWeight": 0.10,
        "singleCap": 0.32,
        "stopLossPct": -4.8,
        "addOnWinMinPct": 7.0,
        "trimProfitPct": 9.0,
        "trimRatio": 0.50,
        "takeProfitPct": 16.0,
        "maxHoldDays": 3,
        "useRegimeSwitch": True,
        "useFastWeakCut": True,
        "useAddOnWin": False,
        "usePartialTrim": True,
        "rotateWeakTechInDecline": True,
        "dailyLossBreakerPct": -2.0,
        "consecLossThrottle": 2,
        "stageTotalPosCap": {"advance": 0.80, "trial": 0.60, "chop": 0.58, "decline": 0.35},
        "stageMaxSlots": {"advance": 4, "trial": 3, "chop": 3, "decline": 2},
        "stageSingleCap": {"advance": 0.30, "trial": 0.25, "chop": 0.25, "decline": 0.18},
        "stageFirstScale": {"advance": 1.00, "trial": 0.82, "chop": 0.78, "decline": 0.60},
    },
    "xiwangjiejie_1to2": {
        "maxPositions": 3,
        "baseFirstWeight": 0.24,
        "addWinWeight": 0.0,
        "singleCap": 0.25,
        "stopLossPct": -6.0,
        "addOnWinMinPct": 9.5,
        "trimProfitPct": 10.0,
        "trimRatio": 0.50,
        "takeProfitPct": 18.0,
        "maxHoldDays": 3,
        "useRegimeSwitch": True,
        "useFastWeakCut": True,
        "useAddOnWin": False,
        "usePartialTrim": True,
        "rotateWeakTechInDecline": False,
        "dailyLossBreakerPct": -2.0,
        "consecLossThrottle": 2,
        "stageTotalPosCap": {"advance": 0.78, "trial": 0.68, "chop": 0.75, "decline": 0.25},
        "stageMaxSlots": {"advance": 3, "trial": 3, "chop": 3, "decline": 2},
        "stageSingleCap": {"advance": 0.25, "trial": 0.25, "chop": 0.25, "decline": 0.15},
        "stageFirstScale": {"advance": 1.00, "trial": 0.90, "chop": 1.00, "decline": 0.55},
    },
    "dongshizhang_highboard": {
        "maxPositions": 2,
        "baseFirstWeight": 0.28,
        "addWinWeight": 0.0,
        "singleCap": 0.30,
        "stopLossPct": -6.0,
        "addOnWinMinPct": 9.5,
        "trimProfitPct": 12.0,
        "trimRatio": 0.50,
        "takeProfitPct": 25.0,
        "maxHoldDays": 4,
        "useRegimeSwitch": True,
        "useFastWeakCut": True,
        "useAddOnWin": False,
        "usePartialTrim": True,
        "rotateWeakTechInDecline": False,
        "dailyLossBreakerPct": -2.5,
        "consecLossThrottle": 2,
        "stageTotalPosCap": {"advance": 0.50, "trial": 0.45, "chop": 0.50, "decline": 0.35},
        "stageMaxSlots": {"advance": 2, "trial": 2, "chop": 2, "decline": 1},
        "stageSingleCap": {"advance": 0.30, "trial": 0.25, "chop": 0.30, "decline": 0.25},
        "stageFirstScale": {"advance": 1.00, "trial": 0.90, "chop": 1.00, "decline": 0.85},
    },
    "ruhan_halfway": {
        "maxPositions": 5,
        "baseFirstWeight": 0.18,
        "addWinWeight": 0.10,
        "singleCap": 0.28,
        "stopLossPct": -5.0,
        "addOnWinMinPct": 6.0,
        "trimProfitPct": 8.0,
        "trimRatio": 0.50,
        "takeProfitPct": 14.0,
        "maxHoldDays": 3,
        "useRegimeSwitch": True,
        "useFastWeakCut": True,
        "useAddOnWin": True,
        "usePartialTrim": True,
        "rotateWeakTechInDecline": False,
        "dailyLossBreakerPct": -2.0,
        "consecLossThrottle": 2,
        "stageTotalPosCap": {"advance": 0.88, "trial": 0.72, "chop": 0.78, "decline": 0.28},
        "stageMaxSlots": {"advance": 5, "trial": 4, "chop": 5, "decline": 2},
        "stageSingleCap": {"advance": 0.28, "trial": 0.25, "chop": 0.25, "decline": 0.14},
        "stageFirstScale": {"advance": 1.00, "trial": 0.90, "chop": 0.95, "decline": 0.50},
    },
    "songsong_defensive": {
        "maxPositions": 4,
        "baseFirstWeight": 0.18,
        "addWinWeight": 0.0,
        "singleCap": 0.22,
        "stopLossPct": -3.2,
        "addOnWinMinPct": 5.0,
        "trimProfitPct": 5.0,
        "trimRatio": 0.50,
        "takeProfitPct": 8.0,
        "maxHoldDays": 2,
        "useRegimeSwitch": True,
        "useFastWeakCut": True,
        "useAddOnWin": False,
        "usePartialTrim": True,
        "rotateWeakTechInDecline": False,
        "dailyLossBreakerPct": -1.8,
        "consecLossThrottle": 2,
        "stageTotalPosCap": {"advance": 0.82, "trial": 0.62, "chop": 0.52, "decline": 0.00},
        "stageMaxSlots": {"advance": 5, "trial": 4, "chop": 4, "decline": 0},
        "stageSingleCap": {"advance": 0.22, "trial": 0.18, "chop": 0.18, "decline": 0.00},
        "stageFirstScale": {"advance": 1.00, "trial": 0.85, "chop": 0.78, "decline": 0.00},
    },
}


def board_of(symbol: str) -> str:
    s = str(symbol or "").strip().lower()
    code = s[2:] if s.startswith(("sh", "sz", "bj")) else s
    if s.startswith("bj") or code.startswith(("43", "83", "87", "88", "92")):
        return "bse"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    return "main"


def board_label(board: str) -> str:
    return {"star": "科创板", "chinext": "创业板", "bse": "北交所", "main": "主板"}.get(board, "主板")


def lot_size(symbol: str) -> int:
    return 200 if board_of(symbol) == "star" else 100


def calc_buy_shares(budget: float, price: float, symbol: str, commission_rate: float = 0.0003) -> int:
    """Compute exact A-share integer lot quantity affordable within budget."""
    if budget <= 0 or price <= 0:
        return 0
    lot = lot_size(symbol)
    unit_cost = price * (1.0 + commission_rate) * lot
    if unit_cost <= 0:
        return 0
    lots = int(budget // unit_cost)
    return lots * lot


def calc_trim_shares(held_qty: int, ratio: float, symbol: str) -> int:
    """Compute partial-sell integer shares without leaving unsellable sub-lot dust."""
    qty = int(held_qty or 0)
    if qty <= 0:
        return 0
    if ratio >= 0.99:
        return qty
    min_lot = lot_size(symbol)
    # If holding is only 1 lot (or too small to split into two valid lots), close full position.
    if qty < min_lot * 2:
        return qty
    raw = int(qty * max(0.1, min(0.95, ratio)))
    sell_qty = max(100, (raw // 100) * 100)
    remain = qty - sell_qty
    if remain < min_lot:
        # Try keeping exactly min_lot; otherwise close full position.
        if qty - min_lot >= 100:
            sell_qty = ((qty - min_lot) // 100) * 100
        else:
            sell_qty = qty
    return max(0, min(qty, sell_qty))


def capital_tier_for_equity(equity: float) -> dict[str, Any]:
    eq = max(0.0, float(equity or 0.0))
    if eq <= 50_000.0:
        return {
            "tierKey": "small",
            "label": "5万以内（精悍分仓）",
            "maxSlotsAdvance": 3,
            "maxSlotsNormal": 2,
            "singleCap": 0.45,
            "firstScale": 1.15,
        }
    if eq <= 300_000.0:
        return {
            "tierKey": "medium",
            "label": "5万–30万（标准3-4仓）",
            "maxSlotsAdvance": 4,
            "maxSlotsNormal": 3,
            "singleCap": 0.35,
            "firstScale": 1.00,
        }
    return {
        "tierKey": "large",
        "label": "30万以上（4-5仓分散）",
        "maxSlotsAdvance": 5,
        "maxSlotsNormal": 5,
        "singleCap": 0.30,
        "firstScale": 0.90,
    }


def strategy_params(style_key: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return mutable execution rule parameters for a style, applying explicit user overrides."""
    base = STYLE_EXECUTION_RULES.get(style_key, STYLE_EXECUTION_RULES["wujiang_dynamic"])
    p = {
        **base,
        "baseMaxPositions": int(base["maxPositions"]),
        "_userMaxPositions": False,
        "stageTotalPosCap": dict(base["stageTotalPosCap"]),
        "stageMaxSlots": dict(base["stageMaxSlots"]),
        "stageSingleCap": dict(base["stageSingleCap"]),
        "stageFirstScale": dict(base["stageFirstScale"]),
        # Backward-compatible aliases used by callers/UI
        "baseSingleWeight": base["baseFirstWeight"],
        "regimeScale": dict(base["stageFirstScale"]),
    }
    if isinstance(overrides, dict):
        custom_flag = overrides.get("customMaxPositions")
        raw_max_pos = overrides.get("maxPositions")
        if custom_flag is False or raw_max_pos in (None, "", 0, "0", "auto", "default"):
            p["maxPositions"] = int(base["maxPositions"])
            p["_userMaxPositions"] = False
        else:
            p["maxPositions"] = max(1, min(20, int(raw_max_pos)))
            p["_userMaxPositions"] = True
        if overrides.get("stopLossPct") is not None:
            p["stopLossPct"] = max(-20.0, min(-1.5, float(overrides["stopLossPct"])))
        if overrides.get("useRegimeSwitch") is not None:
            p["useRegimeSwitch"] = bool(overrides["useRegimeSwitch"])
        if overrides.get("useFastWeakCut") is not None:
            p["useFastWeakCut"] = bool(overrides["useFastWeakCut"])
        if overrides.get("useAddOnWin") is not None:
            p["useAddOnWin"] = bool(overrides["useAddOnWin"])
        if overrides.get("usePartialTrim") is not None:
            p["usePartialTrim"] = bool(overrides["usePartialTrim"])
    return p


def resolve_position_limits(
    style_key: str,
    stage: str,
    equity: float,
    params: dict[str, Any],
    *,
    throttled: bool = False,
    halt_new_buys: bool = False,
    ablation_no_discipline: bool = False,
) -> dict[str, Any]:
    """Compile style + market stage + capital tier + drawdown state into hard numeric caps."""
    stg = stage if stage in ("advance", "trial", "chop", "decline") else "chop"
    tier = capital_tier_for_equity(equity)
    user_custom_slots = bool(params.get("_userMaxPositions"))
    configured_max = max(1, int(params.get("maxPositions", 3)))
    base_max = max(1, int(params.get("baseMaxPositions", configured_max)))
    use_regime = (not ablation_no_discipline) and bool(params.get("useRegimeSwitch", True))
    use_fast_cut = (not ablation_no_discipline) and bool(params.get("useFastWeakCut", True))
    use_add_win = (not ablation_no_discipline) and bool(params.get("useAddOnWin", False))
    use_partial_trim = (not ablation_no_discipline) and bool(params.get("usePartialTrim", True))

    if ablation_no_discipline:
        max_slots = configured_max
        total_cap = 0.96
        if user_custom_slots:
            slot_share = total_cap / max(1, max_slots)
            single_cap = min(0.96, max(slot_share * 1.35, slot_share))
            first_w = min(single_cap, slot_share * 0.96)
        else:
            first_w = float(params.get("baseFirstWeight", 0.20))
            single_cap = max(0.35, first_w * 1.5)
        add_w = 0.0
        stop_loss = -15.0
        max_hold = 8
        use_force_trim = False
        rotate_tech = False
    elif not use_regime:
        total_cap = 0.90
        if user_custom_slots:
            max_slots = configured_max
            slot_share = total_cap / max(1, max_slots)
            raw_add = float(params.get("addWinWeight", 0.12) or 0.12)
            add_w = min(raw_add, slot_share * 0.45) if use_add_win else 0.0
            first_w = slot_share * (0.78 if use_add_win else 0.96)
            single_cap = min(0.95, max(slot_share * 1.35, first_w + add_w))
        else:
            tier_slots = tier["maxSlotsAdvance"]
            max_slots = min(configured_max, tier_slots)
            single_cap = min(float(params.get("singleCap", 0.35)), tier["singleCap"])
            first_w = min(single_cap, float(params.get("baseFirstWeight", 0.18)) * tier["firstScale"])
            add_w = float(params.get("addWinWeight", 0.12) or 0.12) if use_add_win else 0.0
        stop_loss = float(params.get("stopLossPct", -5.0))
        max_hold = int(params.get("maxHoldDays", 4))
        use_force_trim = False
        rotate_tech = False
    else:
        style_slots = int(params["stageMaxSlots"].get(stg, base_max))
        base_total_cap = float(params["stageTotalPosCap"].get(stg, 0.58))
        if user_custom_slots:
            if style_slots <= 0 or base_total_cap <= 0.01:
                max_slots = 0
                total_cap = 0.0
                single_cap = 0.0
                first_w = 0.0
                add_w = 0.0
            else:
                adv_base = max(1, int(params["stageMaxSlots"].get("advance", base_max)))
                if stg == "advance" or style_slots >= adv_base:
                    max_slots = configured_max
                else:
                    max_slots = max(1, min(configured_max, int(round(configured_max * (style_slots / adv_base)))))
                if stg != "decline":
                    if style_key == "dongshizhang_highboard" and configured_max != 2:
                        base_total_cap = {"advance": 0.85, "trial": 0.70, "chop": 0.78}.get(stg, base_total_cap)
                    total_cap = min(0.90, max(base_total_cap, min(0.88, max_slots * 0.07)))
                else:
                    total_cap = min(0.50, max(base_total_cap, min(0.42, max_slots * 0.06))) if base_total_cap > 0 else 0.0
                slot_share = total_cap / max(1, max_slots)
                if use_add_win:
                    first_w = slot_share * (0.76 if max_slots <= 2 else 0.82)
                    add_w = slot_share * (0.24 if max_slots <= 2 else 0.35)
                    single_cap = min(0.95, max(slot_share * 1.45, first_w + add_w))
                else:
                    first_w = slot_share * 0.96
                    add_w = 0.0
                    single_cap = min(0.95, max(slot_share * 1.15, first_w))
        else:
            tier_slots = tier["maxSlotsAdvance"] if stg == "advance" else tier["maxSlotsNormal"]
            max_slots = min(style_slots, tier_slots) if style_slots > 0 else 0
            total_cap = base_total_cap
            style_single = float(params["stageSingleCap"].get(stg, params.get("singleCap", 0.30)))
            single_cap = min(style_single, tier["singleCap"]) if style_single > 0 else 0.0
            stage_scale = float(params["stageFirstScale"].get(stg, 0.8))
            first_w = min(single_cap, float(params.get("baseFirstWeight", 0.18)) * stage_scale * tier["firstScale"])
            # If user manually enabled useAddOnWin on a non-pyramiding style, give sensible +10% step
            raw_add = float(params.get("addWinWeight", 0.0))
            add_w = (raw_add if raw_add > 0 else 0.10) if use_add_win else 0.0
        stop_loss = float(params.get("stopLossPct", -5.0))
        max_hold = int(params.get("maxHoldDays", 4))
        use_force_trim = True
        rotate_tech = bool(params.get("rotateWeakTechInDecline", False))

    if throttled and not ablation_no_discipline:
        first_w *= 0.50
        single_cap *= 0.70
        total_cap *= 0.65
        add_w = 0.0
        if max_slots > 1:
            max_slots = max(1, max_slots - 1)

    if total_cap <= 0.01 or max_slots <= 0:
        total_cap = 0.0
        max_slots = 0
        first_w = 0.0
        add_w = 0.0

    return {
        "styleKey": style_key,
        "stage": stg,
        "capitalTierKey": tier["tierKey"],
        "capitalTierLabel": tier["label"],
        "customMaxPositions": user_custom_slots,
        "configuredMaxPositions": configured_max,
        "baseMaxPositions": base_max,
        "totalPosCap": round(total_cap, 4),
        "totalPosCapPct": round(total_cap * 100.0, 1),
        "singleCap": round(single_cap, 4),
        "singleCapPct": round(single_cap * 100.0, 1),
        "maxSlots": int(max_slots),
        "firstEntryWeight": round(first_w, 4),
        "firstEntryPct": round(first_w * 100.0, 1),
        "addWinWeight": round(add_w, 4),
        "addWinPct": round(add_w * 100.0, 1),
        "addOnWinMinPct": float(params.get("addOnWinMinPct", 5.0)),
        "trimProfitPct": float(params.get("trimProfitPct", 8.0)),
        "trimRatio": float(params.get("trimRatio", 0.50)),
        "trimRatioPct": round(float(params.get("trimRatio", 0.50)) * 100.0, 0),
        "takeProfitPct": float(params.get("takeProfitPct", 16.0)),
        "stopLossPct": round(stop_loss, 2),
        "maxHoldDays": int(max_hold),
        "dailyLossBreakerPct": float(params.get("dailyLossBreakerPct", -2.0)),
        "useRegimeSwitch": use_regime,
        "useFastWeakCut": use_fast_cut,
        "useAddOnWin": use_add_win and add_w > 0,
        "usePartialTrim": use_partial_trim,
        "useForceTrim": use_force_trim,
        "rotateWeakTechInDecline": rotate_tech,
        "throttled": bool(throttled and not ablation_no_discipline),
        "haltNewBuys": bool(halt_new_buys and not ablation_no_discipline),
    }


def describe_active_rules(style_key: str, equity: float, params: dict[str, Any]) -> dict[str, Any]:
    """Summarize the executable rule table enforced for a given style and capital base."""
    tier = capital_tier_for_equity(equity)
    user_custom_slots = bool(params.get("_userMaxPositions"))
    configured_max = max(1, int(params.get("maxPositions", 3)))
    base_max = max(1, int(params.get("baseMaxPositions", configured_max)))
    stage_caps = {}
    for stg in ("advance", "trial", "chop", "decline"):
        lim = resolve_position_limits(style_key, stg, equity, params)
        stage_caps[stg] = {
            "totalPosCapPct": lim["totalPosCapPct"],
            "singleCapPct": lim["singleCapPct"],
            "maxSlots": lim["maxSlots"],
            "firstEntryPct": lim["firstEntryPct"],
        }
    adv = stage_caps["advance"]
    dec = stage_caps["decline"]
    slot_badge = (
        f"持仓上限：自定义最多 {configured_max} 只（原版默认 {base_max} 只）"
        if user_custom_slots
        else f"资金档位：{tier['label']}（原版默认最多 {adv['maxSlots']} 仓）"
    )
    return {
        "styleKey": style_key,
        "capitalTierKey": tier["tierKey"],
        "capitalTierLabel": tier["label"],
        "customMaxPositions": user_custom_slots,
        "maxPositions": configured_max,
        "baseMaxPositions": base_max,
        "stopLossPct": float(params.get("stopLossPct", -5.0)),
        "trimProfitPct": float(params.get("trimProfitPct", 8.0)),
        "trimRatioPct": round(float(params.get("trimRatio", 0.50)) * 100.0, 0),
        "takeProfitPct": float(params.get("takeProfitPct", 16.0)),
        "addOnWinMinPct": float(params.get("addOnWinMinPct", 5.0)),
        "addWinPct": round(float(params.get("addWinWeight", 0.0)) * 100.0, 1) if params.get("useAddOnWin") else 0.0,
        "dailyLossBreakerPct": float(params.get("dailyLossBreakerPct", -2.0)),
        "maxHoldDays": int(params.get("maxHoldDays", 4)),
        "useRegimeSwitch": bool(params.get("useRegimeSwitch", True)),
        "useFastWeakCut": bool(params.get("useFastWeakCut", True)),
        "useAddOnWin": bool(params.get("useAddOnWin", False)),
        "usePartialTrim": bool(params.get("usePartialTrim", True)),
        "rotateWeakTechInDecline": bool(params.get("rotateWeakTechInDecline", False)),
        "stageCaps": stage_caps,
        "summaryBadges": [
            slot_badge,
            f"总仓硬上限：主升 {adv['totalPosCapPct']:.0f}% / 震荡 {stage_caps['chop']['totalPosCapPct']:.0f}% / 退潮 {dec['totalPosCapPct']:.0f}%",
            f"单票硬上限：≤{adv['singleCapPct']:.0f}%（主升最多 {adv['maxSlots']} 仓 · 首仓约 {adv['firstEntryPct']:.1f}%）",
            f"止损红线：{float(params.get('stopLossPct', -5.0)):.1f}% + 炸板/破MA5次日快砍",
            f"冲高减半：非涨停浮盈≥+{float(params.get('trimProfitPct', 8.0)):.0f}% 自动减 50%",
            f"浮盈加仓：{'封板且浮盈≥+' + str(int(params.get('addOnWinMinPct', 5))) + '% 顺势加仓（绝不补亏）' if params.get('useAddOnWin') else '关闭（严格等权/不加码）'}",
            f"回撤降档：单日亏≥{abs(float(params.get('dailyLossBreakerPct', -2.0))):.1f}% 或连亏2笔自动仓位减半",
        ],
    }


def plan_next_day_orders(
    *,
    style_key: str,
    params: dict[str, Any],
    env: dict[str, Any],
    equity: float,
    cash: float,
    holdings: list[dict[str, Any]],
    features_by_sym: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    throttled: bool = False,
    halt_new_buys: bool = False,
    ablation_no_discipline: bool = False,
    commission_rate: float = 0.0003,
    stamp_duty_sell: float = 0.0005,
    slippage_rate: float = 0.0015,
) -> dict[str, Any]:
    """Deterministically plan next-trading-day orders & projected holdings under hard rules.

    Input `holdings` items:
      {symbol, code, name, qty, avgCost, peakPrice, lastClose, heldDays, addedWin, trimmedProfit}
    Input `features_by_sym`:
      symbol -> today's EOD feature dict (close, high, pct, ma5, ma20, aboveMA5, aboveMA20,
      isZt, isDt, bombedZt, consecZt, board, ...)
    Input `candidates`:
      sorted list of {symbol, code, name, score, close, board, ...}
    """
    stage = str(env.get("stage") or "chop")
    zz_pct = float(env.get("zz1000ChangePct") or 0.0)
    kc_abv = env.get("kc50AboveMA20")
    to_yi = float(env.get("turnoverYi") or 15000.0)

    eq = max(1.0, float(equity or 0.0))
    proj_cash = max(0.0, float(cash or 0.0))
    limits = resolve_position_limits(
        style_key,
        stage,
        eq,
        params,
        throttled=throttled,
        halt_new_buys=halt_new_buys,
        ablation_no_discipline=ablation_no_discipline,
    )

    # Working copy of holdings for sequential order projection
    work: dict[str, dict[str, Any]] = {}
    for h in holdings:
        qty = int(h.get("qty") or h.get("quantity") or 0)
        if qty <= 0:
            continue
        sym = str(h.get("symbol") or "").lower()
        if not sym:
            continue
        f = features_by_sym.get(sym)
        px = float((f.get("close") if f else None) or h.get("lastClose") or h.get("latestPrice") or h.get("avgCost") or 0.0)
        avg_cost = float(h.get("avgCost") or h.get("averageCost") or px or 0.0)
        peak_px = max(float(h.get("peakPrice") or px), float((f.get("high") if f else None) or px))
        work[sym] = {
            "symbol": sym,
            "code": str(h.get("code") or sym[2:]),
            "name": str(h.get("name") or sym[2:]),
            "board": (f.get("board") if f else None) or board_of(sym),
            "qty": qty,
            "origQty": qty,
            "avgCost": avg_cost,
            "cost": avg_cost * qty,
            "refPrice": px,
            "peakPrice": peak_px,
            "heldDays": int(h.get("heldDays") or 0),
            "addedWin": bool(h.get("addedWin")),
            "trimmedProfit": bool(h.get("trimmedProfit")),
            "plannedExit": False,
        }

    def total_mv() -> float:
        return sum(p["qty"] * p["refPrice"] for p in work.values() if p["qty"] > 0)

    def cur_total_pos() -> float:
        return (total_mv() / eq) if eq > 0 else 0.0

    before_pos_pct = round(cur_total_pos() * 100.0, 1)
    before_count = len(work)
    before_cash = round(proj_cash, 2)

    orders: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seq = 0

    def emit_sell(pos: dict[str, Any], sell_qty: int, action_type: str, rule_code: str, reason: str) -> None:
        nonlocal proj_cash, seq
        sell_qty = max(0, min(pos["qty"], int(sell_qty)))
        if sell_qty <= 0:
            return
        px = pos["refPrice"]
        before_w = (pos["qty"] * px / eq) * 100.0 if eq > 0 else 0.0
        gross = sell_qty * px
        fee = max(5.0, gross * commission_rate) + gross * stamp_duty_sell
        ratio = sell_qty / max(1, pos["qty"])
        pos["qty"] -= sell_qty
        pos["cost"] *= (1.0 - ratio)
        if pos["qty"] <= 0:
            pos["qty"] = 0
            pos["cost"] = 0.0
            pos["plannedExit"] = True
        else:
            if action_type == "trim_profit":
                pos["trimmedProfit"] = True
        proj_cash += max(0.0, gross - fee)
        after_w = (pos["qty"] * px / eq) * 100.0 if eq > 0 else 0.0
        # Consolidate multiple partial sells for the same symbol on the same morning
        existing = next((o for o in orders if o["side"] == "sell" and o["symbol"] == pos["symbol"]), None)
        if existing is not None:
            existing["quantity"] += sell_qty
            existing["estAmount"] = round(existing["estAmount"] + gross, 2)
            existing["sellRatio"] = round(min(1.0, existing["quantity"] / max(1, pos["origQty"])), 4)
            existing["afterWeightPct"] = round(after_w, 1)
            existing["deltaWeightPct"] = round(after_w - existing["beforeWeightPct"], 1)
            existing["afterTotalPosPct"] = round(cur_total_pos() * 100.0, 1)
            if pos["qty"] <= 0:
                existing["actionType"] = action_type
                existing["actionLabel"] = ACTION_LABELS.get(action_type, existing["actionLabel"])
            existing["reason"] = f"{existing['reason']}；{reason}"
            return
        seq += 1
        orders.append({
            "seq": seq,
            "side": "sell",
            "actionType": action_type,
            "actionLabel": ACTION_LABELS.get(action_type, "卖出"),
            "symbol": pos["symbol"],
            "code": pos["code"],
            "name": pos["name"],
            "board": board_label(pos["board"]),
            "quantity": sell_qty,
            "sellRatio": round(ratio, 4),
            "refPrice": round(px, 3),
            "estAmount": round(gross, 2),
            "beforeWeightPct": round(before_w, 1),
            "afterWeightPct": round(after_w, 1),
            "deltaWeightPct": round(after_w - before_w, 1),
            "afterTotalPosPct": round(cur_total_pos() * 100.0, 1),
            "execWindow": EXEC_WINDOWS.get(action_type, "09:26–10:15"),
            "ruleCode": rule_code,
            "reason": reason,
        })

    # ── Phase 1 & Phase 2A: Evaluate each existing holding ───────────────────
    stop_line = limits["stopLossPct"]
    use_fast_cut = limits["useFastWeakCut"]
    use_partial_trim = limits["usePartialTrim"]
    trim_profit_pct = limits["trimProfitPct"]
    trim_ratio = limits["trimRatio"]
    take_profit_pct = limits["takeProfitPct"]
    max_hold = limits["maxHoldDays"]
    add_candidates: list[dict[str, Any]] = []

    for sym, pos in list(work.items()):
        f = features_by_sym.get(sym)
        if not f:
            continue
        px = pos["refPrice"]
        avg_c = pos["avgCost"] if pos["avgCost"] > 0 else px
        ret_now = ((px / avg_c) - 1.0) * 100.0 if avg_c > 0 else 0.0
        dd_from_peak = ((px / pos["peakPrice"]) - 1.0) * 100.0 if pos["peakPrice"] > 0 else 0.0
        held = pos["heldDays"]
        is_zt = bool(f.get("isZt"))
        is_dt = bool(f.get("isDt"))
        bombed = bool(f.get("bombedZt"))
        above_ma5 = bool(f.get("aboveMA5", True))
        above_ma20 = bool(f.get("aboveMA20", True))
        consec_zt = int(f.get("consecZt") or 0)
        b_code = pos["board"]

        # After pyramiding or partial profit trim, tighten stop floor to breakeven (-0.5%)
        eff_stop = max(stop_line, -0.5) if ((pos["addedWin"] or pos["trimmedProfit"]) and not ablation_no_discipline) else stop_line
        weak_cut_thresh = -4.5 if b_code in ("star", "chinext") else -3.5

        # 1) Full-exit rules (highest priority)
        if bombed or is_dt:
            emit_sell(pos, pos["qty"], "cut_weak", "BOMBED_OR_DT", "当日炸板/跌停形态破坏，次日早盘无条件清仓")
            continue
        if ret_now <= eff_stop:
            tag = "加仓/减半后保本止损" if eff_stop > stop_line else "触发硬止损红线"
            emit_sell(pos, pos["qty"], "cut_stop", "HARD_STOP", f"{tag}（当前 {ret_now:+.1f}% ≤ {eff_stop:.1f}%），全清止损")
            continue
        if limits["rotateWeakTechInDecline"] and b_code in ("star", "chinext") and stage == "decline" and (kc_abv is False or to_yi < 18000.0) and ret_now < 3.0:
            emit_sell(pos, pos["qty"], "sell_rotate", "TECH_ROTATE_OUT", f"科创50走弱/缩量({to_yi:.0f}亿)且大盘退潮，清退弱势20cm旧科技")
            continue
        if use_fast_cut and held == 0 and (not is_zt) and ret_now <= weak_cut_thresh and (not above_ma5):
            emit_sell(pos, pos["qty"], "cut_weak", "DAY1_WEAK_CUT", f"首日走弱破MA5({ret_now:+.1f}%)，次日早盘快刀纠错")
            continue
        if use_fast_cut and held >= 1 and (not is_zt) and (not above_ma5) and ret_now < 2.0:
            emit_sell(pos, pos["qty"], "cut_weak", "BREAK_MA5_CUT", f"跌破MA5且无盈利垫({ret_now:+.1f}%)，次日早盘砍弱换子弹")
            continue
        if use_fast_cut and held >= 1 and (not is_zt) and (not above_ma20) and ret_now <= 2.0:
            emit_sell(pos, pos["qty"], "cut_weak", "BREAK_MA20_CUT", f"跌破MA20弱势结构({ret_now:+.1f}%)，清仓切回MA20上方主线")
            continue
        if stage == "decline" and zz_pct <= -1.8 and consec_zt >= 4 and is_zt and not ablation_no_discipline:
            sell_q = calc_trim_shares(pos["qty"], 0.65, sym)
            emit_sell(pos, sell_q, "trim_profit", "CLIMAX_DECLINE_TRIM", f"{consec_zt}连板高潮遇大盘长阴({zz_pct:+.1f}%)，次日冲高先兑现锁定利润")
            continue
        if ret_now >= take_profit_pct and not is_zt:
            emit_sell(pos, pos["qty"], "sell_profit", "TAKE_PROFIT_FULL", f"达标止盈({ret_now:+.1f}% ≥ +{take_profit_pct:.0f}%)且未封板，落袋为安")
            continue
        if ret_now >= 7.0 and dd_from_peak <= -6.0 and not is_zt:
            emit_sell(pos, pos["qty"], "sell_profit", "TRAIL_STOP", f"高位回撤移动止盈（距峰值 {dd_from_peak:.1f}%，净盈 {ret_now:+.1f}%）")
            continue
        if pos["trimmedProfit"] and (not is_zt) and (not above_ma5):
            emit_sell(pos, pos["qty"], "sell_profit", "CORE_MA5_EXIT", f"已减半底仓跌破MA5，剩余仓位移动止盈清仓({ret_now:+.1f}%)")
            continue
        if held >= max_hold and (not is_zt) and ((not above_ma5) or ret_now < 4.0):
            emit_sell(pos, pos["qty"], "sell_time", "MAX_HOLD_ROTATE", f"持仓满{max_hold}日走平({ret_now:+.1f}%)，换股释放槽位")
            continue

        # 2) Partial profit-taking trim (未封板大盈先减仓 50%，底仓留守 MA5!)
        if use_partial_trim and (not is_zt) and (not pos["trimmedProfit"]) and ret_now >= trim_profit_pct:
            sell_q = calc_trim_shares(pos["qty"], trim_ratio, sym)
            if sell_q > 0:
                if sell_q >= pos["qty"]:
                    emit_sell(pos, sell_q, "sell_profit", "TRIM_SMALL_FULL", f"浮盈达+{ret_now:.1f}%未封板（仅1手无法拆卖，全清落袋）")
                else:
                    emit_sell(pos, sell_q, "trim_profit", "PROFIT_TRIM_HALF", f"浮盈+{ret_now:.1f}% ≥ +{trim_profit_pct:.0f}%且未封板，自动减仓{int(trim_ratio*100)}%锁利润（余仓守MA5）")
            continue

        # 3) Single-stock hard ceiling trim (non-ZT stock exceeding singleCap * 1.12)
        cur_w = (pos["qty"] * px / eq) if eq > 0 else 0.0
        if limits["useForceTrim"] and limits["singleCap"] > 0 and (not is_zt) and cur_w > limits["singleCap"] * 1.12:
            excess_val = (cur_w - limits["singleCap"]) * eq
            raw_sell = int(excess_val / max(px, 0.01))
            sell_q = max(100, (raw_sell // 100) * 100)
            if pos["qty"] - sell_q < lot_size(sym):
                sell_q = pos["qty"] if limits["singleCap"] < 0.05 else max(0, ((pos["qty"] - lot_size(sym)) // 100) * 100)
            if sell_q > 0:
                emit_sell(
                    pos,
                    sell_q,
                    "trim_cap",
                    "SINGLE_CAP_TRIM",
                    f"单票仓位 {cur_w*100:.1f}% 超出「{stage}」单票硬上限 {limits['singleCapPct']:.0f}%，自动压仓",
                )
            continue

        # 4) Eligible winner for pyramiding (only ZT + profit >= addOnWinMinPct + not yet added)
        if limits["useAddOnWin"] and (not pos["addedWin"]) and is_zt and ret_now >= limits["addOnWinMinPct"]:
            add_candidates.append({
                "pos": pos,
                "ret_now": ret_now,
                "cur_w": cur_w,
            })

    # ── Phase 2B: Regime total-position cap forced de-risking ────────────────
    if limits["useForceTrim"] and cur_total_pos() > limits["totalPosCap"] + 0.03:
        # Sort active holdings weakest-first (non-ZT before ZT, lower PnL first)
        active_list = [p for p in work.values() if p["qty"] > 0]
        def weak_sort_key(p: dict[str, Any]) -> tuple[int, float]:
            f = features_by_sym.get(p["symbol"]) or {}
            is_z = 1 if f.get("isZt") else 0
            ret = ((p["refPrice"] / p["avgCost"]) - 1.0) if p["avgCost"] > 0 else 0.0
            return (is_z, ret)

        active_list.sort(key=weak_sort_key)
        for pos in active_list:
            excess_ratio = cur_total_pos() - limits["totalPosCap"]
            if excess_ratio <= 0.02:
                break
            f = features_by_sym.get(pos["symbol"]) or {}
            # In non-zero cap decline, let sealed ZT leaders ride unless total cap is literally 0% (Songsong shutoff)
            if f.get("isZt") and limits["totalPosCap"] > 0.01:
                continue
            px = pos["refPrice"]
            if px <= 0:
                continue
            if limits["totalPosCap"] <= 0.01:
                sell_q = pos["qty"]
                reason = f"触发「{style_key}」弱市断电空仓（总仓硬上限 0%），自动清仓避险"
            else:
                need_val = excess_ratio * eq
                pos_val = pos["qty"] * px
                if need_val >= pos_val * 0.72 or pos["qty"] < lot_size(pos["symbol"]) * 2:
                    sell_q = pos["qty"]
                else:
                    sell_q = calc_trim_shares(pos["qty"], min(0.75, need_val / max(pos_val, 1.0)), pos["symbol"])
                reason = f"当前总仓位 {cur_total_pos()*100:.1f}% 超出该阶段硬上限 {limits['totalPosCapPct']:.0f}%，按弱到强自动降仓"
            emit_sell(pos, sell_q, "trim_regime", "REGIME_TOTAL_CAP", reason)

    # ── Phase 3: Pyramiding ADDS on confirmed ZT winners ─────────────────────
    add_candidates.sort(key=lambda x: -x["ret_now"])
    for item in add_candidates:
        pos = item["pos"]
        if pos["qty"] <= 0:
            continue
        if stage == "decline" or limits["haltNewBuys"] or limits["throttled"]:
            blocked.append({
                "symbol": pos["symbol"], "code": pos["code"], "name": pos["name"],
                "actionType": "add_win", "ruleCode": "THROTTLE_OR_DECLINE_NO_ADD",
                "reason": "处于退潮/风控降档状态，代码自动拦截加仓",
            })
            continue
        px = pos["refPrice"]
        cur_w = (pos["qty"] * px / eq) if eq > 0 else 0.0
        single_room = max(0.0, limits["singleCap"] - cur_w)
        total_room = max(0.0, limits["totalPosCap"] - cur_total_pos())
        add_w = min(limits["addWinWeight"], single_room, total_room)
        if add_w < 0.04:
            why = (
                f"当前仓位 {cur_w*100:.1f}% 已近单票硬上限 {limits['singleCapPct']:.0f}%，代码拦截加仓"
                if single_room < 0.04
                else f"账户总仓位 {cur_total_pos()*100:.1f}% 已近上限 {limits['totalPosCapPct']:.0f}%，代码拦截加仓"
            )
            blocked.append({
                "symbol": pos["symbol"], "code": pos["code"], "name": pos["name"],
                "actionType": "add_win", "ruleCode": "CAP_BLOCK_ADD", "reason": why,
            })
            continue
        exec_est_px = px * (1.0 + slippage_rate)
        budget = min(proj_cash * 0.96, eq * add_w)
        qty = calc_buy_shares(budget, exec_est_px, pos["symbol"], commission_rate)
        if qty <= 0:
            blocked.append({
                "symbol": pos["symbol"], "code": pos["code"], "name": pos["name"],
                "actionType": "add_win", "ruleCode": "CASH_BLOCK_ADD",
                "reason": f"剩余可用资金不足 1 手加仓（现金 ¥{proj_cash:,.0f}）",
            })
            continue
        before_w = cur_w * 100.0
        gross = qty * exec_est_px
        fee = max(5.0, gross * commission_rate)
        proj_cash = max(0.0, proj_cash - gross - fee)
        pos["qty"] += qty
        pos["cost"] += gross + fee
        pos["avgCost"] = pos["cost"] / max(1, pos["qty"])
        pos["addedWin"] = True
        after_w = (pos["qty"] * px / eq) * 100.0
        seq += 1
        orders.append({
            "seq": seq,
            "side": "buy",
            "actionType": "add_win",
            "actionLabel": ACTION_LABELS["add_win"],
            "symbol": pos["symbol"],
            "code": pos["code"],
            "name": pos["name"],
            "board": board_label(pos["board"]),
            "quantity": qty,
            "targetWeight": round(add_w, 4),
            "refPrice": round(px, 3),
            "estAmount": round(gross, 2),
            "beforeWeightPct": round(before_w, 1),
            "afterWeightPct": round(after_w, 1),
            "deltaWeightPct": round(after_w - before_w, 1),
            "afterTotalPosPct": round(cur_total_pos() * 100.0, 1),
            "execWindow": EXEC_WINDOWS["add_win"],
            "ruleCode": "PYRAMID_ZT_WINNER",
            "reason": f"封板确认且浮盈 +{item['ret_now']:.1f}%，顺势加仓（单票压在 ≤{limits['singleCapPct']:.0f}%，加仓后止损上移至保本）",
        })

    # ── Phase 4: Sized NEW BUYS within remaining slots & totalPosCap ─────────
    active_count = sum(1 for p in work.values() if p["qty"] > 0)
    open_slots = max(0, limits["maxSlots"] - active_count)

    # Extra master-specific decline shutoff (e.g. Ruhan declines hard)
    decline_shutoff = False
    if limits["useRegimeSwitch"] and style_key == "ruhan_halfway" and stage == "decline" and zz_pct < -0.5:
        decline_shutoff = True
    if limits["useRegimeSwitch"] and style_key == "songsong_defensive" and stage == "decline":
        decline_shutoff = True

    if candidates:
        if limits["haltNewBuys"]:
            for c in candidates[:3]:
                blocked.append({
                    "symbol": c["symbol"], "code": c["code"], "name": c["name"],
                    "actionType": "open_buy", "ruleCode": "DAILY_LOSS_BREAKER",
                    "reason": f"触发单日亏损≥{abs(limits['dailyLossBreakerPct']):.1f}%熔断红线，暂停新开仓",
                })
        elif decline_shutoff or limits["totalPosCap"] <= 0.01 or limits["maxSlots"] <= 0:
            for c in candidates[:3]:
                blocked.append({
                    "symbol": c["symbol"], "code": c["code"], "name": c["name"],
                    "actionType": "open_buy", "ruleCode": "DECLINE_SHUTOFF",
                    "reason": f"当前大盘处于退潮期且触发该模式空仓/断电开关，拦截新开仓",
                })
        else:
            for c in candidates:
                sym = c["symbol"]
                if sym in work and work[sym]["qty"] > 0:
                    continue
                if open_slots <= 0:
                    blocked.append({
                        "symbol": sym, "code": c["code"], "name": c["name"],
                        "actionType": "open_buy", "ruleCode": "MAX_SLOTS_FULL",
                        "reason": f"已达当前阶段最大分仓数 {limits['maxSlots']} 只，代码拦截超额开仓",
                    })
                    if len(blocked) >= 6:
                        break
                    continue
                total_room = max(0.0, limits["totalPosCap"] - cur_total_pos())
                if total_room < 0.04:
                    blocked.append({
                        "symbol": sym, "code": c["code"], "name": c["name"],
                        "actionType": "open_buy", "ruleCode": "TOTAL_CAP_FULL",
                        "reason": f"账户总仓位 {cur_total_pos()*100:.1f}% 已达本阶段上限 {limits['totalPosCapPct']:.0f}%，拦截新开仓",
                    })
                    if len(blocked) >= 6:
                        break
                    continue
                px = float(c.get("close") or 0.0)
                if px <= 0:
                    continue
                target_w = min(limits["firstEntryWeight"], limits["singleCap"], total_room)
                exec_est_px = px * (1.0 + slippage_rate)
                budget = min(proj_cash * 0.96, eq * target_w)
                qty = calc_buy_shares(budget, exec_est_px, sym, commission_rate)
                if qty <= 0:
                    blocked.append({
                        "symbol": sym, "code": c["code"], "name": c["name"],
                        "actionType": "open_buy", "ruleCode": "LOT_BUDGET_INSUFFICIENT",
                        "reason": f"按首仓比例 {target_w*100:.1f}%（约 ¥{budget:,.0f}）不足买入 1 手（{lot_size(sym)}股）",
                    })
                    continue
                gross = qty * exec_est_px
                fee = max(5.0, gross * commission_rate)
                proj_cash = max(0.0, proj_cash - gross - fee)
                work[sym] = {
                    "symbol": sym,
                    "code": c["code"],
                    "name": c["name"],
                    "board": c.get("boardCode") or board_of(sym),
                    "qty": qty,
                    "origQty": 0,
                    "avgCost": (gross + fee) / qty,
                    "cost": gross + fee,
                    "refPrice": px,
                    "peakPrice": px,
                    "heldDays": 0,
                    "addedWin": False,
                    "trimmedProfit": False,
                    "plannedExit": False,
                }
                open_slots -= 1
                after_w = (qty * px / eq) * 100.0
                seq += 1
                throttle_note = "（已触发降档减半）" if limits["throttled"] else ""
                orders.append({
                    "seq": seq,
                    "side": "buy",
                    "actionType": "open_buy",
                    "actionLabel": ACTION_LABELS["open_buy"],
                    "symbol": sym,
                    "code": c["code"],
                    "name": c["name"],
                    "board": board_label(work[sym]["board"]),
                    "quantity": qty,
                    "targetWeight": round(target_w, 4),
                    "refPrice": round(px, 3),
                    "estAmount": round(gross, 2),
                    "beforeWeightPct": 0.0,
                    "afterWeightPct": round(after_w, 1),
                    "deltaWeightPct": round(after_w, 1),
                    "afterTotalPosPct": round(cur_total_pos() * 100.0, 1),
                    "execWindow": EXEC_WINDOWS["open_buy"],
                    "ruleCode": "SIZED_FIRST_ENTRY",
                    "reason": f"信号评分 {c.get('score', 0):.0f} · 按分仓规则建首仓 {after_w:.1f}%{throttle_note}（上限 {limits['maxSlots']} 仓 / 总仓 ≤{limits['totalPosCapPct']:.0f}%）",
                })

    # ── Build Projected Holdings snapshot AFTER executing all planned orders ─
    projected_holdings: list[dict[str, Any]] = []
    for sym, pos in work.items():
        if pos["qty"] <= 0:
            continue
        f = features_by_sym.get(sym) or {}
        px = pos["refPrice"]
        mv = pos["qty"] * px
        w_pct = (mv / eq * 100.0) if eq > 0 else 0.0
        avg_c = pos["avgCost"] if pos["avgCost"] > 0 else px
        pnl_pct = ((px / avg_c) - 1.0) * 100.0 if avg_c > 0 else 0.0
        eff_stop = max(stop_line, -0.5) if (pos["addedWin"] or pos["trimmedProfit"]) else stop_line
        stop_px = round(avg_c * (1.0 + eff_stop / 100.0), 2) if avg_c > 0 else None
        if pos["origQty"] == 0:
            status_tag = "新开首仓"
        elif pos["addedWin"] and pos["qty"] > pos["origQty"]:
            status_tag = "浮盈加仓"
        elif pos["qty"] < pos["origQty"]:
            status_tag = "已减半/降仓"
        else:
            status_tag = "锁仓续持"
        projected_holdings.append({
            "symbol": sym,
            "code": pos["code"],
            "name": pos["name"],
            "board": board_label(pos["board"]),
            "quantity": pos["qty"],
            "avgCost": round(avg_c, 3),
            "refPrice": round(px, 3),
            "marketValue": round(mv, 2),
            "weightPct": round(w_pct, 1),
            "unrealizedPnlPct": round(pnl_pct, 2),
            "statusTag": status_tag,
            "addedWin": pos["addedWin"],
            "trimmedProfit": pos["trimmedProfit"],
            "stopPrice": stop_px,
            "ma5": round(float(f["ma5"]), 2) if f.get("ma5") else None,
        })
    projected_holdings.sort(key=lambda x: -x["weightPct"])

    after_pos_pct = round(cur_total_pos() * 100.0, 1)
    after_cash_pct = round(max(0.0, 100.0 - after_pos_pct), 1)
    projected_summary = {
        "beforePosPct": before_pos_pct,
        "afterPosPct": after_pos_pct,
        "beforeCount": before_count,
        "afterCount": len(projected_holdings),
        "beforeCash": before_cash,
        "afterCash": round(proj_cash, 2),
        "afterCashPct": after_cash_pct,
        "totalPosCapPct": limits["totalPosCapPct"],
        "singleCapPct": limits["singleCapPct"],
        "maxSlots": limits["maxSlots"],
        "buyCount": sum(1 for o in orders if o["actionType"] == "open_buy"),
        "addCount": sum(1 for o in orders if o["actionType"] == "add_win"),
        "trimCount": sum(1 for o in orders if o["actionType"] in ("trim_profit", "trim_regime", "trim_cap")),
        "sellCount": sum(1 for o in orders if o["actionType"] in ("cut_stop", "cut_weak", "sell_rotate", "sell_profit", "sell_time")),
        "blockedCount": len(blocked),
    }

    return {
        "limits": limits,
        "orders": orders,
        "projectedHoldings": projected_holdings,
        "projectedSummary": projected_summary,
        "blockedSignals": blocked[:8],
    }
