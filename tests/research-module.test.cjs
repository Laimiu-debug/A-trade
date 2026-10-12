const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const helpers=require('../preview/research-module.js');
const source=fs.readFileSync(path.join(__dirname,'../preview/research-module.js'),'utf8');
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const config={initialCapital:100000,maxPositions:5,positionFraction:.2,maxParticipationRate:.01,costMultiplier:1,commissionRate:.0003,minCommission:5,stampDutyRate:null,transferFeeRate:.00001,slippageBps:10,stopLossPct:.08,takeProfitPct:.2,partialTakeProfitPct:.12,partialSellFraction:.5,trailingStopPct:.1,maxHoldingDays:60,startDate:'2025-01-01',endDate:'2026-01-01',strategyIds:['momentum'],strategyParams:{momentum:{lookback:20,minReturn:.05}}};
const strategy={id:'momentum',name:'趋势动量',description:'规则定义',rationale:'可检验假设',caveats:'尚未证明有效',parameters:[{key:'lookback',label:'观察期',default:20,min:2,max:252,step:1},{key:'minReturn',label:'最低收益',default:.05,min:0,max:1,step:.01}]};
function task(id='one',status='completed',kind='backtest'){return {id,name:`任务 ${id}`,kind,status,progress:status==='completed'?1:.25,phase:'计算中',symbols:['sh600900'],config};}
function result(){return {kind:'backtest',config,metrics:{totalReturnPct:10,maxDrawdownPct:3,closedTradeCount:2,tradeCount:5,winRatePct:50,annualizedReturnPct:12,benchmarkReturnPct:4,excessReturnPct:6},curve:[{date:'2025-01-02',equity:100000,cash:50000,drawdownPct:0,benchmarkEquity:100000},{date:'2025-01-03',equity:110000,cash:51000,drawdownPct:3,benchmarkEquity:104000}],trades:[{date:'2025-01-03',signalDate:'2025-01-02',symbol:'sh600900',side:'buy',quantity:100,price:10,fee:5,reason:'规则通过'}],decisions:[{date:'2025-01-02',symbol:'sh600900',action:'buy',status:'queued',reason:'晚间信号'}],warnings:['固定股票池存在选择偏差'],pagination:{decisions:{offset:0,total:410,hasMore:true},trades:{offset:0,total:1,hasMore:false}}};}
function harness({desktop=true,tasks=[task()],results={one:result()},fail=false,createFailures=0,portfolioView=false}={}){
  const listeners=new Map(),calls=[],timers=new Map();let timerId=0;
  const document={addEventListener(type,listener){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(listener);},getElementById(){return null;},querySelector(){return null;}};
  const AT={escape,state:{page:'R03'},render(){},navigate(page){this.state.page=page;}};
  const bridge=async(method,payload)=>{calls.push({method,payload});if(fail)return {ok:false,error:'日线服务未配置'};if(method==='research.catalogue')return {ok:true,data:{strategies:[strategy],defaults:config,tasks,limits:{maxSymbols:50,maxCombinations:125}}};if(method==='research.list')return {ok:true,data:{tasks}};if(method==='research.get')return {ok:true,data:{task:tasks.find(t=>t.id===payload.id),result:results[payload.id]||null}};if(method==='research.create'||method==='research.replay'){if(createFailures-->0)return {ok:false,error:'响应超时'};const t=task('new','queued',payload.kind||'backtest');tasks.unshift(t);return {ok:true,data:t};}if(method==='research.control')return {ok:true,data:{tasks:payload.ids.map(id=>{const t=tasks.find(t=>t.id===id);t.status=payload.action==='pause'?'paused':payload.action==='resume'?'running':'stopped';return t;}),outcomes:payload.ids.map(id=>({id,ok:true}))}};if(method==='research.rename'){const t=tasks.find(t=>t.id===payload.id);t.name=payload.name;return {ok:true,data:{task:t}};}if(method==='research.delete'){for(const id of payload.ids){const index=tasks.findIndex(t=>t.id===id);if(index>=0)tasks.splice(index,1);}return {ok:true,data:{tasks:[...tasks],outcomes:payload.ids.map(id=>({id,ok:true}))}};}throw new Error(method);};
  const window=desktop?{atradeDesktop:{business:bridge}}:{};
  if(portfolioView)window.PortfolioWorkbench={configure(){},available:()=>true,currentStep:()=>5,form:()=>'<div>五步研究设置</div>',result:()=>'<div class="pf-account-curve">实际账户结果</div>'};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/research-display.js'),'utf8'),{window,document});
  vm.runInNewContext(source,{window,AT,document,structuredClone,console,queueMicrotask,setTimeout(fn,delay){timers.set(++timerId,{fn,delay});return timerId;},clearTimeout(id){timers.delete(id);},Blob,URL,Set,Map},{filename:'research-module.js'});
  function dispatch(type,target){for(const listener of listeners.get(type)||[])listener({target,preventDefault(){},stopPropagation(){},stopImmediatePropagation(){}});}
  function click(action,id,extra={}){const el={disabled:false,dataset:{rsAction:action,rsId:id,...extra},hasAttribute(){return false;}};dispatch('click',{closest(){return el;}});}
  function field(key,value){dispatch('input',{dataset:{rsField:key},value,matches(){return true;}});}
  return {module:window.ResearchModule,AT,calls,timers,dispatch,click,field,tasks};
}
test('the fifth workflow step renders funded portfolios and distinguishes pool-only previews',async()=>{
 const h=harness({portfolioView:true,tasks:[task('one','completed','portfolio')]});await h.module.refresh();
 assert.match(h.module.render('R03'),/实际账户结果/);assert.doesNotMatch(h.module.render('R03'),/这是股票池筛选任务/);
 const screen=harness({portfolioView:true,tasks:[task('one','completed','poolPreview')]});await screen.module.refresh();
 assert.match(screen.module.render('R03'),/这是股票池筛选任务/);assert.doesNotMatch(screen.module.render('R03'),/实际账户结果/);
});

