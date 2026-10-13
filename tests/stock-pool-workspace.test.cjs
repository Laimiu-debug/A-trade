const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const generated=spawnSync('python',['-c','import json; from service.portfolio_config import CONFIG_VERSION,catalogue,default_config,normalize_selection; print(json.dumps(dict(version=CONFIG_VERSION,strategies=catalogue(),defaults=default_config(),selection=normalize_selection()),ensure_ascii=False))'],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}});
assert.equal(generated.status,0,generated.stderr);
const definition=JSON.parse(generated.stdout);
// Single-strategy development now requires a declared interval. Pool screening
// keeps its independent recent-day option.
definition.defaults.startDate='2026-01-01';definition.defaults.endDate='2026-06-30';
const code=fs.readFileSync(path.join(root,'preview/portfolio-workbench.js'),'utf8');
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy=value=>JSON.parse(JSON.stringify(value));
const tick=()=>new Promise(setImmediate);
function report(marker='当前筛选'){
  const row={symbol:'sh600001',name:marker,eligible:true,reason:'eligible',close:12.5,amount:123450000,amount20:200000000,memberships:['base','trend','volume']};
  const keys=['base','trend','structure','volume','combined','excluded'];
  return {type:'poolPreview',asOf:'2026-10-09',coverage:{totalSecurities:136},screenPools:Object.fromEntries(keys.map(k=>[k,[row]])),poolCounts:Object.fromEntries(keys.map(k=>[k,136])),poolPagination:Object.fromEntries(keys.map(k=>[k,{offset:0,total:1,pageSize:20,hasMore:false}])),poolSelection:{matchedCount:1,unknownCount:0,funnelStages:[]}};
}
function harness(options={}){
  const storage=options.storage||new Map(),listeners=new Map(),calls=[],errors=[];
  let search='',counter=0;
  const document={addEventListener(type,fn){const set=listeners.get(type)||[];set.push(fn);listeners.set(type,set);},querySelector(selector){return selector==='[data-pf-screen-query]'?{value:search}:null;}};
  const window={},localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v)};
  if(options.importHistory)window.atradeDesktop={importResearchHistory:options.importHistory};
  const AT={escape,navigate:id=>calls.push(['navigate',id])};
  vm.runInNewContext(fs.readFileSync(path.join(root,'preview/research-display.js'),'utf8'),{window,document});
  vm.runInNewContext(code,{window,document,localStorage,AT,structuredClone,setTimeout,clearTimeout,console});
  const hooks={refresh(){},error:e=>errors.push(e.message),feedback:()=>{},create:r=>calls.push(['create',copy(r)]),update:r=>calls.push(['update',copy(r)]),screenCreate:r=>{calls.push(['screenCreate',copy(r)]);return options.screenCreate?.(r)||Promise.resolve({id:'screen-'+(++counter)});},screenGet:r=>{calls.push(['screenGet',copy(r)]);return options.screenGet?.(r)||Promise.resolve({task:{id:r.id,status:'completed'},result:report()});}};
  if(options.activeTaskId)hooks.activeTaskId=options.activeTaskId;
  const module=window.PortfolioWorkbench;
  module.configure(structuredClone(definition),hooks);
  const dispatch=(type,target,extra={})=>{for(const fn of listeners.get(type)||[])fn({target,preventDefault(){},stopImmediatePropagation(){},...extra});};
  const click=(action,extra={})=>dispatch('click',{closest:()=>({dataset:{pfAction:action,...extra},disabled:false})});
  const field=(key,value,type='number')=>dispatch('input',{dataset:{pfField:key,pfType:type},value,checked:value,matches:()=>true});
  const layer=(key,checked)=>dispatch('change',{dataset:{pfPoolLayer:key},checked});
  const mode=value=>dispatch('change',{dataset:{pfPoolMode:''},value});
  const submit=()=>dispatch('submit',{matches:()=>true,querySelectorAll:()=>[]});
  const start=async()=>{click('screen-preview');await tick();};
  return {module,calls,errors,storage,click,field,layer,mode,submit,start,dispatch,search:value=>{search=value;}};
}

