/* Whole-universe research editor. Only the Python service produces results. */
(() => {
  'use strict';
  const percentKeys = new Set(['maxStockWeight','switchThreshold','stopLossPct','takeProfitPct','trailingStopPct','commissionRate','transferFeeRate','stampDutyRate','maxParticipationRate','minReturn','minDrawdown','maxDrawdown','minDominance','minWeightChange','regimeBlend','minNeighborPositiveFraction']);
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
  const defaultsStudy = () => ({trainEnd:'',testStart:'',testEnd:'',persistenceWindow:20,minWindows:7,minRankCorrelation:.2,minPositivePairFraction:.6,permutationCount:999,seed:1729,alpha:.05,rotationEnabled:true});
  const defaultsRotation = () => ({lookbackDays:20,cooldownDays:5,minWeightChange:.1,temperature:.05,regimeBlend:.25,regimeMinObservations:5,warmupDays:120});
  const defaultsExperiment = () => ({capitalMode:'independent',trainDays:140,testDays:39,stepDays:39,expanding:false,maxFolds:5,maxTrials:81,minNeighborPositiveFraction:.7,minNeighborhoodSize:3,maxNeighborhoodStdPct:10,minOosYears:2,minOosRegimes:2});
  const experimentKinds = new Set(['walkForward','systemPlateau']);
  const axisLayer = path => path.startsWith('strategies.')?'strategy':path.startsWith('combinations.')?'combination':path.startsWith('rotation.')?'rotation':'holdings';
  function scanAxes(rows,kind,max=81) {
    const axes=rows.filter(row=>row.path).map(row=>{
      const tokens=String(row.text||'').trim().split(/[\s,，;；、]+/).filter(Boolean);
      const values=[...new Set(tokens.map(value=>row.path.endsWith('.method')?({'交集':'intersection','并集':'union','加权':'weighted'}[value]||value):numeric(value)?stored(row.path,value):null))];
      if(values.length<2||values.length>5||values.includes(null))throw new Error('每个扫描参数需要 2 至 5 个不同有效取值。');
      if(row.path.endsWith('.method')&&values.some(v=>!['intersection','union','weighted'].includes(v)))throw new Error('组合方式请输入交集、并集或加权。');
      return {path:row.path,values};
    });
    if(new Set(axes.map(a=>a.path)).size!==axes.length)throw new Error('扫描参数不能重复。');
    if(kind==='systemPlateau'&&new Set(axes.map(a=>axisLayer(a.path))).size!==4)throw new Error('系统收益平原需要同时选择策略、组合、轮动、持仓四层参数。');
    const count=axes.reduce((total,a)=>total*a.values.length,1);
    if(count>Math.min(81,Number(max)))throw new Error(`共有 ${count} 组参数，超过本次扫描上限 ${Math.min(81,Number(max))}。`);
    return axes;
  }
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
    if(selection.source==='history' && (draft.periodMode!=='dates'||!selection.sourceId))throw new Error('历史数据包须选择数据源并填写覆盖范围内的明确日期。');
    selection.symbols = selection.mode === 'explicit' ? symbols(draft.symbols) : [];
    if (selection.mode === 'explicit' && !selection.symbols.length) throw new Error('指定股票池不能为空。');
    if (!selection.markets.length || !selection.boards.length) throw new Error('请至少选择一个市场和一个板块。');
    if (draft.kind === 'comparison') {
      if (draft.periodMode !== 'dates') throw new Error('训练与样本外比较需要选择明确日期区间。');
      config.study = structuredClone(draft.study);
    } else if(experimentKinds.has(draft.kind)) {
      if(draft.periodMode!=='dates')throw new Error('滚动验证与系统收益平原需要选择明确日期区间。');
      // Per-fold dates are derived by the service. Persist only diagnostic settings.
      config.study=structuredClone(draft.study);
      delete config.study.trainEnd;delete config.study.testStart;delete config.study.testEnd;
    } else delete config.study;
    if (draft.name.trim().length > 100) throw new Error('研究名称最多 100 个字符。');
    selection.lookbackDays = config.lookbackDays;
    selection.benchmarkSymbol = selection.benchmarkSymbol?.trim().toLowerCase() || null;
    const request={kind:draft.kind, name:draft.name.trim(), config, selection};
    if(experimentKinds.has(draft.kind))request.experiment={...structuredClone(draft.experiment),kind:draft.kind,axes:scanAxes(draft.scanAxes,draft.kind,draft.experiment.maxTrials)};
    return request;
  }
  const helpers = {numeric,isPercent,display,stored,symbols,payload,scanAxes,axisLayer};
  if (typeof module === 'object' && module.exports) module.exports = helpers;
  if (typeof window === 'undefined' || typeof AT === 'undefined') return;
  const E = AT.escape, key = 'atrade.portfolio-editor.v1';
  let definition, hooks, draft, importingHistory=false, editingTaskId=null, editorNote='', displayNames={};
  const screen={task:null,meta:null,result:null,pool:'base',query:'',offset:0,signature:null};
  const dayIndices=new Map();
  const expandedDetails=new Set(), heatSlices=new Map();
  const detailAttrs=key=>`data-pf-detail="${E(key)}" ${expandedDetails.has(key)?'open':''}`;
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
    config.rotation={...defaultsRotation(),...config.rotation};
    for (const s of data.strategies) {
      const selected=config.strategies.find(r=>r.kind===s.id);
      enabled[s.id]=!!selected;
      params[s.id]={...Object.fromEntries(s.parameters.map(p=>[p.key,p.default])),...selected?.params};
      pools[s.id]=selected?.pool||'base';weights[s.id]=1;participates[s.id]=true;
    }
    return {kind:'portfolio',name:'',periodMode:'recent',config,selection:structuredClone(data.selection),symbols:'',enabled,params,pools,weights,participates,
      combinations:Object.fromEntries(['intersection','union','weighted'].map(k=>[k,{enabled:false,threshold:0}])),study:defaultsStudy(),experiment:{...defaultsExperiment(),...data.experimentDefaults},scanAxes:Array.from({length:6},()=>({path:'',text:''}))};
  }
  function configure(data, callbacks) {
    if (!data) return;
    hooks=callbacks;definition=data;
    if (draft) return;
    draft=makeDraft(data);
    try {
      const saved=JSON.parse(localStorage.getItem(key));
      if (saved?.version<=data.version && saved.draft && Array.isArray(saved.draft.selection?.markets)) {
        // Corrupt or incomplete old local drafts must not blank the editor.
        const candidate=saved.draft;
        if (candidate.config?.universe && candidate.config?.poolSettings && candidate.config?.allocation &&
            Array.isArray(candidate.selection.boards) && candidate.enabled && candidate.params && candidate.pools &&
            candidate.weights && candidate.participates && candidate.combinations && candidate.study &&
            typeof candidate.name==='string' && typeof candidate.symbols==='string' &&
            data.strategies.every(s=>candidate.params[s.id]) && ['intersection','union','weighted'].every(k=>candidate.combinations[k])) {
          candidate.config.rotation={...defaultsRotation(),...candidate.config.rotation};
          candidate.config.rightsPolicy=candidate.config.rightsPolicy||'skip';
          candidate.selection.source=candidate.selection.source||'local';
          candidate.study={...defaultsStudy(),...candidate.study};
          candidate.experiment={...defaultsExperiment(),...candidate.experiment};
          candidate.scanAxes=Array.isArray(candidate.scanAxes)?candidate.scanAxes:draft.scanAxes;
          draft=candidate;
        }
      }
    } catch {}
    queueReview();
  }
  function save() { try {localStorage.setItem(key,JSON.stringify({version:definition.version,draft}));} catch {} }
  function stock(v){const supplied=v.name&&v.name!==v.symbol&&v.name!==v.symbol?.slice(2)?v.name:null;const row={...v,name:supplied||displayNames[v.symbol]||hooks?.stockName?.(v.symbol)};return window.ResearchDisplay?.stock(row)||`<span>${E(row.name||'名称未提供')}<small>${E(v.symbol)}</small></span>`;}
  const operationText=value=>window.ResearchDisplay?.text(value)||readable(value);
  function screenSignature(){try{const request=screenPayload();return JSON.stringify({config:request.config,selection:request.selection});}catch{return null;}}
  function screenView(r=screen.result,standalone=false){
    if(!r?.screenPools)return screen.task?panel('股票池筛选预览',`<div class="rs-body">${note(screen.meta?.error||screen.meta?.phase||'筛选任务已提交，后台正在读取完整股票池。')}<button type="button" class="rs-button" data-pf-action="screen-task">查看筛选任务与运行控制</button></div>`):'';
    displayNames=r.securityNames||displayNames;
    const pools=r.screenPools||{},rows=pools[screen.pool]||[],page=r.poolPagination?.[screen.pool]||{offset:0,total:rows.length,pageSize:100};
    const names={base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池',excluded:'基础资格排除'};
    const stale=!standalone&&screen.signature!==screenSignature();
    return panel('股票池筛选结果',`<div class="rs-body">${stale?note('当前设置与这份已保存筛选结果不同。请点击“按当前设置筛选股票池”重新计算。'):''}<p>截至 ${E(r.asOf)} 收盘 · 来源证券 ${n(r.coverage?.totalSecurities,0)} 只。每个池均完成全量筛选后分页显示。</p><div class="rs-actions">${Object.entries(names).map(([key,label])=>`<button type="button" class="rs-button ${screen.pool===key?'rs-primary':''}" data-pf-action="screen-pool" data-pf-pool="${key}">${label} ${n(r.poolCounts?.[key],0)}</button>`).join('')}</div><label class="rs-field pf-pool-search"><span>在整个筛选池中搜索名称或代码</span><input type="search" data-pf-screen-query value="${E(screen.query)}" placeholder="股票名称或代码"></label><div class="rs-actions"><button type="button" class="rs-button" data-pf-action="screen-search">搜索股票池</button><button type="button" class="rs-button" data-pf-action="screen-clear">清除搜索</button></div></div>${table(['股票（点击看日线）','收盘价','成交额（元）','趋势窗口涨幅','结构窗口回撤','量能倍数','触发的策略 / 排除原因'],rows.map(row=>`<tr><td>${stock({...row,date:r.asOf})}</td><td>${n(row.close)}</td><td>${n(row.amount,0)}</td><td>${p(row.trendReturnPct)}</td><td>${p(row.drawdownPct)}</td><td>${n(row.volumeRatio)}</td><td>${row.eligible?E((row.matchedStrategies||[]).map(s=>strategyName(s.id)).join('、')||'基础资格通过，尚无策略信号'):E(operationText(row.reason))}</td></tr>`))}<div class="rs-body rs-actions"><span>匹配 ${n(page.total,0)} 只 · 当前 ${page.total? page.offset+1:0} — ${Math.min(page.offset+rows.length,page.total)} 只</span><button type="button" class="rs-button" data-pf-action="screen-page" data-pf-offset="${Math.max(0,page.offset-page.pageSize)}" ${page.offset?'':'disabled'}>上一页股票</button><button type="button" class="rs-button" data-pf-action="screen-page" data-pf-offset="${page.offset+rows.length}" ${page.hasMore?'':'disabled'}>下一页股票</button>${!standalone?'<button type="button" class="rs-button" data-pf-action="screen-task">查看筛选任务</button>':''}</div><p class="rs-help rs-body">筛选是截止日收盘的研究观察，不产生真实买卖。名称缺失会明确标注；股票池条件与回测使用同一套设置。</p>`);
  }
  async function loadScreen(){if(!screen.task)return;const data=await hooks.screenGet({id:screen.task,pool:screen.pool,poolOffset:screen.offset,poolQuery:screen.query});screen.meta=data.task;screen.result=data.result;hooks.refresh();}
  function editTask(t,mode='copy'){
    if(!Array.isArray(t.config?.strategies)||!t.selection)throw new Error('旧版任务支持重命名和查看；请使用新的股票池工作区配置研究。');
    if(t.config.strategies.some(s=>s.id!==s.kind)||t.config.combinations.some(c=>c.id!=='combo_'+c.method))throw new Error('此任务包含自定义策略实例，可重命名或查看；当前表单不能无损编辑这些实例。');
    draft=makeDraft(definition);draft.config=structuredClone(t.config);draft.kind=t.kind;
    draft.name=mode==='copy'?(t.name+' · 副本').slice(0,100):t.name;draft.selection=structuredClone(t.selection);draft.symbols=(t.selection.symbols||[]).join(' ');
    draft.periodMode=t.config.startDate&&t.config.endDate?'dates':'recent';draft.study={...defaultsStudy(),...t.config.study};draft.experiment={...defaultsExperiment(),...t.experiment};
    const selected=new Set(t.config.allocation.strategyIds);
    for(const s of definition.strategies){const node=t.config.strategies.find(n=>n.id===s.id);draft.enabled[s.id]=!!node;draft.participates[s.id]=selected.has(s.id);if(node){draft.params[s.id]=structuredClone(node.params);draft.pools[s.id]=node.pool;}}
    for(const c of t.config.combinations){draft.combinations[c.method]={enabled:true,threshold:c.threshold};draft.participates[c.id]=selected.has(c.id);for(const child of c.children)draft.weights[child.id]=child.weight;}
    draft.scanAxes=(t.experiment?.axes||[]).map(axis=>({path:axis.path,text:axis.values.map(v=>typeof v==='number'?display(axis.path,v):v).join(', ')}));while(draft.scanAxes.length<6)draft.scanAxes.push({path:'',text:''});
    editingTaskId=mode==='edit'&&t.configEditable?t.id:null;editorNote=editingTaskId?'正在编辑尚未冻结行情的任务；保存后更新原任务。':'正在复用已有设置；提交会创建新任务，原报告保留。';setStep(4);queueReview();
  }
  function rotationForm() {
    const r=draft.config.rotation;
    return `<fieldset class="rs-fieldset"><legend>策略轮动</legend><div class="rs-fields">${field('近期组合表现窗口 K（交易日）','config.rotation.lookbackDays',r.lookbackDays,'min="2"')}${field('权重调整冷却期（交易日）','config.rotation.cooldownDays',r.cooldownDays,'min="0"')}${field('最小预算转移比例（%）','config.rotation.minWeightChange',r.minWeightChange,'min="0" max="100"')}${field('软配权平滑程度','config.rotation.temperature',r.temperature,'min="0.000001"')}${field('同环境历史表现占比（%）','config.rotation.regimeBlend',r.regimeBlend,'min="0" max="100"')}${field('同环境最少观察日','config.rotation.regimeMinObservations',r.regimeMinObservations,'min="1"')}${field('轮动预热观察日','config.rotation.warmupDays',r.warmupDays,'min="0"')}</div><p class="rs-help">使用各策略 Top N 组合当时已知的近期净表现。软配权渐进调整预算；硬切换作为对照。单次回测仅用于检查轮动行为，正式研究须先通过训练区持续性检验，再与样本外等权及固定策略比较。</p></fieldset>`;
  }
  function diagnosticForm(withDates=false) {
    const s=draft.study;
    return `<fieldset class="rs-fieldset"><legend>训练、样本外与持续性</legend>${check('通过持续性检验后比较软配权与硬切换','study.rotationEnabled',s.rotationEnabled)}<div class="rs-fields">${withDates?textField('训练结束日期','study.trainEnd',s.trainEnd,'date')+textField('样本外开始日期','study.testStart',s.testStart,'date')+textField('样本外结束日期','study.testEnd',s.testEnd,'date'):''}${field('持续性窗口 K（交易日）','study.persistenceWindow',s.persistenceWindow,'min="2"')}${field('至少非重叠窗口数','study.minWindows',s.minWindows,'min="7"')}${field('最低平均排名相关性','study.minRankCorrelation',s.minRankCorrelation,'min="0" max="1"')}${field('正相关窗口对占比下限','study.minPositivePairFraction',s.minPositivePairFraction,'min="0.5" max="1"')}${field('置换检验次数','study.permutationCount',s.permutationCount,'min="99"')}${field('显著性阈值','study.alpha',s.alpha,'min="0.001" max="0.2"')}${field('检验随机种子','study.seed',s.seed,'min="0"')}</div><p class="rs-help">训练区选择固定策略并检验持续性，样本外冻结参数。至少三个有效不同策略、六对非重叠窗口；证据不足时跳过轮动，仍保留固定策略和等权对照。39 日测试预设通常不足以得出持续性结论。</p></fieldset>`;
  }
  function parameterLabel(path) {
    const parts=path.split('.'),last=parts.at(-1);
    if(parts[0]==='strategies') {
      const s=definition.strategies.find(s=>s.id===parts[1]),v=s?.parameters.find(v=>v.key===last);
      return `策略 · ${s?.name||parts[1]} · ${v?.label||last}${isPercent(path)?'（%）':''}`;
    }
    if(parts[0]==='combinations')return `组合 · ${({intersection:'交集',union:'并集',weighted:'加权'})[parts[1].replace('combo_','')]||parts[1]} · ${last==='method'?'合成方式':(definition.strategies.find(s=>s.id===parts[3])?.name||parts[3])+' 权重'}`;
    return ({'rotation.lookbackDays':'轮动 · 表现窗口','rotation.cooldownDays':'轮动 · 冷却期','rotation.minWeightChange':'轮动 · 最小预算转移（%）','rotation.temperature':'轮动 · 平滑程度','rotation.regimeBlend':'轮动 · 同环境表现占比（%）',topN:'持仓 · 每策略 Top N',maxPositions:'持仓 · 最多持股数',maxStockWeight:'持仓 · 单股上限（%）',switchThreshold:'持仓 · 排名换仓门槛（百分点）'})[path]||path;
  }
  function axisOptions() {
    const choices={'':'不扫描此项'};
    for(const s of definition.strategies.filter(s=>draft.enabled[s.id]))for(const v of s.parameters)choices[`strategies.${s.id}.params.${v.key}`]=`策略 · ${s.name} · ${v.label}${isPercent(v.key)?'（%）':''}`;
    for(const [method,c] of Object.entries(draft.combinations).filter(([,c])=>c.enabled)) {
      choices[`combinations.combo_${method}.method`]=`组合 · ${({intersection:"交集",union:"并集",weighted:"加权"})[method]} · 合成方式`;
      for(const s of definition.strategies.filter(s=>draft.enabled[s.id]))choices[`combinations.combo_${method}.children.${s.id}.weight`]=`组合 · ${({intersection:"交集",union:"并集",weighted:"加权"})[method]} · ${s.name} 权重`;
    }
    for(const [path,label] of Object.entries({lookbackDays:'表现窗口',cooldownDays:'冷却期',minWeightChange:'最小预算转移（%）',temperature:'平滑程度',regimeBlend:'同环境表现占比（%）'}))choices['rotation.'+path]='轮动 · '+label;
    for(const [path,label] of Object.entries({topN:'每策略 Top N',maxPositions:'最多持股数',maxStockWeight:'单股上限（%）',switchThreshold:'排名换仓门槛（百分点）'}))choices[path]='持仓 · '+label;
    return choices;
  }
  function experimentForm() {
    const x=draft.experiment,options=axisOptions();let count='1';
    try{count=String(scanAxes(draft.scanAxes,'walkForward',81).reduce((total,a)=>total*a.values.length,1));}catch{count='待填写有效取值';}
    return `<fieldset class="rs-fieldset"><legend>滚动训练与样本外验证</legend><div class="rs-fields">${select('资金衔接方式','experiment.capitalMode',x.capitalMode||'independent',{independent:'逐折独立资金（稳健性诊断）',continuous:'附加连续资金账户（持仓不重置）'})}${field('每折训练日数','experiment.trainDays',x.trainDays,'min="20"')}${field('每折样本外日数','experiment.testDays',x.testDays,'min="2"')}${field('滚动步长（交易日）','experiment.stepDays',x.stepDays,'min="2"')}${field('最多滚动折数','experiment.maxFolds',x.maxFolds,'min="1" max="8"')}${field('参数组合上限','experiment.maxTrials',x.maxTrials,'min="1" max="81"')}</div>${check('逐折扩大训练区间','experiment.expanding',x.expanding)}<p class="rs-help">每折仅用之前的训练数据挑选参数，随后冻结参数进入样本外。回测区间需要覆盖训练期和后续验证期。</p></fieldset><fieldset class="rs-fieldset"><legend>四层参数扫描</legend><p class="rs-help">${draft.kind==='systemPlateau'?'策略、组合、轮动、持仓四层至少各选择一个参数。先启用组合策略才能选择组合参数。':'可以不扫描参数，直接验证当前设置；也可选择小规模参数网格。'}每项 2–5 个取值，最多 81 组；百分比参数按界面百分数输入。组合方式输入交集、并集或加权，也兼容英文名称。</p><div class="pf-scan-axes">${draft.scanAxes.map((axis,i)=>`<div class="rs-fields">${select(`扫描参数 ${i+1}`,'scanAxes.'+i+'.path',axis.path,options)}${textField('取值（逗号或空格分隔）','scanAxes.'+i+'.text',axis.text)}</div>`).join('')}</div><p class="rs-help">当前网格：${E(count)} 组。实际完成组数与账户模拟次数在报告中分别列出。</p><details><summary>平原稳定性与样本外门槛</summary><div class="rs-fields">${field('邻域正收益比例下限（%）','experiment.minNeighborPositiveFraction',x.minNeighborPositiveFraction,'min="0" max="100"')}${field('最少邻域参数点','experiment.minNeighborhoodSize',x.minNeighborhoodSize,'min="2"')}${field('邻域收益标准差上限（百分点）','experiment.maxNeighborhoodStdPct',x.maxNeighborhoodStdPct,'min="0"')}${field('至少样本外年份数','experiment.minOosYears',x.minOosYears,'min="2"')}${field('至少样本外市场环境数','experiment.minOosRegimes',x.minOosRegimes,'min="2"')}</div></details></fieldset>`;
  }
  function refinementEvidence(r) {
    let body='';
    if(r.validationAudit)body+=panel('样本外使用记录',`<div class="rs-body" data-pf-validation-audit>${note(r.validationAudit.status==='developmentSample'?'该区间已用不同参数计算，现为开发样本，不能作为新的独立验收。':r.validationAudit.status==='reproduction'?'这是相同数据与设置的复现，不增加独立样本外证据。':'本工作区尚未记录该来源与股票池的重复验证。')}<details><summary>查看重复区间与来源范围</summary><pre>${E(JSON.stringify(r.validationAudit,null,2))}</pre></details></div>`);
    if(r.continuous)body+=panel('连续资金滚动账户',`<div class="rs-body" data-pf-continuous>${note('跨折保留现金、持仓和权益，参数变动在前一收盘形成计划，下一开盘交易；缺持续性时采用等权预算。固定与等权对照冻结首折训练参数。')}${metricTable(runOrder.filter(k=>r.continuous.runs?.[k]).map(k=>[runNames[k],r.continuous.runs[k]]))}<details><summary>查看连续账户交接记录</summary><pre>${E(JSON.stringify(r.continuous.retrainLog,null,2))}</pre></details></div>${gatePanel(r.continuous.rotationGate,r.continuous.comparison)}${testingDetails(r.continuous.runs)}`);
    return body;
  }
  function rightsTable(r) {
    if(!r.actionLedger?.length&&!r.cashReceivables?.length&&!r.actionPayables?.length&&!Object.keys(r.preExRights||{}).length)return '';
    const labels={record:'登记收盘持股',ex:'除权及权益入账',cashReceived:'现金到账',cashPaid:'配股付款',subscriptionPaid:'除权前认购付款',subscriptionDeclined:'放弃认购',refundReceivable:'失败认购待退款',unsupported:'权益分配证据不足'};
    return panel('公司行动与待到账权益',`<details class="rs-body" data-pf-rights ${detailAttrs("rights:"+(r.pagination?.path||""))}><summary>查看 ${n(r.pagination?.actionLedger?.total??r.actionLedger?.length,0)} 条权益记录与待到账金额</summary>${table(['日期','证券','登记／除权／到账','登记股数','现金或认购金额','新增股份'],(r.actionLedger||[]).map(a=>`<tr><td>${E(a.date)}</td><td>${E(a.symbol)}</td><td>${E(labels[a.stage]||a.stage)}</td><td>${n(a.entitledQuantity,0)}</td><td>${n(a.cashReceivable??a.subscriptionPayable??a.amount)}</td><td>${n((a.bonusShares||0)+(a.rightsShares||0),0)}</td></tr>`))}<pre>${E(JSON.stringify({receivables:r.cashReceivables,payables:r.actionPayables,reservedCash:r.reservedActionCash,preExRights:r.preExRights},null,2))}</pre></details>`);
  }
  const workflow={review:null,signature:null,pending:false,error:'',serial:0,timer:null,promise:null,submitting:false};
  const stepLabels=['股票池','策略与组合','交易规则','创建任务','查看结果'];
  const kindLabels={poolPreview:'股票池筛选预览',portfolio:'股票池组合回测',comparison:'多策略比较与持续性检验',walkForward:'滚动样本外验证',systemPlateau:'全系统参数扫描与收益平原'};
  const poolLabels={base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池'};
  const presetKey='atrade.strategy-presets.v1';
  const chosenPresets={};
  function currentStep(){return Math.max(1,Math.min(5,Number(draft?.workflowStep)||1));}
  function setStep(step){if(!draft)return;draft.workflowStep=Math.max(1,Math.min(5,Number(step)||1));save();}
  function fold(title,id,body){return '<details class="pf-advanced" '+detailAttrs('editor:'+id)+'><summary>'+E(title)+'</summary><div class="pf-advanced-body">'+body+'</div></details>';}
  function stepButton(step,label,primary=false){return '<button type="button" class="rs-button '+(primary?'rs-primary':'')+'" data-pf-action="workflow-step" data-pf-step="'+step+'">'+E(label)+'</button>';}
  function workflowSection(step,title,subtitle,body){
    return '<section class="pf-workflow-section" data-pf-step-panel="'+step+'" '+(currentStep()===step?'':'hidden')+'><header class="pf-step-heading"><span>'+step+'</span><div><h3 tabindex="-1">'+E(title)+'</h3><p>'+E(subtitle)+'</p></div></header>'+body+
      '<div class="rs-actions pf-step-footer">'+(step>1?stepButton(step-1,'上一步'):'')+(step<4?stepButton(step+1,'下一步：'+stepLabels[step],true):'')+'</div></section>';
  }
  function presetsFor(id){
    try{const rows=JSON.parse(localStorage.getItem(presetKey)||'{}')[id];return Array.isArray(rows)?rows.filter(p=>p&&typeof p.id==='string'&&typeof p.name==='string'&&p.params&&typeof p.params==='object'&&!Array.isArray(p.params)).slice(0,30):[];}catch{return [];}
  }
  function writePresets(id,rows){let saved={};try{saved=JSON.parse(localStorage.getItem(presetKey)||'{}');if(!saved||typeof saved!=='object'||Array.isArray(saved))saved={};}catch{}saved[id]=rows;localStorage.setItem(presetKey,JSON.stringify(saved));}
  function strategyPresets(s){
    const presets=presetsFor(s.id);
    return '<div class="pf-preset-toolbar"><label class="rs-field"><span>已保存参数预设</span><select data-pf-preset-select="'+E(s.id)+'"><option value="">当前参数 / 默认参数</option>'+presets.map(v=>'<option value="'+E(v.id)+'" '+(chosenPresets[s.id]===v.id?'selected':'')+'>'+E(v.name)+'</option>').join('')+'</select></label>'+
      '<label class="rs-field"><span>预设名称（可留空）</span><input data-pf-preset-name="'+E(s.id)+'" type="text" maxlength="60" placeholder="例如：较宽松的量能条件"></label><div class="rs-actions"><button type="button" class="rs-button" data-pf-action="save-strategy-preset" data-pf-strategy="'+E(s.id)+'">保存当前参数</button><button type="button" class="rs-button" data-pf-action="reset-strategy" data-pf-strategy="'+E(s.id)+'">恢复策略默认参数</button>'+
      (chosenPresets[s.id]?'<button type="button" class="rs-button" data-pf-action="delete-strategy-preset" data-pf-strategy="'+E(s.id)+'">删除所选预设</button>':'')+'</div></div>';
  }
  function strategyCard(s){
    const enabled=draft.enabled[s.id],changed=s.parameters.some(v=>draft.params[s.id][v.key]!==v.default);
    return '<article class="pf-strategy"><header>'+check(s.name,'enabled.'+s.id,enabled)+(enabled?check('参与配权 / 比较','participates.'+s.id,draft.participates[s.id]!==false):'')+'</header><p class="rs-help">'+E(s.description)+'</p>'+
      (enabled?'<div class="rs-fields">'+select('使用股票池','pools.'+s.id,draft.pools[s.id],poolLabels)+'</div>'+fold('策略参数与预设 · '+(changed?'已修改':'使用默认参数'),'strategy:'+s.id,strategyPresets(s)+'<div class="rs-fields">'+s.parameters.map(v=>field(v.label+(isPercent(v.key)?'（%）':''),'params.'+s.id+'.'+v.key,draft.params[s.id][v.key],'min="'+display(v.key,v.min)+'" max="'+display(v.key,v.max)+'"')).join('')+'</div>'):'')+'</article>';
  }
  function dataSourceForm(){
    const sources=definition.historySources||[],choices=Object.fromEntries(sources.map(s=>[s.id,(s.declaration?.universe||'历史数据包')+' · '+(s.coverage?.startDate||'')+' — '+(s.coverage?.endDate||'')+' · '+s.symbolCount+' 只']));
    return '<div class="rs-fields">'+select('研究数据来源','selection.source',draft.selection.source||'local',{local:'本地通达信行情',history:'已导入的历史数据包'})+
      (draft.selection.source==='history'?select('历史数据包','selection.sourceId',draft.selection.sourceId,{'':'请选择数据包',...choices}):'')+
      select('股票池范围','selection.mode',draft.selection.mode,{all:'全部 A 股（所选市场与板块）',explicit:'指定股票列表'})+
      (draft.selection.mode==='explicit'?textField('股票代码，逗号或空格分隔','symbols',draft.symbols):'')+'</div>'+
      '<div class="rs-actions">'+(draft.selection.source==='local'?'<button class="rs-button" type="button" data-nav="C01">设置本地行情来源</button>':'')+'<button class="rs-button" type="button" data-pf-action="import-history" '+(importingHistory?'disabled':'')+'>'+(importingHistory?'正在校验历史数据包…':'导入历史 JSON / JSONL 数据包')+'</button></div>'+
      '<p class="rs-help">'+(draft.selection.source==='history'?'历史包校验检查成分、交易状态、日历与权益的包内完整性，不替代对数据商来源的核验。':'本地现存文件代表当前覆盖，不能保证历史成分、退市与权益完整；正式样本外研究需要补齐这些历史事实。')+'</p>';
  }
  function poolCard(key,title,description,fields){
    const count=screen.result?.poolCounts?.[key],stale=screen.result&&screen.signature!==screenSignature();
    return '<article class="pf-pool-card"><header><h4>'+E(title)+'</h4><span class="pf-pool-count">'+(count==null?'尚未筛选':n(count,0)+' 只'+(stale?' · 旧设置':''))+'</span></header><p class="rs-help">'+E(description)+'</p><div class="rs-fields">'+fields+'</div>'+
      '<button type="button" class="rs-button" data-pf-action="screen-pool" data-pf-pool="'+key+'" '+(!screen.result?'disabled':'')+'>查看这个池的股票</button></article>';
  }
  function poolForm(){
    const c=draft.config;
    const period=select('研究区间','periodMode',draft.periodMode,{recent:'最近 N 个交易日',dates:'指定起止日期'})+
      (draft.periodMode==='recent'?field('交易日数量','config.lookbackDays',c.lookbackDays,'min="2"'):textField('开始日期','config.startDate',c.startDate,'date')+textField('结束日期 / 筛选截止日','config.endDate',c.endDate,'date'));
    const markets=[['sh','沪市'],['sz','深市'],['bj','北交所']].map(([v,t])=>check(t,'market.'+v,draft.selection.markets.includes(v))).join('');
    const boards=[['main','主板'],['chinext','创业板'],['star','科创板'],['bse','北交所板块']].map(([v,t])=>check(t,'board.'+v,draft.selection.boards.includes(v))).join('');
    return '<fieldset class="rs-fieldset"><legend>股票池来源与基础资格</legend>'+dataSourceForm()+'<div class="rs-fields">'+period+'</div><div class="pf-options">'+markets+'</div><div class="pf-options">'+boards+'</div><div class="pf-options">'+
      check('剔除已知历史 ST','config.universe.excludeSt',c.universe.excludeSt)+check('严格历史资格（缺历史事实则剔除）','config.universe.strictHistory',c.universe.strictHistory)+'</div>'+
      '<p class="rs-help">读取所有符合范围的文件，分批处理后统一排名。股票池数量、每策略候选 Top N 和实际持仓数分别计算。最近 N 日的实际日期由冻结后的行情日历确定。</p></fieldset>'+
      '<div class="pf-pool-settings"><h3>分层股票池研究参数</h3><p class="rs-help">先筛出基础可交易池，再分别按趋势、回调结构与量能条件生成三个池。三个池可以重叠，不要求逐层减少。</p><div class="pf-pool-grid">'+
      poolCard('base','基础可交易池','通过上市时间、流动性和所选历史资格检查。',field('最少上市交易日','config.universe.minListingDays',c.universe.minListingDays,'min="0"')+field('20 日平均成交额下限（元）','config.universe.minAmount20',c.universe.minAmount20,'min="0"'))+
      poolCard('trend','趋势池','站上窗口均线，均线抬升，阶段涨幅达到下限。',field('趋势窗口','config.poolSettings.trend.window',c.poolSettings.trend.window)+field('趋势最低涨幅（%）','config.poolSettings.trend.minReturn',c.poolSettings.trend.minReturn))+
      poolCard('structure','回调结构池','距离窗口内高点的回撤落在设定区间。',field('结构窗口','config.poolSettings.structure.window',c.poolSettings.structure.window)+field('结构最低回撤（%）','config.poolSettings.structure.minDrawdown',c.poolSettings.structure.minDrawdown)+field('结构最高回撤（%）','config.poolSettings.structure.maxDrawdown',c.poolSettings.structure.maxDrawdown))+
      poolCard('volume','量能活跃池','当日量达到此前窗口平均成交量的设定倍数。',field('量能观察窗口','config.poolSettings.volume.window',c.poolSettings.volume.window)+field('量能最低倍数','config.poolSettings.volume.minRatio',c.poolSettings.volume.minRatio))+'</div></div>'+
      '<div class="rs-actions pf-preview-actions"><button type="button" class="rs-button rs-primary" data-pf-action="screen-preview">按当前设置筛选股票池</button><span class="rs-help">只生成观察列表；取研究结束日收盘或行情最新日。筛选结果显示全部匹配数量，可搜索与分页。</span></div>'+screenView();
  }
  function combinationsForm(){
    const picked=definition.strategies.filter(s=>draft.enabled[s.id]);
    const cards=Object.entries(draft.combinations).map(([method,v])=>'<div class="pf-combination">'+check({intersection:'交集组合',union:'并集组合',weighted:'加权打分组合'}[method],'combinations.'+method+'.enabled',v.enabled)+
      (v.enabled?check('参与配权 / 比较','participates.combo_'+method,draft.participates['combo_'+method]!==false)+field('标准化最低分（0–1）','combinations.'+method+'.threshold',v.threshold,'min="0" max="1"'):'')+'</div>').join('');
    return '<fieldset class="rs-fieldset"><legend>基础策略与组合策略</legend><p class="rs-help">勾选策略并选择它使用的股票池。参数已有默认值，按需展开修改或套用预设。</p>'+definition.strategies.map(strategyCard).join('')+
      '<div class="pf-combinations">'+cards+'</div><p class="rs-help">组合使用启用的基础策略，先将同日得分转为排名百分位，再做交集、并集或加权；可以只让组合策略参与配权。</p>'+
      fold('组合内子策略权重','combination-weights','<div class="rs-fields">'+picked.map(s=>field(s.name+'的权重','weights.'+s.id,draft.weights[s.id],'min="0.000001"')).join('')+'</div>')+'</fieldset>';
  }
  function tradingForm(){
    const c=draft.config,ranking=c.rebalanceMode!=='risk';
    return '<fieldset class="rs-fieldset"><legend>资金与持仓</legend><div class="rs-fields">'+field('初始资金（元）','config.initialCapital',c.initialCapital,'min="1000"')+
      field('每策略候选 Top N','config.topN',c.topN,'min="1" max="100"')+field('组合最多持股数','config.maxPositions',c.maxPositions,'min="1" max="200"')+
      field('单股新买目标上限（%）','config.maxStockWeight',c.maxStockWeight,'min="0.1" max="100"')+
      select('策略之间的配权','config.allocation.mode',c.allocation.mode,{equal:'参与策略等权',single:'固定一个基础或组合策略',soft_rotation:'动态软配权',hard_rotation:'动态硬切换（对照）'})+
      select('策略内股票权重','config.weighting',c.weighting,{equal:'股票等权',rank:'按排名分数加权'})+'</div></fieldset>'+
      '<fieldset class="rs-fieldset"><legend>持仓与调仓</legend><div class="rs-fields">'+select('调仓方式','config.rebalanceMode',c.rebalanceMode,{risk:'风险退出后补仓',rank:'按排名换仓',hybrid:'排名换仓 + 风险退出'})+
      field('补仓 / 排名调整间隔（交易日）','config.rebalanceEvery',c.rebalanceEvery,'min="1"')+
      (ranking?field('排名换仓最小分差（百分点）','config.switchThreshold',c.switchThreshold,'min="0" max="100"'):'')+
      field('止损（%，0 关闭）','config.stopLossPct',c.stopLossPct)+field('从持仓收盘峰值回撤（%，0 关闭）','config.trailingStopPct',c.trailingStopPct)+
      field('止盈（%，0 关闭）','config.takeProfitPct',c.takeProfitPct)+field('最长持仓交易日（0 关闭）','config.maxHoldingDays',c.maxHoldingDays)+'</div>'+
      '<p class="rs-help">股票池与策略评分每日重算；持仓是否调整由上述规则决定。风险每日收盘检查，次交易日开盘尝试执行。风险退出模式保留旧股股数，信号消失不会自动卖出。20% 是新买目标上限，持仓涨跌后权重可能变化。Top 1 仅作对照。</p></fieldset>'+
      fold('交易成本与权益处理','costs','<div class="rs-fields">'+field('佣金（%）','config.commissionRate',c.commissionRate)+field('最低佣金（元）','config.minCommission',c.minCommission)+
      field('卖出印花税（%，空白按日期）','config.stampDutyRate',c.stampDutyRate)+field('过户费（%）','config.transferFeeRate',c.transferFeeRate)+
      field('单边滑点（基点）','config.slippageBps',c.slippageBps)+field('上一交易日成交量参与上限（%）','config.maxParticipationRate',c.maxParticipationRate)+
      select('配股处理','config.rightsPolicy',c.rightsPolicy||'skip',{skip:'不认购，记录放弃权益',subscribe:'资金足够时按公告认购'})+'</div><p class="rs-help">费用、滑点、T+1 与涨跌停成交约束计入账户。现金分红按数据包声明的税后金额，到账前不可用于买入。</p>')+
      fold('策略轮动参数（动态配权与研究对照时生效）','rotation',rotationForm());
  }
  function effectiveView(){
    if(workflow.pending)return note('正在按后端规则校验当前设置…');
    if(workflow.error)return '<div class="pf-config-error" role="alert">'+E(workflow.error)+'</div><p class="rs-help">请修改对应设置；校验未通过时不会创建任务。</p>';
    const r=workflow.review;
    if(!r||workflow.signature!==requestSignature())return note('当前设置已变化，等待校验。创建任务时会再次核对。');
    const config=r.config,selection=r.selection;
    const params=config.strategies.map(s=>{const spec=definition.strategies.find(v=>v.id===s.kind);return '<tr><td>'+E(spec?.name||s.kind)+'</td><td>'+E(poolLabels[s.pool]||s.pool)+'</td><td>'+E(Object.entries(s.params).map(([key,value])=>{const field=spec?.parameters.find(v=>v.key===key);return (field?.label||key)+' '+display(key,value)+(isPercent(key)?'%':'');}).join('；'))+'</td></tr>';});
    const x=r.experiment;
    const strategyLabels=Object.fromEntries(definition.strategies.map(s=>[s.id,s.name]));
    const combinations={combo_intersection:'交集组合',combo_union:'并集组合',combo_weighted:'加权打分组合'};
    const modes={equal:'参与策略等权',single:'固定策略',soft_rotation:'动态软配权',hard_rotation:'动态硬切换（对照）'};
    const rebalance={risk:'风险退出后补仓',rank:'按排名换仓',hybrid:'排名换仓 + 风险退出'};
    const items=[
      ['任务类型与区间',kindLabels[r.kind]+' · '+(config.startDate?config.startDate+' — '+config.endDate:'最近 '+config.lookbackDays+' 个交易日')],
      ['股票池范围',(selection.source==='history'?'历史数据包':'本地行情')+' · '+(selection.mode==='explicit'?'指定 '+selection.symbols.length+' 只股票':'所选市场与板块全部 A 股')],
      ['参与配权的策略',config.allocation.strategyIds.map(id=>strategyLabels[id]||combinations[id]||id).join('、')+' · '+modes[config.allocation.mode]],
      ['资金与持仓',n(config.initialCapital,0)+' 元；每策略 Top '+config.topN+'；最多 '+config.maxPositions+' 只；单股新买上限 '+display('maxStockWeight',config.maxStockWeight)+'%'],
      ['调仓方式',rebalance[config.rebalanceMode]+'；补仓 / 排名调整间隔 '+config.rebalanceEvery+' 个交易日'],
      ['退出规则','止损 '+display('stopLossPct',config.stopLossPct)+'%；止盈 '+display('takeProfitPct',config.takeProfitPct)+'%；峰值回撤 '+display('trailingStopPct',config.trailingStopPct)+'%；最长持仓 '+(config.maxHoldingDays?config.maxHoldingDays+' 日':'不限')+'（比例 0 表示关闭）']
    ];
    return '<p class="pf-config-ready" role="status">配置校验通过 · 将按下列设置执行</p><p><strong>'+E(r.name)+'</strong></p>'+table(['设置','最终生效值'],items.map(([label,value])=>'<tr><td>'+E(label)+'</td><td>'+E(value)+'</td></tr>'))+
      '<p class="rs-help">股票池每天刷新；T 日收盘产生信号，下一交易日开盘尝试成交，启用 T+1。'+(r.execution?.datesResolvedAfterSnapshot?'最近交易日的起止日期将在冻结行情后显示。':'')+'</p>'+
      fold('股票池条件与完整生效设置','effective-config',configuration(config,selection))+
      fold('各策略的生效参数','effective-strategies',table(['策略','使用股票池','最终生效参数'],params))+
      '<p class="rs-help">交易成本：佣金 '+display('commissionRate',config.commissionRate)+'%，最低 '+n(config.minCommission)+' 元；卖出印花税 '+(config.stampDutyRate==null?'按历史日期':display('stampDutyRate',config.stampDutyRate)+'%')+'；过户费 '+display('transferFeeRate',config.transferFeeRate)+'%；滑点 '+n(config.slippageBps)+' 基点。</p>'+
      (x?'<p class="rs-help">参数网格 '+x.axes.reduce((count,a)=>count*a.values.length,1)+' 组；最多 '+x.maxFolds+' 折；每折训练 '+x.trainDays+' 日、样本外 '+x.testDays+' 日。实际完成组数与账户模拟次数将在结果中报告。</p>':'');
  }
  function taskForm(){
    return '<div class="rs-fields">'+select('任务类型','kind',draft.kind,kindLabels)+textField('研究名称（可留空，软件自动命名）','name',draft.name)+
      textField('业绩基准代码（可留空，如 sh000300）','selection.benchmarkSymbol',draft.selection.benchmarkSymbol)+'</div>'+
      '<p class="rs-help">普通回测可使用最近 N 个交易日；策略比较、滚动验证与系统平原需要明确起止日期。未选择基准时，基准收益与市场环境留空。</p>'+
      ((draft.kind==='comparison'||experimentKinds.has(draft.kind))&&(draft.periodMode!=='dates'||!draft.config.startDate||!draft.config.endDate)?'<div class="rs-actions">'+note('请先选择明确的研究起止日期。')+stepButton(1,'返回第 1 步设置日期')+'</div>':'')+
      (draft.kind==='comparison'?'<div class="rs-fields">'+textField('训练结束日期','study.trainEnd',draft.study.trainEnd,'date')+textField('样本外开始日期','study.testStart',draft.study.testStart,'date')+textField('样本外结束日期','study.testEnd',draft.study.testEnd,'date')+'</div>':'')+
      (draft.kind==='comparison'||experimentKinds.has(draft.kind)?fold('持续性检验与轮动准入','persistence',diagnosticForm(false)):'')+
      (experimentKinds.has(draft.kind)?fold('高级研究：滚动验证与参数扫描','experiment',experimentForm()):'')+
      '<section class="pf-effective-panel"><h4>最终生效配置</h4><div data-pf-effective>'+effectiveView()+'</div><button type="button" class="rs-button" data-pf-action="review-settings">重新校验配置</button></section>'+
      '<div class="rs-actions pf-submit"><button type="submit" class="rs-button rs-primary" '+(workflow.submitting?'disabled':'')+'>'+(workflow.submitting?'正在提交…':editingTaskId?'保存任务修改':'按生效配置创建任务')+'</button>'+
      (editorNote?'<button type="button" class="rs-button" data-pf-action="cancel-edit">结束编辑，恢复新建</button>':'')+'<button type="button" class="rs-button" data-pf-action="save">保存设置</button><button type="button" class="rs-button" data-pf-action="reset">恢复测试预设</button><span class="rs-help">任务在本机后台计算，可暂停、继续和停止。</span></div>';
  }
  function requestSignature(){try{return JSON.stringify(payload(draft,definition.strategies));}catch{return null;}}
  function renderEffective(){
    const target=document.querySelector?.('[data-pf-effective]');
    if(target)target.innerHTML=effectiveView();
  }
  function queueReview(){
    workflow.review=null;workflow.signature=null;workflow.error='';
    if(typeof hooks?.previewSettings!=='function')return;
    workflow.serial++;clearTimeout(workflow.timer);workflow.pending=true;renderEffective();
    workflow.timer=setTimeout(()=>{void reviewSettings().catch(()=>{});},400);
  }
  async function reviewSettings(){
    clearTimeout(workflow.timer);
    let request;
    try{request=payload(draft,definition.strategies);}catch(error){workflow.pending=false;workflow.error=error.message;renderEffective();throw error;}
    const signature=JSON.stringify(request);
    if(workflow.review&&workflow.signature===signature)return workflow.review;
    if(workflow.promise?.signature===signature&&workflow.promise.serial===workflow.serial)return workflow.promise.value;
    const serial=++workflow.serial;workflow.pending=true;workflow.error='';renderEffective();
    const promise=Promise.resolve().then(()=>hooks.previewSettings(request)).then(value=>{
      if(serial===workflow.serial&&signature===requestSignature()){workflow.review=value;workflow.signature=signature;workflow.pending=false;renderEffective();}
      return value;
    }).catch(error=>{if(serial===workflow.serial){workflow.pending=false;workflow.error=error.message;renderEffective();}throw error;}).finally(()=>{if(workflow.promise?.value===promise)workflow.promise=null;});
    workflow.promise={signature,serial,value:promise};return promise;
  }
  function screenPayload(){
    const selected=definition.strategies.filter(s=>draft.enabled[s.id]);
    const config={...structuredClone(definition.defaults),universe:structuredClone(draft.config.universe),poolSettings:structuredClone(draft.config.poolSettings),
      startDate:draft.config.startDate,endDate:draft.config.endDate,lookbackDays:draft.config.lookbackDays,allocation:{mode:'equal'}};
    const clean={...draft,kind:'poolPreview',name:('股票池筛选 · '+(draft.name||'当前设置')).slice(0,100),config,
      combinations:Object.fromEntries(['intersection','union','weighted'].map(k=>[k,{enabled:false,threshold:0}])),participates:{},enabled:selected.length?draft.enabled:Object.fromEntries(definition.strategies.map((s,i)=>[s.id,i===0]))};
    const request=payload(clean,definition.strategies);request.name=request.name.slice(0,100);return request;
  }
  function form() {
    if(!draft)return note('正在读取组合回测设置…');
    const active=currentStep();
    const nav=stepLabels.map((label,index)=>'<button type="button" class="pf-step-nav '+(active===index+1?'is-active':'')+'" data-pf-action="workflow-step" data-pf-step="'+(index+1)+'" '+(active===index+1?'aria-current="step"':'')+'><span>'+(index+1)+'</span>'+E(label)+'</button>').join('');
    if(active===5)return '<div class="pf-workbench"><nav class="pf-workflow-nav" aria-label="策略与回测流程">'+nav+'</nav><p class="rs-help">下方选择研究任务，查看进度、收益、逐日持仓与买卖计划。返回前四步可调整设置，已有报告仍保留原配置。</p></div>';
    return '<form data-pf-form class="pf-workbench" novalidate>'+(editorNote?note(editorNote):'')+
      '<div class="pf-intro"><strong>股票池 → 每日评分 → 多股组合 → 次日执行</strong><p>按五步配置研究；每一步都可直接进入。设置自动保存在本机，下一次可以继续使用。</p></div><nav class="pf-workflow-nav" aria-label="策略与回测流程">'+nav+'</nav>'+
      workflowSection(1,'设置并查看股票池','选择数据与范围，调整池条件，然后查看筛选出来的股票。',poolForm())+
      workflowSection(2,'选择策略与组合','基础策略和组合策略都可以参与比较或配权。',combinationsForm())+
      workflowSection(3,'设置资金与交易规则','每天重新评分；按排名或风险退出规则调整持仓。',tradingForm())+
      workflowSection(4,'创建研究任务','核对最终生效配置，再交给本机后台计算。',taskForm())+
      (active===5?'<p class="rs-help">下方选择研究任务，查看进度、收益、逐日持仓与买卖计划。已有报告仍保留原设置。</p>':'')+'</form>';
  }
  const reasonLabels = {stopLoss:'止损',takeProfit:'止盈',trailingStop:'回撤退出',maxHoldingDays:'最长持仓',insufficient:'证据不足',passed:'通过',failed:'未通过',complete:'已完成诊断',running:'计算中',eligible:'合格',unknown:'未知',up:'上行',down:'下行',mixed:'震荡',high:'高',normal:'中',medium:'中',low:'低',observed:'已观测',not_implemented:'旧报告尚未实现',skipped:'已跳过轮动',no_value:'未通过关键检验',unverified:'缺少认证证据',adjusted:'已调整',held:'维持预算',cooldown:'尚在冷却期',belowThreshold:'变化未达到门槛',insufficientHistory:'历史观察不足'};
  Object.assign(reasonLabels,{recentNetPerformanceAndKnownRegime:'近期净收益与当时市场环境',insufficientShadowHistory:'策略组合观察期不足',unreliableShadowValuation:'策略组合估值证据不足',belowWeightThreshold:'预算变化未达到门槛',evidence_integrity_failed:'行情隔离、陈旧估值或比较完整性检查失败',experiment_incomplete:'研究尚未完成',four_layer_system_plateau_not_requested:'本次为滚动验证，没有请求四层系统平原',training_neighborhood_not_stable_in_all_folds:'并非每个训练折都有稳定邻域',data_quality_not_certified:'历史成分或权益等数据质量尚未认证',training_persistence_not_passed_for_frozen_region:'冻结区域未全部通过训练持续性检验',training_persistence_not_passed_for_entire_grid:'参数网格未全部通过训练持续性检验',frozen_region_evidence_missing:'训练冻结区域的成员证据缺失',rotation_did_not_beat_both_baselines_in_every_fold:'轮动未在每折样本外同时超过两项基线',frozen_neighborhood_failed_out_of_sample:'训练冻结的邻域未通过样本外检验',no_common_training_region_across_folds:'不同训练折之间没有共同稳定区域',frozen_neighborhood_failed_years:'冻结邻域未通过分年份验证',frozen_neighborhood_failed_regimes:'冻结邻域未通过分环境验证',insufficient_out_of_sample_years:'样本外覆盖年份不足',insufficient_observed_market_regimes:'真实市场环境覆盖不足'});
  Object.assign(reasonLabels,{projected_intervals_contain_unverified_combinations:'参数区间交叉后存在未验证组合',an_axis_has_no_stable_width:'部分参数轴没有稳定宽度',numeric_region_contains_grid_gaps:'数值参数区域存在网格空洞',empty_region:'没有共同可验证区域',holdout_reused_with_changed_settings:'验证区间已用不同设置计算，现为开发样本'});
  const readable = v => typeof v==='string' ? reasonLabels[v]||v : JSON.stringify(v ?? null);
  function metricTable(results) {
    return table(['方案','累计收益','年化','最大回撤','夏普','完整持仓波段胜率','换手','费用（元）','滑点（元）','成本 / 初始资金'],results.map(([label,r])=>{const m=r?.metrics||{};return `<tr><td>${E(label)}</td><td>${p(m.totalReturnPct)}</td><td>${p(m.annualizedReturnPct)}</td><td>${p(m.maxDrawdownPct)}</td><td>${n(m.sharpe ?? m.sharpeRatio)}</td><td>${p(m.winRatePct)}<small>${numeric(m.closedTradeCount)?n(m.closedTradeCount,0)+' 个已关闭波段':'暂无关闭波段数'}</small></td><td>${p(m.turnoverPct)}</td><td>${n(m.totalFees ?? m.totalCosts)}</td><td>${n(m.totalSlippage)}</td><td>${p(m.costRatioPct)}</td></tr>`;}))+`<p class="rs-help pf-metric-note">胜率按完整关闭的持仓波段统计；费用与滑点均计入净收益，成本占比为两者合计除以初始资金，滑点已包含在成交价中。</p>`;
  }
  function configuration(config,selection={}){
    if(!config)return '';
    const mode={single:'固定策略',equal:'策略等权',soft_rotation:'动态软配权',hard_rotation:'硬切换对照'},pools={base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池'},p=config.poolSettings||{};
    const items=[['区间',config.startDate?config.startDate+' — '+config.endDate:'最近 '+config.lookbackDays+' 个交易日'],['初始资金',n(config.initialCapital)+' 元'],['股票池范围',selection.mode==='explicit'?'指定股票列表':selection.mode==='all'?'全部所选市场与板块':'见原任务来源'],['基础资格','最少上市 '+config.universe?.minListingDays+' 个交易日；20 日均额 ≥ '+n(config.universe?.minAmount20,0)+' 元；'+(config.universe?.excludeSt?'剔除已知 ST':'保留 ST')],['趋势池','窗口 '+p.trend?.window+' 日；最低涨幅 '+thisPercent(p.trend?.minReturn)],['回调结构池','窗口 '+p.structure?.window+' 日；回撤 '+thisPercent(p.structure?.minDrawdown)+' — '+thisPercent(p.structure?.maxDrawdown)],['量能池','此前 '+p.volume?.window+' 日均量；最低 '+n(p.volume?.minRatio)+' 倍'],['组合预算',mode[config.allocation?.mode]||'—'],['候选与持仓','每策略 Top '+config.topN+'；最多 '+config.maxPositions+' 只；新买单股目标上限 '+thisPercent(config.maxStockWeight)],['调仓与风险',({risk:'风险退出后补仓',rank:'按排名换仓',hybrid:'排名与风险调仓'})[config.rebalanceMode]+'；每 '+config.rebalanceEvery+' 日调整；止损 '+thisPercent(config.stopLossPct)+'；止盈 '+thisPercent(config.takeProfitPct)+'；峰值回撤 '+thisPercent(config.trailingStopPct)]];
    for(const node of config.strategies||[]){const def=definition.strategies.find(s=>s.id===node.kind);items.push([def?.name||node.id,(pools[node.pool]||node.pool)+' · '+Object.entries(node.params||{}).map(([key,value])=>(def?.parameters.find(v=>v.key===key)?.label||key)+' '+(isPercent(key)?thisPercent(value):n(value))).join('；')]);}
    for(const node of config.combinations||[])items.push([strategyName(node.id),({intersection:'子策略交集',union:'子策略并集',weighted:'标准分加权'})[node.method]+' · '+node.children.map(child=>strategyName(child.id)+' 权重 '+n(child.weight)).join('；')]);
    return panel('冻结配置与股票池参数',table(['设置','本次取值'],items.map(([label,value])=>`<tr><td>${E(label)}</td><td>${E(value)}</td></tr>`))+`<details class="rs-body"><summary>技术审计：完整参数与来源标识</summary><pre>${E(JSON.stringify({config,selection},null,2))}</pre></details>`);
  }
  const thisPercent=value=>numeric(value)?p(value*100):'—';
  function regimeTable(r) {
    const rows=(r.regimeMetrics||r.metrics?.regimeMetrics||[]).map(v=>`<tr><td>${E(String(v.regime).split('/').map(readable).join(' / '))}</td><td>${n(v.sampleDays,0)}</td><td>${p(v.subsampleReturnPct)}</td><td>${p(v.benchmarkReturnPct)}</td><td>${p(v.conditionalAnnualizedReturnPct??v.annualizedReturnPct)}</td><td>${p(v.maxDrawdownPct)}</td><td>${n(v.sharpe)}</td><td>${p(v.winRatePct)}<small>${n(v.closedTradeCount,0)} 个波段</small></td><td>${p(v.turnoverPct)}</td><td>${n(v.totalFees)}</td><td>${n(v.totalSlippage)}</td><td>${p(v.costRatioPct)}</td></tr>`);
    return panel('按真实市场环境分组的收益',table(['前一收盘市场环境','样本日','子样本复合收益','基准收益','条件年化','条件回撤','条件夏普','完整波段胜率','换手','费用（元）','滑点（元）','成本占比'],rows)+`<p class="rs-help pf-metric-note">环境由所选真实指数的前一收盘状态确定。分组可能跨越不连续日期，条件年化不是一条连续交易策略的年化收益；条件回撤与夏普按该子样本计算。完整交易按退出日环境归属，成本与换手以初始资金为分母。</p>`);
  }
  function equityChart(r,title='账户净值与回撤') {
    const rows=r?.curve||[],equities=rows.flatMap(v=>[v.equity,v.benchmarkEquity].filter(numeric).map(Number));if(!equities.length)return note('尚无实际账户曲线。');
    const low=Math.min(...equities),high=Math.max(...equities),span=Math.max(1,high-low),actualMaxDd=Math.max(0,...rows.map(v=>numeric(v.drawdownPct)?Number(v.drawdownPct):0)),maxDd=Math.max(1,actualMaxDd);
    const x=i=>48+i*920/Math.max(1,rows.length-1),y=v=>160-(v-low)/span*130;
    const line=(key,project)=>{let started=false;return rows.map((v,i)=>{if(!numeric(v[key])){started=false;return '';}const segment=`${started?'L':'M'}${x(i).toFixed(2)},${project(Number(v[key])).toFixed(2)}`;started=true;return segment;}).join(' ');};
    return panel(title,`<div class="rs-body"><svg viewBox="0 0 1000 290" class="pf-equity" role="img" aria-label="逐日账户净值与回撤"><text x="48" y="18" fill="currentColor">净值（元） · 蓝色账户 / 灰色已选真实基准</text><line x1="48" y1="160" x2="968" y2="160" stroke="currentColor" opacity=".2"/><path d="${line('equity',y)}" class="pf-account-curve" fill="none" stroke="var(--accent,#68a9ff)" stroke-width="2.5"/><path d="${line('benchmarkEquity',y)}" class="pf-benchmark-curve" fill="none" stroke="currentColor" opacity=".45" stroke-width="1.5"/><text x="48" y="190" fill="currentColor">回撤（%） · 最大 ${n(actualMaxDd)}%</text><path d="${line('drawdownPct',v=>205+v/maxDd*45)}" class="pf-drawdown-curve" fill="none" stroke="#e6ac69" stroke-width="2"/><text x="48" y="280" fill="currentColor">${E(rows[0].date)}</text><text x="968" y="280" text-anchor="end" fill="currentColor">${E(rows.at(-1).date)}</text></svg><p class="rs-help">净值范围 ${n(low)} — ${n(high)} 元；末日按收盘估值，未强行平仓。缺失点断开显示。</p></div>`);
  }
  const unwrap=v=>v?.result||v;
  const runNames={softRotation:'动态软配权',hardRotation:'动态硬切换（对照）',fixed:'固定策略（仅训练选择）',equalWeight:'所有参与策略等权'};
  const runOrder=['softRotation','hardRotation','fixed','equalWeight'];
  const strategyName=id=>definition.strategies.find(s=>s.id===id)?.name||({combo_intersection:'交集组合',combo_union:'并集组合',combo_weighted:'加权组合'})[id]||id;
  const regimeText=regime=>regime?.status==='observed'?`${readable(regime.trend)} / ${readable(regime.volatility)}波动`:'环境未知；按近期净表现评分';
  const percentageWeights=weights=>Object.entries(weights||{}).map(([id,v])=>`${strategyName(id)} ${n(Number(v)*100)}%`).join(' · ')||'—';
  function rotationLog(r) {
    const logs=r.rotationLog||[],page=r.pagination?.rotationLog||{},path=r.pagination?.path||'';
    return panel('策略权重调整记录',`<details class="rs-body" ${detailAttrs('rotation:'+path)}><summary>查看 ${n(page.total??logs.length,0)} 次调整${page.total>logs.length?'（当前页 '+logs.length+' 次）':''}</summary>${table(['信号日期 → 最早执行日','原预算权重','新预算权重','调整原因','预算转移比例'],logs.map(row=>`<tr><td>${E(row.date)}<small>→ ${E(row.earliestExecutionDate||'—')}</small></td><td>${E(percentageWeights(row.before))}</td><td>${E(percentageWeights(row.after))}</td><td>${E(readable(row.reason))}<small>${E(regimeText(row.regime))}</small></td><td>${p(numeric(row.weightChange)?row.weightChange*100:null)}</td></tr>`))}<div class="rs-actions"><button type="button" class="rs-button" data-pf-action="rotation-page" data-pf-path="${E(path)}" data-pf-offset="${Math.max(0,(page.offset||0)-(page.pageSize||200))}" ${!hooks?.rotationPage||!page.offset?'disabled':''}>前一页调整</button><button type="button" class="rs-button" data-pf-action="rotation-page" data-pf-path="${E(path)}" data-pf-offset="${(page.offset||0)+logs.length}" ${!hooks?.rotationPage||!page.hasMore?'disabled':''}>后一页调整</button></div><details><summary>原始轮动依据</summary><pre>${E(JSON.stringify({rotation:r.rotation,logs},null,2))}</pre></details></details>`);
  }
  function engineResult(r,{nested=false,label='当前组合'}={}) {
    const daily=r.daily||[],path=r.pagination?.path||'';
    let index=dayIndices.get(path)??0;index=Math.max(0,Math.min(index,daily.length-1));dayIndices.set(path,index);
    const day=daily[index],strategies=day?.strategies||day?.topNByStrategy||{};
    const rows=[];
    for(const [id,value] of Object.entries(strategies)) {
      const stocks=Array.isArray(value)?value:value.topN||value.candidates||[];
      stocks.forEach((v,i)=>rows.push(`<tr><td>${E(strategyName(id))}</td><td>${i+1}</td><td>${stock({...v,date:day?.date})}</td><td>${n(v.rawScore,5)}</td><td>${n(v.score,5)}</td><td>${day?.targetWeights?.[v.symbol]>0?'下一开盘目标 '+p(day.targetWeights[v.symbol]*100):'候选观察，尚非买入目标'}</td></tr>`));
    }
    const weights=day?.strategyBudgetWeights||day?.strategyWeights||day?.allocationWeights||{};
    const coverage=r.universeCoverage||r.quality?.coverage?.sourceCoverage||r.quality?.universeCoverage||{};
    const page=r.pagination?.daily||{offset:0,total:daily.length,hasMore:false};
    const action=(name,title,offset,disabled)=>`<button class="rs-button" type="button" data-pf-action="${name}" data-pf-path="${E(path)}" data-pf-offset="${offset}" ${disabled?'disabled':''}>${title}</button>`;
    const fills=day?.fills||(r.trades||[]).filter(row=>row.date===day?.date),activities=day?.activities||(r.decisions||[]).filter(row=>row.date===day?.date);
    const fullDailyLog=Array.isArray(day?.fills)&&Array.isArray(day?.activities)||
      (r.pagination?.trades?.total??Infinity)<=(r.trades?.length||0)&&(r.pagination?.decisions?.total??Infinity)<=(r.decisions?.length||0);
    const holdings=(day?.holdings||[]).map(v=>{const bought=fills.some(row=>row.symbol===v.symbol&&row.side==='buy'),sold=fills.some(row=>row.symbol===v.symbol&&row.side==='sell'),exit=activities.some(row=>row.symbol===v.symbol&&row.action==='sell'&&row.status==='queued');return `<tr><td>${stock({...v,date:day?.date})}</td><td>${n(v.quantity,0)}</td><td>${n(v.marketValue)}</td><td>${p(Number(v.weight)*100)}</td><td>${numeric(day?.targetWeights?.[v.symbol])?p(day.targetWeights[v.symbol]*100):'—'}</td><td>${exit?'已有卖出计划，待下一开盘':bought?'本日买入后持有':sold?'本日减仓后持有':fullDailyLog?'继续持有':'已持仓；操作记录见完整路径'}</td></tr>`;});
    const operationRows=[...fills.map(row=>({...row,status:'filled',action:row.side})),...activities.filter(row=>row.status!=='filled'&&row.action!=='valuation')];
    const operations=table(['股票','操作 / 状态','信号日 → 执行或记录日','股数 / 目标仓位','成交价格 / 费用','原因'],operationRows.map(row=>`<tr><td>${stock(row)}</td><td>${E(window.ResearchDisplay?.operation(row)||operationText(row.action))}<small>${E(operationText(row.status))}</small></td><td>${E(row.signalDate||row.date)}<small>→ ${E(row.date)}</small></td><td>${numeric(row.quantity)?n(row.quantity,0)+' 股':numeric(row.requestedQuantity)?'请求 '+n(row.requestedQuantity,0)+' 股':numeric(row.targetWeight)?'目标 '+p(row.targetWeight*100):'—'}</td><td>${row.status==='filled'?n(row.price)+' 元':'尚未成交'}<small>${row.status==='filled'?'费用 '+n(row.fee)+' 元':''}</small></td><td>${E(operationText(row.reason))}</td></tr>`));
    const today=panel('当日操作：实际成交与待执行计划',`<div class="rs-body"><p>${fullDailyLog?`本日模拟买入 ${fills.filter(row=>row.side==='buy').length} 笔 · 卖出 ${fills.filter(row=>row.side==='sell').length} 笔 · 延后执行 ${activities.filter(row=>row.status==='deferred').length} 项。`:'旧报告当前页可能未包含全部当日操作，请查看当日完整操作路径。'}</p><p class="rs-help">收盘产生的目标和买卖计划尚未成交，最早在下一交易日开盘尝试。候选名单不等于实际买入。</p><button type="button" class="rs-button" data-pf-action="operations-day" data-pf-date="${E(day?.date||'')}" data-pf-path="${E(path)}">查看当日完整操作路径</button></div>${operations}`);
    const regime=day?.regime,status=regime?.status==='observed'?`趋势 ${readable(regime.trend)} · 波动 ${readable(regime.volatility)}`:'未知（缺少所选真实指数的足够历史）';
    const dailyBody=`<div class="rs-body"><div class="rs-actions">${action('previous-page','前一页记录',Math.max(0,page.offset-(page.pageSize||daily.length||20)),!hooks?.dailyPage||page.offset<=0)}${action('previous-day','上一日',0,index<=0)}<strong>${E(day?.date||'暂无每日记录')}</strong>${action('next-day','下一日',0,index>=daily.length-1)}${action('next-page','后一页记录',page.offset+daily.length,!hooks?.dailyPage||!page.hasMore)}</div><p class="rs-help">当前已加载第 ${daily.length?page.offset+1:0} — ${page.offset+daily.length} 日，共 ${page.total} 日。预算权重：${E(percentageWeights(weights))}。预算用于入场分配，实际持仓权重另列。</p><p class="rs-help">基础可交易 ${n(day?.coverage?.eligibleSecurities,0)} 只 · 趋势 ${n(day?.poolCounts?.trend,0)} 只 · 回调结构 ${n(day?.poolCounts?.structure,0)} 只 · 量能 ${n(day?.poolCounts?.volume,0)} 只。</p><p class="rs-help">市场环境：${E(status)}。${day?.rotationDecision?'本日轮动：'+E(readable(day.rotationDecision.reason||day.rotationDecision.status)):'当日没有轮动调整记录。'}</p></div>${table(['策略','排名','股票（点击看日线）','原始分','排名标准分','下一开盘安排'],rows)}${table(['实际持仓','股数','市值（元）','实际权重','待执行目标权重','持仓操作状态'],holdings)}${today}<details class="rs-body"><summary>技术审计（JSON，可选）</summary><pre>${E(JSON.stringify(day||{},null,2))}</pre></details>`;
    const body=(!nested&&r.rotation?.enabled&&r.rotation?.diagnosticOnly?note('本任务为轮动执行诊断，尚未经过持续性准入与样本外双基线验收；不能据此认定轮动有效。'):'')+metricTable([[label,r],...(r.benchmarkMetrics?[["所选业绩基准",{metrics:r.benchmarkMetrics}]]:[])])+equityChart(r,label+' · 账户净值与回撤')+panel('每日候选与策略预算权重',nested?`<details ${detailAttrs('daily:'+path)}><summary class="rs-body">展开逐日 Top N、策略预算与实际持仓</summary>${dailyBody}</details>`:dailyBody)+rotationLog(r);
    if(nested)return body+rightsTable(r)+regimeTable(r);
    return body+panel('数据覆盖与验证边界',`<div class="rs-body">${note((r.warnings||[]).join('；'))}<p>发现 ${n(coverage.discoveredCount,0)} 只 · 选择 ${n(coverage.selectedCount,0)} 只 · 读入 ${n(coverage.loadedCount,0)} 只 · 读取缺口 ${n(coverage.failureCount,0)} 项。</p><p class="rs-help">历史上市日期缺失时，仅以已观察交易日数作为上市年龄下界；严格模式会剔除缺少历史资格的证券。</p><details><summary>查看原始质量与覆盖记录</summary><pre>${E(JSON.stringify({coverage,quality:r.quality},null,2))}</pre></details></div>`)+configuration(r.config,coverage.selection||{});
  }
  function persistencePanel(persistence={}) {
    persistence=persistence||{};
    return panel('持续性检验',`<div class="rs-body"><p>状态：${E(readable(persistence.status))} · 平均排名相关性 ${n(persistence.meanRankCorrelation,4)} · 有效窗口对 ${n(persistence.validPairCount,0)} · 条件置换 p 值 ${n(persistence.pValue,4)}</p><p class="rs-help">${E((persistence.failures||[]).map(readable).join('；'))}</p><details><summary>窗口与相邻排名证据</summary><pre>${E(JSON.stringify(persistence,null,2))}</pre></details></div>`);
  }
  function testingDetails(testing={}) {
    return runOrder.filter(key=>unwrap(testing[key])).map(key=>`<details class="pf-run-detail" ${detailAttrs('run:'+(unwrap(testing[key]).pagination?.path||key))}><summary>${E(runNames[key])} · 样本外净值与逐日明细</summary>${engineResult(unwrap(testing[key]),{nested:true,label:runNames[key]})}</details>`).join('');
  }
  function gatePanel(gate={},comparison={}) {
    gate=gate||{};comparison=comparison||{};
    return panel('轮动关键检验',`<div class="rs-body">${note(gate.conclusion||comparison.conclusion||'未计算轮动系统，不能宣称它优于等权组合。')}<p>状态：${E(readable(gate.status))}</p><p>软配权相对等权净收益差：${p(comparison.softMinusEqualReturnPct)}；相对固定策略：${p(comparison.softMinusFixedReturnPct)}。</p><details><summary>查看准入与验收证据</summary><pre>${E(JSON.stringify(gate,null,2))}</pre></details></div>`);
  }
  function studyResult(r) {
    const testing=r.testing||{},persistence=r.training?.persistence||r.persistence||{};
    return panel('训练区选定，样本外验收',metricTable(runOrder.filter(key=>testing[key]).map(key=>[runNames[key],unwrap(testing[key])]))+`<div class="rs-body"><p>训练选定策略：${E(r.training?.selectedStrategyId||r.selectedStrategyId||'尚未选定')}</p><p class="rs-help">固定策略只在训练区选择；软配权与硬切换通过持续性门槛后才计算。样本外结果用于验收，不据此追加调参。</p></div>`)+gatePanel(r.rotationGate,r.comparison)+persistencePanel(persistence)+testingDetails(testing)+panel('训练策略对照',`<details class="rs-body"><summary>展开训练组合绩效</summary>${metricTable(Object.entries(r.training?.runs||{}).map(([id,result])=>[id,unwrap(result)]))}</details>`)+panel('质量与方法',`<details class="rs-body"><summary>展开数据边界与验证协议</summary><pre>${E(JSON.stringify({quality:r.quality,warnings:r.warnings,protocol:r.protocol},null,2))}</pre></details>`);
  }
  function heatmap(plateau,configuredAxes=[]) {
    const rows=plateau?.heatmap||[];
    if(!rows.length)return note('尚无已计算的参数邻域，不显示示例热图。');
    const axes=configuredAxes.length?configuredAxes:Object.keys(rows[0].params||{}).map(path=>({path,values:[...new Set(rows.map(row=>row.params?.[path]))]}));
    const x=axes[0],y=axes[1],slices=axes.slice(2);
    for(const axis of slices)if(!axis.values.some(value=>String(value)===String(heatSlices.get(axis.path))))heatSlices.set(axis.path,axis.values[0]);
    const visible=rows.filter(row=>slices.every(axis=>String(row.params?.[axis.path])===String(heatSlices.get(axis.path))));
    const values=rows.map(row=>row.neighborhood?.meanNetReturnPct).filter(numeric).map(Number),limit=Math.max(1,...values.map(Math.abs));
    const label=parameterLabel;
    const valueText=(path,value)=>numeric(value)?String(display(path,value)):({intersection:'交集',union:'并集',weighted:'加权'}[value]||String(value));
    const controls=slices.length?`<div class="rs-fields pf-heat-slices">${slices.map(axis=>`<label class="rs-field"><span>固定 ${E(label(axis.path))}</span><select data-pf-heat-slice="${E(axis.path)}">${axis.values.map(value=>`<option value="${E(String(value))}" ${String(value)===String(heatSlices.get(axis.path))?'selected':''}>${E(valueText(axis.path,value))}</option>`).join('')}</select></label>`).join('')}</div>`:'';
    const headings=x?`<div class="pf-heat-axis pf-heat-origin">${E(y?label(y.path)+' ↓':'参数')} / ${E(label(x.path))} →</div>${x.values.map((value,i)=>`<div class="pf-heat-axis" style="grid-column:${i+2};grid-row:1">${E(valueText(x.path,value))}</div>`).join('')}${(y?.values||['当前切片']).map((value,i)=>`<div class="pf-heat-axis" style="grid-column:1;grid-row:${i+2}">${E(y?valueText(y.path,value):value)}</div>`).join('')}`:'';
    return `<p class="rs-help">每格为实际计算的参数组合，颜色表示训练邻域平均净收益。边框标记通过训练邻域门槛的点；样本外证据另列，不用来回选最优点。当前切片显示 ${visible.length} / ${rows.length} 组。</p>${controls}<div class="pf-heat-scroll"><div class="pf-heatmap ${x?'pf-heat-grid':''}" style="${x?'grid-template-columns:150px repeat('+x.values.length+',minmax(140px,1fr))':''}" role="list" aria-label="全系统参数邻域收益热图">${headings}${visible.map(row=>{
      const a=row.neighborhood||{},value=numeric(a.meanNetReturnPct)?Number(a.meanNetReturnPct):null;
      const alpha=value===null?0:Math.min(.65,.12+.5*Math.abs(value)/limit),color=value===null?'transparent':`rgba(${value>=0?'40, 148, 112':'203, 84, 87'}, ${alpha.toFixed(3)})`;
      return `<details role="listitem" class="pf-heat-cell ${a.passed?'pf-heat-pass':''}" style="background:${color};${x?'grid-column:'+(x.values.findIndex(v=>String(v)===String(row.params?.[x.path]))+2)+';grid-row:'+(y?y.values.findIndex(v=>String(v)===String(row.params?.[y.path]))+2:2):''}"><summary><small>${E(row.candidateId)}</small><strong>${p(value)}</strong><span>${a.passed?'训练邻域通过':'训练邻域未通过'}</span><span>${row.rotationExecuted===false?'持续性未通过，含等权替代结果':row.rotationExecuted?'已计算轮动系统':''}</span></summary><p>${E(Object.entries(row.params||{}).map(([path,v])=>parameterLabel(path)+' = '+(numeric(v)?display(path,v):v)).join(' · '))}</p><p>邻域 ${n(a.size,0)} 点 · 标准差 ${n(a.stdNetReturnPct)} 个百分点 · 正收益占比 ${p(numeric(a.positiveFraction)?a.positiveFraction*100:null)}</p><p>训练单点均值 ${p(row.trainingMeanNetReturnPct)} · 样本外均值 ${p(row.oosMeanNetReturnPct)}</p></details>`;
    }).join('')}</div></div>`;
  }
  function experimentResult(r) {
    const plateau=r.plateau||{},ranges=plateau.recommendedRanges||[],folds=r.folds||[];
    const rows=ranges.map(row=>`<tr><td>${E({strategy:'策略',combination:'组合',rotation:'轮动',holdings:'持仓'}[row.layer]||row.layer)}</td><td>${E(parameterLabel(row.path))}</td><td>${E((row.values||[]).map(value=>numeric(value)?display(row.path,value):value).join('、'))}</td></tr>`);
    const foldPanels=folds.map((fold,index)=>`<details class="pf-fold" ${detailAttrs('fold:'+index)}><summary>第 ${n(fold.fold??index+1,0)} 折 · ${E(fold.testing?.softRotation?'已计算轮动对照':fold.rotationGate?.status==='skipped'?'轮动未通过准入':'样本外验证')}</summary><div class="rs-body"><p>训练：${E(fold.trainStart||'—')} — ${E(fold.trainEnd||'—')}；样本外：${E(fold.testStart||'—')} — ${E(fold.testEnd||'—')}</p><details><summary>本折冻结参数与选择依据</summary><pre>${E(JSON.stringify({training:fold.training,selection:fold.selection},null,2))}</pre></details></div>${gatePanel(fold.rotationGate,fold.comparison)}${persistencePanel(fold.persistence)}${metricTable(runOrder.filter(key=>fold.testing?.[key]).map(key=>[runNames[key],unwrap(fold.testing[key])]))}${testingDetails(fold.testing)}</details>`).join('');
    return panel((r.kind||r.type)==='systemPlateau'?'全系统收益平原':'滚动样本外验证',`<div class="rs-body"><p>参数组合总数：${n(r.trialCount,0)} · 逐折候选评估：${n(r.actualCandidateEvaluations,0)} / ${n(r.plannedCandidateEvaluations,0)} · 账户模拟次数：${n(r.engineRunCount,0)} · 滚动折数：${n(folds.length,0)}</p>${note(r.conclusion||plateau.conclusion||'等待完成预设研究；阶段结果不能作为最终结论。')}<p class="rs-help">各折仅用训练数据选择设置，后续样本外冻结。没有通过跨年份、跨市场环境和数据质量门槛时，不给出推荐参数区间。</p></div>`)+panel('参数邻域与平原区域',`<div class="rs-body">${heatmap(plateau,r.experiment?.axes)}</div>`)+panel('推荐参数区间与样本外证据',`<div class="rs-body">${note(plateau.certified?'通过本次预设验证门槛。':'尚未获得经过验证的收益平原。')}${(plateau.reasons||[]).map(reason=>note(readable(reason))).join('')}${ranges.length?table(['参数层','参数','验证通过的取值区间'],rows):'<p>暂无推荐区间。</p>'}<p class="rs-help">推荐区间仅在全部离散网格交叉组合都已验证时输出；未扫描的连续插值不算已验证。</p><details><summary>查看分年份、分环境及邻域验证记录</summary><pre>${E(JSON.stringify(plateau,null,2))}</pre></details></div>`)+panel('逐折训练、检验与样本外账户',foldPanels||note('尚无已完成的滚动折。'))+panel('完整研究设置与数据边界',`<details class="rs-body"><summary>展开冻结配置、研究规模与警告</summary><pre>${E(JSON.stringify({experiment:r.experiment,config:r.config,quality:r.quality,warnings:r.warnings,protocol:r.protocol},null,2))}</pre></details>`);
  }
  function result(r) {
    if(!r)return note('正在准备数据或等待计算结果。');
    displayNames=r.securityNames||{};
    if((r.kind||r.type)==='poolPreview'){screen.task=hooks?.activeTaskId?.()||screen.task;screen.result=r;return screenView(r,true);}
    if(experimentKinds.has(r.kind||r.type))return refinementEvidence(r)+experimentResult(r);
    if(r.kind==='comparison'||r.testing)return refinementEvidence(r)+studyResult(r);
    return refinementEvidence(r)+engineResult(r)+rightsTable(r)+regimeTable(r);
  }
  function set(path,value) {
    const keys=path.split('.');
    if (['market','board'].includes(keys[0])) {
      const field=keys[0]==='market'?'markets':'boards', current=draft.selection[field];
      draft.selection[field]=value?[...new Set([...current,keys[1]])]:current.filter(v=>v!==keys[1]);return;
    }
    let target=draft;for(const k of keys.slice(0,-1))target=target[k];target[keys.at(-1)]=value;
  }
  function capture(el) { if(!el?.dataset?.pfField||!draft)return;set(el.dataset.pfField,el.dataset.pfType==='boolean'?el.checked:el.dataset.pfType==='text'?el.value:stored(el.dataset.pfField,el.value));save();queueReview(); }
  document.addEventListener('toggle',event=>{const key=event.target?.dataset?.pfDetail;if(key){event.target.open?expandedDetails.add(key):expandedDetails.delete(key);}},true);
  document.addEventListener('input',event=>{if(event.target.matches?.('[data-pf-field]'))capture(event.target);});
  document.addEventListener('keydown',event=>{
    if(event.key!=='Enter'||!event.target.closest?.('[data-pf-form]'))return;
    if(event.target.matches?.('[data-pf-screen-query]')){event.preventDefault();screen.query=event.target.value.trim();screen.offset=0;void loadScreen().catch(hooks.error);return;}
    if(currentStep()!==4){event.preventDefault();setStep(Math.min(4,currentStep()+1));hooks.refresh();}
  });
  document.addEventListener('change',event=>{const el=event.target;if(el.dataset?.pfHeatSlice){heatSlices.set(el.dataset.pfHeatSlice,el.value);hooks?.refresh();return;}
    if(el.dataset?.pfPresetSelect){
      const id=el.dataset.pfPresetSelect,preset=presetsFor(id).find(v=>v.id===el.value);
      if(preset){draft.params[id]=structuredClone(preset.params);draft.pools[id]=preset.pool||draft.pools[id];chosenPresets[id]=preset.id;save();queueReview();hooks.refresh();}else{delete chosenPresets[id];}
      return;
    }
    if(!el.matches?.('[data-pf-field]'))return;capture(el);
    if(el.dataset.pfField==='kind'){
      if(experimentKinds.has(draft.kind))expandedDetails.add('editor:experiment');
      if(draft.kind==='comparison')expandedDetails.add('editor:persistence');
    }
    if(el.tagName==='SELECT'||el.type==='checkbox')hooks?.refresh();
  });
  document.addEventListener('submit',event=>{
    if(!event.target.matches?.('[data-pf-form]'))return;event.preventDefault();event.stopImmediatePropagation();
    for(const el of event.target.querySelectorAll('[data-pf-field]'))capture(el);
    if(workflow.submitting)return;
    try {
      const request=payload(draft,definition.strategies),signature=JSON.stringify(request);
      if(typeof hooks.previewSettings!=='function'){editingTaskId?hooks.update({...request,id:editingTaskId}):hooks.create(request);return;}
      workflow.submitting=true;hooks.refresh();
      void reviewSettings().then(effective=>{
        if(requestSignature()!==signature)throw new Error('设置在校验期间发生变化，请按当前配置重新创建任务。');
        const accepted={kind:effective.kind,name:effective.name,config:effective.config,selection:effective.selection,...(effective.experiment?{experiment:effective.experiment}:{})};
        return editingTaskId?hooks.update({...accepted,id:editingTaskId}):hooks.create(accepted);
      }).catch(error=>{setStep(4);hooks.error(error);}).finally(()=>{workflow.submitting=false;hooks.refresh();});
    } catch(error) {setStep(4);hooks.error(error);hooks.refresh();}
  },true);
  document.addEventListener('click',event=>{
    const el=event.target.closest?.('[data-pf-action]');if(!el||el.disabled)return;event.preventDefault();
    if(el.dataset.pfAction==='workflow-step'){setStep(el.dataset.pfStep);hooks.refresh();document.querySelector?.('.pf-workbench')?.scrollIntoView?.({block:'start',behavior:'instant'});return;}
    if(el.dataset.pfAction==='review-settings'){void reviewSettings().catch(()=>{});return;}
    if(el.dataset.pfAction==='reset-strategy'){
      const id=el.dataset.pfStrategy,s=definition.strategies.find(s=>s.id===id);if(!s)return;
      draft.params[id]=Object.fromEntries(s.parameters.map(p=>[p.key,p.default]));delete chosenPresets[id];save();queueReview();hooks.refresh();return;
    }
    if(el.dataset.pfAction==='save-strategy-preset'){
      const id=el.dataset.pfStrategy,s=definition.strategies.find(s=>s.id===id);if(!s)return;
      const name=document.querySelector?.('[data-pf-preset-name="'+id+'"]')?.value.trim().slice(0,60)||s.name+' · 预设 '+(presetsFor(id).length+1);
      const preset={id:'preset-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8),name,params:structuredClone(draft.params[id]),pool:draft.pools[id]};
      writePresets(id,[preset,...presetsFor(id)].slice(0,30));chosenPresets[id]=preset.id;hooks.feedback('已保存策略参数预设：'+name);return;
    }
    if(el.dataset.pfAction==='delete-strategy-preset'){const id=el.dataset.pfStrategy;writePresets(id,presetsFor(id).filter(p=>p.id!==chosenPresets[id]));delete chosenPresets[id];hooks.feedback('所选参数预设已删除，当前参数保留。');return;}
    if(el.dataset.pfAction==='screen-preview'){
      try{const request=screenPayload();screen.signature=screenSignature();screen.result=null;screen.offset=0;screen.query='';void hooks.screenCreate(request).then(created=>{if(!created?.id)return;screen.task=created.id;screen.meta=created;hooks.refresh();}).catch(hooks.error);}catch(error){hooks.error(error);}return;
    }
    if(el.dataset.pfAction==='screen-pool'){screen.pool=el.dataset.pfPool;screen.offset=0;void loadScreen().then(()=>document.querySelector?.('[data-pf-screen-query]')?.scrollIntoView?.({block:'center'})).catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-page'){screen.offset=Number(el.dataset.pfOffset);void loadScreen().catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-search'){screen.query=document.querySelector('[data-pf-screen-query]')?.value.trim()||'';screen.offset=0;void loadScreen().catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-clear'){screen.query='';screen.offset=0;void loadScreen().catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-task'){hooks.openTask(screen.task);return;}
    if(el.dataset.pfAction==='operations-day'){hooks.operationsDay(el.dataset.pfDate,el.dataset.pfPath||'');return;}
    if(el.dataset.pfAction==='cancel-edit'){editingTaskId=null;editorNote='';draft=makeDraft(definition);save();queueReview();hooks.refresh();return;}
    if(el.dataset.pfAction==='import-history'){
      if(typeof window.atradeDesktop?.importResearchHistory!=='function'){hooks.error(new Error('请在桌面应用中导入历史数据包。'));return;}
      importingHistory=true;hooks.refresh();
      void window.atradeDesktop.importResearchHistory().then(response=>{
        if(!response.ok)throw new Error(response.error||'历史数据包校验失败。');
        if(response.canceled)return;
        definition.historySources=response.data.sources;
        draft.selection.source='history';draft.selection.sourceId=response.data.imported.id;
        draft.periodMode='dates';draft.config.startDate=response.data.imported.coverage.startDate;draft.config.endDate=response.data.imported.coverage.endDate;
        draft.config.universe.strictHistory=true;save();hooks.feedback('历史包已校验并保存，已选择其覆盖日期。请核对股票池与权益模型后开始研究。');
      }).catch(hooks.error).finally(()=>{importingHistory=false;hooks.refresh();});return;
    }
    if(el.dataset.pfAction==='configure-plateau'){draft.kind='systemPlateau';draft.periodMode='dates';setStep(4);expandedDetails.add('editor:experiment');queueReview();AT.navigate?.('R03');return;}
    if(el.dataset.pfAction==='reset'){editingTaskId=null;editorNote='';draft=makeDraft(definition);save();queueReview();hooks.refresh();}
    if(el.dataset.pfAction==='save'){save();hooks.feedback('回测设置已保存在本机。');}
    if(el.dataset.pfAction==='rotation-page'){hooks?.rotationPage?.(Number(el.dataset.pfOffset),el.dataset.pfPath||'');}
    if(el.dataset.pfAction==='previous-day'){const path=el.dataset.pfPath||'';dayIndices.set(path,Math.max(0,(dayIndices.get(path)||0)-1));hooks.refresh();}
    if(el.dataset.pfAction==='next-day'){const path=el.dataset.pfPath||'';dayIndices.set(path,(dayIndices.get(path)||0)+1);hooks.refresh();}
    if(['previous-page','next-page'].includes(el.dataset.pfAction)){const path=el.dataset.pfPath||'';dayIndices.set(path,0);hooks?.dailyPage?.(Number(el.dataset.pfOffset),path);}
  });
  window.PortfolioWorkbench={configure,form,result,configuration,helpers,editTask,currentStep,setStep,reviewSettings,newTask(){draft=makeDraft(definition);editingTaskId=null;editorNote='';save();queueReview();},screenRequest:()=>({pool:screen.pool,poolOffset:screen.offset,poolQuery:screen.query}),selectScreenTask(id){screen.task=id;screen.pool='base';screen.offset=0;screen.query='';},screenRefresh:()=>screen.task?loadScreen():Promise.resolve(),available:()=>!!definition,
    selectStrategy(id){if(!draft)return;for(const s of definition.strategies)draft.enabled[s.id]=s.id===id;draft.participates[id]=true;for(const combination of Object.values(draft.combinations))combination.enabled=false;draft.config.allocation.mode='equal';setStep(2);queueReview();},
    plateau(){return `<div class="rs-body">${note('全系统平原按策略、组合、轮动、持仓四层参数计算；训练区选区间，样本外逐折验证。')}<button type="button" class="rs-button rs-primary" data-pf-action="configure-plateau">配置全系统收益平原</button></div>`;}};
})();
