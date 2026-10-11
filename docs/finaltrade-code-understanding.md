# FINAL TRADE 代码理解报告

审阅日期：2026-10-11。阶段：第 1 步，等待用户确认。

目标理解：每个策略每天对历史时点股票池形成可比较的横截面排名，维护自己的 Top N 组合；组合策略也作为独立候选。在先验证策略表现持续性之后，再研究策略间软配权及硬切换对照。最终评估的是整个资金组合及其全部交易成本，收益平原也针对整套系统。

**结论：FINAL TRADE 提供了丰富的信号计算、池内排序、滚动股票池及单策略回测基础，但尚不是上述多策略动态配权系统。现有排名、参数传递、历史股票池和成交账本还有影响结论的差异，不能直接把原回测收益当作新系统的验收结果。**

本报告仅审阅代码、导出策略声明及核对注册对象，没有为本报告运行策略回测。此前 A-trade 的探索性实现与实验不构成对新需求的验收。收到本次分阶段要求后，新增回测实现、数据下载与扩池实验均已暂停。

## 审阅范围与证据层级

源项目为 `H:\CODE\FINAL-TRADE`；目标项目当前工作区为 `H:\CODE\A-trade`。源项目未修改。

覆盖策略注册、所有已注册插件及其计算模块、未注册图形量价与混合波段模块、相关独立回测脚本、股票池与数据加载、主回测两条执行路径、收益平原与 walk-forward、前端参数提交链路。

主要文件：`backend/app/core/strategy_registry.py`、`strategy_plugins.py`、`strategy_ai_meta.py`、`ths_volume_signal.py`、`force_rhythm_strategy.py`、`wulong` 对应插件逻辑、`trend_king_strategy.py`、`b1_strategy.py`、`emotion_limit_up_strategy.py`、`limit_up_arb_strategy.py`、`chart_volume_swing_strategy.py`、`hybrid_band_strategy.py`、`backtest_signal_matrix.py`、`backtest_matrix_engine.py`、`backtest_engine.py`，以及 `store.py`、`models.py`、`tdx_loader.py`、`market_data_sync.py` 和前端筛选/回测页面。

本报告区分三类事实：代码直接定义的逻辑、沿实际调用链确认的行为、由控制流推导的风险。控制流风险没有冒充已运行复现。当前用户保存的任务、前端草稿和某份旧报告的实际参数不在本次读取范围内；默认值不是用户当前生效值。

完整注册声明、参数标题、类型、范围、枚举、schema 默认值、descriptor 默认值和实际插件绑定已保存为 [参数原始附录](/H:/CODE/A-trade/docs/finaltrade-code-parameters.json)。共 **16 个注册入口、208 条参数声明**，其中包含不同策略重复使用的通用门槛，不是 208 个独立优化维度。

## 策略清单

以下“评分”先描述策略计算或插件声明；实际回测是否调用该评分另列，不能把信号页面排序与回测排序混为一谈。

记号：Q 为该策略上下文提供的入场质量分；H/E 为共享分析器的健康/事件分；C 表示截取到 0–100。下表列核心默认值，全部可调参数及范围见 [可读参数表](/H:/CODE/A-trade/docs/finaltrade-parameter-reference.md)。原代码中的比例与百分数混用，例如 `.12` 是 12% 区间收益，而涨停套利的 `min_day_gain=3` 表示 3 个百分点。

