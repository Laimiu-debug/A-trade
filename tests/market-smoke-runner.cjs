const { app, protocol, dialog } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const [directory, phase] = process.argv.slice(2);
if (!directory || !['write', 'read'].includes(phase)) throw new Error('Missing market smoke arguments');
const root = path.resolve(__dirname, '..');
const dataDirectory = path.join(directory, 'workspace');
const fixtureDirectory = path.join(directory, '自选行情目录');
const targetFile = path.join(fixtureDirectory, 'vipdoc/sz/lday/sz000001.day');
const fixtureSymbolCount = 39;
const rendererErrors = [];
let win, nextPickerResult = { canceled: true, filePaths: [] }, pickerCalls = 0;

app.setPath('userData', path.join(directory, 'electron-profile'));
protocol.registerSchemesAsPrivileged([{ scheme: 'atrade', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
// The only simulated desktop boundary is the user's response to the native picker.
dialog.showOpenDialog = async (_window, options) => {
  assert.deepEqual(options.properties, ['openDirectory']);
  assert.equal(options.defaultPath, undefined, '软件不内置个人目录');
  pickerCalls++;
  return nextPickerResult;
};
dialog.showMessageBox = async (_window, options) => {
  throw new Error(`Unexpected modal: ${options.message}`);
};
const evaluate = code => win.webContents.executeJavaScript(code);
const wait = async (expression, label, timeout = 12000) => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  const content = await evaluate('document.querySelector("main")?.innerText.slice(-1600)');
  throw new Error(`${label} 超时: ${content || ''}`);
};
const click = selector => evaluate(`(() => {const el=[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length);if(!el||el.disabled)throw new Error('Missing or disabled: '+${JSON.stringify(selector)});el.click();})()`);
const action = name => click(`[data-market-action="${name}"]`);
const fill = (selector, value) => evaluate(`(() => {const el=document.querySelector(${JSON.stringify(selector)});if(!el)throw new Error('Missing input');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const chartWindow = selector => evaluate(`(() => {const chart=document.querySelector(${JSON.stringify(selector)});return {start:Number(chart.dataset.chartStart),count:chart.hasAttribute('data-chart-count')?Number(chart.dataset.chartCount):null,end:chart.hasAttribute('data-chart-end')?Number(chart.dataset.chartEnd):null};})()`);
const chartKey = (selector, key) => evaluate(`document.querySelector(${JSON.stringify(selector)}).dispatchEvent(new KeyboardEvent('keydown',{key:${JSON.stringify(key)},bubbles:true}))`);
const chartSpan = range => range.count ?? range.end - range.start;
async function checkStudyAlignment() {
  const alignment = await evaluate(`(() => {
    const chart=document.querySelector('[data-market-chart]'), indicator=document.querySelector('[data-market-indicator-chart]');
    const indexes=element=>[...element.querySelectorAll('[data-market-index]')].map(el=>Number(el.dataset.marketIndex));
    const start=Number(chart.dataset.chartStart),count=Number(chart.dataset.chartCount);
    const firstX=Number(document.querySelector('[data-market-study-line="ma20"]').getAttribute('d').trim().match(/^M([^,]+)/)?.[1]);
    return {main:indexes(chart),indicator:indicator?indexes(indicator):[],firstX,expectedX:24+934/count*(Math.max(19,start)-start+.5)};
  })()`);
  assert.deepEqual(alignment.indicator, alignment.main, '副图始终对应当前可见日K的原始索引');
  assert.ok(Math.abs(alignment.firstX - alignment.expectedX) < .01, 'MA20实际观察点与日K中心对齐');
  assert.equal(await evaluate('document.querySelector("[data-market-chart]").outerHTML.includes("NaN")'), false);
}
async function checkWideStudyAlignment() {
  const originalSize = win.getSize();
  for (const width of [1900, 2200]) {
    win.setSize(width, 1300);
    await win.webContents.capturePage(undefined, {stayHidden:true,stayAwake:true});
    const positions = await evaluate(`(() => {const main=document.querySelector('[data-market-chart]'),study=document.querySelector('[data-market-indicator-chart]');return [80,900].map(x=>({main:new DOMPoint(x,89).matrixTransform(main.getScreenCTM()).x,study:new DOMPoint(x,89).matrixTransform(study.getScreenCTM()).x}));})()`);
    for (const point of positions) assert.ok(Math.abs(point.main-point.study)<.5, `${width}px 主副图屏幕X坐标一致：${JSON.stringify(point)}`);
  }
  const before = await chartWindow('[data-market-chart]');
  const intercepted = await evaluate(`(() => {const chart=document.querySelector('[data-market-indicator-chart]'),point=new DOMPoint(450,165).matrixTransform(chart.getScreenCTM()),event=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-120,clientX:point.x,clientY:point.y});chart.dispatchEvent(event);return event.defaultPrevented;})()`);
  assert.equal(intercepted,false,'副图150至180的绘图区外空白不拦截滚轮');
  assert.deepEqual(await chartWindow('[data-market-chart]'),before);
  win.setSize(...originalSize);
}
async function chartPoint(selector, ratio = .5) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'})`);
  return evaluate(`(() => {const chart=document.querySelector(${JSON.stringify(selector)}),point=new DOMPoint(24+934*${ratio},160).matrixTransform(chart.getScreenCTM());return {x:Math.round(point.x),y:Math.round(point.y)};})()`);
}
async function mouseInput(event) {
  win.webContents.sendInputEvent(event);
  await new Promise(resolve => setTimeout(resolve, 25));
  await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
}
async function dragChart(selector, button, distance = .12, options = {}) {
  const before = await chartWindow(selector), from = await chartPoint(selector, .45), to = await chartPoint(selector, .45 + distance);
  await mouseInput({type:'mouseMove', ...from});
  await mouseInput({type:'mouseDown', ...from, button, clickCount:options.clickCount || 1});
  try {
    for (let step = 1; step <= 3; step++) {
      await mouseInput({type:'mouseMove', x:Math.round(from.x+(to.x-from.x)*step/3),y:from.y,modifiers:[`${button}ButtonDown`]});
    }
    assert.equal(await evaluate('document.documentElement.classList.contains("market-dragging")'), true, '按住移动进入拖动状态');
  } finally {
    await mouseInput({type:'mouseUp', ...to, button, clickCount:options.clickCount || 1});
  }
  await wait(`!!document.querySelector(${JSON.stringify(selector)})`, '拖动保留当前图表');
  const after = await chartWindow(selector);
  assert.equal(await evaluate('document.documentElement.classList.contains("market-dragging")'), false, '松开鼠标清理拖动状态');
  assert.ok(Math.abs(chartSpan(after)-chartSpan(before)) < .00001, '拖动保持缩放数量或时间跨度');
  if (options.changed !== false) assert.ok(distance > 0 ? after.start < before.start : after.start > before.start, `${button}按住拖动移动时间轴: ${JSON.stringify({before,after})}`);
  await mouseInput({type:'mouseMove', ...from});
  assert.deepEqual(await chartWindow(selector), after, '松开鼠标后悬停不继续拖动');
  return {before,after};
}
async function cancelChartDrag(selector, reason) {
  const from = await chartPoint(selector, .45), to = await chartPoint(selector, .6);
  await evaluate('document.addEventListener("pointerdown",event=>{window.__marketDragPointerId=event.pointerId},{capture:true,once:true})');
  await mouseInput({type:'mouseMove', ...from});
  await mouseInput({type:'mouseDown', ...from, button:'left', clickCount:1});
  try {
    await mouseInput({type:'mouseMove', ...to, modifiers:['leftButtonDown']});
    assert.equal(await evaluate('document.documentElement.classList.contains("market-dragging")'), true);
    if (reason === 'pointercancel') await evaluate('document.documentElement.dispatchEvent(new PointerEvent("pointercancel",{bubbles:true,pointerId:window.__marketDragPointerId}))');
    else await evaluate('window.dispatchEvent(new Event("blur"))');
    const canceled = await chartWindow(selector);
    assert.equal(await evaluate('document.documentElement.classList.contains("market-dragging")'), false, `${reason}清理拖动状态`);
    await mouseInput({type:'mouseMove', ...from, modifiers:['leftButtonDown']});
    assert.deepEqual(await chartWindow(selector), canceled, `${reason}之后不再跟随按住的鼠标平移`);
  } finally {
    await mouseInput({type:'mouseUp', ...from, button:'left', clickCount:1});
  }
}
async function wheelChart(selector, deltaY, ratio = .5, options = {}, changed = true) {
  const before = await chartWindow(selector);
  const prevented = await evaluate(`(() => {const chart=document.querySelector(${JSON.stringify(selector)}),point=new DOMPoint(24+934*${ratio},160).matrixTransform(chart.getScreenCTM()),event=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:${deltaY},clientX:point.x,clientY:point.y,...${JSON.stringify(options)}});chart.dispatchEvent(event);return event.defaultPrevented;})()`);
  // Wake a hidden test window's compositor so the production RAF batching runs.
  await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
  if (changed) await wait(`(() => {const chart=document.querySelector(${JSON.stringify(selector)});return Number(chart.dataset.chartStart)!==${before.start} || Number(chart.dataset.${before.count === null ? 'chartEnd' : 'chartCount'})!==${before.count ?? before.end};})()`, '滚轮更新可视窗口');
  return {before, after: await chartWindow(selector), prevented};
}
async function navigate(page) {
  await evaluate(`AT.navigate(${JSON.stringify(page)});true`);
  await wait(`AT.state.page===${JSON.stringify(page)}`, `打开 ${page}`);
}
async function screenshot(name) {
  await fs.mkdir(path.join(root, 'artifacts'), { recursive: true });
  await evaluate('document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getTiming().iterations))try{a.finish()}catch{}})');
  // Hidden Electron windows may retain an old compositor frame despite current
  // DOM/RAF state. A reversible one-pixel resize invalidates that surface.
  const [width, height] = win.getSize();
  win.setSize(width + 1, height);
  await win.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
  win.setSize(width, height);
  await win.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
  await new Promise(resolve => setTimeout(resolve, 180));
  const capture = await win.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
  await fs.writeFile(path.join(root, 'artifacts', name), capture.toPNG());
}
async function createFixture() {
  for (const market of ['sh', 'sz', 'bj']) await fs.mkdir(path.join(fixtureDirectory, 'vipdoc', market, 'lday'), { recursive: true });
  await fs.mkdir(path.join(fixtureDirectory, 'T0002/hq_cache'), { recursive: true });
  const names = { sh: [['000001', 'SH INDEX'], ['600000', 'SH BANK']], sz: [['000001', 'SZ BANK']], bj: [['920001', 'BJ STOCK']] };
  for (let index = 0; index < 32; index++) names.sh.push([String(600100 + index), `OBSERVE ${index}`]);
  names.sh.push(['688001', 'STAR STOCK'], ['600777', 'OLDER STOCK']);
  names.sz.push(['300001', 'GROWTH STOCK']);
  for (const [market, securities] of Object.entries(names)) {
    const tnf = Buffer.alloc(50 + securities.length * 360);
    for (let index = 0; index < securities.length; index++) {
      const [code, name] = securities[index];
      tnf.write(code, 50 + index * 360, 'ascii');
      tnf.write(name, 50 + index * 360 + 31, 'ascii');
      const older = code === '600777', count = older ? 80 : 90;
      const extra = code.startsWith('6001'), ordinal = extra ? Number(code) - 600100 : 0;
      const file = Buffer.alloc(count * 32);
      for (let day = 0; day < count; day++) {
        const date = new Date(Date.UTC(2026, 0, 1 + day));
        const offset = day * 32, price = 1000 + day * 2 + (extra ? ordinal * 5 + (day === 89 ? ordinal % 3 === 0 ? 160 : ordinal % 2 ? -160 : 0 : 0) : 0);
        file.writeUInt32LE(Number(date.toISOString().slice(0, 10).replaceAll('-', '')), offset);
        for (const [at, value] of [[4, price], [8, price + 10], [12, price - 10], [16, price + 4]]) file.writeUInt32LE(value, offset + at);
        file.writeFloatLE(older ? 90000000 : 250000 + day * 100 + (extra ? 1000000 + ordinal * 100000 : 0), offset + 20);
        file.writeUInt32LE((10000 + day * 100) * (code === '600131' && day === 89 ? 3 : 1), offset + 24);
      }
      await fs.writeFile(path.join(fixtureDirectory, 'vipdoc', market, 'lday', `${market}${code}.day`), file);
    }
    await fs.writeFile(path.join(fixtureDirectory, 'T0002/hq_cache', `${market}s.tnf`), tnf);
  }
  const minutes = Buffer.alloc(240 * 32), packedDate = ((2026 - 2004) << 11) | (3 * 100 + 31);
  for (let minute = 0; minute < 240; minute++) {
    const offset = minute * 32, price = 11.5 + minute / 1000;
    minutes.writeUInt16LE(packedDate, offset);
    minutes.writeUInt16LE(minute < 120 ? 571 + minute : 781 + minute - 120, offset + 2);
    for (const [at, value] of [[4, price], [8, price + .03], [12, price - .02], [16, price + .01], [20, 100000 + minute * 100]]) minutes.writeFloatLE(value, offset + at);
    minutes.writeUInt32LE(10000 + minute, offset + 24);
  }
  await fs.mkdir(path.join(fixtureDirectory, 'vipdoc/sz/minline'), {recursive: true});
  await fs.writeFile(path.join(fixtureDirectory, 'vipdoc/sz/minline/sz000001.lc1'), minutes);
}

