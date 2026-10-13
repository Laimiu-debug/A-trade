/* Source-backed market lists. Search, filters and rankings stay inline. */
(() => {
  if (!window.AT) return;
  const E = AT.escape;
  const states = new Map();
  const payloads = new Map();
  let composingInput = null;
  const stocksColumns = [
    ['name', '证券', 'name'], ['close', '收盘价', 'price'], ['changePercent', '涨跌幅', 'change'],
    ['return5', '5 日', 'return5'], ['return20', '20 日', 'return20'], ['amount', '成交额', 'amount'],
    ['volumeRatio5', '5日均量比', 'ratio'], ['', '近 20 日', 'trend']
  ];
  const boards = [['all', '全部市场'], ['main', '主板'], ['star', '科创板'], ['chinext', '创业板'], ['bse', '北交所'], ['index', '指数']];
  const pools = [['all', '全部证券'], ['active', '成交活跃'], ['gainers', '涨幅领先'], ['volume', '放量观察']];
  const poolRules = {
    all: '点击列标题排序 · 点击证券名称查看 K 线与分析',
    active: '在当前筛选范围内，同一交易日的股票按成交额取前 50 名，反映成交活跃程度。',
    gainers: '在当前筛选范围内，同一交易日的上涨股票按涨幅取前 50 名。',
    volume: '在当前筛选范围内，同一交易日成交量达到此前 5 个交易日均量的 1.5 倍以上，取比值最高的 50 只股票。'
  };
  const isNumber = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const numeric = value => isNumber(value) ? Number(value) : null;
  const num = (value, digits = 2) => isNumber(value) ? Number(value).toLocaleString('zh-CN', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
  const pct = value => isNumber(value) ? `${Number(value) > 0 ? '+' : ''}${num(value)}%` : '—';
  const tone = value => !isNumber(value) || Number(value) === 0 ? 'neutral' : Number(value) > 0 ? 'up' : 'down';
  const day = value => value ? String(value).slice(0, 10) : '';
  const timestamp = value => {
    const text = String(value ?? '');
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return E(text);
    const parsed = new Date(text);
    return E(Number.isNaN(parsed.valueOf()) ? text : parsed.toLocaleString('zh-CN', {hour12: false}));
  };
  const amount = value => !isNumber(value) ? '—' : Math.abs(Number(value)) >= 1e8 ? `${num(Number(value) / 1e8)} 亿` : Math.abs(Number(value)) >= 1e4 ? `${num(Number(value) / 1e4, 0)} 万` : num(value, 0);
  const stockBoard = item => item.kind === 'index' ? 'index' : item.board || 'unknown';
  const isStock = item => item.kind === 'stock' || (!item.kind && ['main', 'star', 'chinext', 'bse'].includes(stockBoard(item)));
  const isLatest = (item, payload) => !!day(payload.asOf) && day(item.date) === day(payload.asOf);
  const code = item => String(item.code || item.symbol?.replace(/^(sh|sz|bj)/i, '') || '');
  function stateFor(key, type) {
    if (!states.has(key)) states.set(key, {type, query: '', board: 'all', pool: 'all', direction: 'all', freshness: type === 'stocks' ? 'latest' : 'all', category: 'all', above: false, volume: false, sort: type === 'sectors' ? 'name' : 'amount', order: type === 'sectors' ? 'asc' : 'desc', userSort: false, offset: 0, size: 15, compact: true, filtersOpen: false, detailsOpen: false, ...AT.marketPreferences?.getList(key)});
    return states.get(key);
  }
  function resetSort(state) {
    state.sort = state.type === 'sectors' ? 'name' : state.pool === 'gainers' ? 'changePercent' : state.pool === 'volume' ? 'volumeRatio5' : 'amount';
    state.order = state.type === 'sectors' ? 'asc' : 'desc'; state.userSort = false;
  }
  function compare(key, order) {
    return (a, b) => {
      const text = key === 'name' || key === 'category';
      const left = text ? String(a[key] || '') : numeric(a[key]);
      const right = text ? String(b[key] || '') : numeric(b[key]);
      // Missing observations always follow real values, in both directions.
      if (left === null || left === '') return right === null || right === '' ? String(a.symbol || a.id || '').localeCompare(String(b.symbol || b.id || '')) : 1;
      if (right === null || right === '') return -1;
      const result = text ? left.localeCompare(right, 'zh-CN') : left - right;
      return result ? (order === 'asc' ? result : -result) : String(a.symbol || a.id || '').localeCompare(String(b.symbol || b.id || ''));
    };
  }
  function filteredStocks(payload, state) {
    let items = (payload.items || []).filter(item => {
      if (state.query && !(String(item.name || '') + code(item) + String(item.symbol || '')).toLowerCase().includes(state.query.trim().toLowerCase())) return false;
      if (state.board !== 'all' && stockBoard(item) !== state.board) return false;
      if (state.freshness === 'latest' && payload.asOf && !isLatest(item, payload)) return false;
      if (state.freshness === 'older' && isLatest(item, payload)) return false;
      const change = numeric(item.changePercent);
      if (state.direction === 'up' && !(change > 0)) return false;
      if (state.direction === 'down' && !(change < 0)) return false;
      if (state.direction === 'flat' && change !== 0) return false;
      const above = typeof item.aboveMA20 === 'boolean' ? item.aboveMA20 : isNumber(item.close) && isNumber(item.ma20) && Number(item.close) > Number(item.ma20);
      if (state.above && !above) return false;
      if (state.volume && !(numeric(item.volumeRatio5) >= 1.5)) return false;
      return true;
    });
    if (state.pool !== 'all') {
      items = items.filter(item => isLatest(item, payload) && isStock(item));
      const field = state.pool === 'active' ? 'amount' : state.pool === 'gainers' ? 'changePercent' : 'volumeRatio5';
      items = items.filter(item => isNumber(item[field]) && (state.pool === 'volume' ? Number(item[field]) >= 1.5 : Number(item[field]) > 0));
      items.sort(compare(field, 'desc'));
      items = items.slice(0, 50);
    }
    return items.sort(compare(state.sort, state.order));
  }
  function filteredSectors(payload, state) {
    return (payload.items || []).filter(item => {
      if (state.query && !String(item.name || '').toLowerCase().includes(state.query.trim().toLowerCase())) return false;
      if (state.category !== 'all' && item.category !== state.category) return false;
      const change = numeric(item.changePercent);
      if (state.direction === 'up' && !(change > 0)) return false;
      if (state.direction === 'down' && !(change < 0)) return false;
      if (state.direction === 'flat' && change !== 0) return false;
      return true;
    }).sort(compare(state.sort, state.order));
  }
  function choice(field, value, label, current, extra = '') {
    return `<button type="button" class="market-explorer-chip${current === value ? ' market-explorer-selected' : ''}" data-explorer-field="${field}" data-explorer-value="${E(String(value))}" aria-pressed="${current === value}" ${extra}>${E(label)}</button>`;
  }
  function group(label, field, options, current) {
    return `<div class="market-explorer-filter-row"><span class="market-explorer-filter-label">${label}</span><div class="market-explorer-choices">${options.map(([value, text]) => choice(field, value, text, current)).join('')}</div></div>`;
  }
  function layoutControls(state) {
    const count = [state.board !== 'all', state.direction !== 'all', state.category !== 'all', state.above, state.volume, state.freshness !== (state.type === 'stocks' ? 'latest' : 'all')].filter(Boolean).length;
    return `<div class="market-explorer-layout"><button type="button" data-explorer-field="filtersOpen" aria-expanded="${state.filtersOpen}">${state.filtersOpen ? '收起筛选' : '展开筛选'}${count ? ` · ${count} 项` : ''}</button><button type="button" data-explorer-field="compact" aria-pressed="${state.compact}">${state.compact ? '舒展布局' : '紧凑布局'}</button>${state.userSort ? '<button type="button" data-explorer-sort-reset>复原排序</button>' : ''}</div>`;
  }
  function sparkline(values, label) {
    const points = Array.isArray(values) ? values.slice(-20) : [];
    const finite = points.filter(isNumber).map(Number);
    if (finite.length < 2) return '<span class="market-explorer-missing">—</span>';
    const low = Math.min(...finite), high = Math.max(...finite), range = high - low || 1;
    // Null values break the path instead of fabricating a continuous history.
    let previous = false;
    const path = points.map((value, index) => {
      if (!isNumber(value)) {previous = false; return '';}
      const command = previous ? 'L' : 'M'; previous = true;
      return `${command}${(2 + index / (points.length - 1) * 90).toFixed(2)},${(27 - (Number(value) - low) / range * 23).toFixed(2)}`;
    }).join(' ');
    return `<svg class="market-explorer-spark market-explorer-${tone(finite.at(-1) - finite[0])}" viewBox="0 0 94 31" role="img" aria-label="${E(label)}"><path d="${path}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }
  function heading(payload, state, title, type) {
    return `<div class="market-explorer-heading"><div><h2>${E(payload.title || title)}</h2><p>${type === 'stocks' ? `日线研究快照${payload.asOf ? ' · ' + E(day(payload.asOf)) : ''}` : '通达信板块目录 · 点击名称查看成分'}${payload.busy ? ' · 正在更新…' : ''}${payload.loadingMetrics ? ` · 报价读取 ${num(payload.progress?.done || 0, 0)} / ${num(payload.progress?.total ?? payload.items?.length, 0)}` : ''}</p></div><div class="market-explorer-heading-tools"><label class="market-explorer-search">${AT.icon('search')}<input type="text" inputmode="search" data-explorer-query maxlength="100" value="${E(state.query)}" aria-label="${type === 'stocks' ? '搜索证券名称或代码' : '搜索板块名称'}" placeholder="${type === 'stocks' ? '名称 / 代码' : '搜索板块'}" autocomplete="off"></label>${payload.refreshAction ? `<button type="button" class="market-explorer-refresh" data-market-action="${E(payload.refreshAction)}" ${payload.busy ? 'disabled' : ''}>${AT.icon('refresh')}<span>刷新</span></button>` : ''}</div></div>${payload.error && payload.items?.length ? `<div class="market-explorer-feedback" role="alert"><strong>更新未完成 · 保留上次结果</strong><span>${E(typeof payload.error === 'string' ? payload.error : payload.error.message || '行情读取失败，请重试。')}</span></div>` : ''}`;
  }
  function summary(payload, items, type) {
    const all = payload.items || [], comparable = type === 'stocks' ? all.filter(item => isLatest(item, payload) && isStock(item)) : all;
    const rises = comparable.filter(item => numeric(item.changePercent) > 0).length;
    const falls = comparable.filter(item => numeric(item.changePercent) < 0).length;
    const measured = comparable.filter(item => isNumber(item.changePercent)).length;
    const coverage = payload.coverage;
    const count = typeof coverage === 'number' ? coverage : coverage && (coverage.valid ?? coverage.scanned ?? coverage.total);
    return `<div class="market-explorer-summary"><span>已载入 <b>${num(all.length, 0)}</b> ${type === 'stocks' ? '只证券' : '个板块'}</span><span>当前结果 <b>${num(items.length, 0)}</b></span>${measured ? `<span class="market-explorer-up">上涨 ${num(rises, 0)}</span><span class="market-explorer-down">下跌 ${num(falls, 0)}</span>` : ''}${type === 'stocks' && payload.asOf ? `<span>同日股票 ${num(comparable.length, 0)}</span>` : ''}${coverage && isNumber(coverage.excluded) && Number(coverage.excluded) > 0 ? `<span>异常排除 ${num(coverage.excluded, 0)}</span>` : ''}${type === 'sectors' ? `<span>有报价 ${num(measured, 0)}</span>` : ''}${isNumber(count) && Number(count) !== all.length && typeof coverage === 'number' ? `<span>已检查 ${num(count, 0)} 项</span>` : ''}</div>`;
  }
  function headers(columns, state) {
    return `<thead><tr><th class="market-explorer-rank">序号</th>${columns.map(([key, label, cls]) => `<th class="market-explorer-col-${cls}"${key ? ` aria-sort="${state.sort === key ? state.order === 'asc' ? 'ascending' : 'descending' : 'none'}"` : ''}>${key ? `<button type="button" data-explorer-sort="${key}" title="${state.userSort && state.sort === key ? state.order === 'asc' ? '切换倒序' : '恢复默认排序' : '切换正序'}：${E(label)}">${E(label)}<span class="market-explorer-sort-mark" aria-hidden="true">${state.sort === key ? state.userSort ? state.order === 'asc' ? '↑' : '↓' : '默认' : '↕'}</span></button>` : E(label)}</th>`).join('')}</tr></thead>`;
  }
  function pagination(state, total) {
    const pages = Math.max(1, Math.ceil(total / state.size)), current = Math.floor(state.offset / state.size) + 1;
    return `<footer class="market-explorer-pagination"><span>${total ? `${state.offset + 1}–${Math.min(state.offset + state.size, total)} / ${num(total, 0)} 项` : '0 项'}</span><div class="market-explorer-page-size"><span>每页</span>${[15, 30, 60].map(value => choice('size', value, String(value), state.size)).join('')}</div><div class="market-explorer-page-controls"><button type="button" data-explorer-page="-1" aria-label="上一页" ${current <= 1 ? 'disabled' : ''}>上一页</button><span>${current} / ${pages}</span><button type="button" data-explorer-page="1" aria-label="下一页" ${current >= pages ? 'disabled' : ''}>下一页</button></div></footer>`;
  }
  function footnotes(payload, type, state) {
    const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
    return `<div class="market-explorer-source"><button type="button" data-explorer-field="detailsOpen" aria-expanded="${state.detailsOpen}">${state.detailsOpen ? '收起' : '查看'}数据与规则说明</button><div ${state.detailsOpen ? '' : 'hidden'}><p>${type === 'stocks' ? '本地未复权日线 · 5日均量比 = 当日成交量 ÷ 此前 5 个交易日均量 · 热门池按行情规则筛选' : '报价缺失显示 —；来源未给出行情时间时，只标记采集时间。'}</p>${payload.computedAt ? `<p>计算于 ${timestamp(payload.computedAt)}</p>` : ''}</div>${warnings.length ? `<div class="market-explorer-warnings" role="status">${warnings.map(warning => `<p>${E(typeof warning === 'string' ? warning : warning.message || warning.code || '数据需要检查')}</p>`).join('')}</div>` : ''}</div>`;
  }
  function empty(payload, type) {
    return `<div class="market-explorer-empty" role="${payload.error ? 'alert' : 'status'}"><strong>${payload.busy ? '正在整理行情数据…' : payload.error ? '暂时无法读取行情' : '没有符合条件的结果'}</strong><p>${E(payload.error || (payload.busy ? '首次整理全市场日线可能需要一点时间。' : (payload.items || []).length ? '可以更换筛选条件，或清空名称、代码搜索。' : type === 'stocks' ? '连接本地通达信日线目录后，可查看真实研究快照。' : '连接通达信 MCP 后，可读取真实板块和成分。'))}</p>${!payload.busy && (payload.items || []).length ? '<button type="button" data-explorer-reset>清除筛选</button>' : ''}</div>`;
  }
  function stockView(payload = {}) {
    const key = `stocks:${payload.key || payload.kind || 'stocks'}`, state = stateFor(key, payload.kind === 'members' ? 'members' : 'stocks');
    payloads.set(key, {type: 'stocks', payload});
    const items = filteredStocks(payload, state);
    state.offset = Math.max(0, Math.min(state.offset, Math.max(0, Math.ceil(items.length / state.size) - 1) * state.size));
    const visible = items.slice(state.offset, state.offset + state.size);
    return `<section class="panel market-explorer${state.compact ? ' market-explorer-compact' : ''}${state.detailsOpen ? ' market-explorer-details-open' : ''}" data-market-explorer="${E(key)}" data-explorer-type="stocks" aria-busy="${!!payload.busy}">${heading(payload, state, payload.kind === 'members' ? '板块成分' : '证券行情与观察池', 'stocks')}${layoutControls(state)}<div class="market-explorer-pools" aria-label="股票观察池">${pools.map(([value, label]) => choice('pool', value, label, state.pool)).join('')}</div><div class="market-explorer-filters" ${state.filtersOpen ? '' : 'hidden'}>${group('市场', 'board', boards, state.board)}${group('涨跌', 'direction', [['all', '全部'], ['up', '上涨'], ['down', '下跌'], ['flat', '平盘']], state.direction)}<div class="market-explorer-filter-row"><span class="market-explorer-filter-label">形态</span><div class="market-explorer-choices">${choice('above', true, '站上 MA20', state.above)}${choice('volume', true, '放量 ≥ 1.5 倍', state.volume)}</div></div>${state.pool === 'all' ? group('日期', 'freshness', [['latest', '最新同日'], ['older', '较旧 / 缺失'], ['all', '全部日期']], state.freshness) : ''}</div>${summary(payload, items, 'stocks')}<p class="market-explorer-rule">${poolRules[state.pool]} · 排序：正序 → 倒序 → 复原${state.userSort ? ' <button type="button" data-explorer-sort-reset>复原排序</button>' : ''}</p>${visible.length ? `<table class="market-explorer-table">${headers(stocksColumns, state)}<tbody>${visible.map((item, index) => `<tr${/^\d{4}-\d{2}-\d{2}$/.test(day(item.date||payload.asOf))?` data-stock-chart-date="${E(day(item.date||payload.asOf))}"`:''}><td class="market-explorer-rank">${state.offset + index + 1}</td><td class="market-explorer-col-name"><button type="button" class="market-explorer-security" data-stock="${E(item.symbol)}"><strong>${E(item.name || code(item))}</strong><small>${E(code(item))}<span>${E((boards.find(([value]) => value === stockBoard(item)) || [null, item.kind === 'etf' ? 'ETF' : item.kind === 'fund' ? '基金' : ''])[1])}</span></small></button>${AT.marketWatch?.button(item) || ''}${payload.asOf && !isLatest(item, payload) ? `<span class="market-explorer-old">${item.date ? E(day(item.date)) : '日期缺失'}</span>` : ''}</td><td class="market-explorer-col-price">${num(item.close)}</td><td class="market-explorer-col-change market-explorer-${tone(item.changePercent)}">${pct(item.changePercent)}</td><td class="market-explorer-col-return5 market-explorer-${tone(item.return5)}">${pct(item.return5)}</td><td class="market-explorer-col-return20 market-explorer-${tone(item.return20)}">${pct(item.return20)}</td><td class="market-explorer-col-amount">${amount(item.amount)}</td><td class="market-explorer-col-ratio">${num(item.volumeRatio5)}${isNumber(item.volumeRatio5) ? '<small> 倍</small>' : ''}</td><td class="market-explorer-col-trend">${sparkline(item.sparkline, `${item.name || code(item)}近 20 个交易日收盘走势`)}</td></tr>`).join('')}</tbody></table>` : empty(payload, 'stocks')}${pagination(state, items.length)}${footnotes(payload, 'stocks', state)}</section>`;
  }
  function sectorView(payload = {}) {
    const key = `sectors:${payload.key || 'all'}`, state = stateFor(key, 'sectors');
    payloads.set(key, {type: 'sectors', payload});
    const categories = [...new Set((payload.items || []).map(item => item.category).filter(value => typeof value === 'string' && value.trim()))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    if (categories.length && state.category !== 'all' && !categories.includes(state.category)) state.category = 'all';
    const items = filteredSectors(payload, state);
    state.offset = Math.max(0, Math.min(state.offset, Math.max(0, Math.ceil(items.length / state.size) - 1) * state.size));
    const visible = items.slice(state.offset, state.offset + state.size);
    const hasCount = (payload.items || []).some(item => isNumber(item.memberCount));
    const hasTrend = !!AT.sectorTrends || (payload.items || []).some(item => item.sparkline?.length > 1);
    AT.sectorTrends?.setVisible(visible);
    const columns = [['name', '板块名称', 'name'], ['changePercent', '涨跌幅', 'change'], ['amount', '成交额', 'amount'], ...(hasCount ? [['memberCount', '成分数', 'members']] : []), ...(hasTrend ? [['', '近 20 日', 'trend']] : []), ['', '数据时间', 'stamp']];
    return `<section class="panel market-explorer market-explorer-sectors${state.compact ? ' market-explorer-compact' : ''}${state.detailsOpen ? ' market-explorer-details-open' : ''}" data-market-explorer="${E(key)}" data-explorer-type="sectors" aria-busy="${!!payload.busy}">${heading(payload, state, '板块行情', 'sectors')}${layoutControls(state)}<div class="market-explorer-filters" ${state.filtersOpen ? '' : 'hidden'}>${group('涨跌', 'direction', [['all', '全部板块'], ['up', '上涨'], ['down', '下跌'], ['flat', '平盘']], state.direction)}${categories.length ? group('类别', 'category', [['all', '全部'], ...categories.map(value => [value, value])], state.category) : ''}</div>${summary(payload, items, 'sectors')}<p class="market-explorer-rule">排序：正序 → 倒序 → 复原 · 点击名称查看成分股 · 点击走势查看时间轴${state.userSort ? ' <button type="button" data-explorer-sort-reset>复原排序</button>' : ''}${payload.loadingMetrics ? ' · 当前排序仅包含已读取报价，列表会随读取更新' : ''}</p>${visible.length ? `<table class="market-explorer-table">${headers(columns, state)}<tbody>${visible.map((item, index) => `<tr><td class="market-explorer-rank">${state.offset + index + 1}</td><td class="market-explorer-col-name"><button type="button" class="market-explorer-security" data-market-sector="${E(item.id || item.symbol)}"><strong>${E(item.name || item.id)}</strong><small>${E(item.category || code(item))}</small></button>${AT.sectorComparison?.rowButton(item) || ''}</td><td class="market-explorer-col-change market-explorer-${tone(item.changePercent)}">${pct(item.changePercent)}</td><td class="market-explorer-col-amount">${amount(item.amount)}</td>${hasCount ? `<td class="market-explorer-col-members">${num(item.memberCount, 0)}</td>` : ''}${hasTrend ? `<td class="market-explorer-col-trend">${AT.sectorTrends ? AT.sectorTrends.row(item) : sparkline(item.sparkline, `${item.name}近 20 个交易日收盘走势`)}</td>` : ''}<td class="market-explorer-col-stamp">${item.cached ? '<span class="market-explorer-old">旧缓存</span>' : ''}${item.date || item.asOf ? timestamp(item.asOf || item.date) : item.receivedAt ? `<span>采集于 ${timestamp(item.receivedAt)}</span><small>行情时间未提供</small>` : '—'}</td></tr>`).join('')}</tbody></table>` : empty(payload, 'sectors')}${pagination(state, items.length)}${footnotes(payload, 'sectors', state)}</section>`;
  }
  function captureFocus() {
    const input = document.activeElement;
    if (!input?.matches?.('[data-explorer-query]')) return null;
    const key = input.closest('[data-market-explorer]')?.dataset.marketExplorer;
    return key ? {key, start: input.selectionStart, end: input.selectionEnd, direction: input.selectionDirection} : null;
  }
  function restoreFocus(snapshot) {
    if (!snapshot) return;
    const panel = [...document.querySelectorAll('[data-market-explorer]')].find(element => element.dataset.marketExplorer === snapshot.key);
    const input = panel?.querySelector('[data-explorer-query]');
    if (!input) return;
    input.focus({preventScroll: true});
    if (Number.isInteger(snapshot.start) && Number.isInteger(snapshot.end)) input.setSelectionRange(snapshot.start, snapshot.end, snapshot.direction || 'none');
  }
  function refresh(key, input) {
    const state = states.get(key);
    if (state) AT.marketPreferences?.saveList(key, state);
    if (composingInput?.isConnected) return;
    const focus = input ? captureFocus() : null;
    const panel = [...document.querySelectorAll('[data-market-explorer]')].find(element => element.dataset.marketExplorer === key), entry = payloads.get(key);
    if (panel && entry && document.createElement) {
      const active = document.activeElement, dataset = active?.closest?.('[data-market-explorer]') === panel ? {...active.dataset} : null;
      const template = document.createElement('template');
      template.innerHTML = entry.type === 'stocks' ? stockView(entry.payload) : sectorView(entry.payload);
      const next = template.content.firstElementChild;
      if (next) {
        // Keep the live search input attached. Only replace list content, never sibling charts.
        const heading = panel.querySelector('.market-explorer-heading');
        const search = heading?.querySelector('[data-explorer-query]');
        if (search && search.value !== states.get(key)?.query) search.value = states.get(key)?.query || '';
        for (const child of [...panel.children]) if (child !== heading) child.remove();
        for (const child of [...next.children]) if (!child.classList.contains('market-explorer-heading')) panel.append(child);
        panel.className = next.className;
        if (!input && dataset) [...panel.querySelectorAll('button')].find(button => Object.entries(dataset).every(([name, value]) => button.dataset[name] === value))?.focus({preventScroll: true});
        restoreFocus(focus); return;
      }
    }
    AT.render?.();
    restoreFocus(focus);
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-explorer-field],[data-explorer-sort],[data-explorer-page],[data-explorer-reset],[data-explorer-sort-reset]');
    const panel = button?.closest('[data-market-explorer]');
    if (!panel || button.disabled) return;
    const key = panel.dataset.marketExplorer, state = stateFor(key, panel.dataset.explorerType);
    if (button.hasAttribute('data-explorer-reset')) {clearTimeout(state.timer); states.delete(key); AT.marketPreferences?.resetList(key); refresh(key); return;}
    if (button.hasAttribute('data-explorer-sort-reset')) {resetSort(state); state.offset = 0; refresh(key); return;}
    if (button.dataset.explorerSort) {
      const sort = button.dataset.explorerSort;
      if (!state.userSort || state.sort !== sort) {state.sort = sort; state.order = 'asc'; state.userSort = true;}
      else if (state.order === 'asc') state.order = 'desc';
      else resetSort(state);
    } else if (button.dataset.explorerPage) {
      state.offset = Math.max(0, state.offset + Number(button.dataset.explorerPage) * state.size); refresh(key); return;
    } else {
      const field = button.dataset.explorerField, value = button.dataset.explorerValue;
      if (['above','volume','compact','filtersOpen','detailsOpen'].includes(field)) state[field] = !state[field];
      else if (field === 'size') state.size = Number(value);
      else state[field] = value;
      if (field === 'pool') {
        state.freshness = value === 'all' && state.type === 'members' ? 'all' : 'latest';
        state.sort = value === 'gainers' ? 'changePercent' : value === 'volume' ? 'volumeRatio5' : 'amount';
        state.order = 'desc'; state.userSort = false;
        if (value !== 'all' && state.board === 'index') state.board = 'all';
      }
    }
    state.offset = 0; refresh(key);
  });
  function queryChanged(event) {
    if (!event.target.matches('[data-explorer-query]')) return;
    if (event.isComposing || composingInput === event.target) return;
    const input = event.target, panel = input.closest('[data-market-explorer]'), key = panel.dataset.marketExplorer;
    const state = stateFor(key, panel.dataset.explorerType);
    state.query = input.value.slice(0, 100); state.offset = 0;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {if (input.isConnected) refresh(key, input);}, 160);
  }
  document.addEventListener('input', queryChanged);
  document.addEventListener('compositionstart', event => {
    if (!event.target.matches('[data-explorer-query]')) return;
    composingInput = event.target;
    const key = event.target.closest('[data-market-explorer]')?.dataset.marketExplorer;
    clearTimeout(states.get(key)?.timer);
  });
  document.addEventListener('compositionend', event => {if (event.target !== composingInput) return; composingInput = null; queryChanged(event);});
  AT.marketExplorer = {stockView, sectorView, captureFocus, restoreFocus, isComposing: () => !!composingInput?.isConnected};
})();
