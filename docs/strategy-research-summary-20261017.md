# A-trade 研究总结与三组实验策略（2026-10-17）

本轮研究按要求在第 49 批后停止。保留三组机制不同、规则可明确落地的**实验策略**：三段收缩突破、持续强势自身突破、强股支撑收回。它们都不满足“已经证明稳定盈利”的表述；选入是为了保留可观察、可复现的实验方案，而不是把开发数据中最大的收益当成未来保证。

原登记的固定候选试验计数为 **49**。本总结完整列入 program 的 **92 个 progress 节点**，其中有重复时期验证、账户退出对照、零账户诊断、数据与费用核验、历史草案和登记元数据，不能说成 49 套独立策略，也不能把旧账户的重复分析当成新的盈利验证。

本文件汇总已保存的研究证据，并补充三组原规则在本地通达信上的当前扫描实测。扫描没有新增研究批次、账户回测或策略门槛；不代表已下单、实盘部署或后续账户收益。历史复现与当前扫描是应用中的两个独立入口。

## 三组入选及完整范围

排序体现实验落地优先级：规则简洁、机制不同、账户证据完整、风险边界清楚。不同股池、时期、交易数量和费用版本不支持一个严格可比的总收益排行榜。各时期独立以 50 万元开始，表中是整个时期的费用后账户收益，不是年化收益；期末未平仓按原估值纳入净值，PF 则只看已平仓金额。

### 1. 三段收缩突破＋第五日无进展退出

观察连续三段各 10 个交易日的价格区间逐步收窄，末段区间不超过 8%，末段均量不超过首段 70%；同时满足上升 MA80、收盘突破此前 40 日高点、当日量达到此前 40 日均量的 1.2 倍。按事前 20 日均额排原 Top5。保留 5% 收盘止损、10% 收盘跟踪；第 5 个持有交易日收盘未高于风险入场价时安排后续可卖开盘退出。**没有 30% 盈利目标，没有统一最长持有期。**

| 时期 | 板块 | 净总收益 | 最大回撤 | 闭仓数 | PF | 平均仓位 |
|---|---|---:|---:|---:|---:|---:|
| 2022-2023 | 沪深主板 | 6.45% | 18.69% | 60 | 1.175 | 43.88% |
| 2024-2025 | 沪深主板 | -0.68% | 29.96% | 73 | 0.900 | 56.04% |

旧期平均盈利 +14.08%、平均亏损 −3.85%，闭仓净收益 31,611 元，未平浮盈 631 元；因此旧期正收益不是主要靠期末浮盈。新期平均盈利 +12.10%、平均亏损 −4.82%，但 PF 只有 0.900，2025 年账户仍亏 −9.02%。两期闭仓共 133 笔，比只在旧期有 27 笔的 market60 版本更有可读性；仍不足以证明长期稳定。旧/新期前五盈利交易占毛盈利约 75.21%/62.75%，趋势收益集中应继续展示。

它只在原 3141 只沪深主板存续样本上完成上述复验。不能把新三板股池直接替换进去后沿用这些数字。旧期使用当时的 dated 费用，新期复验使用冻结 LegacyFeePolicy；若新包装统一改用另一费用版本，应明确为新执行包装，不宣称旧结果逐笔等价。

来源：[信号定义](G:/CODE/A-trade/docs/signal-contraction-price-proxy-v1.json)、[退出方案](G:/CODE/A-trade/artifacts/strategy-search/contraction-policy-screen-v1/plan.json)、[两期完整对比](G:/CODE/A-trade/artifacts/strategy-search/market-context-screen-v1/comparison.json)。原早期信号协议中的净 30% 事件目标已经被后续现金账户退出方案取代，不能带入实验策略。

### 2. 持续强势自身突破＋固定 10% 跟踪

采用第 48 批 persistent：当日 A 的完整强势候选先按原共同排序取 Top5，再检查十个交易日前 F 是否已满足同一组强势条件。历史证据未知不算失败，也不补入第六名。A 收盘形成信号，最早下一源交易日开盘尝试买入；5% 收盘止损、10% 收盘跟踪。它交易的是候选自身，不是跟风股，也不识别任何主力身份。

