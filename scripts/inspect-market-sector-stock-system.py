"""Sources receipt separating a proposed system from archived component evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1"
OLD = ROOT / "artifacts/strategy-search/reclaim-entry-volatility-cap-v1"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def ref(path):
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main():
    preflight = read(OUT / "preflight.json")
    direction = ROOT / "docs/market-sector-stock-system-v1.json"
    framework = ROOT / "docs/market-sector-stock-system-v1.md"
    if preflight["direction"] != ref(direction) or preflight["framework"] != ref(framework):
        raise ValueError("Registered framework changed")
    if preflight["completeSystemValidated"] or preflight["completeSystemMarketAccounts"] != 0:
        raise ValueError("This receipt describes a framework, not a validated complete system")
    rows = [
        {"层次": "市场", "问题": "什么时候适合进攻", "方法": "总成交额与持续性、宽基趋势、上涨扩散共同决定风险预算", "状态": "候选规则；完整体系待验证"},
        {"层次": "行业或题材", "问题": "资金聚焦哪里", "方法": "相对强度、成交参与度、成员广度及持续性确认主线与退潮", "状态": "历史归属及分类一致性核验中"},
        {"层次": "个股", "问题": "板块中买谁以及何时买", "方法": "分别标记领涨突破、强势回踩和有先行关系及自身启动证据的补涨", "状态": "候选规则；完整体系待验证"},
        {"层次": "组合与轮动", "问题": "机会如何占用同一笔资金", "方法": "统一现金与风险预算，限制同板块及重叠题材敞口，新板块独立确认后接力", "状态": "具体账户契约待冻结"},
        {"层次": "退出与验证", "问题": "怎样小亏并保留大盈利，如何验收", "方法": "失效位和跟踪保护连接入场与仓位；连续账户、宽基/行业对照及逐层移除版本比较", "状态": "完整体系尚无市场账户证据"},
    ]
    files = [ref(framework)["path"], ref(direction)["path"], ref(OUT / "preflight.json")["path"]]
    source = {"label": "用户最新体系要求与已保存研究框架", "files": files,
        "metricDefinitions": [{"label": "研究单位", "definition": "市场、行业/题材、个股、轮动组合及退出构成一个完整账户流程；上市板分组不能代替行业板块。"},
            {"label": "验证设计", "definition": "完整体系与只移除市场、行业筛选或轮动分支的账户对照，使用同日期、股票池、资金及成本。比较完整账户，差值不直接相加作因果归因。"}],
        "caveats": ["这五层是待验证的方法框架，整套体系尚未运行市场账户，不是已实现超额收益的结论。",
            "历史行业记录与指数数据已开始核验，生效/可知日期、分类版本和跨来源映射仍需确认。",
            "2024-09-24之后是主要评价区间，之前不要求盈利。已反复观察的历史不能称为纯净样本外。",
            "下方12行是旧组件实验的6个新账户及6个复用对照，不是这套体系的验证结果。"],
        "evidenceFlow": [{"kind": "validation", "title": "范围与状态", "body": "Goal执行路线已根据用户指示改为完整体系。原组件实验完成归档，研究工作区JSON保持不变。"}]}
    item = {"id": "integrated-system-framework", "title": "市场、行业、个股与轮动体系的研究路线", "queries": [{
        "id": "five-layer-method", "source": source, "rows": rows, "columns": list(rows[0]),
        "reportingPeriod": "2026-10-18研究方向更新",
        "preview": {"kind": "aggregate", "note": "候选方法及当前验证边界，不能作为盈利承诺。", "totalRows": 5}}]}
    previous = read(OLD / "sources.json")
    write(OUT / "sources.json", {"schemaVersion": 1, "items": [item, *previous["items"]]})
    write(OUT / "presentation.json", {"builder": ref(Path(__file__)), "completedAt": datetime.now(timezone.utc).isoformat(),
        "preflight": ref(OUT / "preflight.json"), "sources": ref(OUT / "sources.json"), "componentSources": ref(OLD / "sources.json"),
        "newMarketAccounts": 0, "newStrategyTrials": 0, "completeSystemValidated": False})
    print(json.dumps({"sourceItems": 2, "methodRows": 5, "archivedComponentRows": 12, "completeSystemValidated": False}))


if __name__ == "__main__":
    main()
