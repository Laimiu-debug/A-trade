/* Whole-universe research editor. Only the Python service produces results. */
(() => {
  'use strict';
  const percentKeys = new Set(['maxStockWeight','switchThreshold','stopLossPct','takeProfitPct','trailingStopPct','commissionRate','transferFeeRate','stampDutyRate','maxParticipationRate','minReturn','minDrawdown','maxDrawdown','minDominance']);
  const numeric = value => typeof value !== 'boolean' && value !== null && value !== '' && Number.isFinite(Number(value));
  const isPercent = path => percentKeys.has(path.split('.').at(-1));
  const display = (path, value) => numeric(value) ? +(Number(value) * (isPercent(path) ? 100 : 1)).toFixed(8) : '';
  const stored = (path, value) => value === '' ? null : Number(value) / (isPercent(path) ? 100 : 1);
  function symbols(text) {
    return [...new Set(String(text || '').split(/[\s,，;；、]+/).filter(Boolean).map(value => {
      value = value.toLowerCase();
      if (!/^(?:(?:sh|sz|bj))?\d{6}$/.test(value)) throw new Error('无效股票代码：' + value);
      return /^(sh|sz|bj)/.test(value) ? value : (/^(4|8|92)/.test(value) ? 'bj' : /^[569]/.test(value) ? 'sh' : 'sz') + value;
    }))];
  }
  const defaultsStudy = () => ({trainEnd:'',testStart:'',testEnd:'',persistenceWindow:20,minWindows:7,minRankCorrelation:.2,minPositivePairFraction:.6,permutationCount:999,seed:1729,alpha:.05});
  function payload(draft, catalogue) {
    const config = structuredClone(draft.config), selection = structuredClone(draft.selection);
    const picked = catalogue.filter(s => draft.enabled[s.id]);
    if (!picked.length) throw new Error('至少启用一个基础策略。');
    config.strategies = picked.map(s => ({id:s.id,kind:s.id,pool:draft.pools[s.id] || 'base',params:structuredClone(draft.params[s.id])}));
    config.combinations = Object.entries(draft.combinations).filter(([,c]) => c.enabled).map(([method,c]) => ({
      id:'combo_' + method, method, normalization:'rank', threshold:Number(c.threshold || 0),
      children:picked.map(s => ({id:s.id,weight:Number(draft.weights[s.id] ?? 1)})),
    }));
    if (config.combinations.length && picked.length < 2) throw new Error('组合策略至少启用两个子策略。');
    const strategyIds = [...config.strategies.map(s => s.id), ...config.combinations.map(s => s.id)].filter(id => draft.participates[id] !== false);
    if (!strategyIds.length) throw new Error('请选择至少一个参与配权的基础或组合策略。');
    if (config.allocation.mode === 'single' && strategyIds.length !== 1) throw new Error('固定策略模式只能勾选一个参与配权的策略。');
    config.allocation.strategyIds = strategyIds;
    if (draft.periodMode === 'recent') config.startDate = config.endDate = null;
    else if (!config.startDate || !config.endDate || config.startDate > config.endDate) throw new Error('请填写有效的起止日期。');
    selection.symbols = selection.mode === 'explicit' ? symbols(draft.symbols) : [];
    if (selection.mode === 'explicit' && !selection.symbols.length) throw new Error('指定股票池不能为空。');
    if (!selection.markets.length || !selection.boards.length) throw new Error('请至少选择一个市场和一个板块。');
    if (draft.kind === 'comparison') {
      if (draft.periodMode !== 'dates') throw new Error('训练与样本外比较需要选择明确日期区间。');
      config.study = structuredClone(draft.study);
    } else delete config.study;
    if (draft.name.trim().length > 100) throw new Error('研究名称最多 100 个字符。');
    selection.lookbackDays = config.lookbackDays;
    selection.benchmarkSymbol = selection.benchmarkSymbol?.trim().toLowerCase() || null;
    return {kind:draft.kind, name:draft.name.trim(), config, selection};
  }
  const helpers = {numeric,isPercent,display,stored,symbols,payload};
  if (typeof module === 'object' && module.exports) module.exports = helpers;
  if (typeof window === 'undefined' || typeof AT === 'undefined') return;
  const E = AT.escape, key = 'atrade.portfolio-editor.v1';
  let definition, hooks, draft, dayIndex = 0;
  const n = (v,d=2) => numeric(v) ? Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d}) : '—';
  const p = v => numeric(v) ? n(v) + '%' : '—';
  const field = (label,path,value,attrs='') => `<label class="rs-field"><span>${E(label)}</span><input data-pf-field="${E(path)}" type="number" step="any" value="${E(display(path,value))}" ${attrs}></label>`;
  const textField = (label,path,value,type='text') => `<label class="rs-field"><span>${E(label)}</span><input data-pf-field="${E(path)}" data-pf-type="text" type="${type}" ${path==='name'?'maxlength="100"':''} value="${E(String(value || ''))}"></label>`;
  const select = (label,path,value,options) => `<label class="rs-field"><span>${E(label)}</span><select data-pf-field="${E(path)}" data-pf-type="text">${Object.entries(options).map(([v,t]) => `<option value="${E(v)}" ${v===value?'selected':''}>${E(t)}</option>`).join('')}</select></label>`;
  const check = (label,path,value) => `<label class="pf-check"><input type="checkbox" data-pf-field="${E(path)}" data-pf-type="boolean" ${value?'checked':''}>${E(label)}</label>`;
  const note = text => `<div class="rs-notice">${E(text)}</div>`;
  const table = (headers,rows) => `<div class="rs-table-wrap"><table class="rs-table"><thead><tr>${headers.map(h=>`<th>${E(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.join(''):`<tr><td colspan="${headers.length}">没有记录</td></tr>`}</tbody></table></div>`;
  const panel = (title,body) => `<section class="rs-panel"><header><h2>${E(title)}</h2></header>${body}</section>`;
  function makeDraft(data) {
    const config=structuredClone(data.defaults), enabled={},params={},pools={},weights={},participates={};
    for (const s of data.strategies) {
      const selected=config.strategies.find(r=>r.kind===s.id);
      enabled[s.id]=!!selected;
      params[s.id]={...Object.fromEntries(s.parameters.map(p=>[p.key,p.default])),...selected?.params};
      pools[s.id]=selected?.pool||'base';weights[s.id]=1;participates[s.id]=true;
    }
    return {kind:'portfolio',name:'',periodMode:'recent',config,selection:structuredClone(data.selection),symbols:'',enabled,params,pools,weights,participates,
      combinations:Object.fromEntries(['intersection','union','weighted'].map(k=>[k,{enabled:false,threshold:0}])),study:defaultsStudy()};
  }
  function configure(data, callbacks) {
    if (!data) return;
    hooks=callbacks;definition=data;
    if (draft) return;
    draft=makeDraft(data);
    try {
      const saved=JSON.parse(localStorage.getItem(key));
      if (saved?.version===data.version && saved.draft && Array.isArray(saved.draft.selection?.markets)) {
        // Corrupt or incomplete old local drafts must not blank the editor.
        const candidate=saved.draft;
        if (candidate.config?.universe && candidate.config?.poolSettings && candidate.config?.allocation &&
            Array.isArray(candidate.selection.boards) && candidate.enabled && candidate.params && candidate.pools &&
            candidate.weights && candidate.participates && candidate.combinations && candidate.study &&
            typeof candidate.name==='string' && typeof candidate.symbols==='string' &&
            data.strategies.every(s=>candidate.params[s.id]) && ['intersection','union','weighted'].every(k=>candidate.combinations[k])) draft=candidate;
      }
    } catch {}
  }
  function save() { try {localStorage.setItem(key,JSON.stringify({version:definition.version,draft}));} catch {} }
  function strategyFields(s) {
    const enabled=draft.enabled[s.id];
    return `<article class="pf-strategy"><header>${check(s.name,'enabled.'+s.id,enabled)}${enabled?check('参与配权 / 比较','participates.'+s.id,draft.participates[s.id]!==false):''}</header><p class="rs-help">${E(s.description)}</p>${enabled?`<div class="rs-fields">${select('使用股票池','pools.'+s.id,draft.pools[s.id],{base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池'})}${field('组合内子策略权重','weights.'+s.id,draft.weights[s.id],'min="0.000001"')}${s.parameters.map(v=>field(v.label+(isPercent(v.key)?'（%）':''),'params.'+s.id+'.'+v.key,draft.params[s.id][v.key],`min="${display(v.key,v.min)}" max="${display(v.key,v.max)}"`)).join('')}</div>`:''}</article>`;
  }
  function form() {
    if (!draft) return note('正在读取组合回测设置…');
    const c=draft.config;
    const portfolios=Object.entries(draft.combinations).map(([method,v]) => `<div class="pf-combination">${check({intersection:'交集组合',union:'并集组合',weighted:'加权打分组合'}[method],'combinations.'+method+'.enabled',v.enabled)}${v.enabled?check('参与配权 / 比较','participates.combo_'+method,draft.participates['combo_'+method]!==false)+field('标准化最低分（0–1）','combinations.'+method+'.threshold',v.threshold,'min="0" max="1"'):''}</div>`).join('');
    return `<form data-pf-form class="pf-workbench"><div class="pf-intro"><strong>股票池 → 每日评分 → 多股组合 → 次日执行</strong><p>保存的设置下次打开仍可使用。先运行组合回测，再做策略比较与持续性检验。</p></div><fieldset class="rs-fieldset"><legend>研究与区间</legend><div class="rs-fields">${textField('研究名称（可留空）','name',draft.name)}${select('任务类型','kind',draft.kind,{portfolio:'股票池组合回测',comparison:'多策略比较与持续性检验'})}${select('回测区间','periodMode',draft.periodMode,{recent:'最近 N 个交易日',dates:'指定起止日期'})}${draft.periodMode==='recent'?field('交易日数量','config.lookbackDays',c.lookbackDays,'min="2"'):textField('开始日期','config.startDate',c.startDate,'date')+textField('结束日期','config.endDate',c.endDate,'date')}${field('初始资金（元）','config.initialCapital',c.initialCapital,'min="1000"')}${textField('业绩基准代码（可留空，如 sh000300）','selection.benchmarkSymbol',draft.selection.benchmarkSymbol)}</div><p class="rs-help">最近交易日以所读行情的最新日期为止。未选择基准时，基准收益与市场环境留空。</p></fieldset><fieldset class="rs-fieldset"><legend>股票池来源与基础资格</legend><div class="rs-fields">${select('股票池范围','selection.mode',draft.selection.mode,{all:'全部 A 股（已配置行情源）',explicit:'指定股票列表'})}${draft.selection.mode==='explicit'?textField('股票代码，逗号或空格分隔','symbols',draft.symbols):''}${field('最少上市交易日','config.universe.minListingDays',c.universe.minListingDays,'min="0"')}${field('20 日平均成交额下限（元）','config.universe.minAmount20',c.universe.minAmount20,'min="0"')}</div><div class="pf-options">${[['sh','沪市'],['sz','深市'],['bj','北交所']].map(([v,t])=>check(t,'market.'+v,draft.selection.markets.includes(v))).join('')}${[['main','主板'],['chinext','创业板'],['star','科创板'],['bse','北交所板块']].map(([v,t])=>check(t,'board.'+v,draft.selection.boards.includes(v))).join('')}</div><div class="pf-options">${check('剔除已知历史 ST','config.universe.excludeSt',c.universe.excludeSt)}${check('严格历史资格（缺历史事实则剔除）','config.universe.strictHistory',c.universe.strictHistory)}</div><p class="rs-help">读取所有符合范围的文件，分批处理后统一排名。报告列出读入、排除与缺失数量；本地现存文件不等于无幸存者偏差的历史全市场。</p><details><summary>分层池过滤参数</summary><div class="rs-fields">${field('趋势窗口','config.poolSettings.trend.window',c.poolSettings.trend.window)}${field('趋势最低涨幅（%）','config.poolSettings.trend.minReturn',c.poolSettings.trend.minReturn)}${field('结构窗口','config.poolSettings.structure.window',c.poolSettings.structure.window)}${field('结构最低回撤（%）','config.poolSettings.structure.minDrawdown',c.poolSettings.structure.minDrawdown)}${field('结构最高回撤（%）','config.poolSettings.structure.maxDrawdown',c.poolSettings.structure.maxDrawdown)}${field('量能观察窗口','config.poolSettings.volume.window',c.poolSettings.volume.window)}${field('量能最低倍数','config.poolSettings.volume.minRatio',c.poolSettings.volume.minRatio)}</div></details></fieldset><fieldset class="rs-fieldset"><legend>基础策略与组合策略</legend>${definition.strategies.map(strategyFields).join('')}<div class="pf-combinations">${portfolios}</div><p class="rs-help">组合使用启用的基础策略。先将同日分数转为排名百分位，再做交集、并集或加权；可以只让组合策略参与配权。</p></fieldset><fieldset class="rs-fieldset"><legend>持仓与调仓</legend><div class="rs-fields">${select('策略之间的配权','config.allocation.mode',c.allocation.mode,{equal:'参与策略等权',single:'固定一个基础或组合策略'})}${select('调仓方式','config.rebalanceMode',c.rebalanceMode,{risk:'风险退出后补仓',rank:'按排名换仓',hybrid:'排名换仓 + 风险退出'})}${field('每策略候选 Top N','config.topN',c.topN,'min="1" max="100"')}${field('组合最多持股数','config.maxPositions',c.maxPositions,'min="1" max="200"')}${field('单股新买目标上限（%）','config.maxStockWeight',c.maxStockWeight,'min="0.1" max="100"')}${select('策略内股票权重','config.weighting',c.weighting,{equal:'等权',rank:'按排名分数加权'})}${field('补仓 / 排名调整间隔（交易日）','config.rebalanceEvery',c.rebalanceEvery,'min="1"')}${field('排名换仓最小分差（百分点）','config.switchThreshold',c.switchThreshold,'min="0" max="100"')}${field('止损（%，0 关闭）','config.stopLossPct',c.stopLossPct)}${field('从持仓收盘峰值回撤（%，0 关闭）','config.trailingStopPct',c.trailingStopPct)}${field('止盈（%，0 关闭）','config.takeProfitPct',c.takeProfitPct)}${field('最长持仓交易日（0 关闭）','config.maxHoldingDays',c.maxHoldingDays)}</div><p class="rs-help">每日评分独立于调仓频率；风险每日在收盘检查，触发后下一交易日开盘尝试执行。风险退出模式保留旧股股数，事件信号消失不会自动卖出。Top 1 仅作对照。</p></fieldset><fieldset class="rs-fieldset"><legend>交易成本</legend><div class="rs-fields">${field('佣金（%）','config.commissionRate',c.commissionRate)}${field('最低佣金（元）','config.minCommission',c.minCommission)}${field('卖出印花税（%，空白按日期）','config.stampDutyRate',c.stampDutyRate)}${field('过户费（%）','config.transferFeeRate',c.transferFeeRate)}${field('单边滑点（基点）','config.slippageBps',c.slippageBps)}${field('上一交易日成交量参与上限（%）','config.maxParticipationRate',c.maxParticipationRate)}</div></fieldset>${draft.kind==='comparison'?`<fieldset class="rs-fieldset"><legend>训练、样本外与持续性</legend><div class="rs-fields">${textField('训练结束日期','study.trainEnd',draft.study.trainEnd,'date')}${textField('样本外开始日期','study.testStart',draft.study.testStart,'date')}${textField('样本外结束日期','study.testEnd',draft.study.testEnd,'date')}${field('持续性窗口 K（交易日）','study.persistenceWindow',draft.study.persistenceWindow,'min="2"')}${field('至少非重叠窗口数','study.minWindows',draft.study.minWindows,'min="7"')}${field('最低平均排名相关性','study.minRankCorrelation',draft.study.minRankCorrelation,'min="0" max="1"')}${field('正相关窗口对占比下限','study.minPositivePairFraction',draft.study.minPositivePairFraction,'min="0.5" max="1"')}</div><p class="rs-help">训练区选择固定策略并检验持续性，样本外只比较冻结策略与等权组合。至少三个有效不同策略、六对窗口；39 日测试预设通常不足以得出持续性结论。</p></fieldset>`:''}<div class="rs-actions pf-submit"><button type="submit" class="rs-button rs-primary">保存设置并创建任务</button><button type="button" class="rs-button" data-pf-action="save">保存设置</button><button type="button" class="rs-button" data-pf-action="reset">恢复测试预设</button><span class="rs-help">计算在本机后台执行，可暂停、继续和停止。</span></div></form>`;
  }
  const reasonLabels = {stopLoss:'止损',takeProfit:'止盈',trailingStop:'回撤退出',maxHoldingDays:'最长持仓',insufficient:'证据不足',passed:'通过训练诊断',failed:'未通过',complete:'已完成诊断',running:'计算中',eligible:'合格',unknown:'未知',up:'上行',down:'下行',mixed:'震荡',high:'高',normal:'中',medium:'中',low:'低',observed:'已观测',not_implemented:'尚未实现'};
  const readable = v => typeof v==='string' ? reasonLabels[v]||v : JSON.stringify(v ?? null);
  function metricTable(results) {
    return table(['方案','累计收益','年化','最大回撤','夏普','完整持仓波段胜率','换手','费用（元）','滑点（元）','成本 / 初始资金'],results.map(([label,r])=>{const m=r?.metrics||{};return `<tr><td>${E(label)}</td><td>${p(m.totalReturnPct)}</td><td>${p(m.annualizedReturnPct)}</td><td>${p(m.maxDrawdownPct)}</td><td>${n(m.sharpe ?? m.sharpeRatio)}</td><td>${p(m.winRatePct)}<small>${numeric(m.closedTradeCount)?n(m.closedTradeCount,0)+' 个已关闭波段':'暂无关闭波段数'}</small></td><td>${p(m.turnoverPct)}</td><td>${n(m.totalFees ?? m.totalCosts)}</td><td>${n(m.totalSlippage)}</td><td>${p(m.costRatioPct)}</td></tr>`;}))+`<p class="rs-help pf-metric-note">胜率按完整关闭的持仓波段统计；费用与滑点均计入净收益，成本占比为两者合计除以初始资金，滑点已包含在成交价中。</p>`;
  }
  function regimeTable(r) {
    const rows=(r.regimeMetrics||r.metrics?.regimeMetrics||[]).map(v=>`<tr><td>${E(String(v.regime).split('/').map(readable).join(' / '))}</td><td>${n(v.sampleDays,0)}</td><td>${p(v.subsampleReturnPct)}</td><td>${p(v.meanDailyReturnPct)}</td><td>${p(v.conditionalAnnualizedReturnPct)}</td></tr>`);
    return panel('按真实市场环境分组的收益',table(['前一收盘市场环境','样本日','子样本复合收益','平均日收益','条件年化'],rows)+`<p class="rs-help pf-metric-note">环境由所选真实指数的前一收盘状态确定。分组可能跨越不连续日期，条件年化不是一条连续交易策略的年化收益，也不参与配权。</p>`);
  }
  function equityChart(r,title='账户净值与回撤') {
    const rows=r?.curve||[],equities=rows.flatMap(v=>[v.equity,v.benchmarkEquity].filter(numeric).map(Number));if(!equities.length)return note('尚无实际账户曲线。');
    const low=Math.min(...equities),high=Math.max(...equities),span=Math.max(1,high-low),actualMaxDd=Math.max(0,...rows.map(v=>numeric(v.drawdownPct)?Number(v.drawdownPct):0)),maxDd=Math.max(1,actualMaxDd);
    const x=i=>48+i*920/Math.max(1,rows.length-1),y=v=>160-(v-low)/span*130;
    const line=(key,project)=>{let started=false;return rows.map((v,i)=>{if(!numeric(v[key])){started=false;return '';}const segment=`${started?'L':'M'}${x(i).toFixed(2)},${project(Number(v[key])).toFixed(2)}`;started=true;return segment;}).join(' ');};
    return panel(title,`<div class="rs-body"><svg viewBox="0 0 1000 290" class="pf-equity" role="img" aria-label="逐日账户净值与回撤"><text x="48" y="18" fill="currentColor">净值（元） · 蓝色账户 / 灰色已选真实基准</text><line x1="48" y1="160" x2="968" y2="160" stroke="currentColor" opacity=".2"/><path d="${line('equity',y)}" class="pf-account-curve" fill="none" stroke="var(--accent,#68a9ff)" stroke-width="2.5"/><path d="${line('benchmarkEquity',y)}" class="pf-benchmark-curve" fill="none" stroke="currentColor" opacity=".45" stroke-width="1.5"/><text x="48" y="190" fill="currentColor">回撤（%） · 最大 ${n(actualMaxDd)}%</text><path d="${line('drawdownPct',v=>205+v/maxDd*45)}" class="pf-drawdown-curve" fill="none" stroke="#e6ac69" stroke-width="2"/><text x="48" y="280" fill="currentColor">${E(rows[0].date)}</text><text x="968" y="280" text-anchor="end" fill="currentColor">${E(rows.at(-1).date)}</text></svg><p class="rs-help">净值范围 ${n(low)} — ${n(high)} 元；末日按收盘估值，未强行平仓。缺失点断开显示。</p></div>`);
  }
  function engineResult(r) {
    const daily=r.daily||[];dayIndex=Math.max(0,Math.min(dayIndex,daily.length-1));const day=daily[dayIndex];
    const strategies=day?.strategies||day?.topNByStrategy||{};
    let rows=[];
    for (const [id,value] of Object.entries(strategies)) {
      const stocks=Array.isArray(value)?value:value.topN||value.candidates||[];
      stocks.forEach((v,i)=>rows.push(`<tr><td>${E(id)}</td><td>${i+1}</td><td>${E(v.symbol)}</td><td>${n(v.rawScore,5)}</td><td>${n(v.score,5)}</td></tr>`));
    }
    const weights=day?.strategyBudgetWeights||day?.strategyWeights||day?.allocationWeights||{};
    const coverage=r.universeCoverage||r.quality?.coverage?.sourceCoverage||r.quality?.universeCoverage||{},page=r.pagination?.daily||{offset:0,total:daily.length,hasMore:false};
    const holdings=(day?.holdings||[]).map(v=>`<tr><td>${E(v.symbol)}</td><td>${n(v.quantity,0)}</td><td>${n(v.marketValue)}</td><td>${p(Number(v.weight)*100)}</td><td>${numeric(day?.targetWeights?.[v.symbol])?p(day.targetWeights[v.symbol]*100):'—'}</td></tr>`);
    const regime=day?.regime,status=regime?.status==='observed'?`趋势 ${readable(regime.trend)} · 波动 ${readable(regime.volatility)}`:'未知（缺少所选真实指数的足够历史）';
    return metricTable([['当前组合',r]])+equityChart(r)+panel('每日候选与策略预算权重',`<div class="rs-body"><div class="rs-actions"><button class="rs-button" type="button" data-pf-action="previous-page" data-pf-offset="${Math.max(0,page.offset-(page.pageSize||daily.length||20))}" ${!hooks?.dailyPage||page.offset<=0?'disabled':''}>前一页记录</button><button class="rs-button" type="button" data-pf-action="previous-day" ${dayIndex<=0?'disabled':''}>上一日</button><strong>${E(day?.date||'暂无每日记录')}</strong><button class="rs-button" type="button" data-pf-action="next-day" ${dayIndex>=daily.length-1?'disabled':''}>下一日</button><button class="rs-button" type="button" data-pf-action="next-page" data-pf-offset="${page.offset+daily.length}" ${!hooks?.dailyPage||!page.hasMore?'disabled':''}>后一页记录</button></div><p class="rs-help">当前已加载第 ${daily.length?page.offset+1:0} — ${page.offset+daily.length} 日，共 ${page.total} 日。预算权重：${E(Object.entries(weights).map(([id,v])=>`${id} ${n(Number(v)*100)}%`).join(' · '))}。预算用于入场分配，实际持仓权重另列。</p><p class="rs-help">市场环境：${E(status)}；仅作描述，不改变策略配权。</p></div>${table(['策略','排名','股票','原始分','排名标准分'],rows)}${table(['实际持仓','股数','市值（元）','实际权重','待执行目标权重'],holdings)}<details class="rs-body"><summary>当日资格、持仓与目标原始记录</summary><pre>${E(JSON.stringify(day||{},null,2))}</pre></details>`)+panel('数据覆盖与验证边界',`<div class="rs-body">${note((r.warnings||[]).join('；'))}<p>发现 ${n(coverage.discoveredCount,0)} 只 · 选择 ${n(coverage.selectedCount,0)} 只 · 读入 ${n(coverage.loadedCount,0)} 只 · 读取缺口 ${n(coverage.failureCount,0)} 项。</p><p class="rs-help">历史上市日期缺失时，仅以已观察交易日数作为上市年龄下界；严格模式会剔除缺少历史资格的证券。</p><details><summary>查看原始质量与覆盖记录</summary><pre>${E(JSON.stringify({coverage,quality:r.quality},null,2))}</pre></details></div>`)+panel('冻结配置',`<details class="rs-body"><summary>展开本次配置与来源标识</summary><pre>${E(JSON.stringify({config:r.config,fingerprint:r.fingerprint||r.datasetFingerprint},null,2))}</pre></details>`);
  }
  function studyResult(r) {
    const persistence=r.training?.persistence||r.persistence||{},fixed=r.testing?.fixed,equal=r.testing?.equalWeight;
    const unwrap=v=>v?.result||v;
    return panel('训练区选定，样本外验收',metricTable([['固定策略（仅训练选择）',unwrap(fixed)],['所有参与策略等权',unwrap(equal)]])+`<div class="rs-body">${note(r.comparison?.conclusion||'样本外结果只用于验收，不会用来重新挑选固定策略。')}<p>训练选定策略：${E(r.training?.selectedStrategyId||r.selectedStrategyId||'尚未选定')}</p></div>`)+panel('持续性检验',`<div class="rs-body"><p>状态：${E(readable(persistence.status))} · 平均排名相关性 ${n(persistence.meanRankCorrelation,4)} · 有效窗口对 ${n(persistence.validPairCount,0)} · 条件置换 p 值 ${n(persistence.pValue,4)}</p><p class="rs-help">${E((persistence.failures||[]).map(readable).join('；'))}</p><details><summary>窗口与相邻排名证据</summary><pre>${E(JSON.stringify(persistence,null,2))}</pre></details></div>`)+panel('轮动层准入状态',`<div class="rs-body"><pre>${E(JSON.stringify(r.rotationGate||{},null,2))}</pre>${note('持续性证据不足时先停止轮动研究。未计算轮动系统，不能宣称它优于等权组合。')}</div>`)+panel('训练策略对照',metricTable(Object.entries(r.training?.runs||{}).map(([id,result])=>[id,unwrap(result)])))+panel('质量与方法',`<details class="rs-body"><summary>展开数据边界与验证协议</summary><pre>${E(JSON.stringify({quality:r.quality,warnings:r.warnings,protocol:r.protocol},null,2))}</pre></details>`);
  }
  function result(r) {
    if(!r)return note('正在准备数据或等待计算结果。');
    if(r.kind==='comparison'||r.testing){
      const fixed=r.testing?.fixed?.result||r.testing?.fixed,equal=r.testing?.equalWeight?.result||r.testing?.equalWeight;
      return studyResult(r)+(fixed?equityChart(fixed,'固定策略 · 样本外净值与回撤'):'')+(equal?equityChart(equal,'等权组合 · 样本外净值与回撤'):'');
    }
    return engineResult(r)+regimeTable(r);
  }
  function set(path,value) {
    const keys=path.split('.');
    if (['market','board'].includes(keys[0])) {
      const field=keys[0]==='market'?'markets':'boards', current=draft.selection[field];
      draft.selection[field]=value?[...new Set([...current,keys[1]])]:current.filter(v=>v!==keys[1]);return;
    }
    let target=draft;for(const k of keys.slice(0,-1))target=target[k];target[keys.at(-1)]=value;
  }
  function capture(el) { if(!el?.dataset?.pfField||!draft)return;set(el.dataset.pfField,el.dataset.pfType==='boolean'?el.checked:el.dataset.pfType==='text'?el.value:stored(el.dataset.pfField,el.value));save(); }
  document.addEventListener('input',event=>{if(event.target.matches?.('[data-pf-field]'))capture(event.target);});
  document.addEventListener('change',event=>{const el=event.target;if(!el.matches?.('[data-pf-field]'))return;capture(el);if(el.tagName==='SELECT'||el.type==='checkbox')hooks?.refresh();});
  document.addEventListener('submit',event=>{
    if(!event.target.matches?.('[data-pf-form]'))return;event.preventDefault();event.stopImmediatePropagation();
    for(const el of event.target.querySelectorAll('[data-pf-field]'))capture(el);
    try { hooks.create(payload(draft,definition.strategies)); } catch(error) { hooks.error(error); }
  },true);
  document.addEventListener('click',event=>{
    const el=event.target.closest?.('[data-pf-action]');if(!el||el.disabled)return;event.preventDefault();
    if(el.dataset.pfAction==='reset'){draft=makeDraft(definition);save();hooks.refresh();}
    if(el.dataset.pfAction==='save'){save();hooks.feedback('回测设置已保存在本机。');}
    if(el.dataset.pfAction==='previous-day'){dayIndex=Math.max(0,dayIndex-1);hooks.refresh();}
    if(el.dataset.pfAction==='next-day'){dayIndex++;hooks.refresh();}
    if(['previous-page','next-page'].includes(el.dataset.pfAction)){dayIndex=0;hooks?.dailyPage?.(Number(el.dataset.pfOffset));}
  });
  window.PortfolioWorkbench={configure,form,result,helpers,available:()=>!!definition,
    selectStrategy(id){if(!draft)return;for(const s of definition.strategies)draft.enabled[s.id]=s.id===id;draft.participates[id]=true;for(const combination of Object.values(draft.combinations))combination.enabled=false;draft.config.allocation.mode='equal';save();},
    plateau(){return note('全系统收益平原要基于同一套股票池、组合和轮动系统计算。当前先完成组合回测与持续性检验；旧版单策略参数图不能视为新系统的收益平原。');}};
})();
