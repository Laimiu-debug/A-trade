# A-trade 静态 UI 示意生成提示

使用内置 imagegen 工具生成三张浅色静态页面，并进行局部校准。所有行情、收益、账户金额、策略与日期属于设计示例。文件位置见 [UI 设计初稿](../../ui-design-draft.md)。

## overview-v1.png 初始生成

```text
Use case: ui-mockup. Asset type: static high-fidelity Windows desktop UI design reference for A-trade, a personal Chinese A-share review and research application. Create one polished, front-facing application screenshot, approximately 1600 by 1000 pixels, without a laptop, device frame, perspective, browser chrome, or surrounding marketing material.
Visual system: LIGHT theme, white panels on #F5F7FB canvas; #FFFFFF sidebar; #172333 main text; #59677A secondary text; #2457E5 blue primary controls and navigation; #DCE3ED fine borders; #C7383F for positive Chinese market values and #15803D for negative values, always accompanied by plus/minus signs or text. Comparative charts use blue, purple and teal, with gray dashed benchmark. Elegant Chinese sans-serif typography similar to Microsoft YaHei UI, tabular numerals, readable labels, professional information hierarchy, modest 8-12 px rounded panels, very subtle shadows. Airy but practical financial software, crisp grids, no giant hero banner.
Shared shell: 224 px left sidebar, 56 px top toolbar, 28 px bottom status bar. Left brand 'A-trade'. Sidebar section labels exactly '账户与复盘', '市场与行情', '策略与回测', '分析工具', '设置'. Global top bar has stock search, '任务', and an obvious small '设计演示' badge. Bottom status shows '本机保存' and '自动备份'. All stock-code appearances MUST include the stock name, use exactly six numerical digits, keep any leading zero, and never add exchange letter prefixes. Use '长江电力 600900' for the example stock whenever a code appears. Codes should look clickable in blue. All financial values are explicitly synthetic design samples, not current market information. Chinese labels must be readable and spelled correctly. Do not use English lorem ipsum.
Screen: account overview. Active section '账户与复盘', active page '总览'. Page title exactly '账户总览'; compact selectors '实盘账户' and '最近一年'. The TOPMOST content is a beautiful horizontal swing milestone strip with three distinct, restrained achievement cards titled '第 01 波段', '第 02 波段', '第 03 波段', small blue wave/medal emblems, synthetic signed return and holding-duration summaries. The active achievement has a soft pale-blue background; emphasize chronological progress and achievement without gaming visuals.
Below milestones, four compact metrics for '总资产', '累计收益', '持仓市值', '最大回撤'. Main area: large blue cumulative-return curve with gray dashed benchmark, legends '账户收益' and '基准'; a synchronized smaller drawdown plot immediately below. Right column: next-day plan card titled '次日计划', showing '长江电力 600900' with '查看计划'; beneath it a concise '数据状态' card with TDX and MCP freshness labels. Bottom content is a holdings table with stock-name-plus-code cells, quantity, signed PnL and a small sparkline. Charts occupy the largest share of useful space. Some content can continue below the viewport. No confetti animation is depicted; this is a calm still screenshot.
```

## backtest-v1.png 初始生成

