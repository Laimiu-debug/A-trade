const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {nextDay,validationPayload}=require('../preview/research-workflow.js');
const freeze={id:'freeze-one',name:'动量方案',developmentEnd:'2025-12-31',sourceTaskId:'development',candidateId:'p003',config:{initialCapital:100000,strategies:[{id:'mom',kind:'momentum',params:{lookback:20}}]},selection:{mode:'all'}};

test('frozen validation request includes only identity and later dates, never editable configuration',()=>{
  const request=validationPayload(freeze,{startDate:'2026-01-01',endDate:'2026-03-31',name:'验证',config:{initialCapital:200},selection:{mode:'explicit'},experiment:{axes:[]}});
  assert.deepEqual(request,{kind:'frozenValidation',freezeId:'freeze-one',startDate:'2026-01-01',endDate:'2026-03-31',name:'验证'});
  for(const [startDate,endDate] of [['2025-12-31','2026-03-31'],['2026-01-02','2026-01-01'],['2026-02-30','2026-03-31'],['','2026-03-31']])assert.throws(()=>validationPayload(freeze,{startDate,endDate}));
  assert.throws(()=>validationPayload(null,{}),/先选择/);
  assert.equal(nextDay('2025-12-31'),'2026-01-01');assert.equal(nextDay('2024-02-28'),'2024-02-29');
});

function harness(plans=[freeze]) {
  const events=new Map(),calls=[],created=[],notes=[],navigated=[];
  const document={addEventListener(type,fn){const listeners=events.get(type)||[];listeners.push(fn);events.set(type,listeners);}};
  const AT={escape:value=>String(value??'').replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c])),navigate:page=>navigated.push(page)};
  const wb={startSingle(){},advanced(){},startPlateau(task){notes.push(task);},configuration:()=>'<div>冻结配置</div>'},window={PortfolioWorkbench:wb};
  vm.runInNewContext(fs.readFileSync(require.resolve('../preview/research-workflow.js'),'utf8'),{window,AT,document,console,structuredClone});
  const ui=window.ResearchWorkflow;
  ui.configure({async call(method,payload){calls.push({method,payload});if(method==='research.freezes')return {freezes:plans};if(method==='research.freeze')return {freeze};if(method==='research.get')return {task:{id:payload.id,kind:'singleBacktest'}};throw Error(method);},create:async payload=>created.push(payload),refresh(){},error:err=>notes.push(err.message),feedback:text=>notes.push(text),openTask:id=>notes.push(id)});
  const dispatch=(type,target)=>Promise.all((events.get(type)||[]).map(fn=>fn({target,preventDefault(){},stopPropagation(){}})));
  return {ui,calls,created,notes,navigated,dispatch,async loaded(){ui.mount();await new Promise(setImmediate);}};
}

test('freeze library renders preserved plans and exposes only later-period validation inputs',async()=>{
  const h=harness();await h.loaded();const html=h.ui.render();
  assert.match(html,/2025-12-31/);assert.match(html,/data-workflow-field="startDate" value="2026-01-01"/);
  assert.match(html,/data-workflow-field="endDate"/);assert.doesNotMatch(html,/data-workflow-field="(?:config|lookback|initialCapital)/);
  assert.match(html,/开发期平原与新时段结果会分开保存/);
  await h.dispatch('input',{dataset:{workflowField:'endDate'},value:'2026-03-31'});
  await h.dispatch('submit',{matches:selector=>selector==='[data-workflow-validation]'});
  assert.equal(h.created.length,1);assert.equal(h.created[0].freezeId,freeze.id);assert.equal(h.created[0].config,undefined);
});

test('freezing routes to the saved plan and workflow actions follow completed stages',async()=>{
  const h=harness([]);await h.loaded();assert.match(h.ui.render(),/尚无冻结方案/);
  await h.ui.freeze({id:'development',candidateId:'p003'});assert.equal(h.navigated.at(-1),'R10');assert.match(h.ui.render(),/动量方案/);
  assert.equal(h.ui.actions({status:'running',kind:'singleBacktest'}),'');
  assert.match(h.ui.actions({id:'one',status:'completed',kind:'singleBacktest'}),/用此配置做参数平原/);
  assert.match(h.ui.actions({status:'completed',kind:'frozenValidation'}),/高级组合与轮动/);
  await h.dispatch('click',{closest:()=>({disabled:false,dataset:{workflowAction:'plateau',workflowTask:'one'}})});
  assert.equal(h.navigated.at(-1),'R05');assert.equal(h.notes.at(-1).id,'one');
});

test('freeze names and audit text cannot inject HTML',async()=>{
  const h=harness([{...freeze,name:'<img src=x>',candidateId:'<svg>',config:{...freeze.config,note:'<script>bad</script>'}}]);await h.loaded();
  const html=h.ui.render();assert.doesNotMatch(html,/<img|<svg>|<script>/);assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;/);
});

test('changed computation versions cannot submit an old frozen plan',async()=>{
  const h=harness([{...freeze,versionCompatible:false}]);await h.loaded();
  assert.match(h.ui.render(),/disabled>按冻结方案验证新时段/);
  await h.dispatch('input',{dataset:{workflowField:'endDate'},value:'2026-03-31'});
  await h.dispatch('submit',{matches:selector=>selector==='[data-workflow-validation]'});
  assert.equal(h.created.length,0);assert.match(h.notes.at(-1),/计算版本已变化/);
});

test('validation independence labels remain separate from performance and expose contamination',()=>{
  const h=harness();
  const render=(status,reasons=[])=>h.ui.validationEvidence({kind:'frozenValidation',freeze,validationAudit:{status,reasons}});
  assert.match(render('fresh'),/当前工作区未发现重复使用/);
  assert.match(render('fresh'),/独立性标记与收益表现分开判断/);
  assert.match(render('reproduction',['same_period_reproduction']),/同样设置与数据已运行过/);
  assert.match(render('developmentSample',['previously_used_development_period']),/已用于开发或挑选参数/);
  assert.doesNotMatch(render('fresh'),/验证通过|策略有效/);
});
