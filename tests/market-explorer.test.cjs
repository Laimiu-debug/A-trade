const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function explorer(savedState = {}) {
  const listeners = new Map();
  let renders = 0;
  const AT = {state:savedState,persist(){},escape: value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character])), icon: () => '', render() {renders++;}};
  const context = {AT, window: {AT}, document: {addEventListener(type, handler) {listeners.set(type, handler);}, querySelectorAll() {return [];}}, setTimeout, clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preview/market-preferences.js'), 'utf8'), context);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preview/market-explorer.js'), 'utf8'), context);
  function click(dataset, key = 'stocks:stocks', reset = false) {
    const panel = {dataset: {marketExplorer: key, explorerType: key.startsWith('stocks:') ? 'stocks' : 'sectors'}};
    const button = {dataset, closest: () => panel, hasAttribute: name => reset && name === 'data-explorer-reset'};
    listeners.get('click')({target: {closest: () => button}});
  }
  return {...AT.marketExplorer, click, document: context.document, listeners, state:AT.state, renders:()=>renders};
}
const codes = html => [...html.matchAll(/data-stock="([^"]+)"/g)].map(match => match[1]);
const sectors = html => [...html.matchAll(/data-market-sector="([^"]+)"/g)].map(match => match[1]);
const base = {asOf: '2026-10-09', items: [
  {symbol:'sh600001',name:'主板甲',code:'600001',board:'main',kind:'stock',date:'2026-10-09',close:12,amount:100,changePercent:2,volumeRatio5:1.7,aboveMA20:true},
  {symbol:'sz300001',name:'创业乙',code:'300001',board:'chinext',kind:'stock',date:'2026-10-09',close:20,amount:200,changePercent:-3,volumeRatio5:2,aboveMA20:false},
  {symbol:'sh688001',name:'科创丙',code:'688001',board:'star',kind:'stock',date:'2026-10-09',close:30,amount:null,changePercent:4,volumeRatio5:null,aboveMA20:true},
  {symbol:'sh000001',name:'指数',code:'000001',board:'index',kind:'index',date:'2026-10-09',close:3000,amount:2000,changePercent:6,volumeRatio5:2.5},
  {symbol:'sh600002',name:'较旧丁',code:'600002',board:'main',kind:'stock',date:'2026-09-30',close:40,amount:3000,changePercent:10,volumeRatio5:8},
  {symbol:'bj920001',name:'缺失戊',code:'920001',board:'bse',kind:'stock',date:null,close:10,amount:4000,changePercent:9,volumeRatio5:9}
]};

test('market explorer keeps old and missing dates separate and null values last in both orders', () => {
  const ui = explorer();
  assert.deepEqual(codes(ui.stockView(base)), ['sh000001', 'sz300001', 'sh600001', 'sh688001']);
  ui.click({explorerSort:'amount'});
  assert.deepEqual(codes(ui.stockView(base)), ['sh600001', 'sz300001', 'sh000001', 'sh688001']);
  ui.click({explorerField:'freshness', explorerValue:'older'});
  const html = ui.stockView(base);
  assert.deepEqual(codes(html), ['sh600002', 'bj920001']);
  assert.match(html, /2026-09-30/);
  assert.match(html, /日期缺失/);
});

test('market pools exclude indices and unknown or stale dates, and apply explicit criteria', () => {
  const ui = explorer();
  ui.stockView(base);
  ui.click({explorerField:'pool', explorerValue:'active'});
  assert.deepEqual(codes(ui.stockView(base)), ['sz300001', 'sh600001']);
  ui.click({explorerField:'pool', explorerValue:'gainers'});
  assert.deepEqual(codes(ui.stockView(base)), ['sh688001', 'sh600001']);
  ui.click({explorerField:'pool', explorerValue:'volume'});
  assert.deepEqual(codes(ui.stockView(base)), ['sz300001', 'sh600001']);
  assert.deepEqual(codes(ui.stockView({...base, asOf: null})), []);
});

test('market filtering composes board, direction, MA20, and volume while keeping pools limited to 50', () => {
  const ui = explorer();
  ui.stockView(base);
  ui.click({explorerField:'above', explorerValue:'true'});
  ui.click({explorerField:'volume', explorerValue:'true'});
  assert.deepEqual(codes(ui.stockView(base)), ['sh600001']);
  ui.click({explorerField:'board', explorerValue:'chinext'});
  assert.deepEqual(codes(ui.stockView(base)), []);
  ui.click({}, 'stocks:stocks', true);
  const many = {asOf:'2026-10-09',items:Array.from({length:80}, (_, i) => ({...base.items[0],symbol:`sh${600000 + i}`,amount:i + 1}))};
  ui.stockView(many);
  ui.click({explorerField:'pool', explorerValue:'active'});
  ui.click({explorerField:'size', explorerValue:'60'});
  assert.equal(codes(ui.stockView(many)).length, 50);
  assert.equal(codes(ui.stockView(many))[0], 'sh600079');
  assert.equal(codes(ui.stockView(many)).at(-1), 'sh600030');
});

