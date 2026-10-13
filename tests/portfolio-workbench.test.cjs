const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const helpers=require('../preview/portfolio-workbench.js');
const source=fs.readFileSync(path.join(__dirname,'../preview/portfolio-workbench.js'),'utf8');
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// The real Python catalogue and engine output prevent UI fixtures drifting away
// from the public API contract. All prices here are isolated test data.
const generated=spawnSync('python',['-c',`
import json,datetime,math
from service.portfolio_config import CONFIG_VERSION,catalogue,default_config,normalize_selection,normalize_config,pool_style_catalogue
from service.portfolio_engine import PortfolioEngine
bars=[]
for i in range(100):
 d=(datetime.date(2024,1,1)+datetime.timedelta(days=i)).isoformat()
 p=20+i*.05+math.sin(i/5)*.1
 bars.append(dict(date=d,open=p,high=p+.1,low=p-.1,close=p,volume=2000000,amount=40000000))
config=normalize_config(dict(strategies=[dict(id='momentum',kind='momentum',params=dict(lookback=5,trendWindow=8,minReturn=0))],allocation=dict(mode='single',strategyIds=['momentum']),universe=dict(minListingDays=0,minAmount20=0),startDate=bars[40]['date'],endDate=bars[-1]['date'],topN=2,maxPositions=2,maxStockWeight=.4))
coverage=dict(discoveredCount=136,selectedCount=136,loadedCount=136,failureCount=0)
dataset=dict(securities=[dict(symbol='sh600001',bars=bars),dict(symbol='sz000001',bars=bars)],calendar=[b['date'] for b in bars],universeCoverage=coverage)
engine=PortfolioEngine(dataset,config)
while not engine.step(days=30): pass
print(json.dumps(dict(definition=dict(version=CONFIG_VERSION,strategies=catalogue(),poolStyles=pool_style_catalogue(),defaults=default_config(),selection=normalize_selection()),result=engine.result()),ensure_ascii=False))
`],{encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'},maxBuffer:8*1024*1024,cwd:path.resolve(__dirname,'..')});
assert.equal(generated.status,0,generated.stderr);
const fixture=JSON.parse(generated.stdout);
fixture.definition.defaults.startDate='2024-02-10';fixture.definition.defaults.endDate='2024-04-09';

test('execution capacity failures and daily-only assumptions are visible in account reports',()=>{
 const h=harness();
 const invalid=structuredClone(fixture.result);
 invalid.quality.openingCapacityVerified=false;
 invalid.quality.executionCapacityIssues=[{date:'2024-02-11',symbol:'sh600001',filledQuantity:1000,observedDailyVolume:100}];
 let html=h.module.result(invalid);
 assert.match(html,/data-pf-execution-evidence/);assert.match(html,/账户路径不参与平原选择或认证/);assert.match(html,/模拟成交股数/);assert.match(html,/1,000/);
 invalid.quality.executionCapacityIssues=[];html=h.module.result(invalid);
 assert.match(html,/缺少开盘量证据/);assert.match(html,/下一交易日开盘才可支用/);
});

test('single scan submits explicit relative stability and exposure thresholds in backend units',()=>{
 const h=harness();h.module.selectStrategy('momentum');h.submit();
 const fixed=h.calls.find(row=>row[0]==='create')[1];h.module.startPlateau({...fixed,id:'fixed-integrity',status:'completed'});
 h.field('scanAxes.0.path','strategies.momentum.params.lookback','text');h.field('scanAxes.0.text','15,20,25','text');
 h.field('experiment.maxNeighborhoodRelativeStd','75');h.field('experiment.minNeighborhoodReturnRetention','60');h.field('experiment.minInvestedDays','4');h.submit();
 const request=h.calls.filter(row=>row[0]==='create').at(-1)[1];
 assert.equal(request.experiment.maxNeighborhoodRelativeStd,.75);assert.equal(request.experiment.minNeighborhoodReturnRetention,.6);assert.equal(request.experiment.minInvestedDays,4);
 const html=h.module.plateau();assert.match(html,/最少不同交易路径/);assert.match(html,/中位收益 \/ 邻域峰值/);
});

test('unidentifiable scans and insufficient long-term evidence explain their failed criteria',()=>{
 const h=harness();
 const single={kind:'singlePlateau',status:'completed',config:fixture.result.config,experiment:{},axes:[{path:'topN',values:[1,2,3]}],
   parameterIdentifiability:{distinctBehaviorCount:1,inactiveAxes:['topN']},candidates:[{candidateId:'c001',params:{topN:1},config:fixture.result.config,
     metrics:{totalReturnPct:2,maxDrawdownPct:0,closedTradeCount:3},neighborhood:{passed:false,size:3,distinctBehaviorCount:1,failures:['unidentifiable_parameter_behavior','inactive_parameter_axes']}}]};
 let html=h.module.result(single);assert.match(html,/data-pf-identifiability/);assert.match(html,/未生效参数/);assert.match(html,/参数变化尚不可识别/);assert.doesNotMatch(html,/pf-candidate is-stable/);
 html=h.module.result({kind:'systemPlateau',experiment:{axes:[]},folds:[],plateau:{certified:false,recommendedRanges:[],
   reasons:['insufficient_oos_trading_days','insufficient_oos_calendar_span'],oosCoverage:{tradingDays:45,minTradingDays:504,calendarSpanDays:61,minCalendarSpanDays:730,coveredYears:[]}}});
 assert.match(html,/data-pf-oos-coverage/);assert.match(html,/45 个交易日 \/ 门槛 504/);assert.match(html,/跨年标签不能替代长期证据/);
});

test('increasing required years raises the persisted trading-day and calendar floors',()=>{
 const live={'minOosTradingDays':{value:'504'},'minOosCalendarDays':{value:'730'}};
 const h=harness(new Map(),{advanced:true,querySelector:selector=>live[selector.match(/experiment\.(\w+)/)?.[1]]});h.field('kind','systemPlateau','text');h.field('experiment.minOosYears','3');
 const html=h.module.form();
 assert.match(html,/data-pf-field="experiment.minOosTradingDays"[^>]*value="756"/);
 assert.match(html,/data-pf-field="experiment.minOosCalendarDays"[^>]*value="1095"/);
 assert.equal(live.minOosTradingDays.value,756);assert.equal(live.minOosCalendarDays.value,1095);
 for(const [key,text]of h.storage){const saved=JSON.parse(text);if(saved.draft?.experiment){delete saved.draft.experiment.minOosTradingDays;delete saved.draft.experiment.minOosCalendarDays;h.storage.set(key,JSON.stringify(saved));}}
 const reopened=harness(h.storage,{advanced:true});reopened.click('advanced-plateau');
 const restored=reopened.module.plateau();
 assert.match(restored,/data-pf-field="experiment.minOosTradingDays"[^>]*value="756"/);
 assert.match(restored,/data-pf-field="experiment.minOosCalendarDays"[^>]*value="1095"/);
});
function harness(storage=new Map(),options={}){
 const listeners=new Map(),calls=[],errors=[];
 const document={addEventListener(type,fn){const handlers=listeners.get(type)||[];handlers.push(fn);listeners.set(type,handlers);},querySelector:selector=>options.querySelector?.(selector)||null};
 const window={},localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/research-display.js'),'utf8'),{window,document});
 vm.runInNewContext(source,{window,AT:{escape},document,localStorage,structuredClone,console,setTimeout,clearTimeout},{filename:'portfolio-workbench.js'});
 const hooks={update(value){calls.push(['update',JSON.parse(JSON.stringify(value))]);},refresh(){calls.push(['refresh']);},create(value){calls.push(['create',JSON.parse(JSON.stringify(value))]);},error(error){errors.push(error.message);},feedback(value){calls.push(['feedback',value]);},dailyPage(value,path){calls.push(['page',value,path]);},rotationPage(value,path){calls.push(['rotation',value,path]);}};
 if(options.dailyPage)hooks.dailyPage=options.dailyPage;
 if(options.activeTaskId)hooks.activeTaskId=options.activeTaskId;
 if(options.freeze)hooks.freeze=options.freeze;
 if(options.replayCandidate)hooks.replayCandidate=options.replayCandidate;
 hooks.screenCreate=value=>{calls.push(['screenCreate',JSON.parse(JSON.stringify(value))]);return Promise.resolve({id:'screen-job'});};
 if(options.previewSettings)hooks.previewSettings=value=>{calls.push(['previewSettings',JSON.parse(JSON.stringify(value))]);return options.previewSettings(value);};
 window.PortfolioWorkbench.configure(structuredClone(fixture.definition),hooks);
 if(options.advanced)window.PortfolioWorkbench.advanced();
 const dispatch=(type,target,properties={})=>{for(const fn of listeners.get(type)||[])fn({...properties,target,preventDefault(){},stopImmediatePropagation(){}});};
 const field=(path,value,type='number')=>dispatch('input',{dataset:{pfField:path,pfType:type==='number'?undefined:type},value,checked:value,matches:()=>true});
 const click=(action,extra={})=>dispatch('click',{closest:()=>({dataset:{pfAction:action,pfOffset:'200',...extra},disabled:false})});
 const submit=()=>dispatch('submit',{matches:()=>true,querySelectorAll:()=>[]});
 return {module:window.PortfolioWorkbench,field,click,submit,calls,errors,storage,dispatch};
}

test('single workflow separates basic eligibility from style funnel and keeps advanced pool conditions folded',()=>{
 const h=harness(),html=h.module.form();
 assert.equal(h.module.currentStep(),1);assert.match(html,/aria-label="策略与回测流程"/);
 assert.match(html,/data-pf-step-panel="1" ><header/);assert.match(html,/data-pf-step-panel="2" hidden/);
 assert.equal((html.match(/class="pf-pool-card"/g)||[]).length,4);
 assert.match(html,/固定策略参数/);assert.doesNotMatch(html,/data-pf-detail="editor:single-funnel" open/);
 h.click('workflow-step',{pfStep:'3'});assert.equal(h.module.currentStep(),3);
 assert.match(h.module.form(),/data-pf-step-panel="3" >/);
 assert.match(h.module.form(),/data-pf-detail="editor:costs" ><summary>交易成本与权益处理/);
 h.click('workflow-step',{pfStep:'4'});assert.match(h.module.form(),/最终生效配置/);
 assert.equal(harness(h.storage).module.currentStep(),4);
});

test('Enter advances configuration instead of creating a task, and results hide the editor fields',()=>{
 const h=harness(),target={closest:()=>({}),matches:()=>false};
 h.dispatch('keydown',target,{key:'Enter'});assert.equal(h.module.currentStep(),2);
 h.dispatch('keydown',target,{key:'Enter'});assert.equal(h.module.currentStep(),3);
 assert.equal(h.calls.some(row=>row[0]==='create'),false);
 h.module.setStep(5);const html=h.module.form();assert.doesNotMatch(html,/data-pf-field|data-pf-form/);
 assert.match(html,/返回前四步可调整设置/);assert.match(html,/aria-current="step"/);
});

test('strategy presets preserve parameters and pool, escape names, and support default reset',()=>{
 const h=harness(new Map(),{querySelector:selector=>selector.includes('preset-name')?{value:'<b>我的趋势预设</b>'}:null});
 h.module.selectStrategy('momentum');
 h.field('params.momentum.lookback','17');h.field('pools.momentum','volume','text');
 h.click('save-strategy-preset',{pfStrategy:'momentum'});
 const presets=JSON.parse(h.storage.get('atrade.strategy-presets.v1')).momentum;
 assert.equal(presets[0].params.lookback,17);assert.equal(presets[0].pool,'volume');
 assert.match(h.module.form(),/&lt;b&gt;我的趋势预设&lt;\/b&gt;/);
 h.click('reset-strategy',{pfStrategy:'momentum'});
 h.submit();let request=h.calls.find(row=>row[0]==='create')[1];
 assert.equal(request.config.strategies.find(s=>s.id==='momentum').params.lookback,fixture.definition.strategies.find(s=>s.id==='momentum').parameters.find(p=>p.key==='lookback').default);
 h.dispatch('change',{dataset:{pfPresetSelect:'momentum'},value:presets[0].id});h.submit();
 request=h.calls.filter(row=>row[0]==='create').at(-1)[1];assert.equal(request.config.strategies.find(s=>s.id==='momentum').params.lookback,17);
 assert.equal(request.config.strategies.find(s=>s.id==='momentum').pool,'volume');
 h.click('delete-strategy-preset',{pfStrategy:'momentum'});assert.deepEqual(JSON.parse(h.storage.get('atrade.strategy-presets.v1')).momentum,[]);
});

test('pool screening is independent of unfinished capital and allocation settings',async()=>{
 const h=harness();h.field('config.initialCapital','');h.field('config.allocation.mode','single','text');
 for(const s of fixture.definition.strategies)h.field('participates.'+s.id,false,'boolean');
 h.click('screen-preview');await new Promise(setImmediate);
 const request=h.calls.find(row=>row[0]==='screenCreate')[1];
 assert.equal(request.kind,'poolPreview');assert.equal(request.config.initialCapital,undefined);
 assert.equal(request.config.allocation,undefined);assert.equal(request.config.strategies,undefined);assert.equal(request.selection.mode,'all');assert.deepEqual(h.errors,[]);
});

test('creation uses backend effective configuration including generated name and rejects failed validation',async()=>{
 const h=harness(new Map(),{previewSettings:async request=>({...request,name:'组合回测 · 后端自动名称',config:{...request.config,topN:7},execution:{datesResolvedAfterSnapshot:true}})});
 h.click('workflow-step',{pfStep:'4'});await h.module.reviewSettings();
 assert.match(h.module.form(),/配置校验通过/);assert.match(h.module.form(),/后端自动名称/);
 h.submit();await new Promise(setImmediate);
 const request=h.calls.find(row=>row[0]==='create')[1];assert.equal(request.name,'组合回测 · 后端自动名称');assert.equal(request.config.topN,7);
 const rejected=harness(new Map(),{previewSettings:async()=>{throw new Error('后端校验：持仓设置无效');}});
 rejected.submit();await new Promise(setImmediate);
 assert.equal(rejected.calls.some(row=>row[0]==='create'),false);assert.match(rejected.errors[0],/后端校验/);
 assert.match(rejected.module.form(),/持仓设置无效/);
});

test('changed settings cannot submit an earlier pending effective configuration',async()=>{
 let resolve;
 const h=harness(new Map(),{previewSettings:request=>new Promise(done=>{resolve=()=>done({...request,name:'旧配置',execution:{}});})});
 h.submit();await new Promise(setImmediate);h.field('config.topN','8');resolve();await new Promise(setImmediate);
 assert.equal(h.calls.some(row=>row[0]==='create'),false);assert.match(h.errors[0],/校验期间发生变化/);
 // Finish the scheduled current-settings read so this harness leaves no timer.
 const pending=h.module.reviewSettings();await new Promise(setImmediate);resolve();await pending;
 assert.doesNotMatch(h.module.form(),/正在按后端规则校验当前设置/);
});

test('ratio inputs, negative flip threshold and fee blanks preserve API units',()=>{
 assert.equal(helpers.display('config.stopLossPct',.08),8);
 assert.equal(helpers.stored('config.stopLossPct','6'),.06);
 assert.equal(helpers.display('params.forcePurpleToYellow.minDominance',-1),-100);
 assert.equal(helpers.stored('config.commissionRate','.03'),.0003);
 assert.equal(helpers.stored('config.stampDutyRate',''),null);
 assert.equal(helpers.display('config.stampDutyRate',null),'');
 assert.equal(helpers.stored('study.minRankCorrelation','.3'),.3);
 assert.equal(helpers.stored('config.slippageBps','10'),10);
});

test('full and explicit universes have no hard stock cap; symbols remain strict',()=>{
 const many=Array.from({length:501},(_,i)=>String(600000+i)).join(',');
 assert.equal(helpers.symbols(many).length,501);
 assert.deepEqual(helpers.symbols('600900，sz000001 920001;600900'),['sh600900','sz000001','bj920001']);
 assert.throws(()=>helpers.symbols('600900 wrong'),/无效/);
 const h=harness();h.field('symbols',many,'text');h.submit();
 let request=h.calls.find(c=>c[0]==='create')[1];assert.equal(request.selection.mode,'all');assert.deepEqual(request.selection.symbols,[]);
 h.field('selection.mode','explicit','text');h.submit();request=h.calls.filter(c=>c[0]==='create').at(-1)[1];assert.equal(request.selection.symbols.length,501);
 assert.match(h.module.form(),/入池资格由行情数据是否满足条件决定/);
});

test('independent portfolio fields and strategy parameters reach the actual contract',()=>{
 const h=harness(new Map(),{advanced:true});h.field('config.topN','7');h.field('config.maxPositions','13');h.field('config.maxStockWeight','12');h.field('config.lookbackDays','81');h.field('config.rebalanceMode','rank','text');h.field('config.stopLossPct','3');h.field('config.stampDutyRate','');h.field('params.momentum.lookback','25');h.field('params.momentum.minReturn','7');h.field('pools.momentum','volume','text');h.submit();
 assert.deepEqual(h.errors,[]);const {config,selection}=h.calls.find(c=>c[0]==='create')[1];
 assert.equal(config.topN,7);assert.equal(config.maxPositions,13);assert.equal(config.maxStockWeight,.12);assert.equal(config.rebalanceMode,'rank');assert.equal(config.stopLossPct,.03);assert.equal(config.stampDutyRate,null);assert.equal(selection.lookbackDays,81);
 assert.equal(config.startDate,null);assert.equal(config.endDate,null);
 const m=config.strategies.find(s=>s.kind==='momentum');assert.equal(m.params.lookback,25);assert.equal(m.params.minReturn,.07);assert.equal(m.pool,'volume');
});

test('explicit combinations retain unequal child weights and can alone receive allocation',()=>{
 const h=harness(new Map(),{advanced:true});for(const s of fixture.definition.strategies){h.field('enabled.'+s.id,['forceGoldenCross','momentum'].includes(s.id),'boolean');h.field('participates.'+s.id,false,'boolean');}
 h.field('combinations.weighted.enabled',true,'boolean');h.field('combinations.weighted.threshold','.3');h.field('weights.forceGoldenCross','2');h.field('weights.momentum','7');h.field('config.allocation.mode','single','text');h.submit();
 assert.deepEqual(h.errors,[]);const config=h.calls.find(c=>c[0]==='create')[1].config;
 assert.deepEqual(config.allocation.strategyIds,['combo_weighted']);
 assert.deepEqual(config.combinations[0],{id:'combo_weighted',method:'weighted',normalization:'rank',threshold:.3,children:config.strategies.map(s=>({id:s.id,weight:s.id==='momentum'?7:2}))});
});

test('impossible selections and invalid explicit dates fail before create',()=>{
 const h=harness(new Map(),{advanced:true});h.field('config.allocation.mode','single','text');h.submit();assert.match(h.errors.pop(),/只能勾选一个/);
 h.field('config.allocation.mode','equal','text');h.field('periodMode','dates','text');h.field('config.startDate','2026-01-02','text');h.field('config.endDate','2026-01-01','text');h.submit();assert.match(h.errors.pop(),/有效的起止日期/);
 h.field('periodMode','recent','text');h.field('kind','comparison','text');h.submit();assert.match(h.errors.pop(),/明确日期/);
 assert.equal(h.calls.some(c=>c[0]==='create'),false);
});

test('strategy card resets an old combination-only draft into a valid single selection',()=>{
 const h=harness();h.field('participates.momentum',false,'boolean');h.field('combinations.weighted.enabled',true,'boolean');h.field('config.allocation.mode','single','text');
 h.module.selectStrategy('momentum');h.submit();assert.deepEqual(h.errors,[]);
 const config=h.calls.find(c=>c[0]==='create')[1].config;assert.equal(config.strategies.length,1);assert.deepEqual(config.combinations,[]);assert.deepEqual(config.allocation.strategyIds,['momentum']);
});

test('configuration survives reopening while corrupt or old local drafts are ignored',()=>{
 const h=harness();h.field('config.maxPositions','19');h.field('name','我的组合','text');h.click('save');
 const reopened=harness(h.storage);assert.match(reopened.module.form(),/data-pf-field="config.maxPositions"[^>]*value="19"/);assert.match(reopened.module.form(),/maxlength="100"[^>]*value="我的组合"/);
 const broken=new Map([['atrade.portfolio-editor.v1',JSON.stringify({version:fixture.definition.version,draft:{selection:{markets:['sh']}}})]]);
 assert.match(harness(broken).module.form(),/研究区间与基础资格/);
});

test('real engine schema renders candidates, actual weights, costs, coverage and regime boundaries',()=>{
 const h=harness(),html=h.module.result(fixture.result);
 assert.match(html,/sh600001/);assert.match(html,/momentum/);assert.match(html,/读入 136 只/);
 assert.match(html,/实际持仓/);assert.match(html,/预算用于入场分配/);assert.match(html,/完整持仓波段胜率/);assert.match(html,/成本 \/ 初始资金/);
 assert.match(html,/pf-account-curve/);assert.match(html,/pf-drawdown-curve/);assert.match(html,/未知（缺少所选真实指数/);
 assert.match(html,/条件年化不是一条连续交易策略/);
});

test('continuous capital settings and imported historical sources reach the saved request',()=>{
 const h=harness();h.field('kind','walkForward','text');h.field('periodMode','dates','text');h.field('config.startDate','2024-01-01','text');h.field('config.endDate','2024-12-31','text');h.field('selection.source','history','text');h.field('selection.sourceId','immutable-fixture','text');h.field('config.universe.strictHistory',true,'boolean');h.field('experiment.capitalMode','continuous','text');h.field('config.rightsPolicy','subscribe','text');h.submit();
 assert.deepEqual(h.errors,[]);const request=h.calls.find(c=>c[0]==='create')[1];assert.equal(request.selection.sourceId,'immutable-fixture');assert.equal(request.config.universe.strictHistory,true);assert.equal(request.config.rightsPolicy,'subscribe');assert.equal(request.experiment.capitalMode,'continuous');assert.match(h.module.form(),/JSON \/ JSONL/);
});

test('equity actions and repeated holdout warnings are visible without suggesting certification',()=>{
 const h=harness(),r=structuredClone(fixture.result);r.validationAudit={status:'developmentSample',reusedIntervals:[]};r.actionLedger=[{date:'2024-03-01',symbol:'sh600001',stage:'subscriptionPaid',amount:500}];r.preExRights={pending:{amount:500}};
 const html=h.module.result(r);assert.match(html,/现为开发样本/);assert.match(html,/除权前认购付款/);assert.match(html,/preExRights/);assert.match(html,/公司行动与待到账权益/);
});

test('pool parameters are visible and edit versus copy preserves the original settings',()=>{
 const h=harness();assert.match(h.module.form(),/分层筛选条件/);assert.match(h.module.form(),/按当前设置筛选股票池/);
 const request={id:'pending',name:'可编辑研究',kind:'portfolio',configEditable:true,config:fixture.result.config,selection:fixture.definition.selection};
 h.module.editTask(request,'edit');h.field('config.poolSettings.volume.minRatio','2.4');h.submit();const updated=h.calls.find(row=>row[0]==='update')[1];assert.equal(updated.id,'pending');assert.equal(updated.config.poolSettings.volume.minRatio,2.4);
 h.module.editTask({...request,configEditable:false},'copy');h.submit();const copy=h.calls.find(row=>row[0]==='create')[1];assert.equal(copy.name,'可编辑研究 · 副本');assert.equal(copy.config.topN,request.config.topN);assert.match(h.module.form(),/原报告保留/);
});

test('daily stock names and actual operations are readable instead of requiring raw JSON',()=>{
 const h=harness(),r=structuredClone(fixture.result);r.securityNames={sh600001:'隔离样本甲'};r.daily[0].targetWeights.sz000001=0;r.daily[0].fills=[{symbol:'sh600001',side:'buy',date:r.daily[0].date,signalDate:'2024-01-01',quantity:100,price:10,fee:5,reason:'scheduledRebalance'}];
 const html=h.module.result(r);assert.match(html,/隔离样本甲/);assert.match(html,/已模拟成交/);assert.match(html,/按调仓周期调整排名与仓位/);assert.match(html,/查看当日完整操作路径/);assert.match(html,/data-research-stock-date/);assert.match(html,/候选观察，尚非买入目标/);
});

test('old paged reports do not claim zero daily trades from incomplete loaded audit records',()=>{
 const h=harness(),r=structuredClone(fixture.result);for(const day of r.daily){delete day.fills;delete day.activities;}r.trades=[];r.decisions=[];r.pagination={trades:{total:300},decisions:{total:1000}};
 const html=h.module.result(r);assert.match(html,/旧报告当前页可能未包含全部当日操作/);assert.doesNotMatch(html,/本日模拟买入 0 笔/);
});

test('daily backend pages stay explicit and next page calls the parent loader',async()=>{
 const h=harness(),r=structuredClone(fixture.result);r.daily=r.daily.slice(0,20);r.pagination={daily:{offset:0,total:r.curve.length,pageSize:20,hasMore:true}};
 assert.match(h.module.result(r),new RegExp('共 '+r.curve.length+' 日'));h.click('next-page',{pfOffset:'20'});await new Promise(setImmediate);assert.deepEqual(h.calls.find(row=>row[0]==='page'),['page',20,'']);
});

const selectedDetail=html=>html.slice(html.indexOf('data-pf-day-detail'),html.indexOf('<h2>收盘选股依据'));

test('choosing a curve day displays its fills, holdings and plans immediately below the chart',()=>{
 const h=harness(),r=structuredClone(fixture.result),date=r.daily[1].date;
 h.module.result(r);h.click('select-day',{pfDate:date});const html=h.module.result(r),detail=selectedDetail(html);
 assert.match(html,/role="slider"[^>]*aria-valuenow="1"/);assert.match(detail,new RegExp('data-pf-date="'+date+'"'));
 assert.match(detail,/今日已成交/);assert.match(detail,/收盘持仓（实际持仓）/);assert.match(detail,/下一交易日计划/);assert.match(detail,/股 × /);assert.match(detail,/本日买入后持有/);
 assert.ok(html.indexOf('data-pf-day-detail')>html.indexOf('</svg>'));assert.doesNotMatch(detail,/原始分|排名标准分/);
 h.click('previous-day');assert.match(selectedDetail(h.module.result(r)),/本日没有买入或卖出成交；收盘为空仓/);
 h.dispatch('keydown',{dataset:{pfChart:''}},{key:'End'});assert.match(selectedDetail(h.module.result(r)),new RegExp('data-pf-date="'+r.daily.at(-1).date+'"'));
 h.dispatch('keydown',{dataset:{pfChart:''}},{key:'Home'});assert.match(selectedDetail(h.module.result(r)),new RegExp('data-pf-date="'+r.daily[0].date+'"'));
});

test('curve selection loads a later daily page and never presents the earlier date as its detail',async()=>{
 let resolve;const pages=[],h=harness(new Map(),{dailyPage:(offset,path)=>{pages.push([offset,path]);return new Promise(done=>resolve=done);}}),r=structuredClone(fixture.result),daily=r.daily;
 r.daily=daily.slice(0,20);r.pagination={daily:{offset:0,total:daily.length,pageSize:20,hasMore:true}};
 h.module.result(r);h.click('select-day',{pfDate:daily[34].date});
 let detail=selectedDetail(h.module.result(r));assert.match(detail,/正在加载这一天/);assert.doesNotMatch(detail,/今日已成交|收盘为空仓/);
 await new Promise(setImmediate);assert.deepEqual(pages,[[20,'']]);
 r.daily=daily.slice(20,40);r.pagination.daily.offset=20;h.module.result(r);resolve();await new Promise(setImmediate);
 const html=h.module.result(r);detail=selectedDetail(html);assert.match(html,/aria-valuenow="34"/);assert.match(detail,new RegExp('data-pf-date="'+daily[34].date+'"'));assert.doesNotMatch(detail,/正在加载/);
 h.click('next-day');assert.match(h.module.result(r),/aria-valuenow="35"/);
});

test('day load failure offers retry without reusing the previous days operations',async()=>{
 let attempts=0;const h=harness(new Map(),{dailyPage:()=>{attempts++;return Promise.reject(new Error('读取失败'));}}),r=structuredClone(fixture.result),date=r.daily[40].date;
 r.daily=r.daily.slice(0,20);r.pagination={daily:{offset:0,total:r.curve.length,pageSize:20,hasMore:true}};
 h.module.result(r);h.click('select-day',{pfDate:date});await new Promise(setImmediate);
 const detail=selectedDetail(h.module.result(r));assert.match(detail,/当日明细加载失败/);assert.match(detail,/重新加载当日明细/);assert.doesNotMatch(detail,/今日已成交|收盘为空仓/);
 h.click('select-day',{pfDate:date});await new Promise(setImmediate);assert.equal(attempts,2);
});

test('historical targets, full exit plans, partial fills and no trade days have distinct meanings',()=>{
 const h=harness(),r=structuredClone(fixture.result),day=r.daily[0],symbol='sh600001';
 delete day.nextSessionPlan;delete day.positionControl; // Legacy daily records lack close-time snapshots.
 day.fills=[];day.activities=[];day.targetSignalDate='2024-01-01';day.holdings=[{symbol,quantity:200,weight:.02,marketValue:2000}];day.targetWeights={[symbol]:.02};
 let html=h.module.result(r),detail=selectedDetail(html);assert.match(detail,/本日没有买入或卖出成交；继续持有 1 只股票/);assert.match(detail,/本日没有新增买卖计划/);assert.match(detail,/系统保留 2024-01-01 的收盘目标/);assert.match(html,/沿用收盘目标/);assert.doesNotMatch(html,/下一开盘目标/);
 day.activities=[{date:day.date,symbol,action:'sell',status:'queued',reason:'stopLoss'},{date:day.date,symbol,action:'target',status:'queued',targetWeight:0,reason:'scheduledRebalance'}];day.targetWeights={};
 detail=selectedDetail(h.module.result(r));assert.equal((detail.match(/计划退出持仓/g)||[]).length,1);assert.match(detail,/达到止损线/);assert.match(detail,/拟卖出持仓 200 股/);
 day.fills=[{symbol,side:'sell',quantity:100,price:10,fee:5,realizedPnl:-35,date:day.date,signalDate:'2024-01-01',reason:'stopLoss'}];day.activities.push({symbol,action:'sell',status:'filled',quantity:100,unfilledQuantity:100});
 detail=selectedDetail(h.module.result(r));assert.match(detail,/本笔仍有 100 股未成交/);assert.match(detail,/本笔已实现盈亏 -35 元/);assert.match(detail,/收盘持有 200 股/);assert.doesNotMatch(detail,/本日没有买入/);
});

test('nested account day selections stay independent and changing task clears selection',()=>{
 let taskId='first';const h=harness(new Map(),{activeTaskId:()=>taskId}),first=structuredClone(fixture.result),second=structuredClone(fixture.result);
 first.pagination={path:'testing.fixed'};second.pagination={path:'testing.equalWeight'};
 const r={kind:'comparison',testing:{fixed:first,equalWeight:second}};h.module.result(r);h.click('select-day',{pfPath:'testing.fixed',pfDate:first.daily[5].date});
 let html=h.module.result(r);assert.match(html,new RegExp('data-pf-path="testing.fixed" data-pf-date="'+first.daily[5].date+'"'));assert.match(html,new RegExp('data-pf-path="testing.equalWeight" data-pf-date="'+second.daily[0].date+'"'));
 taskId='second';html=h.module.result(r);assert.match(html,new RegExp('data-pf-path="testing.fixed" data-pf-date="'+first.daily[0].date+'"'));
});

test('a retraining boundary displays the final queued target and explains the replaced plan separately',()=>{
 const h=harness(),r=structuredClone(fixture.result),day=r.daily[0],symbol='sh600001';day.fills=[];day.holdings=[];day.targetWeights={[symbol]:.1};
 delete day.nextSessionPlan; // Preserve coverage of the legacy activity-based fallback.
 day.activities=[{date:day.date,symbol,action:'target',status:'queued',targetWeight:.4,reason:'scheduledRebalance'},{date:day.date,symbol,action:'target',status:'superseded',reason:'trainingPolicyChanged'},{date:day.date,symbol,action:'target',status:'queued',targetWeight:.1,reason:'walkForwardRetrain'}];
 const detail=selectedDetail(h.module.result(r));assert.equal((detail.match(/计划建仓/g)||[]).length,1);assert.match(detail,/目标占总资产 10%/);assert.doesNotMatch(detail,/目标占总资产 40%/);assert.match(detail,/新训练方案替代旧目标/);
});

test('frozen cross-day plans explain adds, exits, position changes and risk thresholds without new queued events',()=>{
 const h=harness(),r=structuredClone(fixture.result),day=r.daily[0],date=day.date;
 day.activities=[];day.equity=100000;day.cash=96000;
 day.holdings=[{symbol:'sh600001',quantity:300,marketValue:3000,weight:.03,costPrice:9,markPrice:10,unrealizedPnl:300,unrealizedPnlPct:11.111,holdingDays:4},{symbol:'sz000001',quantity:100,marketValue:1000,weight:.01}];
 day.positionControl={equity:100000,cash:96000,availableCash:95000,reservedActionCash:1000,stockMarketValue:4000,stockWeight:.04,holdCount:2,maxPositions:3,maxStockWeight:.2,rebalanceMode:'risk',rebalanceDueToday:false,nextSessionDate:'2024-02-11',valuationReliable:true};
 day.fills=[{symbol:'sh600001',side:'buy',positionAction:'add',date,signalDate:'2024-02-08',quantity:200,quantityBefore:100,quantityAfter:300,price:10,amount:2000,fee:5,cashAfter:96000,unfilledQuantity:200,reason:'targetRebalance'}];
 day.nextSessionPlan={asOfDate:date,nextSessionDate:'2024-02-11',nextSessionInBacktest:true,hasPendingOrders:true,rows:[
  {symbol:'sh600001',action:'add',status:'carried',pending:true,currentQuantity:300,currentWeight:.03,targetQuantity:500,quantityDelta:200,targetWeight:.05,targetBudget:5000,signalDate:'2024-02-08',reason:'targetRebalance',executionStatus:'partial',quantityBasis:'frozen',directionEstimated:false},
  {symbol:'sz000001',action:'exit',status:'new',pending:true,currentQuantity:100,currentWeight:.01,targetQuantity:0,quantityDelta:-100,targetWeight:0,signalDate:date,reason:'stopLoss',executionStatus:'notAttempted',riskEvidence:{observedDate:date,gainPct:-.063,stopLossPct:.06}}
 ]};
 const detail=selectedDetail(h.module.result(r));
 assert.match(detail,/仓位与资金控制/);assert.match(detail,/实际股票仓位<\/span><strong>4%/);assert.match(detail,/9.5 万元/);
 assert.match(detail,/加仓买入/);assert.match(detail,/100 → 300 股/);assert.match(detail,/资金净支出 2,005 元/);assert.match(detail,/本笔仍有 200 股未成交/);
 assert.match(detail,/data-pf-plan-group="buy"[\s\S]*?计划加仓/);assert.match(detail,/目标持有 <b>500 股/);assert.match(detail,/沿用 2024-02-08 收盘计划/);
 assert.match(detail,/计划预算 5,000 元；执行以目标股数为准/);assert.doesNotMatch(detail,/信号时预算比例/);
 assert.match(detail,/data-pf-plan-group="sell"[\s\S]*?计划清仓/);assert.match(detail,/变动 -6.3%，达到止损 6%/);
 assert.doesNotMatch(detail,/本日没有新增买卖计划|旧报告未保存完整的跨日计划/);
});

test('completed targets remain holds and future opening quantities remain explicitly unknown',()=>{
 const h=harness(),r=structuredClone(fixture.result),day=r.daily[0];
 day.fills=[];day.activities=[];day.holdings=[{symbol:'sh600001',quantity:200,weight:.3,marketValue:30000}];
 day.nextSessionPlan={asOfDate:day.date,nextSessionDate:null,nextSessionInBacktest:false,hasPendingOrders:true,rows:[
  {symbol:'sh600001',action:'hold',status:'completed',pending:false,currentQuantity:200,currentWeight:.3,targetQuantity:200,quantityDelta:0,targetWeight:.2,reason:'targetRebalance',executionStatus:'completed'},
  {symbol:'sz000001',action:'open',status:'new',pending:true,currentQuantity:0,currentWeight:0,targetQuantity:null,quantityDelta:null,targetWeight:.1,targetBudget:10000,signalDate:day.date,reason:'targetRebalance',directionEstimated:true,quantityBasis:'nextOpen'},
  {symbol:'sh600003',action:'hold',status:'completed',pending:false,currentQuantity:0,targetQuantity:0,targetBudget:100,reason:'targetRebalance'}
 ]};
 const detail=selectedDetail(h.module.result(r));
 assert.match(detail,/data-pf-plan-group="hold"[\s\S]*?继续持有/);assert.match(detail,/既定股数已达到；无需重复买卖/);
 assert.match(detail,/股数待开盘价格确定/);assert.match(detail,/回测区间外，尚无后续成交验证/);
 assert.doesNotMatch(detail,/data-pf-plan-row="sh600003"/);
});

test('unfixed equal-value targets do not imply buys and corporate action shares have a separate explanation',()=>{
 const h=harness(),r=structuredClone(fixture.result),day=r.daily[0];
 day.fills=[];day.activities=[];day.holdings=[{symbol:'sh600001',quantity:2000,weight:.2,marketValue:10000,nextSessionSellableQuantity:1000}];
 day.corporateActions=[{symbol:'sh600001',date:day.date,stage:'ex',entitledQuantity:1000,bonusShares:1000,rightsShares:0,cashReceivable:500}];
 day.nextSessionPlan={asOfDate:day.date,nextSessionDate:null,nextSessionInBacktest:false,hasPendingOrders:true,rows:[
  {symbol:'sh600001',action:'rebalance',status:'new',pending:true,currentQuantity:2000,currentWeight:.2,targetQuantity:null,quantityDelta:null,targetWeight:.2,targetBudget:10000,signalDate:day.date,reason:'scheduledRebalance',directionEstimated:true,quantityBasis:'nextOpen'}
 ]};
 const detail=selectedDetail(h.module.result(r));
 assert.match(detail,/买入 \/ 加仓 0 只/);assert.match(detail,/卖出 \/ 减仓 0 只/);assert.match(detail,/方向待定 1 只/);
 assert.match(detail,/data-pf-plan-group="review"[\s\S]*?开盘核算调整方向/);
 assert.match(detail,/data-pf-day-rights[\s\S]*?登记基数 1,000 股[\s\S]*?新增股份 1,000 股[\s\S]*?应收现金 500 元/);
 assert.match(detail,/下个已知交易日可卖 1,000 股/);assert.doesNotMatch(detail,/data-pf-fill-row=/);
});

test('legacy records filtered to another date cannot establish that the selected day had no trades',()=>{
 const h=harness(),r=structuredClone(fixture.result),day=r.daily[0];delete day.fills;delete day.activities;r.trades=[];r.decisions=[];
 r.pagination={eventDate:'2024-01-01',trades:{total:0},decisions:{total:0}};
 const detail=selectedDetail(h.module.result(r));assert.match(detail,/旧报告当前页可能未包含全部当日操作/);assert.doesNotMatch(detail,/今天没有成交。|本日没有买入或卖出成交/);
});

test('independent nested chart requests retain both requested days even when the active page changes',async()=>{
 const pending=[],h=harness(new Map(),{dailyPage:(offset,path)=>new Promise(resolve=>pending.push({offset,path,resolve}))});
 const a=structuredClone(fixture.result),b=structuredClone(fixture.result),days=a.daily;a.daily=days.slice(0,20);b.daily=days.slice(0,20);
 a.pagination={path:'testing.fixed',daily:{offset:0,pageSize:20,total:days.length}};b.pagination={path:'testing.equalWeight',daily:{offset:0,pageSize:20,total:days.length}};
 const r={kind:'comparison',testing:{fixed:a,equalWeight:b}};h.module.result(r);
 h.click('select-day',{pfPath:'testing.fixed',pfDate:days[34].date});h.click('select-day',{pfPath:'testing.equalWeight',pfDate:days[45].date});await new Promise(setImmediate);
 pending[1].resolve({result:{testing:{equalWeight:{daily:days.slice(40,60)}}}});pending[0].resolve({result:{testing:{fixed:{daily:days.slice(20,40)}}}});await new Promise(setImmediate);
 const html=h.module.result(r);assert.doesNotMatch(html,/明细加载失败|正在加载这一天|报告没有该日明细/);
 assert.match(html,new RegExp('data-pf-path="testing.fixed" data-pf-date="'+days[34].date+'"'));assert.match(html,new RegExp('data-pf-path="testing.equalWeight" data-pf-date="'+days[45].date+'"'));
});

test('missing values remain missing and gaps do not draw through absent observations',()=>{
 const h=harness(),r=structuredClone(fixture.result);r.curve=[{date:'2024-01-01',equity:100,drawdownPct:0},{date:'2024-01-02',equity:null,drawdownPct:null},{date:'2024-01-03',equity:101,drawdownPct:0}];
 const html=h.module.result(r),line=html.match(/<path d="([^"]+)" class="pf-account-curve"/)[1];
 assert.equal((line.match(/M/g)||[]).length,2);assert.doesNotMatch(line,/L/);assert.doesNotMatch(html,/NaN|undefined/);
 r.curve[2].drawdownPct=.55;assert.match(h.module.result(r),/最大 0\.55%/);
 assert.match(h.module.result(null),/等待计算结果/);
});

test('comparison uses real nested results without inventing rotation performance',()=>{
 const h=harness(),r={kind:'comparison',training:{runs:{'single:momentum':fixture.result},selectedStrategyId:'momentum',persistence:{status:'insufficient',passed:false,validPairCount:0,meanRankCorrelation:null,pValue:null}},testing:{fixed:fixture.result,equalWeight:fixture.result},rotationGate:{passed:false,status:'not_implemented'},quality:{certified:false},warnings:[]};
 const html=h.module.result(r);assert.match(html,/证据不足/);assert.match(html,/momentum/);assert.match(html,/固定策略（仅训练选择） · 样本外净值与逐日明细/);assert.match(html,/所有参与策略等权 · 样本外净值与逐日明细/);assert.match(html,/未计算轮动系统/);
});

test('provider controlled names and warnings are escaped',()=>{
 const h=harness(),r=structuredClone(fixture.result);r.warnings=['<img src=x onerror=alert(1)>'];r.daily[0].topNByStrategy={'<script>':[{symbol:'<img>',score:.5,rawScore:1}]};
 const html=h.module.result(r);assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img/);
});

