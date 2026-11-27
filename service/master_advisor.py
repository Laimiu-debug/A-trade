"""Master-Style Daily Action Advisor for A-trade (T11).

Combines:
  1. The selected master's formalized playbook rules (exact position ceiling,
     board/theme preference, error-correction stop-loss timing, scaling/trimming
     triggers, and drawdown red lines)
  2. Target trading day's real market environment (local Tongdaxin FIRST,
     automatic fallback to bundled 2022-2026 real index/stock K-line cache)
  3. The user's real account holdings from AccountService (or beginner-friendly
     simulated holdings entered in T11)

Outputs concrete, plain-Chinese, beginner-actionable guidance (strictly educational
pattern simulation, not investment advice) + today's top style-matched teaching specimens.
"""
from __future__ import annotations

import math
from pathlib import Path
from statistics import mean
from typing import Any

from service import tdx
from service.master_rules import (
    board_of,
    describe_active_rules,
    plan_next_day_orders,
    strategy_params,
)
from service.master_fallback_market import (
    build_fallback_market_env,
    get_fallback_all_stock_symbols,
    get_fallback_names,
    get_fallback_stock_bars,
)


STRATEGY_ID_TO_STYLE_KEY: dict[str, str] = {
    "wujiang_mainline_v1": "wujiang_dynamic",
    "mainline_halfway_v1": "huichanglan_mainline",
    "first_to_second_v1": "xiwangjiejie_1to2",
    "highboard_sniper_v1": "dongshizhang_highboard",
    "ruhan_halfway_v1": "ruhan_halfway",
    "strong_trend_dip_v1": "songsong_defensive",
}

STYLE_KEY_TO_STRATEGY_ID: dict[str, str] = {v: k for k, v in STRATEGY_ID_TO_STYLE_KEY.items()}


def detect_style_key(master: dict | None, override_key: str = "") -> str:
    if override_key in STYLE_PROFILES:
        return override_key
    if override_key in STRATEGY_ID_TO_STYLE_KEY:
        return STRATEGY_ID_TO_STYLE_KEY[override_key]
    name = (master or {}).get("name") or ""
    tag = (master or {}).get("styleTag") or ""
    text = f"{name} {tag}"
    if "乌江" in text:
        return "wujiang_dynamic"
    if "灰常懒" in text:
        return "huichanglan_mainline"
    if "希望" in text or "一进二" in text:
        return "xiwangjiejie_1to2"
    if "董事长" in text or "隔夜排单" in text:
        return "dongshizhang_highboard"
    if "如晗" in text or "鹰舞" in text:
        return "ruhan_halfway"
    if "松松" in text or "可转债" in text:
        return "songsong_defensive"
    return "wujiang_dynamic"


STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "wujiang_dynamic": {
        "key": "wujiang_dynamic",
        "name": "乌江望月 · 动态主线切换与浮盈加仓模式",
        "tagline": "哪里赚钱去哪里 · 试错轻仓/确认暴力浮盈加仓 · 次日10:30前极速纠错",
        "buyWindow": "强市 09:26–10:30 早盘抢主线；弱市 14:01–15:00 尾盘确认抗跌拿先手",
        "sellWindow": "亏损单 09:15–09:45 秒砍（最迟 10:30，历史 88.3% 亏损在此结束）；盈利单冲高分批兑现",
        "timingReason": "早盘前 15–60 分钟流动性最足、承接强弱一目了然。乌江 179 笔亏损单中 93.9% 在次日砍掉，割后 3 日个股平均续跌 -3.06%，早砍才能腾出资金换到当天真龙。",
        "stopLossPct": -5.0,
        "addOnWinMinPct": 5.0,
        "takeProfitTrimPct": 8.0,
        "maxHoldLoseDays": 1,
        "regimeRules": {
            "advance": {"totalPos": "70%–90%（新手禁融资）", "maxSingle": 20, "maxSlots": 4, "board": "优先 20cm 科创/创业硬科技趋势核心与次新容量龙（若科创50>MA20且成交>2万亿）"},
            "trial": {"totalPos": "50%–70%", "maxSingle": 15, "maxSlots": 4, "board": "20cm 科技活口 + 主板 10cm 新启动主线龙头双线试错"},
            "chop": {"totalPos": "40%–60%", "maxSingle": 15, "maxSlots": 3, "board": "缩量震荡放弃笨重中军，聚焦主板 10cm 主线前排（首板/一进二/强势半路）"},
            "decline": {"totalPos": "10%–35%（主动降频缩仓）", "maxSingle": 10, "maxSlots": 2, "board": "彻底清零已退潮旧科技！仅允许极轻仓试错逆势主板连板总龙（尽量尾盘确认抗跌再买）"},
        },
        "redLines": [
            "单笔亏损达 -5% 或次日 10:30 仍水下无力：无条件认错清仓，严禁浮亏补仓摊平。",
            "买入当日未封板且次日低开超 -3%：竞价或 09:35 前直接砍仓，不幻想午后反包。",
            "科创50 跌破 MA20 且两市缩量至 1.8 万亿以下：彻底停止抄底旧科技，迅速切向主板题材或空仓。",
            "若某单日亏损超总资金 2%：当天砍仓后立即停止一切新开仓，次日将单票上限减半。",
        ],
    },
    "huichanglan_mainline": {
        "key": "huichanglan_mainline",
        "name": "灰常懒 · 绝对主线半路与80%早盘15分秒砍模式",
        "tagline": "机会留给有子弹的人 · 死守1-2条绝对主线 · 零融资高盈亏比",
        "buyWindow": "09:45–10:30（占比 38.8%）盯绝对主线红盘 +2%~+5% 强势半路或强势股水下低吸",
        "sellWindow": "09:26–09:45 开盘前 15 分钟果断砍弱留强（历史 80.3% 卖出在此完成）",
        "timingReason": "早盘前 15 分钟迅速清理不及预期持仓、回笼现金子弹，紧接着 09:45–10:30 就能把子弹精准打向当天市场合力最强的新主线前排。",
        "stopLossPct": -4.8,
        "addOnWinMinPct": 7.0,
        "takeProfitTrimPct": 9.0,
        "maxHoldLoseDays": 1,
        "regimeRules": {
            "advance": {"totalPos": "60%–80%", "maxSingle": 20, "maxSlots": 4, "board": "死守当下 1–2 条绝对主线前排核心（主板为主 75% + 20cm 弹性先锋 25%）"},
            "trial": {"totalPos": "40%–60%", "maxSingle": 15, "maxSlots": 3, "board": "轻仓试水新主线红盘低位半路（+1.5%~+4.5%，历史胜率 72.2%）"},
            "chop": {"totalPos": "40%–60%", "maxSingle": 15, "maxSlots": 3, "board": "聚焦高辨识度主线核心反复做，避开 +7% 以上高位半路追涨（磨损率高）"},
            "decline": {"totalPos": "15%–40%", "maxSingle": 12, "maxSlots": 2, "board": "绝对禁止抄底退潮旧科技！只做逆势唯一主线核心或空仓留子弹等情绪高潮后冰点"},
        },
        "redLines": [
            "老手死在抄底上：严禁去抄已退潮旧主线的反抽（灰常懒 73 届前四大亏损全栽在科技反抽）。",
            "次日 09:45 前持仓翻不了红或走弱：宁可卖飞也要秒砍，把亏损严格压在 -5% 以内。",
            "只对当日封死涨停的票给 2–3 天格局空间（封板胜率 72.2% vs 未封板 32.4%）。",
        ],
    },
    "xiwangjiejie_1to2": {
        "key": "xiwangjiejie_1to2",
        "name": "希望姐姐 · 纯主板一进二「克制与重复」模式",
        "tagline": "10届9红 · 97.7%纯主板 · 极简等权一进二 · 炸板次日09:35必砍",
        "buyWindow": "09:15–10:00（81.1% 在 10:30 前完成）只做早盘强势封死的主板一进二",
        "sellWindow": "09:15–09:35（15 笔亏损单 100% 在开盘 5 分钟内砍光，89.8% 卖出在 09:45 前）",
        "timingReason": "一进二讲究封板质量与隔日溢价。早盘 10:00 前封死的一进二辨识度最高；一旦昨日炸板（历史 8 战 8 败，均亏 -11.3%），次日竞价或开盘 5 分钟必须机械核出。",
        "stopLossPct": -6.0,
        "addOnWinMinPct": 9.5,
        "takeProfitTrimPct": 10.0,
        "maxHoldLoseDays": 1,
        "regimeRules": {
            "advance": {"totalPos": "60%–80%", "maxSingle": 25, "maxSlots": 3, "board": "100% 纯主板（60/00）1 进 2 打板，严禁跨界 20cm/30cm"},
            "trial": {"totalPos": "50%–70%", "maxSingle": 25, "maxSlots": 3, "board": "纯主板 1 进 2 前排，等权仓位不赌单吊"},
            "chop": {"totalPos": "50%–75%", "maxSingle": 25, "maxSlots": 3, "board": "震荡分化期是一进二印钞机（历史 6 战 6 胜，T+1 均赚 +9.15%），聚焦早盘封板最强首领"},
            "decline": {"totalPos": "0%–30%（指数大阴线直接减半或空仓）", "maxSingle": 15, "maxSlots": 2, "board": "指数阴线日一进二胜率骤降至 31.2%！仅在指数企稳翻红时做唯一最强主板 1 进 2"},
        },
        "redLines": [
            "克制排在重复前面：绝不做模式外的抄底、首板或 20cm 追高，没有达标一进二就空仓。",
            "严格等权下单（每笔固定 20%–25%），绝不因连胜上头放大单票仓位。",
            "昨日炸板或次日低开不及预期：09:15–09:35 无条件全清，零幻想。",
        ],
    },
    "dongshizhang_highboard": {
        "key": "dongshizhang_highboard",
        "name": "暴涨基金董事长 · 空仓狙击 2板+ 主板高标连板模式",
        "tagline": "66%交易日空仓等待 · 只做2板以上主板真龙 · 隔夜排单64%期权式撤单",
        "buyWindow": "晚间盘后手写复盘选唯一目标隔夜排单占位 → 次日 09:15–09:45（90.8% 买入在此）竞价校验留单/撤单",
        "sellWindow": "亏损次日 09:35 前秒砍；盈利若继续封板锁仓，盘中断板/炸板分批兑现",
        "timingReason": "大盘暴跌或科技退潮时，全市场残存活水会极端抱团到唯一 3 板+ 主板最高标。隔夜排单买的是免费时间优先权，次日竞价不及预期立刻撤单（历史撤单率 64.3%）。",
        "stopLossPct": -6.0,
        "addOnWinMinPct": 9.5,
        "takeProfitTrimPct": 12.0,
        "maxHoldLoseDays": 1,
        "regimeRules": {
            "advance": {"totalPos": "30%–50%（新手改造版，禁满仓单吊）", "maxSingle": 30, "maxSlots": 2, "board": "主板 2 进 3 / 3 板+ 情绪最高标，绝对不做首板"},
            "trial": {"totalPos": "25%–50%", "maxSingle": 25, "maxSlots": 2, "board": "等待周期新龙走出 2–3 板确认后再切入，前排未明朗前空仓"},
            "chop": {"totalPos": "30%–50%", "maxSingle": 30, "maxSlots": 2, "board": "100% 纯主板 2 板+ 辨识度真龙（历史震荡期胜率 100%）"},
            "decline": {"totalPos": "25%–40%（66%日子空仓，只等逆势独苗）", "maxSingle": 25, "maxSlots": 1, "board": "科技大票暴跌时紧盯逆势活口最高标（3板+），不达标宁可连空一周"},
        },
        "redLines": [
            "新手绝不可直接 100% 满仓单吊高标（必须把单票压在 25%–30% 防一字核按钮）。",
            "盘后没写进计划的股票，盘中拉升再凶也绝不临时追进。",
            "高标一旦断板低开，次日 09:35 前立刻砍仓，绝不赌地天板。",
        ],
    },
    "ruhan_halfway": {
        "key": "ruhan_halfway",
        "name": "如晗 · 分仓半路推土机与弱市66%自动降仓模式",
        "tagline": "最适合普通人复制 · 分仓4-5只半路(+3%~+6%) · 弱市自动砍掉66%仓位",
        "buyWindow": "09:45–11:15（红盘 +3%~+6% 强势半路占 75.9%；弱市关闭集合竞价）",
        "sellWindow": "次日上午 09:45–11:30 不能继续涨停或走弱即兑现；封板股可拿 2–4 天分批止盈",
        "timingReason": "分散在 4–5 只站上 MA20 的启动半路股上，既避免单票黑天鹅，又比追板少吃炸板回撤；弱势退潮期主动关掉三分之二仓位，把弱市胜率反拉至 58.3%。",
        "stopLossPct": -5.0,
        "addOnWinMinPct": 6.0,
        "takeProfitTrimPct": 8.0,
        "maxHoldLoseDays": 1,
        "regimeRules": {
            "advance": {"totalPos": "75%–90%", "maxSingle": 20, "maxSlots": 5, "board": "主板+创业板主线启动首波（0板半路 +3%~+6%），全仓分散 4–5 只"},
            "trial": {"totalPos": "60%–75%", "maxSingle": 18, "maxSlots": 4, "board": "昨日涨停池+5%异动池筛选 20 只股票池，做早盘承接最强的半路"},
            "chop": {"totalPos": "65%–80%", "maxSingle": 18, "maxSlots": 5, "board": "推土机黄金期（历史 100% 参与、51.5% 胜率），均匀分仓做低位启动半路"},
            "decline": {"totalPos": "15%–30%（弱市开关触发：砍掉 66% 仓位！）", "maxSingle": 10, "maxSlots": 2, "board": "关闭集合竞价买点！一半以上日子空仓，仅挑极少数逆势真龙极轻仓试水"},
        },
        "redLines": [
            "中证1000 < MA20 且 20 日跌超 -3%：立刻将总仓位压到 30% 以内，关掉竞价模式。",
            "严禁水下抄底冷门股（如晗水下低吸样本全亏 -7.1%），只买红盘强势启动。",
            "单票首次建仓不超过 20%，只有封板浮盈次日才允许顺势加仓（历史 8 战 8 胜）。",
        ],
    },
    "songsong_defensive": {
        "key": "songsong_defensive",
        "name": "松雅湖的松松 · 强趋势低吸与弱市93%断电防守模式",
        "tagline": "期货级紧止损 · 只低吸MA20上方强趋势 · 弱市直接90%断电空仓",
        "buyWindow": "10:00–11:30 & 13:30–14:45 强趋势核心股分歧回踩平盘/水下 -1%~-4% 低吸",
        "sellWindow": "亏损触发 -2.5%~-3.5% 当即/次日早盘无条件止损；次日冲高 +3%~+6% 落袋",
        "timingReason": "不追高就不易吃大面；正股只挑站上 MA20 且前期涨幅靠前的强趋势核心，在盘中分歧低吸（历史平盘承接次日均赚 +2.39%），一旦判断错误立刻以极小伤止损。",
        "stopLossPct": -3.0,
        "addOnWinMinPct": 5.0,
        "takeProfitTrimPct": 5.0,
        "maxHoldLoseDays": 1,
        "regimeRules": {
            "advance": {"totalPos": "65%–85%", "maxSingle": 18, "maxSlots": 5, "board": "MA20 上方强趋势人气龙头回踩低吸 + 高弹性可转债日内波段"},
            "trial": {"totalPos": "50%–65%", "maxSingle": 15, "maxSlots": 4, "board": "强趋势核心平盘附近承接，不追高位加速"},
            "chop": {"totalPos": "40%–55%", "maxSingle": 15, "maxSlots": 4, "board": "严控回撤，快进快出积小胜为大胜，盈利 +4%~+6% 即可落袋"},
            "decline": {"totalPos": "0%–15%（弱市断电开关：砍掉 90% 仓位！）", "maxSingle": 10, "maxSlots": 1, "board": "直接空仓休息等待（松松 50 届弱市砍仓 93%，68 届空仓 10 天夺三冠王）"},
        },
        "redLines": [
            "单笔亏损绝对不允许放任超过 -3.5%（期货级铁律：小伤止损是复利之母）。",
            "绝不追涨已拉升 +7% 以上的日内高点，宁可踏空绝不接最后一棒。",
            "弱势退潮期强制断电空仓，把精力放在复盘等待下一波主升。",
        ],
    },
}


