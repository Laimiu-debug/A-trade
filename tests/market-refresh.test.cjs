const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Expose only test hooks from the real closure; requests, rendering, guards and
// event handlers below still execute the production implementation unchanged.
function runtime() {
  const calls = [], intervals = [], timers = [], listeners = new Map();
  let clock = 1_000_000, renders = 0, stockPayload;
  const AT = {isDesktop:true, pages:{G01:{}}, state:{page:'G01',stock:'sh600001'}, escape:value=>String(value ?? ''), icon:()=>'', button:()=>'', head:()=>'', view:()=>'', render(){renders++;}, marketAnalysis:{overview:()=>'',controls:()=>'',selected:()=>'',scaleValues:()=>[],price:()=>'',forcePanel:()=>''}, marketExplorer:{captureFocus:()=>null,restoreFocus(){},stockView(value){stockPayload=value;return '';},sectorView:()=>''}};
  const document = {hidden:false,activeElement:null,addEventListener(type, handler){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(handler);},querySelector:()=>null,querySelectorAll:()=>[]};
  const window = {addEventListener(){},atradeDesktop:{market(method,payload){return new Promise(resolve=>calls.push({method,payload,resolve}));}}};
  class Clock extends Date {static now(){return clock;}}
  let script = fs.readFileSync(path.join(__dirname,'../preview/market-module.js'),'utf8');
  script = script.replace(/\}\)\(\);\s*$/, `
    AT.testMarket = {
      setup(options={}) {
        source={configured:true,path:'fixture'}; sourceLoaded=true; online={enabled:true,refreshSeconds:30};
        barsData={symbol:AT.state.stock,rows:[{date:'2026-10-08'},{date:'2026-10-09'}]}; selectedIndex=0;
        if (options.date) intradayDate=options.date;
        chartMode=options.mode || 'intraday';
        if(options.sectors){sectorData={items:options.sectors};sectorKey='0|all';}
      },
      setDate(date){intradayDate=date;}, loadIntraday, ensurePage, listView, loadResearch, loadSnapshot, pollMarketJobs, cancelMarketJob, refreshVisible,
      get(){return {intradayDate,intradayBusy,intradayKey,data:intradayData,sectorQuoteDone,sectorQuoteBusy,sectorQuotes:[...sectorQuotes.values()],researchBusy,snapshotBusy,researchError,snapshotError,researchData,snapshotData};},
      intradayHtml(rows){intradayData={date:'2026-10-09',rows,previousClose:10};return intradayChart(rows);}
    };
  })();`);
  vm.runInNewContext(script,{AT,document,window,Date:Clock,performance,structuredClone,queueMicrotask(){},setInterval:fn=>intervals.push(fn),setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}});
  async function reply(index,data,ok=true){calls[index].resolve(ok?{ok:true,data}:{ok:false,error:data});await new Promise(setImmediate);}
  return {AT,document,api:AT.testMarket,calls,timers,reply,stockPayload:()=>stockPayload,renders:()=>renders,tick(ms=31_000){clock+=ms;intervals.forEach(fn=>fn());}};
}

test('changing the intraday date while a request is pending keeps the latest choice in either completion order',async()=>{
  for(const order of [[0,1],[1,0]]) {
    const rt=runtime();rt.api.setup({date:'2026-10-08'});
    const first=rt.api.loadIntraday(true);
    rt.api.setDate('2026-10-09');const second=rt.api.loadIntraday(true);
    assert.deepEqual(rt.calls.map(item=>item.payload.date),['2026-10-08','2026-10-09']);
    for(const index of order) await rt.reply(index,{date:rt.calls[index].payload.date,rows:[{time:'09:31',close:10}]});
    await Promise.all([first,second]);
    assert.equal(rt.api.get().intradayDate,'2026-10-09');
    assert.equal(rt.api.get().data.date,'2026-10-09');
    assert.equal(rt.api.get().intradayBusy,false);
  }
});