test('rotation budget units and saved old drafts preserve new defaults',()=>{
 const h=harness(new Map(),{advanced:true});h.field('config.allocation.mode','soft_rotation','text');h.field('config.rotation.minWeightChange','15');h.field('config.rotation.regimeBlend','40');h.field('config.rotation.temperature','.08');h.submit();
 const config=h.calls.find(c=>c[0]==='create')[1].config;assert.equal(config.rotation.minWeightChange,.15);assert.equal(config.rotation.regimeBlend,.4);assert.equal(config.rotation.temperature,.08);
 const saved=JSON.parse(h.storage.get('atrade.portfolio-editor.v1'));delete saved.draft.config.rotation;delete saved.draft.experiment;delete saved.draft.scanAxes;delete saved.draft.study.rotationEnabled;
 h.storage.set('atrade.portfolio-editor.v1',JSON.stringify(saved));const reopened=harness(h.storage,{advanced:true});assert.match(reopened.module.form(),/data-pf-field="config.rotation.lookbackDays"[^>]*value="20"/);reopened.submit();assert.equal(reopened.errors.length,0);
});

test('four-layer experiment serializes separately from config and bounds scan count',()=>{
 assert.deepEqual(helpers.scanAxes([{path:'combinations.combo_weighted.method',text:'交集, 并集, 加权'}],'walkForward')[0].values,['intersection','union','weighted']);
 const h=harness(new Map(),{advanced:true});h.field('kind','systemPlateau','text');h.field('periodMode','dates','text');h.field('config.startDate','2023-01-01','text');h.field('config.endDate','2025-12-31','text');h.field('combinations.weighted.enabled',true,'boolean');
 const axes=[['strategies.momentum.params.minReturn','3, 5'],['combinations.combo_weighted.method','intersection, weighted'],['rotation.minWeightChange','10, 15'],['maxStockWeight','15, 20']];
 axes.forEach(([path,text],i)=>{h.field('scanAxes.'+i+'.path',path,'text');h.field('scanAxes.'+i+'.text',text,'text');});h.submit();assert.deepEqual(h.errors,[]);
 const request=h.calls.find(c=>c[0]==='create')[1];assert.equal(request.experiment.kind,'systemPlateau');assert.equal(request.config.experiment,undefined);assert.equal(request.config.study.trainEnd,undefined);assert.equal(request.config.study.rotationEnabled,true);assert.deepEqual(request.experiment.axes[0].values,[.03,.05]);assert.deepEqual(request.experiment.axes[2].values,[.1,.15]);assert.deepEqual(request.experiment.axes[3].values,[.15,.2]);assert.equal(request.experiment.axes.reduce((n,a)=>n*a.values.length,1),16);
 assert.throws(()=>helpers.scanAxes(axes.slice(0,3).map(([path,text])=>({path,text})),'systemPlateau'),/四层/);
 assert.throws(()=>helpers.scanAxes(axes.map(([path])=>({path:path.endsWith('.method')?'topN':path,text:'1,2,3,4'})),'walkForward'),/超过/);
 assert.throws(()=>helpers.scanAxes([{path:'topN',text:'1, NaN'}],'walkForward'),/有效/);
 h.field('kind','walkForward','text');for(let i=0;i<6;i++)h.field('scanAxes.'+i+'.path','','text');h.submit();assert.deepEqual(h.calls.filter(c=>c[0]==='create').at(-1)[1].experiment.axes,[]);
});