test('percentage input units round trip, including nested strategy and fee ratios',()=>{
  assert.equal(helpers.displayValue('stopLossPct',.08),8);assert.equal(helpers.storedValue('stopLossPct',8),.08);
  assert.equal(helpers.displayValue('strategyParams.momentum.minReturn',.05),5);assert.equal(helpers.storedValue('commissionRate',.03),.0003);
  assert.equal(helpers.displayValue('stampDutyRate',null),'');assert.equal(helpers.storedValue('maxHoldingDays',20),20);
  assert.equal(helpers.displayValue('maxParticipationRate',.01),1);
});
test('symbol parsing is strict, deduplicated, and capped without silently dropping bad codes',()=>{
  assert.deepEqual(helpers.parseSymbols('600900，sz000001 920001;600900'),['sh600900','sz000001','bj920001']);
  assert.throws(()=>helpers.parseSymbols('600900 wrong'),/无效/);assert.throws(()=>helpers.parseSymbols(''),/至少/);assert.throws(()=>helpers.parseSymbols('600900 000001',1),/最多/);
});
test('parameter grids reject malformed values, duplicate axes, singletons and overlarge experiments',()=>{
  assert.deepEqual(helpers.parseAxis('stopLossPct','8, 4，6 4'),{path:'stopLossPct',values:[.04,.06,.08]});
  assert.throws(()=>helpers.parseAxis('stopLossPct','4,nope'),/必须全部/);assert.throws(()=>helpers.parseAxis('stopLossPct','4,4'),/不同取值/);
  assert.throws(()=>helpers.validateAxes([{path:'x',values:[1,2]},{path:'x',values:[2,3]}]),/不能重复/);
  assert.throws(()=>helpers.validateAxes([{path:'x',values:Array.from({length:126},(_,i)=>i)}]),/超过/);
  assert.equal(helpers.validateAxes([{path:'x',values:[1,2,3]},{path:'y',values:[1,2,3]}]),9);
});
test('missing curves stay missing and break graph lines instead of creating zero return points',()=>{
  const rows=helpers.curveRows({curve:[{date:'2026-01-01',equity:100},{date:'2026-01-02',equity:null},{date:'2026-01-03',equity:102},{date:'invalid',equity:10}]});
  assert.equal(rows.length,3);assert.equal(rows[1].equity,null);assert.equal(rows[0].drawdownPct,null);
  assert.equal(helpers.pathFor(rows,'equity',i=>i,v=>v),'M0.00,100.00 M2.00,102.00');
  assert.equal(helpers.progressPercent(.25),25);assert.equal(helpers.progressPercent(null),0);assert.equal(helpers.progressPercent(7),100);
});
test('browser research routes expose a genuine empty state without fabricated financial results',()=>{
  const h=harness({desktop:false});for(const page of ['R01','R03','R04','R05','R06','R07','R08','R09','G02','G03']){const html=h.module.render(page);assert.match(html,/需要桌面研究服务/);assert.doesNotMatch(html,/42\.80|8\.36|rs-equity|rs-cell-pass/);}assert.equal(h.module.handles('A02'),false);assert.equal(h.module.render('A02'),null);assert.equal(h.calls.length,0);
});
test('real service errors remain visible instead of falling back to demonstration data',async()=>{
  const h=harness({fail:true});await h.module.refresh();assert.match(h.module.render('R03'),/日线服务未配置/);assert.doesNotMatch(h.module.render('R04'),/42\.80/);
});
test('real catalogue and account curves render with warnings and no placeholder performance',async()=>{
  const h=harness();await h.module.refresh();assert.match(h.module.render('R01'),/尚未证明有效/);assert.match(h.module.render('R04'),/10\.00%/);assert.match(h.module.render('R04'),/rs-equity/);assert.match(h.module.render('R04'),/固定股票池存在选择偏差/);
  const html=h.module.render('R03');assert.match(html,/data-rs-field="stampDutyRate" value=""[^>]*placeholder="按成交日期自动"/);assert.doesNotMatch(html,/data-rs-field="stampDutyRate"[^>]* required/);
  assert.match(h.module.render('R06'),/410/);assert.match(h.module.render('R06'),/下一页/);assert.match(h.module.render('R09'),/当前加载页/);
});
test('service pagination offsets are requested and cached separately from chart rendering',async()=>{
  const h=harness();await h.module.refresh();h.click('path-next');await new Promise(setImmediate);
  const req=h.calls.filter(c=>c.method==='research.get').at(-1);assert.equal(req.payload.decisionOffset,200);assert.equal(req.payload.tradeOffset,200);assert.equal(req.payload.pageSize,200);
  assert.match(h.module.render('R06'),/第 2 页/);
});

