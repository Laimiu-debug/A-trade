/* Global AI routes are edited here; account workflows only consume saved profiles. */
(() => {
  const E=AT.escape,I=AT.icon,B=AT.button,bridge=window.atradeDesktop;
  const $=(selector,root=document)=>root.querySelector(selector);
  const $$=(selector,root=document)=>[...root.querySelectorAll(selector)];
  const targets=['text','vision'],pending=new Map(),writes=new Set();
  const labels={text:'文本 AI',vision:'图片 AI'};
  const feedback={text:null,vision:null};
  // Drafts, including an unsubmitted key, stay in this closure for this session only.
  // Never add them to AT.state, public config, storage, or generated HTML.
  const drafts=new Map(),connections={text:null,vision:null},revisions={text:0,vision:0};
  function publicProfile(profile={}) {
    return {baseUrl:String(profile.baseUrl||''),model:String(profile.model||''),enabled:profile.enabled===true,hasKey:profile.hasKey===true};
  }
  function publicConfig(value={}) {
    return {version:2,profiles:Object.fromEntries(targets.map(target=>[target,publicProfile(value.profiles?.[target])])),promptVersion:String(value.promptVersion||''),warning:value.warning||null};
  }
  let config=publicConfig(bridge?.bootstrap?.ai);
  const profile=target=>config.profiles[target];
  const ready=target=>Boolean(profile(target)?.enabled&&profile(target).hasKey&&profile(target).baseUrl&&profile(target).model);
  const status=target=>ready(target)?'已启用':profile(target)?.hasKey?'已停用':'待配置';
  const savedDraft=target=>({...profile(target),apiKey:'',clearKey:false});
  const draft=target=>drafts.get(target)||savedDraft(target);
  const dirty=target=>{
    const value=draft(target),saved=profile(target);
    return value.baseUrl.trim()!==saved.baseUrl||value.model.trim()!==saved.model||value.enabled!==saved.enabled||Boolean(value.apiKey.trim())||Boolean(value.clearKey&&saved.hasKey);
  };
  function capture(form) {
    const target=form.dataset.aiProfile;
    if(!targets.includes(target)||pending.has(target))return;
    drafts.set(target,{baseUrl:form.elements.baseUrl.value,model:form.elements.model.value,apiKey:form.elements.apiKey.value,enabled:form.elements.enabled.checked,clearKey:form.elements.clearKey.checked});
  }
  function restoreForm(target) {
    const form=$(`form[data-ai-profile="${target}"]`);if(!form)return;
    const value=draft(target);
    for(const name of ['baseUrl','model','apiKey'])form.elements[name].value=value[name];
    for(const name of ['enabled','clearKey'])form.elements[name].checked=value[name];
    for(const input of $$('input[aria-invalid]',form))input.removeAttribute('aria-invalid');
  }
  function connectionState(target) {
    const result=connections[target];
    if(!result)return {tone:'neutral',message:'本次启动尚未检测连接。'};
    const stale=result.revision!==revisions[target],when=new Date(result.time).toLocaleString('zh-CN',{hour12:false});
    return {tone:stale?'neutral':result.tone,message:`最近检测${result.tone==='success'?'通过':'未通过'} · ${when}${stale?' · 配置已改变，请重新检测':dirty(target)?' · 对应已保存配置':''}。${result.message}`};
  }
  const field=(label,name,value='',extra='')=>`<label class="field"><span>${E(label)}</span><input name="${name}" value="${E(value)}" ${extra}></label>`;
  function controls(form) {
    const busy=pending.has(form.dataset.aiProfile),offline=!AT.isDesktop;
    form.setAttribute('aria-busy',String(busy));
    for(const input of $$('input',form))input.disabled=busy||offline;
    for(const button of $$('[data-ai-action]',form))button.disabled=busy||offline||(button.dataset.aiAction==='test'&&!form.elements.enabled.checked)||(['save','discard'].includes(button.dataset.aiAction)&&!dirty(form.dataset.aiProfile));
    $('[data-ai-action="discard"]',form).hidden=!dirty(form.dataset.aiProfile);
    $('[data-ai-action="save"]',form).innerHTML=I('save')+(busy?'正在处理…':'保存配置');
  }
  function updateStatus(target) {
    const form=$(`form[data-ai-profile="${target}"]`);if(!form)return;
    const saved=profile(target),state=feedback[target],badge=$('[data-ai-profile-status]',form);
    badge.textContent=status(target);badge.classList.toggle('blue',ready(target));
    const note=$('[data-ai-feedback]',form);
    note.textContent=state?.message||'更改后保存，仅影响此类任务';note.dataset.tone=state?.tone||'neutral';
    note.setAttribute('role',state?.tone==='error'?'alert':'status');
    $('[data-ai-key-note]',form).textContent=saved.hasKey?'密钥已加密保存；留空沿用，界面不会回显。':'密钥仅保存到本机受保护配置，不随账本备份导出。';
    form.elements.apiKey.placeholder=saved.hasKey?'留空沿用已保存密钥':'填写此接口的密钥';
    $('[data-ai-clear-key]',form).hidden=!saved.hasKey;
    const connection=$('[data-ai-connection]',form),tested=connectionState(target);
    connection.textContent=tested.message;connection.dataset.tone=tested.tone;
    const warning=$('[data-ai-config-warning]');if(warning){warning.textContent=config.warning||'';warning.hidden=!config.warning;}
    controls(form);
  }
  function card(target) {
    const c=draft(target),saved=profile(target),isText=target==='text',busy=pending.has(target),state=feedback[target],tested=connectionState(target);
    return `<form class="panel ai-profile" data-ai-profile="${target}" novalidate aria-busy="${busy}">
      <header class="ai-profile-heading"><span class="ai-profile-icon">${I(isText?'note':'import')}</span><div><h2>${labels[target]}</h2><p>${isText?'复盘草稿 · 文字分析':'成交截图 · 图片识别'}</p></div><span class="tag ${ready(target)?'blue':''}" data-ai-profile-status>${status(target)}</span></header>
      <p class="ai-profile-description">${isText?'用于理解记录、整理判断与生成复盘草稿。':'使用支持图片输入的模型，将成交截图整理成可核对的记录。'}</p>
      <div class="ai-profile-fields">
        ${field('接口地址','baseUrl',c.baseUrl,'type="url" placeholder="https://your-provider.com/v1" autocomplete="off" spellcheck="false" maxlength="2000"')}
        ${field('模型名称','model',c.model,'type="text" placeholder="填写服务商提供的模型 ID" autocomplete="off" spellcheck="false" maxlength="200"')}
        ${field('API Key','apiKey','','type="password" placeholder="'+(saved.hasKey?'留空沿用已保存密钥':'填写此接口的密钥')+'" autocomplete="new-password" spellcheck="false" maxlength="16000"')}
        <p class="ai-key-note" data-ai-key-note>${saved.hasKey?'密钥已加密保存；留空沿用，界面不会回显。':'密钥仅保存到本机受保护配置，不随账本备份导出。'}</p>
        <label class="ai-key-clear" data-ai-clear-key ${saved.hasKey?'':'hidden'}><input type="checkbox" name="clearKey" ${c.clearKey?'checked':''}>删除已保存密钥并停用</label>
      </div>
      <label class="ai-profile-enable"><span><strong>启用${labels[target]}</strong><small>使用这一路配置处理${isText?'文字':'图片'}任务</small></span><input class="switch" type="checkbox" name="enabled" aria-label="启用${labels[target]}" ${c.enabled?'checked':''}></label>
      <footer class="ai-profile-footer"><div class="ai-profile-actions">${B('保存配置','save','data-ai-action="save"','primary')}${B('保存并测试','check',`data-ai-action="test" ${!c.enabled?'disabled':''}`)}${B('放弃更改','',`data-ai-action="discard" ${dirty(target)?'':'hidden'}`)}</div><p class="ai-feedback" data-ai-feedback data-tone="${state?.tone||'neutral'}" role="${state?.tone==='error'?'alert':'status'}" aria-live="polite">${E(state?.message||(dirty(target)?'有未保存的更改':'更改后保存，仅影响此类任务'))}</p><p class="ai-connection" data-ai-connection data-tone="${tested.tone}" role="status">${E(tested.message)}</p></footer>
    </form>`;
  }
  function view() {
    return AT.head('AI 模型','全局管理文字与图片任务，可分别使用不同服务商和模型')
      +`<nav class="context-nav" aria-label="相关功能">${AT.groups.find(g=>g.id==='C').pages.map(p=>`<button type="button" data-nav="${p.id}" ${p.id==='C07'?'aria-current="page"':''}>${I(p.icon)}${E(p.title)}</button>`).join('')}</nav>`
      +(!AT.isDesktop?'<p class="ai-settings-note">请在桌面应用中配置 AI。浏览器预览不会保存密钥或发送请求。</p>':'')
      +`<p class="ai-settings-note ai-settings-warning" data-ai-config-warning ${config.warning?'':'hidden'}>${E(config.warning||'')}</p>`
      +`<div class="ai-settings-grid">${targets.map(card).join('')}</div><div class="ai-settings-note"><span>${I('shield')}</span><p>配置供整个应用共用。仅在你使用 AI 功能时发送对应材料；文本和图片分别使用各自的地址、模型与密钥。<br>支持兼容 OpenAI Chat Completions 的接口。提示词会随策略、回测和大盘研究功能继续完善。</p></div>`;
  }
  function validate(target) {
    const form=$(`form[data-ai-profile="${target}"]`),value=draft(target);
    if(form)for(const input of $$('input[aria-invalid]',form))input.removeAttribute('aria-invalid');
    const fail=(name,message)=>{if(form){form.elements[name].setAttribute('aria-invalid','true');form.elements[name].focus();}throw new Error(message);};
    const address=value.baseUrl.trim(),key=value.apiKey.trim();
    if(value.enabled&&!address)fail('baseUrl','请填写接口地址后再启用。');
    let url;
    if(address){
      try{url=new URL(address);}catch{fail('baseUrl','请输入完整的 API 地址，例如 https://your-provider.com/v1。');}
      if((url.protocol!=='https:'&&!(url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname)))||url.username||url.password||url.search||url.hash)fail('baseUrl','接口需使用 HTTPS，本机服务可使用 HTTP；地址中不要包含密钥或参数。');
    }
    if(/\s/.test(key))fail('apiKey','API Key 不能包含空白字符。');
    if(!value.enabled)return;
    if(!value.model.trim())fail('model','请填写模型名称后再启用。');
    const saved=profile(target);
    const normalize=value=>value.replace(/\/+$/,'').replace(/\/chat\/completions$/,'');
    if(!key&&(value.clearKey||!saved.hasKey||normalize(url.href)!==normalize(saved.baseUrl)))fail('apiKey','请填写此接口的 API Key；更换地址后需要重新提供密钥。');
  }
  async function call(method,payload) {
    if(typeof bridge?.ai!=='function')throw new Error('AI 服务尚未启动，请重新打开最新版桌面应用。');
    const result=await bridge.ai(method,payload);
    if(!result?.ok)throw new Error(typeof result?.error==='string'?result.error:result?.error?.message||'操作未完成，请重试。');
    return result.data;
  }
  async function save(target,test=false) {
    if(!targets.includes(target)||pending.has(target)||!AT.isDesktop)return false;
    try{validate(target);}catch(error){feedback[target]={tone:'error',message:error.message};updateStatus(target);return false;}
    const value=draft(target),changed=dirty(target),payload={target,baseUrl:value.baseUrl.trim(),model:value.model.trim(),enabled:value.enabled,clearKey:value.clearKey};
    if(value.apiKey.trim())payload.apiKey=value.apiKey.trim();
    feedback[target]={tone:'neutral',message:test?'正在保存配置并测试…':'正在保存配置…'};
    let saved=false;
    const operation=(async()=>{
      const writing=call('config.save',payload);writes.add(writing);
      let response;try{response=await writing;}finally{writes.delete(writing);}
      const result=publicConfig(response);
      // Another profile may be saving concurrently; only merge the profile written by this request.
      config={...config,profiles:{...config.profiles,[target]:result.profiles[target]},promptVersion:result.promptVersion,warning:result.warning};
      saved=true;value.apiKey='';delete payload.apiKey;drafts.delete(target);if(changed)revisions[target]++;
      restoreForm(target);
      feedback[target]={tone:'success',message:`${labels[target]}配置已保存${profile(target).enabled?'并启用':'，当前已停用'}。${test?'正在检测连接…':''}`};
      updateStatus(target);
      if(test){
        try{const tested=await call('test',{target});connections[target]={tone:'success',message:tested.message||`${labels[target]}连接成功。`,time:Date.now(),revision:revisions[target]};}
        catch(error){connections[target]={tone:'error',message:error.message||'连接未通过，请重试。',time:Date.now(),revision:revisions[target]};throw error;}
        feedback[target]={tone:'success',message:`${labels[target]}配置已保存，连接检测通过。`};
      }
    })();
    pending.set(target,operation);updateStatus(target);
    try{await operation;}catch(error){feedback[target]={tone:'error',message:(saved?'配置已保存，测试未通过：':'')+(error.message||'操作未完成，请重试。')};}
    finally{delete payload.apiKey;pending.delete(target);updateStatus(target);}
    return saved;
  }
  function discard(target) {
    if(pending.has(target))return false;
    drafts.delete(target);feedback[target]={tone:'neutral',message:'已放弃更改，恢复为已保存配置。'};
    restoreForm(target);updateStatus(target);return true;
  }
  const hasUnsaved=()=>targets.some(dirty);
  AT.aiSettings={get config(){return structuredClone(config);},ready,status,hasUnsaved,
    async saveAll(){await Promise.allSettled([...writes]);await Promise.all(targets.filter(dirty).map(target=>save(target)));return !hasUnsaved();},
    discardAll(){for(const target of targets)if(dirty(target))discard(target);return !hasUnsaved();},
    async flush(){const results=await Promise.allSettled([...writes]);return results.every(result=>result.status==='fulfilled')&&!hasUnsaved();}};
  const previousView=AT.view;
  AT.view=page=>page==='C07'?view():previousView(page);
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-ai-action]'),form=button?.closest('form[data-ai-profile]');if(!form||button.disabled)return;
    event.preventDefault();event.stopPropagation();
    const target=form.dataset.aiProfile;
    if(button.dataset.aiAction==='discard'){discard(target);return;}
    capture(form);save(target,button.dataset.aiAction==='test');
  },true);
  document.addEventListener('submit',event=>{const form=event.target;if(!form.matches('form[data-ai-profile]'))return;event.preventDefault();event.stopPropagation();capture(form);save(form.dataset.aiProfile);},true);
  document.addEventListener('input',event=>{
    const form=event.target.closest('form[data-ai-profile]');if(!form)return;
    if(event.target.name==='clearKey'&&event.target.checked){form.elements.enabled.checked=false;form.elements.apiKey.value='';}
    if(event.target.name==='enabled'&&event.target.checked)form.elements.clearKey.checked=false;
    capture(form);event.target.removeAttribute('aria-invalid');feedback[form.dataset.aiProfile]={tone:'neutral',message:dirty(form.dataset.aiProfile)?'有未保存的更改':'与已保存配置一致'};updateStatus(form.dataset.aiProfile);
  });
  // Restore keys only through the password input property; HTML remains key-free.
  const hydrate=()=>{for(const form of $$('form[data-ai-profile]')){restoreForm(form.dataset.aiProfile);controls(form);}};
  const observer=new MutationObserver(hydrate);
  document.addEventListener('DOMContentLoaded',()=>{observer.observe($('#main'),{childList:true});hydrate();},{once:true});
})();