| 注册策略 | 选股和信号逻辑 | 评分与排序 | 数据和核心默认参数 |
| --- | --- | --- | --- |
| `wyckoff_trend_v1` 维科夫趋势 V1 | 趋势池内识别吸筹/派发事件、事件序列及确认；回测按指定入场/退出事件日期处理 | Q 综合阶段、事件、结构、趋势、波动、健康、确认、近期性、K 线、成本中枢及周级背景，并扣风险；默认排序 H/E=.45/.55 | ScreenerResult 与 OHLCV，分析至少 25 根；默认窗口 60，入场 Spring/SOS/JOC/LPS，退出 UTAD/SOW/LPSY；自身参数 schema 为空，继承请求参数 |
| `wyckoff_trend_v2` 维科夫趋势 V2 | 同一检测算法，加强健康/事件分及确认门槛，不是另一个价格指标 | 回测默认 H/E=.5/.5；信号列表仍有独立 .45/.55 回退评分路径 | H/E 最低各 55，等级 B，必须关键确认，矩阵语义 aligned_wyckoff_v2；schema 的 min_score=60 与 descriptor 未填该字段有区别，UI 与裸 API 应分别核对 |
| `relative_strength_breakout_v1` 相对强弱突破 | ret40、回撤、上涨/下跌量比、量能斜率及置信度过滤；本体没有必须突破此前最高价的条件 | H/E/强度/量能/结构权重 .25/.25/.30/.10/.10，按权重和归一；强度来自 ret40，结构奖励适中回撤及缩量 | 日 OHLCV 与筛选派生值；ret40≥.12、回撤≤.22、量比≥1.15、量斜率≥.02、Q≥52、H/E最低45 |
| `score_only_rank_v1` 纯分排序 | 共享 Wyckoff Q 达标，不强制 Wyckoff 入场事件 | 直接 Q；Q 本身仍含事件与阶段信息，不是独立纯动量 | 共享分析数据；正式默认 min_score=60，事件数0，不要求序列；直接空参调用插件另有55回退 |
| `ths_main_force_flip_v1` 主力紫转黄 | 主力量线前一斜率向下，当前转向上，前主力量值为正 | 使用主散公式的信号强度 Q，插件直接按 Q 排序 | 日 close/volume；公式 MA3→EMA3 与 MA3→EMA10 固定，min_score=0；原函数至少3根可计算，不表示EMA已充分预热 |
| `ths_main_force_golden_cross_v1` 主力金叉 | 当前主线>散线，前根主线≤散线 | 同源 Q，含金叉、拐头、力量增长、主散差距等，不是均线交叉 | 与紫转黄同源，固定周期、min_score=0；没有真实资金身份数据 |
| `ths_force_rhythm_v1` 主散节奏波 | 算法本意是主力量波形的周期/振幅稳定、自相关及波谷买点；另有主峰和散户卖点 | 算法 Q 由节奏、拐点、规律度及力量组成；插件本意为 .75Q+.15强度+.10量能，**正式入口却错绑到 Base 插件，不能视为正常执行** | 至少40根、观察80根；周期数≥3，周期CV≤.55，幅度CV≤.85，自相关≥.05，摆幅≥.12；其余波谷与卖出阈值见下文和附录 |
| `wulong_cluster_v1` 五龙聚首 | MA5/10/20/30/60 从离散到收敛再扩散，配合多头排列、放量与突破强度 | Q=.18收敛+.24扩散+.22趋势+.18量能+.18突破；插件排序=.75Q+.15强度+.10量能 | 至少65根OHLCV；ret40≥.06、回撤≤.18、量比≥1.08；收敛≤.04、扩散倍数≥1.6、当日量比≥1.2、强度≥.006、收敛距今≤10、上行MA≥3；Q≥52 |
| `matrix_signal_v1` 矩阵信号 | 插件近似 S1–S9；原生矩阵则直接算价格/成交量压缩、40日收益排名、突破与均线事件；两条路径不等价 | 池四条件分，加2倍S5、1.5倍S6、1倍S7，再除8.5×100；部分 S 条件在插件与矩阵中含义不同 | 日OHLCV/横截面ret40；atr_ratio_max=.7、vol_ratio_max=.6、ret40_top_n=500、sideways=.18、nearMA20=.06、池分≥2、突破量比1.2、Q≥50 |
| `b1_mtf_v1` B1 多周期 | 月MACD多头、周DIF>0，日MA5>MA20且价在MA20上，缩量、低涨跌/振幅、KDJ J勾头 | Q=KDJ项上限40+缩量项上限30+月周MACD项上限30；插件=.70Q+.15强度+.15缩量分 | 实际条件函数需900日、月/周各≥35根；声明 vol_ratio=.8、chg_limit=3、amp_limit_10cm=5、amp_limit_20cm=8、kdj_j_upper=50、min_score=35；五个技术阈值未接入正式注册计算路径 |
| `trend_king_v1` 趋势为王 | A 涨停式强势或 B 连续涨势，`mode=all` | Q为模式原始分/92×100；插件=.65Q+.20强度+.15量能 | 至少120根；历史弹性观察最多250日内向后20日且截到当时可见末日；A日涨≥9.5、历史弹性≥30；B三日≥7或五日≥10且历史弹性≥30 |
| `trend_king_limitup_v1` 涨停板精选 | 固定A模式；9.5%是阈值，不能当所有板块法定涨停率 | 奖励适中量比、历史弹性、近期涨幅、昨日强度、MA60、振幅及板块排名 | 同趋势为王，`mode=a`；信号列表 top_n=8，非回测固定持有8只 |
| `trend_king_rally_v1` 涨势确认 | 固定B模式，当前日涨低于9.5且近3/5日上涨达标 | 奖励近3/5日涨势、连阳、历史弹性、均线及板块；同源插件排名 | `mode=b`；信号列表 top_n=10 |
| `trend_king_pullback_v1` 涨停跟踪池 | 固定C模式，代码仍要求A成立，再要求量比<1.5、历史弹性>50、昨日涨>3；并不等于正在回调 | 用A类原始评分；给出close×.97/.95的参考价，但插件未据此挂低吸委托 | `mode=c`；top_n=0不另截断，min_score=0；其名称与实际条件应明确 |
| `emotion_limit_up_v1` 情绪涨停 | 近5日有主散金叉或紫转黄，当前日涨≥9.5%；两个形态可以发生在不同日期 | Q累加新鲜度、双信号、主散比、量比、历史弹性、板块、昨日涨幅及MA60；插件=.70Q+.20强度+.10量能 | 日OHLCV及板块数据；声明lookback=5、次日量倍数2、最大跌幅5%、需要次日确认；后两项依赖独立helper，未成为正式回测入场约束 |
| `limit_up_arb_v1` 涨停套利 | 昨日涨停，今日涨幅及相对昨日成交量同时达标，可选MA5量比门槛 | Q由超出阈值的涨幅、昨日量比、可选MA5量比与板块排名组成；插件=.85Q+.15量能 | 至少3根；min_day_gain=3、min_volume_ratio_prev=1.2、min_volume_ratio_ma5=0关闭、sector_rank_weight=.08；信号top_n=15；实际引擎有同日收盘成交特例 |

