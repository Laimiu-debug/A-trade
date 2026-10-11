/* Local daily-bar evidence for leadership and market-style changes. */
(() => {
  const finite = value => value !== null && value !== undefined && value !== '' && typeof value !== 'boolean' && Number.isFinite(Number(value));
  const number = value => finite(value) ? Number(value) : null;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  function promotionRate(group) {
    if (!group || !(number(group.observed) > 0) || !finite(group.promoted)) return null;
    const promoted = Number(group.promoted), observed = Number(group.observed);
    return promoted >= 0 && promoted <= observed ? promoted / observed : null;
  }
  function trendRatio(trend, key) {
    if (!trend || !(number(trend.eligible) > 0) || !finite(trend[key])) return null;
    return clamp(Number(trend[key]), 0, 1);
  }
  function rowsFor(timeline) {
    return (timeline || []).filter(item => /^\d{4}-\d{2}-\d{2}$/.test(item.date || '')).map(item => {
      const metrics = item.metrics || {}, promotion = metrics.promotion || {}, trend = metrics.trend || {};
      return {date:item.date, maxHeight:(metrics.observedCount ?? item.observedCount) === 0 ? null : number(metrics.maxHeight ?? item.maxHeight), multiBoardCount:(metrics.observedCount ?? item.observedCount) === 0 ? null : number(metrics.multiBoardCount ?? item.multiBoardCount), metrics,
        promotion:promotionRate(promotion.all), highPromotion:promotionRate(promotion.high), trendShare:trendRatio(trend, 'amountShare'), trendBreadth:trendRatio(trend, 'aboveMA20Ratio')};
    });
  }
  function pathFor(rows, field, x, y) {
    let connected = false;
    return rows.map((item, index) => {
      if (!finite(item[field])) {connected = false; return '';}
      const command = connected ? 'L' : 'M'; connected = true;
      return `${command}${x(index).toFixed(2)},${y(Number(item[field])).toFixed(2)}`;
    }).filter(Boolean).join(' ');
  }
  if (typeof module === 'object' && module.exports) module.exports = {promotionRate, trendRatio, rowsFor, pathFor};
  if (typeof AT === 'undefined') return;
  const E = AT.escape;
  const n = (value, digits = 0) => finite(value) ? Number(value).toLocaleString('zh-CN', {minimumFractionDigits:digits, maximumFractionDigits:digits}) : '—';
  const pct = (value, ratio = false) => finite(value) ? `${n(Number(value) * (ratio ? 100 : 1), 1)}%` : '—';
  const money = value => finite(value) ? `${n(Number(value) / 1e8, 2)} 亿` : '—';
  const tone = value => !finite(value) || !Number(value) ? '' : Number(value) > 0 ? 'leader-research-up' : 'leader-research-down';
  const boards = [['all','全部市场'],['main','主板'],['chinext','创业板'],['star','科创板'],['bse','北交所']];
  let state = {data:null, busy:false, filters:{}}, rows = [], signature = '', start = 0, count = null, hover = null;
  let drag = null, deferred = false, suppressClickUntil = 0, suppressContextUntil = 0;
  const chip = (label, key, value, active) => `<button type="button" class="analysis-tools-chip${active ? ' analysis-tools-selected' : ''}" data-analysis-chip="${key}" data-analysis-value="${E(String(value))}" data-analysis-scope="ladder" aria-pressed="${!!active}">${E(label)}</button>`;
  const stock = (item, day) => `<button type="button" class="analysis-tools-stock" data-stock="${E(item.symbol || '')}"${day ? ` data-analysis-stock-day="${E(day)}"` : ''}><strong>${E(item.name || item.code || item.symbol || '未命名')}</strong><small>${E(item.code || String(item.symbol || '').replace(/^(sh|sz|bj)/, ''))}</small></button>`;
  const watch = item => `<button type="button" class="leader-research-watch" data-analysis-action="watch-add" data-symbol="${E(item.symbol || '')}"${state.busyWrite ? ' disabled' : ''}>跟踪</button>`;
  const observed = group => group ? `${n(group.promoted)} / ${n(group.observed)} 晋级 · 应观察 ${n(group.eligible)} · 缺失 ${n(group.missing)}` : '暂无可观察样本';
  function range() {
    const total = rows.length, size = clamp(count ?? total, Math.min(10, total), total);
    return {start:clamp(start, 0, total - size), count:size, total};
  }
  function reading(index) {
    const item = rows[index]; if (!item) return '';
    const metrics = item.metrics, promotion = metrics.promotion || {}, trend = metrics.trend || {}, premium = metrics.premium || {};
    return `<strong>${E(item.date)}</strong><span>最高 <b>${n(item.maxHeight)} 板</b> · 连板 ${n(item.multiBoardCount)} 只</span><span>昨日连板晋级 <b>${pct(item.promotion, true)}</b><small>${observed(promotion.all)}</small></span><span>趋势候选成交占比 <b>${pct(item.trendShare, true)}</b><small>候选 ${n(trend.count)} / 连续 60 日样本 ${n(trend.eligible)}</small></span><span>昨涨停今日中位表现 <b class="${tone(premium.median)}">${premium.observed > 0 ? pct(premium.median) : '—'}</b><small>可观察 ${n(premium.observed)} / ${n(premium.eligible)}</small></span>`;
  }
  function historyChart() {
    const view = range(), visible = rows.slice(view.start, view.start + view.count);
    if (!visible.length) return `<div class="leader-research-empty" role="status">${state.busy ? '正在计算真实日线的连板与趋势证据…' : '当前范围暂无历史证据。选择本地日线来源或调整研究日期。'}</div>`;
    const width = 1000, left = 66, right = 975, step = (right - left) / view.count, x = i => left + (i + .5) * step;
    const maxHeight = Math.max(2, ...visible.map(item => item.maxHeight || 0));
    const selected = rows.findIndex(item => item.date === state.data?.asOf);
    const index = hover !== null && hover >= view.start && hover < view.start + view.count ? hover : selected >= view.start && selected < view.start + view.count ? selected : view.start + view.count - 1;
    const heightY = value => 102 - value / maxHeight * 70, relayY = value => 218 - value * 70, trendY = value => 334 - value * 70;
    const bars = visible.map((item, i) => item.maxHeight === null ? '' : `<rect x="${(x(i) - Math.max(.5, step * .6) / 2).toFixed(2)}" y="${heightY(item.maxHeight).toFixed(2)}" width="${Math.max(.5, step * .6).toFixed(2)}" height="${Math.max(1, 102 - heightY(item.maxHeight)).toFixed(2)}" rx="1" fill="#7c9bed"/>`).join('');
    const grids = [[32,102,'板',maxHeight],[148,218,'%',100],[264,334,'%',100]].map(([top,bottom,unit,max]) => `<line class="leader-research-grid" x1="${left}" x2="${right}" y1="${top}" y2="${top}"/><line class="leader-research-grid" x1="${left}" x2="${right}" y1="${bottom}" y2="${bottom}"/><text x="54" y="${top + 4}" text-anchor="end">${max}${unit}</text><text x="54" y="${bottom + 4}" text-anchor="end">0</text>`).join('');
    const lines = [['promotion',relayY,'#d99850'],['highPromotion',relayY,'#b16cb2'],['trendShare',trendY,'#338fa0'],['trendBreadth',trendY,'#4e7dd6']].map(([field,y,color]) => `<path data-leader-line="${field}" d="${pathFor(visible,field,x,y)}" fill="none" stroke="${color}" stroke-width="1.8"${field==='highPromotion'||field==='trendBreadth'?' stroke-dasharray="4 3"':''} vector-effect="non-scaling-stroke"/>`).join('');
    const targets = visible.map((item,i) => `<rect class="leader-research-day" data-analysis-day="${E(item.date)}" data-leader-day-target x="${(left+i*step).toFixed(2)}" y="20" width="${step.toFixed(2)}" height="320" fill="transparent"><title>${E(item.date)} · 最高 ${n(item.maxHeight)} 板 · 晋级 ${pct(item.promotion,true)} · 点击回看当日候选</title></rect>`).join('');
    const dates = [...new Set([0, Math.floor((view.count - 1) / 2), view.count - 1])].map(i => `<text x="${x(i)}" y="361" text-anchor="${i===0?'start':i===view.count-1?'end':'middle'}">${E(visible[i].date)}</text>`).join('');
    return `<div class="leader-research-reading" data-leader-reading>${reading(index)}</div><div class="leader-research-chart" data-leader-chart tabindex="0" data-leader-start="${view.start}" data-leader-count="${view.count}" data-leader-total="${view.total}" aria-label="连板与趋势的同日历史对照，滚轮缩放，左右键拖动，方向键查看，回车读取该日候选"><svg viewBox="0 0 ${width} 375" role="img" aria-label="高度、晋级率、趋势成交占比，分栏独立纵轴，共用交易日期"><text x="${left}" y="16">最高连板高度</text><text x="${left}" y="132">昨日连板晋级率（实线） · 昨日 3 板及以上晋级率（虚线）</text><text x="${left}" y="248">趋势候选成交占比（实线） · 连续 60 日样本 MA20 之上占比（虚线）</text>${grids}${bars}${lines}${dates}<line data-leader-crosshair class="leader-research-crosshair" x1="${x(index-view.start)}" x2="${x(index-view.start)}" y1="25" y2="337"/>${targets}</svg></div><div class="leader-research-chart-footer"><span>${E(visible[0].date)} — ${E(visible.at(-1).date)} · ${view.count} / ${view.total} 日 · 滚轮缩放 · 左右键拖动</span><div><button type="button" data-leader-zoom="out" aria-label="缩小连板时间轴" ${view.count===view.total?'disabled':''}>−</button><button type="button" data-leader-zoom="in" aria-label="放大连板时间轴" ${view.count<=Math.min(10,view.total)?'disabled':''}>＋</button><button type="button" data-leader-zoom="reset" ${view.count===view.total?'disabled':''}>显示全部</button></div></div><p class="leader-research-note">历史图覆盖全市场，含当前名称 ST，与下方候选筛选独立。晋级率 = 今日继续涨停数 ÷ 昨日连板且今日可观察数；缺失不算失败，无可观察样本时曲线留空。趋势成交占比的分母为连续 60 日有成交样本的总成交额，不是资金净流入。点击图中日期回看；方向键预览，回车读取。</p>`;
  }
  function headline(metrics) {
    const promotion = metrics?.promotion || {}, trend = metrics?.trend || {}, premium = metrics?.premium || {};
    return `<div class="leader-research-metrics">${[
      ['全市场连板高度',`${n(metrics?.observedCount===0?null:metrics?.maxHeight)}<small> 板</small>`,`连板 ${n(metrics?.multiBoardCount)} 只 · 近似涨停 ${n(metrics?.limitUpCount)} 只`],
      ['昨日连板 · 今日晋级',pct(promotionRate(promotion.all),true),observed(promotion.all)],
      ['昨涨停 · 今日中位表现',premium.observed>0?pct(premium.median):'—',`可观察 ${n(premium.observed)} / ${n(premium.eligible)} · 缺失 ${n(premium.missing)}`],
      ['趋势候选 · 成交占比',pct(trendRatio(trend,'amountShare'),true),`趋势候选 ${n(trend.count)} / 连续 60 日样本 ${n(trend.eligible)}`]
    ].map(([label,value,note])=>`<div><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('')}</div>`;
  }
  function ladder(data) {
    const min = Number(state.filters.minHeight || 2), groups = (data?.groups || []).filter(group => Number(group.height) >= min), items = data?.leaders || groups.flatMap(group => group.items || []);
    const heightOf = item => Number(item.height ?? item.boardHeight) || 0;
    const max = Math.max(0,...items.map(heightOf)), leaders = items.filter(item=>heightOf(item)===max), previous = data?.yesterdayLeaders || [];
    const filters = `<div class="leader-research-filters"><div><span>市场</span>${boards.map(([value,label])=>chip(label,'board',value,(state.filters.board||'all')===value)).join('')}</div><div><span>高度</span>${[2,3,4,5].map(value=>chip(`${value} 板起`,'minHeight',value,Number(state.filters.minHeight||2)===value)).join('')}${chip('排除 ST','excludeST','true',state.filters.excludeST!==false)}</div></div>`;
    const candidate = item => `<article class="leader-research-candidate"><header>${stock(item,data.asOf)}<span class="leader-research-height">${item.sequenceComplete===false?'≥':''}${n(heightOf(item))}<small> 板</small></span></header><div class="leader-research-candidate-facts"><span>当日 <b class="${tone(item.changePercent)}">${pct(item.changePercent)}</b></span><span>成交 <b>${money(item.amount)}</b></span><span>${item.sequenceComplete===false?'观测起点':'本轮起点'} <b>${E(item.boardStartDate||'—')}</b></span><span>本轮涨幅 <b>${pct(item.boardRunReturn)}</b></span></div><footer><small>${item.sequenceComplete===false?'本轮起点前日线不足，高度按可观察部分统计':Number(item.previousHeight)>0?`昨日 ${n(item.previousHeight)} 板 → 今日 ${n(heightOf(item))} 板`:'本日进入观察'}</small>${watch(item)}</footer></article>`;
    const prior = item => `<div class="leader-research-prior" data-leader-result="${E(item.status||'missing')}">${stock(item,data.asOf)}<span><b class="leader-research-result-${E(item.status||'missing')}">${({promoted:'继续晋级',broken:'断板',missing:'数据缺失'})[item.status]||'待核实'}</b><small>${n(item.previousHeight)} 板 → ${item.status==='missing'?'未知':`${n(item.currentHeight)} 板`}</small></span><span class="${tone(item.changePercent)}">${item.status==='missing'?'—':pct(item.changePercent)}</span>${watch(item)}${item.status==='missing'?`<small class="leader-research-missing">${E(item.missingReason||'缺少同日数据，不计入晋级率与断板。')}</small>`:''}</div>`;
    return `<section class="panel leader-research-panel"><header class="leader-research-heading"><div><h2>连板候选与接力结果</h2><p>${E(data?.asOf||'等待数据')} · 点击证券回看当日日 K</p></div><span>${n(items.length)} 只符合当前筛选</span></header>${filters}<div class="leader-research-leaders"><section><h3>最高板候选 <small>${leaders.length?`${n(max)} 板 · ${leaders.length} 只`:'暂无'}</small></h3><div class="leader-research-candidates">${leaders.length?leaders.map(candidate).join(''):`<div class="leader-research-empty">${state.busy?'正在整理候选…':'当前日期和筛选范围没有符合条件的连板候选。'}</div>`}</div></section><section><h3>昨日高标 · 今日表现 <small>昨日 ≥3 板；无高标时观察最高 2 板</small></h3><div class="leader-research-previous">${previous.length?previous.map(prior).join(''):'<div class="leader-research-empty">没有可追踪的昨日高标。缺失结果会单独保留。</div>'}</div></section></div><div class="leader-research-ladder"><h3>高度梯队 <small>由高到低 · 同高度按成交额排列</small></h3>${groups.length?groups.map(group=>`<div class="leader-research-ladder-row analysis-tools-ladder-row"><div class="leader-research-ladder-height"><strong>${n(group.height)}</strong><span>板 · ${(group.items||[]).length} 只</span></div><div class="leader-research-ladder-items">${(group.items||[]).map(item=>`<div>${stock(item,data.asOf)}<span class="${tone(item.changePercent)}">${pct(item.changePercent)}</span><span>${money(item.amount)}</span>${watch(item)}</div>`).join('')}</div></div>`).join(''):'<p class="leader-research-note">当前筛选没有连板梯队；可降低高度门槛或调整日期。</p>'}</div><p class="leader-research-note">趋势候选需连续 60 日有成交、站上 MA20 和 MA60，最近 5 日的 20 日收益均为正。连板候选依据连续收盘涨幅近似识别；最高高度不代表已确认市场龙头。结合晋级、断板反馈、趋势占比一起观察，本地日线不包含封单、炸板次数和盘中回封。</p></section>`;
  }
  function rotations(data) {
    const events = (data?.leadership?.rotationEvents || []).slice(-6).reverse();
    if (!events.length) return '';
    return `<section class="panel leader-research-panel"><header class="leader-research-heading"><div><h2>高标更替线索</h2><p>记录最高梯队变化，点击日期查看当时的候选与接力反馈</p></div></header><div class="leader-research-rotations">${events.map(item=>`<button type="button" data-analysis-day="${E(item.date)}"><span>${E(item.date)}</span><strong>${item.incomplete?'观测不完整 · ':''}${E(({height_reset:'高度回落',leader_change:'最高梯队更替',leader_appeared:'连板候选出现'})[item.reason]||'梯队变化')}</strong><small>${E((item.previousLeaders||[]).map(row=>row.name||row.symbol).join('、')||'暂无')} <span aria-hidden="true">→</span> ${E((item.leaders||[]).map(row=>row.name||row.symbol).join('、')||'暂无')}</small><b>${n(item.previousMaxHeight)} → ${n(item.maxHeight)} 板</b>${item.incomplete?`<small>${E(item.coverageNote||'昨日高标数据不完整，需核验后判断更替。')}</small>`:''}</button>`).join('')}</div></section>`;
  }
  function panel(options = {}) {
    state = {...state,...options,filters:options.filters||state.filters||{}};
    rows = rowsFor(state.data?.timeline);
    const next = `${state.filters.historyDays||250}|${rows.length}|${rows[0]?.date||''}|${rows.at(-1)?.date||''}`;
    if (next !== signature) {endDrag(false);signature=next;start=0;count=null;hover=null;}
    const metrics = state.data?.leadership?.metrics || rows.find(item=>item.date===state.data?.asOf)?.metrics || {};
    return `<div class="leader-research" data-leader-research>${headline(metrics)}<section class="panel leader-research-panel"><header class="leader-research-heading"><div><h2>连板与趋势 · 同日对照</h2><p>高度看空间，晋级看接力，成交占比看趋势活跃度</p></div><div class="leader-research-periods">${[60,120,250,500,1000].map(value=>chip(`${value} 日`,'historyDays',value,Number(state.filters.historyDays||250)===value)).join('')}</div></header><div class="leader-research-cohorts">${[['firstToSecond','首板 → 2 板'],['secondToThird','2 板 → 3 板'],['high','昨日 ≥3 板 → 晋级']].map(([key,label])=>{const group=metrics.promotion?.[key];return `<div><span>${label}</span><b>${pct(promotionRate(group),true)}</b><small>${observed(group)}</small></div>`;}).join('')}</div><div data-leader-history>${historyChart()}</div></section>${ladder(state.data)}${rotations(state.data)}</div>`;
  }
  function paint() {
    if (AT.state.page!=='T08') return;
    const element = document.querySelector('[data-leader-history]'); if (!element) return;
    const focused = document.activeElement?.matches?.('[data-leader-chart]');
    element.innerHTML=historyChart();
    if (focused) element.querySelector('[data-leader-chart]')?.focus({preventScroll:true});
  }
  function endDrag(renderPending = true) {
    const previous=drag;drag=null;
    document.documentElement?.classList.remove('leader-research-dragging');
    if (previous) {if(previous.moved)suppressClickUntil=Date.now()+600;if(previous.button===2)suppressContextUntil=Date.now()+650;try{if(document.documentElement.hasPointerCapture(previous.pointerId))document.documentElement.releasePointerCapture(previous.pointerId);}catch{}}
    const pending=deferred;deferred=false;if(pending&&renderPending&&AT.state.page==='T08')setTimeout(()=>AT.render?.(),0);
  }
  function deferRender() {if(!drag||AT.state.page!=='T08')return false;deferred=true;return true;}
  function invalidate() {endDrag(false);state={data:null,busy:false,filters:{}};rows=[];signature='';start=0;count=null;hover=null;}
  function zoom(factor,anchor=.5) {
    const view=range();if(!view.total)return;
    const size=clamp(Math.round(view.count*factor),Math.min(10,view.total),view.total);
    start=clamp(Math.round(view.start+anchor*(view.count-size)),0,view.total-size);count=size;hover=null;paint();
  }
  function readAt(index) {
    const view=range();hover=clamp(index,view.start,view.start+view.count-1);
    const element=document.querySelector('[data-leader-reading]');if(element)element.innerHTML=reading(hover);
    const line=document.querySelector('[data-leader-crosshair]');if(line){const x=66+(hover-view.start+.5)/view.count*909;line.setAttribute('x1',x);line.setAttribute('x2',x);}
  }
  function chartRatio(event,chart) {const rect=chart.getBoundingClientRect();return clamp(((event.clientX-rect.left)/Math.max(1,rect.width)-.066)/.909,0,1);}
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-leader-zoom]');if(!button||button.disabled||AT.state.page!=='T08')return;
    event.preventDefault();endDrag(false);
    if(button.dataset.leaderZoom==='reset'){start=0;count=null;hover=null;paint();}else zoom(button.dataset.leaderZoom==='in'?.75:1/.75);
  });
  document.addEventListener('wheel',event=>{
    const chart=event.target.closest?.('[data-leader-chart]');if(!chart||AT.state.page!=='T08'||!event.deltaY||event.ctrlKey||event.metaKey)return;
    event.preventDefault();if(!drag)zoom(event.deltaY>0?1.2:.8,chartRatio(event,chart));
  },{passive:false});
  document.addEventListener('pointerdown',event=>{
    const chart=event.target.closest?.('[data-leader-chart]');if(!chart||AT.state.page!=='T08'||![0,2].includes(event.button)||event.pointerType==='touch')return;
    const view=range();if(!view.total)return;endDrag(false);event.preventDefault();chart.focus({preventScroll:true});
    drag={pointerId:event.pointerId,button:event.button,x:event.clientX,y:event.clientY,width:chart.getBoundingClientRect().width*.909,view,moved:false};
  });
  document.addEventListener('pointermove',event=>{
    if(drag){
      if(event.pointerId!==drag.pointerId)return;
      if(AT.state.page!=='T08'||!(event.buttons&(drag.button===0?1:2))){endDrag();return;}
      const distance=event.clientX-drag.x;
      if(!drag.moved){if(Math.hypot(distance,event.clientY-drag.y)<4)return;drag.moved=true;document.documentElement.classList.add('leader-research-dragging');try{document.documentElement.setPointerCapture(event.pointerId);}catch{}}
      event.preventDefault();event.stopPropagation?.();start=clamp(Math.round(drag.view.start-distance/Math.max(1,drag.width)*drag.view.count),0,drag.view.total-drag.view.count);hover=null;paint();return;
    }
    const chart=event.target.closest?.('[data-leader-chart]');if(!chart||AT.state.page!=='T08')return;
    const view=range();if(view.total)readAt(view.start+Math.min(view.count-1,Math.floor(chartRatio(event,chart)*view.count)));
  });
  for(const type of ['pointerup','pointercancel','lostpointercapture'])document.addEventListener(type,event=>{if(event.pointerId===drag?.pointerId)endDrag();});
  for(const type of ['blur','resize'])window.addEventListener(type,()=>endDrag());
  document.addEventListener('visibilitychange',()=>{if(document.hidden)endDrag();});
  for(const type of ['click','dblclick','auxclick'])document.addEventListener(type,event=>{
    if(Date.now()<suppressClickUntil&&(event.target.closest?.('[data-leader-chart]')||event.target===document.documentElement||event.target===document.body)){event.preventDefault();event.stopImmediatePropagation();}
  },true);
  document.addEventListener('contextmenu',event=>{if(event.target.closest?.('[data-leader-chart]')||drag?.button===2||Date.now()<suppressContextUntil){event.preventDefault();suppressContextUntil=0;}});
  document.addEventListener('keydown',event=>{
    if(!event.target.matches?.('[data-leader-chart]')||AT.state.page!=='T08'||!['ArrowLeft','ArrowRight','Home','End','Enter',' ','+','=','-','0'].includes(event.key))return;
    const view=range();if(!view.total)return;event.preventDefault();
    if(['+','=','-'].includes(event.key)){zoom(event.key==='-'?1.2:.8);return;}
    if(event.key==='0'){start=0;count=null;hover=null;paint();return;}
    if(['Enter',' '].includes(event.key)){const index=hover??view.start+view.count-1;document.querySelector(`[data-leader-day-target][data-analysis-day="${rows[index].date}"]`)?.dispatchEvent(new MouseEvent('click',{bubbles:true}));return;}
    const next=event.key==='Home'?0:event.key==='End'?view.total-1:clamp((hover??view.start+view.count-1)+(event.key==='ArrowLeft'?-1:1),0,view.total-1);
    if(next<view.start||next>=view.start+view.count){start=clamp(next<view.start?next:next-view.count+1,0,view.total-view.count);paint();}readAt(next);
  });
  AT.leaderResearch={panel,endDrag,deferRender,invalidate};
})();
