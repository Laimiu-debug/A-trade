(() => {
  const $ = (s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)], E=AT.escape, I=AT.icon, B=AT.button;
  const main=$('#main'),workspaceRoot=$('#workspace-root'); let toastTimer,modalReturnFocus,resizeTimer,noteTimer,renderFrame,workspaceSession;
  const chartStateKeys=['activeTask','researchParams','strategyIds','stockPool','researchMode','heat','plateauRatio','plateauStrategies','resultTab','chartZoom','pathContext'];
  const scrollRegions=['#navigation','.local-tabs','.context-nav','.pass-viewport','.workspace-tabs','.table-wrap','.research-config','.modal','.modal-body'];
  const compactNavigation=()=>typeof AT.state.sidebarCollapsed==='boolean'?AT.state.sidebarCollapsed:matchMedia('(max-width:1080px)').matches;
  const navigationPage=page=>page==='G01'?(AT.ui.stockOrigin||'M04'):page;
  const navigationGroup=page=>navigationPage(page)[0]==='G'?'R':navigationPage(page)[0];
  const navEntryActive=(entry,page)=>entry.id===page||entry.pages?.includes(page);
  function navItems(group,page){return AT.navPages(group).map(p=>`<button type="button" class="nav-item ${navEntryActive(p,page)?'active':''}" data-nav="${p.id}" title="${p.title}" aria-label="${p.title}" ${navEntryActive(p,page)?'aria-current="page"':''}>${I(p.icon)}<span>${p.title}</span></button>`).join('')}
  function positionSubnav(){
    const popup=$('.nav-flyout'),trigger=$(`[data-nav-group="${AT.ui.navGroup}"]`);if(!popup||!trigger)return;
    const top=Math.max(76,Math.min(trigger.getBoundingClientRect().top,window.innerHeight-popup.offsetHeight-40));popup.style.top=top+'px';
  }
  function closeSubnav(focus=false){const id=AT.ui.navGroup;if(!compactNavigation()||!id)return;AT.ui.navGroup=null;updateShell();if(focus)$(`[data-nav-group="${id}"]`)?.focus({preventScroll:true})}
  function capturePosition(){return {x:window.scrollX,y:window.scrollY,regions:scrollRegions.flatMap(selector=>$$(selector).map((el,index)=>({selector,index,x:el.scrollLeft,y:el.scrollTop})))}}
  function restorePosition(position){
    for(const item of position.regions||[]){const el=$$(item.selector)[item.index];if(el){el.scrollLeft=item.x;el.scrollTop=item.y}}
    window.scrollTo({left:position.x||0,top:position.y||0,behavior:'instant'});
  }
  function captureFocus(){
    const el=document.activeElement,scope=el?.closest('#main,#workspace-root,#navigation,#overlay-root');if(!scope)return null;
    const attrs=[...el.attributes].filter(a=>a.name==='id'||a.name==='name'||a.name==='aria-label'||a.name.startsWith('data-'));
    if(!attrs.length)return null;
    const selector=el.localName+attrs.map(a=>`[${a.name}="${CSS.escape(a.value)}"]`).join('');
    return {original:el,scope:'#'+scope.id,selector,index:$$(selector,scope).indexOf(el),start:el.selectionStart,end:el.selectionEnd};
  }
  function restoreFocus(saved){
    if(saved?.original.isConnected)return;
    const scope=saved&&$(saved.scope),el=scope&&$$(saved.selector,scope)[saved.index];
    if(!el||el.disabled||!el.getClientRects().length)return;
    el.focus({preventScroll:true});if(Number.isInteger(saved.start)&&el.setSelectionRange)el.setSelectionRange(saved.start,saved.end);
  }
  function preservePosition(update){const position=capturePosition(),focus=captureFocus();update();restoreFocus(focus);restorePosition(position)}
  AT.ui.taskSelection=new Set(); AT.ui.researchParams={take:15,stop:6,reduce:50};
  function toast(text,undo=false){const el=$('#toast');el.innerHTML=`<span>${E(text)}</span>${undo?'<button data-action="undo-delete">撤销删除</button>':''}`;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{el.classList.remove('show');el.replaceChildren()},undo?10000:3500)}
  AT.toast=toast;
  function applyPrefs(){document.body.classList.toggle('reduce-motion',AT.state.reduced);document.body.classList.toggle('dense',AT.state.compact);document.body.classList.toggle('reading-large',AT.state.largeText);AT.effects.apply()}
  function updateShell(){
    const page=AT.pages[AT.state.page]||AT.pages.A02,navPage=AT.pages[navigationPage(page.id)],group=AT.groups.find(g=>g.id===navPage.group),running=AT.state.tasks.filter(t=>t.status==='running').length,queued=AT.state.tasks.filter(t=>t.status==='queued').length;
    const parent=AT.pageParentFor(navPage.id),compact=compactNavigation();
    document.body.classList.toggle('nav-collapsed',compact);
    const toggle=$('[data-action="toggle-sidebar"]'),toggleLabel=compact?'展开导航栏':'收起导航栏';
    toggle.setAttribute('aria-expanded',String(!compact));toggle.setAttribute('aria-label',toggleLabel);toggle.title=toggleLabel;
    $('.sidebar-toggle-glyph',toggle).innerHTML=I(compact?'expandNav':'collapseNav');
    $('#breadcrumb').innerHTML=`${group.label}<b>/</b><span>${parent?.title||page.title}</span>${parent&&parent.id!==page.id?`<b>/</b><span>${page.title}</span>`:''}`;
    $('#sidebar-tasks').textContent=`${running} 项运行 · ${queued} 项排队`;
    $('#top-task-count').textContent=`运行 ${running} · 排队 ${queued}`;
    if(AT.ui.navGroup===undefined)AT.ui.navGroup=compact?null:navigationGroup(page.id);
    const nav=$('#navigation'),scroll=nav.scrollTop;
    nav.innerHTML=AT.groups.filter(g=>g.id!=='G').map(g=>`<div class="nav-group"><button class="nav-main ${navPage.group===g.id?'active':''}" data-nav-group="${g.id}" title="${g.label}" aria-label="${g.label}" aria-expanded="${AT.ui.navGroup===g.id}" aria-controls="nav-${g.id}">${I(g.icon)}<span>${g.label}</span>${I('chevronDown')}</button>${compact?'':`<div class="nav-children" id="nav-${g.id}" ${AT.ui.navGroup!==g.id?'hidden':''}>${navItems(g,navPage.id)}</div>`}</div>`).join('');
    const expanded=AT.groups.find(g=>g.id===AT.ui.navGroup);
    $('#nav-flyout-root').innerHTML=compact&&expanded?`<nav class="nav-flyout" id="nav-${expanded.id}" aria-label="${expanded.label}子页面"><header><strong>${expanded.label}</strong><button class="icon-button" data-action="close-subnav" aria-label="收起子页面">${I('close')}</button></header><div class="nav-flyout-items">${navItems(expanded,navPage.id)}</div></nav>`:'';
    nav.scrollTop=scroll;
    positionSubnav();
    document.title=`${page.title} · A-trade ${AT.isDesktop?(window.atradeDesktop?.bootstrap?.app?.packaged?'便携版':'桌面开发版'):'HTML 预览'}`;
    AT.businessUI?.updateShell(page.id);
    AT.marketUI?.updateShell(page.id);
    AT.marketNews?.updateShell(page.id);
    AT.analysisTools?.updateShell(page.id);
    window.ResearchModule?.updateShell();
  }
  function snapshot(){return {page:AT.state.page,stock:AT.state.stock,scroll:window.scrollY,viewport:capturePosition(),chartState:structuredClone(Object.fromEntries(chartStateKeys.map(key=>[key,AT.ui[key]]))),ledgerMode:AT.ui.ledgerMode,reviewNote:AT.ui.reviewNote,query:AT.ui.query,board:AT.ui.board,range:AT.ui.range,metric:AT.ui.metric,selected:[...AT.ui.selected],dayDetail:$('.day-drawer')?{payload:structuredClone(AT.ui.dayDetail),scroll:$('.day-drawer .modal-body').scrollTop}:null,milestone:$('#milestone-detail')?{stage:AT.ui.milestoneNumber,filter:AT.ui.milestoneFilter,scroll:$('.milestone-drawer .modal-body').scrollTop}:null,pass:$('.pass-viewport')?.scrollLeft??AT.ui.passPosition}}
  function resolveEntry(page){
    const entry=AT.accountEntries[page];if(!entry)return page;
    if(entry.ledger)AT.ui.ledgerMode=entry.ledger;
    if(entry.note)AT.ui.reviewNote=entry.note;
    AT.ui.pendingEntry=entry;return entry.page;
  }
  function scrollToSection(id){const el=document.getElementById(id);if(el)window.scrollTo({top:el.getBoundingClientRect().top+window.scrollY-82,behavior:'instant'})}
  function savePosition(){AT.ui.positions[AT.state.page]=snapshot();const pass=$('.pass-viewport');if(pass)AT.ui.passPosition=pass.scrollLeft}
  function render(position=null){
    // In-page actions use the live position, never the last navigation snapshot.
    const viewport=position||capturePosition(),focus=position?null:captureFocus();
    cancelAnimationFrame(renderFrame);main.style.minHeight=main.getBoundingClientRect().height+'px';
    applyPrefs();updateShell();main.innerHTML=window.ResearchModule?.handles(AT.state.page)?window.ResearchModule.render(AT.state.page):AT.view(AT.state.page);AT.hydrateIcons();AT.effects.mount();
    // A second data response can cancel this frame; keep focus available to its render now.
    restoreFocus(focus);
    renderFrame=requestAnimationFrame(()=>{
      const mountedFocus=captureFocus();
      AT.drawCharts();AT.drawCandles();wirePass();initView();main.style.minHeight='';
      restoreFocus(mountedFocus);restorePosition(viewport);
      const entry=AT.ui.pendingEntry;AT.ui.pendingEntry=null;
      if(entry?.section)scrollToSection(entry.section);
      if(entry?.dialog&&!AT.businessUI?.handleAction(entry.dialog))actions[entry.dialog]?.();
    });
  }
  AT.render=render;
  function go(page,stock,backState=null,fromHistory=false){
    if(!AT.pages[page])return;
    cancelSearch();
    closeModal();closeBusinessWorkspace();
    const entryPage=page;page=AT.accountEntries[page]?.page||page;
    const samePage=page===AT.state.page&&(!stock||stock===AT.state.stock),position=samePage?capturePosition():null;
    if(!fromHistory&&!backState&&(page!==AT.state.page||stock&&stock!==AT.state.stock)){savePosition();AT.ui.history.push(snapshot())}
    resolveEntry(entryPage);
    if(backState?.chartState)Object.assign(AT.ui,structuredClone(backState.chartState));
    if(backState){Object.assign(AT.ui,{ledgerMode:backState.ledgerMode??AT.ui.ledgerMode,reviewNote:backState.reviewNote??AT.ui.reviewNote,query:backState.query,board:backState.board,range:backState.range,metric:backState.metric,selected:new Set(backState.selected||[]),passPosition:backState.pass});AT.ui.positions[page]=backState}else if(page!==AT.state.page){AT.ui.query='';AT.ui.board='全部';AT.ui.selected.clear()}
    if(page==='G01'&&AT.state.page!=='G01')AT.ui.stockOrigin=AT.state.page[0]==='G'?'M04':AT.state.page;
    AT.state.page=page;AT.ui.navGroup=compactNavigation()?null:navigationGroup(page);if(stock)AT.state.stock=stock;AT.persist();
    if(!fromHistory){try{history[backState?'replaceState':'pushState']({},'',`#${page}${page==='G01'?'?stock='+AT.state.stock:''}`)}catch{}}
    render(backState?(backState.viewport||{y:backState.scroll}):position||{x:0,y:0});if(backState?.milestone)requestAnimationFrame(()=>{AT.ui.milestoneFilter=backState.milestone.filter;milestoneDetail(backState.milestone.stage);$('.milestone-drawer .modal-body').scrollTop=backState.milestone.scroll});
    if(backState?.dayDetail)requestAnimationFrame(()=>{AT.openDayDetail(backState.dayDetail.payload);$('.day-drawer .modal-body').scrollTop=backState.dayDetail.scroll});
  }
  function back(){if(workspaceSession){closeModal();closeBusinessWorkspace();return}const prev=AT.ui.history.pop();if(prev)go(prev.page,prev.stock,prev);else go('M04')}
  function readRoute(){const [raw,params]=(location.hash.slice(1)||(AT.isDesktop&&AT.pages[AT.state.page]?AT.state.page:'A02')).split('?'),stock=new URLSearchParams(params||'').get('stock')?.toLowerCase();if(AT.pages[raw]){const page=resolveEntry(raw);if(page!==AT.state.page)AT.ui.navGroup=compactNavigation()?null:navigationGroup(page);AT.state.page=page;if(page!==raw)history.replaceState({},'',`#${page}`);if(AT.stocks.some(s=>s.code===stock)||AT.isDesktop&&/^(?:(?:sh|sz|bj))?\d{6}$/.test(stock||''))AT.state.stock=stock}}
  history.scrollRestoration='manual';
  function restoreRoute(){
    const [page,query]=(location.hash.slice(1)||'A02').split('?'),stock=new URLSearchParams(query||'').get('stock');
    if(!AT.pages[page]||(page===AT.state.page&&(!stock||stock===AT.state.stock)))return;
    closeModal();closeBusinessWorkspace();
    savePosition();readRoute();
    const saved=AT.accountEntries[page]?null:AT.ui.positions[AT.state.page];
    go(AT.state.page,AT.state.stock,saved||{page:AT.state.page,query:'',board:'全部',range:AT.ui.range,metric:AT.ui.metric,scroll:0,selected:[]},true);
  }
  window.addEventListener('popstate',restoreRoute);
  window.addEventListener('hashchange',restoreRoute);
  function openBusinessWorkspace(title,body,{wide=false,className='',footer=''}={}){
    const key=title+'|'+className,same=workspaceSession?.key===key;
    const position=same?capturePosition():{x:0,y:0},focus=same?captureFocus():null;
    if($('.overlay'))closeOverlay();
    if(!workspaceSession)workspaceSession={position:capturePosition(),focus:captureFocus()};
    workspaceSession.key=key;
    workspaceRoot.innerHTML=`<section class="page-workspace ${wide?'wide':''} ${E(className)}"><header class="workspace-head"><button type="button" class="workspace-back" data-action="close-workspace" aria-label="返回${E(AT.pages[AT.state.page]?.title||'上一页')}" title="返回${E(AT.pages[AT.state.page]?.title||'上一页')}">${I('back')}</button><h1 id="workspace-title">${E(title)}</h1></header><div class="workspace-content modal-body">${body}</div>${footer?`<footer class="workspace-footer modal-footer">${footer}</footer>`:''}</section>`;
    main.hidden=true;workspaceRoot.hidden=false;document.body.classList.add('workspace-open');
    $('.skip-link').href='#workspace-root';AT.hydrateIcons();
    restorePosition(position);
    if(same)restoreFocus(focus);
    else workspaceRoot.focus({preventScroll:true});
  }
  function closeBusinessWorkspace(){
    if(!workspaceSession)return;
    cancelSearch(false);
    AT.businessUI?.captureDraft();
    const saved=workspaceSession;workspaceSession=null;
    workspaceRoot.replaceChildren();workspaceRoot.hidden=true;main.hidden=false;
    document.body.classList.remove('workspace-open');$('.skip-link').href='#main';
    restorePosition(saved.position);restoreFocus(saved.focus);
    if(saved.focus?.original.isConnected)saved.focus.original.focus({preventScroll:true});
    requestAnimationFrame(()=>{AT.drawCharts();AT.drawCandles();});
  }
  function openModal(title,body,{wide=false,drawer=false,confirmation=false,className='',footer=''}={}){
    if(!drawer&&!confirmation){AT.businessUI?.captureDraft();openBusinessWorkspace(title,body,{wide,className,footer});return}
    if(!$('.overlay'))modalReturnFocus=document.activeElement;$('#overlay-root').innerHTML=`<div class="overlay"><section class="modal ${wide?'wide':''} ${drawer?'drawer':''} ${E(className)}" role="dialog" aria-modal="true" aria-labelledby="modal-title"><header class="modal-head"><h2 id="modal-title">${E(title)}</h2><button class="icon-button" data-action="close-modal" aria-label="关闭弹窗">${I('close')}</button></header><div class="modal-body">${body}</div>${footer?`<footer class="modal-footer">${footer}</footer>`:''}</section></div>`;
    document.body.style.overflow='hidden';requestAnimationFrame(()=>{($$('.modal input:not([type=checkbox]):not([readonly]),.modal textarea,.modal select').find(el=>!el.disabled&&el.getClientRects().length)||$('.modal button'))?.focus({preventScroll:true})});
  }
  function closeOverlay(){const had=!!$('.overlay');$('#overlay-root').innerHTML='';document.body.style.overflow='';if(had&&modalReturnFocus?.isConnected)modalReturnFocus.focus({preventScroll:true})}
  function closeModal(){if($('.overlay'))closeOverlay();else closeBusinessWorkspace()}
  AT.openBusinessWorkspace=openBusinessWorkspace;
  AT.closeBusinessWorkspace=closeBusinessWorkspace;
  AT.openBusinessModal=(title,body,options={})=>openModal(title,body,{...options,confirmation:true});
  AT.closeBusinessModal=closeModal;
  AT.navigate=go;
  AT.openDayDetail=payload=>{
    if(!payload.points[payload.index])return;
    const updating=!!$('.day-drawer'),focus=updating?captureFocus():null;
    AT.ui.dayDetail=structuredClone(payload);
    const {context,points,index}=AT.ui.dayDetail,date=points[index].date;
    AT.selectChartDate(context,date);
    if(!updating){
      openModal(AT.dayDetailTitle(context),`<div class="day-date-controls"><button type="button" data-day-step="-1">${I('back')}前一日</button><input type="date" data-day-date aria-label="查看日期"><button type="button" data-day-step="1">后一日${I('forward')}</button></div><div id="day-detail-content" aria-live="polite"></div>`,{drawer:true,className:'day-drawer',footer:B('返回图表','back','data-action="close-modal"')});
    }
    $('#modal-title').textContent=AT.dayDetailTitle(context);
    const input=$('[data-day-date]');input.min=points[0].date;input.max=points.at(-1).date;input.value=date;
    $('[data-day-step="-1"]').disabled=index===0;$('[data-day-step="1"]').disabled=index===points.length-1;
    $('#day-detail-content').innerHTML=AT.dayDetailContent(AT.ui.dayDetail);
    if(updating){restoreFocus(focus);if(document.activeElement.disabled)input.focus({preventScroll:true});$('.day-drawer .modal-body').scrollTop=0}
  };
  function showInfo(){openModal(AT.isDesktop?'桌面开发版与数据说明':'数据与预览说明',`<div class="report-text"><h3>当前可体验</h3><p>页面导航、滚轮通行证、曲线悬停、股票详情与返回、股票筛选、多任务控制、笔记保存和参数预览。</p><h3>数据来源</h3><p>全部行情、账户与回测结果为合成示例。预览没有连接 TDX、MCP、财务数据或券商，不执行真实回测或交易。</p><h3>本地状态</h3><p>${AT.isDesktop?'目标配置、演示任务、跟踪池与笔记自动保存到本机数据文件；每次有效变更保留上一有效版本备份。桌面启动后演示任务保持暂停，可手动继续。浏览器预览与桌面数据分别保存。':'目标配置、演示任务、跟踪池与笔记保存在当前浏览器。关闭页面后任务计时器停止；重新打开从已保存的进度继续。'}</p></div>`,{footer:B('知道了','check','data-action="close-modal"','primary')})}
  function wirePass(){
    const el=$('.pass-viewport');if(!el)return;
    if(AT.ui.passPosition!==null)el.scrollLeft=AT.ui.passPosition;
    else {const active=$('.milestone.current',el);if(active)el.scrollLeft=Math.max(0,active.offsetLeft-el.clientWidth*.38)}
    // Keep wheel gestures inside the entire milestone area, including both ends.
    el.closest('.pass-panel').addEventListener('wheel',e=>{if(e.ctrlKey)return;e.preventDefault();e.stopPropagation();const unit=e.deltaMode===1?16:e.deltaMode===2?el.clientWidth:1;const delta=(Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:-e.deltaY)*unit;el.scrollLeft=Math.max(0,Math.min(el.scrollWidth-el.clientWidth,el.scrollLeft+delta));AT.ui.passPosition=el.scrollLeft},{passive:false});
    let dragging=false,startX=0,startScroll=0,moved=false;
    el.addEventListener('pointerdown',e=>{if(e.pointerType==='touch')return;dragging=true;startX=e.clientX;startScroll=el.scrollLeft;moved=false});
    el.addEventListener('pointermove',e=>{if(!dragging)return;if(Math.abs(e.clientX-startX)>4){moved=true;el.classList.add('dragging');el.scrollLeft=startScroll-(e.clientX-startX)}});
    const end=()=>{dragging=false;el.classList.remove('dragging');AT.ui.passPosition=el.scrollLeft};el.addEventListener('pointerup',end);el.addEventListener('pointerleave',end);
    el.addEventListener('click',e=>{if(moved){e.preventDefault();e.stopPropagation();moved=false}},true);
    el.addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();el.scrollLeft+=e.key==='ArrowRight'?156:-156;AT.ui.passPosition=el.scrollLeft}if(e.key==='Home'){e.preventDefault();el.scrollLeft=0}if(e.key==='End'){e.preventDefault();el.scrollLeft=el.scrollWidth}});
  }
  function initView(){
    AT.settingsPage?.mount();
    if(window.ResearchModule?.handles(AT.state.page)){window.ResearchModule.mount(main);return;}
    AT.drawSparks();
    if($('[data-plateau-ratio]'))$('[data-plateau-ratio]').value=AT.ui.plateauRatio||50;
    if($('[data-plateau-strategies]'))$('[data-plateau-strategies]').value=(AT.ui.plateauStrategies||['trend','volume']).join(',');
    const box=$('[data-select-stocks]');if(box){const type=$('[data-list-type]').dataset.listType,visible=AT.filteredStocks(type),count=visible.filter(s=>AT.ui.selected.has(s.code)).length;box.indeterminate=count>0&&count<visible.length}
    const all=$('[data-all-strategies]');if(all)all.indeterminate=AT.ui.strategyIds.length>0&&AT.ui.strategyIds.length<3;
    if($('#valuation-form'))updateValuation();if(AT.state.page==='G06')renderSearch('',true);
    $$('[data-task-check]').forEach(el=>{el.checked=AT.ui.taskSelection.has(el.dataset.taskCheck)});
    const allTasks=$('[data-all-tasks]');if(allTasks){const checks=$$('[data-task-check]'),count=checks.filter(c=>c.checked).length;allTasks.checked=checks.length>0&&count===checks.length;allTasks.indeterminate=count>0&&count<checks.length}
    if(AT.state.page==='R03'){$('[name="take-profit"]').value=AT.ui.researchParams.take;$('[name="stop-loss"]').value=AT.ui.researchParams.stop;$('[name="reduce-ratio"]').value=AT.ui.researchParams.reduce}
    const run=$('[data-action="run-research"]');if(run)run.disabled=!AT.ui.strategyIds.length||(AT.ui.strategyIds.includes('trend')&&AT.ui.strategyIds.includes('revert'));
  }
  function rerenderList(){const old=$('.stock-list');if(!old)return;preservePosition(()=>{old.outerHTML=AT.stockTable(old.dataset.listType);initView()})}
  function accountSettings(){openModal('账户数据与通行证目标',`<form id="account-form"><p class="field-hint">以下参数控制账户总览中的演示通行证。目标与阶段数量均可设置。</p><label class="field"><span>账户名称</span><input value="${E(AT.state.account)}" readonly></label><div class="field-row"><label class="field"><span>每阶段目标收益率（%）</span><input name="goal" type="number" min="0.1" max="500" step="0.1" value="${AT.state.goal}" required></label><label class="field"><span>阶段总数</span><input name="stages" type="number" min="1" max="200" step="1" value="${AT.state.stages}" required></label></div><label class="field"><span>期初资产（元，预览计算基准）</span><input name="capital" type="number" min="1" max="1000000000000" value="${AT.state.startCapital}" required></label><p class="field-hint">预览暂按逐阶段复利目标展示进度。正式业务的入出金调整、回撤和达标规则尚待确认。阶段上限 200 为预览限制。</p></form>`,{footer:B('取消','','data-action="close-modal"')+B('保存账户数据','save','data-action="save-account"','primary')})}
  function milestoneDetail(n){
    const drawer=$('.milestone-drawer'),previous=$('#milestone-detail')?.dataset.stage;
    AT.ui.milestoneNumber=Math.max(1,Math.min(AT.state.stages,Number(n)||1));
    AT.ui.milestoneFilter=AT.ui.milestoneFilter||'all';
    const stage=AT.ui.milestoneNumber;
    const title=`里程碑 ${String(stage).padStart(2,'0')} · 操作与事件`,body=AT.milestoneContent(stage,AT.ui.milestoneFilter),footer=`<div class="chip-row">${B('上一阶段','back',`data-milestone="${stage-1}" ${stage===1?'disabled':''}`,'small')}${B('下一阶段','chevronRight',`data-milestone="${stage+1}" ${stage===AT.state.stages?'disabled':''}`,'small')}</div>${B('账户数据','settings','data-action="account-settings"','small')}`;
    if(!drawer){openModal(title,body,{drawer:true,className:'milestone-drawer',footer});return}
    // Keep the overlay, scroll container and focused filter alive while records change.
    const content=$('.modal-body',drawer),scroll=content.scrollTop;
    if(previous===String(stage)){
      const template=document.createElement('template');template.innerHTML=body;
      $('.milestone-date-log',drawer).replaceChildren(...$('.milestone-date-log',template.content).childNodes);
      $$('[data-milestone-filter]',drawer).forEach(button=>{const next=$(`[data-milestone-filter="${button.dataset.milestoneFilter}"]`,template.content);button.setAttribute('aria-pressed',next.getAttribute('aria-pressed'));$('span',button).textContent=$('span',next).textContent});
      content.scrollTop=scroll;
    }else{
      const nextFocused=document.activeElement?.textContent?.includes('下一阶段');
      $('#modal-title').textContent=title;content.innerHTML=body;$('.modal-footer',drawer).innerHTML=footer;content.scrollTop=0;
      ($(`[data-milestone="${nextFocused?stage+1:stage-1}"]:not(:disabled)`,drawer)||$('.modal-head button',drawer)).focus({preventScroll:true});
    }
  }

  function picker(){AT.ui.pickerBoard='全部';AT.ui.pickerQuery='';AT.ui.pickerSelection=new Set(AT.ui.stockPool);openModal('选择研究股票',`<div class="filter-row" style="padding:0 0 14px"><input class="search-input" data-picker-query placeholder="股票名称或六位代码" aria-label="搜索研究股票"><select class="select" data-picker-board aria-label="按上市板块选择研究股票"><option>全部</option><option>主板</option><option>创业板</option><option>科创板</option></select></div><div style="padding:10px 0;display:flex;justify-content:space-between"><label class="check-option" style="margin:0"><input type="checkbox" data-picker-all>全选当前结果</label><span class="tiny muted" id="picker-count"></span></div><div id="picker-list"></div>`,{wide:true,confirmation:true,footer:B('取消','','data-action="close-modal"')+B('确认选择','check','data-action="picker-confirm"','primary')});renderPicker()}
  function pickerStocks(){return AT.stocks.filter(s=>(AT.ui.pickerBoard==='全部'||s.board===AT.ui.pickerBoard)&&(!AT.ui.pickerQuery||(s.name+s.code).includes(AT.ui.pickerQuery)))}
  function renderPicker(){preservePosition(()=>{const stocks=pickerStocks();$('#picker-list').innerHTML=stocks.map(s=>`<label class="stock-picker-row"><input type="${AT.ui.researchMode==='intersection'?'checkbox':'radio'}" name="picker-stock" data-pick-stock="${s.code}" ${AT.ui.pickerSelection.has(s.code)?'checked':''}><span><b>${s.name}</b> <span class="muted">${s.code}</span></span><span class="tag">${s.board}</span></label>`).join('')||'<p class="empty-state">没有匹配的股票</p>';$('#picker-count').textContent=`已选 ${AT.ui.pickerSelection.size} 只`;const all=$('[data-picker-all]'),count=stocks.filter(s=>AT.ui.pickerSelection.has(s.code)).length;all.disabled=AT.ui.researchMode!=='intersection';all.checked=stocks.length>0&&count===stocks.length;all.indeterminate=count>0&&count<stocks.length;all.parentElement.title=all.disabled?'当前模式只研究一个股票':''})}
  const searchRequests=new Map([[false,{serial:0,timer:null}],[true,{serial:0,timer:null}]]),searchComposing=new WeakSet();
  function cancelSearch(page){for(const [key,request] of searchRequests){if(page!==undefined&&key!==page)continue;clearTimeout(request.timer);request.serial++}}
  function renderSearch(query,page=false){
    cancelSearch(page);
    const q=query.trim().toLowerCase(),request=searchRequests.get(page),serial=request.serial,hostPage=AT.state.page;
    const targetSelector=page?'#page-search-results':'#search-results',inputSelector=page?'[data-page-search]':'[data-global-search]',target=$(targetSelector),input=$(inputSelector);
    if(!target)return;
    const pages=Object.values(AT.pages).filter(p=>p.title.toLowerCase().includes(q)).slice(0,q?12:5),pageHtml=pages.map(p=>`<button type="button" class="search-result" data-nav="${E(p.id)}">${I(p.icon)}<span>${E(p.title)}</span><small>页面</small></button>`).join('');
    const stockHtml=stocks=>stocks.slice(0,8).map(s=>`<button type="button" class="search-result" data-stock="${E(s.symbol||s.code)}">${I('market')}<span>${E(s.name)} <span class="muted">${E(s.code)}</span></span><small>${E(s.board||({sh:'沪市股票',sz:'深市股票',bj:'北交所股票'}[s.market])||'股票')}</small></button>`).join('');
    const message=text=>`<div class="field-hint" role="status">${E(text)}</div>`;
    if(!AT.isDesktop){target.innerHTML=stockHtml(AT.stocks.filter(s=>(s.name+s.code).toLowerCase().includes(q)))+pageHtml||'<div class="empty-state">没有匹配结果</div>';return}
    if(!q){target.innerHTML=message('输入股票名称、六位代码或拼音首字母，也可搜索页面。')+pageHtml;return}
    if(q.length>100){target.innerHTML=message('搜索内容请控制在 100 字以内。')+pageHtml;return}
    target.innerHTML=message('正在搜索股票…')+pageHtml;
    const current=()=>request.serial===serial&&target.isConnected&&$(targetSelector)===target&&$(inputSelector)===input&&input?.value.trim().toLowerCase()===q&&!searchComposing.has(input)&&AT.state.page===hostPage&&!target.closest('[hidden]');
    request.timer=setTimeout(async()=>{
      if(!current())return;
      try{
        if(typeof window.atradeDesktop?.market!=='function')throw new Error('行情服务尚未启动，请重新打开桌面应用。');
        const response=await window.atradeDesktop.market('market.search',{query:q,kind:'stock',limit:8});
        if(!current())return;
        if(!response?.ok)throw new Error(typeof response?.error==='string'?response.error:response?.error?.message||'股票搜索暂不可用，请稍后重试。');
        const items=(response.data?.items||[]).filter(s=>s.kind==='stock'&&/^(sh|sz|bj)\d{6}$/.test(s.symbol));
        target.innerHTML=(stockHtml(items)||message('未找到股票，请尝试完整名称、六位代码或拼音首字母。'))+pageHtml;
      }catch(error){if(current())target.innerHTML=message(error.message||'股票搜索暂不可用，请稍后重试。')+pageHtml}
    },200);
  }
  function search(){openModal('搜索股票与页面',`<div class="search-field">${I('search')}<input data-global-search aria-label="搜索股票与页面" placeholder="股票名称、代码、拼音首字母或页面关键词" maxlength="100" autocomplete="off"></div><div id="search-results" style="margin-top:12px"></div>`);renderSearch('');$('[data-global-search]')?.focus({preventScroll:true})}
  function updateValuation(){const form=$('#valuation-form');if(!form)return;const data=new FormData(form),values=['profit','growth','pe','water','emotion','actual'].map(k=>Number(data.get(k)));if(values.some(v=>!Number.isFinite(v)||v<=0)){$('#valuation-value').textContent='请输入有效参数';$('#valuation-gap').textContent='所有参数应大于 0。';return}const [profit,growth,pe,water,emotion,actual]=values,value=profit*growth*pe*water*emotion;$('#valuation-value').innerHTML=`${AT.money(value,1)} <small>亿</small>`;$('#valuation-gap').textContent=`情景市值相对实际市值 ${AT.sign((value/actual-1)*100)}% · 输入驱动的示例`}
  function schedule(){let slots=AT.state.concurrency-AT.state.tasks.filter(t=>t.status==='running').length;for(const t of AT.state.tasks){if(slots<=0)break;if(t.status==='queued'){t.status='running';t.step=t.type==='收益平原'?'参数扫描':'日线模拟';slots--}}}
  function taskAction(id,action,redraw=true){const t=AT.state.tasks.find(t=>t.id===id);if(!t)return;let changed=false;if(action==='pause'&&['running','queued'].includes(t.status)){t.status='paused';changed=true}if(action==='resume'&&['paused','stopped'].includes(t.status)){t.status='queued';t.step='等待计算额度';changed=true}if(action==='stop'&&['running','queued','paused'].includes(t.status)){t.status='stopped';changed=true}if(!changed)return;schedule();AT.persist();if(redraw){render();toast(`${t.name}：${AT.statusNames[t.status]}`)}}
  function activateTask(id){
    AT.ui.activeTask=id;const task=AT.state.tasks.find(t=>t.id===id);
    if(task?.params)AT.ui.researchParams={...task.params};if(task?.strategies)AT.ui.strategyIds=[...task.strategies];if(task?.pool)AT.ui.stockPool=[...task.pool];if(task?.mode)AT.ui.researchMode=task.mode;
  }
  function deleteTasks(ids){
    const entries=AT.state.tasks.flatMap((task,index)=>ids.includes(task.id)?[{task:structuredClone(task),index,deletedAt:new Date().toISOString()}]:[]);
    if(!entries.length)return;
    AT.state.taskTrash.unshift(...entries);AT.ui.lastDeleted=entries.map(entry=>entry.task.id);
    AT.state.tasks=AT.state.tasks.filter(t=>!ids.includes(t.id));ids.forEach(id=>AT.ui.taskSelection.delete(id));
    if(ids.includes(AT.ui.activeTask))activateTask(AT.state.tasks[0]?.id||null);
    schedule();AT.persist();render();toast(`已删除 ${entries.length} 个任务，配置与进度保留在回收站`,true);
  }
  function restoreTasks(ids){
    const entries=AT.state.taskTrash.filter(entry=>ids.includes(entry.task.id)).sort((a,b)=>a.index-b.index);
    if(!entries.length){toast('这些任务已恢复');return}
    for(const entry of entries){const task=entry.task;if(AT.state.tasks.some(t=>t.id===task.id))continue;
      // Restoring a deleted running job must not silently restart work.
      if(['running','queued'].includes(task.status)){task.status='paused';task.step='已恢复 · 等待继续'}
      AT.state.tasks.splice(Math.min(entry.index,AT.state.tasks.length),0,task);
    }
    AT.state.taskTrash=AT.state.taskTrash.filter(entry=>!ids.includes(entry.task.id));
    if(!AT.ui.activeTask)activateTask(entries[0].task.id);
    AT.persist();closeModal();render();toast(`已恢复 ${entries.length} 个任务；原运行或排队任务保持暂停`);
  }
  function taskTrash(){openModal('任务回收站',`<p class="field-hint">删除的任务保留配置与进度。恢复后可手动继续，已保存的报告不会删除。</p><div class="task-trash-list">${AT.state.taskTrash.map(({task,deletedAt})=>`<article class="task-trash-item"><div><h3>${E(task.name)}</h3><p>${AT.stockLink(task.stock)} · ${AT.statusNames[task.status]} · ${task.progress.toFixed(1)}%</p><small>删除于 ${E(new Date(deletedAt).toLocaleString('zh-CN'))}</small></div>${B('恢复','resume',`data-task-restore="${E(task.id)}"`,'small')}</article>`).join('')||'<div class="empty-state">回收站为空</div>'}</div>`,{wide:true,footer:AT.state.taskTrash.length?B('全部恢复','resume','data-action="restore-all-tasks"','primary'):B('关闭','','data-action="close-modal"')})}
  function runResearch(plateau=false){
    const p=AT.ui.researchParams;if(!plateau){if(!AT.ui.stockPool.length||!AT.ui.strategyIds.length){toast('请至少选择一只股票和一个策略');return}if(AT.ui.strategyIds.includes('trend')&&AT.ui.strategyIds.includes('revert')){toast('当前策略环境条件冲突，请先调整组合');return}if(!Number.isFinite(p.take)||p.take<1||p.take>100||!Number.isFinite(p.stop)||p.stop<1||p.stop>50||!Number.isFinite(p.reduce)||p.reduce<1||p.reduce>100){toast('请检查止盈、止损和减仓比例的范围');return}}
    const name=plateau?'持仓与止盈参数扫描':AT.ui.strategyIds.map(id=>AT.strategies.find(s=>s.id===id).name).join(' × '),t={id:'t'+Date.now(),name,type:plateau?'收益平原':AT.ui.researchMode==='intersection'?'多策略交集':'个股策略回测',status:'queued',progress:0,stock:AT.ui.stockPool[0]||'600900',date:'2018.01 — 2026.09',created:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}),step:'等待计算额度',count:plateau?40:AT.ui.stockPool.length,params:{...p},strategies:[...AT.ui.strategyIds],pool:[...AT.ui.stockPool],mode:AT.ui.researchMode};AT.state.tasks.push(t);AT.ui.activeTask=t.id;schedule();AT.persist();if(plateau)go('G02');else render();toast('已创建演示任务，可在任务中心管理')
  }
  function updateTasks(){
    if(AT.isDesktop)return;
    if(!AT.state.tasks.some(task=>task.status==='running'))return;
    const focus=captureFocus();
    let transition=false;for(const t of AT.state.tasks){if(t.status!=='running')continue;t.progress=Math.min(100,+(t.progress+.14).toFixed(2));if(t.progress>=100){t.status='complete';t.step='报告已生成';transition=true}else t.step=t.type==='收益平原'?'参数扫描':t.progress<15?'数据准备':t.progress<35?'历史筛选':t.progress<90?'日线模拟':'指标统计'}
    if(transition)schedule();AT.persist();const run=AT.state.tasks.filter(t=>t.status==='running').length,queue=AT.state.tasks.filter(t=>t.status==='queued').length;$('#top-task-count').textContent=`运行 ${run} · 排队 ${queue}`;$('#sidebar-tasks').textContent=`${run} 项运行 · ${queue} 项排队`;
    $$('[data-task-row]').forEach(row=>{const t=AT.state.tasks.find(t=>t.id===row.dataset.taskRow);if(!t)return;$('[data-live-status]',row).innerHTML=AT.statusTag(t.status);$('[data-live-step]',row).textContent=t.step;$('[data-live-percent]',row).textContent=t.progress.toFixed(1)+'%';$('.progress',row).setAttribute('aria-valuenow',t.progress);$('.progress i',row).style.width=t.progress+'%';$('[data-live-actions]',row).innerHTML=AT.taskActions(t)});
    const strip=$('.task-strip');if(strip&&!$('.overlay'))strip.outerHTML=AT.taskStrip();restoreFocus(focus);if(transition&&AT.state.page==='G02'&&!$('.overlay'))render()
  }
  function download(name,content,type='text/plain;charset=utf-8'){const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);toast('已导出到浏览器下载目录')}
  function exportTrades(){const records=AT.state.page[0]==='R'?AT.researchTrace(AT.state.page==='R06'&&AT.ui.pathContext||AT.chartContext('research')):AT.trades;const rows=[['股票名称','股票代码','信号日期','成交日期','操作','数量','价格','说明'],...records.map(t=>[AT.findStock(t.code).name,t.code,t.signal,t.date,t.action,t.qty,t.price,t.reason])];download('A-trade-演示交易记录.csv','\ufeff'+rows.map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n'),'text/csv;charset=utf-8')}
  function reportText(){return `A-trade 研究摘要（HTML 预览演示）\n\n本文件不是实际回测结果。\n研究：趋势回踩 × 量价突破\n对象：${AT.ui.stockPool.map(c=>{const s=AT.findStock(c);return s.name+' '+s.code}).join('、')}\n时序：T 日晚间判断，T+1 执行。\n示例累计收益 +42.80%，示例最大回撤 -8.36%。\n数据、财务和策略引擎尚未接入。\n\n说明：用于评审报告导出、研究路径与页面交互。`}
  function addTrade(){openModal('记录交易 · 演示流水',`<form id="trade-form"><label class="field"><span>股票</span><select name="stock">${AT.stocks.map(s=>`<option value="${s.code}">${s.name} ${s.code}</option>`).join('')}</select></label><div class="field-row"><label class="field"><span>操作</span><select name="action"><option>买入</option><option>减仓</option><option>卖出</option></select></label><label class="field"><span>成交日期</span><input name="date" type="date" value="2026-10-09" required></label><label class="field"><span>数量</span><input name="qty" type="number" value="100" min="1" step="1" required></label><label class="field"><span>成交价格</span><input name="price" type="number" value="28.64" min="0.01" step="0.01" required></label></div><label class="field"><span>操作依据</span><input name="reason" placeholder="记录当时的判断" maxlength="100"></label><p class="field-hint">仅追加预览流水，不向券商发送交易指令；账户资产仍使用固定示例快照。</p></form>`,{footer:B('取消','','data-action="close-modal"')+B('保存演示流水','save','data-action="save-trade"','primary')})}
  function viewTrade(i){const t=AT.trades[i];openModal('交易记录详情',`<div class="report-text"><p>${AT.stockLink(t.code)}</p><h3>${t.action} ${AT.money(t.qty,0)} 股 · ¥ ${AT.money(t.price)}</h3><p>信号日期：${E(t.signal)}<br>成交日期：${E(t.date)}<br>依据：${E(t.reason)}</p><p class="field-hint">示例交易记录，不对应真实成交。</p></div>`,{footer:B('查看日线','market',`data-stock="${t.code}"`)+B('操作路径','route','data-nav="R06"','primary')})}
  function quickNote(){openModal('灵感闪记',`<label class="field"><span>快速记录</span><textarea data-note="A12" rows="8" placeholder="刚刚想到的研究方向……">${E(AT.state.notes.A12||'')}</textarea></label><span class="tiny muted" id="note-state">${AT.isDesktop?'输入后自动保存到本机':'输入后自动保存在本浏览器'}</span>`,{footer:B('完成','check','data-action="close-modal"','primary')})}
  const actions={
    'asset-snapshots':()=>openModal('资产快照',AT.snapshotRecords(),{wide:true}),
    'import-records':()=>openModal('导入与对账',`<div class="report-text"><p>把券商成交文件映射为统一流水，再核对笔数、金额与重复记录。</p><h3>导入字段</h3><p>成交日期 · 股票名称 / 六位代码 · 买卖方向 · 数量 · 成交价 · 手续费</p><h3>对账预览</h3><p>正式应用将在确认后写入交易管理。当前仅展示字段与记录样式，尚未接入文件解析。</p></div>${AT.recordsTable()}`,{wide:true,footer:B('返回交易管理','check','data-action="close-modal"','primary')}),
    'export-ledger':()=>AT.ui.ledgerMode==='cash'?download('A-trade-演示资金流水.csv',`日期,类型,金额,说明\n2026-01-05,期初资金,${AT.state.startCapital},账户演示起算资金`,'text/csv;charset=utf-8'):exportTrades(),
    'export-account-summary':()=>download('A-trade-复盘分享.txt',`A-trade · 账户复盘\n2026.01 — 2026.10 · 演示数据\n累计收益 +25.86%\n\n${AT.state.notes.A09||AT.state.notes.A08||'回顾判断，记录执行。'}\n\n不包含账户姓名、资产金额及持仓数量。`),
    'close-subnav':()=>closeSubnav(true),
    'task-trash':taskTrash,'undo-delete':()=>restoreTasks(AT.ui.lastDeleted||[]),'restore-all-tasks':()=>restoreTasks(AT.state.taskTrash.map(entry=>entry.task.id)),
    'milestone-add-event':()=>{const form=$('#milestone-event-form');if(!form||AT.milestoneData(AT.ui.milestoneNumber).status==='future')return;form.hidden=false;form.scrollIntoView({block:'nearest'});$('[name="event-title"]',form).focus({preventScroll:true})},
    'milestone-cancel-event':()=>{const form=$('#milestone-event-form');if(form){form.reset();form.hidden=true}},
    'search':search,'close-modal':closeModal,'close-workspace':closeBusinessWorkspace,'data-info':showInfo,'account-settings':accountSettings,'back':back,'note':quickNote,'stock-picker':picker,'add-trade':addTrade,
    'export-trades':exportTrades,'export-report':()=>download('A-trade-演示研究摘要.txt',reportText()),
    'backup':()=>{try{const backup=AT.workspaceBackup.create(AT.state,{settings:AT.settingsState});download(AT.isDesktop?'A-trade-界面状态备份.json':'A-trade-预览备份.json',JSON.stringify(backup),'application/json;charset=utf-8')}catch(error){toast(error.message||'界面状态无法导出，请检查本机状态。')}},
    'save-account':()=>{const f=$('#account-form');if(!f.reportValidity())return;const d=new FormData(f);AT.state.goal=+d.get('goal');AT.state.stages=+d.get('stages');AT.state.startCapital=+d.get('capital');AT.ui.passPosition=null;AT.persist();closeModal();render();toast('账户目标已更新，通行证按新参数显示')},
    'picker-confirm':()=>{if(!AT.ui.pickerSelection.size){toast('请至少选择一只股票');return}AT.ui.stockPool=[...AT.ui.pickerSelection];closeModal();render()},
    'run-research':()=>runResearch(false),'run-plateau':()=>runResearch(true),
    'new-research':()=>{AT.ui.researchMode='intersection';AT.ui.strategyIds=['trend','volume'];AT.ui.researchParams={take:15,stop:6,reduce:50};go('R03');toast('配置新的研究，点击“开始演示回测”加入任务队列')},
    'research-stock':()=>{AT.ui.stockPool=[AT.state.stock];AT.ui.researchMode='single';AT.ui.strategyIds=['trend'];go('R03')},
    'save-report':async()=>{const t=AT.state.tasks.find(t=>t.id===AT.ui.activeTask)||AT.state.tasks[0];if(!t){toast('请先创建或恢复研究任务');return}AT.state.reports.unshift({name:t.name+' · 示例摘要',date:new Date().toLocaleDateString('sv-SE'),stock:t.stock});const saved=await AT.persist();toast(saved?'示例摘要已保存到报告库':'保存失败，摘要暂留在当前会话')},
    'save-note':async()=>{const el=$('[data-note]');if(el)AT.state.notes[el.dataset.note]=el.value;clearTimeout(noteTimer);const saved=await AT.persist();toast(saved?(AT.isDesktop?'笔记已保存到本机':'笔记已保存到当前浏览器'):'保存失败，笔记暂留在当前会话')},
    'open-data-folder':async()=>{if(!AT.isDesktop)return;try{const result=await window.atradeDesktop.openDataFolder();if(!result?.ok)toast('未能打开数据文件夹，请按页面路径查看')}catch{toast('未能打开数据文件夹，请按页面路径查看')}},
    'watch-selected':()=>{if(!AT.ui.selected.size){toast('请先选择股票');return}AT.state.watch=[...new Set([...AT.state.watch,...AT.ui.selected])];AT.persist();toast(`已将 ${AT.ui.selected.size} 只股票加入跟踪池`)},
    'save-trade':()=>{const f=$('#trade-form');if(!f.reportValidity())return;const d=new FormData(f),t={account:AT.state.account,date:d.get('date'),signal:'手动录入',code:d.get('stock'),action:d.get('action'),qty:+d.get('qty'),price:+d.get('price'),reason:d.get('reason')||'手动演示记录'};AT.trades.unshift(t);AT.state.customTrades=AT.state.customTrades||[];AT.state.customTrades.unshift(t);AT.persist();closeModal();go('A04');toast('已追加预览流水，固定账户快照保持不变')}
  };
  document.addEventListener('click',e=>{
    if(compactNavigation()&&AT.ui.navGroup&&!e.target.closest('#navigation,#nav-flyout-root,[data-action="toggle-sidebar"]'))closeSubnav();
    if(e.target.classList.contains('overlay')){closeModal();return}
    const el=e.target.closest('button,a[data-nav]');if(!el||el.disabled)return;
    if(el.dataset.action==='toggle-sidebar'){
      preservePosition(()=>{
        AT.state.sidebarCollapsed=!compactNavigation();
        AT.ui.navGroup=compactNavigation()?null:navigationGroup(AT.state.page);
        updateShell();AT.drawCharts();AT.drawCandles();
      });
      AT.persist();return;
    }
    if(el.dataset.action){if(!AT.businessUI?.handleAction(el.dataset.action))actions[el.dataset.action]?.();return}
    if(el.dataset.dayStep){AT.openDayDetail({...AT.ui.dayDetail,index:AT.ui.dayDetail.index+Number(el.dataset.dayStep)});return}
    if(el.dataset.researchDay){const context=JSON.parse(el.dataset.context),points=AT.curvePoints(context,'research','year');AT.openDayDetail({context,points,index:points.findIndex(p=>p.date===el.dataset.researchDay)});return}
    if(el.dataset.navGroup){const id=el.dataset.navGroup;AT.ui.navGroup=AT.ui.navGroup===id?null:id;updateShell();$(`[data-nav-group="${id}"]`).focus({preventScroll:true});return}
    if(el.dataset.ledgerMode){AT.ui.ledgerMode=el.dataset.ledgerMode;preservePosition(()=>{const panel=$('.ledger-panel');panel.querySelectorAll('[data-ledger-mode]').forEach(b=>{const active=b===el;b.classList.toggle('active',active);b.setAttribute('aria-selected',active);b.tabIndex=active?0:-1});const content=$('#ledger-mode-panel');content.setAttribute('aria-labelledby',el.id);content.innerHTML=el.dataset.ledgerMode==='cash'?AT.cashRecords():AT.recordsTable()});return}
    if(el.dataset.reviewNote){AT.ui.reviewNote=el.dataset.reviewNote;preservePosition(()=>{const panel=$('.review-notebook');const heading=$('.panel-head',panel).outerHTML;panel.innerHTML=heading+AT.reviewNotebook();$(`[data-review-note="${AT.ui.reviewNote}"]`)?.focus({preventScroll:true})});return}
    if(el.dataset.scrollSection){scrollToSection(el.dataset.scrollSection);return}
    if(el.dataset.nav){e.preventDefault();if(el.dataset.nav==='R06')AT.ui.pathContext=AT.chartContext(AT.state.page==='R05'?'plateau':'research');go(el.dataset.nav);return}
    if(el.dataset.stock){go('G01',el.dataset.stock);return}
    if(el.dataset.milestoneFilter){AT.ui.milestoneFilter=el.dataset.milestoneFilter;milestoneDetail(AT.ui.milestoneNumber);return}
    if(el.dataset.milestone){milestoneDetail(+el.dataset.milestone);return}
    if(el.dataset.watch){const c=el.dataset.watch;AT.state.watch=AT.state.watch.includes(c)?AT.state.watch.filter(x=>x!==c):[...AT.state.watch,c];AT.persist();render();return}
    if(el.dataset.taskAction){taskAction(el.dataset.task,el.dataset.taskAction);return}
    if(el.dataset.taskDelete){deleteTasks([el.dataset.taskDelete]);return}
    if(el.dataset.taskRestore){restoreTasks([el.dataset.taskRestore]);return}
    if(el.dataset.taskDetail){AT.ui.activeTask=el.dataset.taskDetail;go('G03');return}
    if(el.dataset.workspace){activateTask(el.dataset.workspace);render();return}
    if(el.dataset.batch){const ids=[...AT.ui.taskSelection];if(!ids.length){toast('请先选择任务');return}if(el.dataset.batch==='delete'){deleteTasks(ids);return}ids.forEach(id=>taskAction(id,el.dataset.batch,false));render();toast('已对适用的演示任务执行批量操作');return}
    if(el.dataset.taskFilter){AT.ui.taskFilter=el.dataset.taskFilter;render();return}
    if(el.dataset.range){AT.ui.range=el.dataset.range;render();return}
    if(el.dataset.metric){AT.ui.metric=el.dataset.metric;render();return}
    if(el.dataset.resultTab){AT.ui.resultTab=el.dataset.resultTab;render();return}
    if(el.dataset.sector){AT.ui.sector=el.dataset.sector;go('M03');return}
    if(el.dataset.useStrategy){AT.ui.strategyIds=[el.dataset.useStrategy];AT.ui.researchMode='single';AT.ui.stockPool=AT.ui.stockPool.slice(0,1);go('R03');return}
    if(el.dataset.heat){AT.ui.heat=el.dataset.heat.split(',').map(Number);render();return}
    if(el.dataset.pathStep){AT.ui.pathIndex=(AT.ui.pathIndex+Number(el.dataset.pathStep)+AT.trades.length)%AT.trades.length;render();return}
    if(el.dataset.trade!==undefined){viewTrade(+el.dataset.trade);return}
    if(el.dataset.zoom){AT.ui.chartZoom=el.dataset.zoom==='reset'?1:Math.max(.65,Math.min(3,AT.ui.chartZoom*(el.dataset.zoom==='in'?1.3:1/1.3)));AT.drawCandles();return}
    if(el.dataset.news!==undefined){const [type,title,desc,time,code]=AT.news[+el.dataset.news];openModal(title,`<span class="tag">${type} · 虚构示例</span><div class="report-text section-space"><p>${desc}</p><p>此处展示资讯正文、来源与关联对象的阅读布局，不是实际新闻或公告。</p><h3>关联股票</h3>${AT.stockLink(code)}</div>`,{wide:true});return}
  });
  document.addEventListener('input',e=>{const el=e.target;
    if(el.hasAttribute('data-stock-search')){AT.ui.query=el.value;rerenderList()}
    if(el.hasAttribute('data-picker-query')){AT.ui.pickerQuery=el.value;renderPicker()}
    if(el.hasAttribute('data-global-search')&&!e.isComposing&&!searchComposing.has(el))renderSearch(el.value);
    if(el.hasAttribute('data-page-search')&&!e.isComposing&&!searchComposing.has(el))renderSearch(el.value,true);
    if(el.dataset.note){AT.state.notes[el.dataset.note]=el.value;clearTimeout(noteTimer);const status=$('#note-state');if(status)status.textContent='等待保存…';const value=el.value,key=el.dataset.note;noteTimer=setTimeout(async()=>{const saved=await AT.persist(),current=$('#note-state');if(current&&AT.state.notes[key]===value)current.textContent=saved?(AT.isDesktop?'已保存到本机':'已保存到此浏览器'):'保存失败，笔记暂留在当前会话'},300)}
    if(el.closest('#valuation-form'))updateValuation();
    const param={'take-profit':'take','stop-loss':'stop','reduce-ratio':'reduce'}[el.name];if(param)AT.ui.researchParams[param]=Number(el.value);
  });
  document.addEventListener('compositionstart',e=>{if(e.target.matches('[data-global-search],[data-page-search]')){searchComposing.add(e.target);cancelSearch(e.target.hasAttribute('data-page-search'))}});
  document.addEventListener('compositionend',e=>{if(e.target.matches('[data-global-search],[data-page-search]')){searchComposing.delete(e.target);renderSearch(e.target.value,e.target.hasAttribute('data-page-search'))}});
  document.addEventListener('change',e=>{const el=e.target;
    if(['take-profit','stop-loss','reduce-ratio'].includes(el.name)&&el.validity.valid)render();
    if(el.hasAttribute('data-account')){AT.state.account=el.value;AT.persist();render();toast('已切换账户视图；两类账户暂共用一套演示数据')}
    if(el.hasAttribute('data-board')){AT.ui.board=el.value;rerenderList()}
    if(el.hasAttribute('data-select-stocks')){const stocks=AT.filteredStocks($('[data-list-type]').dataset.listType);stocks.forEach(s=>el.checked?AT.ui.selected.add(s.code):AT.ui.selected.delete(s.code));rerenderList()}
    if(el.dataset.stockCheck){el.checked?AT.ui.selected.add(el.dataset.stockCheck):AT.ui.selected.delete(el.dataset.stockCheck);rerenderList()}
    if(el.hasAttribute('data-research-mode')){AT.ui.researchMode=el.value;if(el.value!=='intersection')AT.ui.stockPool=AT.ui.stockPool.slice(0,1);if(el.value==='single')AT.ui.strategyIds=AT.ui.strategyIds.slice(0,1);render()}
    if(el.dataset.strategy){if(AT.ui.researchMode==='single')AT.ui.strategyIds=[el.dataset.strategy];else AT.ui.strategyIds=el.checked?[...new Set([...AT.ui.strategyIds,el.dataset.strategy])]:AT.ui.strategyIds.filter(x=>x!==el.dataset.strategy);render()}
    if(el.hasAttribute('data-all-strategies')){AT.ui.strategyIds=el.checked?AT.strategies.map(s=>s.id):[];if(AT.ui.researchMode==='single'){AT.ui.researchMode='multiple';toast('全选策略已切换为个股多策略模式')}render()}
    if(el.hasAttribute('data-picker-board')){AT.ui.pickerBoard=el.value;renderPicker()}
    if(el.dataset.pickStock){if(AT.ui.researchMode!=='intersection')AT.ui.pickerSelection=new Set([el.dataset.pickStock]);else el.checked?AT.ui.pickerSelection.add(el.dataset.pickStock):AT.ui.pickerSelection.delete(el.dataset.pickStock);renderPicker()}
    if(el.hasAttribute('data-picker-all')){pickerStocks().forEach(s=>el.checked?AT.ui.pickerSelection.add(s.code):AT.ui.pickerSelection.delete(s.code));renderPicker()}
    if(el.dataset.taskCheck){el.checked?AT.ui.taskSelection.add(el.dataset.taskCheck):AT.ui.taskSelection.delete(el.dataset.taskCheck);initView()}
    if(el.hasAttribute('data-all-tasks')){$$('[data-task-check]').forEach(box=>{box.checked=el.checked;el.checked?AT.ui.taskSelection.add(box.dataset.taskCheck):AT.ui.taskSelection.delete(box.dataset.taskCheck)})}
    if(el.hasAttribute('data-concurrency')){AT.state.concurrency=+el.value;let running=AT.state.tasks.filter(t=>t.status==='running');running.slice(AT.state.concurrency).forEach(t=>{t.status='queued';t.step='等待计算额度'});schedule();AT.persist();render()}
    if(el.dataset.pref){AT.state[el.dataset.pref]=el.checked;applyPrefs();AT.persist();AT.drawCharts();AT.drawCandles()}
    if(el.hasAttribute('data-plateau-ratio')){AT.ui.plateauRatio=+el.value;render();toast(`首次减仓比例设为 ${el.value}%，已切换演示曲线`)}
    if(el.hasAttribute('data-plateau-strategies')){AT.ui.plateauStrategies=el.value.split(',');render()}
    if(el.hasAttribute('data-day-date')){const index=AT.ui.dayDetail.points.findIndex(p=>p.date===el.value);if(index<0){el.value=AT.ui.dayDetail.points[AT.ui.dayDetail.index].date;toast('所选日期不在当前曲线的样例数据中');return}AT.openDayDetail({...AT.ui.dayDetail,index})}
    if(el.id==='restore-file'&&el.files[0])readBackup(el.files[0]);
  });
  document.addEventListener('submit',async e=>{
    if(e.target.id==='milestone-event-form'){
      e.preventDefault();const form=e.target;if(!form.reportValidity())return;
      const d=new FormData(form),title=String(d.get('event-title')).trim(),note=String(d.get('event-note')).trim();
      if(!title||!note){toast('请填写事件标题和内容');return}
      const key=AT.milestoneEventKey(AT.ui.milestoneNumber),items=AT.state.milestoneEvents[key];
      AT.state.milestoneEvents[key]=[...(Array.isArray(items)?items:[]),{kind:'event',local:true,date:d.get('event-date'),time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Taipei'}),title,note,category:d.get('event-category'),code:d.get('event-stock')}];
      form.reset();form.hidden=true;AT.ui.milestoneFilter='event';milestoneDetail(AT.ui.milestoneNumber);const saved=await AT.persist();toast(saved?'日期事件已保存到此里程碑':'保存失败，事件暂留在当前会话');return;
    }
    if(e.target.id==='rule-form'){e.preventDefault();await AT.settingsPage.saveRules(e.target)}});
  async function readBackup(file){
    AT.ui.restoreCandidate=null;
    if(file.size>AT.workspaceBackup.MAX_FILE_BYTES){toast('界面备份文件不能超过 6 MiB');return}
    try{
      let data;try{data=JSON.parse(await file.text())}catch{throw new Error('无法读取 JSON 备份')}
      const {candidate,summary}=AT.workspaceBackup.prepare(data,AT.state,{settings:AT.settingsState,isDesktop:AT.isDesktop});
      AT.ui.restoreCandidate=data;
      const prefs=['compact','largeText','reduced'].map(key=>`${{compact:'紧凑表格',largeText:'大号文字',reduced:'减少动态'}[key]}：${candidate[key]?'开启':'关闭'}`).join(' · ');
      openModal('恢复界面状态',`<div class="report-text"><p>文件：${E(file.name)}</p><p>${summary.tasks} 个演示任务 · ${summary.notes} 份历史笔记 · ${summary.reviews} 份复盘草稿 · ${summary.customTrades} 笔演示流水</p><p>${E(prefs)}</p><p>将恢复文件包含的显示偏好、侧栏、费率与仓位规则等界面状态。${summary.legacy?'旧备份未包含的字段保持现值。':''}${AT.isDesktop?'运行中或排队的演示任务保持暂停。':''}</p><p>正式账户账本、真实跟踪池、行情缓存、研究任务与 AI 配置不在此恢复范围。</p><p class="business-error" data-interface-restore-error role="alert" hidden></p></div>`,{confirmation:true,footer:B('取消','','data-action="close-modal"')+B('确认恢复界面状态','backup','data-action="confirm-restore"','primary')});
    }catch(error){toast(error.message||'无法读取备份文件')}
    finally{const input=$('#restore-file');if(input)input.value=''}
  }
  actions['confirm-restore']=async()=>{
    const data=AT.ui.restoreCandidate;if(!data||AT.ui.restoringBackup)return;
    const button=$('[data-action="confirm-restore"]');
    AT.ui.restoringBackup=true;if(button)button.disabled=true;
    try{
      const {candidate}=AT.workspaceBackup.prepare(data,AT.state,{settings:AT.settingsState,isDesktop:AT.isDesktop});
      if(!await AT.persist(candidate))throw new Error('保存失败，当前界面状态未替换。请检查磁盘空间或目录权限后重试。');
      const previousTrades=new Set(AT.state.customTrades||[]);AT.trades=AT.trades.filter(trade=>!previousTrades.has(trade));
      for(const key of Object.keys(AT.state))if(!Object.hasOwn(candidate,key))delete AT.state[key];
      Object.assign(AT.state,candidate);AT.trades.unshift(...(candidate.customTrades||[]));
      AT.ui.restoreCandidate=null;AT.ui.activeTask=AT.state.tasks[0]?.id||null;AT.ui.taskSelection.clear();AT.ui.restoringBackup=false;
      applyPrefs();closeModal();render();toast(AT.isDesktop?'界面状态已恢复并保存到本机':'界面状态已恢复并保存到浏览器');
    }catch(error){const target=$('[data-interface-restore-error]');if(target){target.hidden=false;target.textContent=error.message}toast(error.message||'恢复失败，当前状态已保留')}
    finally{AT.ui.restoringBackup=false;if(button)button.disabled=false}
  };
  document.addEventListener('keydown',e=>{
    if(e.target.matches('[data-global-search],[data-page-search]')&&['Enter','ArrowDown'].includes(e.key)&&!e.isComposing&&e.keyCode!==229&&!searchComposing.has(e.target)){
      const result=$(e.target.hasAttribute('data-page-search')?'#page-search-results .search-result':'#search-results .search-result');
      if(result){e.preventDefault();if(e.key==='Enter')result.click();else result.focus({preventScroll:true})}return;
    }
    if(e.target.matches('.local-tab')&&['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
      e.preventDefault();const tabs=$$('.local-tab',e.target.closest('[role="tablist"]')),index=tabs.indexOf(e.target),next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus({preventScroll:true});tabs[next].click();return;
    }
    if(e.key==='Escape'&&$('.nav-flyout')){e.preventDefault();closeSubnav(true);return}
    if(e.key==='ArrowDown'&&e.target.matches('[data-nav-group]')&&$('.nav-flyout')){e.preventDefault();$('.nav-flyout .nav-item')?.focus({preventScroll:true});return}
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();search()}
    if(e.key==='Escape'&&$('.overlay'))closeModal();
    if(e.key==='Tab'&&$('.overlay .modal')){const items=$$('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]', $('.overlay .modal')).filter(x=>x.getClientRects().length),first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}
  });
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{const compact=compactNavigation();if(compact!==document.body.classList.contains('nav-collapsed')){AT.ui.navGroup=compact?null:navigationGroup(AT.state.page);updateShell()}positionSubnav();AT.drawCharts();AT.drawCandles()},100)});
  if(!AT.isDesktop)window.addEventListener('beforeunload',()=>{AT.persist()});
  if(AT.isDesktop){
    const desktopLabel=window.atradeDesktop?.bootstrap?.app?.packaged?'便携版':'桌面开发版';
    document.title='A-trade · '+desktopLabel;
    $('.local-identity small').textContent='本机保存 · '+desktopLabel;
    $('.demo-label').textContent=desktopLabel+' · 业务演示';
    $('.statusbar>span:first-child').innerHTML='<i class="status-dot"></i> 业务演示数据 · 未连接 TDX / MCP';
    $('#save-status').textContent=AT.storageWarning||'更改自动保存到本机';
    $('#save-status').dataset.saveState=AT.storageWarning?'warning':'ready';
  }
  readRoute();render();setInterval(updateTasks,2400);
})();