| 时期 | 板块 | 净总收益 | 最大回撤 | 闭仓数 | PF | 平均仓位 |
|---|---|---:|---:|---:|---:|---:|
| 2022-2023 | 主板 | -32.99% | 38.56% | 54 | 0.416 | 34.74% |
| 2022-2023 | 创业板 | -1.83% | 15.06% | 23 | 0.924 | 10.39% |
| 2022-2023 | 科创板 | 2.22% | 6.02% | 6 | 1.575 | 2.25% |
| 2024-2025 | 主板 | -20.04% | 35.12% | 78 | 0.682 | 40.12% |
| 2024-2025 | 创业板 | 17.01% | 9.18% | 18 | 1.992 | 6.97% |
| 2024-2025 | 科创板 | 0.75% | 11.80% | 10 | 1.065 | 3.75% |

与同一共同 Top5 上不要求历史持续的 knownAll 相比，persistent 在 5/6 组有更好账户收益、6/6 组回撤更低，因此保留这个简单机制。但低回撤也伴随低参与：新创业板只有 18 笔闭仓、平均仓位 6.97%，前五盈利交易贡献约 98.33% 毛盈利；旧科创板仅 6 笔、新科创板仅 10 笔。不能把这些账户的低回撤当成满仓同等风险比较，也不能只选创业板 +17.01% 宣称普遍盈利。

主板两个时期仍亏约 −32.99%/−20.04%。第 48 批全部 18 个账户中，平均盈利都大于平均亏损绝对值，但 15 个账户仍亏损；这说明“小亏大赚”的均值形状必须与胜负频率、费用和实际资金占用一起看。

来源：[第48批固定方案](G:/CODE/A-trade/docs/leader-persistence-admission-v1.json)、[完整读出](G:/CODE/A-trade/artifacts/strategy-search/leader-persistence-admission-v1/readout.json)、[读出核验](G:/CODE/A-trade/artifacts/strategy-search/leader-persistence-admission-v1/readout-validation.json)。

### 3. 强股支撑收回＋波动跟踪

采用原强股支撑收回 immediate 信号及第 37 批 volatilityTrail 退出。信号、原 Top5 顺序和次开执行保持原定义；保留 5% 初始收盘止损，跟踪距离使用截止前一日的 20 日平均相对真实波幅乘 3，保护阈值只收紧不放宽。缺失新波动窗口时保留已有保护阈值。它不是 Wilder ATR，也没有根据 9·24 切换参数。

| 时期 | 板块 | 净总收益 | 最大回撤 | 闭仓数 | PF | 平均仓位 |
|---|---|---:|---:|---:|---:|---:|
| 2022-2023 | 主板 | -54.25% | 61.04% | 154 | 0.363 | 95.49% |
| 2022-2023 | 创业板 | -54.42% | 63.71% | 156 | 0.407 | 95.65% |
| 2022-2023 | 科创板 | -57.21% | 59.92% | 127 | 0.404 | 93.92% |
| 2024-2025 | 主板 | 21.50% | 25.67% | 107 | 1.012 | 98.01% |
| 2024-2025 | 创业板 | 111.03% | 34.22% | 79 | 2.062 | 88.45% |
| 2024-2025 | 科创板 | 32.67% | 29.08% | 93 | 1.396 | 91.79% |

新期三个板块均盈利，旧期三个板块均大幅亏损，时期依赖非常明确。新创业板 +111.03% 中，已平仓净收益约 478,280 元、未平浮盈约 76,851 元；新主板 +21.50% 则主要由未平浮盈支持：已平仓约 5,223 元、未平约 102,261 元。不能把两者都描述成已兑现收益。新创业板有 393 个估值不可靠日，新科创板 142 日；审计通过代表记录和算术按冻结口径一致，不证明这些日线估值都可实际成交。

