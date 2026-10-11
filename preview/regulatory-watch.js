/* Source-backed monitoring records; an expected expiry is never a release. */
(() => {
  const finite = value => value !== null && value !== undefined && value !== '' && typeof value !== 'boolean' && Number.isFinite(Number(value));
  const day = value => {if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;const parsed=new Date(`${value}T00:00:00Z`);return !Number.isNaN(parsed.valueOf())&&parsed.toISOString().slice(0,10)===value;};
  const verifyStatuses = new Set(['elapsed','unknown','unlisted']);
  const rank = {expiring:0,active:1,upcoming:2,unknown:3,elapsed:4,unlisted:5};
  const statusOf = item => item.listed === false ? 'unlisted' : Object.hasOwn(rank,item.status) ? item.status : 'unknown';
  const categoryOf = item => item.category || 'focus';
  const number = (value,digits=0) => finite(value) ? Number(value).toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
  function safeUrl(value) {
    if (!value) return '';
    try {const url = new URL(String(value));return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';} catch {return '';}
  }
  function records(items) {return (Array.isArray(items)?items:[]).filter(item => item && ['focus','severe'].includes(categoryOf(item)));}
  function countsFor(items) {
    const result = {active:0,expiring:0,upcoming:0,verify:0,total:items.length,stocks:new Set(items.map(item=>item.symbol||item.code).filter(Boolean)).size};
    for (const item of items) {const status=statusOf(item);if(['active','expiring'].includes(status))result.active++;if(status==='expiring')result.expiring++;if(status==='upcoming')result.upcoming++;if(verifyStatuses.has(status))result.verify++;}
    return result;
  }
  function deadline(item,asOf) {
    const status=statusOf(item), end=day(item.endDate)?item.endDate:'', term=item.endDateKind==='disclosed'?'来源':'预计';
    if(status==='unlisted')return {primary:'等待核验',secondary:'来源已移除，不代表监管已解除',kind:'unknown'};
    if(status==='elapsed')return {primary:`${term}期限已届`,secondary:'是否结束仍待后续披露核验',kind:'elapsed'};
    if(!end||status==='unknown')return {primary:'期限待核验',secondary:'不推算未披露的结束日期',kind:'unknown'};
    if(day(asOf)&&end===asOf)return {primary:`今日${term}截止`,secondary:'来源截止不代表已解除',kind:'due'};
    if(status==='upcoming')return {primary:'尚未开始',secondary:day(item.startDate)?`来源起始日 ${item.startDate}`:'起始日待核验',kind:'upcoming'};
    if(finite(item.remainingSessions)&&Number(item.remainingSessions)>=0)return {primary:`${number(item.remainingSessions)} 个交易日`,secondary:`不含今日 · 含${term}截止日`,kind:Number(item.remainingSessions)<=3?'due':'active'};
    if(finite(item.remainingCalendarDays)&&Number(item.remainingCalendarDays)>=0)return {primary:`${number(item.remainingCalendarDays)} 个自然日`,secondary:'交易日历未覆盖，不能当作交易日',kind:'calendar'};
    return {primary:'交易日待核验',secondary:`${term}截止 ${end}`,kind:'unknown'};
  }
  function selectRows(items,settings={}) {
    const query=String(settings.query||'').trim().toLowerCase(), status=settings.status||'all', market=settings.market||'all', category=settings.category||'all';
    const filtered=items.filter(item=>(!query||`${item.name||''} ${item.code||''} ${item.symbol||''}`.toLowerCase().includes(query))&&(market==='all'||item.market===market)&&(category==='all'||categoryOf(item)===category)&&(status==='all'||status==='active'&&['active','expiring'].includes(statusOf(item))||status==='verify'&&verifyStatuses.has(statusOf(item))||statusOf(item)===status));
    return filtered.map((item,index)=>({item,index})).sort((a,b)=>{
      let result=0;
      if(!settings.sort){result=rank[statusOf(a.item)]-rank[statusOf(b.item)];if(!result){const ad=day(a.item.endDate)?a.item.endDate:'9999',bd=day(b.item.endDate)?b.item.endDate:'9999';result=ad.localeCompare(bd);}return result||a.index-b.index;}
      const key=settings.sort,numeric=['remainingSessions','elapsedSessions','totalSessions'].includes(key);
      const av=numeric?(finite(a.item[key])?Number(a.item[key]):null):a.item[key]||null,bv=numeric?(finite(b.item[key])?Number(b.item[key]):null):b.item[key]||null;
      if(av===null||bv===null)return av===null?(bv===null?a.index-b.index:1):-1;
      result=numeric?av-bv:String(av).localeCompare(String(bv),'zh-CN');return (settings.order==='desc'?-result:result)||a.index-b.index;
    }).map(row=>row.item);
  }
  function cycleSort(settings,key) {
    if(settings.sort!==key)return {...settings,sort:key,order:'asc',page:0};
    if(settings.order==='asc')return {...settings,order:'desc',page:0};
    return {...settings,sort:null,order:null,page:0};
  }
  if(typeof module==='object'&&module.exports)module.exports={statusOf,categoryOf,safeUrl,records,countsFor,deadline,selectRows,cycleSort};
  if(typeof AT==='undefined')return;
  const E=AT.escape, labels={active:'预计期内',expiring:'临近预计截止',upcoming:'待生效',elapsed:'预计期限已届',unknown:'期限待核验',unlisted:'来源已移除'};
  let current={data:null,busy:false,error:'',busyWrite:false}, settings={query:'',status:'all',market:'all',category:'all',sort:null,order:null,page:0}, feedback='', opening=false, generation=0;
  const size=20;
  const chip=(label,kind,value,active)=>`<button type="button" data-regulatory-${kind}="${E(value)}" aria-pressed="${!!active}" class="${active?'active':''}">${E(label)}</button>`;
  const link=(label,url,extra='')=>safeUrl(url)?`<button type="button" data-regulatory-url="${E(safeUrl(url))}" ${extra}>${E(label)}</button>`:'';
  const timestamp=value=>{if(!value)return '—';const parsed=new Date(value);return Number.isNaN(parsed.valueOf())?String(value):parsed.toLocaleString('zh-CN',{hour12:false});};
  const stock=item=>`<button type="button" class="analysis-tools-stock" data-stock="${E(item.symbol||'')}"><strong>${E(item.name||item.code||'未命名证券')}${item.securityType==='fund'?'<span class="regulatory-watch-security-type">基金</span>':''}</strong><small>${E(item.code||item.symbol||'')} · ${E(({sh:'沪市',sz:'深市',bj:'北交所'})[item.market]||'市场待核验')}</small></button>`;
  function sourceNames(item) {
    const sources=Array.isArray(item.sources)?item.sources:[];
    return [...new Set([item.sourceName,...sources.map(source=>typeof source==='string'?source:source.sourceName||source.name)].filter(Boolean))].join('、')||current.data?.sourceName||'来源名单';
  }
  function sourceLinks(item) {
    const links=[],seen=new Set();
    const add=(label,url)=>{const value=safeUrl(url);if(value&&!seen.has(value)){links.push(link(label,value));seen.add(value);}};
    add('名单来源',item.sourceUrl||current.data?.sourceUrl);add('公告原文',item.noticeUrl);
    for(const source of Array.isArray(item.sources)?item.sources:[])if(source&&typeof source==='object'){add(source.sourceName||source.name||'其他来源',source.sourceUrl||source.url);add('公告原文',source.noticeUrl);}
    return links.join('');
  }
  function sourceStatus(source) {
    const label=`${source.sourceName||source.provider||'来源'} · ${source.status==='unavailable'?'暂不可用':source.stale?'旧缓存':source.status==='ready'?'已读取':'待更新'}`, style=source.status==='unavailable'||source.stale?'warning':'';
    return link(label,source.sourceUrl,`class="${style}"`)||`<span class="${style}">${E(label)}</span>`;
  }
  function sortHeading(label,key) {
    const active=settings.sort===key;
    return `<th aria-sort="${active?(settings.order==='asc'?'ascending':'descending'):'none'}"><button type="button" data-regulatory-sort="${key}" title="点击切换正序、倒序、复原">${label}<span aria-hidden="true">${active?(settings.order==='asc'?'↑':'↓'):'↕'}</span></button></th>`;
  }
  function table(items) {
    settings.page=Math.max(0,Math.min(settings.page,Math.max(0,Math.ceil(items.length/size)-1)));
    const offset=settings.page*size,visible=items.slice(offset,offset+size);
    if(!visible.length)return `<div class="regulatory-watch-empty" role="status"><strong>${current.busy?'正在读取监控与严重异动名单…':current.error||current.data?.status==='unavailable'?'暂未取得可用名单':records(current.data?.items).length?'当前筛选没有匹配记录':'本次来源未收录可显示记录'}</strong><p>${current.busy?'保留来源披露的期限与数据时间。':current.error||current.data?.status==='unavailable'?'稍后刷新，或打开来源核实。':'来源覆盖有限，空列表不代表市场中没有被监管的证券。'}</p></div>`;
    return `<table class="regulatory-watch-table"><thead><tr>${sortHeading('证券','name')}<th>记录类型 / 状态</th>${sortHeading('来源起始日','startDate')}${sortHeading('来源截止日','endDate')}${sortHeading('剩余期限','remainingSessions')}<th>来源与跟踪</th></tr></thead><tbody>${visible.map(item=>{
      const status=statusOf(item),expiry=deadline(item,current.data?.asOf),category=categoryOf(item);
      return `<tr data-regulatory-row="${E(item.id||item.symbol||'')}" data-record-status="${status}" data-record-category="${category}"><td>${stock(item)}</td><td><span class="regulatory-watch-category ${category}">${category==='focus'?'重点监控':'严重异常波动'}</span><span class="regulatory-watch-status ${status}">${status==='elapsed'&&item.endDateKind==='disclosed'?'来源期限已届':status==='active'&&item.endDateKind==='disclosed'?'监控期内':status==='expiring'&&item.endDateKind==='disclosed'?'临近期满':labels[status]}</span>${item.title?`<small class="regulatory-watch-title">${E(item.title)}</small>`:''}${category==='severe'?'<small>严重异动记录不等于已进入重点监控</small>':''}</td><td><time>${E(day(item.startDate)?item.startDate:'待核验')}</time>${finite(item.elapsedSessions)&&Number(item.elapsedSessions)>=0?`<small>已计 ${number(item.elapsedSessions)} 个交易日</small>`:''}</td><td><time>${E(day(item.endDate)?item.endDate:'未披露')}</time><small>${day(item.endDate)?item.endDateKind==='disclosed'?'来源披露截止，非解除确认':'预计截止，非解除确认':'不推算统一结束日'}</small>${item.periodChanged?`<small class="regulatory-watch-changed">期限已调整${day(item.previousEndDate)?` · 原 ${E(item.previousEndDate)}`:''}</small>`:''}</td><td><strong class="regulatory-watch-remaining ${expiry.kind}">${E(expiry.primary)}</strong><small>${E(expiry.secondary)}</small></td><td><div class="regulatory-watch-row-actions">${sourceLinks(item)}<button type="button" data-analysis-action="watch-add" data-symbol="${E(item.symbol||'')}" ${current.busyWrite?'disabled':''}>跟踪</button></div><small>${E(sourceNames(item))}</small>${item.stale?`<small class="regulatory-watch-row-stale">该来源旧缓存 · ${E(timestamp(item.receivedAt))}</small>`:''}</td></tr>`;
    }).join('')}</tbody></table><footer class="regulatory-watch-pagination"><span>${offset+1}–${offset+visible.length} / ${items.length} 条记录</span><div><button type="button" data-regulatory-page="previous" ${settings.page===0?'disabled':''}>上一页</button><button type="button" data-regulatory-page="next" ${offset+size>=items.length?'disabled':''}>下一页</button></div></footer>`;
  }
  function panel(options={}) {
    current={...current,...options};
    const data=current.data,items=records(data?.items),counts=countsFor(items),filtered=selectRows(items,settings),errors=[current.error,...(data?.errors||[]).map(value=>typeof value==='string'?value:value.message||'部分来源读取失败')].filter(Boolean);
    const partial=items.length>0&&errors.length>0;
    const statistic=value=>data&&data.status!=='unavailable'?number(value):'—';
    return `<section class="regulatory-watch" data-regulatory-panel aria-busy="${!!current.busy}"><div class="regulatory-watch-summary">${[['active','监控期内',counts.active,'含临近截止记录'],['expiring','临近期满',counts.expiring,'剩余不超过 3 个交易日'],['upcoming','待生效',counts.upcoming,'来源所列起始日尚未到'],['verify','期限待核验',counts.verify,'含预计期限已届与来源移除']].map(([status,label,value,note])=>`<button type="button" data-regulatory-status="${status}" class="${settings.status===status?'active':''}"><span>${label}</span><strong>${statistic(value)}<small> 条</small></strong><small>${note}</small></button>`).join('')}</div><section class="panel regulatory-watch-panel"><header class="regulatory-watch-heading"><div><h2>监管与严重异动记录</h2><p>观察日期 ${E(data?.asOf||'—')} · ${items.length} 条来源记录 · ${counts.stocks} 只证券</p></div><div>${link('查看来源名单',data?.sourceUrl)}</div></header>${data?.stale?`<div class="regulatory-watch-notice warning" role="status"><strong>当前显示旧缓存，期限与名单可能已变化</strong><span>采集于 ${E(timestamp(data.receivedAt))}，可打开来源核实。</span></div>`:''}${errors.length?`<div class="regulatory-watch-notice error" role="alert"><strong>${partial?'部分来源未更新，保留已读取记录':'读取未完成'}</strong>${errors.map(message=>`<p>${E(message)}</p>`).join('')}</div>`:''}${feedback?`<div class="regulatory-watch-notice error" role="alert">${E(feedback)}</div>`:''}<div class="regulatory-watch-filters"><div class="regulatory-watch-filter-row"><span>状态</span><div>${[['all','全部'],['active','监控期内'],['expiring','临近期满'],['upcoming','待生效'],['verify','期限待核验']].map(([value,label])=>chip(label,'status',value,settings.status===value)).join('')}</div><label class="regulatory-watch-search"><span>搜索</span><input type="search" data-regulatory-search value="${E(settings.query)}" placeholder="名称 / 代码" aria-label="搜索监管与严重异动证券" autocomplete="off"></label></div><div class="regulatory-watch-filter-row"><span>类型</span><div>${[['all','全部类型'],['focus','重点监控'],['severe','严重异动']].map(([value,label])=>chip(label,'category',value,settings.category===value)).join('')}</div><span class="regulatory-watch-market-label">市场</span><div>${[['all','全部市场'],['sh','沪市'],['sz','深市'],['bj','北交所']].map(([value,label])=>chip(label,'market',value,settings.market===value)).join('')}</div></div></div><div class="regulatory-watch-list-heading"><span>${filtered.length} 条匹配 · ${settings.sort?'已按所选列排序':'默认临近期满优先'} · 点击列名：正序 → 倒序 → 复原</span>${settings.sort?'<button type="button" data-regulatory-sort-reset>复原排序</button>':''}</div>${table(filtered)}<footer class="regulatory-watch-source"><p>来源 ${E(data?.sourceName||'等待来源数据')}${data?.receivedAt?` · 采集于 ${E(timestamp(data.receivedAt))}`:''}${data?.cached?' · 缓存记录':''}</p>${Array.isArray(data?.sources)&&data.sources.length?`<div class="regulatory-watch-source-status">${data.sources.map(sourceStatus).join('')}</div>`:''}<p>名单来自公开来源汇总，覆盖不等于交易所全量。重点监控与严重异常波动分别展示；预计截止、期限届满或来源移除都不代表已解除监管。</p>${(data?.warnings||[]).map(value=>`<p>${E(typeof value==='string'?value:value.message||value.code||'')}</p>`).join('')}</footer></section></section>`;
  }
  function paint() {
    if(AT.state.page!=='T06')return;
    const element=document.querySelector('[data-regulatory-panel]');if(!element)return;
    const input=document.activeElement?.matches?.('[data-regulatory-search]')?document.activeElement:null,selection=input?[input.selectionStart,input.selectionEnd]:null;
    element.outerHTML=panel();
    if(input){const next=document.querySelector('[data-regulatory-search]');next?.focus({preventScroll:true});try{if(selection&&next)next.setSelectionRange(...selection);}catch{}}
  }
  function invalidate(){generation++;current={data:null,busy:false,error:'',busyWrite:false};feedback='';opening=false;settings={...settings,page:0};}
  document.addEventListener('click',event=>{
    const target=event.target.closest?.('[data-regulatory-status],[data-regulatory-market],[data-regulatory-category],[data-regulatory-sort],[data-regulatory-sort-reset],[data-regulatory-page],[data-regulatory-url]');
    if(!target||target.disabled||AT.state.page!=='T06')return;event.preventDefault();
    const values=target.dataset;
    if(values.regulatoryUrl){
      const url=safeUrl(values.regulatoryUrl);if(!url||opening)return;
      const bridge=window.atradeDesktop;if(typeof bridge?.openExternal!=='function'){feedback='外部浏览器尚未连接，请在桌面应用中打开来源。';paint();return;}
      const requestedGeneration=generation;opening=true;feedback='';Promise.resolve().then(()=>bridge.openExternal(url)).then(result=>{if(result?.ok===false)throw new Error(result.error?.message||result.error||'来源链接打开失败');}).catch(error=>{if(requestedGeneration===generation){feedback=error.message||'来源链接打开失败';paint();}}).finally(()=>{if(requestedGeneration===generation)opening=false;});return;
    }
    if(values.regulatoryStatus){settings.status=values.regulatoryStatus;settings.page=0;}
    else if(values.regulatoryMarket){settings.market=values.regulatoryMarket;settings.page=0;}
    else if(values.regulatoryCategory){settings.category=values.regulatoryCategory;settings.page=0;}
    else if(values.regulatorySort)settings=cycleSort(settings,values.regulatorySort);
    else if(target.hasAttribute('data-regulatory-sort-reset'))settings={...settings,sort:null,order:null,page:0};
    else if(values.regulatoryPage)settings.page=Math.max(0,settings.page+(values.regulatoryPage==='next'?1:-1));
    paint();
  });
  document.addEventListener('input',event=>{if(event.target.matches?.('[data-regulatory-search]')&&AT.state.page==='T06'){settings.query=event.target.value;settings.page=0;paint();}});
  AT.regulatoryWatch={panel,invalidate};
})();
