import type { AIQuickPromptTemplate } from '@/types/contracts'

export const BUILTIN_AI_PROMPTS: AIQuickPromptTemplate[] = [
  { id: 'builtin-chart-1', page: 'chart', label: '解释起爆日', prompt: '为什么判定这个起爆日？', pinned: true },
  { id: 'builtin-chart-2', page: 'chart', label: '结构匹配策略', prompt: '这只票现在的结构符合我的哪条策略？', pinned: false },
  { id: 'builtin-screener-1', page: 'screener', label: 'Step过滤差异', prompt: '解释 Step3 到 Step4 的过滤差异。', pinned: true },
  { id: 'builtin-screener-2', page: 'screener', label: '漏斗瓶颈', prompt: '当前选股漏斗的主要瓶颈在哪一步？', pinned: false },
  { id: 'builtin-backtest-1', page: 'backtest', label: '回撤原因', prompt: '这次回测最大回撤发生在什么时候？可能原因是什么？', pinned: true },
  { id: 'builtin-backtest-2', page: 'backtest', label: '优化建议', prompt: '基于回测结果，有哪些可改进的方向？', pinned: false },
  { id: 'builtin-strategy-1', page: 'strategy', label: '策略差异', prompt: '对比当前策略的参数含义与适用场景。', pinned: true },
  { id: 'builtin-signals-1', page: 'signals', label: '信号解读', prompt: '解释当前待买信号列表的筛选逻辑。', pinned: true },
  { id: 'builtin-review-1', page: 'review', label: '复盘总结', prompt: '总结这段复盘区间的交易表现与改进点。', pinned: true },
  {
    id: 'builtin-sentiment-1',
    page: 'sentiment_valuation',
    label: '五因子估值',
    prompt: '{symbol} 这只票怎么样？请用情绪估值五因子模型逐项分析，给出理论市值与隐含情绪溢价。',
    pinned: true,
  },
  {
    id: 'builtin-sentiment-2',
    page: 'sentiment_valuation',
    label: '赚什么钱',
    prompt: '基于当前参数，{symbol} 未来上涨主要在赚哪个因子的钱？业绩驱动还是情绪驱动？',
    pinned: false,
  },
  {
    id: 'builtin-sentiment-3',
    page: 'sentiment_valuation',
    label: '贵不贵',
    prompt: '{symbol} 现在贵不贵？对比理论市值，偏差主要来自哪个因子？',
    pinned: false,
  },
  {
    id: 'builtin-sentiment-4',
    page: 'sentiment_valuation',
    label: '风险点',
    prompt: '如果预期反转，{symbol} 可能遭遇哪些「五杀」式下跌因子？',
    pinned: false,
  },
  { id: 'builtin-trade-1', page: 'trade', label: '账户状态', prompt: '总结当前模拟账户资金、持仓与挂单情况。', pinned: true },
  { id: 'builtin-trade-2', page: 'trade', label: '待买草稿', prompt: '这些待买草稿的仓位 sizing 合理吗？提交前需要注意什么？', pinned: false },
  { id: 'builtin-portfolio-1', page: 'portfolio', label: '持仓诊断', prompt: '分析当前持仓结构，哪些仓位盈利/亏损最大，风险如何？', pinned: true },
  { id: 'builtin-portfolio-2', page: 'portfolio', label: '调仓建议', prompt: '基于当前持仓，有哪些可以考虑减仓或观察的标的？（不给具体买卖指令）', pinned: false },
  { id: 'builtin-trend-1', page: 'market_trend', label: '龙头解读', prompt: '解读当前趋势龙头/连板梯队，热点在哪里？', pinned: true },
  { id: 'builtin-trend-2', page: 'market_trend', label: '持续性', prompt: '当前领涨标的的持续性如何判断？', pinned: false },
  { id: 'builtin-sector-1', page: 'sector_capital', label: '板块轮动', prompt: '解读当前板块资金曲线，哪些板块在持续吸金？', pinned: true },
  { id: 'builtin-abnormal-1', page: 'abnormal_movement', label: '异动解读', prompt: '解释当前异动扫描结果，哪些票需要重点警惕？', pinned: true },
  { id: 'builtin-cross-1', page: 'cross_validate', label: '共振标的', prompt: '哪些股票在多策略/多日期共振？值得优先关注吗？', pinned: true },
  { id: 'builtin-cross-2', page: 'cross_validate', label: '策略差异', prompt: '各策略选出的票重叠度如何？说明什么？', pinned: false },
  { id: 'builtin-event-1', page: 'event_judgment', label: '规则解释', prompt: '解释当前事件判别配置的维度与打分逻辑。', pinned: true },
  { id: 'builtin-sigbt-1', page: 'signals_backtest', label: '回测解读', prompt: '解读当前待买信号回测结果的关键指标。', pinned: true },
  { id: 'builtin-settings-1', page: 'settings', label: '配置检查', prompt: '帮我检查当前系统配置是否合理，有哪些常见风险点？', pinned: true },
  { id: 'builtin-generic-1', page: 'generic', label: '使用指南', prompt: 'Final Trade 各模块之间如何配合使用？', pinned: true },
]

export function applyPromptPlaceholders(prompt: string, symbol?: string | null) {
  const token = symbol?.trim().toUpperCase() || '当前标的'
  return prompt.replaceAll('{symbol}', token)
}