POSITION_SPLITTING_BENCHMARKS: list[dict[str, Any]] = [
    {
        "styleKey": "dongshizhang_highboard",
        "name": "暴涨基金董事长",
        "pattern": "极端空仓 + 单吊狙击",
        "avgHoldings": 1.00,
        "medianMax": "1 / 1 只",
        "singleDayPct": 100.0,
        "emptyDayPct": 65.6,
        "top1SharePct": 100.0,
        "summary": "65.6% 日子空仓等待，唯一出手的 1 只必是主板 2板+ 最高标（新手务必降单票至 ≤30%）。",
    },
    {
        "styleKey": "xiwangjiejie_1to2",
        "name": "希望姐姐",
        "pattern": "极简等权 2–3 仓",
        "avgHoldings": 1.58,
        "medianMax": "2 / 3 只",
        "singleDayPct": 45.5,
        "emptyDayPct": 21.2,
        "top1SharePct": 69.2,
        "summary": "首买中位 227 万（约 20%–25%/份），严格等权绝不赌单吊，没有达标一进二就空仓。",
    },
    {
        "styleKey": "huichanglan_mainline",
        "name": "灰常懒",
        "pattern": "精悍 2–3 仓",
        "avgHoldings": 2.16,
        "medianMax": "2 / 3 只",
        "singleDayPct": 25.0,
        "emptyDayPct": 17.6,
        "top1SharePct": 59.2,
        "summary": "75% 持仓日维持 2–3 只绝对主线核心，零融资，早盘 15 分钟砍弱腾子弹。",
    },
    {
        "styleKey": "wujiang_dynamic",
        "name": "乌江望月",
        "pattern": "动态 3–5 仓金字塔",
        "avgHoldings": 3.80,
        "medianMax": "3 / 9 只",
        "singleDayPct": 8.0,
        "emptyDayPct": 7.4,
        "top1SharePct": 55.7,
        "summary": "83.2% 日子持 2–6 只（71届3.49→72届4.77→73届收缩至2.91只）；先手 10–15% 试错，次日浮盈加仓长出 55.7% 第一重仓！",
    },
    {
        "styleKey": "songsong_defensive",
        "name": "松雅湖的松松",
        "pattern": "日内分散 + 3–4 只隔夜",
        "avgHoldings": 3.73,
        "medianMax": "4 / 8 只",
        "singleDayPct": 13.0,
        "emptyDayPct": 10.0,
        "top1SharePct": 48.2,
        "summary": "隔夜中位 4 只分散控风险，弱市直接断电砍仓 90%+，绝不重仓死扛。",
    },
    {
        "styleKey": "ruhan_halfway",
        "name": "如晗",
        "pattern": "均权 4–6 仓推土机",
        "avgHoldings": 4.50,
        "medianMax": "5 / 8 只",
        "singleDayPct": 3.1,
        "emptyDayPct": 21.1,
        "top1SharePct": 35.4,
        "summary": "最均衡抗黑天鹅！单吊仅 3.1%，第一重仓仅 35.4%，普通人最易复制的稳健分仓。",
    },
]


def _build_position_splitting_guide(style_key: str, total_equity: float) -> dict[str, Any]:
    active_bench = next((b for b in POSITION_SPLITTING_BENCHMARKS if b["styleKey"] == style_key), POSITION_SPLITTING_BENCHMARKS[3])
    tiers = [
        {
            "tierKey": "small",
            "label": "5 万以内（小资金起步）",
            "active": total_equity <= 50000,
            "maxSlots": "2 仓（强市最多 3 仓）",
            "firstEntryPct": "25%–30%",
            "addOnWinPct": "+15%–20%（仅封板总龙）",
            "singleCapPct": "≤ 50%",
            "template": "灰常懒 / 希望姐姐 精悍 2 仓",
        },
        {
            "tierKey": "medium",
            "label": "5 万 – 30 万（主流散户）",
            "active": 50000 < total_equity <= 300000,
            "maxSlots": "3 仓（主升最多 4 仓）",
            "firstEntryPct": "15%–20%",
            "addOnWinPct": "+15%（次日确认强势）",
            "singleCapPct": "≤ 35%",
            "template": "乌江动态金字塔 / 灰常懒 3 仓",
        },
        {
            "tierKey": "large",
            "label": "30 万以上（中大资金）",
            "active": total_equity > 300000,
            "maxSlots": "4–5 仓",
            "firstEntryPct": "12%–15%",
            "addOnWinPct": "+10%–15%",
            "singleCapPct": "≤ 30%",
            "template": "如晗 4–5 仓推土机 / 乌江金字塔",
        },
    ]
    matched_tier = next((t for t in tiers if t["active"]), tiers[1])
    return {
        "activeMaster": active_bench,
        "matchedTier": matched_tier,
        "capitalTiers": tiers,
        "allBenchmarks": POSITION_SPLITTING_BENCHMARKS,
        "coreRule": "绝不第一天满仓梭哈！先手轻仓试错分摊风险，次日 10:30 前砍弱留强，只把盈利仓加到真龙上。",
    }


def _tdx_root(owner) -> Path | None:
    chosen = owner._get("path") if hasattr(owner, "_get") else None
    if not chosen:
        return None
    try:
        return tdx.normalize_directory(chosen)
    except Exception:
        return None


def _load_bars_prefer_tdx(root: Path | None, symbol: str) -> tuple[list[dict], str]:
    if root is not None:
        try:
            p = tdx.daily_path(root, symbol)
            if p.is_file():
                bars = tdx.parse_daily(tdx.read_stable(p))
                if bars:
                    # Enrich pct
                    out = []
                    prev_c = None
                    for b in bars:
                        pct = ((b["close"] / prev_c - 1.0) * 100.0) if prev_c and prev_c > 0 else 0.0
                        out.append({**b, "pct": round(pct, 2)})
                        prev_c = b["close"]
                    return out, "tdx"
        except Exception:
            pass
    fb = get_fallback_stock_bars(symbol)
    return fb, ("fallback" if fb else "none")


