/* Desktop research tools only consume verified service responses. */
(() => {
  if (!AT.isDesktop) return;
  const bridge = window.atradeDesktop, E = AT.escape, I = AT.icon;
  const pages = new Set(['T01','T02','T03','T04','T05','T06','T07','T08','T09']);
  const $ = (selector, root = document) => root.querySelector(selector);
  const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const n = value => finite(value) ? Number(value) : null;
  const num = (value, digits = 2) => finite(value) ? Number(value).toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
  const pct = value => finite(value) ? `${Number(value)>0?'+':''}${num(value)}%` : '—';
  const money = value => !finite(value) ? '—' : Math.abs(Number(value))>=1e8 ? `${num(Number(value)/1e8)} 亿` : Math.abs(Number(value))>=1e4 ? `${num(Number(value)/1e4)} 万` : num(value);
  const tone = value => !finite(value) || Number(value)===0 ? '' : Number(value)>0 ? 'analysis-tools-up' : 'analysis-tools-down';
  const timestamp = value => {if(!value)return '—';const text=String(value);if(/^\d{4}-\d{2}-\d{2}$/.test(text))return E(text);const date=new Date(text);return E(Number.isNaN(date.valueOf())?text:date.toLocaleString('zh-CN',{hour12:false}));};
  const errorText = value => typeof value==='string' ? value : value?.message || '读取失败，请重试。';
  const symbol = value => {const text=String(value||'').trim().toLowerCase();if(/^(sh|sz|bj)\d{6}$/.test(text))return text;if(!/^\d{6}$/.test(text))return '';return (/^(4|8|92)/.test(text)?'bj':/^[569]/.test(text)?'sh':'sz')+text;};
  const code = value => String(value?.code || value?.symbol?.replace(/^(sh|sz|bj)/,'') || '');
  const statuses = [['watching','观察中'],['planned','计划中'],['holding','持有中'],['archived','已归档']];
  const resources = new Map(), pendingWrites = new Set(), ui = new Map(), selections = new Map();
  let version=0, sourceKey='', feedback=null, busyWrite=false, pendingDelete='', inputTimer, fundsDrag=null, watchRevision=0, watchEditor=false, progressTimer=null, activityCheckedAt=0, activityCheckedDay='';
  const fundsWindow={key:'',start:0,count:null,focus:null};
  const flowFields=[['mainNetInflow','主力'],['superLargeNetInflow','超大单'],['largeNetInflow','大单'],['mediumNetInflow','中单'],['smallNetInflow','小单']];
  const scenario = {growth:'1',pe:'',water:'1',emotion:'1',actual:''};
  let valuationTab='basis';
  const financeSearch={query:'',items:[],total:0,busy:false,error:'',open:false,active:-1,serial:0,composing:false,deferred:false,timer:null};
  const draft = {
    finance:{symbol:symbol(AT.state.stock)}, screen:{query:'',industry:'',page:1,pageSize:20,positive:true,maxPe:'',below:false},
    research:{asOf:''}, trends:{minReturn20:'0',minReturn60:'0',requireAboveMA20:true,requireAboveMA60:true,minVolumeRatio:'0',minAmount:'0',excludeST:true},
    rebounds:{minBoards:'2',minGap:'1',maxGap:'10',lookbackDays:'60'}, ladder:{historyDays:'250',board:'all',minHeight:'2',excludeST:true}, funds:{mode:'activity',period:'today',id:'',name:'',limit:'60',field:'mainNetInflow'},
    watch:{symbol:'',note:'',tags:'',status:'watching'}, financeInput:symbol(AT.state.stock)
  };
  const button = (label, action, attrs='', primary=false) => `<button type="button" class="analysis-tools-button${primary?' analysis-tools-primary':''}${['watch-delete','watch-delete-confirm','preset-delete'].includes(action)?' analysis-tools-danger':''}" data-analysis-action="${action}" ${attrs}>${E(label)}</button>`;
  const chip = (label, field, value, active, scope='ui') => `<button type="button" class="analysis-tools-chip${active?' analysis-tools-selected':''}" data-analysis-chip="${field}" data-analysis-value="${E(String(value))}" data-analysis-scope="${scope}" aria-pressed="${!!active}">${E(label)}</button>`;
  const field = (label, group, key, value, type='number', extra='') => `<label class="analysis-tools-field"><span>${E(label)}</span><input type="${type}" data-analysis-group="${group}" data-analysis-field="${key}" value="${E(String(value??''))}" ${extra}></label>`;
  const textField = (label,group,key,value,extra='') => field(label,group,key,value,'text',extra);
  const head = (page, subtitle, actions='') => AT.viewParts.head(E(AT.pages[page]?.title||'分析工具'),E(subtitle),actions);
  const stockLink = item => `<button type="button" class="analysis-tools-stock" data-stock="${E(item.symbol)}" ${item.eventDate||item.date?`data-analysis-stock-day="${E(item.eventDate||item.date)}"`:''}><strong>${E(item.name||code(item))}</strong><small>${E(code(item))}</small></button>`;
  const badge = text => `<span class="analysis-tools-badge">${E(text)}</span>`;
  function localUI(page) {if(!ui.has(page))ui.set(page,{query:'',direction:'all',sort:null,order:null,defaultSort:({T03:'marketCap',T04:'return20',T05:'mainNetInflow',T07:'eventDate',T09:'updatedAt'}[page]||'amount'),defaultOrder:'desc',offset:0,status:'all'});return ui.get(page);}
  function refresh(page=AT.state.page) {if(pages.has(AT.state.page)&&AT.state.page===page){if(page==='T08'&&AT.leaderResearch?.deferRender())return;if(page==='T02'&&financeSearch.composing){financeSearch.deferred=true;return;}financeSearch.deferred=false;AT.render?.();}}
  const today = () => new Date(Date.now()+8*60*60*1000).toISOString().slice(0,10);
  function fresh(entry) {const ttl=entry.error?30000:entry.request?.[0]==='market.finance.benchmark'?3600000:entry.request?.[0]==='market.watchlist.list'?30000:300000;return entry.ready&&entry.checkedDay===today()&&Date.now()-(entry.checkedAt||0)<ttl;}
  function invalidateRelated(method,except) {
    const family=method.startsWith('market.finance.')?'market.finance.':method.startsWith('market.analysis.')?'market.analysis.':method.startsWith('market.funds.')?'market.funds.':method;
    // Keep displayed data, but never let an already-running request undo this invalidation.
    for(const entry of resources.values())if(entry!==except&&entry.request?.[0].startsWith(family)){entry.epoch++;entry.checkedAt=0;entry.ready=false;}
    if(family==='market.analysis.'||family==='market.funds.')AT.fundsActivity?.invalidate();
  }
  async function call(method,payload={},write=false) {
    if(typeof bridge.market!=='function')throw new Error('行情服务尚未启动，请重新打开桌面应用。');
    const work=Promise.resolve(bridge.market(method,payload)).then(result=>{if(!result?.ok){const error=new Error(errorText(result?.error));error.code=result?.error?.code;error.cancelled=!!result?.cancelled;throw error;}return result.data;});
    if(write)pendingWrites.add(work);
    try{return await work;}finally{pendingWrites.delete(work);}
  }
  function invalidateSource() {closeFinanceSearch();endFundsDrag();AT.fundsActivity?.invalidate();AT.leaderResearch?.invalidate();version++;resources.clear();sourceKey='';feedback=null;}
  function checkSource() {
    const status=AT.marketUI?.status;
    if(!status)return;
    const key=JSON.stringify([status.path,status.latestDate,status.online?.root,status.online?.enabled]);
    if(sourceKey&&sourceKey!==key){closeFinanceSearch();endFundsDrag();AT.fundsActivity?.invalidate();AT.leaderResearch?.invalidate();version++;resources.clear();feedback=null;}
    sourceKey=key;
  }
  function requestFor(page) {
    const date=draft.research.asOf?{asOf:draft.research.asOf}:{};
    if(page==='T01')return ['market.analysis.overview',date];
    if(page==='T02')return draft.finance.symbol?['market.finance.snapshot',{symbol:draft.finance.symbol}]:null;
    const state=localUI(page),paging={query:state.query.trim(),sort:state.sort,order:state.order||'desc',offset:state.offset,limit:20};
    if(page==='T03')return ['market.finance.screen',{query:draft.screen.query.trim(),industry:draft.screen.industry.trim(),page:draft.screen.page,pageSize:draft.screen.pageSize,positive:draft.screen.positive,maxPe:n(draft.screen.maxPe)>0?n(draft.screen.maxPe):null,below:draft.screen.below,scenario:{growth:n(scenario.growth),pe:n(scenario.pe),water:n(scenario.water)},sortBy:state.sort||state.defaultSort,sortOrder:state.order||state.defaultOrder}];
    if(page==='T04')return ['market.analysis.trends',{...date,...draft.trends,minReturn20:n(draft.trends.minReturn20)??0,minReturn60:n(draft.trends.minReturn60)??0,minVolumeRatio:n(draft.trends.minVolumeRatio)??0,minAmount:(n(draft.trends.minAmount)??0)*1e8,...paging}];
    if(page==='T05')return draft.funds.mode==='net'?['market.funds.sectors',{period:draft.funds.period}]:null;
    if(page==='T06')return ['market.regulatory.list',{}];
    if(page==='T07')return ['market.analysis.rebounds',{...date,minBoards:n(draft.rebounds.minBoards)??2,minGap:n(draft.rebounds.minGap)??1,maxGap:n(draft.rebounds.maxGap)??10,lookbackDays:n(draft.rebounds.lookbackDays)??60,...paging}];
    if(page==='T08')return ['market.analysis.ladder',{...date,historyDays:n(draft.ladder.historyDays)??250,board:draft.ladder.board,minHeight:n(draft.ladder.minHeight)??2,excludeST:draft.ladder.excludeST}];
    if(page==='T09')return ['market.watchlist.list',{}];
    return null;
  }
  function resource(page) {
    const request=requestFor(page);if(!request)return {data:null,busy:false,error:'',ready:true,key:''};
    const key=`${version}|${page}|${JSON.stringify(request)}`;
    if(!resources.has(key))resources.set(key,{key,request,data:null,busy:false,error:'',ready:false,serial:0,epoch:0});
    return resources.get(key);
  }
  function auxiliary(kind) {
    const request=kind==='valuation-context'?['market.finance.context',{}]:kind==='benchmark'?(draft.finance.symbol?['market.finance.benchmark',{symbol:draft.finance.symbol}]:null):(/^BK\d{4,6}$/.test(draft.funds.id)?['market.funds.history',{id:draft.funds.id,limit:Number(draft.funds.limit)}]:null);
    if(!request)return {data:null,busy:false,error:'',ready:true,key:''};
    const key=`${version}|${kind}|${JSON.stringify(request)}`;
    if(!resources.has(key))resources.set(key,{key,request,data:null,busy:false,error:'',ready:false,serial:0,epoch:0});
    return resources.get(key);
  }
  async function loadAuxiliary(kind,force=false,invalidate=true) {
    const entry=auxiliary(kind),page=['benchmark','valuation-context'].includes(kind)?'T02':'T05';
    if(!entry.request||entry.busy||fresh(entry)&&!force)return;
    if(force&&invalidate)invalidateRelated(entry.request[0],entry);
    const generation=version,serial=++entry.serial,epoch=entry.epoch;entry.busy=true;entry.error='';refresh(page);
    try{const data=await call(entry.request[0],{...entry.request[1],...(force?{refresh:true}:{})});if(generation===version&&serial===entry.serial&&epoch===entry.epoch){entry.data=data;entry.ready=true;}}
    catch(error){if(generation===version&&serial===entry.serial&&epoch===entry.epoch){entry.error=errorText(error);entry.ready=true;}}
    finally{if(generation===version&&serial===entry.serial){entry.busy=false;if(epoch===entry.epoch){entry.checkedAt=Date.now();entry.checkedDay=today();}if(auxiliary(kind).key===entry.key)refresh(page);}}
  }
  async function load(page,force=false) {
    const entry=resource(page);if(!entry.request||entry.busy||(fresh(entry)||entry.cancelled)&&!force)return;
    const reload=force||entry.ready&&!fresh(entry)&&entry.request[0].startsWith('market.analysis.');
    if(reload)invalidateRelated(entry.request[0],entry);
    const generation=version,serial=++entry.serial,epoch=entry.epoch;entry.busy=true;entry.error='';entry.cancelled=false;
    entry.cancellable=entry.request[0].startsWith('market.analysis.')||entry.request[0]==='market.finance.screen';entry.progress=null;if(entry.cancellable)scheduleProgress();
    refresh(page);
    try {
      const data=await call(entry.request[0],{...entry.request[1],...(reload&&page!=='T09'?{refresh:true}:{})});
      if(generation!==version||serial!==entry.serial||epoch!==entry.epoch)return;
      entry.data=data;entry.ready=true;
    }catch(error){if(generation===version&&serial===entry.serial&&epoch===entry.epoch){if(error.code==='cancelled'||error.cancelled)entry.cancelled=true;entry.error=entry.cancelled?'':errorText(error);entry.ready=true;}}
    finally{if(generation===version&&serial===entry.serial){entry.busy=false;if(epoch===entry.epoch){entry.checkedAt=Date.now();entry.checkedDay=today();}if(resource(page).key===entry.key)refresh(page);}}
  }
  function sameParameters(left,right) {const normalize=value=>Array.isArray(value)?value.map(normalize):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).filter(key=>key!=='refresh').sort().map(key=>[key,normalize(value[key])])):value;return JSON.stringify(normalize(left||{}))===JSON.stringify(normalize(right||{}));}
  function scheduleProgress() {if(progressTimer!==null||document.hidden||!pages.has(AT.state.page))return;progressTimer=setTimeout(async()=>{progressTimer=null;if(document.hidden||!pages.has(AT.state.page))return;const page=AT.state.page,entry=resource(page);if(!entry.busy||!entry.cancellable)return;try{const status=await call('market.research.status'),progress=(status?.jobs||[]).find(job=>job.method===entry.request[0]&&sameParameters(job.parameters,entry.request[1]));if(progress)entry.progress=progress;if(AT.state.page===page&&resource(page)===entry)refresh(page);}catch{}if(AT.state.page===page&&resource(page)===entry&&entry.busy)scheduleProgress();},750);progressTimer?.unref?.();}
  async function cancelAnalysis(id) {const entry=[...resources.values()].find(value=>value.busy&&value.progress?.id===id);if(!entry)return;try{const result=await call('market.research.cancel',{id});if(result?.cancelled)entry.cancelled=true;refresh();}catch(error){feedback={tone:'error',message:errorText(error)};refresh();}}
  function messages(entry,showFeedback=true) {
    const data=entry.data,providerErrors=Array.isArray(data?.errors)?data.errors:[],ready=data?.status==='ready'&&!data.stale&&!entry.error;
    const fundsRecovered=ready&&data.adapter==='official_chart'&&data.items?.length>0;
    const quoteRecovered=ready&&data.quoteProvider==='eastmoney_daily_valuation'&&!data.quoteStale&&n(data.marketCap)>0&&n(data.price)>0&&!!data.quoteAsOf;
    const remaining=providerErrors.filter(error=>!(fundsRecovered&&error?.provider==='eastmoney_funds_quote'||quoteRecovered&&error?.provider==='eastmoney_quote'));
    const errors=[...(entry.error?[entry.error]:[]),...remaining.map(errorText)],recovered=remaining.length<providerErrors.length&&!errors.length;
    const progress=entry.progress,ratio=n(progress?.total)>0?Math.min(100,Math.max(0,Number(progress.completed||0)/Number(progress.total)*100)):null;
    const loading=entry.busy?`<div class="analysis-tools-loading" role="status"><span>${E(progress?.stage|| (entry.request?.[0]==='market.finance.screen'?'正在整理全目录财务，首次读取可能需要几十秒…':data?'正在核验最新数据，保留当前结果…':'正在读取数据…'))}${ratio!==null?` · ${num(ratio,0)}%`:''}</span>${progress?.id?button('取消','analysis-cancel',`data-job-id="${E(progress.id)}"`):''}</div>`:entry.cancelled?'<div class="analysis-tools-notice" role="status">已取消本次分析，可点击重新读取继续。</div>':'';
    return `${loading}${showFeedback&&feedback?`<div class="analysis-tools-notice${feedback.tone==='error'?' analysis-tools-error':feedback.tone==='success'?' analysis-tools-success':''}" role="${feedback.tone==='error'?'alert':'status'}">${E(feedback.message)}</div>`:''}${errors.length?`<div class="analysis-tools-notice analysis-tools-error" role="alert"><strong>${data&&(data.status!=='unavailable'||data.cached||data.stale)?'更新未完成 · 保留已读取结果':'数据暂不可用'}</strong>${errors.map(text=>`<p>${E(text)}</p>`).join('')}</div>`:''}${recovered?'<p class="analysis-tools-hint" role="status">已通过官网备用来源更新</p>':''}${data?.stale||data?.cached?`<p class="analysis-tools-cache">${data.stale?'旧缓存':'已缓存'} · 采集于 ${timestamp(data.receivedAt||data.computedAt)}</p>`:''}${data?.quoteStale?`<p class="analysis-tools-cache">市值为旧缓存 · 数据时间 ${timestamp(data.quoteAsOf)} · 采集于 ${timestamp(data.quoteReceivedAt)}</p>`:''}`;
  }
  function sourceNote(data) {
    if(!data)return '';
    const coverage=data.coverage,notes=Array.isArray(data.warnings)?data.warnings.map(value=>typeof value==='string'?value:value.message||value.code):[];
    return `<footer class="analysis-tools-source"><span>来源 ${E(data.sourceName||data.source||data.provider||'本机记录')}</span>${data.asOf?`<span>数据日 ${timestamp(data.asOf)}</span>`:''}${data.receivedAt||data.computedAt?`<span>${data.receivedAt?'采集于':'计算于'} ${timestamp(data.receivedAt||data.computedAt)}</span>`:''}${coverage?`<span>有效 ${num(coverage.valid??coverage.eligible??coverage.total,0)}${finite(coverage.excluded)?` · 排除 ${num(coverage.excluded,0)}`:''}${finite(coverage.stale)?` · 旧日 ${num(coverage.stale,0)}`:''}</span>`:''}${data.sourceUrl?`<button type="button" data-analysis-url="${E(data.sourceUrl)}">查看来源</button>`:''}${notes.length?`<div class="analysis-tools-source-notes">${notes.map(text=>`<p>${E(text)}</p>`).join('')}</div>`:''}</footer>`;
  }
  function empty(entry,text='没有符合条件的记录') {return `<div class="analysis-tools-empty" role="status"><strong>${entry.busy?'正在读取真实数据…':entry.error?'暂时无法读取':text}</strong><p>${entry.busy?'首次整理数据可能需要一点时间。':entry.error?'可以保留当前筛选条件后重试。':'更换日期或筛选条件，或检查数据源覆盖。'}</p>${button('重新读取','refresh')}</div>`;}
  function dateControls() {return `<div class="analysis-tools-date">${field('研究日期（留空为最新）','research','asOf',draft.research.asOf,'date')} ${button('读取','apply-research','',true)} ${draft.research.asOf?button('最新交易日','latest'):''}</div>`;}
  function sortRows(items,state) {
    const key=state.sort||state.defaultSort,order=state.order||state.defaultOrder;
    return [...items].sort((a,b)=>{const text=['name','industry','status','updatedAt','eventDate','priorEndDate','reportDate','asOf'].includes(key),av=text?String(a[key]||''):n(a[key]),bv=text?String(b[key]||''):n(b[key]);if(av===null||av==='')return bv===null||bv===''?0:1;if(bv===null||bv==='')return -1;const diff=text?av.localeCompare(bv,'zh-CN'):av-bv;return order==='asc'?diff:-diff;});
  }
  function filterRows(items,page) {
    const state=localUI(page),query=state.query.trim().toLowerCase();
    return sortRows(items.filter(item=>!query||(String(item.name||'')+code(item)+String(item.industry||'')+(item.tags||[]).join(' ')+String(item.note||'')).toLowerCase().includes(query)).filter(item=>state.direction==='all'||state.direction==='up'&&n(item.mainNetInflow)>0||state.direction==='down'&&n(item.mainNetInflow)<0).filter(item=>page!=='T09'||state.status==='all'||item.status===state.status),state);
  }
  function search(page,placeholder='筛选名称 / 代码') {return `<label class="analysis-tools-search">${I('search')}<input data-analysis-query value="${E(localUI(page).query)}" placeholder="${E(placeholder)}" aria-label="${E(placeholder)}" autocomplete="off"></label>`;}
  function table(page,columns,items,entry,{remote=false}={}) {
    const state=localUI(page),filtered=remote?items:filterRows(items,page),size=15;
    if(!remote)state.offset=Math.max(0,Math.min(state.offset,Math.max(0,Math.ceil(filtered.length/size)-1)*size));
    const visible=remote?filtered:filtered.slice(state.offset,state.offset+size);
    if(!visible.length)return empty(entry);
    const effectiveSort=state.sort||state.defaultSort,effectiveOrder=state.order||state.defaultOrder;
    return `<p class="analysis-tools-sort-hint">点击列名：正序 → 倒序 → 复原${state.sort?` · ${button('复原排序','sort-reset')}`:' · 当前为默认顺序'}</p><table class="analysis-tools-table"${/^\d{4}-\d{2}-\d{2}$/.test(entry.data?.asOf||'')?` data-stock-chart-date="${E(entry.data.asOf)}"`:''}><thead><tr>${columns.map(column=>`<th class="analysis-tools-col-${column.key}" ${column.sort===false?'':`aria-sort="${effectiveSort===column.key?effectiveOrder==='asc'?'ascending':'descending':'none'}"`}>${column.sort===false?E(column.label):`<button type="button" data-analysis-sort="${column.key}" title="点击切换正序、倒序、复原">${E(column.label)} <span aria-hidden="true">${effectiveSort===column.key?effectiveOrder==='asc'?'↑':'↓':'↕'}</span></button>`}</th>`).join('')}</tr></thead><tbody>${visible.map(item=>`<tr>${columns.map(column=>`<td class="analysis-tools-col-${column.key}">${column.render?column.render(item):E(item[column.key]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table>${remote?'':`<div class="analysis-tools-pagination"><span>${state.offset+1}–${Math.min(state.offset+size,filtered.length)} / ${num(filtered.length,0)} 项</span><div>${button('上一页','previous',state.offset===0?'disabled':'')}${button('下一页','next',state.offset+size>=filtered.length?'disabled':'')}</div></div>`}`;
  }
  const stockColumn={key:'name',label:'证券',render:stockLink};
  const changeColumn={key:'changePercent',label:'涨跌幅',render:item=>`<span class="${tone(item.changePercent)}">${pct(item.changePercent)}</span>`};
  const amountColumn={key:'amount',label:'成交额',render:item=>money(item.amount)};
  const watchColumn={key:'watch',label:'跟踪',sort:false,render:item=>button('加入','watch-add',`data-symbol="${E(item.symbol)}" ${busyWrite?'disabled':''}`)};
  function selected(page) {if(!selections.has(page))selections.set(page,new Map());return selections.get(page);}
  const selectionColumn=page=>({key:'select',label:'选择',sort:false,render:item=>`<input type="checkbox" data-analysis-select="${E(item.symbol)}" aria-label="选择${E(item.name||code(item))}" ${selected(page).has(item.symbol)?'checked':''}>`});
  function bulkActions(page,items) {return `<div class="analysis-tools-bulk">${button('选择本页','select-page',items.length?'':'disabled')}${button('清除选择','select-clear',selected(page).size?'':'disabled')}<span>已选 ${selected(page).size} 只</span>${button('批量加入跟踪池','watch-bulk',selected(page).size&&!busyWrite?'':'disabled',true)}</div>`;}
  function researchPayload(item,page=AT.state.page) {
    const entry=resource(page),request=entry.request?.[1]||{},parameters=JSON.parse(JSON.stringify(request));delete parameters.offset;delete parameters.page;delete parameters.limit;delete parameters.pageSize;
    return {symbol:item.symbol,status:'watching',tags:[],note:`研究线索：${AT.pages[page]?.title||'分析工具'}`,research:{page,asOf:item.eventDate||entry.data?.asOf||null,parameters,source:String(entry.data?.sourceName||entry.data?.source||entry.data?.provider||'本机研究')}};
  }
  const presetPages=new Set(['T03','T04','T07']);
  function presetsFor(page) {return (Array.isArray(AT.state.analysisPresets?.[page])?AT.state.analysisPresets[page]:[]).filter(item=>item&&typeof item.name==='string'&&item.settings&&typeof item.settings==='object').slice(0,12);}
  function presetControls(page) {return `<div class="analysis-tools-presets"><label>常用条件名称 <input data-analysis-preset-name maxlength="40" placeholder="例如：高成交额趋势"></label>${button('保存当前条件','preset-save',busyWrite?'disabled':'')}<div>${presetsFor(page).map((item,index)=>`<span>${button(item.name,'preset-load',`data-preset-index="${index}"`)}${button('×','preset-delete',`data-preset-index="${index}" aria-label="删除${E(item.name)}" ${busyWrite?'disabled':''}`)}</span>`).join('')}</div></div>`;}
  function presetSnapshot(page) {const group=page==='T03'?'screen':page==='T04'?'trends':'rebounds';return {group,values:{...draft[group],...(page==='T03'?{page:1}:{})},asOf:draft.research.asOf,scenario:page==='T03'?{growth:scenario.growth,pe:scenario.pe,water:scenario.water,emotion:scenario.emotion}:null,query:localUI(page).query,sort:localUI(page).sort,order:localUI(page).order};}
  function savePreset(page,index,remove=false) {
    const current=presetsFor(page),name=String($('[data-analysis-preset-name]')?.value||'').trim().slice(0,40);
    if(!remove&&!name){feedback={tone:'error',message:'请先填写常用条件名称。'};refresh(page);return;}
    const next=remove?current.filter((_,i)=>i!==index):[...current.filter(item=>item.name!==name),{name,settings:presetSnapshot(page)}].slice(-12);
    return writeOperation(async()=>{const before=AT.state.analysisPresets,proposed={...(before||{}),[page]:next};AT.state.analysisPresets=proposed;
      try{if(await AT.persist?.()===false)throw new Error('常用条件保存失败，请检查本机数据目录。');feedback={tone:'success',message:remove?'已删除常用条件。':'已保存常用条件。'};}catch(error){if(AT.state.analysisPresets===proposed)AT.state.analysisPresets=before;throw error;}
    });
  }
  function applyPreset(page,index) {const value=presetsFor(page)[index]?.settings,group=page==='T03'?'screen':page==='T04'?'trends':'rebounds';if(!value||value.group!==group)return;for(const key of Object.keys(draft[group]))if(Object.hasOwn(value.values||{},key)&&typeof value.values[key]===typeof draft[group][key])draft[group][key]=value.values[key];if(page==='T03'){draft.screen.page=1;for(const key of ['growth','pe','water','emotion'])if(typeof value.scenario?.[key]==='string')scenario[key]=value.scenario[key];}else draft.research.asOf=/^\d{4}-\d{2}-\d{2}$/.test(value.asOf||'')?value.asOf:'';Object.assign(localUI(page),{offset:0,query:typeof value.query==='string'?value.query:'',sort:typeof value.sort==='string'?value.sort:null,order:['asc','desc'].includes(value.order)?value.order:null});feedback=null;refresh(page);}
  function overview(entry) {
    const counts=entry.data?.counts||{},tiles=[['T04','趋势候选',counts.trends],['T07','断板再涨停',counts.rebounds],['T08','涨停候选',counts.limitUp],['T09','跟踪证券',entry.data?.watchlistCount]];
    const links=[['T02','情绪估值','自动对照同行 PE 与行业历史，直接查看估值位置。'],['T03','跨行业估值筛选','按真实财报与行业整理候选，保留报告期与数据缺口。'],['T04','趋势龙头','比较 20 / 60 日涨幅、均线位置和成交量。'],['T05','板块资金动向','同图比较板块成交额与热度，独立查看主力净流入。'],['T06','异动监管','跟踪公开重点监控与严重异动名单，查看临近期满、延期与来源。'],['T07','断板再涨停','沿历史日线追踪连板结束与再次涨停。'],['T08','连板龙头','跟踪最高板候选、昨日高标与接力晋级，对照趋势变化。'],['T09','统一跟踪池','整理观察、计划和持仓状态，记录后续验证。']];
    return head('T01','从真实数据中整理研究线索',button('刷新','refresh'))+dateControls()+messages(entry)+`<div class="analysis-tools-metrics">${tiles.map(([page,label,value])=>`<button type="button" class="panel analysis-tools-metric" data-nav="${page}"><span>${label}</span><strong>${num(value,0)}</strong><small>${entry.data?.asOf?E(entry.data.asOf):entry.busy?'读取中':'等待数据'}</small></button>`).join('')}</div><section class="panel analysis-tools-panel"><div class="analysis-tools-section-heading"><h2>研究入口</h2><span>每个结果保留数据日期与筛选口径</span></div><div class="analysis-tools-directory">${links.map(([page,title,description])=>`<button type="button" data-nav="${page}"><strong>${title}<span>→</span></strong><p>${description}</p></button>`).join('')}</div>${sourceNote(entry.data)}</section>`;
  }
  function computeValuation(snapshot,settings=scenario) {
    const profit=n(snapshot?.ttmNetProfit),growth=n(settings.growth),pe=n(settings.pe),water=n(settings.water),emotion=n(settings.emotion);
    const manual=n(settings.actual),actual=manual!==null?manual*1e8:n(snapshot?.marketCap);
    if(profit===null)return {eligible:false,reason:'归母净利润 TTM 缺失，暂不计算估值。'};
    if(profit<=0)return {eligible:false,reason:'归母净利润 TTM 为零或亏损，此乘数模型不适用。'};
    if([growth,pe,water,emotion].some(value=>value===null||value<=0))return {eligible:false,reason:'请填写大于 0 的基准 PE 与情景系数。'};
    const base=profit*growth*pe*water,theoretical=base*emotion;
    if(!Number.isFinite(base)||!Number.isFinite(theoretical))return {eligible:false,reason:'输入超出可计算范围，请调整情景参数。'};
    return {eligible:true,profit,base,theoretical,actual:actual>0?actual:null,impliedEmotion:actual>0?actual/base:null,deviation:actual>0?(theoretical/actual-1)*100:null,manualActual:manual!==null};
  }
  function scenarioFields(compact=false) {return `<div class="analysis-tools-fields ${compact?'analysis-tools-compact-fields':''}">${field('盈利增长系数（假设）','scenario','growth',scenario.growth,'number','min="0.01" step="0.01"')}${field('基准 PE（用户假设）','scenario','pe',scenario.pe,'number','min="0.01" step="0.1" placeholder="请填写或采用行业参考"')}${field('大盘水位系数（假设）','scenario','water',scenario.water,'number','min="0.01" step="0.01"')}${field('情绪溢价系数（假设）','scenario','emotion',scenario.emotion,'number','min="0.01" step="0.01"')}</div><p class="analysis-tools-hint">1 表示不调整；增长系数 1.05 表示盈利上调 5%，没有自动预测增长年限。水位参考 = 上证指数 ÷ 3000，最低取 0.5；默认的 1 是中性假设。参考值只在点击采用后写入。</p>`;}
  function financeDetails(data) {
    if(!data)return '';
    return `<dl class="analysis-tools-facts"><div><dt>归母净利润 TTM</dt><dd>${money(data.ttmNetProfit)}</dd></div><div><dt>实际总市值</dt><dd>${money(data.marketCap)}</dd></div><div><dt>行业</dt><dd>${E(data.industry||'来源未提供')}</dd></div><div><dt>报告期</dt><dd>${timestamp(data.reportDate)}</dd></div><div><dt>公告日期</dt><dd>${timestamp(data.announcementDate)}</dd></div><div><dt>TTM 口径</dt><dd>${E(({annual:'全年归母净利润',current_ytd_plus_previous_annual_minus_previous_ytd:'本期累计 + 上年全年 − 上年同期'}[data.ttmMethod])||data.ttmMethod||'来源未提供')}</dd></div><div><dt>市值数据时间</dt><dd>${data.quoteAsOf?timestamp(data.quoteAsOf):'来源未提供'}</dd></div></dl>${data.quoteSourceUrl?`<p class="analysis-tools-hint">市值来源 ${E(({eastmoney_quote:'东方财富行情快照',eastmoney_daily_valuation:'东方财富日收盘估值'}[data.quoteProvider])||data.quoteProvider||'公开行情')} · <button type="button" class="analysis-tools-source-link" data-analysis-url="${E(data.quoteSourceUrl)}">核查市值口径</button></p>`:''}`;
  }
  function valuationResult(data) {
    const result=computeValuation(data);
    if(!result.eligible)return `<div class="analysis-tools-empty"><strong>${E(result.reason)}</strong><p>先读取股票财务，再填写估值情景。</p></div>`;
    return `<div class="analysis-tools-valuation-main"><span>情景市值</span><strong>${money(result.theoretical)}</strong><p>${finite(result.deviation)?`相对实际市值 ${pct(result.deviation)}`:'实际市值缺失，暂不计算偏离'}</p></div><dl class="analysis-tools-facts"><div><dt>情景底座</dt><dd>${money(result.base)}</dd></div><div><dt>模型隐含溢价</dt><dd>${finite(result.impliedEmotion)?num(result.impliedEmotion)+' 倍':'—'}</dd></div><div><dt>对照市值</dt><dd>${money(result.actual)}${result.manualActual?' · 手填':''}</dd></div></dl><p class="analysis-tools-hint">底座 = TTM 盈利 × 增长系数 × 基准 PE × 水位系数；情景市值 = 底座 × 情绪溢价；模型隐含溢价 = 实际市值 ÷ 底座。结果随假设变化，不是目标市值或预期收益。</p>`;
  }
  function historicalBenchmark(data) {
    const history=data?.historical;
    if(!history||n(history.medianPE)===null)return `<p class="analysis-tools-hint">${E(data?.historicalNote||'当前来源未提供长期行业 PE 分布，暂不展示历史一般值。')}</p>`;
    return `<div class="analysis-tools-historical-pe"><div><strong>行业历史参考</strong>${history.stale?'<span class="analysis-tools-badge analysis-tools-old">旧缓存</span>':''}<p>来源行业整体 PE（TTM） · ${num(history.days,0)} 个交易日</p><small>${timestamp(history.startDate)} — ${timestamp(history.endDate)}</small></div><dl><div><dt>历史中位数</dt><dd>${num(history.medianPE)} 倍</dd>${n(history.medianPE)>0?button('采用历史中位数','benchmark-adopt','data-benchmark-key="historicalMedianPE"'):''}</div><div><dt>常见区间（P25–P75）</dt><dd>${num(history.p25PE)} — ${num(history.p75PE)} 倍</dd></div><div><dt>最近行业整体 PE</dt><dd>${num(history.currentPE)} 倍</dd></div></dl><p class="analysis-tools-hint">这是行业整体 PE 的历史分布；上方是当前同行公司的 PE 分布，两者统计口径不同。历史区间仅供对照，不代表公允值。</p></div>`;
  }
  function benchmarkView() {
    const entry=auxiliary('benchmark'),data=entry.data;
    if(!entry.request)return '';
    const values=[['medianPE','行业参考 PE','中位数'],['p25PE','常见区间下沿','第 25 分位'],['p75PE','常见区间上沿','第 75 分位'],['meanPE','同行平均 PE','算术平均']];
    return `<section class="panel analysis-tools-panel analysis-tools-benchmark"><div class="analysis-tools-section-heading"><div><h2>${data?.industry?E(data.industry)+' · ':''}当前同行估值</h2><p class="analysis-tools-hint">同一数据日的同行 PE（TTM）截面</p></div>${button(entry.busy?'更新中…':'更新参考','benchmark-refresh',entry.busy?'disabled':'')}</div>${entry.error?`<p class="analysis-tools-notice analysis-tools-error" role="alert">${E(entry.error)}</p>`:''}${data?`${messages({...entry,error:''},false)}<div class="analysis-tools-benchmark-values">${values.map(([key,label,note])=>`<div><span>${label}</span><strong>${num(data[key])}<small> 倍</small></strong><p>${note}</p>${n(data[key])>0?button('采用此值','benchmark-adopt',`data-benchmark-key="${key}"`):'<small>样本不足</small>'}</div>`).join('')}</div><p class="analysis-tools-hint">有效样本 ${num(data.sampleCount,0)} / ${num(data.totalCount,0)} 家 · 数据日 ${timestamp(data.asOf)} · 当前公司 ${n(data.subjectPE)>0?num(data.subjectPE)+' 倍':'暂无正 PE'} · 排除非正 PE ${num(data.excludedNonPositiveCount,0)} 家 / 缺失 ${num(data.excludedMissingCount,0)} 家。${data.includesSubject?'样本包含当前公司。':''}这代表当前行业分布，不代表长期正常或公允估值。</p>${historicalBenchmark(data)}${sourceNote(data)}`:`<p class="analysis-tools-hint" role="status">${entry.busy?'正在读取同行估值与样本覆盖…':'行业估值尚未读取。'}</p>`}</section>`;
  }
  function valuation(entry) {
    queueMicrotask(()=>{if(AT.state.page==='T02'){loadAuxiliary('benchmark');loadAuxiliary('valuation-context');}});
    const benchmark=auxiliary('benchmark'),context=auxiliary('valuation-context');
    const insight=AT.valuationInsight?.panel({snapshot:entry.data,benchmark:benchmark.data,context:context.data,loading:{snapshot:entry.busy,benchmark:benchmark.busy,context:context.busy},error:{snapshot:entry.error,benchmark:benchmark.error,context:context.error}})||'';
    const tabs=`<nav class="analysis-tools-valuation-tabs" aria-label="估值详情">${['basis','scenario'].map(tab=>`<button type="button" class="analysis-tools-chip${valuationTab===tab?' analysis-tools-selected':''}" data-analysis-action="valuation-tab" data-valuation-tab="${tab}" aria-pressed="${valuationTab===tab}">${tab==='basis'?'数据依据':'手动情景'}</button>`).join('')}<span>自动判断无需填写参数</span></nav>`;
    const manual=`<div class="analysis-tools-two-column"><section class="panel analysis-tools-panel"><div class="analysis-tools-section-heading"><h2>情景参数</h2>${badge('用户假设')}</div><div class="analysis-tools-filter-line">${n(benchmark.data?.medianPE)>0?button('采用同行中位 PE','benchmark-adopt','data-benchmark-key="medianPE"'):''}${n(context.data?.coefficient)>0&&context.data?.status==='ready'&&!context.data?.stale&&!context.error&&!context.busy?button('采用上证水位','water-adopt'):''}</div>${scenarioFields()}${field('实际市值补充（亿元，可选）','scenario','actual',scenario.actual,'number','min="0.01" step="0.01" placeholder="留空使用来源市值"')}<p class="analysis-tools-hint">当前同行 PE 已包含当日市场定价，再乘水位会额外放大结果。手动参数仅用于敏感性对照，不影响上方自动判断。</p></section><section class="panel analysis-tools-panel"><div class="analysis-tools-section-heading"><h2>情景结果</h2>${badge('输入驱动')}</div><div data-analysis-valuation-result>${valuationResult(entry.data)}</div></section></div>`;
    return head('T02','先看同行估值结论，再核查依据与情景',`<button class="analysis-tools-button" data-nav="T03">跨行业筛选</button>`)+`<section class="panel analysis-tools-panel analysis-tools-finance-search"><form data-analysis-form="finance" class="analysis-tools-inline-form">${textField('搜索股票','financeInput','symbol',draft.financeInput,`placeholder="名称 / 代码 / 拼音首字母" maxlength="100" autocomplete="off" role="combobox" aria-autocomplete="list" aria-controls="finance-stock-options" aria-expanded="${financeSearch.open}"${financeSearch.active>=0?` aria-activedescendant="finance-stock-option-${financeSearch.active}"`:''}`)}<button class="analysis-tools-button analysis-tools-primary" type="submit">${entry.busy||benchmark.busy?'重新分析':'搜索并分析'}</button>${entry.data?stockLink(entry.data):''}</form><div data-finance-search-results>${financeSearchResults()}</div>${messages(entry)}</section>${insight}${tabs}${valuationTab==='scenario'?manual:`${benchmarkView()}<section class="panel analysis-tools-panel"><div class="analysis-tools-section-heading"><h2>财务与市值依据</h2></div>${financeDetails(entry.data)}${sourceNote(entry.data)}</section>`}`;
  }
  function financeSearchResults() {
    if(!financeSearch.open)return '';
    if(financeSearch.busy)return '<p class="analysis-tools-stock-search-note" role="status">正在查找股票…</p>';
    if(financeSearch.error)return `<p class="analysis-tools-stock-search-note" role="status">${E(financeSearch.error)}</p>`;
    if(!financeSearch.items.length)return '<p class="analysis-tools-stock-search-note" role="status">没有找到匹配股票。可换名称、代码或拼音首字母；名称检索使用设置中已扫描的行情目录。</p>';
    return `<div class="analysis-tools-stock-options" id="finance-stock-options" role="listbox" aria-label="匹配的股票">${financeSearch.items.map((item,index)=>`<button type="button" role="option" tabindex="-1" id="finance-stock-option-${index}" aria-selected="${financeSearch.active===index}" data-finance-candidate="${E(item.symbol)}"><strong>${E(item.name||item.code)}</strong><span>${E(item.code)}<small>${E(({sh:'沪市',sz:'深市',bj:'北交所'})[item.symbol.slice(0,2)]||'')}</small></span></button>`).join('')}</div><p class="analysis-tools-stock-search-note">${financeSearch.total>financeSearch.items.length?`找到 ${num(financeSearch.total,0)} 只，显示前 ${financeSearch.items.length} 只；可继续输入缩小范围。`:'点击股票直接分析，也可用 ↑ ↓ 选择、Enter 确认。'}</p>`;
  }
  function paintFinanceSearch() {
    if(AT.state.page!=='T02')return;
    const host=$('[data-finance-search-results]'),input=$('[data-analysis-group="financeInput"]');
    if(host)host.innerHTML=financeSearchResults();
    if(input){input.setAttribute('aria-expanded',String(financeSearch.open));if(financeSearch.open&&financeSearch.active>=0)input.setAttribute('aria-activedescendant',`finance-stock-option-${financeSearch.active}`);else input.removeAttribute('aria-activedescendant');}
  }
  function closeFinanceSearch() {
    clearTimeout(financeSearch.timer);financeSearch.serial++;Object.assign(financeSearch,{query:'',items:[],total:0,busy:false,error:'',open:false,active:-1});paintFinanceSearch();
  }
  function analyzeFinance(value) {
    const selected=symbol(value);if(!selected)return;
    if(draft.finance.symbol!==selected)scenario.actual='';
    closeFinanceSearch();feedback=null;draft.finance.symbol=selected;draft.financeInput=selected.slice(2);
    // The primary refresh invalidates the whole family once; sibling requests
    // belong to that same refresh and must not invalidate each other's replies.
    refresh('T02');load('T02',true);loadAuxiliary('benchmark',true,false);loadAuxiliary('valuation-context',true,false);
  }
  async function findFinanceStocks(submit=false) {
    clearTimeout(financeSearch.timer);
    const query=draft.financeInput.trim(),serial=++financeSearch.serial,generation=version;
    if(!query){closeFinanceSearch();return;}
    Object.assign(financeSearch,{query,items:[],total:0,busy:true,error:'',open:true,active:-1});paintFinanceSearch();
    try {
      const data=await call('market.search',{query,kind:'stock',offset:0,limit:8});
      if(serial!==financeSearch.serial||generation!==version||AT.state.page!=='T02'||draft.financeInput.trim()!==query)return;
      financeSearch.items=(data.items||[]).filter(item=>item.kind==='stock'&&symbol(item.symbol)).slice(0,8);financeSearch.total=n(data.total)??financeSearch.items.length;
      if(submit){const exact=financeSearch.items.filter(item=>[item.name,item.code,item.symbol].some(value=>String(value||'').toLowerCase()===query.toLowerCase()));const selected=exact.length===1?exact[0]:financeSearch.total===1?financeSearch.items[0]:null;if(selected){analyzeFinance(selected.symbol);return;}}
    }catch(error){if(serial===financeSearch.serial&&generation===version)financeSearch.error=errorText(error);}
    finally{if(serial===financeSearch.serial&&generation===version){financeSearch.busy=false;paintFinanceSearch();}}
  }
  function scheduleFinanceSearch() {
    closeFinanceSearch();if(!draft.financeInput.trim())return;
    financeSearch.open=true;financeSearch.busy=true;paintFinanceSearch();financeSearch.timer=setTimeout(()=>findFinanceStocks(),180);
  }
  function screening(entry) {
    const data=entry.data,settings=draft.screen;
    const items=(data?.items||[]).map(item=>{const actualPe=Object.hasOwn(item,'actualPe')?n(item.actualPe):n(item.ttmNetProfit)>0&&n(item.marketCap)>0?item.marketCap/item.ttmNetProfit:null;const result=computeValuation(item,{...scenario,emotion:'1',actual:''});return {...item,actualPe,scenarioGap:Object.hasOwn(item,'scenarioGap')?n(item.scenarioGap):result.eligible&&n(item.marketCap)>0?(result.base/item.marketCap-1)*100:null};});
    const columns=[stockColumn,{key:'industry',label:'行业'},{key:'ttmNetProfit',label:'TTM 归母',render:item=>money(item.ttmNetProfit)},{key:'marketCap',label:'日收盘市值',render:item=>`${money(item.marketCap)}${item.quoteAsOf?`<small class="analysis-tools-quote-date${item.quoteStale?' analysis-tools-old':''}">${item.quoteStale?'旧缓存 · ':''}${timestamp(item.quoteAsOf)}</small>`:''}`},{key:'actualPe',label:'市值 / TTM',render:item=>num(item.actualPe)},{key:'scenarioGap',label:'底座偏离',render:item=>`<span class="${tone(item.scenarioGap)}">${pct(item.scenarioGap)}</span>`},{key:'reportDate',label:'报告期',render:item=>timestamp(item.reportDate)},watchColumn];
    return head('T03','真实财务候选 · 跨行业比较保留不同盈利口径的差异',button('刷新','refresh'))+`<section class="panel analysis-tools-panel"><form data-analysis-form="screen" class="analysis-tools-inline-form">${textField('名称 / 代码','screen','query',settings.query,'placeholder="搜索证券"')}${textField('行业','screen','industry',settings.industry,'placeholder="行业名称，可留空"')}<button type="submit" class="analysis-tools-button analysis-tools-primary">筛选</button></form><div class="analysis-tools-filter-line">${chip('盈利为正','positive','true',settings.positive,'screen')}${chip('实际市值低于情景底座','below','true',settings.below,'screen')}${field('市值 / TTM 上限','screen','maxPe',settings.maxPe,'number','min="0" step="1" placeholder="不限"')}</div>${scenarioFields(true)}${presetControls('T03')}<p class="analysis-tools-hint">在完整候选中筛选、排序后分页。符合条件 ${num(data?.total,0)} 条${finite(data?.candidateTotal)?` / 目录 ${num(data.candidateTotal,0)} 条`:''}。情景底座采用用户假设；亏损或缺失 TTM 不产生市盈率与底座估值。</p>${messages(entry)}${bulkActions('T03',items)}${table('T03',[selectionColumn('T03'),...columns],items,entry,{remote:true})}<div class="analysis-tools-pagination"><span>第 ${settings.page} 页${finite(data?.total)?` · ${num(data.total,0)} 条`:''}</span><div>${button('上一页','screen-previous',settings.page<=1?'disabled':'')}${button('下一页','screen-next',!data?.hasMore?'disabled':'')}</div></div>${sourceNote(data)}</section>`;
  }
  function researchFilters(page) {
    if(page==='T04')return `<form data-analysis-form="research" class="analysis-tools-form"><div class="analysis-tools-fields">${field('20 日涨幅至少 %','trends','minReturn20',draft.trends.minReturn20,'number','step="1"')}${field('60 日涨幅至少 %','trends','minReturn60',draft.trends.minReturn60,'number','step="1"')}${field('5 日均量比至少','trends','minVolumeRatio',draft.trends.minVolumeRatio,'number','min="0" step="0.1"')}${field('成交额至少（亿元）','trends','minAmount',draft.trends.minAmount,'number','min="0" step="0.1"')}</div><div class="analysis-tools-filter-line">${chip('站上 MA20','requireAboveMA20','true',draft.trends.requireAboveMA20,'trends')}${chip('站上 MA60','requireAboveMA60','true',draft.trends.requireAboveMA60,'trends')}${chip('排除 ST','excludeST','true',draft.trends.excludeST,'trends')}<button class="analysis-tools-button analysis-tools-primary" type="submit">运行筛选</button></div></form>`;
    return `<form data-analysis-form="research" class="analysis-tools-form"><div class="analysis-tools-fields">${field('此前连板至少','rebounds','minBoards',draft.rebounds.minBoards,'number','min="2" max="10" step="1"')}${field('间隔至少（交易日）','rebounds','minGap',draft.rebounds.minGap,'number','min="1" max="10" step="1"')}${field('间隔最多（交易日）','rebounds','maxGap',draft.rebounds.maxGap,'number','min="1" max="10" step="1"')}${field('回看交易日','rebounds','lookbackDays',draft.rebounds.lookbackDays,'number','min="1" max="250" step="1"')}</div><button class="analysis-tools-button analysis-tools-primary" type="submit">运行筛选</button></form>`;
  }
  function researchView(page,entry) {
    const subtitles={T04:'真实日线趋势筛选 · 条件透明、结果可回看',T07:'历史事件筛选 · 保留此前连板、断板与再涨停日期'};
    const columns=page==='T04'?[stockColumn,changeColumn,{key:'return20',label:'20 日涨幅',render:item=>pct(item.return20)},{key:'return60',label:'60 日涨幅',render:item=>pct(item.return60)},{key:'volumeRatio5',label:'5 日均量比',render:item=>num(item.volumeRatio5)},amountColumn,watchColumn]:[stockColumn,{key:'eventDate',label:'再涨停日',render:item=>timestamp(item.eventDate)},{key:'priorEndDate',label:'此前连板结束',render:item=>timestamp(item.priorEndDate)},{key:'priorBoards',label:'此前高度',render:item=>num(item.priorBoards,0)+' 板'},{key:'gapDays',label:'间隔',render:item=>num(item.gapDays,0)+' 日'},amountColumn,watchColumn];
    const loaded=entry.data?.items?.length||0,total=entry.data?.total??loaded;
    const items=entry.data?.items||[],offset=localUI(page).offset;
    return head(page,subtitles[page],button('刷新','refresh'))+dateControls()+`<section class="panel analysis-tools-panel">${researchFilters(page)}${presetControls(page)}${messages(entry)}<div class="analysis-tools-list-heading"><span>结果 ${num(total,0)} 项${entry.data?.asOf?' · '+E(entry.data.asOf):''}</span>${search(page)}</div>${entry.data?`<p class="analysis-tools-hint">在完整候选中搜索、排序后分页。符合条件 ${num(total,0)} 项，当前读取 ${num(loaded,0)} 项。</p>`:''}${bulkActions(page,items)}${table(page,[selectionColumn(page),...columns],items,entry,{remote:true})}<div class="analysis-tools-pagination"><span>${loaded?offset+1:0}–${offset+loaded} / ${num(total,0)} 项</span><div>${button('上一页','research-previous',offset<=0?'disabled':'')}${button('下一页','research-next',offset+loaded>=total||!loaded?'disabled':'')}</div></div>${sourceNote(entry.data)}</section>`+(page==='T04'?(AT.fundsActivity?.panel({kind:'stock',items,asOf:entry.data?.asOf,contextKey:entry.key,busy:entry.busy})||''):'');
  }
  function flowSeries(items,key) {
    let cumulative=0;
    return items.map(item=>{const value=n(item[key]);if(value!==null)cumulative+=value;return {...item,value,cumulative:value===null?null:cumulative};});
  }
  function clampFundsWindow(length) {
    const count=fundsWindow.count===null?length:Math.max(Math.min(5,length),Math.min(length,Math.round(fundsWindow.count)));
    const start=Math.max(0,Math.min(Math.max(0,length-count),Math.round(fundsWindow.start)));
    fundsWindow.start=start;fundsWindow.count=count>=length?null:count;
    return {start,count,end:start+count};
  }
  function zoomFunds(length,factor,anchor=.5) {
    if(!length)return;
    const view=clampFundsWindow(length),count=Math.max(Math.min(5,length),Math.min(length,Math.round(view.count*factor)));
    fundsWindow.start=view.start+(view.count-count)*Math.max(0,Math.min(1,anchor));fundsWindow.count=count;clampFundsWindow(length);
  }
  function fundsHistory() {
    const entry=auxiliary('fund-history'),data=entry.data,rows=data?.items||[],key=entry.key;
    if(data&&data.name===data.id&&draft.funds.id===data.id&&draft.funds.name)data.name=draft.funds.name;
    if(fundsWindow.key!==key){Object.assign(fundsWindow,{key,start:0,count:null,focus:null});endFundsDrag();}
    const view=clampFundsWindow(rows.length),series=flowSeries(rows,draft.funds.field),visible=series.slice(view.start,view.end),label=flowFields.find(([key])=>key===draft.funds.field)?.[1]||'主力';
    const focus=rows.findIndex(item=>item.date===fundsWindow.focus),index=focus>=view.start&&focus<view.end?focus:view.end-1,selected=series[index];
    const controls=`<div class="analysis-tools-filter-line">${[30,60,120,250].map(value=>chip(`${value} 日`,'limit',String(value),draft.funds.limit===String(value),'funds')).join('')}</div>`;
    let chart='';
    if(visible.length){
      const w=1000,left=68,right=932,top=30,bottom=205,cTop=247,cBottom=338,step=(right-left)/visible.length;
      const dailyMax=Math.max(1,...visible.map(item=>Math.abs(item.value||0))),cumMax=Math.max(1,...visible.map(item=>Math.abs(item.cumulative||0)));
      const dailyY=value=>(top+bottom)/2-value/dailyMax*(bottom-top)/2,cumY=value=>(cTop+cBottom)/2-value/cumMax*(cBottom-cTop)/2;
      let path='',active=false;
      const bars=visible.map((item,i)=>{const x=left+(i+.5)*step;if(item.cumulative===null)active=false;else{path+=`${active?'L':'M'}${x.toFixed(2)} ${cumY(item.cumulative).toFixed(2)} `;active=true;}
        const selectedClass=item.date===selected?.date?' analysis-tools-flow-selected':'';
        if(item.value===null)return `<line class="analysis-tools-flow-missing${selectedClass}" x1="${x}" x2="${x}" y1="${dailyY(0)-3}" y2="${dailyY(0)+3}"/>`;
        return `<rect class="${item.value>=0?'analysis-tools-flow-positive':'analysis-tools-flow-negative'}${selectedClass}" x="${(x-Math.max(1,step*.65)/2).toFixed(2)}" y="${Math.min(dailyY(0),dailyY(item.value)).toFixed(2)}" width="${Math.max(1,step*.65).toFixed(2)}" height="${Math.max(1,Math.abs(dailyY(item.value)-dailyY(0))).toFixed(2)}" rx="1"><title>${E(item.date)} ${E(label)}净额 ${E(money(item.value))}</title></rect>`;
      }).join('');
      const ticks=[0,...(visible.length>2?[Math.floor((visible.length-1)/2)]:[]),visible.length-1].filter((v,i,a)=>a.indexOf(v)===i);
      const selectedX=left+(index-view.start+.5)*step;
      chart=`<div class="analysis-tools-flow-readout"><strong>${E(selected?.date||'')}</strong><span>${label}当日净额 <b class="${tone(selected?.value)}">${money(selected?.value)}</b></span><span>已载入区间累计 <b class="${tone(selected?.cumulative)}">${money(selected?.cumulative)}</b></span><span>板块涨跌 ${pct(selected?.changePercent)}</span></div><svg class="analysis-tools-flow-chart" data-analysis-flow-chart tabindex="0" viewBox="0 0 ${w} 375" role="group" aria-label="${E(data.name||draft.funds.name)}资金历史；滚轮缩放，左右键拖动，方向键逐日查看" data-start="${view.start}" data-count="${view.count}" data-total="${rows.length}"><text x="${left}" y="16">当日净额 · 亿元</text><text x="${left}" y="230">累计净额 · 亿元（从已载入首日计算）</text>${[dailyMax,0,-dailyMax].map(value=>`<line class="analysis-tools-flow-grid" x1="${left}" x2="${right}" y1="${dailyY(value)}" y2="${dailyY(value)}"/><text text-anchor="end" x="58" y="${dailyY(value)+4}">${num(value/1e8,1)}</text>`).join('')}${[cumMax,0,-cumMax].map(value=>`<line class="analysis-tools-flow-grid" x1="${left}" x2="${right}" y1="${cumY(value)}" y2="${cumY(value)}"/><text text-anchor="end" x="58" y="${cumY(value)+4}">${num(value/1e8,1)}</text>`).join('')}${bars}<path class="analysis-tools-flow-line" d="${path}"/><line class="analysis-tools-flow-cursor" x1="${selectedX}" x2="${selectedX}" y1="${top}" y2="${cBottom}"/>${ticks.map(i=>`<text x="${left+(i+.5)*step}" y="365" text-anchor="${i===0?'start':i===visible.length-1?'end':'middle'}">${E(visible[i].date)}</text>`).join('')}</svg><div class="analysis-tools-flow-controls"><span>${E(visible[0].date)} — ${E(visible.at(-1).date)} · 当前 ${visible.length} / 已载入 ${rows.length} 日</span><div>${button('−','funds-zoom-out','aria-label="缩小资金时间轴"')}${button('＋','funds-zoom-in','aria-label="放大资金时间轴"')}${button('显示全部','funds-reset',view.count===rows.length?'disabled':'')}</div></div><p class="analysis-tools-hint">滚轮缩放 · 左键 / 右键拖动 · 点击或方向键查看日期。累计从已载入首日开始，不随缩放重新计算。${series.some(item=>item.value===null)?'部分日期净额缺失，缺口留空，累计仅统计已有数值。':''}</p>`;
    }else chart=`<div class="analysis-tools-empty" role="status"><strong>${entry.busy?'正在读取板块资金历史…':draft.funds.id?'来源暂无可用资金历史':'选择下方板块查看资金变化'}</strong><p>按实际返回日期展示，不补齐缺失交易日。</p></div>`;
    return `<section class="panel analysis-tools-panel analysis-tools-funds-history"><div class="analysis-tools-section-heading"><div><h2>${E(data?.name||draft.funds.name||'板块')} · 资金时间走势</h2><p class="analysis-tools-hint">当日净额柱形与累计变化</p></div>${controls}</div><div class="analysis-tools-filter-line">${flowFields.map(([value,label])=>chip(label,'field',value,draft.funds.field===value,'funds')).join('')}${button('刷新历史','funds-refresh',!draft.funds.id||entry.busy?'disabled':'')}</div>${entry.error?`<p class="analysis-tools-notice analysis-tools-error" role="alert">${E(entry.error)}</p>`:''}${data?messages({...entry,error:''},false):''}${chart}${data&&rows.length<Number(draft.funds.limit)?`<p class="analysis-tools-hint">请求近 ${E(draft.funds.limit)} 日，来源实际提供 ${rows.length} 日。</p>`:''}${sourceNote(data)}</section>`;
  }
  function funds(entry) {
    const modes=`<div class="analysis-tools-filter-line" aria-label="资金观察口径">${chip('成交额与热度','mode','activity',draft.funds.mode==='activity','funds')}${chip('主力净流入','mode','net',draft.funds.mode==='net','funds')}</div>`;
    if(draft.funds.mode!=='net')return head('T05','通达信板块成交额与活跃度 · 按时间比较',button('刷新','refresh'))+modes+(AT.fundsActivity?.panel({kind:'sector'})||'');
    AT.fundsActivity?.suspend();
    if(!draft.funds.id){const first=entry.data?.items?.find(item=>/^BK\d{4,6}$/.test(item.id));if(first){draft.funds.id=first.id;draft.funds.name=first.name||first.id;}}
    queueMicrotask(()=>{if(AT.state.page==='T05')loadAuxiliary('fund-history');});
    const state=localUI('T05'),columns=[{key:'name',label:'板块名称',render:item=>`<button type="button" class="analysis-tools-stock${draft.funds.id===item.id?' analysis-tools-fund-current':''}" data-analysis-action="funds-select" data-fund-id="${E(item.id)}"><strong>${E(item.name)}</strong><small>${draft.funds.id===item.id?'正在查看走势':'查看资金走势'}</small></button>`},{key:'mainNetInflow',label:'主力净流入',render:item=>`<span class="${tone(item.mainNetInflow)}">${money(item.mainNetInflow)}</span>`},{key:'mainNetRatio',label:'净占比',render:item=>pct(item.mainNetRatio)},changeColumn,{key:'superLargeNetInflow',label:'超大单净额',render:item=>money(item.superLargeNetInflow)},{key:'largeNetInflow',label:'大单净额',render:item=>money(item.largeNetInflow)},{key:'asOf',label:'来源时间',render:item=>timestamp(item.asOf)}];
    return head('T05','来源按成交单量分类的资金流向',button('刷新','refresh'))+modes+fundsHistory()+`<section class="panel analysis-tools-panel"><div class="analysis-tools-list-heading"><div class="analysis-tools-filter-line">${[['today','当日'],['5d','近 5 日'],['10d','近 10 日']].map(([value,label])=>chip(label,'period',value,draft.funds.period===value,'funds')).join('')}</div>${search('T05','筛选板块名称')}</div><div class="analysis-tools-filter-line">${[['all','全部'],['up','净流入'],['down','净流出']].map(([value,label])=>chip(label,'direction',value,state.direction===value)).join('')}</div>${messages(entry)}${table('T05',columns,entry.data?.items||[],entry)}<p class="analysis-tools-hint">点击板块名称，在页面上方查看逐日走势。“主力”“大单”等为来源的成交规模分类，不推断交易者身份。</p>${sourceNote(entry.data)}</section>`;
  }
  function ladder(entry) {
    return head('T08','最高板候选与接力表现 · 对照趋势风格变化',button('刷新','refresh'))+dateControls()+messages(entry)+(AT.leaderResearch?.panel({data:entry.data,busy:entry.busy,error:entry.error,filters:draft.ladder,busyWrite})||empty(entry,'等待连板观察数据'))+sourceNote(entry.data);
  }
  function regulatory(entry) {
    return head('T06','重点监控与严重异动 · 跟踪监控期限与来源变化',button(entry.busy?'更新中…':'更新名单','refresh',entry.busy?'disabled':''))+messages({data:null,error:'',busy:false})+(AT.regulatoryWatch?.panel({data:entry.data,busy:entry.busy,error:entry.error,busyWrite})||empty(entry,'等待公开监控名单'));
  }
  function watchlist(entry) {
    const state=localUI('T09'),editing=(entry.data?.items||[]).some(item=>item.symbol===symbol(draft.watch.symbol));
    const columns=[stockColumn,{key:'status',label:'状态',render:item=>badge(statuses.find(([value])=>value===item.status)?.[1]||item.status)},{key:'tags',label:'标签',sort:false,render:item=>E((item.tags||[]).join(' · ')||'—')},{key:'note',label:'观察记录',sort:false,render:item=>`${E(item.note||'—')}${item.research?`<details class="analysis-tools-research-origin"><summary>${E(AT.pages[item.research.page]?.title||item.research.page||'研究来源')}${item.research.asOf?' · '+E(item.research.asOf):''}</summary><small>${E(item.research.source||'')}</small><pre>${E(JSON.stringify(item.research.parameters||{},null,2))}</pre></details>`:''}`},{key:'updatedAt',label:'更新于',render:item=>timestamp(item.updatedAt)},{key:'actions',label:'操作',sort:false,render:item=>`<div class="analysis-tools-row-actions">${button('编辑','watch-edit',`data-symbol="${E(item.symbol)}"`)}${pendingDelete===item.symbol?`${button('确认删除','watch-delete-confirm',`data-symbol="${E(item.symbol)}" ${busyWrite?'disabled':''}`)}${button('取消','watch-delete-cancel')}`:button('删除','watch-delete',`data-symbol="${E(item.symbol)}"`)}</div>`}];
    const editor=watchEditor?`<section class="panel analysis-tools-panel analysis-tools-watch-editor" data-watch-editor><div class="analysis-tools-section-heading"><h2>${editing?'编辑观察':'新增观察'}</h2>${button('收起编辑','watch-close')}</div><form data-analysis-form="watch" class="analysis-tools-form"><div class="analysis-tools-fields">${textField('股票代码','watch','symbol',draft.watch.symbol,'placeholder="六位代码 / sh600900" maxlength="12"')}${textField('标签（逗号分隔）','watch','tags',draft.watch.tags,'placeholder="例如：趋势，业绩" maxlength="168"')}</div><div class="analysis-tools-filter-line">${statuses.map(([value,label])=>chip(label,'status',value,draft.watch.status===value,'watch')).join('')}</div><label class="analysis-tools-field"><span>观察记录</span><textarea data-analysis-group="watch" data-analysis-field="note" rows="3" maxlength="2000" placeholder="记录观察依据、风险与下一次核查事项">${E(draft.watch.note)}</textarea></label><div class="analysis-tools-filter-line"><button type="submit" class="analysis-tools-button analysis-tools-primary" ${busyWrite?'disabled':''}>${busyWrite?'保存中…':editing?'保存更改':'加入跟踪池'}</button>${button('清空表单','watch-new')}</div></form></section>`:'';
    return head('T09','本机持久保存 · 统一整理研究线索',button(watchEditor?'新建观察':'新增 / 继续编辑','watch-open','',true))+messages(entry)+`<section class="panel analysis-tools-panel"><div class="analysis-tools-list-heading"><div class="analysis-tools-filter-line">${[['all','全部状态'],...statuses].map(([value,label])=>chip(label,'status',value,state.status===value)).join('')}</div>${search('T09','搜索名称 / 标签 / 记录')}</div><div class="analysis-tools-backup-actions">${button('导出 JSON','watch-export',busyWrite?'disabled':'')}${button('合并导入 JSON','watch-import',busyWrite?'disabled':'')}<input type="file" data-watch-import accept="application/json,.json" hidden><span>导入仅补充新证券，已有观察记录保留。</span></div>${table('T09',columns,entry.data?.items||[],entry)}${sourceNote(entry.data)}</section>${editor}`;
  }
  function render(page) {
    checkSource();const entry=resource(page);
    if(page==='T05'&&draft.funds.mode==='activity'&&(!activityCheckedAt||activityCheckedDay!==today()||Date.now()-activityCheckedAt>=300000)){if(activityCheckedAt)AT.fundsActivity?.invalidate();activityCheckedAt=Date.now();activityCheckedDay=today();}
    queueMicrotask(()=>{if(AT.state.page===page){load(page);if(resource(page).busy)scheduleProgress();}});
    return `<div class="analysis-tools" data-analysis-page="${page}">${page==='T01'?overview(entry):page==='T02'?valuation(entry):page==='T03'?screening(entry):['T04','T07'].includes(page)?researchView(page,entry):page==='T06'?regulatory(entry):page==='T05'?funds(entry):page==='T08'?ladder(entry):watchlist(entry)}</div>`;
  }
  const previousView=AT.view;
  AT.view=page=>pages.has(page)?render(page):previousView(page);
  function updateShell(page) {if(page!=='T02'){closeFinanceSearch();financeSearch.composing=false;}if(page!=='T08')AT.leaderResearch?.endDrag();if(page!=='T05')endFundsDrag();if(!['T04','T05'].includes(page))AT.fundsActivity?.suspend();if(!pages.has(page))return;const label=$('.demo-label'),status=$('.statusbar>span:first-child');if(label)label.textContent='真实数据 · 研究工具';if(status){const dot=document.createElement('i');dot.className='status-dot';status.replaceChildren(dot,document.createTextNode(' 分析工具 · 来源与数据日期见页面'))}}
  function clearWatch() {watchRevision++;draft.watch={symbol:'',note:'',tags:'',status:'watching'};pendingDelete='';}
  function refreshWatchResources() {for(const [key,entry] of resources){if(entry.request?.[0]==='market.watchlist.list'||entry.request?.[0]==='market.analysis.overview')resources.delete(key);}}
  document.addEventListener('market-watchlist-changed', () => {refreshWatchResources(); if (['T01','T09'].includes(AT.state.page)) refresh();});
  function writeOperation(work) {
    if(busyWrite)return Promise.resolve(false);busyWrite=true;feedback=null;refresh();
    const task=Promise.resolve().then(work).then(()=>true).catch(error=>{feedback={tone:'error',message:errorText(error)};return false;}).finally(()=>{busyWrite=false;refresh();});
    pendingWrites.add(task);void task.then(()=>pendingWrites.delete(task));return task;
  }
  function saveWatch(payload,addOnly=false) {
    const origin=AT.state.page,revision=watchRevision;
    return writeOperation(async()=>{
      if(addOnly){const current=await call('market.watchlist.list');if(current.items?.some(item=>item.symbol===payload.symbol)){feedback={tone:'neutral',message:'该证券已在跟踪池中，原观察记录保持不变。'};return;}const result=await call('market.watchlist.save-many',{items:[payload]});if(result?.skipped&&!result?.added){feedback={tone:'neutral',message:'该证券已在跟踪池中，原观察记录保持不变。'};return;}}
      else await call('market.watchlist.save',payload);
      refreshWatchResources();feedback={tone:'success',message:'已保存到本机跟踪池。'};if(origin==='T09'&&watchRevision===revision){clearWatch();watchEditor=false;}
    });
  }
  function removeWatch(value) {
    const revision=watchRevision;return writeOperation(async()=>{await call('market.watchlist.delete',{symbol:value});refreshWatchResources();pendingDelete='';if(watchRevision===revision&&symbol(draft.watch.symbol)===value){clearWatch();watchEditor=false;}feedback={tone:'success',message:'已从跟踪池移除。'};});
  }
  function addSelected(page) {const items=[...selected(page).values()];if(!items.length)return;return writeOperation(async()=>{const result=await call('market.watchlist.save-many',{items});refreshWatchResources();for(const item of items)if(selected(page).get(item.symbol)===item)selected(page).delete(item.symbol);feedback={tone:'success',message:`已加入 ${result.added||0} 只，保留已有 ${result.skipped||0} 只观察记录。`};});}
  function exportWatch() {return writeOperation(async()=>{const backup=await call('market.watchlist.export'),url=URL.createObjectURL(new Blob([JSON.stringify(backup)],{type:'application/json;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=`A-trade-跟踪池-${today()}.json`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);feedback={tone:'success',message:'已导出跟踪池 JSON。'};});}
  function importWatch(file) {return writeOperation(async()=>{if(!file||file.size>20*1024*1024)throw new Error('请选择不超过 20 MiB 的跟踪池 JSON。');let backup;try{backup=JSON.parse(await file.text());}catch{throw new Error('文件不是有效的 JSON，未更改跟踪池。');}const result=await call('market.watchlist.import',{backup,mode:'merge'});refreshWatchResources();feedback={tone:'success',message:`已导入 ${result.added||0} 只，保留已有 ${result.skipped||0} 只观察记录。`};});}
  function openLeaders(day) {
    if(day && !/^\d{4}-\d{2}-\d{2}$/.test(day))return;
    draft.research.asOf=day||'';localUI('T08').offset=0;feedback=null;
    AT.navigate?.('T08');
  }
  document.addEventListener('click',event=>{
    const candidate=event.target.closest('[data-finance-candidate]');
    if(AT.state.page==='T02'&&candidate?.dataset.financeCandidate){event.preventDefault();const item=financeSearch.items.find(item=>item.symbol===candidate.dataset.financeCandidate);if(financeSearch.open&&!financeSearch.busy&&financeSearch.query===draft.financeInput.trim()&&item)analyzeFinance(item.symbol);return;}
    if(financeSearch.open&&!event.target.closest('.analysis-tools-finance-search'))closeFinanceSearch();
    const leaderLink=event.target.closest('[data-leader-day]');
    if(leaderLink?.dataset.leaderDay!==undefined){event.preventDefault();event.stopImmediatePropagation?.();openLeaders(leaderLink.dataset.leaderDay);return;}
    const target=event.target.closest('[data-analysis-action],[data-analysis-chip],[data-analysis-sort],[data-analysis-day],[data-analysis-url],[data-analysis-stock-day],[data-analysis-select]');
    if(!target||target.disabled)return;
    const page=AT.state.page;if(!pages.has(page))return;
    event.preventDefault();
    const state=localUI(page);
    if(target.dataset.analysisSelect){const value=target.dataset.analysisSelect,item=resource(page).data?.items?.find(row=>row.symbol===value);if(selected(page).has(value))selected(page).delete(value);else if(item)selected(page).set(value,researchPayload(item,page));refresh(page);return;}
    if(target.dataset.analysisStockDay&&typeof AT.marketUI?.openDay==='function'){event.stopImmediatePropagation();AT.marketUI.openDay(target.dataset.stock,target.dataset.analysisStockDay);return;}
    if(target.dataset.analysisUrl){Promise.resolve(bridge.openExternal?.(target.dataset.analysisUrl)).then(result=>{if(result?.ok===false)throw new Error(errorText(result.error));}).catch(error=>{feedback={tone:'error',message:errorText(error)};refresh(page);});return;}
    if(target.dataset.analysisDay){draft.research.asOf=target.dataset.analysisDay;state.offset=0;feedback=null;refresh(page);return;}
    if(target.dataset.analysisSort){const key=target.dataset.analysisSort;if(state.sort!==key){state.sort=key;state.order='asc';}else if(state.order==='asc')state.order='desc';else{state.sort=null;state.order=null;}state.offset=0;if(page==='T03')draft.screen.page=1;refresh(page);return;}
    if(target.dataset.analysisChip){const scope=target.dataset.analysisScope,key=target.dataset.analysisChip,value=target.dataset.analysisValue,object=scope==='ui'?state:draft[scope];if(!object)return;if(scope==='funds'&&key==='mode'){endFundsDrag();AT.fundsActivity?.endDrag();}object[key]=typeof object[key]==='boolean'?!object[key]:value;if(scope==='watch')watchRevision++;if(scope==='screen')draft.screen.page=1;state.offset=0;feedback=null;refresh(page);return;}
    const action=target.dataset.analysisAction,value=target.dataset.symbol;
    if(action==='sort-reset'){state.sort=null;state.order=null;state.offset=0;if(page==='T03')draft.screen.page=1;refresh(page);return;}
    if(action==='analysis-cancel'){cancelAnalysis(Number(target.dataset.jobId));return;}
    if(action==='preset-save'&&presetPages.has(page)){savePreset(page);return;}
    if(action==='preset-load'&&presetPages.has(page)){applyPreset(page,Number(target.dataset.presetIndex));return;}
    if(action==='preset-delete'&&presetPages.has(page)){savePreset(page,Number(target.dataset.presetIndex),true);return;}
    if(action==='select-page'){for(const item of resource(page).data?.items||[])if(item.symbol)selected(page).set(item.symbol,researchPayload(item,page));refresh(page);return;}
    if(action==='select-clear'){selected(page).clear();refresh(page);return;}
    if(action==='watch-bulk'){addSelected(page);return;}
    if(action==='watch-export'){exportWatch();return;}
    if(action==='watch-import'){$('[data-watch-import]')?.click();return;}
    if(action==='benchmark-refresh'){loadAuxiliary('benchmark',true);return;}
    if(action==='valuation-tab'){if(['basis','scenario'].includes(target.dataset.valuationTab)){valuationTab=target.dataset.valuationTab;feedback=null;refresh(page);}return;}
    if(action==='water-adopt'){const entry=auxiliary('valuation-context'),value=entry.data?.coefficient;if(n(value)>0&&entry.data.status==='ready'&&!entry.data.stale&&!entry.error&&!entry.busy){scenario.water=String(Math.round(value*10000)/10000);valuationTab='scenario';feedback={tone:'success',message:`已采用 ${entry.data.asOf||''} 的上证水位 ${num(value,4)}，仅调整手动情景。`};refresh(page);}return;}
    if(action==='benchmark-adopt'){const key=target.dataset.benchmarkKey,value=auxiliary('benchmark').data?.[key];if(['medianPE','p25PE','p75PE','meanPE','historicalMedianPE'].includes(key)&&n(value)>0){scenario.pe=String(Math.round(Number(value)*100)/100);valuationTab='scenario';feedback={tone:'success',message:key==='historicalMedianPE'?'已采用行业整体 PE 的历史中位数，可继续调整情景。':'已采用同行 PE 参考，可在情景参数中继续调整。'};refresh(page);}return;}
    if(action==='funds-select'){const item=resource('T05').data?.items?.find(item=>item.id===target.dataset.fundId);if(item&&/^BK\d{4,6}$/.test(item.id)){draft.funds.id=item.id;draft.funds.name=item.name;refresh(page);queueMicrotask(()=>$('.analysis-tools-funds-history')?.scrollIntoView({block:'start',behavior:'smooth'}));}return;}
    if(action==='funds-refresh'){loadAuxiliary('fund-history',true);return;}
    if(['funds-zoom-in','funds-zoom-out','funds-reset'].includes(action)){const rows=auxiliary('fund-history').data?.items||[];if(action==='funds-reset'){fundsWindow.start=0;fundsWindow.count=null;}else zoomFunds(rows.length,action==='funds-zoom-in'?.75:1.4);refresh(page);return;}
    if(action==='refresh'){feedback=null;load(page,true);if(page==='T02'){loadAuxiliary('benchmark',true,false);loadAuxiliary('valuation-context',true,false);}if(page==='T05'&&draft.funds.mode==='net')loadAuxiliary('fund-history',true,false);if(page==='T04'||page==='T05'&&draft.funds.mode==='activity')AT.fundsActivity?.refresh();return;}
    if(action==='apply-research'){state.offset=0;feedback=null;load(page,true);return;}
    if(action==='latest'){draft.research.asOf='';state.offset=0;feedback=null;refresh(page);return;}
    if(action==='previous'||action==='next'){state.offset=Math.max(0,state.offset+(action==='next'?15:-15));refresh(page);return;}
    if(action==='research-previous'||action==='research-next'){state.offset=Math.max(0,state.offset+(action==='research-next'?20:-20));refresh(page);return;}
    if(action==='screen-previous'||action==='screen-next'){draft.screen.page=Math.max(1,draft.screen.page+(action==='screen-next'?1:-1));state.offset=0;refresh(page);return;}
    if(action==='watch-open'||action==='watch-new'){if(action==='watch-new'||watchEditor)clearWatch();else watchRevision++;watchEditor=true;feedback=null;refresh(page);queueMicrotask(()=>$('[data-watch-editor]')?.scrollIntoView({block:'start',behavior:'smooth'}));return;}
    if(action==='watch-close'){watchEditor=false;watchRevision++;refresh(page);return;}
    if(action==='watch-edit'){const item=resource('T09').data?.items?.find(row=>row.symbol===value);if(item){watchRevision++;watchEditor=true;draft.watch={symbol:item.symbol,note:item.note||'',tags:(item.tags||[]).join('，'),status:item.status};feedback=null;refresh(page);queueMicrotask(()=>$('[data-watch-editor]')?.scrollIntoView({block:'start',behavior:'smooth'}));}return;}
    if(action==='watch-delete'){pendingDelete=value;refresh(page);return;}
    if(action==='watch-delete-cancel'){pendingDelete='';refresh(page);return;}
    if(action==='watch-delete-confirm'){removeWatch(value);return;}
    if(action==='watch-add'){const item=resource(page).data?.items?.find(row=>row.symbol===value)||{symbol:value};saveWatch(researchPayload(item,page),true);}
  });
  document.addEventListener('input',event=>{
    const input=event.target,page=AT.state.page;if(!pages.has(page))return;
    if(input.matches('[data-analysis-query]')){const state=localUI(page);state.query=input.value;state.offset=0;clearTimeout(inputTimer);inputTimer=setTimeout(()=>refresh(page),180);return;}
    const key=input.dataset.analysisField,group=input.dataset.analysisGroup;if(!key||!group)return;
    if(group==='scenario'){scenario[key]=input.value;if(page==='T03')draft.screen.page=1;clearTimeout(inputTimer);inputTimer=setTimeout(()=>refresh(page),180);}
    else if(group==='financeInput'){draft.financeInput=input.value;if(event.isComposing||financeSearch.composing)closeFinanceSearch();else scheduleFinanceSearch();}
    else if(draft[group]){draft[group][key]=input.value;if(group==='watch')watchRevision++;if(group==='screen'){draft.screen.page=1;if(key==='maxPe'){clearTimeout(inputTimer);inputTimer=setTimeout(()=>refresh(page),180);}}}
  });
  document.addEventListener('submit',event=>{
    const form=event.target;if(!form.dataset.analysisForm)return;event.preventDefault();event.stopPropagation();if(!form.reportValidity())return;
    const page=AT.state.page,type=form.dataset.analysisForm;feedback=null;localUI(page).offset=0;
    if(type==='finance'){if(financeSearch.composing)return;const value=symbol(draft.financeInput);if(value)analyzeFinance(value);else if(draft.financeInput.trim())findFinanceStocks(true);else{feedback={tone:'error',message:'请输入股票名称、代码或拼音首字母。'};refresh(page);}}
    if(type==='screen'){draft.screen.page=1;refresh(page);load(page);}
    if(type==='research'){refresh(page);load(page);}
    if(type==='watch'){const value=symbol(draft.watch.symbol);if(!value){feedback={tone:'error',message:'请输入本地目录中存在的六位证券代码。'};refresh(page);return;}saveWatch({...draft.watch,symbol:value,tags:[...new Set(draft.watch.tags.split(/[,，]/).map(value=>value.trim()).filter(Boolean))]});}
  });
  document.addEventListener('change',event=>{if(event.target.matches?.('[data-watch-import]')){const file=event.target.files?.[0];event.target.value='';if(file)importWatch(file);}});
  document.addEventListener('compositionstart',event=>{if(event.target.dataset.analysisGroup==='financeInput'){financeSearch.composing=true;closeFinanceSearch();}});
  document.addEventListener('compositionend',event=>{if(event.target.dataset.analysisGroup==='financeInput'){financeSearch.composing=false;draft.financeInput=event.target.value;if(AT.state.page==='T02'){scheduleFinanceSearch();if(financeSearch.deferred)refresh('T02');}}});
  function endFundsDrag() {const drag=fundsDrag;fundsDrag=null;if(drag)try{document.documentElement?.releasePointerCapture?.(drag.pointerId);}catch{}}
  window.addEventListener?.('blur',endFundsDrag);
  window.addEventListener?.('resize',endFundsDrag);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)endFundsDrag();else if(pages.has(AT.state.page)){refresh();scheduleProgress();}});
  window.addEventListener?.('focus',()=>{if(!document.hidden&&pages.has(AT.state.page)){refresh();scheduleProgress();}});
  function chartPosition(event,chart) {const bounds=chart.getBoundingClientRect();return Math.max(0,Math.min(1,((event.clientX-bounds.left)/bounds.width-.068)/.864));}
  document.addEventListener('wheel',event=>{const chart=event.target.closest('[data-analysis-flow-chart]');if(!chart||AT.state.page!=='T05'||event.ctrlKey||event.metaKey)return;const rows=auxiliary('fund-history').data?.items||[];if(!rows.length||!event.deltaY)return;event.preventDefault();zoomFunds(rows.length,event.deltaY>0?1.25:.8,chartPosition(event,chart));refresh('T05');},{passive:false});
  document.addEventListener('pointerdown',event=>{const chart=event.target.closest('[data-analysis-flow-chart]');if(!chart||AT.state.page!=='T05'||![0,2].includes(event.button))return;const rows=auxiliary('fund-history').data?.items||[],view=clampFundsWindow(rows.length);if(!rows.length)return;event.preventDefault();chart.focus({preventScroll:true});endFundsDrag();fundsDrag={pointerId:event.pointerId,buttonMask:event.button===2?2:1,startX:event.clientX,start:view.start,count:view.count,total:rows.length,width:chart.getBoundingClientRect().width*.864,anchor:chartPosition(event,chart),moved:false};try{document.documentElement?.setPointerCapture?.(event.pointerId);}catch{}});
  document.addEventListener('pointermove',event=>{if(!fundsDrag||event.pointerId!==fundsDrag.pointerId)return;if(AT.state.page!=='T05'||!(event.buttons&fundsDrag.buttonMask)){endFundsDrag();return;}const distance=event.clientX-fundsDrag.startX;if(Math.abs(distance)<4&&!fundsDrag.moved)return;fundsDrag.moved=true;event.preventDefault();fundsWindow.start=fundsDrag.start-Math.round(distance/Math.max(1,fundsDrag.width)*fundsDrag.count);fundsWindow.count=fundsDrag.count;clampFundsWindow(fundsDrag.total);refresh('T05');});
  document.addEventListener('pointerup',event=>{if(!fundsDrag||event.pointerId!==fundsDrag.pointerId)return;const drag=fundsDrag;endFundsDrag();if(AT.state.page!=='T05')return;event.preventDefault();if(!drag.moved){const rows=auxiliary('fund-history').data?.items||[],index=Math.min(rows.length-1,drag.start+Math.floor(drag.anchor*drag.count));fundsWindow.focus=rows[index]?.date;refresh('T05');}queueMicrotask(()=>$('[data-analysis-flow-chart]')?.focus({preventScroll:true}));});
  document.addEventListener('pointercancel',endFundsDrag);
  document.addEventListener('lostpointercapture',endFundsDrag);
  document.addEventListener('contextmenu',event=>{if(event.target.closest('[data-analysis-flow-chart]'))event.preventDefault();});
  document.addEventListener('keydown',event=>{
    if(AT.state.page==='T02'&&event.target.dataset?.analysisGroup==='financeInput'){
      if(event.isComposing||financeSearch.composing||event.keyCode===229)return;
      if(event.key==='Escape'){event.preventDefault();closeFinanceSearch();return;}
      if(['ArrowDown','ArrowUp'].includes(event.key)&&financeSearch.open&&financeSearch.items.length){event.preventDefault();const count=financeSearch.items.length;financeSearch.active=financeSearch.active<0?(event.key==='ArrowDown'?0:count-1):(financeSearch.active+(event.key==='ArrowDown'?1:-1)+count)%count;paintFinanceSearch();return;}
      if(event.key==='Enter'&&financeSearch.open&&!financeSearch.busy&&financeSearch.query===draft.financeInput.trim()&&financeSearch.active>=0){event.preventDefault();analyzeFinance(financeSearch.items[financeSearch.active].symbol);return;}
    }
    if(['Enter',' '].includes(event.key)&&event.target.matches('[data-analysis-day]')){event.preventDefault();event.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}
    if(AT.state.page!=='T05'||!event.target.matches('[data-analysis-flow-chart]')||!['ArrowLeft','ArrowRight','Home','End','+','=','-','0'].includes(event.key))return;
    event.preventDefault();const rows=auxiliary('fund-history').data?.items||[],view=clampFundsWindow(rows.length);if(!rows.length)return;
    if(['+','=','-'].includes(event.key))zoomFunds(rows.length,event.key==='-'?1.25:.8);
    else if(event.key==='0'){fundsWindow.start=0;fundsWindow.count=null;}
    else{const current=rows.findIndex(item=>item.date===fundsWindow.focus),index=event.key==='Home'?0:event.key==='End'?rows.length-1:Math.max(0,Math.min(rows.length-1,(current>=0?current:view.end-1)+(event.key==='ArrowLeft'?-1:1)));fundsWindow.focus=rows[index].date;if(index<view.start)fundsWindow.start=index;if(index>=view.end)fundsWindow.start=index-view.count+1;}
    refresh('T05');queueMicrotask(()=>$('[data-analysis-flow-chart]')?.focus({preventScroll:true}));
  });
  function getAIContext() {
    const page=AT.state.page;if(!pages.has(page))return null;
    const request=requestFor(page),key=request?`${version}|${page}|${JSON.stringify(request)}`:'',entry=resources.get(key),data=entry?.data;
    const pick=(value,keys)=>Object.fromEntries(keys.filter(key=>value?.[key]!==undefined).map(key=>[key,value[key]]));
    const safeText=value=>String(value??'').replace(/(?:[a-z]:[\\/]|\\\\)[^\s<>"']+/gi,'[本地路径已省略]').slice(0,600);
    const state=localUI(page),parameters=request?.[1]||{},asOf=/^\d{4}-\d{2}-\d{2}$/.test(data?.asOf||'')?data.asOf:'';
    const requested=draft.research.asOf&&['T01','T04','T07','T08'].includes(page)?draft.research.asOf:'';
    const cutoff=requested&&asOf?requested<asOf?requested:asOf:requested||asOf;
    const stock=row=>({...pick(row,['symbol','code','name','date','close','changePercent','return20','return60','amount','volumeRatio5','board','boardHeight','height','boardStartDate','boardRunReturn','sequenceComplete','previousHeight','currentHeight','status','missingReason','eventDate','priorEndDate','priorBoards','gapDays'])});
    const bucket=value=>pick(value,['eligible','observed','promoted','broken','missing','rate']);
    const metrics=value=>({ ...pick(value,['observedCount','maxHeight','limitUpCount','multiBoardCount','leaderBreakCount']),promotion:bucket(value?.promotion?.all),firstToSecond:bucket(value?.promotion?.firstToSecond),secondToThird:bucket(value?.promotion?.secondToThird),highPromotion:bucket(value?.highBoardPromotion||value?.promotion?.high),trend:pick(value?.trend,['eligible','count','amountShare','aboveMA20Ratio','aboveMA60Ratio']),premium:pick(value?.premium,['eligible','observed','missing','mean','median'])});
    const loaded=data?.items||[],offset=Number(parameters.offset??((parameters.page||1)-1)*(parameters.pageSize||20))||0;
    const limitations=['仅使用当前页面已经载入的数据，不会额外联网；当前候选或分页结果不代表全市场所有股票。'];
    const context={pageId:page,status:entry?.busy?(data?'refreshing':'loading'):entry?.error?(data?'stale':'error'):data?'ready':'not-loaded',dataDates:[asOf].filter(Boolean),filters:{...pick(parameters,['asOf','query','board','minHeight','excludeST','historyDays','minReturn20','minReturn60','requireAboveMA20','requireAboveMA60','minVolumeRatio','minAmount','minBoards','minGap','maxGap','lookbackDays','period','industry','positive','maxPe','below']),sort:state.sort||state.defaultSort,order:state.order||state.defaultOrder},summary:data?`当前页面数据截至 ${asOf||'来源未提供统一日期'}${entry?.busy?'，正在刷新':''}。`:'当前页面尚无已载入数据。',evidence:{source:{provider:safeText(data?.provider||''),name:safeText(data?.sourceName||''),asOf,requestedAsOf:requested,computedAt:data?.computedAt||null,cached:!!data?.cached,stale:!!data?.stale||!!entry?.error},coverage:pick(data?.coverage,['total','valid','excluded','stale','notYetObserved']),pagination:{totalRows:Number.isFinite(data?.total)?data.total:null,loadedRows:loaded.length,offset,from:loaded.length?offset+1:0,to:offset+loaded.length,hasMore:!!data?.hasMore},warnings:(data?.warnings||[]).slice(0,12).map(value=>safeText(typeof value==='string'?value:value?.message||value?.code))},limitations};
    if(requested&&asOf&&asOf>requested){context.status='date-mismatch';context.summary='已载入数据晚于所选研究截止日，本次不附带候选或历史指标。';limitations.push('数据日期与当前研究日期不一致，不能用后续行情解释所选历史日。');return context;}
    if(!data){if(page==='T05'&&draft.funds.mode==='activity')limitations.push('成交额与热度由独立图表模块保存；这里只能读取当前可见图表摘要，不能据此声称拥有完整历史序列。');return context;}
    if(['T04','T07'].includes(page)) {
      context.evidence.currentRows=loaded.filter(row=>!cutoff||!(row.eventDate||row.date)||(row.eventDate||row.date)<=cutoff).slice(0,30).map(stock);
      limitations.push(`当前筛选共 ${Number.isFinite(data.total)?data.total:'未知'} 项，已载入第 ${loaded.length?offset+1:0}—${offset+loaded.length} 项；单股20/60日收益是截止日指标，不是逐股逐日行情或全量候选历史。`);
    }
    if(page==='T08'||page==='T01') {
      if(page==='T08')Object.assign(context.filters,{sort:'boardHeight_then_amount',order:'desc'});
      context.evidence.metricUnits={promotionRate:'0—1比例；乘100后为百分比',trendAmountShare:'0—1比例；成交额占比，不是净流入',trendAboveMARatio:'0—1比例',premium:'百分数，1表示1%',changePercent:'百分数，1表示1%',amount:'元',height:'板',sampleCounts:'只'};
      const all=(data.timeline||[]).filter(row=>/^\d{4}-\d{2}-\d{2}$/.test(row.date||'')&&(!cutoff||row.date<=cutoff)).sort((a,b)=>a.date.localeCompare(b.date));
      const recent=all.slice(-60),current=all.at(-1),leaders=(data.leaders||[]).filter(row=>!row.date||!cutoff||row.date<=cutoff),yesterday=data.yesterdayLeaders||[];
      context.evidence.rules=Object.fromEntries(Object.entries(pick(data.rules,['timelineScope','leaderScope','ladderScope','trendScope','promotion','trend'])).map(([key,value])=>[key,safeText(value)]));
      context.evidence.metrics=metrics(current?.metrics||data.leadership?.metrics);
      context.evidence.currentLeaders={totalRows:leaders.length,shownRows:Math.min(30,leaders.length),rows:leaders.slice(0,30).map(stock)};
      context.evidence.yesterdayLeaders={totalRows:yesterday.length,shownRows:Math.min(30,yesterday.length),rows:yesterday.slice(0,30).map(stock)};
      context.evidence.timeline={scope:'全市场日线代理指标；不受候选榜单的交易板、ST或最低高度筛选影响',loadedDays:all.length,shownDays:recent.length,from:recent[0]?.date||null,to:recent.at(-1)?.date||null,rows:recent.map(row=>({date:row.date,...metrics(row.metrics||row),leaders:(row.metrics?.leaders||[]).slice(0,8).map(stock)}))};
      context.evidence.rotations=(data.leadership?.rotationEvents||[]).filter(row=>!cutoff||row.date<=cutoff).slice(-20).map(row=>({...pick(row,['date','previousDate','previousMaxHeight','maxHeight','reason','incomplete','coverageNote']),leaders:(row.leaders||[]).slice(0,8).map(stock),previousLeaders:(row.previousLeaders||[]).slice(0,8).map(stock)}));
      context.dataDates=[...new Set([asOf,recent[0]?.date,recent.at(-1)?.date].filter(Boolean))];
      limitations.push(`历史证据已载入 ${all.length} 个实际交易日，本次仅附最近 ${recent.length} 日；缺失观察保留缺失计数，不补成0，也不把缺失算作断板。`,'连板为未复权日线和名义涨跌幅规则的近似观察；趋势成交额占比不是资金净流入；最高板候选不等于已确认龙头。');
    }
    if(page==='T05'&&draft.funds.mode==='net') {
      const historyRequest=/^BK\d{4,6}$/.test(draft.funds.id)?['market.funds.history',{id:draft.funds.id,limit:Number(draft.funds.limit)}]:null;
      const history=historyRequest?resources.get(`${version}|fund-history|${JSON.stringify(historyRequest)}`)?.data:null;
      const rows=(history?.items||[]).filter(row=>/^\d{4}-\d{2}-\d{2}$/.test(row.date||'')&&(!cutoff||row.date<=cutoff));
      context.evidence.fundsHistory={id:draft.funds.id,name:safeText(draft.funds.name),field:draft.funds.field,loadedDays:rows.length,shownDays:Math.min(rows.length,60),rows:rows.slice(-60).map(row=>pick(row,['date','mainNetInflow','superLargeNetInflow','largeNetInflow','mediumNetInflow','smallNetInflow','changePercent']))};
      limitations.push('主力、大单是来源按成交规模的分类，不推断交易者身份；仅含当前选中板块的已载入历史。');
    }
    return context;
  }
  AT.analysisTools={render,openLeaders,invalidateSource,updateShell,computeValuation,flowSeries,getAIContext,async flush(){let successful=true;while(pendingWrites.size){const results=await Promise.allSettled([...pendingWrites]);successful=successful&&results.every(result=>result.status==='fulfilled'&&result.value!==false);}return successful;}};
})();
