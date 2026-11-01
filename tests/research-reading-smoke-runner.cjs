const {app, protocol, dialog, session} = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {BusinessService} = require('../desktop/business-service.cjs');

const directory = process.argv[2];
if (!directory) throw new Error('Missing isolated research reading directory');
const root = path.resolve(__dirname, '..');
const artifacts = path.join(root, 'artifacts');
const dataDirectory = path.join(directory, 'workspace');
app.setPath('userData', path.join(directory, 'profile'));
protocol.registerSchemesAsPrivileged([{scheme:'atrade', privileges:{standard:true, secure:true, supportFetchAPI:true}}]);
dialog.showMessageBox = async (_window, options) => {throw new Error(`Unexpected dialog: ${options.message}`);};

// UI fixtures only: exercise the real IPC, report renderer, settings controls,
// navigation and Chromium layout without running or claiming financial results.
// No source data, reports or preferences in the user's APPDATA are opened.
const metrics = {totalReturnPct:8.5, annualizedReturnPct:11.2, maxDrawdownPct:4.5, sharpe:1.1,
  winRatePct:55, closedTradeCount:20, turnoverPct:80, totalFees:1234.56, totalSlippage:234.56, costRatioPct:1.5};
const account = {metrics, curve:[], daily:[], warnings:['UI fixture · 仅供界面验收'], pagination:{path:'testing.equalWeight'}};
const config = {startDate:'2025-01-02', endDate:'2025-12-31'};
const task = (id, name, kind) => ({id, name, kind, config, status:'completed', progress:1, symbolCount:16,
  createdAt:'2026-10-15T00:00:00Z', updatedAt:'2026-10-15T00:00:00Z', selection:{mode:'explicit', symbols:['sh600000']}});
const readingTask = task('ui-fixture-reading-table', 'UI fixture · 研究表格阅读验收', 'comparison');
const heatTask = task('ui-fixture-reading-heatmap', 'UI fixture · 横向热图阅读验收', 'systemPlateau');
const axes = [{path:'topN', values:[1,2,3,4,5,6,7,8,9]}, {path:'maxHoldingDays', values:[5,10,15]}];
const heatmap = axes[1].values.flatMap((days, y) => axes[0].values.map((topN, x) => ({candidateId:`UI-${y + 1}-${x + 1}`,
  params:{topN, maxHoldingDays:days}, neighborhood:{meanNetReturnPct:x - y, size:4, stdNetReturnPct:1, positiveFraction:.5, passed:false}})));
const fixtures = new Map([
  [readingTask.id, {task:readingTask, result:{kind:'comparison', config, testing:{equalWeight:account},
    conclusion:'UI fixture · 仅供布局和阅读验收，不代表真实回测',
    rotationGate:{status:'skipped',conclusion:'UI fixture · 本次只验收界面，不执行轮动计算'}, persistence:{status:'skipped'}}}],
  [heatTask.id, {task:heatTask, result:{kind:'systemPlateau', config, experiment:{axes}, trialCount:27,
    conclusion:'UI fixture · 仅供横向热图验收，不代表真实回测', plateau:{heatmap, certified:false}, folds:[]}}],
]);
const request = BusinessService.prototype.request;
BusinessService.prototype.request = async function(method, payload = {}, ...rest) {
  if (method === 'research.get' && fixtures.has(payload.id)) return {ok:true, data:structuredClone(fixtures.get(payload.id))};
  const response = await request.call(this, method, payload, ...rest);
  if (response.ok && ['research.catalogue','research.list'].includes(method)) {
    response.data.tasks = [readingTask, heatTask].map(row => structuredClone(row));
  }
  return response;
};