test('M06 defaults to global all-A independently of an existing restricted research draft',async()=>{
  const h=harness();
  h.field('selection.mode','explicit','text');h.field('symbols','600900','text');h.field('config.initialCapital','876543');
  h.field('config.universe.minAmount20','2500');
  const market=h.module.poolWorkspace();
  assert.match(market,/全部 A 股 · 沪市、深市、北交所/);
  h.field('config.universe.minAmount20','987.65');await h.start();
  const request=h.calls.find(c=>c[0]==='screenCreate')[1];
  assert.equal(request.selection.mode,'all');assert.equal(request.selection.source,'local');
  assert.deepEqual(request.selection.markets,['sh','sz','bj']);assert.deepEqual(request.selection.symbols,[]);
  assert.equal(request.config.universe.minAmount20,9876500);
  h.module.form();h.submit();
  const research=h.calls.find(c=>c[0]==='create')[1];
  assert.equal(research.selection.mode,'explicit');assert.deepEqual(research.selection.symbols,['sh600900']);
  assert.equal(research.config.initialCapital,876543);assert.equal(research.config.universe.minAmount20,25000000);
  assert.deepEqual(h.errors,[]);
});

test('pool selection sends one complete query with independent view, pagination and search',async()=>{
  const h=harness();h.module.poolWorkspace();await h.start();
  h.layer('structure',false);h.mode('union');await tick();
  let request=h.calls.filter(c=>c[0]==='screenGet').at(-1)[1];
  assert.equal(request.pool,'combined');assert.deepEqual(request.pools,['trend','volume']);assert.equal(request.poolMode,'union');
  h.click('screen-pool',{pfPool:'trend'});h.click('screen-page',{pfOffset:'20'});await tick();
  request=h.calls.filter(c=>c[0]==='screenGet').at(-1)[1];
  assert.equal(request.pool,'trend');assert.equal(request.poolOffset,20);assert.deepEqual(request.pools,['trend','volume']);
  h.search('边界股票');h.click('screen-search');await tick();
  request=h.calls.filter(c=>c[0]==='screenGet').at(-1)[1];
  assert.equal(request.poolQuery,'边界股票');assert.equal(request.poolOffset,0);
  h.layer('trend',false);h.layer('volume',false);await tick();
  request=h.calls.filter(c=>c[0]==='screenGet').at(-1)[1];
  assert.equal(request.pool,'combined');assert.ok(!request.pools.length||JSON.stringify(request.pools)==='["base"]');
  assert.equal(h.calls.filter(c=>c[0]==='screenCreate').length,1,'set combinations must reuse complete saved pools');
});

test('late prior query responses cannot replace current results or spill across workspaces',async()=>{
  const pending=[];
  const h=harness({screenGet:r=>new Promise(resolve=>pending.push({request:copy(r),resolve}))});
  h.module.poolWorkspace();await h.start();
  h.mode('union');await tick();assert.equal(pending.length,2);
  pending[1].resolve({task:{id:'screen-1'},result:report('最新并集')});await tick();
  pending[0].resolve({task:{id:'screen-1'},result:report('迟到交集')});await tick();
  assert.match(h.module.poolWorkspace(),/最新并集/);assert.doesNotMatch(h.module.poolWorkspace(),/迟到交集/);
  h.click('screen-clear');await tick();h.module.form();
  pending[2].resolve({task:{id:'screen-1'},result:report('仅市场工作区')});await tick();
  assert.doesNotMatch(h.module.form(),/仅市场工作区/);
  assert.match(h.module.poolWorkspace(),/仅市场工作区/);
});

test('changed combination hides old rows while pending or failed and retries the requested view',async()=>{
  let count=0,rejectUpdate;
  const h=harness({screenGet:r=>++count===1?Promise.resolve({task:{id:r.id},result:report('原交集名单')}):new Promise((resolve,reject)=>{rejectUpdate=reject;})});
  h.module.poolWorkspace();await h.start();assert.match(h.module.poolWorkspace(),/原交集名单/);
  h.mode('union');await tick();
  assert.match(h.module.poolWorkspace(),/正在.*更新股票列表/);assert.doesNotMatch(h.module.poolWorkspace(),/原交集名单/);
  rejectUpdate(new Error('隔离查询失败'));await tick();
  assert.match(h.module.poolWorkspace(),/隔离查询失败/);assert.doesNotMatch(h.module.poolWorkspace(),/原交集名单/);
  h.click('screen-retry');await tick();
  const request=h.calls.filter(c=>c[0]==='screenGet').at(-1)[1];assert.equal(request.poolMode,'union');
  assert.equal(count,3);assert.equal(h.calls.filter(c=>c[0]==='screenCreate').length,1);
});

