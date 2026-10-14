/* Strategy evidence belongs to the existing native stock workspace. */
(() => {
  'use strict';
  if (!AT.isDesktop) return;
  const E = AT.escape, cache = new Map(), origins = new Map(), writes = new Set();
  const draftKey = 'atrade.stock-research.drafts.v1';
  const validSymbol = value => /^(sh|sz|bj)\d{6}$/.test(value || '');
  const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '');
  const number = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toFixed(2) : '—';
  const pct = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? (Number(value) * 100).toFixed(2) + '%' : '—';
  const phaseName = value => ({Early:'初期',Mid:'中期',Late:'后期'}[value] || value || '—');
  const confirmName = value => ({confirmed:'已确认',partial:'部分确认',unconfirmed:'未确认',risk_blocked:'风险阻止',pending:'待确认',failed:'未通过'}[value] || '—');
  const exitName = value => String(value || '').startsWith('event_exit:')?'事件退出：'+String(value).slice(11):({open:'期末持仓',stop_loss:'止损',take_profit:'止盈',time_exit:'持仓到期',end_of_data:'行情结束',trailing_stop_daily:'日线跟踪止损'}[value] || value || '—');
  const runKey = row => row.source_type + ':' + row.source_id;
  const normalizeSource = value => String(value || '').replaceAll('\\','/').replace(/\/+$/,'').toLowerCase();
  const current = entry => cache.get(entry.symbol) === entry;
  const visible = entry => current(entry) && AT.state.page === 'G01' && AT.state.stock === entry.symbol;
  const repaint = entry => { if (visible(entry)) AT.marketUI.refreshVisible(); };
  function readDrafts() {try {const value=JSON.parse(localStorage.getItem(draftKey)||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}catch{return {};}}
  function saveDraft(entry) {
    try {const drafts=readDrafts();if(entry.dirty)drafts[entry.symbol]=entry.draft;else delete drafts[entry.symbol];localStorage.setItem(draftKey,JSON.stringify(drafts));return true;}
    catch {entry.feedback='草稿暂未保存到本机，请先保存人工标注。';return false;}
  }
  async function api(path, options = {}) {
    const started = await atradeDesktop.startResearch();
    if (!started.ok) throw Error(started.error);
    const reply = await atradeDesktop.researchRequest({path, ...options});
    if (!reply.ok) throw Error(reply.error || '策略数据读取失败。');
    return reply.data;
  }
  function entryFor({symbol, version, source}) {
    const key = version + '|' + normalizeSource(source);
    let entry = cache.get(symbol);
    if (!entry || entry.key !== key) {
      const origin = origins.get(symbol), params = new URLSearchParams(origin?.query || ''), stored = readDrafts()[symbol];
      const savedDraft = stored&&stored.symbol===symbol&&typeof stored.notes==='string'&&stored.notes.length<=6000?stored:null;
      entry = {symbol,key,source,status:'empty',data:null,error:'',scan:null,scanning:false,scanError:'',
        filter:params.get('signal_strategy_id') || '',run:'',tab:'trades',draft:entry?.draft || savedDraft,dirty:entry?.dirty || !!savedDraft,
        feedback:'',saving:false,showScan:true,noteOpen:entry?.noteOpen || !!savedDraft};
      cache.set(symbol, entry);
      if (cache.size > 20) cache.delete(cache.keys().next().value);
    }
    return entry;
  }
  function ensure(entry, force = false) {
    if (!force && entry.status !== 'empty' || entry.status === 'loading') return;
    entry.status = 'loading'; entry.error = '';
    void api('/api/atrade/stocks/' + entry.symbol).then(data => {
      if (!current(entry)) return;
      if (normalizeSource(data.source) !== normalizeSource(entry.source)) throw Error('行情目录已经变化，请重新读取当前证券后查看策略数据。');
      entry.data = data; entry.status = 'ready';
      if (!entry.dirty) entry.draft = {...(data.annotation || {symbol:entry.symbol,start_date:'',stage:'Mid',trend_class:'Unknown',decision:'保留',notes:'',updated_by:'manual'})};
    }).catch(error => {if(current(entry)){entry.error=error.message;entry.status='error';}}).finally(() => repaint(entry));
  }
  function filtered(entry, rows) {
    return (rows || []).filter(row => (!entry.filter || row.strategy_id === entry.filter) && (!entry.run || runKey(row) === entry.run));
  }
  function strategyOptions(entry) {
    const names = new Map();
    for (const row of [...(entry.data?.strategies || []), ...(entry.data?.runs || []), ...(entry.scan?.strategies || [])]) names.set(row.strategy_id,row.strategy_name);
    if (entry.filter && !names.has(entry.filter)) names.set(entry.filter,'当前策略');
    return [...names].map(([id,name]) => `<option value="${E(id)}" ${entry.filter===id?'selected':''}>${E(name)}</option>`).join('');
  }
  function contextNote(entry) {
    const context = origins.get(entry.symbol);
    if (!context) return '';
    const params = new URLSearchParams(context.query || ''), day = params.get('focus_date') || params.get('signal_as_of_date');
    return `<p class="stock-research-context">来自 ${E(AT.pages[context.sourcePage]?.title || '策略研究')}${validDate(day)?' · 定位日期 '+E(day):''} · 返回可继续原页面</p>`;
  }
  function selected(entry, day) {
    const trades = filtered(entry, entry.data?.trades);
    const events = trades.flatMap(row => [row.entry_date===day?`${row.strategy_name} · 模拟买入 ${row.quantity} 股 / ${number(row.entry_price)} 元`:null,
      row.position_state==='closed'&&row.exit_date===day?`${row.strategy_name} · 模拟卖出 ${row.quantity} 股 / ${number(row.exit_price)} 元`:null]).filter(Boolean);
    const signals = filtered(entry, entry.data?.signals).filter(row=>row.signal_date===day);
    return `<strong>${E(day || '所选交易日')}</strong><span>${events.length?events.map(E).join('；'):signals.length?`${signals.length} 条已保存回测信号，详情见下表`:'该日没有已保存的回测买卖记录'}</span>`;
  }
  function legend({symbol,date}) {
    const entry = cache.get(symbol); if(!entry) return '';
    const legacy=filtered(entry,entry.data?.runs).some(row=>row.warning);
    return `<div class="stock-research-legend"><span><i class="sr-buy"></i>回测买入</span><span><i class="sr-sell"></i>回测卖出</span><span><i class="sr-signal"></i>策略信号</span><span><i class="sr-note"></i>人工启动日</span><a href="#stock-strategy-evidence">查看策略与回测记录 ↓</a>${legacy?'<strong class="stock-research-legacy">包含旧版节奏波结果，需重新回测</strong>':''}</div><div class="stock-research-selected" data-stock-research-selected>${selected(entry,date)}</div>`;
  }
  function overlay({symbol,rows,start,count,left,step,priceY}) {
    const entry = cache.get(symbol); if(!entry?.data) return '';
    const byDate = new Map(rows.slice(start,start+count).map((row,index)=>[row.date,{row,x:left+step*(index+.5)}]));
    const marks = new Map();
    const add=(day,kind,label,description,price)=>{
      const point=byDate.get(day); if(!point)return;
      const key=day+'|'+kind, mark=marks.get(key);
      if(mark){mark.descriptions.add(description);return;}
      marks.set(key,{day,kind,label,descriptions:new Set([description]),x:point.x,y:kind==='signal'?285:Math.max(36,Math.min(265,priceY(price>0?price:point.row.close)))});
    };
    for(const row of filtered(entry,entry.data.trades)){
      add(row.entry_date,'buy','买',`${row.strategy_name} · ${row.run_name} · ${row.quantity}股 / ${number(row.entry_price)}元`,row.entry_price);
      if(row.position_state==='closed')add(row.exit_date,'sell','卖',`${row.strategy_name} · ${row.exit_reason} · ${number(row.exit_price)}元`,row.exit_price);
    }
    for(const row of filtered(entry,entry.data.signals))add(row.signal_date,'signal','信',`${row.strategy_name} · 回测信号 ${row.entry_signal}`,0);
    if(entry.showScan)for(const row of filtered(entry,entry.scan?.signals))add(row.signal_date,'signal','信',`${row.strategy_name} · 默认参数扫描 ${row.entry_signal}`,0);
    let output=[...marks.values()].map(mark=>`<g class="stock-research-mark sr-${mark.kind}" data-stock-research-day="${E(mark.day)}" role="button" tabindex="0" aria-label="${E(mark.day+' '+mark.label)}"><title>${E(mark.day+' · '+[...mark.descriptions].join('；'))}</title><circle cx="${mark.x}" cy="${mark.y}" r="${mark.kind==='signal'?Math.min(7,Math.max(2,step*.3)):9}"/>${mark.kind!=='signal'||step>=20?`<text x="${mark.x}" y="${mark.y+4}" text-anchor="middle">${mark.label}</text>`:''}</g>`).join('');
    const day=entry.draft?.start_date,point=byDate.get(day);
    if(point)output+=`<g class="stock-research-start"><title>${E(day)} · ${entry.dirty?'草稿':'已保存'}人工启动日</title><path d="M${point.x} 26V274"/><text x="${Math.min(850,point.x+5)}" y="20">${entry.dirty?'草稿':'人工'}启动 ${E(day)}</text></g>`;
    return output;
  }
  const table = (heads, rows, empty) => rows.length?`<div class="stock-research-table-wrap" data-stock-kline-skip><table class="stock-research-table"><thead><tr>${heads.map(text=>`<th>${text}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`:`<p class="stock-research-empty">${empty}</p>`;
  const dateButton = day => validDate(day)?`<button type="button" class="stock-research-link" data-stock-research-day="${E(day)}">${E(day)}</button>`:'—';
  const sourceCell = row => `${E(row.strategy_name)}<small>${E(row.run_name || '')} · ${row.source_type==='report'?'导入报告':'已完成回测'}${row.warning?'<br>'+E(row.warning):''}</small>`;
  function history(entry) {
    const trades = filtered(entry,entry.data.trades), signals = filtered(entry,entry.data.signals), scanned=filtered(entry,entry.scan?.signals);
    const tabs=[['trades',`模拟成交 ${trades.length}`],['signals',`回测信号 ${signals.length}`],['scan',`本股扫描 ${scanned.length}`]];
    const controls=`<nav class="market-chart-modes" aria-label="策略记录类型">${tabs.map(([id,label])=>`<button type="button" data-stock-research-tab="${id}" class="${entry.tab===id?'active':''}" aria-pressed="${entry.tab===id}">${label}</button>`).join('')}</nav>`;
    if(entry.tab==='trades')return controls+table(['策略与来源','信号日','模拟买入日 / 价格','模拟卖出日 / 价格','数量','收益率','退出原因'],trades.map(row=>`<tr><td>${sourceCell(row)}</td><td>${dateButton(row.signal_date)}</td><td>${dateButton(row.entry_date)}<small>${number(row.entry_price)} 元</small></td><td>${row.position_state==='open'?'期末仍持仓':dateButton(row.exit_date)+'<small>'+number(row.exit_price)+' 元</small>'}</td><td>${E(row.quantity)}</td><td>${row.position_state==='open'?'未平仓':pct(row.pnl_ratio)}</td><td>${E(exitName(row.exit_reason))}</td></tr>`),'当前条件下没有该证券的已保存模拟成交。可调整筛选，或先运行策略回测。');
    if(entry.tab==='signals')return controls+table(['策略与来源','信号日','入场信号','阶段','等级','质量评分'],signals.map(row=>`<tr><td>${sourceCell(row)}</td><td>${dateButton(row.signal_date)}</td><td>${E(row.entry_signal)}</td><td>${E(row.entry_phase)}</td><td>${E(row.event_grade)}</td><td>${number(row.entry_quality_score)}</td></tr>`),'没有已保存的回测信号。信号出现不代表满足成交条件。');
    return controls+`<p class="stock-research-help">按各策略默认参数扫描最近 60 个交易日；结果用于历史信号观察，不代表账户成交，也不替代当前策略自定义参数的回测。</p>`+table(['策略','信号日','信号','阶段','等级','质量评分'],scanned.map(row=>`<tr><td>${E(row.strategy_name)}</td><td>${dateButton(row.signal_date)}</td><td>${E(row.entry_signal)}</td><td>${E(row.entry_phase)}</td><td>${E(row.event_grade)}</td><td>${number(row.entry_quality_score)}</td></tr>`),entry.scanning?'正在按真实日线扫描…':entry.scan?'本次扫描没有触发信号。':'点击“扫描本股策略”查看所有内置策略的历史触发。');
  }
  function noteForm(entry) {
    const draft=entry.draft || {},options=(items,value)=>items.map(([id,text])=>`<option value="${id}" ${id===value?'selected':''}>${text}</option>`).join('');
    return `<details class="market-chart-details stock-research-note" ${entry.noteOpen?'open':''}><summary>人工标注与研究笔记 · ${entry.dirty?'有未保存修改':entry.data.annotation?'已保存':'尚未标注'}</summary><form data-stock-research-note="${entry.symbol}"><div class="stock-research-fields"><label>人工启动日<input type="date" name="start_date" value="${E(draft.start_date || '')}"></label><label>所处阶段<select name="stage">${options([['Early','初期'],['Mid','中期'],['Late','后期']],draft.stage)}</select></label><label>趋势分类<select name="trend_class">${options([['A','A 类'],['B','B 类'],['A_B','A/B 类'],['Unknown','未分类']],draft.trend_class)}</select></label><label>观察决定<select name="decision">${options([['保留','保留'],['排除','排除']],draft.decision)}</select></label></div><label class="stock-research-note-label">研究笔记<textarea name="notes" rows="3" maxlength="6000">${E(draft.notes || '')}</textarea></label><div class="stock-research-actions"><button type="submit" class="btn primary" ${entry.saving?'disabled':''}>${entry.saving?'保存中…':'保存人工标注'}</button><button type="button" class="btn" data-stock-research-use-day>使用图中所选日期</button><span role="status" data-stock-research-save-state>${E(entry.feedback || (entry.dirty?'有未保存修改':'保存到当前工作区'))}</span></div></form></details>`;
  }
  function render(input) {
    if(!validSymbol(input.symbol))return '';
    const entry=entryFor(input);queueMicrotask(()=>ensure(entry));
    const header=`<header class="stock-research-heading"><div><h2>策略与回测数据</h2>${contextNote(entry)}</div><div class="stock-research-actions"><button type="button" class="btn" data-stock-research-refresh ${entry.status==='loading'?'disabled':''}>刷新记录</button><button type="button" class="btn primary" data-stock-research-scan ${entry.scanning?'disabled':''}>${entry.scanning?'扫描中…':'扫描本股策略'}</button><button type="button" class="btn" data-stock-research-open="backtest">策略回测</button></div></header>`;
    if(entry.status==='empty'||entry.status==='loading')return `<section class="panel stock-research-panel" id="stock-strategy-evidence" aria-busy="true">${header}<p class="stock-research-empty" role="status">正在读取本股的策略快照与回测记录…</p></section>`;
    if(entry.error)return `<section class="panel stock-research-panel" id="stock-strategy-evidence">${header}<p class="stock-research-empty" role="alert">${E(entry.error)}</p></section>`;
    const data=entry.data,signal=data.signal,runs=(data.runs || []).filter(row=>!entry.filter||row.strategy_id===entry.filter);
    const metrics=signal?[['结构阶段',signal.wyckoff_phase || signal.phase_hint || '—'],['入场质量',number(signal.entry_quality_score)],['健康评分',number(signal.health_score)],['事件评分',number(signal.event_score)],['风险评分',number(signal.risk_score)],['信号确认',confirmName(signal.confirmation_status)]]:[];
    return `<section class="panel stock-research-panel" id="stock-strategy-evidence" data-stock-research-symbol="${entry.symbol}">${header}<p class="stock-research-help">日线结构快照截至 ${E(data.as_of_date || '—')}。下方买卖记录来自保存的模拟回测，信号与成交分别列示。</p>${metrics.length?`<div class="stock-research-metrics">${metrics.map(([label,value])=>`<div><span>${label}</span><strong>${E(value)}</strong></div>`).join('')}</div>`:'<p class="stock-research-empty">当前日线不足以生成结构评分。</p>'}${signal?.wy_event_chain?.length?`<details class="market-chart-details"><summary>结构事件与判定依据（${signal.wy_event_chain.length} 项）</summary><div class="stock-research-events">${signal.wy_event_chain.map(row=>`<span>${dateButton(row.date)} · ${E(row.event)} · ${confirmName(signal.event_confirmation_map?.[row.event])}</span>`).join('')}</div></details>`:''}<div class="stock-research-filters"><label>策略<select data-stock-research-filter><option value="">全部策略</option>${strategyOptions(entry)}</select></label><label>回测来源<select data-stock-research-run><option value="">全部已保存回测 / 报告</option>${runs.map(row=>`<option value="${E(runKey(row))}" ${entry.run===runKey(row)?'selected':''}>${E(row.run_name)} · ${E(row.range.date_from)} 至 ${E(row.range.date_to)}</option>`).join('')}</select></label><label class="stock-research-check"><input type="checkbox" data-stock-research-show-scan ${entry.showScan?'checked':''}>在图上显示扫描信号</label></div>${entry.scanError?`<p role="alert">${E(entry.scanError)}</p>`:''}${history(entry)}${data.warnings.map(w=>`<p class="stock-research-help" role="status">${E(w)}</p>`).join('')}${noteForm(entry)}</section>`;
  }
  async function scan(entry) {
    if(entry.scanning)return;entry.scanning=true;entry.scanError='';entry.tab='scan';repaint(entry);
    try {const status=await api('/api/atrade/status');if(status.source_changed)throw Error('未完成任务仍使用此前的行情目录，请先完成或停止任务。');
      const data=await api('/api/backtest/scan-stock-strategies/'+entry.symbol+'?window_days=60&scan_days=60');
      if(current(entry)){entry.scan=data;entry.scan.strategies.forEach(row=>{if(row.strategy_id==='score_only_rank_v1')row.strategy_name='纯评分排名V1';});entry.scan.signals.forEach(row=>{if(row.strategy_id==='score_only_rank_v1')row.strategy_name='纯评分排名V1';});}
    }catch(error){if(current(entry))entry.scanError=error.message;}finally{entry.scanning=false;repaint(entry);}
  }
  document.addEventListener('change',event=>{
    const entry=cache.get(AT.state.stock);if(AT.state.page!=='G01'||!entry)return;
    if(event.target.matches('[data-stock-research-filter]')){entry.filter=event.target.value;entry.run='';repaint(entry);}
    if(event.target.matches('[data-stock-research-run]')){entry.run=event.target.value;repaint(entry);}
    if(event.target.matches('[data-stock-research-show-scan]')){entry.showScan=event.target.checked;repaint(entry);}
  });
  document.addEventListener('input',event=>{
    const form=event.target.closest('form[data-stock-research-note]');if(!form)return;
    const entry=cache.get(form.dataset.stockResearchNote);if(!entry?.draft)return;
    entry.draft[event.target.name]=event.target.value;entry.dirty=true;entry.noteOpen=true;entry.feedback='有未保存修改';
    saveDraft(entry);form.querySelector('[data-stock-research-save-state]').textContent=entry.feedback;
  });
  document.addEventListener('toggle',event=>{if(!event.target.matches?.('.stock-research-note'))return;const symbol=event.target.querySelector('form')?.dataset.stockResearchNote,entry=cache.get(symbol);if(entry)entry.noteOpen=event.target.open;},true);
  document.addEventListener('submit',event=>{
    const form=event.target.closest('form[data-stock-research-note]');if(!form)return;event.preventDefault();
    const entry=cache.get(form.dataset.stockResearchNote);if(!entry||entry.saving)return;
    if(!validDate(entry.draft.start_date)){entry.feedback='请先选择人工启动日。';repaint(entry);return;}
    const payload={...entry.draft,symbol:entry.symbol,updated_by:'manual'};
    entry.saving=true;entry.feedback='';repaint(entry);
    const write=api('/api/atrade/stocks/'+entry.symbol+'/annotation',{method:'PUT',body:payload});writes.add(write);
    void write.then(data=>{
      entry.data.annotation=data.annotation;
      if(JSON.stringify({...entry.draft,symbol:entry.symbol,updated_by:'manual'})===JSON.stringify(payload)){
        entry.draft={...data.annotation};entry.dirty=false;entry.feedback='已保存';
      }else entry.feedback='已保存上一版，当前仍有未保存修改';
      saveDraft(entry);
    }).catch(error=>{entry.feedback=error.message;}).finally(()=>{writes.delete(write);entry.saving=false;repaint(entry);});
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-stock-research-day],[data-stock-research-tab],[data-stock-research-refresh],[data-stock-research-scan],[data-stock-research-use-day],[data-stock-research-open]');
    if(!button||button.disabled||AT.state.page!=='G01')return;event.preventDefault();
    const entry=cache.get(AT.state.stock);if(!entry)return;
    if(button.dataset.stockResearchDay){AT.marketUI.openDay(entry.symbol,button.dataset.stockResearchDay);return;}
    if(button.dataset.stockResearchTab){entry.tab=button.dataset.stockResearchTab;repaint(entry);return;}
    if(button.hasAttribute('data-stock-research-refresh')){ensure(entry,true);repaint(entry);}
    if(button.hasAttribute('data-stock-research-scan'))void scan(entry);
    if(button.hasAttribute('data-stock-research-use-day')){const day=document.querySelector('[data-market-date]')?.value;if(validDate(day)){entry.draft.start_date=day;entry.dirty=true;saveDraft(entry);repaint(entry);}}
    if(button.hasAttribute('data-stock-research-open'))AT.navigate('R03');
  });
  document.addEventListener('keydown',event=>{const el=event.target.closest('[data-stock-research-day]');if(el?.tagName.toLowerCase()==='g'&&['Enter',' '].includes(event.key)){event.preventDefault();event.stopPropagation();el.dispatchEvent(new MouseEvent('click',{bubbles:true}));}},true);
  AT.stockResearch={render,overlay,legend,async flush(){const completed=await Promise.allSettled([...writes]);return completed.every(row=>row.status==='fulfilled')&&[...cache.values()].every(entry=>!entry.dirty||saveDraft(entry));},selected(symbol,day){const entry=cache.get(symbol);const target=document.querySelector('[data-stock-research-selected]');if(entry&&target)target.innerHTML=selected(entry,day);},
    open(symbol,context={}){if(!validSymbol(symbol))return false;origins.set(symbol,context);const entry=cache.get(symbol);if(entry)entry.filter=new URLSearchParams(context.query||'').get('signal_strategy_id')||'';const params=new URLSearchParams(context.query||''),day=params.get('focus_date')||params.get('signal_as_of_date');if(validDate(day))AT.marketUI.openDay(symbol,day);else AT.navigate('G01',symbol);return true;}};
})();
