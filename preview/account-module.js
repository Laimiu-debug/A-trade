/* The desktop account workspace owns real records; browser previews remain independent. */
(() => {
  if (!AT.isDesktop) return;
  const bridge = window.atradeDesktop, E = AT.escape, I = AT.icon, B = AT.button;
  const { head, metric } = AT.viewParts;
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const today = () => new Date().toLocaleDateString('en-CA');
  const kinds = {daily:'每日复盘',weekly:'周度复盘',monthly:'月度复盘',trade:'逐笔复盘',idea:'灵感闪记'};
  const types = {buy:'买入',sell:'卖出',deposit:'入金',withdrawal:'出金',dividend:'分红',fee:'费用',opening_position:'期初持仓',split:'股份调整'};
  let workspace = bridge.bootstrap?.business?.data || bridge.bootstrap?.business || null;
  let workspaceError = '', requestSerial = 0, importState = null, reviewFilter = {kind:'all',date:''};
  let currentReview = null, restoreCandidate = null, draftTimer;
  let activeReviewSection = 'quick', activeScoreDimension = 0;
  let scoreTimer, scoreSerial=0, factsSerial=0, snapshotSession=null;
  const historyFilters={}, tradeSelections={};
  let curveMode='equity';
  const pendingWrites = new Set();
  const draftKey = r => `${r.accountId||workspace?.accountId}|${r.id|| (r.kind==='trade'&&r.draftTradeId?'trade:'+r.draftTradeId:'new:'+r.kind)}`;
  const amount = value => value == null ? '未估值' : AT.money(Number(value));
  const percent = value => value == null ? '未估值' : `${Number(value).toFixed(2)}%`;
  const account = () => workspace?.accounts?.find(a => a.id === workspace.accountId);
  const btn = (text, action, icon = '', cls = '', attrs = '') => B(text,icon,`data-business="${E(action)}" ${attrs}`,cls);
  const msg = text => `<p class="field-hint">${E(text)}</p>`;
  const aiRoute = target => `<div class="business-ai-routing"><span>${target==='vision'?'图片':'文本'} AI · ${E(AT.aiSettings?.status(target)||'待配置')}${AT.aiSettings?.ready(target)?' · '+E(AT.aiSettings.config.profiles[target].model):''}</span>${AT.aiSettings?.ready(target)?'':'<a href="#C07" data-nav="C07">前往设置</a>'}</div>`;
  const empty = (title,detail='',action='') => `<div class="business-empty">${I('account')}<h3>${E(title)}</h3><p>${E(detail)}</p>${action}</div>`;
  const panel = (title,detail,content,actions='',id='') => `<section class="panel section-space"${id?` id="${id}"`:''}><div class="panel-head"><div><h2>${E(title)}</h2>${detail?`<p class="subtitle">${E(detail)}</p>`:''}</div><div class="business-actions">${actions}</div></div>${content}</section>`;
  const field = (label,name,value='',type='text',extra='') => `<label class="field"><span>${E(label)}</span><input name="${E(name)}" type="${type}" value="${E(String(value??''))}" ${extra}></label>`;
  const textfield = (label,name,value='',placeholder='') => `<label class="field"><span>${E(label)}</span><textarea name="${E(name)}" rows="3" placeholder="${E(placeholder)}">${E(String(value??''))}</textarea></label>`;
  const select = (label,name,value,options) => `<label class="field"><span>${E(label)}</span><select name="${E(name)}">${options.map(([id,title])=>`<option value="${E(id)}" ${String(value)===String(id)?'selected':''}>${E(title)}</option>`).join('')}</select></label>`;
  const alert = text => `<div class="business-alert" role="status">${E(text)}</div>`;
  const table = (headers,rows,emptyText='暂无记录') => rows.length ? `<div class="table-wrap"><table class="data-table"><thead><tr>${headers.map(h=>`<th>${E(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : empty(emptyText);
  const recordName = item => `${item.name||item.code||''}${item.name&&item.code?' · '+item.code:''}`;
  function errorText(error) { return typeof error==='string'?error:error?.message||'操作未完成，请重试'; }
  async function call(method,payload={}) {
    if (typeof bridge.business !== 'function') throw new Error('账户服务尚未启动，请关闭并重新打开最新版桌面程序。');
    const pending=Promise.resolve(bridge.business(method,{accountId:workspace?.accountId,...payload})).then(result=>{if(!result?.ok)throw new Error(errorText(result?.error));return result.data;});
    const write=!['workspace','account.asof','account.valuation','review.score','import.preview','backup.export'].includes(method);
    if(write)pendingWrites.add(pending);
    try{return await pending;}finally{pendingWrites.delete(pending);}
  }
  async function callAI(method,payload={}) {
    if (typeof bridge.ai !== 'function') throw new Error('AI 接口尚未启动，请重新打开最新版桌面程序。');
    const pending=Promise.resolve(bridge.ai(method,payload)).then(result=>{if(!result?.ok)throw new Error(errorText(result?.error));return result.data;});
    return await pending;
  }
  function applyWorkspace(data) { if (data?.workspace) data=data.workspace; if(data?.accounts) {if(workspace?.accountId!==data.accountId){importState=null;snapshotSession=null;for(const key of Object.keys(historyFilters))delete historyFilters[key];AT.ui.passPosition=null;}workspace=data;workspaceError='';AT.state.businessAccountId=data.accountId;AT.persist();} }
  async function refresh(accountId=workspace?.accountId) {
    const serial=++requestSerial;
    try {const data=await call('workspace',{accountId});if(serial!==requestSerial)return;applyWorkspace(data);AT.render?.();}
    catch(error){if(serial!==requestSerial)return;workspaceError=errorText(error);AT.render?.();throw error;}
  }
  function notice(error,form) {
    const text=errorText(error), target=form?.querySelector('[data-business-error]');
    if(target){target.textContent=text;target.hidden=false;target.scrollIntoView({block:'nearest'});} else AT.toast?.(text);
  }
  function modal(title,body,footer='',wide=true) {
    AT.openBusinessWorkspace(title,body,{wide,className:'business-modal',footer});
  }
  const formError = '<p class="business-error" data-business-error role="alert" hidden></p>';
  const footer = (label='保存',action='save-form') => btn('取消','close')+btn(label,action,'save','primary');
  function currentForm() {return $('#business-form');}
  function formData(form=currentForm()) {return Object.fromEntries(new FormData(form));}
  function context() {
    const selected=account();
    return `<div class="account-context business-context"><span class="tag blue">${selected?.kind==='real'?'实盘记账':'模拟账户'}</span><select class="select" data-business-account aria-label="选择账户">${(workspace?.accounts||[]).filter(a=>!a.archived||a.id===workspace.accountId).map(a=>`<option value="${E(a.id)}" ${a.id===workspace.accountId?'selected':''}>${E(a.name)}${a.archived?'（已归档）':''}</option>`).join('')}</select><span>${selected?`${E(selected.startDate)} 起算 · 本机账本`:'选择或创建账户'}</span><div class="business-actions right">${btn('账户管理','accounts','account','small')}</div></div>`;
  }
  function noAccount() {
    if(workspaceError)return head('账户与复盘','本机账本')+alert(workspaceError)+btn('重新连接','refresh','refresh');
    if(!workspace)return head('账户与复盘','正在读取本机账本')+empty('正在读取账户…');
    return head('账户与复盘','从第一份账本开始，记录判断与行动')+`<section class="panel business-onboarding"><div class="business-onboarding-mark">${I('account')}</div><span class="eyebrow">你的本地账户</span><h2>让每一笔交易都有据可循</h2><p>先设置账户与期初资金，再导入同花顺成交截图或手工记账。<br>成交、费用、复盘和计划分别保存，账户之间独立核算。</p><div class="business-actions">${btn('创建账户','account-new','plus','primary')}${workspace.accounts?.length?btn('管理已有账户','accounts','account'):''}</div><div class="business-onboarding-steps"><span>01　建立账户</span><span>02　核对成交</span><span>03　细致复盘</span></div></section>`;
  }
  function overview() {
    const s=workspace.summary||{},a=account(),positions=workspace.positions||[];
    const valuation=s.valuationDate?`估值截至 ${s.valuationDate} · 使用已保存价格，来源可在快照中核对`:s.valuationStale?`${s.latestSnapshotDate} 的估值已过期，请按最新流水重新记录快照`:'尚未保存可用于当前持仓的价格，可在快照中从行情填充';
    return head('账户总览','资金、持仓和复盘，来自你的本机账本',btn('导入成交','import','import')+btn('记录交易','ledger-new','plus','primary'))+context()
      +`<div class="metrics">${metric('账户资产',amount(s.equity),s.equity==null?'填写持仓价格后生成资产快照':positions.length?E('现金 + 估值截至 '+s.valuationDate+' 的持仓'):'当前无持仓，资产来自现金')}${metric('可用现金',amount(s.cash),'根据有效流水逐笔核算')}${metric('已实现盈亏',amount(s.realizedPnl),'含成交费用 · 移动平均成本',Number(s.realizedPnl)>=0?'up':'down')}${metric('累计费用',amount(s.totalFees),'佣金、印花税、过户费及其他')}</div>`
      +accountProgress(a,s)
      +panel('当前持仓',valuation,table(['证券','数量','平均成本','持仓成本','估值价格','浮动盈亏'],positions.map(p=>`<tr><td><strong>${E(p.name||p.code)}</strong><small class="business-subtext">${E(p.code)}</small></td><td>${E(p.quantity)}</td><td>${amount(p.averageCost)}</td><td>${amount(p.cost)}</td><td>${p.marketPrice==null?'未估值':amount(p.marketPrice)}${p.priceDetails?`<small class="business-subtext">${E(priceSource(p.priceDetails))}</small>`:''}</td><td>${amount(p.unrealizedPnl)}</td></tr>`),'尚无持仓'),btn('期初持仓','opening-position','plus','small')+btn('资产快照','snapshots','snapshot','small'))
      +`<div class="business-columns">${panel('账户进度',`${a.startDate} 起算`, `<div class="panel-body business-summary"><div><span>期初现金</span><strong>${amount(a.openingCash)}</strong></div><div><span>净投入</span><strong>${amount(s.netContributions)}</strong></div><div><span>账本记录</span><strong>${(workspace.ledger||[]).filter(x=>!x.voided).length} 笔</strong></div><div><span>累计复盘</span><strong>${(workspace.reviews||[]).length} 篇</strong></div></div>`,btn('编辑账户','account-edit','settings','small',`data-id="${E(a.id)}"`))}${panel('接下来', '从事实到判断，再到下一步行动',`<div class="business-next"><button data-nav="A04">${I('ledger')}核对交易与资金${I('chevronRight')}</button><button data-business="review-new">${I('calendar')}写一份每日复盘${I('chevronRight')}</button><button data-business="plan-new">${I('route')}记录下一次操作计划${I('chevronRight')}</button></div>`)}</div>`;
  }
  function accountProgress(a,s) {
    const count=a.stages?.length||0,goal=Number(a.goalPercent)||0;if(!count||!goal)return '';
    const progressUnits=s.returnPct==null?0:Math.max(0,Math.log(1+Math.max(-99.999,s.returnPct)/100)/Math.log(1+goal/100));
    const completed=Math.min(count,Math.floor(progressUnits)),fraction=completed===count?1:progressUnits-completed;
    const progress=Math.min(100,Math.max(0,(completed+fraction-.5)/Math.max(1,count-1)*100)),xs=[64,313,560,808,1055,1300];
    const nodes=Array.from({length:count},(_,index)=>{const current=index===completed,done=index<completed;return `<button class="milestone ${done?'complete':current?'current':'future'}" data-business="stage-detail" data-stage="${index+1}" aria-label="阶段 ${index+1}，${done?'当前收益已达目标':current?'当前进度':'尚未达到'}"><span class="badge-holder">${current?`<svg class="current-arc" viewBox="0 0 90 90" aria-hidden="true"><circle cx="45" cy="45" r="40"/><circle class="arc-value" cx="45" cy="45" r="40" stroke-dasharray="${fraction*251.33} 251.33"/></svg>`:''}<svg class="gem" viewBox="${xs[index%6]} 392 176 193" aria-hidden="true"><image href="assets/milestone-sheet.png" width="1536" height="1024"/></svg>${done?`<span class="badge-check">${I('check')}</span>`:''}</span><span class="sequence">${String(index+1).padStart(2,'0')}</span>${current?'<span class="current-label">当前</span>':''}</button>`;}).join('');
    return `<section class="panel pass-panel section-space"><div class="panel-head"><h2>通行证历程</h2><div class="business-actions"><span class="pass-hint">每阶段 ${goal}% · ${count} 阶段${s.returnPct==null?' · 等待完整估值':''}</span>${btn('调整目标','account-edit','settings','small',`data-id="${E(a.id)}"`)}</div></div><div class="pass-viewport" tabindex="0" aria-label="账户阶段历程，滚轮、左右方向键或拖动浏览"><div class="pass-track"><div class="pass-rail" aria-hidden="true"><div class="pass-fill" style="width:${progress}%"></div></div>${nodes}</div></div><div class="pass-foot">${s.returnPct==null?'补充持仓价格后，按真实收益比较目标':'净投入收益率 '+percent(s.returnPct)+' · 非时间加权'} · 点击徽章查看目标与快照</div></section>`;
  }
  function stageDetail(stage) {
    const a=account(),goal=Number(a.goalPercent)||0,previous=(Math.pow(1+goal/100,stage-1)-1)*100,target=(Math.pow(1+goal/100,stage)-1)*100;
    const snapshots=(workspace.snapshots||[]).filter(s=>s.summary?.returnPct!=null&&s.summary.returnPct>=previous&&s.summary.returnPct<target);
    modal(`阶段 ${stage} · 目标与快照`,`<div class="business-summary"><div><span>累计目标收益率</span><strong>${percent(target)}</strong></div><div><span>当前净投入收益率</span><strong>${percent(workspace.summary?.returnPct)}</strong></div></div><p class="field-hint section-space">每阶段 ${goal}% 按复利累积。用已有完整快照的净投入收益率比较目标；入出金会改变基准，达成状态随当前收益变化。</p><h3 class="section-space">此目标区间的资产快照</h3>${table(['日期','净投入收益率','总资产'],snapshots.map(s=>`<tr><td>${E(s.date)}</td><td>${percent(s.summary.returnPct)}</td><td>${amount(s.summary.equity)}</td></tr>`),'该区间尚无完整快照')}`,btn('关闭','close')+btn('记录快照','snapshot-new','snapshot','primary'));
  }

  function historyState(key) {return historyFilters[key]||(historyFilters[key]={query:'',from:'',to:'',page:0,size:25});}
  function historyPage(key,items) {
    const state=historyState(key),query=state.query.trim().toLocaleLowerCase();
    const all=items.filter(t=>(!state.from||t.date>=state.from)&&(!state.to||t.date<=state.to)&&(!query||[t.code,t.name,t.reason,t.id,t.title,types[t.type]].join(' ').toLocaleLowerCase().includes(query))).slice().sort((a,b)=>b.date.localeCompare(a.date)||(b.time||'').localeCompare(a.time||''));
    const pages=Math.max(1,Math.ceil(all.length/state.size));state.page=Math.max(0,Math.min(state.page,pages-1));
    return {rows:all.slice(state.page*state.size,(state.page+1)*state.size),total:all.length,pages,state};
  }
  function historyControls(key) {
    const f=historyState(key);
    return `<div class="business-filter history-filter"><label>搜索<input type="search" data-history-filter="query" data-history-key="${E(key)}" value="${E(f.query)}" placeholder="证券代码、名称、理由"></label><label>开始<input type="date" data-history-filter="from" data-history-key="${E(key)}" value="${E(f.from)}"></label><label>结束<input type="date" data-history-filter="to" data-history-key="${E(key)}" value="${E(f.to)}"></label>${btn('清除筛选','history-clear','','small',`data-history-key="${E(key)}"`)}</div>`;
  }
  function historyPager(key,page) {return `<div class="business-pagination">${btn('上一页','history-page','','small',`data-history-key="${E(key)}" data-page="${page.state.page-1}" ${page.state.page===0?'disabled':''}`)}<span>第 ${page.state.page+1} / ${page.pages} 页 · 共 ${page.total} 笔</span>${btn('下一页','history-page','','small',`data-history-key="${E(key)}" data-page="${page.state.page+1}" ${page.state.page+1>=page.pages?'disabled':''}`)}</div>`;}
  function ledgerTable(cash=false) {
    const key=cash?'cash':'ledger';
    return `<div data-history-view="${key}">${historyControls(key)}<div data-history-results="${key}">${ledgerResults(cash)}</div></div>`;
  }
  function ledgerResults(cash=false) {
    const key=cash?'cash':'ledger',page=historyPage(key,(workspace.ledger||[]).filter(t=>cash?['deposit','withdrawal','dividend','fee'].includes(t.type):!['deposit','withdrawal','dividend','fee'].includes(t.type)));
    return table(['日期 / 时间','类型','证券 / 说明','数量','价格 / 金额','费用','操作'],page.rows.map(t=>`<tr class="${t.voided?'business-voided':''}"><td>${E(t.date)}<small class="business-subtext">${E(t.time||'')}</small></td><td><span class="tag ${t.type==='buy'?'blue':''}">${E(types[t.type]||t.type)}</span>${t.voided?'<small class="business-subtext">已作废</small>':''}</td><td><strong>${E(recordName(t)||t.reason||'账户资金')}</strong><small class="business-subtext">${E(t.reason||'')}</small></td><td>${t.quantity?E(t.quantity):'—'}</td><td>${amount(cash?t.amount:t.price)}</td><td>${amount(t.fees??((t.commission||0)+(t.stampTax||0)+(t.transferFee||0)+(t.otherFees||0)))}</td><td><div class="business-actions">${!t.voided?btn('编辑','ledger-edit','','small',`data-id="${E(t.id)}"`)+btn('作废','ledger-void','','small',`data-id="${E(t.id)}"`):'—'}</div></td></tr>`))+historyPager(key,page);
  }
  function executionSummary(p) {
    const x=p.execution;if(!x)return '';
    return `<div class="plan-execution"><span>实际数量 <b>${E(x.quantity??0)}</b></span><span>成交均价 <b>${amount(x.averagePrice)}</b></span><span>成交金额 <b>${amount(x.amount)}</b></span><span>费用 <b>${amount(x.fees)}</b></span><span>执行比例 <b>${x.completionPct==null?'未设数量':percent(x.completionPct)}</b></span><span>剩余计划量 <b>${x.remainingQuantity==null?'—':E(x.remainingQuantity)}</b></span></div>${(x.warnings||[]).length?alert(x.warnings.join('；')):''}`;
  }
  function plans() {
    const status={draft:'草稿',watching:'观察中',ready:'已准备',triggered:'已触发',executed:'已执行',cancelled:'已取消',expired:'已失效'};
    return panel('交易计划与执行对照','记录触发条件、退出边界，以及实际执行中的偏差',(workspace.plans||[]).length?`<div class="business-plan-list">${workspace.plans.map(p=>`<article><div><span class="tag blue">${E(status[p.status]||p.status||'待执行')}</span><small>${E(p.date)}</small></div><h3>${E(recordName(p))} · ${E(types[p.action]||p.action||'观察')}</h3><p>${E(p.reason||'尚未填写操作依据')}</p><dl><dt>触发条件</dt><dd>${E(p.trigger||'未填写')}</dd><dt>止损 / 止盈</dt><dd>${E(p.stopLoss||'未填写')} / ${E(p.takeProfit||'未填写')}</dd><dt>执行反馈</dt><dd>${E(p.executionNotes||'尚未记录')}${p.linkedTradeIds?.length?` · 已关联 ${p.linkedTradeIds.length} 笔流水`:''}</dd></dl>${executionSummary(p)}<div class="business-actions">${btn('编辑与复核','plan-edit','compare','small',`data-id="${E(p.id)}"`)}${btn('删除','plan-delete','','small',`data-id="${E(p.id)}"`)}</div></article>`).join('')}</div>`:empty('暂时没有交易计划','明确触发条件后再记录，盘后补上执行反馈'),btn('新增计划','plan-new','plus','small'),'trade-plan');
  }
  function trades() {
    const cash=AT.ui.ledgerMode==='cash';
    return head('交易管理','先核对成交，再检查计划的实际执行',btn('导入与对账','import','import')+btn('记录交易','ledger-new','plus','primary'))+context()
      +panel('账户流水','编辑和作废后重新核算现金、持仓与已实现盈亏',`<div class="business-tabs"><button data-business="ledger-tab" data-mode="trades" aria-pressed="${!cash}">交易流水</button><button data-business="ledger-tab" data-mode="cash" aria-pressed="${cash}">资金流水</button></div>${ledgerTable(cash)}`,btn('记录入出金','cash-new','cash','small')+btn('导出有效流水','export-ledger','export','small'))+plans();
  }
  function reviewList() {
    const rows=(workspace.reviews||[]).filter(r=>(reviewFilter.kind==='all'||r.kind===reviewFilter.kind)&&(!reviewFilter.date||(r.date<=reviewFilter.date&&(r.endDate||r.date)>=reviewFilter.date)));
    return `<div class="business-filter"><label><span>类型</span><select class="select" data-review-kind-filter><option value="all">全部复盘</option>${Object.entries(kinds).map(([id,title])=>`<option value="${id}" ${reviewFilter.kind===id?'selected':''}>${title}</option>`).join('')}</select></label><label><span>日期</span><input type="date" data-review-date-filter value="${E(reviewFilter.date)}"></label>${reviewFilter.date?btn('清除日期','review-filter-clear','','small'):''}</div>`+(rows.length?`<div class="business-review-list">${rows.map(r=>`<article><div class="business-review-meta"><span class="tag">${E(kinds[r.kind]||r.kind)}</span><span>${E(r.date)}${r.endDate?' — '+E(r.endDate):''}</span><span class="business-score">${r.score?.score==null?'未评分':E(r.score.score)+' 分 · '+E(r.score.coverage)+'% 完成'}</span></div><h3>${E(r.title||kinds[r.kind])}</h3><p>${E(Object.values(r.fields||{}).find(v=>typeof v==='string'&&v.trim())||'暂无文字内容')}</p><div class="business-actions">${btn('打开复盘','review-edit','note','small',`data-id="${E(r.id)}"`)}${btn('删除','review-delete','','small',`data-id="${E(r.id)}"`)}</div></article>`).join('')}</div>`:empty('还没有这类复盘','写下市场环境、决策依据与执行偏差，让下一次判断有迹可循'));
  }
  function performance() {
    const s=workspace.summary||{}, snapshots=workspace.snapshots||[];
    return panel('收益与风险','曲线仅使用已保存的完整资产快照；入出金处重新计算可比区间',`<div class="metrics business-inline-metrics">${metric('净投入收益率',percent(s.returnPct),'（资产 − 净投入）÷ 净投入；非时间加权')}${metric('最大回撤',s.maxDrawdown==null?'缺少可比快照':percent(s.maxDrawdown),'仅比较无外部资金流的连续快照')}${metric('完成回合',String(workspace.roundsSummary?.completedCount??(workspace.rounds||[]).filter(r=>r.status==='closed').length)+' 个','从建仓至清仓，按证券分别统计')}${metric('资产快照',String(snapshots.length)+' 份','价格来源与日期随快照保存')}</div><div class="business-tabs">${[['equity','资产'],['return','收益率'],['drawdown','回撤']].map(([mode,label])=>`<button data-business="curve-mode" data-mode="${mode}" aria-pressed="${curveMode===mode}">${label}</button>`).join('')}</div><div data-performance-curve>${curve(snapshots)}</div>`,btn('记录快照','snapshot-new','snapshot','small'),'review-performance');
  }
  function curve(snapshots) {
    const ordered=snapshots.slice().sort((a,b)=>a.date.localeCompare(b.date)),flows=(workspace.ledger||[]).filter(t=>!t.voided&&['deposit','withdrawal','opening_position'].includes(t.type));
    let peak=null,previous=null,segment=0;
    const rows=ordered.map(s=>{
      const equity=s.summary?.equity,net=s.summary?.netContributions;
      if(!Number.isFinite(equity)){peak=null;previous=null;segment++;return {date:s.date,value:null,segment};}
      const reset=!previous||net!==previous.net||flows.some(t=>t.date>previous.date&&t.date<=s.date);
      if(reset){peak=equity;segment++;}else peak=Math.max(peak,equity);
      const drawdown=reset||peak<=0?null:(equity/peak-1)*100;
      previous={date:s.date,net};
      return {date:s.date,segment:curveMode==='drawdown'?segment:0,value:curveMode==='equity'?equity:curveMode==='return'?s.summary?.returnPct:drawdown};
    });
    const valid=rows.filter(r=>Number.isFinite(r.value));
    if(!valid.length)return msg(curveMode==='drawdown'?'尚无连续且无入出金的完整快照可计算回撤。':'保存完整资产快照后显示曲线。');
    const values=valid.map(r=>r.value),min=Math.min(...values),max=Math.max(...values),span=max-min||1;
    const point=r=>`${32+rows.indexOf(r)*836/Math.max(1,rows.length-1)},${166-(r.value-min)/span*126}`;
    const segments=[];let current=[];
    for(const r of rows){if(!Number.isFinite(r.value)||current.length&&current.at(-1).segment!==r.segment){if(current.length)segments.push(current);current=[];}if(Number.isFinite(r.value))current.push(r);}if(current.length)segments.push(current);
    const format=v=>curveMode==='equity'?amount(v):percent(v);
    return `<div class="business-curve"><svg viewBox="0 0 900 208" role="img" aria-label="${curveMode==='equity'?'资产':curveMode==='return'?'净投入收益率':'可比区间回撤'}快照曲线"><line x1="32" y1="168" x2="868" y2="168" stroke="var(--border)"/>${segments.map(group=>`<polyline points="${group.map(point).join(' ')}" fill="none" stroke="var(--primary)" stroke-width="3"/>`).join('')}${valid.map(r=>{const [x,y]=point(r).split(',');return `<circle cx="${x}" cy="${y}" r="5" fill="var(--primary)"><title>${E(r.date)} · ${E(format(r.value))}</title></circle>`;}).join('')}<text x="32" y="198">${E(rows[0].date)}</text><text x="868" y="198" text-anchor="end">${E(rows.at(-1).date)}</text><text x="32" y="20">${E(format(max))}</text></svg>${msg(curveMode==='drawdown'?'回撤以同一可比区间资产峰值计算；入出金、缺价处断开，不跨资金流推算。':curveMode==='return'?'净投入收益率受入出金影响，不代表时间加权收益率。':'点选下方日期，查看该日成交、资金、复盘与计划。')}<div class="curve-dates">${valid.map(r=>btn(`${r.date} · ${format(r.value)}`,'performance-date','','small',`data-date="${E(r.date)}"`)).join('')}</div></div>`;
  }
  function roundsPanel() {
    const s=workspace.roundsSummary||{},rounds=(workspace.rounds||[]).slice().reverse();
    return panel('交易回合与归因','已结束回合统计；开放回合单独标记，证券分红及独立费用按账本关联',`<div class="metrics business-inline-metrics">${metric('胜率',s.winRate==null?'暂无样本':percent(s.winRate),`${s.winCount||0} 胜 / ${s.lossCount||0} 负 / ${s.breakevenCount||0} 平`)}${metric('盈亏比',s.profitLossRatio==null?'暂无可比样本':Number(s.profitLossRatio).toFixed(2),'平均盈利 ÷ 平均亏损绝对值')}${metric('平均持有',s.averageHoldingDays==null?'暂无样本':Number(s.averageHoldingDays).toFixed(1)+' 天','仅已完成回合')}${metric('回合净损益',amount(s.totalPnl),'价差 + 归属分红 − 独立费用')}</div>${table(['证券','起止日期','状态','持有天数','价差损益','分红','独立费用','净损益'],rounds.map(r=>`<tr><td>${E(recordName(r))}</td><td>${E(r.startDate)}<br>${E(r.endDate||'尚未清仓')}</td><td>${r.status==='closed'?'已完成':'持仓中'}</td><td>${E(r.holdingDays??'—')}</td><td>${amount(r.pricePnl??r.realizedPnl)}</td><td>${amount(r.dividendIncome??0)}</td><td>${amount(r.standaloneFees??0)}</td><td>${amount(r.totalPnl??r.realizedPnl)}</td></tr>`),'完成建仓与清仓后可查看完整回合')}<div class="panel-body">${(workspace.attributionWarnings||[]).map(alert).join('')}${(s.bySecurity||[]).length?table(['证券归因','完成回合','价差','分红','独立费用','净损益'],s.bySecurity.map(r=>`<tr><td>${E(recordName(r))}</td><td>${E(r.completedCount)}</td><td>${amount(r.pricePnl)}</td><td>${amount(r.dividendIncome)}</td><td>${amount(r.standaloneFees)}</td><td>${amount(r.totalPnl)}</td></tr>`)):''}</div>`,'','review-rounds');
  }
  function reviewTradesResults() {
    const page=historyPage('review-trades',(workspace.ledger||[]).filter(t=>!t.voided&&['buy','sell'].includes(t.type)));
    return table(['日期','证券','买卖','数量','理由','复盘'],page.rows.map(t=>`<tr><td>${E(t.date)}</td><td>${E(recordName(t))}</td><td>${E(types[t.type])}</td><td>${E(t.quantity)}</td><td>${E(t.reason||'未记录')}</td><td>${btn('逐笔复盘','review-trade','','small',`data-id="${E(t.id)}"`)}</td></tr>`))+historyPager('review-trades',page);
  }
  function reviews() {
    return head('复盘统计','把市场、决策、执行与情绪分别复核',btn('分享摘要','share','share')+btn('写复盘','review-new','plus','primary'))+context()+performance()+roundsPanel()
      +panel('复盘档案','每日、每周、每月、逐笔交易和灵感分别归档',reviewList(),btn('历史笔记','legacy-notes','note','small')+btn('导入复盘','import-reviews','import','small')+btn('导出复盘','export-reviews','export','small'),'review-notes')
      +panel('逐笔成交复核','搜索全部历史成交，评分不以盈亏替代纪律',`<div data-history-view="review-trades">${historyControls('review-trades')}<div data-history-results="review-trades">${reviewTradesResults()}</div></div>`,'','review-execution');
  }
  function performanceDate(date) {
    const ledger=(workspace.ledger||[]).filter(r=>!r.voided&&r.date===date),reviews=(workspace.reviews||[]).filter(r=>r.date<=date&&(r.endDate||r.date)>=date),plans=(workspace.plans||[]).filter(p=>p.date===date||(p.linkedTradeIds||[]).some(id=>ledger.some(t=>t.id===id)));
    modal(`${date} · 当日记录`,`${table(['成交 / 资金','证券 / 说明','数量','价格 / 金额'],ledger.map(t=>`<tr><td>${E(types[t.type])}</td><td>${E(recordName(t)||t.reason)}</td><td>${E(t.quantity||'—')}</td><td>${amount(['buy','sell'].includes(t.type)?t.price:t.amount)}</td></tr>`),'当日没有流水')}<h3 class="section-space">复盘</h3>${reviews.map(r=>`<p>${E(r.title||kinds[r.kind])} ${btn('打开复盘','review-edit','','small',`data-id="${E(r.id)}"`)}</p>`).join('')||msg('当日没有复盘')}<h3 class="section-space">计划与执行</h3>${plans.map(p=>`<section class="business-details"><h4>${E(recordName(p))} · ${E(p.reason||p.trigger)}</h4>${executionSummary(p)}${btn('打开计划','plan-edit','','small',`data-id="${E(p.id)}"`)}</section>`).join('')||msg('当日没有计划或关联执行')}`,btn('关闭','close'));
  }
  function shareText() {
    const rows=(workspace.reviews||[]).filter(r=>r.kind!=='idea').slice(0,6);
    return `A-trade · 复盘摘要\n生成日期：${today()}\n已省略账户名称、资产金额、持仓数量和交易明细。请检查正文内的个人信息。\n\n${rows.map(r=>`${r.date} · ${kinds[r.kind]||r.kind} · ${r.score?.score==null?'未评分':r.score.score+' 分（完成度 '+r.score.coverage+'%）'}\n${r.title||''}\n${Object.entries(r.fields||{}).filter(([key,v])=>key!=='accountFacts'&&typeof v==='string'&&v.trim()).map(([,v])=>v).join('\n')}`).join('\n\n')||'暂无复盘内容'}`;
  }
  function share() {return head('复盘分享','分享前检查复盘文字中的个人信息',btn('导出文字','export-share','export','primary'))+context()+`<div class="share-layout"><section class="panel business-share"><div class="share-brand"><span class="app-logo"></span><strong>A-trade · 投资回顾</strong></div><pre>${E(shareText())}</pre></section><aside class="share-explanation"><h2>分享范围</h2><p>最近六份复盘的文字与自评分。</p><div class="divider"></div><h3>自动省略</h3><p>账户名称、资产金额、持仓数量、原始成交。</p><p class="field-hint section-space">你写在复盘正文里的姓名、金额等内容仍会保留，请导出前自行检查。</p></aside></div>`;}
  AT.accountView=page=>{let html=!workspace?.accountId||!account()?noAccount():page==='A04'?trades():page==='A08'?reviews():page==='A11'?share():overview();if(workspace?.warning)html=html.replace('</section>','</section>'+alert(workspace.warning));return html;};
  const oldView=AT.view;
  AT.view=page=>{
    if(page==='C04')return head('备份与恢复','按数据范围导出与恢复；每种文件使用对应的恢复入口',btn('导出账本备份','backup-export','export','primary'))+`<section class="panel"><div class="panel-body"><h2>账户账本</h2><p class="field-hint section-space">包含所有账户、流水、正式复盘、计划与资产快照。建议在批量导入或修改历史记录前导出一份；AI 密钥、界面状态与真实跟踪池使用各自的设置或备份。</p><label class="field section-space"><span>从账本备份恢复</span><input type="file" data-business-restore accept=".json,application/json"></label>${btn('打开数据文件夹','open-folder','folder')}</div></section><section class="panel section-space"><div class="panel-body"><h2>界面与演示状态</h2><p class="field-hint section-space">包含显示与阅读偏好、侧栏、独立费率与仓位规则、复盘草稿、历史笔记，以及演示目标、任务和流水。复盘草稿随原账户编号保存，换机后需同时恢复对应账本才能继续编辑。</p>${B('导出界面状态','export','data-action="backup"')}<label class="field section-space"><span>从界面状态备份恢复</span><input type="file" id="restore-file" accept=".json,application/json"></label><p class="field-hint">兼容旧版状态副本；选择后先校验并查看摘要，成功保存后才替换当前状态。不包含正式账本、真实跟踪池、行情缓存、研究任务或 AI 配置。</p></div></section>${AT.watchlistBackup?.panel()||''}`;
    if(page==='C06')return head('应用与诊断','A-trade · 本地账户与复盘',B('AI 模型设置','settings','data-nav="C07"'))+`<section class="panel"><div class="account-card"><span class="app-logo"></span><div><h3>A-trade</h3><p>账户、交易记录和复盘保存到本机 SQLite</p></div><span class="tag blue right">本机账本</span></div><div class="panel-body"><div class="business-summary"><div><span>账户数量</span><strong>${workspace?.accounts?.length||0}</strong></div><div><span>全局 AI</span><strong>文本 · ${E(AT.aiSettings?.status('text')||'待配置')}</strong><span>图片 · ${E(AT.aiSettings?.status('vision')||'待配置')}</span></div></div><p class="field-hint section-space">行情、策略和回测仍在后续建设阶段；账户模块不会使用演示行情估算你的持仓。</p><p class="field-hint section-space">AI 仅在点击识别截图或辅助复盘时发送当前材料，识别结果需要人工核对后入账。</p><p class="business-path section-space">${E(AT.storageInfo?.directory||'本机应用数据目录')}</p>${btn('打开数据文件夹','open-folder','folder')}${btn('历史笔记','legacy-notes','note')}</div></section>`;
    return oldView(page);
  };
  const desktopView=AT.view;
  AT.view=page=>{
    let html=desktopView(page);
    if(['C04','C06'].includes(page))html=html.replace('</section>','</section>'+`<nav class="context-nav" aria-label="相关功能">${AT.groups.find(g=>g.id==='C').pages.map(p=>`<button type="button" data-nav="${p.id}" ${page===p.id?'aria-current="page"':''}>${I(p.icon)}${E(p.title)}</button>`).join('')}</nav>`);
    return html;
  };
  function accountsModal() {
    modal('账户管理',`<div class="business-account-list">${(workspace?.accounts||[]).map(a=>`<article><div><span class="tag ${a.archived?'':'blue'}">${a.archived?'已归档':a.kind==='real'?'实盘记账':'模拟账户'}</span><h3>${E(a.name)}</h3><p>${E(a.startDate)} 起算 · 期初现金 ${amount(a.openingCash)}</p></div><div class="business-actions">${!a.archived?btn(a.id===workspace.accountId?'当前账户':'切换','account-switch','','small',`data-id="${E(a.id)}" ${a.id===workspace.accountId?'disabled':''}`):''}${btn('编辑','account-edit','','small',`data-id="${E(a.id)}"`)}${btn(a.archived?'恢复':'归档','account-archive','','small',`data-id="${E(a.id)}" data-archived="${!a.archived}"`)}</div></article>`).join('')||empty('还没有账户')}</div>`,btn('关闭','close')+btn('创建账户','account-new','plus','primary'));
  }
  function accountForm(id) {
    const a=workspace?.accounts?.find(x=>x.id===id)||{kind:'real',startDate:today(),openingCash:0,goalPercent:10,stages:[]};
    modal(id?'编辑账户':'创建账户',`<form id="business-form" data-method="account.save"><input type="hidden" name="id" value="${E(a.id||'')}"><div class="business-form-grid">${field('账户名称','name',a.name,'text','required maxlength="80"')}${select('账户类型','kind',a.kind,[['real','实盘记账'],['paper','模拟账户']])}${field('起算日期','startDate',a.startDate,'date','required')}${field('期初现金（元）','openingCash',a.openingCash,'number','required min="0" step="0.01"')}${field('每阶段目标收益率（%）','goalPercent',a.goalPercent??10,'number','required min="0.1" max="500" step="0.1"')}${field('阶段总数','stageCount',a.stages?.length||20,'number','required min="1" max="100" step="1"')}</div>${msg('期初现金不包含持仓价值。若从持仓中的账户开始，请随后添加“期初持仓”；历史修改会重新核算全部流水。')}${formError}</form>`,footer());
  }
  function ledgerForm(id,type='buy') {
    const t=(workspace?.ledger||[]).find(x=>x.id===id)||{type,date:today(),time:'09:30:00',quantity:'',price:'',amount:'',commission:0,stampTax:0,transferFee:0,otherFees:0};
    modal(id?'编辑流水':'记录交易与资金',`<form id="business-form" data-method="ledger.save"><input type="hidden" name="id" value="${E(t.id||'')}"><div class="business-form-grid">${select('记录类型','type',t.type,Object.entries(types))}${field('日期','date',t.date,'date','required')}${field('成交时间（同日按时间排序）','time',t.time,'time','step="1"')}<div></div><div data-ledger-security>${field('证券代码','code',t.code,'text','inputmode="numeric" maxlength="12"')}${field('证券名称','name',t.name)}</div><div data-ledger-stock>${field('数量（股；股份调整填正负变动）','quantity',t.quantity,'number','min="0" step="0.0001"')}${field('成交价格 / 期初每股成本（元）','price',t.price,'number','min="0" step="0.0001"')}</div><div data-ledger-cash>${field('资金金额（元）','amount',t.amount,'number','min="0.01" step="0.01"')}</div></div><section class="business-details"><header class="business-section-heading">费用明细 · 请按交割单填写</header><div class="business-form-grid">${field('佣金','commission',t.commission??0,'number','min="0" step="0.01"')}${field('印花税','stampTax',t.stampTax??0,'number','min="0" step="0.01"')}${field('过户费','transferFee',t.transferFee??0,'number','min="0" step="0.01"')}${field('其他费用','otherFees',t.otherFees??t.fees??0,'number','min="0" step="0.01"')}</div></section>${textfield('操作理由 / 备注','reason',t.reason)}${field('成交编号（用于去重，可留空）','externalId',t.externalId)}${msg('分红或独立费用可填写证券代码用于回合归因；留空记为账户级资金。没有接入券商，不会发出订单。保存后重新核算账户，现金或持仓不足时不会写入。')}${formError}</form>`,footer());updateLedgerFields();
  }
  function updateLedgerFields() {
    const f=currentForm();if(f?.dataset.method!=='ledger.save')return;
    const type=f.elements.type.value,stock=['buy','sell','opening_position','split'].includes(type);
    $$('[data-ledger-security]',f).forEach(el=>{el.hidden=!stock&&!['dividend','fee'].includes(type);$$('input',el).forEach(i=>{i.disabled=el.hidden;i.required=stock&&i.name==='code';});});
    $$('[data-ledger-stock]',f).forEach(el=>{el.hidden=!stock;$$('input',el).forEach(i=>{i.disabled=!stock;i.required=stock&&['code','quantity','price'].includes(i.name)&&!(type==='split'&&i.name==='price');});});
    const quantity=f.elements.quantity;if(type==='split')quantity.removeAttribute('min');else quantity.min='0.000001';quantity.step='0.000001';
    $$('[data-ledger-cash]',f).forEach(el=>{el.hidden=stock;$$('input',el).forEach(i=>{i.disabled=stock;i.required=!stock;});});
  }
  function reviewSchema() {return workspace?.reviewSchema||{templates:[],dimensions:[],scoreScale:[]};}
  const fallbackFields=[['market','市场环境'],['decision','决策依据'],['execution','执行与偏差'],['risk','仓位与风险'],['emotion','情绪与纪律'],['learning','经验与改进'],['next','下一步行动']];
  function reviewPeriod(kind) {
    const end=today(),date=new Date();if(kind==='weekly')date.setDate(date.getDate()-(date.getDay()+6)%7);if(kind==='monthly')date.setDate(1);
    return {date:kind==='weekly'||kind==='monthly'?date.toLocaleDateString('en-CA'):end,...(['weekly','monthly'].includes(kind)?{endDate:end}:{})};
  }
  function reviewForm(id,kind='daily',tradeId) {
    stashReviewDraft();activeReviewSection='quick';activeScoreDimension=0;scoreSerial++;factsSerial++;
    const saved=(workspace?.reviews||[]).find(x=>x.id===id)||(tradeId?(workspace.reviews||[]).find(x=>x.kind==='trade'&&x.relatedTradeIds?.length===1&&x.relatedTradeIds[0]===tradeId):null);
    const period=reviewPeriod(kind);
    const autoTitle=`${kinds[kind]} · ${period.date}`;
    currentReview=structuredClone(saved||{accountId:workspace.accountId,kind,...period,title:autoTitle,autoTitle,fields:{},scores:{},relatedTradeIds:tradeId?[tradeId]:[],draftTradeId:tradeId});
    if(tradeId&&!saved){const t=workspace.ledger.find(x=>x.id===tradeId);if(t){currentReview.date=t.date;currentReview.title=`${recordName(t)} · ${types[t.type]}复盘`;}}
    const drafts=AT.state.businessReviewDrafts||{},key=draftKey(currentReview),legacyKey=`${workspace.accountId}|new:trade`;
    let draft=drafts[key];
    if(!draft&&tradeId&&!saved&&drafts[legacyKey]?.relatedTradeIds?.includes(tradeId)){
      draft={...drafts[legacyKey],accountId:workspace.accountId,draftTradeId:tradeId};drafts[key]=draft;delete drafts[legacyKey];AT.persist();
    }
    if(draft){currentReview=structuredClone(draft);AT.toast('已恢复未提交的本机复盘草稿');}
    drawReviewForm();loadReviewFacts();
  }
  const quickFields=[['quickObservation','今天观察到了什么'],['quickDecision','决策与执行有什么偏差'],['quickNext','下一次要执行的一件事']];
  function reviewMode(mode) {
    AT.ui.reviewMode=mode;
    $$('[data-review-complete]').forEach(el=>el.hidden=mode!=='full');
    $$('[data-business="review-mode"]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.mode===mode)));
    if(mode==='quick'&&activeReviewSection.startsWith('section-'))showReviewSection('quick');
  }
  function updateReviewTitle() {
    const f=currentForm();if(f?.dataset.method!=='review.save'||!currentReview.autoTitle||f.elements.title.value!==currentReview.autoTitle)return;
    currentReview.autoTitle=`${kinds[f.elements.kind.value]} · ${f.elements.date.value}`;f.elements.title.value=currentReview.autoTitle;
  }
  async function loadReviewFacts() {
    const f=currentForm();if(f?.dataset.method!=='review.save')return;
    const target=f.querySelector('[name="field:accountFacts"]'),start=f.elements.date.value,end=f.elements.endDate?.value||start,accountId=workspace.accountId,serial=++factsSerial;
    if(!target||!start||!end)return;
    const status=f.querySelector('[data-review-facts-status]'),previous=target.value;
    if(previous.trim()&&previous!==currentReview.factsAutoText){if(status)status.textContent='已保留你填写的事实正文；请核对其中日期与当前复盘周期一致。清空此栏后可重新带入。';return;}
    if(status)status.textContent='正在读取所选日期的账本事实…';
    try{
      const data=await call('account.asof',{accountId,date:end});if(serial!==factsSerial||!f.isConnected||workspace.accountId!==accountId||f.elements.date.value!==start||(f.elements.endDate?.value||start)!==end||target.value!==previous)return;
      const ledger=(workspace.ledger||[]).filter(t=>!t.voided&&t.date>=start&&t.date<=end),ids=new Set(ledger.map(t=>t.id));
      const plans=(data.plans||[]).filter(p=>(p.date>=start&&p.date<=end)||(p.linkedTradeIds||[]).some(id=>ids.has(id)));
      const short=value=>String(value||'').slice(0,160),limited=(items,render)=>[...items.slice(0,40).map(render),...(items.length>40?[`另有 ${items.length-40} 条未展开，请在账本中核对。`]:[])];
      const facts=[`账本事实 · ${start}${end!==start?' 至 '+end:''}（持仓截至 '+end+'）`,`现金：${amount(data.summary?.cash)}；资产：${amount(data.summary?.equity)}。`, '持仓：',...limited(data.positions||[],p=>`${short(recordName(p))}：${p.quantity} 股，成本 ${amount(p.cost)}，估值 ${p.marketPrice==null?'缺价':amount(p.marketPrice)}。`),'本期流水：',...limited(ledger,t=>`${t.date} ${types[t.type]} ${short(recordName(t))} ${t.quantity||''} ${['buy','sell','opening_position'].includes(t.type)?'@ '+amount(t.price):t.type==='split'?'股数变动':amount(t.amount)}；理由：${short(t.reason)||'未记录'}。`),'计划与执行：',...limited(plans,p=>`${p.date} ${short(recordName(p))} ${types[p.action]||p.action}；触发：${short(p.trigger)||'未填写'}；实际 ${p.execution?.quantity??0} 股；${short(p.executionNotes)||'未填写偏差'}。`)];
      const factsText=facts.join('\n');target.value=factsText.length>17000?factsText.slice(0,17000)+'\n内容较多，后续事实省略；请在账本中核对。':factsText;currentReview.factsAutoText=target.value;if(status)status.textContent='已带入账本事实；未编辑的自动事实会随复盘日期刷新，手工补充始终保留。';stashReviewDraft();updateReviewProgress();
    }catch(error){if(serial===factsSerial&&status?.isConnected)status.textContent='事实读取失败：'+errorText(error);}
  }
  function updateLiveScore() {
    const f=currentForm();if(f?.dataset.method!=='review.save')return;
    clearTimeout(scoreTimer);const serial=++scoreSerial;
    scoreTimer=setTimeout(async()=>{
      if(!f.isConnected||f!==currentForm())return;
      try{const review=collectReview(),score=await call('review.score',{review});if(serial!==scoreSerial||!f.isConnected)return;currentReview.score=score;const target=f.querySelector('[data-live-score]');if(target)target.innerHTML=scoreSummary(score);const progress=f.querySelector('[data-review-section="scores"] [data-review-progress]');if(progress)progress.textContent=`有效 ${score.ratedCount} / ${score.applicableCount} 项 · 有证据 ${score.evidenceCoverage??0}%`;}
      catch(error){if(serial===scoreSerial&&f.isConnected){const target=f.querySelector('[data-live-score]');if(target)target.innerHTML=msg('评分暂未更新：'+errorText(error));}}
    },180);
  }
  function drawReviewForm() {
    const r=currentReview,schema=reviewSchema(),template=schema.templates?.find(t=>(t.kind||t.id)===r.kind),sections=structuredClone(template?.sections||[{label:'复盘内容',fields:fallbackFields.map(([id,label])=>({id,label}))}]);
    const quickIds=new Set(['accountFacts',...quickFields.map(([id])=>id)]),known=new Set(sections.flatMap(s=>s.fields.map(f=>f.id))),extra=Object.keys(r.fields||{}).filter(id=>id!=='aiDraft'&&!quickIds.has(id)&&!known.has(id));
    if(extra.length)sections.push({label:'导入原文与补充记录',fields:extra.map(id=>({id,label:({importedText:'导入原文',importedLegacyScores:'原评分记录',importedOriginalRecord:'导入原始记录'})[id]||id}))});
    const dims=schema.dimensions||[],scale=schema.scoreScale?.length?schema.scoreScale:[{value:0,label:'明显失误'},{value:1,label:'执行不足'},{value:2,label:'基本达标'},{value:3,label:'良好'},{value:4,label:'充分做到'}];
    const pages=sections.map((section,index)=>({key:'section-'+index,label:section.label,hint:section.fields.length+' 项观察',body:section.fields.map(f=>textfield(f.label,'field:'+f.id,r.fields?.[f.id],f.prompt||'写下当时观察到的事实与判断')).join('')}));
    pages.unshift({key:'quick',label:'快速复盘',hint:'三件事与账本事实',body:quickFields.map(([id,label])=>textfield(label,'field:'+id,r.fields?.[id])).join('')+textfield('当日账本事实','field:accountFacts',r.fields?.accountFacts,'自动带入持仓、成交和计划，保留你的补充')+'<p class="field-hint" data-review-facts-status>仅使用本机账本，行情判断请补充来源。</p>'+btn('补充账本事实','review-facts','','small')});
    if(r.kind!=='idea')pages.push({key:'scores',label:'纪律与决策评分',hint:'逐项自评 · 附上依据',body:msg('盈利不等于好的决策。未评价与不适用不会当作满分；为评分写下可核对的事实。')+`<div class="review-score-tabs" aria-label="评分维度">${dims.map((d,index)=>`<button type="button" data-business="score-dimension" data-score-dimension="${index}" aria-pressed="${index===activeScoreDimension}">${E(d.label)}</button>`).join('')}</div>`+dims.map((d,index)=>`<section class="business-rubric" data-score-panel="${index}" ${index!==activeScoreDimension?'hidden':''}><h3>${E(d.label)}</h3><p>${E(d.description||'')}</p>${msg(scale.map(s=>s.value+' · '+s.label).join('　'))}${(d.items||[]).map(item=>{const raw=r.scores?.[item.id],score=typeof raw==='number'?{score:raw}:raw||{},value=score.notApplicable?'na':score.score==null?'':String(score.score);return `<div class="business-score-item"><header><strong>${E(item.label)}</strong><p>${E(item.prompt||'')}</p></header><div class="business-score-choices" role="group" aria-label="${E(item.label)}评分">${[{value:'',label:'未评',title:'未评价'},...scale.map(s=>({value:String(s.value),label:String(s.value),title:s.label})),{value:'na',label:'不适用',title:'不适用，请填写原因'}].map(s=>`<label title="${E(s.title)}"><input type="radio" name="score:${E(item.id)}" value="${E(s.value)}" aria-label="${E(s.title)}" ${value===s.value?'checked':''}><span>${E(s.label)}</span></label>`).join('')}</div><input name="evidence:${E(item.id)}" value="${E(score.evidence||r.scoreNotes?.[item.id]||'')}" placeholder="评分依据 / 不适用原因" aria-label="${E(item.label)}评分依据"></div>`;}).join('')}</section>`).join('')+`<div data-live-score>${scoreSummary(r.score)}</div>`});
    pages.push({key:'trades',label:'关联成交',hint:(r.relatedTradeIds||[]).length+' 笔已关联',body:tradeCheckboxes('relatedTradeIds',r.relatedTradeIds||[])});
    pages.push({key:'ai',label:'AI 辅助草稿',hint:'检查后采纳为附注',body:msg('将发送当前复盘文字、评分、关联成交，以及所选日期范围内此账户的流水、计划和快照给全局设置中的文本 AI。AI 结论仅作草稿；策略提示词将在后续完善。')+aiRoute('text')+btn('发送当前材料，生成草稿','ai-review','note','small',AT.aiSettings?.ready('text')?'':'disabled')+textfield('已采纳的 AI 草稿','field:aiDraft',r.fields?.aiDraft,'可编辑，不会自动改变评分')});
    if(!pages.some(p=>p.key===activeReviewSection))activeReviewSection=pages[0].key;
    modal(r.id?'编辑复盘':'写一份复盘',`<form id="business-form" class="review-editor" data-method="review.save"><div class="review-basics"><p class="field-hint">输入自动保存为本机草稿，点击“保存复盘”后加入正式档案。</p><input type="hidden" name="id" value="${E(r.id||'')}"><div class="business-form-grid">${select('复盘类型','kind',r.kind,Object.entries(kinds))}${field('日期','date',r.date,'date','required')}${r.kind==='weekly'||r.kind==='monthly'?field('周期结束日期','endDate',r.endDate||r.date,'date','required'):''}</div>${field('标题','title',r.title,'text','required maxlength="200"')}</div><div class="business-tabs review-mode-tabs"><button type="button" data-business="review-mode" data-mode="quick" aria-pressed="true">快速复盘</button><button type="button" data-business="review-mode" data-mode="full" aria-pressed="false">完整复盘</button><span class="field-hint">完整内容、评分与关联成交始终保留</span></div><div class="review-layout"><nav class="review-sections" aria-label="复盘分区">${pages.map((p,index)=>`<button type="button" data-business="review-section" ${p.key.startsWith('section-')?'data-review-complete':''} data-review-section="${p.key}" aria-controls="review-panel-${p.key}" aria-pressed="${p.key===activeReviewSection}"><span class="review-section-number">${String(index+1).padStart(2,'0')}</span><span><strong>${E(p.label)}</strong><small data-review-progress>${E(p.hint)}</small></span></button>`).join('')}</nav><div class="review-content">${pages.map((p,index)=>`<section class="review-section" id="review-panel-${p.key}" data-review-panel="${p.key}" aria-labelledby="review-heading-${p.key}" ${p.key!==activeReviewSection?'hidden':''}><header class="review-section-heading"><span class="eyebrow">${String(index+1).padStart(2,'0')} / ${String(pages.length).padStart(2,'0')}</span><h2 id="review-heading-${p.key}">${E(p.label)}</h2><p>${E(p.hint)}</p></header>${p.body}</section>`).join('')}</div></div>${formError}</form>`,btn('丢弃草稿','review-discard','','small')+btn('返回','close','back')+btn('保存复盘','save-form','save','primary'));
    reviewMode(AT.ui.reviewMode||'quick');updateReviewProgress();updateLiveScore();
  }
  function showReviewSection(key) {
    const panel=$(`[data-review-panel="${CSS.escape(key)}"]`);if(!panel)return;
    activeReviewSection=key;
    $$('[data-review-panel]').forEach(el=>el.hidden=el!==panel);
    $$('[data-review-section]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.reviewSection===key)));
    // Switching a section only changes visibility; all draft controls stay in the form.
    const layout=$('.review-layout');if(layout.getBoundingClientRect().top<80)window.scrollTo({top:layout.getBoundingClientRect().top+window.scrollY-90,behavior:'instant'});
  }
  function showScoreDimension(index) {
    activeScoreDimension=Number(index);
    $$('[data-score-panel]').forEach(el=>el.hidden=Number(el.dataset.scorePanel)!==activeScoreDimension);
    $$('[data-score-dimension]').forEach(el=>el.setAttribute('aria-pressed',String(Number(el.dataset.scoreDimension)===activeScoreDimension)));
  }
  function updateReviewProgress() {
    $$('[data-review-section]').forEach(button=>{
      const key=button.dataset.reviewSection,panel=$(`[data-review-panel="${CSS.escape(key)}"]`),progress=$('[data-review-progress]',button);if(!panel||!progress)return;
      if(key==='trades'){progress.textContent=new FormData(currentForm()).getAll('relatedTradeIds').length+' 笔已关联';return;}
      if(key==='scores'){progress.textContent='当前草稿评分 · 自动更新';return;}
      const fields=$$('textarea[name^="field:"]',panel);if(fields.length)progress.textContent=`已填写 ${fields.filter(el=>el.value.trim()).length} / ${fields.length} 项`;
    });
  }
  function scoreSummary(score) {
    if(!score||typeof score!=='object')return '';
    return `<div class="business-score-overview"><small>当前草稿评分</small><strong>${score.score==null?'尚未评分':E(score.score)+' / 100'}</strong><span>评分完成度 ${E(score.coverage||0)}% · 有证据 ${E(score.evidenceCoverage||0)}%</span><div>${(score.dimensions||[]).map(d=>`<span>${E(d.label)}<b>${d.score==null?'—':E(d.score)}</b></span>`).join('')}</div>${msg(score.explanation||'未填写或无证据的项目仍占分母；有理由的不适用项才排除。')}</div>`;
  }
  function tradeCheckboxes(name,ids) {
    tradeSelections[name]=new Set(ids);
    return `<div data-trade-selector="${E(name)}">${historyControls(name)}<div data-history-results="${E(name)}">${tradeSelectionResults(name)}</div></div>`;
  }
  function tradeSelectionResults(name) {
    const selected=tradeSelections[name]||new Set(),page=historyPage(name,(workspace.ledger||[]).filter(t=>(!t.voided&&['buy','sell'].includes(t.type))||selected.has(t.id))),visible=new Set(page.rows.map(t=>t.id));
    return `<p class="field-hint">已关联 ${selected.size} 笔 · 筛选和翻页保留全部勾选</p>${[...selected].filter(id=>!visible.has(id)).map(id=>`<input type="hidden" name="${E(name)}" value="${E(id)}">`).join('')}<div class="business-trade-checks">${page.rows.map(t=>`<label><input type="checkbox" data-trade-selection="${E(name)}" name="${E(name)}" value="${E(t.id)}" ${selected.has(t.id)?'checked':''}><span>${E(t.date)} · ${E(recordName(t))} · ${E(types[t.type])} ${E(t.quantity)} 股 @ ${amount(t.price)}${t.voided?'（已作废）':''}</span></label>`).join('')||msg('没有符合筛选的成交')}</div>${historyPager(name,page)}`;
  }
  function refreshHistory(key) {
    const target=$(`[data-history-results="${CSS.escape(key)}"]`);if(!target)return;
    target.innerHTML=key==='ledger'||key==='cash'?ledgerResults(key==='cash'):key==='review-trades'?reviewTradesResults():tradeSelectionResults(key);AT.hydrateIcons?.();
  }
  function collectReview() {
    const f=currentForm(),d=formData(f),fields={...currentReview?.fields},scores={...currentReview?.scores};
    for(const [key,value] of Object.entries(d)){if(key.startsWith('field:'))fields[key.slice(6)]=value;if(key.startsWith('score:'))scores[key.slice(6)]={score:value===''||value==='na'?null:Number(value),notApplicable:value==='na',evidence:d['evidence:'+key.slice(6)]||''};}
    return {...currentReview,accountId:currentReview.accountId||workspace.accountId,id:d.id||undefined,kind:d.kind,date:d.date,endDate:d.endDate||undefined,title:d.title,fields,scores,relatedTradeIds:new FormData(f).getAll('relatedTradeIds'),rubricVersion:reviewSchema().version};
  }
  function stashReviewDraft() {
    const f=currentForm();if(f?.dataset.method!=='review.save'||f.dataset.saved==='true')return;
    const r=collectReview();AT.state.businessReviewDrafts=AT.state.businessReviewDrafts||{};
    AT.state.businessReviewDrafts[draftKey(r)]=JSON.parse(JSON.stringify(r));clearTimeout(draftTimer);
    draftTimer=setTimeout(()=>AT.persist(),350);
  }
  function planForm(id) {
    const p=workspace.plans?.find(x=>x.id===id)||{date:today(),action:'buy',status:'draft'};
    modal(id?'计划与执行复核':'新增交易计划',`<form id="business-form" data-method="plan.save"><input type="hidden" name="id" value="${E(p.id||'')}"><div class="business-form-grid">${field('计划日期','date',p.date,'date','required')}${select('计划动作','action',p.action,[['buy','买入'],['sell','卖出'],['add','加仓'],['reduce','减仓'],['hold','持有'],['watch','观察']])}${field('证券代码','code',p.code,'text','required')}${field('证券名称','name',p.name)}${field('计划数量（股，可留空）','quantity',p.quantity,'number','min="0" step="0.0001"')}${select('执行状态','status',p.status,[['draft','草稿'],['watching','观察中'],['ready','已准备'],['triggered','已触发'],['executed','已执行'],['cancelled','已取消'],['expired','已失效']])}</div>${textfield('触发条件','trigger',p.trigger)}<div class="business-form-grid">${field('止损条件','stopLoss',p.stopLoss)}${field('止盈条件','takeProfit',p.takeProfit)}</div>${textfield('计划依据','reason',p.reason)}${textfield('实际执行与偏差','executionNotes',p.executionNotes)}${executionSummary(p)}<section class="business-details"><header class="business-section-heading">关联实际成交</header>${tradeCheckboxes('linkedTradeIds',p.linkedTradeIds||[])}</section>${formError}</form>`,footer('保存计划'));
  }
  function snapshotsModal() {
    modal('资产快照',`${msg('每份快照保存当日现金、持仓价格与总资产。历史日期按截至当日的账本计算；价格来源和日期均可追溯。')}${table(['日期','现金','持仓市值','总资产','价格来源 / 日期','操作'],(workspace.snapshots||[]).map(s=>`<tr><td>${E(s.date)}</td><td>${amount(s.summary?.cash)}</td><td>${amount(s.summary?.marketValue)}</td><td>${amount(s.summary?.equity)}</td><td>${Object.entries(s.priceDetails||{}).map(([code,detail])=>`<small class="business-subtext">${E(code)} · ${E(priceSource(detail))}</small>`).join('')||'手工价格 / '+E(s.date)}</td><td>${btn('查看与编辑','snapshot-edit','','small',`data-date="${E(s.date)}"`)}${btn('当日记录','performance-date','','small',`data-date="${E(s.date)}"`)}</td></tr>`))}`,btn('关闭','close')+btn('新增资产快照','snapshot-new','plus','primary'));
  }
  async function snapshotForm(date=today()) {
    snapshotSession={accountId:workspace.accountId,date,token:0,priceDetails:{},positions:[]};
    modal('保存资产快照',`<form id="business-form" data-method="snapshot.save">${field('快照日期','date',date,'date','required data-snapshot-date')}${msg('先按日期恢复当时持仓，再手动填写或从行情填充价格。缺价不填零；保存前核对来源与价格日期。')}<div class="business-actions">${btn('从行情填充价格','snapshot-market','refresh','small')}</div><p class="field-hint" data-snapshot-status>正在读取历史持仓…</p><div data-snapshot-positions></div>${textfield('补充价格（每行：代码,价格，可留空）','extraPrices','','600000,12.34')}${formError}</form>`,footer('保存快照'));
    return loadSnapshotDate(date);
  }
  function priceSource(detail) {
    if(!detail)return '尚未填写价格';
    return `${detail.source==='manual'?'手工填写':detail.source||'未知来源'} · ${detail.priceDate||'日期未知'}${detail.stale?' · 非目标日价格，请核对':''}${detail.adjustment==='none'?' · 不复权':''}`;
  }
  async function loadSnapshotDate(date) {
    const s=snapshotSession,f=currentForm();if(!s||f?.dataset.method!=='snapshot.save'||!date)return;
    const token=++s.token;s.date=date;s.error='';s.loading=true;f.dataset.loading='true';f.querySelector('[data-snapshot-positions]').inert=true;const status=f.querySelector('[data-snapshot-status]');status.textContent='正在读取 '+date+' 的持仓…';
    try{
      const data=await call('account.asof',{accountId:s.accountId,date});if(s!==snapshotSession||s.token!==token||!f.isConnected)return;
      s.positions=data.positions||[];const existing=(workspace.snapshots||[]).find(x=>x.date===date);s.priceDetails=structuredClone(existing?.priceDetails||{});
      f.querySelector('[data-snapshot-positions]').innerHTML=s.positions.length?`<div class="business-form-grid">${s.positions.map(p=>`<div>${field(`${p.name||p.code} (${p.code}) · ${p.quantity} 股`,'price:'+p.code,existing?.prices?.[p.code]??'','number','min="0.0001" step="0.0001" data-snapshot-price="'+E(p.code)+'"')}<p class="field-hint" data-price-source="${E(p.code)}">${E(priceSource(s.priceDetails[p.code]||(existing?.prices?.[p.code]?{source:'manual',priceDate:date}:null)))}</p></div>`).join('')}</div>`:msg('截至该日没有持仓，快照资产来自现金。');
      status.textContent=`${date} · ${s.positions.length} 只持仓 · 现金 ${amount(data.summary?.cash)}。空白价格会保留为缺价。`;
    }catch(error){if(s===snapshotSession&&s.token===token&&f.isConnected){f.querySelector('[data-snapshot-positions]').innerHTML='';status.textContent=errorText(error);s.error=errorText(error);}}
    finally{if(s===snapshotSession&&s.token===token){s.loading=false;delete f.dataset.loading;f.querySelector('[data-snapshot-positions]').inert=false;}}
  }
  async function fillSnapshotMarket() {
    const s=snapshotSession,f=currentForm();if(!s||s.loading||f?.dataset.method!=='snapshot.save')return;
    const token=++s.token,date=s.date,button=f.querySelector('[data-business="snapshot-market"]'),status=f.querySelector('[data-snapshot-status]');button.disabled=true;s.loading=true;f.dataset.loading='true';status.textContent='正在读取 '+date+' 的不复权价格…';
    const previous=Object.fromEntries($$('[data-snapshot-price]',f).map(el=>[el.dataset.snapshotPrice,el.value]));
    try{const data=await call('account.valuation',{accountId:s.accountId,date});if(s!==snapshotSession||s.token!==token||!f.isConnected||f.elements.date.value!==date)return;
      let filled=0;
      for(const el of $$('[data-snapshot-price]',f)){const code=el.dataset.snapshotPrice,value=data.prices?.[code];if(el.value!==previous[code]||el.value.trim()||!Number.isFinite(value)||value<=0)continue;el.value=String(value);s.priceDetails[code]=data.priceDetails?.[code]||{source:'market',priceDate:date,requestedDate:date,adjustment:'none',stale:false};f.querySelector(`[data-price-source="${CSS.escape(code)}"]`).textContent=priceSource(s.priceDetails[code]);filled++;}
      const missing=(data.missingPrices||[]).join('、');status.textContent=`已填充 ${filled} 只证券，已有手工价格保持原值。${missing?'缺价：'+missing+'。':''}${(data.warnings||[]).join('；')}`;
    }catch(error){if(s===snapshotSession&&s.token===token&&status.isConnected)status.textContent=errorText(error);}
    finally{if(button.isConnected)button.disabled=false;if(s===snapshotSession&&s.token===token){s.loading=false;delete f.dataset.loading;}}
  }
  async function saveForm() {
    const f=currentForm();if(!f||!f.reportValidity()||f.dataset.saving||f.dataset.loading)return;
    const method=f.dataset.method,originAccountId=workspace?.accountId;let data=formData(f);
    if(method==='review.save')data=collectReview();
    else if(method==='snapshot.save'){
      if(snapshotSession?.error)return notice(snapshotSession.error,f);
      const prices={},priceDetails={...snapshotSession?.priceDetails};
      for(const [key,value]of Object.entries(data))if(key.startsWith('price:')&&value!==''){const code=key.slice(6);prices[code]=Number(value);if(!priceDetails[code])priceDetails[code]={source:'manual',priceDate:data.date,requestedDate:data.date,adjustment:'none',stale:false};}
      for(const line of (data.extraPrices||'').split(/\r?\n/).filter(x=>x.trim())){const [code,price]=line.split(/[,，\s]+/);if(!code||price==null||!Number.isFinite(Number(price))||Number(price)<=0)return notice('补充价格请按“代码,正数价格”填写。',f);prices[code]=Number(price);priceDetails[code]={source:'manual',priceDate:data.date,requestedDate:data.date,adjustment:'none',stale:false};}
      for(const code of Object.keys(priceDetails))if(prices[code]==null)delete priceDetails[code];data={date:data.date,prices,priceDetails};
    }
    else {for(const key of ['openingCash','goalPercent','quantity','price','amount','commission','stampTax','transferFee','otherFees'])if(data[key]!==undefined)data[key]=data[key]===''?undefined:Number(data[key]);if(!data.id)delete data.id;if(method==='plan.save')data.linkedTradeIds=new FormData(f).getAll('linkedTradeIds');if(method==='account.save'){data.stages=Array.from({length:Number(data.stageCount)||20},(_,index)=>({index:index+1,targetPercent:Number(data.goalPercent)}));delete data.stageCount;}}
    const savedDraftKey=method==='review.save'?draftKey(data):null;
    stashReviewDraft();f.dataset.saving='true';f.inert=true;const save=$('[data-business="save-form"]');if(save)save.disabled=true;
    try{const result=await call(method,{...data,accountId:originAccountId});if(method==='review.save'){f.dataset.saved='true';delete AT.state.businessReviewDrafts?.[savedDraftKey];await AT.persist();}if(originAccountId!==workspace?.accountId){await refresh(workspace?.accountId);AT.toast('已保存到原账户的本机账本');return;}applyWorkspace(result);if(!result?.accounts&&!result?.workspace)await refresh();if(f.isConnected)AT.closeBusinessModal();AT.render();AT.toast('已保存到本机账本');}
    catch(error){notice(error,f);}finally{delete f.dataset.saving;f.inert=false;if(save?.isConnected)save.disabled=false;}
  }
  function confirmAction(title,description,action,attrs='') {AT.openBusinessModal(title,`<p class="business-confirm">${E(description)}</p>`,{className:'business-modal',footer:btn('取消','close')+btn('确认',action,'check','primary',attrs)});}
  function download(name,data,mime='application/json') {
    const url=URL.createObjectURL(new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type:mime}));
    const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function exportLedger() {
    const headers=['id','date','time','type','code','name','quantity','price','amount','commission','stampTax','transferFee','otherFees','reason','externalId','voided'];
    const cell=value=>{let s=String(value??'');if(/^[=+@\-]/.test(s)&&!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
    download(`A-trade-流水-${today()}.csv`,'\ufeff'+[headers.join(','),...(workspace.ledger||[]).filter(r=>!r.voided).map(r=>headers.map(k=>cell(r[k])).join(','))].join('\r\n'),'text/csv;charset=utf-8');
  }
  function importModal(reset=false) {
    if(reset||!importState||importState.accountId!==workspace.accountId)importState={accountId:workspace.accountId,tab:'images',images:[],rows:[],warnings:[],defaultDate:'',file:null,preview:null,kind:'trades',reviewed:false};
    const s=importState;
    if(s.kind==='reviews')return reviewImportModal();
    modal('导入与对账',`<div class="business-tabs"><button data-business="import-tab" data-tab="images" aria-pressed="${s.tab==='images'}">同花顺成交截图</button><button data-business="import-tab" data-tab="file" aria-pressed="${s.tab==='file'}">表格 / JSON</button></div>${s.tab==='images'?`<div class="business-import-intro"><h3>先识别，再逐笔核对</h3><p>截图会发送给你配置的 AI 视觉模型。请裁掉无关个人信息，确认日期、成交方向、数量和费用后再入账。</p></div><label class="field"><span>选择成交截图（PNG / JPG / WebP，最多 4 张）</span><input type="file" data-business-images accept="image/png,image/jpeg,image/webp" multiple></label>${msg('支持 Ctrl+V 粘贴截图。每张不超过 5 MiB，合计不超过 12 MiB；缺失的费用与日期需要人工补充。')}<div class="business-image-strip">${s.images.map((img,index)=>`<figure><img src="data:${E(img.mimeType)};base64,${img.dataBase64}" alt="${E(img.name)}"><figcaption>${E(img.name)}${btn('移除','import-image-remove','','small',`data-index="${index}"`)}</figcaption></figure>`).join('')}</div>${field('截图未显示日期时，手动补充成交日期（可留空）','importDate',s.defaultDate,'date')}${aiRoute('vision')}<div class="business-actions">${btn(s.busy?'正在识别…':'发送图片并识别','import-recognize','import','primary',s.busy||!s.images.length||!AT.aiSettings?.ready('vision')?'disabled':'')}</div>`:`<label class="field"><span>选择交易记录文件</span><input type="file" data-business-import-file accept=".csv,.xlsx,.json"></label>${msg('支持 CSV、XLSX 与 JSON。自动识别常见中文表头；解析完成后可逐行修正，实际写入前会再次核对。')}${s.file?msg(s.file.name):''}`}${s.warnings?.length?alert(s.warnings.join('；')):''}${s.rows.length&&s.rows.some(row=>importRecord(row).source==='ocr')?`<label class="business-check"><input type="checkbox" data-import-reviewed ${s.reviewed?'checked':''}>我已对照截图核对所选记录的日期、证券、方向、数量、价格与费用</label>`:''}<div data-import-preview>${renderImportPreview()}</div><p class="business-error" data-import-error role="alert" ${s.error?'':'hidden'}>${E(s.error||'')}</p>`,btn('关闭','close')+(s.rows.length?btn('重新校验','import-validate','check')+btn('确认导入选中记录','import-commit','save','primary',s.busy?'disabled':''):''));
  }
  function importRecord(row){return row.entry||row.record||row.values||row;}
  function importVisible(session=importState) {return session===importState&&session?.accountId===workspace?.accountId&&!!$('#workspace-root [data-business-import-file],#workspace-root [data-business-images]');}
  function reviewImportModal() {
    const s=importState;
    modal('导入复盘档案',`<p class="field-hint">支持 LaimiuTrade 复盘 JSON、本软件导出的复盘，以及 Markdown / TXT。原文会完整保留，旧评分不直接转换为当前 24 项评分。</p><label class="field section-space"><span>选择复盘文件（不超过 10 MiB）</span><input type="file" data-business-import-file accept=".json,.md,.markdown,.txt"></label>${s.file?msg(s.file.name):''}${s.warnings?.length?alert(s.warnings.join('；')):''}<div class="business-import-rows">${s.rows.map((row,index)=>{const r=importRecord(row);return `<article data-import-row="${index}"><header><label><input type="checkbox" data-import-selected ${row.selected!==false&&!row.duplicate?'checked':''}><strong>第 ${row.rowNumber||index+1} 篇</strong></label><span class="tag">${row.duplicate?'重复档案':(row.errors||[]).length?'待修正':'待确认'}</span></header><div class="business-form-grid">${select('类型','kind',r.kind||'daily',Object.entries(kinds))}${field('开始日期','date',r.date,'date')}${field('结束日期','endDate',r.endDate||r.date,'date')}${field('标题','title',r.title)}</div><section class="business-details"><header class="business-section-heading">检查导入原文</header>${Object.entries(r.fields||{}).map(([id,value])=>textfield(({importedText:'导入原文',importedLegacyScores:'原评分'})[id]||id,'review-field:'+id,value)).join('')}</section>${(row.errors||[]).length?`<p class="business-error">${E(row.errors.map(errorText).join('；'))}</p>`:''}${(row.warnings||[]).length?msg(row.warnings.join('；')):''}</article>`;}).join('')}</div><p class="business-error" data-import-error role="alert" ${s.error?'':'hidden'}>${E(s.error||'')}</p>`,btn('关闭','close')+(s.rows.length?btn('重新校验','import-validate','check')+btn('确认导入选中复盘','import-commit','save','primary'):''));
  }
  function renderImportPreview() {
    const s=importState;if(!s.rows.length)return '';
    const rows=s.rows;
    return `<div class="business-import-heading"><h3>逐笔校对</h3><p>${rows.length} 行 · 修改后点击“重新校验”，无错误的选中记录才能导入。重复记录默认不选。</p></div><div class="business-import-rows">${rows.map((row,index)=>{const r=importRecord(row),errors=row.errors||[],warnings=row.warnings||[];return `<article data-import-row="${index}"><header><label><input type="checkbox" data-import-selected ${row.selected!==false&&!row.duplicate?'checked':''}><strong>第 ${row.rowNumber||index+1} 行</strong></label><span class="tag ${row.duplicate?'':errors.length?'orange':'blue'}">${row.duplicate?'重复记录':errors.length?'待修正':'待核对'}</span></header><div class="business-import-fields">${field('日期','date',r.date,'date')}${field('时间','time',r.time||'','time','step="1"')}${select('方向','type',r.type||'',[['','请选择方向'],...Object.entries(types)])}${field('代码','code',r.code)}${field('名称','name',r.name)}${field('数量','quantity',r.quantity,'number','min="0" step="0.0001"')}${field('价格','price',r.price,'number','min="0" step="0.0001"')}${field(['buy','sell'].includes(r.type)?'成交金额（不含费用）':'资金金额','amount',r.amount,'number','min="0" step="0.01"')}</div>${renderReconciliation(r)}<section class="business-details"><header class="business-section-heading">费用与成交编号${r.feeMissing?'<span>截图未显示，请核对</span>':''}</header><div class="business-form-grid">${field('佣金','commission',r.commission??'','number','min="0" step="0.01"')}${field('印花税','stampTax',r.stampTax??'','number','min="0" step="0.01"')}${field('过户费','transferFee',r.transferFee??'','number','min="0" step="0.01"')}${field('其他费用','otherFees',r.otherFees??'','number','min="0" step="0.01"')}${field('成交编号','externalId',r.externalId)}${field('备注','reason',r.reason)}</div><label class="business-check"><input type="checkbox" data-fee-confirm ${r.feesConfirmed===true||(!r.feeMissing&&r.source!=='ocr'&&(!r.reconciliation?.fees||r.reconciliation.fees.status==='matched'))?'checked':''}>已核对费用（若为 0，确认免收或自行选择暂按 0 记账）</label></section>${errors.length?`<p class="business-error">${E(errors.map(errorText).join('；'))}</p>`:''}${warnings.length?msg(warnings.map(errorText).join('；')):''}</article>`;}).join('')}</div>`;
  }
  function renderReconciliation(record) {
    return Object.entries(record.reconciliation||{}).filter(([,item])=>item&&typeof item==='object'&&item.status!=='matched').map(([kind,item])=>{
      const fees=kind==='fees', label=fees?'费用合计与分项':'成交金额与数量 × 价格';
      const choices=fees?[['source','采用原文件费用合计（记入其他费用）'],['components','采用已核对的费用分项'],['corrected','已手工修正费用分项']]:[['source','采用原文件成交金额'],['calculated','采用数量 × 价格'],['corrected','已手工修正数量、价格和金额']];
      return `<section class="business-details business-reconciliation" data-reconciliation="${E(kind)}"><h4>${E(label)} · ${item.status==='resolved'?'已选择处理方式':'待核对差异'}</h4>${msg('原文件：'+String(item.sourceValue??'未提供')+' 元；计算值：'+String(item.calculatedValue??'未提供')+' 元。原始值会随记录保留。')}${select('核对后的处理方式','reconcile:'+kind,item.resolution||'',[['','请选择处理方式'],...choices])}</section>`;
    }).join('');
  }
  function collectImportRows() {
    if(!importState)return;
    $$('[data-import-row]').forEach(node=>{const index=Number(node.dataset.importRow),row=importState.rows[index],r={...importRecord(row)};$$('input[name],select[name]',node).forEach(el=>{if(el.name.startsWith('reconcile:'))return;r[el.name]=el.type==='number'?(el.value===''?null:Number(el.value)):el.value;});$$('select[name^="reconcile:"]',node).forEach(el=>{const kind=el.name.slice(10);r.reconciliation={...r.reconciliation,[kind]:{...r.reconciliation?.[kind],resolution:el.value||null}};});const fee=$('[data-fee-confirm]',node);if(fee){r.feesConfirmed=fee.checked;r.feeMissing=!fee.checked;if(fee.checked)for(const key of ['commission','stampTax','transferFee','otherFees'])if(r[key]==null)r[key]=0;}row.record=r;row.entry=r;row.values=r;row.selected=$('[data-import-selected]',node).checked;});
    const date=$('[name="importDate"]');if(date)importState.defaultDate=date.value;const reviewed=$('[data-import-reviewed]');if(reviewed)importState.reviewed=reviewed.checked;
    if(importState.kind==='reviews')$$('[data-import-row]').forEach(node=>{const row=importState.rows[Number(node.dataset.importRow)],r=importRecord(row);r.fields={...r.fields};$$('[name^="review-field:"]',node).forEach(input=>r.fields[input.name.slice(13)]=input.value);});
  }
  function toBase64(text){const bytes=new TextEncoder().encode(text);let str='';for(let i=0;i<bytes.length;i+=8192)str+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(str);}
  async function previewRows(rows,session=importState) {
    if(!session||session.accountId!==workspace.accountId)throw new Error('账户已切换，请在当前账户重新导入。');
    const records=rows.map(r=>importRecord(r)),reviews=session.kind==='reviews';
    return call('import.preview',{accountId:session.accountId,kind:session.kind,filename:reviews?'复盘确认.json':'成交确认.json',contentBase64:toBase64(JSON.stringify(reviews?{reviews:records}:{rows:records.map(r=>({...r,source:session.tab==='images'?'ocr':r.source||'file-import'})),warnings:session.warnings||[]}))});
  }
  function applyImportPreview(result,session=importState) {
    if(!session||importState!==session||session.accountId!==workspace.accountId)return;
    const old=importState.rows||[];
    importState.preview=result;
    importState.rows=(result.rows||result.preview?.rows||[]).map((r,index)=>{
      const previous=old[index]&&importRecord(old[index]),record={...importRecord(r)};
      if(previous)for(const key of ['importKey','source','sourceDetails','feesConfirmed','feeMissing'])if(previous[key]!==undefined)record[key]=previous[key];
      const selected=old[index]&&typeof old[index].selected==='boolean'?old[index].selected:r.selected!==false;
      return {...r,entry:record,record,values:record,selected:!r.duplicate&&selected};
    });
    importState.warnings=[...new Set([...(importState.warnings||[]),...(result.warnings||[])])];
  }
  async function recognizeImages() {
    collectImportRows();if(!importState.images.length)return;const s=importState,token=s.readToken=(s.readToken||0)+1;s.busy=true;importModal();
    try {const result=await callAI('recognize',{images:s.images,accountId:s.accountId,defaultDate:s.defaultDate||undefined});if(importState!==s||workspace.accountId!==s.accountId||s.readToken!==token)return;s.rows=[];s.preview=null;s.reviewed=false;s.warnings=result.warnings||[];if(s.defaultDate)s.warnings.push(`缺失日期由你补充为 ${s.defaultDate}，请逐行确认。`);const rows=(result.rows||[]).map(r=>({...r,source:'ocr',feeMissing:r.feesConfirmed!==true,feesConfirmed:false}));if(!rows.length)throw new Error('未识别到成交记录。请使用更清晰的截图，或改为手工录入。');const preview=await previewRows(rows,s);if(s.readToken!==token)return;applyImportPreview(preview,s);}
    catch(error){if(s.readToken===token)s.error=errorText(error);}finally{if(s.readToken===token){s.busy=false;if(importState===s&&$('[data-business-images]')){importModal();if(s.error){$('[data-import-error]').textContent=s.error;$('[data-import-error]').hidden=false;s.error='';}}}}
  }
  async function validateImport(commit=false) {
    collectImportRows();const s=importState;if(!s||s.busy)return;s.error='';s.busy=true;
    try{
      const selected=s.rows.filter(r=>r.selected!==false);if(commit&&!selected.length)throw new Error('请至少勾选一条记录。');
      if(commit&&s.kind!=='reviews'&&selected.some(r=>importRecord(r).source==='ocr')&&!s.reviewed)throw new Error('请对照截图核对记录，然后勾选“已人工核对”。');
      if(commit&&s.kind!=='reviews'&&selected.some(r=>importRecord(r).feeMissing))throw new Error('请核对费用明细，并勾选每条记录的费用确认。');
      const preview=await previewRows(s.rows,s);if(importState!==s||workspace.accountId!==s.accountId)return;applyImportPreview(preview,s);
      if(!commit){s.busy=false;if(importVisible(s)){importModal();AT.toast('已重新校验');}return;}
      const valid=s.rows.filter(r=>r.selected!==false&&!r.duplicate);
      if(valid.some(r=>(r.errors||[]).length))throw new Error('选中记录仍有错误，请逐行修正后再导入。');
      if(!valid.length)throw new Error('没有可导入的新记录，可能与已有流水重复。');
      const result=await call('import.commit',{accountId:s.accountId,kind:s.kind,rows:valid.map(r=>({...importRecord(r),reviewed:s.reviewed})),previewId:preview.previewId,source:s.tab==='images'?'ocr':'file'});
      if(importState!==s||workspace.accountId!==s.accountId){AT.toast('导入已保存到原账户');return;}
      applyWorkspace(result);if(!result?.accounts&&!result?.workspace)await refresh();if(importVisible(s))AT.closeBusinessModal();AT.render();importState=null;AT.toast(`已导入 ${valid.length} 条记录`);
    }catch(error){s.error=errorText(error);s.busy=false;if(importVisible(s)){importModal();$('[data-import-error]').textContent=s.error;$('[data-import-error]').hidden=false;}}finally{s.busy=false;}
  }
  function fileBase64(file) {return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});}
  async function readImages(files) {
    const s=importState;if(s.imageReading)throw new Error('正在读取截图，请稍后再添加。');
    const list=[...files];if(list.length+s.images.length>4)throw new Error('最多选择 4 张成交截图。');
    if(list.some(f=>!['image/png','image/jpeg','image/webp'].includes(f.type)))throw new Error('请使用 PNG、JPG 或 WebP 图片。');
    if(list.some(f=>f.size>5*1024*1024))throw new Error('每张截图不能超过 5 MiB。');
    if(list.reduce((sum,f)=>sum+f.size,0)+s.images.reduce((sum,f)=>sum+f.size,0)>12*1024*1024)throw new Error('截图总大小不能超过 12 MiB。');
    s.imageReading=true;s.readToken=(s.readToken||0)+1;s.busy=false;
    try{const images=[];for(const file of list)images.push({name:file.name,mimeType:file.type,size:file.size,dataBase64:await fileBase64(file)});if(importState!==s)return;s.images.push(...images);s.rows=[];s.preview=null;s.reviewed=false;s.warnings=[];if(importVisible(s))importModal();}finally{s.imageReading=false;}
  }
  async function readImportFile(file) {
    if(file.size>10*1024*1024)throw new Error('导入文件不能超过 10 MiB。');
    const s=importState,token=s.readToken=(s.readToken||0)+1;s.file={name:file.name};s.error='';s.rows=[];s.warnings=[];s.preview=null;s.busy=false;s.reviewed=false;importModal();
    try{const contentBase64=await fileBase64(file);if(importState!==s||s.readToken!==token)return;
      const result=await call('import.preview',{accountId:s.accountId,kind:s.kind,filename:file.name,contentBase64});if(importState!==s||workspace.accountId!==s.accountId||s.readToken!==token)return;applyImportPreview(result,s);if(importVisible(s))importModal();
    }catch(error){if(importState===s&&s.readToken===token)throw error;}
  }
  async function aiReview() {
    const f=currentForm();if(!f||f.dataset.aiBusy)return;currentReview=collectReview();f.dataset.aiBusy='true';
    const trigger=$('[data-business="ai-review"]');trigger.disabled=true;trigger.textContent='正在生成草稿…';
    try{const review=structuredClone(currentReview);const labels=Object.fromEntries([...reviewSchema().templates.flatMap(t=>t.sections.flatMap(s=>s.fields)).map(f=>[f.id,f.label]),...reviewSchema().dimensions.flatMap(d=>d.items).map(i=>[i.id,i.label])]);const result=await callAI('review',{review,accountId:workspace.accountId});if(!f.isConnected)return;const draft=result.draft;const text=typeof draft==='object'?[draft.summary,...Object.entries(draft.fields||{}).map(([id,value])=>`${labels[id]||id}\n${value}`),...(draft.missingData||[]).map(item=>'待补充：'+item),...Object.entries(draft.suggestedScores||{}).map(([id,value])=>`评分建议 ${labels[id]||id}：${value.score==null?'未建议':value.score} · ${value.comment||''}`)].filter(Boolean).join('\n\n'):result.text||result.content||draft||'';if(!text)throw new Error('AI 未返回复盘文字。');const wrap=document.createElement('div');wrap.className='business-ai-result';wrap.innerHTML=`<h3>AI 建议草稿</h3>${textfield('检查草稿后再采纳','aiGenerated',text)}${msg('采纳后作为独立附注保存，不覆盖你的复盘文字或自评分。')}${btn('采纳为附注','ai-adopt','check','small')}`;trigger.closest('[data-review-panel]').querySelector('.business-ai-result')?.remove();trigger.closest('[data-review-panel]').append(wrap);AT.toast('草稿已生成，检查后可采纳');}
    catch(error){notice(error,f);}finally{delete f.dataset.aiBusy;if(trigger.isConnected){trigger.disabled=false;trigger.textContent='发送当前材料，生成草稿';}}
  }
  async function restoreFile(file) {
    if(file.size>20*1024*1024)throw new Error('备份文件过大，请使用不超过 20 MiB 的 JSON 文件。');
    let data;try{data=JSON.parse(await file.text());}catch{throw new Error('无法读取 JSON 备份。');}restoreCandidate=data;
    confirmAction('恢复账本备份',`文件：${file.name}。恢复将替换所有账户、交易、复盘、计划与快照。建议先导出当前账本；确认后系统校验整份备份，通过后才写入。`,'backup-restore-confirm');
  }
  function historicalNotes() {
    const labels={A08:'原每日笔记',A09:'原周期笔记',A12:'原灵感闪记'},notes=Object.entries(AT.state.notes||{}).filter(([,value])=>typeof value==='string'&&value.trim());
    modal('历史笔记',`${msg('这些是升级前保留的本机笔记。原文不会自动改成正式复盘；你可以复制，或导入当前账户后确认日期与类型。')}${notes.map(([id,text])=>`<section class="business-details"><header class="business-section-heading">${E(labels[id]||id)}</header><pre class="business-legacy-text">${E(text)}</pre>${btn('整理为当前账户复盘','legacy-import','note','small',`data-id="${E(id)}"`)}</section>`).join('')||empty('没有历史笔记')}`,btn('关闭','close'));
  }
  function importHistoricalNote(id) {
    if(!account())return accountForm();
    currentReview={kind:id==='A12'?'idea':id==='A09'?'weekly':'daily',date:today(),title:'历史笔记整理',fields:{importedText:AT.state.notes[id]||''},scores:{},relatedTradeIds:[]};
    drawReviewForm();AT.toast('请核对日期、类型和标题，保存后加入当前账户；原笔记继续保留');
  }
  async function execute(action,el) {
    const id=el?.dataset.id;
    if(action==='close'){stashReviewDraft();return AT.closeBusinessModal();}
    if(action==='history-page'){historyState(el.dataset.historyKey).page=Number(el.dataset.page);return refreshHistory(el.dataset.historyKey);}
    if(action==='history-clear'){const key=el.dataset.historyKey;historyFilters[key]={query:'',from:'',to:'',page:0,size:25};$$(`[data-history-key="${CSS.escape(key)}"][data-history-filter]`).forEach(input=>input.value='');return refreshHistory(key);}
    if(action==='curve-mode'){curveMode=el.dataset.mode;return AT.render();}
    if(action==='performance-date')return performanceDate(el.dataset.date);
    if(action==='refresh')return refresh();
    if(action==='legacy-notes')return historicalNotes();
    if(action==='legacy-import')return importHistoricalNote(id);
    if(action==='data-info')return modal('数据与保存说明',`<div class="report-text"><h3>账户与复盘</h3><p>正式账户、资金、成交、计划、复盘和估值快照保存到本机 SQLite。截图和表格需要你核对后确认导入，不会直接下单。</p><h3>行情与研究</h3><p>市场、策略、工具和回测目前仍为界面演示，尚未接入真实行情或回测计算。账户持仓仅使用你保存的价格估值。</p><h3>升级前的笔记</h3><p>原笔记仍保存在界面状态中，可从历史笔记查看并整理到当前账户。</p></div>`,btn('历史笔记','legacy-notes','note')+btn('关闭','close'));
    if(action==='accounts')return accountsModal();
    if(action==='account-new')return accountForm();
    if(action==='account-edit')return accountForm(id||workspace.accountId);
    if(action==='account-switch'){await refresh(id);AT.closeBusinessModal();return;}
    if(action==='account-archive'){const active=workspace.accountId,result=await call('account.archive',{accountId:id,id,archived:el.dataset.archived==='true'});if(id!==active)await refresh(active);else applyWorkspace(result);AT.render();return accountsModal();}
    if(action==='ledger-new')return ledgerForm();
    if(action==='cash-new')return ledgerForm(null,'deposit');
    if(action==='opening-position')return ledgerForm(null,'opening_position');
    if(action==='ledger-edit')return ledgerForm(id);
    if(action==='ledger-tab'){AT.ui.ledgerMode=el.dataset.mode;return AT.render();}
    if(action==='ledger-void')return confirmAction('作废流水','作废后保留原记录，并重新核算账户。后续交易如出现资金或持仓不足，操作会被拒绝。','ledger-void-confirm',`data-id="${E(id)}"`);
    if(action==='ledger-void-confirm'){applyWorkspace(await call('ledger.void',{id}));AT.closeBusinessModal();AT.render();return AT.toast('记录已作废');}
    if(action==='save-form')return saveForm();
    if(action==='review-new')return reviewForm(null,AT.ui.reviewNote==='A12'?'idea':AT.ui.reviewNote==='A09'?'weekly':'daily');
    if(action==='review-mode')return reviewMode(el.dataset.mode);
    if(action==='review-facts')return loadReviewFacts();
    if(action==='review-section')return showReviewSection(el.dataset.reviewSection);
    if(action==='score-dimension')return showScoreDimension(el.dataset.scoreDimension);
    if(action==='idea')return reviewForm(null,'idea');
    if(action==='review-edit')return reviewForm(id);
    if(action==='review-discard'){stashReviewDraft();const key=draftKey(collectReview());return confirmAction('丢弃未提交草稿','将移除这份未提交草稿；已经保存的正式复盘不受影响。','review-discard-confirm',`data-key="${E(key)}"`);}
    if(action==='review-discard-confirm'){const f=currentForm();if(f)f.dataset.saved='true';delete AT.state.businessReviewDrafts?.[el.dataset.key];await AT.persist();AT.closeBusinessModal();AT.closeBusinessWorkspace();return AT.toast('已丢弃草稿');}
    if(action==='review-trade')return reviewForm(null,'trade',id);
    if(action==='review-filter-clear'){reviewFilter.date='';return AT.render();}
    if(action==='review-delete')return confirmAction('删除复盘','确认删除这份复盘？交易流水不受影响。','review-delete-confirm',`data-id="${E(id)}"`);
    if(action==='review-delete-confirm'){const key=`${workspace.accountId}|${id}`;applyWorkspace(await call('review.delete',{id}));delete AT.state.businessReviewDrafts?.[key];await AT.persist();AT.closeBusinessModal();return AT.render();}
    if(action==='plan-new')return planForm();
    if(action==='plan-edit')return planForm(id);
    if(action==='plan-delete')return confirmAction('删除交易计划','确认删除这份计划与执行备注？关联的交易流水不受影响。','plan-delete-confirm',`data-id="${E(id)}"`);
    if(action==='plan-delete-confirm'){applyWorkspace(await call('plan.delete',{id}));AT.closeBusinessModal();return AT.render();}
    if(action==='snapshots')return snapshotsModal();
    if(action==='stage-detail')return stageDetail(Number(el.dataset.stage));
    if(action==='snapshot-new')return snapshotForm();
    if(action==='snapshot-edit')return snapshotForm(el.dataset.date);
    if(action==='snapshot-market')return fillSnapshotMarket();
    if(action==='share')return AT.navigate('A11');
    if(action==='export-share')return download(`A-trade-复盘分享-${today()}.txt`,shareText(),'text/plain;charset=utf-8');
    if(action==='export-ledger')return exportLedger();
    if(action==='export-reviews')return download(`A-trade-复盘-${today()}.json`,{accountName:account()?.name,reviews:workspace.reviews,reviewSchema:workspace.reviewSchema});
    if(action==='import')return importModal(!importState);
    if(action==='import-reviews'){importState={accountId:workspace.accountId,kind:'reviews',rows:[],images:[],warnings:[],tab:'file',file:null,preview:null};return importModal();}
    if(action==='import-tab'){collectImportRows();importState.tab=el.dataset.tab;return importModal();}
    if(action==='import-image-remove'){collectImportRows();importState.readToken=(importState.readToken||0)+1;importState.busy=false;importState.images.splice(Number(el.dataset.index),1);importState.rows=[];importState.preview=null;importState.reviewed=false;return importModal();}
    if(action==='import-recognize')return recognizeImages();
    if(action==='import-validate')return validateImport(false);
    if(action==='import-commit')return validateImport(true);
    if(action==='ai-review')return aiReview();
    if(action==='ai-adopt'){const source=$('[name="aiGenerated"]'),target=$('[name="field:aiDraft"]');if(source&&target){target.value=source.value;source.closest('.business-ai-result').remove();stashReviewDraft();AT.toast('已填入附注，保存复盘后写入账本');}return;}
    if(action==='backup-export')return download(`A-trade-账本备份-${today()}.json`,await call('backup.export',{}));
    if(action==='backup-restore-confirm'){const result=await call('backup.restore',{backup:restoreCandidate});applyWorkspace(result);if(!result?.accounts&&!result?.workspace)await refresh();restoreCandidate=null;AT.closeBusinessModal();AT.render();return AT.toast('账本备份已恢复');}
    if(action==='open-folder'){const result=await bridge.openDataFolder();if(!result?.ok)throw new Error('无法打开数据文件夹');}
  }
  const requiresAccount=new Set(['ledger-new','cash-new','opening-position','review-new','idea','plan-new','snapshot-new','snapshots','import','import-reviews','export-ledger','export-reviews','share','export-share']);
  function launch(action,el){stashReviewDraft();if(requiresAccount.has(action)&&!account()){accountForm();return;}Promise.resolve(execute(action,el)).catch(error=>notice(error,currentForm()));}
  const aliases={'account-settings':'accounts','add-trade':'ledger-new','import-records':'import','asset-snapshots':'snapshots','export-ledger':'export-ledger','export-account-summary':'export-share','note':'idea','data-info':'data-info'};
  AT.businessUI={handleAction(action){if(!aliases[action])return false;launch(aliases[action]);return true;},refresh,captureDraft(){stashReviewDraft();collectImportRows();},updateShell(page){const real=page.startsWith('A');const label=$('.demo-label');if(label)label.textContent=real?'本机账户 · 真实记账':page.startsWith('C')?'桌面设置':'行情与研究 · 演示';const status=$('.statusbar>span:first-child');if(status)status.innerHTML=`<i class="status-dot"></i> ${real?'本机账本 · 未连接券商':page.startsWith('C')?'本机数据与显示设置':'行情与研究示例 · 未连接 TDX / MCP'}`;},get workspace(){return workspace;}};
  AT.accountModule={async flush(){stashReviewDraft();clearTimeout(draftTimer);const results=await Promise.allSettled([...pendingWrites]);if(results.some(r=>r.status==='rejected'))return false;return await AT.persist();}};
  document.addEventListener('click',event=>{const el=event.target.closest('[data-business]');if(!el||el.disabled)return;event.preventDefault();event.stopPropagation();launch(el.dataset.business,el);},true);
  document.addEventListener('submit',event=>{if(event.target.id!=='business-form')return;event.preventDefault();event.stopPropagation();saveForm();},true);
  document.addEventListener('input',event=>{
    const el=event.target;
    if(el.matches('[data-history-filter]')){const key=el.dataset.historyKey;historyState(key)[el.dataset.historyFilter]=el.value;historyState(key).page=0;refreshHistory(key);}
    if(el.matches('[data-snapshot-price]')&&snapshotSession){const code=el.dataset.snapshotPrice;snapshotSession.priceDetails[code]={source:'manual',priceDate:snapshotSession.date,requestedDate:snapshotSession.date,adjustment:'none',stale:false};const label=$(`[data-price-source="${CSS.escape(code)}"]`);if(label)label.textContent=el.value?priceSource(snapshotSession.priceDetails[code]):'尚未填写价格';}
    if(el.closest('#business-form')){stashReviewDraft();updateReviewProgress();if(!el.matches('[data-history-filter]'))updateLiveScore();}
  },true);
  document.addEventListener('paste',event=>{if(!$('.business-modal [data-business-images]')||!importState)return;const files=[...(event.clipboardData?.items||[])].filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);if(!files.length)return;event.preventDefault();collectImportRows();readImages(files).catch(error=>{const target=$('[data-import-error]');if(target){target.textContent=errorText(error);target.hidden=false;}else notice(error);});});
  document.addEventListener('change',event=>{
    const el=event.target;
    if(el.matches('[data-business-account]')){event.stopPropagation();refresh(el.value).catch(error=>notice(error));}
    if(el.matches('[data-review-kind-filter]')){reviewFilter.kind=el.value;AT.render();}
    if(el.matches('[data-review-date-filter]')){reviewFilter.date=el.value;AT.render();}
    if(el.matches('[data-snapshot-date]'))loadSnapshotDate(el.value).catch(error=>notice(error,currentForm()));
    if(el.matches('[data-trade-selection]')){const ids=tradeSelections[el.dataset.tradeSelection];if(ids){if(el.checked)ids.add(el.value);else ids.delete(el.value);}}
    if(el.matches('#business-form [name="type"]'))updateLedgerFields();
    if(el.matches('#business-form [name="kind"]')&&currentForm().dataset.method==='review.save'){updateReviewTitle();currentReview=collectReview();drawReviewForm();}
    if(el.closest('#business-form')){stashReviewDraft();updateReviewProgress();updateLiveScore();}
    if(el.matches('#business-form [name="date"],#business-form [name="endDate"]')&&currentForm()?.dataset.method==='review.save'){updateReviewTitle();stashReviewDraft();loadReviewFacts();}
    const run=promise=>Promise.resolve(promise).catch(error=>{const target=$('[data-import-error]');if(target){target.textContent=errorText(error);target.hidden=false;}else notice(error);});
    if(el.matches('[data-business-images]')&&el.files.length){collectImportRows();run(readImages(el.files));}
    if(el.matches('[data-business-import-file]')&&el.files[0])run(readImportFile(el.files[0]));
    if(el.matches('[data-business-restore]')&&el.files[0])run(restoreFile(el.files[0]));
  },true);
  document.addEventListener('DOMContentLoaded',()=>{if(!workspace?.accounts||AT.state.businessAccountId&&AT.state.businessAccountId!==workspace.accountId)refresh(AT.state.businessAccountId).catch(()=>{});},{once:true});
})();
