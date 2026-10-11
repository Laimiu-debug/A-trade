const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness(handler) {
  let now = 100000;
  const events = new Map(), windows = new Map(), queue = [], requests = [];
  class Clock extends Date { static now() { return now; } }
  const AT = { isDesktop: true, state: { page: 'M05' }, escape: String, head: () => '', button: () => '', icon: () => '', view: () => '', render() {} };
  const document = { hidden: false, querySelector: () => null, addEventListener: (name, listener) => events.set(name, listener) };
  const window = { addEventListener: (name, listener) => windows.set(name, listener), atradeDesktop: { market: async (method, payload) => { requests.push({ method, payload }); return handler(requests.length); } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preview/market-news.js'), 'utf8'), {
    AT, document, window, Date: Clock, queueMicrotask: fn => queue.push(fn), console,
  });
  return { AT, events, windows, document, requests, advance(ms) { now += ms; }, async settle() {
    while (queue.length) { queue.shift()(); await new Promise(resolve => setImmediate(resolve)); }
    await new Promise(resolve => setImmediate(resolve));
  } };
}
const result = { ok: true, data: { items: [{ title: '已有资讯', summary: '来源摘要' }], sources: [], total: 1, page: 1 } };

test('news revisits and window focus revalidate expired data but reuse fresh results', async () => {
  const app = harness(async () => result);
  app.AT.view('M05'); await app.settle();
  app.AT.view('M05'); await app.settle();
  assert.equal(app.requests.length, 1);
  app.advance(60001);
  app.windows.get('focus')(); await app.settle();
  assert.equal(app.requests.length, 2);
  app.AT.state.page = 'T04'; app.advance(60001);
  app.windows.get('focus')(); await app.settle();
  assert.equal(app.requests.length, 2);
  app.AT.state.page = 'M05'; app.AT.view('M05'); await app.settle();
  assert.equal(app.requests.length, 3);
});

test('failed expired news refresh retains readable data and does not retry on every render', async () => {
  const app = harness(async count => count === 1 ? result : { ok: false, error: '来源暂不可用' });
  app.AT.view('M05'); await app.settle(); app.advance(60001);
  app.events.get('visibilitychange')(); await app.settle();
  const html = app.AT.view('M05'); await app.settle();
  assert.match(html, /已有资讯/); assert.match(html, /来源暂不可用/);
  assert.equal(app.requests.length, 2);
  app.advance(60001); app.document.hidden = true;
  app.events.get('visibilitychange')(); await app.settle();
  assert.equal(app.requests.length, 2);
});