这是三组中高曝光、承受明显回撤的实验方案。仅为同一 UR 家族保留这一个代表，不再把 noProgress、结构退出、突破后切换波动退出或 Ridge 准入各占一个名额，也不按板块事后挑退出。

来源：[原六源信号方案](G:/CODE/A-trade/artifacts/strategy-search/board-coverage-reclaim-v1/plan.json)、[第37批波动退出](G:/CODE/A-trade/docs/reclaim-volatility-trail-v1.json)、[完整读出](G:/CODE/A-trade/artifacts/strategy-search/reclaim-volatility-trail-v1/readout.json)。

## 9·24 分界解释什么，不解释什么

固定市场阶段把 2024 年 9 月 24 日前后分开，并将后段继续细分；没有把其后全部叫成同一种牛市。2024-09-24 至 10-08 的政策上行期，原持续龙头门槛没有产生第 48 批 persistent 信号。第 47 批焦点复核发现三板合计 70 个“当日 A 合格股票日”，但十日前 F 门槛通过数为 0、未知数为 0。这解释了软件事件缺失，**不等于发现 70 个盈利机会**。

该六日阶段已有 UR 持仓可随市场上涨：旧第 37 批六个账户权益都上涨，其中五个账户没有买卖。不能把原持仓的估值提升算作新信号胜率；创业板该六日都有估值质量标记，也不能夸大成可实现套利。持续强势策略在本阶段保持现金，反而提醒它可能错过从低位快速普涨的开始。

市场宽度、可观察成交额和相对强队列研究说明环境与策略参与不同，但这些是同一开发历史的描述。9·24 不进入交易条件；不按回看阶段决定使用哪套策略，不承诺自动识别未来政策行情。

来源：[固定市场阶段](G:/CODE/A-trade/artifacts/strategy-search/market-phase-atlas-v1/atlas.json)、[事件覆盖复核](G:/CODE/A-trade/artifacts/strategy-search/leader-stage-coverage-v1/readout.json)、[连续账户阶段提取](G:/CODE/A-trade/artifacts/strategy-search/joint-cohort-regime-v1/continuous-phase-context.json)。

## 其余机制为什么没有入选

- 早期动量、价格序列、市场扩张/修复、龙头更替、同伴扩散、跟随和局部接力均有保存记录。早期不少是净目标命中、等待时间和独立事件风险观察，不是完整现金账户；不能用高点碰到目标替代真实收益。后续旧方向账户筛查中，有成交的五组均亏，局部接力零成交不构成优势。

- 主散持续主导、方向量统计、压力回踩、成交额持续和价格活动一致等，测试的是日线可观察量。独立主散类账户为负；收缩加压力旧期改善，新期约持平或亏损，增量不稳。金额交叉及匹配伪锚进一步显示，极少成交的低回撤不能说明真实领涨关系，更不能说明资金身份。

- 市场广度、共同样本变化、强势队列反馈、强弱相对反馈和联合状态都有做过。四个固定环境门控未形成重复增量。relativeRepair 的未修复匹配对照新期约 +90.86%，但旧期约 −36.21%，旧期闭仓 PF 仅 0.243；不因单期大赚优先选它。

- 缺口不回补、成交额交接、原领涨下跌后成员保持等有完整账户试验；多数改善表现为减少曝光和少亏，尚无普遍盈利。原领涨列增量预测和阶段覆盖属于零账户诊断，不能当新交易策略成绩。

- 在线闭仓反馈、全机会独立标签扩库、共同拟合支持切换已按时钟约束试验。支持覆盖和误差改善不等于账户收益改善；同一 UR 母信号上反复细分不算增加独立机制。本次停止研究，不再追加新模型或失败口袋过滤。

各条目的精确原状态、来源和结论见末尾完整索引及两份证据审阅文件。未执行讨论和原失败尝试保留，不能因后来某个相关分支已跑就覆盖历史记录。