主要入口：[插件实现](/H:/CODE/FINAL-TRADE/backend/app/core/strategy_plugins.py:61)、[共享信号分析](/H:/CODE/FINAL-TRADE/backend/app/core/signal_analyzer.py:616)、[主散公式](/H:/CODE/FINAL-TRADE/backend/app/core/ths_volume_signal.py:69)、[节奏波](/H:/CODE/FINAL-TRADE/backend/app/core/force_rhythm_strategy.py:194)、[B1](/H:/CODE/FINAL-TRADE/backend/app/core/b1_strategy.py:208)、[趋势为王](/H:/CODE/FINAL-TRADE/backend/app/core/trend_king_strategy.py:131)、[情绪涨停](/H:/CODE/FINAL-TRADE/backend/app/core/emotion_limit_up_strategy.py:198)、[涨停套利](/H:/CODE/FINAL-TRADE/backend/app/core/limit_up_arb_strategy.py:51)。

### 金叉和主散节奏波的准确区别

主散基础公式为：上涨日成交量归入U，下跌日归入D，平盘日两者为零；原实现量除以100。主线 `M=EMA3(MA3(U))`，散线 `R=EMA10(MA3(D))`。金叉是M上穿R；紫转黄是M斜率先降后升。它们没有识别真实主力或散户身份。

基础信号评分为 `C(12 + 12×紫转黄 + 18×金叉 + .48×力量分 + .32×差距分 + 状态加分)`。力量分来自增长、爆发、加速的对数归一组合；差距分来自主散优势比例和两线之比。因此“金叉信号”和“金叉得分很高”是两个条件。

完整节奏波另要求周期数、周期CV、振幅CV、自相关和摆幅达标，然后判断三类买点：紫转黄时主线分位位于 .42–.55；两根前紫转黄且当时分位 .17–.35、当前主线上升；或处于分位≤.015的连续第二根深谷。高分位/散户卖信号会否决部分普通买点；主峰及散户卖点另行产生退出信号。

`require_trough_turn`、`trough_percentile_max`、`trough_turn_percentile_min/max` 等虽然声明或解析，却未完整约束最后买点布尔值；部分深谷和追随判断在上下文计算中仍写死。再叠加注册错绑，必须区分“算法文件意图”“当前正式执行行为”“之后按意图修复的版本”，不得混成同一条历史业绩。

B1 的周/月线还包含当时进行中的周/月；只要输入已经截到当日就不必然有未来信息，但不等于等待周/月收盘确认。该语义应在方案阶段明确。

### 未注册和实验代码

**图形量价波段 `chart_volume_swing_v1`**：识别箱体、放量突破，再等待箱顶缩量回踩或突破后站稳，至少 80 根历史。评分由箱体幅度、突破量、箱内缩量、涨跌量比、K 线质量、箱体长度、信号新鲜度、回踩位置与缩量组成；回踩确认加 8 分，站稳确认加 3 分，最后截到 0–100。A/B 等级门槛为 75/62，入选默认 50。

其完整参数默认：`box_windows=(20,30,40)`、`box_range_min=.06`、`box_range_max=.35`、`breakout_vol_ratio=1.5`、`min_close_strength=.45`、`max_breakout_wick_ratio=.55`、`hold_ratio=.97`、`retest_band=.04`、`shrink_vol_vs_ma5=.75`、`vol_up_down_ratio=1.3`、`vol_lookback=20`、`confirm_hold_days=2`、`max_breakout_age_days=18`、`min_score=50`、`vol_shrink_in_box_max=1.05`、`retest_recent_days=5`、`min_days_after_breakout=2`。数据为日线 OHLCV。

