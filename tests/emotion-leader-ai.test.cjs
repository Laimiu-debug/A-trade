const {test}=require('node:test'),assert=require('node:assert/strict');
const {collect,dateIntent,knownStocks}=require('../preview/page-ai-materials.js');
const {sanitizeContext}=require('../desktop/page-assistant-prompt.cjs');
const stock={symbol:'sh600001',code:'600001',name:'龙头甲'},other={symbol:'sh600002',name:'龙头乙'};
const context=()=>({page:{id:'T10',title:'情绪周期'},capturedAt:new Date().toISOString(),dataDates:['2026-09-08'],filters:{asOf:'2026-09-08'},summary:'当前市场证据',tables:[],limitations:[],evidence:{current:{date:'2026-09-08',label:'FUTURE_MARKET'},leaders:[stock,other],history:[{symbol:'sh600003',name:'历史样本'}]}});
const bars=symbol=>({symbol,name:symbol===stock.symbol?stock.name:other.name,adjustment:'none',volumeUnit:'股',amountUnit:'元',dataAsOf:'2026-09-09',dataVersion:'private-digest',path:'C:\\private',rows:[{date:'2026-09-07',close:10},{date:'2026-09-08',close:11},{date:'2026-09-09',close:9999}]});
function harness(items=[stock]){const calls=[];return {calls,market:async(method,payload)=>{calls.push({method,payload});return method==='market.resolve'?{items}:bars(payload.symbol)}}}
const base=()=>({question:'分析龙头甲',context:context(),today:'2026-10-14'});

test('page rendering and candidate detection are read-only; explicit send fetches only named stocks',async()=>{
 const h=harness();assert.deepEqual(knownStocks(context()),[stock,other]);assert.equal(h.calls.length,0);
 const result=await collect({...base(),...h});
 assert.deepEqual(h.calls.map(row=>row.method),['market.resolve','market.bars']);assert.deepEqual(h.calls[1].payload,{symbol:stock.symbol,end:'2026-09-08',limit:120});
 assert.equal(result.context.evidence.dailyBars.length,2);assert.equal(result.context.evidence.selectedStock.symbol,stock.symbol);
 assert.doesNotMatch(JSON.stringify(result.context),/9999|private-digest|private|dataAsOf/);
 assert.equal(sanitizeContext(result.context).evidence.dailyBars.length,2);
});

test('historical date questions discard later page indicators instead of relabelling them',async()=>{
 const h=harness(),original=context();const result=await collect({...base(),...h,context:original,question:'龙头甲在2026年9月7日怎么样'});
 assert.deepEqual(result.context.dataDates,['2026-09-07']);assert.equal(result.context.evidence.dailyBars.length,1);
 assert.doesNotMatch(JSON.stringify(result.context),/FUTURE_MARKET/);assert.equal(original.evidence.current.label,'FUTURE_MARKET');
 assert.equal(result.context.evidence.requestedStockRange.to,'2026-09-07');
 await assert.rejects(collect({...base(),...harness([]),question:'2026-09-07市场情绪怎样',context:{...context(),evidence:{}}}),/日期.*不同/);
});

test('comparisons preserve three separate bounded series and real holiday cutoffs',async()=>{
 const h=harness([stock,other]);const result=await collect({...base(),...h,question:'比较龙头甲和龙头乙在2026/9/7到2026/9/8'});
 assert.equal(h.calls.length,3);assert.equal(result.context.evidence.requestedStocks.length,2);
 assert.ok(h.calls.slice(1).every(row=>row.payload.start==='2026-09-07'&&row.payload.end==='2026-09-08'));
 assert.equal(sanitizeContext(result.context).evidence.requestedStocks[1].dailyBars.length,2);
});

test('ambiguous, unknown, invalid dates, excessive scope and missing data block provider-bound material',async()=>{
 for(const resolved of [{ambiguous:[{mention:'甲',items:[stock,other]}]},{unknown:['999999']}]){
  let barsCalls=0;await assert.rejects(collect({...base(),market:async method=>{if(method==='market.bars')barsCalls++;return resolved}}),/多个匹配|未找到/);assert.equal(barsCalls,0);
 }
 await assert.rejects(collect({...base(),...harness([stock,other,{symbol:'sh600003'},{symbol:'sh600004'}])}),/最多/);
 await assert.rejects(collect({...base(),...harness(),question:'龙头甲2026-02-30'}),/日期无效/);
 await assert.rejects(collect({...base(),...harness(),question:'龙头甲2027-01-01'}),/晚于今天/);
 await assert.rejects(collect({...base(),market:async method=>method==='market.resolve'?{items:[stock]}:{symbol:stock.symbol,rows:[]}}),/没有可用日线/);
 await assert.rejects(collect({...base(),market:async method=>method==='market.resolve'?{items:[stock]}:bars('sz000001')}),/不一致/);
});

test('generic questions do not fetch any bars; followups and shortcuts only fetch their selected security',async()=>{
 const h=harness([]);await collect({...base(),...h,question:'解释当前情绪',context:{...context(),evidence:{}}});assert.deepEqual(h.calls.map(row=>row.method),['market.resolve']);
 for(const options of [{focus:stock},{previous:[stock],question:'这只股的量能呢'}]){
  const h=harness([]);const result=await collect({...base(),...h,...options});assert.equal(result.stocks[0].symbol,stock.symbol);assert.equal(h.calls.length,2);
 }
});

test('stopping or changing scope during data reads never reaches subsequent material collection',async()=>{
 let active=true,resolve;const pending=new Promise(r=>resolve=r),calls=[];
 const running=collect({...base(),check:()=>{if(!active)throw Error('已停止')},market:async method=>{calls.push(method);if(method==='market.resolve')return {items:[stock,other]};return pending}});
 await new Promise(r=>setImmediate(r));active=false;resolve(bars(stock.symbol));await assert.rejects(running,/已停止/);assert.deepEqual(calls,['market.resolve','market.bars']);
});

test('full ISO, Chinese and slash dates normalize without changing the page or using future cutoffs',()=>{
 for(const question of ['2026-9-7','2026年9月7日','2026/9/7'])assert.equal(dateIntent(question,context(),'2026-10-14').end,'2026-09-07');
 assert.equal(dateIntent('近期怎样',context(),'2026-10-14').end,'2026-09-08');
});

test('a historical stock-chart focus discards future prices and fills even without an explicit date in the question',async()=>{
 const chart={...context(),page:{id:'G01',title:'个股行情'},filters:{selectedDay:'2026-09-07'},evidence:{selectedStock:stock,source:{asOf:'2026-09-07',latestLoadedDate:'2026-09-08'},futureTrade:'LATER_FILL'}};
 const result=await collect({...base(),...harness([]),context:chart,question:'这只股量能如何'});
 assert.equal(result.context.evidence.dailyBars.at(-1).date,'2026-09-07');assert.doesNotMatch(JSON.stringify(result.context),/LATER_FILL|FUTURE_MARKET/);
});

test('pronoun followups preserve the previous historical cutoff, and placeholder examples cannot be submitted',async()=>{
 const result=await collect({...base(),...harness([]),question:'那天它的量能呢',previous:[stock],previousDate:'2026-09-07'});
 assert.equal(result.context.filters.questionAsOf,'2026-09-07');assert.equal(result.context.evidence.dailyBars.at(-1).date,'2026-09-07');
 await assert.rejects(collect({...base(),...harness([]),question:'分析【股票名称或代码】'}),/替换/);
});
