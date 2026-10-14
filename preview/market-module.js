/* Market workspaces render source-backed data only. */
(() => {
  if (!AT.isDesktop) return;
  const bridge = window.atradeDesktop, E = AT.escape, I = AT.icon, B = AT.button;
  AT.pages.G01.title = '个股行情与分析';
  const $ = (selector, root = document) => root.querySelector(selector);
  const marketPages = new Set(['C01', 'G04', 'M01', 'M02', 'M03', 'M04', 'M05', 'G01']);
  const marketNames = {sh: '沪市', sz: '深市', bj: '北交所'};
  const kindNames = {stock: '股票', index: '指数', etf: 'ETF', fund: '基金', bond: '债券'};
  const writes = new Set();
  let source = null, sourceError = '', sourceLoaded = false, sourcePending = null, sourceSerial = 0;
  let pathDraft = null, sourceFeedback = null, sourceBusy = false, picking = false, version = 0;
  let listQuery = '', listOffset = 0, listData = null, listError = '', listKey = '', listSerial = 0, listBusy = false, searchTimer;
  let barsLimit = 240, barsKey = '', barsData = null, barsError = '', barsBusy = false, barsSerial = 0, selectedIndex = -1, lastRenderedPage = '';
  let online = null, onlineDraft = null, onlineBusy = false, onlineFeedback = null;
  let quoteData = null, quoteError = '', quoteBusy = false, quoteKey = '', quoteSerial = 0, quoteUpdated = 0;
  let overviewDaily = null, overviewKey = '', overviewBusy = false, overviewSerial = 0;
  let sectorData = null, sectorDetail = null, sectorId = '', sectorBusy = false, sectorError = '', sectorKey = '', sectorQuery = '', sectorOffset = 0, sectorTimer, sectorSerial = 0;
  let chartMode = 'daily', chartSymbol = '', intradayDate = '', intradayData = null, intradayKey = '', intradayError = '', intradayBusy = false, intradaySerial = 0, intradayIndex = -1, intradayUpdated = 0;
  let dailyWindow = null, minuteWindow = {start: 0, end: 240}, minuteWindowKey = '', zoomFrame = 0;
  let chartDrag = null, suppressChartClickUntil = 0, suppressContextUntil = 0;
  let researchData = null, researchBusy = false, researchError = '', researchKey = '', researchSerial = 0;
  let snapshotData = null, snapshotBusy = false, snapshotError = '', snapshotKey = '', snapshotSerial = 0;
  let researchProgress = null, snapshotProgress = null, jobPollTimer = 0, jobPollBusy = false;
  const cancellingJobs = new Set();
  let researchHistoryDays = 250;
  let sectorQuotes = new Map(), sectorQuoteBusy = false, sectorQuoteSerial = 0, sectorQuoteDone = 0, sectorQuoteError = '', sectorQuoteUpdated = 0;
  let deferredRefreshTimer = 0;
  let studiesFor = null, studies = [], studiesByDate = new Map();
  let pendingDay = null, barsFocusNote = '';
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  // Keep data indices separate from the visible window so hover and drill-down
  // still identify the original record after zooming or moving with the keys.
  function dailyRange(rows = barsData?.rows || []) {
    const count = Math.min(rows.length, dailyWindow?.count || barsLimit);
    const start = clamp(dailyWindow?.start ?? rows.length - count, 0, rows.length - count);
    return {start, count};
  }
  function tradingMinute(value) {
    const match = String(value).match(/(\d{2}):(\d{2})/);
    if (!match) return 0;
    const minute = Number(match[1]) * 60 + Number(match[2]);
    return clamp(minute - 570 - (minute >= 780 ? 90 : 0), 0, 240);
  }
  function minuteLabel(offset) {
    const minute = Math.round(offset);
    if (minute === 120) return '11:30 / 13:00';
    const value = 570 + minute + (minute > 120 ? 90 : 0);
    return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
  }
  function zoomCaption() {
    const range = dailyRange();
    return chartMode === 'daily' ? `${range.count} / ${barsData?.rows?.length || 0} 根日 K` : `${minuteLabel(minuteWindow.start)} — ${minuteLabel(minuteWindow.end)}`;
  }
  function zoomControls() {
    return `<div class="market-zoom-tools"><span>滚轮缩放 · 左键 / 右键拖动 · <span data-market-zoom-range>${zoomCaption()}</span></span>${action('重置视图', 'reset-zoom', '', 'small')}</div>`;
  }
  const overviewSymbols = ['sh000001', 'sz399001', 'sz399006', 'bj899050'];
  const overviewNames = {'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指', 'bj899050': '北证 50'};
  const listLimit = 24;
  const action = (text, name, icon = '', cls = '', attrs = '') => B(text, icon, `data-market-action="${name}" ${attrs}`, cls);
  const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const number = (value, digits = 0) => finite(value) ? Number(value).toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
  const percent = value => finite(value) ? `${Number(value) > 0 ? '+' : ''}${number(value, 2)}%` : '—';
  const direction = value => !finite(value) ? '' : Number(value) >= 0 ? 'up' : 'down';
  const date = value => value ? E(String(value).slice(0, 10)) : '—';
  const time = value => {
    if (!value) return '尚未读取';
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? E(value) : E(parsed.toLocaleString('zh-CN', {hour12: false}));
  };
  const marketName = value => marketNames[String(value || '').toLowerCase()] || E(value || '');
  const providerName = value => ({tdx_lc1: '通达信本地分钟线', tdx_mcp: '通达信 MCP', eastmoney: '东方财富'}[value] || E(value || '通达信'));
  const quoteStamp = quote => `${quote.cached ? '旧缓存 · 上次采集 ' + time(quote.receivedAt) : quote.asOf ? '行情时间 ' + time(quote.asOf) : '行情时间未提供 · 采集于 ' + time(quote.receivedAt)}`;
  const completeOHLC = row => ['open', 'high', 'low', 'close'].every(key => finite(row[key]));
  const shortAmount = value => {
    if (!finite(value)) return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return Math.abs(n) >= 1e8 ? `${number(n / 1e8, 2)} 亿` : Math.abs(n) >= 1e4 ? `${number(n / 1e4, 2)} 万` : number(n, 2);
  };
  const safeError = error => error?.message || '操作未完成，请重试。';
  const warn = warnings => Array.isArray(warnings) && warnings.length ? `<div class="market-warning" role="status">${warnings.map(w => `<p>${E(typeof w === 'string' ? w : w.message || w.code || '数据需要检查')}</p>`).join('')}</div>` : '';
  function refreshVisible() {
    if (!marketPages.has(AT.state.page)) return;
    if (AT.marketExplorer?.isComposing?.()) {
      if (!deferredRefreshTimer) deferredRefreshTimer = setTimeout(() => {deferredRefreshTimer = 0; refreshVisible();}, 180);
      return;
    }
    if (AT.state.page === 'M02') {
      const historyDragging = AT.sectorTrends?.deferRender();
      const comparisonDragging = AT.sectorComparison?.deferRender();
      if (historyDragging || comparisonDragging) return;
    }
    const focus = AT.marketExplorer?.captureFocus();
    AT.render?.();
    AT.marketExplorer?.restoreFocus(focus);
  }
  function invalidateSources() {
    AT.analysisTools?.invalidateSource();
    AT.sectorTrends?.invalidate();
    AT.sectorComparison?.invalidate();
    AT.stockTableCharts?.invalidate();
    version++; listSerial++; barsSerial++; quoteSerial++; intradaySerial++; overviewSerial++; sectorSerial++; sourceSerial++;
    sourceLoaded = false; sourcePending = null; sourceError = '';
    listKey = ''; listData = null; listError = ''; listBusy = false; listOffset = 0;
    barsKey = ''; barsData = null; barsError = ''; barsBusy = false; selectedIndex = -1;
    dailyWindow = null; minuteWindow = {start: 0, end: 240}; minuteWindowKey = '';
    quoteKey = ''; quoteData = null; quoteError = ''; quoteBusy = false; quoteUpdated = 0;
    intradayKey = ''; intradayData = null; intradayError = ''; intradayBusy = false; intradayUpdated = 0;
    overviewKey = ''; overviewDaily = null; overviewBusy = false;
    sectorKey = ''; sectorData = null; sectorDetail = null; sectorError = ''; sectorBusy = false; sectorOffset = 0;
    researchSerial++; researchKey = ''; researchData = null; researchError = ''; researchBusy = false;
    snapshotSerial++; snapshotKey = ''; snapshotData = null; snapshotError = ''; snapshotBusy = false;
    researchProgress = null; snapshotProgress = null;
    sectorQuoteSerial++; sectorQuotes = new Map(); sectorQuoteBusy = false; sectorQuoteDone = 0; sectorQuoteError = ''; sectorQuoteUpdated = 0;
    studiesFor = null; studies = []; studiesByDate = new Map();
  }
  async function call(method, payload = {}) {
    if (typeof bridge.market !== 'function') throw new Error('行情服务尚未启动，请重新打开最新版桌面应用。');
    const response = await bridge.market(method, payload);
    if (!response?.ok) throw new Error(typeof response?.error === 'string' ? response.error : response?.error?.message || '读取行情失败，请重试。');
    return response.data;
  }
  async function loadStatus(force = false) {
    if (sourcePending) return sourcePending;
    if (sourceLoaded && !force) return source;
    sourceError = ''; const serial = ++sourceSerial;
    const work = (async () => {
      try {
        const data = await call('market.status'); if (serial !== sourceSerial) return null;
        source = data;
        if (source?.online) online = source.online;
        sourceLoaded = true;
        if (pathDraft === null) pathDraft = source?.path || '';
        return source;
      } catch (error) {
        if (serial === sourceSerial) {sourceError = safeError(error); sourceLoaded = true;}
        return null;
      } finally {
        if (serial === sourceSerial) {sourcePending = null; refreshVisible();}
      }
    })();
    sourcePending = work;
    return work;
  }
  function sourceTag() {
    return !sourceLoaded ? '正在读取' : sourceError ? '读取失败' : !source?.configured ? '尚未设置' : source.available === false ? '目录不可用' : '已连接';
  }
  function settingsNav() {
    return `<nav class="context-nav" aria-label="相关设置">${AT.groups.find(g => g.id === 'C').pages.map(p => `<button type="button" data-nav="${p.id}" ${p.id === 'C01' ? 'aria-current="page"' : ''}>${I(p.icon)}${E(p.title)}</button>`).join('')}</nav>`;
  }
  function sourceMetrics() {
    return `<div class="market-source-metrics"><div><small>已收录证券</small><strong>${source?.configured ? number(source.symbolCount || 0) : '—'}</strong></div><div><small>日线覆盖</small><strong class="market-date-range">${date(source?.earliestDate)} <span>至</span> ${date(source?.latestDate)}</strong></div><div><small>最近检查</small><strong class="market-time">${time(source?.lastScannedAt)}</strong></div></div>`;
  }
  function statusError() {
    return sourceError ? `<div class="market-feedback" data-tone="error" role="alert"><span>${E(sourceError)}</span>${action('重新读取', 'status', 'refresh', 'small')}</div>` : '';
  }
  function onlineSettings() {
    const saved = online || {}, draft = onlineDraft || saved;
    const followLocal = draft.followLocal ?? !draft.root;
    const configuredRoot = draft.configuredRoot ?? draft.root ?? '';
    return `<section class="panel market-source-panel market-online-settings"><header class="market-source-heading"><span class="market-source-icon">${I('pulse')}</span><div><h2>通达信 MCP · 实时行情与分时</h2><p>通过本机只读 MCP 连接，读取最新价格、分钟线与板块</p></div><span class="tag ${saved.connected ? 'blue' : ''}">${saved.connected ? '连接可用' : saved.enabled ? '等待连接' : '未启用'}</span></header>
      <form id="market-online-form" class="market-source-form" aria-busy="${onlineBusy}">
        <label class="market-online-enable"><span><strong>跟随本地日线目录</strong><small>保存后，修改上方目录时 MCP 会自动跟随</small></span><input class="switch" name="followLocal" type="checkbox" data-market-online-field ${followLocal ? 'checked' : ''} ${onlineBusy ? 'disabled' : ''}></label>
        <div class="market-online-fields"><label class="field"><span>独立通达信目录</span><input name="root" data-market-online-field value="${E(configuredRoot)}" placeholder="填写通达信安装根目录" autocomplete="off" ${onlineBusy || followLocal ? 'disabled' : ''}></label><label class="field"><span>自动刷新间隔（秒）</span><input name="refreshSeconds" type="number" min="10" max="300" step="1" data-market-online-field value="${E(draft.refreshSeconds ?? 30)}" ${onlineBusy ? 'disabled' : ''}></label></div>
        <p class="market-field-note">当前生效目录：${E(saved.root || '尚未选择')} · ${saved.followLocal ?? !saved.root ? '跟随本地日线目录' : '使用独立目录'}。启用实时行情需要该目录包含 TQ 组件。</p>
        <label class="market-online-enable"><span><strong>启用实时行情</strong><small>市场页面可见时更新；每条报价显示源时间</small></span><input class="switch" name="enabled" type="checkbox" data-market-online-field ${draft.enabled ? 'checked' : ''} ${onlineBusy ? 'disabled' : ''}></label><div class="market-source-actions">${action(onlineBusy ? '正在处理…' : '保存设置', 'online-save', 'save', 'primary', onlineBusy ? 'disabled' : '')}${action('保存并检测', 'online-test', 'check', '', onlineBusy ? 'disabled' : '')}</div><p class="market-feedback" data-market-online-feedback data-tone="${onlineFeedback?.tone || 'neutral'}" role="${onlineFeedback?.tone === 'error' ? 'alert' : 'status'}">${E(onlineFeedback?.message || saved.message || '先启动通达信并登录行情，再启用连接。历史分时优先读取本机分钟线。')}</p><p class="market-field-note">本地分钟线缺失时，在线补取东方财富提供的近期交易日分时，实际范围以来源返回日期为准。</p></form></section>`;
  }
  async function saveOnline(test = false) {
    if (onlineBusy) return;
    const form = $('#market-online-form'); if (!form) return;
    const refreshSeconds = Number(form.elements.refreshSeconds.value);
    if (!Number.isInteger(refreshSeconds) || refreshSeconds < 10 || refreshSeconds > 300) {onlineFeedback = {tone: 'error', message: '刷新间隔请输入 10 到 300 之间的整数。'}; refreshVisible(); return;}
    const followLocal = form.elements.followLocal.checked;
    const root = followLocal ? '' : form.elements.root.value.trim();
    if (!followLocal && !root) {onlineFeedback = {tone: 'error', message: '请填写独立通达信目录，或开启跟随本地日线目录。'}; refreshVisible(); return;}
    const payload = {root, followLocal, enabled: form.elements.enabled.checked, refreshSeconds};
    onlineDraft = payload; onlineBusy = true; onlineFeedback = {tone: 'neutral', message: test ? '正在保存并检测行情服务…' : '正在保存…'}; refreshVisible();
    const writing = call('market.online.configure', payload); writes.add(writing);
    try {
      online = await writing; onlineDraft = null; invalidateSources();
      if (test) online = await call('market.online.test');
      onlineFeedback = {tone: test && !online.connected ? 'error' : 'success', message: online.message || (online.enabled ? '实时行情配置已保存。' : '实时行情已停用。')};
      await loadStatus(true);
    } catch (error) {onlineFeedback = {tone: 'error', message: safeError(error)};}
    finally {writes.delete(writing); onlineBusy = false; refreshVisible();}
  }
  function liveNote() {
    if (!online?.enabled) return `<div class="market-connection-note">${I('pulse')}<span>实时行情未启用。配置连接后可查看报价与分时。</span>${B('配置行情服务', 'settings', 'data-nav="C01"', 'small')}</div>`;
    return `<div class="market-connection-note">${I('clock')}<span>${quoteBusy ? '正在更新报价…' : quoteError ? E(quoteError) : `自动刷新 ${number(online.refreshSeconds || 30)} 秒 · ${quoteData?.receivedAt ? '读取于 ' + time(quoteData.receivedAt) : '等待来源返回行情'}`}</span>${action('刷新行情', 'refresh-quotes', 'refresh', 'small', quoteBusy ? 'disabled' : '')}</div>`;
  }
  function quoteCard(symbol, title, quote, error = '') {
    return `<button type="button" class="market-index-card" data-stock="${E(symbol)}"><div><strong>${E(quote?.name || title || symbol)}</strong><small>${E(symbol.slice(2))}</small></div><b class="${direction(quote?.changePercent)}">${number(quote?.price, 2)}</b><span class="${direction(quote?.changePercent)}">${percent(quote?.changePercent)}</span><p class="${quote?.cached ? 'market-cache-label' : ''}">${quote ? quoteStamp(quote) : E(error || (online?.enabled ? '等待报价' : '实时行情未启用'))}</p></button>`;
  }
  function overviewView() {
    const quotes = new Map((quoteData?.items || []).map(item => [item.symbol, item]));
    const errors = new Map((quoteData?.errors || []).map(item => [item.symbol, item.message]));
    return AT.head('市场总览', '指数报价 · 市场风格与活跃程度', B('个股行情', 'market', 'data-nav="M04"') + B('数据源设置', 'settings', 'data-nav="C01"'))
      + `<section class="panel market-overview-panel"><div class="market-section-heading"><h2>主要指数 · 最新报价</h2><span class="tag">${providerName(quoteData?.provider || '通达信行情服务')}</span></div><div class="market-index-grid">${overviewSymbols.map(symbol => quoteCard(symbol, overviewNames[symbol], quotes.get(symbol), errors.get(symbol))).join('')}</div>${liveNote()}${warn(quoteData?.warnings)}</section>`
      + marketJobProgress('market.research') + AT.marketAnalysis.overview({data: researchData, busy: researchBusy, error: researchError, historyDays: researchHistoryDays})
      + `<div class="market-source-notes"><div><h3>板块强弱</h3><p>查看板块涨幅与成交额，筛选并比较活跃方向。</p>${B('打开板块行情', 'boards', 'data-nav="M02"', 'small')}</div><div><h3>股票观察池</h3><p>按成交活跃、涨幅领先、放量变化寻找需要进一步研究的股票。</p>${B('打开股票观察池', 'market', 'data-nav="M04"', 'small')}</div></div>`;
  }
  async function loadResearch(force = false) {
    const key = `${version}|${researchHistoryDays}`;
    if (!source?.configured || researchBusy || (!force && researchKey === key)) return;
    researchKey = key; researchBusy = true; researchError = ''; researchProgress = null; const serial = ++researchSerial; refreshVisible(); scheduleJobPoll();
    try {const data = await call('market.research', {refresh: force, historyDays: researchHistoryDays}); if (serial === researchSerial) researchData = data;}
    catch (error) {if (serial === researchSerial) researchError = safeError(error);}
    finally {if (serial === researchSerial) {researchBusy = false; refreshVisible();}}
  }
  async function loadSnapshot(force = false) {
    const key = String(version);
    if (!source?.configured || snapshotBusy || (!force && snapshotKey === key)) return;
    snapshotKey = key; snapshotBusy = true; snapshotError = ''; snapshotProgress = null; const serial = ++snapshotSerial; refreshVisible(); scheduleJobPoll();
    try {const data = await call('market.snapshot', {refresh: force}); if (serial === snapshotSerial) snapshotData = data;}
    catch (error) {if (serial === snapshotSerial) snapshotError = safeError(error);}
    finally {if (serial === snapshotSerial) {snapshotBusy = false; refreshVisible();}}
  }
  function marketJobProgress(method) {
    const busy = method === 'market.research' ? researchBusy : snapshotBusy;
    if (!busy) return '';
    const job = method === 'market.research' ? researchProgress : snapshotProgress;
    const progress = finite(job?.percent) ? clamp(Number(job.percent), 0, 100) : null;
    const label = job?.status === 'queued' ? '等待本地计算任务完成…' : job?.stage || '正在准备本地行情计算…';
    return `<div class="market-job-progress" data-market-job="${method}" role="status"><div><strong>${E(label)}</strong><span>${job?.total ? `${number(job.completed || 0)} / ${number(job.total)} 项` : '其他行情页面仍可使用'}${progress !== null ? ` · ${number(progress)}%` : ''}</span></div><progress max="100"${progress !== null ? ` value="${progress}"` : ''} aria-label="行情计算进度"></progress><button type="button" class="btn small" data-market-cancel="${method}" ${cancellingJobs.has(method) ? 'disabled' : ''}>${cancellingJobs.has(method) ? '正在取消…' : '取消本次计算'}</button></div>`;
  }
  function paintJobProgress() {
    for (const method of ['market.research', 'market.snapshot']) {
      const element = $(`[data-market-job="${method}"]`);
      if (!element) continue;
      const focused = document.activeElement?.dataset?.marketCancel === method;
      element.outerHTML = marketJobProgress(method);
      if (focused) $(`[data-market-cancel="${method}"]`)?.focus({preventScroll: true});
    }
  }
  function scheduleJobPoll() {
    if (!jobPollTimer && (researchBusy || snapshotBusy)) jobPollTimer = setTimeout(() => {jobPollTimer = 0; pollMarketJobs();}, 1000);
  }
  async function pollMarketJobs() {
    if (jobPollBusy || (!researchBusy && !snapshotBusy)) return;
    jobPollBusy = true;
    try {
      const status = await call('market.research.status');
      researchProgress = status.jobs?.find(job => job.method === 'market.research') || null;
      snapshotProgress = status.jobs?.find(job => job.method === 'market.snapshot') || null;
      paintJobProgress();
    } catch {} // A status error must not fail the underlying calculation.
    finally {jobPollBusy = false; scheduleJobPoll();}
  }
  async function cancelMarketJob(method) {
    if (!['market.research', 'market.snapshot'].includes(method) || cancellingJobs.has(method)) return;
    if (!(method === 'market.research' ? researchBusy : snapshotBusy)) return;
    cancellingJobs.add(method); paintJobProgress();
    try {
      const status = await call('market.research.status'), job = status.jobs?.find(item => item.method === method);
      if (job) await call('market.research.cancel', {id: job.id});
    } catch (error) {
      if (method === 'market.research') researchError = safeError(error); else snapshotError = safeError(error);
      refreshVisible();
    } finally {cancellingJobs.delete(method); paintJobProgress();}
  }
  function loadOverview() {loadResearch();}

  function activeQuoteSymbols() {
    const page = AT.state.page;
    if (page === 'M01') return overviewSymbols;
    if (page === 'M04') return [];
    if (page === 'G01') return [barsData?.symbol || AT.state.stock].filter(symbol => /^(sh|sz|bj)\d{6}$/.test(symbol));
    return [];
  }
  async function loadQuotes(force = false) {
    const symbols = activeQuoteSymbols(), key = symbols.join('|');
    if (!online?.enabled || !symbols.length || quoteBusy || (!force && quoteKey === key)) return;
    quoteKey = key; quoteBusy = true; quoteError = ''; const serial = ++quoteSerial;
    if (quoteData?.items?.some(item => !symbols.includes(item.symbol))) quoteData = null;
    refreshVisible();
    try {const data = await call('market.quotes', {symbols}); if (serial === quoteSerial) quoteData = data;}
    catch (error) {if (serial === quoteSerial) {quoteData = null; quoteError = safeError(error);}}
    finally {if (serial === quoteSerial) {quoteBusy = false; quoteUpdated = Date.now(); refreshVisible();}}
  }
  function sectorsView(detail = false) {
    const header = AT.head(detail ? sectorDetail?.name || '板块详情' : '板块行情', detail ? '当前板块成分 · 本地日线观察与筛选' : '板块强弱与成交活跃度 · 通达信报价', detail ? B('返回板块', 'back', 'data-nav="M02"') : B('数据源设置', 'settings', 'data-nav="C01"'));
    const connectionError = !online?.enabled ? '请先在设置中启用通达信 MCP，读取真实板块目录和成分。' : '';
    if (detail) {
      const lookup = new Map([...(snapshotData?.items || []), ...(snapshotData?.staleItems || [])].map(item => [item.symbol, item]));
      const items = (sectorDetail?.items || []).map(item => ({...item, ...(lookup.get(item.symbol) || {}), code: item.code || item.symbol.slice(2)}));
      return header + marketJobProgress('market.snapshot') + AT.marketExplorer.stockView({items, kind: 'members', key: sectorId, title: sectorDetail?.name || '成分证券', asOf: snapshotData?.asOf, computedAt: snapshotData?.computedAt,
        busy: sectorBusy || snapshotBusy, error: sectorError || snapshotError || connectionError, refreshAction: 'refresh-sectors', warnings: [...(sectorDetail?.warnings || []), ...(snapshotData?.warnings || []), ...(items.some(item => !item.date) ? ['部分成分尚未收录本地日线，指标保持空缺。'] : [])]});
    }
    const items = (sectorData?.items || []).map(item => {const quote = sectorQuotes.get(item.symbol); return {...item, ...(quote ? {price: quote.price, changePercent: quote.changePercent, amount: quote.amount, asOf: quote.asOf, receivedAt: quote.receivedAt, cached: quote.cached} : {})};});
    const comparison = AT.sectorComparison?.panel({items, catalogueReady: !!sectorData, busy: sectorBusy, loadingMetrics: sectorQuoteBusy,
      error: sectorError || sectorQuoteError || connectionError, progress: {done: sectorQuoteDone, total: items.length}}) || '';
    return header + comparison + (AT.sectorTrends?.panel() || '') + AT.marketExplorer.sectorView({items, title: '板块强弱', busy: sectorBusy, error: sectorError || connectionError, refreshAction: 'refresh-sectors',
      loadingMetrics: sectorQuoteBusy, progress: {done: sectorQuoteDone, total: items.length}, computedAt: sectorData?.receivedAt,
      warnings: [...(sectorData?.warnings || []), ...(sectorQuoteError ? [sectorQuoteError] : []), ...(items.some(item => item.receivedAt && !item.asOf) ? ['部分快照未提供交易时间，采集时间不代表行情时刻。'] : []), '板块成交额可能重叠，不相加作为全市场成交额。']});
  }
  async function loadSectorQuotes(force = false) {
    const items = sectorData?.items || [];
    if (sectorQuoteBusy || !online?.enabled || !items.length || AT.state.page !== 'M02' || document.hidden) return;
    if (force) {sectorQuoteDone = 0; sectorQuoteError = ''; sectorQuotes = new Map([...sectorQuotes].map(([symbol, quote]) => [symbol, {...quote, cached: true}]));}
    if (sectorQuoteError || sectorQuoteDone >= items.length) return;
    sectorQuoteBusy = true; sectorQuoteError = ''; const serial = ++sectorQuoteSerial; refreshVisible();
    try {
      for (let index = sectorQuoteDone; index < items.length; index += 24) {
        if (serial !== sectorQuoteSerial || AT.state.page !== 'M02' || document.hidden) break;
        const symbols = items.slice(index, index + 24).map(item => item.symbol);
        const data = await call('market.quotes', {symbols});
        if (serial !== sectorQuoteSerial) return;
        for (const quote of data.items || []) sectorQuotes.set(quote.symbol, quote);
        sectorQuoteDone = Math.min(index + 24, items.length);
        if (!(data.items || []).some(item => !item.cached && finite(item.price))) {
          sectorQuoteError = '本批报价未更新，已暂停后续读取；当前排序只含已取得的报价，可点击刷新重试。'; break;
        }
        if (AT.state.page === 'M02') refreshVisible();
      }
    } catch (error) {if (serial === sectorQuoteSerial) sectorQuoteError = safeError(error);}
    finally {if (serial === sectorQuoteSerial) {sectorQuoteBusy = false; sectorQuoteUpdated = Date.now(); refreshVisible();}}
  }
  async function loadSectors(force = false) {
    const detail = AT.state.page === 'M03', key = `${version}|${detail ? sectorId : 'all'}`;
    if (!online?.enabled || sectorBusy || (!force && sectorKey === key)) {if (!detail) loadSectorQuotes(); return;}
    if (detail && sectorKey !== key) sectorDetail = null;
    sectorKey = key; sectorBusy = true; sectorError = ''; const serial = ++sectorSerial; refreshVisible();
    try {
      if (detail) {if (!sectorId) throw new Error('请先在板块行情中选择一个板块。'); const data = await call('market.sector', {id: sectorId}); if (serial === sectorSerial) sectorDetail = data;}
      else {const data = await call('market.sectors'); if (serial === sectorSerial) {
        const symbols = new Set((data.items || []).map(item => item.symbol));
        sectorData = data; sectorQuoteSerial++;
        sectorQuotes = new Map([...sectorQuotes].filter(([symbol]) => symbols.has(symbol)).map(([symbol, quote]) => [symbol, {...quote, cached: true}]));
        sectorQuoteBusy = false; sectorQuoteDone = 0; sectorQuoteError = '';
      }}
    } catch (error) {if (serial === sectorSerial) sectorError = safeError(error);}
    finally {if (serial === sectorSerial) {sectorBusy = false; refreshVisible(); if (!detail) loadSectorQuotes();}}
  }

  function newsView() {
    return AT.head('资讯中心', '资讯与公告需要独立的内容来源', B('数据源设置', 'settings', 'data-nav="C01"')) + `<section class="panel market-empty"><span class="market-source-icon">${I('report')}</span><h2>暂无已接入的资讯来源</h2><p>本地日线与当前行情接口不提供新闻正文。资讯来源接入后会在这里展示标题、发布时间和原始出处。</p>${B('管理数据源', 'settings', 'data-nav="C01"')}</section>`;
  }
  function settingsView() {
    const busy = sourceBusy || picking || Boolean(sourcePending), feedback = sourceFeedback;
    return AT.head('数据源', '选择自己的通达信目录，读取本机日线', B('数据状态', 'shield', 'data-nav="G04"')) + settingsNav()
      + `<section class="panel market-source-panel"><header class="market-source-heading"><span class="market-source-icon">${I('database')}</span><div><h2>通达信本地日线</h2><p>你的目录 · 本机读取 · 本机缓存</p></div><span class="tag ${source?.available ? 'blue' : ''}">${sourceTag()}</span></header>
        <form id="market-source-form" class="market-source-form" aria-busy="${busy}">
          <label class="field" for="market-source-path"><span>通达信数据目录</span><div class="market-path-entry"><input id="market-source-path" name="path" data-market-path value="${E(pathDraft ?? source?.path ?? '')}" placeholder="选择通达信安装目录或 vipdoc 文件夹" autocomplete="off" spellcheck="false" ${busy ? 'disabled' : ''}>${action(picking ? '正在选择…' : '选择文件夹', 'choose', 'folder', '', busy ? 'disabled' : '')}</div></label>
          <p class="market-field-note">可选择通达信安装目录，也可直接选择其中的 vipdoc 文件夹。原始文件仅供读取。</p>
          <div class="market-source-actions">${action(sourceBusy ? '正在读取…' : '保存并读取', 'configure', 'save', 'primary', busy ? 'disabled' : '')}${action('重新检查目录', 'scan', 'refresh', '', busy || !source?.configured ? 'disabled' : '')}${source?.configured ? B('查看个股行情', 'market', 'data-nav="M04"') : ''}</div>
          <p class="market-feedback" data-tone="${feedback?.tone || 'neutral'}" role="${feedback?.tone === 'error' ? 'alert' : 'status'}" aria-live="polite">${E(feedback?.message || (!sourceLoaded ? '正在读取已保存的目录…' : '选择目录后，保存并读取即可开始。'))}</p>
        </form>${statusError()}${sourceMetrics()}${warn(source?.warnings)}
      </section><div class="market-source-notes"><div><h3>数据如何更新</h3><p>在通达信下载或补齐日线后，点击“重新检查目录”。查看证券时读取其日线，并保存到 A-trade 的本机缓存。</p></div><div><h3>价格与分时各有来源</h3><p>本地日线保留未复权价格；实时行情、分钟线和板块通过下方的通达信 MCP 接入。</p></div></div>` + onlineSettings();
  }
  function statusView() {
    return AT.head('数据状态与质量', '查看来源、覆盖日期与读取状态', B('数据源设置', 'settings', 'data-nav="C01"'))
      + `<section class="panel market-source-panel"><header class="market-source-heading"><span class="market-source-icon">${I('database')}</span><div><h2>本地 TDX 日线</h2><p>${E(source?.path || '尚未选择目录')}</p></div><span class="tag ${source?.available ? 'blue' : ''}">${sourceTag()}</span></header>${statusError()}${sourceMetrics()}${warn(source?.warnings)}<div class="market-source-actions">${action(sourceBusy ? '正在检查…' : '重新检查', 'scan', 'refresh', '', sourceBusy || !source?.configured ? 'disabled' : '')}${B('个股行情', 'market', 'data-nav="M04"', 'primary')}</div>${sourceFeedback ? `<p class="market-feedback" role="status" data-tone="${sourceFeedback.tone}">${E(sourceFeedback.message)}</p>` : ''}</section>`
      + `<section class="panel market-coverage"><h2>当前接入范围</h2><div><span>日线与成交量</span><strong>${source?.configured ? '本地 TDX' : '待选择目录'}</strong><p>价格未复权，股票成交量为股，其他证券按文件口径显示单位。除权、停牌及缺失日期按原始文件呈现。</p></div><div><span>实时行情 / MCP</span><strong>${online?.enabled ? online.connected ? '连接可用' : '已启用 · 等待连接' : '未启用'}</strong><p>通过本机通达信服务读取；行情时间缺失时只标记采集时间。</p></div><div><span>历史分时</span><strong>本地分钟线 / 行情服务</strong><p>双击日 K 查看对应交易日；没有该日记录时会明确提示缺口。</p></div><div><span>资讯 / 财务</span><strong>暂无已接入来源</strong><p>对应页面保持空缺，接入真实来源后再展示。</p></div></section>`;
  }
  function emptySource() {
    if (!sourceLoaded || sourceBusy) return `<section class="panel market-empty" role="status"><span class="market-source-icon">${I('database')}</span><h2>正在读取行情来源</h2><p>首次读取目录可能需要一点时间。</p></section>`;
    if (sourceError) return `<section class="panel market-empty"><h2>行情来源读取失败</h2><p role="alert">${E(sourceError)}</p>${action('重新读取', 'status', 'refresh', 'primary')}</section>`;
    if (!source?.configured) return `<section class="panel market-empty"><span class="market-source-icon">${I('market')}</span><h2>从你的本地日线开始</h2><p>在设置中选择通达信数据目录，随后即可搜索证券、查看真实 K 线。</p>${B('选择数据目录', 'folder', 'data-nav="C01"', 'primary')}</section>`;
    return '';
  }
  function listView() {
    const header = AT.head('个股行情', '全市场筛选 · 成交活跃、涨幅领先与放量观察', B('数据源设置', 'settings', 'data-nav="C01"'));
    const empty = emptySource(); if (empty) return header + empty;
    return header + marketJobProgress('market.snapshot') + AT.marketExplorer.stockView({items: [...(snapshotData?.items || []), ...(snapshotData?.staleItems || [])], kind: 'stocks', title: '股票观察池',
      asOf: snapshotData?.asOf, computedAt: snapshotData?.computedAt, coverage: snapshotData?.coverage, warnings: snapshotData?.warnings,
      busy: snapshotBusy, error: snapshotError, refreshAction: 'refresh-snapshot'});
  }
  function ensureStudies() {
    if (studiesFor === barsData) return;
    studiesFor = barsData;
    studies = barsData?.rows?.length ? AT.marketIndicators.calculate(barsData.rows, {volumeUnit: barsData.volumeUnit}).rows : [];
    studiesByDate = new Map(studies.map(row => [row.date, row]));
  }
  function forceStudyPanel() {
    ensureStudies();
    return AT.marketAnalysis.forcePanel({studies, ...dailyRange(), selectedIndex, volumeUnit: barsData?.volumeUnit});
  }

  function rowReadout(row) {
    if (!row) return '';
    return `<strong>${date(row.date)}</strong><span>开 <b>${number(row.open, 2)}</b></span><span>高 <b>${number(row.high, 2)}</b></span><span>低 <b>${number(row.low, 2)}</b></span><span>收 <b>${number(row.close, 2)}</b></span><span>量 <b>${shortAmount(row.volume)} ${E(barsData?.volumeUnit || '原始单位')}</b></span><span>额 <b>${shortAmount(row.amount)} ${E(barsData?.amountUnit || '元')}</b></span>`;
  }
  function candleChart(rows) {
    if (!rows.length) return '';
    ensureStudies();
    const {start, count} = dailyRange(rows), visible = rows.slice(start, start + count);
    const width = 1040, left = 24, right = 82, top = 26, bottom = 274, volTop = 314, volBottom = 390;
    const studyScale = AT.marketAnalysis.scaleValues(studies.slice(start, start + count));
    const lo = Math.min(...visible.map(row => row.low), ...studyScale), hi = Math.max(...visible.map(row => row.high), ...studyScale);
    const span = Math.max(hi - lo, Math.abs(hi) * .02, .01), min = lo - span * .06, max = hi + span * .06;
    const priceY = value => top + (max - value) / (max - min) * (bottom - top);
    const step = (width - left - right) / count, candleWidth = Math.max(1, Math.min(36, step * .65));
    const volumeMax = Math.max(1, ...visible.map(row => row.volume));
    const grid = Array.from({length: 5}, (_, i) => {
      const value = max - (max - min) * i / 4, y = priceY(value);
      return `<path d="M${left} ${y}H${width - right}" class="market-gridline"/><text x="${width - right + 12}" y="${y + 4}" class="market-axis-label">${number(value, 2)}</text>`;
    }).join('');
    const candles = visible.map((row, index) => {
      const x = left + step * (index + .5), y = priceY(Math.max(row.open, row.close)), height = Math.max(1.2, Math.abs(priceY(row.open) - priceY(row.close))), vh = Math.max(.5, row.volume / volumeMax * (volBottom - volTop));
      const color = row.close >= row.open ? 'var(--color-up)' : 'var(--color-down)';
      return `<g data-market-index="${start + index}"><path d="M${x} ${priceY(row.high)}V${priceY(row.low)}" stroke="${color}"/><rect x="${x - candleWidth / 2}" y="${y}" width="${candleWidth}" height="${height}" fill="${color}"/><rect x="${x - candleWidth / 2}" y="${volBottom - vh}" width="${candleWidth}" height="${vh}" fill="${color}" opacity=".7"/><rect x="${left + step * index}" y="${top}" width="${step}" height="${volBottom - top}" fill="transparent"/></g>`;
    }).join('');
    const labels = [...new Set([0, Math.floor((count - 1) / 2), count - 1])].map(index => `<text x="${left + step * (index + .5)}" y="421" text-anchor="${index === 0 ? 'start' : index === count - 1 ? 'end' : 'middle'}" class="market-axis-label">${date(visible[index].date)}</text>`).join('');
    const overlays = AT.marketAnalysis.price({rows, studies, start, count, left, step, top, bottom, priceY});
    const strategyMarks = AT.stockResearch?.overlay({symbol:barsData?.symbol,rows,start,count,left,step,priceY}) || '';
    return `<svg class="market-candles" viewBox="0 0 ${width} 442" role="img" tabindex="0" aria-label="未复权日线 K 线与成交量，滚轮缩放，左右方向键查看相邻交易日" data-market-chart data-market-zoom="daily" data-chart-start="${start}" data-chart-count="${count}" data-chart-total="${rows.length}" data-chart-left="${left}" data-chart-step="${step}"><title>未复权日线 · 滚轮缩放</title>${grid}<text x="${left}" y="304" class="market-axis-label">成交量（${E(barsData?.volumeUnit || '原始单位')}）</text><text x="${width - right + 12}" y="${volTop + 5}" class="market-axis-label">${shortAmount(volumeMax)}</text>${candles}${overlays}<path data-market-crosshair d="M${left + step * (clamp(selectedIndex - start, 0, count - 1) + .5)} ${top}V${volBottom}" class="market-crosshair"/>${strategyMarks}${labels}</svg>`;
  }
  function intradayReadout(row) {
    if (!row) return '';
    return `<strong>${E(intradayData?.date || intradayDate)} ${E(row.time)}</strong><span>开 <b>${number(row.open, 2)}</b></span><span>高 <b>${number(row.high, 2)}</b></span><span>低 <b>${number(row.low, 2)}</b></span><span>收 <b>${number(row.close, 2)}</b></span><span>量 <b>${shortAmount(row.volume)} ${E(intradayData?.volumeUnit || '原始单位')}</b></span>`;
  }
  function intradayChart(rows) {
    const width = 1040, left = 24, right = 82, top = 26, bottom = 274, volTop = 314, volBottom = 390;
    const {start, end} = minuteWindow;
    const visible = rows.map((row, index) => ({row, index})).filter(({row}) => tradingMinute(row.time) >= start && tradingMinute(row.time) <= end);
    const previous = finite(intradayData?.previousClose) ? Number(intradayData.previousClose) : null;
    const prices = (visible.length ? visible.map(item => item.row) : rows).flatMap(row => [row.low, row.high, row.close].filter(finite).map(Number)); if (previous !== null) prices.push(previous);
    const lo = Math.min(...prices), hi = Math.max(...prices), span = Math.max(hi - lo, Math.abs(hi) * .008, .01);
    const min = lo - span * .07, max = hi + span * .07, priceY = value => top + (max - value) / (max - min) * (bottom - top);
    const pointX = row => left + (tradingMinute(row.time) - start) / (end - start) * (width - left - right);
    const volumeMax = Math.max(1, ...visible.map(({row}) => row.volume || 0)), step = (width - left - right) / (end - start + 1), candleWidth = Math.max(1, Math.min(36, step * .72));
    const grid = Array.from({length: 5}, (_, index) => {const value = max - (max - min) * index / 4, y = priceY(value); return `<path d="M${left} ${y}H${width - right}" class="market-gridline"/><text x="${width - right + 12}" y="${y + 4}" class="market-axis-label">${number(value, 2)}</text>`;}).join('');
    const path = visible.map(({row}, index) => {
      const previousRow = visible[index - 1]?.row;
      const consecutive = previousRow && tradingMinute(row.time) - tradingMinute(previousRow.time) <= 1;
      return `${consecutive ? 'L' : 'M'}${pointX(row)},${priceY(row.close)}`;
    }).join(' ');
    const marks = visible.map(({row, index}) => {const x = pointX(row), reference = finite(row.open) ? row.open : rows[index - 1]?.close ?? previous ?? row.close, color = row.close >= reference ? 'var(--color-up)' : 'var(--color-down)', height = Math.max(.5, Number(row.volume || 0) / volumeMax * (volBottom - volTop)); return `<g data-market-minute-index="${index}">${chartMode === 'intraday' ? `<circle data-market-minute-point cx="${x}" cy="${priceY(row.close)}" r="1.3" fill="var(--primary)"/>` : ''}${chartMode === 'minute' && completeOHLC(row) ? `<path d="M${x} ${priceY(row.high)}V${priceY(row.low)}" stroke="${color}"/><rect data-market-minute-candle x="${x - candleWidth / 2}" y="${priceY(Math.max(row.open, row.close))}" width="${candleWidth}" height="${Math.max(1.2, Math.abs(priceY(row.open) - priceY(row.close)))}" fill="${color}"/>` : ''}${finite(row.volume) ? `<rect x="${x - candleWidth / 2}" y="${volBottom - height}" width="${candleWidth}" height="${height}" fill="${color}" opacity=".7"/>` : ''}<rect x="${x - step / 2}" y="${top}" width="${step}" height="${volBottom - top}" fill="transparent"/></g>`;}).join('');
    const selected = visible.find(item => item.index === intradayIndex)?.row;
    return `<svg class="market-candles market-intraday-chart" viewBox="0 0 ${width} 442" role="img" tabindex="0" aria-label="${chartMode === 'minute' ? '一分钟 K 线' : '分时走势'}，滚轮缩放，左右方向键查看分钟记录" data-market-intraday-chart data-market-zoom="${chartMode}" data-chart-start="${start}" data-chart-end="${end}"><title>${E(intradayData.date)} ${chartMode === 'minute' ? '一分钟 K 线' : '分时走势'}</title><defs><clipPath id="market-minute-clip"><rect x="${left}" y="${top}" width="${width - left - right}" height="${volBottom - top}"/></clipPath></defs>${grid}${previous !== null ? `<path d="M${left} ${priceY(previous)}H${width - right}" class="market-previous-close"/><text x="${left + 3}" y="${priceY(previous) - 7}" class="market-axis-label">昨收 ${number(previous, 2)}</text>` : ''}<text x="${left}" y="304" class="market-axis-label">每分钟成交量（${E(intradayData.volumeUnit || '原始单位')}）</text><text x="${width - right + 12}" y="${volTop + 5}" class="market-axis-label">${shortAmount(volumeMax)}</text><g clip-path="url(#market-minute-clip)">${chartMode === 'intraday' ? `<path d="${path}" fill="none" class="market-price-line"/>` : ''}${marks}<path data-market-minute-crosshair d="${selected ? `M${pointX(selected)} ${top}V${volBottom}` : ''}" class="market-crosshair"/></g>${!visible.length ? `<text x="${(left + width - right) / 2}" y="160" text-anchor="middle" class="market-axis-label">这段时间没有分钟记录</text>` : ''}${[start, (start + end) / 2, end].map((offset, index) => `<text x="${left + index / 2 * (width - left - right)}" y="421" text-anchor="${index === 0 ? 'start' : index === 2 ? 'end' : 'middle'}" class="market-axis-label">${minuteLabel(offset)}</text>`).join('')}</svg>`;
  }
  function intradayView() {
    const data = intradayData, rows = data?.rows || [], days = data?.availableDates || [];
    const selected = rows[intradayIndex];
    const readout = selected && tradingMinute(selected.time) >= minuteWindow.start && tradingMinute(selected.time) <= minuteWindow.end ? intradayReadout(selected) : '这段时间没有分钟记录';
    const dateControl = `<div class="market-intraday-controls"><label>交易日期 <input type="date" data-market-intraday-date value="${E(intradayDate)}" aria-label="分时交易日期" ${intradayBusy ? 'disabled' : ''}></label>${action('刷新分时', 'retry-intraday', 'refresh', 'small', intradayBusy ? 'disabled' : '')}${action('查看最近交易日', 'latest-intraday', '', 'small', intradayBusy ? 'disabled' : '')}</div>`;
    if (intradayBusy) return dateControl + '<div class="market-empty" role="status"><p>正在读取该交易日的分钟记录…</p></div>';
    if (intradayError) return dateControl + `<div class="market-empty"><h2>该日分时暂不可用</h2><p role="alert">${E(intradayError)}</p><p>可在通达信补齐一分钟数据，或连接行情服务后重试。</p>${B('检查数据源', 'settings', 'data-nav="C01"')}</div>`;
    if (!rows.length) return dateControl + `<div class="market-empty"><h2>没有 ${date(intradayDate)} 的分钟记录</h2><p>日线收盘记录无法还原分时走势。${days.length ? `目前可读取到 ${date(days.at(-1))} 的分钟记录。` : '请补齐此交易日的一分钟数据。'}</p>${days.length ? action('查看 ' + days.at(-1), 'available-intraday', 'chart', 'primary', `data-date="${E(days.at(-1))}"`) : ''}</div>${warn(data?.warnings)}`;
    if (chartMode === 'minute' && !rows.some(completeOHLC)) return dateControl + `<div class="market-empty"><h2>该来源只提供分时价格</h2><p>缺少分钟开、高、低价，无法绘制可信的分钟 K 线。可查看已有的真实分时走势。</p><button class="btn primary" data-market-mode="intraday">查看分时</button></div>${warn(data?.warnings)}`;
    return dateControl + zoomControls() + `<div class="market-ohlc" data-market-minute-readout>${readout}</div>${intradayChart(rows)}<p class="market-subtle">${chartMode === 'minute' ? '每根 K 线对应一分钟的开、高、低、收。' : '分时线连接相邻交易分钟的收盘价，缺失分钟保持断线。'} 当前 ${number(rows.length)} 条分钟记录。</p><footer class="market-chart-source"><p>来源：${providerName(data.provider)} · 交易日期 ${date(data.date)}${data.cached ? ' · 本机缓存' : ''}</p><p>${data.asOf ? '数据截至 ' + time(data.asOf) + ' · ' : ''}采集于 ${time(data.receivedAt)} · 成交量（${E(data.volumeUnit || '原始单位')}） / 成交额（${E(data.amountUnit || '元')}）</p></footer>${warn(data.warnings)}`;
  }
  async function loadIntraday(force = false, latest = false) {
    const symbol = barsData?.symbol || AT.state.stock;
    if (!/^(sh|sz|bj)\d{6}$/.test(symbol || '')) return;
    if (!intradayDate && !latest) intradayDate = barsData?.rows?.[selectedIndex]?.date || barsData?.dataAsOf || '';
    const key = `${version}|${symbol}|${latest ? 'latest' : intradayDate}`;
    // A new date supersedes an in-flight request; repeated refreshes of the
    // same date still share the current request instead of duplicating work.
    if ((intradayBusy && (!force || intradayKey === key)) || (!force && intradayKey === key)) return;
    const selectedTime = intradayData?.rows?.[intradayIndex]?.time;
    intradayKey = key; intradayData = null; intradayError = ''; intradayBusy = true; const serial = ++intradaySerial; refreshVisible();
    try {
      const payload = {symbol}; if (!latest && intradayDate) payload.date = intradayDate;
      const data = await call('market.intraday', payload);
      if (serial !== intradaySerial) return;
      intradayData = data; intradayDate = data.date || intradayDate; intradayIndex = (data.rows || []).length - 1;
      intradayKey = `${version}|${symbol}|${intradayDate}`;
      if (minuteWindowKey !== intradayKey) {minuteWindow = {start: 0, end: 240}; minuteWindowKey = intradayKey;}
      else if (selectedTime) {const index = data.rows?.findIndex(row => row.time === selectedTime); if (index >= 0) intradayIndex = index;}
      const selected = data.rows?.[intradayIndex];
      if (!selected || tradingMinute(selected.time) < minuteWindow.start || tradingMinute(selected.time) > minuteWindow.end) {
        intradayIndex = (data.rows || []).findLastIndex(row => tradingMinute(row.time) >= minuteWindow.start && tradingMinute(row.time) <= minuteWindow.end);
      }
    } catch (error) {if (serial === intradaySerial) intradayError = safeError(error);}
    finally {if (serial === intradaySerial) {intradayBusy = false; intradayUpdated = Date.now(); if (AT.state.page === 'G01') AT.render?.();}}
  }
  function selectMinute(index) {
    const rows = intradayData?.rows || [], row = rows[index]; if (!row) return;
    intradayIndex = index;
    const minute = tradingMinute(row.time), span = minuteWindow.end - minuteWindow.start;
    if (minute < minuteWindow.start || minute > minuteWindow.end) {
      const start = clamp(minute < minuteWindow.start ? minute : minute - span, 0, 240 - span);
      minuteWindow = {start, end: start + span}; redrawChart();
    }
    const readout = $('[data-market-minute-readout]'); if (readout) readout.innerHTML = intradayReadout(row);
    const hit = $(`[data-market-minute-index="${index}"] rect:last-child`), line = $('[data-market-minute-crosshair]');
    if (hit && line) line.setAttribute('d', `M${Number(hit.getAttribute('x')) + Number(hit.getAttribute('width')) / 2} 26V390`);
  }
  function barsView() {
    const empty = online?.enabled && sourceLoaded ? '' : emptySource();
    if (empty) return AT.head('个股日线', '查看本地真实价格与成交量', B('返回个股行情', 'back', 'data-nav="M04"')) + empty;
    const currentKey = `${version}|${AT.state.stock}`, data = barsKey === currentKey ? barsData : null, error = barsKey === currentKey ? barsError : '';
    const title = data ? E(data.name || data.code) : E(AT.state.stock || '个股日线');
    const watchButton = data && data.kind !== 'index' ? AT.marketWatch?.button({symbol: data.symbol, name: data.name || data.code, kind: data.kind}) || '' : '';
    const header = AT.head(title, data ? `${E(data.code)} · ${marketName(data.market)} · K 线与分时` : '读取所选证券的本地日线', B('返回', 'back', 'data-action="back"') + watchButton + action('重新读取', 'retry-bars', 'refresh', '', barsBusy ? 'disabled' : '') + B('数据源设置', 'settings', 'data-nav="C01"'));
    const tabs = `<nav class="market-chart-modes" aria-label="行情图表类型">${[['daily', '日 K'], ['intraday', '分时'], ['minute', '分钟 K']].map(([mode, label]) => `<button type="button" data-market-mode="${mode}" aria-pressed="${chartMode === mode}" class="${chartMode === mode ? 'active' : ''}">${label}</button>`).join('')}</nav>`;
    if (barsBusy || !data && !error) return header + '<section class="panel market-empty" role="status"><h2>正在读取日线</h2><p>读取原始价格与成交量，首次打开会建立本机缓存。</p></section>';
    if (error) return header + `<section class="panel market-daily-panel">${tabs}${chartMode === 'daily' ? `<div class="market-empty"><h2>暂时无法显示这只证券的日线</h2><p role="alert">${E(error)}</p><div class="market-source-actions">${action('重新读取', 'retry-bars', 'refresh')}${B('搜索本地证券', 'search', 'data-nav="M04"', 'primary')}</div></div>` : intradayView()}</section>`;
    ensureStudies();
    const rows = data.rows || [], row = rows[selectedIndex] || rows.at(-1), latest = rows.at(-1), previous = rows.at(-2);
    const strategyPanel = data.kind === 'stock' ? AT.stockResearch?.render({symbol:data.symbol,version:data.dataVersion || version,source:source?.path}) || '' : '';
    const change = latest && previous?.close ? (latest.close / previous.close - 1) * 100 : null;
    const quote = quoteData?.items?.find(item => item.symbol === data.symbol);
    const quoteStrip = online?.enabled ? `<div class="market-quote-strip"><span>行情报价</span><strong class="${direction(quote?.changePercent)}">${number(quote?.price, 2)}</strong><b class="${direction(quote?.changePercent)}">${percent(quote?.changePercent)}</b><small class="${quote?.cached ? 'market-cache-label' : ''}">${quote ? quoteStamp(quote) : E(quoteError || '等待行情服务返回报价')}</small>${action('刷新', 'refresh-quotes', 'refresh', 'small', quoteBusy ? 'disabled' : '')}</div>` : '';
    return header + `<section class="panel market-daily-panel">${quoteStrip}<header class="market-daily-heading"><div class="market-last-price"><span>日线收盘</span><strong>${latest ? number(latest.close, 2) : '—'} <small>${data.kind === 'index' ? '点' : '元'}</small></strong><p class="${change === null ? '' : change >= 0 ? 'up' : 'down'}">${change === null ? '暂无前一交易日可比较' : `${change > 0 ? '+' : ''}${number(change, 2)}% · 较前一交易日`}</p></div><div class="market-daily-facts"><span class="tag">未复权</span><p>日线截至 ${date(data.dataAsOf)}</p><p>${rows.length} 根日线 · ${data.cached ? '本机缓存' : data.provider === 'tdx_mcp' ? '通达信 MCP' : '本地文件'}</p></div></header>${tabs}${chartMode !== 'daily' ? intradayView() + '</section>' : `
      ${barsFocusNote ? warn([barsFocusNote]) : ''}<div class="market-chart-toolbar"><h2>日线与成交量</h2><div class="segmented" aria-label="显示日线数量">${[60, 120, 240].map(limit => {const active = !dailyWindow && barsLimit === limit; return `<button type="button" data-market-limit="${limit}" aria-pressed="${active}" class="${active ? 'active' : ''}">近 ${limit} 日</button>`;}).join('')}</div></div>${rows.length ? AT.marketAnalysis.controls() + zoomControls() : ''}
      ${rows.length ? `<div class="market-ohlc" data-market-readout aria-live="off">${rowReadout(row)}</div><div data-market-study-readout>${AT.marketAnalysis.selected(studiesByDate.get(row.date))}</div>${AT.stockResearch?.legend({symbol:data.symbol,date:row.date}) || ''}${candleChart(rows)}<div data-market-study-panels>${forceStudyPanel()}</div><div class="market-day-selector"><span>双击日 K 查看当天分时 · 方向键选日，Enter 打开</span><div>${action('前一日', 'previous-day', '', 'small', selectedIndex <= 0 ? 'disabled' : '')}<input type="date" data-market-date aria-label="选择交易日期" min="${E(rows[0].date)}" max="${E(latest.date)}" value="${E(row.date)}">${action('后一日', 'next-day', '', 'small', selectedIndex >= rows.length - 1 ? 'disabled' : '')}${action('查看分时', 'day-intraday', 'chart', 'small')}</div><p data-market-date-error role="status"></p></div>` : '<div class="market-empty"><p>这只证券当前没有可显示的有效日线。</p></div>'}
      <details class="market-chart-details"><summary>数据来源与说明 · ${data.provider === 'tdx_mcp' ? providerName(data.provider) : '本地日线'}${data.warnings?.length ? ` · ${data.warnings.length} 项提示` : ''}</summary><footer class="market-chart-source"><p>来源：${data.provider === 'tdx_mcp' ? providerName(data.provider) : E(source.path)} · 价格未复权；成交量（${E(data.volumeUnit || '原始单位')}） / 成交额（${E(data.amountUnit || '元')}）</p><p>读取于 ${time(data.lastReadAt)} · 数据版本 ${E(data.dataVersion || '—')}</p></footer></details>${warn(data.warnings)}</section>`}` + strategyPanel;
  }
  async function loadList(force = false) {
    if (!source?.configured || sourceError) return;
    const key = `${version}|${listQuery}|${listOffset}`;
    if (!force && listKey === key) return;
    listKey = key; listBusy = true; listError = ''; const serial = ++listSerial;
    refreshVisible();
    try {
      const data = await call('market.search', {query: listQuery.trim(), offset: listOffset, limit: listLimit});
      if (serial !== listSerial) return;
      listData = data;
    } catch (error) { if (serial === listSerial) { listData = null; listError = safeError(error); } }
    finally { if (serial === listSerial) { listBusy = false; if (AT.state.page === 'M04') AT.render?.(); } }
  }
  async function loadBars(force = false) {
    if ((!source?.configured && !online?.enabled) || sourceError) return;
    const symbol = AT.state.stock, key = `${version}|${symbol}`;
    if (!force && barsKey === key) return;
    barsKey = key; barsError = ''; barsFocusNote = ''; selectedIndex = -1; dailyWindow = null; const serial = ++barsSerial;
    barsData = null; barsBusy = true; refreshVisible();
    try {
      let resolvedSymbol = symbol;
      if (/^\d{6}$/.test(symbol || '')) {
        const matches = await call('market.search', {query: symbol, offset: 0, limit: 200});
        const exact = matches.items.filter(item => item.code === symbol);
        if (exact.length !== 1) throw new Error(exact.length ? '本地存在相同代码的不同市场证券，请在个股行情中选择对应市场。' : '本地目录中尚未找到这只证券，请搜索已收录的证券或重新检查数据目录。');
        resolvedSymbol = exact[0].symbol;
      }
      if (serial !== barsSerial) return;
      let data;
      try {data = await call('market.bars', {symbol: resolvedSymbol, limit: 2400});}
      catch (error) {
        // Funnel computation can read TDX files before the market catalogue has
        // been indexed, especially immediately after switching directories.
        if (!String(error.message).includes('没有可用日线索引') || !source?.configured) throw error;
        await call('market.scan');
        if (serial !== barsSerial) return;
        data = await call('market.bars', {symbol: resolvedSymbol, limit: 2400});
      }
      if (serial !== barsSerial) return;
      barsData = data;
      selectedIndex = data.rows.length - 1;
      applyPendingDay();
      listKey = '';
      await loadStatus(true);
    } catch (error) { if (serial === barsSerial) barsError = safeError(error); }
    finally { if (serial === barsSerial) { barsBusy = false; if (AT.state.page === 'G01' && AT.state.stock === symbol) AT.render?.(); } }
  }
  function ensurePage(page) {
    if (sourceBusy || onlineBusy) return;
    if (!sourceLoaded && !sourcePending) { loadStatus(); return; }
    if (sourcePending || sourceBusy) return;
    if (page === 'M04' || page === 'M03') loadSnapshot();
    if (page === 'G01') loadBars();
    if (page === 'M01') loadOverview();
    if (page === 'M02' || page === 'M03') loadSectors();
    if (page !== 'M04' || !listBusy) loadQuotes();
    if (page === 'G01' && chartMode !== 'daily') loadIntraday();
  }
  async function chooseDirectory() {
    if (picking || sourceBusy) return;
    picking = true; sourceFeedback = {tone: 'neutral', message: '请在系统窗口中选择数据目录。'}; refreshVisible();
    try {
      if (typeof bridge.chooseTDXDirectory !== 'function') throw new Error('文件夹选择暂不可用，可以手动输入路径后保存。');
      const result = await bridge.chooseTDXDirectory();
      if (result?.canceled) { sourceFeedback = {tone: 'neutral', message: '已取消选择，当前配置未更改。'}; return; }
      if (!result?.ok || !result.path) throw new Error(result?.error?.message || result?.error || '无法选择目录，请重试。');
      pathDraft = result.path; sourceFeedback = {tone: 'neutral', message: '目录已选择，点击“保存并读取”后生效。'};
    } catch (error) { sourceFeedback = {tone: 'error', message: safeError(error)}; }
    finally { picking = false; refreshVisible(); }
  }
  async function configure(scanOnly = false) {
    if (sourceBusy || picking) return;
    const path = (pathDraft ?? source?.path ?? '').trim();
    if (!scanOnly && !path) { sourceFeedback = {tone: 'error', message: '请先选择或填写通达信数据目录。'}; refreshVisible(); $('#market-source-path')?.focus(); return; }
    sourceBusy = true; invalidateSources(); sourceFeedback = {tone: 'neutral', message: scanOnly ? '正在检查目录与日线覆盖…' : '正在保存目录并读取日线索引…'}; refreshVisible();
    const writing = (async () => {if (!scanOnly) {source = await call('market.configure', {path}); refreshVisible();} return call('market.scan');})(); writes.add(writing);
    try {
      await writing;
      sourceLoaded = false; await loadStatus(true);
      if (sourceError) throw new Error(sourceError);
      pathDraft = source?.path || path;
      sourceFeedback = {tone: 'success', message: `${scanOnly ? '目录已重新检查' : '目录已保存'}，共收录 ${number(source?.symbolCount || 0)} 只证券。`};
    } catch (error) {
      await loadStatus(true);
      sourceFeedback = {tone: 'error', message: safeError(error)};
    }
    finally { writes.delete(writing); sourceBusy = false; refreshVisible(); }
  }
  function selectDay(index) {
    const rows = barsData?.rows || [], row = rows[index];
    if (!row || AT.state.page !== 'G01') return;
    selectedIndex = index;
    const range = dailyRange(rows);
    if (index < range.start || index >= range.start + range.count) {
      dailyWindow = {count: range.count, start: index < range.start ? index : index - range.count + 1}; redrawChart();
    }
    const readout = $('[data-market-readout]'); if (readout) readout.innerHTML = rowReadout(row);
    const studyReadout = $('[data-market-study-readout]'); if (studyReadout) studyReadout.innerHTML = AT.marketAnalysis.selected(studiesByDate.get(row.date));
    const forceHair = $('[data-market-force-crosshair]'), visibleRange = dailyRange();
    if (forceHair) forceHair.setAttribute('d', `M${24 + 934 / visibleRange.count * (index - visibleRange.start + .5)} 26V150`);
    const input = $('[data-market-date]'); if (input) input.value = row.date;
    const chart = $('[data-market-chart]'), line = $('[data-market-crosshair]');
    if (chart && line) line.setAttribute('d', `M${Number(chart.dataset.chartLeft) + Number(chart.dataset.chartStep) * (index - Number(chart.dataset.chartStart) + .5)} 26V390`);
    const previous = $('[data-market-action="previous-day"]'), next = $('[data-market-action="next-day"]');
    if (previous) previous.disabled = index <= 0;
    if (next) next.disabled = index >= rows.length - 1;
    const error = $('[data-market-date-error]'); if (error) error.textContent = '';
    AT.stockResearch?.selected(AT.state.stock,row.date);
  }
  function redrawChart() {
    if (AT.state.page !== 'G01') return;
    const selector = chartMode === 'daily' ? '[data-market-chart]' : '[data-market-intraday-chart]';
    const chart = $(selector), rows = chartMode === 'daily' ? barsData?.rows : intradayData?.rows;
    if (!chart || !rows?.length) return;
    const focused = document.activeElement === chart;
    const forceFocused = document.activeElement?.matches('[data-market-indicator-chart]');
    chart.outerHTML = chartMode === 'daily' ? candleChart(rows) : intradayChart(rows);
    const panels = $('[data-market-study-panels]'); if (panels && chartMode === 'daily') panels.innerHTML = forceStudyPanel();
    if (focused) $(selector)?.focus({preventScroll: true});
    if (forceFocused) $('[data-market-indicator-chart]')?.focus({preventScroll: true});
    const caption = $('[data-market-zoom-range]'); if (caption) caption.textContent = zoomCaption();
    document.querySelectorAll('[data-market-limit]').forEach(button => {
      const active = !dailyWindow && Number(button.dataset.marketLimit) === barsLimit;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    });
  }
  function queueChartRedraw() {
    if (!zoomFrame) zoomFrame = requestAnimationFrame(() => {
      zoomFrame = 0;
      if (AT.state.page !== 'G01') return;
      redrawChart();
      if (chartMode === 'daily') selectDay(selectedIndex);
      else if (intradayIndex >= 0) selectMinute(intradayIndex);
      else {const readout = $('[data-market-minute-readout]'); if (readout) readout.textContent = '这段时间没有分钟记录';}
    });
  }
  function endChartDrag() {
    const drag = chartDrag; if (!drag) return;
    chartDrag = null;
    document.documentElement.classList.remove('market-dragging');
    if (drag.moved) suppressChartClickUntil = performance.now() + 650;
    if (drag.button === 2) suppressContextUntil = performance.now() + 650;
    // Capture on the stable root: the SVG is replaced while panning.
    if (document.documentElement.hasPointerCapture(drag.pointerId)) document.documentElement.releasePointerCapture(drag.pointerId);
  }
  document.addEventListener('pointerdown', event => {
    const chart = event.target.closest?.('[data-market-zoom]');
    if (!chart) {suppressChartClickUntil = 0; suppressContextUntil = 0; return;}
    if (AT.state.page !== 'G01' || ![0, 2].includes(event.button) || event.pointerType === 'touch') return;
    const matrix = chart.getScreenCTM(); if (!matrix) return;
    const inverse = matrix.inverse(), point = new DOMPoint(event.clientX, event.clientY).matrixTransform(inverse);
    if (point.x < 24 || point.x > 958 || point.y < 26 || point.y > (chart.hasAttribute('data-market-indicator-chart') ? 150 : 390)) return;
    endChartDrag();
    const range = chartMode === 'daily' ? dailyRange() : {start: minuteWindow.start, count: minuteWindow.end - minuteWindow.start};
    chartDrag = {pointerId: event.pointerId, button: event.button, mode: chartMode, symbol: AT.state.stock, date: intradayDate,
      inverse, x: point.x, clientX: event.clientX, clientY: event.clientY, range, moved: false};
    if (event.button === 2) suppressContextUntil = performance.now() + 650;
    event.preventDefault(); chart.focus({preventScroll: true});
  }, true);
  document.addEventListener('pointermove', event => {
    const drag = chartDrag; if (!drag || event.pointerId !== drag.pointerId) return;
    if (!(event.buttons & (drag.button === 0 ? 1 : 2)) || AT.state.page !== 'G01' || drag.mode !== chartMode || drag.symbol !== AT.state.stock || drag.date !== intradayDate) {endChartDrag(); return;}
    event.preventDefault(); event.stopPropagation();
    if (!drag.moved) {
      if (Math.hypot(event.clientX - drag.clientX, event.clientY - drag.clientY) < 4) return;
      drag.moved = true;
      document.documentElement.classList.add('market-dragging');
      // Delay capture until it is a drag so ordinary clicks and double-clicks
      // retain their candle target. Synthetic test pointers may not be active.
      try {document.documentElement.setPointerCapture(event.pointerId);} catch {}
    }
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(drag.inverse);
    const shift = (point.x - drag.x) / 934 * drag.range.count;
    if (drag.mode === 'daily') {
      const total = barsData?.rows?.length || 0;
      const start = clamp(Math.round(drag.range.start - shift), 0, total - drag.range.count);
      dailyWindow = {start, count: drag.range.count};
      selectedIndex = clamp(selectedIndex, start, start + drag.range.count - 1);
    } else {
      const start = clamp(drag.range.start - shift, 0, 240 - drag.range.count);
      minuteWindow = {start, end: start + drag.range.count};
      const rows = intradayData?.rows || [], selected = rows[intradayIndex];
      if (!selected || tradingMinute(selected.time) < start || tradingMinute(selected.time) > minuteWindow.end) {
        const anchor = selected ? tradingMinute(selected.time) : (start + minuteWindow.end) / 2;
        const visible = rows.map((row, index) => ({index, minute: tradingMinute(row.time)})).filter(item => item.minute >= start && item.minute <= minuteWindow.end);
        intradayIndex = visible.reduce((best, item) => best === null || Math.abs(item.minute - anchor) < Math.abs(best.minute - anchor) ? item : best, null)?.index ?? -1;
      }
    }
    queueChartRedraw();
  }, true);
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) document.addEventListener(type, event => {
    if (chartDrag?.pointerId === event.pointerId) endChartDrag();
  }, true);
  for (const type of ['blur', 'resize']) window.addEventListener(type, endChartDrag);
  document.addEventListener('visibilitychange', () => {if (document.hidden) endChartDrag();});
  for (const type of ['click', 'dblclick', 'auxclick']) document.addEventListener(type, event => {
    if (performance.now() < suppressChartClickUntil && (event.target.closest?.('[data-market-zoom]') || event.target === document.documentElement || event.target === document.body)) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('contextmenu', event => {
    if (event.target.closest?.('[data-market-zoom]') || chartDrag?.button === 2 || performance.now() < suppressContextUntil) {
      event.preventDefault(); event.stopPropagation(); suppressContextUntil = 0;
    }
  }, true);
  let wheelRemainder = 0, lastWheelAt = 0, lastWheelMode = '';
  document.addEventListener('wheel', event => {
    if (chartDrag) {event.preventDefault(); return;}
    const chart = event.target.closest?.('[data-market-zoom]');
    if (!chart || AT.state.page !== 'G01' || !event.deltaY || event.ctrlKey || event.metaKey) return;
    const matrix = chart.getScreenCTM(); if (!matrix) return;
    // SVG can be letterboxed at narrow/wide window sizes. Use its real transform,
    // not the element bounds, to keep the candle under the pointer anchored.
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    if (point.x < 24 || point.x > 958 || point.y < 26 || point.y > (chart.hasAttribute('data-market-indicator-chart') ? 150 : 390)) return;
    event.preventDefault(); event.stopPropagation();
    const ratio = (point.x - 24) / 934, now = performance.now();
    if (now - lastWheelAt > 180 || lastWheelMode !== chartMode) wheelRemainder = 0;
    lastWheelAt = now; lastWheelMode = chartMode;
    wheelRemainder += event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 442 : 1);
    const factor = Math.exp(clamp(wheelRemainder, -240, 240) * .002);
    if (chartMode === 'daily') {
      const rows = barsData?.rows || [], range = dailyRange(rows);
      const count = clamp(Math.round(range.count * factor), Math.min(10, rows.length), rows.length);
      if (count === range.count) {
        if (count === rows.length && factor > 1 || count === Math.min(10, rows.length) && factor < 1) wheelRemainder = 0;
        return;
      }
      const anchor = range.start + ratio * range.count;
      const start = clamp(Math.round(anchor - ratio * count), 0, rows.length - count);
      dailyWindow = {start, count}; selectedIndex = clamp(Math.floor(anchor), start, start + count - 1);
    } else {
      const span = minuteWindow.end - minuteWindow.start, nextSpan = clamp(span * factor, 10, 240);
      if (Math.abs(span - nextSpan) < .0001) {wheelRemainder = 0; return;}
      const anchor = minuteWindow.start + ratio * span;
      const start = clamp(anchor - ratio * nextSpan, 0, 240 - nextSpan);
      minuteWindow = {start, end: start + nextSpan};
      const rows = intradayData?.rows || [];
      const visible = rows.map((row, index) => ({index, minute: tradingMinute(row.time)})).filter(item => item.minute >= start && item.minute <= start + nextSpan);
      intradayIndex = visible.reduce((best, item) => best === null || Math.abs(item.minute - anchor) < Math.abs(best.minute - anchor) ? item : best, null)?.index ?? -1;
    }
    wheelRemainder = 0;
    queueChartRedraw();
  }, {passive: false});
  const previousView = AT.view;
  AT.view = page => {
    if (pendingDay && page === 'G01' && pendingDay.symbol !== AT.state.stock) pendingDay = null;
    if (chartDrag && (page !== 'G01' || chartDrag.symbol !== AT.state.stock || chartDrag.mode !== chartMode)) endChartDrag();
    if (page === 'G01' && lastRenderedPage !== 'G01') { barsSerial++; barsKey = ''; barsData = null; barsError = ''; barsBusy = false; }
    if (page === 'G01' && chartSymbol !== AT.state.stock) {chartSymbol = AT.state.stock; chartMode = 'daily'; intradayDate = ''; intradaySerial++; intradayBusy = false; intradayKey = ''; intradayData = null; intradayError = '';}
    if (['M02', 'M03'].includes(page) && page !== lastRenderedPage) {sectorOffset = 0; sectorQuery = '';}
    lastRenderedPage = page;
    if (!marketPages.has(page)) return previousView(page);
    queueMicrotask(() => { if (AT.state.page === page) ensurePage(page); });
    return page === 'C01' ? settingsView() : page === 'G04' ? statusView() : page === 'M01' ? overviewView() : page === 'M02' ? sectorsView() : page === 'M03' ? sectorsView(true) : page === 'M04' ? listView() : page === 'M05' ? newsView() : barsView();
  };
  function updateShell(page) {
    if (page !== 'M01') AT.marketAnalysis?.endHistoryDrag();
    if (page !== 'M02') AT.sectorTrends?.endDrag(false);
    if (page !== 'M02') AT.sectorComparison?.endDrag(false);
    if (page !== 'G01') pendingDay = null;
    if (page === 'M05' || page.startsWith('T')) {endChartDrag(); lastRenderedPage = page;}
    if (!marketPages.has(page)) return;
    const label = $('.demo-label'), status = $('.statusbar>span:first-child');
    if (label) label.textContent = page === 'C01' ? '桌面设置' : '真实行情 · TDX / MCP';
    const asOf = page === 'G01' && barsKey === `${version}|${AT.state.stock}` && barsData ? barsData.dataAsOf : source?.latestDate;
    if (status) { status.replaceChildren(); const dot = document.createElement('i'); dot.className = 'status-dot'; status.append(dot, document.createTextNode(source?.configured ? ` 本地日线${asOf ? ' · 截至 ' + asOf : ''} · MCP ${online?.enabled ? online.connected ? '已连接' : '已启用' : '未启用'}` : ' 行情来源 · 尚未选择数据目录')); }
  }
  function applyPendingDay() {
    if (!pendingDay || pendingDay.symbol !== barsData?.symbol) return;
    const index = barsData.rows.findIndex(row => row.date === pendingDay.date);
    barsFocusNote = index < 0 ? `已加载的日线中没有 ${pendingDay.date} 的记录，请检查历史覆盖范围。` : '';
    if (index >= 0) {
      selectedIndex = index;
      const count = Math.min(barsLimit, barsData.rows.length);
      dailyWindow = {count, start: clamp(index - Math.floor(count / 2), 0, barsData.rows.length - count)};
    }
    pendingDay = null;
  }
  AT.marketUI = {get status() {return source ? structuredClone(source) : null;}, updateShell, refreshVisible,
    getAIContext() {
      if(AT.state.page==='G01'&&barsKey===`${version}|${AT.state.stock}`&&barsData?.symbol===AT.state.stock) {
        const row=barsData.rows?.[selectedIndex]||barsData.rows?.at(-1);
        return {pageId:'G01',dataDates:row?[row.date]:[],filters:{symbol:barsData.symbol,selectedDay:row?.date||''},
          summary:`当前个股 ${barsData.name||barsData.symbol}，选中交易日 ${row?.date||'尚未加载'}。`,
          evidence:{selectedStock:{symbol:barsData.symbol,code:barsData.code,name:barsData.name},source:{asOf:row?.date||'',latestLoadedDate:barsData.rows?.at(-1)?.date,priceBasis:barsData.adjustment||'unknown'}},
          limitations:['个股顶部报价、最新日线摘要和回测交易可能晚于选中的K线日期；历史提问时会排除这些页面结果，仅补充截至所问日期的日线。']};
      }
      if (AT.state.page !== 'M01') return null;
      const {day} = AT.marketAnalysis.selectedObservation(researchData);
      return AT.pageAIContext?.marketStyle({data: researchData, day, busy: researchBusy, error: researchError, historyDays: researchHistoryDays}) || null;
    },
    diagnostics() {
      return {local: {loaded: sourceLoaded, configured: Boolean(source?.configured), available: Boolean(source?.available), lastScanAt: source?.lastScannedAt || null, error: Boolean(sourceError)},
        online: {enabled: Boolean(online?.enabled), connected: Boolean(online?.connected), available: Boolean(online?.available), lastCheckedAt: online?.lastCheckedAt || null, followLocal: online?.followLocal ?? null}};
    },
    openDay(symbol, date) {
      if (!/^(sh|sz|bj)\d{6}$/.test(symbol) || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
      pendingDay = {symbol, date}; chartMode = 'daily'; barsFocusNote = '';
      AT.navigate('G01', symbol);
      if (barsData?.symbol === symbol) {applyPendingDay(); refreshVisible();}
      return true;
    },
    async refresh() {await loadStatus(true); return source;}, async flush() {const results = await Promise.allSettled([...writes]); return results.every(result => result.status === 'fulfilled');}};
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-market-action],[data-market-limit],[data-market-mode],[data-market-sector],[data-market-history-days],[data-market-cancel]');
    if (!button || button.disabled) return;
    event.preventDefault(); event.stopPropagation();
    if (button.dataset.marketCancel) {cancelMarketJob(button.dataset.marketCancel); return;}
    if (button.dataset.marketHistoryDays) {const count = Number(button.dataset.marketHistoryDays); if ([60, 120, 250, 500, 1000].includes(count)) {researchHistoryDays = count; loadResearch();} return;}
    if (button.dataset.marketSector) {sectorId = button.dataset.marketSector; sectorDetail = null; sectorKey = ''; sectorOffset = 0; sectorQuery = ''; AT.navigate('M03'); return;}
    if (button.dataset.marketMode) {if (chartMode === 'daily' && button.dataset.marketMode !== 'daily') intradayDate = barsData?.rows?.[selectedIndex]?.date || intradayDate; chartMode = button.dataset.marketMode; if (chartMode !== 'daily') loadIntraday(intradayBusy); refreshVisible(); return;}
    if (button.dataset.marketLimit) {barsLimit = Number(button.dataset.marketLimit); dailyWindow = null; selectedIndex = (barsData?.rows?.length || 0) - 1; redrawChart(); selectDay(selectedIndex); return;}
    const name = button.dataset.marketAction;
    if (name === 'reset-zoom') {
      if (chartMode === 'daily') {dailyWindow = null; selectedIndex = (barsData?.rows?.length || 0) - 1; redrawChart(); selectDay(selectedIndex);}
      else {minuteWindow = {start: 0, end: 240}; intradayIndex = (intradayData?.rows?.length || 0) - 1; redrawChart(); selectMinute(intradayIndex);}
    }
    if (name === 'refresh-research') loadResearch(true);
    if (name === 'refresh-snapshot') loadSnapshot(true);
    if (name === 'choose') chooseDirectory();
    if (name === 'online-save' || name === 'online-test') saveOnline(name === 'online-test');
    if (name === 'refresh-quotes') loadQuotes(true);
    if (name === 'refresh-sectors') {AT.sectorTrends?.refresh(); AT.sectorComparison?.refresh(); loadSectors(true); if (AT.state.page === 'M03') loadSnapshot(true);}
    if (name === 'previous-sector' || name === 'next-sector') {sectorOffset = Math.max(0, sectorOffset + (name === 'next-sector' ? listLimit : -listLimit)); refreshVisible();}
    if (name === 'retry-intraday') loadIntraday(true);
    if (name === 'latest-intraday') loadIntraday(true, true);
    if (name === 'available-intraday') {intradayDate = button.dataset.date; loadIntraday(true);}
    if (name === 'day-intraday') {chartMode = 'intraday'; intradayDate = barsData?.rows?.[selectedIndex]?.date || ''; loadIntraday(true);}
    if (name === 'configure') configure();
    if (name === 'scan') configure(true);
    if (name === 'status') {sourceLoaded = false; loadStatus(true); refreshVisible();}
    if (name === 'search' || name === 'retry-list') {clearTimeout(searchTimer); listOffset = 0; loadList(true);}
    if (name === 'previous-list' || name === 'next-list') {listOffset = Math.max(0, listOffset + (name === 'next-list' ? listLimit : -listLimit)); loadList();}
    if (name === 'retry-bars') loadBars(true);
    if (name === 'previous-day' || name === 'next-day') selectDay(selectedIndex + (name === 'next-day' ? 1 : -1));
  }, true);
  document.addEventListener('input', event => {
    if (event.target.matches('[data-market-sector-query]')) {sectorQuery = event.target.value; sectorOffset = 0; clearTimeout(sectorTimer); sectorTimer = setTimeout(refreshVisible, 220);}
    if (event.target.matches('[data-market-online-field]')) {const form = event.target.form; onlineDraft = {root: form.elements.root.value, followLocal: form.elements.followLocal.checked, enabled: form.elements.enabled.checked, refreshSeconds: form.elements.refreshSeconds.value}; form.elements.root.disabled = onlineDraft.followLocal; onlineFeedback = {tone: 'neutral', message: '有未保存的更改'}; const note = $('[data-market-online-feedback]'); if (note) {note.textContent = onlineFeedback.message; note.dataset.tone = 'neutral';}}
    if (event.target.matches('[data-market-path]')) {pathDraft = event.target.value; sourceFeedback = {tone: 'neutral', message: '目录有未保存的更改'}; const note = $('.market-source-form .market-feedback'); if (note) {note.textContent = sourceFeedback.message; note.dataset.tone = 'neutral';}}
    if (event.target.matches('[data-market-query]')) {listQuery = event.target.value; listOffset = 0; clearTimeout(searchTimer); searchTimer = setTimeout(() => {if (AT.state.page === 'M04') loadList();}, 280);}
  }, true);
  document.addEventListener('submit', event => {
    if (event.target.id === 'market-source-form') {event.preventDefault(); event.stopPropagation(); configure();}
    if (event.target.id === 'market-online-form') {event.preventDefault(); event.stopPropagation(); saveOnline();}
    if (event.target.id === 'market-search-form') {event.preventDefault(); event.stopPropagation(); clearTimeout(searchTimer); listOffset = 0; loadList(true);}
  }, true);
  document.addEventListener('change', event => {
    if (event.target.matches('[data-market-intraday-date]')) {if (event.target.value) {intradayDate = event.target.value; loadIntraday(true);} return;}
    if (!event.target.matches('[data-market-date]')) return;
    const index = barsData?.rows.findIndex(row => row.date === event.target.value) ?? -1;
    if (index >= 0) selectDay(index);
    else {const note = $('[data-market-date-error]'); if (note) note.textContent = '当前显示范围没有这一天的日线，请选择交易日。';}
  });
  document.addEventListener('pointermove', event => {const candle = event.target.closest('[data-market-index]'); if (candle) selectDay(Number(candle.dataset.marketIndex)); const minute = event.target.closest('[data-market-minute-index]'); if (minute) selectMinute(Number(minute.dataset.marketMinuteIndex));});
  document.addEventListener('click', event => {const candle = event.target.closest('[data-market-index]'); if (candle) selectDay(Number(candle.dataset.marketIndex)); const minute = event.target.closest('[data-market-minute-index]'); if (minute) selectMinute(Number(minute.dataset.marketMinuteIndex));});
  document.addEventListener('dblclick', event => {const candle = event.target.closest('[data-market-index]'); if (!candle) return; event.preventDefault(); event.stopPropagation(); selectDay(Number(candle.dataset.marketIndex)); chartMode = 'intraday'; intradayDate = barsData?.rows?.[selectedIndex]?.date || ''; loadIntraday(true);}, true);
  document.addEventListener('keydown', event => {
    if (event.target.matches('[data-market-intraday-chart]') && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {event.preventDefault(); const count = intradayData?.rows?.length || 0; selectMinute(event.key === 'Home' ? 0 : event.key === 'End' ? count - 1 : Math.max(0, Math.min(count - 1, intradayIndex + (event.key === 'ArrowRight' ? 1 : -1)))); return;}
    if (!event.target.matches('[data-market-chart],[data-market-indicator-chart]') || !['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Enter') {chartMode = 'intraday'; intradayDate = barsData?.rows?.[selectedIndex]?.date || ''; loadIntraday(true); return;}
    selectDay(event.key === 'Home' ? 0 : event.key === 'End' ? barsData.rows.length - 1 : Math.max(0, Math.min(barsData.rows.length - 1, selectedIndex + (event.key === 'ArrowRight' ? 1 : -1))));
  });
  setInterval(() => {
    if (document.hidden || !online?.enabled || !['M01', 'M02', 'M04', 'G01'].includes(AT.state.page)) return;
    if (AT.state.page === 'M02') {
      if (!sectorBusy && Date.now() - sectorQuoteUpdated >= Math.max(10, Number(online.refreshSeconds || 30)) * 1000) loadSectorQuotes(true);
      return;
    }
    if (Date.now() - quoteUpdated >= Math.max(10, Number(online.refreshSeconds || 30)) * 1000) loadQuotes(true);
    const today = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
    if (AT.state.page === 'G01' && chartMode !== 'daily' && intradayDate === today && Date.now() - intradayUpdated >= Math.max(10, Number(online.refreshSeconds || 30)) * 1000) loadIntraday(true);
  }, 1000);
})();