def _get_market_snapshot(owner, root: Path | None, target_date: str | None) -> tuple[dict[str, Any], str]:
    """Get market regime snapshot for target_date (or latest available date)."""
    # Try local TDX index bars first if configured
    if root is not None:
        try:
            sh_p = tdx.daily_path(root, "sh000001")
            if sh_p.is_file():
                sh = tdx.parse_daily(tdx.read_stable(sh_p))
                if sh:
                    sz = tdx.parse_daily(tdx.read_stable(tdx.daily_path(root, "sz399001"))) if tdx.daily_path(root, "sz399001").is_file() else []
                    cyb = tdx.parse_daily(tdx.read_stable(tdx.daily_path(root, "sz399006"))) if tdx.daily_path(root, "sz399006").is_file() else []
                    zz = tdx.parse_daily(tdx.read_stable(tdx.daily_path(root, "sh000852"))) if tdx.daily_path(root, "sh000852").is_file() else []
                    kc = tdx.parse_daily(tdx.read_stable(tdx.daily_path(root, "sh000688"))) if tdx.daily_path(root, "sh000688").is_file() else []

                    sh_dates = [b["date"] for b in sh]
                    chosen_d = target_date if (target_date and target_date in sh_dates) else sh_dates[-1]
                    if target_date and target_date not in sh_dates:
                        earlier = [d for d in sh_dates if d <= target_date]
                        chosen_d = earlier[-1] if earlier else sh_dates[-1]

                    def idx_info(bars_l: list[dict]):
                        pos = next((i for i, b in enumerate(bars_l) if b["date"] == chosen_d), None)
                        if pos is None:
                            return None, None, None, None
                        b = bars_l[pos]
                        prev = bars_l[pos - 1]["close"] if pos > 0 else b["open"]
                        pct = round((b["close"] / prev - 1.0) * 100.0, 2) if prev > 0 else 0.0
                        ma20 = mean(x["close"] for x in bars_l[max(0, pos - 19):pos + 1])
                        r20 = round((b["close"] / bars_l[pos - 20]["close"] - 1.0) * 100.0, 2) if pos >= 20 else 0.0
                        r5 = round((b["close"] / bars_l[pos - 5]["close"] - 1.0) * 100.0, 2) if pos >= 5 else 0.0
                        return pct, b["close"] >= ma20, r20, r5

                    sh_pct, sh_abv, sh_r20, sh_r5 = idx_info(sh)
                    cyb_pct, cyb_abv, cyb_r20, _ = idx_info(cyb)
                    zz_pct, zz_abv, zz_r20, zz_r5 = idx_info(zz)
                    kc_pct, kc_abv, kc_r20, _ = idx_info(kc)
                    sh_b = next(b for b in sh if b["date"] == chosen_d)
                    sz_b = next((b for b in sz if b["date"] == chosen_d), None)
                    to_yi = round((sh_b["amount"] + (sz_b["amount"] if sz_b else 0.0)) / 1e8, 1)

                    r20 = zz_r20 if zz_r20 is not None else (sh_r20 or 0.0)
                    abv = zz_abv if zz_abv is not None else bool(sh_abv)
                    r5 = zz_r5 if zz_r5 is not None else (sh_r5 or 0.0)
                    if abv and r20 >= 3.0:
                        stage, label = "advance", "主升"
                    elif (not abv) and r20 <= -3.0:
                        stage, label = "decline", "主跌退潮"
                    elif abv and r5 >= 1.5:
                        stage, label = "trial", "低位试错"
                    else:
                        stage, label = "chop", "高位震荡"

                    return {
                        "date": chosen_d,
                        "stage": stage,
                        "stageLabel": label,
                        "shChangePct": sh_pct,
                        "cybChangePct": cyb_pct,
                        "zz1000ChangePct": zz_pct,
                        "kc50ChangePct": kc_pct,
                        "zz1000Ret20": zz_r20,
                        "zz1000AboveMA20": zz_abv,
                        "kc50Ret20": kc_r20,
                        "kc50AboveMA20": kc_abv,
                        "turnoverYi": to_yi,
                        "source": "tdx",
                        "sourceLabel": "本地通达信实时同步日线",
                    }, chosen_d
        except Exception:
            pass

    # Fallback to bundled 2022-2026 real market indices
    env = build_fallback_market_env()
    all_d = sorted(env.keys())
    if not all_d:
        return {"date": target_date or "", "stage": "chop", "stageLabel": "震荡", "source": "none", "sourceLabel": "暂无行情"}, target_date or ""
    if target_date and target_date in env:
        chosen_d = target_date
    elif target_date:
        earlier = [d for d in all_d if d <= target_date]
        chosen_d = earlier[-1] if earlier else all_d[-1]
    else:
        chosen_d = all_d[-1]
    snap = dict(env[chosen_d])
    snap["sourceLabel"] = "内置 2022–2026 真实历史行情样本（配置通达信目录后自动切换最新本地日线）"
    return snap, chosen_d


def _symbol_from_code(code: str) -> str:
    c = str(code or "").strip().lower()
    if c.startswith(("sh", "sz", "bj")) and len(c) == 8:
        return c
    c = "".join(ch for ch in c if ch.isdigit()).zfill(6)
    if c.startswith(("43", "83", "87", "88", "92")):
        return f"bj{c}"
    if c.startswith(("5", "6", "9", "11")):
        return f"sh{c}"
    return f"sz{c}"


def _analyze_stock_at_date(bars: list[dict], symbol: str, chosen_date: str) -> dict[str, Any] | None:
    if not bars:
        return None
    pos = None
    for i, b in enumerate(bars):
        if b["date"] <= chosen_date:
            pos = i
        else:
            break
    if pos is None:
        return None
    b = bars[pos]
    lim = 20.0 if symbol.startswith(("sh68", "sz30")) else (30.0 if symbol.startswith("bj") else 10.0)
    is_zt = b["pct"] >= lim - 0.5 and abs(b["close"] - b["high"]) / max(b["high"], 0.01) < 0.006
    is_dt = b["pct"] <= -(lim - 0.5) and abs(b["close"] - b["low"]) / max(b["low"], 0.01) < 0.006
    consec_zt = 0
    for j in range(pos, max(-1, pos - 12), -1):
        jb = bars[j]
        if jb["pct"] >= lim - 0.5 and abs(jb["close"] - jb["high"]) / max(jb["high"], 0.01) < 0.006:
            consec_zt += 1
        else:
            break
    ma5 = mean(x["close"] for x in bars[max(0, pos - 4):pos + 1])
    ma10 = mean(x["close"] for x in bars[max(0, pos - 9):pos + 1])
    ma20 = mean(x["close"] for x in bars[max(0, pos - 19):pos + 1])
    ret5 = ((b["close"] / bars[pos - 5]["close"]) - 1.0) * 100.0 if pos >= 5 and bars[pos - 5]["close"] > 0 else 0.0
    ret20 = ((b["close"] / bars[pos - 20]["close"]) - 1.0) * 100.0 if pos >= 20 and bars[pos - 20]["close"] > 0 else 0.0
    vol5 = mean(x["volume"] for x in bars[max(0, pos - 5):pos]) if pos >= 1 else b["volume"]
    vol_ratio = (b["volume"] / vol5) if vol5 > 0 else 1.0
    # Upper shadow / bomb-board check (touched limit-up intraday but didn't close ZT)
    prev_c = bars[pos - 1]["close"] if pos > 0 else b["open"]
    high_pct = ((b["high"] / prev_c) - 1.0) * 100.0 if prev_c > 0 else 0.0
    bombed_zt = (not is_zt) and (high_pct >= lim - 0.5)
    return {
        "date": b["date"],
        "open": b["open"],
        "high": b["high"],
        "low": b["low"],
        "close": b["close"],
        "pct": b["pct"],
        "amountYi": round(b["amount"] / 1e8, 2),
        "ma5": round(ma5, 3),
        "ma10": round(ma10, 3),
        "ma20": round(ma20, 3),
        "aboveMA5": b["close"] >= ma5,
        "aboveMA20": b["close"] >= ma20,
        "ret5": round(ret5, 2),
        "ret20": round(ret20, 2),
        "volRatio": round(vol_ratio, 2),
        "isZt": is_zt,
        "isDt": is_dt,
        "bombedZt": bombed_zt,
        "consecZt": consec_zt,
        "board": "科创板" if symbol.startswith("sh68") else ("创业板" if symbol.startswith("sz30") else ("北交所" if symbol.startswith("bj") else "主板")),
    }


def _calc_limit_prices(ref_close: float, symbol: str) -> tuple[float, float, float]:
    """Return (limit_up_price, limit_down_price, limit_pct) for next trading day."""
    lim_pct = 20.0 if symbol.startswith(("sh68", "sz30")) else (30.0 if symbol.startswith("bj") else 10.0)
    if ref_close <= 0:
        return 0.0, 0.0, lim_pct
    up_px = round(ref_close * (1.0 + lim_pct / 100.0), 2)
    dn_px = round(ref_close * (1.0 - lim_pct / 100.0), 2)
    return up_px, dn_px, lim_pct


