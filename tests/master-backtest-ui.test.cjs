'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('T11 master-slips serves as pure delivery-slip archive and deep-links Master Modes into R12 (Signals/Prediction) & R03 (Backtest)', async () => {
  const listeners = {};
  const openedRoutes = [];
  let renderedHtml = '';

  const doc = {
    addEventListener(type, fn) {
      listeners[type] = listeners[type] || [];
      listeners[type].push(fn);
    },
    querySelector() { return null; },
    body: { appendChild() {} },
  };

  const AT = {
    isDesktop: true,
    escape: s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch])),
    state: { page: 'T11' },
    navigate(p) { AT.state.page = p; },
    view: () => '',
    render() { renderedHtml = AT.view('T11'); },
    viewParts: { head: (t, sub, actions) => `<header><h1>${t}</h1><p>${sub}</p>${actions || ''}</header>` },
  };

  const StrategyWorkspace = {
    openStrategy(page, route) {
      openedRoutes.push({ page, route });
      AT.state.page = page;
    },
  };

  const windowObj = {
    StrategyWorkspace,
    atradeDesktop: {
      market: async (method) => {
        if (method === 'market.masters.list') {
          return {
            ok: true,
            data: {
              items: [
                { id: 'm1', name: '乌江望月', styleTag: '动态主线', stats: { tradeCount: 1838, uniqueStocks: 95, reportCount: 2, firstDate: '2026-05-01', lastDate: '2026-11-25' } },
                { id: 'm2', name: '暴涨基金董事长', styleTag: '高标连板', stats: { tradeCount: 620, uniqueStocks: 44, reportCount: 1, firstDate: '2026-05-01', lastDate: '2026-11-25' } },
              ],
            },
          };
        }
        if (method === 'market.masters.analyze') {
          return {
            ok: true,
            data: {
              empty: false,
              summary: { totalTrades: 1838, buyCount: 920, sellCount: 918, closedEpisodes: 180, winRatePct: 58.2, profitFactor: 1.92, totalRealizedPnl: 1250000 },
              equityCurve: [
                { date: '2026-06-01', returnPct: 0, drawdownPct: 0, positionRatioPct: 35, holdingCount: 2, stage: 'trial', stageLabel: '试错' },
                { date: '2026-06-02', returnPct: 6.4, drawdownPct: -0.5, positionRatioPct: 70, holdingCount: 4, stage: 'advance', stageLabel: '主升' },
              ],
            },
          };
        }
        if (method === 'market.masters.reports.list') return { ok: true, data: { items: [] } };
        if (method === 'market.masters.trades.list') return { ok: true, data: { items: [], total: 0 } };
        return { ok: true, data: {} };
      },
      business: async (method) => {
        if (method === 'research.get') {
          return {
            ok: true,
            data: {
              task: { id: 'mbt-1', kind: 'masterBacktest', status: 'completed', config: { styleKey: 'dongshizhang_highboard' } },
              result: { styleKey: 'dongshizhang_highboard' },
            },
          };
        }
        return { ok: true, data: { tasks: [] } };
      },
    },
  };

  const ctx = vm.createContext({
    AT,
    StrategyWorkspace,
    window: windowObj,
    document: doc,
    console,
    setTimeout,
    clearTimeout,
    queueMicrotask,
    Promise,
    Number,
    Math,
    Date,
    JSON,
    Set,
    Array,
    Object,
    String,
    Boolean,
  });

  const code = fs.readFileSync(path.join(__dirname, '..', 'preview', 'master-slips.js'), 'utf8');
  vm.runInContext(code, ctx);

  renderedHtml = AT.view('T11');
  await new Promise(r => setTimeout(r, 30));
  renderedHtml = AT.view('T11');

  // 1. Standalone backtest/prediction tabs are removed from T11; pure archive tabs remain
  assert.doesNotMatch(renderedHtml, /data-ms-tab="backtest"/, 'Standalone backtest tab must not appear inside T11');
  assert.doesNotMatch(renderedHtml, /data-ms-tab="advisor"/, 'Standalone advisor tab must not appear inside T11');
  assert.match(renderedHtml, /data-ms-tab="overview"/, 'Overview archive tab must render');
  assert.match(renderedHtml, /data-ms-tab="articles"/, 'Deep articles tab must render');
  assert.match(renderedHtml, /data-ms-tab="database"/, 'Raw delivery slips database tab must render');

  // 2. Unified deep links into Strategy Run (R12) and Strategy Backtest (R03) are rendered
  assert.match(renderedHtml, /data-ms-action="goto-strategy-signals"/, 'Link to R12 Strategy Run / Tomorrow Prediction must render');
  assert.match(renderedHtml, /data-ms-action="goto-strategy-backtest"/, 'Link to R03 Strategy Backtest must render');

  // 3. Clicking goto-strategy-signals routes Wujiang to R12 with wujiang_mainline_v1 & mode=full_market
  for (const fn of (listeners.click || [])) {
    fn({
      preventDefault() {},
      stopImmediatePropagation() {},
      target: {
        closest: sel => sel === '[data-ms-action]' ? { dataset: { msAction: 'goto-strategy-signals' } } : null,
      },
    });
  }
  assert.equal(openedRoutes.length, 1);
  assert.equal(openedRoutes[0].page, 'R12');
  assert.equal(openedRoutes[0].route, '/signals?strategy_id=wujiang_mainline_v1&mode=full_market');

  // 4. Legacy openBacktestTask redirects cleanly into R03 Strategy Backtest with mapped strategy_id
  AT.state.page = 'T11';
  await AT.masterSlips.openBacktestTask('mbt-1');
  assert.equal(openedRoutes.length, 2);
  assert.equal(openedRoutes[1].page, 'R03');
  assert.equal(openedRoutes[1].route, '/backtest?strategy_id=highboard_sniper_v1');
});

test('research-module curveRows supports both legacy and enriched masterBacktest curves on R04/R09/G03', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'preview', 'research-module.js'), 'utf8');
  assert.match(src, /masterRetPct/, 'research-module curveRows must normalize masterRetPct for legacy masterBacktest tasks');
  assert.match(src, /benchRetPct/, 'research-module curveRows must normalize benchRetPct for masterBacktest tasks');
  assert.match(src, /masterDdPct/, 'research-module curveRows must normalize masterDdPct for masterBacktest tasks');
});