test('automatic page ensures cannot replace an explicit latest-date request or duplicate a busy same-date request',async()=>{
  const rt=runtime();rt.api.setup({date:'2026-10-08'});
  const work=rt.api.loadIntraday(true,true);
  await rt.api.loadIntraday();
  await rt.api.loadIntraday(true,true);
  assert.equal(rt.calls.length,1);
  assert.equal(rt.calls[0].payload.date,undefined);
  await rt.reply(0,{date:'2026-10-09',rows:[{time:'09:31',close:10}]});await work;
  assert.equal(rt.api.get().intradayDate,'2026-10-09');
});

test('intraday paths break at missing trading minutes while a lunch break remains consecutive',()=>{
  const rt=runtime();rt.api.setup();
  const rows=['09:31','09:32','10:31','11:30','13:01','13:02'].map((time,i)=>({time,close:10+i,volume:100}));
  const html=rt.api.intradayHtml(rows);
  const line=html.match(/<path d="([^"]*)" fill="none" class="market-price-line"/)[1];
  assert.deepEqual(line.match(/[ML]/g),['M','L','M','M','L','L']);
  assert.equal((html.match(/data-market-minute-point/g)||[]).length,rows.length,'isolated genuine observations remain visible as points');
  assert.doesNotMatch(html,/NaN|undefined/);
});

test('sector quotes repeat on the configured interval and pause outside the visible sector page',async()=>{
  const rt=runtime();rt.AT.state.page='M02';rt.api.setup({sectors:[{id:'sh881001',symbol:'sh881001',name:'fixture'}]});
  rt.api.ensurePage('M02');
  assert.equal(rt.calls[0].method,'market.quotes');
  await rt.reply(0,{items:[{symbol:'sh881001',price:10,changePercent:1}]});
  assert.equal(rt.api.get().sectorQuoteDone,1);
  rt.tick();assert.equal(rt.calls.length,2);
  rt.tick();assert.equal(rt.calls.length,2,'an active pass is never overlapped');
  await rt.reply(1,{items:[{symbol:'sh881001',price:11,changePercent:2}]});
  assert.equal(rt.api.get().sectorQuotes[0].changePercent,2);
  rt.document.hidden=true;rt.tick();assert.equal(rt.calls.length,2);
  rt.document.hidden=false;rt.AT.state.page='C01';rt.tick();assert.equal(rt.calls.length,2);
  rt.AT.state.page='M02';rt.tick();assert.equal(rt.calls.length,3);
});

test('stock lists use a lightweight snapshot independently of the market-style research result',async()=>{
  const rt=runtime();rt.api.setup();rt.AT.state.page='M04';
  rt.api.ensurePage('M04');
  assert.deepEqual(rt.calls.map(item=>item.method),['market.snapshot']);
  await rt.reply(0,{items:[{symbol:'sh600001'}],staleItems:[],asOf:'2026-10-09'});
  rt.api.listView();assert.equal(rt.stockPayload().items.length,1);
  assert.equal(rt.stockPayload().refreshAction,'refresh-snapshot');
  rt.AT.state.page='M01';rt.api.ensurePage('M01');
  const researchIndex=rt.calls.findIndex(item=>item.method==='market.research');
  assert.ok(researchIndex>0);
  await rt.reply(researchIndex,{items:[{symbol:'sh600002'}],asOf:'2026-10-08',timeline:[]});
  rt.api.listView();assert.equal(rt.stockPayload().items[0].symbol,'sh600001');
});

test('a failed sector refresh labels retained quotes as old and retries on the next interval',async()=>{
  const rt=runtime();rt.AT.state.page='M02';rt.api.setup({sectors:[{id:'sh881001',symbol:'sh881001'}]});
  rt.api.ensurePage('M02');await rt.reply(0,{items:[{symbol:'sh881001',price:10,changePercent:1}]});
  rt.tick();await rt.reply(1,{items:[]});
  assert.equal(rt.api.get().sectorQuotes[0].cached,true);
  assert.equal(rt.api.get().sectorQuotes[0].price,10);
  rt.tick();assert.equal(rt.calls.length,3);
  await rt.reply(2,{items:[{symbol:'sh881001',price:11,changePercent:2,cached:false}]});
  assert.equal(rt.api.get().sectorQuotes[0].cached,false);
});

