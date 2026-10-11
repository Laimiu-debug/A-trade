# 监管与期限追踪（T06）

用户明确“异动追踪”用于查看进入监管／重点监控名单、临近来源截止的证券。T06 使用公开名单，
与本地涨跌幅、放量、突破候选分开；旧 `market.analysis.moves` 仅兼容保留，不在 T06 展示为监管。
名单覆盖有限，空列表不代表市场没有受监控证券。实现位于 `service/regulatory_watch.py`，交易日历位于
`service/trading_calendar.py`，页面位于 `preview/regulatory-watch.js`。

## 来源与筛选

| 来源 | 公开页面与接口 | 采用口径 |
| --- | --- | --- |
| 东方财富证券 | [重点监控专题](https://vipmoney.eastmoney.com/collect/min_data/point_stock_monitor/index.html) · [JSON 名单](https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json) | 数组中的证券及 `VALIDATESTARTDATE/VALIDATEENDDATE`；页面将结束日期标为预计截止，`category:focus`、`endDateKind:expected` |
| 广发证券 | [监控证券公示](https://minisite.gf.com.cn/?src=other) · [公开接口](https://store.gf.com.cn/mixturebusiness/monitor/stock_list) | `retcode:0` 的 `data` 中有效记录；按 `key_monitor_stock_type` 精确筛选，日期取 `monitor_start_date/monitor_end_date`，`endDateKind:disclosed` |

广发的 `focus` 只收录公开重点监控、监管提示函、交易所向会员发出的风险通知／短信相关类型。
`GF_FOCUS` 保留“本所／交易所”及逗号差异对应的 6 个原始枚举；`severe` 只收录上海、深圳严重异常波动的 2 个枚举。
普通异常波动、常规 ST／风险警示及退市整理类型排除。按类型过滤，不因证券名称带 ST 就排除明确的监控记录。
明确列示的 ETF／LOF 等基金保留并标识 `securityType:fund`。`2099-12-31`、`9999-12-31` 哨兵日期视为未披露，不生成漫长倒计时。

每条记录保留来源、类型、起始日、截止日及可用公告链接；广发保留原始类型，东方财富统一标为券商公示重点监控证券。同一证券的不同来源、类型或起始日分行展示，
不取最早／最晚期限合并为单一结论。记录 ID 由来源、证券、原始类型与起始日确定；同一记录截止日改变时保存
`periodChanged/previousEndDate`。来源条数与去重后的证券数分别显示。

## 期限和交易日

严重异常波动与公开重点监控名单是不同依据。[深交所主板异常交易实时监控细则](https://docs.static.szse.cn/www/lawrules/rule/stock/W020230217570611262391.pdf)
第 28／29 条的 10 个交易日针对严重异常波动后的申报速率异常监控，第 31 条另列公开重点监控股票；
不能据此推成全部监控证券一律第 10 日自动解除。[上交所科创板相关细则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20250519_10779400.shtml)
及 [上交所市场运行情况](https://www.sse.com.cn/aboutus/mediacenter/conference/)、[深交所监管动态](https://www.szse.cn/disclosure/supervision/dynamic/)
可供核对规则和公开通报，但本次接入的券商名单不等同于交易所完整监管池。

使用 UTC+8 的当前日期。剩余交易日不含今天、包含来源截止日：
`remainingSessions = sessions(max(startDate, today + 1), endDate)` 的数量。
总期数包含起止日；已计交易日包含起始日和今天，最多计至截止日。剩余为 0 仍须区分当日截止和已经届满。
目前仅核验 2025／2026 年年度休市安排；相关区间涉及未知年份时，该交易日数量返回 `None`（JSON `null`），
不以工作日估算。周末调休上班不视为交易日，临时休市或后续延期仍需更新依据。

日历依据：[上交所 2025](https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml)、
[深交所 2025](https://www.szse.cn/disclosure/notice/t20241223_611283.html)、
[上交所 2026](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)、
[深交所 2026](https://www.szse.cn/disclosure/notice/general/t20251222_618087.html)、
[北交所 2026](https://www.bse.cn/important_news/200027428.html)。

| API 状态 | 含义 |
| --- | --- |
| `active` | 当前日期处于来源所列期限内 |
| `expiring` | 来源列示期内，剩余 0～3 个已核验交易日 |
| `upcoming` | 尚未到来源起始日 |
| `elapsed` | 已超过来源截止日，待核验是否解除或延期 |
| `unknown` | 起止日未完整披露，无法确认期限 |
| `unlisted` | 本次成功读取的同一来源已不列示此记录，待核验原因 |

这些是来源记录状态，没有“已解除”状态。东方财富的预计截止、广发披露截止、期限届满和来源移除均不能证明解除监管。
页面按状态、重点监控／严重异动、市场与名称／代码筛选，列排序支持正序、倒序、复原；查看来源、个股行情和加入跟踪池均有独立入口。

## 接口、缓存与历史边界

`market.regulatory.list` 只接受可选布尔值 `refresh`。`asOf` 和其他参数拒绝：当前公开名单不能倒灌过去的研究日期。
结果包含当前 `asOf`、各来源 `sources`、采集时间、缓存及错误状态、`items/counts`、日历来源。
记录包含 `firstSeenAt/lastSeenAt/receivedAt`、`listed`、日期、分类、倒计时与来源链接；这些时间表示本软件的采集观察，不能代替公告发布日期。

各来源独立缓存 300 秒并保存到本机 SQLite。完整解析通过后才更新；来源失败或结构不兼容保留旧数据、旧采集时间，标明 `stale` 和错误。
一个来源失败不清空另一个来源，也不将失败当作名单移除。成功响应为空或移除某条时，该源旧记录标为 `unlisted`，保留最近 90 天的观察痕迹；
期限仍按当前日期计算，但保留缓存过期提示。现阶段不构成完整历史快照库，不保证获得所有曾进入或退出监管的证券。
请求仅访问固定公开名单接口，无需券商登录，不涉及账户或交易操作。

## 验证记录

2026-10-11 验证完成：

- `npm test`：112 项通过，含监管筛选、三态排序、预计与披露日期、缺失、外链和每行缓存标记。
- `npm run test:business`：257 项，255 项通过，2 项因 Windows 符号链接权限跳过；其中监管 19 项、交易日历 3 项。
- `npm run test:analysis`：真实 Electron / IPC / Python、隔离 HTTP 夹具的写入及重开读取通过；覆盖监管名单、严重异动分类、临近期满筛选和断线后保留旧名单。
- 真实公共接口、隔离桌面工作区实测：东方财富 17 条、广发筛选后 22 条，共 39 条期限记录、20 只证券；20 条临近期满、15 条其他期内、4 条待生效。条数包含不同来源及不同监控期，不能当作 39 只股票。
- 真实桌面检查包含无需配置通达信即可加载、类型和状态筛选、名称代码搜索、正序／倒序／复原、来源链接及 900 像素窄窗口。控制台与未处理异常均为 0。来源链接通过系统打开接口验证，测试拦截了最终浏览器启动。

证据为 `artifacts/regulatory-actual-results.json` 和 `artifacts/regulatory-actual*.png`；这些数量是上述采集时点的来源结果，后续会变化。