def _evaluate_holding(
    style_key: str,
    profile: dict[str, Any],
    snap: dict[str, Any],
    holding: dict[str, Any],
    stk: dict[str, Any] | None,
    total_equity: float,
    *,
    effective_stop_pct: float | None = None,
    effective_single_cap_pct: float | None = None,
    trim_profit_pct: float | None = None,
) -> dict[str, Any]:
    """Generate exact, beginner-readable action recommendation for one holding under a master's rules."""
    code = str(holding.get("code") or "")
    sym = _symbol_from_code(code)
    name = str(holding.get("name") or code)
    qty = float(holding.get("quantity") or 0)
    avg_cost = float(holding.get("averageCost") or 0)
    price = (stk["close"] if stk else None) or float(holding.get("marketPrice") or avg_cost or 0)
    mv = qty * price if (qty > 0 and price > 0) else float(holding.get("marketValue") or holding.get("cost") or 0)
    weight_pct = round(mv / total_equity * 100.0, 1) if total_equity > 0 and mv > 0 else None
    pnl_pct = round((price / avg_cost - 1.0) * 100.0, 2) if (avg_cost > 0 and price > 0) else 0.0

    stage = snap.get("stage", "chop")
    zz_pct = snap.get("zz1000ChangePct") or 0.0
    kc_abv = snap.get("kc50AboveMA20")
    to_yi = snap.get("turnoverYi") or 15000.0
    stop_line = float(effective_stop_pct) if effective_stop_pct is not None else float(profile["stopLossPct"])
    max_single = (
        float(effective_single_cap_pct)
        if effective_single_cap_pct is not None
        else float(profile["regimeRules"].get(stage, profile["regimeRules"]["chop"])["maxSingle"])
    )
    trim_line = float(trim_profit_pct) if trim_profit_pct is not None else float(profile["takeProfitTrimPct"])

    is_zt = stk["isZt"] if stk else False
    bombed = stk["bombedZt"] if stk else False
    consec_zt = stk["consecZt"] if stk else 0
    above_ma5 = stk["aboveMA5"] if stk else True
    above_ma20 = stk["aboveMA20"] if stk else True
    day_pct = stk["pct"] if stk else 0.0
    board = stk["board"] if stk else "主板"

    stop_px = round(avg_cost * (1.0 + stop_line / 100.0), 2) if avg_cost > 0 else round(price * (1.0 + stop_line / 100.0), 2)
    ma5_px = round(stk["ma5"], 2) if stk else round(price, 2)
    ma10_px = round(stk["ma10"], 2) if stk and stk.get("ma10") else ma5_px
    ma20_px = round(stk["ma20"], 2) if stk and stk.get("ma20") else ma5_px
    today_low_px = round(stk["low"], 2) if stk and stk.get("low") else round(price * 0.98, 2)
    today_high_px = round(stk["high"], 2) if stk and stk.get("high") else round(price * 1.02, 2)
    zt_px, dt_px, lim_pct = _calc_limit_prices(price, sym)

    # Decision tree tailored to each master's real statistical rules
    if bombed or (stk and stk["isDt"]):
        action_code = "CUT_OPEN"
        action_label = "次日 09:15–09:35 无条件清仓"
        urgency = "high"
        pos_delta = "建议清仓（卖出 100%）"
        exec_win = "09:15–09:35 竞价或开盘前 5 分钟"
        reason = (
            f"当日炸板/跌停属于最危险的形态破坏信号。"
            f"{'在希望姐姐样本中，当日未封板(炸板)胜率 0%、均亏 -11.3%，100% 在次日 09:35 前直接核出。' if style_key == 'xiwangjiejie_1to2' else '在乌江望月与灰常懒的实盘中，形态破位次日 09:45 前秒砍可平均躲过未来 3 日 -3% 以上续跌。'}"
        )
    elif pnl_pct <= stop_line:
        action_code = "CUT_OPEN"
        action_label = "触及止损红线 · 早盘果断认错"
        urgency = "high"
        pos_delta = "建议全清止损（卖出 100%）"
        exec_win = "09:15–09:45（最迟 10:30）"
        reason = (
            f"当前浮亏 {pnl_pct:+.2f}% 已击穿 {stop_line}% 硬止损红线（止损触发价 ¥{stop_px:.2f}）。"
            f"严禁浮亏补仓摊平！次日若 09:45 仍无法放量翻红，立即认错释放子弹。"
        )
    elif style_key == "wujiang_dynamic" and board in ("科创板", "创业板") and stage == "decline" and (kc_abv is False or to_yi < 18000) and pnl_pct < 3.0:
        action_code = "CUT_OPEN"
        action_label = "旧赛道退潮换仓 · 早盘清仓切主线"
        urgency = "high"
        pos_delta = "建议借冲高清仓（卖出 100%）"
        exec_win = "09:26–10:30"
        reason = (
            f"当前科创50位于 MA20 下方且市场偏弱/缩量（{to_yi:.0f}亿），按乌江望月第 73 届「清零科创切主板题材」的纠错纪律，不恋战弱势 20cm 科技，反弹即换仓。"
        )
    elif is_zt and pnl_pct >= profile["addOnWinMinPct"]:
        if stage == "decline" and zz_pct <= -1.8 and consec_zt >= 4:
            action_code = "CLEAR_PROFIT"
            action_label = "高位涨停遇大盘长阴 · 次日冲高兑现"
            urgency = "medium"
            pos_delta = "建议次日早盘分批止盈 60%–100%"
            exec_win = "09:26–10:15 冲高不封即兑现"
            reason = (
                f"已连板 {consec_zt} 板且浮盈 {pnl_pct:+.2f}%，但当日大盘长阴杀跌（中证1000 {zz_pct:+.2f}%）。"
                f"情绪高潮叠加大盘走弱，次日冲高不秒板则分批落袋防核按钮。"
            )
        elif weight_pct is not None and weight_pct >= max_single * 0.95:
            action_code = "HOLD_WATCH"
            action_label = "涨停锁仓持有（仓位已足，不再盲目加仓）"
            urgency = "low"
            pos_delta = "锁仓不动；次日不板则 10:30 前先卖一半"
            exec_win = "次日 09:30–10:30 观察承接"
            reason = (
                f"当日封死涨停、浮盈 {pnl_pct:+.2f}%，符合真龙强持股特征。"
                f"因当前仓位 {weight_pct}% 已近单票上限 {max_single:.0f}%，明日继续封板则锁仓，断板滞涨先减仓 50% 锁利润。"
            )
        else:
            action_code = "ADD_ON_STRENGTH"
            action_label = "浮盈涨停确认 · 次日弱转强可顺势加仓"
            urgency = "medium"
            pos_delta = f"若次日高开 +2%~+6% 且强承接，可浮盈加仓至 ≤{max_single:.0f}%"
            exec_win = "次日 09:25–10:00 确认强承接后加仓"
            reason = (
                f"当日强势封板且浮盈 {pnl_pct:+.2f}%，进入「做对放大战果」窗口！"
                f"加仓前提：必须是板块前排领涨，且加仓后把整体止损线上移至成本价附近。"
            )
    elif pnl_pct >= trim_line and not is_zt:
        action_code = "TRIM_HALF"
        action_label = "大盈未封板 · 次日早盘冲高分批减仓"
        urgency = "medium"
        pos_delta = "建议次日冲高先减仓 50% 锁利润"
        exec_win = "09:26–10:15 急拉无力封板时分批减"
        reason = (
            f"已有可观浮盈 {pnl_pct:+.2f}% 但当日未封死涨停。短线情绪股隔日早盘冲高溢价最高，"
            f"先兑现一半锁定利润，剩余底仓以跌破 MA5（¥{ma5_px:.2f}）作为移动止盈线。"
        )
    elif pnl_pct < 0 and (not above_ma5 or day_pct <= -2.5):
        action_code = "CUT_OPEN"
        action_label = "浮亏且走势转弱 · 次日早盘不及预期砍仓"
        urgency = "high"
        pos_delta = "若次日 09:45 不能翻红站上均价线，清仓 100%"
        exec_win = "09:26–09:45（灰常懒/乌江核心止损窗）"
        reason = (
            f"当前浮亏 {pnl_pct:+.2f}% 且收盘偏弱（当日 {day_pct:+.2f}%，{'跌破' if not above_ma5 else '逼近'} MA5）。"
            f"各大佬 85%–95% 的亏损单都在次日早盘了结，小亏认错换子弹是推土机曲线的第一前提。"
        )
    elif not above_ma20 and pnl_pct <= 2.0:
        action_code = "TRIM_HALF"
        action_label = "跌破 MA20 弱势结构 · 逢反抽减仓/换股"
        urgency = "medium"
        pos_delta = "建议反抽减仓 50%–100%，切回 MA20 上方主线"
        exec_win = "09:30–10:30 逢冲高出局"
        reason = (
            f"该股处于 MA20（¥{ma20_px:.2f}）下方弱势区间，横盘弱势股会消耗信心与机会成本，建议反抽减仓腾出槽位。"
        )
    else:
        action_code = "HOLD_WATCH"
        action_label = "趋势尚可 · 持有观察并设好早盘防守线"
        urgency = "low"
        pos_delta = f"暂持仓不动；次日冲高不板可减半，跌破 ¥{stop_px:.2f} 止损"
        exec_win = "次日 09:30–10:30 动态跟踪"
        reason = (
            f"当前盈亏 {pnl_pct:+.2f}%，仍位于 MA5（¥{ma5_px:.2f}）/ MA20（¥{ma20_px:.2f}）支撑上方且未触发止损线。"
            f"次日若放量突破转强可格局，若缩量滞涨或跌破 MA5 即获利/微亏换股。"
        )

    triggers = [
        f"硬止损触发价：约 ¥{stop_px:.2f}（较成本 {stop_line}%）或次日 09:45 水下弱反抽无力",
        f"移动防守/减仓位：MA5 ≈ ¥{ma5_px:.2f}；早盘冲高不板先卖 50%",
        f"顺势加仓条件：仅当冲击/封死涨停（¥{zt_px:.2f}）且仍为主线前排核心时考虑（绝不浮亏补仓）",
    ]
    return {
        "code": code,
        "symbol": sym,
        "name": name,
        "board": board,
        "quantity": qty,
        "averageCost": round(avg_cost, 3),
        "latestPrice": round(price, 3),
        "latestDate": stk["date"] if stk else snap.get("date"),
        "dayChangePct": day_pct,
        "unrealizedPnlPct": pnl_pct,
        "marketValue": round(mv, 2),
        "weightPct": weight_pct,
        "isZt": is_zt,
        "bombedZt": bombed,
        "consecZt": consec_zt,
        "aboveMA5": above_ma5,
        "aboveMA20": above_ma20,
        "ma5Price": ma5_px,
        "ma10Price": ma10_px,
        "ma20Price": ma20_px,
        "todayLowPrice": today_low_px,
        "todayHighPrice": today_high_px,
        "stopPrice": stop_px,
        "limitUpPrice": zt_px,
        "limitDownPrice": dt_px,
        "limitPct": lim_pct,
        "actionCode": action_code,
        "actionLabel": action_label,
        "urgency": urgency,
        "targetPositionChange": pos_delta,
        "executionWindow": exec_win,
        "triggers": triggers,
        "plainReason": reason,
    }