来源：[图形量价参数](/H:/CODE/FINAL-TRADE/backend/app/core/chart_volume_swing_strategy.py:24)、[图形量价评分](/H:/CODE/FINAL-TRADE/backend/app/core/chart_volume_swing_strategy.py:446)。模块明确注明独立验证后再考虑接入，注册表中没有该策略。

**混合波段 `hybrid_band`**：图形量价分起步，紫转黄加 10、金叉加 8、满足条件的节奏波拐点加 12、MA10 缩量回踩加 6；40 日涨幅排名前 100/200/500 再分别加 8/5/2。必须满足“图形信号或趋势回踩”且综合分至少 82。它是原始分数直接相加，没有横截面排名或 z-score 标准化，也没有动态策略权重。

继承全部图形量价参数，另外 `min_hybrid_score=82`、`ret40_rank_bonus_100=8`、`ret40_rank_bonus_200=5`、`ret40_rank_bonus_500=2`。趋势回踩内部还有固定阈值，不能把这些都当作现有可调表单参数。来源：[混合波段](/H:/CODE/FINAL-TRADE/backend/app/core/hybrid_band_strategy.py:52)。文件明确写着不接入主程序。

`hybrid_band_screener.py` 又单独复制了一版混合公式，没有 core 版本的 ret40 排名加分；它先过滤混合分，再在已命中候选中执行 ret40 范围 .08–.80、Top500 筛选，最终每日 Top3。默认成交额门槛为3e8，并可加载优化输出文件覆盖参数。它与 core.hybrid 也不能视为同一逐字执行版本。来源：[独立扫描器](/H:/CODE/FINAL-TRADE/backend/scripts/hybrid_band_screener.py:129)。

**波段换股脚本**：`backtest_band_rotation.py` 的 rotation 是单槽或少数槽空出来后买下一只信号股票，不是多个策略按近期业绩分配资金。默认单槽，另有双槽实验；没有统一每日 Top N 目标组合、策略权重和换仓阈值。

脚本还使用全历史 15 日信号聚类后保留最高分信号，这会用之后的信号决定此前哪些信号应被删除；交易日历只取信号出现日期，不能直接当作完整交易所日历。20 日波段收益按信号起始日归属整笔交易收益，也不等于该窗口逐日盯市收益。此路径不能直接迁移为无未来信息的组合账本。来源：[轮换脚本](/H:/CODE/FINAL-TRADE/backend/scripts/backtest_band_rotation.py:46)、[事后信号去重](/H:/CODE/FINAL-TRADE/backend/app/core/chart_volume_swing_strategy.py:529)。

注册文件还残留 `__removed_force_crossover_v1`、`__removed_force_launch_v1`、`__removed_force_pullback_v1`、`__removed_wulong_cluster_v2` 描述符构造代码。它们不是四个可正常选择的在用策略；其中最后一个还引出了下述注册错绑。

**调参与诊断脚本**：`hybrid_band_optimizer.py`、`optimize_band_target.py`、`morning_band_report.py` 属于图形/混合波段实验链，前者同样先做15日最高分去重，排名也基于已经筛过的信号池，并会为趋势回踩额外复制加分候选，不能视作全股票池独立策略轮动。`tune_weike_rhythm.py` 固定单只股票及2026年3月至6月区间，用三个指定信号日的命中情况作为优化目标；这是人工形态拟合，不是多股票收益验证，也不能计为本系统的样本外证据。调参与诊断文件不作为另外的在用策略注册入口。来源：[混合优化器](/H:/CODE/FINAL-TRADE/backend/scripts/hybrid_band_optimizer.py:83)、[形态拟合目标](/H:/CODE/FINAL-TRADE/backend/scripts/tune_weike_rhythm.py:19)。

这些脚本还包含以下独立或旧版规则，已经与在用策略分开核对：

