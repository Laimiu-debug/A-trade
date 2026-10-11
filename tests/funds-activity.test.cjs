const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {cleanRows, calculate, linePath, candidateItems} = require('../preview/funds-activity.js');

function runtime() {
  const listeners = new Map(), timers = [], requests = [], captures = new Set();
  const AT = {isDesktop: true, state: {page: 'T05'}, escape: value => String(value ?? ''), render() {}};
  const document = {activeElement: null, documentElement: {classList: {remove() {}, add() {}}, hasPointerCapture: id => captures.has(id), setPointerCapture: id => captures.add(id), releasePointerCapture: id => captures.delete(id)}, addEventListener(name, handler) {listeners.set(name, handler);}, querySelector() {return null;}, querySelectorAll() {return [];}};
  const window = {addEventListener() {}, atradeDesktop: {market(method, payload) {return new Promise(resolve => requests.push({method, payload, resolve}));}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preview/funds-activity.js'), 'utf8'), {AT, document, window, setTimeout: handler => timers.push(handler)});
  const runTimers = () => {while (timers.length) timers.shift()();};
  function click(dataset) {listeners.get('click')({target: {closest: () => ({dataset})}, preventDefault() {}}); runTimers();}
  async function resolve(index, data, ok = true) {requests[index].resolve(ok ? {ok, data} : {ok, error: data}); await new Promise(setImmediate); runTimers();}
  return {ui: AT.fundsActivity, AT, requests, runTimers, click, resolve, listeners, captures};
}
const chosen = html => [...html.matchAll(/data-funds-activity-remove="([^"]+)"/g)].map(match => match[1]);
const members = Array.from({length: 8}, (_, i) => ({symbol: `sh88000${i}`, name: `板块${i}`, activityPercent: 8 - i}));
const rows = Array.from({length: 80}, (_, i) => ({date: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10), close: 100 + i, amount: (i + 1) * 1e8, activityPercent: i < 4 ? null : (i % 7 - 3) * 10}));

test('turnover and activity use real date alignment, preserve zero, and never connect missing dates', () => {
  const series = [
    {symbol: 'a', rows: [{date: '2026-01-01', amount: 0, activityPercent: 0}, {date: '2026-01-02', amount: 2e8, activityPercent: 25}, {date: '2026-01-03', amount: 3e8, activityPercent: -10}]},
    {symbol: 'b', rows: [{date: '2026-01-01', amount: null, activityPercent: null}, {date: '2026-01-03', amount: 5e8, activityPercent: 60}]}
  ];
  const amounts = calculate(series, 'amount');
  assert.deepEqual(amounts.dates, ['2026-01-01', '2026-01-02', '2026-01-03']);
  assert.deepEqual(amounts.series.map(item => item.values), [[0, 2, 3], [null, null, 5]]);
  assert.deepEqual(calculate(series).series[0].values, [0, 25, -10]);
  assert.equal(linePath([0, null, 10], 100, 100, 0, 10), 'M0.00,100.00 M100.00,0.00');
});

test('returns are recalculated at a shared close baseline instead of source-specific return fields', () => {
  const result = calculate([
    {symbol: 'a', rows: [{date: '2026-01-01', close: 100}, {date: '2026-01-02', close: 110, returnPercent: 999}, {date: '2026-01-03', close: 121}]},
    {symbol: 'b', rows: [{date: '2026-01-02', close: 2000, returnPercent: -999}, {date: '2026-01-03', close: 2200}]}
  ], 'returnPercent');
  assert.equal(result.baseDate, '2026-01-02');
  assert.deepEqual(result.series.map(item => item.values[0]), [0, 0]);
  assert.ok(result.series.every(item => Math.abs(item.values[1] - 10) < 1e-8));
  assert.equal(calculate([{symbol: 'a', rows: [{date: '2026-01-01', close: 1}]}, {symbol: 'b', rows: [{date: '2026-01-02', close: 1}]}], 'returnPercent').reason, 'no-common-date');
});

test('invalid dates, nonfinite fields, negative amounts, and incomplete heat cannot become fabricated observations', () => {
  assert.deepEqual(cleanRows([{date: '2026-02-30'}, {date: '2026-13-01'}, {date: 'wrong'}, {date: '2026-02-28', amount: 1}, {date: '2026-02-28', amount: 2}]), [{date: '2026-02-28', amount: 2}]);
  const data = [{symbol: 'a', rows: [{date: '2026-01-01', amount: -3, activityPercent: null}, {date: '2026-01-02', amount: Infinity, activityPercent: '20'}]}];
  assert.equal(calculate(data).reason, 'missing-values');
  assert.deepEqual(calculate(data, 'amount').series[0].values, [null, null]);
  assert.deepEqual(candidateItems([{symbol: 'sh600000'}, {id: 'sz000001'}, {symbol: 'sh600000'}, {symbol: 'BK0001'}]).map(item => item.symbol), ['sh600000', 'sz000001']);
});

test('sector catalogue selects the first five and loads sequentially; complete chart waits for all selected histories', async () => {
  const rt = runtime();
  assert.match(rt.ui.panel({kind: 'sector'}), /正在读取本地板块日线目录/); rt.runTimers();
  assert.equal(rt.requests[0].method, 'market.funds.activity.list');
  await rt.resolve(0, {items: members});
  assert.deepEqual(chosen(rt.ui.panel()), members.slice(0, 5).map(item => item.symbol));
  assert.deepEqual(JSON.parse(JSON.stringify(rt.requests[1].payload)), {symbol: 'sh880000', kind: 'sector', limit: 60, window: 5});
  await rt.resolve(1, {items: rows});
  assert.doesNotMatch(rt.ui.panel(), /data-funds-activity-chart/);
  for (let i = 2; i <= 5; i++) await rt.resolve(i, {items: rows});
  assert.match(rt.ui.panel(), /data-activity-total="60"/);
  assert.equal((rt.ui.panel().match(/data-funds-activity-line=/g) || []).length, 5);
  assert.match(rt.ui.panel(), /不能表示主力净流入/);
  rt.click({fundsActivityToggle: 'sh880005'});
  assert.match(rt.ui.panel(), /最多同时对比 5 个/);
  rt.click({fundsActivityRemove: 'sh880000'});
  rt.click({fundsActivityToggle: 'sh880005'});
  assert.deepEqual(chosen(rt.ui.panel()), ['sh880001', 'sh880002', 'sh880003', 'sh880004', 'sh880005']);
  assert.equal(rt.requests.at(-1).payload.symbol, 'sh880005');
});

test('stock candidates never request the sector catalogue; as-of and changing result context are forwarded correctly', async () => {
  const rt = runtime(); rt.AT.state.page = 'T04';
  rt.ui.panel({kind: 'stock', items: members.slice(0, 2), contextKey: 'first', asOf: '2026-02-01'}); rt.runTimers();
  assert.equal(rt.requests[0].method, 'market.funds.activity.history');
  assert.equal(rt.requests[0].payload.asOf, '2026-02-01');
  await rt.resolve(0, {items: rows}); await rt.resolve(1, {items: rows});
  rt.click({fundsActivityRemove: 'sh880001'});
  assert.deepEqual(chosen(rt.ui.panel({kind: 'stock', items: members.slice(0, 2), contextKey: 'first', asOf: '2026-02-01'})), ['sh880000']);
  rt.ui.panel({kind: 'stock', items: members.slice(2, 4), contextKey: 'second', asOf: '2026-01-01'}); rt.runTimers();
  assert.deepEqual(chosen(rt.ui.panel()), ['sh880002', 'sh880003']);
  assert.equal(rt.requests.at(-1).payload.asOf, '2026-01-01');
  assert.equal(rt.requests.at(-1).payload.symbol, 'sh880002');
});

test('refresh retains manual selection and requests uncached catalogue and selected histories exactly once', async () => {
  const rt = runtime(); rt.ui.panel({kind: 'sector'}); rt.runTimers(); await rt.resolve(0, {items: members.slice(0, 2)});
  await rt.resolve(1, {items: rows}); await rt.resolve(2, {items: rows});
  rt.click({fundsActivityRemove: 'sh880001'});
  rt.ui.refresh(); rt.runTimers(); assert.equal(rt.requests[3].payload.refresh, true);
  await rt.resolve(3, {items: [...members].reverse()});
  assert.deepEqual(chosen(rt.ui.panel()), ['sh880000']);
  assert.equal(rt.requests[4].payload.refresh, true); await rt.resolve(4, {items: rows});
  rt.click({fundsActivityToggle: 'sh880002'});
  assert.equal(rt.requests[5].payload.refresh, undefined);
});

test('period changes use the requested limit and hide the stale chart until that period is available', async () => {
  const rt = runtime(); rt.AT.state.page = 'T04'; rt.ui.panel({kind: 'stock', items: members.slice(0, 1)}); rt.runTimers(); await rt.resolve(0, {items: rows});
  assert.match(rt.ui.panel(), /data-activity-total="60"/);
  rt.click({fundsActivityPeriod: '750'}); assert.equal(rt.requests[1].payload.limit, 750);
  assert.doesNotMatch(rt.ui.panel(), /data-funds-activity-chart/);
  await rt.resolve(1, {items: rows}); assert.match(rt.ui.panel(), /data-activity-total="80"/);
  assert.match(rt.ui.panel(), /不足所选天数时仅显示已有日线/);
  rt.click({fundsActivityField: 'returnPercent'});
  assert.match(rt.ui.panel(), /data-activity-base="2026-01-01"/);
  assert.equal(rt.requests.length, 2, 'field changes recompute the existing history without network requests');
});

test('source invalidation discards responses and the active page owns the only sequential queue', async () => {
  const rt = runtime(); rt.ui.panel({kind: 'sector'}); rt.runTimers();
  rt.ui.invalidate(); rt.AT.state.page = 'T04'; rt.ui.panel({kind: 'stock', items: members.slice(2, 4)}); rt.runTimers();
  assert.equal(rt.requests.length, 1, 'no overlapping resource request');
  await rt.resolve(0, {items: members});
  assert.equal(rt.requests[1].payload.symbol, 'sh880002');
  assert.deepEqual(chosen(rt.ui.panel()), ['sh880002', 'sh880003']);
  rt.AT.state.page = 'M01'; await rt.resolve(1, {items: rows});
  assert.equal(rt.requests.length, 2, 'leaving page stops queue');
  rt.AT.state.page = 'T04'; rt.ui.panel({kind: 'stock', items: members.slice(2, 4)}); rt.runTimers();
  assert.equal(rt.requests[2].payload.symbol, 'sh880002', 'hidden-page response was discarded');
  rt.ui.suspend(); await rt.resolve(2, {items: rows});
  assert.equal(rt.requests.length, 3, 'switching same-page mode also stops queue');
});

test('empty and unavailable catalogues settle without repeated calls and expose the settings entry', async () => {
  const rt = runtime(); rt.ui.panel({kind: 'sector'}); rt.runTimers();
  await rt.resolve(0, {items: [], status: 'unavailable', errors: [{message: '未选择通达信目录'}]});
  const html = rt.ui.panel(); rt.runTimers();
  assert.match(html, /aria-busy="false"/); assert.match(html, /未选择通达信目录/); assert.match(html, /data-nav="C01"/);
  assert.equal(rt.requests.length, 1);
  rt.ui.refresh(); rt.runTimers(); await rt.resolve(1, {items: members.slice(0, 1)});
  assert.deepEqual(chosen(rt.ui.panel()), ['sh880000']);
});

test('older catalogue rows stay available but are not auto-selected; real source warnings survive without formula duplication', async () => {
  const rt = runtime(); rt.ui.panel({kind: 'sector'}); rt.runTimers();
  const formula = '量能热度＝（当日成交额 ÷ 含当日的完整窗口平均成交额－1）×100%；反映成交活跃程度，不代表主力净流入。';
  await rt.resolve(0, {items: [{...members[0], stale: true, asOf: '2026-01-01'}, members[1]], warnings: [formula, '部分板块日期早于最新观察日']});
  assert.deepEqual(chosen(rt.ui.panel()), ['sh880001']);
  assert.match(rt.ui.panel(), /旧日 2026-01-01/);
  await rt.resolve(1, {items: rows, warnings: [formula, '实际不足 750 日']});
  const html = rt.ui.panel();
  assert.match(html, /板块与分类指数/); assert.match(html, /部分板块日期早于最新观察日/); assert.match(html, /实际不足 750 日/);
  assert.doesNotMatch(html, /量能热度＝/); assert.equal((html.match(/不能表示主力净流入/g) || []).length, 1);
});

test('wheel zoom and both mouse buttons pan the date window without rebasing; suspension releases pointer capture', async () => {
  const rt = runtime(); rt.AT.state.page = 'T04'; rt.ui.panel({kind: 'stock', items: members.slice(0, 1)}); rt.runTimers(); await rt.resolve(0, {items: rows});
  rt.click({fundsActivityField: 'returnPercent'});
  const chart = {getBoundingClientRect: () => ({left: 0, width: 1000}), focus() {}};
  const event = {target: {closest: () => chart}, preventDefault() {}, stopPropagation() {}, clientX: 500, clientY: 100};
  rt.listeners.get('wheel')({...event, deltaY: -100});
  let html = rt.ui.panel(); assert.match(html, /data-activity-count="48"/); assert.match(html, /data-activity-start="6"/);
  const base = html.match(/data-activity-base="([^"]+)"/)[1];
  rt.listeners.get('pointerdown')({...event, pointerId: 1, pointerType: 'mouse', button: 0});
  rt.listeners.get('pointermove')({...event, pointerId: 1, buttons: 1, clientX: 400});
  assert.match(rt.ui.panel(), /data-activity-start="11"/); assert.ok(rt.captures.has(1));
  rt.listeners.get('pointerup')({pointerId: 1}); assert.equal(rt.captures.size, 0);
  rt.listeners.get('pointerdown')({...event, pointerId: 2, pointerType: 'mouse', button: 2});
  rt.listeners.get('pointermove')({...event, pointerId: 2, buttons: 2, clientX: 600});
  assert.match(rt.ui.panel(), /data-activity-start="6"/); assert.ok(rt.captures.has(2));
  assert.equal(rt.ui.panel().match(/data-activity-base="([^"]+)"/)[1], base);
  rt.ui.suspend(); assert.equal(rt.captures.size, 0);
});
