/* Local close-of-day emotion-cycle workspace, using dated theme observations. */
(() => {
  if (typeof AT === 'undefined' || !AT.isDesktop) return;
  const E = AT.escape, bridge = window.atradeDesktop;
  const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const finite = x => x !== null && x !== undefined && typeof x !== 'boolean' && Number.isFinite(Number(x));
  const num = (x, digits = 0) => finite(x) ? Number(x).toLocaleString('zh-CN', {maximumFractionDigits:digits, minimumFractionDigits:digits}) : '—';
  const pct = (x, ratio = false) => finite(x) ? `${num(Number(x) * (ratio ? 100 : 1), 1)}%` : '—';
  const stages = [['trial','低位试错'],['advance','主升'],['chop','高位震荡'],['decline','主跌退潮'],['unknown','待确认']];
  const state = {asOf:'', historyDays:120, excludeST:true, themeId:'', data:null, error:'', message:'', busy:false, writing:false, serial:0, key:'', checkedAt:0, source:'', calendar:[], editor:false, sectors:[], sectorId:'', importBusy:false, draft:{name:'', symbols:'', effectiveFrom:today(), source:'manual'}};
  const btn = (label, action, attrs = '', primary = false) => `<button type="button" class="analysis-tools-button${primary ? ' analysis-tools-primary' : ''}" data-emotion-action="${action}" ${attrs}>${E(label)}</button>`;
  const render = () => {if (AT.state.page === 'T10') AT.render?.();};
  async function call(method, payload = {}) {
    const result = await bridge.market(method, payload);
    if (!result?.ok) throw new Error(typeof result?.error === 'string' ? result.error : result?.error?.message || '读取失败，请重试');
    return result.data;
  }
  const payload = () => ({...(state.asOf ? {asOf:state.asOf} : {}), historyDays:state.historyDays, excludeST:state.excludeST, themeId:state.themeId});
  function checkSource() {
    const source = JSON.stringify([AT.marketUI?.status?.path, AT.marketUI?.status?.latestDate]);
    if (state.source && state.source !== source) {state.serial++; state.busy=false; state.key=''; state.data=null; state.calendar=[];}
    state.source = source;
  }
  async function load(refresh = false) {
    if (state.busy && !refresh) return;
    const serial = ++state.serial, request = payload();
    state.key = JSON.stringify(request); state.busy=true; state.error=''; render();
    try {
      const result = await call('market.analysis.sentiment', {...request, ...(refresh ? {refresh:true} : {})});
      if (serial !== state.serial) return;
      state.data=result; state.checkedAt=Date.now();
      state.calendar=[...new Set([...state.calendar, ...(result.timeline || []).map(x => x.date)])].sort();
    } catch (error) {if (serial === state.serial) state.error=error.message;}
    finally {if (serial === state.serial) {state.busy=false; render();}}
  }
  function selectDate(date) {
    if (date && (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date > today())) {state.message='请选择有效的历史日期'; render(); return;}
    state.serial++; state.busy=false; state.asOf=date; state.data=null; state.error=''; state.key=''; render();
  }
  const stock = item => `<button type="button" class="analysis-tools-stock" data-emotion-stock="${E(item.symbol)}"><strong>${E(item.name || item.code)}</strong><small>${E(item.code || item.symbol)}</small></button>`;
  function stocks(title, rows = [], subtitle = '', allowAI = false) {
    return `<section class="panel emotion-panel"><div class="panel-head"><div><h2>${E(title)} <small>${rows.length} 只</small></h2><p>${E(subtitle)}</p></div></div>${rows.length ? `<div class="emotion-table-scroll"><table class="emotion-table"><thead><tr><th>证券</th><th>高度 / 昨日</th><th>当日涨幅</th><th>5日涨幅</th><th>成交额</th><th>观察依据</th><th>操作</th></tr></thead><tbody>${rows.map(x => `<tr><td>${stock(x)}</td><td>${num(x.boardHeight ?? x.currentHeight)} / ${num(x.previousHeight)}</td><td class="${Number(x.changePercent)>0?'analysis-tools-up':'analysis-tools-down'}">${pct(x.changePercent)}</td><td>${pct(x.return5)}</td><td>${finite(x.amount)?`${num(x.amount/1e8,2)} 亿`:'—'}</td><td>${E(x.roleReason || ({promoted:'继续晋级',broken:'断板反馈',missing:x.missingReason || '数据缺失'}[x.status]) || '量价候选')}</td><td><div class="emotion-stock-actions">${allowAI?AT.emotionLeaderAI?.button(x)||'':''}${btn('跟踪','watch',`data-symbol="${E(x.symbol)}" ${state.writing?'disabled':''}`)}</div></td></tr>`).join('')}</tbody></table></div>` : '<div class="emotion-empty">当前日期没有符合条件的候选；缺失数据不会按失败处理。</div>'}</section>`;
  }
  function themeEditor() {
    if (!state.editor) return '';
    const d = state.draft;
    return `<section class="panel emotion-panel" data-emotion-editor><div class="panel-head"><h2>记录题材归属</h2>${btn('收起','editor')}</div><p class="analysis-tools-hint">确认哪些股票属于同一题材，才能比较龙头、跟随与补涨。每次保存是一份带日期的快照，未生效的归属不会倒填历史；事后补录的历史归属会单独标记。</p><div class="emotion-form"><label>题材名称<input data-emotion-field="name" value="${E(d.name)}" maxlength="40" placeholder="例如：机器人"></label><label>归属生效日期<input type="date" data-emotion-field="effectiveFrom" max="${today()}" value="${E(d.effectiveFrom)}"></label><label class="emotion-wide">证券代码（至少两只，空格或逗号分隔）<textarea data-emotion-field="symbols" placeholder="sh600001 sz000001">${E(d.symbols)}</textarea></label></div><div class="emotion-actions">${btn(state.writing?'保存中…':'保存归属快照','save-theme',state.writing?'disabled':'',true)}<span>${d.source==='tdx_snapshot'?'当前通达信成分，仅从今天起生效':'手工确认归属；历史日期由你核验'}</span></div><div class="emotion-import">${btn(state.importBusy?'读取中…':'读取通达信板块','sectors',state.importBusy?'disabled':'')}<select aria-label="导入题材板块" data-emotion-field="sectorId"><option value="">选择可用板块</option>${state.sectors.map(s => `<option value="${E(s.id)}" ${s.id===state.sectorId?'selected':''}>${E(s.name)}</option>`).join('')}</select>${btn('导入成分到表单','import-sector',!state.sectorId||state.importBusy?'disabled':'')}</div></section>`;
  }
  function view() {
    checkSource();
    const key=JSON.stringify(payload());
    if (!state.busy && !state.error && (key!==state.key || Date.now()-state.checkedAt>300000)) queueMicrotask(() => load());
    queueMicrotask(()=>{if(AT.state.page!=='T10')return;const label=document.querySelector('.demo-label'),status=document.querySelector('.statusbar>span:first-child');if(label)label.textContent='本地日线 · 情绪观察';if(status)status.textContent='日线收盘观察 · '+(state.data?.asOf||'正在读取')+' · 来源与口径见页面';});
    const data=state.data, current=data?.current, evidence=current?.evidence || {}, all=data?.themes || [];
    const selected=all.find(t => t.id===state.themeId);
    const date=data?.asOf || '';
    const idx=state.calendar.indexOf(date), previous=idx>0?state.calendar[idx-1]:'', next=idx>=0?state.calendar[idx+1]:'';
    const cohorts=current?.metrics||{}, premiumCohort=cohorts.premium||{}, relayCohort=cohorts.promotion?.all||{}, highCohort=cohorts.highBoardPromotion||{};
    const overview = current ? `<section class="panel emotion-panel"><div class="emotion-current"><div><span>${current.scope==='market'?'目录市场周期':'目录样本周期'} · ${E(date)}</span><h2 class="emotion-stage-${E(current.stage)}">${E(current.label)}</h2><p>${E(current.reason)}</p>${current.pending?`<small>当日观察：${E(current.observationLabel)} · 连续 ${num(current.confirmDays)} 日，等待确认</small>`:''}</div><div class="emotion-cycle">${stages.slice(0,4).map(([id,label], i)=>`<span class="emotion-stage-${id}${current.stage===id?' active':''}"><b>0${i+1}</b>${E(label)}</span>`).join('')}</div></div><div class="emotion-metrics">${[['最高连板',`${num(evidence.maxHeight)} 板`,'保留并列空间高标'],['近似涨停 / 跌停',`${num(evidence.limitUp)} / ${num(evidence.limitDown)}`,'按收盘名义限价观察'],['昨日涨停溢价',pct(evidence.premium),`可比 ${num(premiumCohort.observed)} / 应观察 ${num(premiumCohort.eligible)}`],['昨日连板晋级',pct(evidence.promotion,true),`晋级 ${num(relayCohort.promoted)} / 可比 ${num(relayCohort.observed)} · 缺失 ${num(relayCohort.missing)}`],['昨日高标失败',pct(evidence.highFailure,true),`断板 ${num(highCohort.broken)} / 可比 ${num(highCohort.observed)} · 缺失 ${num(highCohort.missing)}`],['有效覆盖',`${num(evidence.observed)} / ${num(evidence.eligible)} · ${pct(evidence.coverage,true)}`,'有成交股票 / 已出现样本']].map(([k,v,hint])=>`<div><span>${k}</span><strong>${v}</strong><small>${E(hint)}</small></div>`).join('')}</div></section>` : '';
    const timeline=data?.timeline || [];
    const chart=timeline.length?`<section class="panel emotion-panel"><div class="panel-head"><div><h2>情绪阶段与连板高度</h2><p>每格一个交易日，点击回看当时判断；柱高为最高板，颜色为已确认阶段。</p></div></div><div class="emotion-timeline" style="gap:${timeline.length>250?0:2}px" aria-label="情绪周期历史">${timeline.map(row=>`<button type="button" data-emotion-day="${E(row.date)}" class="emotion-stage-${E(row.stage)}${row.date===date?' selected':''}" title="${E(`${row.date} ${row.label} · 最高${row.evidence.maxHeight}板 · 涨停${row.evidence.limitUp}家`)}" aria-label="${E(row.date+' '+row.label)}"><i style="height:${Math.max(4,Math.min(100,row.evidence.maxHeight*7))}%"></i></button>`).join('')}</div><div class="emotion-legend"><span>${E(timeline[0].date)}</span>${stages.map(([id,label])=>`<span class="emotion-stage-${id}">● ${label}</span>`).join('')}<span>${E(date)}</span></div></section>`:'';
    const themes=`<section class="panel emotion-panel"><div class="panel-head"><div><h2>题材与跟随关系</h2><p>空间龙头与人气龙头可能不同，按高度保留并列候选。补涨必须先有同题材归属。</p></div>${btn('记录题材归属','editor')}</div><div class="emotion-actions"><label>研究范围 <select aria-label="情绪周期题材" data-emotion-filter="themeId"><option value="">全目录 · 查看空间高标</option>${all.map(t=>`<option value="${E(t.id)}" ${state.themeId===t.id?'selected':''}>${E(t.name)} · ${E(t.effectiveFrom)} · ${t.observed}/${t.symbols.length}只</option>`).join('')}</select></label>${selected?btn('移除这份快照','delete-theme',state.writing?'disabled':''):''}</div>${!all.length?'<p class="emotion-empty">尚无此日期已生效的题材归属。可手工填写，或读取通达信板块后保存，建立龙头、跟随和补涨的对照。</p>':''}${selected?`<div class="emotion-theme-stats"><span>来源：${selected.source==='tdx_snapshot'?'当日通达信成分快照':selected.historicalAnnotation?'事后手工标注（非当时已知成分）':'手工确认'}</span><span>生效：${E(selected.effectiveFrom)}</span><span>涨停 ${num(selected.limitUpCount)} 只</span><span>平均涨幅 ${pct(selected.meanChange)}</span><span>缺少当日日线 ${num(selected.missing)} 只</span></div>`:''}</section>`;
    const rules=data?`<details class="panel emotion-panel"><summary>判断规则、讲解来源与数据口径</summary><p>四阶段与龙头 / 补涨 / 切换框架参考公开讲解，以下阈值是本产品的日线观察规则，未经收益优化。</p><ol>${(data.rules||[]).map(r=>`<li>${E(r)}</li>`).join('')}</ol><div class="emotion-sources">${(data.references||[]).map(r=>btn(`${r.title}（${r.kind}）`,'source',`data-url="${E(r.url)}"`)).join('')}</div></details>`:'';
    return `<div class="analysis-tools emotion-workspace" data-emotion-page="T10">${AT.viewParts.head('情绪周期','观察赚钱效应如何从龙头扩散，再从高标负反馈中收缩',btn(state.busy?'计算中…':'刷新观察','refresh',state.busy?'disabled':''))}<section class="panel emotion-panel emotion-controls"><label>观察日期<input type="date" data-emotion-filter="asOf" max="${today()}" value="${E(state.asOf || date)}" aria-label="情绪周期观察日期"></label>${btn('前一交易日','previous',previous?`data-day="${previous}"`:'disabled')}${btn('后一交易日','next',next?`data-day="${next}"`:'disabled')}${btn('最新数据','latest')}<label>历史范围<select data-emotion-filter="historyDays">${[60,120,250,500,1000].map(n=>`<option value="${n}" ${state.historyDays===n?'selected':''}>${n}个交易日</option>`).join('')}</select></label><label><input type="checkbox" data-emotion-filter="excludeST" ${state.excludeST?'checked':''}>排除当前ST / 退市名称</label>${btn('查看连板梯队','ladder',date?`data-day="${E(date)}"`:'disabled')}</section>${state.error?`<div class="emotion-error" role="alert">${E(state.error)} ${btn('重试','refresh')}</div>`:''}${state.message?`<div class="emotion-feedback" role="status">${E(state.message)}</div>`:''}${state.busy?`<div class="emotion-loading" role="status">正在读取日线并计算历史固定队列…可继续切换页面。${btn('取消计算','cancel')}</div>`:''}${data?.stale?'<div class="emotion-error">数据未更新，当前为上次成功快照。</div>':''}${overview}${chart}${themeEditor()}${themes}${data?stocks(selected?`${selected.name} · 空间龙头候选`:'空间龙头候选',selected?selected.leaders:data.leaders,'点击证券查看所选日K线；分析此股会打开右上角 AI；也可直接在 AI 中输入名称或代码。',true):''}${data?stocks('昨日高标今日反馈',data.yesterdayLeaders,'昨日固定队列的晋级、断板和缺失；不是只看今天仍在榜的股票。',true):''}${selected?stocks('同题材跟随候选',selected.followers,'同题材今日上涨的非最高板股票；日线不能确认盘中带动因果。')+stocks('低位补涨候选',selected.catchups,'题材高标至少3板，低位首板 / 两板且5日涨幅低于高标。'):data?stocks('低位候选 · 待核实跟随关系',data.lowCandidates,'没有题材归属时保留低位观察，不自动贴上跟风、补涨或切换标签。'):''}${data?`<section class="panel emotion-panel"><h2>切换观察</h2>${(data.rotationCandidates||[]).map(x=>`<p><strong>${E(x.oldTheme)} → ${E(x.newTheme)} · ${E(x.label)}</strong><br>旧高标 ${num(x.oldBroken)}/${num(x.oldObserved)} 断板，新题材 ${num(x.newLimitUps)} 只低位涨停。${E(x.reason)}</p>`).join('')}<p>旧高标断板与新低位股出现，是需要核对题材的线索。确认“新旧题材切换”还需要两个不同题材的归属、启动日期和连续表现；当前页面保留逐日高标与低位候选，供对照核验。</p></section>`:''}${rules}${data?`<details class="panel emotion-panel"><summary>数据来源与限制 · ${E(data.asOf||'尚无日线')}</summary><ul>${(data.warnings||[]).map(x=>`<li>${E(x)}</li>`).join('')}</ul><p>本地通达信日线 · 计算于 ${E(data.computedAt || '')} · 未复权收盘观察</p></details>`:''}</div>`;
  }
  const oldView=AT.view;
  AT.view=function(page) {return page==='T10'?view():oldView(page);};
  async function write(work) {
    if (state.writing) return;
    state.writing=true; state.message=''; render();
    try {await work();} catch(error) {state.message=error.message;}
    finally {state.writing=false; render();}
  }
  document.addEventListener('click',event=>{
    if (AT.state.page!=='T10') return;
    const stockButton=event.target.closest('[data-emotion-stock]');
    if(stockButton) {event.preventDefault(); event.stopImmediatePropagation(); AT.marketUI?.openDay(stockButton.dataset.emotionStock,state.data?.asOf); return;}
    const dateButton=event.target.closest('[data-emotion-day]');
    if(dateButton) {event.preventDefault(); selectDate(dateButton.dataset.emotionDay); return;}
    const target=event.target.closest('[data-emotion-action]'); if(!target||target.disabled) return;
    event.preventDefault(); const action=target.dataset.emotionAction;
    if(action==='refresh') {load(true); return;}
    if(action==='cancel') {(async()=>{try{const status=await call('market.research.status');const matches=(status.jobs||[]).filter(j=>j.method==='market.analysis.sentiment'&&['asOf','historyDays','excludeST','themeId'].every(k=>j.parameters?.[k]===payload()[k]));for(const job of matches)await call('market.research.cancel',{id:job.id});state.serial++;state.busy=false;state.error='已取消本次计算，点击刷新可重新读取。';render();}catch(error){state.message=error.message;render();}})();return;}
    if(action==='previous'||action==='next') {selectDate(target.dataset.day);return;}
    if(action==='latest') {selectDate(''); return;}
    if(action==='ladder') {AT.analysisTools?.openLeaders(target.dataset.day); return;}
    if(action==='editor') {state.editor=!state.editor; render();return;}
    if(action==='source') {Promise.resolve(bridge.openExternal?.(target.dataset.url)).catch(error=>{state.message=error.message;render();});return;}
    if(action==='watch') {write(async()=>{const symbol=target.dataset.symbol;const existing=await call('market.watchlist.list',{});if(existing.items?.some(x=>x.symbol===symbol)){state.message='这只证券已在跟踪池，保留原记录。';return;}await call('market.watchlist.save',{symbol,note:'研究线索：情绪周期',research:{page:'T10',asOf:state.data?.asOf,parameters:payload(),source:'本地日线情绪周期'}});state.message='已加入统一跟踪池。';document.dispatchEvent(new CustomEvent('market-watchlist-changed'));});return;}
    if(action==='save-theme') {write(async()=>{const symbols=[...new Set(state.draft.symbols.trim().split(/[,，、;；\s]+/).filter(Boolean).map(s=>s.toLowerCase()).map(s=>/^(sh|sz|bj)\d{6}$/.test(s)?s:/^\d{6}$/.test(s)?(/^(4|8|92)/.test(s)?'bj':/^[569]/.test(s)?'sh':'sz')+s:s))]; const item=await call('market.emotion.themes.save',{...state.draft,symbols});state.themeId=item.id;state.editor=false;state.message='题材快照已保存，自生效日期起可参与对照。';state.draft={name:'',symbols:'',effectiveFrom:today(),source:'manual'};await load(true);});return;}
    if(action==='delete-theme') {write(async()=>{await call('market.emotion.themes.delete',{id:state.themeId});state.themeId='';state.message='已移除题材快照。';await load(true);});return;}
    if(action==='sectors'||action==='import-sector') {(async()=>{state.importBusy=true;render();try {if(action==='sectors')state.sectors=(await call('market.sectors')).items||[];else {const sector=await call('market.sector',{id:state.sectorId});state.draft={name:sector.name||'',symbols:(sector.items||[]).map(s=>s.symbol).join(' '),effectiveFrom:today(),source:'tdx_snapshot'};state.message='已读取当前成分，请核对后保存；当前成分不回填历史。';}} catch(error){state.message=error.message;}finally{state.importBusy=false;render();}})();}
  });
  document.addEventListener('input',event=>{
    const key=event.target.dataset.emotionField;
    if(key&&key!=='sectorId') {state.draft[key]=event.target.value;if(key==='symbols'&&state.draft.source==='tdx_snapshot')state.draft.source='manual';}
  });
  document.addEventListener('change',event=>{
    if(AT.state.page!=='T10')return;
    const filter=event.target.dataset.emotionFilter, key=event.target.dataset.emotionField;
    if(key==='sectorId') {state.sectorId=event.target.value; render();return;}
    if(!filter)return;
    if(filter==='asOf') {selectDate(event.target.value);return;}
    state[filter]=filter==='excludeST'?event.target.checked:filter==='historyDays'?Number(event.target.value):event.target.value;
    state.serial++;state.busy=false;state.key='';state.data=null;state.error='';render();
  });
  AT.emotionCycle={view, getLeaderScope(){checkSource();return {data:state.data,themeId:state.themeId,filters:payload(),source:state.source,busy:state.busy};}, getAIContext() {
    const d=state.data;
    const pick=(row,keys)=>Object.fromEntries(keys.filter(key=>row?.[key]!==undefined).map(key=>[key,row[key]]));
    const observation=row=>pick(row,['date','stage','label','observation','observationLabel','confirmDays','pending','reason','evidence','scope']);
    const stock=row=>pick(row,['symbol','code','name','date','boardHeight','currentHeight','previousHeight','changePercent','return5','return20','amount','role','roleReason','status','missingReason']);
    const history=(d?.timeline||[]).filter(row=>row.date<=d.asOf).slice(-60).map(observation);
    return {pageId:'T10',summary:'日线情绪周期与题材跟随观察；所有数值阈值为本产品规则。',dataDates:d?.asOf?[d.asOf]:[],filters:payload(),status:{busy:state.busy,error:state.error,stale:d?.stale||false},
      evidence:d?{source:{asOf:d.asOf},current:observation(d.current),timeline:{from:history[0]?.date,to:history.at(-1)?.date,loadedDays:d.timeline.length,shownDays:history.length,rows:history},leaders:d.leaders.slice(0,12).map(stock),yesterdayLeaders:d.yesterdayLeaders.slice(0,12).map(stock),themes:d.themes.slice(0,3).map(t=>({name:t.name,effectiveFrom:t.effectiveFrom,source:t.source,historicalAnnotation:t.historicalAnnotation||false,createdAt:t.createdAt,leaders:t.leaders.slice(0,12).map(stock),followers:t.followers.slice(0,12).map(stock),catchups:t.catchups.slice(0,12).map(stock)})),rules:d.rules,rotationCandidates:d.rotationCandidates.slice(0,12)}: {},
      limitations:['仅使用截止日及之前日线；当前名称与目录存在存续样本偏差。','情绪历史最多60日；最高板与昨日高标各附前12只，题材附前3组、每组各类候选最多12只，材料不是完整名单。','最高板不是已确认人气龙头，跟随候选不证明盘中因果。','题材归属为带日期的用户确认或当日成分快照；阶段阈值不是92科比原版量化公式。']};
  }};
})();
