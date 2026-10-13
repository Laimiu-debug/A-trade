const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {nextDay,validationPayload}=require('../preview/research-workflow.js');
const escape=value=>String(value??'').replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
const freeze={id:'freeze-one',name:'动量参数',developmentEnd:'2025-12-31',sourceTaskId:'development',candidateId:'p003',config:{initialCapital:100000,endDate:'2025-12-31',topN:3,strategies:[{id:'momentum',kind:'momentum',params:{lookback:20}}]},selection:{mode:'all'}};

function harness(plans=[freeze],options={}){
 const events=new Map(),calls=[],created=[],notes=[],navigated=[],applied=[];
 const document={addEventListener(type,fn){const rows=events.get(type)||[];rows.push(fn);events.set(type,rows);}};
 const AT={escape,navigate:page=>navigated.push(page)};
 const wb={startSingle(task){applied.push(task);},advanced(){},scanCurrent(){},startPlateau(task){applied.push(task);},configuration:config=>'<div>'+escape(JSON.stringify(config))+'</div>'},window={PortfolioWorkbench:wb,ResearchDates:require('../preview/research-date-input.js')};
 vm.runInNewContext(fs.readFileSync(require.resolve('../preview/research-workflow.js'),'utf8'),{window,AT,document,console,structuredClone});
 const ui=window.ResearchWorkflow;
 ui.configure({async call(method,payload){calls.push({method,payload});if(method==='research.freezes')return {freezes:plans};if(method==='research.freeze'){if(options.saveWait)await options.saveWait;return {freeze};}if(method==='research.get')return {task:{id:payload.id,name:'原回测',kind:'singlePlateau',status:'completed',config:freeze.config,selection:freeze.selection},result:{config:freeze.config,candidates:[{candidateId:'p003',config:{...freeze.config,topN:4}}]}};throw Error(method);},create:async payload=>created.push(payload),refresh(){},error:err=>notes.push(err.message),feedback:text=>notes.push(text),openTask:id=>notes.push(id)});
 const dispatch=(type,target)=>Promise.all((events.get(type)||[]).map(fn=>fn({target,preventDefault(){},stopPropagation(){}})));
 const click=(action,extra={})=>dispatch('click',{closest:()=>({disabled:false,dataset:{workflowAction:action,...extra}})});
 const fill=(field,value)=>dispatch('input',{dataset:{workflowField:field},value});
 const submit=()=>dispatch('submit',{matches:selector=>selector==='[data-workflow-validation]'});
 return {ui,calls,created,notes,navigated,applied,dispatch,click,fill,submit,async loaded(){ui.mount();await new Promise(setImmediate);}};
}

test('validation submits only a saved identity and later dates, never editable parameters',()=>{
 assert.deepEqual(validationPayload(freeze,{startDate:'2026-01-01',endDate:'2026-03-31',name:'验证',config:{initialCapital:1},experiment:{axes:[]}}),{kind:'frozenValidation',freezeId:'freeze-one',startDate:'2026-01-01',endDate:'2026-03-31',name:'验证'});
 for(const [startDate,endDate]of [['2025-12-31','2026-03-31'],['2026-01-02','2026-01-01'],['2026-02-30','2026-03-31'],['','2026-03-31']])assert.throws(()=>validationPayload(freeze,{startDate,endDate}));
 assert.throws(()=>validationPayload(null,{}),/先选择/);assert.equal(nextDay('2025-12-31'),'2026-01-01');assert.equal(nextDay('2024-02-28'),'2024-02-29');
});

