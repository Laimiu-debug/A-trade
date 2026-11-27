"""Dynamic, causal, multi-axis A-share market style radar and early-warning engine (v2).

Fixes the rigidity of static binary thresholds and 2-day confirmation lag by adding:
  1. Continuous Offense/Defense Thermometer (0–100) + 3d EMA + 5d Velocity + 30d Adaptive Percentile.
  2. Board Track Axis (-100 主板10cm题材 ↔ +100 20cm科创创业科技) from real per-board breadth,
     5d/20d median returns, and relative turnover-share rotation.
  3. Structure Axis (-100 大容量趋势中军 ↔ +100 短线连板接力) comparing top-10% turnover core
     stocks against short-term multi-board relay heat.
  4. Early-Warning Style-Shift & Divergence Alerts (`shiftAlerts`) that trigger BEFORE lagging
     2-day confirmation (e.g., 20cm→10cm track rotation, top divergence, ice-point repair).
  5. Direct, honest mapping to A-trade's 7 active Strategy Center strategies and position-splitting bands.

Design invariants:
  - Strictly causal: only uses observations up to and including day `t`.
  - History-window invariant: rolling state is bounded to `ROLLING_WINDOW = 30` sessions
    (well inside `WARMUP_RECORDS = 100`), so 60-day and 250-day snapshots produce identical readings.
  - Zero NaN/Inf: every numeric value is finite or `None`.
  - Graceful degradation: when market coverage is insufficient, reports `"insufficient"` without
    fabricating style conclusions.
"""
from __future__ import annotations

import math
from typing import Any

VERSION = "market-style-dynamic-v2"
ROLLING_WINDOW = 30
EMA_ALPHA = 0.55

REGIME_META: dict[str, dict[str, str]] = {
    "insufficient": {
        "label": "样本不足",
        "tone": "unknown",
        "color": "#94a3b8",
        "headline": "有效样本未达全市场阈值，暂不输出动态风格",
    },
    "growth_20cm_bull": {
        "label": "20cm科技趋势主升",
        "tone": "positive",
        "color": "#2563eb",
        "headline": "增量与赚钱效应集中于科创/创业 20cm 弹性主线",
    },
    "broad_bull": {
        "label": "广度共振主升",
        "tone": "positive",
        "color": "#0f766e",
        "headline": "全市场趋势广度与量能共振扩散，主升容错率高",
    },
    "main_10cm_relay": {
        "label": "主板10cm题材抱团",
        "tone": "amber",
        "color": "#d97706",
        "headline": "资金聚焦主板 10cm 题材前排与连板接力，弹性高于大盘科技",
    },
    "rotation_chop": {
        "label": "结构轮动震荡",
        "tone": "neutral",
        "color": "#0284c7",
        "headline": "指数与广度拉锯轮动，宜精选前排控仓、忌追高跟风",
    },
    "top_divergence": {
        "label": "高位分歧预警",
        "tone": "warning",
        "color": "#ea580c",
        "headline": "表观强趋势下内部广度或高标接力已现掉队，严防高位退潮",
    },
    "ice_repair": {
        "label": "冰点修复试错",
        "tone": "teal",
        "color": "#0d9488",
        "headline": "经历连续冰点杀跌后出现首日修复反攻，适合轻仓试错新活口",
    },
    "full_defense": {
        "label": "全面退潮防守",
        "tone": "negative",
        "color": "#dc2626",
        "headline": "市场广度与赚钱效应同步塌陷，以空仓控回撤为第一铁律",
    },
}

