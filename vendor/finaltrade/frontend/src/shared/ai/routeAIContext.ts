import { buildPageTitle } from '@/state/aiAssistantStore'
import type { AIChatContext, AIChatPage } from '@/types/contracts'

export function resolveAIChatPage(pathname: string): AIChatPage {
  if (pathname.startsWith('/stocks/') && pathname.endsWith('/chart')) return 'chart'
  if (pathname.startsWith('/signals/cross-validate')) return 'cross_validate'
  if (pathname.startsWith('/signals/backtest')) return 'signals_backtest'
  if (pathname.startsWith('/signals')) return 'signals'
  if (pathname.startsWith('/market/sentiment-valuation')) return 'sentiment_valuation'
  if (pathname.startsWith('/market/sector-capital')) return 'sector_capital'
  if (pathname.startsWith('/market/abnormal-movement')) return 'abnormal_movement'
  if (pathname.startsWith('/market/trend')) return 'market_trend'
  if (pathname.startsWith('/strategy/events')) return 'event_judgment'
  if (pathname.startsWith('/strategy')) return 'strategy'
  if (pathname.startsWith('/backtest')) return 'backtest'
  if (pathname.startsWith('/trade')) return 'trade'
  if (pathname.startsWith('/portfolio')) return 'portfolio'
  if (pathname.startsWith('/review/share')) return 'review'
  if (pathname.startsWith('/review/news')) return 'review'
  if (pathname.startsWith('/review')) return 'review'
  if (pathname.startsWith('/screener')) return 'screener'
  if (pathname.startsWith('/settings')) return 'settings'
  if (pathname.startsWith('/ai')) return 'ai_records'
  return 'generic'
}

export function buildRouteAIContext(pathname: string): AIChatContext {
  const page = resolveAIChatPage(pathname)
  return {
    page,
    title: buildPageTitle(page),
    payload: {
      route: pathname,
    },
  }
}

export function getPageHint(page: AIChatPage): string {
  const hints: Record<AIChatPage, string> = {
    chart: '可以问起爆日判定、Wyckoff 结构、策略匹配等。',
    screener: '可以问漏斗各 Step 过滤差异、瓶颈在哪一步。',
    signals: '可以问待买信号筛选逻辑、评分含义、某条信号是否值得跟踪。',
    signals_backtest: '可以问待买信号 ETF 回测结果、参数含义与改进方向。',
    cross_validate: '可以问多策略共振标的、重叠度、哪几只票值得重点看。',
    backtest: '可以问最大回撤原因、参数优化、策略执行细节。',
    strategy: '可以问策略差异、参数适用场景、如何组合使用。',
    event_judgment: '可以问事件判别规则、维度权重、某类事件如何打分。',
    review: '可以问复盘区间表现、标签统计、改进方向。',
    trade: '可以问模拟下单、T+1 规则、费用计算、待买草稿如何提交。',
    portfolio: '可以问持仓结构、盈亏分布、哪只仓位需要处理。',
    market_trend: '可以问趋势龙头、连板梯队、热点切换与龙头持续性。',
    sector_capital: '可以问板块资金流向、领涨板块与持续性。',
    abnormal_movement: '可以问异动规则、严重异常票、监管风险提示。',
    sentiment_valuation: '可以问五因子估值、隐含情绪溢价、贵不贵、赚哪个因子的钱。',
    settings: '可以问数据路径、Provider 配置、同步策略与常见报错。',
    ai_records: '可以问历史 AI 分析结论、如何结合当前页面理解某条记录。',
    generic: '基于当前页面数据提问；切换到具体功能页可获得更精准的回答。',
  }
  return hints[page] ?? hints.generic
}
