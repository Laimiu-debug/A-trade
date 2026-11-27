/* Strategy, master-backtest & master-delivery-slip buy/sell annotations on the native G01 K-line workspace. */
(() => {
  'use strict';
  if (!AT.isDesktop) return;
  const E = AT.escape, cache = new Map(), origins = new Map(), writes = new Set();
  const draftKey = 'atrade.stock-research.drafts.v1';
  const validSymbol = value => /^(sh|sz|bj)\d{6}$/.test(value || '');
  const validDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '');
  const number = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toFixed(2) : '—';
  const intNum = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 0}) : '—';
  const pct = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? (Number(value) * 100).toFixed(2) + '%' : '—';
  const pctDirect = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? (Number(value) > 0 ? '+' : '') + Number(value).toFixed(2) + '%' : '—';
  const confirmName = value => ({confirmed:'已确认',partial:'部分确认',unconfirmed:'未确认',risk_blocked:'风险阻止',pending:'待确认',failed:'未通过'}[value] || '—');
  const exitName = value => String(value || '').startsWith('event_exit:')?'事件退出：'+String(value).slice(11):({open:'期末持仓',stop_loss:'止损',take_profit:'止盈',time_exit:'持仓到期',end_of_data:'行情结束',trailing_stop_daily:'日线跟踪止损'}[value] || value || '—');
  const runKey = row => row.source_type + ':' + row.source_id;
  const normalizeSource = value => String(value || '').replaceAll('\\','/').replace(/\/+$/,'').toLowerCase();
  const current = entry => cache.get(entry.symbol) === entry;
  const visible = entry => current(entry) && AT.state.page === 'G01' && AT.state.stock === entry.symbol;
  const repaint = entry => { if (visible(entry)) AT.marketUI.refreshVisible(); };

  const DEFAULT_MASTER_STYLES = [
    { key: 'wujiang_dynamic', shortName: '乌江望月', name: '乌江望月 · 动态主线切换与浮盈加仓' },
    { key: 'huichanglan_mainline', shortName: '灰常懒', name: '灰常懒 · 绝对主线半路与早盘秒砍' },
    { key: 'xiwangjiejie_1to2', shortName: '希望姐姐', name: '希望姐姐 · 纯主板一进二克制重复' },
    { key: 'dongshizhang_highboard', shortName: '暴涨基金董事长', name: '董事长 · 空仓狙击2板+主板高标' },
    { key: 'ruhan_halfway', shortName: '如晗', name: '如晗 · 分仓半路推土机与弱市降仓' },
    { key: 'songsong_defensive', shortName: '松雅湖的松松', name: '松松 · 强趋势低吸与弱市断电防守' },
  ];

  function masterActionMeta(actionType, side) {
    if (actionType === 'add_win') return { cls: 'm-add', short: '加', dir: 'buy', chip: 'is-buy' };
    if (actionType === 'open_buy') return { cls: 'm-open', short: '建', dir: 'buy', chip: 'is-buy' };
    if (actionType === 'trim_profit' || actionType === 'trim_regime' || actionType === 'trim_cap') return { cls: 'm-trim', short: '减', dir: 'sell', chip: 'is-trim' };
    if (actionType === 'cut_stop' || actionType === 'cut_weak' || actionType === 'sell_rotate') return { cls: 'm-stop', short: '损', dir: 'sell', chip: 'is-sell' };
    if (actionType === 'sell_profit' || actionType === 'sell_time') return { cls: 'm-sell', short: '盈', dir: 'sell', chip: 'is-sell' };
    return side === 'buy'
      ? { cls: 'm-open', short: '建', dir: 'buy', chip: 'is-buy' }
      : { cls: 'm-sell', short: '卖', dir: 'sell', chip: 'is-sell' };
  }

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
  async function marketApi(method, payload = {}) {
    if (typeof atradeDesktop?.market !== 'function') return null;
    const reply = await atradeDesktop.market(method, payload);
    if (!reply?.ok) throw Error(reply?.error || '大佬标注数据读取失败。');
    return reply.data;
  }

  function entryFor({symbol, version, source}) {
    const key = version + '|' + normalizeSource(source);
    let entry = cache.get(symbol);
    if (!entry || entry.key !== key) {
      const origin = origins.get(symbol), params = new URLSearchParams(origin?.query || ''), stored = readDrafts()[symbol];
      const savedDraft = stored&&stored.symbol===symbol&&typeof stored.notes==='string'&&stored.notes.length<=6000?stored:null;
      const initialTab = origin?.preferredTab || 'trades';
      entry = {
        symbol, key, source,
        status:'empty', data:null, error:'',
        masterStatus:'empty', masterData:null, masterError:'',
        singleSim: entry?.symbol === symbol ? entry.singleSim : null,
        singleSimBusy: false, singleSimError: '',
        singleSimStyle: origin?.styleKey || entry?.singleSimStyle || 'wujiang_dynamic',
        scan:null, scanning:false, scanError:'',
        filter:params.get('signal_strategy_id') || '', run:'',
        masterBtRun: origin?.backtestTaskId || '',
        masterSlipId: origin?.masterId || '',
        tab: initialTab,
        draft:entry?.draft || savedDraft, dirty:entry?.dirty || !!savedDraft,
        feedback:'', saving:false,
        showStrategy: true, showMasterBt: true, showMasterSlips: true, showScan: true,
        noteOpen:entry?.noteOpen || !!savedDraft,
      };
      cache.set(symbol, entry);
      if (cache.size > 20) cache.delete(cache.keys().next().value);
    }
    return entry;
  }

  function ensure(entry, force = false) {
    if (force || (entry.status === 'empty' && entry.status !== 'loading')) {
      entry.status = 'loading'; entry.error = '';
      void api('/api/atrade/stocks/' + entry.symbol).then(data => {
        if (!current(entry)) return;
        if (normalizeSource(data.source) !== normalizeSource(entry.source)) throw Error('行情目录已经变化，请重新读取当前证券后查看策略数据。');
        entry.data = data; entry.status = 'ready';
        if (!entry.dirty) entry.draft = {...(data.annotation || {symbol:entry.symbol,start_date:'',stage:'Mid',trend_class:'Unknown',decision:'保留',notes:'',updated_by:'manual'})};
      }).catch(error => {if(current(entry)){entry.error=error.message;entry.status='error';}}).finally(() => repaint(entry));
    }
    if (typeof atradeDesktop?.market === 'function' && (force || entry.masterStatus === 'empty')) {
      entry.masterStatus = 'loading'; entry.masterError = '';
      void marketApi('market.masters.stockMarkers', {symbol: entry.symbol}).then(data => {
        if (!current(entry)) return;
        entry.masterData = data; entry.masterStatus = 'ready';
        if (entry.masterBtRun && entry.masterBtRun !== '__single__' && !(data?.masterBacktestRuns || []).some(r => r.taskId === entry.masterBtRun)) {
          entry.masterBtRun = '';
        }
        if (entry.masterSlipId && !(data?.masters || []).some(m => m.id === entry.masterSlipId)) {
          entry.masterSlipId = '';
        }
      }).catch(error => {if(current(entry)){entry.masterError=error.message;entry.masterStatus='error';}}).finally(() => repaint(entry));
    }
  }

  async function runSingleStockSim(entry) {
    if (entry.singleSimBusy || typeof atradeDesktop?.market !== 'function') return;
    entry.singleSimBusy = true; entry.singleSimError = '';
    entry.showMasterBt = true;
    repaint(entry);
    try {
      const data = await marketApi('market.masters.stockSimulate', {
        symbol: entry.symbol,
        styleKey: entry.singleSimStyle || 'wujiang_dynamic',
        initialCapital: Number(AT.state?.startCapital || 100000),
      });
      if (!current(entry)) return;
      entry.singleSim = data;
      entry.masterBtRun = '__single__';
      entry.tab = 'masterBacktest';
    } catch (error) {
      if (current(entry)) entry.singleSimError = error.message;
    } finally {
      if (current(entry)) { entry.singleSimBusy = false; repaint(entry); }
    }
  }

  function filtered(entry, rows) {
    return (rows || []).filter(row => (!entry.filter || row.strategy_id === entry.filter) && (!entry.run || runKey(row) === entry.run));
  }

  function activeMasterBtTrades(entry) {
    const saved = entry.masterData?.masterBacktestTrades || [];
    const simTrades = (entry.singleSim?.trades || []).map(t => ({
      ...t,
      taskId: '__single__',
      taskName: `单股即时测算 · ${entry.singleSim.shortStyleName || entry.singleSim.styleName}`,
      styleKey: entry.singleSim.styleKey,
      styleName: entry.singleSim.styleName,
    }));
    if (entry.masterBtRun === '__single__') return simTrades;
    if (entry.masterBtRun) return saved.filter(t => t.taskId === entry.masterBtRun);
    return [...simTrades, ...saved];
  }

  function activeMasterSlips(entry) {
    const slips = entry.masterData?.masterSlips || [];
    if (!entry.masterSlipId) return slips;
    return slips.filter(s => s.masterId === entry.masterSlipId);
  }

  function activeResearchTrades(entry) {
    return entry.masterData?.researchTrades || [];
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
    const params = new URLSearchParams(context.query || ''), day = context.focusDate || params.get('focus_date') || params.get('signal_as_of_date');
    return `<p class="stock-research-context">来自 ${E(AT.pages[context.sourcePage]?.title || '策略研究')}${validDate(day)?' · 定位日期 '+E(day):''} · 返回可继续原页面</p>`;
  }

  function selected(entry, day) {
    const chips = [];
    if (entry.showStrategy) {
      for (const row of filtered(entry, entry.data?.trades)) {
        if (row.entry_date === day) chips.push(`<span class="sr-day-chip is-buy"><b>[策略买]</b> ${E(row.strategy_name)} · ${intNum(row.quantity)}股 @ ${number(row.entry_price)}元</span>`);
        if (row.position_state === 'closed' && row.exit_date === day) chips.push(`<span class="sr-day-chip is-sell"><b>[策略卖]</b> ${E(row.strategy_name)} · ${E(exitName(row.exit_reason))} · ${number(row.exit_price)}元 (${pct(row.pnl_ratio)})</span>`);
      }
      for (const row of activeResearchTrades(entry)) {
        if (row.date !== day) continue;
        const isBuy = row.side === 'buy';
        chips.push(`<span class="sr-day-chip ${isBuy?'is-buy':'is-sell'}"><b>[${E(row.actionLabel)}]</b> ${E(row.taskName)} · ${intNum(row.quantity)}股 @ ${number(row.price)}元${row.returnPct!=null?' ('+pctDirect(row.returnPct)+')':''}</span>`);
      }
    }
    if (entry.showMasterBt) {
      for (const t of activeMasterBtTrades(entry)) {
        if (t.date !== day) continue;
        const m = masterActionMeta(t.actionType, t.side);
        const wInfo = t.beforeStockWeightPct != null ? ` · 仓${number(t.beforeStockWeightPct)}→${number(t.afterStockWeightPct)}%` : '';
        const pInfo = t.returnPct != null ? ` · ${pctDirect(t.returnPct)}` : '';
        chips.push(`<span class="sr-day-chip ${m.chip}"><b>[大佬回测·${E(t.actionLabel)}]</b> ${E(t.taskName)} · ${intNum(t.quantity)}股 @ ${number(t.price)}元${wInfo}${pInfo}${t.reason?' · '+E(t.reason):''}</span>`);
      }
    }
    if (entry.showMasterSlips) {
      for (const s of activeMasterSlips(entry)) {
        if (s.date !== day) continue;
        chips.push(`<span class="sr-day-chip is-slip"><b>[大佬实盘·${E(s.masterName)}]</b> ${E(s.legLabel)} ${intNum(s.quantity)}股 @ ${number(s.price)}元${s.time?' ('+E(s.time)+')':''}</span>`);
      }
    }
    const signals = entry.showScan ? [...filtered(entry, entry.data?.signals), ...filtered(entry, entry.scan?.signals)].filter(row => row.signal_date === day) : filtered(entry, entry.data?.signals).filter(row => row.signal_date === day);
    if (!chips.length && signals.length) {
      chips.push(`<span>${signals.length} 条策略信号触发，详情见下表</span>`);
    }
    return `<strong>${E(day || '所选交易日')}</strong>${chips.length ? chips.join('') : '<span>该日没有命中的回测或大佬实盘买卖点（点击图中彩色标记或用方向键切换交易日）</span>'}`;
  }

  function legend({symbol, date}) {
    const entry = cache.get(symbol);
    if (!entry || (!entry.data && !entry.masterData && !entry.singleSim)) return '';
    const stratCount = filtered(entry, entry.data?.trades).length + activeResearchTrades(entry).length;
    const mBtTrades = activeMasterBtTrades(entry);
    const mSlips = activeMasterSlips(entry);
    const sigCount = (entry.data?.signals?.length || 0) + (entry.scan?.signals?.length || 0);
    const legacy = filtered(entry, entry.data?.runs).some(row => row.warning);
    const styles = entry.masterData?.availableStyles?.length ? entry.masterData.availableStyles : DEFAULT_MASTER_STYLES;
    const sim = entry.singleSim;
    const hasMarket = typeof atradeDesktop?.market === 'function';
    const hasAnyMarks = stratCount > 0 || mBtTrades.length > 0 || mSlips.length > 0 || sigCount > 0 || entry.draft?.start_date;
    if (!hasAnyMarks && !hasMarket) return '';

    const simBar = hasMarket ? `<div class="stock-research-simbar" data-stock-master-simbar>
      <strong>单股高手买卖点标注：</strong>
      <select data-stock-master-style aria-label="选择高手策略风格">
        ${styles.map(s => `<option value="${E(s.key)}" ${entry.singleSimStyle === s.key ? 'selected' : ''}>${E(s.name)}</option>`).join('')}
      </select>
      <button type="button" class="btn small primary" data-stock-master-sim ${entry.singleSimBusy ? 'disabled' : ''}>${entry.singleSimBusy ? '测算中…' : '标出该高手买卖点'}</button>
      ${sim ? `<span class="stock-research-sim-badge"><b>${E(sim.shortStyleName || sim.styleName)}</b> · ${sim.totalTrades || 0}笔 · 收益 ${pctDirect(sim.summary?.totalReturnPct)} · 胜率 ${number(sim.summary?.winRatePct)}%</span><button type="button" class="btn small" data-stock-master-sim-clear>清除单股测算</button>` : '<span style="color:var(--secondary)">无需先跑全市场，点一下直接按硬规则在本股 K 线上标出建/加/减/损/盈</span>'}
      ${entry.singleSimError ? `<span style="color:var(--color-error)">${E(entry.singleSimError)}</span>` : ''}
    </div>` : '';

    return `<div class="stock-research-legend">
      <label class="stock-research-toggle"><input type="checkbox" data-stock-layer="showStrategy" ${entry.showStrategy ? 'checked' : ''}><i class="sr-buy"></i><i class="sr-sell"></i>策略回测 (${stratCount})</label>
      <label class="stock-research-toggle"><input type="checkbox" data-stock-layer="showMasterBt" ${entry.showMasterBt ? 'checked' : ''}><i class="sr-m-open"></i><i class="sr-m-add"></i><i class="sr-m-trim"></i><i class="sr-m-stop"></i>大佬回测 (${mBtTrades.length})</label>
      <label class="stock-research-toggle"><input type="checkbox" data-stock-layer="showMasterSlips" ${entry.showMasterSlips ? 'checked' : ''}><i class="sr-slip-buy"></i>大佬实盘 (${mSlips.length})</label>
      <span><i class="sr-signal"></i>信号 (${sigCount})</span>
      <span><i class="sr-note"></i>人工启动日</span>
      <a href="#stock-strategy-evidence">查看完整买卖点明细 ↓</a>
      ${legacy ? '<strong class="stock-research-legacy">包含旧版节奏波结果，需重新回测</strong>' : ''}
    </div>${simBar}<div class="stock-research-selected" data-stock-research-selected>${selected(entry, date)}</div>`;
  }

  function overlay({symbol, rows, start, count, left, step, top = 26, bottom = 274, priceY}) {
    const entry = cache.get(symbol);
    if (!entry || (!entry.data && !entry.masterData && !entry.singleSim)) return '';
    const byDate = new Map(rows.slice(start, start + count).map((row, index) => [row.date, {row, x: left + step * (index + .5)}]));
    const badgeMarks = new Map();
    const signalMarks = new Map();

    const addBadge = (day, cls, label, dir, description) => {
      const point = byDate.get(day);
      if (!point) return;
      const key = `${day}|${cls}|${label}`;
      const existing = badgeMarks.get(key);
      if (existing) { existing.descriptions.add(description); return; }
      badgeMarks.set(key, { day, cls, label, dir, point, descriptions: new Set([description]) });
    };
    const addSignal = (day, description) => {
      const point = byDate.get(day);
      if (!point) return;
      const existing = signalMarks.get(day);
      if (existing) { existing.descriptions.add(description); return; }
      signalMarks.set(day, { day, x: point.x, descriptions: new Set([description]) });
    };

    // 1. Strategy backtest markers (Finaltrade R03 + ResearchService R04/R15)
    if (entry.showStrategy) {
      for (const row of filtered(entry, entry.data?.trades)) {
        addBadge(row.entry_date, 'buy', '买', 'buy', `[策略回测] ${row.strategy_name} · ${row.run_name} · 买入 ${row.quantity}股 @ ${number(row.entry_price)}元`);
        if (row.position_state === 'closed') {
          addBadge(row.exit_date, 'sell', '卖', 'sell', `[策略回测] ${row.strategy_name} · ${exitName(row.exit_reason)} @ ${number(row.exit_price)}元 (${pct(row.pnl_ratio)})`);
        }
      }
      for (const row of activeResearchTrades(entry)) {
        const isBuy = row.side === 'buy';
        addBadge(row.date, isBuy ? 'buy' : 'sell', isBuy ? '研买' : '研卖', isBuy ? 'buy' : 'sell',
          `[研究回测] ${row.taskName} · ${row.actionLabel} ${intNum(row.quantity)}股 @ ${number(row.price)}元${row.reason ? ' · ' + row.reason : ''}`);
      }
    }

    // 2. Master backtest & single-stock simulation markers (建/加/减/损/盈)
    if (entry.showMasterBt) {
      for (const t of activeMasterBtTrades(entry)) {
        const m = masterActionMeta(t.actionType, t.side);
        const wTxt = t.beforeStockWeightPct != null ? ` 仓${number(t.beforeStockWeightPct)}→${number(t.afterStockWeightPct)}%` : '';
        const pTxt = t.returnPct != null ? ` (${pctDirect(t.returnPct)})` : '';
        addBadge(t.date, m.cls, m.short, m.dir, `[大佬回测·${t.actionLabel}] ${t.taskName} · ${intNum(t.quantity)}股 @ ${number(t.price)}元${wTxt}${pTxt} · ${t.reason || ''}`);
      }
    }

    // 3. Master real delivery slip markers (实买 / 实加 / 实卖)
    if (entry.showMasterSlips) {
      for (const s of activeMasterSlips(entry)) {
        const isBuy = s.side === 'buy';
        const short = isBuy ? (s.legType === 'slip_add' ? '实加' : '实买') : '实卖';
        addBadge(s.date, isBuy ? 'slip-buy' : 'slip-sell', short, isBuy ? 'buy' : 'sell',
          `[大佬实盘·${s.masterName}] ${s.legLabel} ${intNum(s.quantity)}股 @ ${number(s.price)}元${s.time ? ' ' + s.time : ''}${s.note ? ' · ' + s.note : ''}`);
      }
    }

    // 4. Strategy signals at bottom rail
    for (const row of filtered(entry, entry.data?.signals)) {
      addSignal(row.signal_date, `${row.strategy_name} · 回测信号 ${row.entry_signal}`);
    }
    if (entry.showScan) {
      for (const row of filtered(entry, entry.scan?.signals)) {
        addSignal(row.signal_date, `${row.strategy_name} · 默认参数扫描 ${row.entry_signal}`);
      }
    }

    // Stack same-day same-direction badges vertically without colliding even near chart top/bottom bounds
    const groupTotals = new Map();
    for (const mark of badgeMarks.values()) {
      const k = `${mark.day}|${mark.dir}`;
      groupTotals.set(k, (groupTotals.get(k) || 0) + 1);
    }
    const slotCounter = new Map();
    const minY = top + 12, maxY = bottom - 12, stackStep = 19;
    const badgeSvg = [...badgeMarks.values()].map(mark => {
      const slotKey = `${mark.day}|${mark.dir}`;
      const slot = slotCounter.get(slotKey) || 0;
      const totalInGroup = groupTotals.get(slotKey) || 1;
      slotCounter.set(slotKey, slot + 1);
      const { row, x } = mark.point;
      const bw = mark.label.length > 1 ? 26 : 18, bh = 16;
      let cy, tri;
      if (mark.dir === 'buy') {
        const anchorY = priceY(row.low);
        const startY = Math.max(minY, Math.min(anchorY + 16, maxY - (totalInGroup - 1) * stackStep));
        cy = startY + slot * stackStep;
        tri = slot === 0 && cy > anchorY + bh / 2 ? `<polygon points="${x},${cy - bh/2 - 4} ${x - 3.5},${cy - bh/2 + 0.5} ${x + 3.5},${cy - bh/2 + 0.5}"/>` : '';
      } else {
        const anchorY = priceY(row.high);
        const startY = Math.min(maxY, Math.max(anchorY - 16, minY + (totalInGroup - 1) * stackStep));
        cy = startY - slot * stackStep;
        tri = slot === 0 && cy < anchorY - bh / 2 ? `<polygon points="${x},${cy + bh/2 + 4} ${x - 3.5},${cy + bh/2 - 0.5} ${x + 3.5},${cy + bh/2 - 0.5}"/>` : '';
      }
      return `<g class="stock-research-mark sr-${mark.cls}" data-stock-research-day="${E(mark.day)}" role="button" tabindex="0" aria-label="${E(mark.day + ' ' + mark.label)}"><title>${E(mark.day + ' · ' + [...mark.descriptions].join('；'))}</title>${tri}<rect x="${(x - bw/2).toFixed(1)}" y="${(cy - bh/2).toFixed(1)}" width="${bw}" height="${bh}" rx="4"/><text x="${x.toFixed(1)}" y="${(cy + 3.5).toFixed(1)}" text-anchor="middle">${E(mark.label)}</text></g>`;
    }).join('');

    const signalSvg = [...signalMarks.values()].map(mark =>
      `<g class="stock-research-mark sr-signal" data-stock-research-day="${E(mark.day)}" role="button" tabindex="0" aria-label="${E(mark.day + ' 信')}"><title>${E(mark.day + ' · ' + [...mark.descriptions].join('；'))}</title><circle cx="${mark.x}" cy="285" r="${Math.min(7, Math.max(2, step * .3))}"/>${step >= 20 ? `<text x="${mark.x}" y="289" text-anchor="middle">信</text>` : ''}</g>`
    ).join('');

    let output = badgeSvg + signalSvg;
    const day = entry.draft?.start_date, point = byDate.get(day);
    if (point) output += `<g class="stock-research-start"><title>${E(day)} · ${entry.dirty ? '草稿' : '已保存'}人工启动日</title><path d="M${point.x} 26V274"/><text x="${Math.min(850, point.x + 5)}" y="20">${entry.dirty ? '草稿' : '人工'}启动 ${E(day)}</text></g>`;
    return output;
  }

  const table = (heads, rows, empty) => rows.length ? `<div class="stock-research-table-wrap" data-stock-kline-skip><table class="stock-research-table"><thead><tr>${heads.map(text => `<th>${text}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : `<p class="stock-research-empty">${empty}</p>`;
  const dateButton = day => validDate(day) ? `<button type="button" class="stock-research-link" data-stock-research-day="${E(day)}">${E(day)}</button>` : '—';
  const sourceCell = row => `${E(row.strategy_name)}<small>${E(row.run_name || '')} · ${row.source_type === 'report' ? '导入报告' : '已完成回测'}${row.warning ? '<br>' + E(row.warning) : ''}</small>`;

  function history(entry) {
    const trades = filtered(entry, entry.data?.trades), resTrades = activeResearchTrades(entry);
    const signals = filtered(entry, entry.data?.signals), scanned = filtered(entry, entry.scan?.signals);
    const mBtTrades = activeMasterBtTrades(entry), mSlips = activeMasterSlips(entry);
    const tabs = [
      ['trades', `策略成交 ${trades.length + resTrades.length}`],
      ['masterBacktest', `大佬回测 ${mBtTrades.length}`],
      ['masterSlips', `大佬实盘 ${mSlips.length}`],
      ['signals', `回测信号 ${signals.length}`],
      ['scan', `本股扫描 ${scanned.length}`],
    ];
    const controls = `<nav class="market-chart-modes" aria-label="策略记录类型">${tabs.map(([id, label]) => `<button type="button" data-stock-research-tab="${id}" class="${entry.tab === id ? 'active' : ''}" aria-pressed="${entry.tab === id}">${label}</button>`).join('')}</nav>`;

    if (entry.tab === 'masterBacktest') {
      const runs = entry.masterData?.masterBacktestRuns || [];
      const simOpt = entry.singleSim ? `<option value="__single__" ${entry.masterBtRun === '__single__' ? 'selected' : ''}>单股即时测算 · ${E(entry.singleSim.shortStyleName || entry.singleSim.styleName)}（${entry.singleSim.totalTrades} 笔）</option>` : '';
      const runSel = (runs.length || entry.singleSim) ? `<div class="stock-research-filters" style="margin:10px 0"><label>大佬回测来源<select data-stock-master-bt-run><option value="">全部高手回测（含单股测算）</option>${simOpt}${runs.map(r => `<option value="${E(r.taskId)}" ${entry.masterBtRun === r.taskId ? 'selected' : ''}>${E(r.taskName)} · ${E(r.startDate)} 至 ${E(r.endDate)}（${r.tradeCount} 笔）</option>`).join('')}</select></label></div>` : '';
      let simCurveHtml = '';
      if (entry.singleSim?.curve?.length > 1 && (!entry.masterBtRun || entry.masterBtRun === '__single__')) {
        const sc = entry.singleSim.curve;
        const vals = [0, ...sc.map(p => Number(p.returnPct || 0))];
        const minV = Math.min(...vals), maxV = Math.max(0.5, ...vals), spanV = Math.max(1, maxV - minV);
        const W = 760, H = 140, L = 52, R = 16, T = 14, B = 24, pw = W - L - R, ph = H - T - B;
        const xAt = i => L + (i / (sc.length - 1)) * pw;
        const yAt = v => T + (1 - (v - minV) / spanV) * ph;
        const pts = sc.map((p, i) => `${xAt(i).toFixed(1)},${yAt(Number(p.returnPct || 0)).toFixed(1)}`).join(' ');
        const endRet = Number(sc[sc.length - 1].returnPct || 0);
        const col = endRet >= 0 ? '#C7383F' : '#15803D';
        simCurveHtml = `<div style="margin:8px 0 12px;padding:10px 12px;border:1px solid var(--color-border);border-radius:8px;background:var(--color-subtle)">
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;margin-bottom:6px">
            <strong>单股高手回测收益曲线 · ${E(entry.singleSim.shortStyleName || entry.singleSim.styleName)}</strong>
            <span>累计收益 <b style="color:${col}">${pctDirect(endRet)}</b> · 最大回撤 <b style="color:#15803D">${pctDirect(entry.singleSim.summary?.maxDrawdownPct)}</b></span>
          </div>
          <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;min-height:110px;display:block" role="img" aria-label="单股高手回测收益曲线">
            <line x1="${L}" y1="${yAt(0).toFixed(1)}" x2="${W - R}" y2="${yAt(0).toFixed(1)}" stroke="#94A3B8" stroke-dasharray="3,3"/>
            <text x="${L - 6}" y="${(T + 8).toFixed(1)}" text-anchor="end" fill="#64748B" font-size="10">${pctDirect(maxV)}</text>
            <text x="${L - 6}" y="${(T + ph).toFixed(1)}" text-anchor="end" fill="#64748B" font-size="10">${pctDirect(minV)}</text>
            <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"/>
            <text x="${L}" y="${H - 5}" fill="#64748B" font-size="10">${E(sc[0].date)}</text>
            <text x="${W - R}" y="${H - 5}" text-anchor="end" fill="#64748B" font-size="10">${E(sc[sc.length - 1].date)}</text>
          </svg>
        </div>`;
      }
      return controls + runSel + simCurveHtml + table(
        ['回测任务 / 风格', '成交日期', '执行动作', '股数', '成交价', '单票仓位变化', '盈亏 / 收益', '触发硬规则'],
        mBtTrades.map(t => `<tr>
          <td>${E(t.taskName)}<small>${E(t.styleName || '')}</small></td>
          <td>${dateButton(t.date)}</td>
          <td><strong>${E(t.actionLabel)}</strong></td>
          <td>${t.side === 'buy' ? '+' : '-'}${intNum(t.quantity)}</td>
          <td>${number(t.price)} 元</td>
          <td>${t.beforeStockWeightPct != null ? `${number(t.beforeStockWeightPct)}% → <b>${number(t.afterStockWeightPct)}%</b>` : '—'}</td>
          <td>${t.returnPct != null ? `${pctDirect(t.returnPct)}` : '—'}</td>
          <td style="white-space:normal;max-width:260px">${E(t.reason || '—')}</td>
        </tr>`),
        '本股暂无已保存的高手思路回测成交。可在上方选择高手风格点击「标出该高手买卖点」即时测算，或在 T11 运行高手思路历史回测。'
      );
    }

    if (entry.tab === 'masterSlips') {
      const masters = entry.masterData?.masters || [];
      const mSel = masters.length ? `<div class="stock-research-filters" style="margin:10px 0"><label>筛选大佬<select data-stock-master-slip-id><option value="">全部大佬（${masters.length} 位）</option>${masters.map(m => `<option value="${E(m.id)}" ${entry.masterSlipId === m.id ? 'selected' : ''}>${E(m.name)}（${m.count} 笔）</option>`).join('')}</select></label></div>` : '';
      return controls + mSel + table(
        ['大佬', '成交日期 / 时间', '实盘动作', '股数', '成交均价', '成交金额', '备注'],
        mSlips.map(s => `<tr>
          <td><strong>${E(s.masterName)}</strong><small>${E(s.styleTag || '')}</small></td>
          <td>${dateButton(s.date)}<small>${E(s.time || '—')}</small></td>
          <td>${E(s.legLabel)}</td>
          <td>${s.side === 'buy' ? '+' : '-'}${intNum(s.quantity)}</td>
          <td>${number(s.price)} 元</td>
          <td>${number(s.amount)} 元</td>
          <td style="white-space:normal;max-width:240px">${E(s.note || '—')}</td>
        </tr>`),
        '大佬交割单库中暂无本股实盘记录。可在 T11「大佬交割单」载入顽主杯 6 大高手库或导入交割单。'
      );
    }

    if (entry.tab === 'trades') {
      const rowsHtml = [
        ...trades.map(row => `<tr><td>${sourceCell(row)}</td><td>${dateButton(row.signal_date)}</td><td>${dateButton(row.entry_date)}<small>${number(row.entry_price)} 元</small></td><td>${row.position_state === 'open' ? '期末仍持仓' : dateButton(row.exit_date) + '<small>' + number(row.exit_price) + ' 元</small>'}</td><td>${E(row.quantity)}</td><td>${row.position_state === 'open' ? '未平仓' : pct(row.pnl_ratio)}</td><td>${E(exitName(row.exit_reason))}</td></tr>`),
        ...resTrades.map(row => `<tr><td>${E(row.taskName)}<small>本地研究回测 · ${E(row.kind)}</small></td><td>—</td><td>${row.side === 'buy' ? dateButton(row.date) + '<small>' + number(row.price) + ' 元</small>' : '—'}</td><td>${row.side === 'sell' ? dateButton(row.date) + '<small>' + number(row.price) + ' 元</small>' : '—'}</td><td>${intNum(row.quantity)}</td><td>${row.returnPct != null ? pctDirect(row.returnPct) : '—'}</td><td>${E(row.reason || row.actionLabel)}</td></tr>`),
      ];
      return controls + table(['策略与来源', '信号日', '模拟买入日 / 价格', '模拟卖出日 / 价格', '数量', '收益率', '退出原因'], rowsHtml, '当前条件下没有该证券的已保存策略模拟成交。可调整筛选，或先运行策略回测。');
    }
    if (entry.tab === 'signals') return controls + table(['策略与来源', '信号日', '入场信号', '阶段', '等级', '质量评分'], signals.map(row => `<tr><td>${sourceCell(row)}</td><td>${dateButton(row.signal_date)}</td><td>${E(row.entry_signal)}</td><td>${E(row.entry_phase)}</td><td>${E(row.event_grade)}</td><td>${number(row.entry_quality_score)}</td></tr>`), '没有已保存的回测信号。信号出现不代表满足成交条件。');
    return controls + `<p class="stock-research-help">按各策略默认参数扫描最近 60 个交易日；结果用于历史信号观察，不代表账户成交，也不替代当前策略自定义参数的回测。</p>` + table(['策略', '信号日', '信号', '阶段', '等级', '质量评分'], scanned.map(row => `<tr><td>${E(row.strategy_name)}</td><td>${dateButton(row.signal_date)}</td><td>${E(row.entry_signal)}</td><td>${E(row.entry_phase)}</td><td>${E(row.event_grade)}</td><td>${number(row.entry_quality_score)}</td></tr>`), entry.scanning ? '正在按真实日线扫描…' : entry.scan ? '本次扫描没有触发信号。' : '点击“扫描本股策略”查看所有内置策略的历史触发。');
  }

  function noteForm(entry) {
    const draft = entry.draft || {}, options = (items, value) => items.map(([id, text]) => `<option value="${id}" ${id === value ? 'selected' : ''}>${text}</option>`).join('');
    return `<details class="market-chart-details stock-research-note" ${entry.noteOpen ? 'open' : ''}><summary>人工标注与研究笔记 · ${entry.dirty ? '有未保存修改' : entry.data?.annotation ? '已保存' : '尚未标注'}</summary><form data-stock-research-note="${entry.symbol}"><div class="stock-research-fields"><label>人工启动日<input type="date" name="start_date" value="${E(draft.start_date || '')}"></label><label>所处阶段<select name="stage">${options([['Early', '初期'], ['Mid', '中期'], ['Late', '后期']], draft.stage)}</select></label><label>趋势分类<select name="trend_class">${options([['A', 'A 类'], ['B', 'B 类'], ['A_B', 'A/B 类'], ['Unknown', '未分类']], draft.trend_class)}</select></label><label>观察决定<select name="decision">${options([['保留', '保留'], ['排除', '排除']], draft.decision)}</select></label></div><label class="stock-research-note-label">研究笔记<textarea name="notes" rows="3" maxlength="6000">${E(draft.notes || '')}</textarea></label><div class="stock-research-actions"><button type="submit" class="btn primary" ${entry.saving ? 'disabled' : ''}>${entry.saving ? '保存中…' : '保存人工标注'}</button><button type="button" class="btn" data-stock-research-use-day>使用图中所选日期</button><span role="status" data-stock-research-save-state>${E(entry.feedback || (entry.dirty ? '有未保存修改' : '保存到当前工作区'))}</span></div></form></details>`;
  }

  function forceStatistics(data) {
    const statistics = data.force_statistics, row = statistics?.current || {}, meta = statistics?.metadata || {};
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    const pressure = value => finite(value) ? (value > 0 ? '+' : '') + (value * 100).toFixed(2) : '—';
    const share = value => finite(value) ? (value * 100).toFixed(2) + '%' : '—';
    const ratio = value => finite(value) ? value.toFixed(2) + ' 倍' : '—';
    const count = value => Number.isInteger(value) && value >= 0 ? String(value) : '—';
    const values = [['近 5 根量价压力', pressure(row.pressure5)], ['近 20 根量价压力', pressure(row.pressure20)],
      ['20 根承接代理份额', share(row.support20)], ['20 根正压力日占比', share(row.positiveShare20)],
      ['相对前 20 根均量', ratio(row.volumeRatio20)], ['有效量价记录', count(meta.validBarCount) + ' / ' + count(meta.inputBarCount) + ' 根']];
    const missing = ['pressure5', 'pressure20', 'support20', 'positiveShare20', 'volumeRatio20'].some(key => !finite(row[key]));
    const unavailable = !statistics ? '暂未返回量价统计，可刷新本股记录后查看。' : row.barStatus === 'invalid' || row.barStatus === 'missing' ? '当前量价数据缺失或无效，相关统计显示 —。' : missing ? '历史不足或窗口内有缺值，相关统计显示 —，不会以 0 替代。' : '';
    const basis = meta.calendarBasis === 'providedCalendar' ? '按提供的交易日历对齐；缺失记录保持未知。' : '窗口按已观察 K 线根数计算，未核验完整交易日历。';
    const diagnostics = (row.diagnostics || []).filter(value => ['priceUp20PressureNegative', 'priceDown20PressurePositive'].includes(value)).map(value => value === 'priceUp20PressureNegative' ? '近 20 根价格上涨而量价压力为负，两者衡量的关系不同。' : '近 20 根价格下跌而量价压力为正，两者衡量的关系不同。');
    return `<section data-stock-force-statistics aria-label="主散量能量价统计"><h3>主散量能 · 量价统计</h3><p class="stock-research-help">截至 ${E(row.date || meta.lastDate || data.as_of_date || '—')} · ${E(basis)}仅为量价代理，不代表真实主力 / 散户持仓。</p><div class="stock-research-metrics">${values.map(([label, value]) => `<div><span>${label}</span><strong>${E(value)}</strong></div>`).join('')}</div>${unavailable ? `<p class="stock-research-help" role="status">${E(unavailable)}</p>` : ''}${diagnostics.map(text => `<p class="stock-research-help">${E(text)}</p>`).join('')}<details class="market-chart-details"><summary>统计口径与缺值说明</summary><p class="stock-research-help">量价压力范围为 −100 至 +100，按相对成交量加权；承接代理份额由压力换算，不识别投资者身份。正压力日占比为近 20 根中压力为正的比例。相对均量为当前成交量除以前 20 根平均成交量，不含当前根。</p><p class="stock-research-help">${finite(meta.requiredBars?.pressure5) && finite(meta.requiredBars?.pressure20) ? `5 根压力至少需要 ${E(meta.requiredBars.pressure5)} 根、20 根压力至少需要 ${E(meta.requiredBars.pressure20)} 根连续有效观察。` : ''}历史不足、缺值或无有效成交量时保持未知。${statistics?.version ? '统计版本：' + E(statistics.version) + '。' : ''}</p></details></section>`;
  }

  function render(input) {
    if (!validSymbol(input.symbol)) return '';
    const entry = entryFor(input); queueMicrotask(() => ensure(entry));
    const header = `<header class="stock-research-heading"><div><h2>策略与大佬买卖点数据</h2>${contextNote(entry)}</div><div class="stock-research-actions"><button type="button" class="btn" data-stock-research-refresh ${entry.status === 'loading' ? 'disabled' : ''}>刷新记录</button><button type="button" class="btn primary" data-stock-research-scan ${entry.scanning ? 'disabled' : ''}>${entry.scanning ? '扫描中…' : '扫描本股策略'}</button><button type="button" class="btn" data-stock-research-open="backtest">策略回测</button><button type="button" class="btn" data-stock-research-open="masters">大佬交割单</button></div></header>`;
    const hasMasterPanel = entry.masterStatus === 'ready' || !!entry.singleSim;
    if ((entry.status === 'empty' || entry.status === 'loading') && !hasMasterPanel) return `<section class="panel stock-research-panel" id="stock-strategy-evidence" aria-busy="true">${header}<p class="stock-research-empty" role="status">正在读取本股的策略快照、大佬回测与实盘记录…</p></section>`;
    if (entry.error && !hasMasterPanel) return `<section class="panel stock-research-panel" id="stock-strategy-evidence">${header}<p class="stock-research-empty" role="alert">${E(entry.error)}</p></section>`;
    const data = entry.data || { trades: [], signals: [], runs: [], strategies: [], warnings: [], signal: null, as_of_date: '' };
    const signal = data.signal, runs = (data.runs || []).filter(row => !entry.filter || row.strategy_id === entry.filter);
    const metrics = signal ? [['结构阶段', signal.wyckoff_phase || signal.phase_hint || '—'], ['入场质量', number(signal.entry_quality_score)], ['健康评分', number(signal.health_score)], ['事件评分', number(signal.event_score)], ['风险评分', number(signal.risk_score)], ['信号确认', confirmName(signal.confirmation_status)]] : [];
    const showStrategyFilter = !!(data.strategies?.length || entry.filter), showRunFilter = !!(runs.length || entry.run);
    const stratErrorBar = entry.error ? `<p class="stock-research-help" role="alert">策略快照提示：${E(entry.error)}（下方仍可正常查看大佬回测与大佬实盘买卖点）</p>` : '';
    return `<section class="panel stock-research-panel" id="stock-strategy-evidence" data-stock-research-symbol="${entry.symbol}">${header}${stratErrorBar}<p class="stock-research-help">日线结构快照截至 ${E(data.as_of_date || '—')}。上方 K 线图已统一叠加策略回测、大佬回测（建/加/减/损/盈）与大佬实盘交割单标注；点击任意日期可快速定位 K 线。</p>${metrics.length ? `<div class="stock-research-metrics">${metrics.map(([label, value]) => `<div><span>${label}</span><strong>${E(value)}</strong></div>`).join('')}</div>` : (entry.data ? '<p class="stock-research-empty">当前日线不足以生成结构评分。</p>' : '')}${entry.data ? forceStatistics(data) : ''}${signal?.wy_event_chain?.length ? `<details class="market-chart-details"><summary>结构事件与判定依据（${signal.wy_event_chain.length} 项）</summary><div class="stock-research-events">${signal.wy_event_chain.map(row => `<span>${dateButton(row.date)} · ${E(row.event)} · ${confirmName(signal.event_confirmation_map?.[row.event])}</span>`).join('')}</div></details>` : ''}<div class="stock-research-filters">${showStrategyFilter ? `<label>策略<select data-stock-research-filter><option value="">全部策略</option>${strategyOptions(entry)}</select></label>` : ''}${showRunFilter ? `<label>回测来源<select data-stock-research-run><option value="">全部已保存回测 / 报告</option>${runs.map(row => `<option value="${E(runKey(row))}" ${entry.run === runKey(row) ? 'selected' : ''}>${E(row.run_name)} · ${E(row.range.date_from)} 至 ${E(row.range.date_to)}</option>`).join('')}</select></label>` : ''}<label class="stock-research-check"><input type="checkbox" data-stock-research-show-scan ${entry.showScan ? 'checked' : ''}>在图上显示扫描信号</label></div>${entry.scanError ? `<p class="business-error" role="alert">${E(entry.scanError)}</p>` : ''}${history(entry)}${(data.warnings || []).map(w => `<p class="stock-research-help" role="status">${E(w)}</p>`).join('')}${entry.data ? noteForm(entry) : ''}</section>`;
  }

  async function scan(entry) {
    if (entry.scanning) return; entry.scanning = true; entry.scanError = ''; entry.tab = 'scan'; repaint(entry);
    try {
      const status = await api('/api/atrade/status'); if (status.source_changed) throw Error('未完成任务仍使用此前的行情目录，请先完成或停止任务。');
      const data = await api('/api/backtest/scan-stock-strategies/' + entry.symbol + '?window_days=60&scan_days=60');
      if (current(entry)) {
        entry.scan = data;
        entry.scan.strategies.forEach(row => { if (row.strategy_id === 'score_only_rank_v1') row.strategy_name = '纯评分排名V1'; });
        entry.scan.signals.forEach(row => { if (row.strategy_id === 'score_only_rank_v1') row.strategy_name = '纯评分排名V1'; });
      }
    } catch (error) { if (current(entry)) entry.scanError = error.message; } finally { entry.scanning = false; repaint(entry); }
  }

  document.addEventListener('change', event => {
    const entry = cache.get(AT.state.stock); if (AT.state.page !== 'G01' || !entry) return;
    if (event.target.matches('[data-stock-research-filter]')) { entry.filter = event.target.value; entry.run = ''; repaint(entry); }
    if (event.target.matches('[data-stock-research-run]')) { entry.run = event.target.value; repaint(entry); }
    if (event.target.matches('[data-stock-research-show-scan]')) { entry.showScan = event.target.checked; repaint(entry); }
    if (event.target.matches('[data-stock-layer]')) {
      const k = event.target.dataset.stockLayer;
      if (['showStrategy', 'showMasterBt', 'showMasterSlips'].includes(k)) { entry[k] = event.target.checked; repaint(entry); }
    }
    if (event.target.matches('[data-stock-master-style]')) { entry.singleSimStyle = event.target.value; }
    if (event.target.matches('[data-stock-master-bt-run]')) { entry.masterBtRun = event.target.value; repaint(entry); }
    if (event.target.matches('[data-stock-master-slip-id]')) { entry.masterSlipId = event.target.value; repaint(entry); }
  });

  document.addEventListener('input', event => {
    const form = event.target.closest('form[data-stock-research-note]'); if (!form) return;
    const entry = cache.get(form.dataset.stockResearchNote); if (!entry?.draft) return;
    entry.draft[event.target.name] = event.target.value; entry.dirty = true; entry.noteOpen = true; entry.feedback = '有未保存修改';
    saveDraft(entry); form.querySelector('[data-stock-research-save-state]').textContent = entry.feedback;
  });
  document.addEventListener('toggle', event => { if (!event.target.matches?.('.stock-research-note')) return; const symbol = event.target.querySelector('form')?.dataset.stockResearchNote, entry = cache.get(symbol); if (entry) entry.noteOpen = event.target.open; }, true);
  document.addEventListener('submit', event => {
    const form = event.target.closest('form[data-stock-research-note]'); if (!form) return; event.preventDefault();
    const entry = cache.get(form.dataset.stockResearchNote); if (!entry || entry.saving) return;
    if (!validDate(entry.draft.start_date)) {
      const chartDay = document.querySelector('[data-market-date]')?.value;
      if (validDate(chartDay)) entry.draft.start_date = chartDay;
      else { entry.feedback = '请先选择人工启动日。'; repaint(entry); return; }
    }
    const payload = { ...entry.draft, symbol: entry.symbol, updated_by: 'manual' };
    entry.saving = true; entry.feedback = ''; repaint(entry);
    const write = api('/api/atrade/stocks/' + entry.symbol + '/annotation', { method: 'PUT', body: payload }); writes.add(write);
    void write.then(data => {
      entry.data.annotation = data.annotation;
      if (JSON.stringify({ ...entry.draft, symbol: entry.symbol, updated_by: 'manual' }) === JSON.stringify(payload)) {
        entry.draft = { ...data.annotation }; entry.dirty = false; entry.feedback = '已保存';
      } else entry.feedback = '已保存上一版，当前仍有未保存修改';
      saveDraft(entry);
    }).catch(error => { entry.feedback = error.message; }).finally(() => { writes.delete(write); entry.saving = false; repaint(entry); });
  });

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-stock-research-day],[data-stock-research-tab],[data-stock-research-refresh],[data-stock-research-scan],[data-stock-research-use-day],[data-stock-research-open],[data-stock-master-sim],[data-stock-master-sim-clear]');
    if (!button || button.disabled || AT.state.page !== 'G01') return; event.preventDefault();
    const entry = cache.get(AT.state.stock); if (!entry) return;
    if (button.dataset.stockResearchDay) { AT.marketUI.openDay(entry.symbol, button.dataset.stockResearchDay); return; }
    if (button.dataset.stockResearchTab) { entry.tab = button.dataset.stockResearchTab; repaint(entry); return; }
    if (button.hasAttribute('data-stock-research-refresh')) { ensure(entry, true); repaint(entry); }
    if (button.hasAttribute('data-stock-research-scan')) void scan(entry);
    if (button.hasAttribute('data-stock-master-sim')) void runSingleStockSim(entry);
    if (button.hasAttribute('data-stock-master-sim-clear')) {
      entry.singleSim = null;
      entry.singleSimError = '';
      if (entry.masterBtRun === '__single__') entry.masterBtRun = '';
      repaint(entry);
      return;
    }
    if (button.hasAttribute('data-stock-research-use-day')) { const day = document.querySelector('[data-market-date]')?.value; if (validDate(day)) { entry.draft.start_date = day; entry.dirty = true; saveDraft(entry); repaint(entry); } }
    if (button.hasAttribute('data-stock-research-open')) AT.navigate(button.dataset.stockResearchOpen === 'masters' ? 'T11' : 'R03');
  });

  document.addEventListener('keydown', event => { const el = event.target.closest('[data-stock-research-day]'); if (el?.tagName.toLowerCase() === 'g' && ['Enter', ' '].includes(event.key)) { event.preventDefault(); event.stopPropagation(); el.dispatchEvent(new MouseEvent('click', { bubbles: true })); } }, true);

  AT.stockResearch = {
    render, overlay, legend,
    async flush() {
      const completed = await Promise.allSettled([...writes]);
      return completed.every(row => row.status === 'fulfilled') && [...cache.values()].every(entry => !entry.dirty || saveDraft(entry));
    },
    selected(symbol, day) {
      const entry = cache.get(symbol);
      const target = document.querySelector('[data-stock-research-selected]');
      if (entry && target) target.innerHTML = selected(entry, day);
    },
    open(symbol, context = {}) {
      if (!validSymbol(symbol)) return false;
      origins.set(symbol, context);
      const entry = cache.get(symbol);
      if (entry) entry.filter = new URLSearchParams(context.query || '').get('signal_strategy_id') || '';
      const params = new URLSearchParams(context.query || ''), day = params.get('focus_date') || params.get('signal_as_of_date');
      if (validDate(day)) AT.marketUI.openDay(symbol, day); else AT.navigate('G01', symbol);
      return true;
    },
    openFromMaster(symbol, day, opts = {}) {
      if (!validSymbol(symbol)) return false;
      const isBt = opts.tab === 'backtest' || opts.tab === 'advisor';
      const ctx = {
        sourcePage: 'T11',
        focusDate: validDate(day) ? day : '',
        masterId: !isBt ? (opts.masterId || '') : '',
        backtestTaskId: isBt ? (opts.backtestTaskId || '') : '',
        styleKey: opts.styleKey || '',
        preferredTab: isBt ? 'masterBacktest' : 'masterSlips',
      };
      origins.set(symbol, ctx);
      const entry = cache.get(symbol);
      if (entry) {
        entry.tab = ctx.preferredTab;
        if (ctx.backtestTaskId) { entry.masterBtRun = ctx.backtestTaskId; entry.showMasterBt = true; }
        if (ctx.masterId) { entry.masterSlipId = ctx.masterId; entry.showMasterSlips = true; }
        if (ctx.styleKey) entry.singleSimStyle = ctx.styleKey;
        entry.masterStatus = 'empty'; // refresh latest saved backtest / slips
      }
      if (validDate(day)) AT.marketUI.openDay(symbol, day); else AT.navigate('G01', symbol);
      return true;
    },
  };
})();
