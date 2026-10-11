const {test} = require('node:test');
const assert = require('node:assert/strict');
const {cleanRows, rankLeaders, calculate, linePath, displayWarnings} = require('../preview/sector-comparison.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function runtime() {
  const listeners = new Map(), timers = [], requests = [];
  const AT = {isDesktop:true,state:{page:'M02'},escape:value=>String(value??''),render(){}};
  const document = {activeElement:null,documentElement:{classList:{remove(){},add(){}},hasPointerCapture(){return false;}},addEventListener(name,handler){listeners.set(name,handler);},querySelector(){return null;},querySelectorAll(){return [];}};
  const window = {addEventListener(){},atradeDesktop:{market(method,payload){return new Promise(resolve=>requests.push({method,payload,resolve}));}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/sector-comparison.js'),'utf8'),{AT,document,window,setTimeout:handler=>timers.push(handler)});
  function runTimers(){while(timers.length) timers.shift()();}
  function click(dataset){const button={dataset,hasAttribute:name=>name==='data-sector-comparison-reset'&&dataset.reset};listeners.get('click')({target:{closest:()=>button},preventDefault(){}});runTimers();}
  async function resolve(index,data){requests[index].resolve({ok:true,data});await new Promise(setImmediate);runTimers();}
  return {ui:AT.sectorComparison,AT,requests,runTimers,click,resolve};
}
const chosen=html=>[...html.matchAll(/data-sector-compare-remove="([^"]+)"/g)].map(match=>match[1]);
const rows=Array.from({length:70},(_,i)=>({date:new Date(Date.UTC(2026,0,1+i)).toISOString().slice(0,10),close:100+i}));

test('sector leaders use all known positive changes with stable ties and no fabricated values', () => {
  const items = [{id:'b',changePercent:5},{id:'a',changePercent:5},{id:'c',changePercent:2},{id:'zero',changePercent:0},{id:'fall',changePercent:-3},{id:'missing',changePercent:null},{id:'string',changePercent:'9'},{id:'huge',changePercent:Infinity}];
  assert.deepEqual(rankLeaders(items, 3).map(item => item.id), ['a','b','c']);
  assert.equal(rankLeaders(items, 8).length, 3);
  assert.deepEqual(rankLeaders([{id:'a',changePercent:1},{id:'a',changePercent:2}], 5).map(item => item.changePercent), [2]);
});

test('comparison normalizes different index levels using a shared baseline and leaves missing dates null', () => {
  const result = calculate([
    {id:'a',rows:[{date:'2026-01-01',close:100},{date:'2026-01-02',close:110},{date:'2026-01-03',close:121},{date:'2026-01-04',close:99}]},
    {id:'b',rows:[{date:'2026-01-02',close:2000},{date:'2026-01-04',close:2200}]}
  ], 4);
  assert.equal(result.baseDate, '2026-01-02');
  assert.deepEqual(result.dates, ['2026-01-02','2026-01-03','2026-01-04']);
  assert.equal(result.series[0].values[0], 0);
  assert.ok(Math.abs(result.series[0].values[1] - 10) < 1e-9);
  assert.equal(result.series[1].values[1], null);
  assert.ok(Math.abs(result.series[1].values[2] - 10) < 1e-9);
  assert.match(linePath([0,null,10],100,100,0,10), /^M0\.00,100\.00 M100\.00,0\.00$/);
});

test('period chooses the common date inside its time window; a retained baseline survives refresh', () => {
  const rows = Array.from({length:6},(_,i)=>({date:`2026-01-0${i+1}`,close:100+i}));
  const result = calculate([{id:'a',rows},{id:'b',rows}],3);
  assert.equal(result.baseDate,'2026-01-04');
  assert.equal(result.dates.length,3);
  const refreshed = calculate([{id:'a',rows:[...rows,{date:'2026-01-07',close:107}]},{id:'b',rows:[...rows,{date:'2026-01-07',close:207}]}],3,result.baseDate);
  assert.equal(refreshed.baseDate,'2026-01-04');
  assert.equal(refreshed.dates.length,4);
});

test('non-overlapping histories have no false comparison and short/invalid series are excluded', () => {
  const result=calculate([{id:'a',rows:[{date:'2026-01-01',close:10},{date:'2026-01-02',close:11}]},{id:'b',rows:[{date:'2026-02-01',close:20},{date:'2026-02-02',close:21}]}],60);
  assert.equal(result.reason,'no-common-date'); assert.deepEqual(result.dates,[]);
  assert.deepEqual(cleanRows([{date:'bad',close:1},{date:'2026-01-01',close:0},{date:'2026-01-02',close:NaN},{date:'2026-01-03',close:2},{date:'2026-01-03',close:3}]),[{date:'2026-01-03',close:3}]);
});

test('initial selection waits for the complete catalogue quote pass and does not change on filtering or quote updates', async()=>{
  const rt=runtime(), items=Array.from({length:9},(_,i)=>({id:`s${i}`,name:`板块${i}`,changePercent:i+1}));
  assert.deepEqual(chosen(rt.ui.panel({items:[],busy:false,error:'尚未启用'})),[]);
  rt.ui.panel({items,progress:{done:0,total:9},busy:false,loadingMetrics:false});rt.runTimers();assert.equal(rt.requests.length,0);
  rt.ui.panel({items,progress:{done:3,total:9},loadingMetrics:true});rt.runTimers();assert.equal(rt.requests.length,0);
  assert.deepEqual(chosen(rt.ui.panel({items,progress:{done:9,total:9}})),['s8','s7','s6','s5','s4']);
  rt.runTimers();assert.equal(rt.requests.length,1);
  await rt.resolve(0,{rows});
  assert.doesNotMatch(rt.ui.panel(),/data-sector-comparison-chart/,'do not compare partially loaded series with a moving baseline');
  for(let i=1;i<5;i++) await rt.resolve(i,{rows});
  assert.match(rt.ui.panel(),/data-compare-total="60"/);
  assert.equal((rt.ui.panel().match(/data-sector-comparison-line=/g)||[]).length,5);
  assert.deepEqual(chosen(rt.ui.panel({items:items.map(item=>({...item,changePercent:10-item.changePercent})),progress:{done:9,total:9}})),['s8','s7','s6','s5','s4']);
  rt.click({sectorComparisonTop:'3'});
  assert.deepEqual(chosen(rt.ui.panel()),['s0','s1','s2']);
});

test('selection stays within eight and manually removed entries stay removed',()=>{
  const rt=runtime(), items=Array.from({length:9},(_,i)=>({id:`s${i}`,changePercent:i+1}));
  rt.ui.panel({items,progress:{done:9,total:9}});
  rt.click({sectorComparisonTop:'8'});
  rt.click({sectorCompareToggle:'s0'});
  assert.equal(chosen(rt.ui.panel()).length,8);
  assert.match(rt.ui.panel(),/最多同时对比 8 个板块/);
  rt.click({sectorCompareRemove:'s8'});
  assert.equal(chosen(rt.ui.panel({items,progress:{done:9,total:9}})).length,7);
  rt.click({sectorCompareToggle:'s0'});
  assert.ok(chosen(rt.ui.panel()).includes('s0'));
});

test('leaving the page stops the sequential queue and changing source discards in-flight responses',async()=>{
  const rt=runtime();
  rt.ui.panel({items:[{id:'old-a',changePercent:2},{id:'old-b',changePercent:1}],progress:{done:2,total:2}});rt.runTimers();
  assert.equal(rt.requests.length,1);
  rt.AT.state.page='M01';await rt.resolve(0,{rows});assert.equal(rt.requests.length,1);
  rt.ui.invalidate();rt.AT.state.page='M02';
  rt.ui.panel({items:[{id:'new-a',changePercent:1}],progress:{done:1,total:1}});rt.runTimers();
  assert.equal(rt.requests[1].payload.id,'new-a');
  rt.ui.invalidate();
  rt.ui.panel({items:[{id:'last-a',changePercent:1}],progress:{done:1,total:1}});rt.runTimers();
  assert.equal(rt.requests.length,2,'do not start overlapping requests');
  await rt.resolve(1,{rows});
  assert.equal(rt.requests[2].payload.id,'last-a');
  assert.doesNotMatch(rt.ui.panel(),/data-sector-comparison-chart/,'the response from the previous source was ignored');
  await rt.resolve(2,{rows});
  assert.deepEqual(chosen(rt.ui.panel()),['last-a']);
  assert.match(rt.ui.panel(),/data-sector-comparison-line="last-a"/);
});

test('manual refresh bypasses the history cache once per selected sector while retaining selection',async()=>{
  const rt=runtime();
  rt.ui.panel({items:[{id:'a',changePercent:2},{id:'b',changePercent:1}],progress:{done:2,total:2}});rt.runTimers();
  assert.equal(rt.requests[0].payload.refresh,undefined);
  await rt.resolve(0,{rows});await rt.resolve(1,{rows});
  const before=chosen(rt.ui.panel());
  rt.ui.refresh();rt.runTimers();
  assert.deepEqual(chosen(rt.ui.panel()),before);
  assert.equal(rt.requests[2].payload.refresh,true);
  const newerRows=[...rows,{date:'2026-03-22',close:170}];
  await rt.resolve(2,{rows:newerRows});assert.equal(rt.requests[3].payload.refresh,true);
  await rt.resolve(3,{rows:newerRows});
  assert.match(rt.ui.panel(),/data-compare-total="60"/,'explicit history refresh keeps the chosen period instead of growing the old base window');
  rt.click({sectorCompareToggle:'c'});
  assert.equal(rt.requests[4].payload.refresh,undefined,'newly added sectors use the normal cache');
});

test('an empty completed catalogue is distinct from initial loading and can later populate',()=>{
  const rt=runtime();
  let html=rt.ui.panel({items:[],catalogueReady:false,progress:{done:0,total:0}});
  assert.match(html,/正在读取板块报价/);assert.match(html,/aria-busy="true"/);
  html=rt.ui.panel({items:[],catalogueReady:true,progress:{done:0,total:0}});
  assert.match(html,/当前数据源暂无板块/);assert.match(html,/aria-busy="false"/);
  assert.doesNotMatch(html,/正在读取板块报价/);
  html=rt.ui.panel({items:[{id:'a',changePercent:1}],catalogueReady:true,progress:{done:1,total:1}});
  assert.deepEqual(chosen(html),['a']);
});

test('chart warnings omit only the inapplicable volume-unit note and group repeated relevant warnings',()=>{
  assert.deepEqual(displayWarnings([
    {name:'银行',warnings:['此类证券的成交量保留终端原始单位','本次未更新','本次未更新','另一个关于成交量异常的有效提示']},
    {name:'电力',warnings:['此类证券的成交量保留终端原始单位。','本次未更新'],error:'读取失败'}
  ]),['银行、电力：本次未更新','银行：另一个关于成交量异常的有效提示','电力：读取失败']);
});
