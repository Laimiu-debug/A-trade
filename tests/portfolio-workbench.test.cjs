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
from service.portfolio_config import CONFIG_VERSION,catalogue,default_config,normalize_selection,normalize_config
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
print(json.dumps(dict(definition=dict(version=CONFIG_VERSION,strategies=catalogue(),defaults=default_config(),selection=normalize_selection()),result=engine.result()),ensure_ascii=False))
`],{encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'},maxBuffer:8*1024*1024,cwd:path.resolve(__dirname,'..')});
assert.equal(generated.status,0,generated.stderr);
const fixture=JSON.parse(generated.stdout);
function harness(storage=new Map(),options={}){
 const listeners=new Map(),calls=[],errors=[];
 const document={addEventListener(type,fn){const handlers=listeners.get(type)||[];handlers.push(fn);listeners.set(type,handlers);},querySelector:selector=>options.querySelector?.(selector)||null};
 const window={},localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/research-display.js'),'utf8'),{window,document});
 vm.runInNewContext(source,{window,AT:{escape},document,localStorage,structuredClone,console,setTimeout,clearTimeout},{filename:'portfolio-workbench.js'});
 const hooks={update(value){calls.push(['update',JSON.parse(JSON.stringify(value))]);},refresh(){calls.push(['refresh']);},create(value){calls.push(['create',JSON.parse(JSON.stringify(value))]);},error(error){errors.push(error.message);},feedback(value){calls.push(['feedback',value]);},dailyPage(value,path){calls.push(['page',value,path]);},rotationPage(value,path){calls.push(['rotation',value,path]);}};
 hooks.screenCreate=value=>{calls.push(['screenCreate',JSON.parse(JSON.stringify(value))]);return Promise.resolve({id:'screen-job'});};
 if(options.previewSettings)hooks.previewSettings=value=>{calls.push(['previewSettings',JSON.parse(JSON.stringify(value))]);return options.previewSettings(value);};
 window.PortfolioWorkbench.configure(structuredClone(fixture.definition),hooks);
 const dispatch=(type,target,properties={})=>{for(const fn of listeners.get(type)||[])fn({...properties,target,preventDefault(){},stopImmediatePropagation(){}});};
 const field=(path,value,type='number')=>dispatch('input',{dataset:{pfField:path,pfType:type==='number'?undefined:type},value,checked:value,matches:()=>true});
 const click=(action,extra={})=>dispatch('click',{closest:()=>({dataset:{pfAction:action,pfOffset:'200',...extra},disabled:false})});
 const submit=()=>dispatch('submit',{matches:()=>true,querySelectorAll:()=>[]});
 return {module:window.PortfolioWorkbench,field,click,submit,calls,errors,storage,dispatch};
}

test('five steps keep pool settings visible and advanced parameters folded by default',()=>{
 const h=harness(),html=h.module.form();
 assert.equal(h.module.currentStep(),1);assert.match(html,/aria-label="策略与回测流程"/);
 assert.match(html,/data-pf-step-panel="1" ><header/);assert.match(html,/data-pf-step-panel="2" hidden/);
 assert.equal((html.match(/class="pf-pool-card"/g)||[]).length,4);
 assert.match(html,/策略参数与预设 · 使用默认参数/);assert.doesNotMatch(html,/data-pf-detail="editor:strategy:[^"]+" open/);
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
 assert.equal(request.kind,'poolPreview');assert.equal(request.config.initialCapital,fixture.definition.defaults.initialCapital);
 assert.equal(request.config.allocation.mode,'equal');assert.equal(request.selection.mode,'all');assert.deepEqual(h.errors,[]);
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
 assert.match(h.module.form(),/分批处理后统一排名/);
});

test('independent portfolio fields and strategy parameters reach the actual contract',()=>{
 const h=harness();h.field('config.topN','7');h.field('config.maxPositions','13');h.field('config.maxStockWeight','12');h.field('config.lookbackDays','81');h.field('config.rebalanceMode','rank','text');h.field('config.stopLossPct','3');h.field('config.stampDutyRate','');h.field('params.momentum.lookback','25');h.field('params.momentum.minReturn','7');h.field('pools.momentum','volume','text');h.submit();
 assert.deepEqual(h.errors,[]);const {config,selection}=h.calls.find(c=>c[0]==='create')[1];
 assert.equal(config.topN,7);assert.equal(config.maxPositions,13);assert.equal(config.maxStockWeight,.12);assert.equal(config.rebalanceMode,'rank');assert.equal(config.stopLossPct,.03);assert.equal(config.stampDutyRate,null);assert.equal(selection.lookbackDays,81);
 assert.equal(config.startDate,null);assert.equal(config.endDate,null);
 const m=config.strategies.find(s=>s.kind==='momentum');assert.equal(m.params.lookback,25);assert.equal(m.params.minReturn,.07);assert.equal(m.pool,'volume');
});

test('explicit combinations retain unequal child weights and can alone receive allocation',()=>{
 const h=harness();for(const s of fixture.definition.strategies){h.field('enabled.'+s.id,['forceGoldenCross','momentum'].includes(s.id),'boolean');h.field('participates.'+s.id,false,'boolean');}
 h.field('combinations.weighted.enabled',true,'boolean');h.field('combinations.weighted.threshold','.3');h.field('weights.forceGoldenCross','2');h.field('weights.momentum','7');h.field('config.allocation.mode','single','text');h.submit();
 assert.deepEqual(h.errors,[]);const config=h.calls.find(c=>c[0]==='create')[1].config;
 assert.deepEqual(config.allocation.strategyIds,['combo_weighted']);
 assert.deepEqual(config.combinations[0],{id:'combo_weighted',method:'weighted',normalization:'rank',threshold:.3,children:config.strategies.map(s=>({id:s.id,weight:s.id==='momentum'?7:2}))});
});

test('impossible selections and invalid explicit dates fail before create',()=>{
 const h=harness();h.field('config.allocation.mode','single','text');h.submit();assert.match(h.errors.pop(),/只能勾选一个/);
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
 assert.match(harness(broken).module.form(),/股票池来源与基础资格/);
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
 const h=harness();assert.match(h.module.form(),/分层股票池研究参数/);assert.match(h.module.form(),/按当前设置筛选股票池/);
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

test('daily backend pages stay explicit and next page calls the parent loader',()=>{
 const h=harness(),r=structuredClone(fixture.result);r.pagination={daily:{offset:0,total:650,hasMore:true}};
 assert.match(h.module.result(r),/共 650 日/);h.click('next-page');assert.deepEqual(h.calls.at(-1),['page',200,'']);
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
 const h=harness();h.field('config.allocation.mode','soft_rotation','text');h.field('config.rotation.minWeightChange','15');h.field('config.rotation.regimeBlend','40');h.field('config.rotation.temperature','.08');h.submit();
 const config=h.calls.find(c=>c[0]==='create')[1].config;assert.equal(config.rotation.minWeightChange,.15);assert.equal(config.rotation.regimeBlend,.4);assert.equal(config.rotation.temperature,.08);
 const saved=JSON.parse(h.storage.get('atrade.portfolio-editor.v1'));delete saved.draft.config.rotation;delete saved.draft.experiment;delete saved.draft.scanAxes;delete saved.draft.study.rotationEnabled;
 h.storage.set('atrade.portfolio-editor.v1',JSON.stringify(saved));const reopened=harness(h.storage);assert.match(reopened.module.form(),/data-pf-field="config.rotation.lookbackDays"[^>]*value="20"/);reopened.submit();assert.equal(reopened.errors.length,0);
});

test('four-layer experiment serializes separately from config and bounds scan count',()=>{
 assert.deepEqual(helpers.scanAxes([{path:'combinations.combo_weighted.method',text:'交集, 并集, 加权'}],'walkForward')[0].values,['intersection','union','weighted']);
 const h=harness();h.field('kind','systemPlateau','text');h.field('periodMode','dates','text');h.field('config.startDate','2023-01-01','text');h.field('config.endDate','2025-12-31','text');h.field('combinations.weighted.enabled',true,'boolean');
 const axes=[['strategies.momentum.params.minReturn','3, 5'],['combinations.combo_weighted.method','intersection, weighted'],['rotation.minWeightChange','10, 15'],['maxStockWeight','15, 20']];
 axes.forEach(([path,text],i)=>{h.field('scanAxes.'+i+'.path',path,'text');h.field('scanAxes.'+i+'.text',text,'text');});h.submit();assert.deepEqual(h.errors,[]);
 const request=h.calls.find(c=>c[0]==='create')[1];assert.equal(request.experiment.kind,'systemPlateau');assert.equal(request.config.experiment,undefined);assert.equal(request.config.study.trainEnd,undefined);assert.equal(request.config.study.rotationEnabled,true);assert.deepEqual(request.experiment.axes[0].values,[.03,.05]);assert.deepEqual(request.experiment.axes[2].values,[.1,.15]);assert.deepEqual(request.experiment.axes[3].values,[.15,.2]);assert.equal(request.experiment.axes.reduce((n,a)=>n*a.values.length,1),16);
 assert.throws(()=>helpers.scanAxes(axes.slice(0,3).map(([path,text])=>({path,text})),'systemPlateau'),/四层/);
 assert.throws(()=>helpers.scanAxes(axes.map(([path])=>({path:path.endsWith('.method')?'topN':path,text:'1,2,3,4'})),'walkForward'),/超过/);
 assert.throws(()=>helpers.scanAxes([{path:'topN',text:'1, NaN'}],'walkForward'),/有效/);
 h.field('kind','walkForward','text');for(let i=0;i<6;i++)h.field('scanAxes.'+i+'.path','','text');h.submit();assert.deepEqual(h.calls.filter(c=>c[0]==='create').at(-1)[1].experiment.axes,[]);
});

test('comparison exposes failed value verdict and correct independent nested pagination',()=>{
 const h=harness(),run=structuredClone(fixture.result);run.pagination={path:'testing.softRotation',daily:{offset:20,total:200,pageSize:20,hasMore:true},rotationLog:{offset:0,total:300,pageSize:200,hasMore:true}};
 run.rotationLog=[{date:'2024-03-01',earliestExecutionDate:'2024-03-02',before:{momentum:1},after:{momentum:.6,forceGoldenCross:.4},reason:'performance',weightChange:.4}];
 const html=h.module.result({kind:'comparison',testing:{softRotation:run,hardRotation:fixture.result,fixed:fixture.result,equalWeight:fixture.result},rotationGate:{status:'no_value',conclusion:'轮动没有创造价值：样本外净收益未超过等权组合。'},comparison:{softMinusEqualReturnPct:-1.2},training:{persistence:{status:'passed'}}});
 assert.match(html,/轮动没有创造价值/);assert.match(html,/动态软配权/);assert.match(html,/动态硬切换/);assert.match(html,/data-pf-path="testing.softRotation"/);assert.match(html,/2024-03-02/);
 h.click('next-page',{pfPath:'testing.softRotation'});assert.deepEqual(h.calls.at(-1),['page',200,'testing.softRotation']);h.click('rotation-page',{pfPath:'testing.softRotation'});assert.deepEqual(h.calls.at(-1),['rotation',200,'testing.softRotation']);
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