app.whenReady().then(async () => {
  if (phase === 'write') await createFixture();
  win = await require('../desktop/runtime.cjs').createDesktop({ dataDirectory, show: false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', (_event, details) => { if (details.level === 'error') rendererErrors.push(details.message); });
    window.webContents.on('preload-error', (_event, _file, error) => rendererErrors.push(error.message));
  } });
  await evaluate(`window.__marketSmokeErrors=[];window.addEventListener('error',event=>__marketSmokeErrors.push(event.message));window.addEventListener('unhandledrejection',event=>__marketSmokeErrors.push(String(event.reason)));`);
  await wait('!!AT.marketUI', '行情模块启动');

  if (phase === 'write') {
    await navigate('M04');
    await wait('document.querySelector("main").innerText.includes("从你的本地日线开始")', '新用户空配置');
    assert.equal(await evaluate('document.querySelectorAll(".market-stock-card,.market-explorer-table [data-stock]").length'), 0, '未配置不显示演示股票');
    await click('[data-nav="C01"]');
    await wait('!!document.querySelector("[data-market-path]") && !document.querySelector("[data-market-action=choose]").disabled', '目录设置');
    assert.equal(await evaluate('document.querySelector("[data-market-path]").value'), '');
    await action('choose');
    await wait('document.querySelector(".market-feedback").innerText.includes("已取消")', '取消系统选择');
    assert.equal(await evaluate('document.querySelector("[data-market-path]").value'), '');
    nextPickerResult = { canceled: false, filePaths: [path.join(fixtureDirectory, 'vipdoc')] };
    await action('choose');
    await wait(`document.querySelector('[data-market-path]').value===${JSON.stringify(path.join(fixtureDirectory, 'vipdoc'))}`, '选择个人目录');
    assert.equal(pickerCalls, 2);
    await action('configure');
    await wait(`AT.marketUI.status?.symbolCount===${fixtureSymbolCount} && !document.querySelector("[data-market-action=configure]").disabled`, '保存并扫描');
    assert.equal((await evaluate('AT.marketUI.status')).path, fixtureDirectory, 'vipdoc规范为安装根目录');
    await screenshot('market-settings.png');
    await fill('[data-market-path]', path.join(directory, 'missing'));
    await action('configure');
    await wait('document.querySelector(".market-feedback[data-tone=error]") && !document.querySelector("[data-market-action=configure]").disabled', '无效目录明确提示');
    assert.equal((await evaluate('AT.marketUI.status')).path, fixtureDirectory, '校验失败保留上次可用配置');

    await navigate('M04');
    await wait('document.querySelectorAll(".market-explorer-table [data-stock]").length===15', '全量研究分页证券列表');
    const research = await evaluate("atradeDesktop.market('market.research',{})");
    assert.equal(research.ok, true);
    assert.equal(research.data.asOf, '2026-03-31');
    assert.equal(research.data.items.length, fixtureSymbolCount - 1);
    assert.equal(research.data.staleItems.length, 1);
    assert.equal(research.data.style.key, 'insufficient', '小样本不伪装为全市场风格');
    const stockRows = () => evaluate('[...document.querySelectorAll(".market-explorer-table [data-stock]")].map(el=>el.dataset.stock)');
    const field = async (name, value) => {
      if (['board', 'direction', 'freshness', 'above', 'volume'].includes(name) && await evaluate('document.querySelector(".market-explorer-filters")?.hidden')) await click('[data-explorer-field="filtersOpen"]');
      await click(`[data-explorer-field="${name}"][data-explorer-value="${value}"]`);
    };
    await click('[data-explorer-sort="amount"]');
    const ascending = [...research.data.items].sort((a,b)=>a.amount-b.amount || a.symbol.localeCompare(b.symbol)).slice(0,15).map(row=>row.symbol);
    assert.deepEqual(await stockRows(), ascending, '排序作用于全量快照，不是当前15条');
    await click('[data-explorer-page="1"]');
    assert.deepEqual(await stockRows(), [...research.data.items].sort((a,b)=>a.amount-b.amount || a.symbol.localeCompare(b.symbol)).slice(15,30).map(row=>row.symbol));
    await fill('[data-explorer-query]', '600131');
    await wait('document.querySelectorAll(".market-explorer-table [data-stock]").length===1', '搜索覆盖当前页外股票');
    assert.deepEqual(await stockRows(), ['sh600131']);
    await fill('[data-explorer-query]', '');
    await wait('document.querySelectorAll(".market-explorer-table [data-stock]").length===15', '清空搜索恢复分页');
    await field('board', 'star');
    assert.deepEqual(await stockRows(), ['sh688001']);
    await field('board', 'all');
    await field('freshness', 'older');
    assert.deepEqual(await stockRows(), ['sh600777']);
    assert.match(await evaluate('document.querySelector(".market-explorer-table").innerText'), /2026-03-21/);
    await field('pool', 'active');
    await field('size', '60');
    const active = await stockRows();
    assert.equal(active.length, fixtureSymbolCount - 2, '热门观察池排除指数与旧日股票');
    assert.ok(!active.includes('sh000001') && !active.includes('sh600777'));
    await field('pool', 'gainers');
    assert.deepEqual(await stockRows(), [...research.data.items].filter(row=>row.kind==='stock' && row.changePercent>0).sort((a,b)=>b.changePercent-a.changePercent || a.symbol.localeCompare(b.symbol)).map(row=>row.symbol));
    await field('pool', 'volume');
    assert.deepEqual(await stockRows(), ['sh600131'], '放量池使用真实前五日均量比');
    await field('pool', 'all');
    await field('size', '15');
    await screenshot('market-list.png');
    await navigate('M01');
    await wait('document.querySelector(".market-analysis-style")?.innerText.includes("2026-03-31")', '市场风格使用统一研究日期');
    assert.equal(await evaluate('document.querySelectorAll(".market-analysis-style").length'), 1);
    assert.match(await evaluate('document.querySelector(".market-analysis-style").innerText'), /数据不足/);
    assert.doesNotMatch(await evaluate('document.querySelector("main").innerText'), /本地指数收盘|成长占优|演示指数/);
    assert.equal(await evaluate('document.querySelectorAll("[data-market-style-day]").length'), research.data.timeline.length);
    await click(`[data-market-style-day="${research.data.timeline[0].date}"]`);
    assert.match(await evaluate('document.querySelector(".market-analysis-date").innerText'), new RegExp(research.data.timeline[0].date), '选择历史风格同步日期');
    await click('[data-market-style-day="2026-03-31"]');
    await screenshot('market-style.png');
    await navigate('M04');
    await fill('[data-explorer-query]', '000001');
    await wait('document.querySelectorAll(".market-explorer-table [data-stock]").length===2', '同代码不同市场不混淆');
    await click('[data-stock="sh000001"]');
    await wait('!!document.querySelector("[data-market-chart]")', '指数日线');
    assert.match(await evaluate('document.querySelector("[data-market-readout]").innerText'), /手/);
    await click('[data-action="back"]');
    await wait('document.querySelectorAll(".market-explorer-table [data-stock]").length===2', '返回保留搜索');
    await click('[data-stock="sz000001"]');
    await wait('!!document.querySelector("[data-market-chart]")', '股票日线');
    assert.equal(await evaluate('AT.state.stock'), 'sz000001');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-chart] [data-market-index]").length'), 90);
    assert.match(await evaluate('document.querySelector("main").innerText'), /SZ BANK/);
    assert.match(await evaluate('document.querySelector("[data-market-readout]").innerText'), /股/);
    assert.equal(await evaluate('document.querySelectorAll("[data-candles],.intraday-panel,.overlay").length'), 0);
    await evaluate('document.querySelector("[data-market-chart]").dispatchEvent(new KeyboardEvent("keydown",{key:"Home",bubbles:true}))');
    assert.match(await evaluate('document.querySelector("[data-market-readout]").innerText'), /2026-01-01/);
    await click('[data-market-limit="60"]');
    await wait('document.querySelectorAll("[data-market-chart] [data-market-index]").length===60', '切换日线范围');
    await click('[data-market-limit="240"]');
    await wait('document.querySelectorAll("[data-market-chart] [data-market-index]").length===90', '返回完整日线');
    assert.equal(await evaluate('document.querySelectorAll(".market-analysis-controls [data-market-study]").length'), 4);
    await checkStudyAlignment();
    await checkWideStudyAlignment();
    await click('[data-market-study="ma"]');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-study-line^=ma]").length'), 0, '均线可关闭');
    await click('[data-market-study="ma"]');
    await click('[data-market-study="force"]');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-indicator-chart]").length'), 0, '副图可关闭');
    await click('[data-market-study="force"]');
    await click('[data-market-study="signals"]');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-signal]").length'), 0, '信号标记可关闭');
    await click('[data-market-study="signals"]');
    await click('[data-market-study="box"]');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-study-line^=box]").length'), 2, '显示箱体上下沿');
    assert.equal(await evaluate('document.querySelector("[data-market-study-line=boxHigh]").getAttribute("d").trim()'), '', '未满足突破条件时不编造箱体');
    await checkStudyAlignment();
    await screenshot('market-daily.png');

    const dailySelector = '[data-market-chart]';
    const zoomedDaily = await wheelChart(dailySelector, -240, .35);
    assert.equal(zoomedDaily.prevented, true, '图表内滚轮用于缩放');
    assert.ok(zoomedDaily.after.count < 90 && zoomedDaily.after.start > 0, '向上滚轮放大日K视窗');
    assert.ok(Math.abs(zoomedDaily.after.start + .35 * zoomedDaily.after.count - 90 * .35) <= .5, '鼠标处交易位置保持锚定');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-chart] [data-market-index]").length'), zoomedDaily.after.count);
    await checkStudyAlignment();
    assert.equal(await evaluate('(()=>{const event=new WheelEvent("wheel",{bubbles:true,cancelable:true,deltaY:120});document.querySelector(".market-chart-toolbar").dispatchEvent(event);return event.defaultPrevented;})()'), false, '图表外保留页面滚动');
    for (const modifier of ['ctrlKey', 'metaKey']) {
      const ignored = await wheelChart(dailySelector, -120, .35, {[modifier]: true}, false);
      assert.equal(ignored.prevented, false, `${modifier}滚轮保留浏览器行为`);
      assert.deepEqual(ignored.after, ignored.before);
    }
    const zoomedStart = zoomedDaily.after.start;
    await evaluate(`document.querySelector('[data-market-index="${zoomedStart}"]').dispatchEvent(new PointerEvent('pointermove',{bubbles:true}))`);
    assert.match(await evaluate('document.querySelector("[data-market-readout]").innerText'), new RegExp(new Date(Date.UTC(2026, 0, 1 + zoomedStart)).toISOString().slice(0, 10)), '缩放后悬停显示原始交易日');
    await chartKey(dailySelector, 'ArrowLeft');
    assert.equal((await chartWindow(dailySelector)).start, zoomedStart - 1, '方向键越过左边缘自动移动视窗');
    await dragChart(dailySelector, 'left', .12);
    await dragChart(dailySelector, 'right', -.12);
    await checkStudyAlignment();
    await dragChart(dailySelector, 'left', .12, {clickCount:2});
    assert.equal(await evaluate('document.querySelectorAll("[data-market-intraday-chart]").length'), 0, '双击序列中的拖动不钻取分时');
    assert.equal(await evaluate('(()=>{const event=new MouseEvent("contextmenu",{bubbles:true,cancelable:true,button:2});document.querySelector("[data-market-chart]").dispatchEvent(event);return event.defaultPrevented;})()'), true, '图内右键不显示浏览器菜单');
    assert.equal(await evaluate('(()=>{const event=new MouseEvent("contextmenu",{bubbles:true,cancelable:true,button:2});document.querySelector(".market-chart-toolbar").dispatchEvent(event);return event.defaultPrevented;})()'), false, '图外不拦截右键菜单');
    const beforeOutside = await chartWindow(dailySelector);
    assert.equal(await evaluate('(()=>{const event=new PointerEvent("pointerdown",{bubbles:true,cancelable:true,button:0,buttons:1,pointerId:991,pointerType:"mouse"});document.querySelector(".market-chart-toolbar").dispatchEvent(event);return event.defaultPrevented;})()'), false, '图外按下不接管拖动');
    await evaluate('document.querySelector("[data-market-chart]").dispatchEvent(new PointerEvent("pointermove",{bubbles:true,buttons:1,pointerId:991,pointerType:"mouse",clientX:400,clientY:200}))');
    assert.deepEqual(await chartWindow(dailySelector), beforeOutside, '图外起始的拖动不移动图表');
    await cancelChartDrag(dailySelector, 'pointercancel');
    await cancelChartDrag(dailySelector, 'blur');
    const leftEdge = await dragChart(dailySelector, 'right', .57, {changed:false});
    assert.equal(leftEdge.after.start, 0, '拖出绘图区仍完成拖动且不越过最早记录');
    const sameEdge = await dragChart(dailySelector, 'left', .12, {changed:false});
    assert.deepEqual(sameEdge.after, sameEdge.before, '到达数据边界后不生成空白交易日');
    const expandedDaily = await wheelChart(dailySelector, 240, .35);
    assert.ok(expandedDaily.after.count > expandedDaily.before.count, '向下滚轮缩小日K');
    await action('reset-zoom');
    assert.deepEqual(await chartWindow(dailySelector), {start: 0, count: 90, end: null});
    for (let step = 0; step < 6; step++) await wheelChart(dailySelector, -240, .5, {}, step < 5);
    assert.equal((await chartWindow(dailySelector)).count, 10, '日K放大有最小可视数量');
    await screenshot('market-daily-zoom.png');
    await chartKey(dailySelector, 'End');
    assert.equal((await chartWindow(dailySelector)).start, 80, 'End可从放大窗口移动至最新交易日');
    // Use Chromium's own click synthesis to catch regressions from preventDefault/capture.
    await new Promise(resolve => setTimeout(resolve, 700));
    const doublePoint = await chartPoint(dailySelector, .95);
    await mouseInput({type:'mouseMove', ...doublePoint});
    for (const clickCount of [1, 2]) {
      await mouseInput({type:'mouseDown', ...doublePoint, button:'left', clickCount});
      await mouseInput({type:'mouseUp', ...doublePoint, button:'left', clickCount});
    }
    await wait('!!document.querySelector("[data-market-intraday-chart]")', '双击日K读取当日真实分时');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-minute-index]").length'), 240);
    assert.match(await evaluate('document.querySelector("[data-market-minute-readout]").innerText'), /2026-03-31/);
    assert.match(await evaluate('document.querySelector("[data-market-minute-readout]").innerText'), /股/);
    assert.equal(await evaluate('document.querySelectorAll(".overlay,.day-drawer").length'), 0, '分时使用页内视图');
    await evaluate('document.querySelector("[data-market-intraday-chart]").dispatchEvent(new KeyboardEvent("keydown",{key:"Home",bubbles:true}))');
    assert.match(await evaluate('document.querySelector("[data-market-minute-readout]").innerText'), /09:31/);
    await screenshot('market-intraday.png');
    await click('[data-market-mode="minute"]');
    await wait('document.querySelectorAll("[data-market-minute-candle]").length===240', '一分钟K线使用分钟开高低收');
    assert.doesNotMatch(await evaluate('document.querySelector("[data-market-intraday-chart]").outerHTML'), /NaN|undefined/);
    await screenshot('market-minute-candles.png');
    const minuteSelector = '[data-market-intraday-chart]';
    const zoomedMinute = await wheelChart(minuteSelector, -240, .75);
    assert.equal(zoomedMinute.prevented, true);
    assert.ok(zoomedMinute.after.end - zoomedMinute.after.start < 240, '分钟K向上滚轮放大');
    assert.ok(Math.abs(zoomedMinute.after.start + .75 * (zoomedMinute.after.end - zoomedMinute.after.start) - 180) < .5, `分钟时间轴保持鼠标锚点: ${JSON.stringify(zoomedMinute)}`);
    const firstMinute = await evaluate('Number(document.querySelector("[data-market-minute-index]").dataset.marketMinuteIndex)');
    assert.ok(firstMinute > 0);
    await evaluate('document.querySelector("[data-market-minute-index]").dispatchEvent(new PointerEvent("pointermove",{bubbles:true}))');
    const expectedMinute = firstMinute < 120 ? 571 + firstMinute : 781 + firstMinute - 120;
    assert.match(await evaluate('document.querySelector("[data-market-minute-readout]").innerText'), new RegExp(`${String(Math.floor(expectedMinute / 60)).padStart(2, '0')}:${String(expectedMinute % 60).padStart(2, '0')}`), '分钟K悬停仍对应实际分钟');
    await chartKey(minuteSelector, 'ArrowLeft');
    assert.ok((await chartWindow(minuteSelector)).start < zoomedMinute.after.start, '分钟方向键越过可视边缘');
    await dragChart(minuteSelector, 'left', .12);
    await dragChart(minuteSelector, 'right', -.12);
    const expandedMinute = await wheelChart(minuteSelector, 240, .75);
    assert.ok(expandedMinute.after.end - expandedMinute.after.start > zoomedMinute.after.end - zoomedMinute.after.start, '分钟K向下滚轮缩小');
    await action('reset-zoom');
    assert.deepEqual(await chartWindow(minuteSelector), {start: 0, count: null, end: 240});
    await wheelChart(minuteSelector, -240, .6);
    await screenshot('market-minute-zoom.png');
    await click('[data-market-mode="intraday"]');
    const lineBefore = await chartWindow(minuteSelector);
    const zoomedLine = await wheelChart(minuteSelector, -240, .6);
    assert.ok(zoomedLine.after.end - zoomedLine.after.start < lineBefore.end - lineBefore.start, '分时折线同样支持滚轮缩放');
    await dragChart(minuteSelector, 'left', .12);
    await dragChart(minuteSelector, 'right', -.12);
    await action('reset-zoom');
    await evaluate('(()=>{const input=document.querySelector("[data-market-intraday-date]");input.value="2026-03-30";input.dispatchEvent(new Event("change",{bubbles:true}));})()');
    await wait('document.querySelector("main").innerText.includes("没有 2026-03-30 的分钟记录")', '缺失日不制造分时或静默换日期');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-intraday-chart]").length'), 0);
    await action('available-intraday');
    await wait('!!document.querySelector("[data-market-intraday-chart]")', '显式切换可用日期');
    assert.equal(await evaluate('document.querySelector("[data-market-intraday-date]").value'), '2026-03-31');
    await action('latest-intraday');
    await wait('!!document.querySelector("[data-market-intraday-chart]")', '读取最近实际交易日');
    await click('[data-market-mode="daily"]');
    await wait('document.querySelectorAll("[data-market-chart] [data-market-index]").length===10', '分时返回日K保留缩放窗口');
    await action('reset-zoom');
    await wait('document.querySelectorAll("[data-market-chart] [data-market-index]").length===90', '重置完整日K');

    const original = await fs.readFile(targetFile), stat = await fs.stat(targetFile);
    const corrected = Buffer.from(original);
    corrected.writeUInt32LE(1007, 16); // Same-size correction of an old trading day.
    await fs.writeFile(targetFile, corrected);
    await fs.utimes(targetFile, stat.atime, stat.mtime);
    await navigate('M04');
    await wait('!!document.querySelector("[data-stock=sz000001]")', '返回搜索列表');
    await click('[data-stock="sz000001"]');
    await wait('!!document.querySelector("[data-market-chart]")', '返回图表重新校验源数据');
    await evaluate('document.querySelector("[data-market-chart]").dispatchEvent(new KeyboardEvent("keydown",{key:"Home",bubbles:true}))');
    assert.match(await evaluate('document.querySelector("[data-market-readout]").innerText'), /10\.07/, '历史修订不被前端缓存遮住');
    await fs.appendFile(targetFile, Buffer.from([1]));
    await action('retry-bars');
    await wait('document.querySelector("main").innerText.includes("32 字节")', '不完整文件显示错误');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-chart]").length'), 0, '坏文件不伪装为新数据');
    await fs.writeFile(targetFile, corrected);
    await action('retry-bars');
    await wait('!!document.querySelector("[data-market-chart]")', '源文件修复后恢复');
    await fs.rename(fixtureDirectory, fixtureDirectory + '-offline');
    await action('retry-bars');
    await wait('document.querySelector(".market-warning")?.innerText.includes("此前缓存")', '离线缓存明确标记');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-chart] [data-market-index]").length'), 90);
    await screenshot('market-offline.png');
    await fs.rename(fixtureDirectory + '-offline', fixtureDirectory);
    await action('retry-bars');
    await wait('!!document.querySelector("[data-market-chart]") && !document.querySelector(".market-warning")', '重新连接文件');

    win.setSize(900, 700);
    await new Promise(resolve => setTimeout(resolve, 200));
    for (const page of ['C01', 'G04', 'M01', 'M02', 'M03', 'M04', 'M05', 'G01']) {
      await navigate(page);
      await wait('!document.querySelector("[aria-busy=true]")', '窄屏页面加载');
      assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth && document.querySelector("main").scrollWidth<=document.querySelector("main").clientWidth+1'), true, `${page}窄屏无横向溢出`);
      if (page.startsWith('M')) {
        assert.equal(await evaluate('document.querySelectorAll("[data-chart],[data-candles],[data-news]").length'), 0, `${page}不使用演示图表与资讯`);
        assert.doesNotMatch(await evaluate('document.querySelector("main").innerText'), /3,862\.18|12,481\.36|成长占优|演示指数|资金示例|虚构示例/);
      }
    }
    await wait('!!document.querySelector("[data-market-chart]")', '最终日线视图');
    await screenshot('market-daily-small.png');
    // The quick entry edits the same real watchlist and appends to existing notes.
    const seeded = await evaluate("atradeDesktop.market('market.watchlist.save',{symbol:'sz000001',status:'planned',note:'原观察理由',tags:['重点']})");
    assert.equal(seeded.ok, true, seeded.error);
    await click('[data-market-watch="sz000001"]');
    await wait('document.querySelector("[data-watch-submit]")?.disabled === false', '跟踪快捷入口读取原记录');
    await fill('#market-watch-form textarea[name="reason"]', '回看日K后的补充观察');
    await click('[data-watch-submit]');
    await wait('!document.querySelector("#market-watch-form")', '追加观察理由成功');
    const watched = await evaluate("atradeDesktop.market('market.watchlist.list',{})");
    const observation = watched.data.items.find(item => item.symbol === 'sz000001');
    assert.equal(observation.note, '原观察理由\n\n回看日K后的补充观察');
    assert.equal(observation.status, 'planned');
    assert.deepEqual(observation.tags, ['重点']);
    await navigate('M04');
    await wait('!!document.querySelector("[data-explorer-query]")', '回到可保存偏好的个股列表');
    await field('board', 'main');
    await field('size', '30');
    await click('[data-explorer-sort="changePercent"]');
    if (await evaluate('document.querySelector("[data-explorer-field=compact]").getAttribute("aria-pressed")==="true"')) await click('[data-explorer-field="compact"]');
    await click('[data-explorer-field="filtersOpen"]');
    assert.equal(await evaluate('document.querySelector(".market-explorer-filters").hidden'), true);
    assert.equal(await evaluate('document.querySelector("[data-explorer-query]").value'), '000001');
    assert.equal(await evaluate('AT.persist()'), true, '筛选偏好可写入真实桌面工作区');
    await screenshot('market-optimized-list.png');
    await navigate('G01');
    await wait('!!document.querySelector("[data-market-chart]")', '回到日K保存指标偏好');
    await click('[data-market-study="ma"]');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-study-line^=ma]").length'), 0);
    assert.equal(await evaluate('AT.state.marketPreferences.studies.ma'), false);
  } else {
    assert.equal(await evaluate('AT.state.page'), 'G01', '重启恢复页面');
    assert.equal(await evaluate('AT.state.stock'), 'sz000001', '重启恢复市场标识');
    await wait('!!document.querySelector("[data-market-chart]")', '重启读取缓存日线');
    assert.equal((await evaluate('AT.marketUI.status')).path, fixtureDirectory);
    assert.equal((await evaluate('AT.marketUI.status')).symbolCount, fixtureSymbolCount);
    const preferences = await evaluate('AT.state.marketPreferences');
    assert.equal(preferences.lists['stocks:stocks'].board, 'main');
    assert.equal(preferences.lists['stocks:stocks'].query, '000001');
    assert.equal(preferences.lists['stocks:stocks'].size, 30);
    assert.equal(preferences.lists['stocks:stocks'].sort, 'changePercent');
    assert.equal(preferences.lists['stocks:stocks'].compact, false);
    assert.equal(preferences.lists['stocks:stocks'].filtersOpen, false);
    assert.equal(preferences.studies.ma, false);
    assert.equal(await evaluate('document.querySelectorAll("[data-market-study-line^=ma]").length'), 0, '重启恢复均线开关');
    const watched = await evaluate("atradeDesktop.market('market.watchlist.list',{})");
    assert.equal(watched.data.items.find(item => item.symbol === 'sz000001').note, '原观察理由\n\n回看日K后的补充观察');
    await navigate('M04');
    await wait('document.querySelectorAll(".market-explorer-table [data-stock]").length===1', '重启自动恢复查询与市场筛选');
    assert.equal(await evaluate('document.querySelector(".market-explorer-table [data-stock]").dataset.stock'), 'sz000001');
    assert.equal(await evaluate('document.querySelector("[data-explorer-query]").value'), '000001');
    assert.equal(await evaluate('document.querySelector(".market-explorer-filters").hidden'), true);
    await navigate('G01');
    await wait('!!document.querySelector("[data-market-chart]")', '恢复原日线验收页面');
    const bars = await evaluate("atradeDesktop.market('market.bars',{symbol:'sz000001',limit:240})");
    assert.equal(bars.ok, true);
    assert.equal(bars.data.cached, true);
    assert.equal(bars.data.rows[0].close, 10.07);
    assert.equal(bars.data.code, '000001');
    assert.equal((await evaluate("atradeDesktop.market('account.save',{})")).ok, false, '行情桥接只开放行情方法');
    await evaluate("location.hash='#G01?stock=SH600000';true");
    await wait('AT.state.stock==="sh600000" && !!document.querySelector("[data-market-chart]")', '大写路由规范为小写');
    assert.equal(await evaluate('AT.persist()'), true, '规范路由仍可保存');
    await evaluate("AT.navigate('G01','000001');true");
    await wait('document.querySelector("main").innerText.includes("相同代码的不同市场")', '裸代码歧义不猜市场');
    assert.equal(await evaluate('document.querySelectorAll("[data-market-chart]").length'), 0);
  }

  assert.deepEqual(rendererErrors, [], '没有渲染或 preload 错误');
  assert.deepEqual(await evaluate('window.__marketSmokeErrors'), [], '没有未处理交互异常');
  assert.equal(await evaluate('AT.marketUI.flush()'), true);
  assert.equal(await evaluate('AT.persist()'), true, '真实证券路由不阻断全局保存');
  console.log(`PASS 行情桌面 ${phase} 阶段`);
  win.once('closed', () => app.exit(0));
  await click('[data-window-action="close"]');
}).catch(async error => {
  console.error(error.stack);
  if (win && !win.isDestroyed()) {
    try { await screenshot('market-smoke-failure.png'); } catch {}
    win.destroy();
  }
  app.exit(1);
});
