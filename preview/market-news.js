/* Publisher summaries stay in-page; full documents open only on an explicit click. */
(() => {
  if (!AT.isDesktop) return;
  const E = AT.escape, bridge = window.atradeDesktop;
  const state = {source: 'all', query: '', symbol: '', queryDraft: '', symbolDraft: '', page: 1};
  let data = null, busy = false, error = '', requestKey = '', serial = 0, choices = [], checkedAt = null;
  const CACHE_MS = 60 * 1000;
  const stamp = value => value && Number.isFinite(new Date(value).valueOf()) ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '来源未提供时间';
  const visible = () => AT.state.page === 'M05';
  const redraw = () => {if (visible()) AT.render?.();};
  async function call(method, payload) {
    const result = await bridge.market(method, payload);
    if (!result?.ok) throw new Error(result?.error || '资讯读取失败，请稍后重试。');
    return result.data;
  }
  async function load(refresh = false) {
    const payload = {source: state.source, query: state.query, ...(state.symbol ? {symbol: state.symbol} : {}), page: state.page, refresh};
    const key = JSON.stringify({...payload, refresh: false});
    if (busy || !refresh && requestKey === key && checkedAt !== null && Date.now() - checkedAt < CACHE_MS) return;
    if (key !== requestKey) data = null;
    requestKey = key; busy = true; error = ''; const current = ++serial; redraw();
    try {const result = await call('market.news.list', payload); if (current === serial) data = result;}
    catch (failure) {if (current === serial) error = failure.message;}
    finally {if (current === serial) {checkedAt = Date.now(); busy = false; redraw();}}
  }
  async function search() {
    if (busy) return;
    const raw = state.symbolDraft.trim().toLowerCase();
    choices = []; error = '';
    if (raw && !/^(sh|sz|bj)\d{6}$/.test(raw)) {
      busy = true; redraw();
      try {
        const result = await call('market.search', {query: raw, limit: 100});
        choices = result.items.filter(item => item.kind === 'stock' && (!/^\d{6}$/.test(raw) || item.code === raw));
        if (choices.length !== 1) {
          error = choices.length ? '请选择要查看的股票。' : '本地目录未找到股票。也可填写带市场的代码，例如 sh600900。';
          busy = false; redraw(); return;
        }
        state.symbol = choices[0].symbol; choices = [];
      } catch (failure) {error = failure.message; busy = false; redraw(); return;}
      busy = false;
    } else state.symbol = raw;
    state.query = state.queryDraft.trim(); state.page = 1; requestKey = ''; await load();
  }
  function view() {
    queueMicrotask(() => {if (visible()) load();});
    const items = data?.items || [], messages = [...(data?.warnings || []), ...(data?.errors || []).map(item => `${item.provider}：${item.message}`)];
    return AT.head('资讯中心', '市场快讯与公司公告 · 保留发布时间和原始出处', AT.button(busy ? '读取中…' : '刷新资讯', 'refresh', `data-market-news-action="refresh" ${busy ? 'disabled' : ''}`))
      + `<section class="panel market-news-panel"><div class="market-news-toolbar"><div class="market-news-tabs" aria-label="资讯来源">${[['all', '全部来源'], ['eastmoney', '市场快讯'], ['announcements', '公司公告']].map(([key, title]) => `<button type="button" data-market-news-source="${key}" aria-pressed="${state.source === key}" ${busy ? 'disabled' : ''}>${title}</button>`).join('')}</div><span class="market-news-scope">近期来源条目 · 按发布时间排序</span></div><form id="market-news-search"><label><span>关键词</span><input name="query" data-market-news-input="query" value="${E(state.queryDraft)}" placeholder="在已接入的近期资讯中检索" maxlength="100"></label><label><span>关联股票</span><input name="symbol" data-market-news-input="symbol" value="${E(state.symbolDraft)}" placeholder="名称 / 代码，可留空" maxlength="40"></label><button type="submit" class="btn primary" ${busy ? 'disabled' : ''}>查找</button><button type="button" class="btn" data-market-news-action="clear" ${busy ? 'disabled' : ''}>清空</button></form>${choices.length ? `<div class="market-news-choices">${choices.slice(0, 20).map(item => `<button type="button" data-market-news-symbol="${E(item.symbol)}">${E(item.name)} · ${E(item.code)}</button>`).join('')}</div>` : ''}${error ? `<p class="market-warning" role="alert">${E(error)}</p>` : ''}<div class="market-news-sources">${(data?.sources || []).map(source => `<span><i class="${source.status === 'unavailable' || source.stale ? 'unavailable' : ''}"></i>${E(source.sourceName)} · ${source.status === 'unavailable' ? '暂不可用' : source.stale ? '保留旧缓存' : source.cached ? '已缓存' : '已更新'}${source.receivedAt ? ' · ' + E(stamp(source.receivedAt)) : ''}</span>`).join('')}</div><div class="market-news-list" aria-busy="${busy}">${items.length ? items.map(item => `<article class="market-news-item"><div class="market-news-meta"><span>${E(item.sourceName)}</span><time>${E(stamp(item.publishedAt))}</time></div><h2>${E(item.title)}</h2>${item.summary ? `<p>${E(item.summary)}</p>` : '<p class="market-news-no-summary">来源未提供摘要，正文可查看原始公告。</p>'}<div class="market-news-item-actions">${item.url ? `<button type="button" data-market-news-url="${E(item.url)}">打开原文 ${AT.icon('link')}</button>` : '<span>原文链接缺失</span>'}${item.symbol ? `<button type="button" data-stock="${E(item.symbol)}">查看个股</button>` : ''}</div></article>`).join('') : `<div class="market-empty compact"><h2>${busy ? '正在读取公开资讯…' : data?.status === 'unavailable' ? '资讯来源暂时无法连接' : '没有匹配的近期资讯'}</h2><p>${busy ? '快讯与公告独立读取，来源状态会显示在列表上方。' : '可更换来源、关键词或股票后重试。'}</p></div>`}</div><div class="market-news-pagination"><span>${data ? `${data.total} 条匹配资讯 · 第 ${data.page} 页` : ''}</span><div><button type="button" data-market-news-page="-1" ${busy || state.page <= 1 ? 'disabled' : ''}>上一页</button><button type="button" data-market-news-page="1" ${busy || !data?.hasMore ? 'disabled' : ''}>下一页</button></div></div>${messages.length ? `<div class="market-news-notes">${messages.map(message => `<p>${E(message)}</p>`).join('')}</div>` : ''}</section>`;
  }
  const previousView = AT.view;
  AT.view = page => page === 'M05' ? view() : previousView(page);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && visible()) load(); });
  window.addEventListener?.('focus', () => { if (visible()) load(); });
  document.addEventListener('input', event => {
    const key = event.target.dataset.marketNewsInput;
    if (key === 'query') state.queryDraft = event.target.value;
    if (key === 'symbol') state.symbolDraft = event.target.value;
  });
  document.addEventListener('submit', event => {
    if (event.target.id !== 'market-news-search') return;
    event.preventDefault(); event.stopPropagation(); search();
  }, true);
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-market-news-source],[data-market-news-action],[data-market-news-page],[data-market-news-url],[data-market-news-symbol]');
    if (!button || button.disabled) return;
    event.preventDefault(); event.stopPropagation();
    if (button.dataset.marketNewsUrl) {
      try {
        const result = await bridge.openExternal(button.dataset.marketNewsUrl);
        if (!result.ok) {error = result.error; redraw();}
      } catch (failure) {error = failure.message || '原文暂时无法打开。'; redraw();}
      return;
    }
    if (button.dataset.marketNewsSymbol) {state.symbolDraft = button.dataset.marketNewsSymbol; await search(); return;}
    if (button.dataset.marketNewsSource) {state.source = button.dataset.marketNewsSource; state.page = 1; choices = []; await load(); return;}
    if (button.dataset.marketNewsPage) {state.page += Number(button.dataset.marketNewsPage); await load(); return;}
    if (button.dataset.marketNewsAction === 'clear') {Object.assign(state, {query: '', queryDraft: '', symbol: '', symbolDraft: '', page: 1}); choices = []; await load(); return;}
    await load(true);
  }, true);
  AT.marketNews = {updateShell(page) {
    if (page !== 'M05') return;
    const label = document.querySelector('.demo-label'), status = document.querySelector('.statusbar>span:first-child');
    if (label) label.textContent = '公开资讯 · 来源可追溯';
    if (status) status.textContent = '东方财富快讯 / 公司公告 · 原文在浏览器打开';
  }};
})();