原始草案也保留：`threeStageContractionBreakoutDraft` 后来另行冻结实施；`oversoldStabilizationRepairDraft`、`breadthRepairRelativeStrengthDraft` 未见以原稿身份完成账户，不能把后来相关修复研究视为原稿已执行。同伴后确认、固定群体局部接力、日内与隔夜分解的早期提案有后续相关执行，附录做关联而不重复计数。固定掩码路由曾延期；本次不会因这些未执行条目重新开启研究。

## 第 49 批最终检查

量先价一步（OBV）试验的 12 个账户和读出核验均通过；原 Top5 机会 12,721 个，两政策处置 25,442 条。候选 volumeLead 仅 3/6 组提高账户回报，绝对盈利仅新主板一组：+23.40%、最大回撤 25.94%、120 笔闭仓、PF 1.162。其闭仓净收益约 111,677 元、未平浮盈约 5,333 元，正值并非主要靠期末浮盈，但另外五组均亏。

候选六组总收益按旧主板/创业/科创、新主板/创业/科创依次为 −78.56%、−72.80%、−29.80%、+23.40%、−79.27%、−22.89%。因此不把它列入三选，也不因最后一批有局部正例继续开启下一研究。

来源：[49完整读出](G:/CODE/A-trade/artifacts/strategy-search/volume-lead-breakout-v1/readout.json)、[49读出核验](G:/CODE/A-trade/artifacts/strategy-search/volume-lead-breakout-v1/readout-validation.json)。

## 应用中的当前环境与选股

入口：**策略与回测 → 实验策略 → 分析当前环境与选股**。程序只读已配置的本地通达信日线，以已声明交易日历对齐，运行三组原始选股规则；收缩只扫描主板，持续强势和支撑收回分别在主板、创业板、科创板内排名。页面展示候选股票、原条件值、排名和覆盖信息，不下单。

已完成一次真实扫描：请求日期为 **2026-10-17**，应有最近交易日为 **2026-10-16**，但本地可比较日线实际只到 **2026-10-15**，落后 1 个交易日。读取 5,261 只股票，其中 5,215 只有截止日日线（99.13%），读取失败 0；缺失没有补值。以下是 **10 月 15 日的历史截止扫描**，不能当成 10 月 17 日的新信号，应更新通达信后再扫描。

| 实验策略 | 此次条件匹配数量 | 范围 |
|---|---:|---|
| 收缩突破·停滞退出 | 0 | 主板 |
| 持续强势·再次突破 | 0 | 三板分别排名 |
| 支撑收回·波动跟踪 | 15 | 主板、创业板、科创板各 5 |

截至该日，可观察样本的 MA20 上方占比 14.77%（5,181 只有效）、MA60 上方占比 22.47%（5,118 只有效）、20 日收益中位数 −8.82%（5,180 只有效），页面描述为“弱势收缩”。这只是价格参与度和量能的解释性分档，**不是已验证的收益适配评分，也不改变原始信号准入**；三策略均同时提示覆盖不完整。成交额指标是完整价量窗口内“个股当日额/自身前20日均额”的中位数，不能解读成全市场成交额水平。

15 个支撑收回匹配只证明当日价量条件满足，不能推断主力身份、保证反弹、当作已成交持仓或下一开盘可以买到。当前版本不包含财务基本面筛选；真实持仓的保护阈值和退出需要入场价格及持仓状态，本次扫描不生成卖出指令。当前本地覆盖也不等同历史冻结股池，因此不是此前收益的复验。

来源：[实际扫描结果](G:/CODE/A-trade/artifacts/strategy-search/final-research-review-v1/current-scan-validation-0002/result.json)、[扫描检查记录](G:/CODE/A-trade/artifacts/strategy-search/final-research-review-v1/current-scan-validation-0002/verification.json)。检查记录确认本次扫描未运行资金账户回测，已有账户库、行情配置库、研究库及工作区状态文件的字节摘要保持不变。保存的结果保留原扫描时点，重新打开时另行显示新鲜度，不自动重跑或把旧日期改成今天。

## 使用边界与证据入口