test('plateau has an independent persisted wizard and a usable 16-combination starting example',()=>{
 const h=harness();h.field('config.topN','17');h.field('name','普通回测草稿','text');h.click('workflow-step',{pfStep:'3'});
 h.click('advanced-plateau');
 let html=h.module.plateau();assert.match(html,/收益平原创建流程/);assert.match(html,/16 组/);assert.match(html,/data-pf-action="plateau-example"/);
 assert.match(html,/data-pf-scanned-field="config.topN"/);assert.doesNotMatch(html,/data-pf-field="config.topN"/);
 assert.match(html,/data-pf-scan-method="1"/);assert.match(html,/基础值不限制候选范围/);
 h.field('name','平原草稿','text');h.click('workflow-step',{pfStep:'2'});
 html=h.module.form();assert.equal(h.module.currentStep(),3);assert.match(html,/value="17"/);assert.match(html,/value="普通回测草稿"/);assert.doesNotMatch(html,/<option value="systemPlateau"/);
 h.click('advanced-plateau');html=h.module.plateau();assert.equal(h.module.currentStep(),2);assert.match(html,/value="平原草稿"/);
 const reopened=harness(h.storage);reopened.click('advanced-plateau');html=reopened.module.plateau();assert.equal(reopened.module.currentStep(),2);assert.match(html,/value="平原草稿"/);assert.match(reopened.module.form(),/value="17"/);
});

