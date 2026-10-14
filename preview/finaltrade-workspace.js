/* Project navigation and bounded RPC for the strategy editor. */
(() => {
 'use strict';
 const channel='atrade.research.v1',routes={R11:'/screener',R01:'/strategy',R02:'/strategy/events',R12:'/signals',R13:'/trade',R03:'/backtest',R05:'/backtest?section=plateau',R08:'/reports',G02:'/tasks',G03:'/tasks'};
 let ready=false,loading=false,error='',frame=null,childRoute='',desiredRoute='',evidence=null,timer=null,taskSummary={running:0,queued:0},legacySummary={running:0,queued:0};
 const requests=new Set();
 const handles=page=>Object.hasOwn(routes,page);
 const root=()=>document.getElementById('strategy-workspace-root');
 function context(){let dates={};try{const config=JSON.parse(localStorage.getItem('atrade.single-editor.v1')||'null')?.draft?.config||{};if(/^\d{4}-\d{2}-\d{2}$/.test(config.startDate||''))dates.dateFrom=config.startDate;if(/^\d{4}-\d{2}-\d{2}$/.test(config.endDate||''))dates.dateTo=config.endDate;}catch{}return {compact:AT.state.compact===true,largeText:AT.state.largeText===true,reduced:AT.state.reduced===true,fees:AT.state.fees,positionRules:AT.state.positionRules,initialCapital:AT.state.startCapital,accountId:AT.state.businessAccountId||null,researchDates:dates};}
 function post(action,data){frame?.contentWindow?.postMessage({channel,action,data},'atrade://reference');}
 function updateShell(){
  const counts=window.ResearchModule?.taskCounts?.(),local=counts?.loaded?counts:legacySummary;
  for(const [id,text] of [['top-task-count',`运行 ${local.running+taskSummary.running} · 排队 ${local.queued+taskSummary.queued}`],['sidebar-tasks',`${local.running+taskSummary.running} 项运行 · ${local.queued+taskSummary.queued} 项排队`]]){const el=document.getElementById(id);if(el)el.textContent=text;}
  if(!handles(AT.state.page))return;
  const label=document.querySelector('.demo-label'),status=document.querySelector('.statusbar>span:first-child');
  if(label&&label.textContent!=='本地策略研究')label.textContent='本地策略研究';if(status&&status.textContent.trim()!=='共用行情来源 · 结果保存在本机')status.innerHTML='<i class="status-dot"></i> 共用行情来源 · 结果保存在本机';
 }
 function render(){return `<section class="strategy-workspace" data-strategy-workspace><div class="strategy-startup panel"><h2>策略工作区</h2><p role="status">${AT.escape(error||'正在载入策略与行情…')}</p>${error?'<button class="btn" data-strategy-retry>重新载入</button>':''}</div></section>`;}
 function openRoute(route){desiredRoute=route;if(ready&&frame&&childRoute!==route)post('navigate',{route});}
 async function mount(page=AT.state.page){
  const container=root();if(!container)return;
  container.hidden=false;document.getElementById('main').hidden=true;const skip=document.querySelector('.skip-link');if(skip)skip.href='#strategy-workspace-root';updateShell();
  const requested=routes[page]||'/screener';if(!desiredRoute||!childRoute||!routeBelongsToPage(childRoute,page))desiredRoute=requested;
  if(ready){openRoute(desiredRoute);post('context',context());return;}
  if(loading||error)return;
  if(!window.atradeDesktop?.startResearch){error='请在桌面应用中使用策略工作区。';container.innerHTML=render();return;}
  loading=true;container.innerHTML=render();
  try{
   const result=await atradeDesktop.startResearch();if(!result.ok)throw Error(result.error);
   ready=true;error='';container.innerHTML='';frame=document.createElement('iframe');frame.title='策略研究工作区';frame.dataset.strategyFrame='';frame.src='atrade://reference'+desiredRoute;frame.addEventListener('load',()=>{post('context',context());openRoute(desiredRoute);});container.append(frame);
   if(!handles(AT.state.page))container.hidden=true;
   clearInterval(timer);timer=setInterval(updateCounts,2500);void updateCounts();
  }catch(failure){error=failure.message;container.innerHTML=render();}finally{loading=false;}
 }
 function routePage(route){const path=String(route).split('?')[0];return path==='/screener'?'R11':path==='/strategy'?'R01':path==='/strategy/events'?'R02':path==='/signals'?'R12':path==='/trade'?'R13':path==='/reports'||path==='/signals/backtest'?'R08':path==='/tasks'?'G02':path==='/backtest'?(String(route).includes('section=plateau')?'R05':'R03'):null;}
 function routeBelongsToPage(route,page){return routePage(route)===page||page==='G03'&&routePage(route)==='G02';}
 function hide(){const container=root();if(container)container.hidden=true;document.getElementById('main').hidden=false;const skip=document.querySelector('.skip-link');if(skip)skip.href='#main';}
 async function updateCounts(){if(!ready)return;try{const [data,legacy]=await Promise.all([atradeDesktop.researchRequest({path:'/api/atrade/tasks'}),atradeDesktop.business('research.list')]);const rows=data.data?.items||[],old=legacy.data?.tasks||[];taskSummary={running:rows.filter(row=>row.status==='running').length,queued:rows.filter(row=>row.status==='pending').length};legacySummary={running:old.filter(row=>['running','pausing','stopping'].includes(row.status)).length,queued:old.filter(row=>row.status==='queued').length};updateShell();}catch{} }
 function reply(event,data){event.source.postMessage({channel,id:event.data.id,...data},'atrade://reference');}
 async function dispatch(action,payload={}){
  if(action==='context')return context();
  if(action==='business'){
   if(!['workspace','research.list','research.get','research.control','research.export','research.rename','research.delete'].includes(payload.method))throw Error('不支持此工作区操作。');
   const result=await atradeDesktop.business(payload.method,payload.payload||{});if(!result.ok)throw Error(result.error);return result.data;
  }
  if(action==='navigate'){
   if(!['C01','C02','C03','C07','A04','R04','R15','M04','G01'].includes(payload.page))throw Error('页面不存在。');
   if(payload.page==='R04'&&payload.taskId){await ResearchModule.openTask(payload.taskId,'R04');return true;}
   if(payload.page==='G01'){if(!/^(sh|sz|bj)\d{6}$/.test(payload.symbol||''))throw Error('证券代码无效。');AT.navigate('G01',payload.symbol);return true;}
   if(payload.page==='M04'&&/^(sh|sz|bj)\d{6}$/.test(payload.symbol||''))AT.state.stock=payload.symbol;
   AT.navigate(payload.page);return true;
  }
  if(action==='plan-draft'){
   if(!/^(sh|sz|bj)\d{6}$/.test(payload.symbol||'')||!/^\d{4}-\d{2}-\d{2}$/.test(payload.signal_date||''))throw Error('计划的证券或日期无效。');
   const quantity=payload.sizing_mode==='lots'?Number(payload.sizing_value)*100:0;
   await AT.accountModule.openPlanDraft({researchDraftId:String(payload.id||'').slice(0,200),code:payload.symbol,name:String(payload.name||'').slice(0,100),date:payload.signal_date,action:'buy',status:'draft',quantity:Number.isFinite(quantity)&&quantity>0?quantity:undefined,trigger:payload.reference_price?`信号参考价 ${payload.reference_price} 元，请填写实际买入条件。`:'请填写实际买入条件。',reason:`${payload.source==='screener'?'选股漏斗':'策略信号'} · ${payload.signal_date}`});return true;
  }
  if(action==='ai-open'){AT.pageAI?.open(typeof payload.question==='string'?payload.question.slice(0,4000):'');return true;}
  if(action==='market'){
   if(!['market.intraday','market.watchlist.save','market.watchlist.list'].includes(payload.method))throw Error('不支持此行情操作。');
   let result=await atradeDesktop.market(payload.method,payload.payload||{});
   if(payload.method==='market.watchlist.save'&&!result.ok&&result.error?.includes('尚未收录本地行情')){const scan=await atradeDesktop.market('market.scan');if(!scan.ok)throw Error(scan.error);await AT.marketUI?.refresh();result=await atradeDesktop.market(payload.method,payload.payload||{});}
   if(!result.ok)throw Error(result.error);return result.data;
  }
  if(action==='export-report'){const result=await atradeDesktop.exportResearchReport(payload.id);if(!result.ok)throw Error(result.error);return result.data;}
  throw Error('不支持此操作。');
 }
 window.addEventListener('message',event=>{
  if(event.origin!=='atrade://reference'||event.source!==frame?.contentWindow||event.data?.channel!==channel)return;
  const {action,payload={},id}=event.data;
  if(action==='route'){
   const stock=/^\/stocks\/((?:sh|sz|bj)\d{6})\/chart(?:\?(.*))?$/.exec(String(payload.route||''));
   if(stock){
    if(!handles(AT.state.page))return;
    const origin=routeBelongsToPage(childRoute,AT.state.page)?childRoute:routes[AT.state.page],context={query:stock[2]||'',sourcePage:AT.state.page,sourceRoute:origin};
    desiredRoute=origin;post('navigate',{route:origin});
    if(AT.stockResearch)AT.stockResearch.open(stock[1],context);else AT.navigate('G01',stock[1]);
    return;
   }
   childRoute=payload.route;desiredRoute=childRoute;
   if(!handles(AT.state.page))return;
   const page=routePage(childRoute);
   if(page&&AT.state.page!==page){AT.state.page=page;AT.ui.navGroup=page[0]==='G'?'R':page[0];history.replaceState(null,'','#'+page);void AT.persist();AT.render();}return;
  }
  if(action==='evidence'){evidence=AT.pageAIContext?.sanitize(payload);AT.pageAI?.refresh();return;}
  if(action==='changed'){void updateCounts();return;}
  if(!id){if(['navigate','ai-open'].includes(action))void dispatch(action,payload).catch(failure=>AT.toast(failure.message));return;}
  if(typeof id!=='string'||id.length>64||requests.has(id))return;
  requests.add(id);Promise.resolve(dispatch(action,payload)).then(data=>reply(event,{ok:true,data}),failure=>reply(event,{ok:false,error:failure.message})).finally(()=>requests.delete(id));
 });
 document.addEventListener('click',event=>{if(event.target.closest?.('[data-strategy-retry]')){error='';void mount();}});
 function aiContext(){if(!handles(AT.state.page)||!evidence)return null;const chart=/^\/stocks\/((?:sh|sz|bj)\d{6})\/chart/.exec(childRoute),{desktopVisible,...details}=evidence,serialized=JSON.stringify(details),dates=[...new Set([...serialized.matchAll(/\b\d{4}-\d{2}-\d{2}\b/g)].map(match=>match[0]))].slice(0,20);return AT.pageAIContext.limit(AT.pageAIContext.sanitize({version:1,page:{id:AT.state.page,title:chart?evidence.title||'个股图表':AT.pages[AT.state.page]?.title,scope:chart?'stock:'+chart[1]:AT.state.page},capturedAt:new Date().toISOString(),dataDates:dates,filters:{...evidence.filters,...(evidence.as_of_date?{asOf:evidence.as_of_date}:{})},summary:serialized.slice(0,20000),tables:desktopVisible?.tables||[],evidence:details,limitations:['仅包含当前策略工作区的参数与当前可见表格分页，不包含未显示的其他分页、账户凭据或私密备注。',...(!dates.length?['当前工作区材料未给出可核对的数据日期，不能把保存时间或页面打开时间当作行情日期。']:[])]}));}
 window.StrategyWorkspace={handles,render,mount,hide,updateShell,context,aiContext,openRoute,planSaved(id){post('plan-saved',{id});},counts:()=>({...taskSummary}),async flush(){return true;}};
 window.FinaltradeWorkspace=window.StrategyWorkspace;
})();
