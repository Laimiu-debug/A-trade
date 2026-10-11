# 公开资讯、财务与行业资金

`service/market_information.py` 提供无密钥的只读公共信息适配器。数据取自东方财富公开网站对应接口，使用 Python 标准库，不调用旧项目运行环境、用户配置或账户。旧 TRADE 的资讯过滤与 FINAL-TRADE 的财务接口仅作为实现参考；不沿用用动态/静态市盈率替代 TTM、或从市值和市盈率倒推利润的做法。

## 服务契约

服务类 `MarketInformation(owner, fetcher=None)` 使用 `owner.db` 的 SQLite 连接。桌面路由对应以下方法：

| 路由 | 方法 | 请求 |
| --- | --- | --- |
| `market.news.list` | `news_list` | `source: all/eastmoney/announcements`，可选 `query`、`symbol`、`page`、`refresh` |
| `market.finance.snapshot` | `finance_snapshot` | `symbol`，可选 `refresh` |
| `market.finance.screen` | `finance_screen` | 可选 `query`、`industry`、`positive`、`maxPe`、`below`、`scenario`、`sortBy`、`sortOrder`、`page`、`pageSize`、`refresh` |
| `market.finance.benchmark` | `finance_benchmark` | `symbol`，可选 `refresh` |
| `market.funds.sectors` | `funds_sectors` | `period: today/5d/10d`，可选 `refresh` |
| `market.funds.history` | `funds_history` | `id: BKxxxx`，可选 `limit: 30/60/120/250`（默认 60）、`refresh` |

证券代码采用 `sh600900`、`sz000001`、`bj920047` 格式。页面从 1 开始，资讯每页 20 条，财务每页默认 20、最多 30 条。未知参数、非法代码和周期在联网前拒绝。资讯查询在已取得的近期条目中筛选，不宣称全历史检索；财务查询在完整的最新 A 股财报目录中匹配名称/代码，行业为来源实际行业名称的精确匹配。

通用响应字段：`provider`、`sourceName`、`sourceUrl`、`receivedAt`、`cached`、`stale`、`status`（`ready` / `empty` / `unavailable`）、`warnings`、`errors: [{provider,message}]`。`receivedAt` 是采集时间，不替代新闻发布时间、财报日期或行情时间。`empty` 表示有效响应中没有匹配项目；`unavailable` 表示请求失败且没有有效缓存。

### 资讯

`items` 每条包含 `id/title/summary/url/publishedAt/sourceName/symbol`，公告另外包含 `announcementDate`。返回 `page/pageSize/total/hasMore`；合并来源时 `sources` 独立列出每个来源状态。

