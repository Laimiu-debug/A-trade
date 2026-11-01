(() => {
  const E=AT.escape, B=AT.button;
  AT.state=AT.settingsState.normalizeRules(AT.state);
  const feedback={}, errors={}, pending=new Set(), timers={}, revisions={};
  const sections={C02:'fees',C03:'positionRules'};
  const ruleFields={fees:[['commissionRate','佣金费率',10000,'万分之'],['minCommission','最低佣金',1e12,'元']],positionRules:[['singleStockMax','单股仓位上限',100,'%'],['totalMax','总仓位上限',100,'%']]};
  const restored=new Set(Object.keys(AT.state.settingsRuleDrafts||{}));
  const draftFor=section=>AT.state.settingsRuleDrafts?.[section];
  const nextRevision=section=>(revisions[section]=(revisions[section]||0)+1);
  const withoutDraft=section=>{const result={...AT.state.settingsRuleDrafts};delete result[section];return result;};
  const ruleSources=new Map();
  const rememberRuleSource=section=>ruleSources.set(section,{active:AT.state[section],draft:draftFor(section)});
  Object.values(sections).forEach(rememberRuleSource);
  function syncRuleSource(section) {
    const previous=ruleSources.get(section);
    if(previous.active===AT.state[section]&&previous.draft===draftFor(section))return false;
    // Restoring a backup replaces these objects. Old feedback belongs to the old draft.
    clearTimeout(timers[section]);nextRevision(section);delete feedback[section];delete errors[section];
    if(draftFor(section))restored.add(section);else restored.delete(section);
    rememberRuleSource(section);return true;
  }
  let diagnostic=null, diagnosticError='', diagnosticBusy=false, requested=false;
  const nav=page=>`<nav class="context-nav" aria-label="相关设置">${AT.groups.find(g=>g.id==='C').pages.map(p=>`<button type="button" data-nav="${p.id}" ${p.id===page?'aria-current="page"':''}>${AT.icon(p.icon)}${E(p.title)}</button>`).join('')}</nav>`;
  function rulesView(page) {
    const section=sections[page];syncRuleSource(section);
    const fees=section==='fees', draft=draftFor(section), values=draft||AT.state[section], busy=pending.has(section);
    const message=feedback[section]||(draft?(restored.has(section)?'已恢复上次草稿，尚未应用；确认后保存参数。':'草稿尚未应用；确认后保存参数。'):'更改后点击“保存参数”应用。');
    return AT.head(AT.pages[page].title,fees?'设置新建回测的默认费用与账户核对参数':'设置账户计划的仓位核对上限')+nav(page)+`<section class="panel settings-form-panel"><form id="rule-form" data-rule-section="${section}" class="panel-body" aria-busy="${busy}" novalidate><div class="settings-rule-heading"><h2>${fees?'费用参数':'仓位参数'}</h2><span class="settings-rule-state" data-rule-state data-state="${draft?'draft':'saved'}">${draft?'草稿 · 尚未应用':'当前参数'}</span></div><p class="settings-rule-description">${fees?'佣金费率作为新建策略回测的默认单边费用，已有回测保持原参数。最低佣金供账户核对，实际费用按交割单填写。':'用于核对账户计划。单股上限不能高于总仓位上限；保存参数不会调整已有持仓或账户流水。'}</p>${AT.state[section+'NeedsReview']?`<div class="notice settings-migration">待核对的历史共用参数：${E(AT.state.ruleA??'未填写')} / ${E(AT.state.ruleB??'未填写')}。旧值已保留供核对，请确认后保存本页参数。</div>`:''}<div class="field-row section-space">${ruleFields[section].map(([key,label,max,unit])=>`<label class="field"><span>${label}（${unit}）</span><input name="${key}" type="number" min="0" max="${max}" step="any" value="${E(values[key])}" required aria-describedby="rule-current-${key} rule-error-${key}" ${errors[section]?.[key]?'aria-invalid="true"':''} ${busy?'disabled':''}><small class="settings-current-value" id="rule-current-${key}">当前已生效：${E(AT.state[section][key])} ${unit}</small><small class="settings-field-error" id="rule-error-${key}" data-rule-error="${key}" ${errors[section]?.[key]?'':'hidden'}>${E(errors[section]?.[key]||'')}</small></label>`).join('')}</div><p class="field-hint">${fees?'万分之 2.5 等于 0.025%。两项均可填写 0。':'两项范围均为 0—100%，允许填写 0。'}</p><div class="settings-form-actions">${B(busy?'正在保存…':'保存参数','save',`type="submit" ${busy?'disabled':''}`,'primary')}${B('放弃修改','refresh',`data-rule-discard="${section}" ${busy||!draft?'disabled':''}`)}</div><p class="settings-feedback" data-rule-feedback role="status" aria-live="polite">${E(message)}</p><p class="field-hint settings-draft-hint">输入会自动保留为草稿，可切页或关闭后继续。只有“保存参数”会应用修改。</p></form></section>`;
  }
  function updateFeedback(section, message) {
    feedback[section]=message;
    const form=document.querySelector('#rule-form');
    if(form?.dataset.ruleSection===section)form.querySelector('[data-rule-feedback]').textContent=message;
  }
  function displayErrors(form, invalid) {
    const section=form.dataset.ruleSection;errors[section]=invalid;
    for(const input of form.querySelectorAll('input[name]')){
      const message=invalid[input.name],hint=form.querySelector(`[data-rule-error="${input.name}"]`);
      if(message)input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');
      hint.textContent=message||'';hint.hidden=!message;
    }
    const first=Object.keys(invalid)[0];
    if(first){updateFeedback(section,invalid[first]);const input=form.elements[first];input.focus({preventScroll:true});input.scrollIntoView({block:'nearest',behavior:'instant'});}
  }
  function scheduleDraft(section) {
    clearTimeout(timers[section]);const revision=nextRevision(section);
    timers[section]=setTimeout(async()=>{
      if(syncRuleSource(section))return;
      if(AT.ui.committingSettings||AT.ui.restoringBackup){scheduleDraft(section);return;}
      let saved=false;try{saved=await AT.persist();}catch{}
      if(revisions[section]!==revision||!draftFor(section))return;
      if(!saved)updateFeedback(section,'草稿暂未保存，请重试；当前输入已保留。');
      else if(!Object.keys(errors[section]||{}).length)updateFeedback(section,'草稿已保留，尚未应用；确认后保存参数。');
    },250);
  }
  async function saveRules(form) {
    const section=form.dataset.ruleSection;if(!Object.values(sections).includes(section)||pending.has(section))return;
    if(AT.ui.committingSettings||AT.ui.restoringBackup){AT.toast('正在保存其他设置，请稍后重试');return false;}
    const invalid={};
    for(const [key,label,max] of ruleFields[section]){
      const input=form.elements[key];
      if(input.value.trim()===''||input.validity.badInput)invalid[key]=`请填写${label}。`;
      else if(!Number.isFinite(Number(input.value))||Number(input.value)<0||Number(input.value)>max)invalid[key]=`${label}须在 0—${max} 之间。`;
    }
    const values=Object.fromEntries([...form.querySelectorAll('input[name]')].map(input=>[input.name,Number(input.value)]));
    if(section==='positionRules'&&!Object.keys(invalid).length&&values.singleStockMax>values.totalMax)invalid.singleStockMax='单股仓位上限不能高于总仓位上限。';
    displayErrors(form,invalid);if(Object.keys(invalid).length)return false;
    try{AT.settingsState.validateRules({...AT.state,[section]:values});}
    catch(error){updateFeedback(section,error.message);return false;}
    clearTimeout(timers[section]);nextRevision(section);
    const candidate={...AT.state,[section]:values,[section+'NeedsReview']:false,settingsRuleDrafts:withoutDraft(section)};
    pending.add(section);AT.ui.committingSettings=true;
    feedback[section]='正在保存…';AT.render();
    let saved=false;
    try{saved=await AT.persist(candidate);}
    catch{}
    finally{pending.delete(section);AT.ui.committingSettings=false;}
    if(saved){AT.state[section]=values;AT.state[section+'NeedsReview']=false;AT.state.settingsRuleDrafts=withoutDraft(section);restored.delete(section);rememberRuleSource(section);}
    feedback[section]=saved?'参数已保存。':'保存失败，草稿已保留；可重试或放弃修改。';
    // Preserve navigation and other UI changes made while the candidate was being saved.
    if(saved&&!await AT.persist())feedback[section]='参数已保存，其他界面更改尚未保存，请重试。';
    if(sections[AT.state.page]===section)AT.render();
    return saved;
  }
  async function discardRules(section) {
    if(!draftFor(section)||pending.has(section)||AT.ui.committingSettings||AT.ui.restoringBackup)return false;
    clearTimeout(timers[section]);nextRevision(section);
    const candidate={...AT.state,settingsRuleDrafts:withoutDraft(section)};
    pending.add(section);AT.ui.committingSettings=true;feedback[section]='正在放弃草稿…';AT.render();
    let saved=false;
    try{saved=await AT.persist(candidate);}catch{}
    finally{pending.delete(section);AT.ui.committingSettings=false;}
    if(saved){AT.state.settingsRuleDrafts=withoutDraft(section);delete errors[section];restored.delete(section);rememberRuleSource(section);}
    feedback[section]=saved?'已放弃修改，恢复当前已生效参数。':'草稿清除失败，输入仍保留，请重试。';
    if(saved&&!await AT.persist())feedback[section]='草稿已放弃，其他界面更改尚未保存，请重试。';
    if(sections[AT.state.page]===section)AT.render();
    return saved;
  }
  const date=value=>value?new Date(value).toLocaleString('zh-CN'):'尚无记录';
  const item=(label,value,detail='')=>`<div class="settings-status-item"><span>${E(label)}</span><strong>${E(value)}</strong>${detail?`<small>${E(detail)}</small>`:''}</div>`;
  function diagnosticsView() {
    const d=diagnostic;
    return AT.head('应用与诊断','查看本机服务、保存状态与最近检测结果',B('刷新状态','refresh',`data-diagnostics="refresh" ${diagnosticBusy?'disabled':''}`)+B('复制脱敏诊断','copy',`data-diagnostics="copy" ${diagnosticBusy||!AT.isDesktop?'disabled':''}`))+nav('C06')+
      `<section class="panel settings-diagnostics"><div class="panel-head"><h2>A-trade</h2><span class="tag">${E(d?'版本 '+d.app.version:AT.isDesktop?'桌面应用':'浏览器预览')}</span></div><div class="panel-body"><p class="field-hint">${AT.isDesktop?'账户与复盘保存在本机账本，行情按已配置的数据源读取。刷新状态不会主动测试外部连接。':'浏览器预览使用独立的浏览器存储；连接与服务诊断需在桌面应用中查看。'}</p>${diagnosticError?`<p class="settings-feedback" role="alert">${E(diagnosticError)}</p>`:''}<div class="settings-status-grid">${d?[
      item('账户服务',d.services.account?'进程运行':'不可用'),item('行情服务',d.services.market?'本机接口可用':'读取失败'),item('研究服务',d.services.research?'进程运行':'不可用'),
      item('界面保存',d.storage.state==='saved'?'已保存':d.storage.state==='error'?'保存失败':'本次尚未保存',date(d.storage.savedAt)),
      item('本地日线',d.local.configured?(d.local.available?'目录可用':'目录不可用'):'未配置','最近扫描：'+date(d.local.lastScanAt)),
      item('通达信 MCP',d.online.enabled?(d.online.connected?'连接可用':'尚未连接'):'未启用','最近检测：'+date(d.online.lastCheckedAt)),
      ...['text','vision'].map(target=>item(target==='text'?'文字 AI':'图片 AI',d.ai[target].enabled?(d.ai[target].configured?'已启用':'配置不完整'):'未启用','连接结果见 AI 模型设置')),
      item('运行环境',`${d.app.platform} / ${d.app.arch}`,`Electron ${d.app.electron} · Node ${d.app.node}`)
      ].join(''):item('运行状态',diagnosticBusy?'正在读取…':AT.isDesktop?'等待检查':'独立预览')}</div>${d?.storage.recoveryWarning?'<div class="notice">启动时检测到工作区恢复提示，请检查本地文件与备份。</div>':''}<p class="field-hint">状态采集：${E(date(d?.checkedAt))}</p><p class="field-hint section-space">复制内容仅包含版本、服务状态与时间，不包含账户、持仓、目录、接口地址、模型名称或密钥。</p><div class="settings-form-actions">${B('数据源','database','data-nav="C01"')}${B('AI 模型','tools','data-nav="C07"')}${B('备份与恢复','backup','data-nav="C04"')}</div></div></section>${AT.isDesktop?`<section class="panel settings-diagnostics"><div class="panel-body"><h2>本机数据位置</h2><p class="settings-local-path">${E(AT.storageInfo?.directory||'暂不可用')}</p>${B('打开数据文件夹','folder','data-action="open-data-folder"')}</div></section>`:''}`;
  }
  async function refresh(copy=false) {
    if(diagnosticBusy||!AT.isDesktop)return;
    requested=true;diagnosticBusy=true;diagnosticError='';if(AT.state.page==='C06')AT.render();
    try {
      const result=await window.atradeDesktop.diagnostics(copy);
      if(!result?.ok)throw new Error(result?.error||'读取诊断信息失败');
      diagnostic=result.data;if(copy)AT.toast('已复制脱敏诊断信息');
    }catch(error){diagnosticError=error.message;}
    finally{diagnosticBusy=false;if(AT.state.page==='C06')AT.render();}
  }
  const priorView=AT.view;
  AT.view=page=>sections[page]?rulesView(page):page==='C06'?diagnosticsView():priorView(page);
  document.addEventListener('input',event=>{
    const form=event.target.closest('#rule-form');if(!form)return;
    const section=form.dataset.ruleSection;if(pending.has(section))return;
    AT.state.settingsRuleDrafts={...AT.state.settingsRuleDrafts,[section]:Object.fromEntries([...form.querySelectorAll('input[name]')].map(input=>[input.name,input.value]))};
    rememberRuleSource(section);
    restored.delete(section);displayErrors(form,{});
    updateFeedback(section,'正在保留草稿，尚未应用…');
    const badge=form.querySelector('[data-rule-state]');badge.dataset.state='draft';badge.textContent='草稿 · 尚未应用';
    form.querySelector('[data-rule-discard]').disabled=false;scheduleDraft(section);
  });
  document.addEventListener('click',event=>{
    const el=event.target.closest('[data-diagnostics],[data-rule-discard]');if(!el||el.disabled)return;
    if(el.dataset.diagnostics)refresh(el.dataset.diagnostics==='copy');
    if(el.dataset.ruleDiscard)void discardRules(el.dataset.ruleDiscard);
  });
  AT.settingsPage={saveRules,rulesView,mount(){if(AT.state.page==='C06'&&AT.isDesktop&&!requested)refresh();}};
})();