| 文件 | 实际用途及关键差别 |
| --- | --- |
| `verify_weike_rules.py` | 旧版手写买点：节奏成立、分位<.72，散户卖压且分位>.22时拒绝，再取紫转黄分位 .35–.52、拐点分位 .17–.30 或深谷≤.05仍下降；不是当前 core evaluator。用指定期望日及拒绝日核对形态 |
| `tune_weike_rhythm.py` | 另一旧版 custom 买点，允许分位≤.08的下降/平坦深谷、.30–.55紫转黄及经典拐点；以人工指定日期命中为拟合目标，不是收益评分 |
| `debug_weike_rhythm.py` | 调用现有 THS/rhythm/evaluator 输出逐日诊断；没有独立新交易策略 |
| `validate_chart_volume_scoring.py` | 对评分区间比较胜率、盈亏因子和均值，仍使用未来15日最高分去重；固定止损12%、止盈30%、持有60日、跟踪15%的实验条件，不是独立样本外验证 |
| `optimize_band_target.py` | 新增结构止损、保本、时间止损、均线/箱沿退出；先读当日最高价激活保本再用最低价测止损，隐含无法从日线确定的日内顺序；收盘规则可当日收盘成交。同一段最多21,600配置择优，没有独立留出 |
| `hybrid_band_optimizer.py` | 最大46,080组过滤/退出/槽位配置，同段按命中收益目标择优；重叠窗口把窗口内入场交易的完整未来盈亏计入，即使交易尚未在窗口内退出，不能当该窗口已实现或逐日盯市收益 |
| `morning_band_report.py` | 四套手选配置复用上述逻辑再择优；窗口统计也提前计入完整交易收益。命名“最终验证”不能替代未见数据。该文件导入即执行读写，审计仅阅读，没有导入 |

上述最多配置数是源码网格上限，不表示本次执行了这些试验，也不等于已找到有效策略。相关源：[独立退出](/H:/CODE/FINAL-TRADE/backend/scripts/optimize_band_target.py:54)、[重叠窗口统计](/H:/CODE/FINAL-TRADE/backend/scripts/hybrid_band_optimizer.py:222)、[晨报窗口统计](/H:/CODE/FINAL-TRADE/backend/scripts/morning_band_report.py:53)。

## 股票池来源、过滤和更新

### 来源与历史时点能力

| 来源 | 实际行为 | 限制 |
| --- | --- | --- |
| 本地基础池 | 扫描配置通达信目录下沪深北日线文件，按代码前缀识别 A 股 | 当前存在的文件集合不等于某历史日上市成员全集；缺退市证券恢复机制 |
| 趋势池 | 用 `run_id` 找到漏斗参数，在历史刷新日重算 Step1–4；`auto` 用 Step4，空时回退 Step3 | 界面手工池及浏览器本地重算结果没有全部进入该 `run_id` |
| 全市场池 | 基础过滤后按证券代码排序，截取 `max_symbols`，默认 120 | 名称“全市场”不表示全量；存在明显的代码截取偏差 |
| 股票搜索库 | 静态股票列表，只保留 `list_status=L` | 无历史日期参数 |
| 在线同步 | 用同步截止日的 BaoStock `query_all_stock` 名单下载历史行情 | 不是逐历史日的上市、退市成员档案 |
| 模拟降级 | 部分真实数据加载失败分支会返回模拟候选池并附原因 | 必须显式识别；分支存在不代表用户某份旧报告已经使用模拟数据 |

源码：[TDX 基础池](/H:/CODE/FINAL-TRADE/backend/app/tdx_loader.py:952)、[滚动趋势池](/H:/CODE/FINAL-TRADE/backend/app/store.py:9210)、[全市场截取](/H:/CODE/FINAL-TRADE/backend/app/store.py:9361)、[模拟降级](/H:/CODE/FINAL-TRADE/backend/app/store.py:8873)、[在线名单](/H:/CODE/FINAL-TRADE/backend/app/market_data_sync.py:170)。

日线指标确实按 `date <= as_of_date` 截断，这一点可以借鉴。但证券名称取当前 TNF，流通股本取当前 `base.dbf`；ST 依据当前名称，历史换手使用当前股本。“上市满 250 日”检查的是当前整个文件记录数，不是截至回测日的上市天数。故不能据此声称已经消除幸存者偏差或全部未来信息。

来源：[历史截断及上市记录数](/H:/CODE/FINAL-TRADE/backend/app/tdx_loader.py:487)、[当前名称及股本](/H:/CODE/FINAL-TRADE/backend/app/tdx_loader.py:978)、[ST 判断](/H:/CODE/FINAL-TRADE/backend/app/store.py:7982)。

TDX 与在线 CSV 的价格口径也有区别：在线同步取前复权价格，却只保留 OHLCV 与成交额，没有同时保留原始价、历史 ST、除权参考价和完整公司行动。加载器允许 TDX 失败后转 CSV，因此不能只凭统一 CandlePoint 类型认定输入价格口径相同。来源：[在线下载参数](/H:/CODE/FINAL-TRADE/backend/app/market_data_sync.py:256)、[行情降级读取](/H:/CODE/FINAL-TRADE/backend/app/tdx_loader.py:672)。

### 四步漏斗及默认条件

