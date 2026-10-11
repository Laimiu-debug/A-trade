// Real Electron -> Python market service -> MCP handshake -> fixture TQ module.
// Only the provider implementation is simulated; preload and IPC stay intact.
const {app, protocol, dialog, session} = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const [directory, phase] = process.argv.slice(2);
if (!directory || !['write', 'read'].includes(phase)) throw new Error('Missing live market smoke arguments');
const root = path.resolve(__dirname, '..');
const dataDirectory = path.join(directory, 'workspace');
const fixtureDirectory = path.join(directory, '个人通达信');
const vendorDirectory = path.join(fixtureDirectory, 'PYPlugins/user');
const failedQuoteFile = path.join(vendorDirectory, 'quotes-offline');
const networkAttemptFile = path.join(directory, 'network-guard/attempted');
const rendererErrors = [], externalRequests = [];
const expectedTools = ['tdx_bars', 'tdx_quotes', 'tdx_sector_stocks', 'tdx_sectors', 'tdx_status'];
let win;

app.setPath('userData', path.join(directory, 'electron-profile'));
protocol.registerSchemesAsPrivileged([{scheme: 'atrade', privileges: {standard: true, secure: true, supportFetchAPI: true}}]);
dialog.showMessageBox = async (_window, options) => {throw new Error(`Unexpected modal: ${options.message}`);};
dialog.showOpenDialog = async () => {throw new Error('This test uses editable path fields, not a picker');};