def _build_holding_scenario_playbook(
    act: dict[str, Any],
    lim: dict[str, Any],
    snap: dict[str, Any],
    style_key: str,
    total_equity: float,
) -> dict[str, Any]:
    """Build explicit 4-scenario playbook (默认基准 / 涨停怎么做 / 冲高震荡怎么做 / 破位怎么做) for one holding."""
    from service.master_rules import calc_buy_shares, calc_trim_shares

    sym = act["symbol"]
    qty = int(round(float(act.get("quantity") or 0)))
    px = float(act.get("latestPrice") or act.get("averageCost") or 0.0)
    avg_c = float(act.get("averageCost") or px)
    pnl = float(act.get("unrealizedPnlPct") or 0.0)
    cur_w = float(act.get("weightPct") or 0.0)
    zt_px = float(act.get("limitUpPrice") or round(px * 1.10, 2))
    stop_px = float(act.get("stopPrice") or round(avg_c * 0.95, 2))
    ma5_px = float(act.get("ma5Price") or px)
    ma20_px = float(act.get("ma20Price") or px)
    low_px = float(act.get("todayLowPrice") or round(px * 0.98, 2))
    low_open_px = round(px * 0.97, 2)
    breakeven_px = round(avg_c * 0.997, 2) if avg_c > 0 else px
    stage = snap.get("stage", "chop")

    trim_ratio = float(lim.get("trimRatio", 0.5))
    trim_profit_pct = float(lim.get("trimProfitPct", 8.0))
    trim_ref_px = round(max(avg_c * (1.0 + trim_profit_pct / 100.0), px * 1.045), 2) if avg_c > 0 else round(px * 1.05, 2)
    trim_ref_px = min(trim_ref_px, round(zt_px * 0.99, 2)) if zt_px > 0 else trim_ref_px
    trim_qty = calc_trim_shares(qty, trim_ratio, sym) if qty > 0 else 0

    ord_row = act.get("plannedOrder")
    # 1. Base Default Plan
    if ord_row:
        verb = "买入" if ord_row["side"] == "buy" else "卖出"
        base_plan = {
            "tag": ord_row["actionLabel"],
            "window": ord_row["execWindow"],
            "headline": f"{verb} {ord_row['quantity']:,} 股（参考价 ¥{ord_row['refPrice']:.2f} · 约 ¥{ord_row['estAmount']:,.0f}）",
            "detail": (
                f"按今日收盘数据触发【{ord_row['actionLabel']}】：{ord_row['reason']}。"
                f"执行后单票仓位由 {ord_row['beforeWeightPct']:.1f}% → {ord_row['afterWeightPct']:.1f}%。"
            ),
        }
    else:
        base_plan = {
            "tag": "锁仓观察",
            "window": act.get("executionWindow") or "09:30–10:30",
            "headline": f"默认续持 {qty:,} 股不动（当前占比 {cur_w:.1f}% · 浮盈 {pnl:+.2f}%）",
            "detail": (
                f"今日收盘 ¥{px:.2f} 未触发强制止损或机械减仓阈值，明日早盘无需挂盲目市价卖单，按下方盘中三种走势分条件应对。"
            ),
        }

    # 2. Limit-Up Scenario ("涨停怎么做")
    single_cap_pct = float(lim.get("singleCapPct") or 30.0)
    room_pct = max(0.0, single_cap_pct - cur_w)
    add_pct = min(float(lim.get("addWinPct") or 0.0), room_pct)
    add_budget = total_equity * (add_pct / 100.0) if total_equity > 0 and add_pct >= 4.0 else 0.0
    potential_add_qty = calc_buy_shares(add_budget, zt_px * 0.98, sym) if (add_budget > 0 and zt_px > 0) else 0
    bomb_sell_qty = qty if style_key in ("xiwangjiejie_1to2", "dongshizhang_highboard") or trim_qty <= 0 else trim_qty

    if ord_row and ord_row["actionType"] == "add_win":
        zt_headline = f"明日涨停价 ¥{zt_px:.2f} · 弱转强顺势加仓 {ord_row['quantity']:,} 股"
        zt_detail = (
            f"今日已封板确认！若明日竞价高开 +2%~+6% 且 09:25–10:00 放量强承接/冲击涨停 ¥{zt_px:.2f}，"
            f"执行加仓 {ord_row['quantity']:,} 股（仓位升至 {ord_row['afterWeightPct']:.1f}% ≤ 单票上限 {single_cap_pct:.0f}%），"
            f"加仓后将总仓防守线立即上移至保本价 ¥{breakeven_px:.2f}；若明日低开无溢价则取消加仓计划。"
        )
    elif lim.get("useAddOnWin") and stage != "decline" and potential_add_qty > 0 and pnl >= -1.0:
        zt_headline = f"明日涨停价 ¥{zt_px:.2f} · 封死锁仓，前排真龙可顺势加仓约 {potential_add_qty:,} 股"
        zt_detail = (
            f"若明日早盘放量上板并封死 ¥{zt_px:.2f}：原仓 {qty:,} 股坚决锁仓不卖；"
            f"若同时为当日板块领涨前排且 10:00 前强势回封/突破，可顺势加仓约 {potential_add_qty:,} 股（约 ¥{potential_add_qty * zt_px:,.0f}，单票压在 ≤{single_cap_pct:.0f}%），加仓后止损上移至保本价 ¥{breakeven_px:.2f}。"
        )
    elif stage == "decline" and int(act.get("consecZt") or 0) >= 3:
        zt_headline = f"明日涨停价 ¥{zt_px:.2f} · 缩量硬封留底仓，烂板分歧逢板兑现"
        zt_detail = (
            f"当前大盘处于退潮期且个股已在高位（{act.get('consecZt')}板）：若明日缩量硬封 ¥{zt_px:.2f} 为全场唯一活口则锁仓；"
            f"若冲板反复开板放巨量，直接在涨停板附近分批止盈 50%~100% 防次日核按钮，绝不在高位加仓。"
        )
    else:
        zt_headline = f"明日涨停价 ¥{zt_px:.2f} · 封死涨停则全部 {qty:,} 股锁仓格局"
        zt_detail = (
            f"若明日早盘快速拉升封死涨停 ¥{zt_px:.2f}（即使今日原处于浮亏/震荡，涨停即代表超预期弱转强反转），"
            f"立即取消原定止损/减仓卖出，全部 {qty:,} 股锁仓持有博弈连板溢价（当前仓位/模式不追高加仓）。"
        )
    bomb_fallback = (
        f"【封板后炸板应对】若触及涨停 ¥{zt_px:.2f} 后炸板回落跌超 3%（跌破 ¥{zt_px * 0.97:.2f}）且 3 分钟内无法回封："
        f"立即卖出 {bomb_sell_qty:,} 股{'（全清防核按钮）' if bomb_sell_qty >= qty else f'（先减半锁利，余 {qty - bomb_sell_qty:,} 股看收盘能否守住 MA5）'}。"
    )
    limit_up_plan = {
        "limitUpPrice": zt_px,
        "window": "09:25–10:30 盯封单与承接",
        "headline": zt_headline,
        "detail": zt_detail,
        "bombFallback": bomb_fallback,
    }

    # 3. Intraday Surge Non-ZT / Chop Scenario ("冲高不板 / 震荡怎么做")
    if trim_qty > 0 and trim_qty < qty and lim.get("usePartialTrim", True):
        remain_q = qty - trim_qty
        trim_plan = {
            "triggerPrice": trim_ref_px,
            "window": "09:30–10:30 急拉滞涨窗",
            "headline": f"冲高至 ¥{trim_ref_px:.2f} 附近不封板 → 先减半卖出 {trim_qty:,} 股",
            "detail": (
                f"若明日早盘冲高至 ¥{trim_ref_px:.2f} 一带（分时急拉但量能跟不上、迟迟无法封死 ¥{zt_px:.2f}），"
                f"先卖出 {trim_qty:,} 股锁定阶段利润；剩余 {remain_q:,} 股底仓以 5 日线 MA5（¥{ma5_px:.2f}）作为移动防守线，收盘不破继续拿。"
            ),
        }
    else:
        trim_plan = {
            "triggerPrice": trim_ref_px,
            "window": "09:30–10:30 冲高兑现窗",
            "headline": f"冲高至 ¥{trim_ref_px:.2f} 附近滞涨不板 → 择高止盈落袋",
            "detail": (
                f"当前持仓 {qty:,} 股{'（仅1手不可拆半）' if qty <= 200 else ''}：若明日早盘拉高至 ¥{trim_ref_px:.2f} 附近分时拐头跌破均价线且不封板，直接兑现落袋；"
                f"若全天温和站稳分时均线及 MA5（¥{ma5_px:.2f}）上方，则持有至尾盘再定。"
            ),
        }

    # 4. Breakdown / Weakness Scenario ("破位 / 走弱怎么做")
    sell_win = "09:15–09:35" if style_key in ("xiwangjiejie_1to2", "dongshizhang_highboard") else "09:25–09:45（最迟 10:30）"
    breakdown_plan = {
        "stopPrice": stop_px,
        "ma5Price": ma5_px,
        "ma20Price": ma20_px,
        "lowOpenCutPrice": low_open_px,
        "window": sell_win,
        "headline": f"跌破止损价 ¥{stop_px:.2f} 或破 MA5 (¥{ma5_px:.2f}) 不翻红 → 无条件清仓 {qty:,} 股",
        "detail": (
            f"① 价格硬红线：盘中一旦触及止损价 ¥{stop_px:.2f}（{lim.get('stopLossPct', -5.0):+.1f}%），无条件卖出全部 {qty:,} 股，绝不水下补仓摊平；"
            f"② 结构破位线：若竞价大幅低开破 ¥{low_open_px:.2f}（-3%），或盘中跌破 MA5（¥{ma5_px:.2f}）/ 昨日最低价（¥{low_px:.2f}）且在 {sell_win} 前无法放量翻红站回分时均价线，果断清仓换取现金子弹！"
        ),
    }

    return {
        "basePlan": base_plan,
        "limitUpPlan": limit_up_plan,
        "trimHoldPlan": trim_plan,
        "breakdownPlan": breakdown_plan,
    }