let win;
const errors = [];
const evidence = {fixture:'UI fixtures only; no strategy or return validation', sizes:[], scroll:[]};
const evaluate = code => win.webContents.executeJavaScript(code).catch(error => {throw new Error(`${error.message} [${code.slice(0,160)}]`);});
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
async function navigate(page) {
  await evaluate(`AT.navigate(${JSON.stringify(page)});true`);
  await wait(`AT.state.page===${JSON.stringify(page)}&&document.querySelector('main').dataset.renderedPage===${JSON.stringify(page)}`, 'navigate ' + page);
  await wake();
}
async function openReport(id, selector) {
  await evaluate(`ResearchModule.openTask(${JSON.stringify(id)},'R04')`);
  await wait(`!!document.querySelector(${JSON.stringify(selector)})`, 'rendered report ' + id);
  await wake();
}
async function screenshot(name, selector) {
  if (selector) await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start',behavior:'instant'});window.scrollBy({top:-95,behavior:'instant'});true`);
  // Explicit screenshot watermark, outside layout, so UI fixtures cannot be
  // mistaken for an actual saved strategy report when the task title is offscreen.
  await evaluate(`(()=>{let label=document.getElementById('reading-ui-fixture-label');if(!label){label=document.createElement('div');label.id='reading-ui-fixture-label';label.textContent='UI fixture · 仅界面验收';label.style.cssText='position:fixed;right:24px;bottom:34px;z-index:9999;padding:3px 8px;border:1px solid #8a95a5;border-radius:4px;background:#fff;color:#374151;font:12px sans-serif;pointer-events:none';document.body.append(label);}return true;})()`);
  await wake();
  await fs.writeFile(path.join(artifacts, name), (await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true})).toPNG());
  await evaluate("document.getElementById('reading-ui-fixture-label')?.remove();true");
}
async function preferences(largeText, compact) {
  await navigate('C05');
  for (const [key, value] of Object.entries({largeText, compact})) {
    await evaluate(`(()=>{const input=document.querySelector('[data-pref="${key}"]');if(!input)throw Error('Missing reading preference');if(input.checked!==${value})input.click();})()`);
  }
  assert.equal(await evaluate('AT.state.largeText'), largeText);
  assert.equal(await evaluate('AT.state.compact'), compact);
  assert.equal(await evaluate('AT.persist()'), true, 'reading preferences save in the isolated workspace');
  await navigate('R04');
}
async function measure() {
  return evaluate(`(()=>{
    const table=document.querySelector('.rs-table'),td=table.querySelector('td'),th=table.querySelector('th'),small=table.querySelector('small');
    const labelRange=document.createRange();labelRange.selectNodeContents(td);
    const style=el=>getComputedStyle(el), number=(el,key)=>parseFloat(style(el)[key]);
    return {viewport:innerWidth,client:document.documentElement.clientWidth,width:document.documentElement.scrollWidth,
      tableFont:number(td,'fontSize'),headerFont:number(th,'fontSize'),captionFont:number(small,'fontSize'),
      buttonFont:number(document.querySelector('.rs-button'),'fontSize'),
      cellPadding:number(td,'paddingTop')+number(td,'paddingBottom'),headerPadding:number(th,'paddingTop')+number(th,'paddingBottom'),
      rowHeight:td.parentElement.getBoundingClientRect().height,tableWidth:table.getBoundingClientRect().width,
      labelWidth:td.getBoundingClientRect().width,labelLines:new Set([...labelRange.getClientRects()].map(rect=>Math.round(rect.top))).size,
      largeText:document.body.classList.contains('reading-large'),compact:document.body.classList.contains('dense')};
  })()`);
}
async function checkScroll(selector, name) {
  const position = await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.scrollIntoView({block:'center',behavior:'instant'});const max=el.scrollWidth-el.clientWidth;el.scrollLeft=Math.min(230,max);return {x:el.scrollLeft,max};})()`);
  assert.ok(position.x > 50, `${name} fixture has horizontal overflow: ${JSON.stringify(position)}`);
  await evaluate('AT.render();AT.render();true');
  await wake();
  let actual = await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollLeft`);
  assert.ok(Math.abs(actual - position.x) <= 1, `${name} retains horizontal position through consecutive renders (${position.x} -> ${actual})`);
  await navigate('C05');
  await evaluate('history.back();true');
  await wait(`AT.state.page==='R04'&&!!document.querySelector(${JSON.stringify(selector)})`, name + ' history return');
  await wake();
  actual = await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollLeft`);
  assert.ok(Math.abs(actual - position.x) <= 1, `${name} retains horizontal position after leaving and browser return (${position.x} -> ${actual})`);
  evidence.scroll.push({name, expected:position.x, actual, max:position.max});
}

app.whenReady().then(async () => {
  await fs.mkdir(artifacts, {recursive:true});
  const guardDirectory = path.join(directory, 'network-guard');
  await fs.mkdir(guardDirectory, {recursive:true});
  await fs.writeFile(path.join(guardDirectory, 'sitecustomize.py'), "import urllib.request\ndef blocked(self, request, *args, **kwargs):\n raise RuntimeError('Public network disabled in isolated reading smoke')\nurllib.request.OpenerDirector.open = blocked\n");
  process.env.PYTHONPATH = [guardDirectory, root].join(path.delimiter);
  session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']}, (_details, callback) => callback({cancel:true}));
  win = await require('../desktop/runtime.cjs').createDesktop({dataDirectory, show:false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', (_event, details) => {if (details.level === 'error') errors.push(details.message);});
    window.webContents.on('preload-error', (_event, _file, error) => errors.push(error.message));
  }});
  await evaluate('window.__readingErrors=[];window.addEventListener("error",event=>__readingErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__readingErrors.push(String(event.reason)));');
  assert.equal(app.getPath('userData'), path.join(directory, 'profile'));
  await openReport(readingTask.id, '.rs-table small');
  win.webContents.debugger.attach('1.3');
  for (const width of [820,1080,1440]) {
    await win.webContents.debugger.sendCommand('Emulation.setDeviceMetricsOverride', {width, height:900, deviceScaleFactor:1, mobile:false});
    await wake();
    const states = {};
    for (const [mode, largeText, compact] of [['normal',false,false],['large',true,false],['dense',false,true],['large-dense',true,true]]) {
      await preferences(largeText, compact);
      const state = await measure();
      states[mode] = state;
      evidence.sizes.push({requestedWidth:width,mode,...state});
      assert.equal(state.viewport,width);
      assert.ok(state.width <= state.client + 1, `report has no page overflow at ${width}px/${mode}: ${JSON.stringify(state)}`);
      assert.equal(state.largeText,largeText); assert.equal(state.compact,compact);
      assert.ok(state.labelWidth >= state.tableFont * 6, `strategy label retains readable width at ${width}px/${mode}`);
      assert.ok(state.labelLines <= 2 && state.rowHeight <= state.tableFont * 6, `strategy label does not become a vertical column at ${width}px/${mode}: ${JSON.stringify(state)}`);
      await screenshot(`research-reading-${width}-${mode}.png`, '.rs-table-wrap');
    }
    for (const key of ['tableFont','headerFont','captionFont','buttonFont']) {
      assert.ok(states.large[key] > states.normal[key], `large text increases ${key} at ${width}px`);
      assert.equal(states['large-dense'][key], states.large[key], `dense keeps the selected ${key} at ${width}px`);
    }
    assert.ok(states.normal.tableFont >= 14 && states.normal.captionFont >= 12, 'regular research text has a readable baseline');
    assert.ok(states.dense.cellPadding < states.normal.cellPadding, 'dense reduces table cell spacing');
    assert.ok(states.dense.headerPadding < states.normal.headerPadding, 'dense reduces table header spacing');
    assert.ok(states.dense.rowHeight < states.normal.rowHeight, 'dense increases visible row count without reducing the font');
  }
  await win.webContents.debugger.sendCommand('Emulation.setDeviceMetricsOverride', {width:820, height:900, deviceScaleFactor:1, mobile:false});
  await preferences(false,false);
  await checkScroll('.rs-table-wrap', 'research table');
  await screenshot('research-reading-table-restored.png', '.rs-table-wrap');
  await openReport(heatTask.id, '.pf-heat-scroll');
  await checkScroll('.pf-heat-scroll', 'parameter heatmap');
  await screenshot('research-reading-heatmap-restored.png', '.pf-heat-scroll');
  for (const width of [820,1080,1440]) {
    await win.webContents.debugger.sendCommand('Emulation.setDeviceMetricsOverride', {width, height:900, deviceScaleFactor:1, mobile:false});
    await wake();
    const state = await evaluate('({viewport:innerWidth,client:document.documentElement.clientWidth,width:document.documentElement.scrollWidth})');
    evidence.sizes.push({page:'heatmap',requestedWidth:width,...state});
    assert.ok(state.width <= state.client + 1, 'heatmap stays in its own scroll region at ' + width);
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(await evaluate('__readingErrors'), []);
  await fs.writeFile(path.join(artifacts, 'research-reading-evidence.json'), JSON.stringify(evidence, null, 2));
  await win.webContents.debugger.sendCommand('Emulation.clearDeviceMetricsOverride');
  win.webContents.debugger.detach();
  console.log('PASS UI fixture evidence:', JSON.stringify(evidence));
  win.close();setTimeout(() => app.exit(0), 300);
}).catch(async error => {
  console.error(error.stack);
  await fs.mkdir(artifacts, {recursive:true});
  await fs.writeFile(path.join(artifacts, 'research-reading-evidence.json'), JSON.stringify({...evidence, failure:error.message}, null, 2));
  if (win) win.destroy();
  app.exit(1);
});
