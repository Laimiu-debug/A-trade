/* Sector charts use the terminal's own index history, loaded only for visible rows. */
(() => {
  if (!AT.isDesktop) return;
  // The row chart and comparison must read the same response after a refresh.
  // Retain resolved promises as well as pending ones so their independent queues
  // share a single source snapshot for each sector in this refresh generation.
  const sharedHistories = new Map();
  let forceHistoryIds = new Set();
  AT.sectorHistoryData = {
    reset(force = false, ids = []) {forceHistoryIds = force ? new Set([...sharedHistories.keys(), ...ids].filter(Boolean)) : new Set(); sharedHistories.clear();},
    requireFresh(ids) {for (const id of ids) if (!sharedHistories.has(id)) forceHistoryIds.add(id);},
    read(id) {
      if (!sharedHistories.has(id)) sharedHistories.set(id, window.atradeDesktop.market('market.sector.history', {id, limit: 250, ...(forceHistoryIds.delete(id) ? {refresh: true} : {})}));
      return sharedHistories.get(id);
    }
  };
  const E = AT.escape, histories = new Map(), names = new Map();
  let visible = [], selected = '', running = false, generation = 0, scheduled = false;
  let count = 60, start = null, hover = null, drag = null, suppressClick = 0, suppressContext = 0, deferredRender = false;
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  const number = n => Number.isFinite(n) ? n.toLocaleString('zh-CN', {maximumFractionDigits: 2, minimumFractionDigits: 2}) : '—';
  const rowsFor = id => histories.get(id)?.data?.rows || [];
  const rerender = () => {if (AT.state.page === 'M02') {if (AT.marketUI?.refreshVisible) AT.marketUI.refreshVisible(); else {const focus = AT.marketExplorer?.captureFocus(); AT.render(); AT.marketExplorer?.restoreFocus(focus);}}};
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => {scheduled = false; loadNext();}, 0);
  }
  async function loadNext() {
    if (running || AT.state.page !== 'M02') return;
    const id = [selected, ...visible].find(id => id && !histories.has(id));
    if (!id) return;
    running = true; const serial = generation;
    histories.set(id, {busy: true});
    updateHistory(id);
    try {
      const response = await AT.sectorHistoryData.read(id);
      if (serial !== generation) return;
      if (!response?.ok) throw new Error(response?.error?.message || response?.error || '板块走势读取失败');
      histories.set(id, {data: response.data});
    } catch (error) {
      if (serial === generation) histories.set(id, {error: error.message || '板块走势读取失败'});
    } finally {
      running = false;
      if (serial === generation) updateHistory(id);
      schedule();
    }
  }
  function setVisible(items) {
    visible = items.map(item => item.id || item.symbol);
    items.forEach(item => names.set(item.id || item.symbol, item.name));
    schedule();
  }
  function path(rows, width, height, margin = 3) {
    if (rows.length < 2) return '';
    const lo = Math.min(...rows.map(row => row.close)), hi = Math.max(...rows.map(row => row.close)), span = hi - lo || 1;
    return rows.map((row, i) => `${i ? 'L' : 'M'}${(margin + i / (rows.length - 1) * (width - margin * 2)).toFixed(2)},${(height - margin - (row.close - lo) / span * (height - margin * 2)).toFixed(2)}`).join(' ');
  }
  function rowParts(item) {
    const id = item.id || item.symbol, entry = histories.get(id), rows = rowsFor(id).slice(-20);
    const state = rows.length > 1 ? `${rows[0].date} 至 ${rows.at(-1).date}` : entry?.busy || !entry ? '正在读取指数日线' : '指数日线暂缺 · 点击查看';
    return {id, title: `${item.name} · ${state}`, body: rows.length > 1 ? `<svg viewBox="0 0 110 32" class="${rows.at(-1).close >= rows[0].close ? 'market-explorer-up' : 'market-explorer-down'}" aria-hidden="true"><path d="${path(rows, 110, 32)}" fill="none" stroke="currentColor" stroke-width="1.6"/></svg><small>${E(rows.at(-1).date.slice(5))}${entry.data.stale ? ' · 旧缓存' : ''}</small>` : `<span>${entry?.busy || !entry ? '读取中…' : '暂无日线'}</span>`};
  }
  function row(item) {
    const parts = rowParts(item);
    return `<button type="button" class="sector-trend-row" data-sector-trend="${E(parts.id)}" title="${E(parts.title)}" aria-label="查看${E(item.name)}板块指数走势">${parts.body}</button>`;
  }
  function updateHistory(id) {
    if (AT.state.page !== 'M02') return;
    // Do not replace the page (or focused buttons/inputs) as each row arrives.
    const parts = rowParts({id, name: names.get(id) || id});
    for (const button of document.querySelectorAll('[data-sector-trend]')) {
      if (button.dataset.sectorTrend !== id) continue;
      button.innerHTML = parts.body; button.title = parts.title;
    }
    if (id === selected) paint();
  }
  function range() {
    const rows = rowsFor(selected), size = Math.min(count, rows.length), from = clamp(start ?? rows.length - size, 0, Math.max(0, rows.length - size));
    return {rows: rows.slice(from, from + size), from, size, total: rows.length};
  }
  function reading(row) {
    if (!row) return '移动鼠标查看日期与指数点位';
    return `${E(row.date)} <strong>${number(row.close)}</strong><span>开 ${number(row.open)} · 高 ${number(row.high)} · 低 ${number(row.low)}</span>`;
  }
  function panel() {
    if (!selected) return '';
    const entry = histories.get(selected), data = entry?.data, current = range(), rows = current.rows;
    const warnings = [...(data?.warnings || []), ...(entry?.error ? [entry.error] : [])];
    const change = rows.length > 1 ? (rows.at(-1).close / rows[0].close - 1) * 100 : null;
    const lo = rows.length ? Math.min(...rows.map(row => row.close)) : null, hi = rows.length ? Math.max(...rows.map(row => row.close)) : null;
    return `<section class="panel sector-history-panel" data-sector-history-panel aria-label="板块指数走势"><header><div><h2>${E(names.get(selected) || data?.name || selected)} <small>板块指数</small></h2><p>${E(data?.sourceName || '通达信板块指数日线')} · ${E(selected)}${data?.dataAsOf ? ` · 更新至 ${E(data.dataAsOf)}` : ''}${data?.stale ? ' · 旧缓存' : ''}</p></div><div class="sector-history-actions">${[30, 60, 120, 250].map(n => `<button type="button" data-sector-history-count="${n}" aria-pressed="${count === n}" class="${count === n ? 'selected' : ''}">${n} 日</button>`).join('')}<button type="button" data-sector-history-close aria-label="收起板块走势图">收起</button></div></header>${rows.length > 1 ? `<div class="sector-history-stats"><span data-sector-history-reading>${reading(rows[clamp(hover ?? rows.length - 1, 0, rows.length - 1)])}</span><span class="market-explorer-${change >= 0 ? 'up' : 'down'}">区间 ${change >= 0 ? '+' : ''}${number(change)}%</span></div><div class="sector-history-chart" data-sector-history-chart tabindex="0" aria-label="板块指数日线，滚轮缩放，左键或右键拖动，方向键移动窗口"><span class="sector-history-y top">${number(hi)}</span><span class="sector-history-y bottom">${number(lo)}</span><svg viewBox="0 0 1000 200" preserveAspectRatio="none" role="img" aria-label="${E(rows[0].date)}至${E(rows.at(-1).date)}收盘指数走势"><path d="${path(rows, 1000, 200, 8)}" fill="none" stroke="var(--primary)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg><div class="sector-history-crosshair" data-sector-history-crosshair hidden></div></div><div class="sector-history-dates"><span>${E(rows[0].date)}</span><span>${E(rows[Math.floor((rows.length - 1) / 2)].date)}</span><span>${E(rows.at(-1).date)}</span></div><footer><span>滚轮缩放 · 左键 / 右键拖动 · 当前 ${current.size} / ${current.total} 个交易日</span><button type="button" data-sector-history-reset>显示全部</button></footer>` : `<div class="sector-history-empty">${entry?.busy || !entry ? '正在读取真实板块指数日线…' : '此板块暂无可展示的指数日线'}</div>`}${warnings.length ? `<div class="sector-history-warnings" role="status">${warnings.map(w => `<p>${E(w)}</p>`).join('')}</div>` : ''}<p class="sector-history-note">走势图为通达信该板块指数的收盘点位，缺失日期不补造；区间涨跌按首尾收盘计算。</p></section>`;
  }
  function paint() {
    const element = document.querySelector('[data-sector-history-panel]');
    const focused = document.activeElement?.matches?.('[data-sector-history-chart]');
    if (element) element.outerHTML = panel();
    if (focused) document.querySelector('[data-sector-history-chart]')?.focus({preventScroll: true});
  }
  function endDrag(renderPending = true) {
    const previous = drag; drag = null;
    document.documentElement.classList.remove('sector-history-dragging');
    if (previous) {
      if (previous.moved) suppressClick = Date.now() + 180;
      if (previous.button === 2) suppressContext = Date.now() + 650;
      try {if (document.documentElement.hasPointerCapture(previous.pointerId)) document.documentElement.releasePointerCapture(previous.pointerId);} catch {}
    }
    const needed = deferredRender; deferredRender = false;
    if (needed && renderPending && AT.state.page === 'M02') setTimeout(rerender, 0);
  }
  function deferRender() {
    if (!drag || AT.state.page !== 'M02') return false;
    deferredRender = true; return true;
  }
  function invalidate() {endDrag(false); generation++; AT.sectorHistoryData.reset(); histories.clear(); names.clear(); visible = []; selected = ''; start = null; hover = null;}
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-sector-trend],[data-sector-history-count],[data-sector-history-close],[data-sector-history-reset]');
    if (!button || Date.now() < suppressClick) return;
    endDrag(false);
    if (button.dataset.sectorTrend) {selected = button.dataset.sectorTrend; count = 60; start = null; hover = null; schedule(); rerender(); (typeof requestAnimationFrame === 'function' ? requestAnimationFrame : setTimeout)(() => document.querySelector('[data-sector-history-panel]')?.scrollIntoView({behavior: 'smooth', block: 'start'}));}
    else if (button.hasAttribute('data-sector-history-close')) {selected = ''; rerender();}
    else if (button.dataset.sectorHistoryCount) {count = Number(button.dataset.sectorHistoryCount); start = null; hover = null; paint();}
    else {count = Math.max(2, rowsFor(selected).length); start = 0; hover = null; paint();}
  });
  document.addEventListener('wheel', event => {
    const chart = event.target.closest('[data-sector-history-chart]');
    if (!chart || AT.state.page !== 'M02' || !event.deltaY || event.ctrlKey || event.metaKey) return;
    if (drag) {event.preventDefault(); return;}
    const current = range(); if (current.total < 2) return;
    event.preventDefault();
    const rect = chart.getBoundingClientRect(), ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const next = clamp(Math.round(current.size * (event.deltaY > 0 ? 1.2 : .8)), Math.min(10, current.total), current.total);
    start = clamp(Math.round(current.from + ratio * (current.size - next)), 0, current.total - next); count = next; hover = null; paint();
  }, {passive: false});
  document.addEventListener('pointerdown', event => {
    const chart = event.target.closest?.('[data-sector-history-chart]');
    if (!chart || AT.state.page !== 'M02' || ![0, 2].includes(event.button) || event.pointerType === 'touch') return;
    const current = range(); if (!current.size) return;
    endDrag(false);
    event.preventDefault(); chart.focus({preventScroll: true});
    drag = {pointerId: event.pointerId, button: event.button, symbol: selected, generation,
      x: event.clientX, y: event.clientY, start: current.from, size: current.size, total: current.total, width: chart.getBoundingClientRect().width, moved: false};
    if (event.button === 2) suppressContext = Date.now() + 650;
  });
  document.addEventListener('pointermove', event => {
    if (drag) {
      if (event.pointerId !== drag.pointerId) return;
      if (!(event.buttons & (drag.button === 0 ? 1 : 2)) || AT.state.page !== 'M02' || selected !== drag.symbol || generation !== drag.generation) {endDrag(); return;}
      const delta = event.clientX - drag.x;
      if (!drag.moved) {
        if (Math.hypot(delta, event.clientY - drag.y) < 4) return;
        drag.moved = true; document.documentElement.classList.add('sector-history-dragging');
        // The page and chart can repaint while dragging; capture on the stable root.
        try {document.documentElement.setPointerCapture(event.pointerId);} catch {}
      }
      event.preventDefault(); event.stopPropagation?.();
      suppressClick = Date.now() + 180;
      const next = clamp(Math.round(drag.start - delta / drag.width * drag.size), 0, drag.total - drag.size);
      if (start !== next) {start = next; hover = null; paint();}
      return;
    }
    const chart = event.target.closest?.('[data-sector-history-chart]'); if (!chart || AT.state.page !== 'M02') return;
    const current = range(), rect = chart.getBoundingClientRect(), ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    hover = Math.round(ratio * (current.size - 1));
    const label = document.querySelector('[data-sector-history-reading]'), line = chart.querySelector('[data-sector-history-crosshair]');
    if (label) label.innerHTML = reading(current.rows[hover]);
    if (line) {line.hidden = false; line.style.left = `${ratio * 100}%`;}
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) document.addEventListener(type, event => {
    if (event.pointerId === drag?.pointerId) endDrag();
  });
  for (const type of ['blur', 'resize']) window.addEventListener(type, () => endDrag());
  document.addEventListener('visibilitychange', () => {if (document.hidden) endDrag();});
  document.addEventListener('contextmenu', event => {
    if (event.target.closest?.('[data-sector-history-chart]') || drag?.button === 2 || Date.now() < suppressContext) {event.preventDefault(); suppressContext = 0;}
  });
  document.addEventListener('keydown', event => {
    if (!event.target.matches('[data-sector-history-chart]') || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); const current = range();
    start = event.key === 'Home' ? 0 : event.key === 'End' ? current.total - current.size : clamp(current.from + (event.key === 'ArrowLeft' ? -1 : 1) * Math.max(1, Math.round(current.size / 5)), 0, current.total - current.size);
    hover = null; paint(); document.querySelector('[data-sector-history-chart]')?.focus({preventScroll: true});
  });
  function refresh() {endDrag(false); generation++; AT.sectorHistoryData.reset(true, [selected, ...visible]); histories.clear(); schedule();}
  AT.sectorTrends = {setVisible, row, panel, invalidate, refresh, endDrag, deferRender};
})();
