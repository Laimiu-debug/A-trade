/* Local daily-bar activity comparisons. Turnover is not money-flow attribution. */
(() => {
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  const fields = {activityPercent: '成交额热度', amount: '成交额', returnPercent: '区间涨幅'};
  const periods = [30, 60, 120, 250, 750];
  const symbolFor = item => item.symbol || item.id;
  function cleanRows(rows = []) {
    const dates = new Map();
    for (const row of rows) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(row?.date || '')) continue;
      const parsed = new Date(`${row.date}T00:00:00Z`);
      if (!Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== row.date) continue;
      dates.set(row.date, row);
    }
    return [...dates.values()].sort((a, b) => a.date.localeCompare(b.date));
  }
  function calculate(series, field = 'activityPercent', period = 60) {
    const ready = series.map(item => ({...item, rows: cleanRows(item.rows)})).filter(item => item.rows.length);
    const dates = [...new Set(ready.flatMap(item => item.rows.map(row => row.date)))].sort().slice(-period);
    if (!dates.length) return {dates: [], series: [], baseDate: '', reason: 'empty'};
    const maps = ready.map(item => new Map(item.rows.map(row => [row.date, row])));
    const baseDate = field === 'returnPercent' ? dates.find(date => maps.every(map => Number.isFinite(map.get(date)?.close) && map.get(date).close > 0)) : '';
    if (field === 'returnPercent' && !baseDate) return {dates: [], series: [], baseDate: '', reason: 'no-common-date'};
    const shown = baseDate ? dates.filter(date => date >= baseDate) : dates;
    const compared = ready.map((item, i) => ({symbol: item.symbol, name: item.name, values: shown.map(date => {
      const row = maps[i].get(date);
      if (field === 'returnPercent') return Number.isFinite(row?.close) && row.close > 0 ? (row.close / maps[i].get(baseDate).close - 1) * 100 : null;
      const value = row?.[field];
      return Number.isFinite(value) && (field !== 'amount' || value >= 0) ? value / (field === 'amount' ? 1e8 : 1) : null;
    })}));
    return {dates: shown, series: compared, baseDate, reason: shown.length < 2 ? 'too-short' : compared.every(item => item.values.every(value => value === null)) ? 'missing-values' : ''};
  }
  function linePath(values, width, height, low, high) {
    let continued = false;
    return values.map((value, index) => {
      if (!Number.isFinite(value)) {continued = false; return '';}
      const command = continued ? 'L' : 'M'; continued = true;
      return `${command}${(index / Math.max(1, values.length - 1) * width).toFixed(2)},${(height - (value - low) / (high - low || 1) * height).toFixed(2)}`;
    }).filter(Boolean).join(' ');
  }
  function candidateItems(items = []) {
    const unique = new Map();
    for (const item of items) {const symbol = symbolFor(item); if (/^(sh|sz|bj)\d{6}$/.test(symbol || '') && !unique.has(symbol)) unique.set(symbol, {...item, symbol});}
    return [...unique.values()];
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {cleanRows, calculate, linePath, candidateItems};
  if (typeof AT === 'undefined' || !AT.isDesktop) return;
  const E = AT.escape, colors = ['#3065e8', '#e18d27', '#ab56c6', '#14978a', '#d8606d'];
  const states = new Map();
  let active = null, generation = 0, running = false, scheduled = false, drag = null, deferredRender = false, suppressClick = 0, suppressContext = 0;
  const pageFor = kind => kind === 'sector' ? 'T05' : 'T04';
  const visible = state => state && active === state && AT.state.page === pageFor(state.kind);
  const errorText = value => typeof value === 'string' ? value : value?.message || '读取失败';
  const formulaNote = value => typeof value === 'string' && value.startsWith('量能热度＝') && value.endsWith('反映成交活跃程度，不代表主力净流入。');
  const display = (value, field = active?.field) => Number.isFinite(value) ? `${field === 'amount' ? '' : value > 0 ? '+' : ''}${value.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}${field === 'amount' ? ' 亿' : '%'}` : '—';
  function stateFor(kind) {
    if (!states.has(kind)) states.set(kind, {kind, selected: [], members: [], histories: new Map(), force: new Set(), auto: true, period: 60, field: 'activityPercent', query: '', contextKey: null, asOf: '', list: null, listForce: false, model: {dates: [], series: []}, start: 0, count: null, hover: null, notice: ''});
    return states.get(kind);
  }
  const historyKey = (state, symbol) => `${state.asOf}|${state.period}|${symbol}`;
  const entryFor = (state, symbol) => state.histories.get(historyKey(state, symbol));
  const nameFor = (state, symbol) => state.members.find(item => item.symbol === symbol)?.name || entryFor(state, symbol)?.data?.name || symbol;
  const waiting = state => state.selected.some(symbol => !entryFor(state, symbol) || entryFor(state, symbol).busy);
  function resetWindow(state) {state.start = 0; state.count = null; state.hover = null;}
  function rebuild(state) {
    if (waiting(state)) return;
    state.model = calculate(state.selected.map(symbol => ({symbol, name: nameFor(state, symbol), rows: entryFor(state, symbol)?.data?.items || []})), state.field, state.period);
  }
  function schedule() {if (!scheduled) {scheduled = true; setTimeout(() => {scheduled = false; loadNext();}, 0);}}
  async function call(method, payload) {
    const response = await window.atradeDesktop.market(method, payload);
    if (!response?.ok) throw new Error(errorText(response?.error));
    return response.data;
  }
  async function loadNext() {
    const state = active;
    if (running || !visible(state)) return;
    const list = state.kind === 'sector' && !state.list;
    const symbol = list ? '' : state.selected.find(value => !entryFor(state, value));
    if (!list && !symbol) return;
    const serial = generation, key = historyKey(state, symbol);
    const entry = {busy: true};
    if (list) state.list = entry; else state.histories.set(key, entry);
    running = true; paint();
    try {
      const force = list ? state.listForce : state.force.delete(symbol);
      if (list) state.listForce = false;
      const data = await call(list ? 'market.funds.activity.list' : 'market.funds.activity.history', list ? {...(force ? {refresh: true} : {})} : {symbol, kind: state.kind, limit: state.period, window: 5, ...(state.asOf ? {asOf: state.asOf} : {}), ...(force ? {refresh: true} : {})});
      if (serial !== generation || !visible(state)) return;
      if (list) {
        state.list = {data}; state.members = candidateItems(data?.items || []);
        if (state.auto && state.members.length) {state.selected = state.members.filter(item => !item.stale).slice(0, 5).map(symbolFor); state.auto = false; resetWindow(state);}
      } else state.histories.set(key, {data});
    } catch (error) {
      if (serial === generation && visible(state)) {
        if (list) state.list = {error: errorText(error)};
        else state.histories.set(key, {error: errorText(error)});
      }
    } finally {
      // A hidden-page response must never become the new visible page's data.
      if (state.list === entry) state.list = null;
      if (state.histories.get(key) === entry) state.histories.delete(key);
      running = false;
      if (serial === generation && visible(state)) {rebuild(state); paint();}
      schedule();
    }
  }
  function range(state = active) {
    const total = state.model.dates.length, size = Math.min(state.count ?? total, total), from = clamp(state.start, 0, Math.max(0, total - size));
    return {total, size, from, dates: state.model.dates.slice(from, from + size)};
  }
  function reading(index, state = active) {
    if (!state.model.dates[index]) return '移动到曲线上查看同日数据';
    return `<time>${E(state.model.dates[index])}</time>${state.selected.map((symbol, i) => `<span style="--activity-color:${colors[i]}"><i></i>${E(nameFor(state, symbol))}<strong>${display(state.model.series.find(item => item.symbol === symbol)?.values[index], state.field)}</strong></span>`).join('')}`;
  }
  function statusFor(state, symbol) {
    const entry = entryFor(state, symbol), data = entry?.data, rows = cleanRows(data?.items || []);
    if (!entry || entry.busy) return '读取日线…';
    if (entry.error || data?.status === 'unavailable' && !rows.length) return '日线暂不可用';
    if (!rows.length) return '暂无本地日线';
    return `${data.stale ? '旧缓存 · ' : ''}${rows[0].date} 至 ${rows.at(-1).date} · ${rows.length} 日`;
  }
  function notes(state) {
    const groups = new Map();
    const add = (name, value) => {const message = errorText(value); if (!groups.has(message)) groups.set(message, new Set()); groups.get(message).add(name);};
    for (const symbol of state.selected) {
      const entry = entryFor(state, symbol), name = nameFor(state, symbol);
      if (entry?.error) add(name, entry.error);
      for (const value of [...(entry?.data?.warnings || []), ...(entry?.data?.errors || [])]) if (!formulaNote(value)) add(name, value);
    }
    return [...groups].map(([message, names]) => `${[...names].join('、')}：${message}`);
  }
  function panel(next) {
    if (next) {
      const state = stateFor(next.kind === 'stock' ? 'stock' : 'sector');
      if (active !== state) {endDrag(false); active = state;}
      if (state.kind === 'stock') {
        if (state.contextKey !== (next.contextKey ?? '') || state.asOf !== (next.asOf || '')) {
          generation++; endDrag(false); state.contextKey = next.contextKey ?? ''; state.asOf = next.asOf || ''; state.auto = true; state.selected = []; state.histories.clear(); state.force.clear(); state.notice = ''; state.query = ''; resetWindow(state);
        }
        state.members = candidateItems(next.items || []);
        state.payloadBusy = !!next.busy;
        if (state.auto && state.members.length) {state.selected = state.members.slice(0, 5).map(symbolFor); state.auto = false;}
      }
      rebuild(state); schedule();
    }
    const state = active;
    if (!state) return '';
    const current = range(state), loading = waiting(state), listBusy = state.kind === 'sector' && (!state.list || state.list.busy);
    const query = state.query.trim().toLowerCase(), matched = state.members.filter(item => !query || `${item.name || ''}${item.symbol}`.toLowerCase().includes(query)), candidates = matched.slice(0, 20);
    const values = state.model.series.flatMap(item => item.values.slice(current.from, current.from + current.size)).filter(Number.isFinite);
    const rawLow = Math.min(0, ...values), rawHigh = Math.max(0, ...values), padding = Math.max((rawHigh - rawLow) * .08, state.field === 'amount' ? .05 : .5), low = state.field === 'amount' ? 0 : rawLow - padding, high = rawHigh + padding;
    const y = value => 250 - (value - low) / (high - low) * 250;
    const warnings = notes(state), listErrors = [...(state.list?.error ? [state.list.error] : []), ...(state.list?.data?.errors || []).map(errorText)], listWarnings = (state.list?.data?.warnings || []).filter(value => !formulaNote(value)).map(errorText);
    const empty = listBusy ? '正在读取本地板块日线目录…' : state.payloadBusy && !state.selected.length ? '正在筛选龙头候选…' : loading ? `正在读取所选 ${state.selected.length} 个${state.kind === 'sector' ? '板块' : '候选'}的成交额日线…` : !state.selected.length ? state.members.length ? '选择上方候选加入对比，最多同时观察 5 条曲线。' : state.kind === 'sector' ? '暂无可用板块日线，请选择通达信目录并下载板块日线数据。' : '暂无龙头候选，请先运行上方趋势筛选。' : state.model.reason === 'no-common-date' ? '所选证券在这个周期内没有共同基准日，请调整周期或选择。' : state.model.reason === 'missing-values' ? '这个周期的成交额数据不足以计算所选指标；热度需要完整的 5 日窗口。' : '当前可比较的日线不足，请调整周期或选择。';
    const label = state.kind === 'sector' ? '板块与分类指数 · 成交额走势' : '龙头候选 · 成交额观察';
    const sourceData = state.selected.map(symbol => entryFor(state, symbol)?.data).find(data => data?.items?.length) || state.list?.data;
    return `<section class="panel funds-activity-panel" data-funds-activity-panel data-activity-kind="${state.kind}" aria-label="${label}" aria-busy="${!!(listBusy || loading)}"><header><div><h2>${label}</h2><p>${state.kind === 'sector' ? '按本地最新成交额热度初选 · 板块与分类指数可叠加对比 5 个' : '来自本次趋势筛选的候选 · 可叠加对比 5 只证券'}${state.asOf ? ` · 截至 ${E(state.asOf)}` : ''}</p></div><button type="button" class="funds-activity-refresh" data-funds-activity-action="refresh">刷新</button></header><div class="funds-activity-controls"><div role="group" aria-label="观察指标">${Object.entries(fields).map(([key, name]) => `<button type="button" data-funds-activity-field="${key}" aria-pressed="${state.field === key}">${name}</button>`).join('')}</div><div role="group" aria-label="观察周期">${periods.map(n => `<button type="button" data-funds-activity-period="${n}" aria-pressed="${state.period === n}">${n} 日</button>`).join('')}</div></div><div class="funds-activity-selection"><label><span>添加对比</span><input data-funds-activity-search value="${E(state.query)}" placeholder="搜索${state.kind === 'sector' ? '板块' : '候选'}名称 / 代码" aria-label="搜索成交额对比候选" autocomplete="off"></label><span>${matched.length} 个${query ? '匹配' : '可选'} · 已选 ${state.selected.length} / 5</span></div><div class="funds-activity-candidates">${candidates.map(item => `<button type="button" data-funds-activity-toggle="${E(item.symbol)}" aria-pressed="${state.selected.includes(item.symbol)}" title="${E(item.symbol)}${item.asOf ? ` · ${E(item.asOf)}` : ''}">${E(item.name || item.symbol)}${item.stale ? `<small>旧日 ${E(item.asOf || '')}</small>` : ''}<span aria-hidden="true">${state.selected.includes(item.symbol) ? '−' : '+'}</span></button>`).join('')}${matched.length > 20 ? '<small>显示前 20 项，搜索可找到其他候选</small>' : ''}${query && !matched.length ? '<small>没有匹配的候选</small>' : ''}</div><div class="funds-activity-legend">${state.selected.map((symbol, i) => `<div style="--activity-color:${colors[i]}"><i></i><span><strong>${E(nameFor(state, symbol))}</strong><small>${E(statusFor(state, symbol))}</small></span><button type="button" data-funds-activity-remove="${E(symbol)}" aria-label="移除${E(nameFor(state, symbol))}">×</button></div>`).join('')}</div>${!loading && !state.model.reason && current.size > 1 ? `<div class="funds-activity-reading" data-funds-activity-reading>${reading(clamp(state.hover ?? current.from + current.size - 1, current.from, current.from + current.size - 1), state)}</div><div class="funds-activity-chart" data-funds-activity-chart data-activity-start="${current.from}" data-activity-count="${current.size}" data-activity-total="${current.total}" data-activity-base="${E(state.model.baseDate || '')}" tabindex="0" aria-label="${label}，滚轮缩放，左键或右键拖动，方向键逐日查看"><svg viewBox="0 0 1000 250" preserveAspectRatio="none" role="img" aria-label="${E(current.dates[0])}至${E(current.dates.at(-1))}${fields[state.field]}">${[low, (low + high) / 2, high].map(value => `<line x1="0" x2="1000" y1="${y(value).toFixed(2)}" y2="${y(value).toFixed(2)}" class="funds-activity-grid"/>`).join('')}<line x1="0" x2="1000" y1="${y(0).toFixed(2)}" y2="${y(0).toFixed(2)}" class="funds-activity-zero"/>${state.model.series.map(item => `<path data-funds-activity-line="${E(item.symbol)}" d="${linePath(item.values.slice(current.from, current.from + current.size), 1000, 250, low, high)}" fill="none" stroke="${colors[state.selected.indexOf(item.symbol)]}" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join('')}</svg><span class="funds-activity-axis top">${display(high, state.field)}</span><span class="funds-activity-axis bottom">${display(low, state.field)}</span><div data-funds-activity-crosshair class="funds-activity-crosshair" hidden></div></div><div class="funds-activity-dates"><span>${E(current.dates[0])}</span><span>${E(current.dates[Math.floor((current.size - 1) / 2)])}</span><span>${E(current.dates.at(-1))}</span></div><footer><span>滚轮缩放 · 左 / 右键拖动 · 当前 ${current.size} / ${current.total} 日${state.model.baseDate ? ` · 共同基准 ${E(state.model.baseDate)} = 0%` : ''}</span><button type="button" data-funds-activity-action="reset">显示全部</button></footer>` : `<div class="funds-activity-empty" role="status">${empty}${state.kind === 'sector' && !listBusy && !loading ? '<button type="button" data-nav="C01">设置数据源</button>' : ''}</div>`}${state.notice ? `<p class="funds-activity-notice" role="status">${E(state.notice)}</p>` : ''}${listErrors.length || listWarnings.length || warnings.length ? `<div class="funds-activity-notice" role="status">${[...listErrors, ...listWarnings, ...warnings].map(value => `<p>${E(value)}</p>`).join('')}</div>` : ''}<p class="funds-activity-note">成交额热度 =（当日成交额 ÷ 含当日 5 日均额 − 1）× 100%；用于观察交易活跃度，不能表示主力净流入。${state.field === 'returnPercent' ? '涨幅使用共同基准日收盘价重算。' : ''}各曲线按真实交易日期对齐，缺失日期断线；不足所选天数时仅显示已有日线。</p>${sourceData ? `<p class="funds-activity-source">来源 ${E(sourceData.sourceName || '通达信本地日线')}${sourceData.sourceRoot ? ` · ${E(sourceData.sourceRoot)}` : ''}${sourceData.asOf ? ` · 数据日 ${E(sourceData.asOf)}` : ''}${sourceData.stale ? ' · 旧缓存' : ''}</p>` : ''}</section>`;
  }
  function paint() {
    if (!visible(active)) return;
    const element = document.querySelector('[data-funds-activity-panel]');
    if (!element) return;
    const focused = document.activeElement, contains = element.contains(focused), attributes = ['data-funds-activity-search', 'data-funds-activity-chart', 'data-funds-activity-field', 'data-funds-activity-period', 'data-funds-activity-toggle', 'data-funds-activity-remove', 'data-funds-activity-action'];
    const attr = contains && attributes.find(name => focused?.hasAttribute?.(name)), value = attr ? focused.getAttribute(attr) : '', selection = attr === 'data-funds-activity-search' ? [focused.selectionStart, focused.selectionEnd] : null;
    element.outerHTML = panel();
    if (attr) for (const node of document.querySelectorAll(`[${attr}]`)) if (node.getAttribute(attr) === value) {node.focus({preventScroll: true}); if (selection) node.setSelectionRange(...selection); break;}
  }
  function endDrag(renderPending = true) {
    const previous = drag; drag = null; document.documentElement.classList.remove('funds-activity-dragging');
    if (previous) {
      if (previous.moved) suppressClick = Date.now() + 180;
      if (previous.button === 2) suppressContext = Date.now() + 650;
      try {if (document.documentElement.hasPointerCapture(previous.pointerId)) document.documentElement.releasePointerCapture(previous.pointerId);} catch {}
    }
    const pending = deferredRender; deferredRender = false;
    if (pending && renderPending && visible(active)) setTimeout(() => AT.render(), 0);
  }
  function deferRender() {if (!drag || !visible(active)) return false; deferredRender = true; return true;}
  function invalidate() {endDrag(false); generation++; states.clear(); active = null;}
  function suspend() {endDrag(false); generation++; active = null;}
  function refresh() {
    if (!active) return;
    endDrag(false); generation++; active.histories.clear(); active.selected.forEach(symbol => active.force.add(symbol));
    if (active.kind === 'sector') {active.list = null; active.listForce = true;}
    resetWindow(active); schedule(); paint();
  }
  function changeSelection(symbol) {
    const state = active; if (!state.members.some(item => item.symbol === symbol) && !state.selected.includes(symbol)) return;
    state.auto = false; state.notice = '';
    if (state.selected.includes(symbol)) state.selected = state.selected.filter(value => value !== symbol);
    else if (state.selected.length < 5) state.selected.push(symbol);
    else {state.notice = '最多同时对比 5 个，请先移除一个。'; paint(); return;}
    resetWindow(state); rebuild(state); schedule(); paint();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-funds-activity-toggle],[data-funds-activity-remove],[data-funds-activity-field],[data-funds-activity-period],[data-funds-activity-action]');
    if (!button || !visible(active) || Date.now() < suppressClick) return;
    event.preventDefault(); endDrag(false);
    const data = button.dataset;
    if (data.fundsActivityToggle || data.fundsActivityRemove) changeSelection(data.fundsActivityToggle || data.fundsActivityRemove);
    else if (fields[data.fundsActivityField]) {active.field = data.fundsActivityField; resetWindow(active); rebuild(active); paint();}
    else if (periods.includes(Number(data.fundsActivityPeriod))) {active.period = Number(data.fundsActivityPeriod); resetWindow(active); rebuild(active); schedule(); paint();}
    else if (data.fundsActivityAction === 'refresh') refresh();
    else if (data.fundsActivityAction === 'reset') {resetWindow(active); paint();}
  });
  document.addEventListener('input', event => {if (event.target.matches?.('[data-funds-activity-search]') && visible(active)) {active.query = event.target.value; paint();}});
  document.addEventListener('wheel', event => {
    const chart = event.target.closest?.('[data-funds-activity-chart]');
    if (!chart || !visible(active) || !event.deltaY || event.ctrlKey || event.metaKey) return;
    const current = range(); if (current.total < 2) return;
    event.preventDefault(); if (drag) return;
    const rect = chart.getBoundingClientRect(), ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1), size = clamp(Math.round(current.size * (event.deltaY > 0 ? 1.2 : .8)), Math.min(10, current.total), current.total);
    active.start = clamp(Math.round(current.from + ratio * (current.size - size)), 0, current.total - size); active.count = size; active.hover = null; paint();
  }, {passive: false});
  document.addEventListener('pointerdown', event => {
    const chart = event.target.closest?.('[data-funds-activity-chart]');
    if (!chart || !visible(active) || ![0, 2].includes(event.button) || event.pointerType === 'touch') return;
    const current = range(); if (current.total < 2) return;
    endDrag(false); event.preventDefault(); chart.focus({preventScroll: true});
    drag = {pointerId: event.pointerId, button: event.button, generation, state: active, x: event.clientX, y: event.clientY, start: current.from, size: current.size, total: current.total, width: Math.max(1, chart.getBoundingClientRect().width), moved: false};
    if (event.button === 2) suppressContext = Date.now() + 650;
  });
  function readAt(index) {
    active.hover = index; const current = range(), readingElement = document.querySelector('[data-funds-activity-reading]'), line = document.querySelector('[data-funds-activity-crosshair]');
    if (readingElement) readingElement.innerHTML = reading(index);
    if (line) {line.hidden = false; line.style.left = `${(index - current.from) / Math.max(1, current.size - 1) * 100}%`;}
  }
  document.addEventListener('pointermove', event => {
    if (drag) {
      if (event.pointerId !== drag.pointerId) return;
      if (!(event.buttons & (drag.button === 0 ? 1 : 2)) || !visible(drag.state) || generation !== drag.generation) {endDrag(); return;}
      const delta = event.clientX - drag.x;
      if (!drag.moved) {if (Math.hypot(delta, event.clientY - drag.y) < 4) return; drag.moved = true; document.documentElement.classList.add('funds-activity-dragging'); try {document.documentElement.setPointerCapture(event.pointerId);} catch {}}
      event.preventDefault(); event.stopPropagation?.(); suppressClick = Date.now() + 180;
      const next = clamp(Math.round(drag.start - delta / drag.width * drag.size), 0, drag.total - drag.size);
      if (active.start !== next) {active.start = next; active.hover = null; paint();} return;
    }
    const chart = event.target.closest?.('[data-funds-activity-chart]'); if (!chart || !visible(active)) return;
    const current = range(), rect = chart.getBoundingClientRect(), ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
    readAt(current.from + Math.round(ratio * Math.max(0, current.size - 1)));
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) document.addEventListener(type, event => {if (event.pointerId === drag?.pointerId) endDrag();});
  for (const type of ['blur', 'resize']) window.addEventListener(type, () => endDrag());
  document.addEventListener('visibilitychange', () => {if (document.hidden) endDrag();});
  document.addEventListener('contextmenu', event => {if (event.target.closest?.('[data-funds-activity-chart]') || drag?.button === 2 || Date.now() < suppressContext) {event.preventDefault(); suppressContext = 0;}});
  document.addEventListener('keydown', event => {
    if (!event.target.matches?.('[data-funds-activity-chart]') || !visible(active) || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); const current = range();
    if (event.shiftKey && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {active.start = clamp(current.from + (event.key === 'ArrowLeft' ? -1 : 1) * Math.max(1, Math.round(current.size / 5)), 0, current.total - current.size); active.hover = null; paint();}
    else {const next = event.key === 'Home' ? 0 : event.key === 'End' ? current.total - 1 : clamp((active.hover ?? current.from + current.size - 1) + (event.key === 'ArrowLeft' ? -1 : 1), 0, current.total - 1); if (next < current.from || next >= current.from + current.size) {active.start = clamp(next < current.from ? next : next - current.size + 1, 0, current.total - current.size); paint();} readAt(next);}
  });
  AT.fundsActivity = {panel, invalidate, refresh, endDrag, deferRender, suspend};
})();