test('sector rankings use actual fields and never infer trade dates or categories', () => {
  const ui = explorer();
  const payload = {loadingMetrics:true,progress:{done:2,total:3},items:[
    {id:'one',name:'板块甲',changePercent:2,amount:200,receivedAt:'2026-10-11T15:20:01+08:00',cached:true},
    {id:'two',name:'板块乙',changePercent:null,amount:null},
    {id:'three',name:'板块丙',changePercent:-2,amount:100}
  ]};
  ui.sectorView(payload);
  ui.click({explorerSort:'changePercent'},'sectors:all');
  let html = ui.sectorView(payload);
  assert.deepEqual(sectors(html), ['three','one','two']);
  assert.match(html, /行情时间未提供/);
  assert.match(html, /旧缓存/);
  assert.match(html, /报价读取 2 \/ 3/);
  assert.doesNotMatch(html, /data-explorer-field="category"/);
  ui.click({explorerSort:'changePercent'},'sectors:all');
  assert.deepEqual(sectors(ui.sectorView(payload)), ['one','three','two']);
  ui.click({explorerField:'direction',explorerValue:'down'},'sectors:all');
  assert.deepEqual(sectors(ui.sectorView(payload)), ['three']);
});

test('sorting cycles ascending, descending and the default without changing filters or pools', () => {
  const ui = explorer();
  ui.stockView(base);
  ui.click({explorerField:'pool', explorerValue:'gainers'});
  const defaults = codes(ui.stockView(base));
  ui.click({explorerSort:'changePercent'});
  assert.deepEqual(codes(ui.stockView(base)), ['sh600001','sh688001']);
  assert.match(ui.stockView(base), /切换倒序/);
  ui.click({explorerSort:'changePercent'});
  assert.deepEqual(codes(ui.stockView(base)), defaults);
  assert.match(ui.stockView(base), /恢复默认排序/);
  ui.click({explorerSort:'changePercent'});
  assert.deepEqual(codes(ui.stockView(base)), defaults);
  assert.doesNotMatch(ui.stockView(base), /data-explorer-sort-reset/);
  assert.match(ui.stockView(base), /data-explorer-value="gainers" aria-pressed="true"/);
  ui.click({explorerSort:'changePercent'});
  assert.deepEqual(codes(ui.stockView(base)), ['sh600001','sh688001']);
  const sectorData = {items:[{id:'a',name:'甲',changePercent:2},{id:'b',name:'乙',changePercent:1},{id:'c',name:'丙',changePercent:-1}]};
  ui.sectorView(sectorData);
  ui.click({explorerField:'direction',explorerValue:'up'},'sectors:all');
  const original = sectors(ui.sectorView(sectorData));
  for (let i = 0; i < 3; i++) ui.click({explorerSort:'changePercent'},'sectors:all');
  assert.deepEqual(sectors(ui.sectorView(sectorData)), original);
  assert.equal(sectors(ui.sectorView(sectorData)).length, 2);
});

test('member list filters are isolated per sector and source labels are escaped', () => {
  const ui = explorer();
  ui.stockView({...base,kind:'members',key:'sector-a'});
  ui.click({explorerField:'board',explorerValue:'star'}, 'stocks:sector-a');
  assert.deepEqual(codes(ui.stockView({...base,kind:'members',key:'sector-a'})), ['sh688001']);
  assert.equal(codes(ui.stockView({...base,kind:'members',key:'sector-b'})).length, 6);
  const html = ui.stockView({asOf:'2026-10-09',items:[{...base.items[0],name:'<img src=x onerror=alert(1)>'}],warnings:['<script>bad()</script>']});
  assert.doesNotMatch(html, /<img|<script>/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&lt;script&gt;/);
});

test('members include missing and old local records by default, but exclude both from hot pools', () => {
  const ui = explorer(), payload = {...base,kind:'members',key:'sector-a'};
  let html = ui.stockView(payload);
  assert.equal(codes(html).length, base.items.length);
  assert.match(html, /日期缺失/);
  assert.match(html, /2026-09-30/);
  ui.click({explorerField:'pool',explorerValue:'active'},'stocks:sector-a');
  assert.deepEqual(codes(ui.stockView(payload)), ['sz300001','sh600001']);
  ui.click({explorerField:'pool',explorerValue:'all'},'stocks:sector-a');
  assert.equal(codes(ui.stockView(payload)).length, base.items.length);
  ui.click({}, 'stocks:sector-a', true);
  assert.equal(codes(ui.stockView(payload)).length, base.items.length);
});