const evaluate = code => win.webContents.executeJavaScript(code);
const wait = async (expression, label, timeout = 20000) => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  const content = await evaluate('document.querySelector("main")?.innerText.slice(-2200)');
  throw new Error(`${label} 超时: ${content || ''}`);
};
const click = selector => evaluate(`(() => {const el=[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length);if(!el||el.disabled)throw new Error('Missing or disabled: '+${JSON.stringify(selector)});el.click();})()`);
const action = name => click(`[data-market-action="${name}"]`);
const fill = (selector, value) => evaluate(`(() => {const el=document.querySelector(${JSON.stringify(selector)});if(!el)throw new Error('Missing input');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const setChecked = (selector, value) => evaluate(`(() => {const el=document.querySelector(${JSON.stringify(selector)});if(!el)throw new Error('Missing checkbox');el.checked=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
async function navigate(page) {
  await evaluate(`AT.navigate(${JSON.stringify(page)});true`);
  await wait(`AT.state.page===${JSON.stringify(page)}`, `打开 ${page}`);
}
async function sectorMouse(event) {
  win.webContents.sendInputEvent(event);
  await new Promise(resolve => setTimeout(resolve, 25));
  await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
}
async function dragSector(button) {
  await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
  await new Promise(resolve => setTimeout(resolve, 80));
  await evaluate('document.querySelector("[data-sector-history-chart]").scrollIntoView({block:"center",behavior:"instant"})');
  await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
  const before = await evaluate('document.querySelector(".sector-history-dates").innerText');
  const rect = await evaluate('(()=>{const r=document.querySelector("[data-sector-history-chart]").getBoundingClientRect();return {x:Math.round(r.left+r.width*.35),y:Math.round(r.top+r.height*.5),delta:Math.round(r.width*.12)};})()');
  const from={x:rect.x,y:rect.y},to={x:rect.x+rect.delta,y:rect.y};
  await sectorMouse({type:'mouseMove',...from});
  await sectorMouse({type:'mouseDown',...from,button,clickCount:1});
  try {
    await sectorMouse({type:'mouseMove',...to,modifiers:[`${button}ButtonDown`]});
    assert.equal(await evaluate('document.documentElement.classList.contains("sector-history-dragging")'),true,`${button}按住移动时间轴`);
  } finally {await sectorMouse({type:'mouseUp',...to,button,clickCount:1});}
  const after=await evaluate('document.querySelector(".sector-history-dates").innerText');
  assert.notEqual(after,before,'拖动日期窗口变化');
  assert.equal(await evaluate('document.documentElement.classList.contains("sector-history-dragging")'),false,'松开结束拖动');
  await sectorMouse({type:'mouseMove',...from});
  assert.equal(await evaluate('document.querySelector(".sector-history-dates").innerText'),after,'松开后不再移动窗口');
}
const comparisonRange = () => evaluate('(()=>{const c=document.querySelector("[data-sector-comparison-chart]");return {...c.dataset};})()');
const comparisonLines = () => evaluate('[...document.querySelectorAll("[data-sector-comparison-line]")].map(el=>el.dataset.sectorComparisonLine)');
async function dragComparison(button) {
  await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
  await evaluate('document.querySelector("[data-sector-comparison-chart]").scrollIntoView({block:"center",behavior:"instant"})');
  const before=await comparisonRange();
  const point=await evaluate('(()=>{const r=document.querySelector("[data-sector-comparison-chart]").getBoundingClientRect();return {x:Math.round(r.left+r.width*.35),y:Math.round(r.top+r.height*.5),distance:Math.round(r.width*.1)};})()');
  const from={x:point.x,y:point.y},to={x:point.x+point.distance,y:point.y};
  await sectorMouse({type:'mouseMove',...from});await sectorMouse({type:'mouseDown',...from,button,clickCount:1});
  try{await sectorMouse({type:'mouseMove',...to,modifiers:[`${button}ButtonDown`]});}
  finally{await sectorMouse({type:'mouseUp',...to,button,clickCount:1});}
  const after=await comparisonRange();
  assert.ok(Number(after.compareStart)<Number(before.compareStart),`${button}对比图拖动移至较早时间`);
  assert.equal(after.compareCount,before.compareCount);assert.equal(after.compareBase,before.compareBase,'拖动不重新计算涨幅基准');
  await sectorMouse({type:'mouseMove',...from});
  assert.equal((await comparisonRange()).compareStart,after.compareStart,'释放后不继续移动');
  await new Promise(resolve=>setTimeout(resolve,200));
}
async function screenshot(name) {
  await fs.mkdir(path.join(root, 'artifacts'), {recursive: true});
  await evaluate('document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getTiming().iterations))try{a.finish()}catch{}})');
  await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
  await new Promise(resolve => setTimeout(resolve, 150));
  const image = await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
  await fs.writeFile(path.join(root, 'artifacts', name), image.toPNG());
}

const vendorSource = `
from pathlib import Path
import json
import os
ROOT = Path(__file__).parent
def record(method, data=None):
    with (ROOT / 'calls.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'method':method,'data':data,'pid':os.getpid()})+'\\n')
class tq:
    _initialized = False
    @classmethod
    def initialize(cls, value):
        print('fixture vendor output must not corrupt MCP JSON')
        record('initialize')
        cls._initialized = True
    @classmethod
    def close(cls):
        record('close')
        cls._initialized = False
    @classmethod
    def get_market_snapshot(cls, stock_code):
        record('snapshot', stock_code)
        if (ROOT / 'quotes-offline').exists():
            return {}
        sector = stock_code.startswith('880')
        number = int(stock_code[:6]) - 880000 if sector else 0
        price = 100 + number if sector else {'000001.SH':3100.5,'399001.SZ':9800.25,'399006.SZ':2100.75,'899050.BJ':1500.5}.get(stock_code,10.5)
        return dict(Now=str(price),LastClose=str(100 if sector else price-0.5),Open=str(price-0.2),Max=str(price+0.5),Min=str(price-0.5),Volume='1000',Amount=str(number*10 if sector else 1.05),ErrorId='0')
    @classmethod
    def get_market_data(cls, stock_list, period, count, start_time, end_time, dividend_type, field_list, fill_data):
        record('bars', {'symbols':stock_list,'period':period,'start':start_time,'end':end_time})
        import pandas as pd
        day = start_time[:8] if start_time else '20260930'
        stamps = [day+'093100',day+'093200',day+'093300'] if period=='1m' else [day]
        if stock_list[0].startswith(('880','881')) and period=='1d':
            stamps = pd.bdate_range(end=day,periods=count).strftime('%Y%m%d').tolist()
        values = dict(Open=10,High=11,Low=9,Close=10.5,Volume=1000,Amount=1.05)
        sector_number = int(stock_list[0][:6])-880000 if stock_list[0].startswith(('880','881')) else 0
        return {field:pd.DataFrame({stock_list[0]:[values[field]+(sector_number*2+index*(0.01+sector_number*0.001) if field in ('Open','High','Low','Close') else index) for index in range(len(stamps))]},index=stamps) for field in field_list}
    @classmethod
    def get_sector_list(cls, list_type):
        record('sectors', list_type)
        return [{'Code':'880001.SH','Name':'Fixture Banks'},{'Code':'880002.SH','Name':'Fixture Technology'}] + [{'Code':str(880000+number)+'.SH','Name':'Fixture Sector '+str(number).zfill(2)} for number in range(3,31)]
    @classmethod
    def get_stock_list_in_sector(cls, block_code, block_type):
        record('sectorStocks', block_code)
        return ['600900.SH','000001.SZ']
`;

async function fixture() {
  const names = {sh: [['000001', 'SH INDEX'], ['600900', 'YANGTZE POWER']], sz: [['000001', 'SZ BANK'], ['399001', 'SZ INDEX'], ['399006', 'GROWTH INDEX']], bj: [['899050', 'BJ INDEX']]};
  await fs.mkdir(path.join(fixtureDirectory, 'T0002/hq_cache'), {recursive: true});
  for (const [market, entries] of Object.entries(names)) {
    await fs.mkdir(path.join(fixtureDirectory, 'vipdoc', market, 'lday'), {recursive: true});
    const tnf = Buffer.alloc(50 + entries.length * 360);
    for (let index = 0; index < entries.length; index++) {
      const [code, name] = entries[index];
      tnf.write(code, 50 + index * 360, 'ascii');
      tnf.write(name, 50 + index * 360 + 31, 'ascii');
      const bytes = Buffer.alloc(3 * 32);
      for (let day = 0; day < 3; day++) {
        const offset = day * 32;
        bytes.writeUInt32LE(20260928 + day, offset);
        for (const [at, value] of [[4, 1000 + day], [8, 1100 + day], [12, 900 + day], [16, 1050 + day]]) bytes.writeUInt32LE(value, offset + at);
        bytes.writeFloatLE(10500 + day, offset + 20);
        bytes.writeUInt32LE(1000 + day, offset + 24);
      }
      await fs.writeFile(path.join(fixtureDirectory, 'vipdoc', market, 'lday', `${market}${code}.day`), bytes);
    }
    await fs.writeFile(path.join(fixtureDirectory, 'T0002/hq_cache', `${market}s.tnf`), tnf);
  }
  await fs.mkdir(vendorDirectory, {recursive: true});
  await fs.writeFile(path.join(vendorDirectory, 'tqcenter.py'), vendorSource, 'utf8');
  await fs.writeFile(path.join(fixtureDirectory, 'PYPlugins/TPythClient.dll'), 'fixture - never loaded');
}

app.whenReady().then(async () => {
  if (phase === 'write') await fixture();
  // A test guard blocks and records any accidental public-network fallback.
  // It leaves the real private Python/MCP process boundary untouched.
  const guard = path.join(directory, 'network-guard');
  await fs.mkdir(guard, {recursive: true});
  await fs.writeFile(path.join(guard, 'sitecustomize.py'), `from pathlib import Path\nimport urllib.request\ndef blocked(*args, **kwargs):\n    Path(__file__).with_name('attempted').write_text('unexpected network')\n    raise RuntimeError('External network disabled in live market smoke')\nurllib.request.OpenerDirector.open = blocked\n`);
  process.env.PYTHONPATH = guard;
  session.defaultSession.webRequest.onBeforeRequest({urls: ['http://*/*', 'https://*/*']}, (details, callback) => {
    externalRequests.push(details.url); callback({cancel: true});
  });
  win = await require('../desktop/runtime.cjs').createDesktop({dataDirectory, show: false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', (_event, details) => {if (details.level === 'error') rendererErrors.push(details.message);});
    window.webContents.on('preload-error', (_event, _file, error) => rendererErrors.push(error.message));
  }});
  await evaluate(`window.__liveErrors=[];window.addEventListener('error',event=>__liveErrors.push(event.message));window.addEventListener('unhandledrejection',event=>__liveErrors.push(String(event.reason)));`);
  await wait('!!AT.marketUI', '行情模块启动');

  if (phase === 'write') {
    await navigate('C01');
    await wait('!!document.querySelector("[data-market-path]") && !document.querySelector("[data-market-action=configure]").disabled', '设置就绪');
    assert.equal(await evaluate('document.querySelector("#market-online-form [name=enabled]").checked'), false);
    await fill('[data-market-path]', fixtureDirectory);
    await action('configure');
    await wait('AT.marketUI.status?.symbolCount===6 && !document.querySelector("[data-market-action=configure]").disabled', '读取测试日线');
    await setChecked('#market-online-form [name=followLocal]', false);
    await fill('#market-online-form [name=root]', fixtureDirectory);
    await fill('#market-online-form [name=refreshSeconds]', '300');
    await setChecked('#market-online-form [name=enabled]', true);
    await action('online-test');
    await wait('AT.marketUI.status?.online?.connected && !document.querySelector("[data-market-action=online-test]").disabled', '真实MCP检测');
    const status = (await evaluate("atradeDesktop.market('market.online.status')")).data;
    assert.equal(status.enabled, true);
    assert.equal(status.root, fixtureDirectory);
    assert.equal(status.refreshSeconds, 300);
    assert.deepEqual(status.tools.sort(), expectedTools);
    await screenshot('market-live-settings.png');

    await navigate('M01');
    await wait('document.querySelector(".market-overview-panel").innerText.includes("3,100.50") && !document.querySelector("[data-market-action=refresh-quotes]").disabled', 'MCP指数快照');
    const overview = await evaluate('document.querySelector(".market-overview-panel").innerText');
    assert.match(overview, /行情时间未提供/);
    assert.match(overview, /9,800\.25/);
    assert.doesNotMatch(overview, /3,862\.18|12,481\.36|成长占优/);
    await wait('document.querySelector(".market-analysis-style")?.innerText.includes("2026-09-30")', '风格使用日线统一日期');
    assert.match(await evaluate('document.querySelector(".market-analysis-style").innerText'), /数据不足/);
    await screenshot('market-live-overview.png');

    await navigate('M02');
    await wait('!!document.querySelector("[data-market-sector=sh880001]")', 'MCP板块目录');
    assert.match(await evaluate('document.querySelector("main").innerText'), /Fixture Banks/);
    await wait('!document.querySelector(".market-explorer-rule").innerText.includes("读取更新") && document.querySelector(".market-explorer-summary").innerText.includes("有报价 30")', '全目录分批读取30板块报价');
    assert.equal(await evaluate('document.querySelectorAll(".market-explorer-table [data-market-sector]").length'), 15);
    await wait('document.querySelectorAll("[data-sector-comparison-line]").length===5','全目录涨幅前5板块同图');
    assert.deepEqual(await comparisonLines(),['sh880030','sh880029','sh880028','sh880027','sh880026'],'头部选取覆盖当前分页之外的板块');
    const compareInitial=await comparisonRange();
    assert.equal(Number(compareInitial.compareTotal),60);
    assert.equal(new Set(await evaluate('[...document.querySelectorAll("[data-sector-comparison-line]")].map(el=>el.getAttribute("stroke")||getComputedStyle(el).stroke)')).size,5,'对比曲线用不同颜色');
    await evaluate(`(()=>{const chart=document.querySelector('[data-sector-comparison-chart]'),r=chart.getBoundingClientRect();chart.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-120,clientX:r.left+r.width*.5}));})()`);
    const compareZoomed=await comparisonRange();
    assert.ok(Number(compareZoomed.compareCount)<60);assert.equal(compareZoomed.compareBase,compareInitial.compareBase,'缩放保持共同基准');
    await dragComparison('left');
    await click('[data-sector-comparison-reset]');
    await evaluate(`(()=>{const chart=document.querySelector('[data-sector-comparison-chart]'),r=chart.getBoundingClientRect();chart.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-120,clientX:r.left+r.width*.1}));})()`);
    await dragComparison('right');
    await new Promise(resolve=>setTimeout(resolve,200));
    await click('[data-sector-comparison-reset]');
    assert.equal(Number((await comparisonRange()).compareCount),60);
    await click('[data-sector-comparison-period="250"]');
    assert.equal(Number((await comparisonRange()).compareTotal),250);
    assert.doesNotMatch(await evaluate('document.querySelector("[data-sector-comparison-chart]").outerHTML'),/NaN|undefined/);
    await screenshot('market-live-sector-comparison.png');
    await click('[data-explorer-sort="changePercent"]');
    assert.equal(await evaluate('document.querySelector(".market-explorer-table [data-market-sector]").dataset.marketSector'), 'sh880001', '首次点击按涨跌幅正序');
    await click('[data-explorer-sort="changePercent"]');
    assert.equal(await evaluate('document.querySelector(".market-explorer-table [data-market-sector]").dataset.marketSector'), 'sh880030', '排序包含24条批次以外的板块');
    await click('[data-explorer-sort="changePercent"]');
    assert.equal(await evaluate('document.querySelector(".market-explorer-table [data-market-sector]").dataset.marketSector'), 'sh880001', '第三次点击恢复默认名称顺序');
    await click('[data-explorer-sort="changePercent"]');
    await click('[data-explorer-sort="changePercent"]');
    await fill('[data-explorer-query]', 'Sector 29');
    await wait('document.querySelectorAll(".market-explorer-table [data-market-sector]").length===1', '板块跨页搜索');
    assert.equal(await evaluate('document.querySelector("[data-market-sector]").dataset.marketSector'), 'sh880029');
    await fill('[data-explorer-query]', 'Banks');
    await wait('!!document.querySelector("[data-market-sector=sh880001]")', '筛选真实板块');
    assert.equal((await comparisonLines()).length,5,'搜索列表不改变图中板块');
    await click('[data-sector-compare-toggle="sh880001"]');
    await wait('document.querySelectorAll("[data-sector-comparison-line]").length===6','手动加入板块同图');
    assert.equal(await evaluate('document.querySelector("[data-explorer-query]").value'),'Banks');
    await click('[data-sector-compare-remove="sh880001"]');
    assert.equal((await comparisonLines()).length,5);
    await click('[data-sector-comparison-top="3"]');
    await wait('document.querySelectorAll("[data-sector-comparison-line]").length===3','切换前3');
    assert.deepEqual(await comparisonLines(),['sh880030','sh880029','sh880028'],'列表搜索不影响全目录头部选取');
    await click('[data-sector-comparison-top="5"]');
    await wait('document.querySelectorAll("[data-sector-comparison-line]").length===5','恢复前5');
    await wait('!!document.querySelector("[data-sector-trend=sh880001] svg path")', '列表读取真实板块指数缩略图');
    await click('[data-sector-trend=sh880001]');
    await wait('!!document.querySelector("[data-sector-history-chart]")', '页内板块历史图');
    assert.match(await evaluate('document.querySelector("[data-sector-history-panel]").innerText'), /Fixture Banks[\s\S]*60 \/ 250 个交易日/);
    assert.equal(await evaluate('AT.state.page'), 'M02', '查看走势保留板块页');
    assert.equal(await evaluate('document.querySelector("[data-explorer-query]").value'), 'Banks', '查看走势保留筛选');
    await click('[data-sector-history-count="30"]');
    assert.match(await evaluate('document.querySelector("[data-sector-history-panel] footer").innerText'), /30 \/ 250/);
    await dragSector('left');
    await dragSector('right');
    assert.match(await evaluate('document.querySelector("[data-sector-history-panel] footer").innerText'), /30 \/ 250/, '拖动保持缩放跨度');
    await evaluate('window.dispatchEvent(new Event("blur"));true');
    await new Promise(resolve => setTimeout(resolve, 200)); // Let the release-click guard expire.
    await click('[data-sector-history-reset]');
    assert.match(await evaluate('document.querySelector("[data-sector-history-panel] footer").innerText'), /250 \/ 250/);
    await evaluate(`(()=>{const chart=document.querySelector('[data-sector-history-chart]'),r=chart.getBoundingClientRect();chart.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-120,clientX:r.left+r.width*.5}));})()`);
    assert.match(await evaluate('document.querySelector("[data-sector-history-panel] footer").innerText'), /200 \/ 250/);
    assert.doesNotMatch(await evaluate('document.querySelector("[data-sector-history-chart]").outerHTML'), /NaN|undefined/);
    await screenshot('market-live-sector-history.png');
    await click('[data-sector-history-close]');
    assert.equal(await evaluate('document.querySelector("[data-sector-history-panel]")'), null);
    await screenshot('market-live-sectors.png');
    await click('[data-market-sector=sh880001]');
    await wait('AT.state.page==="M03" && document.querySelectorAll(".market-explorer-table [data-stock]").length===2', 'MCP板块成分');
    assert.match(await evaluate('document.querySelector("main").innerText'), /YANGTZE POWER|600900/);
    await screenshot('market-live-sector.png');
    await click('[data-stock=sh600900]');
    await wait('document.querySelectorAll("[data-market-chart] [data-market-index]").length===3', '真实日K');
    assert.equal(await evaluate('document.querySelector("[data-market-study-line=ma20]").getAttribute("d").trim()'), '', '3条日线不足MA20不造均线');
    assert.equal(await evaluate('[...document.querySelectorAll("[data-market-indicator-chart] .market-analysis-line")].every(path=>!path.getAttribute("d").includes("L"))'), true, '主散量能第三根刚完成初始化，不向前两根伪造连线');
    await screenshot('market-live-daily.png');
    await evaluate('document.querySelector("[data-market-index=\\"2\\"]").dispatchEvent(new MouseEvent("dblclick",{bubbles:true}))');
    await wait('document.querySelectorAll("[data-market-minute-index]").length===3', '双击日K取得MCP真实分钟');
    assert.equal(await evaluate('document.querySelector("[data-market-intraday-date]").value'), '2026-09-30');
    assert.match(await evaluate('document.querySelector(".market-chart-source").innerText'), /通达信 MCP/);
    assert.match(await evaluate('document.querySelector("[data-market-minute-readout]").innerText'), /09:33/);
    assert.equal(await evaluate('document.querySelectorAll(".overlay,.day-drawer").length'), 0);
    await screenshot('market-live-intraday.png');
    await click('[data-market-mode=minute]');
    await wait('document.querySelectorAll("[data-market-minute-candle]").length===3', '分钟K绘图');
    assert.doesNotMatch(await evaluate('document.querySelector("[data-market-intraday-chart]").outerHTML'), /NaN|undefined/);
    await screenshot('market-live-minute-k.png');

    await wait('document.querySelector(".market-quote-strip").innerText.includes("10.50") && !document.querySelector("[data-market-action=refresh-quotes]").disabled', '报价已写缓存');
    await fs.writeFile(failedQuoteFile, 'simulate provider empty response');
    await action('refresh-quotes');
    await wait('!!document.querySelector(".market-quote-strip .market-cache-label") && !document.querySelector("[data-market-action=refresh-quotes]").disabled', '失败报价显示旧缓存');
    assert.match(await evaluate('document.querySelector(".market-quote-strip").innerText'), /旧缓存 · 上次采集/);
    assert.match(await evaluate('document.querySelector(".market-quote-strip").innerText'), /10\.50/);
    await screenshot('market-live-cached.png');
    await navigate('M02');
    await fill('[data-explorer-query]', '');
    await wait('document.querySelectorAll(".market-explorer-table [data-market-sector]").length===15', '返回板块排序');
    const beforeFailure = await evaluate('[...document.querySelectorAll(".market-explorer-table [data-market-sector]")].map(el=>el.dataset.marketSector)');
    assert.equal(beforeFailure[0], 'sh880030');
    await action('refresh-sectors');
    await wait('document.querySelector(".market-explorer-warnings")?.innerText.includes("暂停后续读取") && !document.querySelector("[data-market-action=refresh-sectors]").disabled', '失败报价批次停止并说明缺口');
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".market-explorer-table [data-market-sector]")].map(el=>el.dataset.marketSector)'), beforeFailure, '报价失败保留完整已知板块排序，包括后续未读取批次');
    assert.equal(await evaluate('document.querySelectorAll(".market-explorer-table .market-explorer-old").length'), 15, '失败保留的报价明确标记旧缓存');
    await wait('document.querySelectorAll(".sector-trend-row svg").length===15', '可见板块历史队列完成');
    const stoppedCalls = (await fs.readFile(path.join(vendorDirectory, 'calls.jsonl'), 'utf8')).trim().split('\n').length;
    await new Promise(resolve => setTimeout(resolve, 650));
    assert.equal((await fs.readFile(path.join(vendorDirectory, 'calls.jsonl'), 'utf8')).trim().split('\n').length, stoppedCalls, '失败后不会渲染触发自动重试循环');
    await screenshot('market-live-sector-cached.png');
    await evaluate("AT.navigate('G01','sh600900');true");
    await wait('!!document.querySelector("[data-market-mode=daily]")', '返回个股保留最终选择');
    await click('[data-market-mode=daily]');
    await wait('!!document.querySelector("[data-market-chart]")', '保存最终日K页面');
  } else {
    assert.equal(await evaluate('AT.state.page'), 'G01');
    assert.equal(await evaluate('AT.state.stock'), 'sh600900');
    await wait('!!document.querySelector("[data-market-chart]") && !!document.querySelector(".market-cache-label")', '重启恢复日K和报价缓存');
    await navigate('C01');
    await wait('!!document.querySelector("#market-online-form") && !document.querySelector("[data-market-action=online-test]").disabled', '重启设置');
    assert.equal(await evaluate('document.querySelector("#market-online-form [name=enabled]").checked'), true);
    assert.equal(await evaluate('document.querySelector("#market-online-form [name=followLocal]").checked'), false, '重启保留独立MCP目录选择');
    assert.equal(await evaluate('document.querySelector("#market-online-form [name=root]").value'), fixtureDirectory);
    assert.equal(await evaluate('document.querySelector("#market-online-form [name=refreshSeconds]").value'), '300');
    await action('online-test');
    await wait('AT.marketUI.status?.online?.connected && !document.querySelector("[data-market-action=online-test]").disabled', '重启MCP检测');
    const status = (await evaluate("atradeDesktop.market('market.online.status')")).data;
    assert.deepEqual(status.tools.sort(), expectedTools);
    await fs.unlink(failedQuoteFile);
    await navigate('M01');
    await wait('document.querySelector(".market-overview-panel").innerText.includes("3,100.50") && !document.querySelector("[data-market-action=refresh-quotes]").disabled', '恢复最新报价');
    assert.equal(await evaluate('document.querySelectorAll(".market-overview-panel:first-of-type .market-cache-label").length'), 0);
    await screenshot('market-live-restarted.png');
  }
  const log = (await fs.readFile(path.join(vendorDirectory, 'calls.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  for (const method of ['initialize', 'snapshot', 'sectors', 'sectorStocks', 'bars']) assert.ok(log.some(item => item.method === method), `${method} reached actual MCP worker`);
  assert.ok(log.some(item => item.method === 'bars' && item.data.period === '1m' && item.data.start === '20260930'));
  if (phase === 'read') assert.ok(new Set(log.filter(item => item.method === 'initialize').map(item => item.pid)).size >= 2, 'restart creates a new MCP process');
  assert.deepEqual(externalRequests, [], 'renderer made no external requests');
  assert.equal(await fs.access(networkAttemptFile).then(() => true, () => false), false, 'Python never attempted public-network fallback');
  assert.deepEqual(rendererErrors, [], 'renderer/preload errors');
  assert.deepEqual(await evaluate('window.__liveErrors'), [], 'unhandled renderer errors');
  assert.equal(await evaluate('AT.marketUI.flush()'), true);
  assert.equal(await evaluate('AT.persist()'), true);
  console.log(`PASS MCP 行情桌面 ${phase} 阶段`);
  win.once('closed', () => app.exit(0));
  await click('[data-window-action=close]');
}).catch(async error => {
  console.error(error.stack);
  if (win && !win.isDestroyed()) {
    try {await screenshot('market-live-failure.png');} catch {}
    win.destroy();
  }
  app.exit(1);
});