| 步骤 | 主要规则与默认值 |
| --- | --- |
| Step1 | 区间涨幅排名从 1 开始，`top_n=500`；换手至少 .05、成交额至少 5e8、振幅至少 .03；实现还有限制到 400 的上限 |
| Step2 | 回撤 .05–.25，回调最多 3 日；MA10 高于 MA20 至少 5 日、MA5 高于 MA10 至少 3 日；价格高于 MA20 且偏离不超过 .08；默认不接纳 B 趋势 |
| Step3 | 量能斜率至少 .05、上涨/下跌量比至少 1.3、回调量比不超过 .9；默认拒绝高潮尖顶、背离、上影风险及降级数据 |
| Step4 | `ai_confidence >= .55`，题材阶段为“发酵中”或“高潮”；按 `score + ai_confidence*20` 排序，默认最终 8 只 |

这里的 `ai_confidence` 和题材阶段在基础池中由价量公式产生，不是历史 AI 判断和历史题材数据库。来源：[默认配置](/H:/CODE/FINAL-TRADE/backend/app/models.py:41)、[过滤实现](/H:/CODE/FINAL-TRADE/backend/app/store.py:8171)、[衍生标签](/H:/CODE/FINAL-TRADE/backend/app/tdx_loader.py:587)。

### 更新频率

后端快照通常由“加载输入池”“运行 Step1”“一键全部”触发。单独运行 Step2/3/4 在前端本地重算，不会生成新 `run_id`。回测支持 daily、weekly 和 position 刷新：daily 每个扫描日刷新，weekly 每周首个扫描日刷新；position 先用种子池预演，再依预演退出日生成刷新计划，并非真实组合逐日持仓事件的闭环刷新。

刷新日期使用工作日生成，未等同于交易所节假日日历。Loader 30 分钟、输入池磁盘缓存 12 小时、趋势过滤缓存 24 小时是缓存有效期，不是行情定时更新频率。本次主链路没有发现固定定时行情同步机制，存在的是同步 API 与独立脚本。

来源：[前端触发](/H:/CODE/FINAL-TRADE/frontend/src/pages/screener/ScreenerPage.tsx:2017)、[扫描日期](/H:/CODE/FINAL-TRADE/backend/app/store.py:8247)、[刷新规则](/H:/CODE/FINAL-TRADE/backend/app/store.py:8321)、[position 预演](/H:/CODE/FINAL-TRADE/backend/app/store.py:13729)。

## 多策略组合和交易执行现状

正式回测请求只有一个 `strategy_id`，不是可同时分配资金的多个策略。最大 5 持仓表示一个策略可以持有多只股票，并不表示持有五个策略。每日候选可以重新产生并排序，但已有持仓主要按退出规则释放，未实现“新股票标准化得分超过旧持仓一定门槛才换仓”的目标组合维护。

信号页面调用插件 `rank_signals` 并可按 `signal_top_n` 截断；回测候选构建则没有调用同一排名接口，独立事件策略直接使用 `entry_quality`，其他路径使用健康/事件加权。策略页面中的 Top N 与回测持仓 Top N 因此不能直接视为同一组合。来源：[单策略请求](/H:/CODE/FINAL-TRADE/backend/app/models.py:720)、[信号页排名](/H:/CODE/FINAL-TRADE/backend/app/store.py:7260)、[回测排序](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:2557)。

现有混合波段只是若干指标分数相加；没有通用交集/并集/标准化加权组合树，没有每个组合策略独立的 Top N 净值序列，也没有基于近期 K 日组合表现的策略权重、冷却期、调整阈值和轮动日志。相邻窗口策略排名持续性检验、等权策略组合基线、训练期选定的固定单策略基线均未形成完整闭环。

### 必须先澄清或修复的实现差异

1. **主散节奏波插件错绑，已核对实例对象。** `v44` 先创建并注册 `ths_force_rhythm_v1`，随后被重新赋值为已移除的五龙 V2 描述符，最终插件表把 ForceRhythmPlugin 挂在 `__removed_wulong_cluster_v2` 上。真正的节奏波入口取到 BaseStrategyPlugin。上下文函数虽仍可能算出节奏波，但无节奏波上下文的行并不总被拒绝，会让其他评分通过并被赋予“节奏波谷”标签。不能把现有结果当作完整原版节奏波策略。

   来源：[首次登记](/H:/CODE/FINAL-TRADE/backend/app/core/strategy_registry.py:972)、[变量复用](/H:/CODE/FINAL-TRADE/backend/app/core/strategy_registry.py:1202)、[插件绑定](/H:/CODE/FINAL-TRADE/backend/app/core/strategy_registry.py:1891)、[回退插件](/H:/CODE/FINAL-TRADE/backend/app/core/strategy_registry.py:1964)、[回测候选路径](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:2421)。