test('refresh failures remain visible alongside the previous stock and sector rows', () => {
  const ui = explorer();
  const stockHtml = ui.stockView({...base,error:'日线文件暂不可读'});
  assert.equal(codes(stockHtml).length, 4);
  assert.match(stockHtml, /role="alert"/);
  assert.match(stockHtml, /更新未完成 · 保留上次结果/);
  assert.match(stockHtml, /日线文件暂不可读/);
  const sectorHtml = ui.sectorView({items:[{id:'one',name:'板块甲'}],error:{message:'<MCP> 连接失败'}});
  assert.deepEqual(sectors(sectorHtml), ['one']);
  assert.match(sectorHtml, /role="alert"/);
  assert.match(sectorHtml, /&lt;MCP&gt; 连接失败/);
});

test('focus helpers restore search selection in the same list after an async rerender', () => {
  const ui = explorer();
  assert.equal(ui.captureFocus(), null);
  const oldPanel = {dataset:{marketExplorer:'sectors:all'}};
  ui.document.activeElement = {matches:selector=>selector==='[data-explorer-query]',closest:()=>oldPanel,selectionStart:1,selectionEnd:3,selectionDirection:'backward'};
  const snapshot = ui.captureFocus();
  assert.equal(snapshot.key, 'sectors:all');
  let focused = 0, selected = null;
  const newInput = {focus:options=>{assert.equal(options.preventScroll,true);focused++;},setSelectionRange:(...args)=>{selected=args;}};
  const newPanel = {dataset:{marketExplorer:'sectors:all'},querySelector:()=>newInput};
  ui.document.querySelectorAll = () => [newPanel];
  ui.restoreFocus(snapshot);
  assert.equal(focused,1);
  assert.deepEqual(selected,[1,3,'backward']);
  ui.document.querySelectorAll = () => [{...newPanel,dataset:{marketExplorer:'stocks:stocks'}}];
  ui.restoreFocus(snapshot);
  ui.restoreFocus(null);
  assert.equal(focused,1,'navigation away does not focus another list');
});

test('timestamps normalize equivalent timezone offsets without changing date-only or invalid values', () => {
  const ui = explorer(), utc = '2026-10-11T06:20:01Z', offset = '2026-10-11T14:20:01+08:00';
  const received = value => ui.sectorView({items:[{id:'one',name:'板块甲',receivedAt:value}],computedAt:value});
  assert.equal(received(utc), received(offset), 'same instant renders the same local time in any host timezone');
  assert.ok(received(utc).includes(new Date(utc).toLocaleString('zh-CN',{hour12:false})));
  const quoted = value => ui.sectorView({items:[{id:'one',name:'板块甲',asOf:value}]});
  assert.equal(quoted(utc), quoted(offset));
  assert.match(quoted('2026-10-09'), />2026-10-09<\/td>/);
  assert.match(quoted('未知时间 <source>'), /未知时间 &lt;source&gt;/);
});

test('saved list filters, sorting, page size and folded layout survive reopening', () => {
  const ui = explorer();
  ui.stockView(base);
  ui.click({explorerField:'board',explorerValue:'main'});
  ui.click({explorerField:'size',explorerValue:'30'});
  ui.click({explorerField:'filtersOpen'});
  ui.click({explorerField:'detailsOpen'});
  ui.click({explorerSort:'changePercent'});
  const reopened = explorer(JSON.parse(JSON.stringify(ui.state)));
  const html = reopened.stockView(base);
  assert.deepEqual(codes(html), ['sh600001']);
  assert.match(html, /data-explorer-field="filtersOpen" aria-expanded="true"/);
  assert.match(html, /market-explorer-details-open/);
  assert.equal(reopened.state.marketPreferences.lists['stocks:stocks'].size, 30);
  assert.equal(reopened.state.marketPreferences.lists['stocks:stocks'].sort, 'changePercent');
  assert.equal(reopened.state.marketPreferences.lists['stocks:stocks'].order, 'asc');
});

test('Chinese composition never applies partial search or rerenders before committed text', async () => {
  const ui = explorer(); ui.stockView(base);
  const panel = {dataset:{marketExplorer:'stocks:stocks',explorerType:'stocks'}};
  const input = {value:'zhong',isConnected:true,matches:()=>true,closest:()=>panel};
  ui.document.activeElement = input;
  ui.listeners.get('compositionstart')({target:input});
  ui.listeners.get('input')({target:input,isComposing:true});
  await new Promise(resolve=>setTimeout(resolve,200));
  assert.equal(ui.renders(),0);
  assert.equal(ui.state.marketPreferences,undefined);
  assert.equal(ui.isComposing(),true);
  input.value='中';
  ui.listeners.get('compositionend')({target:input,isComposing:false});
  await new Promise(resolve=>setTimeout(resolve,200));
  assert.equal(ui.renders(),1);
  assert.equal(ui.state.marketPreferences.lists['stocks:stocks'].query,'中');
  assert.equal(ui.isComposing(),false);
});

test('source warnings remain present when rule explanations are folded', () => {
  const ui = explorer();
  const html = ui.stockView({...base,warnings:['部分文件读取失败']});
  assert.match(html,/data-explorer-field="detailsOpen" aria-expanded="false"/);
  assert.match(html,/market-explorer-warnings" role="status">[\s\S]*部分文件读取失败/);
});
