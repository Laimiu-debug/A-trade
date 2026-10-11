# 设计令牌放大细节板生成记录

使用内置 imagegen，整体规范板作为风格参考。精确数值以 [设计令牌规范](../design-tokens.md) 为准。

## 视觉基础与通用组件

```text
Use case: ui-mockup. Create a legible enlarged DETAIL SHEET for the existing A-trade light design system, using the provided image only as a style reference. This must be a new landscape design-system artboard with just FOUR large panels, not the whole eight-panel poster. Landscape about 1920x1280 or largest supported, text and swatch labels visibly large. White cards on #F5F7FB, dark #172333 headings, flat solid fills, blue #2457E5 actions. Crisp Simplified Chinese. Header "A-trade · 视觉基础与通用组件" and small "V0.2 · 放大细节". No marketing slogan, no device mockup. Exact labels and tokens below must be correct.

2x2 panels:
01 配色与语义. Draw swatches using precisely these colors, each with the correct readable HEX label. Neutral: 页面 #F5F7FB, 工作面 #FFFFFF, 次级面 #F0F3F8, 主文字 #172333, 次文字 #59677A, 禁用 #6B7280, 轻边界 #DCE3ED, 控件边界 #7C8BA1. Action: 主蓝 #2457E5, 悬停 #1D46BE, 按下 #173A9C, 选中底 #E8EFFF, 悬停底 #EEF3FF. Semantic: 上涨 #C7383F, 下跌 #15803D, 警告 #A16207, 错误 #B91C1C. Four pale background strips #FFF2F2 / #EDF9F0 / #FFFBEB / #FFF1F2. Use solid colors, avoid shiny or gradient swatches. Positive financial numbers red, negative green.

02 文字、间距与外形. YaHei UI / Segoe UI style. Display type samples with exact size/line-height labels: 页面24/32, 分区18/26, 卡片16/24, 正文14/22, 说明12/18, 指标28/36, 通行证32/40. Large tabular numeral 1,258,432.17. Show increasing spacing rulers 4/8/12/16/24/32/48px. Rounded corner specimens 控件6px / 卡片10px / 浮层12px. Border1px, focus2px, simple outline icons18/20px. Body-size labels describe the actual future app token; all sheet annotations themselves should be large enough to read.

03 组件与完整状态. Six primary buttons labeled 正常 / 悬停 / 按下 / 焦点 / 禁用 / 加载; blue gradient is NOT needed, states differ by exact solid blues above. Button text 开始研究; loading 运行中. Secondary 保存配置, danger outline 停止任务. A focused search input "长江电力 600900", number field 阶段目标 20 %, validation error 请输入有效数值. Checkboxes 空选 / 已选 / 半选 and 全选当前结果, market filter 主板 / 创业板 / 科创板. Tabs 收益曲线 / 操作记录, toggle 减少动态效果. Chips 运行中 / 排队 / 已暂停 / 已完成 / 失败 with icons. Inline feedback 已保存 / 保存失败·重试 / 暂无数据 / missing value —. Caption 控件32/36/40px; table row sample 44px. Keep stock names paired with exactly six-digit stock codes, no exchange letters.

04 桌面布局与层级. A clean Windows desktop wireframe, no macOS colored window dots. Annotate canvas1440×900, sidebar224px, top56px, bottom28px, contentpadding24px, inspector360px, 12-column grid. Mini narrow variant sidebar72px. Give seven layer chips IN THIS EXACT ORDER: 内容0 / 固定栏10 / 菜单20 / 抽屉30 / 对话框40 / 提示50 / 通知60. All seven must be present. Example soft shadow 0 12 32 · 12%, overlay24%. Windows scale100% /125% /150%. Small comparison table heights36/44/56px.

Preserve the reference's clean coherent visual style but do not copy its compressed layout, small lettering or text errors. Four panels only; roomy large specimens, no pass or task queue on this sheet.
```

## 图表与业务组件

```text
Use case: ui-mockup. Create a legible enlarged BUSINESS COMPONENT detail sheet for A-trade using the attached entire design board only as STYLE reference. New landscape artboard about1920x1280 or highest supported, just FOUR generous panels in2x2. White panels on#F5F7FB, navy#172333, blue#2457E5. Flat precise Chinese UI design, no device mockup or marketing slogan. Header "A-trade · 图表与业务组件", small "V0.2 · 示例数据 · 静态分镜". This is a design specimen, not working app.

05 图表语言. Three mini charts: account return blue line with grey dashed benchmark and +18.20% red; drawdown green BELOW zero with -8.40% minus sign; red-up/green-down K-line and volume titled 长江电力600900. Fourth mini parameter heatmap titled 收益平原: use ONLY sequential BLUE colors #EFF6FF #DBEAFE #93C5FD #3B82F6 #1D4ED8, no green cells or green legend. Clearly label metric and unit. Legend color chips #2457E5,#7C3AED,#0F766E,#A16207, dashed#59677A; 主线2px/对比1.5px. Events blue▲买入, amber◆减仓, purple▼退出, red■止损, grayverticaldash风格变化. Small linked stock row 长江电力600900 with 查看 and 返回.

06 账户收益通行证. Make a polished continuous pass path with tasteful badge nodes and chapter medals, not isolated trade cards. Editable fields labeled 阶段目标20%, 阶段总数40 and 设置目标, explicitly "示例，可自定义". Summary exact 已达成3/40阶段, 当前第4阶段, 账户整体收益推进, 本阶段+12.00%/目标20.00%. Path nodes01through10: 01-03 blue complete,04 current outlined,05-10futuregrey. Chapter tabs01–10/11–20/21–30/31–40. Buttons展开全部 and 定位当前. Selected stage card 第4阶段 with mini account curve and 查看阶段记录. Account return drives the pass. Do not show30% or70as fixed requirements. Overall progress3/40 must not be shown as3/10.

07 多回测任务. Six readable table rows with names, stages, independent bars and controls, selected checkboxes. Summary exactly 运行2·排队3·暂停1. Toolbar 全选当前结果 /批量暂停/批量继续/停止选中. CPU54% ·内存4.2/8.0GB, small示意. Rows:
单策略·长江电力600900 |运行中62%|暂停 停止
交集研究·股票池24只 |运行中31%|暂停 停止
收益平原·股票池30只 |排队·等待资源|调整优先级
多策略·长江电力600900 |排队|调整优先级
批量估值·股票池120只 |排队|调整优先级
单策略·贵州茅台600519 |已暂停48%|继续 停止.
Queued rows must not have fabricated percentages. Add 暂停队列调度 control. Small footnote关闭结果页不停止任务·保存进度后可恢复. Regular queued state grey, resource issue amber, running blue, paused grey.

08 动画与反馈. Clearly label 静态分镜. Draw a wide four-frame node sequence: 待达成 at0ms -> 阶段点亮700ms -> 章节徽章900ms -> 终点徽章1100ms. Reuse coherent blue/white collectible badge motifs. Separate minimal fifth thumbnail 减少动态·直接呈现. Draw duration strips反馈120ms /切换180ms /浮层240ms /曲线首显320ms. Rules节点位移≤4px·抽屉位移≤12px, 数值不滚动·游标即时响应. Include a small toast 多项任务已完成·查看结果. All animation is depicted using still storyboards, no claim of live movement.

All stock code occurrences paired with the corresponding stock name and exactly6digits, no sh/sz prefixes. Financial positive red#C7383F, negative green#15803D. Pass targets configurable, task results fictitious. Make labels larger and clearer than in the full-board reference.
```
