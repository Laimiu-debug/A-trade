const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function toolsUI(handler=async()=>({ok:true,data:{items:[]}}),options={}) {
  const events=new Map(),windowEvents=new Map(),queue=[],requests=[];
  let clock=Date.now();class TestDate extends Date {constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
  const AT={isDesktop:true,state:{page:'T01',stock:''},escape:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),icon:()=>'',viewParts:{head:(title,subtitle,actions)=>`<h1>${title}</h1><p>${subtitle}</p>${actions}`},pages:Object.fromEntries(Array.from({length:9},(_,i)=>[`T0${i+1}`,{title:`工具${i+1}`}])) ,view:()=>'<p>演示占位 fallback</p>',renderCount:0,render(){this.renderCount++;},marketUI:{status:{path:'D:\\fixture',latestDate:'2026-10-09'}}};
  const bridge={market:async(method,payload)=>{requests.push([method,payload]);return handler(method,payload);}};
  const document={addEventListener:(name,listener)=>events.set(name,listener),querySelector:()=>null};
  const context={AT,window:{atradeDesktop:bridge,addEventListener:(name,listener)=>windowEvents.set(name,listener)},document,queueMicrotask:callback=>queue.push(callback),setTimeout:options.setTimeout||setTimeout,clearTimeout:options.clearTimeout||clearTimeout,Date:TestDate,console};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/analysis-tools.js'),'utf8'),context);
  const settle=async()=>{while(queue.length){queue.shift()();await new Promise(resolve=>setImmediate(resolve));}};
  const click=dataset=>{const el={dataset,disabled:false};events.get('click')({target:{closest:()=>el},preventDefault(){}});};
  const input=(group,key,value)=>events.get('input')({target:{dataset:{analysisGroup:group,analysisField:key},value,matches:()=>false}});
  return {AT,api:AT.analysisTools,requests,settle,click,input,events,windowEvents,document,advanceTime(ms){clock+=ms;}};
}

test('all desktop analysis routes replace demo pages without synthesizing data',async()=>{
  const app=toolsUI();
  for(let i=1;i<=9;i++){
    const page=`T0${i}`;app.AT.state.page=page;
    const html=app.AT.view(page);
    assert.doesNotMatch(html,/演示占位 fallback|358|6523|5,191/);
    assert.match(html,/data-analysis-page=/);
    await app.settle();
  }
  assert.deepEqual(app.requests.map(([method])=>method),['market.analysis.overview','market.finance.context','market.finance.screen','market.analysis.trends','market.regulatory.list','market.analysis.rebounds','market.analysis.ladder','market.watchlist.list']);
});

test('market-style link opens the selected historical ladder with candidate filters',async()=>{
  const app=toolsUI();
  app.AT.navigate=page=>{app.AT.state.page=page;};
  app.AT.state.page='M01';
  app.click({leaderDay:'2026-09-18',nav:'T08'});
  assert.equal(app.AT.state.page,'T08');
  app.AT.view('T08');await app.settle();
  assert.equal(app.requests.at(-1)[0],'market.analysis.ladder');
  assert.deepEqual(JSON.parse(JSON.stringify(app.requests.at(-1)[1])),{asOf:'2026-09-18',historyDays:250,board:'all',minHeight:2,excludeST:true});
  app.click({analysisChip:'board',analysisScope:'ladder',analysisValue:'chinext'});
  app.AT.view('T08');await app.settle();
  assert.equal(app.requests.at(-1)[1].board,'chinext');
  assert.equal(app.requests.at(-1)[1].asOf,'2026-09-18');
  app.api.openLeaders('not-a-date');assert.equal(app.AT.state.page,'T08');
});

test('regulatory list remains current and does not inherit a historical research date',async()=>{
  const app=toolsUI();
  app.input('research','asOf','2025-06-30');
  app.AT.state.page='T06';
  const html=app.AT.view('T06');await app.settle();
  assert.doesNotMatch(html,/data-analysis-field="asOf"|放量触发|涨跌幅触发|突破回看/);
  assert.equal(app.requests.at(-1)[0],'market.regulatory.list');
  assert.equal(JSON.stringify(app.requests.at(-1)[1]),'{}');
  app.click({analysisAction:'refresh'});await app.settle();
  assert.equal(JSON.stringify(app.requests.at(-1)[1]),'{"refresh":true}');
});

test('valuation uses TTM and explicit scenario parameters with consistent monetary units',()=>{
  const {api}=toolsUI();
  const settings={growth:1.2,pe:10,water:.9,emotion:1.1,actual:''};
  const result=api.computeValuation({ttmNetProfit:2e8,marketCap:3e9},settings);
  assert.equal(result.eligible,true);
  assert.equal(result.base,2.16e9);
  assert.equal(result.theoretical,2.376e9);
  assert.equal(result.impliedEmotion,3e9/2.16e9);
  assert.equal(result.manualActual,false);
  assert.equal(api.computeValuation({ttmNetProfit:2e8,marketCap:3e9},{...settings,actual:40}).actual,4e9);
  for(const profit of [null,undefined,0,-2e8])assert.equal(api.computeValuation({ttmNetProfit:profit},settings).eligible,false);
  assert.equal(api.computeValuation({ttmNetProfit:2e8},{...settings,pe:''}).eligible,false);
  assert.equal(api.computeValuation({ttmNetProfit:2e8},{...settings,pe:Infinity}).eligible,false);
});