test('parameter library preserves existing plans without introducing a separate freeze workflow',async()=>{
 const h=harness();await h.loaded();let html=h.ui.render();assert.match(html,/参数收藏/);assert.match(html,/载入回测配置/);assert.doesNotMatch(html,/冻结|第 5 阶段|data-workflow-validation/);
 await h.click('validate-saved');html=h.ui.workspace();assert.match(html,/data-workflow-inline/);assert.match(html,/data-workflow-field="startDate" value="2026-01-01"/);
 assert.match(html,/data-workflow-field="endDate"/);assert.doesNotMatch(html,/data-workflow-field="(?:config|lookback|initialCapital)/);
 await h.fill('endDate','2026-03-31');await h.submit();assert.equal(h.created[0].freezeId,freeze.id);assert.equal(h.calls.some(row=>row.method==='research.freeze'),false);
});

test('saving a selected point stays in the current workspace and uses server-side source identity',async()=>{
 const h=harness([]);await h.loaded();assert.match(h.ui.render(),/还没有保存参数/);
 await h.ui.save({id:'development',candidateId:'p003',config:{initialCapital:1}});
 assert.deepEqual(h.navigated,[]);const request=h.calls.find(row=>row.method==='research.freeze').payload;
 assert.equal(request.id,'development');assert.equal(request.candidateId,'p003');assert.equal(request.config,undefined);assert.doesNotMatch(request.name,/冻结/);assert.match(h.ui.render(),/动量参数/);
 assert.equal(h.ui.actions({status:'running',kind:'singleBacktest'}),'');assert.match(h.ui.actions({id:'one',status:'completed',kind:'singleBacktest'}),/测试更多参数|保存参数|用其他日期验证/);
});

test('opening validation is read-only and submission automatically saves before creating the later test',async()=>{
 const h=harness([]);await h.ui.prepare({id:'development',candidateId:'p003'});
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].method,'research.get');assert.deepEqual(h.navigated,[]);
 const html=h.ui.workspace({id:'development'});assert.match(html,/用其他日期验证 · p003/);assert.match(html,/&quot;topN&quot;:4/);assert.doesNotMatch(html,/冻结/);
 await h.fill('startDate','2026-01-02');await h.fill('endDate','2026-03-31');await h.submit();
 assert.equal(h.calls.filter(row=>row.method==='research.freeze').length,1);assert.equal(h.created.length,1);assert.equal(h.created[0].freezeId,freeze.id);assert.equal(h.created[0].config,undefined);assert.equal(h.created[0].startDate,'2026-01-02');
});

test('invalid dates never save a new parameter record or create a validation task',async()=>{
 const h=harness([]);await h.ui.prepare({id:'development',candidateId:'p003'});await h.fill('startDate','2025-12-31');await h.fill('endDate','2026-03-31');await h.submit();
 assert.equal(h.calls.some(row=>row.method==='research.freeze'),false);assert.equal(h.created.length,0);assert.match(h.notes.at(-1),/晚于原回测/);
});

test('rapid repeated validation submits create one task while the automatic save is in flight',async()=>{
 let release;const saveWait=new Promise(resolve=>{release=resolve;}),h=harness([],{saveWait});
 await h.ui.prepare({id:'development',candidateId:'p003'});await h.fill('endDate','2026-03-31');const first=h.submit();await new Promise(setImmediate);await h.submit();release();await first;
 assert.equal(h.calls.filter(row=>row.method==='research.freeze').length,1);assert.equal(h.created.length,1);
});

test('old computation versions block saved parameter validation but allow a fresh editable backtest',async()=>{
 const h=harness([{...freeze,versionCompatible:false}]);await h.loaded();await h.click('validate-saved');assert.match(h.ui.workspace(),/disabled>开始验证/);
 await h.fill('endDate','2026-03-31');await h.submit();assert.equal(h.created.length,0);assert.match(h.notes.at(-1),/计算版本已变化/);
 await h.click('apply-saved');assert.equal(h.navigated.at(-1),'R03');assert.equal(h.applied[0].config.topN,3);assert.equal(h.applied[0].selection.mode,'all');assert.notEqual(h.applied[0].config,freeze.config);
});

test('parameter names and configuration cannot inject markup into the library',async()=>{
 const h=harness([{...freeze,name:'<img src=x>',config:{...freeze.config,note:'<script>bad</script>'}}]);await h.loaded();const html=h.ui.render();assert.doesNotMatch(html,/<img|<script>/);assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;/);
});

test('date reuse explanations remain independent of positive or negative performance',()=>{
 const h=harness(),render=(status,reasons=[])=>h.ui.validationEvidence({kind:'frozenValidation',freeze,validationAudit:{status,reasons}});
 assert.match(render('fresh'),/未发现这段日期被重复使用/);assert.match(render('fresh'),/新的日期不等于参数有效/);assert.doesNotMatch(render('fresh'),/验证通过|策略有效|冻结/);
 assert.match(render('reproduction',['same_period_reproduction']),/同样设置与数据已运行过/);assert.match(render('developmentSample',['previously_used_development_period']),/用于回测或挑选参数/);
});