def _build_new_position_plan(
    style_key: str,
    profile: dict[str, Any],
    snap: dict[str, Any],
    lim: dict[str, Any],
    plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    total_equity: float,
    avail_cash: float,
) -> dict[str, Any]:
    """Build explicit next-day new position opening plan ('如果要新开仓位要怎么做')."""
    from service.master_rules import calc_buy_shares

    proj_sum = plan["projectedSummary"]
    orders = plan["orders"]
    stage = snap.get("stage", "chop")
    zz_pct = float(snap.get("zz1000ChangePct") or 0.0)

    sell_orders = [o for o in orders if o["side"] == "sell"]
    buy_orders = [o for o in orders if o["actionType"] == "open_buy"]
    planned_buy_by_sym = {o["symbol"]: o for o in buy_orders}

    cash_released = round(sum(float(o.get("estAmount") or 0.0) for o in sell_orders), 2)
    full_exits = sum(1 for o in sell_orders if o["actionType"] in ("cut_stop", "cut_weak", "sell_rotate", "sell_profit", "sell_time"))
    before_cnt = int(proj_sum.get("beforeCount") or 0)
    after_sell_cnt = max(0, before_cnt - full_exits)
    max_slots = int(lim.get("maxSlots") or 0)
    open_slots = max(0, max_slots - after_sell_cnt)
    cash_for_buys = round(avail_cash + cash_released, 2)
    first_pct = float(lim.get("firstEntryPct") or 15.0)
    per_slot_budget = round(min(cash_for_buys * 0.96, total_equity * (first_pct / 100.0)), 2) if total_equity > 0 else 0.0

    decline_shutoff = (
        (lim.get("useRegimeSwitch", True) and style_key == "ruhan_halfway" and stage == "decline" and zz_pct < -0.5)
        or (lim.get("useRegimeSwitch", True) and style_key == "songsong_defensive" and stage == "decline")
        or float(lim.get("totalPosCap") or 0.0) <= 0.01
        or max_slots <= 0
    )

    can_open = (not decline_shutoff) and (not lim.get("haltNewBuys")) and open_slots > 0 and per_slot_budget >= 2000

    if decline_shutoff:
        status_tone = "warn"
        headline = f"明日禁止盲目新开仓 · 当前处于「{snap.get('stageLabel', '退潮')}」防守/断电保护期"
        execution_rule = (
            f"当前环境触发弱市保护（阶段总仓硬上限 ≤{lim['totalPosCapPct']:.0f}% · 最多 {max_slots} 仓），"
            f"明日第一要务是管住手保留现金！除非大盘放量反包转强且出现全市场唯一逆势连板总龙，否则一律空仓观望。"
        )
    elif open_slots <= 0:
        status_tone = "warn"
        headline = f"当前持仓已满 {after_sell_cnt}/{max_slots} 仓 · 明日须先卖出弱势旧仓腾出槽位后，才允许递补开新仓"
        execution_rule = (
            f"按设定的最多 {max_slots} 只持仓硬约束，若明日早盘旧仓全部强势锁仓，则严禁超额新开第 {max_slots + 1} 只！"
            f"只有当盘中某只旧仓触发破位止损或冲高清仓、实际持股降到 {max(0, max_slots - 1)} 只以下时，才可用回笼资金按下方备选池择强递补 1 只。"
        )
    elif not candidates:
        status_tone = "warn"
        headline = f"账户空余 {open_slots} 个槽位（可用资金 ¥{cash_for_buys:,.0f}），但今日收盘无符合该高手买点结构的达标标的"
        execution_rule = "宁缺毋滥是顶尖游资的核心纪律：没有模式内高分标的时绝不强行凑单，明日空仓/轻仓等待下一交易日信号。"
    else:
        status_tone = "ok"
        sell_first_tip = (
            f"先在 09:15–09:45 执行旧仓卖出回笼约 ¥{cash_released:,.0f} 现金与槽位，随后"
            if sell_orders else "当前现金与槽位充足，明日"
        )
        headline = (
            f"明日可择强新开最多 {open_slots} 仓（上限 {max_slots} 只 · 总仓 ≤{lim['totalPosCapPct']:.0f}%）"
            f" · 单票标准首仓 {first_pct:.1f}%（约 ¥{per_slot_budget:,.0f}/只）"
        )
        execution_rule = (
            f"{sell_first_tip}在「{profile['buyWindow']}」紧盯下方候选池前排："
            f"严格等分/轻仓试错（单票首仓 ≤{first_pct:.1f}%，严禁第一天满仓梭哈），只买盘中率先走强达标的前 {open_slots} 只；"
            f"若前排低开走弱或一字无量买不到，顺延观察备选标的，不达触发条件坚决放弃留现金。"
        )

    cand_playbooks: list[dict[str, Any]] = []
    stop_pct = float(lim.get("stopLossPct") or -5.0)
    max_show = max(6, max_slots + 4)
    for idx, c in enumerate(candidates[:max_show], 1):
        sym = c["symbol"]
        close_px = float(c["close"])
        ma5_px = float(c.get("ma5") or close_px)
        zt_px, _, _ = _calc_limit_prices(close_px, sym)
        ord_row = planned_buy_by_sym.get(sym)
        ref_budget = total_equity * (first_pct / 100.0) if total_equity > 0 else 15000.0
        ref_qty = ord_row["quantity"] if ord_row else calc_buy_shares(ref_budget, close_px * 1.002, sym)
        ref_amt = ord_row["estAmount"] if ord_row else round(ref_qty * close_px, 2)
        ref_w = ord_row["afterWeightPct"] if ord_row else (round(ref_amt / total_equity * 100.0, 1) if total_equity > 0 else first_pct)
        init_stop_px = round(close_px * (1.0 + stop_pct / 100.0), 2)

        if style_key == "xiwangjiejie_1to2":
            trigger_zone = f"竞价高开 +1.5%~+5.5%（¥{close_px*1.015:.2f}~¥{close_px*1.055:.2f}），10:00 前放量冲击涨停 ¥{zt_px:.2f} 时排板/打板确认"
            abort_rule = f"① 竞价水下低开（< ¥{close_px:.2f}）或高开 >+7% 缩量秒砸直接放弃；② 10:30 后才勉强封板的后排跟风不打；③ 指数早盘大跌超 -1% 时仓位减半或放弃"
        elif style_key == "dongshizhang_highboard":
            trigger_zone = f"09:15–09:25 竞价爆量高开弱转强（¥{close_px*1.02:.2f}~¥{close_px*1.07:.2f}），开盘快速上板 ¥{zt_px:.2f} 确认合力时狙击"
            abort_rule = f"① 竞价不及预期低开（< ¥{close_px*0.98:.2f}）或被核按钮立刻撤单放弃（董事长历史 64% 排单撤单）；② 同梯队出现更强卡位龙时不做跟风老二"
        elif style_key in ("huichanglan_mainline", "ruhan_halfway"):
            trigger_zone = f"09:45–10:45 红盘 +2.0%~+6.0%（¥{close_px*1.02:.2f}~¥{close_px*1.06:.2f}）站稳分时均价线且量比放大时分仓半路介入"
            abort_rule = f"① 开盘低开低走跌破昨日收盘 ¥{close_px:.2f} 且水下震荡绝不抄底；② 已急拉超 +7.5%（> ¥{close_px*1.075:.2f}）不追高防冲高回落吃面"
        elif style_key == "songsong_defensive":
            dip_lo = round(min(ma5_px, close_px * 0.975), 2)
            dip_hi = round(close_px * 1.01, 2)
            trigger_zone = f"10:00–14:30 盘中分歧回踩平盘至 MA5 支撑区（¥{dip_lo:.2f}~¥{dip_hi:.2f}）缩量企稳不破时低吸"
            abort_rule = f"① 早盘直接高开急拉 +5% 以上（> ¥{close_px*1.05:.2f}）绝不追高；② 放量直线跳水击穿 MA5（¥{ma5_px:.2f}）无承接时不接飞刀"
        else:  # wujiang_dynamic
            if c.get("board") in ("科创板", "创业板"):
                trigger_zone = f"09:30–10:30 回踩分时均线不破（¥{close_px*0.99:.2f}~¥{close_px*1.04:.2f}）或放量突破前期高点时轻仓试错"
            else:
                trigger_zone = (
                    f"{'14:15–14:55 尾盘确认抗跌站稳 MA5（¥' + f'{ma5_px:.2f}' + '）拿先手' if stage == 'decline' else f'09:30–10:30 分歧转一致红盘（¥{close_px*1.01:.2f}~¥{close_px*1.06:.2f}）或突破上板 ¥{zt_px:.2f} 试错'}"
                )
            abort_rule = f"① 竞价低开超 -3%（< ¥{close_px*0.97:.2f}）且无承接直接剔除；② 所属题材板块早盘集体退潮大跌时不逆势硬顶"

        post_buy = (
            f"买入 {ref_qty:,} 股后当日 T+1 锁仓：① 若当日强势封死涨停（¥{zt_px:.2f}），次日享受连板溢价，并按涨停剧本决定锁仓或顺势加仓；"
            f"② 若买入当日冲高回落/炸板，T+1 日 09:15–09:45 严格以 ¥{init_stop_px:.2f}（{stop_pct:+.1f}%）或跌破 MA5（¥{ma5_px:.2f}）无条件快刀止损！"
        )

        cand_playbooks.append({
            **c,
            "rank": idx,
            "role": "planned" if ord_row else "watchlist",
            "roleLabel": "明日计划建仓" if ord_row else "候补递补观察",
            "plannedQty": ref_qty,
            "plannedAmount": ref_amt,
            "plannedWeightPct": ref_w,
            "limitUpPrice": zt_px,
            "initialStopPrice": init_stop_px,
            "buyWindow": profile["buyWindow"],
            "triggerZone": trigger_zone,
            "abortConditions": abort_rule,
            "postBuyPlan": post_buy,
        })

    return {
        "canOpenNew": can_open,
        "declineShutoff": decline_shutoff,
        "statusTone": status_tone,
        "headline": headline,
        "executionRule": execution_rule,
        "currentCount": before_cnt,
        "afterSellCount": after_sell_cnt,
        "maxSlots": max_slots,
        "openSlots": open_slots,
        "availableCashBefore": round(avail_cash, 2),
        "cashReleasedBySells": cash_released,
        "availableCashForNewBuys": cash_for_buys,
        "firstEntryPct": first_pct,
        "singleCapPct": lim["singleCapPct"],
        "totalPosCapPct": lim["totalPosCapPct"],
        "perSlotBudget": per_slot_budget,
        "plannedBuyCount": len(buy_orders),
        "candidatePlaybooks": cand_playbooks,
    }


def _build_tomorrow_timeline(
    holding_actions: list[dict[str, Any]],
    new_pos_plan: dict[str, Any],
    lim: dict[str, Any],
    snap: dict[str, Any],
) -> list[dict[str, str]]:
    """Build chronological 4-window action checklist for tomorrow."""
    urgent_sells = [h for h in holding_actions if h.get("orderSide") == "sell" and h.get("urgency") == "high"]
    trims = [h for h in holding_actions if h.get("orderSide") == "sell" and h.get("urgency") != "high"]
    adds = [h for h in holding_actions if h.get("orderActionType") == "add_win"]
    holds = [h for h in holding_actions if h.get("orderSide") == "hold"]
    planned_buys = [c for c in new_pos_plan.get("candidatePlaybooks", []) if c.get("role") == "planned"]

    s1_parts = []
    if urgent_sells:
        s1_parts.append("重点核验破位/止损持仓：" + "、".join(f"{h['name']}({h['code']})" for h in urgent_sells) + " 竞价承接，准备早盘果断出局")
    if holds or adds:
        s1_parts.append("观察续持标的：" + "、".join(f"{h['name']}(防线¥{h['stopPrice']:.2f})" for h in (adds + holds)[:4]) + " 高低开幅度")
    if planned_buys:
        s1_parts.append("核对新开候选：" + "、".join(f"{c['name']}({c['code']})" for c in planned_buys[:3]) + " 是否符合高开/承接预期，低开走弱直接划掉")
    if not s1_parts:
        s1_parts.append("空仓观察两市竞价涨跌停家数与主线板块高开强度，不急于挂隔夜盲买单")

    s2_parts = []
    if urgent_sells:
        s2_parts.append("优先执行止损/清退：" + "；".join(f"卖出 {h['name']} {h['orderQuantity']:,}股" for h in urgent_sells))
    if adds:
        s2_parts.append("确认涨停/强承接顺势加仓：" + "；".join(f"{h['name']} 弱转强加仓 {h['orderQuantity']:,}股（低开则取消）" for h in adds))
    if not s2_parts:
        s2_parts.append("无强制早盘砍仓单；紧盯现有持仓是否守住 MA5 与分时均价线，封死涨停则锁仓不动")

    s3_parts = []
    if trims:
        s3_parts.append("冲高减仓锁利：" + "；".join(f"{h['name']} 减仓 {h['orderQuantity']:,}股" for h in trims))
    if new_pos_plan.get("canOpenNew") and planned_buys:
        s3_parts.append(
            "择强建立新首仓：" + "；".join(
                f"{c['name']}({c['code']}) 满足盘中条件买 {c['plannedQty']:,}股(≈¥{c['plannedAmount']:,.0f})"
                for c in planned_buys[:3]
            )
        )
    elif not new_pos_plan.get("canOpenNew"):
        s3_parts.append(new_pos_plan.get("headline") or "今日暂不新开仓，保持仓位纪律")
    else:
        s3_parts.append("等待盘中主线分歧转一致买点，不达触发区间宁可空置槽位")

    s4_text = (
        f"收盘前核查账户总仓位严格 ≤{lim['totalPosCapPct']:.0f}%、持股数 ≤{lim['maxSlots']} 只（当前情绪阶段：{snap.get('stageLabel', '—')}）；"
        f"当日新买标的受 T+1 限制，盘后立即锚定次日硬止损线（{lim['stopLossPct']:+.1f}%），绝不把短线试错做成中线死扛。"
    )

    return [
        {"window": "09:15 – 09:25", "phase": "集合竞价核验", "action": "；".join(s1_parts) + "。"},
        {"window": "09:25 – 09:45", "phase": "早盘排雷与连板确认", "action": "；".join(s2_parts) + "。"},
        {"window": "09:45 – 11:00", "phase": "冲高止盈与新开首仓", "action": "；".join(s3_parts) + "。"},
        {"window": "14:00 – 15:00", "phase": "尾盘风控与红线复核", "action": s4_text},
    ]