2. **退出信号依赖当前仍在入选池且买入筛选为真。** legacy 候选循环先排除不在允许池的日期，再对 `generate_signals=False` 提前 continue，之后才收集退出事件。已有持仓离池、买入形态消失的日子可能漏记卖出信号。节奏波买入信号与其主峰/散户卖点常相互排斥，尤其需要独立维护退出路径。

   来源：[提前过滤与退出采集](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:2411)、[节奏波退出条件](/H:/CODE/FINAL-TRADE/backend/app/core/force_rhythm_strategy.py:626)。

3. **部分表单参数没有接通底层计算。** B1 五个技术阈值在正式快照路径未传入计算函数，评估函数也没有消费这些参数；情绪涨停的 `require_next_day_confirm` 未接入正式执行，部分次日条件只在独立 helper 中使用。节奏波还有声明阈值与底层固定阈值并存。声明可调不等于修改后实际改变交易。

   来源：[B1 快照](/H:/CODE/FINAL-TRADE/backend/app/store.py:5895)、[B1 评估](/H:/CODE/FINAL-TRADE/backend/app/core/b1_strategy.py:349)、[情绪策略快照](/H:/CODE/FINAL-TRADE/backend/app/store.py:5898)。详细字段见策略表及参数附录。

   B1 wrapper 还尝试从 CandlePoint 读取 `symbol`，但该模型没有此字段，正式路径的创业/科创振幅判断会落到普通股票分支；独立 B1 API 另有参数通路，不能与注册策略路径混同。涨停判断也需统一：部分策略直接写9.5%，另一工具按代码区分10/20/30%但没有历史日期/ST感知。

4. **信号和成交时序并不全部满足新要求。** 涨停套利存在收盘确认后以同日收盘价进入的分支；其他分支虽通常延迟入场，但日内高低价触发止损止盈直接使用阈值价，没有完整处理跳空、涨跌停排队和成交容量。部分最后一天待执行退出还会变成末日卖出，不能直接沿用为统一 T+1 开盘模型。

   来源：[涨停套利入场](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:237)、[日线触价退出](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:1855)、[末日退出](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:1933)。

5. **资金回收只有日期，没有日内先后。** 同一日期的日内或收盘卖出回款可能在该日较早的开盘买入时被使用。新仓规模又按初始资金加已实现盈亏计算，而非完整逐日盯市权益。费用主要是单一 bps，快照中的最低佣金、印花税、过户费及滑点为零，并非逐项还原 A 股交易费用。

   来源：[资金释放](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:3097)、[仓位计算](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:3156)、[费用快照](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:3857)。

6. **分批退出可能遗失余股，这是控制流推导，尚未实验复现。** 信号腿以原始仓位扣两次 50% 后认定全平；落地交易却依当前剩余股数再乘 50%，并按整手取整。初始 1,000 股可能依次卖 500、200，剩 300 股，而活动仓位随后按最后退出日移除。需要真实账本不变量测试，不能只看最终 trade 列表。

   来源：[退出比例腿](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:1766)、[落地股数](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:3018)、[活动仓位](/H:/CODE/FINAL-TRADE/backend/app/core/backtest_engine.py:3187)。

这些发现说明需要修正调用与账本，不是断言所有旧回测、所有日期都发生了上述问题。

## 收益平原与市场环境现状

原项目已有 grid/LHS 扫描、局部邻域分、区域聚类、参数范围、walk-forward 与市场分段报告，可以作为研究基础。主扫描轴为窗口、最低分、止损、止盈、跟踪止损、减仓比例、持仓数、仓位比例、股票上限、每日候选上限；尚未覆盖组合方式/权重、轮动 K/阈值/冷却期及持仓替换阈值四层联动。

存在以下直接差异：

- 邻域评分循环在给当前点赋值 `point_score` 时，已经读取其他点的 `point_score`；后面的点可能仍为初始值，局部评分可能受点顺序影响。[评分循环](/H:/CODE/FINAL-TRADE/backend/app/store.py:11148)
- 区域连接使用若干最近点，没有保证严格相邻网格，也没有排除只有一个点的区域；稀疏采样可能跨空洞连接。[区域构建](/H:/CODE/FINAL-TRADE/backend/app/store.py:11237)
- 初步区域先产生，再对部分区域运行 walk-forward，其样本外通过率加入区域评分并再次排序。这段验证已参与选择，不能同时称为最终未触碰测试。[验证参与重排](/H:/CODE/FINAL-TRADE/backend/app/store.py:11380)
- walk-forward 确实存在，会在训练段选择候选参数再跑测试段；不能说原项目完全没有样本外功能。但它仍针对单策略参数，未完成整个多策略系统的独立基线比较。[滚动验证](/H:/CODE/FINAL-TRADE/backend/app/store.py:13064)
- 牛/震荡/熊标签由被测策略自身滚动净值和回撤生成。不同策略可能在同一日期被分到不同“市场状态”，也没有独立高低波动状态。这不符合“同一外部市场环境比较各策略”。[市场分段](/H:/CODE/FINAL-TRADE/backend/app/store.py:12758)