STRATEGY_GUIDE_BY_REGIME: dict[str, dict[str, Any]] = {
    "growth_20cm_bull": {
        "matchedStrategies": [
            {"id": "wujiang_mainline_v1", "name": "乌江双轨主线核心", "fit": "首选", "note": "切换/聚焦 20cm 科创创业容量起爆轨，次日强势确认浮盈加仓"},
            {"id": "mainline_halfway_v1", "name": "主线趋势半路", "fit": "主力", "note": "贴近 MA20 放量突破启动的 20cm/趋势中军，回撤可控"},
            {"id": "vcp_contraction_v1", "name": "VCP波动收缩突破", "fit": "辅助", "note": "捕捉蓄势完毕的机构趋势主升浪首波起爆"},
        ],
        "positionPlan": {
            "totalRange": "65%–85%",
            "slots": "3–4 只",
            "maxSingle": "20%（先手 10–15%，浮盈确认可加至 25%）",
            "riskHint": "主升期核心防踏空、禁追偏离 MA20 过远的杂毛，新手仍禁止上融资杠杆",
        },
    },
    "broad_bull": {
        "matchedStrategies": [
            {"id": "wujiang_mainline_v1", "name": "乌江双轨主线核心", "fit": "首选", "note": "双轨并行，优先前排领涨与高辨识度总龙"},
            {"id": "wulong_cluster_v1", "name": "五龙聚首", "fit": "主力", "note": "多头共振放量突破在普涨扩散期赔率极佳"},
            {"id": "wyckoff_trend_v2", "name": "维科夫趋势V2", "fit": "辅助", "note": "适合波段中线跟踪吸筹完成后的主升段"},
        ],
        "positionPlan": {
            "totalRange": "70%–90%",
            "slots": "3–5 只",
            "maxSingle": "20%",
            "riskHint": "顺势持有主线核心，跌破 5/10 日线或放量滞涨再分批锁利",
        },
    },
    "main_10cm_relay": {
        "matchedStrategies": [
            {"id": "wujiang_mainline_v1", "name": "乌江双轨主线核心", "fit": "首选", "note": "切至 main_10cm 轨：聚焦主板封板核心与首阴反包"},
            {"id": "first_to_second_v1", "name": "纯主板一进二接力", "fit": "主力", "note": "希望姐姐纯主板换手 1 进 2，炸板次日 09:35 必砍"},
            {"id": "mainline_halfway_v1", "name": "主线趋势半路", "fit": "辅助", "note": "聚焦主板前排低位红盘半路，避开失血的旧 20cm 科技"},
        ],
        "positionPlan": {
            "totalRange": "45%–65%",
            "slots": "2–3 只",
            "maxSingle": "15%–20%",
            "riskHint": "远离已破位退潮的旧科技中军；纯主板题材切忌做后排跟风，不板就早盘走",
        },
    },
    "rotation_chop": {
        "matchedStrategies": [
            {"id": "strong_trend_dip_v1", "name": "强趋势回踩低吸", "fit": "首选", "note": "轮动市最忌追涨！只低吸 MA20 上方缩量止跌企稳的强趋势核心"},
            {"id": "vcp_contraction_v1", "name": "VCP波动收缩突破", "fit": "主力", "note": "严格过滤缩量窄幅蓄势标的，降低轮动磨损"},
            {"id": "mainline_halfway_v1", "name": "主线趋势半路", "fit": "辅助", "note": "仅在早盘出现明确板块合力时轻仓试错前排"},
        ],
        "positionPlan": {
            "totalRange": "35%–55%",
            "slots": "2–3 只",
            "maxSingle": "15%",
            "riskHint": "电风扇轮动期宁可少做不做，单笔止损锁死 -5% 以内，有 +5%~+8% 盈利及时兑现",
        },
    },
    "top_divergence": {
        "matchedStrategies": [
            {"id": "strong_trend_dip_v1", "name": "强趋势回踩低吸", "fit": "观察", "note": "停止追高突破！仅保留最强趋势核心回踩确认信号"},
            {"id": "wujiang_mainline_v1", "name": "乌江双轨主线核心", "fit": "控仓", "note": "仅保留逆势总龙尾仓或等待退潮后赛道切换"},
        ],
        "positionPlan": {
            "totalRange": "20%–40%（主动减仓）",
            "slots": "1–2 只",
            "maxSingle": "10%–15%",
            "riskHint": "高位分歧期高潮断板先砍 60% 以上仓位锁利润，绝不加杠杆接最后一棒",
        },
    },
    "ice_repair": {
        "matchedStrategies": [
            {"id": "wujiang_mainline_v1", "name": "乌江双轨主线核心", "fit": "首选", "note": "冰点首日试错最先逆势封板或首阴反包的唯一活口"},
            {"id": "first_to_second_v1", "name": "纯主板一进二接力", "fit": "试错", "note": "轻仓试错冰点后首批换手 1 进 2 新周期先锋"},
            {"id": "strong_trend_dip_v1", "name": "强趋势回踩低吸", "fit": "试错", "note": "捕捉抗跌主线核心的止跌企稳修复点"},
        ],
        "positionPlan": {
            "totalRange": "20%–35%（试错轻仓）",
            "slots": "1–2 只",
            "maxSingle": "10%–12%",
            "riskHint": "修复首日只下先手试错仓，绝不一把满仓；次日确认持续性再加仓，否则早盘砍",
        },
    },
    "full_defense": {
        "matchedStrategies": [
            {"id": "wujiang_mainline_v1", "name": "乌江双轨主线核心", "fit": "极轻仓/观望", "note": "默认空仓等待；仅允许极轻仓观察唯一逆势主板活口"},
        ],
        "positionPlan": {
            "totalRange": "0%–15%（空仓防守优先）",
            "slots": "0–1 只",
            "maxSingle": "≤ 10%",
            "riskHint": "全面退潮期保住本金就是跑赢 90% 的人！严禁抄底下跌中继与补亏摊平",
        },
    },
    "insufficient": {
        "matchedStrategies": [],
        "positionPlan": {
            "totalRange": "—",
            "slots": "—",
            "maxSingle": "—",
            "riskHint": "请先配置并刷新完整本地通达信日线，以解锁动态市场风格与策略匹配",
        },
    },
}


