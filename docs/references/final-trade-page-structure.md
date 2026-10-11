# final-trade 页面划分参考

2026-10-11，按用户指定仓库 `https://github.com/Laimiu-debug/final-trade.git` 阅读源码。参考提交：`2a00ffe7c50b5f023a5db9f1c09ace1683eb1912`。仅阅读参考项目，没有运行其服务或导入账户数据。

## 源码依据

- `frontend/src/app/routes.tsx` 与 `frontend/src/shared/components/AppShell.tsx`：以选股漏斗、待买信号、交叉验证、策略中心、策略回测、模拟交易、持仓管理、复盘统计、复盘分享等工作页面构成导航；股票图表从股票上下文进入。
- `frontend/src/pages/portfolio/PortfolioPage.tsx`：资产、现金、盈亏、持仓列表集中管理。
- `frontend/src/pages/trade/TradePage.tsx`：待成交单、交易配置、订单与成交明细同页；相邻记录类型用局部 Tabs。
- `frontend/src/pages/review/ReviewPage.tsx`：统计、权益曲线、回撤曲线、月度收益、持仓快照、成交与已平仓明细在同一复盘页面。该页同时挂载 ReviewWorkspacePanel，将笔记复盘与统计放在同一流程中。
- `frontend/src/pages/review/ReviewWorkspacePanel.tsx`：日复盘、周复盘、标签与统计的内部组织可作为本项目复盘笔记的参考。
- `frontend/src/pages/review/ReviewSharePage.tsx`：分享有独立页面职责。
- `frontend/src/pages/strategy/StrategyCenterPage.tsx`：策略参数/预设在策略中心维护；业务页选择策略。
- `frontend/src/pages/backtest/BacktestPage.tsx`：配置、任务、收益平原、收益曲线、执行明细及导出围绕同一研究组织。
- `frontend/src/pages/market-trend/MarketTrendPage.tsx`：趋势龙头与连板梯队使用同页切换。

## A-trade 的对应落点

保留已确定的五大模块、浅色设计令牌、里程碑与多任务交互，采用参考项目的业务职责划分。

| 页面 | 内容 |
| --- | --- |
| 账户总览 A02 | 总览与持仓整合，顶部里程碑；资产快照抽屉、账户数据配置 |
| 交易管理 A04 | 实盘/模拟账户记录、交易/资金流水、次日计划、导入操作 |
| 复盘统计 A08 | 统计及收益/回撤曲线、每日/周期笔记、灵感、操作执行对照 |
| 复盘分享 A11 | 回顾卡片、复盘文字与摘要导出 |
| 策略中心 R01 | 策略列表、环境与冲突说明、选择策略进入回测 |
| 回测工作区 R03 | 多任务、配置与结果；参数平原与研究对比在工作区切换，操作路径由结果进入 |
| 报告库 R08 | 已存研究摘要及报告详情 |

工具导航收为情绪估值、趋势龙头、板块资金动向、异动追踪、统一跟踪池五项；系统设置收为一个入口。资讯继续放在市场模块。

## 兼容与边界

- A03 → 总览账户配置；A07 → 总览快照；A15 → 总览里程碑。
- A05 → 交易管理导入；A06 → 资金流水；A13 → 次日计划区。
- A01/A09/A10/A12/A14 → 复盘统计及相应笔记/曲线/执行区块。
- 保留 `notes.A08`、`notes.A09`、`notes.A12`，不迁移或覆盖用户已保存文字。
- 仍是 HTML 交互预览；真实交易执行、导入解析、账户结算、研究和行情服务不在此轮实现范围。
- final-trade 的实时交易功能和 AI 等额外能力不因参考页面划分而自动引入。
