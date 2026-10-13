/* Shared, real OHLC previews for stock rows. Prices never come from demo data. */
(() => {
  'use strict';
  const COUNT = 30, CACHE_LIMIT = 240, CONCURRENCY = 2;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function symbol(value) {
    const text = String(value || '').trim().toLowerCase();
    if (/^(sh|sz|bj)\d{6}$/.test(text)) return text;
    if (!/^\d{6}$/.test(text)) return '';
    return (/^(4|8|92)/.test(text) ? 'bj' : /^[569]/.test(text) ? 'sh' : 'sz') + text;
  }
  function date(value) {
    const text = String(value || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return '';
    const stamp = new Date(text + 'T00:00:00Z');
    return Number.isFinite(+stamp) && stamp.toISOString().slice(0, 10) === text ? text : '';
  }
  const price = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
  function bars(rows, end = '') {
    const unique = new Map(), cutoff = date(end);
    for (const row of Array.isArray(rows) ? rows : []) {
      const day = date(row?.date);
      if (!day || cutoff && day > cutoff || !['open','high','low','close'].every(key => price(row[key]))) continue;
      if (row.high < Math.max(row.open, row.close) || row.low > Math.min(row.open, row.close) || row.low > row.high) continue;
      unique.set(day, {...row, date:day});
    }
    return [...unique.values()].sort((a,b) => a.date.localeCompare(b.date)).slice(-COUNT);
  }
  function plot(rows) {
    if (!rows.length) return '';
    const high = Math.max(...rows.map(row => row.high)), low = Math.min(...rows.map(row => row.low));
    const span = Math.max(high - low, high * .005, .01), middle = (high + low) / 2;
    const y = value => 34 - (value - middle) / span * 52, step = 174 / COUNT;
    const offset = (COUNT - rows.length) * step;
    const digits = value => Number(value.toFixed(4)).toString();
    return `<svg class="stock-kline-svg" viewBox="0 0 180 68" role="img" aria-label="${rows.length} 根未复权日K，${escape(rows[0].date)} 至 ${escape(rows.at(-1).date)}"><path class="stock-kline-grid" d="M3 8H177 M3 34H177 M3 60H177"/>${rows.map((row,i) => {
      const x = 3 + offset + (i + .5) * step, top = Math.min(y(row.open), y(row.close)), height = Math.max(1, Math.abs(y(row.open) - y(row.close)));
      const tone = row.close > row.open ? 'up' : row.close < row.open ? 'down' : 'flat';
      return `<g data-kline-candle="${escape(row.date)}" class="stock-kline-${tone}"><title>${escape(row.date)} · 开 ${digits(row.open)} · 高 ${digits(row.high)} · 低 ${digits(row.low)} · 收 ${digits(row.close)}</title><line x1="${x.toFixed(2)}" x2="${x.toFixed(2)}" y1="${y(row.high).toFixed(2)}" y2="${y(row.low).toFixed(2)}"/><rect x="${(x-1.8).toFixed(2)}" y="${top.toFixed(2)}" width="3.6" height="${height.toFixed(2)}"/></g>`;
    }).join('')}</svg>`;
  }
  const helpers = {symbol, date, bars, plot};
  if (typeof module === 'object' && module.exports) module.exports = helpers;
  if (typeof window === 'undefined' || typeof AT === 'undefined') return;

  const cache = new Map(), queue = [], observed = new Set(), states = new WeakMap();
  let generation = 0, running = 0, scheduled = false;
  const selector = '[data-stock],[data-stock-chart-symbol]';
  const ready = () => AT.isDesktop && typeof window.atradeDesktop?.market === 'function';
  function context(row) {
    const nodes = [row, ...row.querySelectorAll(selector)];
    const source = nodes.find(node => node.dataset && symbol(node.dataset.stockChartSymbol || node.dataset.stock));
    if (!source) return null;
    const stock = symbol(source.dataset.stockChartSymbol || source.dataset.stock);
    const explicit = [source.dataset.stockChartDate, source.dataset.researchStockDate, source.dataset.analysisStockDay,
      row.dataset.stockChartDate, row.closest('table')?.dataset.stockChartDate].find(value => date(value));
    const name = source.querySelector('strong')?.textContent?.trim() || (source.tagName!=='TR'?source.textContent?.trim():'') || stock;
    return {symbol:stock, end:date(explicit), name, cell:source.closest('td')};
  }
  function prune() {
    for (const button of observed) {
      if (button.isConnected) continue;
      observer?.unobserve(button);observed.delete(button);
    }
    for (const entry of cache.values()) for (const button of entry.targets) if (!button.isConnected) entry.targets.delete(button);
    for (const [key, entry] of cache) {
      if (cache.size <= CACHE_LIMIT) break;
      if (entry.status !== 'loading' && entry.status !== 'queued') cache.delete(key);
    }
  }
  function paint(button, entry) {
    const state = states.get(button);
    if (!state || !button.isConnected || state.entry !== entry) return;
    const rows = entry.rows || [], latest = rows.at(-1)?.date;
    const loading = ['loading','queued'].includes(entry.status);
    const message = !ready() ? '连接桌面行情后显示' : loading ? '读取日 K…' : entry.status === 'error' ? '日 K 暂不可用' : state.end ? '该日期前暂无日 K' : '暂无日 K 数据';
    button.innerHTML = rows.length ? plot(rows) + `<span class="stock-kline-caption"><span>${rows.length} 日 · ${escape(latest)}</span><span>${entry.stale?'旧缓存':'查看大图 ↗'}</span></span>` : `<span class="stock-kline-empty">${message}</span><span class="stock-kline-caption">点击查看完整日 K</span>`;
    button.setAttribute('aria-busy', String(loading));
    button.title = `${state.name} · ${rows.length?rows[0].date+' 至 '+latest:message} · 未复权${state.end?' · 截至 '+state.end:''} · 点击查看完整日 K`;
    const cell = button.closest('td'), retry = cell.querySelector('[data-stock-kline-retry]');
    if (entry.status === 'error' && !retry && ready()) {
      const action = document.createElement('button');action.type='button';action.className='stock-kline-retry';action.dataset.stockKlineRetry='';action.textContent='重试';cell.append(action);
    } else if (entry.status !== 'error') retry?.remove();
  }
  function update(entry) { for (const button of entry.targets) paint(button, entry); }
  function request(button, force = false) {
    const state = states.get(button);if (!state || !button.isConnected) return;
    if (!ready()) {state.entry={status:'empty',targets:new Set([button])};paint(button,state.entry);return;}
    const key = state.symbol + '|' + state.end;
    let entry = cache.get(key);
    if (entry && (entry.status === 'loading' || entry.status === 'queued' || !force && Date.now()-entry.time < (entry.status==='error'?15000:60000))) {
      state.entry=entry;entry.targets.add(button);paint(button,entry);return;
    }
    entry={status:'queued',targets:new Set([button]),time:Date.now(),generation,symbol:state.symbol,end:state.end};
    state.entry=entry;cache.delete(key);cache.set(key,entry);queue.push(entry);paint(button,entry);prune();pump();
  }
  function pump() {
    while (running < CONCURRENCY && queue.length) {
      const entry = queue.shift();
      if (entry.generation !== generation) continue;
      if (![...entry.targets].some(button => button.isConnected)) {entry.status='empty';entry.time=0;continue;}
      running++;entry.status='loading';update(entry);
      Promise.resolve().then(() => window.atradeDesktop.market('market.bars', {symbol:entry.symbol,period:'1d',limit:COUNT,...(entry.end?{end:entry.end}:{})})).then(reply => {
        if (entry.generation !== generation) return;
        if (!reply?.ok) throw new Error('Daily bars unavailable');
        if (reply.data?.symbol && symbol(reply.data.symbol)!==entry.symbol) throw new Error('Daily bars identity mismatch');
        entry.rows=bars(reply.data?.rows,entry.end);entry.stale=!!reply.data?.stale;entry.status='ready';
      }).catch(() => {if(entry.generation===generation){entry.status='error';entry.rows=[];}}).finally(() => {
        running--;entry.time=Date.now();if(entry.generation===generation)update(entry);prune();pump();
      });
    }
  }
  const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {observer.unobserve(entry.target);observed.delete(entry.target);request(entry.target);}
  }, {rootMargin:'120px 0px'}) : null;
  function install(cell, item) {
    cell.classList.add('stock-kline-cell');
    const existing=cell.querySelector('[data-stock-kline]');
    if (existing && states.get(existing)?.symbol===item.symbol && states.get(existing)?.end===item.end) return;
    cell.replaceChildren();
    const button=document.createElement('button');button.type='button';button.className='stock-kline-button';
    button.dataset.stockKline=item.symbol;button.dataset.stockKlineDate=item.end;
    button.setAttribute('aria-label', `${item.name}，查看${item.end?item.end+' 截至的':'最近'}日 K 线`);
    button.innerHTML='<span class="stock-kline-empty">等待显示日 K</span><span class="stock-kline-caption">近 30 个交易日 · 未复权</span>';
    states.set(button,item);cell.append(button);
    const entry=cache.get(item.symbol+'|'+item.end);
    if (entry && Date.now()-entry.time<60000 && entry.status==='ready') {item.entry=entry;entry.targets.add(button);paint(button,entry);}
    else if(observer){observed.add(button);observer.observe(button);}else request(button);
  }
  function enhance(table) {
    if (!table.tHead || table.closest('[data-stock-kline-skip]')) return;
    const rows=[...table.tBodies].flatMap(body=>[...body.rows]), items=rows.map(row=>context(row));
    const first=items.find(Boolean);if(!first)return;
    let index=Number(table.dataset.stockKlineColumn), heading=table.querySelector('[data-stock-kline-heading]');
    if (!heading) {
      // The market table already reserves a trend column; replace its close-only sparkline.
      const old=table.tHead.querySelector('.market-explorer-col-trend');
      index=old?.cellIndex??(first.cell?.cellIndex!=null?first.cell.cellIndex+1:table.tHead.rows[0].cells.length);
      heading=old||document.createElement('th');heading.dataset.stockKlineHeading='';heading.scope='col';heading.classList.add('stock-kline-heading');
      heading.innerHTML='日 K 线<small>近 30 个交易日 · 未复权</small>';
      if(!old)table.tHead.rows[0].insertBefore(heading,table.tHead.rows[0].cells[index]||null);
      table.dataset.stockKlineColumn=String(index);table.dataset.stockKlineReuse=old?'true':'false';
      if (table.parentElement.matches('.table-wrap,.rs-table-wrap,.stock-kline-table-wrap')) table.parentElement.classList.add('stock-kline-table-wrap');
      else {const wrap=document.createElement('div');wrap.className='stock-kline-table-wrap';table.before(wrap);wrap.append(table);}
    }
    table.classList.add('stock-kline-table');
    rows.forEach((row,i)=>{
      const item=items[i];
      if (!item && row.cells.length===1 && row.cells[0].colSpan>1) {
        if (row.dataset.stockKlineSpanned===undefined && table.dataset.stockKlineReuse!=='true') {row.cells[0].colSpan++;row.dataset.stockKlineSpanned='';}return;
      }
      let cell=[...row.cells].find(td=>td.classList.contains('stock-kline-cell'));
      if(!cell){cell=table.dataset.stockKlineReuse==='true'?row.cells[index]:document.createElement('td');if(!cell)return;if(!cell.parentElement)row.insertBefore(cell,row.cells[index]||null);cell.classList.add('stock-kline-cell');}
      if(item)install(cell,item);else cell.textContent='—';
    });
  }
  function mount(root=document) {prune();for(const table of root.querySelectorAll('table'))enhance(table);}
  function schedule() {if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;mount();});}
  function invalidate() {
    generation++;cache.clear();queue.length=0;
    for(const button of document.querySelectorAll('[data-stock-kline]')) {
      const state=states.get(button);if(!state)continue;
      state.entry={status:'queued',targets:new Set([button])};paint(button,state.entry);
      if(observer){observer.unobserve(button);observed.add(button);observer.observe(button);}else request(button);
    }
  }
  AT.stockTableCharts={mount,invalidate,helpers};
  document.addEventListener('click', event => {
    const retry=event.target.closest?.('[data-stock-kline-retry]');
    if(retry){event.preventDefault();event.stopImmediatePropagation();request(retry.closest('td').querySelector('[data-stock-kline]'),true);return;}
    const button=event.target.closest?.('[data-stock-kline]');if(!button)return;
    const state=states.get(button);if(!state)return;
    event.preventDefault();event.stopImmediatePropagation();
    if(state.end&&AT.marketUI?.openDay?.(state.symbol,state.end))return;
    AT.navigate?.('G01',state.symbol);
  });
  document.addEventListener('DOMContentLoaded', () => {
    const mutation=new MutationObserver(records=>{
      if(records.some(record=>!record.target.closest?.('.stock-kline-cell,.stock-kline-heading') && [...record.addedNodes,...record.removedNodes].some(node=>node.nodeType===1)))schedule();
    });
    for(const root of document.querySelectorAll('#main,#workspace-root,#overlay-root'))mutation.observe(root,{childList:true,subtree:true});
    mount();
  });
})();