所有 2022–2025 数据已经反复用于开发；本次排序本身也看过这些结果。2026 数据在更早工作中已经暴露，本轮研究没有用其回测或调参；交付时的 2026 当前扫描只是原规则的日线条件匹配，不是样本外收益验证，不能宣称天然干净的样本外。5% 收盘止损不是实际亏损上限，隔夜跳空、涨跌停、T+1、部分成交、费用和容量仍可能使损失更大。不同源的存续样本、历史状态、未复权价格、估值标记和费用版本必须保留。

三组只作为明确标注的实验策略保存，不能称“最赚钱三套”“已认证主力策略”或“稳定小亏大赚”。没有固定平均赢家、胜率、单笔净 30% 或年化收益准入门槛；本次也未授权真实券商订单。

证据：[完整机器可读总索引](G:/CODE/A-trade/artifacts/strategy-search/final-research-review-v1/research-overview.json)、[早期29家族审阅](G:/CODE/A-trade/artifacts/strategy-search/final-research-review-v1/early-review.json)、[后期26目录审阅](G:/CODE/A-trade/artifacts/strategy-search/final-research-review-v1/later-review.json)、[第49批完成时的冻结登记](G:/CODE/A-trade/artifacts/strategy-search/volume-lead-breakout-v1/program-after.json)。总索引保存三组选用的协议、plan、readout 和 proof 的字节 SHA；两份审阅覆盖有重合的证据，不相加为55个策略。

## 附录：全部 progress 节点索引

下面逐项保留原登记状态。早期草案若有后来实际执行的相关分支，会另外标注；不把原条目状态静默改写。没有单独文件的元数据可通过总索引的 programPointer 精确定位。

### 早期价格与序列信号

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| baseline | completed-development-diagnostic | 原program:/progress/baseline |
| extensions | completed-development-diagnostic | 原program:/progress/extensions |
| sequentialCandidate | implemented-synthetic-tests-only | [signal_sequence.py](G:/CODE/A-trade/service/signal_sequence.py)；后来相关执行：rawPriceSequentialDiagnostic |
| rawPriceSequentialDiagnostic | completed-development-diagnostic-not-qualified | 原program:/progress/rawPriceSequentialDiagnostic |
| nextCandidateHypotheses | draft-not-registered | [signal-next-hypotheses-v1.json](G:/CODE/A-trade/docs/signal-next-hypotheses-v1.json) |
| rawPriceContractionDiagnostic | completed-development-diagnostic-not-qualified | 原program:/progress/rawPriceContractionDiagnostic |

