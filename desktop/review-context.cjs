function buildReviewContext(workspace, review) {
  const start = review.date, end = review.endDate || start;
  const selected = new Set(Array.isArray(review.relatedTradeIds) ? review.relatedTradeIds : []);
  const inPeriod = item => item.date >= start && item.date <= end;
  const ledger = (workspace.ledger || []).filter(item => !item.voided && (inPeriod(item) || selected.has(item.id)));
  const tradeIds = new Set(ledger.filter(item => ['buy', 'sell'].includes(item.type)).map(item => item.id));
  return {
    period: { start, end }, ledger,
    plans: (workspace.plans || []).filter(item => inPeriod(item) ||
      (Array.isArray(item.linkedTradeIds) && item.linkedTradeIds.some(id => tradeIds.has(id)))).map(item => {
      const plan = { ...item };
      if (Array.isArray(plan.linkedTradeIds)) plan.linkedTradeIds = plan.linkedTradeIds.filter(id => tradeIds.has(id));
      if (plan.execution) {
        const execution = plan.execution;
        const withinContext = Array.isArray(execution.matchedTradeIds) && Array.isArray(execution.excludedTradeIds) &&
          execution.matchedTradeIds.every(id => tradeIds.has(id)) && execution.excludedTradeIds.every(id => tradeIds.has(id));
        if (!withinContext) {
          delete plan.execution;
          plan.executionContextNote = '执行汇总含本次范围外或未提供的成交，未发送该汇总；请仅依据本次 ledger 核对执行。';
        }
      }
      return plan;
    }),
    snapshots: (workspace.snapshots || []).filter(inPeriod),
    reviewSchema: workspace.reviewSchema,
    marketDataAvailable: false, strategyContextAvailable: false, backtestContextAvailable: false,
  };
}

module.exports = { buildReviewContext };