test('fold account selector pages the chosen nested execution ledger only',async()=>{
 const first=result(),second=result();first.pagination.path='folds.0.testing.fixed';second.pagination.path='folds.1.testing.equalWeight';second.trades[0].symbol='sz000001';
 const h=harness({tasks:[task('one','completed','walkForward')],results:{one:{kind:'walkForward',folds:[{testing:{fixed:first}},{testing:{equalWeight:second}}]}}});await h.module.refresh();
 assert.match(h.module.render('R06'),/第 2 折 · 等权组合/);
 h.dispatch('change',{value:'folds.1.testing.equalWeight',dataset:{},hasAttribute:name=>name==='data-rs-result-path'});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.calls.filter(c=>c.method==='research.get').at(-1).payload.resultPath,'folds.1.testing.equalWeight');assert.match(h.module.render('R06'),/sz000001/);
 h.click('path-next');await new Promise(resolve=>setImmediate(resolve));const request=h.calls.filter(c=>c.method==='research.get').at(-1).payload;assert.equal(request.resultPath,'folds.1.testing.equalWeight');assert.equal(request.decisionOffset,200);assert.equal(request.tradeOffset,200);
});
test('controls issue backend state changes and never increment progress on a local timer',async()=>{
  const h=harness({tasks:[task('one','running')]});await h.module.refresh();h.click('control','one',{rsControl:'pause'});await h.module.flush();assert.equal(h.tasks[0].status,'paused');assert.equal(h.tasks[0].progress,.25);assert.deepEqual(h.calls.filter(c=>c.method==='research.control').map(c=>JSON.parse(JSON.stringify(c.payload))),[{ids:['one'],action:'pause'}]);
  h.AT.state.page='A02';await h.module.refresh();assert.ok([...h.timers.values()].some(timer=>timer.delay===10000));
});
test('plateau holdout is visibly tied to recommendation and candidate replay uses frozen-data API',async()=>{
  const plateau={kind:'plateau',config,candidates:[{id:'c1',params:{stopLossPct:.04},config,passes:false,blocks:[{index:0,passes:false,metrics:{closedTradeCount:0},failures:['样本不足']}],failures:['样本不足']}],regions:[],recommendation:null,validation:null,trialCount:9,completedTrials:3,evaluatedParameterTrialCount:1,split:{blocks:[{startDate:'2025-01-01',endDate:'2025-09-01'}],training:{startDate:'2025-01-01',endDate:'2025-09-01'},holdout:{startDate:'2025-09-02',endDate:'2026-01-01',days:80}},warnings:[],methodology:{finalHoldoutUsedForSelection:false}};
  const h=harness({tasks:[task('one','completed','plateau')],results:{one:plateau}});await h.module.refresh();const html=h.module.render('R05');assert.match(html,/没有符合条件的稳定区域/);assert.match(html,/1 \/ 9/);assert.match(html,/独立留出只属于训练区预先推荐/);assert.match(html,/尚无独立验收结果/);h.click('candidate-run','c1');await h.module.flush();assert.ok(h.calls.some(call=>call.method==='research.replay'&&call.payload.id==='one'&&call.payload.candidateId==='c1'));assert.equal(h.AT.state.page,'R04');
});
test('research creation converts percentage form values and preserves auto tax',async()=>{
  const h=harness({tasks:[]});await h.module.refresh();h.field('symbols','600900');h.field('stopLossPct','6');h.field('stampDutyRate','');h.dispatch('submit',{dataset:{rsForm:'backtest'},matches(){return true;},querySelectorAll(){return [];}});await h.module.flush();const req=h.calls.find(c=>c.method==='research.create');assert.ok(req);assert.equal(req.payload.config.stopLossPct,.06);assert.equal(req.payload.config.stampDutyRate,null);assert.deepEqual(JSON.parse(JSON.stringify(req.payload.symbols)),['sh600900']);assert.equal(h.AT.state.page,'G03');
});
test('an uncertain create retry reuses its request id, while an edited configuration gets a fresh id',async()=>{
  const h=harness({tasks:[],createFailures:2});await h.module.refresh();h.field('symbols','600900');const submit=()=>h.dispatch('submit',{dataset:{rsForm:'backtest'},matches(){return true;},querySelectorAll(){return [];}});
  submit();await h.module.flush();submit();await h.module.flush();h.field('stopLossPct','7');submit();await h.module.flush();const calls=h.calls.filter(c=>c.method==='research.create');assert.ok(calls[0].payload.requestId);assert.equal(calls[0].payload.requestId,calls[1].payload.requestId);assert.notEqual(calls[1].payload.requestId,calls[2].payload.requestId);
});
test('an incomplete or insufficient-data plateau cannot offer a runnable training replay',async()=>{
  const base={kind:'plateau',candidates:[{id:'c1',config,params:{stopLossPct:.04},blocks:[{index:0}]}],axes:[{path:'stopLossPct',values:[.04,.06,.08]}],split:{blocks:[{startDate:'2025-01-01',endDate:'2025-09-01'}]},trialCount:3,warnings:[]};
  for(const [status,r] of [['completed',{...base,split:null}],['completed',{...base,candidates:[{...base.candidates[0],blocks:[]}]}],['running',base]]){
    const h=harness({tasks:[task('one',status,'plateau')],results:{one:r}});await h.module.refresh();assert.match(h.module.render('R05'),/data-rs-action="candidate-run"[^>]* disabled/);
    h.click('candidate-run','c1');await h.module.flush();assert.equal(h.calls.some(call=>call.method==='research.replay'),false,'non-runnable candidates cannot bypass the disabled control');
  }
});
test('Chinese path filtering searches translated reasons and UTC timestamps render in Taipei time',async()=>{
  const t=task();t.createdAt='2026-10-11T00:10:00Z';t.updatedAt='2026-10-11T01:10:00Z';const r=result();r.decisions.push({date:'2025-01-03',symbol:'sh600900',action:'sell',status:'queued',reason:'stopLoss'});const h=harness({tasks:[t],results:{one:r}});await h.module.refresh();h.field('pathQuery','止损');const html=h.module.render('R06');assert.match(html,/达到止损阈值/);assert.match(html,/待次日执行/);assert.doesNotMatch(html,/晚间信号/);assert.match(h.module.render('R04'),/08:10:00/);assert.match(h.module.render('R04'),/台北时间/);
});
test('server supplied names and warnings are escaped, including task action attributes',async()=>{
  const evil=task('x" onclick="alert(1)');evil.name='<img src=x onerror=alert(1)>';const r=result();r.warnings=['<script>alert(1)</script>'];const h=harness({tasks:[evil],results:{[evil.id]:r}});await h.module.refresh();for(const page of ['G02','R04','R09']){const html=h.module.render(page);assert.doesNotMatch(html,/<script>|<img src=x|data-rs-id="x" onclick/);assert.match(html,/&lt;/);}
});

test('task center can search, rename and explicitly delete a saved task',async()=>{
 const h=harness({tasks:[task('one'),task('two')]});await h.module.refresh();assert.match(h.module.render('G02'),/搜索任务名称/);assert.match(h.module.render('G02'),/重命名/);assert.match(h.module.render('G02'),/新增研究/);
 h.click('task-rename','one');h.dispatch('input',{value:'改名研究',hasAttribute:name=>name==='data-rs-edit-name'});h.click('task-rename-save');await h.module.flush();assert.equal(h.tasks.find(t=>t.id==='one').name,'改名研究');
 h.dispatch('input',{value:'改名研究',hasAttribute:name=>name==='data-rs-task-query'});const html=h.module.render('G02');assert.match(html,/改名研究/);assert.doesNotMatch(html,/任务 two/);
 h.click('task-delete','one');assert.match(h.module.render('G02'),/确认删除/);assert.equal(h.calls.some(c=>c.method==='research.delete'),false);h.click('task-delete-confirm');await h.module.flush();assert.ok(h.calls.some(c=>c.method==='research.delete'));assert.equal(h.tasks.some(t=>t.id==='one'),false);
});