### 领涨、接力与跟随

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| leadershipResearch | first-persistent-leader-trial-completed-development-only | [signal-leadership-research-v1.json](G:/CODE/A-trade/docs/signal-leadership-research-v1.json) |
| persistentLeaderTrial | completed-development-diagnostic-independently-replayed | [signal-persistent-leader-price-proxy-v1.json](G:/CODE/A-trade/docs/signal-persistent-leader-price-proxy-v1.json) |
| marketExpansionTrial | completed-development-diagnostic-independent-scope-audit-passed | [result-readout.json](G:/CODE/A-trade/artifacts/strategy-search/signal-market-expansion-v1/result-readout.json) |
| marketRepairTrial | completed-development-diagnostic-independent-scope-audit-passed | [result-readout.json](G:/CODE/A-trade/artifacts/strategy-search/signal-market-repair-v1/result-readout.json) |
| supplementalPersistentLeader | completed-fixed-method-supplemental-replication-independently-replayed | [supplemental-persistent-leader-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/supplemental-persistent-leader-readout-v1.json) |
| leadershipReplacementProposal | registered-rule-executed-see-leadershipReplacement | [leadership-replacement-research-note-v1.json](G:/CODE/A-trade/artifacts/strategy-search/leadership-replacement-research-note-v1.json)；后来相关执行：leadershipReplacement |
| supplementalMarketRepair | completed-fixed-method-replication-with-external-schema-correction-and-independent-replay | [supplemental-market-repair-result-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/supplemental-market-repair-result-readout-v1.json) |
| leadershipReplacement | completed-development-diagnostic-with-independent-replay | [leadership-replacement-result-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/leadership-replacement-result-readout-v1.json) |
| nextMechanismDiscussion | mechanism-discussion-only-not-registered-or-tested | [next-signal-mechanisms-discussion-v1.json](G:/CODE/A-trade/artifacts/strategy-search/next-signal-mechanisms-discussion-v1.json) |
| leaderPeerExpansion | completed-development-diagnostic | [leader-peer-expansion-result-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/leader-peer-expansion-result-readout-v1.json) |
| leaderMemberProposals | method-review-only-not-registered-or-executed | [leader-member-state-machine-proposals-v1.json](G:/CODE/A-trade/artifacts/strategy-search/leader-member-state-machine-proposals-v1.json) |
| leaderFollowerConfirmation | completed-development-diagnostic | [leader-follower-confirmation-result-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/leader-follower-confirmation-result-readout-v1.json) |
| leaderLocalSuccessionProposal | method-proposal-only-not-registered-or-executed | [leader-local-succession-proposal-v1.json](G:/CODE/A-trade/artifacts/strategy-search/leader-local-succession-proposal-v1.json)；后来相关执行：leaderLocalSuccession |
| leaderLocalSuccession | completed-development-diagnostic | [leader-local-succession-result-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/leader-local-succession-result-readout-v1.json) |
| earlyStrengthEmergence | completed-development-diagnostic | [early-strength-emergence-result-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/early-strength-emergence-result-readout-v1.json) |
| leaderLagFollowers | completed-development-direction-policy-comparison-independently-verified-not-promoted | 原program:/progress/leaderLagFollowers |
| leaderAmountHandoff | completed-development-experiment | 原program:/progress/leaderAmountHandoff |
| leaderMatchedAnchor | completed-development-experiment | 原program:/progress/leaderMatchedAnchor |
| leaderPullbackRetention | completed-development-experiment | 原program:/progress/leaderPullbackRetention |
| leaderIncrementalPrediction | completed-development-prediction-diagnostic | 原program:/progress/leaderIncrementalPrediction |
| leaderStageCoverage | completed-development-coverage-diagnostic | 原program:/progress/leaderStageCoverage |
| leaderPersistenceAdmission | completed-development-account-comparison | 原program:/progress/leaderPersistenceAdmission |

### 成交量、金额与盘口代理

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| sessionDecomposition | completed-development-diagnostic | [session-decomposition-result-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/session-decomposition-result-readout-v1.json) |
| forceStatisticsScreen | completed | [comparison.json](G:/CODE/A-trade/artifacts/strategy-search/force-statistics-screen-v1/comparison.json) |
| forceContextScreen | completed | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/force-context-screen-v1/readout.json) |
| supplementalForceContextScreen | completed-qualified-historical-diagnostic | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/supplemental-force-context-screen-v1/readout.json) |
| peerAmountPersistence | completed-independently-verified-not-promoted | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/peer-amount-persistence-v1/readout.json) |
| peerPriceActivityAlignment | completed-independently-verified-developmental-improvement-not-promoted | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/peer-price-activity-alignment-v1/readout.json) |
| volumeAnchorAcceptance | completed-development-acceptance-policy-comparison-independently-verified-not-promoted | 原program:/progress/volumeAnchorAcceptance |
| fullGapRetention | completed-development-experiment | 原program:/progress/fullGapRetention |
| volumeLeadBreakout | completed-development-account-comparison | 原program:/progress/volumeLeadBreakout |

### 账户退出与独立执行

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| datedFeeEventAdapter | reviewed-synthetic-prototype-only | 原program:/progress/datedFeeEventAdapter |
| fixedSignalAccount | reviewed-synthetic-account-only | 原program:/progress/fixedSignalAccount；后来相关执行：accountExitComparison |
| accountExitComparison | completed-development-account-comparison | [account-exit-comparison-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/account-exit-comparison-readout-v1.json) |
| directionAccountScreen | completed | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/direction-account-screen-v1/readout.json) |
| contractionPolicyScreen | completed | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/contraction-policy-screen-v1/readout.json) |

