/* Master Trader Delivery Slip Database & Reverse-Engineering Study Module (T11). */
(() => {
  if (typeof AT === 'undefined' || !AT.isDesktop) return;
  const bridge = window.atradeDesktop, E = AT.escape;
  const finite = v => v !== null && v !== undefined && v !== '' && typeof v !== 'boolean' && Number.isFinite(Number(v));
  const num = (v, d = 2) => finite(v) ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
  const pct = (v, d = 2, signed = false) => finite(v) ? `${signed && Number(v) > 0 ? '+' : ''}${num(v, d)}%` : '—';
  const money = v => {
    if (!finite(v)) return '—';
    const n = Number(v);
    if (Math.abs(n) >= 1e8) return `${num(n / 1e8, 2)} 亿`;
    if (Math.abs(n) >= 1e4) return `${num(n / 1e4, 2)} 万`;
    return `¥${num(n, 2)}`;
  };
  const tone = v => !finite(v) || Number(v) === 0 ? '' : Number(v) > 0 ? 'ms-up' : 'ms-down';
  const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const errText = e => typeof e === 'string' ? e : e?.message || '操作失败，请重试';
  const btn = (label, action, attrs = '', cls = '') =>
    `<button type="button" class="analysis-tools-button${cls ? ' ' + cls : ''}" data-ms-action="${action}" ${attrs}>${E(label)}</button>`;

  const STAGE_COLORS = {
    trial: '#D97706',
    advance: '#C7383F',
    chop: '#7C3AED',
    decline: '#15803D',
    unknown: '#94A3B8',
  };

  const STYLE_DEFAULT_SLOTS = {
    wujiang_dynamic: 4,
    huichanglan_mainline: 3,
    xiwangjiejie_1to2: 3,
    dongshizhang_highboard: 2,
    ruhan_halfway: 5,
    songsong_defensive: 4,
  };

  const STYLE_TO_STRATEGY_ID = {
    wujiang_dynamic: 'wujiang_mainline_v1',
    huichanglan_mainline: 'mainline_halfway_v1',
    xiwangjiejie_1to2: 'first_to_second_v1',
    dongshizhang_highboard: 'highboard_sniper_v1',
    ruhan_halfway: 'ruhan_halfway_v1',
    songsong_defensive: 'strong_trend_dip_v1',
  };

  function resolveMasterStrategyId(master, overrideStyleKey = '') {
    const skey = inferStyleKeyFromMaster(master, overrideStyleKey);
    return STYLE_TO_STRATEGY_ID[skey] || 'wujiang_mainline_v1';
  }

  function jumpToStrategyWorkspace(targetMode = 'signals', master = activeMaster(), overrideStyleKey = '') {
    const strategyId = resolveMasterStrategyId(master, overrideStyleKey);
    try {
      const sharedRaw = JSON.parse(localStorage.getItem('tdx-strategy-shared-v1') || '{}');
      localStorage.setItem('tdx-strategy-shared-v1', JSON.stringify({
        last_strategy_id: strategyId,
        params_by_strategy: sharedRaw?.params_by_strategy || {},
        presets_by_strategy: sharedRaw?.presets_by_strategy || {},
      }));
    } catch {}
    const page = targetMode === 'backtest' ? 'R03' : 'R12';
    const route = targetMode === 'backtest'
      ? `/backtest?strategy_id=${encodeURIComponent(strategyId)}`
      : `/signals?strategy_id=${encodeURIComponent(strategyId)}&mode=full_market`;
    if (window.StrategyWorkspace?.openStrategy) {
      window.StrategyWorkspace.openStrategy(page, route);
    } else {
      AT.navigate?.(page);
    }
  }

  const SLOT_LINE_COLORS = ['#C7383F', '#2457E5', '#D97706', '#7C3AED', '#0F766E', '#15803D', '#DB2777', '#475569', '#EA580C', '#0284C7'];

  function inferStyleKeyFromMaster(master, overrideKey = '') {
    if (overrideKey && STYLE_DEFAULT_SLOTS[overrideKey]) return overrideKey;
    const txt = `${master?.name || ''} ${master?.styleTag || ''}`;
    if (txt.includes('灰常懒')) return 'huichanglan_mainline';
    if (txt.includes('希望') || txt.includes('一进二')) return 'xiwangjiejie_1to2';
    if (txt.includes('董事长') || txt.includes('隔夜排单')) return 'dongshizhang_highboard';
    if (txt.includes('如晗') || txt.includes('鹰舞')) return 'ruhan_halfway';
    if (txt.includes('松松') || txt.includes('可转债')) return 'songsong_defensive';
    return 'wujiang_dynamic';
  }

  const state = {
    loaded: false,
    masters: [],
    activeId: '',
    tab: 'overview',
    analysis: null,
    analysisBusy: false,
    analysisError: '',
    analysisSerial: 0,
    tradesData: null,
    tradesFilter: { query: '', side: 'all', dateFrom: '', dateTo: '', page: 1, pageSize: 50 },
    selBuyPage: 1,
    posEpPage: 1,
    reports: [],
    activeReportId: '',
    aiBusy: false,
    compareBusy: false,
    busy: false,
    message: null, // { tone: 'ok'|'err'|'warn', text: '' }
    confirmDeleteMaster: false,
    editorMode: '', // '' | 'new-master' | 'edit-master' | 'import' | 'edit-trade'
    masterDraft: { id: '', name: '', styleTag: '', initialCapital: '', sourceDesc: '', notes: '' },
    tradeDraft: { id: '', tradeDate: today(), tradeTime: '', side: 'buy', code: '', name: '', quantity: '', price: '', amount: '', fees: '', positionRatio: '', accountEquity: '', note: '' },
    imp: {
      tab: 'images', // 'images' | 'file' | 'manual'
      images: [],
      defaultDate: '',
      defaultYear: today().slice(0, 4),
      inputHash: '',
      source: 'ocr',
      rows: [],
      warnings: [],
      busy: false,
      reviewed: false,
      fileName: '',
    },
    pendingWrites: new Set(),
    sourceKey: '',
    presetsBusy: false,
    advisor: null,
    advisorBusy: false,
    advisorError: '',
    planSection: 'all', // 'all' | 'holdings' | 'newpos' | 'orders'
    advisorOpts: {
      styleKey: '',
      targetDate: '',
      maxPositions: '',
      useCustomHoldings: false,
      customEquity: '100000',
      customCash: '',
      customHoldings: [], // [{ code, name, quantity, averageCost }]
      draftCode: '',
      draftName: '',
      draftQty: '1000',
      draftCost: '',
    },
    backtest: null,
    backtestBusy: false,
    backtestError: '',
    backtestTasks: [],
    backtestTasksLoaded: false,
    autoBootstrappedBt: false,
    activeBacktestTaskId: '',
    backtestTask: null,
    backtestPollTimer: null,
    btCurveMode: 'return', // 'return' | 'equity' | 'slots'
    btCurveIndex: -1,
    btTradeFilter: 'all',
    btTradeQuery: '',
    btTradeSort: 'desc',
    btTradePage: 1,
    btTradePageSize: 20,
    btSnapshotDate: '',
    btReplayOpen: false,
    backtestOpts: {
      styleKey: '',
      startDate: '2026-05-01',
      endDate: '2026-11-25',
      initialCapital: '100000',
      maxPositions: '',
      comparePositionCounts: true,
      stopLossPct: '-5',
      useRegimeSwitch: true,
      useFastWeakCut: true,
      useAddOnWin: true,
      usePartialTrim: true,
      dataSource: 'auto',
    },
  };

  const BT_STATUS_LABEL = {
    queued: '排队中',
    running: '计算中',
    pausing: '正在暂停',
    stopping: '正在停止',
    paused: '已暂停',
    stopped: '已停止',
    completed: '已完成',
    failed: '执行失败',
  };
  const btTaskAlive = t => ['queued', 'running', 'pausing', 'stopping'].includes(t?.status);
  const isWorkbenchTab = t => t === 'backtest' || t === 'advisor';

  const render = () => { if (AT.state.page === 'T11') AT.render?.(); };
  const activeMaster = () => state.masters.find(m => m.id === state.activeId) || null;
  const setMessage = (text, kind = 'ok') => { state.message = text ? { tone: kind, text } : null; };

  async function callMarket(method, payload = {}, isWrite = false) {
    if (typeof bridge?.market !== 'function') throw new Error('本地行情服务尚未启动，请重启桌面应用。');
    const work = Promise.resolve(bridge.market(method, payload)).then(res => {
      if (!res?.ok) throw new Error(errText(res?.error));
      return res.data;
    });
    if (isWrite) state.pendingWrites.add(work);
    try { return await work; } finally { state.pendingWrites.delete(work); }
  }

  async function callBusiness(method, payload = {}, isWrite = false) {
    if (typeof bridge?.business !== 'function') throw new Error('本地研究任务服务尚未启动，请重启桌面应用。');
    const work = Promise.resolve(bridge.business(method, payload)).then(res => {
      if (!res?.ok) throw new Error(errText(res?.error));
      return res.data;
    });
    if (isWrite) state.pendingWrites.add(work);
    try { return await work; } finally { state.pendingWrites.delete(work); }
  }

  async function callAI(method, payload = {}) {
    if (typeof bridge?.ai !== 'function') throw new Error('AI 服务暂不可用，请重启桌面应用。');
    const res = await bridge.ai(method, payload);
    if (!res?.ok) throw new Error(errText(res?.error));
    return res.data;
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
      reader.onerror = () => reject(new Error('文件读取失败'));
      reader.readAsDataURL(file);
    });
  }

  function downloadJSON(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function formatMarkdownSafe(raw) {
    const lines = String(raw || '').replace(/\r\n/g, '\n').split('\n');
    const html = [];
    let inList = false;
    let inTable = false;
    let tableHeaderDone = false;
    const inline = text => E(text)
      .replace(/&lt;br\s*\/?&gt;/gi, '<br>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
    const closeList = () => { if (inList) { html.push('</ul>'); inList = false; } };
    const closeTable = () => { if (inTable) { html.push('</tbody></table>'); inTable = false; tableHeaderDone = false; } };
    for (let i = 0; i < lines.length; i++) {
      const t = lines[i].trim();
      if (!t) { closeList(); closeTable(); continue; }
      if (/^---+$/.test(t)) { closeList(); closeTable(); html.push('<hr>'); continue; }
      if (t.startsWith('|') && t.endsWith('|')) {
        closeList();
        const cells = t.slice(1, -1).split('|').map(c => c.trim());
        const isSep = cells.every(c => /^:?-{2,}:?$/.test(c));
        if (isSep) continue;
        if (!inTable) {
          inTable = true;
          tableHeaderDone = true;
          html.push(`<table><thead><tr>${cells.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>`);
        } else {
          html.push(`<tr>${cells.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`);
        }
        continue;
      }
      closeTable();
      if (/^###\s+/.test(t)) { closeList(); html.push(`<h3>${inline(t.replace(/^###\s+/, ''))}</h3>`); continue; }
      if (/^##\s+/.test(t)) { closeList(); html.push(`<h2>${inline(t.replace(/^##\s+/, ''))}</h2>`); continue; }
      if (/^#\s+/.test(t)) { closeList(); html.push(`<h1>${inline(t.replace(/^#\s+/, ''))}</h1>`); continue; }
      if (/^>\s*/.test(t)) { closeList(); html.push(`<blockquote>${inline(t.replace(/^>\s*/, ''))}</blockquote>`); continue; }
      if (/^[-*•]\s+/.test(t) || /^\d+[.、]\s+/.test(t)) {
        if (!inList) { html.push('<ul>'); inList = true; }
        html.push(`<li>${inline(t.replace(/^([-*•]|\d+[.、])\s+/, ''))}</li>`);
        continue;
      }
      closeList();
      html.push(`<p>${inline(t)}</p>`);
    }
    closeList();
    closeTable();
    return html.join('');
  }

  // ── Data Loading ───────────────────────────────────────────────────────────

  async function refreshMasters(selectId = '') {
    try {
      const data = await callMarket('market.masters.list');
      state.masters = data.items || [];
      state.loaded = true;
      if (selectId && state.masters.some(m => m.id === selectId)) {
        state.activeId = selectId;
      } else if (!state.activeId || !state.masters.some(m => m.id === state.activeId)) {
        state.activeId = state.masters[0]?.id || '';
      }
      render();
      if (state.activeId) {
        await Promise.all([loadAnalysis(false), loadTrades(), loadReports()]);
      } else {
        state.analysis = null;
        state.tradesData = null;
        state.reports = [];
        render();
      }
    } catch (error) {
      state.loaded = true;
      setMessage(errText(error), 'err');
      render();
    }
  }

  async function loadAnalysis(refresh = false) {
    const mid = state.activeId;
    if (!mid) return;
    const serial = ++state.analysisSerial;
    state.analysisBusy = true;
    state.analysisError = '';
    render();
    try {
      const data = await callMarket('market.masters.analyze', { masterId: mid, refresh });
      if (serial !== state.analysisSerial || state.activeId !== mid) return;
      state.analysis = data;
    } catch (error) {
      if (serial === state.analysisSerial && state.activeId === mid) {
        state.analysisError = errText(error);
      }
    } finally {
      if (serial === state.analysisSerial) {
        state.analysisBusy = false;
        render();
      }
    }
  }

  async function loadTrades() {
    const mid = state.activeId;
    if (!mid) return;
    try {
      const f = state.tradesFilter;
      const pageSize = Math.max(10, Math.min(200, Number(f.pageSize) || 50));
      const page = Math.max(1, Number(f.page) || 1);
      const data = await callMarket('market.masters.trades.list', {
        masterId: mid,
        query: f.query,
        side: f.side,
        dateFrom: f.dateFrom || undefined,
        dateTo: f.dateTo || undefined,
        offset: (page - 1) * pageSize,
        limit: pageSize,
      });
      if (state.activeId === mid) {
        const maxPage = Math.max(1, Math.ceil((data.total || 0) / pageSize));
        if (page > maxPage && data.total > 0) {
          f.page = maxPage;
          return loadTrades();
        }
        state.tradesData = data;
        render();
      }
    } catch (error) {
      setMessage(errText(error), 'err');
      render();
    }
  }

  async function loadReports() {
    const mid = state.activeId;
    if (!mid) return;
    try {
      const data = await callMarket('market.masters.reports.list', { masterId: mid });
      if (state.activeId === mid) {
        state.reports = data.items || [];
        if (!state.activeReportId || !state.reports.some(r => r.id === state.activeReportId)) {
          state.activeReportId = state.reports[0]?.id || '';
        }
        render();
      }
    } catch {}
  }

  function selectMaster(id) {
    if (!id || id === state.activeId) return;
    state.activeId = id;
    state.confirmDeleteMaster = false;
    state.editorMode = '';
    state.analysis = null;
    state.analysisError = '';
    state.tradesData = null;
    state.tradesFilter.page = 1;
    state.selBuyPage = 1;
    state.posEpPage = 1;
    state.reports = [];
    state.activeReportId = '';
    state.advisor = null;
    state.advisorError = '';
    state.advisorOpts.styleKey = '';
    state.backtestError = '';
    state.backtestOpts.styleKey = '';
    state.message = null;
    resetImportState();
    const nextMaster = state.masters.find(m => m.id === id) || null;
    const matchedStyle = inferStyleKeyFromMaster(nextMaster);
    if (state.backtestTasks.length) {
      const matchingTask = state.backtestTasks.find(t => t.config?.masterId === id || t.config?.styleKey === matchedStyle);
      if (matchingTask && matchingTask.id !== state.activeBacktestTaskId) {
        state.activeBacktestTaskId = matchingTask.id;
        state.backtestTask = matchingTask;
        state.backtest = null;
        if (isWorkbenchTab(state.tab)) void loadBacktestTaskDetail(matchingTask.id, true);
      }
    }
    render();
    Promise.all([loadAnalysis(false), loadTrades(), loadReports()]);
    if (isWorkbenchTab(state.tab)) runAdvisor();
  }

  async function installPresets() {
    if (state.presetsBusy) return;
    state.presetsBusy = true;
    setMessage('正在载入顽主杯 6 位顶级高手实盘交割单（5,715 笔）与深度操盘手册，请稍候…', 'ok');
    render();
    try {
      const res = await callMarket('market.masters.presets.install', {}, true);
      const firstId = res.masters?.[0]?.id || '';
      setMessage(`已成功载入 ${res.installedCount} 位顽主杯顶级高手（新增 ${res.insertedTrades} 笔交割单，${res.insertedReports} 篇深度风格分析文章）！`, 'ok');
      await refreshMasters(firstId);
    } catch (error) {
      setMessage(errText(error), 'err');
    } finally {
      state.presetsBusy = false;
      render();
    }
  }

  async function runAdvisor() {
    const master = activeMaster();
    if (state.advisorBusy) return;
    state.advisorBusy = true;
    state.advisorError = '';
    render();
    try {
      const opts = state.advisorOpts;
      const btOpts = state.backtestOpts;
      const sharedStyle = btOpts.styleKey || opts.styleKey || '';
      const customMax = String(btOpts.maxPositions !== '' ? btOpts.maxPositions : (opts.maxPositions || '')).trim();
      const payload = {
        masterId: master?.id || undefined,
        styleKey: sharedStyle || undefined,
        targetDate: opts.targetDate || undefined,
        maxPositions: customMax ? Number(customMax) : undefined,
        customMaxPositions: Boolean(customMax),
        stopLossPct: Number(btOpts.stopLossPct || -5),
        useRegimeSwitch: Boolean(btOpts.useRegimeSwitch),
        useFastWeakCut: Boolean(btOpts.useFastWeakCut),
        useAddOnWin: Boolean(btOpts.useAddOnWin),
        usePartialTrim: Boolean(btOpts.usePartialTrim),
      };
      if (opts.useCustomHoldings) {
        payload.customPositions = opts.customHoldings.map(h => ({
          code: h.code, name: h.name || h.code, quantity: Number(h.quantity || 0), averageCost: Number(h.averageCost || 0),
        }));
        const unifiedEquity = Number(btOpts.initialCapital || opts.customEquity || 100000);
        const summaryObj = { equity: unifiedEquity };
        if (String(opts.customCash || '').trim() !== '') {
          summaryObj.cash = Number(opts.customCash);
        }
        payload.customSummary = summaryObj;
      }
      state.advisor = await callMarket('market.masters.advise', payload);
      if (!btOpts.styleKey && state.advisor?.styleKey) {
        btOpts.styleKey = state.advisor.styleKey;
        opts.styleKey = state.advisor.styleKey;
      }
      if (!opts.targetDate && state.advisor?.marketSnapshot?.date) opts.targetDate = state.advisor.marketSnapshot.date;
    } catch (error) {
      state.advisorError = errText(error);
    } finally {
      state.advisorBusy = false;
      render();
    }
  }

  function notifyTaskShell() {
    try { void window.ResearchModule?.refresh?.(); } catch {}
    try { window.StrategyWorkspace?.updateShell?.(); } catch {}
  }

  function clearBacktestPoll() {
    if (state.backtestPollTimer) {
      clearTimeout(state.backtestPollTimer);
      state.backtestPollTimer = null;
    }
  }

  function scheduleBacktestPoll() {
    clearBacktestPoll();
    if (!state.backtestTasks.some(btTaskAlive) && !btTaskAlive(state.backtestTask)) return;
    state.backtestPollTimer = setTimeout(() => {
      state.backtestPollTimer = null;
      void loadBacktestTasks(state.activeBacktestTaskId, true);
    }, 750);
  }

  async function fetchFullBacktestDetail(taskId) {
    const first = await callBusiness('research.get', { id: taskId, pageSize: 1000, tradeOffset: 0 });
    if (!first?.result) return first;
    const allTrades = [...(first.result.trades || [])];
    const total = Number(first.result.pagination?.trades?.total || allTrades.length);
    let offset = allTrades.length;
    while (offset < total && offset < 5000) {
      const next = await callBusiness('research.get', { id: taskId, pageSize: 1000, tradeOffset: offset });
      const chunk = next?.result?.trades || [];
      if (!chunk.length) break;
      allTrades.push(...chunk);
      offset += chunk.length;
    }
    first.result = { ...first.result, trades: allTrades, _taskId: taskId };
    return first;
  }

  async function loadBacktestTaskDetail(taskId, silent = false) {
    if (!taskId || typeof bridge?.business !== 'function') return;
    try {
      const data = await fetchFullBacktestDetail(taskId);
      if (state.activeBacktestTaskId && state.activeBacktestTaskId !== taskId) return;
      if (data?.task) {
        state.backtestTask = data.task;
        state.backtestTasks = state.backtestTasks.map(t => t.id === taskId ? data.task : t);
      }
      if (data?.result) {
        state.backtest = data.result;
        state.btSnapshotDate = '';
        state.btCurveIndex = -1;
        state.btTradePage = 1;
        state.backtestError = '';
        if (!state.backtestOpts.styleKey && data.result.styleKey) state.backtestOpts.styleKey = data.result.styleKey;
      } else if (data?.task?.status === 'failed') {
        state.backtest = null;
        state.backtestError = data.task.error || '回测任务执行失败，请检查日期区间或数据源配置。';
      } else {
        state.backtest = null;
      }
    } catch (error) {
      if (!silent) state.backtestError = errText(error);
    } finally {
      render();
    }
  }

  async function loadBacktestTasks(selectId = '', fromPoll = false) {
    if (typeof bridge?.business !== 'function') return;
    try {
      const data = await callBusiness('research.list');
      const list = (data?.tasks || []).filter(t => t.kind === 'masterBacktest');
      const prevTask = state.backtestTask;
      state.backtestTasks = list;
      state.backtestTasksLoaded = true;
      const targetId = selectId && list.some(t => t.id === selectId)
        ? selectId
        : (state.activeBacktestTaskId && list.some(t => t.id === state.activeBacktestTaskId)
          ? state.activeBacktestTaskId
          : (list[0]?.id || ''));
      state.activeBacktestTaskId = targetId;
      const current = list.find(t => t.id === targetId) || null;
      state.backtestTask = current;
      if (current) {
        const justFinished = prevTask?.id === current.id && prevTask?.status !== current.status;
        const needResult = current.status === 'completed' && (!state.backtest || state.backtest._taskId !== current.id);
        if (current.status === 'failed') {
          state.backtest = null;
          state.backtestError = current.error || '回测任务执行失败，请检查日期区间或数据源配置。';
        } else if (needResult || justFinished) {
          const detail = await fetchFullBacktestDetail(current.id);
          if (state.activeBacktestTaskId === current.id) {
            if (detail?.task) state.backtestTask = detail.task;
            state.backtest = detail?.result || null;
            state.btSnapshotDate = '';
            state.btCurveIndex = -1;
            state.btTradePage = 1;
            state.backtestError = detail?.task?.status === 'failed' ? (detail.task.error || '回测任务执行失败') : '';
            if (!state.backtestOpts.styleKey && state.backtest?.styleKey) state.backtestOpts.styleKey = state.backtest.styleKey;
          }
        }
        if (justFinished) notifyTaskShell();
      } else {
        state.backtest = null;
        if (!fromPoll && isWorkbenchTab(state.tab) && !state.autoBootstrappedBt && !state.backtestBusy) {
          state.autoBootstrappedBt = true;
          queueMicrotask(() => runBacktest());
        }
      }
    } catch (error) {
      if (!fromPoll) state.backtestError = errText(error);
    } finally {
      render();
      scheduleBacktestPoll();
    }
  }

  async function runBacktest() {
    const master = activeMaster();
    if (state.backtestBusy) return;
    state.backtestBusy = true;
    state.backtestError = '';
    render();
    try {
      const o = state.backtestOpts;
      const rawMaxPos = String(o.maxPositions ?? '').trim();
      const hasCustomSlots = rawMaxPos !== '' && Number(rawMaxPos) >= 1;
      const reqPayload = {
        masterId: master?.id || undefined,
        masterName: master?.name || undefined,
        styleKey: o.styleKey || undefined,
        startDate: o.startDate,
        endDate: o.endDate,
        initialCapital: Number(o.initialCapital || 100000),
        maxPositions: hasCustomSlots ? Number(rawMaxPos) : undefined,
        customMaxPositions: hasCustomSlots,
        comparePositionCounts: Boolean(o.comparePositionCounts),
        stopLossPct: Number(o.stopLossPct || -5),
        useRegimeSwitch: Boolean(o.useRegimeSwitch),
        useFastWeakCut: Boolean(o.useFastWeakCut),
        useAddOnWin: Boolean(o.useAddOnWin),
        usePartialTrim: Boolean(o.usePartialTrim),
        dataSource: o.dataSource || 'auto',
      };
      state.btTradePage = 1;
      state.btCurveIndex = -1;
      if (state.btCurveMode === 'slots' && !o.comparePositionCounts) state.btCurveMode = 'return';
      if (typeof bridge?.business === 'function') {
        const created = await callBusiness('research.create', {
          kind: 'masterBacktest',
          config: reqPayload,
        }, true);
        const task = created?.task || created;
        if (!task?.id) throw new Error('研究任务服务未返回任务编号，请重试。');
        state.activeBacktestTaskId = task.id;
        state.backtestTask = task;
        state.backtestTasks = [task, ...state.backtestTasks.filter(t => t.id !== task.id)];
        state.backtestTasksLoaded = true;
        state.backtest = null;
        if (!o.styleKey && task.config?.styleKey) o.styleKey = task.config.styleKey;
        notifyTaskShell();
        scheduleBacktestPoll();
      } else {
        const directRes = await callMarket('market.masters.backtest', reqPayload);
        state.backtest = directRes ? { ...directRes, _taskId: 'direct' } : null;
        if (!o.styleKey && state.backtest?.styleKey) o.styleKey = state.backtest.styleKey;
      }
    } catch (error) {
      state.backtestError = errText(error);
    } finally {
      state.backtestBusy = false;
      render();
    }
  }

  async function controlBacktestTask(taskId, action) {
    if (!taskId) return;
    try {
      await callBusiness('research.control', { ids: [taskId], action }, true);
      notifyTaskShell();
      await loadBacktestTasks(taskId);
    } catch (error) {
      state.backtestError = errText(error);
      render();
    }
  }

  async function deleteBacktestTask(taskId) {
    if (!taskId) return;
    try {
      await callBusiness('research.delete', { ids: [taskId] }, true);
      if (state.activeBacktestTaskId === taskId) {
        state.activeBacktestTaskId = '';
        state.backtestTask = null;
        state.backtest = null;
      }
      notifyTaskShell();
      await loadBacktestTasks();
    } catch (error) {
      state.backtestError = errText(error);
      render();
    }
  }

  function runWorkbenchSync() {
    void runAdvisor();
    void runBacktest();
  }

  async function openBacktestTask(taskId) {
    let styleKey = '';
    if (taskId && typeof bridge?.business === 'function') {
      try {
        const detail = await callBusiness('research.get', { id: taskId, pageSize: 10, tradeOffset: 0 });
        styleKey = detail?.task?.config?.styleKey || detail?.result?.styleKey || '';
      } catch {}
    }
    jumpToStrategyWorkspace('backtest', activeMaster(), styleKey);
  }

  function resetImportState() {
    state.imp = {
      tab: 'images',
      images: [],
      defaultDate: '',
      defaultYear: today().slice(0, 4),
      inputHash: '',
      source: 'ocr',
      rows: [],
      warnings: [],
      busy: false,
      reviewed: false,
      fileName: '',
    };
  }

  // ── SVG Bulldozer Equity + Position + Emotion Stage Ribbon Chart ──────────

  function renderEquityChart(curve) {
    if (!Array.isArray(curve) || !curve.length) {
      return '<div class="ms-notice">暂无足够交易日数据绘制曲线。</div>';
    }
    const W = 860, H = 290, padL = 54, padR = 18, padT = 18, eqH = 148, gap = 22, posH = 66;
    const plotW = W - padL - padR;
    const rets = curve.map(c => Number(c.returnPct || 0));
    const dds = curve.map(c => Number(c.drawdownPct || 0));
    const minY = Math.min(0, ...rets);
    const maxY = Math.max(0.5, ...rets);
    const spanY = Math.max(1, maxY - minY);
    const xAt = i => padL + (curve.length === 1 ? plotW / 2 : (i / (curve.length - 1)) * plotW);
    const yEq = v => padT + (1 - (v - minY) / spanY) * eqH;
    const zeroY = yEq(0);

    const eqPoints = curve.map((c, i) => `${xAt(i).toFixed(1)},${yEq(Number(c.returnPct || 0)).toFixed(1)}`).join(' ');
    const areaPoints = `${xAt(0).toFixed(1)},${zeroY.toFixed(1)} ${eqPoints} ${xAt(curve.length - 1).toFixed(1)},${zeroY.toFixed(1)}`;

    // Emotion stage ribbon under equity chart
    const ribbonY = padT + eqH + 4;
    const cellW = plotW / curve.length;
    const ribbonRects = curve.map((c, i) => {
      const color = STAGE_COLORS[c.stage] || STAGE_COLORS.unknown;
      return `<rect x="${(padL + i * cellW).toFixed(1)}" y="${ribbonY}" width="${Math.max(1.2, cellW + 0.3).toFixed(1)}" height="8" fill="${color}" opacity="0.82"><title>${E(c.date)} · ${E(c.stageLabel)} · 收益 ${pct(c.returnPct, 2, true)} · 仓位 ${pct(c.positionRatioPct, 1)}</title></rect>`;
    }).join('');

    // Bottom lane: Position ratio bars + drawdown line
    const posTop = padT + eqH + gap;
    const barW = Math.max(1.4, Math.min(12, cellW * 0.72));
    const posBars = curve.map((c, i) => {
      const p = Math.max(0, Math.min(100, Number(c.positionRatioPct || 0)));
      const bh = (p / 100) * posH;
      const x = xAt(i) - barW / 2;
      const y = posTop + posH - bh;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" fill="#2457E5" opacity="0.28"><title>${E(c.date)} 估算仓位 ${p.toFixed(1)}% · 持股 ${c.holdingCount} 只</title></rect>`;
    }).join('');

    const minDd = Math.min(-5, ...dds);
    const yDd = v => posTop + (Math.abs(Math.min(0, v)) / Math.abs(minDd)) * posH;
    const ddPoints = curve.map((c, i) => `${xAt(i).toFixed(1)},${yDd(Number(c.drawdownPct || 0)).toFixed(1)}`).join(' ');

    const gridTicks = [maxY, maxY * 0.5 + minY * 0.5, minY];
    const gridLines = gridTicks.map(v => {
      const y = yEq(v).toFixed(1);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#E8EDF4" stroke-dasharray="3,3"/><text x="${padL - 6}" y="${Number(y) + 4}" text-anchor="end" fill="#59677A" font-size="11">${v >= 0 ? '+' : ''}${v.toFixed(1)}%</text>`;
    }).join('');

    const dateTicks = [0, Math.floor((curve.length - 1) / 2), curve.length - 1]
      .filter((v, idx, arr) => arr.indexOf(v) === idx)
      .map(idx => `<text x="${xAt(idx).toFixed(1)}" y="${H - 6}" text-anchor="${idx === 0 ? 'start' : idx === curve.length - 1 ? 'end' : 'middle'}" fill="#59677A" font-size="11">${E(curve[idx].date)}</text>`)
      .join('');

    const finalRet = Number(curve[curve.length - 1].returnPct || 0);
    const strokeColor = finalRet >= 0 ? '#C7383F' : '#15803D';
    const fillColor = finalRet >= 0 ? '#C7383F' : '#15803D';

    return `<div class="ms-chart-legend">
      <span><i class="ms-legend-dot" style="background:${strokeColor}"></i>估算净值收益率（红盈绿亏）</span>
      <span><i class="ms-legend-dot" style="background:#2457E5;opacity:.45"></i>下方柱：日终估算总仓位 (0–100%)</span>
      <span><i class="ms-legend-dot" style="background:#15803D"></i>下方折线：动态回撤 (最深 ${pct(Math.min(...dds), 1)})</span>
      <span>色带：情绪周期（<b style="color:#D97706">试错</b> / <b style="color:#C7383F">主升</b> / <b style="color:#7C3AED">震荡</b> / <b style="color:#15803D">退潮</b>）</span>
    </div>
    <div class="ms-chart-wrap">
      <svg class="ms-chart-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="大佬收益曲线、仓位与情绪周期对照图">
        ${gridLines}
        <line x1="${padL}" y1="${zeroY.toFixed(1)}" x2="${W - padR}" y2="${zeroY.toFixed(1)}" stroke="#7C8BA1" stroke-width="1"/>
        <polygon points="${areaPoints}" fill="${fillColor}" opacity="0.10"/>
        <polyline points="${eqPoints}" fill="none" stroke="${strokeColor}" stroke-width="2.2" stroke-linejoin="round"/>
        ${ribbonRects}
        <line x1="${padL}" y1="${posTop}" x2="${W - padR}" y2="${posTop}" stroke="#DCE3ED"/>
        <line x1="${padL}" y1="${posTop + posH}" x2="${W - padR}" y2="${posTop + posH}" stroke="#DCE3ED"/>
        <text x="${padL - 6}" y="${posTop + 10}" text-anchor="end" fill="#59677A" font-size="10">仓100%</text>
        <text x="${padL - 6}" y="${posTop + posH}" text-anchor="end" fill="#59677A" font-size="10">0%</text>
        ${posBars}
        <polyline points="${ddPoints}" fill="none" stroke="#15803D" stroke-width="1.5" stroke-dasharray="4,2"/>
        ${dateTicks}
      </svg>
    </div>`;
  }

  function stockBtn(symbol, name, code, day = '') {
    return `<button type="button" class="analysis-tools-stock" data-ms-stock="${E(symbol)}" data-ms-day="${E(day)}"><strong>${E(name || code)}</strong><small>${E(code || symbol)}</small></button>`;
  }

  function renderPaginationBar({ scope, page, pageSize, total, pageSizes = [15, 20, 50, 100], unit = '笔' }) {
    const safeTotal = Math.max(0, Number(total) || 0);
    const safeSize = Math.max(1, Number(pageSize) || 20);
    const totalPages = Math.max(1, Math.ceil(safeTotal / safeSize));
    const cur = Math.max(1, Math.min(totalPages, Number(page) || 1));
    const startIdx = safeTotal ? (cur - 1) * safeSize + 1 : 0;
    const endIdx = Math.min(safeTotal, cur * safeSize);

    const pages = [];
    const addPage = p => { if (!pages.includes(p) && p >= 1 && p <= totalPages) pages.push(p); };
    addPage(1);
    for (let p = cur - 2; p <= cur + 2; p++) addPage(p);
    addPage(totalPages);
    pages.sort((a, b) => a - b);

    const pageBtns = [];
    let prev = 0;
    for (const p of pages) {
      if (prev && p - prev > 1) pageBtns.push('<span class="ms-page-ellipsis">…</span>');
      pageBtns.push(`<button type="button" class="ms-page-btn ${p === cur ? 'active' : ''}" data-ms-pagenav="${E(scope)}:${p}" ${p === cur ? 'aria-current="page"' : ''}>${p}</button>`);
      prev = p;
    }

    const sizeSel = pageSizes?.length ? `<label style="display:inline-flex;align-items:center;gap:4px">每页
      <select data-ms-pagesize="${E(scope)}" aria-label="每页显示条数" style="padding:3px 6px;font-size:12px">
        ${pageSizes.map(s => `<option value="${s}" ${Number(s) === safeSize ? 'selected' : ''}>${s} ${E(unit)}</option>`).join('')}
      </select>
    </label>` : '';

    return `<div class="ms-pagination" data-ms-pagination="${E(scope)}">
      <span>共 <strong>${num(safeTotal, 0)}</strong> ${E(unit)} · 当前显示 <strong>${startIdx}–${endIdx}</strong> ${E(unit)}（第 <strong>${cur}</strong> / ${totalPages} 页）</span>
      <div class="ms-pagination-controls">
        ${sizeSel}
        <button type="button" class="ms-page-btn" data-ms-pagenav="${E(scope)}:1" ${cur <= 1 ? 'disabled' : ''}>首页</button>
        <button type="button" class="ms-page-btn" data-ms-pagenav="${E(scope)}:${cur - 1}" ${cur <= 1 ? 'disabled' : ''}>上一页</button>
        ${pageBtns.join('')}
        <button type="button" class="ms-page-btn" data-ms-pagenav="${E(scope)}:${cur + 1}" ${cur >= totalPages ? 'disabled' : ''}>下一页</button>
        <button type="button" class="ms-page-btn" data-ms-pagenav="${E(scope)}:${totalPages}" ${cur >= totalPages ? 'disabled' : ''}>末页</button>
        ${totalPages > 5 ? `<label style="display:inline-flex;align-items:center;gap:4px;margin-left:4px">跳至<input type="number" min="1" max="${totalPages}" value="${cur}" data-ms-pagejump="${E(scope)}" aria-label="跳转页码" style="width:56px;padding:3px 6px;font-size:12px">页</label>` : ''}
      </div>
    </div>`;
  }

  function barRows(items, labelKey, pctKey, detailFn, toneFn) {
    if (!items || !items.length) return '<p class="ms-sub">暂无样本数据</p>';
    return `<div class="ms-bar-list">${items.map(item => {
      const p = Math.max(0, Math.min(100, Number(item[pctKey] || 0)));
      const cls = toneFn ? toneFn(item) : '';
      return `<div class="ms-bar-item"><div class="ms-bar-head"><span>${E(item[labelKey])}</span><span>${E(detailFn(item))}</span></div><div class="ms-bar-track"><div class="ms-bar-fill ${cls}" style="width:${p.toFixed(1)}%"></div></div></div>`;
    }).join('')}</div>`;
  }

  // ── Sub-view Renderers ─────────────────────────────────────────────────────

  function renderMasterForm() {
    const d = state.masterDraft;
    const isEdit = state.editorMode === 'edit-master';
    return `<section class="ms-studio" data-ms-master-form>
      <div class="panel-head"><div><h2>${isEdit ? '编辑大佬档案' : '新建大佬交割单数据库'}</h2><p class="ms-sub">每位大佬独立保存成套交割单、行情对齐缓存与 AI 学习研报，方便横向对比不同顶尖选手。</p></div>${btn('收起', 'close-editor')}</div>
      <div class="ms-form-grid">
        <label>大佬名称（必填）<input data-ms-mfield="name" value="${E(d.name)}" maxlength="80" placeholder="例如：推土机A大佬 / 92科比 / 某游资实盘"></label>
        <label>风格标签<input data-ms-mfield="styleTag" value="${E(d.styleTag)}" maxlength="120" placeholder="例如：情绪主升龙头 / 趋势回踩低吸"></label>
        <label>参考期初本金（元，可留空自动估算）<input type="number" min="1000" step="1000" data-ms-mfield="initialCapital" value="${E(d.initialCapital)}" placeholder="例如 1000000"></label>
      </div>
      <label class="ms-field">交割单来源与时间跨度说明<input data-ms-mfield="sourceDesc" value="${E(d.sourceDesc)}" maxlength="500" placeholder="例如：2025年3月-2026年2月 连续实盘交割单截图"></label>
      <label class="ms-field">我的学习笔记 / 核心观察<textarea data-ms-mfield="notes" placeholder="随时记录你对这位大佬的观察和纪律要点……">${E(d.notes)}</textarea></label>
      <div class="ms-toolbar-group">
        ${btn(state.busy ? '保存中…' : (isEdit ? '保存档案修改' : '创建大佬数据库'), 'save-master', state.busy ? 'disabled' : '', 'analysis-tools-primary')}
        ${btn('取消', 'close-editor')}
      </div>
    </section>`;
  }

  function renderImportStudio(master) {
    const imp = state.imp;
    const visionReady = AT.aiSettings?.ready('vision');
    const selectedCount = imp.rows.filter(r => r.selected && (!r.errors || !r.errors.length)).length;
    const errorCount = imp.rows.filter(r => r.errors && r.errors.length).length;

    const previewTable = imp.rows.length ? `
      <div class="ms-toolbar">
        <div><strong>待核对交割单明细（共 ${imp.rows.length} 笔，可入库 ${selectedCount} 笔${errorCount ? `，${errorCount} 笔有待修正错误` : ''}）</strong><p class="ms-sub" style="margin:2px 0 0">所有字段均可直接修改；若截图只含股票名称，系统已尝试按本地通达信索引自动匹配 6 位代码。</p></div>
        <div class="ms-toolbar-group">
          ${btn('全选有效行', 'imp-select-valid')}
          ${btn('重新校验与匹配代码', 'imp-revalidate', imp.busy ? 'disabled' : '')}
        </div>
      </div>
      <div class="ms-table-scroll">
        <table class="ms-table ms-edit-table">
          <thead><tr><th>选</th><th>成交日期</th><th>时间</th><th>方向</th><th>6位代码</th><th>股票名称</th><th>数量(股)</th><th>均价</th><th>金额</th><th>参考总资产</th><th>备注/校验</th></tr></thead>
          <tbody>${imp.rows.map((item, idx) => {
            const r = item.record || {};
            const hasErr = item.errors && item.errors.length;
            return `<tr class="${hasErr ? 'ms-row-err' : item.duplicate ? 'ms-row-dup' : ''}" data-ms-imp-row="${idx}">
              <td><input type="checkbox" data-ms-imp-select="${idx}" ${item.selected ? 'checked' : ''} ${hasErr ? 'disabled' : ''}></td>
              <td><input type="date" data-ms-imp-cell="${idx}:tradeDate" value="${E(r.tradeDate || '')}"></td>
              <td><input type="text" data-ms-imp-cell="${idx}:tradeTime" value="${E(r.tradeTime || '')}" placeholder="09:35:00" style="min-width:68px"></td>
              <td><select data-ms-imp-cell="${idx}:side"><option value="">请选择</option><option value="buy" ${r.side === 'buy' ? 'selected' : ''}>买入</option><option value="sell" ${r.side === 'sell' ? 'selected' : ''}>卖出</option></select></td>
              <td><input type="text" data-ms-imp-cell="${idx}:code" value="${E(r.code || '')}" placeholder="6位代码" maxlength="10"></td>
              <td><input type="text" data-ms-imp-cell="${idx}:name" value="${E(r.name || '')}" placeholder="名称"></td>
              <td><input type="number" step="1" data-ms-imp-cell="${idx}:quantity" value="${E(r.quantity ?? '')}"></td>
              <td><input type="number" step="0.001" data-ms-imp-cell="${idx}:price" value="${E(r.price ?? '')}"></td>
              <td><input type="number" step="0.01" data-ms-imp-cell="${idx}:amount" value="${E(r.amount ?? '')}"></td>
              <td><input type="number" step="1" data-ms-imp-cell="${idx}:accountEquity" value="${E(r.accountEquity ?? '')}" placeholder="可选"></td>
              <td style="min-width:160px">
                <input type="text" data-ms-imp-cell="${idx}:note" value="${E(r.note || '')}" placeholder="可选备注">
                ${(item.errors || []).map(e => `<div class="ms-row-msg err">✕ ${E(e)}</div>`).join('')}
                ${(item.warnings || []).map(w => `<div class="ms-row-msg warn">! ${E(w)}</div>`).join('')}
              </td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>
      <div class="ms-toolbar">
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
          <input type="checkbox" data-ms-imp-reviewed ${imp.reviewed ? 'checked' : ''}>
          我已核对日期、证券代码、买卖方向、数量与成交均价
        </label>
        <div class="ms-toolbar-group">
          ${btn('清空预览', 'imp-clear')}
          ${btn(imp.busy ? '写入中…' : `确认写入「${master.name}」数据库（${selectedCount} 笔）`, 'imp-commit', imp.busy || !selectedCount || !imp.reviewed ? 'disabled' : '', 'analysis-tools-primary')}
        </div>
      </div>` : '';

    const manualForm = `<div class="ms-form-grid">
      <label>成交日期<input type="date" data-ms-tfield="tradeDate" value="${E(state.tradeDraft.tradeDate)}"></label>
      <label>成交时间（可选，如 09:35:00）<input type="text" data-ms-tfield="tradeTime" value="${E(state.tradeDraft.tradeTime)}" placeholder="09:35:00"></label>
      <label>买卖方向<select data-ms-tfield="side"><option value="buy" ${state.tradeDraft.side === 'buy' ? 'selected' : ''}>买入</option><option value="sell" ${state.tradeDraft.side === 'sell' ? 'selected' : ''}>卖出</option></select></label>
      <label>证券代码（或仅填名称自动匹配）<input type="text" data-ms-tfield="code" value="${E(state.tradeDraft.code)}" placeholder="如 000001 或 sh600900"></label>
      <label>证券名称<input type="text" data-ms-tfield="name" value="${E(state.tradeDraft.name)}" placeholder="如 平安银行"></label>
      <label>成交数量（股）<input type="number" min="1" step="100" data-ms-tfield="quantity" value="${E(state.tradeDraft.quantity)}" placeholder="1000"></label>
      <label>成交均价（元）<input type="number" min="0.001" step="0.01" data-ms-tfield="price" value="${E(state.tradeDraft.price)}" placeholder="12.34"></label>
      <label>成交金额（可留空自动算）<input type="number" min="0" step="0.01" data-ms-tfield="amount" value="${E(state.tradeDraft.amount)}" placeholder="留空自动=数量×均价"></label>
      <label>参考总资产（可选）<input type="number" min="0" step="100" data-ms-tfield="accountEquity" value="${E(state.tradeDraft.accountEquity)}" placeholder="截图中若可见总资产可填"></label>
    </div>
    <label class="ms-field">备注<input type="text" data-ms-tfield="note" value="${E(state.tradeDraft.note)}" placeholder="如：二板弱转强半路买入"></label>
    <div class="ms-toolbar-group">
      ${btn(state.busy ? '保存中…' : (state.tradeDraft.id ? '更新这笔记录' : '写入该大佬数据库'), 'save-trade', state.busy ? 'disabled' : '', 'analysis-tools-primary')}
    </div>`;

    return `<section class="ms-studio" data-ms-import-studio>
      <div class="panel-head">
        <div><h2>导入交割单到「${E(master.name)}」数据库</h2><p class="ms-sub">支持批量交割单截图（AI 视觉识别，一次最多 8 张，支持 Ctrl+V 粘贴）、CSV/XLSX/JSON 表格导入或手工补录。</p></div>
        ${btn('收起导入区', 'close-editor')}
      </div>
      <div class="ms-tabs">
        <button type="button" class="ms-tab ${imp.tab === 'images' ? 'active' : ''}" data-ms-imptab="images">交割单图片 AI 识别</button>
        <button type="button" class="ms-tab ${imp.tab === 'file' ? 'active' : ''}" data-ms-imptab="file">CSV / XLSX / JSON 导入</button>
        <button type="button" class="ms-tab ${imp.tab === 'manual' ? 'active' : ''}" data-ms-imptab="manual">${state.tradeDraft.id ? '编辑单笔成交' : '单笔手工录入'}</button>
      </div>
      ${imp.tab === 'images' ? `
        <div class="ms-dropzone">
          <div><strong>选择或直接 Ctrl+V 粘贴交割单截图（PNG / JPG / WebP，单批最多 8 张）</strong></div>
          <input type="file" data-ms-images accept="image/png,image/jpeg,image/webp" multiple>
          <span style="font-size:12px;color:var(--color-secondary)">支持同花顺、东财、通达信、淘股吧实盘贴等各类交割单截图；仅显示股票名称时会自动匹配本地 6 位代码。</span>
        </div>
        ${imp.images.length ? `<div class="ms-img-strip">${imp.images.map((img, i) => `<figure class="ms-img-card"><img src="data:${E(img.mimeType)};base64,${img.dataBase64}" alt="${E(img.name)}"><figcaption><span title="${E(img.name)}">${E(img.name)}</span><button type="button" class="analysis-tools-button" style="padding:2px 6px;font-size:11px" data-ms-action="imp-remove-img" data-index="${i}">删</button></figcaption></figure>`).join('')}</div>` : ''}
        <div class="ms-form-grid">
          <label>截图无完整年份时的参考年份<input type="text" data-ms-imp-opt="defaultYear" value="${E(imp.defaultYear)}" maxlength="4" placeholder="例如 2025"></label>
          <label>截图完全无日期时的默认日期（可选）<input type="date" data-ms-imp-opt="defaultDate" value="${E(imp.defaultDate)}"></label>
          <label>图片视觉 AI 状态<div style="padding:7px 0;font-size:12.5px">${visionReady ? `<span class="ms-down">● 已就绪 (${E(AT.aiSettings?.status('vision'))})</span>` : `<span class="ms-warn">● 未配置图片 AI</span> <button type="button" class="text-button" data-nav="C07">去设置 AI 模型 →</button>`}</div></label>
        </div>
        <div class="ms-toolbar-group">
          ${btn(imp.busy ? 'AI 正在识别交割单截图…' : `发送 ${imp.images.length} 张截图并识别`, 'imp-recognize', imp.busy || !imp.images.length || !visionReady ? 'disabled' : '', 'analysis-tools-primary')}
          ${imp.images.length ? btn('清空图片', 'imp-clear-imgs') : ''}
        </div>
      ` : imp.tab === 'file' ? `
        <div class="ms-dropzone">
          <strong>选择 CSV / XLSX / JSON 交割单文件</strong>
          <input type="file" data-ms-file accept=".csv,.xlsx,.json">
          <span style="font-size:12px;color:var(--color-secondary)">${imp.fileName ? `已读取：${E(imp.fileName)}` : '自动识别常见券商中文表头（成交日期、证券代码、证券名称、买卖方向、成交数量、成交均价等）。'}</span>
        </div>
      ` : manualForm}
      ${imp.warnings.length ? `<div class="ms-notice warn">${imp.warnings.map(w => `<div>• ${E(w)}</div>`).join('')}</div>` : ''}
      ${imp.tab !== 'manual' ? previewTable : ''}
    </section>`;
  }

  function renderOverviewTab(a) {
    const s = a.summary || {};
    const cov = a.coverage || {};
    const rules = a.guidance?.originalRules || [];
    const topWins = a.representativeCases?.topWins || [];
    const worstLosses = a.representativeCases?.worstLosses || [];
    return `
      <div class="ms-kpi-grid">
        <div class="ms-kpi"><span class="ms-kpi-label">估算累计收益 / 净盈亏</span><strong class="ms-kpi-value ${tone(s.totalReturnPct)}">${pct(s.totalReturnPct, 2, true)}</strong><span class="ms-kpi-hint">净盈亏 ${money(s.totalPnl)} · 基准 ${money(s.capitalBasis)}</span></div>
        <div class="ms-kpi"><span class="ms-kpi-label">最大动态回撤 / 收益回撤比</span><strong class="ms-kpi-value ms-down">${pct(s.maxDrawdownPct, 2)}</strong><span class="ms-kpi-hint">推土机平滑度比值：${s.returnToDrawdown !== null && s.returnToDrawdown !== undefined ? num(s.returnToDrawdown, 2) : '—'}</span></div>
        <div class="ms-kpi"><span class="ms-kpi-label">闭环胜率 / 完整战役</span><strong class="ms-kpi-value">${pct(s.winRatePct, 1)}</strong><span class="ms-kpi-hint">已平仓 ${s.closedEpisodes || 0} 轮 · 持仓中 ${s.openEpisodes || 0} 轮 · ${s.uniqueStocks || 0} 只股</span></div>
        <div class="ms-kpi"><span class="ms-kpi-label">盈亏比 / 盈利因子</span><strong class="ms-kpi-value ms-primary">${finite(s.payoffRatio) ? `${num(s.payoffRatio, 2)} 倍` : '—'}</strong><span class="ms-kpi-hint">盈利因子 ${finite(s.profitFactor) ? num(s.profitFactor, 2) : '—'} · 均盈 ${pct(a.exits?.avgWinPct, 2, true)} / 均亏 ${pct(a.exits?.avgLossPct, 2)}</span></div>
        <div class="ms-kpi"><span class="ms-kpi-label">持有天数（盈利单 vs 亏损单）</span><strong class="ms-kpi-value">${num(s.avgHoldDaysWin, 1)} / ${num(s.avgHoldDaysLoss, 1)} 天</strong><span class="ms-kpi-hint">截断亏损速度决定曲线陡度</span></div>
        <div class="ms-kpi"><span class="ms-kpi-label">日均总仓位 / 空仓占比</span><strong class="ms-kpi-value">${pct(s.avgPositionPct, 1)}</strong><span class="ms-kpi-hint">空仓天数占 ${pct(a.position?.emptyDayRatioPct, 1)} · 峰值 ${pct(a.position?.maxPositionPct, 1)}</span></div>
        <div class="ms-kpi"><span class="ms-kpi-label">日均 / 最大同时持股</span><strong class="ms-kpi-value">${num(s.avgHoldingCount, 2)} / ${a.position?.maxHoldingCount ?? 0} 只</strong><span class="ms-kpi-hint">单票中位仓位约 ${pct(a.position?.medianSingleWeightPct, 1)}</span></div>
        <div class="ms-kpi"><span class="ms-kpi-label">本地通达信行情对齐覆盖</span><strong class="ms-kpi-value">${cov.tradesWithDailyBar || 0} / ${s.tradeCount || 0} 笔</strong><span class="ms-kpi-hint">全市场情绪 ${cov.marketEnvDays || 0} 日 · 1分钟分时 ${cov.tradesWithIntraday || 0} 笔</span></div>
      </div>

      <section class="panel ms-panel">
        <div class="panel-head">
          <div><h2>推土机净值曲线 · 仓位节拍 · 情绪周期对齐</h2><p class="ms-sub">${E(s.capitalSourceLabel || '')} · 区间 ${E(a.dateRange?.firstDate || '')} 至 ${E(a.dateRange?.lastDate || '')}（${a.dateRange?.tradingDays || 0} 个交易日）</p></div>
        </div>
        ${renderEquityChart(a.curve)}
      </section>

      <div class="ms-two-col">
        <section class="panel ms-panel">
          <h2>自动提炼的核心规律摘要</h2>
          <p class="ms-sub">全部由大佬交割单数据库与本地通达信日线定量计算得出。</p>
          <div class="ms-rule-list">
            ${rules.length ? rules.map(r => `<div class="ms-rule-card"><strong>${E(r.dimension)}</strong>${E(r.rule)}</div>`).join('') : '<p class="ms-sub">样本量偏少，导入更多连续交割单后将自动提炼规律。</p>'}
          </div>
        </section>
        <section class="panel ms-panel">
          <h2>代表性正反战役（大肉 vs 止损）</h2>
          <p class="ms-sub">点击股票可跳转对应成交日的日 K 线直接复盘现场。</p>
          <div class="ms-table-scroll">
            <table class="ms-table">
              <thead><tr><th>类型</th><th>标的</th><th>建仓→清仓</th><th>形态</th><th class="ms-num">收益率</th><th class="ms-num">持有</th><th>退出模式</th></tr></thead>
              <tbody>
                ${[...topWins.slice(0, 3).map(e => ({ ...e, _k: '大肉' })), ...worstLosses.slice(0, 3).map(e => ({ ...e, _k: '止损' }))].map(e => `<tr>
                  <td><span class="ms-pill ${e._k === '大肉' ? 'buy' : 'sell'}">${e._k}</span></td>
                  <td>${stockBtn(e.symbol, e.name, e.code, e.entryDate)}</td>
                  <td>${E(e.entryDate)} → ${E(e.exitDate || '持仓')}</td>
                  <td>${E(e.entryPatternLabel)}</td>
                  <td class="ms-num ${tone(e.returnPct)}"><strong>${pct(e.returnPct, 2, true)}</strong></td>
                  <td class="ms-num">${num(e.holdingTradingDays, 0)}天</td>
                  <td>${E(e.exitPatternLabel)}</td>
                </tr>`).join('') || '<tr><td colspan="7" style="text-align:center;color:var(--color-disabled)">暂无已闭环战役</td></tr>'}
              </tbody>
            </table>
          </div>
        </section>
      </div>`;
  }

  function renderEnvironmentTab(a) {
    const env = a.environment || {};
    if (!env.available) {
      return `<section class="panel ms-panel"><div class="ms-notice warn">当前尚未对齐本地全市场情绪周期。请先在「设置 → 数据源」选择并扫描本地通达信目录，然后点击右上角「刷新行情对齐」。</div></section>`;
    }
    return `
      <section class="panel ms-panel">
        <h2>情绪周期四阶段：大佬在什么环境重仓、什么环境空仓？</h2>
        <p class="ms-sub">按交易日将大佬的出手频率、日均仓位与闭环胜率对齐到本地通达信情绪周期模型（低位试错 / 主升 / 高位震荡 / 主跌退潮）。</p>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead><tr><th>情绪阶段</th><th class="ms-num">区间交易日</th><th class="ms-num">建仓/出手日</th><th class="ms-num">出手频率</th><th class="ms-num">日均估算仓位</th><th class="ms-num">日均持股</th><th class="ms-num">闭环战役</th><th class="ms-num">阶段胜率</th><th class="ms-num">单笔均收益</th><th class="ms-num">阶段盈亏</th></tr></thead>
            <tbody>${(env.byStage || []).map(row => `<tr>
              <td><span class="ms-pill stage-${E(row.stage)}">${E(row.label)}</span></td>
              <td class="ms-num">${row.tradingDays}</td>
              <td class="ms-num">${row.buyDays}</td>
              <td class="ms-num">${pct(row.attackFreqPct, 1)}</td>
              <td class="ms-num"><strong>${pct(row.avgPositionPct, 1)}</strong></td>
              <td class="ms-num">${num(row.avgHoldingCount, 2)} 只</td>
              <td class="ms-num">${row.closedEpisodes}</td>
              <td class="ms-num">${pct(row.winRatePct, 1)}</td>
              <td class="ms-num ${tone(row.avgReturnPct)}">${pct(row.avgReturnPct, 2, true)}</td>
              <td class="ms-num ${tone(row.totalPnl)}">${money(row.totalPnl)}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>
      </section>

      <section class="panel ms-panel">
        <h2>出手建仓日 vs 不出手日 vs 空仓日：全市场环境指标对比</h2>
        <p class="ms-sub">建仓日 ${env.buyDayCount || 0} 天 · 不买日 ${env.idleDayCount || 0} 天 · 完全空仓日 ${env.emptyDayCount || 0} 天。两列差异越大，说明该指标越可能是大佬的真实择时开关。</p>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead><tr><th>全市场指标</th><th class="ms-num">建仓日均值</th><th class="ms-num">不买日均值</th><th class="ms-num">空仓日均值</th></tr></thead>
            <tbody>${(env.buyVsIdle || []).map(row => `<tr>
              <td><strong>${E(row.metric)}</strong></td>
              <td class="ms-num">${finite(row.buyDay) ? `${num(row.buyDay, 2)} ${E(row.unit)}` : '—'}</td>
              <td class="ms-num">${finite(row.idleDay) ? `${num(row.idleDay, 2)} ${E(row.unit)}` : '—'}</td>
              <td class="ms-num">${finite(row.emptyDay) ? `${num(row.emptyDay, 2)} ${E(row.unit)}` : '—'}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>
      </section>`;
  }

  function renderSelectionTab(a) {
    const sel = a.selection || {};
    const cg = sel.controlGroup || {};
    const buys = (a.enrichedTrades || []).filter(t => t.side === 'buy');
    return `
      <section class="panel ms-panel">
        <h2>同日全市场正负样本对照：大佬买的票在全市场排第几？</h2>
        <p class="ms-sub">对比大佬每次首笔买入标的与当日全市场所有活跃股票的横截面排名分位（100% 表示全市场第一）。</p>
        <div class="ms-kpi-grid">
          <div class="ms-kpi"><span class="ms-kpi-label">成交额全市场分位（中位数）</span><strong class="ms-kpi-value ms-primary">${finite(cg.medianAmountRankPct) ? `前 ${num(Math.max(0.1, 100 - cg.medianAmountRankPct), 1)}%` : '—'}</strong><span class="ms-kpi-hint">标的中位成交额 ${num(cg.medianStockAmountYi, 2)} 亿 vs 市场中位 ${num(cg.marketMedianAmountYi, 3)} 亿</span></div>
          <div class="ms-kpi"><span class="ms-kpi-label">5日 / 20日强势分位均值</span><strong class="ms-kpi-value">${pct(cg.avgRet5RankPct, 1)} / ${pct(cg.avgRet20RankPct, 1)}</strong><span class="ms-kpi-hint">买前5日均涨 ${pct(cg.avgRet5Before, 2, true)} · 买前20日均涨 ${pct(cg.avgRet20Before, 2, true)}</span></div>
          <div class="ms-kpi"><span class="ms-kpi-label">均线多头 / 20日突破 / 昨涨停占比</span><strong class="ms-kpi-value">${pct(cg.bullStackRatePct, 0)} / ${pct(cg.breakout20RatePct, 0)} / ${pct(cg.priorLimitUpRatePct, 0)}</strong><span class="ms-kpi-hint">买入当日平均5日量比 ${num(cg.avgVolRatio5, 2)} 倍</span></div>
          <div class="ms-kpi"><span class="ms-kpi-label">日内买入价位落点 / 相对分时VWAP</span><strong class="ms-kpi-value">${pct(cg.avgIntradayPosPct, 1)}</strong><span class="ms-kpi-hint">0%=日内最低 100%=最高/板价 · 偏离VWAP ${pct(cg.avgVwapDiffPct, 2, true)}</span></div>
        </div>
      </section>

      <div class="ms-two-col">
        <section class="panel ms-panel">
          <h2>买入结构形态拆解</h2>
          <div class="ms-table-scroll">
            <table class="ms-table">
              <thead><tr><th>买点结构</th><th class="ms-num">首仓数</th><th class="ms-num">占比</th><th class="ms-num">胜率</th><th class="ms-num">均收益</th><th class="ms-num">均持有</th></tr></thead>
              <tbody>${(sel.patterns || []).map(p => `<tr>
                <td><strong>${E(p.label)}</strong></td>
                <td class="ms-num">${p.openBuyCount}</td>
                <td class="ms-num">${pct(p.sharePct, 1)}</td>
                <td class="ms-num">${pct(p.winRatePct, 1)}</td>
                <td class="ms-num ${tone(p.avgReturnPct)}">${pct(p.avgReturnPct, 2, true)}</td>
                <td class="ms-num">${num(p.avgHoldDays, 1)}天</td>
              </tr>`).join('') || '<tr><td colspan="6">暂无样本</td></tr>'}</tbody>
            </table>
          </div>
        </section>

        <section class="panel ms-panel">
          <h2>板块偏好、日内价位带与盘中时段</h2>
          <h3>买价相对昨收涨跌幅带</h3>
          ${barRows(sel.priceZones, 'label', 'sharePct', r => `${r.count} 笔 (${pct(r.sharePct, 1)})`, () => 'up')}
          <h3 style="margin-top:14px">上市板块偏好</h3>
          ${barRows(sel.boards, 'label', 'sharePct', r => `${r.count} 笔 (${pct(r.sharePct, 1)})`)}
          <h3 style="margin-top:14px">成交时段分布</h3>
          ${barRows(sel.sessions, 'label', 'sharePct', r => `${r.count} 笔 (${pct(r.sharePct, 1)})`, () => 'warn')}
        </section>
      </div>

      <section class="panel ms-panel">
        <h2>逐笔买入现场还原（共 ${buys.length} 笔 · 点击股票跳转当日 K 线）</h2>
        <p class="ms-sub">完整记录每笔买入当时的市场情绪阶段、个股连板高度、全市场成交额分位、买价日内落点及次日开盘/收盘反馈。</p>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead><tr><th>日期 / 时间</th><th>股票</th><th>动作</th><th>买点形态</th><th>情绪阶段</th><th class="ms-num">成交价(较昨收)</th><th class="ms-num">日内落点</th><th class="ms-num">成交额分位</th><th class="ms-num">昨高度</th><th class="ms-num">次日开/收</th></tr></thead>
            <tbody>${(() => {
              const pageSize = 20;
              const cur = Math.max(1, Math.min(Math.max(1, Math.ceil(buys.length / pageSize)), state.selBuyPage || 1));
              const slice = buys.slice((cur - 1) * pageSize, cur * pageSize);
              return slice.map(t => `<tr>
              <td>${E(t.tradeDate)}<br><small style="color:var(--color-disabled)">${E(t.tradeTime || '—')}</small></td>
              <td>${stockBtn(t.symbol, t.name, t.code, t.tradeDate)}</td>
              <td><span class="ms-pill buy">${E(({ open: '首仓', add_win: '浮盈加', add_loss: '浮亏补', t_buy: '做T买' }[t.legRole]) || '买入')}</span></td>
              <td>${E(t.entryPatternLabel || '—')}</td>
              <td>${t.marketEnv ? `<span class="ms-pill stage-${E(t.marketEnv.stage)}">${E(t.marketEnv.stageLabel)}</span>` : '—'}</td>
              <td class="ms-num">${num(t.price, 2)} <span class="${tone(t.buyVsPrevClosePct)}">(${pct(t.buyVsPrevClosePct, 1, true)})</span></td>
              <td class="ms-num">${pct(t.intradayPricePos, 0)}</td>
              <td class="ms-num">${t.crossSection?.amountRankPct != null ? `前 ${num(Math.max(0.1, 100 - t.crossSection.amountRankPct), 1)}%` : '—'}</td>
              <td class="ms-num">${t.bar ? `${t.bar.priorBoardHeight}→${t.bar.boardHeight}板` : '—'}</td>
              <td class="ms-num">${t.bar ? `<span class="${tone(t.bar.next1OpenPct)}">${pct(t.bar.next1OpenPct, 1, true)}</span> / <span class="${tone(t.bar.next1ClosePct)}">${pct(t.bar.next1ClosePct, 1, true)}</span>` : '—'}</td>
            </tr>`).join('') || '<tr><td colspan="10">暂无买入记录</td></tr>';
            })()}</tbody>
          </table>
        </div>
        ${buys.length > 20 ? renderPaginationBar({ scope: 'sel', page: state.selBuyPage || 1, pageSize: 20, total: buys.length, pageSizes: [] }) : ''}
      </section>`;
  }

  function renderPositionExitTab(a) {
    const pos = a.position || {};
    const ex = a.exits || {};
    const eps = a.episodes || [];
    const epPageSize = 20;
    const epCurPage = Math.max(1, Math.min(Math.max(1, Math.ceil(eps.length / epPageSize)), state.posEpPage || 1));
    const pagedEps = eps.slice((epCurPage - 1) * epPageSize, epCurPage * epPageSize);
    return `
      <div class="ms-two-col">
        <section class="panel ms-panel">
          <h2>分仓、加减仓与连亏控仓纪律</h2>
          <p class="ms-sub">看清大佬怎么分配子弹：是重仓单吊还是分仓控制，是顺势加仓还是死扛补仓。</p>
          <div class="ms-kpi-grid" style="grid-template-columns:repeat(2,minmax(0,1fr));margin-bottom:14px">
            <div class="ms-kpi"><span class="ms-kpi-label">分批建仓 / 分批止盈比例</span><strong class="ms-kpi-value">${pct(pos.stagedBuyRatePct, 1)} / ${pct(pos.stagedSellRatePct, 1)}</strong><span class="ms-kpi-hint">同日做 T 股票日次：${pos.tTradeDaysCount || 0} 次</span></div>
            <div class="ms-kpi"><span class="ms-kpi-label">浮盈加仓 vs 浮亏补仓</span><strong class="ms-kpi-value">${pos.addWinCount || 0} 次 / ${pos.addLossCount || 0} 次</strong><span class="ms-kpi-hint">加盈胜率 ${pct(pos.addWinEpisodeWinRatePct, 0)} · 补亏胜率 ${pct(pos.addLossEpisodeWinRatePct, 0)}</span></div>
            <div class="ms-kpi"><span class="ms-kpi-label">单票中位 / P90 重仓占比</span><strong class="ms-kpi-value">${pct(pos.medianSingleWeightPct, 1)} / ${pct(pos.p90SingleWeightPct, 1)}</strong><span class="ms-kpi-hint">按资金基准折算</span></div>
            <div class="ms-kpi"><span class="ms-kpi-label">亏损后下一笔仓位变化</span><strong class="ms-kpi-value ${pos.lossContractionPct != null && pos.lossContractionPct < 0 ? 'ms-down' : ''}">${pct(pos.lossContractionPct, 1, true)}</strong><span class="ms-kpi-hint">亏后均额 ${money(pos.avgSizeAfterLoss)} vs 盈后 ${money(pos.avgSizeAfterWin)}</span></div>
          </div>
          <h3>每日同时持股只数分布</h3>
          ${barRows(pos.holdingDistribution, 'label', 'sharePct', r => `${r.days} 天 (${pct(r.sharePct, 1)})`)}
        </section>

        <section class="panel ms-panel">
          <h2>止盈止损与退出纪律（推土机曲线密码）</h2>
          <p class="ms-sub">量化亏损截断速度、盘中最大浮盈(MFE)回撤吐出幅度，以及卖出后 3 日走势验证卖点精准度。</p>
          <div class="ms-kpi-grid" style="grid-template-columns:repeat(2,minmax(0,1fr));margin-bottom:14px">
            <div class="ms-kpi"><span class="ms-kpi-label">次日果断止损 / -5%内止损占比</span><strong class="ms-kpi-value ms-primary">${pct(ex.cutWithin1dPct, 1)} / ${pct(ex.cutWithin5PctLossRate, 1)}</strong><span class="ms-kpi-hint">最大单笔亏损 ${pct(ex.worstLossPct, 2)} · 亏损中位 ${pct(ex.medianLossPct, 2)}</span></div>
            <div class="ms-kpi"><span class="ms-kpi-label">盈利单 MFE / 利润回撤吐出</span><strong class="ms-kpi-value ms-up">${pct(ex.avgMfeWinPct, 2, true)} / ${pct(ex.avgProfitGivebackPct, 2)}</strong><span class="ms-kpi-hint">最大单笔盈利 ${pct(ex.bestWinPct, 2, true)} · 亏损单均MAE ${pct(ex.avgMaeLossPct, 2)}</span></div>
            <div class="ms-kpi"><span class="ms-kpi-label">卖出后 3日 / 5日个股均涨跌</span><strong class="ms-kpi-value">${pct(ex.avgPostExit3dPct, 2, true)} / ${pct(ex.avgPostExit5dPct, 2, true)}</strong><span class="ms-kpi-hint">卖在高点（卖后3日下跌）占比 ${pct(ex.sellNearPeakRatePct, 1)}</span></div>
            <div class="ms-kpi"><span class="ms-kpi-label">平均持仓（全 / 盈 / 亏）</span><strong class="ms-kpi-value">${num(ex.avgHoldDaysAll, 1)} / ${num(ex.avgHoldDaysWin, 1)} / ${num(ex.avgHoldDaysLoss, 1)} 天</strong><span class="ms-kpi-hint">盈 ${ex.winCount || 0} 笔 · 亏 ${ex.lossCount || 0} 笔</span></div>
          </div>
          <h3>退出模式分布</h3>
          <div class="ms-table-scroll">
            <table class="ms-table">
              <thead><tr><th>退出模式</th><th class="ms-num">笔数</th><th class="ms-num">占比</th><th class="ms-num">均收益</th><th class="ms-num">均持有</th><th class="ms-num">卖后3日</th></tr></thead>
              <tbody>${(ex.patterns || []).map(p => `<tr>
                <td>${E(p.label)}</td><td class="ms-num">${p.count}</td><td class="ms-num">${pct(p.sharePct, 1)}</td>
                <td class="ms-num ${tone(p.avgReturnPct)}">${pct(p.avgReturnPct, 2, true)}</td>
                <td class="ms-num">${num(p.avgHoldDays, 1)}天</td>
                <td class="ms-num ${tone(p.avgPost3dPct)}">${pct(p.avgPost3dPct, 2, true)}</td>
              </tr>`).join('') || '<tr><td colspan="6">暂无已平仓战役</td></tr>'}</tbody>
            </table>
          </div>
        </section>
      </div>

      <section class="panel ms-panel">
        <h2>完整交易战役闭环（共 ${eps.length} 轮 · 建仓 → 加减仓 → 清仓）</h2>
        <p class="ms-sub">将每只股票从空仓建仓到清仓归零归为一轮完整战役；点击「AI剖析」可针对单笔经典战役生成深度复盘。</p>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead><tr><th>标的</th><th>周期</th><th class="ms-num">持有</th><th>买点 / 环境</th><th class="ms-num">买/卖次(加盈/补亏)</th><th class="ms-num">峰值投入</th><th class="ms-num">实现盈亏</th><th class="ms-num">收益率</th><th class="ms-num">MFE / MAE</th><th class="ms-num">卖后3日</th><th>退出模式</th><th>操作</th></tr></thead>
            <tbody>${pagedEps.map(e => `<tr>
              <td>${stockBtn(e.symbol, e.name, e.code, e.entryDate)}</td>
              <td>${E(e.entryDate)}<br><small style="color:var(--color-disabled)">→ ${E(e.exitDate || '持仓中')}</small></td>
              <td class="ms-num">${e.holdingTradingDays != null ? `${e.holdingTradingDays}天` : '—'}</td>
              <td>${E(e.entryPatternLabel)}<br><span class="ms-pill stage-${E(e.entryStage)}">${E(e.entryStageLabel)}</span></td>
              <td class="ms-num">${e.buyCount}/${e.sellCount} <small style="color:var(--color-disabled)">(${e.addWinCount}/${e.addLossCount})</small></td>
              <td class="ms-num">${money(e.peakCostAmount)}</td>
              <td class="ms-num ${tone(e.realizedPnl)}">${money(e.realizedPnl)}</td>
              <td class="ms-num ${tone(e.returnPct)}"><strong>${pct(e.returnPct, 2, true)}</strong></td>
              <td class="ms-num"><span class="ms-up">${pct(e.mfePct, 1, true)}</span> / <span class="ms-down">${pct(e.maePct, 1)}</span></td>
              <td class="ms-num ${tone(e.postExit3dPct)}">${pct(e.postExit3dPct, 1, true)}</td>
              <td>${E(e.exitPatternLabel)}</td>
              <td>${btn('AI剖析', 'ai-study-episode', `data-episode-id="${E(e.id)}" ${state.aiBusy ? 'disabled' : ''}`)}</td>
            </tr>`).join('') || '<tr><td colspan="12">暂无战役记录</td></tr>'}</tbody>
          </table>
        </div>
        ${eps.length > epPageSize ? renderPaginationBar({ scope: 'ep', page: epCurPage, pageSize: epPageSize, total: eps.length, pageSizes: [], unit: '轮' }) : ''}
      </section>`;
  }

  function renderCoachTab(a) {
    const g = a.guidance || {};
    const dep = g.realtimeDependencyPct;
    const depColor = dep == null ? '#6B7280' : dep >= 60 ? '#C7383F' : dep >= 35 ? '#D97706' : '#15803D';
    const textAIReady = AT.aiSettings?.ready('text');
    const activeRep = state.reports.find(r => r.id === state.activeReportId) || state.reports[0] || null;
    const comp = a.selfComparison;

    return `
      <section class="panel ms-panel">
        <div class="panel-head">
          <div><h2>实时盯盘依赖度诊断（诚实评估可复制性）</h2><p class="ms-sub">${E(g.realtimeDependencyLevel || '')}</p></div>
          <strong style="font-size:22px;color:${depColor};font-variant-numeric:tabular-nums">${dep != null ? `${dep}%` : '—'}</strong>
        </div>
        <div class="ms-meter-bar" style="margin-bottom:14px"><div class="ms-meter-fill" style="width:${Math.min(100, dep || 0)}%;background:${depColor}"></div></div>
        <div class="ms-kpi-grid">
          <div class="ms-kpi"><span class="ms-kpi-label">早盘抢筹 (≤09:45) 买点占比</span><strong class="ms-kpi-value">${pct(g.dependencyBreakdown?.openRushBuyPct, 1)}</strong><span class="ms-kpi-hint">越低越适合非盯盘复刻</span></div>
          <div class="ms-kpi"><span class="ms-kpi-label">近涨停打板追强占比</span><strong class="ms-kpi-value">${pct(g.dependencyBreakdown?.nearLimitChasePct, 1)}</strong><span class="ms-kpi-hint">需盘口确认或通道排板</span></div>
          <div class="ms-kpi"><span class="ms-kpi-label">可非盯盘友好子集表现</span><strong class="ms-kpi-value ${tone(g.nonRealtimeSubset?.avgReturnPct)}">${pct(g.nonRealtimeSubset?.avgReturnPct, 2, true)}</strong><span class="ms-kpi-hint">${g.nonRealtimeSubset?.count || 0} 笔 · 胜率 ${pct(g.nonRealtimeSubset?.winRatePct, 1)} · 均拿 ${num(g.nonRealtimeSubset?.avgHoldDays, 1)} 天</span></div>
          <div class="ms-kpi"><span class="ms-kpi-label">强盘中博弈子集表现</span><strong class="ms-kpi-value ${tone(g.realtimeHeavySubset?.avgReturnPct)}">${pct(g.realtimeHeavySubset?.avgReturnPct, 2, true)}</strong><span class="ms-kpi-hint">${g.realtimeHeavySubset?.count || 0} 笔 · 胜率 ${pct(g.realtimeHeavySubset?.winRatePct, 1)} · 均拿 ${num(g.realtimeHeavySubset?.avgHoldDays, 1)} 天</span></div>
        </div>
      </section>

      <div class="ms-two-col">
        <section class="panel ms-panel">
          <h2>轨道一：大佬原版全维度规则提炼</h2>
          <p class="ms-sub">完整保留大佬在盘中节奏、情绪择时、选股、分仓与止盈止损上的原生特征。</p>
          <div class="ms-rule-list">
            ${(g.originalRules || []).map(r => `<div class="ms-rule-card"><strong>${E(r.dimension)}</strong>${E(r.rule)}</div>`).join('') || '<p class="ms-sub">暂无足够样本</p>'}
          </div>
        </section>
        <section class="panel ms-panel">
          <h2>轨道二：不能实时盯盘专用改造方案</h2>
          <p class="ms-sub">把大佬核心优势翻译成「盘后 20 分钟复盘 + 次日券商条件单 / 集合竞价执行」的可落地纪律。</p>
          <div class="ms-rule-list">
            ${(g.nonRealtimeChecklist || []).map(c => `<div class="ms-rule-card ms-rule-nonrt"><strong>${E(c.phase)}</strong>${E(c.action)}</div>`).join('')}
          </div>
        </section>
      </div>

      <section class="panel ms-panel">
        <div class="panel-head">
          <div><h2>我的实盘账户 vs 大佬纪律对比</h2><p class="ms-sub">匿名汇总你本机「账户与复盘」里的成交闭环统计，和大佬逐项对比，一眼揪出差距在哪。</p></div>
          ${btn(state.compareBusy ? '正在对比…' : '拉取我的实盘账户做对比', 'compare-self', state.compareBusy ? 'disabled' : '', 'analysis-tools-primary')}
        </div>
        ${comp ? `
          <p class="ms-sub">已对比：<strong>${E(comp.selfAccountName)}</strong>（${comp.selfTradeCount} 笔成交，${comp.selfClosedCount} 轮闭环）</p>
          <div class="ms-table-scroll">
            <table class="ms-table">
              <thead><tr><th>纪律维度</th><th class="ms-num">大佬数据库</th><th class="ms-num">我的实盘</th><th>纠偏建议</th></tr></thead>
              <tbody>${(comp.rows || []).map(r => `<tr>
                <td><strong>${E(r.metric)}</strong></td>
                <td class="ms-num">${finite(r.master) ? `${num(r.master, 2)}${E(r.unit)}` : '—'}</td>
                <td class="ms-num">${finite(r.self) ? `${num(r.self, 2)}${E(r.unit)}` : '—'}</td>
                <td>${E(r.advice)}</td>
              </tr>`).join('')}</tbody>
            </table>
          </div>
        ` : '<div class="ms-notice">点击右上角按钮，立即与你的本地实盘账本进行无隐私泄露的纪律对比。</div>'}
      </section>

      <section class="panel ms-panel">
        <div class="panel-head">
          <div><h2>AI 交易导师深度解构与持久化研报库</h2><p class="ms-sub">把定量统计、正反战役与非盯盘约束喂给文本 AI，生成的每一份学习手册都会自动存入该大佬的 SQLite 数据库随时回看。</p></div>
          ${!textAIReady ? '<button type="button" class="text-button" data-nav="C07">先配置文本 AI 模型 →</button>' : ''}
        </div>
        <div class="ms-toolbar-group" style="margin-bottom:14px">
          ${btn(state.aiBusy ? 'AI 正在深度解构…' : '生成全维度学习手册', 'ai-study', `data-kind="playbook" ${state.aiBusy || !textAIReady ? 'disabled' : ''}`, 'analysis-tools-primary')}
          ${btn('定制非盯盘改造方案', 'ai-study', `data-kind="non_realtime_plan" ${state.aiBusy || !textAIReady ? 'disabled' : ''}`)}
          ${btn('深度剖析正反经典战役', 'ai-study', `data-kind="trade_deep_dive" ${state.aiBusy || !textAIReady ? 'disabled' : ''}`)}
          ${btn('生成实盘对比纠偏计划', 'ai-study', `data-kind="self_compare" ${state.aiBusy || !textAIReady || !comp ? 'disabled' : ''}`)}
        </div>
        ${state.reports.length ? `
          <div class="ms-two-col" style="grid-template-columns:280px minmax(0,1fr)">
            <div class="ms-report-list">
              ${state.reports.map(r => `<div class="ms-report-item ${activeRep?.id === r.id ? 'active' : ''}" data-ms-report="${E(r.id)}">
                <div><strong>${E(r.title)}</strong><small>${E(r.createdAt?.slice(0, 16).replace('T', ' ') || '')} · ${E(r.model || 'AI')}</small></div>
                <button type="button" class="analysis-tools-button" style="padding:3px 7px;font-size:11px" data-ms-action="delete-report" data-report-id="${E(r.id)}">删</button>
              </div>`).join('')}
            </div>
            ${activeRep ? `<article class="ms-report-box">${formatMarkdownSafe(activeRep.content)}</article>` : ''}
          </div>
        ` : '<div class="ms-notice">该大佬还没有保存 AI 解构研报，点击上方按钮即可生成并永久入库；你也可以随时点击右上角全局「AI」按钮就当前页面细节追问。</div>'}
      </section>`;
  }

  function renderDatabaseTab(master) {
    const td = state.tradesData || { items: [], batches: [], total: 0 };
    const f = state.tradesFilter;
    return `
      <section class="panel ms-panel">
        <div class="panel-head">
          <div><h2>「${E(master.name)}」原始交割单数据库（共 ${num(td.total || 0, 0)} 笔）</h2><p class="ms-sub">独立存储在本机 <code>masters.sqlite3</code> 中，支持分页浏览、多维检索、逐笔编辑、删除与按导入批次整批撤销。</p></div>
          ${btn('导入/录入交割单', 'open-import', '', 'analysis-tools-primary')}
        </div>
        <div class="ms-filter-row">
          <input type="text" data-ms-tfilter="query" value="${E(f.query)}" placeholder="搜索代码 / 名称 / 备注" style="min-width:200px">
          <select data-ms-tfilter="side"><option value="all" ${f.side === 'all' ? 'selected' : ''}>全部方向</option><option value="buy" ${f.side === 'buy' ? 'selected' : ''}>仅买入</option><option value="sell" ${f.side === 'sell' ? 'selected' : ''}>仅卖出</option></select>
          <input type="date" data-ms-tfilter="dateFrom" value="${E(f.dateFrom)}" aria-label="开始日期">
          <input type="date" data-ms-tfilter="dateTo" value="${E(f.dateTo)}" aria-label="结束日期">
          ${btn('筛选', 'apply-tfilter')}
        </div>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead><tr><th>成交日期</th><th>时间</th><th>方向</th><th>股票</th><th class="ms-num">数量(股)</th><th class="ms-num">成交均价</th><th class="ms-num">成交金额</th><th class="ms-num">参考总资产</th><th>备注</th><th>操作</th></tr></thead>
            <tbody>${(td.items || []).map(t => `<tr>
              <td>${E(t.tradeDate)}</td>
              <td>${E(t.tradeTime || '—')}</td>
              <td><span class="ms-pill ${t.side === 'buy' ? 'buy' : 'sell'}">${t.side === 'buy' ? '买入' : '卖出'}</span></td>
              <td>${stockBtn(t.symbol, t.name, t.code, t.tradeDate)}</td>
              <td class="ms-num">${num(t.quantity, 0)}</td>
              <td class="ms-num">${num(t.price, 3)}</td>
              <td class="ms-num">${money(t.amount)}</td>
              <td class="ms-num">${t.accountEquity ? money(t.accountEquity) : '—'}</td>
              <td>${E(t.note || '—')}</td>
              <td style="white-space:nowrap">${btn('编辑', 'edit-trade', `data-trade-id="${E(t.id)}"`)} ${btn('删除', 'delete-trade', `data-trade-id="${E(t.id)}"`)}</td>
            </tr>`).join('') || '<tr><td colspan="10" style="text-align:center;color:var(--color-disabled)">暂无符合条件的交割单记录</td></tr>'}</tbody>
          </table>
        </div>
        ${renderPaginationBar({ scope: 'db', page: f.page || 1, pageSize: f.pageSize || 50, total: td.total || 0, pageSizes: [20, 50, 100, 200] })}
      </section>

      ${(td.batches || []).length ? `<section class="panel ms-panel">
        <h3>历史导入批次管理</h3>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead><tr><th>导入时间</th><th>来源方式</th><th class="ms-num">截图数</th><th class="ms-num">入库笔数</th><th>操作</th></tr></thead>
            <tbody>${td.batches.map(b => `<tr>
              <td>${E(b.createdAt?.slice(0, 19).replace('T', ' '))}</td>
              <td>${E({ ocr: '图片 AI 识别', csv: 'CSV 文件', xlsx: 'Excel 文件', json: 'JSON 文件', 'db-import': '数据库导入' }[b.source] || b.source)}</td>
              <td class="ms-num">${b.imageCount || 0}</td>
              <td class="ms-num">${b.rowCount || 0}</td>
              <td>${btn('撤销整批', 'delete-batch', `data-batch-id="${E(b.id)}"`, 'analysis-tools-danger')}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>
      </section>` : ''}`;
  }

  function normalizeBtCurve(bt) {
    const raw = Array.isArray(bt?.curve) ? bt.curve : [];
    const initCap = finite(bt?.config?.initialCapital)
      ? Number(bt.config.initialCapital)
      : (finite(bt?.summary?.initialCapital) ? Number(bt.summary.initialCapital) : 100000);
    const stageMap = { advance: '主升', trial: '低位试错', chop: '高位震荡', decline: '主跌退潮' };
    return raw.filter(c => c && c.date).map(c => {
      const mRet = finite(c.masterRetPct) ? Number(c.masterRetPct) : (finite(c.returnPct) ? Number(c.returnPct) : 0);
      const aRet = finite(c.ablationRetPct) ? Number(c.ablationRetPct) : mRet;
      const bRet = finite(c.benchRetPct) ? Number(c.benchRetPct) : 0;
      const mDd = finite(c.masterDdPct) ? Number(c.masterDdPct) : (finite(c.drawdownPct) ? Number(c.drawdownPct) : 0);
      const eq = finite(c.equity) ? Number(c.equity) : initCap * (1 + mRet / 100);
      const ablEq = finite(c.ablationEquity) ? Number(c.ablationEquity) : initCap * (1 + aRet / 100);
      const benchEq = finite(c.benchmarkEquity) ? Number(c.benchmarkEquity) : initCap * (1 + bRet / 100);
      const posPct = finite(c.positionPct) ? Number(c.positionPct) : 0;
      const holdCnt = finite(c.holdingCount) ? Number(c.holdingCount) : 0;
      return {
        ...c,
        masterRetPct: mRet,
        ablationRetPct: aRet,
        benchRetPct: bRet,
        masterDdPct: mDd,
        equity: eq,
        ablationEquity: ablEq,
        benchmarkEquity: benchEq,
        positionPct: posPct,
        holdingCount: holdCnt,
        stageLabel: c.stageLabel || stageMap[c.stage] || '震荡',
      };
    });
  }

  function renderMultiSlotChart(items) {
    const valid = (items || []).filter(it => Array.isArray(it.curve) && it.curve.length > 1);
    if (!valid.length) return '<div class="ms-notice">暂无不同持仓数量的曲线数据。</div>';
    const W = 860, H = 290, padL = 62, padR = 20, padT = 18, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const allVals = [0];
    for (const it of valid) {
      for (const pt of it.curve) {
        if (finite(pt.returnPct)) allVals.push(Number(pt.returnPct));
      }
    }
    const minY = Math.min(...allVals);
    const maxY = Math.max(1.0, ...allVals);
    const spanY = Math.max(1.0, maxY - minY);
    const yAt = v => padT + (1 - (v - minY) / spanY) * plotH;
    const zeroY = yAt(0);
    const ticks = [maxY, (maxY + minY) / 2, minY];
    const gridLines = ticks.map(v => {
      const y = yAt(v).toFixed(1);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#E8EDF4" stroke-dasharray="3,3"/><text x="${padL - 6}" y="${Number(y) + 4}" text-anchor="end" fill="#59677A" font-size="11">${v >= 0 ? '+' : ''}${v.toFixed(1)}%</text>`;
    }).join('');
    const refCurve = valid[0].curve;
    const xRef = i => padL + (refCurve.length === 1 ? plotW / 2 : (i / (refCurve.length - 1)) * plotW);
    const dateTicks = [0, Math.floor((refCurve.length - 1) / 2), refCurve.length - 1]
      .filter((v, idx, arr) => arr.indexOf(v) === idx)
      .map(idx => `<text x="${xRef(idx).toFixed(1)}" y="${H - 7}" text-anchor="${idx === 0 ? 'start' : idx === refCurve.length - 1 ? 'end' : 'middle'}" fill="#59677A" font-size="11">${E(refCurve[idx].date)}</text>`)
      .join('');

    const lines = valid.map((it, idx) => {
      const color = SLOT_LINE_COLORS[idx % SLOT_LINE_COLORS.length];
      const len = it.curve.length;
      const pts = it.curve.map((pt, i) => {
        const x = padL + (len === 1 ? plotW / 2 : (i / (len - 1)) * plotW);
        const y = yAt(finite(pt.returnPct) ? Number(pt.returnPct) : 0);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      const width = it.isCurrent ? 3.0 : 1.7;
      const opacity = it.isCurrent ? 1.0 : 0.78;
      return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="${width}" opacity="${opacity}" stroke-linejoin="round"><title>最多 ${it.maxPositions} 只：收益 ${pct(it.totalReturnPct, 2, true)} · 回撤 ${pct(it.maxDrawdownPct, 2)}</title></polyline>`;
    }).join('');

    const legend = valid.map((it, idx) => {
      const color = SLOT_LINE_COLORS[idx % SLOT_LINE_COLORS.length];
      return `<span><i class="ms-legend-dot" style="background:${color}"></i><strong style="${it.isCurrent ? 'color:var(--color-primary)' : ''}">${it.maxPositions}只${it.isStyleDefault ? '(原版)' : ''}${it.isCurrent ? '★' : ''}</strong> ${pct(it.totalReturnPct, 1, true)}</span>`;
    }).join('');

    return `<div class="ms-chart-legend">${legend}</div>
      <div class="ms-chart-wrap">
        <svg class="ms-chart-svg ms-bt-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="不同持仓数量下的回测收益曲线对比图">
          ${gridLines}
          <line x1="${padL}" y1="${zeroY.toFixed(1)}" x2="${W - padR}" y2="${zeroY.toFixed(1)}" stroke="#7C8BA1" stroke-width="1"/>
          ${lines}
          ${dateTicks}
        </svg>
      </div>`;
  }

  function renderBacktestChart(bt, mode = 'return') {
    if (mode === 'slots' && bt?.positionCountComparison?.length > 1) {
      return renderMultiSlotChart(bt.positionCountComparison);
    }
    const curve = normalizeBtCurve(bt);
    if (!curve.length) {
      return '<div class="ms-notice">暂无足够交易日数据绘制回测收益曲线。</div>';
    }
    const isEquity = mode === 'equity';
    const initCap = finite(bt?.config?.initialCapital)
      ? Number(bt.config.initialCapital)
      : (finite(bt?.summary?.initialCapital) ? Number(bt.summary.initialCapital) : curve[0].equity || 100000);
    const W = 860, H = 310, padL = isEquity ? 68 : 58, padR = 18, padT = 18, eqH = 166, gap = 22, posH = 64;
    const plotW = W - padL - padR;

    const mKey = isEquity ? 'equity' : 'masterRetPct';
    const aKey = isEquity ? 'ablationEquity' : 'ablationRetPct';
    const bKey = isEquity ? 'benchmarkEquity' : 'benchRetPct';
    const baseline = isEquity ? initCap : 0;

    const allVals = [baseline];
    for (const c of curve) {
      allVals.push(c[mKey], c[aKey], c[bKey]);
    }
    const minY = Math.min(...allVals);
    const maxY = Math.max(isEquity ? baseline * 1.01 : 1.0, ...allVals);
    const spanY = Math.max(isEquity ? Math.max(100, baseline * 0.02) : 1.0, maxY - minY);
    const xAt = i => padL + (curve.length === 1 ? plotW / 2 : (i / (curve.length - 1)) * plotW);
    const yEq = v => padT + (1 - (v - minY) / spanY) * eqH;
    const baseLineY = yEq(baseline);

    const pts = key => curve.map((c, i) => `${xAt(i).toFixed(1)},${yEq(c[key]).toFixed(1)}`).join(' ');
    const masterPts = pts(mKey);
    const ablPts = pts(aKey);
    const benchPts = pts(bKey);
    const areaPts = `${xAt(0).toFixed(1)},${baseLineY.toFixed(1)} ${masterPts} ${xAt(curve.length - 1).toFixed(1)},${baseLineY.toFixed(1)}`;
    const endUp = curve[curve.length - 1].masterRetPct >= 0;
    const mainColor = endUp ? '#C7383F' : '#15803D';

    const selIdx = state.btCurveIndex >= 0 && state.btCurveIndex < curve.length ? state.btCurveIndex : curve.length - 1;
    const selPt = curve[selIdx];
    const selX = xAt(selIdx);
    const selY = yEq(selPt[mKey]);

    const ribbonY = padT + eqH + 4;
    const cellW = plotW / curve.length;
    const ribbonRects = curve.map((c, i) => {
      const color = STAGE_COLORS[c.stage] || STAGE_COLORS.unknown;
      return `<rect x="${(padL + i * cellW).toFixed(1)}" y="${ribbonY}" width="${Math.max(1.2, cellW + 0.3).toFixed(1)}" height="7" fill="${color}" opacity="0.82"/>`;
    }).join('');

    const posTop = padT + eqH + gap;
    const barW = Math.max(1.4, Math.min(10, cellW * 0.72));
    const posBars = curve.map((c, i) => {
      const p = Math.max(0, Math.min(100, c.positionPct));
      const bh = (p / 100) * posH;
      return `<rect x="${(xAt(i) - barW / 2).toFixed(1)}" y="${(posTop + posH - bh).toFixed(1)}" width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" fill="#2457E5" opacity="0.28"/>`;
    }).join('');

    const dds = curve.map(c => c.masterDdPct);
    const minDd = Math.min(-5, ...dds);
    const yDd = v => posTop + (Math.abs(Math.min(0, v)) / Math.abs(minDd)) * posH;
    const ddPts = curve.map((c, i) => `${xAt(i).toFixed(1)},${yDd(c.masterDdPct).toFixed(1)}`).join(' ');

    const hitRects = curve.map((c, i) => {
      const w = Math.max(2, plotW / curve.length);
      return `<rect x="${(xAt(i) - w / 2).toFixed(1)}" y="${padT}" width="${w.toFixed(1)}" height="${eqH + gap + posH}" fill="transparent" style="cursor:crosshair" data-ms-bt-pt="${i}"><title>${E(c.date)}（${E(c.stageLabel)}）· 净值 ${money(c.equity)} (${pct(c.masterRetPct, 2, true)}) · 回撤 ${pct(c.masterDdPct, 2)} · 无纪律 ${pct(c.ablationRetPct, 2, true)} · 基准 ${pct(c.benchRetPct, 2, true)} · 仓位 ${pct(c.positionPct, 1)} (${c.holdingCount}只)</title></rect>`;
    }).join('');

    const fmtAxisY = v => isEquity
      ? (Math.abs(v) >= 1e4 ? `${(v / 1e4).toFixed(1)}万` : `¥${v.toFixed(0)}`)
      : `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;

    const ticks = [maxY, (maxY + minY) / 2, minY];
    const gridLines = ticks.map(v => {
      const y = yEq(v).toFixed(1);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#E8EDF4" stroke-dasharray="3,3"/><text x="${padL - 6}" y="${Number(y) + 4}" text-anchor="end" fill="#59677A" font-size="11">${fmtAxisY(v)}</text>`;
    }).join('');

    const dateTicks = [0, Math.floor((curve.length - 1) / 2), curve.length - 1]
      .filter((v, idx, arr) => arr.indexOf(v) === idx)
      .map(idx => `<text x="${xAt(idx).toFixed(1)}" y="${H - 6}" text-anchor="${idx === 0 ? 'start' : idx === curve.length - 1 ? 'end' : 'middle'}" fill="#59677A" font-size="11">${E(curve[idx].date)}</text>`)
      .join('');

    const benchLabel = bt?.benchmarkSummary?.name || '中证1000基准';

    return `
      <div class="ms-curve-inspector" data-ms-bt-inspector>
        <div class="ms-curve-inspector-metrics">
          <span><strong>${E(selPt.date)}</strong> <span class="ms-pill stage-${E(selPt.stage)}">${E(selPt.stageLabel)}</span></span>
          <span>净值 <strong>${money(selPt.equity)}</strong>（<b class="${tone(selPt.masterRetPct)}">${pct(selPt.masterRetPct, 2, true)}</b>）</span>
          <span>动态回撤 <b class="ms-down">${pct(selPt.masterDdPct, 2)}</b></span>
          <span>仓位 <strong>${pct(selPt.positionPct, 1)}</strong>（持股 <b>${selPt.holdingCount}</b> 只）</span>
          <span style="color:var(--color-secondary)">无纪律 ${pct(selPt.ablationRetPct, 2, true)} · 基准 ${pct(selPt.benchRetPct, 2, true)}</span>
        </div>
        <div class="ms-toolbar-group">
          ${btn('◀ 前一日', 'bt-curve-prev', selIdx <= 0 ? 'disabled' : '')}
          ${btn('后一日 ▶', 'bt-curve-next', selIdx >= curve.length - 1 ? 'disabled' : '')}
        </div>
      </div>
      <div class="ms-chart-legend">
        <span><i class="ms-legend-dot" style="background:${mainColor}"></i>完整高手模式（含择时开关 + 止损纪律）</span>
        <span><i class="ms-legend-dot" style="background:#64748B"></i>无纪律对照组（虚线 · 去择时 + -15%死扛）</span>
        <span><i class="ms-legend-dot" style="background:#2457E5"></i>${E(benchLabel)}</span>
        <span><i class="ms-legend-dot" style="background:#15803D"></i>下方折线：完整模式动态回撤（柱：当日仓位%）</span>
        <span>色带：<b style="color:#D97706">试错</b> / <b style="color:#C7383F">主升</b> / <b style="color:#7C3AED">震荡</b> / <b style="color:#15803D">退潮</b>（点击曲线任意日期可联动查看）</span>
      </div>
      <div class="ms-chart-wrap">
        <svg class="ms-chart-svg ms-bt-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="高手思路历史回测收益曲线与动态回撤图">
          ${gridLines}
          <line x1="${padL}" y1="${baseLineY.toFixed(1)}" x2="${W - padR}" y2="${baseLineY.toFixed(1)}" stroke="#7C8BA1" stroke-width="1"/>
          <polygon points="${areaPts}" fill="${mainColor}" opacity="0.09"/>
          <polyline points="${benchPts}" fill="none" stroke="#2457E5" stroke-width="1.6" opacity="0.78"/>
          <polyline points="${ablPts}" fill="none" stroke="#64748B" stroke-width="1.6" stroke-dasharray="5,3" opacity="0.85"/>
          <polyline points="${masterPts}" fill="none" stroke="${mainColor}" stroke-width="2.5" stroke-linejoin="round"/>
          ${ribbonRects}
          <line x1="${padL}" y1="${posTop}" x2="${W - padR}" y2="${posTop}" stroke="#DCE3ED"/>
          <line x1="${padL}" y1="${posTop + posH}" x2="${W - padR}" y2="${posTop + posH}" stroke="#DCE3ED"/>
          <text x="${padL - 6}" y="${posTop + 10}" text-anchor="end" fill="#59677A" font-size="10">仓100%</text>
          <text x="${padL - 6}" y="${posTop + posH}" text-anchor="end" fill="#59677A" font-size="10">0%</text>
          ${posBars}
          <polyline points="${ddPts}" fill="none" stroke="#15803D" stroke-width="1.5" stroke-dasharray="4,2"/>
          <line x1="${selX.toFixed(1)}" y1="${padT}" x2="${selX.toFixed(1)}" y2="${posTop + posH}" stroke="#2457E5" stroke-width="1" stroke-dasharray="3,3" opacity="0.75"/>
          <circle cx="${selX.toFixed(1)}" cy="${selY.toFixed(1)}" r="4" fill="${mainColor}" stroke="#FFFFFF" stroke-width="1.5"/>
          ${hitRects}
          ${dateTicks}
        </svg>
      </div>`;
  }

  function renderPositionCountComparison(bt, effCurveMode = 'return') {
    const rows = bt?.positionCountComparison || [];
    if (!rows.length) return '';
    const bestRetRow = rows.reduce((best, r) => (!best || r.totalReturnPct > best.totalReturnPct ? r : best), null);
    const bestCalmarRow = rows.reduce((best, r) => {
      const c = finite(r.calmarRatio) ? Number(r.calmarRatio) : -999;
      const bc = best && finite(best.calmarRatio) ? Number(best.calmarRatio) : -999;
      return c > bc ? r : best;
    }, null);
    const showingSlots = effCurveMode === 'slots';
    return `
      <div class="ms-wb-subsec" data-ms-slot-comparison>
        <div class="ms-wb-subhead">
          <div>
            <h3>不同持仓数量（1～10只）收益与风险对比</h3>
            <small style="color:var(--color-secondary)">同一套选股、择时与止损下对比单吊集中 vs 分仓平滑；点击「套用」同步切换左右两侧推演与回测。</small>
          </div>
          <div class="ms-toolbar-group" style="font-size:12px">
            ${bestRetRow ? `<span class="ms-pill buy">最高收益：<b>${bestRetRow.maxPositions}只</b> (${pct(bestRetRow.totalReturnPct, 1, true)})</span>` : ''}
            ${bestCalmarRow && finite(bestCalmarRow.calmarRatio) ? `<span class="ms-pill">最优回撤比：<b>${bestCalmarRow.maxPositions}只</b> (${num(bestCalmarRow.calmarRatio, 2)})</span>` : ''}
            <button type="button" class="ms-filter-pill ${showingSlots ? 'active' : ''}" data-ms-bt-cmode="${showingSlots ? 'return' : 'slots'}">${showingSlots ? '返回单仓净值主图' : '在上方主图叠看多仓数曲线'}</button>
          </div>
        </div>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead>
              <tr>
                <th>持仓上限</th>
                <th class="ms-num">累计收益</th>
                <th class="ms-num">超额Alpha</th>
                <th class="ms-num">最大回撤</th>
                <th class="ms-num">回撤比</th>
                <th class="ms-num">胜率</th>
                <th class="ms-num">均/峰持</th>
                <th class="ms-num">笔数</th>
                <th>联动操作</th>
              </tr>
            </thead>
            <tbody>${rows.map((r, idx) => {
              const color = SLOT_LINE_COLORS[idx % SLOT_LINE_COLORS.length];
              return `<tr${r.isCurrent ? ' style="background:var(--color-selected)"' : ''}>
                <td>
                  <i class="ms-legend-dot" style="background:${color}"></i>
                  <strong>${r.maxPositions}只</strong>
                  ${r.isStyleDefault ? '<span class="ms-pill" style="margin-left:3px">默认</span>' : ''}
                  ${r.isCurrent ? '<span class="ms-pill act-open_buy" style="margin-left:3px">当前</span>' : ''}
                </td>
                <td class="ms-num ${tone(r.totalReturnPct)}"><strong>${pct(r.totalReturnPct, 2, true)}</strong></td>
                <td class="ms-num ${tone(r.alphaVsBenchmarkPct)}">${pct(r.alphaVsBenchmarkPct, 1, true)}</td>
                <td class="ms-num ms-down"><strong>${pct(r.maxDrawdownPct, 2)}</strong></td>
                <td class="ms-num">${finite(r.calmarRatio) ? num(r.calmarRatio, 2) : '—'}</td>
                <td class="ms-num">${pct(r.winRatePct, 1)}</td>
                <td class="ms-num">${num(r.avgHoldingCount, 1)}/${r.maxHoldingCount ?? 0}</td>
                <td class="ms-num">${num(r.tradeCount, 0)}</td>
                <td>${r.isCurrent
                  ? '<span style="font-size:11.5px;color:var(--color-primary);font-weight:600">当前生效</span>'
                  : btn('套用同步', 'bt-apply-slot-run', `data-slots="${r.maxPositions}" ${state.backtestBusy ? 'disabled' : ''}`)}</td>
              </tr>`;
            }).join('')}</tbody>
          </table>
        </div>
      </div>`;
  }

  function renderArticlesTab(master) {
    const activeRep = state.reports.find(r => r.id === state.activeReportId) || state.reports[0] || null;
    return `<section class="panel ms-panel">
      <div class="panel-head">
        <div>
          <h2>「${E(master.name)}」深度操盘文章与小白实操手册（共 ${state.reports.length} 篇）</h2>
          <p class="ms-sub">基于逐笔交割单与真实历史大盘环境深度复盘：拆解纠错止损、浮盈加仓 vs 减仓条件、主线赛道切换时间轴与小白可直接照搬的仓位铁律。</p>
        </div>
        ${btn(state.presetsBusy ? '载入中…' : '同步/补全内置深度手册', 'install-presets', state.presetsBusy ? 'disabled' : '')}
      </div>
      ${state.reports.length ? `
        <div class="ms-two-col" style="grid-template-columns:290px minmax(0,1fr);align-items:start">
          <div class="ms-report-list">
            ${state.reports.map(r => `<div class="ms-report-item ${activeRep?.id === r.id ? 'active' : ''}" data-ms-report="${E(r.id)}">
              <div><strong>${E(r.title)}</strong><small>${E(r.model || '深度研报')} · ${E(r.createdAt?.slice(0, 10) || '')}</small></div>
            </div>`).join('')}
          </div>
          ${activeRep ? `<article class="ms-report-box">${formatMarkdownSafe(activeRep.content)}</article>` : ''}
        </div>
      ` : '<div class="ms-notice">该档案暂无深度分析文章。点击右上角「同步/补全内置深度手册」载入顽主杯 6 大高手的实证手册，或在「AI导师」标签页一键生成。</div>'}
    </section>`;
  }

  function actionPill(actionType, actionLabel, side) {
    const cls = actionType ? `act-${E(actionType)}` : (side === 'buy' ? 'buy' : 'sell');
    const txt = actionLabel || (side === 'buy' ? '买入' : '卖出');
    return `<span class="ms-pill ${cls}">${E(txt)}</span>`;
  }

  function renderPredictedOrdersBlock(plan, title, subtitle, signalDate = '', embedded = false) {
    if (!plan) return '';
    const orders = plan.orders || plan.predictedOrders || [];
    const projHoldings = plan.projectedHoldings || [];
    const ps = plan.projectedSummary || {};
    const blocked = plan.blockedSignals || [];
    const lim = plan.limits || plan.activeLimits || {};
    const wrapTag = embedded ? 'div' : 'section';
    const wrapCls = embedded ? 'ms-wb-subsec' : 'panel ms-panel';
    return `
      <${wrapTag} class="${wrapCls}" data-ms-predicted-orders>
        <div class="${embedded ? 'ms-wb-subhead' : 'panel-head'}">
          <div>
            <${embedded ? 'h3' : 'h2'}>${E(title)}</${embedded ? 'h3' : 'h2'}>
            <small style="color:var(--color-secondary);display:block;margin-top:2px">${E(subtitle)}</small>
          </div>
          <div class="ms-toolbar-group" style="font-size:11.5px">
            <span class="ms-pill">总仓 ${pct(ps.beforePosPct, 1)} → <b>${pct(ps.afterPosPct, 1)}</b> (≤${num(ps.totalPosCapPct ?? lim.totalPosCapPct, 0)}%)</span>
            <span class="ms-pill">持股 ${ps.beforeCount ?? 0} → <b>${ps.afterCount ?? 0}</b>/${ps.maxSlots ?? lim.maxSlots ?? '—'}只</span>
            <span class="ms-pill">余现金 <b>${money(ps.afterCash)}</b> (${pct(ps.afterCashPct, 1)})</span>
          </div>
        </div>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead>
              <tr>
                <th>序</th><th>动作</th><th>股票</th><th class="ms-num">股数</th><th class="ms-num">参考价</th><th class="ms-num">预估额</th><th class="ms-num">单票仓位</th><th>触发硬规则与时段</th>
              </tr>
            </thead>
            <tbody>${orders.map(o => `<tr>
              <td class="ms-num">${o.seq}</td>
              <td>${actionPill(o.actionType, o.actionLabel, o.side)}</td>
              <td>${stockBtn(o.symbol, o.name, o.code, signalDate)} <small style="color:var(--color-disabled)">${E(o.board || '')}</small></td>
              <td class="ms-num"><strong>${o.side === 'buy' ? '+' : '-'}${num(o.quantity, 0)}</strong></td>
              <td class="ms-num">${num(o.refPrice, 2)}</td>
              <td class="ms-num">${money(o.estAmount)}</td>
              <td class="ms-num">${pct(o.beforeWeightPct, 1)}→<strong>${pct(o.afterWeightPct, 1)}</strong></td>
              <td style="font-size:11.5px;max-width:260px;white-space:normal">${E(o.reason)} <small style="color:var(--color-disabled)">(${E(o.execWindow || '')})</small></td>
            </tr>`).join('') || '<tr><td colspan="8" style="text-align:center;color:var(--color-secondary)">按当前硬规则约束，明日无新增委托（锁仓不动或空仓等待）。</td></tr>'}</tbody>
          </table>
        </div>
        ${blocked.length ? `<div class="ms-notice warn">
          <strong>规则自动拦截（共 ${blocked.length} 项）：</strong>
          ${blocked.map(b => `<div>• <b>${E(b.name || b.code)} (${E(b.code)})</b>：${E(b.reason)}</div>`).join('')}
        </div>` : ''}
        <div class="ms-wb-subhead" style="margin-top:4px">
          <strong style="font-size:12.5px">执行后预期持仓（${projHoldings.length} 只 · 现金 ${pct(ps.afterCashPct, 1)}）</strong>
        </div>
        <div class="ms-table-scroll">
          <table class="ms-table">
            <thead>
              <tr><th>股票</th><th class="ms-num">预期股数</th><th class="ms-num">成本/参考价</th><th class="ms-num">单票占比</th><th class="ms-num">浮盈</th><th>状态</th><th class="ms-num">止损 / MA5</th></tr>
            </thead>
            <tbody>${projHoldings.map(h => `<tr>
              <td>${stockBtn(h.symbol, h.name, h.code, signalDate)} <small style="color:var(--color-disabled)">${E(h.board || '')}</small></td>
              <td class="ms-num"><strong>${num(h.quantity, 0)}</strong></td>
              <td class="ms-num">${num(h.avgCost, 2)} / ${num(h.refPrice, 2)}</td>
              <td class="ms-num"><strong>${pct(h.weightPct, 1)}</strong></td>
              <td class="ms-num ${tone(h.unrealizedPnlPct)}">${pct(h.unrealizedPnlPct, 2, true)}</td>
              <td><span class="ms-pill">${E(h.statusTag || '持有')}</span></td>
              <td class="ms-num">${h.stopPrice ? num(h.stopPrice, 2) : '—'} / ${h.ma5 ? num(h.ma5, 2) : '—'}</td>
            </tr>`).join('') || '<tr><td colspan="7" style="text-align:center;color:var(--color-secondary)">执行后空仓（100% 现金防守）</td></tr>'}</tbody>
          </table>
        </div>
      </${wrapTag}>`;
  }

  function renderWorkbenchOverviewStrip(adv, bt, isBtRunning) {
    const snap = adv?.marketSnapshot || {};
    const dir = adv?.directive || {};
    const lim = adv?.activeLimits || {};
    const projSum = adv?.projectedSummary || {};
    const timelineSteps = adv?.timelineChecklist || [];
    const ms = bt?.summary || {};
    const as_ = bt?.ablationSummary || {};
    const bm = bt?.benchmarkSummary || {};
    const pm = bt?.proofMetrics || {};
    const hasBt = !!bt;

    return `
      <section class="panel ms-wb-overview" data-ms-wb-overview>
        <div class="ms-wb-kpi-strip">
          <div class="ms-kpi">
            <span class="ms-kpi-label">今日市场与情绪 · ${E(snap.date || '—')}</span>
            <strong class="ms-kpi-value"><span class="${tone(snap.shChangePct)}">${pct(snap.shChangePct, 2, true)}</span> / <span class="${tone(snap.cybChangePct)}">${pct(snap.cybChangePct, 2, true)}</span></strong>
            <span class="ms-kpi-hint"><span class="ms-pill stage-${E(snap.stage || 'chop')}" style="padding:1px 6px;font-size:10.5px">${E(snap.stageLabel || '待读取')}</span> 额 ${finite(snap.turnoverYi) ? `${num(snap.turnoverYi, 0)}亿` : '—'} · 科创${snap.kc50AboveMA20 ? '>MA20' : '<MA20'}</span>
          </div>
          <div class="ms-kpi">
            <span class="ms-kpi-label">明日总仓 / 单票红线</span>
            <strong class="ms-kpi-value ms-primary">${E(dir.recommendedTotalPos || '—')} / ≤${E(String(dir.maxSinglePosPct ?? '—'))}%</strong>
            <span class="ms-kpi-hint">最多 ${E(String(dir.maxHoldings ?? '—'))} 只 · 首仓 ${pct(lim.firstEntryPct, 1)} · 硬止损 ${pct(lim.stopLossPct, 1)}</span>
          </div>
          <div class="ms-kpi">
            <span class="ms-kpi-label">预计明日仓位与委托</span>
            <strong class="ms-kpi-value">${pct(dir.currentPositionPct, 1)} → ${pct(projSum.afterPosPct, 1)}</strong>
            <span class="ms-kpi-hint">持股 ${projSum.beforeCount ?? 0}→${projSum.afterCount ?? 0} 只 · 委托 ${(adv?.predictedOrders || []).length} 笔</span>
          </div>
          <div class="ms-proof-card primary">
            <span class="ms-kpi-label">① 完整高手模式回测</span>
            <strong class="ms-kpi-value ${hasBt ? tone(ms.totalReturnPct) : ''}">${hasBt ? pct(ms.totalReturnPct, 2, true) : (isBtRunning ? '测算中…' : '待测算')}</strong>
            <span class="ms-kpi-hint">${hasBt ? `净值 ${money(ms.finalEquity)} · 回撤 <b class="ms-down">${pct(ms.maxDrawdownPct, 2)}</b> · 胜率 ${pct(ms.winRatePct, 1)}` : '同步参数计算同区间净值与胜率'}</span>
          </div>
          <div class="ms-proof-card">
            <span class="ms-kpi-label">② 无纪律对照组（死扛）</span>
            <strong class="ms-kpi-value ${hasBt ? tone(as_.totalReturnPct) : ''}">${hasBt ? pct(as_.totalReturnPct, 2, true) : '—'}</strong>
            <span class="ms-kpi-hint">${hasBt ? `回撤 <b class="ms-down">${pct(as_.maxDrawdownPct, 2)}</b> · 守纪律少亏 <b>${pct(pm.drawdownSavedPct, 2, true)}</b>` : '去择时开关 + -15% 止损对照'}</span>
          </div>
          <div class="ms-proof-card">
            <span class="ms-kpi-label">③ 同期基准：${E(bm.name || '中证1000')}</span>
            <strong class="ms-kpi-value ${hasBt ? tone(bm.totalReturnPct) : ''}">${hasBt ? pct(bm.totalReturnPct, 2, true) : '—'}</strong>
            <span class="ms-kpi-hint">${hasBt ? `基准回撤 ${pct(bm.maxDrawdownPct, 2)} · 超额 Alpha <b class="${tone(pm.alphaVsBenchmarkPct)}">${pct(pm.alphaVsBenchmarkPct, 2, true)}</b>` : '同期市场指数收益与回撤对照'}</span>
          </div>
        </div>
        ${adv ? `
          <div class="ms-wb-verdict-row">
            <div><strong>明日总纲：</strong>${E(dir.positionVerdict || '')}${dir.specialAlert ? ` <span class="ms-warn">（⚠ ${E(dir.specialAlert)}）</span>` : ''}</div>
            <small style="color:var(--color-secondary)">日线源：${E(snap.sourceLabel || '—')}</small>
          </div>
          ${timelineSteps.length ? `
            <div aria-label="明日分时段执行时间表">
              <div style="font-size:12px;font-weight:650;color:var(--color-secondary);margin-bottom:6px">明日分时段执行时间表（盘前竞价 → 早盘排雷 → 止盈开仓 → 尾盘风控）</div>
              <div class="ms-timeline-grid">
                ${timelineSteps.map(s => `<div class="ms-timeline-step">
                  <code>${E(s.window)}</code>
                  <strong>${E(s.phase)}</strong>
                  <p>${E(s.action)}</p>
                </div>`).join('')}
              </div>
            </div>
          ` : ''}
        ` : ''}
      </section>`;
  }

  function renderTomorrowActionColumn(adv) {
    if (state.advisorError) {
      return `<section class="panel ms-panel"><div class="ms-notice err">${E(state.advisorError)}</div></section>`;
    }
    if (!adv) {
      return `<section class="panel ms-panel"><div class="ms-notice">${state.advisorBusy
        ? '正在结合今日收盘日线与当前持仓，逐票推演明日默认动作、涨停应对、破位止损与新开仓买法…'
        : '正在准备明日实战推演方案…'}</div></section>`;
    }

    const snap = adv.marketSnapshot || {};
    const dir = adv.directive || {};
    const prof = adv.profile || {};
    const holdings = adv.holdingActions || [];
    const orders = adv.predictedOrders || [];
    const npp = adv.newPositionPlan || {};
    const candPlaybooks = npp.candidatePlaybooks || [];
    const splitGuide = adv.positionSplittingGuide || null;
    const splitTiers = splitGuide?.capitalTiers || [];
    const splitBench = splitGuide?.allBenchmarks || [];
    const pSec = state.planSection || 'all';
    const showHoldings = pSec === 'all' || pSec === 'holdings';
    const showNewPos = pSec === 'all' || pSec === 'newpos';
    const showOrders = pSec === 'all' || pSec === 'orders';
    const accLabel = adv.accountSource === 'custom'
      ? `演练持仓 (${holdings.length}只 · 资产 ${money(dir.accountEquityBasis)})`
      : `实盘账户${adv.accountName ? `「${adv.accountName}」` : ''} (${holdings.length}只 · 资产 ${money(dir.accountEquityBasis)})`;

    return `
      <section class="panel ms-panel" data-ms-plan-pane>
        <div class="panel-head">
          <div>
            <h2>明日实战作战执行台</h2>
            <p class="ms-sub">${E(accLabel)} · 同一面板闭环覆盖旧仓四场景应对、新开仓预案与委托汇总</p>
          </div>
          <div class="ms-filter-pills" role="tablist" aria-label="明日作战执行焦点">
            ${[
              ['all', '全部展开'],
              ['holdings', `已有持仓 (${holdings.length})`],
              ['newpos', `新开仓 (${candPlaybooks.length})`],
              ['orders', `预测委托 (${orders.length})`],
            ].map(([k, lbl]) => `<button type="button" class="ms-filter-pill ${pSec === k ? 'active' : ''}" data-ms-plan-sec="${k}">${E(lbl)}</button>`).join('')}
          </div>
        </div>

        ${showHoldings ? `<div data-ms-holding-playbooks>
          <div class="ms-wb-subhead" style="margin-bottom:8px">
            <h3>对已有仓位怎么做 · 逐票全场景作战卡（${holdings.length} 只）</h3>
            ${!state.advisorOpts.useCustomHoldings ? btn('手动录入/演练持仓', 'adv-open-custom') : ''}
          </div>
          ${holdings.length ? `<div class="ms-rule-list">
            ${holdings.map(h => {
              const sp = h.scenarioPlaybook || {};
              const bp = sp.basePlan || {};
              const zp = sp.limitUpPlan || {};
              const tp = sp.trimHoldPlan || {};
              const cp = sp.breakdownPlan || {};
              return `<div class="ms-action-card urg-${E(h.urgency)}">
                <div class="ms-action-head">
                  <div>
                    ${stockBtn(h.symbol, h.name, h.code, h.latestDate)}
                    <span class="ms-pill" style="margin-left:5px">${E(h.board)}</span>
                    ${h.isZt ? `<span class="ms-pill buy" style="margin-left:4px">${h.consecZt || 1}连板</span>` : (h.bombedZt ? '<span class="ms-pill sell" style="margin-left:4px">今日炸板</span>' : '')}
                    <span style="margin-left:6px;font-size:12px;color:var(--color-secondary)">
                      持 <strong>${num(h.quantity, 0)}</strong>股 (<b>${pct(h.weightPct, 1)}</b>) · 成本 <b>¥${num(h.averageCost, 2)}</b> · 收 <b>¥${num(h.latestPrice, 2)}</b> (<b class="${tone(h.unrealizedPnlPct)}">${pct(h.unrealizedPnlPct, 2, true)}</b>)
                    </span>
                  </div>
                  <span class="ms-action-badge urg-${E(h.urgency)}">${E(h.actionLabel)}</span>
                </div>
                <div class="ms-price-anchors">
                  <span>涨停价 <b class="ms-up">¥${num(h.limitUpPrice, 2)}</b></span>
                  <span>减半位 <b>¥${num(tp.triggerPrice, 2)}</b></span>
                  <span>MA5 <b>¥${num(h.ma5Price, 2)}</b>(${h.aboveMA5 ? '线上' : '破位'})</span>
                  <span>MA20 <b>¥${num(h.ma20Price, 2)}</b></span>
                  <span>今低 <b>¥${num(h.todayLowPrice, 2)}</b></span>
                  <span>硬止损 <b class="ms-down">¥${num(h.stopPrice, 2)}</b></span>
                </div>
                <div class="ms-scenario-grid">
                  <div class="ms-scenario-box sc-base">
                    <div class="ms-scenario-head"><strong>📌 明日基准指令</strong><code>${E(bp.window || h.executionWindow || '')}</code></div>
                    <div class="ms-scenario-headline">${E(bp.headline || h.targetPositionChange || '')}</div>
                    <div class="ms-scenario-detail">${E(bp.detail || h.plainReason || '')}</div>
                  </div>
                  <div class="ms-scenario-box sc-zt">
                    <div class="ms-scenario-head"><strong>🔥 涨停怎么做 (¥${num(h.limitUpPrice, 2)})</strong><code>${E(zp.window || '09:25–10:30')}</code></div>
                    <div class="ms-scenario-headline">${E(zp.headline || '')}</div>
                    <div class="ms-scenario-detail">${E(zp.detail || '')}</div>
                    ${zp.bombFallback ? `<div class="ms-scenario-sub">${E(zp.bombFallback)}</div>` : ''}
                  </div>
                  <div class="ms-scenario-box sc-trim">
                    <div class="ms-scenario-head"><strong>⚖️ 冲高不板 / 震荡怎么做</strong><code>${E(tp.window || '09:30–10:30')}</code></div>
                    <div class="ms-scenario-headline">${E(tp.headline || '')}</div>
                    <div class="ms-scenario-detail">${E(tp.detail || '')}</div>
                  </div>
                  <div class="ms-scenario-box sc-cut">
                    <div class="ms-scenario-head"><strong>🛡️ 破位 / 走弱怎么做</strong><code>${E(cp.window || '09:15–09:45')}</code></div>
                    <div class="ms-scenario-headline">${E(cp.headline || '')}</div>
                    <div class="ms-scenario-detail">${E(cp.detail || '')}</div>
                  </div>
                </div>
              </div>`;
            }).join('')}
          </div>` : `
            <div class="ms-notice" style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
              <div>
                <strong>当前为空仓（0 只持股，100% 现金）</strong>：无旧仓排雷负担，直接按下方新开仓预案择强建首仓。
              </div>
              <div class="ms-toolbar-group">
                ${btn('录入当前持仓 →', 'adv-open-custom', '', 'analysis-tools-primary')}
                ${btn('填入 3 只演示持仓', 'adv-load-demo')}
              </div>
            </div>
          `}
        </div>` : ''}

        ${showNewPos ? `<div class="${showHoldings ? 'ms-wb-subsec' : ''}" data-ms-new-pos-plan>
          <div class="ms-wb-subhead">
            <h3>如果要新开仓位怎么做 · 额度与候选买法预案</h3>
            <div class="ms-toolbar-group" style="font-size:11.5px">
              <span class="ms-pill">可开槽位 <b>${npp.openSlots ?? 0}</b>/${npp.maxSlots ?? '—'}只</span>
              <span class="ms-pill">可用资金 <b>${money(npp.availableCashForNewBuys)}</b></span>
              <span class="ms-pill">标准首仓 <b>${pct(npp.firstEntryPct, 1)}</b> (≈${money(npp.perSlotBudget)})</span>
            </div>
          </div>
          <div class="ms-notice ${E(npp.statusTone || 'ok')}">
            <div><strong>${E(npp.headline || '')}</strong></div>
            <div style="margin-top:3px;font-size:12px">${E(npp.executionRule || '')}</div>
          </div>
          ${candPlaybooks.length ? `<div class="ms-rule-list">
            ${candPlaybooks.map(c => `<div class="ms-cand-card ${c.role === 'planned' ? 'planned' : ''}">
              <div class="ms-action-head">
                <div>
                  <span class="ms-pill ${c.role === 'planned' ? 'act-open_buy' : ''}" style="margin-right:5px">#${c.rank} ${E(c.roleLabel)}</span>
                  ${stockBtn(c.symbol, c.name, c.code, snap.date)}
                  <span class="ms-pill" style="margin-left:5px">${E(c.board)}</span>
                  ${(c.matchTags || []).map(t => `<span class="ms-pill" style="margin-left:3px">${E(t)}</span>`).join('')}
                  <span style="margin-left:6px;font-size:12px;color:var(--color-secondary)">
                    收 <b>¥${num(c.close, 2)}</b> (<span class="${tone(c.pct)}">${pct(c.pct, 2, true)}</span>) · ${num(c.amountYi, 1)}亿 · 评分 <b>${num(c.score, 0)}</b> · 涨停 <b class="ms-up">¥${num(c.limitUpPrice, 2)}</b>
                  </span>
                </div>
                <span class="ms-pill buy">首仓 <b>${num(c.plannedQty, 0)}股</b> (≈${money(c.plannedAmount)} · ${pct(c.plannedWeightPct, 1)})</span>
              </div>
              <div class="ms-cand-rules-grid">
                <div class="ms-cand-rule-box"><strong>✅ 明日怎么买</strong><span>${E(c.triggerZone)}</span></div>
                <div class="ms-cand-rule-box"><strong>⛔ 什么情况坚决放弃</strong><span>${E(c.abortConditions)}</span></div>
                <div class="ms-cand-rule-box"><strong>🔄 买入后预案</strong><span>${E(c.postBuyPlan)}</span></div>
              </div>
            </div>`).join('')}
          </div>` : '<div class="ms-notice warn">今日收盘扫描未发现满足当前高手买点硬门槛的标的，明日切勿强行开新仓。</div>'}
        </div>` : ''}

        ${showOrders ? renderPredictedOrdersBlock(
          {
            orders: adv.predictedOrders,
            projectedHoldings: adv.projectedHoldings,
            projectedSummary: adv.projectedSummary,
            blockedSignals: adv.blockedSignals,
            limits: adv.activeLimits,
          },
          `明日基准预测委托单汇总（共 ${orders.length} 笔）`,
          `按「先止损/压仓/减半 → 再浮盈加仓 → 最后空余槽位建首仓」排序，股数已按主板100股/科创200股取整。`,
          snap.date || '',
          showHoldings || showNewPos
        ) : ''}

        <details class="ms-details" style="margin-top:14px">
          <summary>参考：6 大高手实盘分仓对照、资金档位与模式风控红线</summary>
          <div class="ms-details-body">
            ${splitGuide ? `<div class="ms-two-col">
              <div class="ms-table-scroll">
                <h3>资金档位分仓参考</h3>
                <table class="ms-table">
                  <thead><tr><th>档位</th><th>分仓</th><th>首仓</th><th>加仓</th><th>上限</th></tr></thead>
                  <tbody>${splitTiers.map(t => `<tr${t.active ? ' style="background:#eef5ff;font-weight:600"' : ''}>
                    <td>${t.active ? '▶ ' : ''}${E(t.label)}</td><td>${E(t.maxSlots)}</td><td>${E(t.firstEntryPct)}</td><td>${E(t.addOnWinPct)}</td><td>${E(t.singleCapPct)}</td>
                  </tr>`).join('')}</tbody>
                </table>
              </div>
              <div class="ms-table-scroll">
                <h3>6 大高手实盘分仓基准</h3>
                <table class="ms-table">
                  <thead><tr><th>高手</th><th>模式</th><th class="ms-num">均持</th><th class="ms-num">空仓</th></tr></thead>
                  <tbody>${splitBench.map(b => `<tr${b.styleKey === adv.styleKey ? ' style="background:#eef5ff;font-weight:600"' : ''}>
                    <td>${E(b.name)}</td><td>${E(b.pattern)}</td><td class="ms-num">${num(b.avgHoldings, 2)}只</td><td class="ms-num">${pct(b.emptyDayPct, 1)}</td>
                  </tr>`).join('')}</tbody>
                </table>
              </div>
            </div>` : ''}
            <div class="ms-rule-list">
              ${(prof.redLines || []).map((line, idx) => `<div class="ms-rule-card"><strong>红线 ${idx + 1}</strong>${E(line)}</div>`).join('')}
            </div>
            <p class="ms-sub" style="margin:0;font-size:11.5px">免责声明：${E(adv.disclaimer || '')}</p>
          </div>
        </details>
      </section>
    `;
  }

  function renderBacktestProofColumn(master, defaultBtSlots) {
    const bt = state.backtest;
    const curTask = state.backtestTask;
    const isRunning = btTaskAlive(curTask);
    const ms = bt?.summary || {};
    const pm = bt?.proofMetrics || {};
    const rs = bt?.ruleExecutionStats || null;
    const activeRules = bt?.activeRules || null;
    const timeline = bt?.positionTimeline || [];
    const selSnap = timeline.find(d => d.date === state.btSnapshotDate) || timeline[timeline.length - 1] || null;
    const hasSlotComparison = (bt?.positionCountComparison || []).length > 1;
    const effCurveMode = state.btCurveMode === 'slots' && !hasSlotComparison ? 'return' : (state.btCurveMode || 'return');

    const allBtTrades = bt?.trades || [];
    const orderedTrades = state.btTradeSort === 'asc' ? [...allBtTrades] : [...allBtTrades].reverse();
    const tFilter = state.btTradeFilter || 'all';
    const matchTradeCat = (t, cat) => {
      if (cat === 'all') return true;
      if (cat === 'open_buy') return t.actionType === 'open_buy' || (!t.actionType && t.side === 'buy');
      if (cat === 'add_win') return t.actionType === 'add_win';
      if (cat === 'trim') return ['trim_profit', 'trim_regime', 'trim_cap'].includes(t.actionType);
      if (cat === 'exit') return ['cut_stop', 'cut_weak', 'sell_rotate', 'sell_profit', 'sell_time'].includes(t.actionType) || (!t.actionType && t.side === 'sell');
      return true;
    };
    const qRaw = String(state.btTradeQuery || '').trim().toLowerCase();
    const queryMatchedTrades = qRaw
      ? orderedTrades.filter(t => `${t.date || ''} ${t.code || ''} ${t.name || ''} ${t.symbol || ''} ${t.actionLabel || ''} ${t.reason || ''}`.toLowerCase().includes(qRaw))
      : orderedTrades;
    const catCounts = {
      all: queryMatchedTrades.length,
      open_buy: queryMatchedTrades.filter(t => matchTradeCat(t, 'open_buy')).length,
      add_win: queryMatchedTrades.filter(t => matchTradeCat(t, 'add_win')).length,
      trim: queryMatchedTrades.filter(t => matchTradeCat(t, 'trim')).length,
      exit: queryMatchedTrades.filter(t => matchTradeCat(t, 'exit')).length,
    };
    const filteredTrades = queryMatchedTrades.filter(t => matchTradeCat(t, tFilter));
    const btPageSize = Math.max(10, Math.min(200, Number(state.btTradePageSize) || 20));
    const btTotalPages = Math.max(1, Math.ceil(filteredTrades.length / btPageSize));
    const btCurPage = Math.max(1, Math.min(btTotalPages, Number(state.btTradePage) || 1));
    const pagedBtTrades = filteredTrades.slice((btCurPage - 1) * btPageSize, btCurPage * btPageSize);

    const taskSwitcher = state.backtestTasks.length ? `
      <div class="ms-toolbar-group" style="font-size:12px">
        <select class="ms-wb-task-select" data-ms-bt-task-switch aria-label="切换已保存的高手回测任务">
          ${state.backtestTasks.slice(0, 15).map(t => {
            const retPct = finite(t.stats?.total_return) ? Number(t.stats.total_return) * 100 : null;
            return `<option value="${E(t.id)}" ${t.id === state.activeBacktestTaskId ? 'selected' : ''}>${E(t.name)} · ${E(BT_STATUS_LABEL[t.status] || t.status)}${retPct !== null ? ` (${pct(retPct, 1, true)})` : ''}</option>`;
          }).join('')}
        </select>
        ${curTask && !isRunning ? btn('删', 'bt-delete-task', `data-task-id="${E(curTask.id)}" title="删除当前回测记录"`) : ''}
      </div>` : '';

    return `
      ${state.backtestError ? `<div class="ms-notice err">${E(state.backtestError)}</div>` : ''}
      ${!bt ? `<section class="panel ms-panel">
        <div class="panel-head">
          <div><h2>同参数历史回测验证台</h2><p class="ms-sub">与左侧明日作战计划共用同一套策略参数、持仓只数与止损开关</p></div>
          ${taskSwitcher}
        </div>
        <div class="ms-notice">${isRunning
          ? '正在后台高速计算同参数历史回测收益曲线、1～10只持仓对比与模拟交割单，完成后将在此自动呈现…'
          : '点击顶部 <strong>「同步推演并回测」</strong>，即可在此同屏验证历史净值曲线、多仓数对比与逐笔交割单。'}</div>
      </section>` : `
        <section class="panel ms-panel" data-ms-bt-curve-panel>
          <div class="panel-head">
            <div>
              <h2>同参数历史回测曲线（${E(bt.dateRange?.startDate)} ～ ${E(bt.dateRange?.endDate)} · ${bt.dateRange?.tradingDays}日）</h2>
              <p class="ms-sub">${E(bt.dataSourceLabel || '')} · ${bt.universeCount || 0}股 · 最多 <b>${bt.config?.maxPositions || activeRules?.maxPositions || defaultBtSlots}只</b>${bt.config?.customMaxPositions ? ' [自定义]' : ' [默认]'} · 均/峰持 ${num(ms.avgHoldingCount, 2)}/${ms.maxHoldingCount ?? 0}只</p>
            </div>
            <div class="ms-toolbar-group">
              <div class="ms-filter-pills" role="tablist" aria-label="回测收益曲线显示模式">
                <button type="button" class="ms-filter-pill ${effCurveMode === 'return' ? 'active' : ''}" data-ms-bt-cmode="return">收益率(%)</button>
                <button type="button" class="ms-filter-pill ${effCurveMode === 'equity' ? 'active' : ''}" data-ms-bt-cmode="equity">净值(¥)</button>
                ${hasSlotComparison ? `<button type="button" class="ms-filter-pill ${effCurveMode === 'slots' ? 'active' : ''}" data-ms-bt-cmode="slots">1~10只对比</button>` : ''}
              </div>
              ${taskSwitcher}
            </div>
          </div>
          ${renderBacktestChart(bt, effCurveMode)}
          ${(pm.verdictPoints || []).length ? `<div class="ms-rule-list" style="margin-top:10px">
            ${(pm.verdictPoints || []).map(v => `<div class="ms-rule-card ms-rule-nonrt" style="padding:9px 12px;font-size:12px">${E(v)}</div>`).join('')}
          </div>` : ''}
          ${renderPositionCountComparison(bt, effCurveMode)}
        </section>

        <section class="panel ms-panel" data-ms-bt-trades>
          <div class="panel-head">
            <div>
              <h2>规则执行审计 & 因果模拟交割单（${num(filteredTrades.length, 0)} / ${num(bt.totalTrades || allBtTrades.length, 0)} 笔）</h2>
              <p class="ms-sub">点击任意股票直接跳转 K 线复盘买卖点标注；点击上方曲线任意日期可联动查看当日持仓快照。</p>
            </div>
            <div class="ms-filter-pills">
              ${[
                ['all', `全部 (${catCounts.all})`],
                ['open_buy', `首仓 (${catCounts.open_buy})`],
                ['add_win', `加仓 (${catCounts.add_win})`],
                ['trim', `减半 (${catCounts.trim})`],
                ['exit', `止损/清仓 (${catCounts.exit})`],
              ].map(([k, lbl]) => `<button type="button" class="ms-filter-pill ${tFilter === k ? 'active' : ''}" data-ms-bt-tfilter="${k}">${E(lbl)}</button>`).join('')}
            </div>
          </div>

          ${rs ? `<div class="ms-stat-chips">
            <div class="ms-stat-chip"><span>首仓分仓建仓</span><strong>${rs.openBuys || 0} 笔</strong></div>
            <div class="ms-stat-chip"><span>封板浮盈加仓</span><strong>${rs.addWins || 0} 笔</strong></div>
            <div class="ms-stat-chip"><span>冲高减半(50%)</span><strong>${rs.profitTrims || 0} 笔</strong></div>
            <div class="ms-stat-chip"><span>退潮/超限压仓</span><strong>${(rs.regimeTrims || 0) + (rs.capTrims || 0) + (rs.techRotations || 0)} 笔</strong></div>
            <div class="ms-stat-chip"><span>硬止损 / 破位快砍</span><strong>${(rs.hardStops || 0) + (rs.fastWeakCuts || 0)} 笔</strong></div>
            <div class="ms-stat-chip"><span>风控降档 / 拦截</span><strong>${rs.throttleDays || 0}天 / ${(rs.capBlocks || 0) + (rs.auctionCancels || 0) + (rs.regimeShutoffs || 0)}次</strong></div>
          </div>` : ''}

          <div class="ms-filter-row" style="margin-bottom:10px">
            <input type="text" data-ms-bt-tquery value="${E(state.btTradeQuery)}" placeholder="搜索代码 / 名称 / 日期 / 触发规则…" style="min-width:200px;flex:1">
            <select data-ms-bt-tsort aria-label="交割单排序方式">
              <option value="desc" ${state.btTradeSort !== 'asc' ? 'selected' : ''}>最新优先（倒序）</option>
              <option value="asc" ${state.btTradeSort === 'asc' ? 'selected' : ''}>时间顺序（正序）</option>
            </select>
            ${(state.btTradeQuery || tFilter !== 'all') ? btn('重置', 'bt-reset-tfilter') : ''}
          </div>
          <div class="ms-table-scroll">
            <table class="ms-table">
              <thead><tr><th>日期</th><th>动作</th><th>股票</th><th class="ms-num">股数</th><th class="ms-num">成交价</th><th class="ms-num">单票仓位</th><th class="ms-num">盈亏 / 收益</th><th>触发硬规则</th></tr></thead>
              <tbody>${pagedBtTrades.map(t => `<tr>
                <td>${E(t.date)}</td>
                <td>${actionPill(t.actionType, t.actionLabel, t.side)}</td>
                <td>${stockBtn(t.symbol, t.name, t.code, t.date)}</td>
                <td class="ms-num">${t.side === 'buy' ? '+' : '-'}${num(t.quantity, 0)}</td>
                <td class="ms-num">${num(t.price, 2)}</td>
                <td class="ms-num">${t.beforeStockWeightPct != null ? `${pct(t.beforeStockWeightPct, 1)}→<b>${pct(t.afterStockWeightPct, 1)}</b>` : '—'}</td>
                <td class="ms-num ${tone(t.pnl)}">${t.pnl != null ? `${money(t.pnl)} (${pct(t.returnPct, 1, true)})` : '—'}</td>
                <td style="font-size:11.5px;max-width:230px;white-space:normal">${E(t.reason || '—')}</td>
              </tr>`).join('') || '<tr><td colspan="8" style="text-align:center">当前筛选下无成交记录</td></tr>'}</tbody>
            </table>
          </div>
          ${renderPaginationBar({ scope: 'bt', page: btCurPage, pageSize: btPageSize, total: filteredTrades.length, pageSizes: [15, 20, 50, 100], unit: '笔' })}

          ${(timeline.length || (bt.byStage || []).length) ? `<details class="ms-details" data-ms-bt-holdings style="margin-top:12px" ${state.btReplayOpen ? 'open' : ''}>
            <summary>按日持仓回放（当前：${E(selSnap?.date || '—')} · ${selSnap?.holdingCount ?? 0}只） & 分情绪阶段拆解</summary>
            <div class="ms-details-body">
              ${timeline.length ? `<div>
                <div class="ms-wb-subhead" style="margin-bottom:8px">
                  <span style="font-size:12px">总资产 <strong>${money(selSnap?.equity)}</strong> · 股票仓位 <strong>${pct(selSnap?.positionPct, 1)}</strong> · 现金 <strong>${pct(selSnap?.cashPct, 1)}</strong></span>
                  <select data-ms-bt-snapdate aria-label="持仓回放日期">
                    ${[...timeline].reverse().map(d => `<option value="${E(d.date)}" ${selSnap?.date === d.date ? 'selected' : ''}>${E(d.date)} · ${E(d.stageLabel)} · 仓${pct(d.positionPct, 0)} (${d.holdingCount}只)</option>`).join('')}
                  </select>
                </div>
                <div class="ms-table-scroll">
                  <table class="ms-table">
                    <thead><tr><th>股票</th><th class="ms-num">持股</th><th class="ms-num">成本/收盘</th><th class="ms-num">单票仓位</th><th class="ms-num">浮盈</th><th>状态</th></tr></thead>
                    <tbody>${(selSnap?.holdings || []).map(h => `<tr>
                      <td>${stockBtn(h.symbol, h.name, h.code, selSnap.date)}</td>
                      <td class="ms-num">${num(h.quantity, 0)}</td>
                      <td class="ms-num">${num(h.avgCost, 2)} / ${num(h.closePrice, 2)}</td>
                      <td class="ms-num"><strong>${pct(h.weightPct, 1)}</strong></td>
                      <td class="ms-num ${tone(h.pnlPct)}"><strong>${pct(h.pnlPct, 2, true)}</strong></td>
                      <td><span class="ms-pill">${E(h.statusTag || '首仓')}</span></td>
                    </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--color-secondary)">当日空仓防守</td></tr>'}</tbody>
                  </table>
                </div>
              </div>` : ''}
              ${(bt.byStage || []).length ? `<div class="ms-table-scroll">
                <h3>分市场情绪阶段回测表现</h3>
                <table class="ms-table">
                  <thead><tr><th>情绪阶段</th><th class="ms-num">天数</th><th class="ms-num">均仓</th><th class="ms-num">回合</th><th class="ms-num">胜率</th><th class="ms-num">均收益</th><th class="ms-num">净盈亏</th></tr></thead>
                  <tbody>${(bt.byStage || []).map(r => `<tr>
                    <td><span class="ms-pill stage-${E(r.stage)}">${E(r.label)}</span></td>
                    <td class="ms-num">${r.days}</td>
                    <td class="ms-num">${pct(r.avgPosPct, 1)}</td>
                    <td class="ms-num">${r.episodes}</td>
                    <td class="ms-num">${pct(r.winRatePct, 1)}</td>
                    <td class="ms-num ${tone(r.avgReturnPct)}">${pct(r.avgReturnPct, 2, true)}</td>
                    <td class="ms-num ${tone(r.totalPnl)}">${money(r.totalPnl)}</td>
                  </tr>`).join('')}</tbody>
                </table>
              </div>` : ''}
            </div>
          </details>` : ''}
          ${(bt.caveats || []).length ? `<div class="ms-notice warn" style="margin-top:10px;font-size:11.5px">${(bt.caveats || []).map(c => `<div>• ${E(c)}</div>`).join('')}</div>` : ''}
        </section>
      `}`;
  }

  function renderUnifiedWorkbenchTab(master) {
    const btOpts = state.backtestOpts;
    const advOpts = state.advisorOpts;
    const styles = [
      { key: 'wujiang_dynamic', name: '乌江望月 · 动态主线切换 + 浮盈加仓 + 极速纠错' },
      { key: 'huichanglan_mainline', name: '灰常懒 · 绝对主线半路 + 80%早盘15分秒砍' },
      { key: 'xiwangjiejie_1to2', name: '希望姐姐 · 纯主板一进二「克制与重复」' },
      { key: 'dongshizhang_highboard', name: '暴涨基金董事长 · 空仓狙击 2板+ 主板高标' },
      { key: 'ruhan_halfway', name: '如晗 · 分仓半路推土机 + 弱市 66% 降仓开关' },
      { key: 'songsong_defensive', name: '松雅湖的松松 · 强趋势低吸 + 弱市 90% 断电防守' },
    ];
    const sharedStyle = btOpts.styleKey || advOpts.styleKey || '';
    const sharedMaxPos = btOpts.maxPositions !== '' ? btOpts.maxPositions : (advOpts.maxPositions || '');
    const effStyleKey = inferStyleKeyFromMaster(master, sharedStyle);
    const defaultSlots = STYLE_DEFAULT_SLOTS[effStyleKey] || 4;
    const unifiedRules = state.advisor?.activeRules || state.backtest?.activeRules || null;
    const curTask = state.backtestTask;
    const isBtRunning = btTaskAlive(curTask);
    const taskPct = finite(curTask?.progress) ? Math.max(0, Math.min(100, Number(curTask.progress) * 100)) : 0;
    const syncBusy = state.advisorBusy || state.backtestBusy;

    return `
      <section class="panel ms-wb-console" data-ms-unified-workbench>
        <div class="panel-head">
          <div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <h2>高手实战推演与历史回测 · 合并同屏工作台</h2>
              ${(unifiedRules?.summaryBadges || []).map(b => `<span class="ms-rule-chip">${E(b)}</span>`).join('')}
            </div>
            <p class="ms-sub" style="margin:3px 0 0">统一一套策略、资金、持仓上限与风控纪律：左侧生成明日全场景作战方案，右侧同屏验证同参数历史净值、多仓数对比与模拟交割单。</p>
          </div>
          <div class="ms-toolbar-group">
            ${btn('2026熊市出清', 'bt-preset-range', 'data-start="2026-05-01" data-end="2026-11-25"')}
            ${btn('2022熊转震荡', 'bt-preset-range', 'data-start="2022-07-01" data-end="2023-02-01"')}
            ${btn('22-26跨周期', 'bt-preset-range', 'data-start="2022-07-01" data-end="2026-11-25"')}
            ${btn(syncBusy ? '正在同步测算…' : '同步推演并回测', 'run-backtest', syncBusy ? 'disabled' : '', 'analysis-tools-primary')}
            <button type="button" class="analysis-tools-button" data-nav="G02">任务中心 (G02)</button>
          </div>
        </div>

        <div class="ms-wb-param-grid">
          <label>高手策略模型（推演与回测共用）
            <select data-ms-bt-opt="styleKey">
              <option value="">自动匹配当前大佬 (${E(master?.name || '默认乌江望月')})</option>
              ${styles.map(s => `<option value="${E(s.key)}" ${sharedStyle === s.key ? 'selected' : ''}>${E(s.name)}</option>`).join('')}
            </select>
          </label>
          <label>最大持仓上限（只）
            <input type="number" min="1" max="20" step="1" data-ms-bt-opt="maxPositions" value="${E(sharedMaxPos)}" placeholder="默认 (${defaultSlots}只)">
          </label>
          <label>硬止损红线（%）
            <input type="number" min="-20" max="-1.5" step="0.5" data-ms-bt-opt="stopLossPct" value="${E(btOpts.stopLossPct)}">
          </label>
          <label>统一资金基准（元）
            <input type="number" min="10000" step="10000" data-ms-bt-opt="initialCapital" value="${E(btOpts.initialCapital)}">
          </label>
          <label>今日持仓来源 & 推演日
            <div class="ms-wb-inline-pair">
              <select data-ms-adv-opt="useCustomHoldings" aria-label="我的当前持仓数据来源">
                <option value="false" ${!advOpts.useCustomHoldings ? 'selected' : ''}>实盘账户持仓</option>
                <option value="true" ${advOpts.useCustomHoldings ? 'selected' : ''}>手动演练持仓</option>
              </select>
              <input type="date" data-ms-adv-opt="targetDate" value="${E(advOpts.targetDate)}" aria-label="今日收盘推演基准日" title="留空自动取最新交易日">
            </div>
          </label>
          <label>历史回测区间 & 样本源
            <div class="ms-wb-inline-range">
              <input type="date" data-ms-bt-opt="startDate" value="${E(btOpts.startDate)}" aria-label="回测开始日期">
              <span style="color:var(--color-disabled)">～</span>
              <input type="date" data-ms-bt-opt="endDate" value="${E(btOpts.endDate)}" aria-label="回测结束日期">
              <select data-ms-bt-opt="dataSource" aria-label="回测行情样本源" title="行情样本源">
                <option value="auto" ${btOpts.dataSource === 'auto' ? 'selected' : ''}>自动源</option>
                <option value="tdx" ${btOpts.dataSource === 'tdx' ? 'selected' : ''}>本地TDX</option>
              </select>
            </div>
          </label>
        </div>

        <div class="ms-wb-toggles-bar">
          <div class="ms-slot-pills" style="margin:0">
            <span style="color:var(--color-secondary);margin-right:2px">快捷持仓数：</span>
            ${[
              ['', `默认(${defaultSlots}只)`],
              ['1', '1只单吊'],
              ['2', '2只'],
              ['3', '3只'],
              ['4', '4只'],
              ['5', '5只'],
              ['6', '6只'],
              ['8', '8只'],
              ['10', '10只'],
            ].map(([val, lbl]) => `<button type="button" class="ms-slot-pill ${String(sharedMaxPos || '') === val ? 'active' : ''}" data-ms-action="bt-pick-slots" data-slots="${val}">${E(lbl)}</button>`).join('')}
          </div>
          <div class="ms-wb-checks">
            <label><input type="checkbox" data-ms-bt-chk="useRegimeSwitch" ${btOpts.useRegimeSwitch ? 'checked' : ''}> 四阶段总仓/退潮压仓</label>
            <label><input type="checkbox" data-ms-bt-chk="useFastWeakCut" ${btOpts.useFastWeakCut ? 'checked' : ''}> 炸板/破MA5快砍</label>
            <label><input type="checkbox" data-ms-bt-chk="useAddOnWin" ${btOpts.useAddOnWin ? 'checked' : ''}> 封板浮盈加仓</label>
            <label><input type="checkbox" data-ms-bt-chk="usePartialTrim" ${btOpts.usePartialTrim ? 'checked' : ''}> 冲高未板减半</label>
            <label style="color:var(--color-primary);font-weight:600"><input type="checkbox" data-ms-bt-chk="comparePositionCounts" ${btOpts.comparePositionCounts ? 'checked' : ''}> 同步测算1～10只曲线</label>
          </div>
        </div>

        ${advOpts.useCustomHoldings ? `
          <div class="ms-studio" style="padding:12px 14px;gap:10px">
            <div class="ms-wb-subhead">
              <div><strong>手动演练今日持仓（不影响实盘账本，录入即重算左侧明日方案）</strong></div>
              <div class="ms-toolbar-group">
                ${btn('一键填入 3 只经典演练持仓', 'adv-load-demo')}
                ${btn('清空持仓（按空仓推演）', 'adv-clear-custom')}
              </div>
            </div>
            <div class="ms-form-grid" style="grid-template-columns:160px 1fr">
              <label>可用现金（留空=总资金-市值）<input type="number" min="0" step="1000" data-ms-adv-opt="customCash" value="${E(advOpts.customCash)}" placeholder="自动计算"></label>
              <div class="ms-sim-row" style="align-self:end">
                <input type="text" data-ms-adv-opt="draftCode" value="${E(advOpts.draftCode)}" placeholder="6位代码 如 002086">
                <input type="text" data-ms-adv-opt="draftName" value="${E(advOpts.draftName)}" placeholder="名称(可选)">
                <input type="number" min="100" step="100" data-ms-adv-opt="draftQty" value="${E(advOpts.draftQty)}" placeholder="持仓股数">
                <input type="number" min="0.01" step="0.01" data-ms-adv-opt="draftCost" value="${E(advOpts.draftCost)}" placeholder="成本均价">
                ${btn('添加持仓并重算', 'adv-add-custom', '', 'analysis-tools-primary')}
              </div>
            </div>
            ${advOpts.customHoldings.length ? `<div class="ms-table-scroll"><table class="ms-table">
              <thead><tr><th>代码</th><th>名称</th><th class="ms-num">持仓股数</th><th class="ms-num">成本均价</th><th>操作</th></tr></thead>
              <tbody>${advOpts.customHoldings.map((h, i) => `<tr><td>${E(h.code)}</td><td>${E(h.name)}</td><td class="ms-num">${num(h.quantity, 0)}</td><td class="ms-num">${num(h.averageCost, 2)}</td><td>${btn('移除', 'adv-del-custom', `data-index="${i}"`)}</td></tr>`).join('')}</tbody>
            </table></div>` : ''}
          </div>
        ` : ''}

        ${curTask && (isBtRunning || curTask.status === 'paused' || curTask.status === 'stopped') ? `
          <div class="ms-wb-task-inline" role="status" aria-live="polite">
            <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:220px">
              <span class="ms-pill">${E(BT_STATUS_LABEL[curTask.status] || curTask.status)}</span>
              <strong>${E(curTask.name || '高手回测任务')}</strong>
              <span style="color:var(--color-secondary)">${E(curTask.phase || '正在后台执行…')}${isBtRunning ? ` (${num(taskPct, 0)}%)` : ''}</span>
              ${isBtRunning ? `<div class="ms-meter-bar" style="flex:1;max-width:220px"><div class="ms-meter-fill" style="width:${taskPct.toFixed(1)}%;background:var(--color-primary)"></div></div>` : ''}
            </div>
            <div class="ms-toolbar-group">
              ${isBtRunning ? btn('停止', 'bt-stop-task', `data-task-id="${E(curTask.id)}" ${curTask.status === 'stopping' ? 'disabled' : ''}`, 'analysis-tools-danger') : ''}
              ${curTask.status === 'paused' ? btn('继续', 'bt-resume-task', `data-task-id="${E(curTask.id)}"`, 'analysis-tools-primary') : ''}
            </div>
          </div>
        ` : ''}
      </section>

      ${renderWorkbenchOverviewStrip(state.advisor, state.backtest, isBtRunning)}

      <div class="ms-wb-split" data-ms-wb-split>
        <div class="ms-wb-col" data-ms-wb-left>
          ${renderTomorrowActionColumn(state.advisor)}
        </div>
        <div class="ms-wb-col" data-ms-wb-right>
          ${renderBacktestProofColumn(master, defaultSlots)}
        </div>
      </div>
    `;
  }

  // ── Main View ──────────────────────────────────────────────────────────────

  function view() {
    const srcPath = AT.marketUI?.status?.path || '';
    const srcLatest = AT.marketUI?.status?.latestDate || '';
    const sourceKey = srcPath ? JSON.stringify([srcPath, srcLatest]) : '';
    if (state.sourceKey && sourceKey && state.sourceKey !== sourceKey && state.activeId) {
      state.sourceKey = sourceKey;
      queueMicrotask(() => loadAnalysis(true));
    } else if (sourceKey) {
      state.sourceKey = sourceKey;
    }
    if (!state.loaded) queueMicrotask(() => refreshMasters());
    if (isWorkbenchTab(state.tab)) {
      if (!state.backtestTasksLoaded) queueMicrotask(() => loadBacktestTasks());
      if (!state.advisor && !state.advisorBusy && !state.advisorError) queueMicrotask(() => runAdvisor());
    }
    queueMicrotask(() => {
      if (AT.state.page !== 'T11') return;
      const label = document.querySelector('.demo-label'), status = document.querySelector('.statusbar>span:first-child');
      if (label) label.textContent = '本机 SQLite · 大佬交割单库';
      if (status) status.textContent = `大佬交割单数据库（共 ${state.masters.length} 位大佬） · 结合本地通达信日线/分时对齐`;
    });

    const master = activeMaster();
    const headActions = `
      ${btn(state.presetsBusy ? '载入高手库中…' : '载入顽主杯 6 大高手库', 'install-presets', state.presetsBusy ? 'disabled' : '', 'analysis-tools-primary')}
      ${btn('去策略运行 · 明日预测', 'goto-strategy-signals')}
      ${btn('去策略回测 · 同源验证', 'goto-strategy-backtest')}
      ${btn('新建大佬档案', 'new-master')}
      <label class="analysis-tools-button" style="cursor:pointer">导入数据库 JSON<input type="file" data-ms-import-db accept=".json" hidden></label>
      ${master ? btn(state.analysisBusy ? '对齐计算中…' : '刷新行情对齐', 'refresh-analysis', state.analysisBusy ? 'disabled' : '') : ''}
    `;

    const msgBar = state.message ? `<div class="ms-notice ${E(state.message.tone)}" role="status">${E(state.message.text)}</div>` : '';

    if (state.loaded && !state.masters.length && state.editorMode !== 'new-master') {
      return `<div class="ms-workspace" data-ms-page="T11">
        ${AT.viewParts.head('大佬交割单', '顽主杯 6 大实战高手实盘交割单数据库、战役复盘、深度操盘手册与 AI 逆向拆解归档', headActions)}
        ${msgBar}
        <section class="panel ms-empty-hero">
          <h2>一键载入「顽主杯 6 大顶级高手」实盘交割单数据库（5,715 笔）</h2>
          <p class="ms-sub">已内置 <strong>乌江望月（71-73届 1,838笔）、灰常懒（73届冠军）、希望姐姐（10届9红）、暴涨基金董事长（2个月5倍）、如晗、松松</strong> 共 5,715 笔经 K 线核验的真实交割单与操盘手册；对应的 6 套量化大佬策略已统一放入<strong>「策略与回测 → 策略运行 / 策略回测」</strong>同源执行。</p>
          <div class="ms-steps-strip">
            <div class="ms-step-box"><strong>01 · 实盘交割单数据库与推土机曲线</strong>独立建库保存每位高手的真实历史交割单，复盘环境择时、买点分位、分仓与止盈止损统计。</div>
            <div class="ms-step-box"><strong>02 · 策略运行 (R12) · 今日信号与明日预测</strong>在「策略运行」选择任一大佬策略，直接扫描本地通达信今日买点并生成明日持仓/开仓四场景预案。</div>
            <div class="ms-step-box"><strong>03 · 策略回测 (R03) · 100%同源历史回测</strong>在「策略回测」使用完全一致的信号公式、通达信股票池与情绪压仓/加减仓规则运行回测与收益平原。</div>
          </div>
          <div class="ms-toolbar-group">
            ${btn(state.presetsBusy ? '正在载入 6 大高手库…' : '一键载入顽主杯 6 大高手实盘库（推荐）', 'install-presets', state.presetsBusy ? 'disabled' : '', 'analysis-tools-primary')}
            ${btn('前往策略运行 · 明日预测 (R12) →', 'goto-strategy-signals')}
            ${btn('前往策略回测 · 历史验证 (R03) →', 'goto-strategy-backtest')}
            ${btn('新建空白大佬档案', 'new-master')}
          </div>
        </section>
      </div>`;
    }

    const libraryBar = `<section class="panel ms-panel">
      <div class="ms-library-bar">
        ${state.masters.map(m => `<button type="button" class="ms-master-card ${m.id === state.activeId ? 'active' : ''}" data-ms-select="${E(m.id)}">
          <div class="ms-master-card-top"><strong title="${E(m.name)}">${E(m.name)}</strong>${m.styleTag ? `<span class="ms-tag" title="${E(m.styleTag)}">${E(m.styleTag)}</span>` : ''}</div>
          <div class="ms-master-card-meta"><span>${m.stats?.tradeCount || 0} 笔成交</span><span>${m.stats?.uniqueStocks || 0} 只股</span><span>${m.stats?.reportCount || 0} 份研报</span></div>
          <div class="ms-master-card-dates">${m.stats?.firstDate ? `${E(m.stats.firstDate)} — ${E(m.stats.lastDate)}` : '尚未导入交割单'}</div>
        </button>`).join('')}
      </div>
      ${master ? `<div class="ms-toolbar">
        <div class="ms-toolbar-group">
          ${btn(`用「${master.name}」跑策略运行/明日预测 →`, 'goto-strategy-signals', '', 'analysis-tools-primary')}
          ${btn(`用「${master.name}」跑策略回测 →`, 'goto-strategy-backtest')}
          ${btn('导入交割单截图 / 表格', 'open-import')}
          ${btn('编辑大佬档案', 'edit-master')}
          ${btn('导出数据库 JSON', 'export-db')}
          ${state.confirmDeleteMaster
            ? `${btn('确认永久删除该大佬库', 'delete-master-confirm', '', 'analysis-tools-danger')} ${btn('取消', 'delete-master-cancel')}`
            : btn('删除档案', 'delete-master-ask', '', 'analysis-tools-danger')}
        </div>
        <span style="font-size:12px;color:var(--color-secondary)">${master.sourceDesc ? `来源：${E(master.sourceDesc)}` : '数据库：masters.sqlite3'}</span>
      </div>` : ''}
    </section>`;

    let body = '';
    if (master) {
      if (isWorkbenchTab(state.tab)) state.tab = 'overview';
      const a = state.analysis;
      const warningsBar = a?.warnings?.length ? `<div class="ms-notice warn">${a.warnings.map(w => `<div>• ${E(w)}</div>`).join('')}</div>` : '';
      const tabsBar = `<div class="ms-tabs">
        ${[
          ['overview', '总览与推土机曲线'],
          ['articles', `深度文章与手册 (${state.reports.length})`],
          ['environment', '市场环境与择时'],
          ['selection', '选股与买点解构'],
          ['position', '分仓加减仓与止盈止损'],
          ['coach', 'AI导师与非盯盘转化'],
          ['database', `交割单数据库 (${master.stats?.tradeCount || 0})`],
        ].map(([id, label]) => {
          return `<button type="button" class="ms-tab ${state.tab === id ? 'active' : ''}" data-ms-tab="${id}">${E(label)}</button>`;
        }).join('')}
      </div>`;

      let tabContent = '';
      if (state.tab === 'database') {
        tabContent = renderDatabaseTab(master);
      } else if (state.tab === 'articles') {
        tabContent = renderArticlesTab(master);
      } else if (state.analysisBusy && !a) {
        tabContent = '<div class="ms-notice">正在对齐本地通达信/内置历史日线、计算全市场情绪周期与收益曲线，请稍候…</div>';
      } else if (state.analysisError) {
        tabContent = `<div class="ms-notice err">${E(state.analysisError)} ${btn('重试分析', 'refresh-analysis')}</div>`;
      } else if (!a || a.empty) {
        tabContent = `<section class="panel ms-panel"><div class="ms-notice">「${E(master.name)}」数据库中还没有成交记录。点击上方 <strong>「导入交割单截图 / 表格」</strong> 上传几张大佬交割单截图即可开始全自动解构！</div></section>`;
      } else {
        tabContent = state.tab === 'overview' ? renderOverviewTab(a)
          : state.tab === 'environment' ? renderEnvironmentTab(a)
          : state.tab === 'selection' ? renderSelectionTab(a)
          : state.tab === 'position' ? renderPositionExitTab(a)
          : renderCoachTab(a);
      }
      body = `${tabsBar}${warningsBar}${tabContent}`;
    }

    return `<div class="ms-workspace" data-ms-page="T11">
      ${AT.viewParts.head('大佬交割单', '按大佬独立建库保存交割单，结合本地通达信行情反推选股、分仓、加减仓与止盈止损纪律', headActions)}
      ${msgBar}
      ${(state.editorMode === 'new-master' || state.editorMode === 'edit-master') ? renderMasterForm() : ''}
      ${state.masters.length ? libraryBar : ''}
      ${master && state.editorMode === 'import' ? renderImportStudio(master) : ''}
      ${body}
    </div>`;
  }

  // Register page renderer
  const prevView = AT.view;
  AT.view = function (page) { return page === 'T11' ? view() : prevView(page); };

  // ── Actions & Event Handling ───────────────────────────────────────────────

  async function handleAddImages(files) {
    const list = [...(files || [])].filter(f => f && f.type?.startsWith('image/'));
    if (!list.length) return;
    if (list.length + state.imp.images.length > 8) {
      setMessage('单批最多选择 8 张交割单截图。', 'err'); render(); return;
    }
    try {
      for (const file of list) {
        if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name || '图片'} 超过 5 MiB 限制`);
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw new Error('仅支持 PNG、JPG 或 WebP 格式截图');
        const dataBase64 = await fileToBase64(file);
        state.imp.images.push({ name: file.name || `粘贴截图-${state.imp.images.length + 1}.png`, mimeType: file.type, dataBase64 });
      }
      setMessage(`已添加 ${list.length} 张交割单截图，点击「发送截图并识别」提取成交明细。`, 'ok');
    } catch (error) {
      setMessage(errText(error), 'err');
    }
    render();
  }

  async function runImageOCR() {
    const master = activeMaster();
    if (!master || !state.imp.images.length || state.imp.busy) return;
    state.imp.busy = true;
    state.imp.warnings = [];
    setMessage('正在发送交割单截图给图片 AI 识别，请稍候…', 'ok');
    render();
    try {
      const ocr = await callAI('master.recognize', {
        images: state.imp.images,
        defaultDate: state.imp.defaultDate || undefined,
        defaultYear: state.imp.defaultYear || undefined,
      });
      state.imp.inputHash = ocr.inputHash || '';
      state.imp.source = 'ocr';
      const parsed = await callMarket('market.masters.import.parse', {
        masterId: master.id,
        source: 'ocr',
        rows: ocr.rows || [],
      });
      state.imp.rows = parsed.rows || [];
      state.imp.warnings = [...new Set([...(ocr.warnings || []), ...(parsed.warnings || [])])];
      state.imp.reviewed = false;
      setMessage(`识别完成：共提取 ${state.imp.rows.length} 笔成交（有效 ${parsed.summary?.valid || 0} 笔，重复 ${parsed.summary?.duplicates || 0} 笔）。请核对后写入数据库。`, 'ok');
    } catch (error) {
      setMessage(errText(error), 'err');
    } finally {
      state.imp.busy = false;
      render();
    }
  }

  async function handleFileUpload(file) {
    const master = activeMaster();
    if (!master || !file) return;
    state.imp.busy = true;
    state.imp.fileName = file.name;
    render();
    try {
      const contentBase64 = await fileToBase64(file);
      const parsed = await callMarket('market.masters.import.parse', {
        masterId: master.id,
        filename: file.name,
        contentBase64,
      });
      state.imp.source = parsed.source || 'csv';
      state.imp.rows = parsed.rows || [];
      state.imp.warnings = parsed.warnings || [];
      state.imp.reviewed = false;
      setMessage(`文件解析完成：共 ${state.imp.rows.length} 笔成交，请核对后写入数据库。`, 'ok');
    } catch (error) {
      setMessage(errText(error), 'err');
    } finally {
      state.imp.busy = false;
      render();
    }
  }

  async function revalidateImportRows() {
    const master = activeMaster();
    if (!master || !state.imp.rows.length) return;
    state.imp.busy = true;
    render();
    try {
      const parsed = await callMarket('market.masters.import.parse', {
        masterId: master.id,
        source: state.imp.source,
        rows: state.imp.rows.map(r => r.record),
      });
      state.imp.rows = parsed.rows || [];
      setMessage(`已重新校验：有效 ${parsed.summary?.valid || 0} 笔，待修 ${parsed.summary?.invalid || 0} 笔。`, parsed.summary?.invalid ? 'warn' : 'ok');
    } catch (error) {
      setMessage(errText(error), 'err');
    } finally {
      state.imp.busy = false;
      render();
    }
  }

  async function commitImportRows() {
    const master = activeMaster();
    if (!master || state.imp.busy) return;
    const chosen = state.imp.rows.filter(r => r.selected && (!r.errors || !r.errors.length)).map(r => r.record);
    if (!chosen.length) { setMessage('请至少勾选一条无错误的记录。', 'err'); render(); return; }
    state.imp.busy = true;
    render();
    try {
      const res = await callMarket('market.masters.import.commit', {
        masterId: master.id,
        source: state.imp.source,
        inputHash: state.imp.inputHash,
        imageCount: state.imp.images.length,
        rows: chosen,
      }, true);
      resetImportState();
      state.editorMode = '';
      setMessage(`已成功写入 ${res.inserted} 笔交割单到「${master.name}」数据库${res.skipped ? `（跳过重复 ${res.skipped} 笔）` : ''}，正在自动对齐本地行情…`, 'ok');
      await refreshMasters(master.id);
    } catch (error) {
      setMessage(errText(error), 'err');
    } finally {
      state.imp.busy = false;
      render();
    }
  }

  function compactAnalysisForAI(a, episodeId = '') {
    const ep = episodeId ? (a.episodes || []).find(e => e.id === episodeId) : null;
    return {
      dateRange: a.dateRange,
      summary: a.summary,
      coverage: a.coverage,
      environment: a.environment,
      selection: a.selection,
      position: a.position,
      exits: a.exits,
      guidance: a.guidance,
      representativeCases: a.representativeCases,
      selfComparison: a.selfComparison || null,
      selectedEpisode: ep || null,
      sampleBuys: (a.enrichedTrades || []).filter(t => t.side === 'buy').slice(0, 20).map(t => ({
        date: t.tradeDate, time: t.tradeTime, code: t.code, name: t.name, legRole: t.legRole,
        price: t.price, buyVsPrevClosePct: t.buyVsPrevClosePct, intradayPricePos: t.intradayPricePos,
        entryPattern: t.entryPatternLabel, marketStage: t.marketEnv?.stageLabel,
        amountRankPct: t.crossSection?.amountRankPct, priorHeight: t.bar?.priorBoardHeight,
        next1OpenPct: t.bar?.next1OpenPct, next1ClosePct: t.bar?.next1ClosePct,
      })),
    };
  }

  async function runAIStudy(kind, episodeId = '') {
    const master = activeMaster();
    const a = state.analysis;
    if (!master || !a || a.empty || state.aiBusy) return;
    state.aiBusy = true;
    state.tab = 'coach';
    setMessage('AI 交易导师正在深度解构大佬交割单数据，请稍候…', 'ok');
    render();
    try {
      const digest = compactAnalysisForAI(a, episodeId);
      const result = await callAI('master.study', {
        kind,
        masterName: master.name,
        styleTag: master.styleTag,
        analysis: digest,
      });
      const saved = await callMarket('market.masters.reports.save', {
        masterId: master.id,
        kind: result.kind,
        title: episodeId && digest.selectedEpisode
          ? `${master.name} · 战役剖析 ${digest.selectedEpisode.name}(${digest.selectedEpisode.entryDate})`
          : result.title,
        content: result.content,
        model: result.model,
        meta: { generatedAt: result.generatedAt, promptVersion: result.promptVersion, episodeId: episodeId || null },
      }, true);
      state.activeReportId = saved.report.id;
      setMessage('AI 深度解构报告已生成并永久保存到该大佬数据库！', 'ok');
      await Promise.all([loadReports(), refreshMasters(master.id)]);
    } catch (error) {
      setMessage(errText(error), 'err');
    } finally {
      state.aiBusy = false;
      render();
    }
  }

  async function runSelfCompare() {
    const master = activeMaster();
    if (!master || state.compareBusy) return;
    state.compareBusy = true;
    render();
    try {
      const data = await callMarket('market.masters.compareSelf', { masterId: master.id });
      state.analysis = data;
      setMessage('已完成与你的本地实盘账户纪律对比！', 'ok');
    } catch (error) {
      setMessage(errText(error), 'err');
    } finally {
      state.compareBusy = false;
      render();
    }
  }

  // Click delegation
  document.addEventListener('click', event => {
    if (AT.state.page !== 'T11') return;

    const stockEl = event.target.closest('[data-ms-stock]');
    if (stockEl) {
      event.preventDefault(); event.stopImmediatePropagation();
      const sym = stockEl.dataset.msStock, day = stockEl.dataset.msDay || undefined;
      if (AT.stockResearch?.openFromMaster) {
        AT.stockResearch.openFromMaster(sym, day, {
          tab: state.tab,
          masterId: state.activeId || '',
          backtestTaskId: state.activeBacktestTaskId || '',
          styleKey: state.backtest?.styleKey || state.advisor?.styleKey || state.backtestOpts.styleKey || '',
        });
      } else {
        AT.marketUI?.openDay(sym, day);
      }
      return;
    }
    const masterEl = event.target.closest('[data-ms-select]');
    if (masterEl) { event.preventDefault(); selectMaster(masterEl.dataset.msSelect); return; }

    const tabEl = event.target.closest('[data-ms-tab]');
    if (tabEl) {
      event.preventDefault();
      if (tabEl.dataset.msTab === 'advisor') {
        jumpToStrategyWorkspace('signals', activeMaster());
        return;
      }
      if (tabEl.dataset.msTab === 'backtest') {
        jumpToStrategyWorkspace('backtest', activeMaster());
        return;
      }
      state.tab = tabEl.dataset.msTab;
      render();
      return;
    }

    const planSecEl = event.target.closest('[data-ms-plan-sec]');
    if (planSecEl) {
      event.preventDefault();
      state.planSection = planSecEl.dataset.msPlanSec || 'all';
      render();
      return;
    }

    const impTabEl = event.target.closest('[data-ms-imptab]');
    if (impTabEl) { event.preventDefault(); state.imp.tab = impTabEl.dataset.msImptab; render(); return; }

    const repEl = event.target.closest('[data-ms-report]');
    if (repEl && !event.target.closest('[data-ms-action]')) {
      event.preventDefault(); state.activeReportId = repEl.dataset.msReport; render(); return;
    }

    const pageEl = event.target.closest('button[data-ms-pagenav]');
    if (pageEl && !pageEl.disabled) {
      event.preventDefault();
      const [scope, pStr] = String(pageEl.dataset.msPagenav || '').split(':');
      const p = Math.max(1, Number(pStr) || 1);
      if (scope === 'bt') { state.btTradePage = p; render(); }
      else if (scope === 'db') { state.tradesFilter.page = p; loadTrades(); }
      else if (scope === 'sel') { state.selBuyPage = p; render(); }
      else if (scope === 'ep') { state.posEpPage = p; render(); }
      return;
    }

    const cmodeEl = event.target.closest('[data-ms-bt-cmode]');
    if (cmodeEl) {
      event.preventDefault();
      state.btCurveMode = cmodeEl.dataset.msBtCmode || 'return';
      render();
      return;
    }

    const ptEl = event.target.closest('[data-ms-bt-pt]');
    if (ptEl) {
      event.preventDefault();
      const idx = Number(ptEl.dataset.msBtPt);
      const curve = normalizeBtCurve(state.backtest);
      if (Number.isFinite(idx) && idx >= 0 && idx < curve.length) {
        state.btCurveIndex = idx;
        const d = curve[idx].date;
        if ((state.backtest?.positionTimeline || []).some(item => item.date === d)) {
          state.btSnapshotDate = d;
          state.btReplayOpen = true;
        }
        render();
      }
      return;
    }

    const btFilterEl = event.target.closest('[data-ms-bt-tfilter]');
    if (btFilterEl) {
      event.preventDefault();
      state.btTradeFilter = btFilterEl.dataset.msBtTfilter || 'all';
      state.btTradePage = 1;
      render();
      return;
    }

    const actionEl = event.target.closest('[data-ms-action]');
    if (!actionEl || actionEl.disabled) return;
    event.preventDefault();
    const action = actionEl.dataset.msAction;

    if (action === 'install-presets') { installPresets(); return; }
    if (action === 'goto-strategy-signals' || action === 'open-tab-advisor') {
      jumpToStrategyWorkspace('signals', activeMaster());
      return;
    }
    if (action === 'goto-strategy-backtest' || action === 'open-tab-backtest') {
      jumpToStrategyWorkspace('backtest', activeMaster());
      return;
    }
    if (action === 'open-tab-overview') { state.tab = 'overview'; render(); return; }
    if (action === 'bt-pick-slots' || action === 'adv-pick-slots') {
      const slotsVal = actionEl.dataset.slots ?? '';
      state.backtestOpts.maxPositions = slotsVal;
      state.advisorOpts.maxPositions = slotsVal;
      void runAdvisor();
      return;
    }
    if (action === 'bt-apply-slot-run') {
      const slotsVal = actionEl.dataset.slots ?? '';
      state.backtestOpts.maxPositions = slotsVal;
      state.advisorOpts.maxPositions = slotsVal;
      runWorkbenchSync();
      return;
    }
    if (action === 'bt-curve-prev' || action === 'bt-curve-next') {
      const curve = normalizeBtCurve(state.backtest);
      if (curve.length) {
        const cur = state.btCurveIndex >= 0 && state.btCurveIndex < curve.length ? state.btCurveIndex : curve.length - 1;
        const nextIdx = Math.max(0, Math.min(curve.length - 1, cur + (action === 'bt-curve-prev' ? -1 : 1)));
        state.btCurveIndex = nextIdx;
        const d = curve[nextIdx].date;
        if ((state.backtest?.positionTimeline || []).some(item => item.date === d)) {
          state.btSnapshotDate = d;
          state.btReplayOpen = true;
        }
        render();
      }
      return;
    }
    if (action === 'bt-reset-tfilter') {
      state.btTradeFilter = 'all';
      state.btTradeQuery = '';
      state.btTradePage = 1;
      render();
      return;
    }
    if (action === 'run-advisor') { runAdvisor(); return; }
    if (action === 'run-both') { runWorkbenchSync(); return; }
    if (action === 'adv-open-custom') {
      state.advisorOpts.useCustomHoldings = true;
      render();
      return;
    }
    if (action === 'adv-load-demo') {
      state.advisorOpts.useCustomHoldings = true;
      state.advisorOpts.targetDate = state.advisorOpts.targetDate || '2026-11-12';
      state.advisorOpts.customEquity = state.backtestOpts.initialCapital || '100000';
      state.advisorOpts.customCash = '';
      state.advisorOpts.customHoldings = [
        { code: '002086', name: '东方海洋', quantity: 5000, averageCost: 4.20 },
        { code: '688981', name: '中芯国际', quantity: 200, averageCost: 105.0 },
        { code: '002827', name: '高争民爆', quantity: 400, averageCost: 75.84 },
      ];
      runAdvisor(); return;
    }
    if (action === 'adv-clear-custom') { state.advisorOpts.customHoldings = []; runAdvisor(); return; }
    if (action === 'adv-add-custom') {
      const o = state.advisorOpts;
      const code = String(o.draftCode || '').replace(/\D/g, '').slice(0, 6);
      const qty = Number(o.draftQty || 0);
      const cost = Number(o.draftCost || 0);
      if (code.length !== 6 || qty <= 0 || cost <= 0) {
        setMessage('请填写正确的 6 位股票代码、持仓股数与成本价。', 'err'); render(); return;
      }
      o.customHoldings.push({ code, name: (o.draftName || '').trim() || code, quantity: qty, averageCost: cost });
      o.draftCode = ''; o.draftName = ''; o.draftCost = '';
      setMessage('已添加持仓，正在按今日收盘日线重算明日全场景作战计划…', 'ok');
      runAdvisor(); return;
    }
    if (action === 'adv-del-custom') {
      state.advisorOpts.customHoldings.splice(Number(actionEl.dataset.index), 1);
      runAdvisor(); return;
    }
    if (action === 'run-backtest') { runWorkbenchSync(); return; }
    if (action === 'bt-select-task') {
      const tid = actionEl.dataset.taskId;
      if (tid) {
        state.activeBacktestTaskId = tid;
        state.backtestTask = state.backtestTasks.find(t => t.id === tid) || null;
        void loadBacktestTaskDetail(tid);
      }
      return;
    }
    if (action === 'bt-stop-task') { void controlBacktestTask(actionEl.dataset.taskId, 'stop'); return; }
    if (action === 'bt-resume-task') { void controlBacktestTask(actionEl.dataset.taskId, 'resume'); return; }
    if (action === 'bt-delete-task') { void deleteBacktestTask(actionEl.dataset.taskId); return; }
    if (action === 'bt-refresh-tasks') { void loadBacktestTasks(state.activeBacktestTaskId); return; }
    if (action === 'bt-preset-range') {
      state.backtestOpts.startDate = actionEl.dataset.start || '2026-05-01';
      state.backtestOpts.endDate = actionEl.dataset.end || '2026-11-25';
      runWorkbenchSync(); return;
    }
    if (action === 'new-master') {
      state.editorMode = 'new-master';
      state.masterDraft = { id: '', name: '', styleTag: '', initialCapital: '', sourceDesc: '', notes: '' };
      render(); return;
    }
    if (action === 'edit-master') {
      const m = activeMaster(); if (!m) return;
      state.editorMode = 'edit-master';
      state.masterDraft = { id: m.id, name: m.name, styleTag: m.styleTag || '', initialCapital: m.initialCapital ?? '', sourceDesc: m.sourceDesc || '', notes: m.notes || '' };
      render(); return;
    }
    if (action === 'close-editor') { state.editorMode = ''; render(); return; }
    if (action === 'open-import') {
      state.editorMode = state.editorMode === 'import' ? '' : 'import';
      state.tradeDraft = { id: '', tradeDate: today(), tradeTime: '', side: 'buy', code: '', name: '', quantity: '', price: '', amount: '', fees: '', positionRatio: '', accountEquity: '', note: '' };
      render(); return;
    }
    if (action === 'refresh-analysis') { loadAnalysis(true); return; }
    if (action === 'save-master') {
      (async () => {
        state.busy = true; render();
        try {
          const d = state.masterDraft;
          const res = await callMarket('market.masters.save', {
            id: d.id || undefined,
            name: d.name,
            styleTag: d.styleTag,
            initialCapital: d.initialCapital === '' ? null : Number(d.initialCapital),
            sourceDesc: d.sourceDesc,
            notes: d.notes,
          }, true);
          state.editorMode = '';
          setMessage(`大佬档案「${res.master.name}」已保存到本机数据库。`, 'ok');
          await refreshMasters(res.master.id);
        } catch (error) { setMessage(errText(error), 'err'); }
        finally { state.busy = false; render(); }
      })(); return;
    }
    if (action === 'delete-master-ask') { state.confirmDeleteMaster = true; render(); return; }
    if (action === 'delete-master-cancel') { state.confirmDeleteMaster = false; render(); return; }
    if (action === 'delete-master-confirm') {
      (async () => {
        const m = activeMaster(); if (!m) return;
        state.busy = true; render();
        try {
          await callMarket('market.masters.delete', { id: m.id }, true);
          state.confirmDeleteMaster = false;
          state.activeId = '';
          setMessage(`已删除「${m.name}」的交割单数据库。`, 'ok');
          await refreshMasters();
        } catch (error) { setMessage(errText(error), 'err'); }
        finally { state.busy = false; render(); }
      })(); return;
    }
    if (action === 'export-db') {
      (async () => {
        const m = activeMaster(); if (!m) return;
        try {
          const bundle = await callMarket('market.masters.export', { masterId: m.id });
          downloadJSON(`A-trade-大佬库-${m.name}-${today()}.json`, bundle);
          setMessage(`已导出「${m.name}」完整数据库（含成交与研报）。`, 'ok');
        } catch (error) { setMessage(errText(error), 'err'); }
        render();
      })(); return;
    }
    if (action === 'imp-remove-img') {
      state.imp.images.splice(Number(actionEl.dataset.index), 1); render(); return;
    }
    if (action === 'imp-clear-imgs') { state.imp.images = []; render(); return; }
    if (action === 'imp-clear') { state.imp.rows = []; state.imp.warnings = []; state.imp.reviewed = false; render(); return; }
    if (action === 'imp-select-valid') {
      for (const r of state.imp.rows) r.selected = !(r.errors && r.errors.length);
      render(); return;
    }
    if (action === 'imp-recognize') { runImageOCR(); return; }
    if (action === 'imp-revalidate') { revalidateImportRows(); return; }
    if (action === 'imp-commit') { commitImportRows(); return; }
    if (action === 'save-trade') {
      (async () => {
        const m = activeMaster(); if (!m) return;
        state.busy = true; render();
        try {
          const d = state.tradeDraft;
          await callMarket('market.masters.trades.save', {
            masterId: m.id,
            id: d.id || undefined,
            tradeDate: d.tradeDate,
            tradeTime: d.tradeTime,
            side: d.side,
            code: d.code,
            name: d.name,
            quantity: d.quantity,
            price: d.price,
            amount: d.amount || undefined,
            fees: d.fees || 0,
            positionRatio: d.positionRatio || undefined,
            accountEquity: d.accountEquity || undefined,
            note: d.note,
          }, true);
          state.tradeDraft = { id: '', tradeDate: d.tradeDate, tradeTime: '', side: 'buy', code: '', name: '', quantity: '', price: '', amount: '', fees: '', positionRatio: '', accountEquity: '', note: '' };
          setMessage('成交记录已写入数据库，正在刷新对齐分析…', 'ok');
          await refreshMasters(m.id);
        } catch (error) { setMessage(errText(error), 'err'); }
        finally { state.busy = false; render(); }
      })(); return;
    }
    if (action === 'edit-trade') {
      const tid = actionEl.dataset.tradeId;
      const item = (state.tradesData?.items || []).find(t => t.id === tid);
      if (!item) return;
      state.editorMode = 'import';
      state.imp.tab = 'manual';
      state.tradeDraft = {
        id: item.id, tradeDate: item.tradeDate, tradeTime: item.tradeTime || '', side: item.side,
        code: item.code, name: item.name, quantity: item.quantity, price: item.price,
        amount: item.amount, fees: item.fees || '', positionRatio: item.positionRatio ?? '',
        accountEquity: item.accountEquity ?? '', note: item.note || '',
      };
      render(); return;
    }
    if (action === 'delete-trade') {
      (async () => {
        const m = activeMaster(); if (!m) return;
        try {
          await callMarket('market.masters.trades.delete', { masterId: m.id, ids: [actionEl.dataset.tradeId] }, true);
          setMessage('已删除该笔交割单记录。', 'ok');
          await refreshMasters(m.id);
        } catch (error) { setMessage(errText(error), 'err'); render(); }
      })(); return;
    }
    if (action === 'delete-batch') {
      (async () => {
        const m = activeMaster(); if (!m) return;
        try {
          const res = await callMarket('market.masters.trades.delete', { masterId: m.id, batchId: actionEl.dataset.batchId }, true);
          setMessage(`已撤销该导入批次（共移除 ${res.deleted} 笔记录）。`, 'ok');
          await refreshMasters(m.id);
        } catch (error) { setMessage(errText(error), 'err'); render(); }
      })(); return;
    }
    if (action === 'apply-tfilter') { state.tradesFilter.page = 1; loadTrades(); return; }
    if (action === 'compare-self') { runSelfCompare(); return; }
    if (action === 'ai-study') { runAIStudy(actionEl.dataset.kind || 'playbook'); return; }
    if (action === 'ai-study-episode') { runAIStudy('trade_deep_dive', actionEl.dataset.episodeId || ''); return; }
    if (action === 'delete-report') {
      (async () => {
        const m = activeMaster(); if (!m) return;
        try {
          await callMarket('market.masters.reports.delete', { masterId: m.id, id: actionEl.dataset.reportId }, true);
          setMessage('已删除该份 AI 研报。', 'ok');
          await Promise.all([loadReports(), refreshMasters(m.id)]);
        } catch (error) { setMessage(errText(error), 'err'); render(); }
      })(); return;
    }
  });

  // Input / Change bindings
  document.addEventListener('input', event => {
    if (AT.state.page !== 'T11') return;
    const t = event.target;
    if (t.matches?.('[data-ms-bt-tquery]')) {
      const pos = typeof t.selectionStart === 'number' ? t.selectionStart : null;
      state.btTradeQuery = t.value;
      state.btTradePage = 1;
      render();
      const nextInput = document.querySelector('[data-ms-bt-tquery]');
      if (nextInput) {
        nextInput.focus();
        try { if (pos !== null) nextInput.setSelectionRange(pos, pos); } catch {}
      }
      return;
    }
    if (t.dataset.msMfield) { state.masterDraft[t.dataset.msMfield] = t.value; return; }
    if (t.dataset.msTfield) { state.tradeDraft[t.dataset.msTfield] = t.value; return; }
    if (t.dataset.msTfilter) { state.tradesFilter[t.dataset.msTfilter] = t.value; return; }
    if (t.dataset.msImpOpt) { state.imp[t.dataset.msImpOpt] = t.value; return; }
    if (t.dataset.msAdvOpt) {
      const k = t.dataset.msAdvOpt;
      state.advisorOpts[k] = k === 'useCustomHoldings' ? (t.value === 'true') : t.value;
      if (k === 'maxPositions') state.backtestOpts.maxPositions = t.value;
      if (k === 'customEquity') state.backtestOpts.initialCapital = t.value;
      if (k === 'useCustomHoldings') render();
      return;
    }
    if (t.dataset.msBtOpt) {
      const k = t.dataset.msBtOpt;
      state.backtestOpts[k] = t.value;
      if (k === 'styleKey') state.advisorOpts.styleKey = t.value;
      if (k === 'maxPositions') state.advisorOpts.maxPositions = t.value;
      if (k === 'initialCapital') state.advisorOpts.customEquity = t.value;
      return;
    }
    if (t.dataset.msImpCell) {
      const [idxStr, field] = t.dataset.msImpCell.split(':');
      const row = state.imp.rows[Number(idxStr)];
      if (row?.record) row.record[field] = t.value;
    }
  });

  document.addEventListener('keydown', event => {
    if (AT.state.page !== 'T11') return;
    if (event.key === 'Enter' && event.target?.dataset?.msTfilter === 'query') {
      event.preventDefault();
      state.tradesFilter.page = 1;
      loadTrades();
    }
  });

  document.addEventListener('change', event => {
    if (AT.state.page !== 'T11') return;
    const t = event.target;
    if (t.matches('[data-ms-images]')) { handleAddImages(t.files); t.value = ''; return; }
    if (t.matches('[data-ms-file]')) { if (t.files?.[0]) handleFileUpload(t.files[0]); t.value = ''; return; }
    if (t.matches('[data-ms-import-db]')) {
      const file = t.files?.[0]; t.value = '';
      if (!file) return;
      (async () => {
        try {
          const text = await file.text();
          const bundle = JSON.parse(text);
          const res = await callMarket('market.masters.importDb', { bundle }, true);
          setMessage(`已导入大佬库「${res.master.name}」（${res.insertedTrades} 笔成交，${res.insertedReports} 份研报）。`, 'ok');
          await refreshMasters(res.master.id);
        } catch (error) { setMessage(errText(error), 'err'); render(); }
      })();
      return;
    }
    if (t.dataset.msPagesize) {
      const scope = t.dataset.msPagesize;
      const sz = Math.max(10, Math.min(200, Number(t.value) || 20));
      if (scope === 'bt') { state.btTradePageSize = sz; state.btTradePage = 1; render(); }
      else if (scope === 'db') { state.tradesFilter.pageSize = sz; state.tradesFilter.page = 1; loadTrades(); }
      return;
    }
    if (t.dataset.msPagejump) {
      const scope = t.dataset.msPagejump;
      const p = Math.max(1, Number(t.value) || 1);
      if (scope === 'bt') { state.btTradePage = p; render(); }
      else if (scope === 'db') { state.tradesFilter.page = p; loadTrades(); }
      else if (scope === 'sel') { state.selBuyPage = p; render(); }
      else if (scope === 'ep') { state.posEpPage = p; render(); }
      return;
    }
    if (t.matches('[data-ms-bt-tsort]')) {
      state.btTradeSort = t.value || 'desc';
      state.btTradePage = 1;
      render();
      return;
    }
    if (t.dataset.msImpSelect !== undefined) {
      const row = state.imp.rows[Number(t.dataset.msImpSelect)];
      if (row) { row.selected = t.checked; render(); }
      return;
    }
    if (t.matches('[data-ms-imp-reviewed]')) { state.imp.reviewed = t.checked; render(); return; }
    if (t.matches('[data-ms-bt-task-switch]')) {
      const tid = t.value;
      if (tid) {
        state.activeBacktestTaskId = tid;
        state.backtestTask = state.backtestTasks.find(x => x.id === tid) || null;
        void loadBacktestTaskDetail(tid);
      }
      return;
    }
    if (t.dataset.msAdvOpt) {
      const k = t.dataset.msAdvOpt;
      state.advisorOpts[k] = k === 'useCustomHoldings' ? (t.value === 'true') : t.value;
      if (k === 'styleKey') state.backtestOpts.styleKey = t.value;
      if (k === 'maxPositions') state.backtestOpts.maxPositions = t.value;
      if (k === 'customEquity') state.backtestOpts.initialCapital = t.value;
      if (['useCustomHoldings', 'styleKey', 'targetDate', 'maxPositions', 'customEquity', 'customCash'].includes(k)) void runAdvisor();
      return;
    }
    if (t.dataset.msBtOpt) {
      const k = t.dataset.msBtOpt;
      state.backtestOpts[k] = t.value;
      if (k === 'styleKey') state.advisorOpts.styleKey = t.value;
      if (k === 'maxPositions') state.advisorOpts.maxPositions = t.value;
      if (k === 'initialCapital') state.advisorOpts.customEquity = t.value;
      if (['styleKey', 'maxPositions', 'stopLossPct', 'initialCapital'].includes(k)) void runAdvisor();
      else render();
      return;
    }
    if (t.dataset.msBtChk) {
      const k = t.dataset.msBtChk;
      state.backtestOpts[k] = t.checked;
      if (['useRegimeSwitch', 'useFastWeakCut', 'useAddOnWin', 'usePartialTrim'].includes(k)) void runAdvisor();
      return;
    }
    if (t.matches('[data-ms-bt-snapdate]')) { state.btSnapshotDate = t.value; state.btReplayOpen = true; render(); return; }
    if (t.dataset.msTfilter === 'side' || t.dataset.msTfilter === 'dateFrom' || t.dataset.msTfilter === 'dateTo') {
      state.tradesFilter[t.dataset.msTfilter] = t.value;
      state.tradesFilter.page = 1;
      loadTrades();
    }
  });

  // Ctrl+V screenshot paste when import studio is open on T11
  document.addEventListener('paste', event => {
    if (AT.state.page !== 'T11' || state.editorMode !== 'import' || state.imp.tab !== 'images') return;
    const items = [...(event.clipboardData?.items || [])].filter(i => i.kind === 'file' && i.type.startsWith('image/'));
    if (!items.length) return;
    event.preventDefault();
    handleAddImages(items.map(i => i.getAsFile()).filter(Boolean));
  });

  AT.masterSlips = {
    view,
    openBacktestTask,
    flush: async () => {
      if (!state.pendingWrites.size) return true;
      await Promise.allSettled([...state.pendingWrites]);
      return true;
    },
    getAIContext() {
      const m = activeMaster();
      const a = state.analysis;
      if (!m) return { pageId: 'T11', summary: '尚未选择或创建大佬交割单数据库。', dataDates: [], filters: {}, status: {}, evidence: {}, limitations: [] };
      return {
        pageId: 'T11',
        summary: `当前正在研究大佬「${m.name}」的交割单数据库（共 ${m.stats?.tradeCount || 0} 笔成交，区间 ${m.stats?.firstDate || '—'} 至 ${m.stats?.lastDate || '—'}）。用户无法实时盯盘，需要兼顾原版解构与非盯盘改造指导。`,
        dataDates: [m.stats?.firstDate, m.stats?.lastDate].filter(Boolean),
        filters: { masterName: m.name, tab: state.tab },
        status: { busy: state.analysisBusy, error: state.analysisError },
        evidence: a && !a.empty ? compactAnalysisForAI(a) : {},
        limitations: [
          ...(a?.warnings || []),
          '分析基于本地交割单数据库与本地通达信日线/1分钟分时对齐计算；公开量价无法观测盘口撮合、VIP通道或未公开催化消息。',
        ],
      };
    },
  };
})();