```text
Use case: ui-mockup. Asset type: static high-fidelity Windows desktop UI design reference for A-trade, a personal Chinese A-share review and research application. Create one polished, front-facing application screenshot, approximately 1600 by 1000 pixels, without a laptop, device frame, perspective, browser chrome, or surrounding marketing material.
Visual system: LIGHT theme, white panels on #F5F7FB canvas; #FFFFFF sidebar; #172333 main text; #59677A secondary text; #2457E5 blue primary controls and navigation; #DCE3ED fine borders; #C7383F for positive Chinese market values and #15803D for negative values, always accompanied by plus/minus signs or text. Comparative charts use blue, purple and teal, with gray dashed benchmark. Elegant Chinese sans-serif typography similar to Microsoft YaHei UI, tabular numerals, readable labels, professional information hierarchy, modest 8-12 px rounded panels, very subtle shadows. Airy but practical financial software, crisp grids, no giant hero banner.
Shared shell: 224 px left sidebar, 56 px top toolbar, 28 px bottom status bar. Left brand 'A-trade'. Sidebar section labels exactly '账户与复盘', '市场与行情', '策略与回测', '分析工具', '设置'. Global top bar has stock search, '任务', and an obvious small '设计演示' badge. Bottom status shows '本机保存' and '自动备份'. All stock-code appearances MUST include the stock name, use exactly six numerical digits, keep any leading zero, and never add exchange letter prefixes. Use '长江电力 600900' for the example stock whenever a code appears. Codes should look clickable in blue. All financial values are explicitly synthetic design samples, not current market information. Chinese labels must be readable and spelled correctly. Do not use English lorem ipsum.
Screen: research and backtest workspace. Active section '策略与回测'. Page title '回测研究'. Near the top show mode tabs '单策略', '多策略', '交集', '收益平原'; active '交集'. Left configuration panel about 280 px wide has '研究配置', a stock chip '长江电力 600900', two selected sample strategy chips, an environment filter, train/validation date-range controls, and a concise label '晚间判断 · 次交易日执行'. Use modest input fields and one blue '运行研究' action.
Wide results panel: tab row '结果', '操作路径', '参数稳定性', '报告'. Active '结果'. Main display is a large blue return curve and separate green/neutral drawdown subplot on identical date axes; purple and gray dashed comparison curves with explicit legends. Small metric cards show signed return, drawdown, sample count and '验证区间'. Below curves, a compact parameter heatmap labeled '参数稳定性', using sequential pale-to-deep blue cells rather than red-green. Selected cell has a clear border.
A compact bottom-running-task bar reads exactly '收益平原研究 62% · 186 / 300 组' with a real filled progress bar and controls '暂停', '停止', and a visibly inactive '继续'. Label this running task '阶段结果', distinguishing it from the completed selected backtest above. Top right actions '保存报告' and '生成计划'. Keep the interface realistic, grid-aligned and readable.
```

## stock-analysis-v1.png 初始生成

```text
Use case: ui-mockup. Asset type: static high-fidelity Windows desktop UI design reference for A-trade, a personal Chinese A-share review and research application. Create one polished, front-facing application screenshot, approximately 1600 by 1000 pixels, without a laptop, device frame, perspective, browser chrome, or surrounding marketing material.
Visual system: LIGHT theme, white panels on #F5F7FB canvas; #FFFFFF sidebar; #172333 main text; #59677A secondary text; #2457E5 blue primary controls and navigation; #DCE3ED fine borders; #C7383F for positive Chinese market values and #15803D for negative values, always accompanied by plus/minus signs or text. Comparative charts use blue, purple and teal, with gray dashed benchmark. Elegant Chinese sans-serif typography similar to Microsoft YaHei UI, tabular numerals, readable labels, professional information hierarchy, modest 8-12 px rounded panels, very subtle shadows. Airy but practical financial software, crisp grids, no giant hero banner.
Shared shell: 224 px left sidebar, 56 px top toolbar, 28 px bottom status bar. Left brand 'A-trade'. Sidebar section labels exactly '账户与复盘', '市场与行情', '策略与回测', '分析工具', '设置'. Global top bar has stock search, '任务', and an obvious small '设计演示' badge. Bottom status shows '本机保存' and '自动备份'. All stock-code appearances MUST include the stock name, use exactly six numerical digits, keep any leading zero, and never add exchange letter prefixes. Use '长江电力 600900' for the example stock whenever a code appears. Codes should look clickable in blue. All financial values are explicitly synthetic design samples, not current market information. Chinese labels must be readable and spelled correctly. Do not use English lorem ipsum.
Screen: shared stock daily-chart and analysis detail. Active section '市场与行情'. Prominent top-left '返回' button followed by page title '长江电力 600900' and subtitle '日线与分析'; preserve visible six-digit code with name and blue clickable styling. Header has compact source/date freshness and an obvious '设计演示' badge, watch-pool action '加入跟踪', and a '研究' action.
Dominant center: spacious professional daily candlestick chart using A-share red-up and green-down candles, thin blue/purple/amber moving-average lines, sparse pale grid, legible axes; separate volume subplot below. Include exactly three operation marker callouts '买入', '减仓', '退出' with distinct blue, amber and purple marker shapes. A date-range toolbar includes '3个月', '半年', '一年', '全部'.
Right information column about 320 px wide shows '研究依据' with a concise condition list and an evidence link, then a '跟踪事件' timeline with synthetic rows '入选观察', '断板记录', '再次涨停'; describe these as design examples, not actual market history.
Below the chart, tabs '策略信号', '估值参考', '交易记录', '跟踪事件'. Selected '交易记录' displays a clean compact table with synthetic transactions, every stock-code cell paired with '长江电力'. A small date-selection indicator visually links the chart and transaction table. Include '次日计划' in the right column with a '查看计划' action, without investment instructions or promised gains.
```

