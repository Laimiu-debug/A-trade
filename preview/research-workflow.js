/* Saved parameter sets and optional verification, kept beside the result. */
(() => {
  'use strict';
  const Dates=typeof module==='object'&&module.exports?require('./research-date-input.js'):window.ResearchDates;
  const nextDay=Dates.nextDay;
  function validationPayload(plan, draft) {
    if (!plan?.id) throw new Error('请先选择要验证的参数。');
    const {startDate,endDate}=Dates.range(draft.startDate,draft.endDate,'验证起止日期');
    if (startDate<=plan.developmentEnd) throw new Error('验证日期必须晚于原回测结束日期。');
    return {kind:'frozenValidation',freezeId:plan.id,startDate,endDate,name:String(draft.name||'').trim()};
  }
  const helpers={nextDay,validationPayload};
  if(typeof module==='object'&&module.exports)module.exports=helpers;
  if(typeof window==='undefined'||typeof AT==='undefined')return;
  const E=AT.escape,cleanName=value=>String(value||'').replace(/冻结方案/g,'保存参数');
  let hooks,plans=[],loaded=false,loading=false,selectedId='',error='',creating=false,libraryOpen=false,selection=null;
  const drafts=new Map();
  const draftStorage='atrade.validation-dates.v1';
  try{for(const [key,value]of Object.entries(JSON.parse(localStorage.getItem(draftStorage))||{}).slice(-50))if(value&&typeof value==='object')drafts.set(key,{name:String(value.name||''),startDate:String(value.startDate||''),endDate:String(value.endDate||'')});}catch{}
  function saveDrafts(){try{localStorage.setItem(draftStorage,JSON.stringify(Object.fromEntries([...drafts].slice(-50))));}catch{}}
  const button=(label,action,attrs='',primary=false)=>`<button type="button" class="rs-button${primary?' rs-primary':''}" data-workflow-action="${action}" ${attrs}>${E(label)}</button>`;
  const notice=text=>`<div class="rs-notice" role="status">${E(text)}</div>`;
  const selected=()=>plans.find(plan=>plan.id===selectedId)||plans[0]||null;
  const selectionKey=value=>value?.plan?.id||[value?.id,value?.candidateId||'fixed'].join(':');
  function draftFor(value){
    const key=selectionKey(value),plan=value.plan||value;
    if(!drafts.has(key)){drafts.set(key,{name:'',startDate:nextDay(plan.developmentEnd),endDate:''});saveDrafts();}
    return drafts.get(key);
  }
  async function load(){
    if(loading||!hooks)return;loading=true;error='';hooks.refresh();
    try{const data=await hooks.call('research.freezes',{});plans=Array.isArray(data.freezes)?data.freezes:[];if(!plans.some(plan=>plan.id===selectedId))selectedId=plans[0]?.id||'';loaded=true;}
    catch(failure){error=failure.message||'保存参数读取失败。';}
    finally{loading=false;hooks.refresh();}
  }
  async function source(payload){
    const data=await hooks.call('research.get',{id:payload.id}),task=data.task,result=data.result;
    if(!task||task.status!=='completed'||!['singleBacktest','singlePlateau'].includes(task.kind))throw new Error('请等待回测或参数测试完成。');
    let config=result?.config||task.config;
    if(task.kind==='singlePlateau'){
      const point=result?.candidates?.find(row=>row.candidateId===payload.candidateId);
      if(!point?.config)throw new Error('请选择已完成的参数点。');config=point.config;
    }
    return {id:task.id,candidateId:payload.candidateId,name:task.name,config,selection:task.selection,developmentEnd:config.endDate};
  }
  async function save(payload){
    const value=await source(payload),name=(value.name+' · '+(value.candidateId||'固定参数')).slice(0,100);
    const data=await hooks.call('research.freeze',{id:value.id,...(value.candidateId?{candidateId:value.candidateId}:{}),name});
    if(!data.freeze?.id)throw new Error('服务没有返回保存参数。');
    selectedId=data.freeze.id;plans=[data.freeze,...plans.filter(plan=>plan.id!==data.freeze.id)];
    hooks.feedback('参数已保存，可从“参数收藏”再次载入。');return data.freeze;
  }
  async function prepare(payload){
    selection=await source(payload);error='';hooks.refresh();
    // Opening the form does not write a plan. The server fixes the completed
    // source configuration when the user actually starts validation.
    document.querySelector?.('[data-workflow-inline]')?.scrollIntoView?.({block:'start',behavior:'smooth'});
  }
  function validationForm(value){
    const plan=value.plan||value,draft=draftFor(value),old=plan.versionCompatible===false;
    const fields=[['name','任务名称（可留空）','text'],['startDate','验证开始日期','date'],['endDate','验证结束日期','date']].map(([key,label,type])=>type==='date'?Dates.field(label,draft[key],{key:'validation:'+selectionKey(value)+':'+key,attrs:`data-workflow-field="${key}"`,min:nextDay(plan.developmentEnd)}):`<label class="rs-field"><span>${label}</span><input type="${type}" data-workflow-field="${key}" value="${E(draft[key])}" maxlength="100"></label>`).join('');
    return `<section class="rs-panel" data-workflow-inline><header><h2>用其他日期验证${value.candidateId?' · '+E(value.candidateId):''}</h2>${button('收起','close-validation')}</header><div class="rs-body"><p>原回测截至 <strong>${E(plan.developmentEnd)}</strong>。选择此后的一段日期，检查同一组参数在其他行情中的表现。</p><p class="rs-help">软件自动保存所选参数，并沿用股票范围、资金、费用和交易规则。已经使用过的日期会明确标记，不能当成新的验证证据。</p>${old?notice('计算版本已变化，请重新运行原回测后再验证。'):''}<form data-workflow-validation><div class="rs-fields">${fields}</div><div class="rs-actions rs-inline-submit"><button type="submit" class="rs-button rs-primary" ${creating||old?'disabled':''}>${creating?'正在创建…':'开始验证'}</button></div></form><details class="rs-details"><summary>查看本次沿用的参数</summary>${window.PortfolioWorkbench?.configuration?.(plan.config,plan.selection)||''}</details>${error?notice(error):''}</div></section>`;
  }
  function library(){
    if(!libraryOpen)return '';
    const plan=selected();
    return `<section class="rs-panel" data-workflow-library><header><h2>参数收藏</h2><div class="rs-actions">${button('刷新','reload',loading?'disabled':'')}${button('收起','close-library')}</div></header><div class="rs-body">${error?notice(error):''}${!plan?notice(loading?'正在读取保存参数…':'还没有保存参数。回测或参数测试完成后，可以直接点击“保存参数”。'):`<label class="rs-field"><span>选择保存参数</span><select data-workflow-freeze>${plans.map(item=>`<option value="${E(item.id)}" ${item.id===plan.id?'selected':''}>${E(cleanName(item.name)||item.id)} · 原回测截至 ${E(item.developmentEnd)}</option>`).join('')}</select></label><p class="rs-help">载入后可以修改设置并创建新的回测；已有结果保持原来的参数。</p><div class="rs-actions">${button('载入回测配置','apply-saved','',true)}${button('用其他日期验证','validate-saved')}${plan.sourceTaskId?button('查看原结果','source',`data-workflow-task="${E(plan.sourceTaskId)}"`):''}</div><details class="rs-details"><summary>查看保存参数</summary>${window.PortfolioWorkbench?.configuration?.(plan.config,plan.selection)||''}</details>`}</div></section>`;
  }
  function workspace(task){
    const relevant=selection&&(selection.plan||selection.id===task?.id);
    return library()+(relevant?validationForm(selection):'');
  }
  function actions(task){
    if(task?.status!=='completed')return '';
    const attrs=`data-workflow-task="${E(task.id)}"`;
    if(task.kind==='singleBacktest')return `<div class="rs-workflow-next rs-actions">${button('用当前配置测试更多参数','plateau',attrs,true)}${button('保存参数','save-task',attrs)}${button('用其他日期验证','validate-task',attrs)}${button('参数收藏','library')}</div>`;
    if(task.kind==='singlePlateau')return '<p class="rs-help">比较附近参数的收益与回撤，然后直接回填、保存或查看买卖记录。需要检查其他行情时，选择“用其他日期验证”。</p>';
    if(task.kind==='frozenValidation')return `<div class="rs-workflow-next rs-actions">${button('参数收藏','library')}${button('组合与轮动研究','advanced')}</div>`;
    return '';
  }
  function validationEvidence(result){
    if(result?.kind!=='frozenValidation')return '';
    const audit=result.validationAudit,plan=result.freeze||{};
    const states={fresh:'当前工作区未发现这段日期被重复使用',reproduction:'这是相同参数和日期的重复计算，不增加验证证据',developmentSample:'这段日期已经用于选参或其他测试，不能作为新的验证证据'};
    const reasons={previously_used_development_period:'该区间已用于回测或挑选参数。',holdout_reused_with_changed_settings:'该区间曾使用其他设置或数据测试。',same_period_reproduction:'同样设置与数据已运行过该区间。'};
    return `<section class="rs-panel"><header><h2>其他日期验证结果</h2></header><div class="rs-body">${notice(audit?states[audit.status]||'日期使用情况待核对':'尚未形成可核对的日期记录。')}<p>所用参数：${E(cleanName(plan.name)||'—')}；原回测截至 ${E(plan.developmentEnd||'—')}。</p>${(audit?.reasons||[]).map(reason=>`<p>${E(reasons[reason]||reason)}</p>`).join('')}<p class="rs-help">新的日期不等于参数有效，还需结合收益、回撤、交易数量与成交证据判断。这里只能核对本工作区留存的日期记录。</p><details class="rs-details"><summary>查看日期使用记录</summary><pre>${E(JSON.stringify(audit||{},null,2))}</pre></details></div></section>`;
  }
  function render(){libraryOpen=true;return library()+(selection?validationForm(selection):'');}
  document.addEventListener('change',event=>{if(event.target.matches?.('[data-workflow-freeze]')){selectedId=event.target.value;error='';hooks.refresh();}});
  function captureField(event){const key=event.target.dataset?.workflowField;if(key&&selection&&['name','startDate','endDate'].includes(key)){draftFor(selection)[key]=event.target.value;saveDrafts();}}
  document.addEventListener('input',captureField);
  document.addEventListener('change',captureField);
  document.addEventListener('submit',async event=>{
    if(!event.target.matches?.('[data-workflow-validation]'))return;event.preventDefault();event.stopPropagation();if(creating||!selection)return;
    try{
      const value=selection,plan=value.plan||value,draft={...draftFor(value)};
      if(plan.versionCompatible===false)throw new Error('计算版本已变化，请重新运行原回测后再验证。');
      // Validate dates before saving, then submit only the immutable server ID.
      validationPayload({...plan,id:plan.id||'pending'},draft);creating=true;hooks.refresh();
      const stored=value.plan||await save({id:value.id,candidateId:value.candidateId});
      const payload=validationPayload(stored,draft);await hooks.create(payload);selection=null;
    }catch(failure){error=failure.message;hooks.error(failure);}
    finally{creating=false;hooks.refresh();}
  });
  document.addEventListener('click',async event=>{
    const target=event.target.closest?.('[data-workflow-action]');if(!target||target.disabled||!hooks)return;event.preventDefault();
    const action=target.dataset.workflowAction;
    if(action==='reload'){void load();return;}
    if(action==='library'||action==='freezes'){libraryOpen=true;if(!loaded)void load();else hooks.refresh();return;}
    if(action==='close-library'){libraryOpen=false;hooks.refresh();return;}
    if(action==='close-validation'){selection=null;hooks.refresh();return;}
    if(action==='source'){hooks.openTask(target.dataset.workflowTask);return;}
    if(action==='single'){window.PortfolioWorkbench.startSingle();AT.navigate('R03');return;}
    if(action==='plateau-page'){window.PortfolioWorkbench.scanCurrent();AT.navigate('R05');return;}
    if(action==='advanced'){window.PortfolioWorkbench.advanced();AT.navigate('R03');return;}
    try{
      if(action==='save-task'){target.disabled=true;await save({id:target.dataset.workflowTask});return;}
      if(action==='validate-task'){await prepare({id:target.dataset.workflowTask});return;}
      if(action==='validate-saved'){const plan=selected();if(plan){selection={plan};hooks.refresh();}return;}
      if(action==='apply-saved'){const plan=selected();if(plan){window.PortfolioWorkbench.startSingle({name:cleanName(plan.name),config:structuredClone(plan.config),selection:structuredClone(plan.selection)});AT.navigate('R03');hooks.feedback('已载入保存参数，可调整后直接开始回测。');}return;}
      if(action==='plateau'){const data=await hooks.call('research.get',{id:target.dataset.workflowTask});const source=structuredClone(data.task);if(data.result?.config)source.config=structuredClone(data.result.config);window.PortfolioWorkbench.startPlateau(source);AT.navigate('R05');}
    }catch(failure){hooks.error(failure);}finally{target.disabled=false;}
  });
  window.ResearchWorkflow={configure(value){hooks=value;},render,workspace,actions,validationEvidence,save,freeze:save,prepare,mount(){if(!loaded&&!loading&&!error)void load();},openFreezes(){libraryOpen=true;if(!loaded)void load();else hooks.refresh();}};
})();
