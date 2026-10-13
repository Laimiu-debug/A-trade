import type {
  AbnormalMovementResponse,
  CrossValidateResponse,
  PortfolioSnapshot,
  SectorCapitalFlowResponse,
} from '@/types/contracts'

export function summarizePortfolioSnapshot(data: PortfolioSnapshot | undefined | null) {
  if (!data) return null
  return {
    as_of_date: data.as_of_date,
    total_asset: data.total_asset,
    cash: data.cash,
    position_value: data.position_value,
    realized_pnl: data.realized_pnl,
    unrealized_pnl: data.unrealized_pnl,
    pending_order_count: data.pending_order_count,
    positions: (data.positions ?? []).slice(0, 20).map((item) => ({
      symbol: item.symbol,
      name: item.name,
      quantity: item.quantity,
      pnl_ratio: item.pnl_ratio,
      pnl_amount: item.pnl_amount,
      holding_days: item.holding_days,
    })),
    position_count: data.positions?.length ?? 0,
  }
}

export function summarizeTradeState(input: {
  portfolio?: PortfolioSnapshot | null
  pendingDraftCount?: number
  pendingOrderCount?: number
  recentOrderCount?: number
}) {
  return {
    portfolio: summarizePortfolioSnapshot(input.portfolio),
    pending_draft_count: input.pendingDraftCount ?? 0,
    pending_order_count: input.pendingOrderCount ?? input.portfolio?.pending_order_count ?? 0,
    recent_order_count: input.recentOrderCount ?? 0,
  }
}

export function summarizeSectorCapital(data: SectorCapitalFlowResponse | undefined | null) {
  if (!data) return null
  return {
    date_from: data.date_from,
    date_to: data.date_to,
    sector_count: data.series?.length ?? 0,
    top_leaders: (data.leaders ?? []).slice(0, 8).map((item) => ({
      sector: item.sector,
      leader_days: item.leader_days,
      max_return_pct: item.max_return_pct,
    })),
  }
}

export function summarizeCrossValidate(data: CrossValidateResponse | null | undefined) {
  if (!data) return null
  return {
    date_from: data.date_from,
    date_to: data.date_to,
    as_of_date: data.as_of_date,
    strategy_pools: data.strategy_pools,
    total_unique_stocks: data.total_unique_stocks,
    top_stocks: (data.results ?? []).slice(0, 12).map((item) => ({
      symbol: item.symbol,
      name: item.name,
      overlap_count: item.overlap_count,
      best_score: item.best_score,
      range_return_pct: item.range_return_pct,
    })),
  }
}

export function summarizeAbnormalMovement(data: AbnormalMovementResponse | null | undefined) {
  if (!data) return null
  return {
    scan_mode: data.scan_mode,
    date_from: data.date_from,
    date_to: data.date_to,
    event_count: data.events?.length ?? 0,
    top_events: (data.events ?? []).slice(0, 12).map((item) => ({
      symbol: item.symbol,
      name: item.name,
      kind: item.kind,
      status: item.status,
      board: item.board,
    })),
  }
}

export function summarizeTrendLeaders(input: {
  tab?: string
  leaderCount?: number
  ladderCount?: number
  topSymbols?: Array<{ symbol: string; name: string; score?: number }>
}) {
  return {
    tab: input.tab,
    leader_count: input.leaderCount ?? 0,
    ladder_count: input.ladderCount ?? 0,
    top_symbols: (input.topSymbols ?? []).slice(0, 12),
  }
}