test('opening a separate saved pool task does not replace the standalone workspace result',async()=>{
  const h=harness({activeTaskId:()=> 'research-detail'});
  h.module.poolWorkspace();await h.start();assert.match(h.module.poolWorkspace(),/当前筛选/);
  h.module.result(report('另一个保存任务'));
  const market=h.module.poolWorkspace();
  assert.ok(market.includes('当前筛选'),'opening task details replaced the independently saved market result');
  assert.ok(!market.includes('另一个保存任务'),'research task rows leaked into the market workspace');
  await h.module.screenRefresh();
  assert.equal(h.calls.filter(c=>c[0]==='screenGet').at(-1)[1].id,'screen-1');
});

test('historical data import completing after navigation changes only its originating workspace',async()=>{
  let complete;
  const h=harness({importHistory:()=>new Promise(resolve=>{complete=resolve;})});
  h.module.poolWorkspace();h.click('import-history');h.module.form();
  complete({ok:true,data:{sources:[{id:'isolated-history'}],imported:{id:'isolated-history',coverage:{startDate:'2024-01-01',endDate:'2024-12-31'}}}});await tick();
  h.submit();const research=h.calls.find(c=>c[0]==='create')[1];
  assert.equal(research.selection.source,'local');assert.equal(research.config.endDate,'2026-06-30');
  h.module.poolWorkspace();await h.start();const market=h.calls.find(c=>c[0]==='screenCreate')[1];
  assert.equal(market.selection.source,'history');assert.equal(market.selection.sourceId,'isolated-history');
  assert.equal(market.config.endDate,'2024-12-31');assert.equal(market.config.universe.strictHistory,true);
  assert.deepEqual(h.errors,[]);
});

test('screen rows use yuan-to-wan conversion and retained cutoff for stock charts',async()=>{
  const value=report();value.screenPools.combined.push({symbol:'sh600002',name:'旧报告均额缺失',eligible:true,amount:0});
  const h=harness({screenGet:r=>Promise.resolve({task:{id:r.id},result:copy(value)})});
  h.module.poolWorkspace();await h.start();const html=h.module.poolWorkspace();
  assert.match(html,/当日成交额（万元）/);assert.match(html,/20 日均成交额（万元）/);
  assert.match(html,/<td>12,345<\/td><td>20,000<\/td>/);
  assert.match(html,/旧报告均额缺失[\s\S]*?<td>0<\/td><td>—<\/td>/);
  assert.match(html,/data-research-stock-date="2026-10-09"/);
  h.field('config.poolSettings.trend.window','31');assert.match(h.module.poolWorkspace(),/已保存筛选结果不同|旧设置/);
});

test('standalone pool settings persist separately and copy intentionally into backtest strategies',async()=>{
  const h=harness();h.field('config.initialCapital','765432');
  h.module.poolWorkspace();h.field('config.universe.minAmount20','888');h.layer('structure',false);h.mode('union');await h.start();
  const reopened=harness({storage:h.storage});
  assert.match(reopened.module.poolWorkspace(),/data-pf-field="config.universe.minAmount20"[^>]*value="888"/);
  await reopened.module.screenRefresh();assert.equal(reopened.calls.find(c=>c[0]==='screenGet')[1].id,'screen-1');
  reopened.click('pool-to-research');assert.equal(reopened.module.currentStep(),1);reopened.field('config.startDate','2026-01-01','text');reopened.field('config.endDate','2026-06-30','text');reopened.submit();
  const request=reopened.calls.find(c=>c[0]==='create')[1];
  assert.equal(request.config.initialCapital,765432);assert.equal(request.config.universe.minAmount20,8880000);
  assert.deepEqual(request.config.poolSettings.combined,{pools:['trend','volume'],mode:'union'});
  assert.ok(request.config.strategies.every(row=>row.pool==='combined'));
  assert.deepEqual(reopened.errors,[]);
});

