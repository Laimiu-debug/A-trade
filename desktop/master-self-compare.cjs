'use strict';

/**
 * Compute an anonymized behavioral discipline summary from the user's local
 * account workspace ledger for side-by-side comparison against a master's DB.
 * Never exports account IDs, raw yuan balances, or private notes.
 */
function summarizeSelfAccount(workspaceData) {
  const account = (workspaceData?.accounts || []).find(a => a.id === workspaceData?.accountId) || workspaceData?.account || {};
  const ledger = Array.isArray(workspaceData?.ledger) ? workspaceData.ledger : [];
  const trades = ledger
    .filter(row => row && !row.voided && (row.type === 'buy' || row.type === 'sell') && row.code && Number(row.quantity) > 0 && Number(row.price) > 0)
    .sort((a, b) => String(a.date || '').localeCompare(String(b.date || '')) || String(a.time || '').localeCompare(String(b.time || '')));

  const positions = new Map();
  const closed = [];
  const dailyHoldings = new Map();

  for (const row of trades) {
    const code = String(row.code);
    const qty = Number(row.quantity);
    const price = Number(row.price);
    const amount = Number(row.amount || qty * price);
    const fees = Number(row.fees || 0);
    const date = String(row.date || '');

    if (row.type === 'buy') {
      const pos = positions.get(code) || {
        code, entryDate: date, qty: 0, cost: 0, peakCost: 0, realizedPnl: 0,
      };
      pos.qty += qty;
      pos.cost += amount + fees;
      pos.peakCost = Math.max(pos.peakCost, pos.cost);
      positions.set(code, pos);
    } else {
      const pos = positions.get(code);
      if (pos && pos.qty > 0) {
        const matched = Math.min(qty, pos.qty);
        const avgCost = pos.cost / pos.qty;
        const costBasis = avgCost * matched;
        const proceeds = (amount - fees) * (matched / qty);
        pos.realizedPnl += proceeds - costBasis;
        pos.qty = Math.max(0, pos.qty - matched);
        pos.cost = Math.max(0, pos.cost - costBasis);
        if (pos.qty <= 1e-4) {
          const d0 = Date.parse(pos.entryDate), d1 = Date.parse(date);
          const holdDays = Number.isFinite(d0) && Number.isFinite(d1) ? Math.max(0, Math.round((d1 - d0) / 86400000)) : null;
          const returnPct = pos.peakCost > 0 ? (pos.realizedPnl / pos.peakCost) * 100 : 0;
          closed.push({
            returnPct,
            outcome: pos.realizedPnl > 1e-4 ? 'win' : pos.realizedPnl < -1e-4 ? 'loss' : 'flat',
            holdDays,
          });
          positions.delete(code);
        }
      }
    }
    if (date) dailyHoldings.set(date, positions.size);
  }

  const wins = closed.filter(x => x.outcome === 'win');
  const losses = closed.filter(x => x.outcome === 'loss');
  const avg = arr => arr.length ? Math.round((arr.reduce((s, v) => s + v, 0) / arr.length) * 100) / 100 : null;
  const avgWin = avg(wins.map(x => x.returnPct));
  const avgLoss = avg(losses.map(x => x.returnPct));
  const holdValues = [...dailyHoldings.values()];

  return {
    accountName: typeof account.name === 'string' && account.name.trim() ? account.name.trim() : '我的账户',
    tradeCount: trades.length,
    closedCount: closed.length,
    winRatePct: closed.length ? Math.round((wins.length / closed.length) * 1000) / 10 : null,
    payoffRatio: avgWin !== null && avgLoss !== null && Math.abs(avgLoss) > 1e-6 ? Math.round(Math.abs(avgWin / avgLoss) * 100) / 100 : null,
    avgHoldDaysWin: avg(wins.map(x => x.holdDays).filter(v => v !== null)),
    avgHoldDaysLoss: avg(losses.map(x => x.holdDays).filter(v => v !== null)),
    worstLossPct: losses.length ? Math.round(Math.min(...losses.map(x => x.returnPct)) * 100) / 100 : null,
    avgHoldingCount: holdValues.length ? Math.round((holdValues.reduce((s, v) => s + v, 0) / holdValues.length) * 100) / 100 : null,
  };
}

module.exports = { summarizeSelfAccount };
