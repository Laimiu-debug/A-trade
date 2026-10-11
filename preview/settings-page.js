(() => {
  const E=AT.escape, B=AT.button;
  AT.state=AT.settingsState.normalizeRules(AT.state);
  const drafts={}, feedback={}, pending=new Set();
  const sections={C02:'fees',C03:'positionRules'};
  let diagnostic=null, diagnosticError='', diagnosticBusy=false, requested=false;
  const nav=page=>`<nav class="context-nav" aria-label="相关设置">${AT.groups.find(g=>g.id==='C').pages.map(p=>`<button type="button" data-nav="${p.id}" ${p.id===page?'aria-current="page"':''}>${AT.icon(p.icon)}${E(p.title)}</button>`).join('')}</nav>`;
  function rulesView(page) {
    const section=sections[page], fees=section==='fees', values=drafts[section]||AT.state[section], busy=pending.has(section);
    const fields=fees?[['commissionRate','佣金费率（万分之）',10000],['minCommission','最低佣金（元）',1e12]]:[['singleStockMax','单股仓位上限（%）',100],['totalMax','总仓位上限（%）',100]];
    return AT.head(AT.pages[page].title,'分别保存费率与仓位参数，互不覆盖')+nav(page)+`<section class="panel settings-form-panel"><form id="rule-form" data-rule-section="${section}" class="panel-body" aria-busy="${busy}"><p class="field-hint">当前仅保存参数，尚未自动应用到账本费用或研究计算；成交费用仍按交割单填写。</p>${AT.state[section+'NeedsReview']?`<div class="notice settings-migration">旧版共用参数：${E(AT.state.ruleA??'未填写')} / ${E(AT.state.ruleB??'未填写')}。无法判断原所属分类，已保留旧值供核对；请分别确认并保存本页参数。</div>`:''}<div class="field-row section-space">${fields.map(([key,label,max])=>`<label class="field"><span>${label}</span><input name="${key}" type="number" min="0" max="${max}" step="any" value="${E(values[key])}" required ${busy?'disabled':''}></label>`).join('')}</div><p class="field-hint">${fees?'万分之 2.5 等于 0.025%；允许填写 0，最低佣金单位为元。':'范围为 0—100%，单股上限不能高于总仓位上限。允许填写 0。'}</p><div class="settings-form-actions">${B(busy?'正在保存…':'保存参数','save',`type="submit" ${busy?'disabled':''}`,'primary')}${B('放弃修改','refresh',`data-rule-discard="${section}" ${busy||!drafts[section]?'disabled':''}`)}</div><p class="settings-feedback" data-rule-feedback role="status" aria-live="polite">${E(feedback[section]||'更改后点击保存，仅影响本页参数。')}</p></form></section>`;
  }
  async function saveRules(form) {
    const section=form.dataset.ruleSection;if(!Object.values(sections).includes(section)||pending.has(section))return;
    if(AT.ui.committingSettings||AT.ui.restoringBackup){AT.toast('正在保存其他设置，请稍后重试');return false;}
    if(!form.reportValidity())return;
    const values=Object.fromEntries([...form.querySelectorAll('input[name]')].map(input=>[input.name,Number(input.value)]));
    try{AT.settingsState.validateRules({...AT.state,[section]:values});}
    catch(error){feedback[section]=error.message;form.querySelector('[data-rule-feedback]').textContent=error.message;return false;}
    const candidate={...AT.state,[section]:values,[section+'NeedsReview']:false};
    pending.add(section);AT.ui.committingSettings=true;
    feedback[section]='正在保存…';AT.render();
    let saved=false;
    try{saved=await AT.persist(candidate);}
    catch{}
    finally{pending.delete(section);AT.ui.committingSettings=false;}
    if(saved){AT.state[section]=values;AT.state[section+'NeedsReview']=false;delete drafts[section];}
    feedback[section]=saved?'参数已保存。':'保存失败，草稿已保留；可重试或放弃修改。';
    // Preserve navigation and other UI changes made while the candidate was being saved.
    if(saved&&!await AT.persist())feedback[section]='参数已保存，其他界面更改尚未保存，请重试。';
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
    const section=form.dataset.ruleSection;drafts[section]=Object.fromEntries([...form.querySelectorAll('input[name]')].map(input=>[input.name,input.value]));
    feedback[section]='有未保存的更改';form.querySelector('[data-rule-feedback]').textContent=feedback[section];form.querySelector('[data-rule-discard]').disabled=false;
  });
  document.addEventListener('click',event=>{
    const el=event.target.closest('[data-diagnostics],[data-rule-discard]');if(!el||el.disabled)return;
    if(el.dataset.diagnostics)refresh(el.dataset.diagnostics==='copy');
    if(el.dataset.ruleDiscard){delete drafts[el.dataset.ruleDiscard];feedback[el.dataset.ruleDiscard]='已放弃未保存的修改';AT.render();}
  });
  AT.settingsPage={saveRules,rulesView,mount(){if(AT.state.page==='C06'&&AT.isDesktop&&!requested)refresh();}};
})();