test('screen requests and saved-result validity do not depend on enabled strategies or their parameters',async()=>{
  const h=harness();h.module.form();await h.start();
  const first=h.calls.find(call=>call[0]==='screenCreate')[1];
  for(const strategy of definition.strategies){h.field('enabled.'+strategy.id,false,'boolean');h.field('participates.'+strategy.id,false,'boolean');}
  h.field('params.momentum.minReturn','not-a-number');h.field('config.initialCapital','');
  h.field('config.allocation.mode','single','text');h.field('combinations.weighted.enabled',true,'boolean');
  assert.doesNotMatch(h.module.form(),/当前设置与这份已保存筛选结果不同/);
  await h.start();const second=h.calls.filter(call=>call[0]==='screenCreate').at(-1)[1];
  assert.deepEqual(second,first);
  assert.deepEqual(Object.keys(second.config).sort(),['endDate','lookbackDays','poolSettings','startDate','universe']);
  h.field('kind','poolPreview','text');h.submit();
  const created=h.calls.find(call=>call[0]==='create')[1];
  assert.deepEqual(created.config,first.config);
  assert.deepEqual(h.errors,[]);
});

test('pool explanations show frozen actual values and thresholds, with exclusions and unknown facts separate',async()=>{
  const value=report();
  const condition=(key,label,actual,threshold,unit,passed,extra={})=>({key,label,actual,threshold,unit,passed,status:passed==null?'unknown':passed?'pass':'fail',operator:'>=',required:true,...extra});
  const base={status:'pass',passed:true,inPool:true,conditions:[condition('amount20','20 日平均成交额',20000000,10000000,'元',true),condition('historicalSt','历史 ST',null,false,'',null,{operator:'==',required:false})]};
  const trend={status:'pass',passed:true,inPool:true,rankWindow:40,rankUniverseCount:136,rankUnavailableCount:2,conditions:[condition('windowReturn','趋势窗口涨幅',.1,.05,'ratio',true),condition('returnRank','全池涨幅排名',7,[1,10],'名',true,{operator:'between',enabled:true})]};
  for(const rows of Object.values(value.screenPools))for(const row of rows)row.poolEvidence={base,trend,volume:{status:'pass',passed:true,inPool:true,conditions:[condition('volumeRatio','量能倍数',2,1.2,'倍',true)]},structure:{status:'pass',passed:true,inPool:true,conditions:[condition('drawdown','窗口回撤',.1,[.02,.25],'ratio',true,{operator:'between'})]}};
  const excluded={...copy(value.screenPools.base[0]),eligible:false,reason:'amount20BelowMinimum',memberships:[],poolEvidence:{base:{status:'fail',passed:false,inPool:false,conditions:[condition('amount20','20 日平均成交额',9000000,10000000,'元',false)]}}};
  value.screenPools.excluded=[excluded];
  const h=harness({screenGet:request=>Promise.resolve({task:{id:request.id},result:copy(value)})});h.module.poolWorkspace();await h.start();
  let html=h.module.poolWorkspace();
  assert.match(html,/入池依据（实际值 \/ 条件）/);assert.doesNotMatch(html,/股票池归属 \/ 排除原因|触发的策略/);
  assert.match(html,/10%；要求≥ 5%/);assert.match(html,/2 倍；要求≥ 1.2 倍/);
  assert.match(html,/data-pf-evidence-summary[\s\S]*?全池涨幅排名：7 名；要求位于 1 名 — 10 名[\s\S]*?<details/);
  assert.match(html,/有效样本 136 只 · 历史不足 2 只/);
  assert.match(html,/2,000 万元；要求≥ 1,000 万元/);
  assert.match(html,/历史 ST：数据缺失；要求为 否/);assert.match(html,/资料未提供，当前规则不因此剔除/);
  h.field('config.universe.minAmount20','2500');html=h.module.poolWorkspace();
  const cell=html.split('data-pf-pool-evidence>')[1].split('</td>')[0];
  assert.match(cell,/要求≥ 1,000 万元/);assert.doesNotMatch(cell,/2,500 万元/);
  h.click('screen-pool',{pfPool:'excluded'});await tick();html=h.module.poolWorkspace();
  assert.match(html,/未通过的基础条件/);assert.match(html,/未通过基础资格/);
  assert.match(html,/900 万元；要求≥ 1,000 万元/);assert.doesNotMatch(html,/<th>入池依据/);
  assert.deepEqual(h.errors,[]);
});

