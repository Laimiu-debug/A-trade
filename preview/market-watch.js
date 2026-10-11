/* Quick observations use the same persistent watchlist as the research tools. */
(() => {
  if (!window.AT || !AT.isDesktop) return;
  const E = AT.escape, pending = new Set();
  const statuses = {watching:'观察中', planned:'计划中', holding:'持有中', archived:'已归档'};
  let serial = 0;
  const valid = symbol => typeof symbol === 'string' && /^(sh|sz|bj)\d{6}$/.test(symbol);
  async function call(method, payload = {}) {
    const result = await window.atradeDesktop.market(method, payload);
    if (!result?.ok) throw new Error(typeof result?.error === 'string' ? result.error : result?.error?.message || '跟踪池操作失败，请重试。');
    return result.data;
  }
  function button(item = {}) {
    if (!valid(item.symbol) || item.kind === 'index') return '';
    return `<button type="button" class="market-watch-button" data-market-watch="${E(item.symbol)}" data-market-watch-name="${E(item.name || item.symbol)}" aria-label="跟踪 ${E(item.name || item.symbol)}，记录观察理由">＋ 跟踪</button>`;
  }
  async function open(item) {
    if (!valid(item.symbol)) return;
    const token = ++serial;
    AT.openBusinessModal('加入跟踪池 · ' + (item.name || item.symbol), `<form id="market-watch-form" class="market-watch-form" data-symbol="${E(item.symbol)}" data-token="${token}"><p class="market-watch-current" data-watch-current role="status">正在读取已有观察记录…</p><label class="field"><span>观察理由（可选）</span><textarea name="reason" rows="4" maxlength="2000" placeholder="记录关注原因、待核验的信号或下一步观察条件"></textarea></label><p class="field-hint">已有记录会追加本次理由，保留原备注、标签和跟踪状态。</p><p class="market-watch-error" data-watch-error role="alert"></p></form>`, {className:'market-watch-modal', footer:'<button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary" form="market-watch-form" data-watch-submit disabled>保存观察</button>'});
    const form = document.getElementById('market-watch-form');
    try {
      const data = await call('market.watchlist.list');
      if (document.getElementById('market-watch-form') !== form || token !== serial) return;
      const old = data.items?.find(row => row.symbol === item.symbol);
      form.querySelector('[data-watch-current]').innerHTML = old ? `<strong>已在跟踪池 · ${E(statuses[old.status] || old.status)}</strong>${old.tags?.length ? `<span>${old.tags.map(E).join(' · ')}</span>` : ''}${old.note ? `<details><summary>查看已有备注</summary><p>${E(old.note)}</p></details>` : ''}` : '保存后可在“分析工具 → 跟踪池”继续管理。';
      document.querySelector('[data-watch-submit]').disabled = false;
    } catch (error) {
      if (document.getElementById('market-watch-form') === form) {
        form.querySelector('[data-watch-current]').textContent = '已有记录读取失败';
        form.querySelector('[data-watch-error]').textContent = error.message;
      }
    }
  }
  async function saveObservation(symbol, reason = '') {
    if (!valid(symbol)) throw new Error('证券代码无效。');
    const text = String(reason).trim();
    if (text.length > 2000 || text.includes('\0')) throw new Error('观察理由最多 2000 字。');
    const result = await call('market.watchlist.save', {symbol, ...(text ? {appendNote: text} : {})});
    document.dispatchEvent(new CustomEvent('market-watchlist-changed', {detail: {symbol}}));
    return result;
  }
  document.addEventListener('click', event => {
    const target = event.target.closest?.('[data-market-watch]');
    if (target && !target.disabled) {event.preventDefault(); void open({symbol: target.dataset.marketWatch, name: target.dataset.marketWatchName});}
  });
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form.id !== 'market-watch-form') return;
    event.preventDefault();
    if (form.dataset.saving === 'true') return;
    const submit = document.querySelector('[data-watch-submit]');
    if (!submit || submit.disabled) return;
    form.dataset.saving = 'true'; submit.disabled = true;
    const promise = (async () => {
      try {
        await saveObservation(form.dataset.symbol, form.elements.reason.value);
        if (document.getElementById('market-watch-form') === form) AT.closeBusinessModal();
        AT.toast?.('观察已保存到跟踪池');
      } catch (error) {
        if (document.getElementById('market-watch-form') === form) form.querySelector('[data-watch-error]').textContent = error.message;
        else AT.toast?.(error.message);
        throw error;
      } finally {form.dataset.saving = 'false'; submit.disabled = false;}
    })();
    pending.add(promise); promise.then(() => pending.delete(promise), () => pending.delete(promise));
  });
  AT.marketWatch = {button, open, saveObservation, async flush() {return (await Promise.allSettled([...pending])).every(result => result.status === 'fulfilled');}};
})();