def _scan_style_candidates(
    owner,
    root: Path | None,
    style_key: str,
    snap: dict[str, Any],
    chosen_date: str,
    *,
    candidate_limit: int = 12,
) -> tuple[list[dict[str, Any]], str]:
    """Scan active stocks on chosen_date matching the selected master's quantitative entry structure."""
    # Prefer local TDX top active stocks if configured, else use bundled 544-stock archive
    symbols: list[str] = []
    names: dict[str, str] = {}
    pool_label = "内置 544 只历史活跃标的池"

    if root is not None and snap.get("source") == "tdx":
        try:
            from service.master_backtest import _read_tdx_daily_window, _select_tdx_universe
            rows = owner.db.execute(
                "SELECT symbol, name FROM securities WHERE kind='stock' "
                "AND symbol NOT LIKE 'bj%' AND latest_date >= ? ORDER BY symbol",
                (chosen_date,),
            ).fetchall()
            if rows:
                selected = _select_tdx_universe(
                    [{"symbol": r["symbol"], "name": r["name"]} for r in rows],
                    set(get_fallback_names().keys()),
                    limit=600,
                )
                symbols = [s for s, _ in selected]
                names = {s: n for s, n in selected}
                pool_label = f"本地通达信沪深均衡池（{len(symbols)} 只，已剔除北交所）"
        except Exception:
            pass

    if not symbols:
        symbols = [
            s for s in get_fallback_all_stock_symbols()
            if not s.startswith("bj") and not s[2:].startswith(("11", "12", "51", "56", "58", "15", "16"))
        ]
        names = get_fallback_names()

    stage = snap.get("stage", "chop")
    kc_abv = snap.get("kc50AboveMA20")
    to_yi = snap.get("turnoverYi") or 16000.0
    prefer_tech_20cm = (style_key == "wujiang_dynamic") and (kc_abv is True) and (to_yi >= 20000)

    cands = []
    use_tdx_window = root is not None and snap.get("source") == "tdx"
    read_win = None
    if use_tdx_window:
        try:
            from service.master_backtest import _read_tdx_daily_window
            read_win = _read_tdx_daily_window
        except Exception:
            read_win = None

    for sym in symbols:
        if use_tdx_window and read_win is not None:
            try:
                p = tdx.daily_path(root, sym)
                if not p.is_file():
                    continue
                raw_bars = read_win(p, chosen_date, chosen_date, warmup_bars=35)
                bars = []
                prev_c = None
                for b in raw_bars:
                    pct = ((b["close"] / prev_c - 1.0) * 100.0) if prev_c and prev_c > 0 else 0.0
                    bars.append({**b, "pct": round(pct, 2)})
                    prev_c = b["close"]
            except Exception:
                continue
        else:
            bars, _ = _load_bars_prefer_tdx(root if snap.get("source") == "tdx" else None, sym)
        if not bars or len(bars) < 25:
            continue
        stk = _analyze_stock_at_date(bars, sym, chosen_date)
        if not stk or stk["date"] != chosen_date:
            continue
        if stk["amountYi"] < 1.5:  # liquidity filter
            continue

        score = 0.0
        match_tags = []
        entry_plan = ""

        if style_key == "wujiang_dynamic":
            if not stk["aboveMA20"]:
                continue
            if prefer_tech_20cm:
                if stk["board"] in ("科创板", "创业板") and stk["amountYi"] >= 6.0 and stk["pct"] >= 3.0:
                    score = 70 + min(20, stk["ret20"] * 0.4) + min(10, stk["volRatio"] * 3)
                    match_tags = ["放量科技主升", f"{stk['board']}容量核心", f"成交{stk['amountYi']}亿"]
                    entry_plan = "次日早盘 09:30–10:30 回踩均价线不破或放量突破时 15% 试错，封板确认次日再加仓"
            else:
                # Weak/choppy regime -> Mainboard theme core or strong breakout
                if stk["board"] == "主板" and (stk["consecZt"] >= 1 or (stk["pct"] >= 4.0 and stk["ret5"] >= 10.0)):
                    score = 68 + stk["consecZt"] * 8 + min(15, stk["amountYi"] * 1.2)
                    match_tags = ["缩量切主板题材", f"{stk['consecZt']}连板" if stk["consecZt"] else "强势半路", f"成交{stk['amountYi']}亿"]
                    entry_plan = "先 10% 轻仓拿先手（弱市尽量等 14:30 确认抗跌或早盘分歧转一致），次日不强 09:45 砍"
        elif style_key == "huichanglan_mainline":
            if not stk["aboveMA20"] or stk["board"] not in ("主板", "创业板"):
                continue
            if (2.0 <= stk["pct"] <= 7.0 or stk["isZt"]) and stk["ret20"] >= 10.0 and stk["volRatio"] >= 1.1:
                score = 65 + min(20, stk["ret20"] * 0.35) + (10 if stk["isZt"] else 5)
                match_tags = ["主线趋势MA20上", "半路/强势承接", f"20日+{stk['ret20']:.1f}%"]
                entry_plan = "次日 09:45–10:30 红盘 +2%~+5% 承接强势时 15% 半路切入，破 -5% 秒砍"
        elif style_key == "xiwangjiejie_1to2":
            if stk["board"] != "主板" or stk["consecZt"] != 1 or not stk["aboveMA20"]:
                continue
            score = 72 + min(15, stk["amountYi"] * 1.5) + min(12, stk["volRatio"] * 4)
            match_tags = ["纯主板首板完成", "明日一进二备选", f"量比{stk['volRatio']}"]
            entry_plan = "明日高开 +2%~+5% 且 10:00 前强势上板时 20% 等权打一进二；指数大阴线减半或放弃"
        elif style_key == "dongshizhang_highboard":
            if stk["board"] != "主板" or stk["consecZt"] < 2 or not stk["aboveMA20"]:
                continue
            score = 75 + stk["consecZt"] * 6 + min(12, stk["amountYi"])
            match_tags = [f"纯主板{stk['consecZt']}连板高标", "情绪抱团候选", f"5日+{stk['ret5']:.1f}%"]
            entry_plan = "盘后列入唯一候选观察，次日 09:15–09:25 竞价爆量高开强转强才出手（不及预期直接撤单放弃）"
        elif style_key == "ruhan_halfway":
            if not stk["aboveMA20"] or stk["consecZt"] >= 2:
                continue
            if (3.0 <= stk["pct"] <= 9.5 or stk["consecZt"] == 1) and stk["amountYi"] >= 3.0 and stk["volRatio"] >= 1.2:
                score = 64 + min(18, stk["volRatio"] * 5) + min(15, stk["ret5"] * 0.8)
                match_tags = ["0板/首板启动池", "MA20上方放量", f"量比{stk['volRatio']}"]
                entry_plan = "纳入次日 20 只核心股票池，09:45–11:00 涨幅 +3%~+6% 强势分仓 15% 半路"
        else:  # songsong_defensive
            if not stk["aboveMA20"] or stk["ret20"] < 15.0:
                continue
            if -4.5 <= stk["pct"] <= 2.5:
                score = 68 + min(22, stk["ret20"] * 0.35) + (8 if stk["aboveMA5"] else 0)
                match_tags = ["强趋势回踩低吸", f"20日+{stk['ret20']:.1f}%", f"当日{stk['pct']:+.1f}%"]
                entry_plan = "强趋势分歧回踩 MA5/平盘附近轻仓低吸，次日冲高 +3%~+5% 落袋，破 -3% 铁血止损"

        if score > 0:
            cands.append({
                "symbol": sym,
                "code": sym[2:],
                "name": names.get(sym, sym[2:]),
                "board": stk["board"],
                "close": stk["close"],
                "pct": stk["pct"],
                "amountYi": stk["amountYi"],
                "consecZt": stk["consecZt"],
                "ret5": stk["ret5"],
                "ret20": stk["ret20"],
                "ma5": stk["ma5"],
                "score": round(min(99.0, score), 1),
                "matchTags": match_tags,
                "entryPlan": entry_plan,
                "stopHint": f"跌破 MA5 ({stk['ma5']:.2f}) 或亏损超 {abs(STYLE_PROFILES[style_key]['stopLossPct'])}% 止损",
            })

    cands.sort(key=lambda x: (-x["score"], -x["amountYi"]))
    return cands[:max(12, candidate_limit)], pool_label