test('fine filters toggle independently, convert displayed percentages, and persist into backtest input',async()=>{
  const h=harness();let html=h.module.poolWorkspace();
  assert.match(html,/data-pf-field="config.poolSettings.structure.maxPriceVsMa20"[^>]*disabled/);
  const optional=(key,checked)=>h.dispatch('change',{dataset:{pfOptional:key},checked});
  optional('config.universe.minAmplitude20',true);
  optional('config.poolSettings.structure.maxPriceVsMa20',true);
  optional('config.poolSettings.structure.minMa10Above20Days',true);
  optional('config.poolSettings.volume.minVolumeChange20',true);
  h.field('config.poolSettings.structure.maxPriceVsMa20','12.5');
  h.field('config.poolSettings.structure.maDaysMode','consecutive','text');
  h.field('config.poolSettings.trend.rankEnabled',true,'boolean');
  h.field('config.poolSettings.trend.rankEnd','10');
  h.field('config.poolSettings.volume.excludeUpperShadow',true,'boolean');
  await h.start();
  const request=h.calls.filter(call=>call[0]==='screenCreate').at(-1)[1];
  assert.equal(request.config.universe.minAmplitude20,.03);
  assert.equal(request.config.poolSettings.structure.maxPriceVsMa20,.125);
  assert.equal(request.config.poolSettings.structure.minMa10Above20Days,5);
  assert.equal(request.config.poolSettings.structure.maDaysMode,'consecutive');
  assert.equal(request.config.poolSettings.volume.minVolumeChange20,.05);
  assert.equal(request.config.poolSettings.trend.rankEnd,10);
  assert.equal(request.config.poolSettings.volume.excludeUpperShadow,true);
  const reopened=harness({storage:h.storage});html=reopened.module.poolWorkspace();
  assert.match(html,/data-pf-field="config.poolSettings.structure.maxPriceVsMa20"[^>]*value="12.5"/);
  reopened.click('pool-to-research');reopened.field('config.startDate','2026-01-01','text');reopened.field('config.endDate','2026-06-30','text');reopened.submit();
  const backtest=reopened.calls.find(call=>call[0]==='create')[1];
  assert.deepEqual(backtest.config.poolSettings,request.config.poolSettings);
  optional('config.poolSettings.structure.maxPriceVsMa20',false);await h.start();
  assert.equal(h.calls.filter(call=>call[0]==='screenCreate').at(-1)[1].config.poolSettings.structure.maxPriceVsMa20,null);
  assert.deepEqual(h.errors,[]);assert.deepEqual(reopened.errors,[]);
});

test('old saved pool drafts hydrate fine-filter defaults without changing existing thresholds',async()=>{
  const h=harness();h.module.poolWorkspace();h.field('config.poolSettings.trend.window','31');await h.start();
  const key='atrade.stock-pool.v1',saved=JSON.parse(h.storage.get(key));
  delete saved.draft.config.universe.minAmplitude20;
  const legacy={trend:['window','minReturn'],structure:['window','minDrawdown','maxDrawdown'],volume:['window','minRatio']};
  for(const [pool,keys] of Object.entries(legacy))for(const name of Object.keys(saved.draft.config.poolSettings[pool]))if(!keys.includes(name))delete saved.draft.config.poolSettings[pool][name];
  h.storage.set(key,JSON.stringify(saved));
  const reopened=harness({storage:h.storage});reopened.module.poolWorkspace();await reopened.start();
  const config=reopened.calls.find(call=>call[0]==='screenCreate')[1].config;
  assert.equal(config.poolSettings.trend.window,31);assert.equal(config.poolSettings.trend.rankEnabled,false);
  assert.equal(config.poolSettings.trend.rankWindow,40);assert.equal(config.poolSettings.structure.peakBasis,'close');
  assert.equal(config.poolSettings.structure.minMa10Above20Days,null);assert.equal(config.poolSettings.volume.excludeBlowoff,false);
  assert.equal(config.universe.minAmplitude20,null);
  const frozen=reopened.module.configuration({...config,poolSettings:{...config.poolSettings,trend:{...config.poolSettings.trend,rankEnabled:true,rankStart:2,rankEnd:17}}},{mode:'all'},true);
  assert.match(frozen,/40 日涨幅排名 2 — 17/);assert.doesNotMatch(frozen,/策略预算|初始资金/);
  assert.deepEqual(reopened.errors,[]);
});
