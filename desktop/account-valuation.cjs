// Read existing market configuration only. Prices remain a preview until snapshot.save.
const MAX_POSITIONS = 100;
const BAR_LIMIT = 240;
const CONCURRENCY = 2;

function validDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function symbolForCode(code) {
  if (!/^\d{6}$/.test(code)) return null;
  if (/^(60|68|5)/.test(code)) return `sh${code}`;
  if (/^(00|30|15|16|18)/.test(code)) return `sz${code}`;
  if (/^(43|83|87|88|92)/.test(code)) return `bj${code}`;
  // Unknown instruments need an explicit price instead of guessing an exchange.
  return null;
}

function createAccountValuation({ business, market }) {
  let pending = null;
  async function evaluate({ accountId, date }) {
    const asof = await business.request('account.asof', { accountId, date });
    if (!asof?.ok) return asof || { ok: false, error: '无法读取历史持仓。' };
    if (asof.data?.accountId !== accountId || asof.data?.date !== date || !Array.isArray(asof.data.positions)) {
      return { ok: false, error: '历史持仓与所选账户或日期不一致，请重新读取。' };
    }
    const positions = asof.data.positions;
    if (positions.length > MAX_POSITIONS) return { ok: false, error: `一次最多读取 ${MAX_POSITIONS} 个持仓的行情，请手工填写价格。` };
    const data = { accountId, date, positions, prices: {}, priceDetails: {}, missingPrices: [], warnings: [] };
    if (!positions.length) return { ok: true, data };
    const codes = [...new Set(positions.map(item => item.code))];
    if (codes.some(code => typeof code !== 'string' || !/^\d{6}$/.test(code))) {
      return { ok: false, error: '持仓证券代码格式无效，请先核对账本。' };
    }
    let status;
    try { status = await market.request('market.status', {}); } catch { /* Leave prices empty. */ }
    if (!status?.ok || !(status.data?.configured || status.data?.online?.enabled)) {
      data.missingPrices = codes;
      data.warnings.push('行情尚未配置或无法读取，请到行情设置选择已有数据源，或手工填写价格。');
      return { ok: true, data };
    }
    const results = new Array(codes.length);
    let next = 0;
    async function worker() {
      while (next < codes.length) {
        const index = next++, code = codes[index], symbol = symbolForCode(code);
        if (!symbol) { results[index] = { warning: `${code}：无法确认证券市场，请手工核对价格。` }; continue; }
        try {
          const response = await market.request('market.bars', { symbol, period: '1d', limit: BAR_LIMIT, end: date, adjustment: 'none' });
          if (!response?.ok) throw new Error('行情读取失败，请检查已配置的数据源。');
          const bars = response.data;
          if (bars?.symbol !== symbol || bars.adjustment !== 'none' || !Array.isArray(bars.rows) || bars.rows.length > BAR_LIMIT) {
            throw new Error('行情身份、复权方式或返回数量不符合估值要求，未采用价格。');
          }
          const rows = bars.rows.filter(row => validDate(row?.date) && row.date <= date &&
            typeof row.close === 'number' && Number.isFinite(row.close) && row.close > 0);
          const latest = rows.reduce((last, row) => !last || row.date > last.date ? row : last, null);
          if (!latest) throw new Error('目标日期及以前没有有效未复权收盘价，请手工核对。');
          const source = typeof bars.provider === 'string' && /^[a-z0-9_-]{1,60}$/i.test(bars.provider) ? bars.provider : 'tdx_day';
          const stale = bars.stale === true || latest.date < date;
          const warnings = (Array.isArray(bars.warnings) ? bars.warnings : []).filter(item => typeof item === 'string').slice(0, 3).map(item => `${code}：${item.slice(0, 200)}`);
          if (stale) warnings.unshift(`${code}：采用 ${latest.date} 的收盘价${bars.stale ? '，源数据尚未更新' : ''}，请核对后保存。`);
          results[index] = { price: latest.close, details: { source, priceDate: latest.date, requestedDate: date, adjustment: 'none', stale }, warnings };
        } catch (error) {
          results[index] = { warning: `${code}：${error.message || '行情读取失败，请手工核对价格。'}` };
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, codes.length) }, worker));
    codes.forEach((code, index) => {
      const result = results[index];
      if (result.price !== undefined) {
        data.prices[code] = result.price;
        data.priceDetails[code] = result.details;
        data.warnings.push(...result.warnings);
      } else { data.missingPrices.push(code); data.warnings.push(result.warning); }
    });
    return { ok: true, data };
  }
  return async function valuation(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
        typeof payload.accountId !== 'string' || !payload.accountId || payload.accountId.length > 100 || !validDate(payload.date)) {
      return { ok: false, error: '请选择有效账户和估值日期。' };
    }
    const { accountId, date } = payload, key = `${accountId}|${date}`;
    if (pending) return pending.key === key ? pending.promise : { ok: false, error: '已有估值正在读取，请完成后再更改账户或日期。' };
    const promise = evaluate({ accountId, date }).catch(() => ({ ok: false, error: '无法读取账户估值，原始账本未更改。' }));
    pending = { key, promise };
    try { return await promise; } finally { if (pending?.promise === promise) pending = null; }
  };
}

module.exports = { createAccountValuation, symbolForCode, MAX_POSITIONS, BAR_LIMIT, CONCURRENCY };
