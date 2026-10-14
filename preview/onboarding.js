/* First-use setup uses the same market form and services as the rest of A-trade. */
(() => {
  const handles=page=>['C08','G05'].includes(page);
  const sourceKey=source=>JSON.stringify([source?.lastScannedAt||'',source?.symbolCount||0,source?.latestDate||'']);
  const connected=source=>!!(source?.configured&&source.available&&source.symbolCount>0);
  function guideState(value) {
    const state=value?.version===1?value:{};
    return {version:1,status:['active','dismissed','complete'].includes(state.status)?state.status:'new',
      sample:state.sample&&typeof state.sample==='object'?state.sample:null,
      entry:['R11','R03'].includes(state.entry)?state.entry:null};
  }
  function sampleValid(guide,source) {
    const sample=guideState(guide).sample;
    return connected(source)&&!!sample&&sample.sourceKey===sourceKey(source)&&!sample.stale&&Number.isInteger(sample.count)&&sample.count>0&&sample.count<=120;
  }
  function progress(guide,source) {
    const ready=sampleValid(guide,source),state=guideState(guide);
    return {connected:connected(source),verified:ready,entry:ready&&!!state.entry,count:Number(connected(source))+Number(ready)+Number(ready&&!!state.entry)};
  }
  function shouldStart(bootstrap,hash='') {
    return !!bootstrap&&!bootstrap.state&&!hash&&!bootstrap.market?.data?.configured&&!bootstrap.business?.accounts?.length;
  }
  async function verifySample({market,query='',check=()=>{}}) {
    const before=await market('market.status',{});check();
    if(!connected(before))throw Error('请先保存行情目录并读入日线；空目录不能完成数据检查。');
    const found=await market('market.search',{query:query.trim(),kind:'stock',indexedOnly:true,offset:0,limit:200});check();
    const candidates=(found.items||[]).filter(row=>row.kind==='stock'&&row.latestDate);
    const exact=query.trim()?candidates.filter(row=>[row.symbol,row.code,row.name].some(value=>value?.toLowerCase()===query.trim().toLowerCase())):[];
    const stock=query.trim()?(exact.length===1?exact[0]:found.total===1?candidates[0]:null):candidates[0];
    if(!stock)throw Error(query.trim()?'没有唯一可读取的证券，请填写已下载日线的完整代码（如 sh600000）。':'没有已下载并建立索引的股票日线；请补齐股票日线后重新读取目录。');
    const result=await market('market.bars',{symbol:stock.symbol,limit:120});check();
    if(result.symbol!==stock.symbol)throw Error('返回的股票与检查对象不一致，请重试。');
    const rows=result.rows||[],validDate=date=>{if(!/^\d{4}-\d{2}-\d{2}$/.test(date||''))return false;const parsed=new Date(date+'T00:00:00Z');return Number.isFinite(+parsed)&&parsed.toISOString().slice(0,10)===date;};
    if(!rows.length||rows.length>120||rows.some(row=>!validDate(row.date)||!['open','high','low','close'].every(key=>Number.isFinite(row[key])&&row[key]>0)))throw Error('日线为空或价格字段不完整，请先在通达信补齐日线。');
    if(result.stale)throw Error('目前只能读取未更新的缓存，未通过检查；请连接原目录并重新读取。');
    const after=await market('market.status',{});check();
    if(!connected(after)||before.path!==after.path||sourceKey(before)!==sourceKey(after))throw Error('检查期间行情目录或索引已变化，请按当前目录重新检查。');
    const sorted=[...rows].sort((a,b)=>a.date.localeCompare(b.date));
    return {symbol:stock.symbol,name:String(result.name||stock.name||stock.symbol).slice(0,80),from:sorted[0].date,to:sorted.at(-1).date,count:sorted.length,
      sourceKey:sourceKey(after),checkedAt:new Date().toISOString(),cached:!!result.cached,stale:false};
  }
  const api={handles,sourceKey,connected,guideState,sampleValid,progress,shouldStart,verifySample};
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(typeof window==='undefined'||typeof AT==='undefined')return;
  if(!AT.isDesktop){const previous=AT.view;AT.view=page=>handles(page)?AT.head('新手入门','连接数据后开始选股与策略研究')+'<section class="panel"><div class="panel-body"><h2>请在桌面应用中完成入门设置</h2><p class="field-hint">依次连接通达信本地日线、检查一只股票，再进入选股漏斗或策略回测。浏览器预览不读取本机日线，AI、实时行情和账户配置均可稍后完成。</p></div></section>':previous(page);return;}
  const E=AT.escape,B=AT.button;
  let checking=false,serial=0,error='',saveFailed=false,notice='',query='',selectedStep=null,pending=new Set(),statusRequested=false;
  const source=()=>AT.marketUI?.status||window.atradeDesktop?.bootstrap?.market?.data||null;
  const guide=()=>guideState(AT.state.onboarding);
  const button=(text,action,primary=false,disabled=false)=>B(text,'',`data-onboarding-action="${action}" ${disabled?'disabled':''}`,primary?'primary':'');
  const market=async(method,payload)=>{const result=await window.atradeDesktop.market(method,payload);if(!result?.ok)throw Error(typeof result?.error==='string'?result.error:result?.error?.message||'读取失败，请重试。');return result.data;};
  function refresh(){updateShell();if(handles(AT.state.page))AT.render?.();}
  async function save(changes) {
    AT.state.onboarding={...guide(),...changes};
    const work=Promise.resolve(AT.persist());pending.add(work);
    let ok=false;try{ok=await work;}catch{}finally{pending.delete(work);}
    saveFailed=!ok;
    if(!ok)error='入门进度尚未保存，请点击重试保存；当前检查结果仍保留在本次窗口。';
    refresh();return ok;
  }
  async function verify() {
    if(checking||AT.marketUI?.setupBusy)return;
    checking=true;const id=++serial;error='';notice='正在读取一只股票的实际日线…';
    AT.state.onboarding={...guide(),status:'active',sample:null};refresh();
    try {
      const sample=await verifySample({market,query,check:()=>{if(id!==serial)throw Error('已停止检查');}});
      if(id!==serial)return;
      await AT.marketUI?.refresh();if(id!==serial)return;
      if(sample.sourceKey!==sourceKey(source()))throw Error('行情索引已变化，请重新检查。');
      selectedStep=2;await save({sample});notice='日线读取检查通过。请选择一种研究方式。';
    }catch(failure){if(id===serial){error=failure.message||'检查失败，请重试。';notice='';await save({sample:null});}}
    finally{if(id===serial){checking=false;refresh();}}
  }
  function view() {
    const state=guide(),data=source(),p=progress(state,data),busy=checking||AT.marketUI?.setupBusy,sample=p.verified?state.sample:null;
    const status=p.connected?'已读入日线':data?.configured?'目录已保存，尚无可用日线':'尚未连接',step=selectedStep??(p.verified?2:p.connected?1:0);
    const steps=[['连接行情',p.connected],['检查日线',p.verified],['选择研究方式',p.entry]];
    return AT.head('新手入门','先接好数据，再完成第一轮研究',button(state.status==='complete'&&p.verified?'返回使用':'稍后设置',state.status==='complete'&&p.verified?'return':'dismiss'))+
      `<div class="onboarding-page" data-onboarding-page><section class="panel onboarding-intro"><div><span class="eyebrow">开始使用 A-trade</span><h2>从自己的行情开始</h2><p>选股、策略回测和情绪观察共用这份日线。AI、实时行情和账户记账可按需要再配置。</p></div><span class="onboarding-count">${p.count}<small> / 3 步</small></span><ol class="onboarding-steps">${steps.map(([label,done],index)=>`<li class="${done?'done':''}" ${step===index?'aria-current="step"':''}><button type="button" data-onboarding-step="${index}" ${checking?'disabled':''}><b>${done?'✓':index+1}</b><span>${label}</span><small>${done?'已完成':'待完成'}</small></button></li>`).join('')}</ol></section>
      ${error?`<div class="onboarding-feedback error" role="alert">${E(error)} ${saveFailed?button('重试保存','save'):button('重新检查','verify',false,busy||!p.connected)}</div>`:notice?`<p class="onboarding-feedback" role="status">${E(notice)}</p>`:''}
      <section class="onboarding-step" aria-labelledby="onboarding-connect" ${step!==0?'hidden':''}><div class="onboarding-step-heading"><span>01</span><div><h2 id="onboarding-connect">连接通达信本地日线</h2><p>还没有日线？先在通达信下载盘后日线，再选择安装目录或其中的 vipdoc 文件夹。</p></div><b class="onboarding-state">${status}</b></div>${AT.marketUI?.connectionForm?.()||'<p>行情设置正在准备…</p>'}${p.connected?`<div class="onboarding-next">${button('下一步：检查日线','next',true,busy)}</div>`:''}</section>
      <section class="panel onboarding-check" ${step!==1?'hidden':''}><div class="onboarding-step-heading"><span>02</span><div><h2>检查能否真正读到价格</h2><p>抽查一只已下载的股票，核对日期、数量和价格字段。不会启动选股、回测或 AI。</p></div></div><div class="onboarding-check-actions"><label class="field"><span>股票名称或代码（可留空自动选一只）</span><input data-onboarding-query value="${E(query)}" maxlength="100" placeholder="例如 sh600000" ${checking?'readonly':''}></label>${button(checking?'正在检查…':p.verified?'重新检查日线':'检查一只日线','verify',true,busy||!p.connected)}${checking?button('停止检查','stop'):''}</div>${sample?`<div class="onboarding-sample" data-onboarding-sample><strong>${E(sample.name)} <small>${E(sample.symbol)}</small></strong><span>${E(sample.from)} 至 ${E(sample.to)} · 实际读取 ${sample.count} 根日线</span><small>${sample.count<60?'历史不足60根，部分策略可能无法计算；建议补齐更长历史。':'读取通畅；回测可用区间仍取决于所选股票的历史覆盖。'} 单股检查不代表全市场数据完整。</small></div>`:state.sample?'<p class="onboarding-note">行情目录或索引已变化，请重新检查；上次结果不作为当前完成依据。</p>':'<p class="onboarding-note">保存目录后，点击检查；只有实际读到日线才会点亮这一步。</p>'}</section>
      <section class="panel onboarding-research" ${step!==2?'hidden':''}><div class="onboarding-step-heading"><span>03</span><div><h2>选择第一轮研究方式</h2><p>先从一个入口开始。运行结束后，任务与结果会保存在任务中心和报告库。</p></div></div><div class="onboarding-flows"><article><h3>先找股票</h3><ol><li>加载输入池，确认股票数量和日期</li><li>运行第1步，再按需要逐步筛选</li><li>点击股票名称，查看行情和策略数据</li></ol><button type="button" class="btn primary" data-onboarding-entry="R11" ${!p.verified||busy?'disabled':''}>进入选股漏斗 →</button></article><article><h3>验证一个策略</h3><ol><li>选择策略、股票范围与回测日期</li><li>确认交易参数，启动回测</li><li>检查收益、交易明细与数据缺失提示</li></ol><button type="button" class="btn" data-onboarding-entry="R03" ${!p.verified||busy?'disabled':''}>进入策略回测 →</button></article></div>${state.entry?'<p class="onboarding-note">已选择研究入口；运行后的成功或失败以任务结果为准。</p>':''}</section>
      <details class="panel onboarding-optional"><summary>可选设置 · AI、实时行情、账户与备份</summary><p>这些配置不影响使用本地日线做选股和回测。</p><div class="onboarding-options">${[
        ['AI 提问',AT.aiSettings?.ready('text')?'文本模型已配置；连接结果见模型设置':'填写自己的文本模型后，可解读页面和按需分析个股','C07'],
        ['实时与分时','需要时再启用通达信 MCP；日线研究可以先开始','C01'],
        ['账户记账','记录自己的账户与成交，供后续复盘使用','A03'],
        ['费用规则','核对新回测的默认费用；已有任务参数会保留','C02'],
        ['备份与恢复','定期导出工作区与账本副本，避免本机数据丢失','C04']
       ].map(([title,description,page])=>`<button type="button" data-nav="${page}"><strong>${E(title)} ${AT.icon('chevronRight')}</strong><span>${E(description)}</span></button>`).join('')}</div></details>
      <footer class="onboarding-footer"><p>${state.status==='complete'&&p.verified?'你已完成入门配置，可随时回来重新检查。':state.status==='complete'?'行情状态已变化，请重新检查；也可以先返回软件使用。':'进度保存在本机；离开后可从「设置 → 新手入门」继续。'}</p>${button('完成引导，开始使用','finish',true,busy||!p.verified||!state.entry)}</footer></div>`;
  }
  function updateShell() {
    let banner=document.getElementById('onboarding-resume');
    const active=guide().status==='active'&&!handles(AT.state.page);
    if(!banner&&active){banner=document.createElement('aside');banner.id='onboarding-resume';banner.className='onboarding-resume';banner.setAttribute('aria-label','入门进度');document.querySelector('.topbar')?.after(banner);}
    if(!banner)return;banner.hidden=!active;
    if(active){const p=progress(guide(),source()),hint=p.verified&&AT.state.page==='R11'?'先加载输入池，再运行第1步；筛选后点击股票名称查看行情。':p.verified&&AT.state.page==='R03'?'选择策略、范围和日期，确认参数后启动回测。':p.verified?'日线已检查':p.connected?'下一步：检查一只日线':'下一步：连接行情';banner.innerHTML=`<span>入门设置 ${p.count}/3 · ${hint}</span><div><button type="button" data-nav="C08">${p.count===3?'完成引导':'继续引导'}</button><button type="button" data-onboarding-action="dismiss">稍后再说</button></div>`;}
  }
  function start() {
    if(shouldStart(window.atradeDesktop?.bootstrap,location.hash)){
      AT.state.onboarding={version:1,status:'active',sample:null,entry:null};AT.state.page='C08';void save({});
    }
  }
  const previous=AT.view;AT.view=page=>handles(page)?view():previous(page);
  document.addEventListener('input',event=>{if(event.target.matches('[data-onboarding-query]'))query=event.target.value;});
  document.addEventListener('click',async event=>{
    const stepButton=event.target.closest('[data-onboarding-step]');
    if(stepButton&&!stepButton.disabled){selectedStep=Number(stepButton.dataset.onboardingStep);error='';notice='';refresh();return;}
    const element=event.target.closest('[data-onboarding-action],[data-onboarding-entry]');if(!element||element.disabled)return;
    event.preventDefault();const action=element.dataset.onboardingAction,entry=element.dataset.onboardingEntry;
    if(entry){if(!sampleValid(guide(),source())||checking||AT.marketUI?.setupBusy)return;error='';if(await save({status:'active',entry}))AT.navigate(entry);}
    if(action==='verify')void verify();
    if(action==='next'){selectedStep=1;notice='';refresh();}
    if(action==='return')AT.navigate(guide().entry||'M04');
    if(action==='stop'){serial++;checking=false;notice='已停止检查，可以重新开始。';void save({sample:null});}
    if(action==='save'){error='';await save({});}
    if(action==='dismiss'){serial++;checking=false;error='';if(await save({status:'dismissed'}))AT.navigate(guide().entry||'M04');}
    if(action==='finish'&&sampleValid(guide(),source())&&guide().entry&&!checking&&!AT.marketUI?.setupBusy){error='';if(await save({status:'complete'}))AT.navigate(guide().entry);}
  });
  AT.onboarding={handles,start,refresh,updateShell,view,mount(){if(!handles(AT.state.page))return;if(['new','dismissed'].includes(guide().status))void save({status:'active'});if(!AT.marketUI?.status&&!statusRequested){statusRequested=true;void AT.marketUI?.refresh();}},async flush(){const saved=await Promise.all([...pending]);return saved.every(Boolean);}};
})();
