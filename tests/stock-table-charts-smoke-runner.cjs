const {app, protocol, dialog, session} = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {MarketService} = require('../desktop/market-service.cjs');

const directory = process.argv[2];
if (!directory) throw new Error('Missing isolated smoke directory');
const root = path.resolve(__dirname, '..');
const dataDirectory = path.join(directory, 'workspace');
const fixtureDirectory = path.join(directory, 'tdx-fixture');
const errors = [], barRequests = [];
let win, heldBars;

app.setPath('userData', path.join(directory, 'profile'));
protocol.registerSchemesAsPrivileged([{scheme:'atrade', privileges:{standard:true, secure:true, supportFetchAPI:true}}]);
dialog.showMessageBox = async (_window, options) => {throw new Error(`Unexpected dialog: ${options.message}`);};
// Observe the real IPC service boundary without mocking data, bridge methods,
// renderer modules, or the market service's replies.
const request = MarketService.prototype.request;
MarketService.prototype.request = function(method, payload = {}) {
  if (method === 'market.bars') barRequests.push(structuredClone(payload));
  const response = request.call(this, method, payload);
  if (method === 'market.bars' && payload.symbol === heldBars?.symbol && payload.limit === 30) {
    const held = heldBars;
    heldBars = null;
    return Promise.resolve(response).then(async value => {
      held.ready(value);
      await held.released;
      return value;
    });
  }
  return response;
};

const evaluate = code => win.webContents.executeJavaScript(code).catch(error => {
  throw new Error(`${error.message} [${code.slice(0, 180)}]`);
});
async function wake() {
  await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true});
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
}
async function wait(expression, label, timeout = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await wake();
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error(`${label}: ${await evaluate('document.querySelector("main")?.innerText.slice(-3000)')}`);
}
async function screenshot(name) {
  await fs.mkdir(path.join(root, 'artifacts'), {recursive:true});
  const [width, height] = win.getSize();
  win.setSize(width + 1, height); await wake();
  win.setSize(width, height); await wake();
  await new Promise(resolve => setTimeout(resolve, 120));
  const capture = await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true});
  await fs.writeFile(path.join(root, 'artifacts', name), capture.toPNG());
}
async function fixture() {
  const dates = [];
  for (let day = new Date('2024-01-02T00:00:00Z'); dates.length < 75; day = new Date(day.getTime() + 86400000)) {
    if (day.getUTCDay() > 0 && day.getUTCDay() < 6) dates.push(day.toISOString().slice(0, 10));
  }
  await fs.mkdir(path.join(fixtureDirectory, 'vipdoc', 'sh', 'lday'), {recursive:true});
  for (let stock = 0; stock < 3; stock++) {
    const bytes = Buffer.alloc(dates.length * 32);
    for (let i = 0; i < dates.length; i++) {
      const open = 1000 + stock * 200 + i * 5;
      const close = open + (i % 3 === 0 ? -11 : 15);
      const at = i * 32, volume = 2000000 + i * 1000;
      bytes.writeUInt32LE(Number(dates[i].replaceAll('-', '')), at);
      bytes.writeUInt32LE(open, at + 4);
      bytes.writeUInt32LE(Math.max(open, close) + 8, at + 8);
      bytes.writeUInt32LE(Math.min(open, close) - 6, at + 12);
      bytes.writeUInt32LE(close, at + 16);
      bytes.writeFloatLE(close / 100 * volume, at + 20);
      bytes.writeUInt32LE(volume, at + 24);
    }
    await fs.writeFile(path.join(fixtureDirectory, 'vipdoc', 'sh', 'lday', `sh${600000 + stock}.day`), bytes);
  }
  return dates;
}
function sampleTable(dates) {
  const row = (id, symbol, name, date = '') => `<tr id="${id}"${date ? ` data-stock-chart-date="${date}"` : ''}><td><input type="checkbox" aria-label="选择${name}"></td><td><button type="button" class="market-explorer-security" data-stock="${symbol}"><strong>${name}</strong><small>${symbol}</small></button></td><td>+2.16%</td><td>+16.80%</td><td><button type="button">加入</button></td></tr>`;
  return `<section class="panel" id="smoke-stock-panel"><div class="panel-head"><h2>股票列表 · 日 K 线</h2></div><p class="subtle">隔离合成行情，用于验证表格内 K 线和历史日期。</p><div class="table-wrap"><table id="smoke-stocks"><thead><tr><th>选择</th><th>证券</th><th>涨跌幅</th><th>20 日涨幅</th><th>跟踪</th></tr></thead><tbody><tr id="stock-divider"><td colspan="5">证券分组</td></tr>${row('latest', 'sh600000', '最新日线样本')}${row('duplicate', 'sh600000', '重复证券共用行情')}${row('historical', 'sh600001', '指定历史日期', dates[40])}${row('prehistory', 'sh600001', '历史覆盖前无数据', '2023-01-02')}${row('missing', 'sh600003', '没有日线的证券')}</tbody></table></div></section><section class="panel"><div class="panel-head"><h2>账户统计</h2></div><table id="smoke-metrics"><thead><tr><th>累计收益</th><th>最大回撤</th></tr></thead><tbody><tr><td>6.8%</td><td>2.1%</td></tr></tbody></table></section><div style="height:2200px" aria-hidden="true"></div><section class="panel"><table id="smoke-offscreen"><thead><tr><th>选择</th><th>证券</th><th>涨跌幅</th><th>20 日涨幅</th><th>跟踪</th></tr></thead><tbody>${row('offscreen', 'sh600002', '滚动后读取日线')}</tbody></table></section>`;
}
async function candleDates(selector) {
  return evaluate(`Array.from(document.querySelectorAll(${JSON.stringify(selector + ' [data-kline-candle]')}),el=>el.dataset.klineCandle||el.dataset.date)`);
}
function calls(symbol, end) {
  return barRequests.filter(payload => payload.symbol === symbol && (payload.end || '') === (end || '') && payload.limit === 30);
}

