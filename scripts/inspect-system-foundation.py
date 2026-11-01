"""Compact evidence receipt for system inputs; no strategy-return claim."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("_system_foundation_receipt_utils", ROOT / "scripts/run-liquid-trend-breakout.py")
U = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(U)
BASE = ROOT / "artifacts/strategy-search/market-sector-stock-system-v1"
OUT = BASE / "foundation-evidence-v1"


def main():
    value = U.read(BASE / "foundation-readout.json")
    source, prices = U.read(U.bound(value["publishedSnapshotProof"])), U.read(U.bound(value["priceMatrixProof"]))
    if not source["passed"] or not prices["passed"] or value["completeSystemValidated"] or value["newMarketAccounts"] != 0:
        raise ValueError("Verified source foundation, with no account-outcome promotion, required")
    if OUT.exists():
        raise ValueError("Foundation receipt already exists")
    OUT.mkdir()
    cohorts = {r["publicationDate"]: r for r in value["snapshotCoverage"]}
    dates = [{"公布日期": r["publicationDate"], "最早用于决策": cohorts[r["publicationDate"]]["availableFrom"], "发布名单证券数": r["members"],
        "行业大类数": r["industries"], "固定股票池归属已知": cohorts[r["publicationDate"]]["cohortKnown"],
        "固定股票池归属未知": cohorts[r["publicationDate"]]["cohortUnknown"], "代码还原记录数": r["codeRecoveries"], "原始名称差异记录数": r["rawLabelDifferences"]} for r in source["sourceSnapshots"]]
    boards = {"main": "主板来源", "chinext": "创业板来源", "star": "科创板来源"}
    table = [{"原始数据组": boards[k], "保留股票数": r["stocks"], "原始日线数": r["sourceBars"], "逐项核验数值数": r["sourceBars"] * 6} for k, r in prices["sourceCounts"].items()]
    methods = U.read(U.bound(value["protocol"]))["matchedPrimaryAccounts"]
    controls = [{"账户": r["id"], "用途": r["scope"], "状态": "已固定研究假设；账户未执行"} for r in methods]
    files = ["artifacts/strategy-search/market-sector-stock-system-v1/foundation-readout.json", value["publishedSnapshotProof"]["path"],
        value["priceMatrixProof"]["path"], value["protocol"]["path"], value["readableRules"]["path"],
        source["tests"]["path"], "service/research_sector_snapshots.py", "service/research_capco_snapshot_tables.py"]
    src = {"label": "行业公布时点、逐股归属与统一价格输入验证", "files": files,
        "metricDefinitions": [{"label": "归属可用时间", "definition": "只用公布日期之后的首个独立日历交易日起生效的完整行业名单。公布日不用新名单，缺失股票保持未知。"},
            {"label": "行业", "definition": "同一期中上协原始文件中的门类加大类代码，两个排序交叉核对；没有把申万或通达信分类拼进来。"},
            {"label": "价格核验", "definition": "4999只既定股票的六个原始字段，按独立证券/日期二分定位，与存储矩阵逐项精确核验。缺日保留NaN，未补值。"},
            {"label": "执行设计", "definition": "五个主区间账户与一个弱市压力账户，均为待实施验证的完整账户设计，本数据阶段没有新增市场账户或策略试验次数。"}],
        "caveats": ["数据层通过不表示整套交易体系达标；完整体系尚无市场账户结果。",
            "固定的当前幸存股票池、原始价模型及历史可交易性限制保留，不代表完整时点全A股。",
            "使用当前官方迁移的历史发布档案，缺原发布日字节档案；源文档截断与名称差异均留档，代码还原有明确来源记录。",
            "数据截止2026-10-15，缺2026-10-16。历史已被观察，不称为纯净样本外。"],
        "evidenceFlow": [{"kind": "validation", "title": "行业时间与代码", "body": "18项边界与解析检查通过，6期名单的股票/大类/次类代码核验，1259个交易日逐日成员图独立重建。"},
            {"kind": "calculation", "title": "统一股票池", "body": "三个上市板来源合为一个4999只股票矩阵，36601974个原始数值逐项一致，用户工作区112份JSON保持不变。"}]}
    queries = []
    for identifier, rows, period, note in (
            ("published-snapshot-availability", dates, "2024-04-03至2026-09-30发布档案", "名单证券数包含来源本身的证券范围；固定研究股票池另列已知和未知，不能混作全A股覆盖。"),
            ("whole-cohort-price-audit", table, "2021-08-02至2026-10-15，含热身数据", "三个上市板只表示原始数据来源，后续在同一账户内按行业竞争资金。"),
            ("fixed-system-account-design", controls, "主要评价2024-09-24至2026-10-15", "五个待执行主账户，另有一个弱市压力账户；这里没有收益结果。")):
        queries.append({"id": identifier, "source": src, "rows": rows, "columns": list(rows[0]), "reportingPeriod": period,
            "preview": {"kind": "aggregate", "note": note, "totalRows": len(rows)}})
    U.write(OUT / "sources.json", {"schemaVersion": 1, "items": [{"id": "integrated-system-foundation", "title": "整套体系的数据基础与固定实验设计", "queries": queries}]})
    U.write(OUT / "presentation.json", {"builder": U.ref(Path(__file__)), "foundation": U.ref(BASE / "foundation-readout.json"),
        "sources": U.ref(OUT / "sources.json"), "newMarketAccounts": 0, "newStrategyTrials": 0, "completeSystemValidated": False})
    U.emit({"sourceQueries": len(queries), "rows": sum(len(q['rows']) for q in queries), "completeSystemValidated": False})


if __name__ == "__main__":
    main()