test('axis changes clear incompatible values and errors identify the exact field and units',()=>{
 const h=harness();h.click('advanced-plateau');h.module.plateau();h.field('scanAxes.0.path','maxStockWeight','text');
 let saved=JSON.parse(h.storage.get('atrade.plateau-editor.v1')).draft;assert.equal(saved.scanAxes[0].text,'');
 h.field('scanAxes.0.text','150, 200','text');let html=h.module.plateau();assert.match(html,/单股上限（%）：请输入 0.1 至 100 %/);
 h.field('scanAxes.0.text','15, 20','text');h.field('scanAxes.3.path','maxStockWeight','text');h.field('scanAxes.3.text','20, 30','text');
 html=h.module.plateau();assert.match(html,/与另一扫描项重复/);
 h.click('plateau-example');html=h.module.plateau();assert.match(html,/16 组/);assert.doesNotMatch(html,/与另一扫描项重复/);
 h.dispatch('change',{dataset:{pfScanMethod:'1'},value:'union',checked:false});html=h.module.plateau();assert.match(html,/需要 2 至 5 个不同有效取值/);
 h.dispatch('change',{dataset:{pfScanMethod:'1'},value:'union',checked:true});html=h.module.plateau();assert.match(html,/16 组/);
});

test('plateau effective review separates overridden parameters from fixed values and reaches the real candidate contract',async()=>{
 const h=harness(new Map(),{previewSettings:async request=>({...request,name:'候选对照'})});h.click('advanced-plateau');h.module.plateau();
 h.field('config.startDate','2020-01-01','text');h.field('config.endDate','2025-12-31','text');h.field('config.maxPositions','19');
 h.field('scanAxes.3.text','3, 7','text');h.click('workflow-step',{pfStep:'4'});await h.module.reviewSettings();
 const html=h.module.plateau();assert.match(html,/data-pf-scan-summary/);assert.match(html,/data-pf-fixed-summary/);
 assert.match(html,/data-pf-scan-path="topN"/);assert.doesNotMatch(html,/data-pf-fixed-path="topN"/);assert.match(html,/data-pf-fixed-path="maxPositions"[\s\S]*?19 只/);
 assert.match(html,/持续性通过后/);assert.match(html,/普通回测的配权模式不用于固定本次研究/);
 h.submit();await new Promise(setImmediate);assert.deepEqual(h.errors,[]);const request=h.calls.find(c=>c[0]==='create')[1];
 const verification=spawnSync('python',['-c',`import json,sys\nfrom itertools import product\nfrom service.portfolio_experiment import normalize_experiment,apply_parameters\nv=json.load(sys.stdin);x=normalize_experiment(v['experiment'],v['config'])\nrows=[apply_parameters(v['config'],dict(zip([a['path'] for a in x['axes']],values))) for values in product(*[a['values'] for a in x['axes']])]\nassert len(rows)==16\nassert set(r['topN'] for r in rows)=={3,7}\nassert all(r['maxPositions']==19 and r['commissionRate']==v['config']['commissionRate'] for r in rows)\nprint('16 candidates verified')`],{input:JSON.stringify(request),encoding:'utf8',cwd:path.resolve(__dirname,'..'),env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 assert.equal(verification.status,0,verification.stderr);assert.match(verification.stdout,/16 candidates verified/);
});

test('legacy system plateau drafts migrate without discarding partially filled axes',()=>{
 const h=harness(new Map(),{advanced:true});h.field('name','未填完的旧平原','text');h.field('kind','systemPlateau','text');h.field('scanAxes.0.path','topN','text');h.field('scanAxes.0.text','5','text');
 const reopened=harness(h.storage,{advanced:true});assert.match(reopened.module.form(),/option value="portfolio" selected/);
 reopened.click('advanced-plateau');const html=reopened.module.plateau();assert.match(html,/value="未填完的旧平原"/);assert.match(html,/value="5"/);assert.match(html,/需要 2 至 5 个不同有效取值/);
});

test('ineffective risk scans are explained while ordinary walk-forward may keep all parameters fixed',()=>{
 const h=harness();h.click('advanced-plateau');h.module.plateau();h.field('scanAxes.3.path','stopLossPct','text');h.field('scanAxes.3.text','5, 8','text');h.field('config.rebalanceMode','rank','text');
 assert.match(h.module.plateau(),/只按排名换仓，这项风险退出参数不生效/);
 h.field('config.rebalanceMode','hybrid','text');assert.doesNotMatch(h.module.plateau(),/这项风险退出参数不生效/);
 h.module.advanced();h.field('kind','walkForward','text');const html=h.module.form();assert.match(html,/可以不扫描或只选择部分参数/);assert.doesNotMatch(html,/本次是四层全系统平原/);
});

test('new and edited plateau tasks target only the plateau draft',()=>{
 const h=harness();h.field('name','不要覆盖的回测','text');h.module.newTask('systemPlateau');h.field('name','新平原','text');h.module.setStep(4,'systemPlateau');assert.match(h.module.plateau(),/value="新平原"/);
 h.module.form();assert.match(h.module.form(),/value="不要覆盖的回测"/);
 h.module.setStep(5,'systemPlateau');assert.equal(h.module.currentStep(),5);assert.match(h.module.plateau(),/收益平原创建流程/);
 h.module.newTask('systemPlateau');assert.equal(h.module.currentStep(),1);assert.match(h.module.form(),/value="不要覆盖的回测"/);
});

test('scanned reference numbers cannot block a valid grid and shared weights remain editable for fixed combinations',()=>{
 const h=harness(new Map(),{advanced:true});h.field('config.topN','');h.click('advanced-plateau');h.module.plateau();h.click('plateau-import');
 h.field('config.startDate','2020-01-01','text');h.field('config.endDate','2025-12-31','text');
 h.submit();assert.deepEqual(h.errors,[]);const request=h.calls.find(c=>c[0]==='create')[1];assert.equal(request.config.topN,3);assert.deepEqual(request.experiment.axes.find(a=>a.path==='topN').values,[3,5]);
 h.field('combinations.weighted.enabled',true,'boolean');h.field('scanAxes.1.path','combinations.combo_union.children.momentum.weight','text');h.field('scanAxes.1.text','2, 3','text');
 const html=h.module.plateau();assert.match(html,/data-pf-field="weights.momentum"/);assert.match(html,/其余组合仍固定使用此值/);
 h.field('weights.momentum','7');h.submit();const next=h.calls.filter(c=>c[0]==='create').at(-1)[1];
 assert.equal(next.config.combinations.find(c=>c.id==='combo_union').children.find(c=>c.id==='momentum').weight,2);
 assert.equal(next.config.combinations.find(c=>c.id==='combo_weighted').children.find(c=>c.id==='momentum').weight,7);
});

test('comparison exposes failed value verdict and correct independent nested pagination',async()=>{
 const h=harness(),run=structuredClone(fixture.result);run.daily=run.daily.slice(20,40);run.pagination={path:'testing.softRotation',daily:{offset:20,total:run.curve.length,pageSize:20,hasMore:true},rotationLog:{offset:0,total:300,pageSize:200,hasMore:true}};
 run.rotationLog=[{date:'2024-03-01',earliestExecutionDate:'2024-03-02',before:{momentum:1},after:{momentum:.6,forceGoldenCross:.4},reason:'performance',weightChange:.4}];
 const html=h.module.result({kind:'comparison',testing:{softRotation:run,hardRotation:fixture.result,fixed:fixture.result,equalWeight:fixture.result},rotationGate:{status:'no_value',conclusion:'轮动没有创造价值：样本外净收益未超过等权组合。'},comparison:{softMinusEqualReturnPct:-1.2},training:{persistence:{status:'passed'}}});
 assert.match(html,/轮动没有创造价值/);assert.match(html,/动态软配权/);assert.match(html,/动态硬切换/);assert.match(html,/data-pf-path="testing.softRotation"/);assert.match(html,/2024-03-02/);
 h.click('next-page',{pfPath:'testing.softRotation',pfOffset:'40'});await new Promise(setImmediate);assert.deepEqual(h.calls.find(row=>row[0]==='page'),['page',40,'testing.softRotation']);h.click('rotation-page',{pfPath:'testing.softRotation'});assert.deepEqual(h.calls.at(-1),['rotation',200,'testing.softRotation']);
 h.dispatch('toggle',{dataset:{pfDetail:'run:testing.softRotation'},open:true});assert.match(h.module.result({kind:'comparison',testing:{softRotation:run}}),/data-pf-detail="run:testing.softRotation" open/);
});

test('system plateau paints only observed neighborhoods and never invents certification',()=>{
 const h=harness(),r={kind:'systemPlateau',trialCount:16,engineRunCount:80,actualCandidateEvaluations:16,plannedCandidateEvaluations:32,folds:[{fold:1,trainStart:'2023-01-01',trainEnd:'2023-06-01',testStart:'2023-06-02',testEnd:'2023-07-01',persistence:null,testing:{equalWeight:fixture.result},rotationGate:{status:'skipped'}}],plateau:{certified:false,recommendedRanges:[],reasons:['insufficient_out_of_sample_years'],heatmap:[{candidateId:'c1',params:{topN:5},rotationExecuted:false,trainingMeanNetReturnPct:3,oosMeanNetReturnPct:-1,neighborhood:{meanNetReturnPct:2,stdNetReturnPct:1,positiveFraction:.75,size:5,passed:true}}]}};
 const html=h.module.result(r);assert.match(html,/参数组合总数：16/);assert.match(html,/账户模拟次数：80/);assert.match(html,/pf-heat-cell pf-heat-pass/);assert.match(html,/持续性未通过，含等权替代结果/);assert.match(html,/样本外覆盖年份不足/);assert.match(html,/暂无推荐区间/);assert.match(html,/2023-06-02/);assert.doesNotMatch(html,/NaN|undefined/);
 r.plateau.heatmap=[];assert.match(h.module.result(r),/不显示示例热图/);assert.doesNotMatch(h.module.result(r),/class="pf-heat-cell/);
});

test('higher parameter layers are explicit heatmap slices with only computed cells',()=>{
 const h=harness(),axes=[{path:'topN',values:[2,3]},{path:'maxPositions',values:[4,5]},{path:'rotation.lookbackDays',values:[10,20]}],heatmap=[];
 for(const n of [2,3])for(const positions of [4,5])for(const k of [10,20])heatmap.push({candidateId:`n${n}p${positions}k${k}`,params:{topN:n,maxPositions:positions,'rotation.lookbackDays':k},neighborhood:{meanNetReturnPct:k===10?1:-2,size:3,passed:false},rotationExecuted:true});
 const r={kind:'systemPlateau',experiment:{axes},plateau:{heatmap},folds:[]};let html=h.module.result(r);assert.equal((html.match(/class="pf-heat-cell/g)||[]).length,4);assert.match(html,/data-pf-heat-slice="rotation.lookbackDays"/);assert.match(html,/n2p4k10/);assert.doesNotMatch(html.split('查看分年份')[0],/n2p4k20/);
 h.dispatch('change',{dataset:{pfHeatSlice:'rotation.lookbackDays'},value:'20'});html=h.module.result(r);assert.match(html,/n2p4k20/);assert.equal((html.match(/class="pf-heat-cell/g)||[]).length,4);
});

test('single workflow uses exactly one strategy and style presets change only the stock funnel',()=>{
 const h=harness();h.module.selectStrategy('trendBreakout');h.field('params.trendBreakout.lookback','43');h.field('config.universe.minAmount20','2400');
 h.click('apply-pool-style',{pfStyle:'breakout'});h.submit();assert.deepEqual(h.errors,[]);
 const request=h.calls.find(row=>row[0]==='create')[1];assert.equal(request.kind,'singleBacktest');assert.equal(request.config.strategies.length,1);assert.equal(request.config.strategies[0].params.lookback,43);assert.equal(request.config.strategies[0].pool,'combined');assert.deepEqual(request.config.combinations,[]);assert.deepEqual(request.config.allocation,{mode:'single',strategyIds:['trendBreakout']});assert.equal(request.config.universe.minAmount20,24000000);assert.equal(request.config.poolSettings.structure.minDrawdown,0);
 const html=h.module.form();assert.match(html,/data-pf-style="breakout" aria-pressed="true"/);assert.doesNotMatch(html,/data-pf-field="config.rotation|data-pf-field="config.allocation.mode|data-pf-field="combinations\./);assert.match(html,/data-nav="R10"/);assert.match(html,/data-pf-action="advanced"/);assert.match(html,/明确开发截止日/);
 h.field('config.poolSettings.structure.maxDrawdown','10');assert.match(h.module.form(),/当前：自定义漏斗/);
 const gate=spawnSync('python',['-c','import json,sys\nfrom service.research_pipeline import single_config\nsingle_config(json.load(sys.stdin)["config"])'],{input:JSON.stringify(request),encoding:'utf8',cwd:path.resolve(__dirname,'..'),env:{...process.env,PYTHONIOENCODING:'utf-8'}});assert.equal(gate.status,0,gate.stderr);
});

test('single plateau copies completed fixed settings and submits only low-dimensional development settings',()=>{
 const h=harness();h.module.selectStrategy('momentum');h.click('apply-pool-style',{pfStyle:'momentum'});h.field('config.topN','4');h.field('config.maxPositions','9');h.field('name','固定研究','text');h.submit();
 const fixed=h.calls.find(row=>row[0]==='create')[1],task={...fixed,id:'fixed-task',status:'completed'};h.module.startPlateau(task);
 assert.equal(h.module.currentStep(),2);let html=h.module.plateau();assert.equal((html.match(/data-pf-axis-row=/g)||[]).length,3);assert.doesNotMatch(html,/data-pf-field="experiment.trainDays|data-pf-field="experiment.testDays|data-pf-field="study.rotationEnabled|data-pf-field="config.rotation/);
 h.field('scanAxes.0.path','strategies.momentum.params.lookback','text');h.field('scanAxes.0.text','15, 20, 25','text');h.submit();const scan=h.calls.filter(row=>row[0]==='create').at(-1)[1];assert.equal(scan.kind,'singlePlateau');assert.deepEqual(scan.experiment.axes,[{path:'strategies.momentum.params.lookback',values:[15,20,25]}]);assert.equal(scan.experiment.minClosedTrades,3);assert.equal(scan.experiment.maxDrawdownPct,30);assert.equal(scan.experiment.trainDays,undefined);assert.equal(scan.config.study,undefined);assert.deepEqual(scan.config.poolSettings,fixed.config.poolSettings);assert.equal(scan.config.maxPositions,9);assert.equal(scan.config.topN,4);
 const validation=spawnSync('python',['-c','import json,sys\nfrom service.research_pipeline import normalize_single_experiment\nv=json.load(sys.stdin);normalize_single_experiment(v["experiment"],v["config"])'],{input:JSON.stringify(scan),encoding:'utf8',cwd:path.resolve(__dirname,'..'),env:{...process.env,PYTHONIOENCODING:'utf-8'}});assert.equal(validation.status,0,validation.stderr);
 const original=h.module.form();assert.match(original,/value="固定研究"/);assert.doesNotMatch(original,/关键参数平原"/);
 const reopened=harness(h.storage);assert.match(reopened.module.plateau(),/15, 20, 25/);assert.match(reopened.module.form(),/value="固定研究"/);
});

test('single plateau rejects too many axes, combinations, and oversized grids before submitting',()=>{
 assert.throws(()=>helpers.scanAxes([],'singlePlateau'),/1 至 3/);
 assert.throws(()=>helpers.scanAxes(['topN','maxPositions','stopLossPct','maxHoldingDays'].map(path=>({path,text:'1,2'})),'singlePlateau'),/1 至 3/);
 assert.throws(()=>helpers.scanAxes([{path:'rotation.lookbackDays',text:'10,20'}],'singlePlateau'),/只扫描/);
 assert.throws(()=>helpers.scanAxes([{path:'combinations.combo_union.method',text:'交集,并集'}],'singlePlateau'),/只扫描/);
 assert.throws(()=>helpers.scanAxes(['topN','maxPositions','maxHoldingDays'].map(path=>({path,text:'1,2,3,4'})),'singlePlateau'),/27/);
});

test('candidate choice is explicit, incomplete scans stay disabled, and replay uses the chosen point',async()=>{
 const frozen=[],replayed=[],h=harness(new Map(),{activeTaskId:()=> 'scan-task',freeze:request=>{frozen.push(request);},replayCandidate:request=>{replayed.push(request);}});
 const r={kind:'singlePlateau',status:'running',config:fixture.result.config,axes:[{path:'topN',values:[2,3,4]}],candidates:[{candidateId:'c002',params:{topN:3},config:fixture.result.config,metrics:{totalReturnPct:4,maxDrawdownPct:2,closedTradeCount:6},neighborhood:{size:3,meanNetReturnPct:3,stdNetReturnPct:1,positiveFraction:1,passed:true}}]};
 let html=h.module.result(r);assert.match(html,/data-pf-action="freeze-candidate" data-pf-candidate="c002" disabled/);assert.match(html,/data-pf-action="replay-candidate" data-pf-candidate="c002" disabled/);assert.deepEqual(frozen,[]);
 r.status='completed';html=h.module.result(r);assert.match(html,/data-pf-action="freeze-candidate" data-pf-candidate="c002" >/);assert.match(html,/不会自动采用收益最高点/);h.click('freeze-candidate',{pfCandidate:'c002'});h.click('replay-candidate',{pfCandidate:'c002'});await new Promise(setImmediate);assert.equal(frozen[0].id,'scan-task');assert.equal(frozen[0].candidateId,'c002');assert.equal(replayed[0].candidateId,'c002');assert.doesNotMatch(html,/滚动折数|样本外均值/);
});

test('single and legacy advanced drafts remain separate across scope changes and restart',()=>{
 const h=harness();h.field('name','单策略草稿','text');h.module.advanced();h.field('name','高级组合草稿','text');h.field('config.allocation.mode','soft_rotation','text');h.module.startSingle();assert.match(h.module.form(),/value="单策略草稿"/);h.module.advanced();assert.match(h.module.form(),/value="高级组合草稿"/);
 const reopened=harness(h.storage);assert.match(reopened.module.form(),/value="单策略草稿"/);reopened.module.advanced();assert.match(reopened.module.form(),/value="高级组合草稿"/);assert.match(reopened.module.form(),/option value="soft_rotation" selected/);
});