test('valuation name search shows real stock candidates and selecting one analyzes its qualified symbol',async()=>{
  const items=[{symbol:'sh600900',code:'600900',name:'长江电力',kind:'stock'},{symbol:'sh000001',code:'000001',name:'上证指数',kind:'index'}];
  const app=toolsUI(async method=>({ok:true,data:method==='market.search'?{items,total:1}:{items:[]}}));
  app.AT.state.page='T02';app.input('financeInput','symbol','长江');
  await new Promise(resolve=>setTimeout(resolve,210));
  const request=app.requests.find(([method])=>method==='market.search');
  assert.equal(JSON.stringify(request[1]),JSON.stringify({query:'长江',kind:'stock',offset:0,limit:8}));
  assert.equal(app.requests.some(([method])=>method==='market.finance.snapshot'),false);
  const html=app.AT.view('T02');assert.match(html,/data-finance-candidate="sh600900"/);assert.doesNotMatch(html,/data-finance-candidate="sh000001"/);
  app.click({financeCandidate:'sh600900'});await app.settle();
  assert.equal(app.requests.find(([method])=>method==='market.finance.snapshot')[1].symbol,'sh600900');
  assert.doesNotMatch(app.AT.view('T02'),/data-finance-candidate=/);
});

test('valuation lookup discards late responses and keyboard selection does not parse a name as a code',async()=>{
  const pending=new Map();
  const app=toolsUI((method,payload)=>method==='market.search'?new Promise(resolve=>pending.set(payload.query,resolve)):Promise.resolve({ok:true,data:{items:[]}}));
  app.AT.state.page='T02';app.input('financeInput','symbol','旧');await new Promise(resolve=>setTimeout(resolve,200));
  app.input('financeInput','symbol','电力');await new Promise(resolve=>setTimeout(resolve,200));
  pending.get('电力')({ok:true,data:{items:[{symbol:'sh600900',code:'600900',name:'长江电力',kind:'stock'},{symbol:'sh600011',code:'600011',name:'华能国际',kind:'stock'}],total:2}});await app.settle();await new Promise(resolve=>setImmediate(resolve));
  pending.get('旧')({ok:true,data:{items:[{symbol:'sh600001',code:'600001',name:'旧结果',kind:'stock'}],total:1}});await new Promise(resolve=>setImmediate(resolve));
  assert.doesNotMatch(app.AT.view('T02'),/旧结果/);
  const target={dataset:{analysisGroup:'financeInput'},matches:()=>false};
  for(const key of ['ArrowDown','ArrowDown','Enter'])app.events.get('keydown')({target,key,preventDefault(){}});
  await app.settle();
  assert.equal(app.requests.find(([method])=>method==='market.finance.snapshot')[1].symbol,'sh600011');
});

test('valuation stock search respects IME composition and page/source cancellation',async()=>{
  let finish;
  const app=toolsUI(method=>method==='market.search'?new Promise(resolve=>{finish=resolve;}):Promise.resolve({ok:true,data:{items:[]}}));
  app.AT.state.page='T02';const target={dataset:{analysisGroup:'financeInput'},value:'长江'};
  app.events.get('compositionstart')({target});app.input('financeInput','symbol','chang');
  app.events.get('submit')({target:{dataset:{analysisForm:'finance'},reportValidity:()=>true},preventDefault(){},stopPropagation(){}});
  await new Promise(resolve=>setTimeout(resolve,210));assert.equal(app.requests.length,0);
  app.events.get('compositionend')({target});await new Promise(resolve=>setTimeout(resolve,210));assert.equal(app.requests[0][1].query,'长江');
  app.AT.state.page='T04';app.api.updateShell('T04');
  finish({ok:true,data:{items:[{symbol:'sh600900',code:'600900',name:'过期股票',kind:'stock'}],total:1}});await new Promise(resolve=>setImmediate(resolve));
  app.AT.state.page='T02';assert.doesNotMatch(app.AT.view('T02'),/过期股票/);
  app.input('financeInput','symbol','CJDL');app.api.invalidateSource();await new Promise(resolve=>setTimeout(resolve,210));assert.equal(app.requests.filter(([method])=>method==='market.search').length,1);
});

