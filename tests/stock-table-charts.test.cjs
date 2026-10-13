const {test} = require('node:test');
const assert = require('node:assert/strict');
const {symbol, date, bars, plot} = require('../preview/stock-table-charts.js');

const candle = (day, values = {}) => ({date:day, open:10, high:12, low:8, close:11, ...values});
const attributes = tag => Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, key, value]) => [key, value]));
const renderedCandles = html => [...html.matchAll(/<g\b([^>]*)>([\s\S]*?)<\/g>/g)].map(([, props, content]) => ({
  ...attributes(props), wick:attributes(content.match(/<line\b[^>]*>/)?.[0] || ''),
  body:attributes(content.match(/<rect\b[^>]*>/)?.[0] || '')
}));

test('stock symbols retain explicit exchange identity and infer bare A-share codes', () => {
  for (const [value, expected] of [
    [' SH600900 ', 'sh600900'], ['SZ000001', 'sz000001'], ['sh000001', 'sh000001'],
    ['600900', 'sh600900'], ['688137', 'sh688137'], ['301190', 'sz301190'],
    ['000001', 'sz000001'], ['920001', 'bj920001'], ['430047', 'bj430047'], ['830799', 'bj830799']
  ]) assert.equal(symbol(value), expected, String(value));
  assert.notEqual(symbol('sh000001'), symbol('sz000001'));
  for (const value of [null, undefined, '', '60090', '1234567', '../600900', 'sh600900<script>', 'NASDAQ:AAPL']) {
    assert.equal(symbol(value), '', String(value));
  }
});

test('cutoff dates validate the calendar and preserve the source day in timestamps', () => {
  assert.equal(date('2024-02-29'), '2024-02-29');
  assert.equal(date('2026-10-09T15:00:00+08:00'), '2026-10-09');
  assert.equal(date('2026-10-09 09:31:00'), '2026-10-09');
  assert.equal(date('2026-10-09T23:59:59-07:00'), '2026-10-09');
  for (const value of [null, undefined, '', '2026-02-29', '2026-04-31', '2026-00-01', '2026-13-01',
    '2026-10-00', '2026-10-32T09:31:00', '20261009', '2026-1-9', '<script>']) {
    assert.equal(date(value), '', String(value));
  }
});

test('historical bars are sorted, deduplicated and bounded after excluding future dates', () => {
  const rows = Array.from({length:50}, (_, i) => candle(new Date(Date.UTC(2026, 7, 1 + i)).toISOString().slice(0, 10), {close:10 + i / 100}));
  const input = rows.toReversed();
  input.push(candle('2026-08-15T15:00:00+08:00', {close:11.75}));
  const saved = structuredClone(input);
  const result = bars(input, '2026-09-05T15:00:00+08:00');
  assert.equal(result.length, 30);
  assert.equal(result[0].date, '2026-08-07');
  assert.equal(result.at(-1).date, '2026-09-05');
  assert.equal(new Set(result.map(row => row.date)).size, result.length);
  assert.ok(result.every((row, i) => row.date <= '2026-09-05' && (!i || row.date > result[i - 1].date)));
  assert.equal(result.find(row => row.date === '2026-08-15').close, 11.75);
  assert.deepEqual(input, saved, 'source data must remain unchanged');
  assert.notEqual(result[0], input.find(row => row.date === result[0].date), 'normalized rows must be copies');
  assert.deepEqual(bars(input, '2026-07-31'), []);
  assert.equal(bars(input).at(-1).date, '2026-09-19');
});

test('missing, nonnumeric and inconsistent OHLC values never become candles', () => {
  const invalid = [null, {}, '2026-10-09', candle('2026-02-30'), candle('not a date'),
    candle('2026-10-09', {high:10.5}), candle('2026-10-09', {low:10.5}),
    candle('2026-10-09', {high:8, low:12})];
  for (const key of ['open', 'high', 'low', 'close']) {
    for (const value of [NaN, Infinity, -Infinity, 0, -1, null, undefined, '10', true]) {
      invalid.push(candle('2026-10-09', {[key]:value}));
    }
  }
  assert.deepEqual(bars(invalid), []);
  assert.deepEqual(bars(undefined), []);
  assert.deepEqual(bars({rows:[candle('2026-10-09')]}), []);
  const valid = candle('2026-10-09', {volume:0, amount:null});
  assert.deepEqual(bars([...invalid, valid]), [valid], 'OHLC availability does not depend on optional volume');
});

test('each real bar renders its high-low wick and open-close body with its direction', () => {
  const html = plot(bars([
    candle('2026-10-07', {open:10, close:11}),
    candle('2026-10-08', {open:11, close:10}),
    candle('2026-10-09', {open:10, close:10})
  ]));
  const rendered = renderedCandles(html);
  assert.equal(rendered.length, 3);
  assert.deepEqual(rendered.map(row => row.class), ['stock-kline-up', 'stock-kline-down', 'stock-kline-flat']);
  assert.deepEqual(rendered.map(row => row['data-kline-candle']), ['2026-10-07', '2026-10-08', '2026-10-09']);
  for (const row of rendered) {
    const high = Number(row.wick.y1), low = Number(row.wick.y2), top = Number(row.body.y), height = Number(row.body.height);
    assert.equal(row.wick.x1, row.wick.x2, 'wick is vertical');
    assert.ok(high < low, 'high price plots above low price');
    assert.ok(top > high && top + height < low, 'body reflects open and close inside the wick');
    assert.ok(height > 0 && Number(row.body.width) > 0);
  }
  assert.equal(rendered[0].body.y, rendered[1].body.y);
  assert.equal(rendered[0].body.height, rendered[1].body.height);
  assert.ok(Number(rendered[2].body.height) < Number(rendered[0].body.height), 'doji remains visible without an invented price range');
  assert.match(html, /开 10 · 高 12 · 低 8 · 收 11/);
  assert.match(html, /3 根未复权日K，2026-10-07 至 2026-10-09/);
  assert.doesNotMatch(html, /NaN|Infinity/);
});

test('a single flat bar stays finite and empty data does not fabricate an SVG', () => {
  const html = plot(bars([candle('2026-10-09', {open:10, high:10, low:10, close:10})]));
  const [row] = renderedCandles(html);
  assert.equal(row.class, 'stock-kline-flat');
  assert.equal(row.wick.y1, row.wick.y2);
  assert.ok(Number(row.body.height) > 0);
  assert.doesNotMatch(html, /NaN|Infinity/);
  assert.equal(plot([]), '');
  assert.equal(plot(bars([candle('2026-10-09', {open:null})])), '');
});

test('SVG labels escape external text even when the renderer is called directly', () => {
  const input = '2026-10-09" onload="bad()"><script>&\'payload</script>';
  const html = plot([candle(input)]);
  assert.match(html, /&quot; onload=&quot;bad\(\)&quot;&gt;&lt;script&gt;&amp;&#39;payload&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script|" onload="|<img/);
  assert.equal(renderedCandles(html).length, 1);
});