def _finite(value: Any) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _round(value: float | None, places: int = 2) -> float | None:
    if not _finite(value):
        return None
    return round(float(value), places)


def _scale(value: float | None, lo: float, hi: float, out_lo: float = 0.0, out_hi: float = 100.0) -> float | None:
    if not _finite(value) or hi == lo:
        return None
    ratio = _clamp((float(value) - lo) / (hi - lo), 0.0, 1.0)
    return out_lo + ratio * (out_hi - out_lo)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile_rank(value: float, history: list[float]) -> float | None:
    if not history:
        return None
    below_or_eq = sum(1 for item in history if item <= value)
    return round(below_or_eq / len(history) * 100.0, 1)


def _board_label(axis: float | None) -> str:
    if axis is None:
        return "单一板块样本"
    if axis >= 22.0:
        return "20cm科技主导"
    if axis >= 8.0:
        return "偏向20cm弹性"
    if axis <= -22.0:
        return "10cm主板主导"
    if axis <= -8.0:
        return "偏向10cm主板"
    return "双轨均衡"


def _structure_label(axis: float | None) -> str:
    if axis is None:
        return "结构样本不足"
    if axis >= 22.0:
        return "短线连板接力主导"
    if axis >= 8.0:
        return "偏向题材接力"
    if axis <= -22.0:
        return "大容量趋势中军主导"
    if axis <= -8.0:
        return "偏向容量趋势"
    return "趋势与接力并存"


def _liquidity_label(total_ratio: float | None, median_ratio: float | None) -> str:
    ratio = total_ratio if _finite(total_ratio) else median_ratio
    if not _finite(ratio):
        return "量能待观测"
    r = float(ratio)
    if r >= 1.25:
        return "显著放量增量"
    if r >= 1.08:
        return "温和放量活跃"
    if r >= 0.88:
        return "存量平稳换手"
    if r >= 0.72:
        return "缩量分歧观望"
    return "极度缩量失血"


def _momentum_label(velocity: float | None) -> str:
    if not _finite(velocity):
        return "初始观测"
    v = float(velocity)
    if v >= 8.0:
        return "升温加速"
    if v >= 3.0:
        return "温和改善"
    if v <= -8.0:
        return "退潮降温"
    if v <= -3.0:
        return "动摇分化"
    return "稳定延续"