### 市场环境与修复

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| marketStatePanel | completed-independent-replay-passed-observation-only | [signal-market-state-panel-v1.json](G:/CODE/A-trade/docs/signal-market-state-panel-v1.json) |
| supplementalMarketState | actual-observations-completed-and-independently-replayed | [supplemental-market-state-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/supplemental-market-state-readout-v1.json) |
| causalRegimeAdaptation | routing-deferred-after-distinctness-review | [causal-regime-adaptation-outline-v1.json](G:/CODE/A-trade/artifacts/strategy-search/causal-regime-adaptation-outline-v1.json) |
| marketPhaseAtlas | completed-descriptive-first-edition | 原program:/progress/marketPhaseAtlas |
| marketContextScreen | completed-qualified-historical-comparison | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/market-context-screen-v1/readout.json) |
| relativeRepairScreen | completed-qualified-historical-comparison | [readout-v2.json](G:/CODE/A-trade/artifacts/strategy-search/relative-repair-screen-v1/readout-v2.json) |
| relativeRepairCostStress | completed-qualified-historical-cost-comparison | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/relative-repair-cost-stress-v1/readout.json) |
| relativeRepairContextDiagnostic | completed-qualified-continuous-context-diagnostic | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/relative-repair-context-diagnostic-v1/readout.json) |
| relativeRepairForwardPath | completed-qualified-all-opportunity-path-diagnostic | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/relative-repair-forward-path-v1/readout.json) |
| relativeRepairVolatilityStop | completed-qualified-historical-negative-comparison | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/relative-repair-volatility-stop-v1/readout.json) |
| strengthFeedback | completed-development-feedback-comparison-independently-verified-not-promoted | 原program:/progress/strengthFeedback |
| reclaimRelativeFeedbackContext | completed-development-context-diagnostic-independently-verified-not-promoted | 原program:/progress/reclaimRelativeFeedbackContext |
| jointCohortRegime | completed-descriptive-development-diagnostic | 原program:/progress/jointCohortRegime |
| boardEnvironmentContext | completed-descriptive-development-diagnostic | 原program:/progress/boardEnvironmentContext |

### 支撑收回与退出变化

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| strongUndercutReclaimTiming | completed-independently-verified-developmental-trial-not-promoted | [readout.json](G:/CODE/A-trade/artifacts/strategy-search/strong-undercut-reclaim-timing-v1/readout.json) |
| undercutReclaimContextDiagnostic | completed-descriptive-diagnostic-independently-verified-not-promoted | 原program:/progress/undercutReclaimContextDiagnostic |
| reclaimBreadthEntry | completed-development-comparison-independently-verified-not-promoted | 原program:/progress/reclaimBreadthEntry |
| boardCoverageReclaim | completed-development-coverage-replication-independently-verified-not-promoted | 原program:/progress/boardCoverageReclaim |
| reclaimExitTransfer | completed-development-exit-transfer-independently-verified-not-promoted | 原program:/progress/reclaimExitTransfer |
| reclaimVolatilityTrail | completed-development-volatility-trail-independently-verified-not-promoted | 原program:/progress/reclaimVolatilityTrail |
| reclaimExitMechanism | completed-development-account-decomposition-independently-verified-not-promoted | 原program:/progress/reclaimExitMechanism |
| reclaimBreakoutVolatilityTrail | completed-development-staged-exit-independently-verified-not-promoted | 原program:/progress/reclaimBreakoutVolatilityTrail |

### 在线反馈与独立标签

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| onlineFeedbackEntry | completed-development-online-feedback-independently-verified-not-promoted | 原program:/progress/onlineFeedbackEntry |
| independentShadowLabels | completed-development-diagnostic | 原program:/progress/independentShadowLabels |
| allOpportunityFeedback | completed-development-experiment | 原program:/progress/allOpportunityFeedback |
| commonSupportSwitch | completed-development-experiment | 原program:/progress/commonSupportSwitch |