## overview-v1.png 局部校准

```text
Edit this UI mockup only to align navigation and action labels with the approved design; keep the entire light palette, layout, milestone strip, charts, card sizes, example data and all name-plus-six-digit-code stock cells. Replace the sidebar content with exactly these five parent modules, spelled verbatim: 账户与复盘, 市场与行情, 策略与回测, 分析工具, 设置. Under the active 账户与复盘 parent show only six child shortcuts: 总览, 交易流水, 资金流水, 每日复盘, 次日计划, 灵感闪记; 总览 is selected. Do not show invented extra menu groups. In every holdings-table row replace the 买入 / 卖出 / 更多 action cluster with 查看 / 计划 / 更多, retaining the same visual sizing. The next-day plan card should contain only research-plan labels and 查看计划, without purchase-price instructions: replace its three detailed action-price rows with 计划状态 待确认, 关联研究 波段研究示例, 执行日 下一交易日. This is a static planning and review interface, not a brokerage execution screen.
```

## backtest-v1.png 局部校准

```text
Edit this light research UI mockup only to make the training-versus-validation visualization and sample drawdown internally consistent. Preserve all layout, sidebar, Chinese title, stock-name-plus-six-digit-code appearance, heatmap, controls and task-progress bar. In BOTH the cumulative-return plot and the drawdown plot, shade the history from 2018 through 2021 very lightly and label it 寻参区间. Clearly mark a vertical boundary at 2022-01 and label 2022 through 2024 验证区间; add small text 图示为全区间路径，统计卡仅计算验证区间. Redraw the drawdown subplot as a restrained synthetic curve that never drops below -22.34% throughout the plot, with y ticks 0%, -10%, -20%, -30% and a bottom trough explicitly at -22.34% inside the validation period; do not label the last point -22.34% unless it is also that trough. Keep 最大回撤 -22.34% at the top as the validation metric. Keep 设计演示 obvious. No real-data or performance-accuracy claims.
```

## stock-analysis-v1.png 局部校准

```text
Edit this stock-analysis UI mockup only to make the right-column tracking timeline chronologically coherent. Preserve all layout, light palette, candlestick/volume charts, operation markers, transaction table, sidebar, header and stock name 长江电力 with six-digit code 600900. In 跟踪事件 replace all three existing event rows with a TOP-TO-BOTTOM ASCENDING timeline: 2024-03-18 入选观察 (设计示例), 2024-05-20 断板记录 (设计示例), 2024-06-12 再次涨停 (设计示例). Give each row only these neutral labels, removing explanatory market-story claims. Retain the 设计演示 badge. Keep the timeline dates legible and clear.
```

## overview-v1.png 回撤标记校准

首次校准请求长时间未返回，结束该请求后使用相同提示重试；本地交付采用重试后完成的图片。

```text
Edit this account-overview UI screenshot only to fix the incorrect sign on the right-end label of the drawdown subplot. The line is below the zero line, so that label must read EXACTLY '-12.36%' with a MINUS SIGN. The top metric 最大回撤 already says -12.36% and must remain unchanged. Preserve every other pixel as closely as possible: five-module navigation, milestone cards, account metrics, table, all stock names and six-digit codes, neutral plan labels, pale palette, typography and charts. Do not change dates, values, letters, icons, or layout anywhere else.
```