test('cancel targets the active list calculation, preserves old results, and does not restart it on render',async()=>{
  const rt=runtime();rt.api.setup();rt.AT.state.page='M04';
  const initial=rt.api.loadSnapshot();await rt.reply(0,{items:[{symbol:'sh600001'}],asOf:'2026-10-09'});await initial;
  const refresh=rt.api.loadSnapshot(true);
  const cancel=rt.api.cancelMarketJob('market.snapshot');
  assert.equal(rt.calls[2].method,'market.research.status');
  await rt.reply(2,{jobs:[{id:4,method:'market.analysis.trend'},{id:5,method:'market.snapshot'}]});
  assert.equal(rt.calls[3].method,'market.research.cancel');assert.equal(rt.calls[3].payload.id,5);
  await rt.reply(3,{cancelled:true});await cancel;
  await rt.reply(1,'已取消本次市场计算，保留上次结果。',false);await refresh;
  assert.equal(rt.api.get().snapshotData.items[0].symbol,'sh600001');
  assert.match(rt.api.get().snapshotError,/已取消/);
  rt.api.ensurePage('M04');assert.equal(rt.calls.length,4);
});

test('background renders wait for Chinese input composition to finish',()=>{
  const rt=runtime();rt.AT.state.page='M04';
  rt.AT.marketExplorer.isComposing=()=>true;
  rt.api.refreshVisible();rt.api.refreshVisible();
  assert.equal(rt.renders(),0);assert.equal(rt.timers.length,1);
  rt.AT.marketExplorer.isComposing=()=>false;
  rt.timers.shift()();assert.equal(rt.renders(),1);
});

test('sector row and comparison share one forced source response after manual refresh',async()=>{
  const requests=[],timers=[];
  const AT={isDesktop:true,state:{page:'M02'},escape:value=>String(value??''),render(){}};
  const document={activeElement:null,documentElement:{classList:{remove(){},add(){}},hasPointerCapture:()=>false},addEventListener(){},querySelector:()=>null,querySelectorAll:()=>[]};
  const window={addEventListener(){},atradeDesktop:{market(method,payload){return new Promise(resolve=>requests.push({method,payload,resolve}));}}};
  const context={AT,document,window,setTimeout:fn=>timers.push(fn)};
  for(const file of ['sector-trends.js','sector-comparison.js'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview',file),'utf8'),context);
  const flush=()=>{while(timers.length)timers.shift()();};
  const item={id:'sh881001',symbol:'sh881001',name:'fixture',changePercent:2};
  const rows=[{date:'2026-10-08',close:10},{date:'2026-10-09',close:11}];
  AT.sectorTrends.setVisible([item]);AT.sectorComparison.panel({items:[item],progress:{done:1,total:1}});flush();
  assert.equal(requests.length,1,'the two consumers share the pending request');
  requests[0].resolve({ok:true,data:{rows}});await new Promise(setImmediate);flush();
  assert.match(AT.sectorTrends.row(item),/10-09/);assert.match(AT.sectorComparison.panel(),/2026-10-09/);
  AT.sectorTrends.refresh();AT.sectorComparison.refresh();flush();
  assert.equal(requests.length,2);assert.equal(requests[1].payload.refresh,true);
  requests[1].resolve({ok:true,data:{rows:[...rows,{date:'2026-10-12',close:12}]}});await new Promise(setImmediate);flush();
  assert.match(AT.sectorTrends.row(item),/10-12/);assert.match(AT.sectorComparison.panel(),/2026-10-12/);
  assert.equal(requests.length,2);
  AT.sectorTrends.setVisible([{...item,id:'sh881002',symbol:'sh881002'}]);flush();
  assert.equal(requests[2].payload.refresh,undefined,'a newly visited sector uses the normal source cache');
});