class DynamicStyleTracker:
    """Stateful, bounded-window causal tracker producing `dynamic` for each timeline day."""

    def __init__(self, *, min_market_symbols: int = 1000, min_coverage: float = 0.7) -> None:
        self.min_market_symbols = int(min_market_symbols)
        self.min_coverage = float(min_coverage)
        self._offense_hist: list[float] = []
        self._offense_ema: float | None = None
        self._board_axis_hist: list[float] = []
        self._total_amount_hist: list[float] = []
        self._growth_share_hist: list[float] = []
        self._prev_regime: str | None = None
        self._regime_streak: int = 0

    def _reset_transient(self) -> None:
        self._offense_ema = None
        self._prev_regime = None
        self._regime_streak = 0

    def step(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Compute one strictly causal dynamic style observation using only `metrics` and bounded history."""
        count = int(metrics.get("sampleCount") or 0)
        expected = int(metrics.get("expectedCount") or 0)
        coverage = float(metrics.get("coverage") or 0.0)
        complete = expected >= self.min_market_symbols and coverage >= self.min_coverage and count > 0

        if not complete:
            self._reset_transient()
            meta = REGIME_META["insufficient"]
            return {
                "version": VERSION,
                "status": "insufficient",
                "regimeKey": "insufficient",
                "regimeLabel": meta["label"],
                "regimeTone": meta["tone"],
                "regimeColor": meta["color"],
                "headline": meta["headline"],
                "summary": f"有效样本 {count} / {expected}（覆盖率 {coverage * 100:.1f}%），低于全市场门槛，保留原始观测不编造动态风格。",
                "confidencePct": 0,
                "streakDays": 0,
                "offenseScore": None,
                "offenseEma": None,
                "offenseVelocity": None,
                "offensePercentile30": None,
                "momentumLabel": "样本不足",
                "boardAxis": None,
                "boardAxisDelta5": None,
                "boardLabel": "样本不足",
                "boardMetrics": {},
                "structureAxis": None,
                "structureLabel": "样本不足",
                "structureMetrics": {},
                "liquidity": {
                    "label": "样本不足",
                    "totalAmountYi": _round(metrics.get("totalAmountYi"), 1),
                    "totalAmountRatio20": None,
                    "medianAmountRatio20": _round(metrics.get("medianAmountRatio20"), 2),
                },
                "shiftAlerts": [],
                "strategyGuide": STRATEGY_GUIDE_BY_REGIME["insufficient"],
            }

        breadth = metrics.get("breadth")
        advance = metrics.get("advanceRatio")
        decline = metrics.get("declineRatio")
        ret20 = metrics.get("medianReturn20")
        ret5 = metrics.get("medianReturn5")
        daily = metrics.get("medianChangePercent")
        breadth_chg5 = metrics.get("breadthChange5")
        med_amt_r20 = metrics.get("medianAmountRatio20")
        limit_up = int(metrics.get("limitUpApprox") or 0)
        limit_down = int(metrics.get("limitDownApprox") or 0)
        multi_board = int(metrics.get("multiBoardApprox") or 0)
        max_height = int(metrics.get("maxBoardHeight") or 0)
        total_amt_yi = metrics.get("totalAmountYi")

        leadership = metrics.get("leadership") or {}
        promo_all = ((leadership.get("promotion") or {}).get("all")) or {}
        promo_rate = promo_all.get("rate") if int(promo_all.get("observed") or 0) >= 5 else None
        high_promo = leadership.get("highBoardPromotion") or ((leadership.get("promotion") or {}).get("high")) or {}
        high_obs = int(high_promo.get("observed") or 0)
        high_rate = high_promo.get("rate") if high_obs >= 5 else None
        premium = leadership.get("premium") or {}
        premium_med = premium.get("median") if int(premium.get("observed") or 0) >= 5 else None
        trend_share = (leadership.get("trend") or {}).get("amountShare")

        # Rolling 20-day total amount ratio (strictly excluding today!)
        prior_amt_window = self._total_amount_hist[-20:]
        total_amt_ratio20: float | None = None
        if _finite(total_amt_yi) and float(total_amt_yi) > 0 and len(prior_amt_window) >= 5:
            avg_prior_amt = _mean(prior_amt_window)
            if avg_prior_amt and avg_prior_amt > 0:
                total_amt_ratio20 = float(total_amt_yi) / avg_prior_amt

        # ── 1. Continuous Offense / Defense Thermometer (0–100) ──────────────────
        components: list[tuple[float, float]] = []

        p_breadth = _scale(breadth, 0.15, 0.85, 0.0, 100.0)
        if p_breadth is not None:
            components.append((0.28, p_breadth))

        m20 = _scale(ret20, -10.0, 10.0, 0.0, 100.0)
        m5 = _scale(ret5, -5.0, 5.0, 0.0, 100.0)
        if m20 is not None and m5 is not None:
            components.append((0.24, 0.58 * m20 + 0.42 * m5))
        elif m20 is not None:
            components.append((0.24, m20))

        p_adv = _scale(advance, 0.15, 0.85, 0.0, 100.0)
        if p_adv is not None:
            ld_penalty = min(35.0, max(0.0, limit_down - 3) * 0.9)
            lu_boost = min(14.0, limit_up * 0.18)
            components.append((0.20, _clamp(p_adv - ld_penalty + lu_boost, 0.0, 100.0)))

        liq_parts = [v for v in (
            _scale(med_amt_r20, 0.60, 1.50, 10.0, 95.0),
            _scale(total_amt_ratio20, 0.70, 1.40, 10.0, 95.0),
        ) if v is not None]
        if liq_parts:
            components.append((0.14, sum(liq_parts) / len(liq_parts)))

        relay_parts = [v for v in (
            _scale(trend_share, 0.15, 0.62, 10.0, 95.0),
            _scale(promo_rate, 0.12, 0.68, 10.0, 95.0),
            _scale(premium_med, -3.5, 3.5, 10.0, 95.0),
        ) if v is not None]
        if relay_parts:
            components.append((0.14, sum(relay_parts) / len(relay_parts)))

        weight_sum = sum(w for w, _ in components)
        raw_offense = sum(w * v for w, v in components) / weight_sum if weight_sum > 0 else 50.0
        offense_score = round(_clamp(raw_offense, 0.0, 100.0), 1)

        prev_offense = self._offense_hist[-1] if self._offense_hist else None
        if self._offense_ema is None:
            offense_ema = offense_score
        else:
            offense_ema = round(EMA_ALPHA * offense_score + (1.0 - EMA_ALPHA) * self._offense_ema, 1)

        ref_idx = min(5, len(self._offense_hist))
        offense_velocity = round(offense_score - self._offense_hist[-ref_idx], 1) if ref_idx >= 1 else 0.0
        recent_offense_30 = (self._offense_hist + [offense_score])[-ROLLING_WINDOW:]
        offense_pct30 = _percentile_rank(offense_score, recent_offense_30)
        recent_min_5 = min(self._offense_hist[-5:]) if self._offense_hist else None

        # ── 2. Board Track Axis (-100 主板10cm题材 ↔ +100 20cm科创创业科技) ─────
        bs = metrics.get("boardStats") or {}
        g_cnt = int(bs.get("growth20Count") or 0)
        m_cnt = int(bs.get("main10Count") or 0)
        g_r5, m_r5 = bs.get("growth20MedianRet5"), bs.get("main10MedianRet5")
        g_r20, m_r20 = bs.get("growth20MedianRet20"), bs.get("main10MedianRet20")
        g_brd, m_brd = bs.get("growth20Breadth"), bs.get("main10Breadth")
        g_share = bs.get("growth20AmountShare")

        board_axis: float | None = None
        board_axis_delta5: float | None = None
        ret5_spread: float | None = None
        ret20_spread: float | None = None
        breadth_spread_pts: float | None = None
        share_delta_pts: float | None = None

        if g_cnt >= 1 and m_cnt >= 1 and _finite(g_r5) and _finite(m_r5):
            ret5_spread = float(g_r5) - float(m_r5)
            ret20_spread = (float(g_r20) - float(m_r20)) if (_finite(g_r20) and _finite(m_r20)) else 0.0
            breadth_spread_pts = ((float(g_brd) - float(m_brd)) * 100.0) if (_finite(g_brd) and _finite(m_brd)) else 0.0
            prior_g_share = _mean(self._growth_share_hist[-20:])
            if _finite(g_share) and prior_g_share is not None:
                share_delta_pts = (float(g_share) - prior_g_share) * 100.0
            else:
                share_delta_pts = 0.0
            raw_board = ret5_spread * 8.0 + ret20_spread * 3.0 + breadth_spread_pts * 1.15 + share_delta_pts * 4.5
            board_axis = round(_clamp(raw_board, -100.0, 100.0), 1)
            if self._board_axis_hist:
                b_ref = min(5, len(self._board_axis_hist))
                board_axis_delta5 = round(board_axis - self._board_axis_hist[-b_ref], 1)
            else:
                board_axis_delta5 = 0.0

        # ── 3. Structure Axis (-100 大容量趋势中军 ↔ +100 短线连板接力) ──────────
        core_r5 = bs.get("coreTop10Ret5")
        core_r20 = bs.get("coreTop10Ret20")
        core_excess5 = (float(core_r5) - float(ret5)) if (_finite(core_r5) and _finite(ret5)) else None
        core_excess20 = (float(core_r20) - float(ret20)) if (_finite(core_r20) and _finite(ret20)) else None

        relay_pull = (
            min(40.0, max(0, max_height - 1) * 7.5)
            + min(30.0, multi_board * 1.4)
            + (_scale(promo_rate, 0.20, 0.65, -10.0, 22.0) or 0.0)
            + (_scale(premium_med, -1.5, 3.0, -8.0, 14.0) or 0.0)
        )
        core_pull = (
            (_scale(trend_share, 0.20, 0.65, 5.0, 52.0) or 20.0)
            + (_clamp((core_excess5 or 0.0) * 7.5, -20.0, 30.0))
            + (_clamp((core_excess20 or 0.0) * 2.8, -15.0, 22.0))
        )
        structure_axis = round(_clamp(relay_pull - core_pull, -100.0, 100.0), 1)

        # ── 4. Early-Warning Style Shift & Divergence Alerts ─────────────────────
        shift_alerts: list[dict[str, str]] = []

        prior_board_avg5 = _mean(self._board_axis_hist[-5:]) if len(self._board_axis_hist) >= 3 else None
        if board_axis is not None and board_axis <= -12.0 and (
            (board_axis_delta5 is not None and board_axis_delta5 <= -15.0)
            or (prior_board_avg5 is not None and prior_board_avg5 >= 0.0)
        ):
            shift_alerts.append({
                "id": "track_shift_to_10cm",
                "severity": "warning",
                "title": "赛道切换预警：20cm科技降温 → 资金切向主板10cm题材",
                "detail": (
                    f"20cm vs 10cm 赛道轴已降至 {board_axis:+.1f}（5日变化 {board_axis_delta5 or 0:+.1f}），"
                    f"主板 5 日中位领先 {abs(ret5_spread or 0):.2f}%。类似乌江 2026-08/10 切换拐点，需停追弱势旧科技。"
                ),
                "actionHint": "将乌江策略切至主板轨或启用纯主板一进二，严控旧科技抄底冲动。",
            })
        elif board_axis is not None and board_axis >= 12.0 and (
            (board_axis_delta5 is not None and board_axis_delta5 >= 15.0)
            or (prior_board_avg5 is not None and prior_board_avg5 <= 0.0)
        ):
            shift_alerts.append({
                "id": "track_shift_to_20cm",
                "severity": "positive",
                "title": "赛道切换预警：资金回流 20cm 科创/创业科技主线",
                "detail": (
                    f"20cm vs 10cm 赛道轴升至 {board_axis:+.1f}（5日变化 {board_axis_delta5 or 0:+.1f}），"
                    f"20cm 板块 5 日中位领先 +{ret5_spread or 0:.2f}%。"
                ),
                "actionHint": "可切回乌江 20cm 容量轨与主线趋势半路，聚焦放量突破 MA20 的科技核心。",
            })

        high_div_flag = (
            offense_ema >= 50.0
            and (
                (_finite(breadth_chg5) and float(breadth_chg5) <= -6.0)
                or (_finite(med_amt_r20) and float(med_amt_r20) >= 1.18 and _finite(daily) and float(daily) <= -0.5)
                or (high_rate is not None and high_rate < 0.25 and limit_down >= 8)
                or offense_velocity <= -10.0
            )
        )
        if high_div_flag:
            shift_alerts.append({
                "id": "top_divergence_warn",
                "severity": "danger",
                "title": "高位顶背离预警：表观仍强但内部广度/高标开始掉队",
                "detail": (
                    f"进攻温度均值 {offense_ema:.1f}，但 5 日变化 {offense_velocity:+.1f}"
                    + (f"、同样本 5 日广度收缩 {float(breadth_chg5):+.1f} 个百分点" if _finite(breadth_chg5) else "")
                    + (f"、当日放量收跌（中位 {float(daily):+.2f}%）" if (_finite(daily) and float(daily) <= -0.5) else "")
                    + "。"
                ),
                "actionHint": "高潮断板或滞涨标的先兑现 60% 以上仓位锁利润，禁止加仓追高。",
            })

        ice_repair_flag = (
            recent_min_5 is not None
            and recent_min_5 < 32.0
            and prev_offense is not None
            and (
                (offense_score - prev_offense >= 8.0 and _finite(advance) and float(advance) >= 0.60)
                or (offense_velocity >= 11.0 and offense_score >= 38.0)
            )
        )
        if ice_repair_flag:
            shift_alerts.append({
                "id": "ice_point_window",
                "severity": "positive",
                "title": "冰点修复试错窗：恐慌释放后的首日情绪反攻",
                "detail": (
                    f"近 5 日温度一度跌至 {recent_min_5:.1f} 冰点，今日快速修复至 {offense_score:.1f}"
                    f"（单日 {offense_score - prev_offense:+.1f}，上涨占比 {(advance or 0) * 100:.1f}%）。"
                ),
                "actionHint": "可用 10%–15% 先手试错逆势活口或一进二先锋，次日确认延续再加仓。",
            })

        if limit_down >= max(15, limit_up * 2) or (high_rate is not None and high_rate < 0.20 and limit_down >= 10):
            shift_alerts.append({
                "id": "relay_freeze_warn",
                "severity": "danger",
                "title": "核按钮退潮警报：跌停扩散或高标接力大面积断板",
                "detail": f"近似跌停 {limit_down} 只 vs 涨停 {limit_up} 只" + (f"，高标晋级率仅 {high_rate * 100:.1f}%" if high_rate is not None else "") + "。",
                "actionHint": "严格执行早盘 09:45 前砍弱止损铁律，总仓位压至 15% 以内或空仓。",
            })

        if (
            offense_score >= 58.0
            and _finite(total_amt_ratio20) and float(total_amt_ratio20) >= 1.15
            and _finite(breadth_chg5) and float(breadth_chg5) >= 4.0
            and not high_div_flag
        ):
            shift_alerts.append({
                "id": "volume_resonance",
                "severity": "positive",
                "title": "量价广度共振：增量资金进场推动趋势扩散",
                "detail": f"观察总成交额达 20 日均额的 {float(total_amt_ratio20):.2f} 倍，5 日广度扩张 +{float(breadth_chg5):.1f} 个百分点。",
                "actionHint": "可将总仓位提至 70%–85%，优先运行乌江主线核心与五龙聚首。",
            })

        # ── 5. 7-State Smooth Dynamic Regime Classification ──────────────────────
        strong_10cm_relay = (
            offense_ema >= 42.0
            and ((board_axis is not None and board_axis <= -15.0) or structure_axis >= 22.0)
            and (max_height >= 3 or multi_board >= 10)
        )
        if ice_repair_flag and offense_ema < 52.0:
            regime_key = "ice_repair"
            margin = max(0.0, (offense_score - (prev_offense or offense_score)) - 6.0)
        elif (
            offense_ema < 35.0
            or limit_down >= max(20, limit_up * 2)
            or (_finite(breadth) and float(breadth) < 0.28 and _finite(ret5) and float(ret5) < -2.0)
        ):
            regime_key = "full_defense"
            margin = max(0.0, 38.0 - offense_ema)
        elif strong_10cm_relay and limit_down < 10 and (board_axis is not None and board_axis <= -15.0 and max_height >= 4 and multi_board >= 15):
            regime_key = "main_10cm_relay"
            margin = max(abs(board_axis) - 10.0, structure_axis - 15.0)
        elif high_div_flag:
            regime_key = "top_divergence"
            margin = max(0.0, abs(offense_velocity) - 4.0)
        elif offense_ema >= 55.0 and board_axis is not None and board_axis >= 15.0 and structure_axis <= 22.0:
            regime_key = "growth_20cm_bull"
            margin = min(offense_ema - 52.0, board_axis - 10.0)
        elif strong_10cm_relay:
            regime_key = "main_10cm_relay"
            margin = max(abs(board_axis or 0.0) - 10.0, structure_axis - 15.0)
        elif offense_ema >= 58.0 and _finite(breadth) and float(breadth) >= 0.55:
            regime_key = "broad_bull"
            margin = min(offense_ema - 54.0, (float(breadth) - 0.50) * 100.0)
        else:
            regime_key = "rotation_chop"
            margin = max(0.0, 14.0 - abs(offense_ema - 48.0))

        confidence_pct = int(round(_clamp(54.0 + margin * 1.4 + (8.0 if regime_key == self._prev_regime else 0.0), 48.0, 96.0)))
        self._regime_streak = (self._regime_streak + 1) if regime_key == self._prev_regime else 1
        self._prev_regime = regime_key

        # Update bounded causal history AFTER computing today's ratios
        self._offense_hist.append(offense_score)
        if len(self._offense_hist) > ROLLING_WINDOW:
            self._offense_hist = self._offense_hist[-ROLLING_WINDOW:]
        self._offense_ema = offense_ema

        if board_axis is not None:
            self._board_axis_hist.append(board_axis)
            if len(self._board_axis_hist) > ROLLING_WINDOW:
                self._board_axis_hist = self._board_axis_hist[-ROLLING_WINDOW:]
        if _finite(total_amt_yi) and float(total_amt_yi) > 0:
            self._total_amount_hist.append(float(total_amt_yi))
            if len(self._total_amount_hist) > ROLLING_WINDOW:
                self._total_amount_hist = self._total_amount_hist[-ROLLING_WINDOW:]
        if _finite(g_share):
            self._growth_share_hist.append(float(g_share))
            if len(self._growth_share_hist) > ROLLING_WINDOW:
                self._growth_share_hist = self._growth_share_hist[-ROLLING_WINDOW:]

        meta = REGIME_META[regime_key]
        board_lbl = _board_label(board_axis)
        struct_lbl = _structure_label(structure_axis)
        liq_lbl = _liquidity_label(total_amt_ratio20, med_amt_r20)
        mom_lbl = _momentum_label(offense_velocity)

        summary_parts = [
            f"进攻温度 {offense_score:.1f}/100（3日平滑 {offense_ema:.1f}，近30日第 {offense_pct30 or 50:.0f} 分位，{mom_lbl}）",
            f"赛道倾向：{board_lbl}",
            f"结构倾向：{struct_lbl}",
            f"量能气候：{liq_lbl}",
        ]
        if shift_alerts:
            summary_parts.append(f"触发 {len(shift_alerts)} 项风格切换/风险预警")

        return {
            "version": VERSION,
            "status": "observed",
            "regimeKey": regime_key,
            "regimeLabel": meta["label"],
            "regimeTone": meta["tone"],
            "regimeColor": meta["color"],
            "headline": meta["headline"],
            "summary": "；".join(summary_parts) + "。",
            "confidencePct": confidence_pct,
            "streakDays": self._regime_streak,
            "offenseScore": offense_score,
            "offenseEma": offense_ema,
            "offenseVelocity": offense_velocity,
            "offensePercentile30": offense_pct30,
            "momentumLabel": mom_lbl,
            "boardAxis": board_axis,
            "boardAxisDelta5": board_axis_delta5,
            "boardLabel": board_lbl,
            "boardMetrics": {
                "growth20Count": g_cnt,
                "main10Count": m_cnt,
                "growth20Breadth": _round(g_brd, 4),
                "main10Breadth": _round(m_brd, 4),
                "growth20MedianRet5": _round(g_r5, 2),
                "main10MedianRet5": _round(m_r5, 2),
                "growth20MedianRet20": _round(g_r20, 2),
                "main10MedianRet20": _round(m_r20, 2),
                "growth20AmountShare": _round(g_share, 4),
                "ret5Spread": _round(ret5_spread, 2),
                "ret20Spread": _round(ret20_spread, 2),
            },
            "structureAxis": structure_axis,
            "structureLabel": struct_lbl,
            "structureMetrics": {
                "coreTop10Ret5": _round(core_r5, 2),
                "coreTop10Ret20": _round(core_r20, 2),
                "coreExcess5": _round(core_excess5, 2),
                "coreExcess20": _round(core_excess20, 2),
                "coreTop10AmountShare": _round(bs.get("coreTop10AmountShare"), 4),
                "maxBoardHeight": max_height,
                "multiBoardApprox": multi_board,
                "promotionRate": _round(promo_rate, 4),
            },
            "liquidity": {
                "label": liq_lbl,
                "totalAmountYi": _round(total_amt_yi, 1),
                "totalAmountRatio20": _round(total_amt_ratio20, 2),
                "medianAmountRatio20": _round(med_amt_r20, 2),
            },
            "shiftAlerts": shift_alerts,
            "strategyGuide": STRATEGY_GUIDE_BY_REGIME[regime_key],
        }


def compute_dynamic_timeline(
    metric_rows: list[dict[str, Any]],
    *,
    min_market_symbols: int = 1000,
    min_coverage: float = 0.7,
) -> list[dict[str, Any]]:
    tracker = DynamicStyleTracker(min_market_symbols=min_market_symbols, min_coverage=min_coverage)
    return [tracker.step(row) for row in metric_rows]