因此，当前原项目平原不能直接证明“多策略 × 多股票 × 市场环境 × 四层参数”的稳定区域。

## 现有代码与目标的差距

| 目标 | 可借鉴基础 | 仍缺少或需修正 |
| --- | --- | --- |
| 历史股票池 | 历史日期截断、滚动漏斗 | 历史成员/ST/上市状态/股本/行动，统一价格口径、交易日历和明确数据失败策略 |
| 每策略每日全池排序 | 插件信号与排名、日候选 | 策略统一评分契约，未触发股票的排名定义，前端/回测同一排名与 Top N |
| 每日维护 Top N | 多证券持仓和仓位限制 | 目标权重、持仓比较、得分差换仓、调仓周期、未成交目标延续 |
| 组合策略 | 独立 hybrid 加分实验 | 可配置交/并/加权、排名或 z-score 标准化、缺失得分规则、组合策略独立净值 |
| 轮动持续性检验 | 可运行不同单策略 | 同一历史池、同一成本下的 Top N 策略收益面板，相邻窗口排名相关、样本外持续性结论 |
| 市场环境与软配权 | 自身净值分段报告 | 共同外部市场状态、只看过去的权重决策、阈值、冷却、现金选择、调整日志 |
| 全部交易成本 | bps 与部分 T+1 逻辑 | 开盘可用资金时序、不可成交队列、分项成本、成交容量、公司行动、余股守恒 |
| 整套系统收益平原 | 参数扫描、局部评分、walk-forward | 四层有限参数空间、试验总数、邻域均值/波动/正收益率、跨年份/状态及独立样本外稳定区域 |
| 关键价值检验 | 可输出单次绩效 | 同资金/池/成本的等权策略组合、训练期固定单策略、轮动与基准比较及明确淘汰结论 |
| 审计输出 | 交易、曲线、任务持久化 | 每日各策略 Top N 与标准化分、每日策略权重、目标/实际仓位、全部权重变动原因与持续性结果 |

## 当前 A trade 探索版本的边界

此前版本实现的是：候选股票信号、多个条件 AND 交集、按信号排入场队列、共享资金与风险退出、单套配置的参数平原。它可以作为事件驱动执行和研究任务的基础，但没有每日目标 Top N 维护，也没有策略资金子组合、通用组合策略或动态轮动。

此前新增的 `forceGoldenCross` 为原主散公式金叉候选；`forceTrend` 是简化的持续主导假设，**不等于 FINAL TRADE 的完整主散节奏波**。50 股分层名单已冻结，但新的扩池回测尚未完成；不能报告为已验证成功。

暂停时 `service/backtest_engine.py` 的数据质量增强处于中间态：参考昨收/暴露检查已有改动，汇总和新增测试尚未完成；`service/research_data.py` 为未验收数据适配草稿；现金分红账本尚未实现。本次没有擅自回滚或继续完成这些代码，也没有把它们计入已通过验证的交付。后续是否沿用须在方案阶段明确。

## 需要用户确认的歧义

1. **迁移范围**：本报告理解为在 A-trade 中搭建新模块，以 FINAL TRADE 的策略逻辑为来源；是否同时要求修改 FINAL TRADE 本身？当前未修改源项目。
2. **首批策略**：你所指的“主散量能”是否特指完整 `ths_force_rhythm_v1`？金叉、紫转黄、节奏波是三个不同入口。首批是优先这些，再选少量异类策略对照，还是要求全部 16 个入口都参与第一轮？原错绑和未生效参数按策略意图修复，还是另保留旧行为复现对照，也需确认。
3. **回测设置**：回测起止日期、股票池范围、业绩基准、初始资金、Top N 与调仓频率均尚未填写。报告中的默认参数只说明原代码，不作为本次实验设置。
4. **后续设计须明确的规则**：没有合格股票时是否留现金；多个策略选到同股的仓位如何合并及限额；得分差门槛按排名分还是 z-score；“轮动赢过等权”的判据是净年化优先还是同时要求回撤/夏普条件；这些影响结论，不能默认为一种口径。

本次停在第 1 步。用户确认代码理解和上述范围后，下一步只提交方案设计；方案再次确认后，才进入单策略/组合策略回测与持续性检验。持续性不成立先报告，不越过该关卡继续轮动。最终样本外轮动不能战胜事先确定的等权对照时，明确报告“轮动没有创造价值”，不为改善展示继续调参。