test('late research responses cannot render a different page or survive a source switch',async()=>{
  let done;
  const app=toolsUI(()=>new Promise(resolve=>{done=resolve;}));
  app.AT.state.page='T04';app.AT.view('T04');await app.settle();
  app.AT.state.page='T09';const before=app.AT.renderCount;
  done({ok:true,data:{items:[{symbol:'sh600001',name:'旧来源股票',amount:1e8}],total:1}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.AT.renderCount,before);
  app.api.invalidateSource();app.AT.state.page='T04';
  assert.doesNotMatch(app.AT.view('T04'),/旧来源股票/);
});

test('refresh failures preserve previous research rows and show an error inline',async()=>{
  let count=0;
  const app=toolsUI(async()=>++count===1?{ok:true,data:{items:[{symbol:'sh600001',code:'600001',name:'真实候选',amount:1e8}],total:1}}:{ok:false,error:'来源暂不可读'});
  app.AT.state.page='T04';app.AT.view('T04');await app.settle();
  assert.match(app.AT.view('T04'),/真实候选/);
  app.click({analysisAction:'refresh'});await app.settle();
  const html=app.AT.view('T04');
  assert.match(html,/真实候选/);assert.match(html,/来源暂不可读/);assert.match(html,/role="alert"/);
});

test('finance screening excludes losses without deriving a false PE, and escapes remote text',async()=>{
  const rows=[{symbol:'sh600001',name:'盈利股',ttmNetProfit:1e8,marketCap:1e9},{symbol:'sh600002',name:'亏损股',ttmNetProfit:-1e8,marketCap:1e9},{symbol:'sh600003',name:'<script>缺失</script>',ttmNetProfit:null,marketCap:1e9}];
  const app=toolsUI(async(method,payload)=>{const items=rows.filter(item=>!payload.positive||item.ttmNetProfit>0);return {ok:true,data:{items,total:items.length}};});
  app.AT.state.page='T03';app.AT.view('T03');await app.settle();
  let html=app.AT.view('T03');assert.match(html,/盈利股/);assert.doesNotMatch(html,/亏损股/);
  app.click({analysisChip:'positive',analysisValue:'true',analysisScope:'screen'});
  app.AT.view('T03');await app.settle();assert.equal(app.requests.at(-1)[1].positive,false);
  html=app.AT.view('T03');assert.match(html,/亏损股/);assert.match(html,/&lt;script&gt;缺失&lt;\/script&gt;/);assert.doesNotMatch(html,/<script>/);
});

test('adding an already followed stock preserves its existing notes without a save request',async()=>{
  const app=toolsUI(async(method)=>({ok:true,data:method==='market.watchlist.list'?{items:[{symbol:'sh600001',note:'已有观察记录'}]}:{items:[]}}));
  app.AT.state.page='T04';app.click({analysisAction:'watch-add',symbol:'sh600001'});await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(app.requests.map(([method])=>method),['market.watchlist.list']);
  assert.match(app.AT.view('T04'),/原观察记录保持不变/);
});

test('watchlist refresh sends only supported fields and text sorting toggles both directions',async()=>{
  const app=toolsUI(async()=>({ok:true,data:{items:[{symbol:'sh600001',name:'A证券',updatedAt:'2026-10-09'},{symbol:'sh600002',name:'B证券',updatedAt:'2026-10-08'}]}}));
  app.AT.state.page='T09';app.AT.view('T09');await app.settle();
  app.click({analysisAction:'refresh'});await app.settle();
  assert.equal(JSON.stringify(app.requests.at(-1)[1]),'{}');
  app.click({analysisSort:'name'});
  let html=app.AT.view('T09');assert.ok(html.indexOf('A证券')<html.indexOf('B证券'));
  app.click({analysisSort:'name'});
  html=app.AT.view('T09');assert.ok(html.indexOf('B证券')<html.indexOf('A证券'));
  app.click({analysisSort:'name'});
  html=app.AT.view('T09');assert.ok(html.indexOf('A证券')<html.indexOf('B证券'));assert.match(html,/当前为默认顺序/);
});

test('numeric sorting cycles ascending descending and default without clearing filters',async()=>{
  const rows=[{symbol:'sh600001',name:'匹配甲',return20:10,amount:9},{symbol:'sh600002',name:'匹配乙',return20:30,amount:2},{symbol:'sh600003',name:'不匹配项',return20:40,amount:1}];
  const app=toolsUI(async(method,payload)=>({ok:true,data:{items:rows.filter(item=>!payload.query||item.name.includes(payload.query)).sort((a,b)=>(a[payload.sort||'return20']-b[payload.sort||'return20'])*(payload.sort&&payload.order==='asc'?1:-1))}}));
  app.AT.state.page='T04';app.AT.view('T04');await app.settle();
  app.events.get('input')({target:{value:'匹配甲',matches:()=>true}});
  app.click({analysisSort:'amount'});app.AT.view('T04');await app.settle();assert.doesNotMatch(app.AT.view('T04'),/匹配乙/);
  app.events.get('input')({target:{value:'匹配',matches:()=>true}});
  app.click({analysisSort:'amount'});app.AT.view('T04');await app.settle();let html=app.AT.view('T04');assert.ok(html.indexOf('匹配甲')<html.indexOf('匹配乙'));
  app.click({analysisSort:'amount'});app.AT.view('T04');await app.settle();html=app.AT.view('T04');assert.ok(html.indexOf('匹配乙')<html.indexOf('匹配甲'));assert.match(html,/当前为默认顺序/);assert.match(html,/value="匹配"/);
  app.click({analysisSort:'return20'});app.AT.view('T04');await app.settle();html=app.AT.view('T04');assert.ok(html.indexOf('匹配甲')<html.indexOf('匹配乙'));
});

test('benchmark reference includes coverage and separate historical scope, adopted only on explicit click',async()=>{
  const benchmark={industry:'电力',asOf:'2026-10-09',medianPE:30.16,p25PE:19,p75PE:52,meanPE:84.64,sampleCount:94,totalCount:109,excludedNonPositiveCount:10,excludedMissingCount:5,includesSubject:true,historicalMedianPE:20,historical:{medianPE:20,days:250,startDate:'2025-10-01',endDate:'2026-10-09',currentPE:23,p25PE:18,p75PE:24}};
  const app=toolsUI(async method=>({ok:true,data:method==='market.finance.benchmark'?benchmark:{ttmNetProfit:4e8,marketCap:8e9}}));
  app.AT.state.page='T02';app.input('financeInput','symbol','600900');app.input('scenario','pe','12');app.input('scenario','growth','1.3');app.input('scenario','water','0.9');app.input('scenario','emotion','1.2');
  app.events.get('submit')({target:{dataset:{analysisForm:'finance'},reportValidity:()=>true},preventDefault(){},stopPropagation(){}});await app.settle();await new Promise(resolve=>setImmediate(resolve));
  let html=app.AT.view('T02');assert.match(html,/有效样本 94 \/ 109/);assert.match(html,/2026-10-09/);assert.match(html,/行业整体 PE 的历史分布/);assert.doesNotMatch(html,/data-analysis-group="scenario"/,'自动分析默认无需填写情景');
  app.click({analysisAction:'valuation-tab',valuationTab:'scenario'});assert.match(app.AT.view('T02'),/data-analysis-field="pe" value="12"/);
  app.click({analysisAction:'benchmark-adopt',benchmarkKey:'medianPE'});html=app.AT.view('T02');assert.match(html,/data-analysis-field="pe" value="30.16"/);
  assert.equal((html.match(/已采用同行 PE 参考/g)||[]).length,1,'成功提示只出现一次');
  app.click({analysisAction:'benchmark-adopt',benchmarkKey:'notAnAllowedKey'});assert.match(app.AT.view('T02'),/data-analysis-field="pe" value="30.16"/);
  app.click({analysisAction:'benchmark-adopt',benchmarkKey:'historicalMedianPE'});html=app.AT.view('T02');assert.match(html,/data-analysis-field="pe" value="20"/);
  for(const [key,value] of [['growth','1.3'],['water','0.9'],['emotion','1.2']])assert.ok(html.includes(`data-analysis-field="${key}" value="${value}"`));
});

test('fund history preserves missing values and cumulative totals across viewport zoom, drag and reset',async()=>{
  const items=Array.from({length:50},(_,i)=>({date:`2026-${i<30?'08':'09'}-${String(i<30?i+1:i-29).padStart(2,'0')}`,mainNetInflow:i===15?null:i%2?2e8:-1e8,largeNetInflow:3e7}));
  const app=toolsUI(async method=>({ok:true,data:method==='market.funds.history'?{id:'BK0001',name:'真实板块',items,asOf:items.at(-1).date}:{items:[{id:'BK0001',name:'真实板块',mainNetInflow:3e8}]}}));
  app.AT.state.page='T05';app.click({analysisChip:'mode',analysisValue:'net',analysisScope:'funds'});app.AT.view('T05');await app.settle();app.AT.view('T05');await app.settle();
  let html=app.AT.view('T05');assert.match(html,/data-count="50" data-total="50"/);assert.match(html,/来源实际提供 50 日/);assert.match(html,/部分日期净额缺失/);
  const series=app.api.flowSeries(items,'mainNetInflow');assert.equal(series[15].value,null);assert.equal(series[15].cumulative,null);assert.equal(series[16].cumulative,series[14].cumulative+items[16].mainNetInflow);
  app.click({analysisAction:'funds-zoom-in'});html=app.AT.view('T05');assert.match(html,/data-count="38" data-total="50"/);
  let prevented=false;
  const chart={getBoundingClientRect:()=>({left:0,width:1000}),focus(){}};
  app.events.get('wheel')({target:{closest:()=>chart},clientX:932,deltaY:-100,preventDefault(){prevented=true;}});assert.equal(prevented,true);
  html=app.AT.view('T05');const before=Number(html.match(/data-start="(\d+)"/)[1]);assert.match(html,/data-count="30" data-total="50"/);
  app.events.get('pointerdown')({target:{closest:()=>chart},button:2,pointerId:1,clientX:500,preventDefault(){}});
  app.events.get('pointermove')({pointerId:1,buttons:2,clientX:620,preventDefault(){}});
  html=app.AT.view('T05');assert.ok(Number(html.match(/data-start="(\d+)"/)[1])<before);
  app.events.get('pointerup')({pointerId:1,preventDefault(){}});
  app.click({analysisAction:'funds-reset'});html=app.AT.view('T05');assert.match(html,/data-start="0" data-count="50" data-total="50"/);
  app.click({analysisChip:'limit',analysisValue:'250',analysisScope:'funds'});app.AT.view('T05');await app.settle();assert.equal(app.requests.filter(([method])=>method==='market.funds.history').at(-1)[1].limit,250);
  html=app.AT.view('T05');assert.match(html,/请求近 250 日，来源实际提供 50 日/);
  app.click({analysisChip:'field',analysisValue:'largeNetInflow',analysisScope:'funds'});assert.match(app.AT.view('T05'),/大单当日净额/);
  app.click({analysisAction:'funds-zoom-in'});html=app.AT.view('T05');const previous=html.match(/data-start="(\d+)"/)[1];
  app.events.get('pointerdown')({target:{closest:()=>chart},button:0,pointerId:2,clientX:500,preventDefault(){}});
  app.windowEvents.get('blur')();app.events.get('pointermove')({pointerId:2,buttons:1,clientX:800,preventDefault(){}});assert.equal(app.AT.view('T05').match(/data-start="(\d+)"/)[1],previous);
  app.events.get('pointerdown')({target:{closest:()=>chart},button:0,pointerId:3,clientX:500,preventDefault(){}});
  app.api.updateShell('G01');app.events.get('pointermove')({pointerId:3,buttons:1,clientX:800,preventDefault(){}});assert.equal(app.AT.view('T05').match(/data-start="(\d+)"/)[1],previous);
  const unchanged=app.AT.view('T05').match(/data-count="(\d+)"/)[1];
  for(const key of ['ctrlKey','metaKey'])app.events.get('wheel')({target:{closest:()=>chart},clientX:500,deltaY:-100,[key]:true,preventDefault(){throw new Error('系统组合键滚轮不应被拦截');}});
  assert.equal(app.AT.view('T05').match(/data-count="(\d+)"/)[1],unchanged);
  for(const cancel of [()=>app.windowEvents.get('resize')(),()=>{app.document.hidden=true;app.events.get('visibilitychange')();app.document.hidden=false;},()=>app.events.get('pointermove')({pointerId:4,buttons:0,clientX:800,preventDefault(){}})]){
    app.events.get('pointerdown')({target:{closest:()=>chart},button:0,pointerId:4,clientX:500,preventDefault(){}});cancel();
    app.events.get('pointermove')({pointerId:4,buttons:1,clientX:800,preventDefault(){}});assert.equal(app.AT.view('T05').match(/data-start="(\d+)"/)[1],previous);
  }
});

test('unavailable uncached sources do not claim previously read results are preserved',async()=>{
  const app=toolsUI(async()=>({ok:true,data:{status:'unavailable',cached:false,stale:false,items:[],errors:[{message:'读取失败'}]}}));
  app.AT.state.page='T05';app.click({analysisChip:'mode',analysisValue:'net',analysisScope:'funds'});app.AT.view('T05');await app.settle();
  const html=app.AT.view('T05');assert.match(html,/数据暂不可用/);assert.doesNotMatch(html,/保留已读取结果/);
});

test('research views expose full candidate search sorting and server pagination',async()=>{
  const app=toolsUI(async()=>({ok:true,data:{items:[{symbol:'sh600001',name:'候选一'},{symbol:'sh600002',name:'候选二'}],total:1216}}));
  for(const page of ['T04','T07']){
    app.AT.state.page=page;app.AT.view(page);await app.settle();
    const html=app.AT.view(page);
    assert.match(html,/符合条件 1,216 项，当前读取 2 项/);
    assert.match(html,/完整候选中搜索、排序后分页/);
    app.click({analysisAction:'research-next'});app.AT.view(page);await app.settle();assert.equal(app.requests.at(-1)[1].offset,20);assert.equal(app.requests.at(-1)[1].limit,20);
  }
});

test('successful official fallbacks show recovery while failures, stale values and missing quotes stay visible',async()=>{
  const funds={status:'ready',stale:false,adapter:'official_chart',asOf:'2026-10-09',items:[{id:'BK001',name:'真实板块',mainNetInflow:1e8}],errors:[{provider:'eastmoney_funds_quote',message:'即时资金接口不可用'}]};
  const finance={status:'ready',stale:false,symbol:'sh600900',name:'真实证券',ttmNetProfit:1e8,marketCap:1e9,price:10,quoteAsOf:'2026-10-09',quoteProvider:'eastmoney_daily_valuation',quoteStale:false,errors:[{provider:'eastmoney_quote',message:'即时市值接口不可用'}]};
  const cases=[
    ['T05',funds,true],['T02',finance,true],
    ['T05',{...funds,stale:true},false],
    ['T05',{...funds,status:'unavailable',items:[],errors:[{provider:'eastmoney_funds',message:'所有资金来源不可用'}]},false],
    ['T05',{...funds,items:[]},false],
    ['T05',{...funds,errors:[...funds.errors,{provider:'other',message:'其他来源仍失败'}]},false],
    ['T02',{...finance,quoteStale:true},false],
    ['T02',{...finance,marketCap:null},false],
    ['T02',{...finance,status:'unavailable',quoteProvider:null,marketCap:null,price:null,errors:[{provider:'eastmoney_valuation',message:'所有估值来源不可用'}]},false]
  ];
  for(const [page,data,recovered] of cases){
    const app=toolsUI(async()=>({ok:true,data}));app.AT.state.page=page;
    if(page==='T02'){
      app.input('financeInput','symbol','600900');
      app.events.get('submit')({target:{dataset:{analysisForm:'finance'},reportValidity:()=>true},preventDefault(){},stopPropagation(){}});
    }else {app.click({analysisChip:'mode',analysisValue:'net',analysisScope:'funds'});app.AT.view(page);}
    await app.settle();await new Promise(resolve=>setImmediate(resolve));
    const html=app.AT.view(page);
    if(recovered){assert.match(html,/已通过官网备用来源更新/);assert.doesNotMatch(html,/role="alert"/);assert.match(html,/2026-10-09/);}
    else {assert.match(html,/role="alert"/);assert.doesNotMatch(html,/已通过官网备用来源更新/);}
  }
  let count=0;
  const app=toolsUI(async()=>++count===1?{ok:true,data:funds}:{ok:false,error:'新一轮请求失败'});
  app.AT.state.page='T05';app.click({analysisChip:'mode',analysisValue:'net',analysisScope:'funds'});app.AT.view('T05');await app.settle();
  app.click({analysisAction:'refresh'});await app.settle();await new Promise(resolve=>setImmediate(resolve));
  const html=app.AT.view('T05');
  assert.match(html,/真实板块/);assert.match(html,/新一轮请求失败/);assert.match(html,/role="alert"/);assert.doesNotMatch(html,/已通过官网备用来源更新/);
});


test('funds default uses local activity and requests online net flows only after switching',async()=>{
  const app=toolsUI();let panels=0,invalidations=0;
  app.AT.fundsActivity={panel({kind}){panels++;return `<p>real ${kind} amount activity</p>`;},endDrag(){},suspend(){},invalidate(){invalidations++;}};
  app.AT.state.page='T05';
  assert.match(app.AT.view('T05'),/real sector amount activity/);await app.settle();assert.equal(app.requests.length,0);
  app.click({analysisChip:'mode',analysisValue:'net',analysisScope:'funds'});app.AT.view('T05');await app.settle();
  assert.equal(app.requests[0][0],'market.funds.sectors');
  app.click({analysisChip:'mode',analysisValue:'activity',analysisScope:'funds'});assert.match(app.AT.view('T05'),/real sector amount activity/);
  app.api.invalidateSource();assert.equal(invalidations,1);assert.ok(panels>=2);
});

const tick=()=>new Promise(setImmediate);
const submitForm=(app,type)=>app.events.get('submit')({target:{dataset:{analysisForm:type},reportValidity:()=>true},preventDefault(){},stopPropagation(){}});

test('changing the analyzed security clears only the security-specific manual market cap',async()=>{
  const app=toolsUI(async(method,payload)=>({ok:true,data:method==='market.finance.snapshot'?{symbol:payload.symbol,ttmNetProfit:1e8,marketCap:1e10}:{}}));
  app.AT.state.page='T02';app.input('financeInput','symbol','600900');submitForm(app,'finance');await tick();await app.settle();
  app.input('scenario','actual','40');app.input('scenario','pe','10');app.click({analysisAction:'valuation-tab',valuationTab:'scenario'});
  submitForm(app,'finance');await tick();await app.settle();assert.match(app.AT.view('T02'),/data-analysis-field="actual" value="40"/,'same security retains its override');
  app.input('financeInput','symbol','600001');submitForm(app,'finance');await tick();await app.settle();
  const html=app.AT.view('T02');assert.match(html,/data-analysis-field="actual" value=""/);assert.match(html,/data-analysis-field="pe" value="10"/);assert.match(html,/100.00 亿/);assert.match(html,/-90.00%/);assert.doesNotMatch(html,/40.00 亿 · 手填/);
});

test('flush waits for the entire add workflow including pre-read and reports a failed write',async()=>{
  let resolveList,resolveSave;
  const app=toolsUI(method=>new Promise(resolve=>{if(method==='market.watchlist.list')resolveList=resolve;else resolveSave=resolve;}));
  app.AT.state.page='T04';app.click({analysisAction:'watch-add',symbol:'sh600001'});
  let finished=false;const flushing=app.api.flush().then(value=>{finished=true;return value;});await tick();assert.equal(finished,false);
  resolveList({ok:true,data:{items:[]}});await tick();assert.equal(app.requests.at(-1)[0],'market.watchlist.save-many');assert.equal(finished,false);
  resolveSave({ok:false,error:'disk full'});assert.equal(await flushing,false);assert.match(app.AT.view('T04'),/disk full/);
});

test('watchlist saves preserve a later draft and repaint the currently visible page',async()=>{
  let resolveSave;
  const app=toolsUI(method=>method==='market.watchlist.save'?new Promise(resolve=>resolveSave=resolve):{ok:true,data:{items:[]}});
  app.AT.state.page='T09';assert.doesNotMatch(app.AT.view('T09'),/data-watch-editor/);app.click({analysisAction:'watch-open'});
  app.input('watch','symbol','600001');app.input('watch','note','first');submitForm(app,'watch');await tick();
  app.click({analysisAction:'watch-new'});app.input('watch','symbol','600002');app.input('watch','note','unsaved next observation');
  app.AT.state.page='T04';const before=app.AT.renderCount;resolveSave({ok:true,data:{}});await tick();assert.ok(app.AT.renderCount>before);
  app.AT.state.page='T09';const html=app.AT.view('T09');assert.match(html,/unsaved next observation/);assert.match(html,/data-analysis-field="symbol" value="600002"/);assert.doesNotMatch(html,/保存中…/);
  app.click({analysisAction:'watch-close'});assert.doesNotMatch(app.AT.view('T09'),/data-watch-editor/);app.click({analysisAction:'watch-open'});assert.match(app.AT.view('T09'),/unsaved next observation/);
});

test('ready resources revalidate after TTL and a source-family refresh invalidates sibling pages',async()=>{
  const app=toolsUI(async()=>({ok:true,data:{items:[],total:0}}));
  app.AT.state.page='T04';app.AT.view('T04');await app.settle();const original=app.requests.length;
  app.AT.view('T04');await app.settle();assert.equal(app.requests.length,original);
  app.advanceTime(300001);app.AT.view('T04');await app.settle();assert.equal(app.requests.length,original+1);assert.equal(app.requests.at(-1)[1].refresh,true);
  app.AT.state.page='T07';app.AT.view('T07');await app.settle();app.click({analysisAction:'refresh'});await tick();
  const beforeReturn=app.requests.filter(([method])=>method==='market.analysis.trends').length;
  app.AT.state.page='T04';app.AT.view('T04');await app.settle();assert.equal(app.requests.filter(([method])=>method==='market.analysis.trends').length,beforeReturn+1);
  app.AT.state.page='T06';app.AT.view('T06');await app.settle();const beforeDay=app.requests.length;
  app.advanceTime(86400000);app.AT.view('T06');await app.settle();assert.equal(app.requests.length,beforeDay+1);
});

test('invalidated in-flight results cannot become fresh again after a related source refresh',async()=>{
  let oldLadder,refreshedTrends,ladderCalls=0;
  const app=toolsUI(method=>{
    if(method==='market.analysis.ladder'){ladderCalls++;if(ladderCalls===2)return new Promise(resolve=>oldLadder=resolve);return {ok:true,data:{sourceName:ladderCalls===1?'已读取的梯队':'刷新后的梯队',items:[]}};}
    if(method==='market.analysis.trends')return new Promise(resolve=>refreshedTrends=resolve);
    return {ok:true,data:{items:[]}};
  });
  app.AT.state.page='T08';app.AT.view('T08');await app.settle();app.click({analysisAction:'refresh'});await tick();
  app.AT.state.page='T04';app.click({analysisAction:'refresh'});await tick();
  oldLadder({ok:true,data:{sourceName:'已经失效的迟到结果',items:[]}});await tick();refreshedTrends({ok:true,data:{items:[]}});await tick();
  app.AT.state.page='T08';const previous=app.AT.view('T08');assert.doesNotMatch(previous,/已经失效的迟到结果/);assert.match(previous,/已读取的梯队/);
  await app.settle();assert.equal(ladderCalls,3);assert.match(app.AT.view('T08'),/刷新后的梯队/);
});

test('simultaneous finance force refreshes settle after bounded ordinary revalidation',async()=>{
  const pending=[];
  const app=toolsUI((method,payload)=>method.startsWith('market.finance.')?new Promise(resolve=>pending.push(()=>resolve({ok:true,data:{symbol:payload.symbol,ttmNetProfit:1e8,marketCap:1e9,medianPE:20,coefficient:1,status:'ready'}}))):{ok:true,data:{items:[]}});
  app.AT.state.page='T02';app.input('financeInput','symbol','600900');submitForm(app,'finance');
  for(let pass=0;pass<4;pass++){pending.splice(0).forEach(resolve=>resolve());await tick();app.AT.view('T02');await app.settle();}
  assert.equal(pending.length,0);const requests=app.requests.filter(([method])=>method.startsWith('market.finance.'));assert.ok(requests.length<=5,'a single force group must not invalidate its own retries forever');
  const count=app.requests.length;app.AT.view('T02');await app.settle();assert.equal(app.requests.length,count);
});

test('sector activity invalidates once per expired visit without invalidating every render',async()=>{
  const app=toolsUI();let invalidations=0;app.AT.fundsActivity={panel(){return '';},invalidate(){invalidations++;}};app.AT.state.page='T05';
  app.AT.view('T05');app.AT.view('T05');assert.equal(invalidations,0);app.advanceTime(300001);app.AT.view('T05');app.AT.view('T05');assert.equal(invalidations,1);
});

test('remote filter and sort payloads apply before pagination, including zero PE as unlimited',async()=>{
  const app=toolsUI(async()=>({ok:true,data:{items:[],total:200,hasMore:true}}));app.AT.state.page='T03';
  app.input('screen','maxPe','0');app.click({analysisAction:'screen-next'});app.AT.view('T03');await app.settle();assert.equal(app.requests.at(-1)[1].page,2);assert.equal(app.requests.at(-1)[1].maxPe,null);
  app.click({analysisSort:'actualPe'});app.AT.view('T03');await app.settle();const request=app.requests.at(-1)[1];assert.equal(request.page,1);assert.equal(request.sortBy,'actualPe');assert.equal(request.sortOrder,'asc');assert.equal(request.positive,true);assert.equal(request.scenario.pe,null);
  app.AT.state.page='T04';app.events.get('input')({target:{value:'候选',matches:()=>true}});app.click({analysisAction:'research-next'});app.AT.view('T04');await app.settle();const trends=app.requests.at(-1)[1];assert.equal(trends.query,'候选');assert.equal(trends.offset,20);assert.equal(trends.limit,20);
});

test('saved presets persist through desktop state, restore conditions and report a persistence failure',async()=>{
  const app=toolsUI();app.AT.state.page='T04';app.document.querySelector=selector=>selector==='[data-analysis-preset-name]'?{value:'常用趋势'}:null;
  app.AT.persist=async()=>false;app.input('trends','minReturn20','18');app.click({analysisAction:'preset-save'});await tick();assert.equal(app.AT.state.analysisPresets,undefined);assert.match(app.AT.view('T04'),/保存失败/);
  let persisted;app.AT.persist=async()=>{persisted=JSON.parse(JSON.stringify(app.AT.state));return true;};app.click({analysisAction:'preset-save'});await tick();assert.equal(persisted.analysisPresets.T04[0].settings.values.minReturn20,'18');
  app.input('trends','minReturn20','90');app.click({analysisAction:'preset-load',presetIndex:'0'});app.AT.view('T04');await app.settle();assert.equal(app.requests.at(-1)[1].minReturn20,18);
});

test('preset writes participate in flush and a failed old write cannot overwrite newer state',async()=>{
  const app=toolsUI();let finish;app.AT.state.page='T04';app.document.querySelector=()=>({value:'A'});app.AT.persist=()=>new Promise(resolve=>finish=resolve);
  app.click({analysisAction:'preset-save'});await tick();let done=false;const flushing=app.api.flush().then(value=>{done=true;return value;});await tick();assert.equal(done,false);
  const newer={T04:[{name:'B',settings:{group:'trends',values:{minReturn20:'20'}}}]};app.AT.state.analysisPresets=newer;finish(false);assert.equal(await flushing,false);assert.equal(app.AT.state.analysisPresets,newer);
});

test('screen scenario gaps use service values even when the unrelated emotion multiplier is empty',async()=>{
  const app=toolsUI(async()=>({ok:true,data:{items:[{symbol:'sh600001',ttmNetProfit:1e8,marketCap:1e9,actualPe:10,scenarioGap:50}],total:1}}));
  app.AT.state.page='T03';app.input('scenario','emotion','');app.AT.view('T03');await app.settle();assert.match(app.AT.view('T03'),/\+50.00%/);
});

test('bulk selections keep structured research provenance from their original result pages',async()=>{
  const app=toolsUI(async(method,payload)=>method==='market.watchlist.save-many'?{ok:true,data:{added:2,skipped:0}}:{ok:true,data:{asOf:'2026-10-09',sourceName:'本地日线',items:[{symbol:payload.offset?'sh600002':'sh600001',name:'候选'}],total:21}});
  app.AT.state.page='T04';app.AT.view('T04');await app.settle();app.click({analysisAction:'select-page'});app.click({analysisAction:'research-next'});app.AT.view('T04');await app.settle();app.click({analysisAction:'select-page'});app.click({analysisAction:'watch-bulk'});assert.equal(await app.api.flush(),true);
  const items=app.requests.find(([method])=>method==='market.watchlist.save-many')[1].items;assert.deepEqual(Array.from(items,item=>item.symbol),['sh600001','sh600002']);assert.equal(items[0].research.page,'T04');assert.equal(items[0].research.asOf,'2026-10-09');assert.equal(items[0].research.parameters.offset,undefined);assert.match(app.AT.view('T04'),/已选 0 只/);
});

test('JSON imports reject malformed input and explicitly request non-overwriting merge mode',async()=>{
  const app=toolsUI(async()=>({ok:true,data:{added:1,skipped:1}}));app.AT.state.page='T09';
  const upload=file=>app.events.get('change')({target:{matches:()=>true,files:[file],value:'chosen'}});
  upload({size:5,text:async()=>'{bad'});assert.equal(await app.api.flush(),false);assert.equal(app.requests.length,0);
  const backup={format:'a-trade.watchlist',schemaVersion:1,items:[{symbol:'sh600001'}]};upload({size:100,text:async()=>JSON.stringify(backup)});assert.equal(await app.api.flush(),true);const request=app.requests[0];assert.equal(request[0],'market.watchlist.import');assert.equal(request[1].mode,'merge');assert.equal(request[1].backup.items[0].symbol,'sh600001');
});

test('progress matches exact parameters and cancellation stays idle until explicit retry',async()=>{
  const timers=[];let finish;
  const app=toolsUI((method,payload)=>{
    if(method==='market.analysis.trends')return new Promise(resolve=>finish=resolve);
    if(method==='market.research.status'){const request=app.requests.find(([name])=>name==='market.analysis.trends')[1];return {ok:true,data:{jobs:[{id:1,method:'market.analysis.trends',parameters:{...request,minReturn20:99},stage:'wrong',completed:1,total:2},{id:2,method:'market.analysis.trends',parameters:{...request,refresh:true},stage:'correct job',completed:1,total:2}]}};}
    if(method==='market.research.cancel'){finish({ok:false,error:'cancelled',cancelled:true});return {ok:true,data:{cancelled:true}};}
    return {ok:true,data:{}};
  },{setTimeout:handler=>{timers.push(handler);return timers.length;},clearTimeout(){}});
  app.AT.state.page='T04';app.AT.view('T04');await app.settle();await timers.shift()();assert.match(app.AT.view('T04'),/correct job/);assert.doesNotMatch(app.AT.view('T04'),/>wrong/);
  app.click({analysisAction:'analysis-cancel',jobId:'2'});await tick();app.AT.view('T04');await app.settle();assert.match(app.AT.view('T04'),/已取消本次分析/);assert.equal(app.requests.filter(([method])=>method==='market.analysis.trends').length,1);
  app.click({analysisAction:'refresh'});await tick();assert.equal(app.requests.filter(([method])=>method==='market.analysis.trends').length,2);finish({ok:true,data:{items:[]}});await tick();
});