### 数据、成本与质量证据

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| pathRiskAudit | completed-post-hoc-development-audit | 原program:/progress/pathRiskAudit |
| publicEvidenceSupplement | completed-bounded-public-fetch-normalization-and-price-reconciliation | 原program:/progress/publicEvidenceSupplement |
| historicalUniverseCoverageAudit | completed-offline-four-date-audit | 原program:/progress/historicalUniverseCoverageAudit |
| missingHistorySupplement | completed-bounded-quality-fetch | 原program:/progress/missingHistorySupplement |
| missingHistoryCoverageRound | partial-data-recovery-completed-offline-audit | 原program:/progress/missingHistoryCoverageRound |
| allSecurityBasicProbe | failed-before-data-query | 原program:/progress/allSecurityBasicProbe |
| delistingPlaceholderAudit | completed-three-fixed-issuer-raw-audit | 原program:/progress/delistingPlaceholderAudit |
| leadershipFeaturePanel | completed-raw-feature-extraction-independently-replayed | [signal-leadership-panel-v1.json](G:/CODE/A-trade/docs/signal-leadership-panel-v1.json) |
| supplementalIntervalQuality | completed-independent-quality-audit-no-strategy-outcomes | [signal-supplemental-interval-quality-v1.json](G:/CODE/A-trade/docs/signal-supplemental-interval-quality-v1.json) |
| supplementalSource | completed-independent-cutoff-source-verification-no-strategy-outcomes | [supplemental-source-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/supplemental-source-readout-v1.json) |
| supplementalFeaturePanel | completed-unchanged-features-with-independent-numeric-and-additional-closure-audits | [supplemental-panel-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/supplemental-panel-readout-v1.json) |
| officialRightsPilot | official-archive-parsed-and-one-event-reconciled-with-limited-coverage | [official-rights-pilot-root-readout-v1.json](G:/CODE/A-trade/artifacts/strategy-search/official-rights-pilot-root-readout-v1.json) |
| historicalTransferFeeAudit | static-model-confirmed-historical-change-reported-exact-primary-notice-retrieval-incomplete | [historical-transfer-fee-model-gap-v1.json](G:/CODE/A-trade/artifacts/strategy-search/historical-transfer-fee-model-gap-v1.json) |
| historicalFeeIncrementalReview | incremental-evidence;early-transfer-and-broker-tariff-gaps-remain | [historical-fee-gap-review-v1.json](G:/CODE/A-trade/artifacts/strategy-search/historical-fee-gap-review-v1.json) |
| researchFeeProfile | implemented-synthetic-tests-only-not-integrated | 原program:/progress/researchFeeProfile |
| datedFeeIntegrationPlan | proposal-only-not-integrated | [dated-fee-integration-plan-v1.json](G:/CODE/A-trade/artifacts/strategy-search/dated-fee-integration-plan-v1.json)；后来相关执行：datedFeeEventAdapter |
| corporateActionEvidenceReview | issuer-terms-and-two-provider-rows-verified;account-repair-pending | 原program:/progress/corporateActionEvidenceReview |

### 研究登记与目标元数据

| 条目 | 原登记状态 | 主要证据 / 后续关联 |
|---|---|---|
| completedFixedCandidateTrials | 登记元数据 | 原program:/progress/completedFixedCandidateTrials |
| currentScopeTestsPassed | 登记元数据 | 原program:/progress/currentScopeTestsPassed |
| currentScopeTestEvidence | 登记元数据 | 原program:/progress/currentScopeTestEvidence |
| lastTurnClassification | 登记元数据 | 原program:/progress/lastTurnClassification |
| nextStage | 登记元数据 | 原program:/progress/nextStage |
| knownProxyLimitations | 登记元数据 | 原program:/progress/knownProxyLimitations |
| completedSupplementalReplications | 登记元数据 | 原program:/progress/completedSupplementalReplications |
