/* Source-backed market observations and optional chart studies. */
(() => {
  const E = AT.escape;
  const finite = value => value !== null && value !== undefined && Number.isFinite(Number(value));
  const n = (value, digits = 2) => finite(value) ? Number(value).toLocaleString('zh-CN', {maximumFractionDigits: digits, minimumFractionDigits: digits}) : '—';
  const pct = (value, ratio = false) => finite(value) ? `${n(Number(value) * (ratio ? 100 : 1), 1)}%` : '—';
  const styles = {insufficient: '#9aa8be', unconfirmed: '#8b9caa', defensive: '#697c9c', boards: '#e88959', speculative: '#bc79dc', trend: '#427ae9', rotation: '#57a9aa'};
  let chosenDay = '';
  const enabled = {ma: true, force: true, signals: true, box: false, ...AT.marketPreferences?.getStudies()};
  let displayedTimeline = [];
  let styleDateNote = '';
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  let historyWindow = null, historySignature = '', historyOptions = {};
  let historyFrame = 0, historyDrag = null, suppressHistoryClickUntil = 0;
  let wheelRemainder = 0, lastWheelAt = 0;
  function historyRange() {
    const total = displayedTimeline.length;
    const count = clamp(historyWindow?.count ?? total, Math.min(10, total), total);
    return {start: clamp(historyWindow?.start ?? 0, 0, total - count), count, total};
  }
  function revealHistoryDay(day) {
    const index = displayedTimeline.findIndex(item => item.date === day), range = historyRange();
    if (index < 0 || index >= range.start && index < range.start + range.count) return;
    historyWindow = {count: range.count, start: clamp(index < range.start ? index : index - range.count + 1, 0, range.total - range.count)};
  }
  function redrawHistory() {
    const root = document.querySelector('[data-market-style-explorer]');
    if (!root || AT.state.page !== 'M01') return;
    const focus = document.activeElement?.dataset.marketStyleZoom;
    root.innerHTML = historyView(displayedTimeline, historyOptions.day, historyOptions.days, historyOptions.busy);
    if (focus) root.querySelector(`[data-market-style-zoom="${focus}"]`)?.focus({preventScroll: true});
  }
  function queueHistoryRedraw() {
    if (!historyFrame) historyFrame = requestAnimationFrame(() => {historyFrame = 0; redrawHistory();});
  }
  function zoomHistory(factor, ratio = .5) {
    const range = historyRange();
    if (!range.total) return false;
    const count = clamp(Math.round(range.count * factor), Math.min(10, range.total), range.total);
    if (count === range.count) return false;
    const anchor = range.start + clamp(ratio, 0, 1) * range.count;
    historyWindow = {count, start: clamp(Math.round(anchor - ratio * count), 0, range.total - count)};
    return true;
  }
  function historyView(timeline, day, historyDays, busy) {
    const signature = `${historyDays}|${timeline.length}|${timeline[0]?.date}|${timeline.at(-1)?.date}`;
    if (signature !== historySignature) {endHistoryDrag(); historyWindow = null; wheelRemainder = 0; historySignature = signature;}
    displayedTimeline = timeline;
    historyOptions = {day, days: historyDays, busy};
    const {start, count, total} = historyRange(), visible = timeline.slice(start, start + count);
    const first = timeline[0]?.date, last = timeline.at(-1)?.date;
    const selected = Math.max(0, timeline.findIndex(item => item.date === day));
    const focusIndex = clamp(selected - start, 0, count - 1);
    let connected = false;
    const path = visible.map((item, index) => {
      const value = item.metrics?.breadth;
      if (!finite(value) || item.key === 'insufficient') {connected = false; return '';}
      const command = connected ? 'L' : 'M'; connected = true;
      return `${command}${(index + .5) / count * 1000},${105 - Number(value) * 95}`;
    }).join(' ');
    return `<div class="market-style-history-controls"><div class="market-style-ranges" aria-label="风格历史范围">${[60, 120, 250, 500, 1000].map(value => `<button type="button" data-market-history-days="${value}" aria-pressed="${historyDays === value}" ${busy ? 'disabled' : ''}>${value} 日</button>`).join('')}</div><div class="market-style-date-controls"><button type="button" data-market-style-step="-1" aria-label="前一交易日" ${!total || selected === 0 ? 'disabled' : ''}>前一日</button><input type="date" data-market-style-date aria-label="查看风格日期" min="${E(first || '')}" max="${E(last || '')}" value="${E(day || '')}" ${!total ? 'disabled' : ''}><button type="button" data-market-style-step="1" aria-label="后一交易日" ${!total || selected === total - 1 ? 'disabled' : ''}>后一日</button></div></div>${count ? `<div class="market-style-chart-heading"><span>MA20 之上占比 · 风格时间轴</span><div class="market-style-zoom-controls"><span>滚轮缩放 · 拖动查看</span><button type="button" data-market-style-zoom="out" aria-label="缩小时间轴" title="缩小时间轴" ${count === total ? 'disabled' : ''}>−</button><button type="button" data-market-style-zoom="in" aria-label="放大时间轴" title="放大时间轴" ${count <= Math.min(10, total) ? 'disabled' : ''}>＋</button><button type="button" data-market-style-zoom="reset" ${count === total ? 'disabled' : ''}>显示全部</button></div></div><div class="market-style-history" data-market-style-chart data-style-start="${start}" data-style-count="${count}" data-style-total="${total}" aria-label="历史风格与市场广度，可滚轮缩放和拖动"><svg viewBox="0 0 1000 142" preserveAspectRatio="none" aria-hidden="true"><path d="M0 10H1000M0 57.5H1000M0 105H1000" class="market-gridline"/><path d="${path}" fill="none" stroke="#4c82cd" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg><div class="market-style-days">${visible.map((item, index) => `<button type="button" data-market-style-day="${E(item.date)}" style="--style-color:${styles[item.key] || styles.insufficient}" class="${day === item.date ? 'active' : ''}" aria-pressed="${day === item.date}" tabindex="${index === focusIndex ? 0 : -1}" aria-label="${E(item.date + ' ' + item.label)}" title="${E(item.date + ' · ' + item.label)} · MA20之上 ${pct(item.metrics?.breadth, true)}"><i></i></button>`).join('')}</div></div><div class="market-style-axis">${[...new Set([0, Math.floor((count - 1) / 4), Math.floor((count - 1) / 2), Math.floor((count - 1) * .75), count - 1])].map(index => `<span>${E(visible[index].date)}</span>`).join('')}</div><p class="market-style-visible-range">${E(visible[0].date)} — ${E(visible.at(-1).date)} · 当前 ${count} / 全部 ${total} 个交易日</p><div class="market-analysis-legend">${Object.entries(styles).filter(([key]) => visible.some(item => item.key === key)).map(([key, color]) => `<span><i style="background:${color}"></i>${E(visible.find(item => item.key === key).label)}</span>`).join('')}</div>` : ''}${styleDateNote ? `<p class="market-style-date-note" role="status">${E(styleDateNote)}</p>` : ''}`;
  }
  function overview({data, busy, error, historyDays = 250}) {
    const timeline = data?.timeline || [], current = timeline.find(item => item.date === chosenDay) || data?.style;
    const day = timeline.find(item => item.date === chosenDay)?.date || data?.asOf;
    const metrics = current?.metrics || {};
    return `<section class="panel market-analysis-style"><div class="market-section-heading"><div><h2>市场风格观察</h2><p class="market-subtle">市场广度、趋势与活跃程度 · 基于本地日线</p></div><button class="btn small" data-market-action="refresh-research" ${busy ? 'disabled' : ''}>${busy ? '正在统计…' : '更新观察'}</button></div>${error ? `<p class="market-warning" role="alert">${E(error)}</p>` : ''}${!data ? `<div class="market-empty compact"><p>${busy ? '正在读取本地股票，计算同一交易日的市场广度与风格…' : '选择并读取本地日线后，这里显示市场风格与判定依据。'}</p></div>` : `<div class="market-analysis-lead"><div><span class="market-analysis-date">${E(day || '—')} · 收盘观察${data.cached ? ' · 已保存快照' : ''}</span><h3 style="--style-color:${styles[current?.key] || styles.insufficient}">${E(current?.label || '数据不足')}</h3><p>${E(current?.reason || '')}</p>${current?.pendingKey ? '<small>新的风格仍在连续确认中</small>' : ''}</div><div class="market-analysis-coverage"><strong>${n(metrics.sampleCount, 0)} <small>/ ${n(metrics.expectedCount, 0)}</small></strong><span>可观察股票 · 覆盖率 ${pct(metrics.coverage, true)}</span><p>本次统计日 ${E(data.asOf || '—')}</p></div></div><div class="market-analysis-facts">${[
      ['站上 MA20', pct(metrics.breadth, true)], ['20日中位涨幅', pct(metrics.medianReturn20)], ['上涨 / 下跌', `${n(metrics.advanceCount, 0)} / ${n(metrics.declineCount, 0)}`], ['近似涨停 / 跌停', `${n(metrics.limitUpApprox, 0)} / ${n(metrics.limitDownApprox, 0)}`], ['连板 / 最高高度', `${n(metrics.multiBoardApprox, 0)} / ${n(metrics.maxBoardHeight, 0)}`], ['高弹性成交占比', pct(metrics.wildAmountShare, true)]
    ].map(([label, value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('')}</div>${leadershipEvidence(metrics.leadership || current?.leadership?.metrics, day)}<div data-market-style-explorer>${historyView(timeline, day, historyDays, busy)}</div><p class="market-analysis-method">上方风格标签描述市场广度，接力、趋势和风险证据分别展示，不合成为单一评分。趋势扩散：MA20 之上 ≥55%，20日中位涨幅 &gt;2%；连板活跃、高弹性与退潮按广度及量价规则判定，普通风格变化需连续两日确认；旧标签最多沿用20个交易日，过期显示待确认。仅作价格与成交活跃度观察。</p><div class="market-analysis-notes">${(data.warnings || []).map(message => `<p>${E(message)}</p>`).join('')}</div>`}</section>`;
  }
  function leadershipEvidence(metrics, day) {
    if (!metrics) return `<div class="market-leader-entry"><p>连板接力、昨日高标反馈与趋势成交占比可在连板龙头中并排回看。</p><button type="button" data-nav="T08" data-leader-day="${E(day || '')}">查看连板龙头 →</button></div>`;
    const promotion = metrics.promotion || {}, all = promotion.all || {}, high = metrics.highBoardPromotion || promotion.high || {}, trend = metrics.trend || {}, premium = metrics.premium || {}, risk = metrics.risk || {};
    const rate = group => group?.observed > 0 && finite(group.promoted) ? pct(group.promoted / group.observed, true) : '—';
    const trendPct = key => trend.eligible > 0 ? pct(trend[key], true) : '—';
    const sample = group => `${n(group?.promoted, 0)} / ${n(group?.observed, 0)} 晋级 · 应观察 ${n(group?.eligible, 0)} · 缺失 ${n(group?.missing, 0)}`;
    return `<section class="market-leader-evidence" data-market-leader-evidence><header><div><h3>接力、趋势与风险</h3><p>与上方所选交易日同步 · 看证据变化，再判断风格切换</p></div><button type="button" data-nav="T08" data-leader-day="${E(day || '')}">查看当日连板龙头 →</button></header><div class="market-leader-evidence-grid"><article><span class="market-leader-evidence-label">连板接力</span><strong>${rate(all)}<small> 昨日连板晋级率</small></strong><p>${sample(all)}</p><dl><div><dt>首板 → 2 板</dt><dd>${rate(promotion.firstToSecond)}</dd></div><div><dt>2 板 → 3 板</dt><dd>${rate(promotion.secondToThird)}</dd></div><div><dt>昨日高标继续晋级</dt><dd>${rate(high)}</dd></div></dl><small>高标 ${sample(high)}</small></article><article><span class="market-leader-evidence-label">趋势活跃度</span><strong>${trendPct('amountShare')}<small> 候选成交占比</small></strong><p>候选 ${n(trend.count, 0)} / 连续 60 日样本 ${n(trend.eligible, 0)}</p><dl><div><dt>MA20 之上</dt><dd>${trendPct('aboveMA20Ratio')}</dd></div><div><dt>MA60 之上</dt><dd>${trendPct('aboveMA60Ratio')}</dd></div><div><dt>高弹性成交占比</dt><dd>${trend.eligible > 0 ? pct(metrics.highElasticity?.amountShare, true) : '—'}</dd></div></dl><small>占比分母均为连续 60 日有成交样本；反映交易活跃度。</small></article><article><span class="market-leader-evidence-label">昨日强势股反馈</span><strong>${premium.observed > 0 ? pct(premium.median) : '—'}<small> 昨涨停今日中位表现</small></strong><p>可观察 ${n(premium.observed, 0)} / ${n(premium.eligible, 0)} · 缺失 ${n(premium.missing, 0)}</p><dl><div><dt>昨日连板断板</dt><dd>${n(metrics.brokenCount, 0)} 只</dd></div><div><dt>昨日高标断板</dt><dd>${n(metrics.leaderBreakCount, 0)} 只</dd></div><div><dt>近似跌停 / 下跌占比</dt><dd>${n(risk.limitDownCount, 0)} / ${pct(risk.declineRatio, true)}</dd></div></dl><small>缺失、无成交或涨跌幅规则待核验不计为断板。</small></article></div><div class="market-leader-evidence-candidates"><span>当日最高连板候选</span><strong>${(metrics.leaders || []).length ? (metrics.leaders || []).map(item => `${E(item.name || item.symbol)} · ${n(item.height, 0)} 板`).join('　') : '暂无可观察的连板候选'}</strong></div><p class="market-leader-evidence-method">连板按本地日线与名义涨停价近似识别；趋势候选需站上 MA20 / MA60，且最近 5 日的 20 日收益均为正。高弹性指近 10 日涨幅超过 30%。历史 ST 身份与新股特殊规则未完整覆盖，候选需结合 K 线核验。</p></section>`;
  }
  function controls() {
    return `<div class="market-analysis-controls" aria-label="K线图形分析">${[['ma', '均线 MA5 / 10 / 20'], ['force', '主散量能'], ['signals', '金叉 / 转黄'], ['box', '箱体突破']].map(([key, label]) => `<button type="button" data-market-study="${key}" aria-pressed="${enabled[key]}" class="${enabled[key] ? 'active' : ''}">${E(label)}</button>`).join('')}<small>主散量能为量价估算，非实际资金流</small></div>`;
  }
  function selected(row) {
    if (!row) return '';
    return `<div class="market-analysis-readout">${[['ma5', 'MA5', '#d99843'], ['ma10', 'MA10', '#996bc1'], ['ma20', 'MA20', '#468dda']].map(([key, label, color]) => `<span style="color:${color}">${label} <b>${n(row[key])}</b></span>`).join('')}<span>5日均量比 <b>${n(row.volumeRatio5)}${finite(row.volumeRatio5) ? ' 倍' : ''}</b></span><span>主力线 <b>${n(row.mainForce, 1)}</b></span><span>散户线 <b>${n(row.retailForce, 1)}</b></span><span class="market-analysis-signal">${[row.goldenCross ? '主力金叉' : '', row.purpleToYellow ? '紫转黄' : '', row.boxBreakout ? '箱体突破' : '', row.boxSignal ? '突破后确认' : ''].filter(Boolean).join(' · ') || '本根未触发观察信号'}</span></div>`;
  }
  function scaleValues(rows) {
    return rows.flatMap(row => [...(enabled.ma ? [row.ma5, row.ma10, row.ma20] : []), ...(enabled.box ? [row.boxHigh, row.boxLow] : [])]).filter(finite).map(Number);
  }
  function pathFor(rows, key, x, y) {
    let segment = false;
    return rows.map((row, index) => {if (!finite(row?.[key])) {segment = false; return '';}
      const command = segment ? 'L' : 'M'; segment = true; return `${command}${x(index)},${y(Number(row[key]))}`;
    }).join(' ');
  }
  function price({rows, studies, start, count, left, step, top, bottom, priceY}) {
    const visible = studies.slice(start, start + count), x = i => left + step * (i + .5);
    const lines = enabled.ma ? [['ma5', '#d99843'], ['ma10', '#996bc1'], ['ma20', '#468dda']].map(([key, color]) => `<path d="${pathFor(visible, key, x, priceY)}" stroke="${color}" class="market-analysis-line" data-market-study-line="${key}"/>`).join('') : '';
    const box = enabled.box ? [['boxHigh', '#23a69d'], ['boxLow', '#8391ad']].map(([key, color]) => `<path d="${pathFor(visible, key, x, priceY)}" stroke="${color}" class="market-analysis-line market-analysis-box" data-market-study-line="${key}"/>`).join('') : '';
    const marks = visible.map((row, index) => {
      const labels = [...(enabled.signals ? [row.goldenCross ? '金叉' : '', row.purpleToYellow ? '转黄' : ''] : []), ...(enabled.box ? [row.boxBreakout ? '突破' : '', row.boxSignal ? '确认' : ''] : [])].filter(Boolean);
      if (!labels.length) return '';
      const cy = Math.max(top + 8, Math.min(bottom - 8, priceY(rows[start + index].low) + 13));
      return `<g class="market-analysis-marker" data-market-signal="${E(labels.join(' / '))}"><circle cx="${x(index)}" cy="${cy}" r="${count > 120 ? 2.5 : 4}"/><title>${E(row.date)} · ${E(labels.join(' / '))} · 收盘后观察信号</title>${count <= 60 ? `<text x="${x(index)}" y="${Math.min(bottom, cy + 16)}" text-anchor="middle">${E(labels.join('/'))}</text>` : ''}</g>`;
    }).join('');
    return `<g class="market-analysis-overlays" pointer-events="none">${lines}${box}${marks}</g>`;
  }
  function forcePanel({studies, start, count, selectedIndex, volumeUnit}) {
    if (!enabled.force || !count) return '';
    const visible = studies.slice(start, start + count), values = visible.flatMap(row => [row.mainForce, row.retailForce]).filter(finite).map(Number);
    const max = Math.max(1, ...values) * 1.08, left = 24, step = 934 / count, x = i => left + step * (i + .5), y = value => 150 - value / max * 122;
    const marks = visible.map((row, index) => `<g data-market-index="${start + index}">${finite(row.mainForce) ? `<rect x="${x(index) - Math.min(24, step * .3)}" y="${y(row.mainForce)}" width="${Math.min(48, step * .6)}" height="${150 - y(row.mainForce)}" fill="${row.mainForceState === 'falling' ? '#9278ca' : '#e8b354'}" opacity=".28"/>` : ''}<rect x="${left + step * index}" y="26" width="${step}" height="124" fill="transparent"/></g>`).join('');
    return `<section class="market-analysis-force" data-market-force-panel><div class="market-analysis-force-heading"><strong>主散量能</strong><span><i style="background:#dca446"></i>主力线 <i style="background:#8d75c5"></i>散户线 · ${E(volumeUnit === '股' ? '手' : volumeUnit || '原始单位')}</span></div><svg viewBox="0 0 1040 180" class="market-candles" tabindex="0" role="img" aria-label="主散量能，与日K同步缩放和拖动" data-market-indicator-chart data-market-zoom="daily" data-chart-start="${start}" data-chart-count="${count}"><path d="M24 150H958M24 89H958M24 28H958" class="market-gridline"/>${marks}<g pointer-events="none"><path d="${pathFor(visible, 'mainForce', x, y)}" stroke="#dca446" class="market-analysis-line"/><path d="${pathFor(visible, 'retailForce', x, y)}" stroke="#8d75c5" class="market-analysis-line"/><path data-market-force-crosshair d="M${x(Math.max(0, Math.min(count - 1, selectedIndex - start)))} 26V150" class="market-crosshair"/></g><text x="970" y="32" class="market-axis-label">${n(max, 0)}</text>${!values.length ? '<text x="490" y="90" text-anchor="middle" class="market-axis-label">历史不足，等待指标暖机</text>' : ''}</svg></section>`;
  }
  function endHistoryDrag() {
    const drag = historyDrag;
    if (!drag) return;
    historyDrag = null;
    document.documentElement.classList.remove('market-style-dragging');
    if (drag.moved) suppressHistoryClickUntil = performance.now() + 650;
    if (document.documentElement.hasPointerCapture(drag.pointerId)) document.documentElement.releasePointerCapture(drag.pointerId);
  }
  document.addEventListener('wheel', event => {
    const chart = event.target.closest?.('[data-market-style-chart]');
    if (!chart || AT.state.page !== 'M01' || !event.deltaY || event.ctrlKey || event.metaKey) return;
    const rect = chart.getBoundingClientRect();
    if (!rect.width || event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) return;
    event.preventDefault(); event.stopPropagation();
    if (historyDrag) return;
    const now = performance.now();
    if (now - lastWheelAt > 180 || Math.sign(wheelRemainder) !== Math.sign(event.deltaY)) wheelRemainder = 0;
    lastWheelAt = now;
    wheelRemainder += event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
    const factor = Math.exp(clamp(wheelRemainder, -240, 240) * .002);
    if (zoomHistory(factor, (event.clientX - rect.left) / rect.width)) {wheelRemainder = 0; queueHistoryRedraw();}
    else {
      const range = historyRange();
      if (factor > 1 && range.count === range.total || factor < 1 && range.count === Math.min(10, range.total)) wheelRemainder = 0;
    }
  }, {passive: false});
  document.addEventListener('pointerdown', event => {
    const chart = event.target.closest?.('[data-market-style-chart]');
    if (!chart || AT.state.page !== 'M01' || ![0, 2].includes(event.button) || event.pointerType === 'touch') return;
    endHistoryDrag();
    const rect = chart.getBoundingClientRect();
    if (!rect.width) return;
    historyDrag = {pointerId: event.pointerId, button: event.button, x: event.clientX, y: event.clientY, width: rect.width, range: historyRange(), moved: false};
    event.preventDefault();
  }, true);
  document.addEventListener('pointermove', event => {
    const drag = historyDrag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (AT.state.page !== 'M01' || !(event.buttons & (drag.button === 0 ? 1 : 2))) {endHistoryDrag(); return;}
    if (!drag.moved) {
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) return;
      drag.moved = true;
      document.documentElement.classList.add('market-style-dragging');
      try {document.documentElement.setPointerCapture(event.pointerId);} catch {}
    }
    event.preventDefault(); event.stopPropagation();
    const shift = (event.clientX - drag.x) / drag.width * drag.range.count;
    historyWindow = {count: drag.range.count, start: clamp(Math.round(drag.range.start - shift), 0, drag.range.total - drag.range.count)};
    queueHistoryRedraw();
  }, true);
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) document.addEventListener(type, event => {
    if (historyDrag?.pointerId === event.pointerId) endHistoryDrag();
  }, true);
  for (const type of ['blur', 'resize']) window.addEventListener(type, endHistoryDrag);
  document.addEventListener('visibilitychange', () => {if (document.hidden) endHistoryDrag();});
  for (const type of ['click', 'dblclick', 'auxclick']) document.addEventListener(type, event => {
    if (performance.now() < suppressHistoryClickUntil && (event.target.closest?.('[data-market-style-chart]') || event.target === document.documentElement || event.target === document.body)) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('contextmenu', event => {
    if (event.target.closest?.('[data-market-style-chart]') || historyDrag?.button === 2) {event.preventDefault(); event.stopPropagation();}
  }, true);
  document.addEventListener('click', event => {
    const zoom = event.target.closest('[data-market-style-zoom]');
    if (zoom && !zoom.disabled) {
      endHistoryDrag(); wheelRemainder = 0;
      if (zoom.dataset.marketStyleZoom === 'reset') historyWindow = null;
      else zoomHistory(zoom.dataset.marketStyleZoom === 'in' ? .7 : 1 / .7);
      redrawHistory(); return;
    }
    const day = event.target.closest('[data-market-style-day]');
    if (day) {chosenDay = day.dataset.marketStyleDay; styleDateNote = ''; revealHistoryDay(chosenDay); AT.render?.();}
    const step = event.target.closest('[data-market-style-step]');
    if (step && !step.disabled) {const found = displayedTimeline.findIndex(item => item.date === chosenDay), index = found < 0 ? displayedTimeline.length - 1 : found; chosenDay = displayedTimeline[Math.max(0, Math.min(displayedTimeline.length - 1, index + Number(step.dataset.marketStyleStep)))]?.date || ''; styleDateNote = ''; revealHistoryDay(chosenDay); AT.render?.();}
    const study = event.target.closest('[data-market-study]');
    if (study && Object.hasOwn(enabled, study.dataset.marketStudy)) {enabled[study.dataset.marketStudy] = !enabled[study.dataset.marketStudy]; AT.marketPreferences?.saveStudies(enabled); AT.render?.();}
  });
  document.addEventListener('change', event => {
    if (!event.target.matches('[data-market-style-date]') || !event.target.value) return;
    const requested = event.target.value, selected = displayedTimeline.findLast(item => item.date <= requested);
    if (!selected) {styleDateNote = '该日期早于当前已读取范围，请选择更长的历史范围。'; AT.render?.(); return;}
    chosenDay = selected.date; revealHistoryDay(chosenDay); styleDateNote = chosenDay === requested ? '' : `该日没有记录，显示前一交易日 ${chosenDay}。`; AT.render?.();
  });
  document.addEventListener('keydown', event => {
    if (!event.target.matches('[data-market-style-day]') || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const current = displayedTimeline.findIndex(item => item.date === event.target.dataset.marketStyleDay);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? displayedTimeline.length - 1 : Math.max(0, Math.min(displayedTimeline.length - 1, current + (event.key === 'ArrowRight' ? 1 : -1)));
    chosenDay = displayedTimeline[next].date; revealHistoryDay(chosenDay); styleDateNote = ''; AT.render?.();
    document.querySelector(`[data-market-style-day="${chosenDay}"]`)?.focus({preventScroll: true});
  });
  AT.marketAnalysis = {endHistoryDrag, overview, controls, selected, scaleValues, price, forcePanel};
})();
