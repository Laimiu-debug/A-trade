const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const listeners = new Map(), windowListeners = new Map(), timers = [], calls = [], captures = new Set(), rowButtons = new Map();
  let panelHtml = '', labelHtml = '', renderCount = 0;
  const line = {hidden: true, style: {}};
  const chart = {focus() {}, matches: selector => selector === '[data-sector-history-chart]', getBoundingClientRect: () => ({left: 0, width: 1000}), querySelector: () => line};
  const panel = {set outerHTML(html) {panelHtml = html;}, scrollIntoView() {}};
  const AT = {isDesktop:true, state:{page:'M02'}, escape:value=>String(value ?? '').replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c])), render() {renderCount++;panelHtml = AT.sectorTrends.panel();}};
  const document = {documentElement:{classList:{add() {}, remove() {}},setPointerCapture:id=>captures.add(id),releasePointerCapture:id=>captures.delete(id),hasPointerCapture:id=>captures.has(id)}, addEventListener(type, handler) {listeners.set(type, handler);}, querySelectorAll() {return [...rowButtons.values()];}, querySelector(selector) {return selector === '[data-sector-history-panel]' ? panel : selector === '[data-sector-history-chart]' ? chart : selector === '[data-sector-history-reading]' ? {set innerHTML(value) {labelHtml=value;}} : null;}};
  const pending = [];
  const window = {addEventListener(type, handler) {windowListeners.set(type,handler);}, atradeDesktop:{market(method, payload) {calls.push({method,payload});return new Promise(resolve=>pending.push(resolve));}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/sector-trends.js'),'utf8'), {AT, document, window, setTimeout:callback=>timers.push(callback)});
  const rows = Array.from({length:250}, (_, i) => ({date:new Date(Date.UTC(2025,0,1+i)).toISOString().slice(0,10),open:100+i,high:102+i,low:99+i,close:101+i}));
  function flush() {let guard=100;while (timers.length && guard--) timers.shift()();assert.ok(guard > 0);}
  function click(dataset, attribute='') {const button = {dataset,hasAttribute:name=>name === attribute};listeners.get('click')({target:{closest:()=>button}});}
  function event(type, properties={}) {let prevented=false;listeners.get(type)({target:{closest:()=>chart},pointerId:1,clientX:0,clientY:10,buttons:1,preventDefault(){prevented=true;},...properties});return prevented;}
  async function resolve(data) {pending.shift()({ok:true,data:data || {rows, dataAsOf:rows.at(-1).date,sourceName:'通达信板块指数日线',warnings:[]}});await new Promise(resolve=>setImmediate(resolve));}
  return {AT,rows,flush,click,event,resolve,calls,chart,line,document,captures,rowButtons,windowEvent:type=>windowListeners.get(type)(),renderCount:()=>renderCount,html:()=>panelHtml,label:()=>labelHtml};
}

test('sector history loads visible rows only, keeps selection inline, and supports zoom, both drag buttons and reset', async () => {
  const ui=fixture();
  ui.AT.sectorTrends.setVisible([{id:'sh881376',name:'数字媒体'}]);ui.flush();
  assert.equal(ui.calls.length,1);
  assert.equal(ui.calls[0].payload.id,'sh881376');
  await ui.resolve();ui.flush();
  assert.match(ui.AT.sectorTrends.row({id:'sh881376',name:'数字媒体'}), /<svg/);
  ui.click({sectorTrend:'sh881376'});ui.flush();
  assert.match(ui.html(), /当前 60 \/ 250/);
  assert.equal(ui.event('wheel',{deltaY:-100,clientX:500,ctrlKey:false}),true);
  assert.match(ui.html(), /当前 48 \/ 250/);
  ui.event('pointermove',{clientX:0});
  const date = /2025-\d\d-\d\d/.exec(ui.label())[0];
  ui.event('pointerdown',{button:0,clientX:100});ui.event('pointermove',{clientX:900});ui.event('pointerup');
  ui.event('pointermove',{clientX:0});
  assert.ok(/2025-\d\d-\d\d/.exec(ui.label())[0] < date);
  const afterLeft = ui.label();
  ui.event('pointerdown',{button:2,clientX:100,buttons:2});ui.event('pointermove',{clientX:800,buttons:2});ui.event('pointerup');
  ui.event('pointermove',{clientX:0});
  assert.ok(ui.label() < afterLeft);
  assert.equal(ui.event('contextmenu'),true);
  await new Promise(resolve=>setTimeout(resolve,190));
  ui.click({},'data-sector-history-reset');
  assert.match(ui.html(), /当前 250 \/ 250/);
  ui.click({sectorHistoryCount:'30'});
  assert.match(ui.html(), /当前 30 \/ 250/);
  ui.click({},'data-sector-history-close');
  assert.equal(ui.html(),'');
});

test('drag tracks pointer identity, releases stable capture, and ignores movement after buttons are released', async () => {
  const ui=fixture();ui.AT.sectorTrends.setVisible([{id:'sh881376',name:'数字媒体'}]);ui.flush();await ui.resolve();ui.click({sectorTrend:'sh881376'});
  const original=ui.html();
  ui.event('pointerdown',{button:0,clientX:100,pointerId:7});
  ui.event('pointermove',{clientX:800,pointerId:8});
  assert.equal(ui.html(),original);assert.equal(ui.captures.size,0);
  ui.event('pointermove',{clientX:800,pointerId:7});
  assert.ok(ui.captures.has(7));assert.notEqual(ui.html(),original);
  const moved=ui.html();
  ui.event('pointerup',{pointerId:8});assert.ok(ui.captures.has(7));
  ui.event('pointermove',{clientX:950,pointerId:7,buttons:0});
  assert.equal(ui.captures.size,0);assert.equal(ui.html(),moved);
  for(const end of ['pointerup','pointercancel','lostpointercapture','blur','resize','page','source']) {
    ui.event('pointerdown',{button:2,buttons:2,clientX:50,pointerId:9});ui.event('pointermove',{clientX:400,buttons:2,pointerId:9});
    assert.ok(ui.captures.has(9),end);
    if(end==='blur'||end==='resize')ui.windowEvent(end);
    else if(end==='source')ui.AT.sectorTrends.invalidate();
    else if(end==='page') {ui.AT.state.page='G01';ui.AT.sectorTrends.endDrag(false);}
    else ui.event(end,{pointerId:9});
    assert.equal(ui.captures.size,0,end);
    if(end==='page') ui.AT.state.page='M02';
  }
});

test('background history updates only its row and preserves a dragging chart and focused input', async () => {
  const ui=fixture();ui.AT.sectorTrends.setVisible([{id:'sh881376',name:'数字媒体'},{id:'sh881106',name:'种植业'}]);ui.flush();await ui.resolve();ui.click({sectorTrend:'sh881376'});ui.flush();
  const row={dataset:{sectorTrend:'sh881106'},innerHTML:''};ui.rowButtons.set('sh881106',row);
  const input={matches:()=>false};ui.document.activeElement=input;
  ui.event('pointerdown',{button:0,clientX:100,pointerId:3});ui.event('pointermove',{clientX:700,pointerId:3});
  const before=ui.html(), renders=ui.renderCount();
  assert.equal(ui.AT.sectorTrends.deferRender(),true);
  await ui.resolve();ui.flush();
  assert.match(row.innerHTML,/<svg/);
  assert.equal(ui.html(),before);assert.equal(ui.renderCount(),renders);
  assert.equal(ui.document.activeElement,input);assert.ok(ui.captures.has(3));
  ui.event('pointerup',{pointerId:3});ui.flush();
  assert.equal(ui.captures.size,0);assert.equal(ui.renderCount(),renders+1);
  assert.equal(ui.AT.sectorTrends.deferRender(),false);
});

test('navigation stops the pending queue and source invalidation discards late old-source results', async () => {
  const ui=fixture();
  ui.AT.sectorTrends.setVisible([{id:'sh881376',name:'数字媒体'},{id:'sh881106',name:'种植业'}]);ui.flush();
  ui.AT.state.page='G01'; await ui.resolve();ui.flush();
  assert.equal(ui.calls.length,1);
  ui.AT.state.page='M02';ui.AT.sectorTrends.setVisible([{id:'sh881106',name:'种植业'}]);ui.flush();
  assert.equal(ui.calls.length,2);
  ui.AT.sectorTrends.invalidate();await ui.resolve();ui.flush();
  assert.doesNotMatch(ui.AT.sectorTrends.row({id:'sh881106',name:'种植业'}), /<svg/);
  assert.equal(ui.AT.sectorTrends.panel(),'');
});

test('empty history stays empty and surfaces provider warnings without a fabricated line', async () => {
  const ui=fixture();ui.AT.sectorTrends.setVisible([{id:'sh881376',name:'数字媒体'}]);ui.flush();
  await ui.resolve({rows:[],warnings:['终端没有此板块日线 <source>']});ui.flush();ui.click({sectorTrend:'sh881376'});
  assert.doesNotMatch(ui.html(), /<svg/);
  assert.match(ui.html(), /终端没有此板块日线 &lt;source&gt;/);
  assert.match(ui.html(), /暂无可展示/);
});