def generate_master_advice(
    owner,
    master: dict | None,
    *,
    style_key: str = "",
    target_date: str | None = None,
    positions: list[dict] | None = None,
    account_summary: dict | None = None,
    overrides: dict[str, Any] | None = None,
    tdx_root_override: Path | str | None = None,
    external_candidates: list[dict[str, Any]] | None = None,
    external_pool_label: str = "",
) -> dict[str, Any]:
    """Main entry point for market.masters.advise and /api/atrade/master-plan."""
    skey = detect_style_key(master, style_key)
    profile = STYLE_PROFILES[skey]
    root: Path | None = None
    if tdx_root_override:
        try:
            p_cand = Path(tdx_root_override)
            root = tdx.normalize_directory(str(p_cand.parent if p_cand.name.lower() == "vipdoc" else p_cand))
        except Exception:
            try:
                p_cand = Path(tdx_root_override)
                if p_cand.is_dir():
                    root = p_cand
            except Exception:
                root = None
    if root is None:
        root = _tdx_root(owner)
    snap, chosen_date = _get_market_snapshot(owner, root, target_date)
    stage = snap.get("stage", "chop")
    reg = profile["regimeRules"].get(stage, profile["regimeRules"]["chop"])

    # Determine account equity & cash basis
    pos_list = list(positions or [])
    summary = account_summary or {}
    total_equity = float(summary.get("equity") or 0)
    raw_cash = float(summary.get("cash") or 0)
    if total_equity <= 0:
        mv_sum = sum(float(p.get("marketValue") or p.get("cost") or (float(p.get("quantity") or 0) * float(p.get("averageCost") or 0))) for p in pos_list)
        total_equity = raw_cash + mv_sum
    if total_equity <= 0:
        total_equity = 100000.0  # default 10万 reference for beginner simulation

    # Run the EXACT SAME hard-coded rule & sizing engine used by master_backtest!
    from service.master_rules import resolve_position_limits
    params = strategy_params(skey, overrides)
    pre_lim = resolve_position_limits(skey, stage, total_equity, params)

    # Evaluate each holding & collect normalized EOD features for the shared rule engine
    holding_actions = []
    current_mv_total = 0.0
    holding_inputs: list[dict[str, Any]] = []
    plan_feats: dict[str, dict[str, Any]] = {}
    for h in pos_list:
        code = str(h.get("code") or "").strip()
        if not code:
            continue
        sym = _symbol_from_code(code)
        bars, _ = _load_bars_prefer_tdx(root, sym)
        stk = _analyze_stock_at_date(bars, sym, chosen_date)
        act = _evaluate_holding(
            skey,
            profile,
            snap,
            h,
            stk,
            total_equity,
            effective_stop_pct=pre_lim["stopLossPct"],
            effective_single_cap_pct=pre_lim["singleCapPct"],
            trim_profit_pct=pre_lim["trimProfitPct"],
        )
        current_mv_total += act["marketValue"]
        holding_actions.append(act)

        qty_i = int(round(float(h.get("quantity") or 0)))
        if qty_i > 0:
            px_now = float(act["latestPrice"] or act["averageCost"] or 0.0)
            avg_c = float(act["averageCost"] or px_now or 0.0)
            holding_inputs.append({
                "symbol": sym,
                "code": code,
                "name": act["name"],
                "qty": qty_i,
                "avgCost": avg_c,
                "peakPrice": max(px_now, float(stk["high"]) if stk else px_now, avg_c),
                "lastClose": px_now,
                "heldDays": 1,
                "addedWin": False,
                "trimmedProfit": False,
            })
            if stk:
                plan_feats[sym] = {
                    **stk,
                    "board": board_of(sym),
                }
            else:
                plan_feats[sym] = {
                    "close": px_now,
                    "high": px_now,
                    "pct": 0.0,
                    "ma5": px_now,
                    "ma20": px_now,
                    "aboveMA5": True,
                    "aboveMA20": True,
                    "isZt": False,
                    "isDt": False,
                    "bombedZt": False,
                    "consecZt": 0,
                    "board": board_of(sym),
                }

    # Ensure equity >= current holdings MV and derive available cash cleanly
    if current_mv_total > total_equity:
        total_equity = current_mv_total
    avail_cash = raw_cash if raw_cash > 0 else max(0.0, total_equity - current_mv_total)

    if external_candidates is not None:
        candidates = []
        for raw_c in external_candidates:
            sym = _symbol_from_code(str(raw_c.get("symbol") or raw_c.get("code") or ""))
            bars, _ = _load_bars_prefer_tdx(root, sym)
            stk = _analyze_stock_at_date(bars, sym, chosen_date)
            close_px = float(stk["close"]) if stk else float(raw_c.get("close") or 0.0)
            if close_px <= 0:
                continue
            pct_v = float(stk["pct"]) if stk else float(raw_c.get("pct") or 0.0)
            amt_v = float(stk["amountYi"]) if stk else float(raw_c.get("amountYi") or 0.0)
            czt_v = int(stk["consecZt"]) if stk else int(raw_c.get("consecZt") or 0)
            ma5_v = float(stk["ma5"]) if stk else float(raw_c.get("ma5") or close_px)
            board_lbl = stk["board"] if stk else ("科创板" if sym.startswith("sh68") else ("创业板" if sym.startswith("sz30") else ("北交所" if sym.startswith("bj") else "主板")))
            score_v = float(raw_c.get("score") or 70.0)
            sig_lbl = str(raw_c.get("signalLabel") or "").strip()
            sig_reason = str(raw_c.get("reason") or "").strip()
            tags = list(raw_c.get("matchTags") or ([sig_lbl] if sig_lbl else []))
            candidates.append({
                "symbol": sym,
                "code": sym[2:],
                "name": str(raw_c.get("name") or sym[2:]),
                "board": board_lbl,
                "close": close_px,
                "pct": pct_v,
                "amountYi": amt_v,
                "consecZt": czt_v,
                "ret5": float(stk["ret5"]) if stk else 0.0,
                "ret20": float(stk["ret20"]) if stk else 0.0,
                "ma5": ma5_v,
                "score": round(min(99.0, score_v), 1),
                "matchTags": tags[:3],
                "entryPlan": sig_reason or profile["buyWindow"],
                "stopHint": f"跌破 MA5 ({ma5_v:.2f}) 或亏损超 {abs(pre_lim['stopLossPct']):.1f}% 止损",
            })
        candidates.sort(key=lambda x: (-x["score"], -x["amountYi"]))
        pool_label = external_pool_label or f"策略运行同源候选池（共 {len(candidates)} 只）"
    else:
        candidates, pool_label = _scan_style_candidates(
            owner, root, skey, snap, chosen_date, candidate_limit=max(12, int(params.get("maxPositions", 5)) * 2 + 4)
        )
    cand_inputs = [
        {
            "symbol": c["symbol"],
            "code": c["code"],
            "name": c["name"],
            "score": c["score"],
            "close": c["close"],
            "boardCode": board_of(c["symbol"]),
        }
        for c in candidates
    ]
    for c in candidates:
        if c["symbol"] not in plan_feats:
            plan_feats[c["symbol"]] = {
                "close": c["close"],
                "high": c["close"],
                "pct": c["pct"],
                "ma5": c.get("ma5") or c["close"],
                "aboveMA5": c["close"] >= (c.get("ma5") or c["close"]),
                "aboveMA20": True,
                "isZt": c.get("consecZt", 0) >= 1,
                "isDt": False,
                "bombedZt": False,
                "consecZt": c.get("consecZt", 0),
                "board": board_of(c["symbol"]),
            }

    plan = plan_next_day_orders(
        style_key=skey,
        params=params,
        env=snap,
        equity=total_equity,
        cash=avail_cash,
        holdings=holding_inputs,
        features_by_sym=plan_feats,
        candidates=cand_inputs,
    )
    lim = plan["limits"]
    proj_sum = plan["projectedSummary"]
    orders_by_sym = {o["symbol"]: o for o in plan["orders"]}

    # Sync each holding card directly with the exact computed order quantities, weights & 4-scenario playbook
    for act in holding_actions:
        if total_equity > 0 and act["marketValue"] > 0:
            act["weightPct"] = round(act["marketValue"] / total_equity * 100.0, 1)
        act["stopPrice"] = round(act["averageCost"] * (1.0 + lim["stopLossPct"] / 100.0), 2) if act["averageCost"] > 0 else act["stopPrice"]
        ord_row = orders_by_sym.get(act["symbol"])
        if ord_row:
            act["plannedOrder"] = ord_row
            act["orderSide"] = ord_row["side"]
            act["orderActionType"] = ord_row["actionType"]
            act["orderQuantity"] = ord_row["quantity"]
            act["estAmount"] = ord_row["estAmount"]
            act["afterWeightPct"] = ord_row["afterWeightPct"]
            verb = "买入" if ord_row["side"] == "buy" else "卖出"
            act["actionLabel"] = f"{ord_row['actionLabel']} · {verb} {ord_row['quantity']:,} 股"
            act["targetPositionChange"] = (
                f"{verb} {ord_row['quantity']:,} 股（约 ¥{ord_row['estAmount']:,.0f}），"
                f"单票仓位 {ord_row['beforeWeightPct']:.1f}% → {ord_row['afterWeightPct']:.1f}%"
            )
            act["executionWindow"] = ord_row["execWindow"]
            act["plainReason"] = ord_row["reason"]
            act["urgency"] = "high" if ord_row["actionType"] in ("cut_stop", "cut_weak", "sell_rotate", "trim_regime") else "medium"
        else:
            act["plannedOrder"] = None
            act["orderSide"] = "hold"
            act["orderActionType"] = "hold"
            act["orderQuantity"] = 0
            act["estAmount"] = 0.0
            act["afterWeightPct"] = act["weightPct"]
            act["actionLabel"] = f"锁仓持有 · {int(act['quantity']):,} 股"
            act["targetPositionChange"] = f"仓位维持 {act['weightPct'] or 0:.1f}% 不动（未触发止损/减仓/加仓阈值）"
            act["urgency"] = "low"
        act["scenarioPlaybook"] = _build_holding_scenario_playbook(act, lim, snap, skey, total_equity)

    # Sort highest urgency first (high -> medium -> low)
    urg_rank = {"high": 0, "medium": 1, "low": 2}
    holding_actions.sort(key=lambda x: (urg_rank.get(x["urgency"], 9), -(x["weightPct"] or 0)))

    new_pos_plan = _build_new_position_plan(
        skey, profile, snap, lim, plan, candidates, total_equity, avail_cash
    )
    timeline_checklist = _build_tomorrow_timeline(holding_actions, new_pos_plan, lim, snap)

    current_pos_pct = round(current_mv_total / total_equity * 100.0, 1) if total_equity > 0 else 0.0

    # Concrete execution summary verdict (driven directly by the rule engine output)
    n_ord = len(plan["orders"])
    if n_ord > 0:
        pos_verdict = (
            f"已按「{snap['stageLabel']}」代码硬约束（总仓 ≤{lim['totalPosCapPct']:.0f}% · 单票 ≤{lim['singleCapPct']:.0f}% · 最多 {lim['maxSlots']} 仓）"
            f"直接生成 {n_ord} 笔明日预测交割单：预计总仓位 {proj_sum['beforePosPct']:.1f}% → {proj_sum['afterPosPct']:.1f}%"
            f"（持股 {proj_sum['beforeCount']} → {proj_sum['afterCount']} 只，余现金 {proj_sum['afterCashPct']:.1f}%）。"
        )
    elif proj_sum["afterCount"] == 0:
        pos_verdict = (
            f"当前空仓（0%）且处于「{snap['stageLabel']}」环境（总仓上限 {lim['totalPosCapPct']:.0f}% / 最多 {lim['maxSlots']} 仓）。"
            f"按代码规则今日无达标开仓委托，明日继续空仓保留 100% 现金。"
        )
    else:
        pos_verdict = (
            f"当前持仓 {proj_sum['beforeCount']} 只（总仓位 {current_pos_pct:.1f}%），均在「{snap['stageLabel']}」硬上限"
            f"（总仓 ≤{lim['totalPosCapPct']:.0f}% · 单票 ≤{lim['singleCapPct']:.0f}%）以内且未触发止损/减半/加仓条件，明日默认锁仓，盘中按下方涨停/冲高/破位剧本执行。"
        )

    # Dynamic Wujiang board-switch alert
    special_alert = ""
    if skey == "wujiang_dynamic":
        to_yi = snap.get("turnoverYi") or 0
        kc_abv = snap.get("kc50AboveMA20")
        if kc_abv is True and to_yi >= 20000:
            special_alert = f"【乌江望月赛道开关 → 科技主升】科创50>MA20 且两市成交 {to_yi:.0f} 亿（≥2万亿），代码已放行 20cm 科技趋势核心选股。"
        else:
            special_alert = f"【乌江望月赛道开关 → 主板防守】科创50 {'<MA20' if kc_abv is False else '偏弱'}、两市成交 {to_yi:.0f} 亿，代码已自动拦截科创20cm新开并在退潮期清退弱势旧科技。"

    ovr = overrides or {}
    return {
        "styleKey": skey,
        "masterName": (master or {}).get("name") or profile["name"],
        "accountSource": ovr.get("accountSource") or "account",
        "accountName": ovr.get("accountName") or "",
        "profile": {
            "name": profile["name"],
            "tagline": profile["tagline"],
            "buyWindow": profile["buyWindow"],
            "sellWindow": profile["sellWindow"],
            "timingReason": profile["timingReason"],
            "stopLossPct": lim["stopLossPct"],
            "redLines": profile["redLines"],
        },
        "marketSnapshot": snap,
        "activeRules": describe_active_rules(skey, total_equity, params),
        "activeLimits": lim,
        "predictedOrders": plan["orders"],
        "projectedHoldings": plan["projectedHoldings"],
        "projectedSummary": proj_sum,
        "blockedSignals": plan["blockedSignals"],
        "timelineChecklist": timeline_checklist,
        "newPositionPlan": new_pos_plan,
        "directive": {
            "recommendedTotalPos": f"≤ {lim['totalPosCapPct']:.0f}%",
            "maxSinglePosPct": lim["singleCapPct"],
            "maxHoldings": lim["maxSlots"],
            "boardFocus": reg["board"],
            "currentPositionPct": current_pos_pct,
            "accountEquityBasis": round(total_equity, 2),
            "availableCashBasis": round(avail_cash, 2),
            "positionVerdict": pos_verdict,
            "specialAlert": special_alert,
        },
        "holdingActions": holding_actions,
        "candidates": candidates,
        "candidatePoolLabel": pool_label,
        "positionSplittingGuide": _build_position_splitting_guide(skey, total_equity),
        "availableStyles": [{"key": k, "name": v["name"], "tagline": v["tagline"]} for k, v in STYLE_PROFILES.items()],
        "disclaimer": "本推演严格基于历史交割单提炼的硬编码定量规则进行教学模拟，不构成任何投资建议；A股实盘存在跳空、涨跌停流动性与突发消息风险，盈亏自负。",
    }
