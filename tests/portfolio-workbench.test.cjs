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
function harness(storage=new Map()){
 const listeners=new Map(),calls=[],errors=[];
 const document={addEventListener(type,fn){const handlers=listeners.get(type)||[];handlers.push(fn);listeners.set(type,handlers);}};
 const window={},localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
 vm.runInNewContext(source,{window,AT:{escape},document,localStorage,structuredClone,console},{filename:'portfolio-workbench.js'});
 const hooks={refresh(){calls.push(['refresh']);},create(value){calls.push(['create',JSON.parse(JSON.stringify(value))]);},error(error){errors.push(error.message);},feedback(value){calls.push(['feedback',value]);},dailyPage(value){calls.push(['page',value]);}};
 window.PortfolioWorkbench.configure(structuredClone(fixture.definition),hooks);
 const dispatch=(type,target)=>{for(const fn of listeners.get(type)||[])fn({target,preventDefault(){},stopImmediatePropagation(){}});};
 const field=(path,value,type='number')=>dispatch('input',{dataset:{pfField:path,pfType:type==='number'?undefined:type},value,checked:value,matches:()=>true});
 const click=action=>dispatch('click',{closest:()=>({dataset:{pfAction:action,pfOffset:'200'},disabled:false})});
 const submit=()=>dispatch('submit',{matches:()=>true,querySelectorAll:()=>[]});
 return {module:window.PortfolioWorkbench,field,click,submit,calls,errors,storage};
}

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

test('daily backend pages stay explicit and next page calls the parent loader',()=>{
 const h=harness(),r=structuredClone(fixture.result);r.pagination={daily:{offset:0,total:650,hasMore:true}};
 assert.match(h.module.result(r),/共 650 日/);h.click('next-page');assert.deepEqual(h.calls.at(-1),['page',200]);
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
 const html=h.module.result(r);assert.match(html,/证据不足/);assert.match(html,/momentum/);assert.match(html,/固定策略 · 样本外净值与回撤/);assert.match(html,/等权组合 · 样本外净值与回撤/);assert.match(html,/未计算轮动系统/);
});

test('provider controlled names and warnings are escaped',()=>{
 const h=harness(),r=structuredClone(fixture.result);r.warnings=['<img src=x onerror=alert(1)>'];r.daily[0].topNByStrategy={'<script>':[{symbol:'<img>',score:.5,rawScore:1}]};
 const html=h.module.result(r);assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img/);
});
