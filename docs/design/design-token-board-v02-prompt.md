# 设计令牌视觉总览生成记录

版本 V0.2，2026-10-10。使用内置 imagegen 生成一张完整视觉规范板，供配色、组件、通行证、多任务与动画分镜评审。图中目标、任务、行情及资源数字均为示例。精确令牌以 [设计令牌规范](../design-tokens.md) 为准。

## 生成提示

整体图另配两张放大细节板，分别展示视觉基础与通用组件、图表与业务组件。

```text
Use case: ui-mockup.
Asset type: one complete visual design-token specimen board for the personal Windows A-share research app A-trade. This is a flat, front-facing, carefully typeset design system artboard, not a desktop app screenshot, not a marketing poster, not a device mockup.
Primary request: draw the entire existing V0.2 light design system so the user can judge all visual ingredients together. Include real-looking UI specimens, color chips, type scale, spacing and layout measurements, component states, chart language, a configurable long-term ACCOUNT-RETURN PASS, a MULTI-TASK research queue, and motion storyboards.
Canvas: tall portrait 2160 x 3840, high resolution with extremely legible Simplified Chinese labels. Generous orderly grid, large panel titles, tidy alignment, restrained white cards on #F5F7FB. Use the full canvas without clipping. Small technical captions should still be readable when zooming. Render text crisply like a finished design-system sheet. Most content is visual examples rather than paragraphs.
Header: wordmark "A-trade" and title "浅色设计令牌总览", subtitle "V0.2 · 视觉基础 / 组件 / 图表 / 通行证 / 多任务 / 动效". Small chip "设计规范 · 示例数据". Use dark navy headings and restrained blue rules. No giant decorative logo, 3D objects, people, devices, photography, gradients filling the whole board, or fantasy-game decoration.

Layout: eight numbered, clearly bounded panels on a consistent grid. Panels 01 and 02 side by side in the first row; 03 and 04 side by side next; then 05, 06, 07, 08 across the full width, with sufficient height for charts, pass and queue. Overall feel: calm, precise, professional, approachable. The PASS gets a polished collectible badge style but remains a clean financial workstation.

01 "配色与语义"
Draw orderly labeled swatches with EXACT hex labels:
- 页面 #F5F7FB, 工作面 #FFFFFF, 次级面 #F0F3F8
- 主文字 #172333, 次文字 #59677A, 禁用文字 #6B7280
- 轻边界 #DCE3ED, 控件边界 #7C8BA1
- 主色 #2457E5, 悬停 #1D46BE, 按下 #173A9C
- 上涨 #C7383F, 下跌 #15803D, 警告 #A16207, 错误 #B91C1C
- 选中 #E8EFFF, 悬停底 #EEF3FF
Small pale paired state backgrounds #FFF2F2, #EDF9F0, #FFFBEB, #FFF1F2.
Positive returns must be red with plus sign; negative returns green with minus sign. Errors are separately labeled and have an error icon. Never rely only on color.

02 "文字与间距"
Use Microsoft YaHei UI / Segoe UI appearance. Show the samples at visibly different scales with size / line-height labels:
"页面标题 24/32", "分区标题 18/26", "卡片标题 16/24", "正文文字 14/22", "辅助说明 12/18", "账户指标 28/36", "通行证指标 32/40".
Show a clearly aligned numeric specimen "1,258,432.17" and return pair "+12.00%" in red, "-3.20%" in green. Use tabular numerals.
Show seven blue spacing rulers labeled 4, 8, 12, 16, 24, 32, 48 px. Rulers must visually increase.
Show corner samples "控件 6px", "卡片 10px", "浮层 12px", plus "边框 1px · 焦点 2px".
Small outlined icons for search, back, settings, play, pause, stop: "图标 18/20px".

03 "基础组件与状态"
Show large, legible specimens of the same primary button in labeled normal, hover, pressed, focused, disabled, loading states. Button words "开始研究"; loading word "运行中". Focus gets a blue 2px outline. Secondary button "保存配置", danger outline button "停止任务".
Show normal search field containing "长江电力 600900", focused numeric field labeled "阶段目标" value "20" suffix "%", and an error field with nearby "请输入有效数值". Labels above inputs.
Show checkboxes unchecked, checked, indeterminate. Include "全选当前结果", segmented market choices "主板 / 创业板 / 科创板", chips "运行中 / 排队 / 已暂停 / 已完成 / 失败".
Show tab selected "收益曲线" beside "操作记录", switch "减少动态效果".
Show small readable feedback examples "已保存", "保存失败 · 重试", "暂无数据", and missing value "—". A visible line says "控件 32 / 36 / 40px".
Any time a stock code is visible it must be exactly six digits AND immediately paired with its stock name. No sh/sz/bj prefixes anywhere.

04 "布局与层级"
Draw a mini wireframe of the desktop layout with exact measurement annotations: canvas 1440×900, sidebar 224px, top bar 56px, bottom status 28px, page padding 24px, right inspector 360px. Inside the content show a 12-column guide and a main chart with side details. This is a layout ruler diagram, not a dense screenshot.
Show compact alternatives "窄窗导航 72px" and "表格行高 36 / 44 / 56px".
Draw gently offset layer tiles labeled "内容 0", "固定栏 10", "菜单 20", "抽屉 30", "对话框 40", "提示 50", "通知 60".
Show a soft dialog shadow specimen, with technical captions "阴影 0 12 32 · 12%" and "遮罩 24%".
Footer chips "100% / 125% / 150% 缩放".

05 "图表与金融组件"
Draw four crisp mini charts in a row or 2x2:
A: blue account return curve with grey dashed benchmark. Legend "账户收益 / 基准"; positive ending label "+18.20%" red.
B: drawdown plot with zero at the TOP and a green series entirely AT OR BELOW zero. Label "回撤" and negative trough "-8.40%" with MINUS SIGN. No positive drawdown labels.
C: red-up, green-down daily candlesticks and volume, titled "长江电力 600900 · 日线示意".
D: a 5-by-5 parameter heatmap with sequential blue cells and a color legend, title "收益平原", short caption "指标与单位单独标注".
Below the charts show series swatches #2457E5, #7C3AED, #0F766E, #A16207 and dashed #59677A; line weights "主线 2px / 对比 1.5px".
Show event glyphs with labels: blue upward triangle "买入", amber diamond "减仓", purple downward triangle "退出", red square "止损", grey vertical dashed "风格变化".
Show one stock table specimen row "长江电力 600900", selected checkbox, "+12.00%" red, buttons "查看 / 计划", and a return control "返回". Keep all numbers illustrative.

06 "账户收益通行证"
This is the hero business component, a long-term progress PASS, NOT a handful of isolated single-trade cards. Beautiful connected numbered nodes, completed blue badges, current outlined node, future light-grey nodes, and larger chapter medal silhouettes. Light-blue accents, dimensionality only via restrained shadows, no game currencies or reward chests.
IMPORTANT: Stage target and total stage count are USER CONFIGURABLE. Do not hard-code 30% or 70.
Show a compact editable configuration on the right: label "阶段目标" value "20%" and label "阶段总数" value "40", button "设置目标". Add explicit nearby small label "示例，可自定义".
Show summary EXACTLY: "账户整体收益推进", "已达成 3 / 40 阶段", "当前第 4 阶段", "本阶段 +12.00% / 目标 20.00%".
Draw a horizontal path of nodes 01 through 10: 01,02,03 completed and connected in blue; 04 highlighted as CURRENT; 05–10 future. Include chapter tabs "01–10", "11–20", "21–30", "31–40" and controls "展开全部 / 定位当前".
Show an elegant selected-stage detail card with small illustrative account curve, "目标与实际收益", and "查看阶段记录". Overall progress represents 3/40, not 3/10. The currently selected stage is 04. No single-stock return should unlock this account pass.

07 "多回测任务中心"
A realistic compact queue component with six aligned rows, not one progress bar. Top summary EXACTLY "运行 2 · 排队 3 · 暂停 1". Show "自动分配", a small illustrative resource line "CPU 54% · 内存 4.2 / 8.0 GB", labeled "示意".
Toolbar: "全选当前结果", "批量暂停", "批量继续", "停止选中", "暂停队列调度".
Columns "任务 / 对象", "状态与阶段", "进度", "操作".
Six rows:
- "单策略研究 · 长江电力 600900": blue 62% bar, "运行中", buttons "暂停 / 停止".
- "交集研究 · 股票池 24只": blue 31% bar, "运行中", buttons "暂停 / 停止".
- "收益平原 · 股票池 30只": "排队 · 等待资源", no fake percentage, "调整优先级".
- "多策略对比 · 长江电力 600900": "排队", no percentage, "调整优先级".
- "批量估值 · 股票池 120只": "排队", no percentage, "调整优先级".
- "单策略研究 · 贵州茅台 600519": grey 48% bar, "已暂停", buttons "继续 / 停止".
Small note "关闭结果页不停止任务 · 保存进度后可恢复". Clearly distinguish per-row controls and batch controls. Use stable row alignment and restrained icons. Resource values and tasks are illustration only.

08 "动画节奏与分镜"
A static motion-spec row, not claiming to be animated. Label "静态分镜".
Draw five simple successive frames of the same pass node: "0ms 待达成", "点亮 700ms", "章节徽章 900ms", "终点徽章 1100ms", "减少动态：直接呈现". Include small arrows. Do not flash, particle-explode, or animate numbers.
Underneath draw timing pills "反馈 120ms", "切换 180ms", "浮层 240ms", "曲线首显 320ms".
Show max-motion ruler "节点位移 ≤4px · 抽屉位移 ≤12px", "缩放与游标即时响应".
Small footer "视觉评审稿 · 数值口径以设计规范为准".

Strict constraints: white/light grey theme, blue actions, red positive / green negative financial convention, exact six-digit stock codes paired with names, explicit configurable pass targets, multiple independently controllable research tasks, complete 8-panel composition. No imaginary app functionality claims, no real market advice, no exchange prefixes, no 30% / 70 fixed-goal UI, no unrequested dark theme. The result should look like a coherent professional Chinese desktop product design-system board.
```
