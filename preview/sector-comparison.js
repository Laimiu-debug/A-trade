/* Compare real sector index closes on one common percentage-return scale. */
(() => {
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  function cleanRows(rows = []) {
    const byDate = new Map();
    for (const row of rows) if (/^\d{4}-\d{2}-\d{2}$/.test(row?.date || '') && Number.isFinite(row.close) && row.close > 0) byDate.set(row.date, row.close);
    return [...byDate].sort(([a], [b]) => a.localeCompare(b)).map(([date, close]) => ({date, close}));
  }
  function rankLeaders(items = [], count = 5) {
    const unique = new Map();
    for (const item of items) {
      const id = item.id || item.symbol;
      if (id && Number.isFinite(item.changePercent) && item.changePercent > 0) unique.set(id, item);
    }
    return [...unique.values()].sort((a, b) => b.changePercent - a.changePercent || String(a.id || a.symbol).localeCompare(String(b.id || b.symbol))).slice(0, count);
  }
  function calculate(series, period = 60, preferredBase = '') {
    const ready = series.map(item => ({...item, rows: cleanRows(item.rows)})).filter(item => item.rows.length > 1);
    if (!ready.length) return {dates: [], series: [], baseDate: '', reason: 'empty'};
    const maps = ready.map(item => new Map(item.rows.map(row => [row.date, row.close])));
    const all = [...new Set(ready.flatMap(item => item.rows.map(row => row.date)))].sort();
    const wanted = all.slice(-Math.max(2, period));
    const baseDate = preferredBase && maps.every(map => map.has(preferredBase)) ? preferredBase : wanted.find(date => maps.every(map => map.has(date)));
    if (!baseDate) return {dates: [], series: [], baseDate: '', reason: 'no-common-date'};
    const dates = all.filter(date => date >= baseDate);
    return {dates, baseDate, reason: dates.length < 2 ? 'too-short' : '', series: ready.map((item, index) => ({
      id: item.id, name: item.name, baseClose: maps[index].get(baseDate),
      values: dates.map(date => maps[index].has(date) ? (maps[index].get(date) / maps[index].get(baseDate) - 1) * 100 : null)
    }))};
  }
  function linePath(values, width, height, low, high) {
    const span = high - low || 1; let continued = false;
    return values.map((value, index) => {
      if (!Number.isFinite(value)) {continued = false; return '';}
      const command = continued ? 'L' : 'M'; continued = true;
      return `${command}${(index / Math.max(1, values.length - 1) * width).toFixed(2)},${(height - (value - low) / span * height).toFixed(2)}`;
    }).filter(Boolean).join(' ');
  }
  function displayWarnings(series) {
    const grouped = new Map();
    for (const item of series) for (const warning of [...(item.warnings || []), ...(item.error ? [item.error] : [])]) {
      const message = String(warning).trim();
      // This chart only displays closes and returns, so a volume-unit note does not apply.
      if (message.replace(/[。.]$/, '') === '此类证券的成交量保留终端原始单位') continue;
      if (!grouped.has(message)) grouped.set(message, new Set());
      grouped.get(message).add(item.name);
    }
    return [...grouped].map(([message, names]) => `${[...names].join('、')}：${message}`);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {cleanRows, rankLeaders, calculate, linePath, displayWarnings};
  if (typeof AT === 'undefined' || !AT.isDesktop) return;
  const E = AT.escape, histories = new Map(), members = new Map(), refreshIds = new Set();
  const colors = ['#3065e8', '#e18d27', '#ab56c6', '#14978a', '#d8606d', '#78882a', '#63709a', '#a96736'];
  let payload = {}, selected = [], autoSelect = true, topCount = 5, period = 60;
  let running = false, scheduled = false, generation = 0, model = {dates: [], series: [], baseDate: ''}, baseKey = '';
  let start = 0, count = null, hover = null, drag = null, suppressClick = 0, suppressContext = 0, deferredRender = false, notice = '';
  const fmt = value => Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
  const idFor = item => item.id || item.symbol;
  const nameFor = id => members.get(id)?.name || histories.get(id)?.data?.name || id;
  const readyToPick = () => !!payload.items?.length && !payload.busy && !payload.loadingMetrics && (payload.error || !payload.progress?.total || payload.progress.done >= payload.progress.total);
  const waiting = () => selected.some(id => !histories.has(id) || histories.get(id)?.busy);
  function rerender() {
    if (AT.state.page !== 'M02') return;
    if (AT.marketUI?.refreshVisible) {AT.marketUI.refreshVisible(); return;}
    const focus = AT.marketExplorer?.captureFocus(); AT.render(); AT.marketExplorer?.restoreFocus(focus);
  }
  function schedule() {
    if (scheduled) return;
    scheduled = true; setTimeout(() => {scheduled = false; loadNext();}, 0);
  }
  function rebuild() {
    if (waiting()) return;
    const key = `${period}:${selected.join(',')}`, oldBase = key === baseKey ? model.baseDate : '';
    model = calculate(selected.map(id => ({id, name: nameFor(id), rows: histories.get(id)?.data?.rows || []})), period, oldBase);
    if (key !== baseKey) {start = 0; count = null; hover = null;}
    baseKey = key;
  }
  async function loadNext() {
    if (running || AT.state.page !== 'M02') return;
    const id = selected.find(id => !histories.has(id)); if (!id) return;
    running = true; const serial = generation;
    histories.set(id, {busy: true}); paint();
    try {
      const force = refreshIds.delete(id);
      const response = await (AT.sectorHistoryData ? AT.sectorHistoryData.read(id) : window.atradeDesktop.market('market.sector.history', {id, limit: 250, ...(force ? {refresh: true} : {})}));
      if (serial !== generation) return;
      if (!response?.ok) throw new Error(response?.error?.message || response?.error || '指数日线读取失败');
      histories.set(id, {data: response.data});
    } catch (error) {
      if (serial === generation) histories.set(id, {error: error.message || '指数日线读取失败'});
    } finally {
      running = false;
      if (serial === generation) {rebuild(); paint();}
      schedule();
    }
  }
  function pickTop(n) {
    endDrag(false); topCount = n; autoSelect = false; notice = '';
    selected = rankLeaders(payload.items || [], n).map(idFor);
    baseKey = ''; model = {dates: [], series: [], baseDate: ''}; start = 0; count = null; hover = null;
    rebuild(); schedule();
  }
  function range() {
    const total = model.dates.length, size = Math.min(count ?? total, total), from = clamp(start, 0, Math.max(0, total - size));
    return {from, size, total, dates: model.dates.slice(from, from + size)};
  }
  function reading(index) {
    const date = model.dates[index]; if (!date) return '同日对比各板块累计涨幅';
    return `<time>${E(date)}</time>${selected.map((id, colorIndex) => {
      const series = model.series.find(item => item.id === id), value = series?.values[index];
      return `<span style="--compare-color:${colors[colorIndex]}"><i></i>${E(nameFor(id))}<strong>${fmt(value)}</strong></span>`;
    }).join('')}`;
  }
  function statusFor(id) {
    const entry = histories.get(id), rows = cleanRows(entry?.data?.rows || []);
    if (!entry || entry.busy) return '读取日线…';
    if (entry.error) return '读取失败';
    if (rows.length < 2) return '日线暂缺';
    return `${entry.data.stale ? '旧缓存 · ' : ''}${rows.at(-1).date}`;
  }
  function panel(next) {
    if (next) {
      payload = next;
      for (const item of next.items || []) members.set(idFor(item), item);
      if (autoSelect && readyToPick()) pickTop(topCount);
      schedule();
    }
    const current = range(), loading = waiting(), shown = current.dates;
    const values = model.series.flatMap(item => item.values.slice(current.from, current.from + current.size)).filter(Number.isFinite);
    const rawLow = Math.min(0, ...values), rawHigh = Math.max(0, ...values), padding = Math.max((rawHigh - rawLow) * .08, .5);
    const low = rawLow - padding, high = rawHigh + padding, y = value => 260 - (value - low) / (high - low) * 260;
    const quoteCount = (payload.items || []).filter(item => Number.isFinite(item.changePercent)).length;
    const oldQuotes = selected.filter(id => members.get(id)?.cached || members.get(id)?.stale).length;
    const warnings = displayWarnings(selected.map(id => ({name: nameFor(id), warnings: histories.get(id)?.data?.warnings, error: histories.get(id)?.error})));
    const catalogueEmpty = payload.catalogueReady && !payload.items?.length && !payload.busy && !payload.loadingMetrics && !payload.error;
    const empty = catalogueEmpty ? '当前数据源暂无板块，请检查通达信板块目录或刷新重试。' : autoSelect && payload.error && !payload.items?.length ? '板块报价尚不可用，请在下方检查数据源或刷新重试。' : autoSelect || payload.loadingMetrics || payload.busy ? '正在读取板块报价，完成后选取涨幅靠前的板块…' : loading ? `正在读取所选 ${selected.length} 个板块的真实指数日线…` : !selected.length ? '暂无正涨幅板块；也可在下方列表点 + 自选板块。' : model.reason === 'no-common-date' ? '所选板块在这个周期内没有共同基准日，请调整周期或板块。' : '可比较的日线不足，请调整板块或周期。';
    return `<section class="panel sector-comparison-panel" data-sector-comparison-panel aria-label="头部板块走势对比" aria-busy="${!!(loading || payload.busy || payload.loadingMetrics || (autoSelect && !catalogueEmpty && !payload.error))}"><header><div><h2>头部板块走势对比 <small>同一基准 · 累计涨幅</small></h2><p>按最新已读报价涨幅选取 · 已读取 ${quoteCount} / ${(payload.items || []).length} 个板块${oldQuotes ? ` · ${oldQuotes} 个所选板块使用旧报价` : ''}</p></div><div class="sector-comparison-top" aria-label="重新选取涨幅靠前板块">${[3, 5, 8].map(n => `<button type="button" data-sector-comparison-top="${n}"${!readyToPick() ? ' disabled' : ''} title="重新选取正涨幅前 ${n} 个板块" class="${topCount === n ? 'selected' : ''}">前 ${n}</button>`).join('')}</div></header><div class="sector-comparison-toolbar"><div class="sector-comparison-period" aria-label="板块对比周期">${[20, 60, 120, 250].map(n => `<button type="button" data-sector-comparison-period="${n}" aria-pressed="${period === n}">${n} 日</button>`).join('')}</div><span>${model.baseDate && !loading ? `基准日 ${E(model.baseDate)} = 0%` : `已选 ${selected.length} / 8 个板块`}</span></div><div class="sector-comparison-legend">${selected.map((id, index) => `<div style="--compare-color:${colors[index]}"><i></i><span><strong>${E(nameFor(id))}</strong><small>${E(statusFor(id))}</small></span><button type="button" data-sector-compare-remove="${E(id)}" aria-label="从对比中移除${E(nameFor(id))}" title="移除对比">×</button></div>`).join('')}</div>${shown.length > 1 && !loading ? `<div class="sector-comparison-reading" data-sector-comparison-reading aria-live="off">${reading(clamp(hover ?? current.from + current.size - 1, current.from, current.from + current.size - 1))}</div><div class="sector-comparison-chart" data-sector-comparison-chart data-compare-start="${current.from}" data-compare-count="${current.size}" data-compare-total="${current.total}" data-compare-base="${E(model.baseDate)}" tabindex="0" aria-label="板块累计涨幅对比，滚轮缩放，左键或右键拖动，方向键逐日查看，Shift 加方向键移动窗口"><svg viewBox="0 0 1000 260" preserveAspectRatio="none" role="img" aria-label="${E(shown[0])}至${E(shown.at(-1))}板块累计涨幅">${[low, (low + high) / 2, high].map(value => `<line x1="0" x2="1000" y1="${y(value).toFixed(2)}" y2="${y(value).toFixed(2)}" class="sector-comparison-grid"/>`).join('')}<line x1="0" x2="1000" y1="${y(0).toFixed(2)}" y2="${y(0).toFixed(2)}" class="sector-comparison-zero"/>${model.series.map(item => `<path data-sector-comparison-line="${E(item.id)}" d="${linePath(item.values.slice(current.from, current.from + current.size), 1000, 260, low, high)}" fill="none" stroke="${colors[selected.indexOf(item.id)]}" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join('')}</svg><span class="sector-comparison-y top">${fmt(high)}</span><span class="sector-comparison-y zero" style="top:${y(0) / 260 * 100}%">0%</span><span class="sector-comparison-y bottom">${fmt(low)}</span><div data-sector-comparison-crosshair class="sector-comparison-crosshair" hidden></div></div><div class="sector-comparison-dates"><span>${E(shown[0])}</span><span>${E(shown[Math.floor((shown.length - 1) / 2)])}</span><span>${E(shown.at(-1))}</span></div><footer><span>滚轮缩放 · 左 / 右键拖动 · 当前 ${current.size} / ${current.total} 个交易日</span><button type="button" data-sector-comparison-reset>显示全部</button></footer>` : `<div class="sector-comparison-empty" role="status">${empty}</div>`}${notice ? `<p class="sector-comparison-notice" role="status">${E(notice)}</p>` : ''}${payload.error ? '<p class="sector-comparison-notice">部分报价读取失败；当前选取仅依据已读报价，完整排名尚未确认。</p>' : ''}${warnings.length ? `<div class="sector-comparison-notice" role="status">${warnings.map(warning => `<p>${E(warning)}</p>`).join('')}</div>` : ''}<p class="sector-comparison-note">通达信板块指数收盘价，统一从共同基准日计算涨幅；缺失日期断线。点击下方板块旁的 + 可自选，最多 8 个；刷新保留所选板块。</p></section>`;
  }
  function paint() {
    if (AT.state.page !== 'M02') return;
    const element = document.querySelector('[data-sector-comparison-panel]');
    const active = document.activeElement, focused = element?.contains(active), attr = ['data-sector-comparison-chart', 'data-sector-comparison-period', 'data-sector-comparison-top', 'data-sector-compare-remove', 'data-sector-comparison-reset'].find(name => active?.hasAttribute?.(name));
    const value = attr ? active.getAttribute(attr) : '';
    if (element) element.outerHTML = panel();
    if (focused && attr) for (const node of document.querySelectorAll(`[${attr}]`)) if (node.getAttribute(attr) === value) {node.focus({preventScroll: true}); break;}
  }
  function rowButton(item) {
    const id = idFor(item), checked = selected.includes(id);
    return `<button type="button" class="sector-comparison-add${checked ? ' selected' : ''}" data-sector-compare-toggle="${E(id)}" aria-pressed="${checked}" aria-label="${checked ? '移除' : '添加'}${E(item.name || id)}${checked ? '的' : '到'}走势对比" title="${checked ? '移除对比' : '添加到走势对比'}">${checked ? '−' : '+'}</button>`;
  }
  function endDrag(renderPending = true) {
    const previous = drag; drag = null; document.documentElement.classList.remove('sector-comparison-dragging');
    if (previous) {
      if (previous.moved) suppressClick = Date.now() + 180;
      if (previous.button === 2) suppressContext = Date.now() + 650;
      try {if (document.documentElement.hasPointerCapture(previous.pointerId)) document.documentElement.releasePointerCapture(previous.pointerId);} catch {}
    }
    const needed = deferredRender; deferredRender = false;
    if (needed && renderPending && AT.state.page === 'M02') setTimeout(rerender, 0);
  }
  function deferRender() {if (!drag || AT.state.page !== 'M02') return false; deferredRender = true; return true;}
  function invalidate() {
    endDrag(false); generation++; histories.clear(); members.clear(); refreshIds.clear(); selected = []; autoSelect = true; topCount = 5; payload = {}; notice = ''; baseKey = '';
    model = {dates: [], series: [], baseDate: ''}; start = 0; count = null; hover = null;
  }
  function refresh() {endDrag(false); generation++; histories.clear(); baseKey = ''; selected.forEach(id => refreshIds.add(id)); AT.sectorHistoryData?.requireFresh(selected); schedule();}
  function changeSelection(id) {
    endDrag(false); autoSelect = false; topCount = 0; notice = '';
    if (selected.includes(id)) selected = selected.filter(value => value !== id);
    else if (selected.length >= 8) {notice = '最多同时对比 8 个板块，请先移除一个。'; paint(); return;}
    else selected.push(id);
    baseKey = ''; model = {dates: [], series: [], baseDate: ''}; rebuild(); schedule(); rerender();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-sector-compare-toggle],[data-sector-compare-remove],[data-sector-comparison-top],[data-sector-comparison-period],[data-sector-comparison-reset]');
    if (!button || AT.state.page !== 'M02' || Date.now() < suppressClick) return;
    event.preventDefault(); endDrag(false);
    if (button.dataset.sectorCompareToggle || button.dataset.sectorCompareRemove) changeSelection(button.dataset.sectorCompareToggle || button.dataset.sectorCompareRemove);
    else if (button.dataset.sectorComparisonTop && readyToPick()) {pickTop(Number(button.dataset.sectorComparisonTop)); rerender();}
    else if (button.dataset.sectorComparisonPeriod) {period = Number(button.dataset.sectorComparisonPeriod); baseKey = ''; rebuild(); paint();}
    else if (button.hasAttribute('data-sector-comparison-reset')) {start = 0; count = null; hover = null; paint();}
  });
  document.addEventListener('wheel', event => {
    const chart = event.target.closest?.('[data-sector-comparison-chart]');
    if (!chart || AT.state.page !== 'M02' || !event.deltaY || event.ctrlKey || event.metaKey) return;
    const current = range(); if (current.total < 2) return;
    event.preventDefault(); if (drag) return;
    const rect = chart.getBoundingClientRect(), ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
    const size = clamp(Math.round(current.size * (event.deltaY > 0 ? 1.2 : .8)), Math.min(10, current.total), current.total);
    start = clamp(Math.round(current.from + ratio * (current.size - size)), 0, current.total - size); count = size; hover = null; paint();
  }, {passive: false});
  document.addEventListener('pointerdown', event => {
    const chart = event.target.closest?.('[data-sector-comparison-chart]');
    if (!chart || AT.state.page !== 'M02' || ![0, 2].includes(event.button) || event.pointerType === 'touch') return;
    const current = range(); if (current.total < 2) return;
    endDrag(false); event.preventDefault(); chart.focus({preventScroll: true});
    drag = {pointerId: event.pointerId, button: event.button, generation, x: event.clientX, y: event.clientY, start: current.from, size: current.size, total: current.total, width: Math.max(1, chart.getBoundingClientRect().width), moved: false};
    if (event.button === 2) suppressContext = Date.now() + 650;
  });
  function readAt(index) {
    hover = index; const current = range(), label = document.querySelector('[data-sector-comparison-reading]'), line = document.querySelector('[data-sector-comparison-crosshair]');
    if (label) label.innerHTML = reading(index);
    if (line) {line.hidden = false; line.style.left = `${(index - current.from) / Math.max(1, current.size - 1) * 100}%`;}
  }
  document.addEventListener('pointermove', event => {
    if (drag) {
      if (event.pointerId !== drag.pointerId) return;
      if (!(event.buttons & (drag.button === 0 ? 1 : 2)) || AT.state.page !== 'M02' || generation !== drag.generation) {endDrag(); return;}
      const delta = event.clientX - drag.x;
      if (!drag.moved) {
        if (Math.hypot(delta, event.clientY - drag.y) < 4) return;
        drag.moved = true; document.documentElement.classList.add('sector-comparison-dragging');
        try {document.documentElement.setPointerCapture(event.pointerId);} catch {}
      }
      event.preventDefault(); event.stopPropagation?.(); suppressClick = Date.now() + 180;
      const next = clamp(Math.round(drag.start - delta / drag.width * drag.size), 0, drag.total - drag.size);
      if (start !== next) {start = next; hover = null; paint();} return;
    }
    const chart = event.target.closest?.('[data-sector-comparison-chart]'); if (!chart || AT.state.page !== 'M02') return;
    const current = range(), rect = chart.getBoundingClientRect(), ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
    readAt(current.from + Math.round(ratio * Math.max(0, current.size - 1)));
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) document.addEventListener(type, event => {if (event.pointerId === drag?.pointerId) endDrag();});
  for (const type of ['blur', 'resize']) window.addEventListener(type, () => endDrag());
  document.addEventListener('visibilitychange', () => {if (document.hidden) endDrag();});
  document.addEventListener('contextmenu', event => {
    if (event.target.closest?.('[data-sector-comparison-chart]') || drag?.button === 2 || Date.now() < suppressContext) {event.preventDefault(); suppressContext = 0;}
  });
  document.addEventListener('keydown', event => {
    if (!event.target.matches?.('[data-sector-comparison-chart]') || AT.state.page !== 'M02' || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); const current = range();
    if (event.shiftKey && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
      start = clamp(current.from + (event.key === 'ArrowLeft' ? -1 : 1) * Math.max(1, Math.round(current.size / 5)), 0, current.total - current.size); hover = null; paint();
    } else {
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? current.total - 1 : clamp((hover ?? current.from + current.size - 1) + (event.key === 'ArrowLeft' ? -1 : 1), 0, current.total - 1);
      if (next < current.from || next >= current.from + current.size) {start = clamp(next < current.from ? next : next - current.size + 1, 0, current.total - current.size); paint();}
      readAt(next);
    }
  });
  AT.sectorComparison = {panel, rowButton, invalidate, refresh, endDrag, deferRender};
})();
