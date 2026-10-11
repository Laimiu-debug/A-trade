# 个股图形观察指标

`preview/market-indicators.js` 是不访问网络、不改写行情的纯 JavaScript 模块。
浏览器使用 `AT.marketIndicators.calculate(rows, { volumeUnit })`，Node 使用
`require('../preview/market-indicators.js').calculate(...)`。

输入按日期升序，至少包含 `date`（或 `time`）、`open`、`high`、`low`、`close`、
`volume`。所有数值必须是有限数字；成交量可以为零，价格必须为正且符合 OHLC 关系。
输出 `{ rows, metadata, notes }`，输出行与输入行一一对应，原始数据保持不变。
在完整历史上计算后按可视区间截取；滚轮缩放或平移不应改变计算起点。

## 来源与名称

主散量能公式已逐项对照本机 FINAL-TRADE 的：

- `backend/app/core/ths_volume_signal.py`
- `frontend/src/shared/utils/thsVolumeSignal.ts`
- `frontend/src/shared/charts/KLineChart.tsx`

TRADE 的 `backend/trade_app/research/ths_volume_domain.py` 使用同一套主散公式。
这里的“主力”“散户”是原指标名称，表示按收盘涨跌拆分后的平滑成交量，
**不是逐笔成交身份识别或真实资金流**。显示名称为“主散量能”。

图形量价波段按 FINAL-TRADE 的
`backend/app/core/chart_volume_swing_strategy.py` 默认参数计算，保留其图形确认、
量价健康条件和评分，不移植下单、回测执行或事后择优去重。

## 价格和成交量基础线

| 字段 | 含义 | 首次可用所需连续有效根数 |
| --- | --- | --- |
| `ma5/ma10/ma20` | 含当根收盘价简单均线 | 5 / 10 / 20 |
| `volumeMA5` | 含当根成交量简单均线，单位同输入 | 5 |
| `volumeRatio5` | 当根成交量 / 前 5 根均量，不含当根；属于区间量比 | 6 |

`volumeRatio5` 不是实时行情终端基于盘中时长计算的“量比”，界面应标注“较前 5 根均量”。

## 主散量能与事件

设 `V` 是成交量：输入为“股”时除以 100 转成“手”，输入已经为“手”时不重复转换；
其他单位保留原单位。结果单位在 `metadata.forceUnit`，不要固定写“手”。

上涨量 `U[t] = close[t] > close[t-1] ? V[t] : 0`；
下跌量 `D[t] = close[t] < close[t-1] ? V[t] : 0`；首根两者都为零。

- `mainForce = EMA(MA(U, 3), 3)`，EMA 系数为 `2 / (3 + 1)`。
- `retailForce = EMA(MA(D, 3), 10)`，EMA 系数为 `2 / (10 + 1)`。
- 内部 MA 初始阶段按已有根数取平均，EMA 用首项初始化，保留原项目计算轨迹。
- `mainForceState` 比较本根与前根主力线，取 `rising/falling/flat`。
- `purpleToYellow`：前根主力线为正且回落，本根主力线为正且回升。
- `volumeConversion` 是 `purpleToYellow` 的别名，不引入另一套转化公式。
- `goldenCross`：本根主力线大于散户线，前根主力线不高于散户线。
- `forceGap = mainForce - retailForce`。
- `forceDominance = forceGap / (mainForce + retailForce)`；分母为零时为 `null`。

金叉是主力线上穿散户线，不能用 MA5/MA10 交叉代替。绘图可用黄色表示主力线上升、
紫色表示回落、蓝色表示散户线；事件可标在对应 K 线，副图沿用同一日期坐标。

和旧版早期显示值相比，本模块收紧初始化显示：前两根主散值为 `null`，第四根起
允许金叉，第五根起允许紫转黄，确保比较点也已完成初始化。初始化后的数值和原公式一致。
数值保持计算精度，由界面格式化；事件使用未四舍五入的内部数值判断。

## 图形量价波段

计算需要 80 根连续有效 K 线。每一根只在当时可见的历史中寻找最近突破，
先看 40 根箱体，再看 30、20 根；最多向前找 18 根。

- 箱顶取箱内最高收盘价，箱底取最低低价；完整高低振幅为 6%–35%。
- 箱体后半段均量不高于前半段的 1.05 倍。
- 突破收盘超过箱顶 0.3%，成交量至少为此前 20 根均量的 1.5 倍，
  收盘位置 `(close-low)/(high-low)` 至少 0.45。
- 排除上影比例至少 0.55、成交量至少 2 倍、并且收阴的突破柱。
- 量价健康：近 20 根上涨日成交量（平盘计入）/下跌日成交量至少 1.3，
  且没有近 5 根涨幅超过 3%但均量比前 5 根缩至 75%以下的背离。
- 均线要求 MA5 > MA10 > MA20，且收盘高于 MA20。
- 突破至少两根后，最近五根低价回踩箱顶 ±4%、当根量不高于 MA5 量的 75%、
  收盘不低于箱顶 97%，得到 `pullback_retest`（缩量回踩）。
- 否则，突破至少三根后，最近两根收盘不低于箱顶 97%，且没有相对前 5 根
  均量超过 1.3 倍的阴线，得到 `hold_above`（突破站稳）。
- 确认、量价和均线都通过才评分；使用原策略箱体振幅、突破量、箱内缩量、
  涨跌量比、K 线收盘位置、箱体长度、突破时间、回踩距离、回踩缩量与确认加分权重。
  分数至少 50 才标记 `boxSignal`，75 / 62 分分别划为 A / B，否则为 C。

| 逐根字段 | 用法 |
| --- | --- |
| `boxHigh/boxLow` | 当时找到的箱体价位；没找到为 `null` |
| `boxStartIndex/boxEndIndex/breakoutIndex` | 原始输入数组位置，可定位箱体和突破柱 |
| `boxBreakout` | 当前这根就是有效突破柱 |
| `boxConfirmed` | 已出现回踩或站稳确认；不代表所有策略条件通过 |
| `boxSignal` | 确认、量价、均线和最低评分全部通过 |
| `boxScore` | 通过前置条件后的评分；尚不能评分时为 `null` |
| `chart` | 暖机完成后的明细；含 `type`、`rejectReason`、`scoreBreakdown` 等 |

每根的箱体位置均按该根当时已知的信息生成。不要把最新一根的评分或信号回填到
较早的突破日期；若画整个箱体区域，要注明观察截止日期。当前最后一根未收盘时，
对应观察值会随原始行情更新。

## 数据边界

缺失/非有限值、矛盾 OHLC、负成交量、重复或倒序日期会保留位置并标为 `valid:false`，
从下一根重新初始化；不会填零、排序后掩盖异常或跨缺口连线。

旧版部分比率用极小分母、固定上限或零基数默认值。本模块的比率分母为零时返回
`null`，不据此生成箱体信号。全零成交量不会产生金叉或紫转黄。未复权行情遇到除权
可能出现价格不连续，指标沿用输入口径，界面应保留行情来源和复权说明。

指标仅展示公式在现有行情上的观察结果，未评价其预测能力；也没有接入账户自动操作。

## 验证

`node --test tests/market-indicators.test.cjs` 覆盖原 Python 固定向量、箱体确认和评分、
量单位换算、初始化、异常/零分母、输入不变性及追加未来数据不改写历史结果。
测试不依赖旧项目路径或 Python 环境。
