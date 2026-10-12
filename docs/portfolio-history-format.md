# 历史股票池数据包与权益模型

入口为桌面“策略与回测 → 回测工作区 → 导入历史研究 JSON / JSONL 数据包”。导入后选择已保存来源，并在软件设置其覆盖范围内的研究日期、股票池、策略和资金。源文件不被修改；同内容重复导入不生成多个来源。

## 包头与数据

包的 `schema` 必须为 `historical-package-v1`。`coverage.startDate/endDate` 是完整历史证据覆盖期；`declaration` 需要明确 `universe`、`source`、独立的 `calendarSource`，并声明 `completeMembership=true` 和 `completeActions=true`。这些声明由数据提供方负责；软件能检查内部一致性，不能独立证明数据商从未漏掉证券或事件。

`dataset` 字段：

| 字段 | 要求 |
| --- | --- |
| `adjustment` | `raw`，成交只能使用未复权价格 |
| `calendar` | 独立、顺序唯一、覆盖全部日线日期的交易日历，可含预热日期 |
| `membershipByDate` | 覆盖期每个交易日的完整历史成分代码；含当时存在、后来退出或退市的证券 |
| `securities` | 所有涉及证券的记录；代码为 `sh600000`、`sz000001`、`bj920001` 等规范格式 |
| `benchmark` | 可选 `{symbol,bars:[{date,close},…]}`；界面指定的基准必须与包内代码一致 |

每只证券提供 `symbol`、`listingDate`、可选 `delistDate`、`bars`、`stByDate`、`tradeStatusByDate`、`precloseByDate`、`corporateActionsComplete=true` 及 `corporateActions`。每日 `bars` 要有 `date/open/high/low/close/volume`，`amount` 单位为元、`volume` 为股。停牌日也需来自数据源的可追溯估值记录，成交状态设为 `0`；不能自行复制价格再声明已验证。`stByDate` 必须是真正布尔值，`tradeStatusByDate` 为 `0/1` 或布尔值；每个当日成分的证据都需完整。

参考昨收指当日实际除权除息参考价。它与前一原始收盘的差异必须对应声明的权益事件。策略技术价格使用当时已知参考价向前因果衔接，不能将后来复权结果提前写入信号；成交价始终保持原始值。

格式示例 [portfolio-history-package.json](examples/portfolio-history-package.json) 仅使用两只合成证券与四个交易日，演示历史成分退出和现金权益。它可用于检查导入格式，没有足够长度检验策略、持续性或平原；不能当真实行情使用。

## JSONL / NDJSON

普通 JSON 限 512 MiB；JSONL/NDJSON 限 2 GiB，单条记录最多 16 MiB。超限明确拒绝，绝不只取前 120 只。JSONL 第一行是完整包头与 `dataset.calendar`，可包括基准；证券和成分随后逐行写入：

```json
{"security":{"symbol":"sh600000","listingDate":"2000-01-01","bars":[],"stByDate":{},"tradeStatusByDate":{},"precloseByDate":{},"corporateActionsComplete":true,"corporateActions":[]}}
{"date":"2024-01-02","members":["sh600000","sz000001"]}
```

上面只是记录形状，实际空日线无效。日期不得重复，证券不得重复，成分不能引用没有记录的证券。导入会读取并校验整个声明范围；保存时按证券和日期分块，研究读取相应证券及预热历史。长区间仍有真实内存和计算成本，分块不意味着无限资源。

## 权益事件

每个事件必须提供证券内唯一 `id`、`source`、`announcementDate`、`recordDate`、`exDate`，满足公告日 ≤ 登记日 < 除权日。类型如下：

| `type` | 金额与股份字段 | 日期 |
| --- | --- | --- |
| `distribution`（默认） | `cashNetPerShare`、`bonusSharesPerShare`、`rightsSharesPerShare`、`rightsPrice` | 有现金需 `cashPayDate`，有新增股份需 `shareListingDate`，有配股需 `rightsPayDate` |
| `cash_settlement` | 仅 `cashNetPerShare`，用于明确的退市现金结算 | `cashPayDate` |
| `rights_refund` | `rightsSharesPerShare` 与正 `rightsPrice`，无分红或送股 | `rightsPayDate` 为认购付款，`exDate` 此类型表示结果确认日，`cashPayDate` 为退款到账 |

比率按每一登记股计算，例如每 10 股送 2 股应写 `bonusSharesPerShare=0.2`。现金必须是声明的每股税后净额，`cashTaxModel=declared_net`。公告通常公布税前现金，不能直接当成个人税后到账额。个人差别化股息税还涉及持股期限与扣缴时点，当前模型不自动还原这一过程；规则来源为 [税务总局财税〔2015〕101 号](https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html)。

登记收盘持仓决定权益。除权日确认应收现金并调整股份与成本；应收现金计入净值，到真实付款日才成为可支用现金。送股保持总成本，股份上市前不可卖。配股可设置“不认购”或“资金足够时认购”；付款可以早于除权，先形成已付款待配股资产，成功时转入股份、失败时转为待退款现金，不利用最终成功与否决定前期是否付款。未来付款的配股会锁定所需现金。日期从公告提供，不硬编码固定间隔；可参考 [上交所权益分派公告](https://star.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-06-09/688069_20260609_3CK3.pdf) 与 [深交所配股缴款及退款示例](https://disc.static.szse.cn/disc/disk01/finalpage/2010-03-09/1073b1a2-b84c-4319-870d-487fe934f8a6.PDF)。

当前未支持非整数股份的登记结算分配；遇到这种持仓会列出未核算权益并阻止认证，不静默取整。更复杂的换股、合并或没有明确现金结算的退市也不能伪装成已支持事件。应使用完整公告/供应商适配器补足模型后再认证。

## 质量与验证边界

认证同时要求独立日历、时点成分、历史状态、完整权益声明、覆盖范围一致，且实际持仓没有陈旧估值、隔离证券或未核算权益。`certificateScope=internallyValidatedDeclaredUniverseAndNetCashModel` 明确认证范围；包内完整性、来源真实性、策略统计有效性是不同问题。严格模式缺事实则排除，存在关键缺口则不给认证或参数推荐。

软件保留同工作区样本外使用记录；同区间改参再跑会成为开发样本。正式验收需使用此前未用区间，具体门控见 [系统研究协议](portfolio-system-research.md)。
