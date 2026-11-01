/* Reviewed experimental rules. Opening this page never starts a replay. */
(() => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const number = (value, digits = 2) => finite(value) ? value.toLocaleString('zh-CN', {maximumFractionDigits:digits, minimumFractionDigits:digits}) : '—';
  const percent = value => finite(value) ? `${number(value)}%` : '—';
  const money = value => finite(value) ? `${number(value)} 元` : '—';
  const BOARD = {main:'主板', chinext:'创业板', star:'科创板'};
  const STATUS = {queued:'等待执行', pending:'等待执行', running:'正在复现', completed:'已完成', failed:'执行失败', stopped:'已停止', cancelled:'已停止', stopping:'正在停止', interrupted:'执行中断'};
  const alive = task => ['queued','pending','running','stopping'].includes(task?.status);
  const taskLabel = task => task?.kind==='currentScan' && task.status==='running' ? '正在扫描' : STATUS[task?.status] || task?.status || '状态未知';
  const sourceLabel = source => source?.label || [source?.period, BOARD[source?.board] || source?.board, `${source?.start || source?.startDate || '—'} — ${source?.end || source?.endDate || '—'}`].filter(Boolean).join(' · ');
  const textList = value => Array.isArray(value) ? value.map(item => typeof item === 'string' ? item : item?.text || item?.description || item?.label || '').filter(Boolean) : typeof value === 'string' ? [value] : value && typeof value === 'object' ? Object.entries(value).map(([key,item]) => `${key}：${typeof item === 'string' ? item : Array.isArray(item) ? item.join('；') : ''}`).filter(x => !x.endsWith('：')) : [];
  const list = value => {const items=textList(value);return items.length ? `<ul>${items.map(item=>`<li>${escape(item)}</li>`).join('')}</ul>` : '<p class="exp-muted">暂无补充说明。</p>';};
  const metricsOf = value => value?.metrics || value?.account || value || {};
  function metric(value, names) {const row=metricsOf(value);for (const name of names) if (finite(row[name])) return row[name];return null;}
  function researchRows(strategy) {
    const rows = strategy?.researchResults || strategy?.accountResults || strategy?.research?.rows || strategy?.results;
    if (Array.isArray(rows)) return rows;
    return (strategy?.sources || []).filter(s=>s.metrics || s.researchResult || s.result).map(s=>({...s,...(s.researchResult || s.result || {}),metrics:s.metrics || s.researchResult?.metrics || s.result?.metrics}));
  }
  function resultTable(rows, label='已研究区间的账户结果') {
    if (!rows.length) return '<p class="exp-muted">研究概览暂未提供；缺失数值不作零收益处理。</p>';
    return `<div class="exp-table-wrap" tabindex="0" aria-label="${escape(label)}，可横向滚动"><table><caption>${escape(label)}</caption><thead><tr><th scope="col">时期 / 板块</th><th scope="col">净收益</th><th scope="col">最大回撤</th><th scope="col">闭仓数</th><th scope="col">盈利因子</th><th scope="col">平均盈利 / 亏损</th><th scope="col">未平浮盈</th></tr></thead><tbody>${rows.map(row=>`<tr><th scope="row">${escape([row.period,BOARD[row.board]||row.board].filter(Boolean).join(' · ') || row.label || '本次复现')}</th><td>${percent(metric(row,['accountReturnPct','netReturnPct','returnPct']))}</td><td>${percent(metric(row,['maxDrawdownPct','accountMaxDrawdownPct']))}</td><td>${number(metric(row,['closed','closedTradeCount','closedCount']),0)}</td><td>${number(metric(row,['profitFactor']))}</td><td>${percent(metric(row,['meanWinPct','meanWinReturnPct']))} / ${percent(metric(row,['meanLossPct','meanLossReturnPct']))}</td><td>${money(metric(row,['openUnrealizedPnlAmount','openPositionUnrealizedPnl']))}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function summaryView(summary) {
    if (!summary) return '<p class="exp-muted">完成后在这里查看净账户结果；尚无结果不会显示成零收益。</p>';
    const a=summary.account || summary.metrics || summary;
    const closed=summary.closedEpisodes || summary.closedDistribution || {};
    const normalized={...a,...summary.metrics,closed:a.closed ?? closed.count,profitFactor:a.profitFactor ?? closed.profitFactor,meanWinPct:a.meanWinPct ?? closed.meanWinReturnPct,meanLossPct:a.meanLossPct ?? closed.meanLossReturnPct,openUnrealizedPnlAmount:a.openUnrealizedPnlAmount ?? summary.openPositionsAtCutoff?.unrealizedPnlAmount};
    const q=summary.quality || {}, c=summary.concentration || {};
    const count=value=>Array.isArray(value)?value.length:finite(value)?value:null;
    return resultTable([normalized],'本次复现结果') + `<p class="exp-muted">费用 ${money(metric(a,['feesPaid','totalFees']))} · 平均仓位 ${percent(metric(a,['meanExposurePct']))} · 期末未平 ${number(metric(a,['openPositions']),0)} 笔。期末未平仓按已记录价格估值，与已闭仓收益分开。</p><dl class="exp-facts"><div><dt>闭仓胜率 / 平均单笔</dt><dd>${percent(metric(a,['winRatePct']))} / ${percent(metric(a,['meanReturnPct']))}</dd></div><div><dt>前五大盈利占毛盈利</dt><dd>${percent(c.top5ShareOfGrossProfitPct)}</dd></div><div><dt>估值不可靠日</dt><dd>${number(count(q.valuationUnreliableDates ?? q.valuationUnreliableDays),0)}</dd></div><div><dt>完整性问题 / 数值缺失项</dt><dd>${number(count(q.integrityIssues),0)} / ${number(count(q.numericUnknownPaths),0)}</dd></div></dl>`;
  }
  function errorMessage(error) {
    if (typeof error === 'string') return error;
    return error?.message || error?.reason || error?.detail || '操作未完成，请刷新状态后重试。';
  }
  function progressValue(task) {
    const p=task?.progress;
    const raw=typeof p==='number' ? p : p?.percent ?? p?.percentage ?? (finite(p?.completed)&&finite(p?.total)&&p.total>0 ? p.completed/p.total*100 : null);
    return finite(raw) ? Math.max(0,Math.min(100,raw)) : null;
  }
  function progressLabel(task) {
    const p=task?.progress || {};
    return ({verifyInputs:'核对历史来源',loadInputs:'读取历史来源',signals:'核对固定信号',account:'执行历史账户',queued:'等待执行',completed:'已完成'})[p.phase] || '执行中';
  }
  function evidenceValue(item) {
    if (item?.value===null || item?.value===undefined || (typeof item.value==='number' && !finite(item.value))) return '—';
    const value=finite(item.value)?number(item.value,item.unit==='只'||item.unit==='笔'||item.unit==='日'?0:2):String(item.value);
    return value+(item.unit?' '+item.unit:'');
  }
  function scanTitle(result) {return result?.asOfDate ? `截至 ${result.asOfDate} 的信号` : '当前环境与选股';}
  function currentFreshness(result,freshness) {
    const checked=freshness?.available===true && typeof freshness.stale==='boolean';
    return {checked,expectedAsOfDate:checked?freshness.expectedAsOfDate:null,stale:checked?freshness.stale:null,
      lagSessions:checked?freshness.lagSessions:null,scannedExpectedDate:result?.expectedAsOfDate||null};
  }
  function emptyCandidates(strategy) {
    const boards=strategy?.boards||[];
    if(strategy?.counts?.evaluated===0 || (boards.length&&boards.every(b=>b.status==='unavailable'||b.evaluatedCount===0)))return '数据不足，当前无法判断是否有符合规则的股票。';
    const missing=boards.filter(b=>b.status==='unavailable'||b.evaluatedCount===0).map(b=>BOARD[b.board]||b.board);
    return strategy?.counts?.signals===0 ? `截至该日，已评估范围内暂无符合规则的股票；未评估数据不算作不符合。${missing.length?'数据不足的板块：'+missing.join('、')+'。':''}` : '当前候选结果不可用，不能当作零候选。';
  }
  function candidateView(candidate) {
    return `<li class="exp-candidate"><div class="exp-candidate-head"><div><span class="exp-rank">${number(candidate.rank,0)}</span><button type="button" class="exp-stock" data-stock="${escape(candidate.symbol)}"${candidate.signalDate?` data-stock-day="${escape(candidate.signalDate)}"`:''} aria-label="查看 ${escape(candidate.name||candidate.symbol)} 日线"><strong>${escape(candidate.name || candidate.symbol)}</strong></button> <span class="exp-muted">${escape(candidate.symbol)} · ${escape(BOARD[candidate.board]||candidate.board||'')}</span></div><span>收盘 ${number(candidate.close)} 元</span></div><p>${escape(candidate.reason || '符合已保存的策略规则')}</p><p class="exp-muted">信号日 ${escape(candidate.signalDate || '—')}</p><details data-exp-detail="candidate-${escape(candidate.symbol)}"><summary>查看符合依据</summary><dl class="exp-evidence">${(candidate.evidence||[]).map(e=>`<div><dt>${escape(e.label)}</dt><dd>${escape(evidenceValue(e))}</dd></div>`).join('')}</dl>${candidate.warnings?.length?`<div class="exp-candidate-warning">${list(candidate.warnings)}</div>`:''}</details></li>`;
  }
  const helpers={escape,finite,number,percent,money,sourceLabel,researchRows,resultTable,summaryView,errorMessage,alive,progressValue,progressLabel,evidenceValue,scanTitle,candidateView,emptyCandidates,currentFreshness};
  if (typeof module === 'object' && module.exports) module.exports=helpers;
  if (typeof window === 'undefined') return;

  const state={catalogue:null,tasks:[],strategyId:null,sourceIds:{},selectedTask:null,detail:null,error:'',notice:'',loading:false,creating:false,pendingRequest:null,root:null,timer:null,refreshing:false,
    scans:[],selectedScan:null,scanDetail:null,scanError:'',scanCreating:false,scanRefreshing:false,scanRequest:null,scanTimer:null,openDetails:new Set()};
  const handles=page=>page==='R16';
  const active=()=>state.root?.isConnected && (!window.AT?.state || handles(window.AT.state.page));
  const strategy=()=>state.catalogue?.strategies?.find(s=>s.id===state.strategyId);
  const source=()=>strategy()?.sources?.find(s=>s.id===state.sourceIds[state.strategyId]);
  async function rpc(action,payload={}) {
    if (!window.atradeDesktop?.business) throw new Error('请在 A-trade 桌面端打开实验策略。当前预览不会启动任何任务。');
    const reply=await window.atradeDesktop.business(`research.experimental.${action}`,payload);
    if (!reply?.ok) throw new Error(errorMessage(reply?.error));
    return reply.data;
  }
  function facts(s) {
    if (!s) return '';
    const capital=s.initialCapital ?? s.capital ?? s.config?.initialCapital;
    const dates=`${s.start || s.startDate || '—'} — ${s.end || s.endDate || '—'}`;
    const fee=s.feeLabel || s.feesLabel || (typeof s.fees==='string'?s.fees:'沿用该区间已研究的费用与滑点');
    return `<dl class="exp-facts"><div><dt>固定区间</dt><dd>${escape(dates)}</dd></div><div><dt>范围</dt><dd>${escape(BOARD[s.board]||s.board||s.label||'固定样本')}</dd></div><div><dt>初始资金</dt><dd>${capital===undefined?'沿用已研究配置':money(capital)}</dd></div><div><dt>交易成本</dt><dd>${escape(fee)}</dd></div></dl>`;
  }
  function environmentMetrics(rows) {
    return `<dl class="exp-environment-metrics">${(rows||[]).map(row=>`<div><dt>${escape(row.label)}</dt><dd>${escape(evidenceValue(row))}</dd>${row.denominator!==null&&row.denominator!==undefined?`<small>分母 ${escape(row.denominator)}</small>`:''}</div>`).join('')}</dl>`;
  }
  function scanResult(result,freshness) {
    if (!result) return '<div class="exp-empty">尚无扫描结果。点击上方按钮读取本地行情并分析三组策略，不会创建交易账户。</div>';
    const coverage=result.coverage||{},env=result.environment||{},fresh=currentFreshness(result,freshness);
    return `<section class="exp-scan-result" aria-labelledby="exp-scan-result-title"><header><h2 id="exp-scan-result-title">${escape(scanTitle(result))}</h2><p>扫描请求日期 ${escape(result.requestedDate||'—')} · 当前应有已完成日线 ${escape(fresh.expectedAsOfDate||'未核验')}</p>${result.source?.benchmarkLatestDate?`<p>沪深300实际截止 ${escape(result.source.benchmarkLatestDate)}</p>`:''}</header>${!fresh.checked?`<div class="exp-stale" role="status"><strong>无法核验当前时效</strong><p>这是已保存的扫描。扫描时应有日线 ${escape(fresh.scannedExpectedDate||'—')}；当前是否过期尚未核验，不能当作今日信号。可刷新扫描状态重新核验。</p></div>`:fresh.stale?`<div class="exp-stale" role="status"><strong>本地数据未更新到应有日期</strong><p>当前信号截至 ${escape(result.asOfDate||'—')}，落后 ${number(fresh.lagSessions,0)} 个交易日。请先更新本地行情，再重新扫描；这些不是今日信号。</p></div>`:''}<dl class="exp-scan-coverage"><div><dt>读取证券</dt><dd>${number(coverage.loaded,0)}</dd></div><div><dt>截止日有行情</dt><dd>${number(coverage.asOfCount,0)} <small> / ${percent(coverage.asOfPct)}</small></dd></div><div><dt>读取失败</dt><dd>${number(coverage.failed,0)}</dd></div></dl>${result.warnings?.length?`<div class="exp-scan-warnings">${list(result.warnings)}</div>`:''}<section class="exp-environment"><h3>环境观察：${escape(env.label||'证据不足')}</h3><p>${escape(env.summary||'暂无可评估的环境摘要。')}</p>${environmentMetrics(env.metrics)}<details data-exp-detail="environment-boards"><summary>各板块环境与统计口径</summary>${(env.boards||[]).map(board=>`<section class="exp-board-environment"><h3>${escape(BOARD[board.board]||board.board)} · ${escape(board.label||'')}</h3><p>${escape(board.summary||'')}</p>${environmentMetrics(board.metrics)}</section>`).join('')}${list(env.methodology)}</details><p class="exp-muted">环境用于解释当前背景，不额外改变三套策略的入场规则，也不识别主力、散户或量化资金身份。</p></section><div class="exp-scan-strategies">${(result.strategies||[]).map(s=>`<section class="exp-current-strategy" data-exp-scan-strategy="${escape(s.strategyId)}"><header><h3>${escape(s.name||s.strategyId)}</h3><span>${number(s.counts?.signals,0)} 只符合 · ${number(s.counts?.evaluated,0)} 只评估</span></header><p class="exp-fit"><strong>${escape(s.environmentFit?.label||'环境适配未判断')}</strong> ${escape(s.environmentFit?.summary||'')}</p>${s.candidates?.length?`<ol class="exp-candidates">${s.candidates.map(candidateView).join('')}</ol>`:`<p class="exp-no-candidate">${escape(emptyCandidates(s))}</p>`}<details data-exp-detail="scan-rule-${escape(s.strategyId)}"><summary>覆盖与策略局限</summary><ul>${(s.boards||[]).map(b=>`<li>${escape(BOARD[b.board]||b.board)}：符合 ${number(b.signalCount,0)}，评估 ${number(b.evaluatedCount,0)}${b.status?' · '+escape(({evaluated:'已评估',unavailable:'数据不足 / 未评估'})[b.status]||b.status):''}${b.reason?' · '+escape(b.reason):''}</li>`).join('')}</ul>${list(s.limitations)}</details></section>`).join('')}</div><p class="exp-execution-notice">${escape(result.executionNotice||'只生成研究候选，不创建账户或实盘委托。收盘信号不等于下一日一定能成交。')}</p></section>`;
  }
  function scannerPanel() {
    const task=state.scanDetail?.task || state.scans.find(t=>t.id===state.selectedScan),busy=alive(task),p=progressValue(task);
    return `<section class="exp-panel exp-scanner" aria-labelledby="exp-scan-title"><header class="exp-section-head"><div><h2 id="exp-scan-title">分析本地行情</h2><p>读取已完成的日线，给出环境概况和三套规则各自符合的股票。</p></div><div class="exp-scan-actions"><button type="button" class="exp-primary" data-exp-action="scan-create" ${state.scanCreating||busy?'disabled':''}>${state.scanCreating?'正在创建扫描…':busy?'正在分析…':'分析当前环境与选股'}</button>${busy?`<button type="button" data-exp-action="scan-stop" ${task.status==='stopping'?'disabled':''}>${task.status==='stopping'?'正在停止…':'停止扫描'}</button>`:''}</div></header>${state.scanError?`<div class="exp-error" role="alert">${escape(state.scanError)}</div>`:''}${busy?`<div class="exp-scan-progress" aria-live="polite"><p>${escape(taskLabel(task))}${task.progress?.date?' · '+escape(task.progress.date):''}${p===null?'':' · 当前阶段 '+number(p,0)+'%'}</p><progress aria-label="当前扫描阶段进度" ${p===null?'':`value="${p}"`} max="100"></progress></div>`:''}${task?.error?`<div class="exp-error">${escape(errorMessage(task.error))}</div>`:''}${scanResult(state.scanDetail?.result,state.scanDetail?.freshness)}<details class="exp-scan-history" data-exp-detail="scan-history"><summary>已保存的扫描记录 ${state.scans.length?'（'+state.scans.length+'）':''}</summary><div class="exp-section-head"><p>重新打开页面只读取记录，不自动扫描。</p><button type="button" data-exp-action="scan-refresh" ${state.scanRefreshing?'disabled':''}>刷新扫描状态</button></div>${state.scans.length?`<div class="exp-task-list">${state.scans.map(t=>`<button type="button" data-exp-scan="${escape(t.id)}" class="exp-task${t.id===state.selectedScan?' is-selected':''}" aria-pressed="${t.id===state.selectedScan}"><span>本地行情扫描<small>${escape(t.createdAt||'')}</small></span><span class="exp-status">${escape(taskLabel(t))}</span></button>`).join('')}</div>`:'<p class="exp-muted">尚无扫描记录。</p>'}</details></section>`;
  }
  function cataloguePanel() {
    const selected=strategy(),items=state.catalogue?.strategies || [];
    if (!items.length) return `<section class="exp-panel exp-empty" aria-live="polite">${state.loading?'正在读取已保存的实验策略…':'尚未读取到实验策略。'}<button type="button" data-exp-action="refresh" ${state.loading?'disabled':''}>重新读取</button></section>`;
    return `<div class="exp-workbench"><nav class="exp-catalogue" aria-label="选择实验策略">${items.map((item,index)=>`<button type="button" class="exp-strategy-choice${item.id===state.strategyId?' is-selected':''}" data-exp-strategy="${escape(item.id)}" aria-pressed="${item.id===state.strategyId}"><span class="exp-choice-number">${index+1}</span><span><strong>${escape(item.name)}</strong><small>${escape(item.shortDescription || item.description || '固定规则 · 实验用途')}</small></span></button>`).join('')}</nav><section class="exp-panel exp-strategy-detail" aria-labelledby="exp-strategy-title"><header><span class="exp-eyebrow">实验策略</span><h2 id="exp-strategy-title">${escape(selected?.name)}</h2><p>${escape(selected?.description || '')}</p></header><div class="exp-rule-columns"><section><h3>入场规则</h3>${list(selected?.rules)}</section><section><h3>退出与风险</h3>${list(selected?.exit)}${selected?.dataRequirements?`<details data-exp-detail="data"><summary>数据要求</summary>${list(selected.dataRequirements)}</details>`:''}</section></div><div class="exp-limits"><h3>这组策略的局限</h3>${list(selected?.limitations)}</div><details class="exp-research" data-exp-detail="strategy-research"><summary>查看全部已研究区间结果 <span>${researchRows(selected).length ? researchRows(selected).length+' 个账户' : '历史开发数据'}</span></summary>${resultTable(researchRows(selected))}<p class="exp-muted">包含盈利和亏损区间。2022–2025 已反复用于开发；9·24 分段延续原有资金与持仓，不代表新信号独立收益。收益不是承诺。</p></details><section class="exp-replay" aria-labelledby="exp-replay-title"><h3 id="exp-replay-title">复现已研究区间</h3><p>选择已固定的样本和区间，按原始规则、资本及费用重新执行。不会启动实盘交易。</p><label for="exp-source">固定研究区间</label><select id="exp-source" data-exp-source ${state.creating?'disabled':''}>${(selected?.sources||[]).map(s=>`<option value="${escape(s.id)}" ${s.id===state.sourceIds[state.strategyId]?'selected':''}>${escape(sourceLabel(s))}${s.available===false?' · 当前不可用':''}</option>`).join('')}</select>${facts(source())}<div class="exp-replay-action"><button type="button" class="exp-primary" data-exp-action="create" ${state.creating||!source()||source()?.available===false?'disabled':''}>${state.creating?'正在创建复现任务…':'开始复现此区间'}</button><span>仅点击后执行；保留本次独立任务与结果。</span></div></section></section></div>`;
  }
  function historyPanel() {
    const selected=state.detail?.task || state.tasks.find(t=>t.id===state.selectedTask),progress=progressValue(selected);
    return `<section class="exp-panel exp-history" aria-labelledby="exp-history-title"><header class="exp-section-head"><div><h2 id="exp-history-title">复现任务</h2><p>已保存的任务与结果，独立于原研究归档。</p></div><button type="button" data-exp-action="refresh-tasks" ${state.refreshing?'disabled':''}>刷新状态</button></header>${state.tasks.length?`<div class="exp-task-list" aria-label="选择复现任务">${state.tasks.map(t=>`<button type="button" data-exp-task="${escape(t.id)}" class="exp-task${t.id===state.selectedTask?' is-selected':''}" aria-pressed="${t.id===state.selectedTask}"><span><strong>${escape(t.name || state.catalogue?.strategies?.find(s=>s.id===t.strategyId)?.name || '实验复现')}</strong><small>${escape(t.createdAt || '')}${t.sourceLabel?' · '+escape(t.sourceLabel):''}</small></span><span class="exp-status exp-status-${escape(t.status||'unknown')}">${escape(taskLabel(t))}</span></button>`).join('')}</div>`:'<div class="exp-empty">尚未创建复现任务。上方三组实验策略已生成，等待你选择区间。</div>'}${selected?`<section class="exp-task-detail" aria-live="polite"><div class="exp-section-head"><div><h3>${escape(selected.name || '选中任务')}</h3><p>${escape(taskLabel(selected))}${selected.message?' · '+escape(selected.message):''}</p></div>${alive(selected)?`<button type="button" data-exp-action="stop" ${selected.status==='stopping'?'disabled':''}>${selected.status==='stopping'?'正在停止…':'停止此任务'}</button>`:''}</div>${alive(selected)&&progress!==null?`<progress max="100" value="${progress}" aria-label="复现进度"></progress><p class="exp-muted">${escape(progressLabel(selected))} · 当前阶段 ${number(progress,0)}%</p>`:''}${selected.error?`<p class="exp-error">${escape(errorMessage(selected.error))}</p>`:''}${summaryView(state.detail?.summary || selected.summary)}${state.detail?.resultRef?'<p class="exp-muted">完整执行结果已保存至此任务的独立记录。</p>':''}</section>`:''}</section>`;
  }
  function overview() {
    const data=state.catalogue?.researchOverview || state.catalogue?.overview;
    const findings=data?.findings || data?.conclusions || state.catalogue?.limitations;
    const rows=data?.families || data?.researchFamilies || [];
    return `<details class="exp-panel exp-overview" data-exp-detail="all-research"><summary>全部研究与选择依据</summary><p>49 批研究已收束。保存这三种不同机制供实验复现，不代表已找到稳定盈利策略；不会自动增加新试验。</p>${data?list([data.meaning,data.scope,data.execution].filter(Boolean)):""}${findings?list(findings):'<p>选择兼顾净账户收益、回撤、不同年份与板块、费用、仓位、未平仓和盈利集中度。同一入场家族的退出或模型变体不会重复占据三个名额。</p>'}${Array.isArray(rows)&&rows.length?`<ul class="exp-family-list">${rows.map(row=>`<li><strong>${escape(row.name || row.title || row.family || row.id)}</strong><span>${escape(row.conclusion || row.reason || row.status || '')}</span></li>`).join('')}</ul>`:''}<p class="exp-muted">全部历史属于开发证据。日线量价不能识别主力、散户或量化资金身份；平均盈利大于平均亏损，也不等于净账户长期盈利。</p></details>`;
  }
  function content() {
    return `<header class="exp-page-head"><div><span class="exp-eyebrow">三组实验策略 · 当前观察</span><h1>当前环境与选股</h1><p>先核对本地行情截止日，再看环境和符合规则的股票。</p></div><span class="exp-badge">实验用途</span></header><div class="exp-notice">扫描只做分析和选股，不发送实盘委托。历史开发结果不保证当前有效。</div>${scannerPanel()}<details class="exp-historical" data-exp-detail="historical"><summary>策略规则、研究结果与历史复现 <span>辅助核对 · 不自动执行</span></summary>${state.error?`<div class="exp-error" role="alert">${escape(state.error)} <button type="button" data-exp-action="dismiss-error">收起</button></div>`:''}${state.notice?`<div class="exp-success" role="status">${escape(state.notice)}</div>`:''}${cataloguePanel()}${historyPanel()}${overview()}</details>`;
  }
  function rememberDetails() {
    const host=state.root?.querySelector('[data-experimental-page]');
    if(host)state.openDetails=new Set([...host.querySelectorAll('details[open][data-exp-detail]')].map(d=>d.dataset.expDetail));
  }
  function restoreDetails(host) {for(const d of host.querySelectorAll('details[data-exp-detail]'))d.open=state.openDetails.has(d.dataset.expDetail);}
  function render() {rememberDetails();return `<div class="experimental-strategies" data-experimental-page="R16">${content()}</div>`;}
  function paint() {
    if (!active()) return;
    const host=state.root.querySelector('[data-experimental-page]');if (!host) return;
    rememberDetails();
    const focus=document.activeElement?.id,scrollX=window.scrollX,scrollY=window.scrollY;
    host.innerHTML=content();
    restoreDetails(host);
    if (focus) document.getElementById(focus)?.focus({preventScroll:true});
    window.scrollTo({left:scrollX,top:scrollY,behavior:'instant'});
  }
  function chooseDefaults() {
    if (!strategy()) state.strategyId=state.catalogue?.strategies?.[0]?.id || null;
    for (const s of state.catalogue?.strategies || []) if (!s.sources?.some(x=>x.id===state.sourceIds[s.id])) state.sourceIds[s.id]=s.sources?.[0]?.id;
  }
  function schedulePoll() {
    clearTimeout(state.timer);state.timer=null;
    if (active() && state.tasks.some(alive)) state.timer=setTimeout(()=>{if (active()) void refreshTasks(false);},2200);
  }
  function scheduleScanPoll() {
    clearTimeout(state.scanTimer);state.scanTimer=null;
    if(active()&&state.scans.some(alive))state.scanTimer=setTimeout(()=>{if(active())void refreshScans();},2200);
  }
  async function scanDetail(id) {
    const data=await rpc('scan.get',{id});
    if(state.selectedScan===id){state.scanDetail=data;if(data.task)state.scans=state.scans.map(t=>t.id===id?data.task:t);paint();}
  }
  async function refreshScans() {
    if(state.scanRefreshing)return;state.scanRefreshing=true;
    try{
      const value=await rpc('scan.list');state.scans=Array.isArray(value?.tasks)?value.tasks:[];
      if(!state.selectedScan && state.scans.length)state.selectedScan=state.scans[0].id;
      if(state.selectedScan && state.scans.some(t=>t.id===state.selectedScan))await scanDetail(state.selectedScan);
      else {state.selectedScan=null;state.scanDetail=null;}
    }catch(error){state.scanError=errorMessage(error);}
    finally{state.scanRefreshing=false;paint();scheduleScanPoll();}
  }
  async function createScan() {
    const selected=state.scanDetail?.task||state.scans.find(t=>t.id===state.selectedScan);
    if(state.scanCreating||alive(selected))return;
    state.scanRequest ||= globalThis.crypto?.randomUUID?.() || `scan-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    state.scanCreating=true;state.scanError='';paint();
    try{
      const value=await rpc('scan.create',{requestId:state.scanRequest}),task=value?.task||value;
      if(!task?.id)throw Error('扫描服务未返回任务编号，请刷新扫描记录。');
      state.scans=[task,...state.scans.filter(t=>t.id!==task.id)];state.selectedScan=task.id;state.scanDetail={task};state.scanRequest=null;
    }catch(error){state.scanError=errorMessage(error);}
    finally{state.scanCreating=false;paint();scheduleScanPoll();}
  }
  async function stopScan() {
    const task=state.scanDetail?.task||state.scans.find(t=>t.id===state.selectedScan);
    if(!alive(task)||task.status==='stopping')return;
    task.status='stopping';state.scanError='';paint();
    try{await rpc('scan.stop',{id:task.id});await refreshScans();}
    catch(error){state.scanError=errorMessage(error);await refreshScans();}paint();
  }
  async function detail(id,paintAfter=true) {
    const value=await rpc('get',{id});
    if (state.selectedTask===id) {state.detail=value;if (value.task) state.tasks=state.tasks.map(t=>t.id===id?value.task:t);if (paintAfter)paint();}
  }
  async function refreshTasks(showError=true) {
    if (state.refreshing) return;
    state.refreshing=true;
    try {
      const data=await rpc('list');state.tasks=Array.isArray(data?.tasks)?data.tasks:[];
      if (!state.selectedTask && state.tasks.length) state.selectedTask=state.tasks[0].id;
      if (state.selectedTask && state.tasks.some(t=>t.id===state.selectedTask)) await detail(state.selectedTask,false);
      else {state.selectedTask=null;state.detail=null;}
    } catch(error) {if (showError || state.tasks.some(alive)) state.error=errorMessage(error);}
    finally {state.refreshing=false;paint();schedulePoll();}
  }
  async function load() {
    if (state.loading) return;
    state.loading=true;state.error='';paint();
    try {state.catalogue=await rpc('catalogue');chooseDefaults();await refreshTasks(true);}
    catch(error){state.error=errorMessage(error);}
    finally{state.loading=false;paint();}
  }
  async function create() {
    const s=strategy(),src=source();if (state.creating || !s || !src || src.available===false) return;
    const key=`${s.id}:${src.id}`;
    if (!state.pendingRequest || state.pendingRequest.key!==key) state.pendingRequest={key,id:globalThis.crypto?.randomUUID?.() || `experimental-${Date.now()}-${Math.random().toString(16).slice(2)}`};
    state.creating=true;state.error='';state.notice='';paint();
    try {
      const value=await rpc('create',{strategyId:s.id,sourceId:src.id,requestId:state.pendingRequest.id});
      const task=value?.task || value;if (!task?.id) throw new Error('服务未返回任务编号，请先刷新任务状态。');
      state.selectedTask=task.id;state.detail={task};state.tasks=[task,...state.tasks.filter(x=>x.id!==task.id)];state.pendingRequest=null;
      state.notice='复现任务已创建。执行状态和结果会保存在下方。';
    } catch(error){state.error=errorMessage(error);}
    finally{state.creating=false;paint();schedulePoll();}
  }
  async function stop() {
    const id=state.selectedTask;if (!id) return;
    const task=state.tasks.find(t=>t.id===id);if (!alive(task) || task.status==='stopping') return;
    task.status='stopping';state.error='';paint();
    try {await rpc('stop',{id});await refreshTasks();state.notice='停止请求已提交，保留已有任务记录。';}
    catch(error){state.error=errorMessage(error);await refreshTasks(false);}
    paint();
  }
  function clicked(event) {
    const button=event.target.closest('button');if (!button || !state.root?.contains(button) || button.disabled) return;
    if (button.dataset.expStrategy) {state.strategyId=button.dataset.expStrategy;state.error='';state.notice='';paint();return;}
    if (button.dataset.expTask) {state.selectedTask=button.dataset.expTask;state.detail=null;paint();void detail(state.selectedTask).catch(e=>{state.error=errorMessage(e);paint();});return;}
    if (button.dataset.expScan) {state.selectedScan=button.dataset.expScan;state.scanDetail=null;paint();void scanDetail(state.selectedScan).catch(e=>{state.scanError=errorMessage(e);paint();});return;}
    const action=button.dataset.expAction;
    if (action==='scan-create')void createScan();
    else if(action==='scan-stop')void stopScan();
    else if(action==='scan-refresh')void refreshScans();
    else if (action==='create') void create();
    else if (action==='stop') void stop();
    else if (action==='refresh') void load();
    else if (action==='refresh-tasks') void refreshTasks();
    else if (action==='dismiss-error') {state.error='';paint();}
  }
  function changed(event) {
    if (event.target.matches('[data-exp-source]')) {state.sourceIds[state.strategyId]=event.target.value;state.notice='';paint();}
  }
  function toggled(event) {
    const key=event.target.dataset?.expDetail;
    if(key && active() && state.root.contains(event.target)){if(event.target.open)state.openDetails.add(key);else state.openDetails.delete(key);}
  }
  function mount(main) {
    if (state.root!==main) {state.root?.removeEventListener('click',clicked);state.root?.removeEventListener('change',changed);state.root?.removeEventListener('toggle',toggled,true);state.root=main;main.addEventListener('click',clicked);main.addEventListener('change',changed);main.addEventListener('toggle',toggled,true);}
    restoreDetails(main);
    if (!state.catalogue) void load();else {paint();void refreshTasks(false);}void refreshScans();
  }
  function unmount() {rememberDetails();clearTimeout(state.timer);clearTimeout(state.scanTimer);state.timer=null;state.scanTimer=null;state.root?.removeEventListener('click',clicked);state.root?.removeEventListener('change',changed);state.root?.removeEventListener('toggle',toggled,true);state.root=null;}
  window.ExperimentalStrategies={handles,render,mount,unmount};
})();