app.whenReady().then(async () => {
  const dates = await fixture();
  session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*', 'https://*/*']}, (_details, callback) => callback({cancel:true}));
  win = await require('../desktop/runtime.cjs').createDesktop({dataDirectory, show:false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', (_event, details) => {if (details.level === 'error') errors.push(details.message);});
    window.webContents.on('preload-error', (_event, _file, error) => errors.push(error.message));
  }});
  await evaluate('window.__stockTableErrors=[];window.addEventListener("error",event=>__stockTableErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__stockTableErrors.push(String(event.reason)));');
  assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`)).ok, true);
  assert.equal((await evaluate("atradeDesktop.market('market.scan')")).ok, true);
  await evaluate('AT.navigate("C05");AT.marketUI.refresh()');
  await wake();
  assert.equal(await evaluate('typeof AT.stockTableCharts?.mount'), 'function', '统一股票表格 K 线模块已在桌面加载');
  barRequests.length = 0;
  const html = sampleTable(dates);
  const dividerSpan = await evaluate(`(()=>{document.querySelector('main').innerHTML=${JSON.stringify(html)};const cell=document.querySelector('#stock-divider td'),before=cell.colSpan;AT.stockTableCharts.mount(document.querySelector('main'));window.scrollTo(0,0);return {before,after:cell.colSpan};})()`);
  assert.deepEqual(dividerSpan, {before:5,after:6}, '非股票分隔行的 colspan 只补上新增的 K 线列');
  await wait('document.querySelectorAll("#smoke-stocks .stock-kline-svg").length===3', '真实日线填入可见表格');
  await wait('!document.querySelector("#prehistory .stock-kline-svg") && /无|没有|暂无|缺/.test(document.querySelector("#prehistory .stock-kline-cell")?.innerText||"")', '历史覆盖前的空数据提示');
  await wait('!document.querySelector("#missing .stock-kline-svg") && /无|没有|暂无|缺|未|不可用/.test(document.querySelector("#missing .stock-kline-cell")?.innerText||"")', '无日线证券提示');
  assert.deepEqual(await candleDates('#latest'), dates.slice(-30), '最近 30 根 K 线来自真实 OHLC 返回值');
  assert.deepEqual(await candleDates('#historical'), dates.slice(11, 41), '历史行只显示截止当日的 30 根 K 线');
  assert.equal(calls('sh600000').length, 1, '重复证券共用一次 market.bars IPC');
  assert.equal(calls('sh600001', dates[40]).length, 1, '历史截止日期构成独立缓存键');
  assert.equal(calls('sh600002').length, 0, '屏幕外的行未读取日线');
  assert.deepEqual(await evaluate('(()=>{const table=document.querySelector("#smoke-metrics");return {headings:table.querySelectorAll("th").length,cells:table.querySelectorAll("td").length,kline:table.querySelectorAll(".stock-kline-cell").length};})()'), {headings:2,cells:2,kline:0}, '非证券统计表保持原样');
  assert.equal(await evaluate('document.querySelector("#smoke-stocks").outerHTML.includes("NaN")'), false);
  assert.equal(await evaluate('document.querySelectorAll("#smoke-stocks [data-stock-kline-heading]").length'), 1);
  assert.equal(await evaluate('document.querySelectorAll("#smoke-stocks .stock-kline-cell").length'), 5);
  for (const width of [1480, 1100]) {
    win.setSize(width, 1100);
    await wake();
    await screenshot(`stock-table-charts-${width}.png`);
    const box = await evaluate('(()=>{const svg=document.querySelector("#latest .stock-kline-svg"),button=svg.closest("button"),s=svg.getBoundingClientRect(),b=button.getBoundingClientRect();return {width:s.width,height:s.height,fits:s.left>=b.left-1&&s.right<=b.right+1};})()');
    assert.ok(box.width >= 120 && box.height >= 35 && box.fits, `${width}px 下 K 线在按钮内完整显示：${JSON.stringify(box)}`);
  }
  // Exercise an actual DOM reorder and two additional mount passes; sorting and
  // page render hooks must not accumulate columns or refetch cached series.
  await evaluate('(()=>{const rows=document.querySelector("#smoke-stocks tbody");rows.prepend(rows.lastElementChild);AT.stockTableCharts.mount();AT.stockTableCharts.mount();})()');
  await wake();
  assert.equal(await evaluate('document.querySelectorAll("#smoke-stocks [data-stock-kline-heading]").length'), 1);
  assert.equal(await evaluate('document.querySelectorAll("#smoke-stocks .stock-kline-cell").length'), 5);
  assert.equal(await evaluate('document.querySelector("#stock-divider td").colSpan'), 6, '多次挂载和排序后分隔行 colspan 不再增加');
  assert.equal(calls('sh600000').length, 1, '排序和重复挂载复用已加载行情');
  await evaluate('document.querySelector("#offscreen").scrollIntoView({block:"center",behavior:"instant"});true');
  await wait('!!document.querySelector("#offscreen .stock-kline-svg")', '滚动进入屏幕后读取日线');
  assert.equal(calls('sh600002').length, 1);
  assert.deepEqual(await candleDates('#offscreen'), dates.slice(-30));

  // Delay one real, already-read response while the fixture's source is
  // rescanned. The old generation must never overwrite the new source's OHLC.
  await evaluate('document.querySelector("#latest").scrollIntoView({block:"center",behavior:"instant"});true');
  await wake();
  const originalTitle = await evaluate('document.querySelector("#latest [data-kline-candle]:last-child title").textContent');
  let responseReady, releaseResponse;
  const ready = new Promise(resolve => {responseReady = resolve;});
  heldBars = {symbol:'sh600000', ready:responseReady, released:new Promise(resolve => {releaseResponse = resolve;})};
  await evaluate('AT.stockTableCharts.invalidate();true');
  await wake();
  const priorResponse = await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(new Error('Old-source response was not observed')), 12000))]);
  assert.equal(priorResponse.ok, true, '延迟的响应仍由真实行情服务返回');
  const stockFile = path.join(fixtureDirectory, 'vipdoc', 'sh', 'lday', 'sh600000.day');
  const bytes = await fs.readFile(stockFile);
  for (let i = 0; i < dates.length; i++) {
    for (const offset of [4, 8, 12, 16]) bytes.writeUInt32LE(bytes.readUInt32LE(i * 32 + offset) + 500, i * 32 + offset);
    bytes.writeFloatLE(bytes.readUInt32LE(i * 32 + 16) / 100 * bytes.readUInt32LE(i * 32 + 24), i * 32 + 20);
  }
  await fs.writeFile(stockFile, bytes);
  assert.equal((await evaluate("atradeDesktop.market('market.scan')")).ok, true);
  await evaluate('AT.stockTableCharts.invalidate();true');
  await wait('document.querySelector("#latest [data-kline-candle]:last-child title")?.textContent.includes("开 18.7")', '数据源重扫后重新读取真实 OHLC');
  const refreshedTitle = await evaluate('document.querySelector("#latest [data-kline-candle]:last-child title").textContent');
  assert.notEqual(refreshedTitle, originalTitle);
  assert.equal(calls('sh600000').length, 3, '每个数据源版本独立读取，重复行仍然只共享一次请求');
  assert.equal(calls('sh600002').length, 1, '失效后屏幕外证券仍不预先读取');
  releaseResponse();
  await wake();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await evaluate('document.querySelector("#latest [data-kline-candle]:last-child title").textContent'), refreshedTitle, '迟到的旧数据源响应不能覆盖新 K 线');

  const historicButton = '#historical button[data-stock-kline]';
  await evaluate(`document.querySelector(${JSON.stringify(historicButton)}).scrollIntoView({block:'center',behavior:'instant'});document.querySelector(${JSON.stringify(historicButton)}).focus();true`);
  await wake();
  assert.equal(await evaluate(`document.activeElement===document.querySelector(${JSON.stringify(historicButton)})`), true, 'K 线按钮可用键盘聚焦');
  win.webContents.focus();
  win.webContents.sendInputEvent({type:'keyDown', keyCode:'Enter'});
  win.webContents.sendInputEvent({type:'char', keyCode:'\r'});
  win.webContents.sendInputEvent({type:'keyUp', keyCode:'Enter'});
  await wait(`AT.state.page==='G01'&&AT.state.stock==='sh600001'&&document.querySelector('[data-market-date]')?.value===${JSON.stringify(dates[40])}`, '键盘打开完整日 K 并定位历史日期');
  await screenshot('stock-table-charts-open-day.png');

  assert.deepEqual(await evaluate('__stockTableErrors'), []);
  assert.deepEqual(errors, []);
  console.log('PASS stock table charts: real OHLC/latest and historical30, absent history, IPC dedup, viewport loading, metrics excluded, remount/sort stability, source rescan and stale response protection, native keyboard date drilldown, 1480/1100 visual captures');
  win.close();
  setTimeout(() => app.exit(0), 300);
}).catch(error => {
  console.error(error.stack);
  if (win && !win.isDestroyed()) win.destroy();
  app.exit(1);
});
