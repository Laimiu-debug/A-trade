/* Frozen research plans and the separate, later-period validation step. */
(() => {
  'use strict';
  const nextDay = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return '';
    const date = new Date(value + 'T00:00:00Z');
    date.setUTCDate(date.getUTCDate() + 1);
    return Number.isFinite(date.valueOf()) ? date.toISOString().slice(0, 10) : '';
  };
  function validationPayload(freeze, draft) {
    if (!freeze?.id) throw new Error('请先选择已冻结的方案。');
    const startDate = String(draft.startDate || ''), endDate = String(draft.endDate || '');
    const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
    if (!validDate(startDate) || !validDate(endDate) || startDate > endDate) throw new Error('请填写有效的新时段起止日期。');
    if (startDate <= freeze.developmentEnd) throw new Error('新时段必须晚于冻结方案的开发截止日。');
    return {kind:'frozenValidation', freezeId:freeze.id, startDate, endDate, name:String(draft.name || '').trim()};
  }
  const helpers = {nextDay, validationPayload};
  if (typeof module === 'object' && module.exports) module.exports = helpers;
  if (typeof window === 'undefined' || typeof AT === 'undefined') return;
  const E = AT.escape;
  let hooks, plans = [], loaded = false, loading = false, selectedId = '', error = '', creating = false;
  const drafts = new Map();
  const selected = () => plans.find(plan => plan.id === selectedId) || plans[0] || null;
  const button = (label, action, attrs = '', primary = false) => `<button type="button" class="rs-button${primary ? ' rs-primary' : ''}" data-workflow-action="${action}" ${attrs}>${E(label)}</button>`;
  const notice = text => `<div class="rs-notice" role="status">${E(text)}</div>`;
  function draftFor(plan) {
    if (!drafts.has(plan.id)) drafts.set(plan.id, {name:'',startDate:nextDay(plan.developmentEnd),endDate:''});
    return drafts.get(plan.id);
  }
  async function load() {
    if (loading || !hooks) return;
    loading = true; error = ''; hooks.refresh();
    try {
      const data = await hooks.call('research.freezes', {});
      plans = Array.isArray(data.freezes) ? data.freezes : [];
      if (!plans.some(plan => plan.id === selectedId)) selectedId = plans[0]?.id || '';
      loaded = true;
    } catch (failure) { error = failure.message || '冻结方案读取失败。'; }
    finally { loading = false; hooks.refresh(); }
  }
  async function freeze(payload) {
    const data = await hooks.call('research.freeze', payload);
    if (!data.freeze?.id) throw new Error('服务没有返回冻结方案。');
    selectedId = data.freeze.id;
    plans = [data.freeze, ...plans.filter(plan => plan.id !== data.freeze.id)];
    loaded = false;
    hooks.feedback('方案已冻结。请选择严格晚于开发期的新时段，验证期间不改参数。');
    AT.navigate('R10');
  }
  function actions(task) {
    if (task?.status !== 'completed') return '';
    if (task.kind === 'singleBacktest') return `<div class="rs-workflow-next">${notice('固定参数回测已完成。下一步只挑少量关键参数做开发期平原测试，其他条件继续固定。')}${button('用此配置做参数平原','plateau',`data-workflow-task="${E(task.id)}"`,true)}</div>`;
    if (task.kind === 'singlePlateau') return notice('先检查相邻参数的表现，再在下方明确选择一个候选并冻结。新时段验证不会重新挑选参数。');
    if (task.kind === 'frozenValidation') return `<div class="rs-workflow-next">${notice('这是冻结参数的新时段验证。结合独立性、成本和回撤判断证据；组合与轮动需要多个已单独研究的策略，并另建研究记录。')}${button('进入高级组合与轮动研究','advanced')}${button('查看冻结方案','freezes')}</div>`;
    return '';
  }
  function validationEvidence(result) {
    if (result?.kind !== 'frozenValidation') return '';
    const audit=result.validationAudit,plan=result.freeze||{};
    const states={fresh:'当前工作区未发现重复使用',reproduction:'同一时段复现，不增加独立证据',developmentSample:'已使用过的开发样本，不能作为新证据'};
    const reasons={previously_used_development_period:'该区间已用于开发或挑选参数。',holdout_reused_with_changed_settings:'该区间曾使用其他设置或数据验证。',same_period_reproduction:'同样设置与数据已运行过该区间。'};
    return `<section class="rs-panel"><header><h2>冻结方案与新时段证据</h2></header><div class="rs-body">${notice(audit?states[audit.status]||'独立性状态待核对':'尚未形成可核对的验证区间。')}<p>冻结方案：${E(plan.name||'—')}；开发截止：${E(plan.developmentEnd||'—')}。</p>${(audit?.reasons||[]).map(reason=>`<p>${E(reasons[reason]||reason)}</p>`).join('')}<p class="rs-help">独立性标记与收益表现分开判断。这里只能核对本工作区留存的使用记录，无法证明该时段从未在其他地方被查看或使用。</p><details class="rs-details"><summary>查看历史区间重用记录</summary><pre>${E(JSON.stringify(audit||{},null,2))}</pre></details></div></section>`;
  }
  function render() {
    const plan = selected();
    const header = `<section class="rs-panel"><header><h2>第 5 阶段 · 冻结后验证新时段</h2>${button('刷新冻结方案','reload',loading?'disabled':'')}</header><div class="rs-body"><p>开发期结束后固定股票范围、漏斗、策略、仓位与交易成本，再单独加载后面的行情。验证失败也保留原方案和结果，修改设置需要开始新一轮研究。</p>${error?notice(error):''}</div></section>`;
    if (!plan) return header + `<section class="rs-panel"><div class="rs-body">${notice(loading?'正在读取冻结方案…':'尚无冻结方案。先完成单策略固定回测和少量参数平原测试，再在结果中选择候选冻结。')}${button('打开单策略研究','single', '', true)}${button('打开参数平原','plateau-page')}</div></section>`;
    const draft = draftFor(plan), config = plan.config || {}, strategies = config.strategies || [];
    const fields = [['name','验证任务名称','text'],['startDate','新时段开始日期','date'],['endDate','新时段结束日期','date']].map(([key,label,type])=>`<label class="rs-field"><span>${label}</span><input type="${type}" data-workflow-field="${key}" value="${E(draft[key])}" ${type==='date'?`required min="${E(nextDay(plan.developmentEnd))}"`:'maxlength="100" placeholder="留空自动命名"'}></label>`).join('');
    return header + `<section class="rs-panel"><header><h2>选择不可修改的冻结方案</h2></header><div class="rs-body"><label class="rs-field"><span>冻结方案</span><select data-workflow-freeze>${plans.map(item=>`<option value="${E(item.id)}" ${item.id===plan.id?'selected':''}>${E(item.name || item.id)} · 开发截至 ${E(item.developmentEnd || '—')}</option>`).join('')}</select></label><div class="rs-metrics"><div class="rs-metric"><span>开发截止</span><strong>${E(plan.developmentEnd || '—')}</strong><small>新验证必须从此日期之后开始</small></div><div class="rs-metric"><span>冻结策略</span><strong>${E(strategies.map(item=>hooks.strategyName?.(item.kind)||item.kind).join('、') || '—')}</strong><small>资金、交易规则和股票池一起冻结</small></div><div class="rs-metric"><span>候选来源</span><strong>${E(plan.candidateId || '固定参数')}</strong><small>${E(plan.createdAt || '')}</small></div></div>${notice(plan.evidence?.label||'开发证据尚未提供，不能认定已形成稳定区域。')}${plan.versionCompatible===false?notice('计算版本已变化，请重新完成开发研究并冻结后再验证。'):''}${notice('冻结只代表参数固定，不代表策略已验证有效。开发期平原与新时段结果会分开保存。')}${window.PortfolioWorkbench?.configuration?.(config,plan.selection)||''}<details class="rs-details"><summary>查看冻结证据与版本</summary><pre>${E(JSON.stringify(plan,null,2))}</pre></details>${plan.sourceTaskId?button('查看开发期研究','source',`data-workflow-task="${E(plan.sourceTaskId)}"`):''}</div></section><section class="rs-panel"><header><h2>仅设置新的验证时段</h2></header><form class="rs-body" data-workflow-validation><div class="rs-fields">${fields}</div><p class="rs-help">此前行情只用于指标预热；验证账户从初始资金开始。重复使用同一时段会如实标记，不算新增独立证据。已有数据源和冻结规则沿用，无需再填写。</p><button type="submit" class="rs-button rs-primary" ${creating||plan.versionCompatible===false?'disabled':''}>${creating?'正在创建…':'按冻结方案验证新时段'}</button></form></section>`;
  }
  document.addEventListener('change', event => {
    if (event.target.matches?.('[data-workflow-freeze]')) {selectedId=event.target.value; error=''; hooks.refresh();}
  });
  document.addEventListener('input', event => {
    const key=event.target.dataset?.workflowField,plan=selected();
    if (key && plan && ['name','startDate','endDate'].includes(key)) draftFor(plan)[key]=event.target.value;
  });
  document.addEventListener('submit', async event => {
    if (!event.target.matches?.('[data-workflow-validation]')) return;
    event.preventDefault(); event.stopPropagation();
    if (creating) return;
    try {const plan=selected();if(plan?.versionCompatible===false)throw new Error('计算版本已变化，请重新完成开发研究并冻结后再验证。');const payload=validationPayload(plan,plan?draftFor(plan):{});creating=true;hooks.refresh();await hooks.create(payload);}
    catch (failure) {hooks.error(failure);}
    finally {creating=false;hooks.refresh();}
  });
  document.addEventListener('click', async event => {
    const target=event.target.closest?.('[data-workflow-action]');
    if (!target || target.disabled || !hooks) return;
    event.preventDefault();
    const action=target.dataset.workflowAction;
    if (action==='reload') {void load();return;}
    if (action==='freezes') {AT.navigate('R10');return;}
    if (action==='plateau-page') {AT.navigate('R05');return;}
    if (action==='source') {hooks.openTask(target.dataset.workflowTask);return;}
    if (action==='single') {window.PortfolioWorkbench.startSingle();AT.navigate('R03');return;}
    if (action==='advanced') {window.PortfolioWorkbench.advanced();AT.navigate('R03');return;}
    if (action==='plateau') {
      try {const data=await hooks.call('research.get',{id:target.dataset.workflowTask});const source=structuredClone(data.task);if(data.result?.config)source.config=structuredClone(data.result.config);window.PortfolioWorkbench.startPlateau(source);AT.navigate('R05');}
      catch (failure) {hooks.error(failure);}
    }
  });
  window.ResearchWorkflow={configure(value){hooks=value;},render,actions,validationEvidence,freeze,mount(){if(!loaded&&!loading&&!error)void load();},openFreezes(){AT.navigate('R10');}};
})();