- [东方财富快讯](https://kuaixun.eastmoney.com/)：一次最多读取近期 150 条。原文链接使用已验证的 `https://finance.eastmoney.com/a/<资讯编号>.html`，不拼接会返回 404 的快讯站 `/a/` 路径。
- [东方财富公司公告](https://data.eastmoney.com/notices/)：一次最多读取近期 100 条，可按股票筛选。标题、展示时间和公告日期分开保留，摘要为空时保持为空，原文链接指向公告详情。
- 去除来源摘要中的 HTML、脚本与样式，不自动生成正文或 AI 摘要。公司公告不是第二家独立新闻机构；两个接口均属于东方财富。

### 财务与 TTM

单股响应和筛选 `items` 包含 `symbol/name/industry/ttmNetProfit/reportDate/announcementDate/ttmMethod/components/missing/units`。利润、市值单位均为元，价格为元/股。`components` 保留实际报告期、公告日期、原始累计归母净利润和加减符号，便于核对。

已披露年报直接采用全年归母净利润；其他季度采用：

```
TTM 归母净利润 = 本年累计归母净利润 + 上年全年归母净利润 - 上年同期累计归母净利润
```

缺少任一必需财报、利润为无效值时返回 `ttmNetProfit: null` 和明确 `missing`，不会把单季或年初至今利润冒充 TTM。零与亏损保留实际数值。相同报告期多次披露时采用已公告的较新记录，未来公告不接纳。来源为[东方财富业绩报表](https://data.eastmoney.com/bbsj/)的数据中心 `RPT_LICO_FN_CPD`，取 `PARENT_NETPROFIT`；接口列的是累计值。

单股另返回 `marketCap/price/peTTM/quoteAsOf/quoteReceivedAt/quoteProvider/quoteSourceUrl/quoteCached/quoteStale`。即时市值来源失败时，自动改用[东方财富估值分析](https://data.eastmoney.com/gzfx/) `RPT_VALUEANALYSIS_DET` 的 `TOTAL_MARKET_CAP/CLOSE_PRICE/PE_TTM/TRADE_DATE`。此时 `quoteProvider` 为 `eastmoney_daily_valuation`，`quoteAsOf` 仅给实际交易日 `YYYY-MM-DD`，明确为日收盘快照，不伪造实时戳。两个估值来源都失败时保留原有效估值及其原时间，`quoteStale: true`；没有缓存则市值和价格保持 `null`。估值失败不影响有效财报展示。

跨行业筛选先整理完整 A 股最新财报目录，为全部候选补齐构造 TTM 所需的历史报告，再读取完整同一交易日的日收盘估值目录。常见报告期按 A 股财报目录批量读取，少数特殊报告期按实际所需证券过滤；不逐股读取行情。每一阶段校验分页总数、证券身份及日期，全部成功后才发布完整快照。单个证券缺少必需财报或市值时保留 `missing`，不以其他日期的价格或市值 / PE 倒推盈利填补。

**筛选和排序在完整候选中执行，然后分页。** `positive`、`below` 为布尔值，分别要求 TTM 为正、实际市值低于情景底座；`maxPe` 为正数或 `null`，口径为来源同日总市值 / 实际财报构造的正 TTM。`scenario` 仅接受 `growth/pe/water` 三个正数或空值，底座 = TTM × growth × pe × water；`below: true` 时三个系数必须有效。未填写情景时仍可按盈利和市盈率筛选。`sortBy` 接受 `symbol/name/industry/ttmNetProfit/marketCap/actualPe/scenarioGap/reportDate`，`sortOrder` 为 `asc/desc`；默认按证券代码升序，相同值按代码升序，缺失值始终排在末尾。情景底座仅反映用户假设，不是已验证的公允估值。

响应包含 `scope: complete_catalog`、`candidateTotal`（完整财报目录数量）、`total/filteredTotal`（筛选后数量）、`page/pageSize/hasMore`、`industries/catalogReceivedAt/asOf`。行内新增 `actualPe`、`scenarioGap`（底座 / 市值 − 1，以百分数表示）。`coverage` 的 `total/ttmAvailable/marketCapAvailable/comparable/excluded` 说明完整目录中财报、市值及正 TTM 可比覆盖；`excluded` 为不能形成有效正 PE 对照的数量，而非用户筛选排除数。每家公司保留各自报告期，`asOf/quoteAsOf` 给出共同估值日期。

筛选条件、排序与页码共用一个数据库完整快照，因此新分析进程和条件变更无需重复下载目录。任一来源分页失败、混入其他交易日或超出预算时不发布部分排名：保留上次完整快照、原日期及 `stale: true`；没有完整缓存则 `scope: unavailable/status: unavailable/items: []`。有效完整源目录内的个别字段缺失仍明确计入覆盖缺口。冷启动整理过程可通过 `owner.progress(stage, completed, total)` 报告阶段和页进度。

2026-10-11 核验公开接口能力：`RPT_VALUEANALYSIS_DET` 提供总市值、收盘价、PE(TTM) 和交易日，但未提供可直接采用的归母利润 TTM 字段；因此全候选继续使用 `RPT_LICO_FN_CPD` 原始累计财报构造 TTM。来源可按实际交易日返回完整估值目录，按财报期及来源 A 股证券类型返回完整历史财报目录。无需推测未核验的 TTM 字段，也不把来源 PE 的财务时点当作原始财报。

### 行业 PE 参考

`market.finance.benchmark` 返回 `symbol/name/industry/industryId/asOf/subjectPE`，以及 `medianPE/p25PE/p75PE/meanPE/sampleCount/totalCount/excludedNonPositiveCount/excludedMissingCount`。`items` 保留同行的证券身份、名称、`peTTM` 与实际同一交易日 `asOf`。该基准来自东方财富估值分析 `RPT_VALUEANALYSIS_DET`：先读取所选证券最新日收盘估值的 `BOARD_CODE/BOARD_NAME/TRADE_DATE`，再按该行业、该交易日读取完整同行样本，最多 2,000 只 / 4 页。行业分类与财报目录或通达信板块未必相同，不能仅按板块中文名拼接。

统计包括所选证券（`includesSubject: true`）；仅纳入有限且大于零的来源 **PE_TTM**。非正值（亏损或零）与缺失分别计数，保留在原始样本中但不进入分布。25%、50%、75% 分位使用排序位置 `(n - 1) × p` 的线性插值，均值为正 PE 的算术均值；不缩尾、不人为剔除高 PE。没有正样本时统计值为 `null`，不使用固定行业倍数。中位数是当前同行横截面的参考，不代表合理价格。

`historical` 为单独口径：官网 `RPT_VALUEINDUSTRY_DET` 同一 `BOARD_CODE`、不晚于 `asOf` 的最近 250 个来源交易日 **行业整体 PE(TTM)**。包含 `metric: provider_industry_pe_ttm`、`medianPE/p25PE/p75PE/meanPE`、`sampleCount/days/startDate/endDate/currentPE` 和升序 `items: [{date,peTTM}]`，同时带自己的来源、缓存与失败状态。它是行业整体倍数的时间分布，与上面的同日个股 PE 分布不同。历史 PE 失败不隐藏有效同行统计；不能将历史中位数称为公允倍数或保证回归目标。所有参数仅作可供用户主动采用的参考，不自动覆盖已输入的情景 PE。

### 行业资金

响应包含 `period/items/total/asOf/units/adapter`。每行包含 `id/name/changePercent/mainNetInflow/mainNetRatio/superLargeNetInflow/largeNetInflow/mediumNetInflow/smallNetInflow/asOf/url`。

来源为[东方财富行业资金](https://data.eastmoney.com/bkzj/hy.html)当前行业口径 `m:90 s:4`。主接口为公开行情明细；失败时改用该页面实际调用的官方图表接口 `https://data.eastmoney.com/dataapi/bkzj/getbkzj`，以板块 ID 连接各字段，不能按不同字段的排序位置连接。备用接口百分数字段为百分之一，除以 100；资金金额保持元，不把成交额当净流入。

| 周期 | 涨跌幅 | 主力净流入 | 主力净占比 | 超大单 / 大单 / 中单 / 小单 |
| --- | --- | --- | --- | --- |
| 当日 | f3 | f62 | f184 | f66 / f72 / f78 / f84 |
| 5 日 | f109 | f164 | f165 | f166 / f168 / f170 / f172 |
| 10 日 | f160 | f174 | f175 | f176 / f178 / f180 / f182 |

`mainNetInflow` 等金额为元；`changePercent`、`mainNetRatio` 为百分数数值，例如 `5.1` 表示 5.1%。日期读取来源 `f124`，不使用采集时间代替。补充字段失败或行业身份不匹配时该字段保持缺失并提示，关键净流入全部缺失时拒绝本次快照。图表字段由有界的多次读取组成，属于近同时快照，不保证逐字段原子一致。

资金流是来源按成交单大小估算的口径，主力指超大单与大单，不代表已识别真实机构账户。板块成分可能重叠，不应加总为全市场资金。列表的三个周期均为来源最近一期；休市时显示最近交易日。

### 板块资金日期序列

`market.funds.history` 使用[板块资金详情页](https://data.eastmoney.com/bkzj/BK0428.html)公开脚本实际调用的 `https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`，`secid=90.BKxxxx`、`klt=101`、`lmt=0`。固定主机路径，无任意 URL 请求。此处的公开网页 `ut` 为来源前端常量，不是用户密钥。

响应为 `id/name/requestedLimit/availableCount/total/asOf/startDate/endDate/units/items`，附通用来源与缓存状态。`availableCount` 为来源实际返回的总交易日数，`total` 为截取到所选数量后的实际条数；`items` 按日期升序，每项包含 `date/mainNetInflow/superLargeNetInflow/largeNetInflow/mediumNetInflow/smallNetInflow/close/changePercent`。全部净流入为 **元**，涨跌幅为百分数，收盘为板块指数点位。

官方 `fields2=f51..f65` 的前六列依次为日期、主力、小单、中单、大单、超大单；第十二、十三列分别为收盘指数与涨跌幅，不能套用列表字段顺序。每日值是该日净流入，不是 5/10 日累计快照。负数与零保留，缺失为 `null`，不补交易日、不插值、不将缺失视为零。身份不匹配、重复或未来日期、字段数量变化、主力金额全缺失时拒收，并保留原有效缓存和原采集时间。

来源可能少于所选天数：2026-10-11 电力板块原始联网响应实际提供 120 日（2026-04-13 至 2026-10-09）。选择 250 时仍只显示实际 120 日，并明确提示；不能宣称已经取得 250 日。不同长度共用一次源数据缓存。该公共接口可能直接断开连接；无有效缓存时显示不可用，既不借用其他行业数据，也不以价格/成交额生成资金曲线。

## 缓存与网络边界

- 新表 `information_cache` 位于现有业务数据库中，保留最多 300 个有效快照。所有来源、代码、筛选和周期使用隔离的缓存键；不读取旧软件数据库。
- 资讯、资金（含历史）、单股估值与财务快照缓存 5 分钟；跨行业完整快照、行业 PE 参考与历史 PE 缓存 1 小时；完整财报目录 6 小时。手动刷新绕过有效期。请求失败保留旧缓存与原采集时间，返回错误原因及 `stale: true`。
- 财报及同日估值目录分别最多 10,000 条 / 20 页，单组历史补充最多 40,000 条 / 80 页，单次响应最多 2 MiB。普通组合读取最多 30 个请求、60 秒预算；首次整理完整跨行业快照单独限制为 100 个请求、180 秒，子目录共享该总预算。单请求连接/读取超时 5 秒、总读取截止 12 秒。既不无限重试，也不绕过登录、验证码或访问限制。
- 仅固定 HTTPS 来源主机与路径；禁止重定向。证券身份、日期、分页完整性、行业身份、有限数值经过验证，结构变化不写成新有效数据。

## 验证

离线测试：`python -m unittest tests.test_market_information -v`。覆盖累计财报构造 TTM、年报/零/亏损/缺期、重复报告修订、未来公告、字段身份、新闻摘要与原文链接、分页范围、独立来源失败、缓存原时间、估值降级、资金周期与单位、按 ID 关联、缺失字段、固定来源与响应字节上限。

2026-10-11 本机只读联网实测：快讯 150 条、长江电力公司公告、A 股财报目录 5,580 家 / 128 个行业；官网资金备用接口当日、5 日、10 日均返回 128 个行业。即时行情明细在本机连接失败，日收盘估值和资金官网图表降级均成功。

同日全候选新实现使用隔离内存数据库验证：50 次请求、29.09 秒完成 5,580 家候选的完整快照，全部取得构造 TTM 所需财报；其中 5,572 家有 2026-10-09 同日市值，3,934 家同时具备正 TTM 和有效市值。全候选市值 / TTM 不超过 20 的结果为 890 家。随后更改上限、排序和页码，仅从完整缓存过滤，耗时 0.172 秒，没有新增请求。这是该时点的数据覆盖与本机耗时记录，不表示盈利预测或后续设备性能保证。

新增离线检查覆盖跨来源分页的全候选筛选与排序、缓存跨服务实例复用、后续页失败保留完整旧快照、缺失与亏损排除、参数联网前校验及阶段进度。实际独立 Python 进程测试使用临时日线与数据库，验证长分析期间跟踪池写入仍完成、取消退出计算进程，以及重启新计算进程后跟踪记录保持；不访问用户行情目录。

长江电力实测：2026 半年累计归母净利润 14,755,694,774.53 元 + 2025 年报 34,502,809,176.39 元 − 2025 半年累计 13,056,352,473.55 元 = TTM **36,202,151,477.37 元**；[该股估值来源](https://data.eastmoney.com/gzfx/detail/600900.html)给出的最近交易日为 2026-10-09、收盘价 28.99 元、总市值 709,333,631,586.84 元。以上为接口与计算验证样例，不是固定产品数据，后续显示以新的真实响应为准。

本轮扩展测试共 40 项，新增同日同行正 PE 分位与非正/缺失覆盖、不同日期或行业拒收、目录分页缺口、行业历史独立失败、原缓存时间保留、资金日序列实际字段顺序、元单位与负号、日期排序、缺值不补零、身份/重复/未来日期拒收、范围切换共用缓存，以及非法参数联网前拒绝。

2026-10-11 生产估值适配器实测：所选长江电力对应来源电力行业 `016007`，2026-10-09 有 109 个同行样本、94 个正 PE；中位数 30.155480、25% 分位 16.357285、75% 分位 53.320465，均值 84.643962（受极高 PE 样本影响）。独立行业历史源返回 2025-09-22 至 2026-10-09 共 250 日，中位数 31.453787。资金历史原始接口曾成功返回 120 日，后续同源请求亦出现连接断开，因此不能把该源描述为持续可用。


## 通达信成交额热度与龙头观察（2026-10-11）

核对本机 `H:/CODE/FINAL-TRADE` 后确认：`backend/app/core/sector_analyzer.py` 读取 32 字节 `.day` 记录；`sector_capital_flow.py` 的资金热度等于 `(当日成交额 / 含当日窗口均额 - 1) * 100`，其 `leaders` 是每日热度领先的板块。`market_momentum.py` 中的趋势龙头按窗口涨幅与平均成交额门槛筛选，没有独立的主力净流入接口。旧项目的固定行业合并表未直接复制；本应用显示当前所选目录的真实指数身份。

新增 `service/funds_activity.py`，与公开净流入接口独立：

- `market.funds.activity.list({refresh?})`：读取用户已选择的通达信目录，保留 `sh8802xx` 起的板块与分类指数以及 `sh881xxx` 行业细分指数，排除 `sh8800xx/sh8801xx` 统计指数。真实名称来自 TNF，读取各文件尾部 6 条计算最新 5 日热度。目录按最新观察日优先，旧记录标记 `stale` 并置后；不是东方财富行业目录。
- `market.funds.activity.history({symbol,kind,limit?,window?,asOf?,refresh?})`：`kind` 为 `sector/stock`；区间 30/60/120/250/750，均额窗口 5/10/20；前端目前统一使用 5 日窗口。响应 `items` 包括 `date/close/amount/amountAverage/amountDelta/activityPercent/changePercent/returnPercent`，成交额和额差单位为元，其余比例为百分数。
- 直接读取所选目录的日线，本地可用时不请求网络。只有本地日线失败且 MCP 为同一个目录时，才尝试整段 MCP 日线；不会拼接不同来源。没有配置目录时提示前往设置，不内置 `D:/TDX` 路径。
- `asOf` 先截断原始数据再计算并选取区间，龙头图跟随趋势筛选截止日期；不足完整均额窗口时热度为空，零成交额仍有效，均额为零时不作除法。跨股票涨幅比较由前端选共同基日重算，缩放和拖动不重新改变基日。
- 本地读取有文件大小上限和稳定性校验，检查路径边界、OHLC、日期顺序、未来日期与非有限值。按来源目录、类型、证券、截止日期保存原始历史缓存，最多 300 项；切周期或窗口复用缓存。刷新失败保留同源旧缓存及采集时间，缺失日不补零。

界面：T05 默认“成交额与热度”，最多 5 个板块或分类指数同图、搜索添加与移除、30–750 日；T04 在趋势结果下提供本次候选的成交额比较。“主力净流入”仍为独立入口，两种口径不会自动改名或混算。

实际验证：本机 `D:/TDX` 读取 1,069 个有效板块及分类指数，排除 50 个统计指数，最新日为 2026-10-09；隔离 Electron 工作区实际显示 5 条板块曲线、750 日历史及 5 条龙头候选曲线，原生滚轮与左右键拖动、共同基日、刷新保留选择、900 像素布局通过。`sh600900` 截至 2025-06-30 的历史实际返回 250 条且没有越过截止日期。截图与结果保存在忽略目录 `artifacts/funds-activity-actual*`、`artifacts/leader-activity-actual.png`，均为本地真实日线。

东方财富历史源再次实测：BK0428、BK1033 在 Python 与 Electron 原生请求均于 HTTP 响应前断开；官网页面和 JS 可打开，但它们仍调用相同历史服务。未找到已验证可用的另一官方历史入口，因此不能宣称主力净流入历史已经恢复。

新增量能后端测试 16 项通过；业务全套 227 项中 225 项通过、2 项因 Windows 符号链接权限跳过。成交额前端及接线相关 25 项测试通过；分析工具 Electron 9 页 write/read 烟测通过，夹具与实际通达信验收分开记录。
