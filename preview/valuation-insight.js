/* Source-aligned relative valuation, independent of optional scenario inputs. */
(() => {
  const number = value => value !== null && value !== undefined && value !== '' && typeof value !== 'boolean' && Number.isFinite(Number(value)) ? Number(value) : null;
  const positive = value => number(value) > 0 ? Number(value) : null;
  const day = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
  const symbol = value => typeof value === 'string' && /^(sh|sz|bj)\d{6}$/.test(value.toLowerCase()) ? value.toLowerCase() : null;
  function historicalPosition(history) {
    const currentPE = positive(history?.currentPE);
    const rows = Array.isArray(history?.items) ? history.items.filter(item => day(item.date) && positive(item.peTTM) !== null) : [];
    const unique = new Set(rows.map(item => item.date));
    const endDate = day(history?.endDate);
    const valid = history?.metric === 'provider_industry_pe_ttm' && currentPE !== null && rows.length >= 3 && unique.size === rows.length && endDate && rows.every(item => item.date <= endDate);
    if (!valid || history?.status === 'unavailable') return {available:false, sampleCount:rows.length};
    const latest = rows.find(item => item.date === endDate);
    if (!latest || Math.abs(Number(latest.peTTM) - currentPE) > 1e-8) return {available:false, sampleCount:rows.length};
    const lower = rows.filter(item => Number(item.peTTM) < currentPE).length;
    const equal = rows.filter(item => Number(item.peTTM) === currentPE).length;
    const percentile = (lower + equal / 2) / rows.length * 100;
    return {available:true, currentPE, percentile, sampleCount:rows.length, startDate:rows.map(item => item.date).sort()[0], endDate, stale:!!history.stale};
  }
  function compute(snapshot, benchmark) {
    const result = {available:false, label:'等待估值依据', tone:'neutral', reason:'选择证券并读取财报与同行估值后，自动生成比较结果。', subjectPE:positive(benchmark?.subjectPE), medianPE:positive(benchmark?.medianPE), p25PE:positive(benchmark?.p25PE), p75PE:positive(benchmark?.p75PE), relativePercent:null, sampleCount:number(benchmark?.sampleCount), historical:historicalPosition(benchmark?.historical), stale:!!benchmark?.stale, cached:!!benchmark?.cached, warnings:[], asOf:day(benchmark?.asOf), industry:benchmark?.industry || ''};
    if (!benchmark) return result;
    const identity = symbol(benchmark.symbol), financialIdentity = symbol(snapshot?.symbol);
    if (!identity || snapshot?.symbol && (!financialIdentity || identity !== financialIdentity)) return {...result, historical:{available:false}, label:'暂不作比较', reason:'财报与同行估值的证券身份尚未一致，请重新读取所选证券。'};
    if (benchmark.method !== 'same_day_positive_ttm_pe' || !result.asOf) return {...result, label:'比较口径待核验', reason:'尚未取得同一交易日、同一行业的 PE(TTM) 比较依据。'};
    if (benchmark.status === 'unavailable') return {...result, label:'同行估值暂不可用', reason:'同行数据来源未就绪，暂不生成相对估值结论。'};
    if (snapshot?.status === 'unavailable') result.warnings.push('财报来源暂不可用；以下仅比较估值来源提供的同日 PE(TTM)。');
    if (snapshot?.stale) result.warnings.push('财报为上次缓存；同行比较仍采用其标示交易日的来源 PE(TTM)。');
    const profit = number(snapshot?.ttmNetProfit);
    if (profit !== null && profit <= 0 && !snapshot?.stale) return {...result, label:'正 PE 模型不适用', reason:result.subjectPE !== null ? '最新财报归母净利润 TTM 为零或亏损；来源正 PE 可能采用不同财务时点，暂不据此判断便宜或偏贵。' : '最新财报归母净利润 TTM 为零或亏损，不能使用正 PE 的同行分布判断估值位置。'};
    if (profit !== null && profit <= 0 && snapshot?.stale) result.warnings.push('旧财报 TTM 为零或亏损，与本次正 PE 的财务时点尚未核对；以下比较仅依据本次同行估值来源。');
    if (profit === null) result.warnings.push('财报 TTM 尚未齐备；以下仅比较估值来源提供的同日 PE(TTM)。');
    if (result.subjectPE === null) return {...result, label:'个股 PE 暂不适用', reason:'所选证券的同日 PE(TTM) 缺失、为零或为负，暂不生成相对估值结论。'};
    if (result.sampleCount === null || !Number.isInteger(result.sampleCount) || result.sampleCount < 3) return {...result, label:'同行样本不足', reason:'至少需要 3 个同日正 PE 样本才能判断估值位置。'};
    if ([result.p25PE, result.medianPE, result.p75PE].some(value => value === null) || result.p25PE > result.medianPE || result.medianPE > result.p75PE) return {...result, label:'同行区间待补全', reason:'同行中位数与 25%—75% 分位尚未形成有效区间。'};
    if (Array.isArray(benchmark.items) && benchmark.items.length) {
      const subject = benchmark.items.find(item => symbol(item.symbol) === identity);
      const identities = benchmark.items.map(item => symbol(item.symbol));
      if (!subject || day(subject.asOf) !== result.asOf || positive(subject.peTTM) === null || Math.abs(Number(subject.peTTM) - result.subjectPE) > 1e-8 || benchmark.items.some(item => day(item.asOf) !== result.asOf) || identities.some(value => value === null) || new Set(identities).size !== identities.length || benchmark.items.filter(item => positive(item.peTTM) !== null).length !== result.sampleCount) return {...result, label:'同日数据待核验', reason:'所选证券与同行样本的日期、身份、样本数或 PE 数值不一致，暂不合并得出结论。'};
    }
    result.relativePercent = (result.subjectPE / result.medianPE - 1) * 100;
    if (!Number.isFinite(result.relativePercent)) return {...result, relativePercent:null, label:'比较数值超出范围', reason:'来源数值无法形成有效比例，请重新读取。'};
    result.available = true;
    result.position = result.subjectPE < result.p25PE ? 'below' : result.subjectPE > result.p75PE ? 'above' : 'within';
    result.label = {below:'低于多数同行', within:'处于同行常见区间', above:'高于多数同行'}[result.position];
    result.tone = {below:'low', within:'neutral', above:'high'}[result.position];
    result.reason = '以同行正 PE 样本的 25%—75% 分位作为常见区间。';
    if (snapshot?.announcementDate && String(snapshot.announcementDate).slice(0, 10) > result.asOf) result.warnings.push('财报披露日晚于本次同行估值日；这里只比较该估值日的来源 PE。');
    return result;
  }
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
  const format = (value, digits = 2) => number(value) === null ? '—' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits:digits, maximumFractionDigits:digits});
  const errorMessage = value => typeof value === 'string' ? value : value?.message || (value ? '来源暂不可用' : '');
  const stateFlag = (state, key) => typeof state === 'object' && state !== null ? state[key] : state;
  function waterCard(context, loading, error) {
    const points = positive(context?.indexPoints), baseline = positive(context?.baselinePoints), floor = positive(context?.floorCoefficient), coefficient = positive(context?.coefficient);
    const valid = points !== null && baseline !== null && floor !== null && coefficient !== null && Math.abs(coefficient - Math.max(floor, points / baseline)) < 1e-7 && context?.status !== 'unavailable';
    const isOld = !!context?.stale || !!error;
    const source = typeof context?.sourceUrl === 'string' && /^https?:\/\//.test(context.sourceUrl) ? `<button type="button" class="valuation-insight-source" data-analysis-url="${escape(context.sourceUrl)}">${escape(context.sourceName || '指数来源')}</button>` : escape(context?.sourceName || '等待指数来源');
    return `<section class="valuation-insight-water"><div><span class="valuation-insight-eyebrow">大盘水位 · 模型解释</span><h3>${valid ? `${format(coefficient, 3)}<small> 倍${isOld ? ' · 旧数据参考' : ''}</small>` : loading ? '正在读取指数…' : '指数数据暂不可用'}</h3><p>${valid ? `上证指数 ${format(points)} 点 ÷ ${format(baseline, 0)}，最低取 ${format(floor, 1)}。` : '上证指数 ÷ 3000，最低取 0.5；缺少指数时不默认填 1。'}<br>3000 点对应 1，是原公式的模型约定，不代表公允水位或上涨空间。</p></div><div class="valuation-insight-water-meta"><span>${escape(context?.asOf || '暂无数据日期')} · ${source}</span>${error ? `<small>${escape(errorMessage(error))}${valid ? '，保留上次指数供核对。' : ''}</small>` : ''}${context?.stale ? '<small>指数来源已标记过期，等待更新。</small>' : ''}${valid && !isOld && !loading ? '<button type="button" class="analysis-tools-button" data-analysis-action="water-adopt">用于手动情景</button>' : ''}</div></section>`;
  }
  function panel({snapshot, benchmark, context, loading = {}, error = {}} = {}) {
    const financeError = errorMessage(stateFlag(error, 'snapshot')), benchmarkError = errorMessage(stateFlag(error, 'benchmark'));
    const result = compute(snapshot && financeError ? {...snapshot, stale:true} : snapshot, benchmark);
    const busy = !!(stateFlag(loading, 'snapshot') || stateFlag(loading, 'benchmark'));
    const old = result.stale || result.available && !!benchmarkError;
    const status = old ? '旧数据参考' : busy ? '正在更新' : !result.available && (financeError || benchmarkError) ? '数据暂不可用' : result.cached ? '来源缓存' : '自动计算';
    const reason = !result.available && busy ? '正在核对所选证券与同日同行数据…' : result.reason;
    const relative = result.relativePercent === null ? '—' : Math.abs(result.relativePercent) < .005 ? '与中位数相同' : `${result.relativePercent < 0 ? '折价' : '溢价'} ${format(Math.abs(result.relativePercent), 2)}%`;
    const metrics = result.available ? `<div class="valuation-insight-metrics"><div><span>个股 PE(TTM)</span><strong>${format(result.subjectPE)}<small> 倍</small></strong><small>${escape(result.asOf)} · 同日收盘口径</small></div><div><span>相对同行中位数</span><strong>${relative}</strong><small>同行中位数 ${format(result.medianPE)} 倍</small></div><div><span>同行常见区间</span><strong>${format(result.p25PE)}<small> — </small>${format(result.p75PE)}</strong><small>25%—75% 分位 · ${format(result.sampleCount, 0)} 个正 PE 样本</small></div></div>` : '';
    const warnings = [...result.warnings, ...(old ? ['本次来源未确认更新，现有数值仅供核对原日期，不能当作最新结论。'] : []), ...(financeError ? [`财报：${financeError}`] : []), ...(benchmarkError ? [`同行估值：${benchmarkError}`] : [])];
    const historical = result.historical.available ? `<div class="valuation-insight-history"><span>行业自身历史位置</span><strong>${format(result.historical.percentile, 0)}<small>% 分位</small></strong><p>行业整体 PE ${format(result.historical.currentPE)} 倍 · ${escape(result.historical.startDate)} — ${escape(result.historical.endDate)} · ${format(result.historical.sampleCount, 0)} 个有效交易日${result.historical.stale || old ? ' · 旧数据参考' : ''}<br>历史分位按较低样本加同值样本的一半计算；只比较行业自身，和上方个股在同行中的位置独立。</p></div>` : '<p class="valuation-insight-note">行业历史依据暂未齐备；不影响上方同日同行比较。</p>';
    return `<section class="panel valuation-insight" data-valuation-insight><header class="valuation-insight-header"><div><h2>自动估值观察</h2><p>${escape(result.industry || '同行比较')}${result.asOf ? ` · 截至 ${escape(result.asOf)}` : ''} · 直接读取数据，不需要先填写系数</p></div><span class="valuation-insight-status${old ? ' valuation-insight-old' : ''}">${status}</span></header><div class="valuation-insight-verdict valuation-insight-${result.tone}"><strong>${escape(result.available && old ? `原日期观察：${result.label}` : !result.available && busy ? '正在整理估值依据' : result.label)}</strong><p>${escape(reason)}</p></div>${metrics}${warnings.length ? `<div class="valuation-insight-warnings" role="status">${warnings.map(message => `<p>${escape(message)}</p>`).join('')}</div>` : ''}${result.available ? `<p class="valuation-insight-note">折溢价 = 个股 PE ÷ 同行中位数 − 1。这里是相对行业的位置，不是涨跌预测；当前同行 PE 已包含市场环境，不再叠乘大盘水位。</p>` : ''}${historical}${waterCard(context, !!stateFlag(loading, 'context'), stateFlag(error, 'context'))}</section>`;
  }
  if (typeof module === 'object' && module.exports) module.exports = {compute, historicalPosition, panel};
  if (typeof AT !== 'undefined') AT.valuationInsight = {compute, panel};
})();
