/* Whole-universe research editor. Only the Python service produces results. */
(() => {
  'use strict';
  const percentKeys = new Set(['maxStockWeight','switchThreshold','stopLossPct','takeProfitPct','trailingStopPct','commissionRate','transferFeeRate','stampDutyRate','maxParticipationRate','minReturn','minDrawdown','maxDrawdown','minDominance','minWeightChange','regimeBlend','minNeighborPositiveFraction','minAmplitude20','maxPriceVsMa20','minVolumeChange20']);
  const numeric = value => typeof value !== 'boolean' && value !== null && value !== '' && Number.isFinite(Number(value));
  const isPercent = path => percentKeys.has(path.split('.').at(-1));
  const displayScale = path => path.split('.').at(-1)==='minAmount20' ? .0001 : isPercent(path) ? 100 : 1;
  const display = (path, value) => numeric(value) ? +(Number(value) * displayScale(path)).toFixed(8) : '';
  const stored = (path, value) => value === '' ? null : Number(value) / displayScale(path);
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
  const defaultsSingleExperiment=()=>({...defaultsExperiment(),maxTrials:27,minClosedTrades:3,maxDrawdownPct:30});
  const experimentKinds = new Set(['walkForward','systemPlateau','singlePlateau']);
  const singleKinds = new Set(['singleBacktest','singlePlateau']);
  const axisLayer = path => path.startsWith('strategies.')?'strategy':path.startsWith('combinations.')?'combination':path.startsWith('rotation.')?'rotation':'holdings';
  function scanAxes(rows,kind,max=81) {
    const axes=rows.map((row,index)=>({...row,index})).filter(row=>row.path).map(row=>{
      const tokens=String(row.text||'').trim().split(/[\s,，;；、]+/).filter(Boolean);
      const values=[...new Set(tokens.map(value=>row.path.endsWith('.method')?({'交集':'intersection','并集':'union','加权':'weighted'}[value]||value):numeric(value)?stored(row.path,value):null))];
      if(values.length<2||values.length>5||values.includes(null))throw new Error(`扫描项 ${row.index+1}（${row.path}）：需要 2 至 5 个不同有效取值；只填一个值请移到固定条件。`);
      if(row.path.endsWith('.method')&&values.some(v=>!['intersection','union','weighted'].includes(v)))throw new Error('组合方式请输入交集、并集或加权。');
      return {path:row.path,values};
    });
    if(new Set(axes.map(a=>a.path)).size!==axes.length)throw new Error('扫描参数不能重复。');
    if(kind==='singlePlateau'&&(axes.length<1||axes.length>3))throw new Error('单策略平原只扫描 1 至 3 个关键参数；其余参数保持固定。');
    if(kind==='singlePlateau'&&axes.some(a=>a.path.startsWith('combinations.')||a.path.startsWith('rotation.')||a.values.some(v=>typeof v!=='number')))throw new Error('单策略平原只扫描策略数值参数或持仓参数。');
    if(kind==='systemPlateau'&&new Set(axes.map(a=>axisLayer(a.path))).size!==4)throw new Error('系统收益平原需要同时选择策略、组合、轮动、持仓四层参数。');
    const count=axes.reduce((total,a)=>total*a.values.length,1);
    const limit=Math.min(kind==='singlePlateau'?27:81,Number(max));
    if(count>limit)throw new Error(`共有 ${count} 组参数，超过本次扫描上限 ${limit}。`);
    return axes;
  }
  function payload(draft, catalogue) {
    if(draft.kind==='poolPreview')return poolPayload(draft);
    const config = structuredClone(draft.config), selection = structuredClone(draft.selection);
    const picked = catalogue.filter(s => draft.enabled[s.id]);
    if (!picked.length) throw new Error('至少启用一个基础策略。');
    if(singleKinds.has(draft.kind)&&picked.length!==1)throw new Error('这一阶段只研究一个策略，请先选择单一策略。');
    config.strategies = picked.map(s => ({id:s.id,kind:s.id,pool:draft.pools[s.id] || 'base',params:structuredClone(draft.params[s.id])}));
    config.combinations = Object.entries(draft.combinations).filter(([,c]) => c.enabled).map(([method,c]) => ({
      id:'combo_' + method, method, normalization:'rank', threshold:Number(c.threshold || 0),
      children:picked.map(s => ({id:s.id,weight:Number(draft.weights[s.id] ?? 1)})),
    }));
    if (config.combinations.length && picked.length < 2) throw new Error('组合策略至少启用两个子策略。');
    if(singleKinds.has(draft.kind)&&(config.combinations.length||config.allocation.mode!=='single'))throw new Error('单策略研究不使用组合或轮动配权。');
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
    } else if(draft.kind==='singlePlateau') {
      if(draft.periodMode!=='dates')throw new Error('单策略平原需要明确的开发区间起止日期。');
      delete config.study;
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
    if(experimentKinds.has(draft.kind)){
      request.experiment=draft.kind==='singlePlateau'?{axes:scanAxes(draft.scanAxes,draft.kind,27),...Object.fromEntries(['minNeighborPositiveFraction','minNeighborhoodSize','maxNeighborhoodStdPct','minClosedTrades','maxDrawdownPct'].map(key=>[key,draft.experiment[key]]))}:{...structuredClone(draft.experiment),kind:draft.kind,axes:scanAxes(draft.scanAxes,draft.kind,draft.experiment.maxTrials)};
      // Numeric reference fields are overwritten in every candidate anyway.
      // Use the first declared value for validation, so a hidden empty/obsolete
      // base value cannot prevent a valid grid from being created.
      for(const axis of request.experiment.axes){
        if(typeof axis.values[0]!=='number')continue;
        const parts=axis.path.split('.'),last=parts.at(-1);let target;
        if(parts[0]==='strategies')target=config.strategies.find(s=>s.id===parts[1])?.params;
        else if(parts[0]==='combinations'){const c=config.combinations.find(c=>c.id===parts[1]);target=parts[2]==='children'?c?.children.find(c=>c.id===parts[3]):c;}
        else target=parts.length===2?config[parts[0]]:config;
        if(target)target[last]=axis.values[0];
      }
    }
    return request;
  }
  function poolPayload(draft){
    const current=draft.config,selection=structuredClone(draft.selection);
    const config={universe:structuredClone(current.universe),poolSettings:structuredClone(current.poolSettings),lookbackDays:current.lookbackDays,
      startDate:draft.periodMode==='recent'?null:current.startDate,endDate:draft.periodMode==='recent'?null:current.endDate};
    if(draft.periodMode!=='recent'&&(!config.startDate||!config.endDate||config.startDate>config.endDate))throw new Error('请填写有效的起止日期。');
    if(selection.source==='history'&&(draft.periodMode!=='dates'||!selection.sourceId))throw new Error('历史数据包须选择数据源并填写覆盖范围内的明确日期。');
    selection.symbols=selection.mode==='explicit'?symbols(draft.symbols):[];
    if(selection.mode==='explicit'&&!selection.symbols.length)throw new Error('指定股票池不能为空。');
    if(!selection.markets.length||!selection.boards.length)throw new Error('请至少选择一个市场和一个板块。');
    selection.lookbackDays=config.lookbackDays;selection.benchmarkSymbol=null;
    return {kind:'poolPreview',name:('股票池筛选 · '+(draft.name||'当前设置')).slice(0,100),config,selection};
  }
  const helpers = {numeric,isPercent,display,stored,symbols,payload,poolPayload,scanAxes,axisLayer};
  if (typeof module === 'object' && module.exports) module.exports = helpers;
  if (typeof window === 'undefined' || typeof AT === 'undefined') return;
  const E = AT.escape, key = 'atrade.single-editor.v1';
  let definition, hooks, draft, importingHistory=false, editingTaskId=null, editorNote='', displayNames={};
  const freshScreen=()=>({task:null,meta:null,result:null,pool:'base',query:'',offset:0,signature:null,serial:0,busy:false});
  let screen=freshScreen(),scope='research';
  const scopes={},marketKey='atrade.stock-pool.v1',plateauKey='atrade.single-plateau-editor.v1';
  const scopeKey=name=>({market:marketKey,plateau:plateauKey,advanced:'atrade.portfolio-editor.v1',advancedPlateau:'atrade.plateau-editor.v1'}[name]||key);
  const scopeFor=kind=>kind==='singlePlateau'?'plateau':kind==='systemPlateau'?'advancedPlateau':kind==='singleBacktest'?'research':'advanced';
  const isSingle=()=>singleKinds.has(draft?.kind);
  const isPlateau=()=>scope==='plateau'||scope==='advancedPlateau';
  const combinedDefaults=()=>({pools:['trend','structure','volume'],mode:'intersection'});
  function ensureCombined(value){
    value.config.universe={...structuredClone(definition.defaults.universe),...value.config.universe};
    for(const key of ['trend','structure','volume'])value.config.poolSettings[key]={...structuredClone(definition.defaults.poolSettings[key]),...value.config.poolSettings[key]};
    value.config.poolSettings.combined={...combinedDefaults(),...value.config.poolSettings.combined};return value;
  }
  function restoredDraft(value){
    if(!value?.config?.universe||!value.config.allocation||!value.config.poolSettings||!Array.isArray(value.selection?.markets)||!Array.isArray(value.selection?.boards)||!value.enabled||!value.params||!value.pools||!value.weights||!value.participates||!value.combinations||!definition.strategies.every(s=>value.params[s.id]))return null;
    value.config.rotation={...defaultsRotation(),...value.config.rotation};value.config.rightsPolicy=value.config.rightsPolicy||'skip';value.selection.source=value.selection.source||'local';value.study={...defaultsStudy(),...value.study};value.experiment={...(singleKinds.has(value.kind)?defaultsSingleExperiment():defaultsExperiment()),...value.experiment};value.scanAxes=Array.isArray(value.scanAxes)?value.scanAxes:Array.from({length:singleKinds.has(value.kind)?3:6},()=>({path:'',text:''}));return ensureCombined(value);
  }
  function activateScope(next){
    if(!definition||scope===next)return;
    scopes[scope]={draft,screen,editingTaskId,editorNote};
    clearTimeout(workflow.timer);workflow.serial++;workflow.pending=false;workflow.review=null;workflow.signature=null;
    if(!scopes[next]&&next!=='market'){
      let value=ensureCombined(makeDraft(definition,next==='research'||next==='plateau'));
      if(next==='plateau'||next==='advancedPlateau'){value.kind=next==='plateau'?'singlePlateau':'systemPlateau';value.periodMode='dates';if(next==='advancedPlateau')value.combinations.union.enabled=true;}
      try{const saved=JSON.parse(localStorage.getItem(scopeKey(next))),candidate=restoredDraft(saved?.draft);if(candidate&&scopeFor(candidate.kind)===next)value=candidate;else if(next==='advanced'&&candidate?.kind==='systemPlateau'&&!localStorage.getItem(scopeKey('advancedPlateau')))saveScope('advancedPlateau',candidate,freshScreen());}catch{}
      scopes[next]={draft:ensureCombined(value),screen:freshScreen(),editingTaskId:null,editorNote:''};
      if(['plateau','advancedPlateau'].includes(next)&&!value.scanAxes.some(row=>row.path))seedPlateauAxes(value);
    }
    if(!scopes[next]){
      const value=ensureCombined(makeDraft(definition,false)),view=freshScreen();view.pool='combined';
      value.selection={...value.selection,source:'local',mode:'all',symbols:[],markets:['sh','sz','bj'],boards:['main','chinext','star','bse']};
      try{const saved=JSON.parse(localStorage.getItem(marketKey));if(saved?.draft?.config?.universe&&saved.draft.config.poolSettings&&Array.isArray(saved.draft.selection?.markets)&&Array.isArray(saved.draft.selection?.boards)){
        value.config.universe={...value.config.universe,...saved.draft.config.universe};
        value.config.poolSettings={...value.config.poolSettings,...saved.draft.config.poolSettings};
        for(const name of ['startDate','endDate','lookbackDays'])value.config[name]=saved.draft.config[name]??value.config[name];
        value.periodMode=saved.draft.periodMode||'recent';value.selection=saved.draft.selection;value.symbols=saved.draft.symbols||'';
        Object.assign(view,{task:saved.screen?.task||null,signature:saved.screen?.signature||null});
      }}catch{}
      scopes[next]={draft:ensureCombined(value),screen:view};
      if(!view.task)expandedDetails.add('editor:stock-pool-settings');
    }
    ({draft,screen}=scopes[next]);editingTaskId=scopes[next].editingTaskId||null;editorNote=scopes[next].editorNote||'';scope=next;
  }
  function saveScope(name,value,view){try{localStorage.setItem(scopeKey(name),JSON.stringify({version:definition.version,draft:value,...(name==='market'?{screen:{task:view.task,signature:view.signature}}:{})}));}catch{}}
  const dayIndices=new Map(), selectedDates=new Map(), selectedDaily=new Map(), dayRequests=new Map(), chartRuns=new Map();
  let resultTaskId;
  const expandedDetails=new Set(), heatSlices=new Map();
  const detailAttrs=key=>`data-pf-detail="${E(key)}" ${expandedDetails.has(key)?'open':''}`;
  const n = (v,d=2) => numeric(v) ? Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d}) : '—';
  const p = v => numeric(v) ? n(v) + '%' : '—';
  const field = (label,path,value,attrs='') => {
    const axes=isPlateau()?scannedEditorAxes(path):[];
    if(axes.length&&path.startsWith('weights.'))return `<label class="rs-field"><span>${E(label)} · 各组合的基础权重</span><input data-pf-field="${E(path)}" type="number" step="any" value="${E(display(path,value))}" ${attrs}><small>仅 ${E(axes.map(a=>parameterLabel(a.path)).join('、'))} 使用扫描候选；其余组合仍固定使用此值。</small></label>`;
    return axes.length?`<div class="rs-field pf-scanned-field" data-pf-scanned-field="${E(path)}"><span>${E(label)} · 已设为扫描项</span><strong>${E(axes.map(a=>a.text||'候选值待填写').join(' / '))}</strong><small>逐组使用上述候选值；在第 2 步修改。基础值 ${E(display(path,value))} 不作为固定值。</small></div>`:`<label class="rs-field"><span>${E(label)}</span><input data-pf-field="${E(path)}" type="number" step="any" value="${E(display(path,value))}" ${attrs}></label>`;
  };
  const textField = (label,path,value,type='text') => `<label class="rs-field"><span>${E(label)}</span><input data-pf-field="${E(path)}" data-pf-type="text" type="${type}" ${path==='name'?'maxlength="100"':''} value="${E(String(value || ''))}"></label>`;
  const select = (label,path,value,options) => `<label class="rs-field"><span>${E(label)}</span><select data-pf-field="${E(path)}" data-pf-type="text">${Object.entries(options).map(([v,t]) => `<option value="${E(v)}" ${v===value?'selected':''}>${E(t)}</option>`).join('')}</select></label>`;
  const check = (label,path,value) => `<label class="pf-check"><input type="checkbox" data-pf-field="${E(path)}" data-pf-type="boolean" ${value?'checked':''}>${E(label)}</label>`;
  const note = text => `<div class="rs-notice">${E(text)}</div>`;
  const table = (headers,rows) => `<div class="rs-table-wrap"><table class="rs-table"><thead><tr>${headers.map(h=>`<th>${E(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.join(''):`<tr><td colspan="${headers.length}">没有记录</td></tr>`}</tbody></table></div>`;
  const panel = (title,body) => `<section class="rs-panel"><header><h2>${E(title)}</h2></header>${body}</section>`;
  function makeDraft(data,single=true) {
    const config=structuredClone(data.defaults), enabled={},params={},pools={},weights={},participates={};
    config.rotation={...defaultsRotation(),...config.rotation};
    for (const s of data.strategies) {
      const selected=config.strategies.find(r=>r.kind===s.id);
      enabled[s.id]=single?s.id===(config.strategies[0]?.kind||data.strategies[0]?.id):!!selected;
      params[s.id]={...Object.fromEntries(s.parameters.map(p=>[p.key,p.default])),...selected?.params};
      pools[s.id]=selected?.pool||'base';weights[s.id]=1;participates[s.id]=true;
    }
    if(single){config.allocation.mode='single';const base=data.poolStyles?.find(row=>row.id==='base');if(base)config.poolSettings=structuredClone(base.poolSettings);else config.poolSettings.combined={pools:[],mode:'intersection'};}
    return {kind:single?'singleBacktest':'portfolio',name:'',periodMode:single?'dates':'recent',config,selection:structuredClone(data.selection),symbols:'',enabled,params,pools,weights,participates,
      combinations:Object.fromEntries(['intersection','union','weighted'].map(k=>[k,{enabled:false,threshold:0}])),study:defaultsStudy(),experiment:{...(single?defaultsSingleExperiment():defaultsExperiment()),...(single?data.singleExperimentDefaults:data.experimentDefaults),...(single?{maxTrials:27}:{})},scanAxes:Array.from({length:single?3:6},()=>({path:'',text:''}))};
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
          candidate.experiment={...(singleKinds.has(candidate.kind)?defaultsSingleExperiment():defaultsExperiment()),...candidate.experiment};
          candidate.scanAxes=Array.isArray(candidate.scanAxes)?candidate.scanAxes:draft.scanAxes;
          draft=candidate;
        }
      }
    } catch {}
    ensureCombined(draft);
    if(!singleKinds.has(draft.kind))draft=ensureCombined(makeDraft(data));
    queueReview();
  }
  function save() { saveScope(scope,draft,screen); }
  function stock(v){const supplied=v.name&&v.name!==v.symbol&&v.name!==v.symbol?.slice(2)?v.name:null;const row={...v,name:supplied||displayNames[v.symbol]||hooks?.stockName?.(v.symbol)};return window.ResearchDisplay?.stock(row)||`<span>${E(row.name||'名称未提供')}<small>${E(v.symbol)}</small></span>`;}
  const operationText=value=>window.ResearchDisplay?.text(value)||readable(value);
  function screenSignature(){try{const request=screenPayload();delete request.config.poolSettings.combined;return JSON.stringify({config:request.config,selection:request.selection});}catch{return null;}}
  function combinationControls(){
    const value=draft.config.poolSettings.combined;
    return `<div class="pf-pool-composer" data-pf-pool-composer><h3>自选叠加</h3><p class="rs-help">基础资格始终生效。在下方勾选要叠加的条件；不勾选时显示全部基础可交易股票。</p><div class="pf-options">${['trend','structure','volume'].map(key=>`<label class="pf-check"><input type="checkbox" data-pf-pool-layer="${key}" ${value.pools.includes(key)?'checked':''}>${poolLabels[key]}</label>`).join('')}</div><div class="rs-actions"><label class="rs-field"><span>叠加方式</span><select data-pf-pool-mode><option value="intersection" ${value.mode==='intersection'?'selected':''}>同时满足（交集）</option><option value="union" ${value.mode==='union'?'selected':''}>满足任一（并集）</option></select></label><button type="button" class="rs-button" data-pf-action="screen-combined" ${screen.result?'':'disabled'}>查看叠加结果</button></div><p class="rs-help">${value.pools.length?value.pools.map(key=>poolLabels[key]).join(value.mode==='union'?' 或 ':' + '):'全部基础可交易池'} · ${value.mode==='union'?'任一勾选条件通过即可':'所有勾选条件均需通过'}。可在回测策略的“使用股票池”中选择“叠加筛选池”。</p></div>`;
  }
  function funnelView(r){
    const stages=r.poolSelection?.funnelStages||[],picked=draft.config.poolSettings.combined;
    return `<div class="pf-funnel" aria-label="四层累计筛选漏斗">${['base','trend','structure','volume'].map((key,i)=>{
      const stage=stages.find(s=>s.pool===key),active=screen.pool==='combined'&&picked.mode==='intersection'&&JSON.stringify(picked.pools)===JSON.stringify(['trend','structure','volume'].slice(0,i));
      return `<button type="button" class="pf-funnel-stage ${active?'is-active':''}" data-pf-action="screen-funnel" data-pf-stage="${key}" aria-pressed="${active}"><span>${i+1} · ${poolLabels[key]}</span><strong>${stage?.after==null?'—':n(stage.after,0)} <small>只</small></strong><small>${stage?.removed==null?'原报告缺少完整分层数据':`本层剔除 ${n(stage.removed,0)} 只`}</small></button>`;
    }).join('')}</div><p class="rs-help">四层按基础 → 趋势 → 回调结构 → 量能逐层取交集。点击任意一层，查看截至该层保留的股票；数量按整个池统计，不受下方搜索和分页影响。</p>`;
  }
  function evidenceValue(value,condition){
    if(value===null||value===undefined)return '数据缺失';
    if(Array.isArray(value))return value.map(item=>evidenceValue(item,condition)).join(' — ');
    if(typeof value==='boolean')return value?'是':'否';
    if(numeric(value)){
      if(condition.unit==='ratio')return n(Number(value)*100,4)+'%';
      if(condition.key==='amount20'&&condition.unit==='元')return n(Number(value)/10000,4)+' 万元';
      return n(value,4)+(condition.unit&&condition.unit!=='date'?' '+condition.unit:'');
    }
    return String(value);
  }
  function conditionText(condition){
    if(condition.enabled===false)return condition.label;
    const operators={'>':'>','>=':'≥','<':'<','<=':'≤','==':'为',between:'位于'};
    const actual=evidenceValue(condition.actual,condition),threshold=evidenceValue(condition.threshold,condition);
    return `${condition.label}：${actual}；要求${operators[condition.operator]||condition.operator||'为'} ${threshold}`;
  }
  function poolEvidence(row,r){
    const evidence=row.poolEvidence;
    if(!evidence?.base)return `<span data-pf-evidence-summary>${row.eligible?'已保存的入池归属见左列。':E(operationText(row.reason))}</span><small>旧筛选结果未保存逐项条件数据，请重新筛选查看入池依据。</small>`;
    const selected=screen.pool==='combined'?(r.poolSelection?.pools||draft.config.poolSettings.combined.pools):['base','excluded'].includes(screen.pool)?[]:[screen.pool];
    const layers=['base',...new Set(selected.filter(key=>key!=='base'))],statusText={pass:'通过',fail:'未通过',unknown:'数据不足'};
    const summarize=key=>{
      const entry=evidence[key];if(!entry)return poolLabels[key]+'：旧结果缺少逐项数据';
      const relevant=(entry.conditions||[]).filter(condition=>condition.enabled!==false&&condition.required!==false);
      const problem=relevant.find(condition=>condition.passed===false)||relevant.find(condition=>condition.passed==null);
      const preferred=key==='trend'&&relevant.some(condition=>condition.key==='returnRank')?'returnRank':{base:'amount20',trend:'windowReturn',structure:'drawdown',volume:'volumeRatio'}[key];
      const condition=problem||relevant.find(condition=>condition.key===preferred)||relevant[0];
      return `${poolLabels[key]}${statusText[entry.status]||'待核对'}${condition?' · '+conditionText(condition):''}`;
    };
    const passedLayers=layers.filter(key=>key!=='base'&&evidence[key]?.inPool);
    const title=!row.eligible?'未通过基础资格':screen.pool==='combined'&&selected.length?(r.poolSelection?.mode==='union'?'满足任一条件：':'同时满足：')+(passedLayers.map(key=>poolLabels[key]).join('、')||'基础可交易池'):'符合'+poolLabels[screen.pool==='combined'?'base':screen.pool];
    const summaryLayers=!row.eligible?['base']:layers.length>1?layers.filter(key=>key!=='base'):['base'];
    const detail=(row.eligible?['base','trend','structure','volume']:['base']).map(key=>{
      const entry=evidence[key];if(!entry)return `<section><strong>${E(poolLabels[key])}</strong><p>旧结果未保存这一层的逐项条件。</p></section>`;
      const rankInfo=key==='trend'&&entry.conditions?.some(condition=>condition.key==='returnRank'&&condition.enabled)?`<p>按 ${n(entry.rankWindow,0)} 日涨幅排名 · 有效样本 ${n(entry.rankUniverseCount,0)} 只 · 历史不足 ${n(entry.rankUnavailableCount,0)} 只；同涨幅按代码升序。</p>`:'';
      return `<section><strong>${E(poolLabels[key])} · ${E(statusText[entry.status]||'待核对')}</strong>${rankInfo}<ul>${(entry.conditions||[]).map(condition=>`<li><span>${E(conditionText(condition))}</span><small>${condition.enabled===false?'未启用此条件':condition.required===false&&condition.passed==null?'资料未提供，当前规则不因此剔除':E(statusText[condition.status]||'待核对')}${condition.note&&!(condition.required===false&&condition.passed==null)?' · '+E(condition.note):''}</small></li>`).join('')}</ul></section>`;
    }).join('');
    return `<div data-pf-evidence-summary><strong>${E(title)}</strong>${summaryLayers.map(key=>`<small>${E(summarize(key))}</small>`).join('')}</div><details class="pf-pool-evidence" data-pf-evidence-details ${detailAttrs('pool-evidence:'+r.asOf+':'+row.symbol+':'+screen.pool)}><summary>查看逐项对照</summary><div class="pf-pool-evidence-body">${detail}</div></details>`;
  }
  function screenView(r=screen.result,standalone=false){
    if(screen.queryError||r?.screenPools&&screen.appliedRequest&&screen.appliedRequest!==JSON.stringify(screenRequest()))return panel('股票池筛选结果',`<div class="rs-body">${note(screen.queryError||'正在按当前叠加条件与搜索范围更新股票列表…')}<button type="button" class="rs-button" data-pf-action="screen-retry">重新读取结果</button></div>`);
    if(!r?.screenPools)return screen.task?panel('股票池筛选预览',`<div class="rs-body">${note(screen.meta?.error||screen.meta?.phase||'筛选任务已提交，后台正在读取完整股票池。')}<button type="button" class="rs-button" data-pf-action="screen-task">查看筛选任务与运行控制</button></div>`):'';
    displayNames=r.securityNames||displayNames;
    const pools=r.screenPools||{},rows=pools[screen.pool]||[],page=r.poolPagination?.[screen.pool]||{offset:0,total:rows.length,pageSize:100};
    const names={combined:'叠加筛选池',base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池',excluded:'基础资格排除'};
    const stale=!standalone&&screen.signature!==screenSignature();
    return panel('股票池筛选结果',`<div class="rs-body">${stale?note('当前设置与这份已保存筛选结果不同。请点击“按当前设置筛选股票池”重新计算。'):''}<p>截至 ${E(r.asOf)} 收盘 · 来源证券 ${n(r.coverage?.totalSecurities,0)} 只。按当日成交额降序、代码升序排列；入池只看下方条件。每个池均完成全量筛选后分页显示。</p>${funnelView(r)}${r.poolSelection?.unknownCount?note('有 '+n(r.poolSelection.unknownCount,0)+' 只股票缺少分层记录，未将未知视为通过。'):''}<div class="rs-actions pf-single-pools">${Object.entries(names).map(([key,label])=>`<button type="button" class="rs-button ${screen.pool===key?'rs-primary':''}" data-pf-action="screen-pool" data-pf-pool="${key}">${label} ${n(key==='combined'?r.poolSelection?.matchedCount:r.poolCounts?.[key],0)}</button>`).join('')}</div><p class="rs-help">上方“趋势池／回调结构池／量能活跃池”按钮用于单独查看条件；“叠加筛选池”使用当前勾选条件。</p><h3 data-pf-pool-result-title>${E(names[screen.pool])}</h3><label class="rs-field pf-pool-search"><span>在整个筛选池中搜索名称或代码</span><input type="search" data-pf-screen-query value="${E(screen.query)}" placeholder="股票名称或代码"></label><div class="rs-actions"><button type="button" class="rs-button" data-pf-action="screen-search">搜索股票池</button><button type="button" class="rs-button rs-quiet" data-pf-action="screen-clear">清除搜索</button></div></div>${table(['股票（点击看日线）',screen.pool==='excluded'?'未通过的基础条件':'入池依据（实际值 / 条件）','所属股票池','收盘价（元）','当日成交额（万元）','20 日均成交额（万元）'],rows.map(row=>`<tr><td>${stock({...row,date:r.asOf})}</td><td class="pf-evidence-cell" data-pf-pool-evidence>${poolEvidence(row,r)}</td><td class="pf-membership-cell">${row.eligible?(row.memberships||[]).map(key=>`<span class="pf-membership">${E(poolLabels[key]||key)}</span>`).join('')||'基础可交易池':'未入池'}</td><td>${n(row.close)}</td><td>${numeric(row.amount)?n(row.amount/10000):'—'}</td><td>${numeric(row.amount20)?n(row.amount20/10000):'—'}</td></tr>`))}<div class="rs-body rs-actions"><span data-pf-pool-pagination>匹配 ${n(page.total,0)} 只 · 当前 ${page.total? page.offset+1:0} — ${Math.min(page.offset+rows.length,page.total)} 只</span><button type="button" class="rs-button" data-pf-action="screen-page" data-pf-offset="${Math.max(0,page.offset-page.pageSize)}" ${page.offset?'':'disabled'}>上一页股票</button><button type="button" class="rs-button" data-pf-action="screen-page" data-pf-offset="${page.offset+rows.length}" ${page.hasMore?'':'disabled'}>下一页股票</button>${!standalone?'<button type="button" class="rs-button" data-pf-action="screen-task">查看筛选任务</button>':''}</div><p class="rs-help rs-body">筛选使用截止日收盘数据。20 日均额包含截止日，不足 20 条日线或旧报告未保存该值时显示“—”。回测时按每个交易日重新筛选，不把本日名单用于历史日期。</p>`);
  }
  function screenRequest(){const combined=draft?.config.poolSettings.combined||combinedDefaults();return {pool:screen.pool,pools:combined.pools.length?[...combined.pools]:['base'],poolMode:combined.mode,poolOffset:screen.offset,poolQuery:screen.query};}
  async function loadScreen(){
    if(!screen.task||!hooks.screenGet)return;
    const target=screen,serial=++target.serial,request=screenRequest();target.queryError='';
    try{const data=await hooks.screenGet({id:target.task,...request});if(serial!==target.serial)return;const firstResult=!target.result?.screenPools&&data.result?.screenPools;target.meta=data.task;target.result=data.result;target.appliedRequest=JSON.stringify(request);if(firstResult&&target===screen&&scope==='market')expandedDetails.delete('editor:stock-pool-settings');if(target===screen)hooks.refresh();}
    catch(error){if(serial!==target.serial)return;target.queryError='未能更新股票池：'+error.message;if(target===screen)hooks.refresh();throw error;}
  }
  function editTask(t,mode='copy'){
    activateScope(scopeFor(t.kind));
    if(!Array.isArray(t.config?.strategies)||!t.selection)throw new Error('旧版任务支持重命名和查看；请使用新的股票池工作区配置研究。');
    if(t.config.strategies.some(s=>s.id!==s.kind)||t.config.combinations.some(c=>c.id!=='combo_'+c.method))throw new Error('此任务包含自定义策略实例，可重命名或查看；当前表单不能无损编辑这些实例。');
    draft=makeDraft(definition);draft.config=structuredClone(t.config);ensureCombined(draft);draft.kind=t.kind;
    draft.name=mode==='copy'?(t.name+' · 副本').slice(0,100):t.name;draft.selection=structuredClone(t.selection);draft.symbols=(t.selection.symbols||[]).join(' ');
    draft.periodMode=t.config.startDate&&t.config.endDate?'dates':'recent';draft.study={...defaultsStudy(),...t.config.study};draft.experiment={...(singleKinds.has(t.kind)?defaultsSingleExperiment():defaultsExperiment()),...t.experiment};
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
    if(parts[0]==='combinations')return `组合 · ${({intersection:'交集',union:'并集',weighted:'加权'})[parts[1].replace('combo_','')]||parts[1]} · ${last==='method'?'合成方式':last==='threshold'?'最低标准分':(definition.strategies.find(s=>s.id===parts[3])?.name||parts[3])+' 权重'}`;
    return ({'rotation.lookbackDays':'轮动 · 表现窗口','rotation.cooldownDays':'轮动 · 冷却期','rotation.minWeightChange':'轮动 · 最小预算转移（%）','rotation.temperature':'轮动 · 平滑程度','rotation.regimeBlend':'轮动 · 同环境表现占比（%）','rotation.regimeMinObservations':'轮动 · 同环境最少观察日','rotation.warmupDays':'轮动 · 预热观察日',topN:'持仓 · 每策略 Top N',maxPositions:'持仓 · 最多持股数',maxStockWeight:'持仓 · 单股上限（%）',switchThreshold:'持仓 · 排名换仓门槛（百分点）',rebalanceEvery:'持仓 · 调整间隔',stopLossPct:'持仓 · 止损（%）',takeProfitPct:'持仓 · 止盈（%）',trailingStopPct:'持仓 · 峰值回撤（%）',maxHoldingDays:'持仓 · 最长持仓'})[path]||path;
  }
  function axisOptions() {
    const choices={'':'不扫描此项'};
    for(const s of definition.strategies.filter(s=>draft.enabled[s.id]))for(const v of s.parameters)choices[`strategies.${s.id}.params.${v.key}`]=`策略 · ${s.name} · ${v.label}${isPercent(v.key)?'（%）':''}`;
    for(const [method,c] of Object.entries(draft.combinations).filter(([,c])=>c.enabled&&!isSingle())) {
      choices[`combinations.combo_${method}.method`]=`组合 · ${({intersection:"交集",union:"并集",weighted:"加权"})[method]} · 合成方式`;
      for(const s of definition.strategies.filter(s=>draft.enabled[s.id]))choices[`combinations.combo_${method}.children.${s.id}.weight`]=`组合 · ${({intersection:"交集",union:"并集",weighted:"加权"})[method]} · ${s.name} 权重`;
    }
    if(!isSingle())for(const [path,label] of Object.entries({lookbackDays:'表现窗口',cooldownDays:'冷却期',minWeightChange:'最小预算转移（%）',temperature:'平滑程度',regimeBlend:'同环境表现占比（%）'}))choices['rotation.'+path]='轮动 · '+label;
    for(const [path,label] of Object.entries({topN:'每策略 Top N',maxPositions:'最多持股数',maxStockWeight:'单股上限（%）',switchThreshold:'排名换仓门槛（百分点）',rebalanceEvery:'调整间隔（交易日）',stopLossPct:'止损（%）',takeProfitPct:'止盈（%）',trailingStopPct:'持仓峰值回撤（%）',maxHoldingDays:'最长持仓（交易日）'}))choices[path]='持仓 · '+label;
    return choices;
  }
  const layerLabels={strategy:'策略信号',combination:'组合方法',rotation:'策略轮动',holdings:'持仓控制'};
  const axisSpecs={topN:[1,100,true,'只'],maxPositions:[1,200,true,'只'],maxStockWeight:[.001,1,false,'%'],switchThreshold:[0,1,false,'百分点'],rebalanceEvery:[1,252,true,'交易日'],stopLossPct:[0,.99,false,'%'],takeProfitPct:[0,10,false,'%'],trailingStopPct:[0,.99,false,'%'],maxHoldingDays:[0,10000,true,'交易日'],
    'rotation.lookbackDays':[2,252,true,'交易日'],'rotation.cooldownDays':[0,252,true,'交易日'],'rotation.minWeightChange':[0,1,false,'%'],'rotation.temperature':[.0001,10,false,''],'rotation.regimeBlend':[0,1,false,'%']};
  function axisInfo(path,value=draft){
    const parts=path.split('.'),last=parts.at(-1);let current,spec=axisSpecs[path],description='每组只替换此项，其他条件沿用固定设置。';
    if(parts[0]==='strategies'){
      const p=definition.strategies.find(s=>s.id===parts[1])?.parameters.find(v=>v.key===last);
      current=value.params[parts[1]]?.[last];if(p)spec=[p.min,p.max,p.type==='integer',isPercent(path)?'%':p.type==='integer'?'交易日':''];
      description='改变这一个策略的信号条件；同策略的其他参数仍固定。';
    }else if(parts[0]==='combinations'){
      const method=parts[1]?.replace('combo_','');current=last==='method'?method:value.weights[parts[3]];
      if(last!=='method')spec=[.000001,1000000,false,''];
      description=last==='method'?'交集：各子策略都选中；并集：任一子策略选中。当前并集与加权在相同子权重和最低得分下等价，示例只比较交集与并集。':'只改变该组合内这个子策略的权重。';
    }else current=parts[0]==='rotation'?value.config.rotation[last]:value.config[path];
    const [min,max,integer,unit]=spec||[];
    return {current,min,max,integer,unit:unit||'',description,method:last==='method'};
  }
  function exampleValues(path,value=draft){
    const info=axisInfo(path,value);if(info.method)return '交集, 并集';
    if(!numeric(info.current)||!numeric(info.min)||!numeric(info.max))return '';
    const current=Number(info.current),step=info.integer?Math.max(1,Math.round(Math.abs(current)*.25)):Math.max((info.max-info.min)/20,Math.abs(current)*.25);
    const other=current+step<=info.max?current+step:Math.max(info.min,current-step);
    return [...new Set([current,other].map(v=>display(path,v)))].join(', ');
  }
  function seedPlateauAxes(value){
    const strategies=definition.strategies.filter(s=>value.enabled[s.id]);
    if(value.kind==='singlePlateau'){
      if(strategies.length!==1)return false;
      const s=strategies[0],parameter=s.parameters.find(p=>p.type==='integer')||s.parameters[0];
      if(!parameter)return false;
      const path=`strategies.${s.id}.params.${parameter.key}`,info=axisInfo(path,value),current=Number(info.current),step=info.integer?Math.max(1,Math.round(Math.abs(current)*.2)):Math.max((info.max-info.min)/40,Math.abs(current)*.2);
      const values=[...new Set([Math.max(info.min,current-step),current,Math.min(info.max,current+step)].map(v=>info.integer?Math.round(v):v))];
      value.scanAxes=[{path,text:values.map(v=>display(path,v)).join(', ')},{path:'',text:''},{path:'',text:''}];value.experiment.maxTrials=27;return true;
    }
    if(strategies.length<2)return false;
    const s=strategies.find(s=>s.parameters.some(p=>p.type==='integer'))||strategies[0],p=s.parameters.find(p=>p.type==='integer')||s.parameters[0];
    const method=Object.keys(value.combinations).find(k=>value.combinations[k].enabled)||'union';value.combinations[method].enabled=true;value.participates['combo_'+method]=true;
    value.config.allocation.mode='equal';
    const path=`strategies.${s.id}.params.${p.key}`;
    value.scanAxes=[{path,text:exampleValues(path,value)},{path:`combinations.combo_${method}.method`,text:'交集, 并集'},
      {path:'rotation.lookbackDays',text:'10, 20'},{path:'topN',text:'3, 5'},{path:'',text:''},{path:'',text:''}];
    return true;
  }
  function scannedEditorAxes(path){
    let candidates=[path.startsWith('config.')?path.slice(7):path];
    if(path.startsWith('params.')){const [,id,key]=path.split('.');candidates=[`strategies.${id}.params.${key}`];}
    if(path.startsWith('weights.'))candidates=Object.keys(draft.combinations).map(k=>`combinations.combo_${k}.children.${path.split('.')[1]}.weight`);
    return (draft.scanAxes||[]).filter(row=>candidates.includes(row.path));
  }
  function scanState(){
    const options=axisOptions(),errors={},selected=draft.scanAxes.map((a,index)=>({...a,index})).filter(a=>a.path),seen=new Set();let axes=[],message='';
    for(const row of selected){
      const label=options[row.path]||parameterLabel(row.path),info=axisInfo(row.path);
      try{
        if(!options[row.path])throw new Error('对应策略或组合已停用，请重新选择扫描参数。');
        if(seen.has(row.path))throw new Error('与另一扫描项重复，请合并候选值。');seen.add(row.path);
        const axis=scanAxes([row],'walkForward',81)[0];
        if(!info.method&&axis.values.some(v=>v<info.min||v>info.max||info.integer&&!Number.isInteger(v)))throw new Error(`请输入 ${display(row.path,info.min)} 至 ${display(row.path,info.max)} ${info.unit}${info.integer?'范围内的整数':'范围内的数字'}。`);
        if(['stopLossPct','takeProfitPct','trailingStopPct','maxHoldingDays'].includes(row.path)&&draft.config.rebalanceMode==='rank')throw new Error('当前只按排名换仓，这项风险退出参数不生效；请到固定条件改用风险或混合调仓，或换一个扫描项。');
        axes.push(axis);
      }catch(error){errors[row.index]=`${label}：${error.message.replace(/^扫描项 \d+（[^）]+）：/,'')}`;}
    }
    if(!Object.keys(errors).length){try{axes=scanAxes(draft.scanAxes,draft.kind,draft.experiment.maxTrials);}catch(error){message=error.message;}}
    const count=Object.keys(errors).length?null:axes.reduce((total,a)=>total*a.values.length,1);
    return {axes,errors,message,count,selected,valid:!Object.keys(errors).length&&!message};
  }
  const axisValueText=(path,value)=>value==null?'未记录':typeof value==='number'?n(display(path,value),8)+(axisInfo(path).unit?' '+axisInfo(path).unit:''):({intersection:'交集',union:'并集',weighted:'加权'})[value]||String(value);
  function scanOverview(){
    const state=scanState(),layers=new Set(state.selected.map(a=>axisLayer(a.path)));
    let body=isSingle()?'<p class="rs-help">只选 1–3 个关键参数；股票池漏斗、未选策略参数、交易成本都固定。组合和轮动留到第 6 阶段。</p>':`<div class="pf-scan-layers">${Object.entries(layerLabels).map(([key,name])=>`<span class="${layers.has(key)?'is-ready':''}">${E(name)} · ${layers.has(key)?'已选':'未选'}</span>`).join('')}</div>`;
    if(state.message)body+=`<p class="pf-config-error" role="alert">${E(state.message)}</p>`;
    if(state.count!==null)body+=`<p class="pf-scan-count"><strong>${state.count} 组</strong> = ${state.axes.map(a=>a.values.length).join(' × ')||'尚未选择扫描项'} 个候选值组合；本次上限 ${n(draft.experiment.maxTrials,0)} 组。</p>`;
    else body+='<p class="pf-config-error">请先修正标红的扫描项，再计算组合数量。</p>';
    if(state.valid&&state.axes.length){
      let combos=[[]];for(const a of state.axes)combos=combos.flatMap(row=>a.values.map(v=>[...row,v]));
      body+=`<details><summary>查看实际测试组合示例（前 ${Math.min(4,combos.length)} / ${combos.length} 组）</summary>${table(state.axes.map(a=>parameterLabel(a.path)),combos.slice(0,4).map(row=>'<tr>'+row.map((v,i)=>'<td>'+E(axisValueText(state.axes[i].path,v))+'</td>').join('')+'</tr>'))}<p class="rs-help">每组分别替换这几项，再运行完整回测；不是把几个候选值求平均。</p></details>`;
    }
    return body;
  }
  function scanEditor(){
    const options=axisOptions(),state=scanState(),system=draft.kind==='systemPlateau';
    if(draft.kind==='singlePlateau')return '<div class="pf-scan-explanation"><h3>只改变少量关键参数</h3><p>这次所有候选都使用同一开发区间。比较相邻参数的收益、回撤与交易样本，寻找一片稳定区域。此处不使用未来验证区间，也不自动把最高收益点设为最终方案。</p><p><b>扫描项：</b>每组使用下方候选值替换参考值。<b>其余条件：</b>沿用第 3 步的固定配置。最多 3 项、每项 2–5 个值、合计不超过 27 组。</p><button type="button" class="rs-button" data-pf-action="plateau-example">填入一个参数的邻近示例</button><p class="rs-help">示例仅说明填写方式，不能视为最优参数。</p></div><div class="pf-scan-axes">'+draft.scanAxes.slice(0,3).map((axis,i)=>{const info=axisInfo(axis.path),example=axis.path?exampleValues(axis.path):'';return '<article class="pf-scan-axis" data-pf-axis-row="'+i+'"><div class="rs-fields">'+select('关键参数 '+(i+1),'scanAxes.'+i+'.path',axis.path,options)+(axis.path?'<label class="rs-field"><span>候选值'+(info.unit?'（'+E(info.unit)+'）':'')+' · 2–5 个</span><input data-pf-field="scanAxes.'+i+'.text" data-pf-type="text" value="'+E(axis.text)+'" placeholder="'+E(example)+'" aria-describedby="pf-scan-error-'+i+'"></label>':'<p class="rs-help">留空即不增加扫描维度。</p>')+'</div>'+(axis.path?'<p class="rs-help">基础参考值：<b>'+E(axisValueText(axis.path,info.current))+'</b> · '+E(info.description)+'</p>':'')+'<p id="pf-scan-error-'+i+'" data-pf-axis-error="'+i+'" class="pf-config-error" role="alert">'+E(state.errors[i]||'')+'</p></article>';}).join('')+'</div><div class="pf-scan-overview" data-pf-scan-overview>'+scanOverview()+'</div>';
    return `<div class="pf-scan-explanation"><h3>平原是在测试哪些参数？</h3><p>普通回测只运行一套参数。收益平原把下方候选值交叉组合，检验相邻参数组合的表现是否稳定，再用后续数据验证。</p><p><b>列入扫描的参数：</b>每组用候选值替换基础值；基础值不限制候选范围，也不会自动加入候选。数值参考字段按首个候选值送交校验，避免空白旧值阻碍有效扫描。<br><b>没有列入扫描的参数：</b>全部保持第 3 步的固定设置。</p><p class="rs-help">${system?'本次是四层全系统平原，策略、组合、轮动、持仓各至少选一项。':'本次是滚动样本外验证，可以不扫描或只选择部分参数；固定当前设置也能运行，但不会给出全系统平原认证。'}示例只演示填写方法，不是已验证的收益参数。</p><button class="rs-button" type="button" data-pf-action="plateau-example">填入 16 组四层示例</button></div><div class="pf-scan-axes">${draft.scanAxes.map((axis,i)=>{
      const info=axisInfo(axis.path),error=state.errors[i],example=axis.path?exampleValues(axis.path):'';
      const values=String(axis.text||'').split(/[\s,，;；、]+/),methodChoices={intersection:'交集',union:'并集',weighted:'加权'};
      return `<article class="pf-scan-axis" data-pf-axis-row="${i}"><div class="rs-fields">${select('扫描项 '+(i+1),'scanAxes.'+i+'.path',axis.path,{...options,...(axis.path&&!options[axis.path]?{[axis.path]:parameterLabel(axis.path)+'（已停用）'}:{})})}${axis.path?(info.method?`<fieldset class="pf-method-choices"><legend>勾选 2–3 种候选方法</legend>${Object.entries(methodChoices).map(([key,label])=>`<label class="pf-check"><input type="checkbox" data-pf-scan-method="${i}" value="${key}" ${values.includes(key)||values.includes(label)?'checked':''}>${label}</label>`).join('')}</fieldset>`:`<label class="rs-field"><span>候选值${info.unit?'（'+E(info.unit)+'）':''} · 填 2–5 个，用逗号或空格分隔</span><input type="text" data-pf-field="scanAxes.${i}.text" data-pf-type="text" value="${E(axis.text)}" placeholder="例如：${E(example)}" aria-describedby="pf-scan-error-${i}"></label>`):'<p class="rs-help">可选扫描项；留空时不增加测试维度。</p>'}</div>${axis.path?`<p class="rs-help">基础参考值：<b>${E(axisValueText(axis.path,info.current))}</b>（将被候选值替换）${numeric(info.min)?` · 合法范围 ${display(axis.path,info.min)}–${display(axis.path,info.max)} ${E(info.unit)}${info.integer?'，整数':''}`:''}</p><p class="rs-help">${E(info.description)}</p>${!info.method?`<button class="rs-button rs-quiet" type="button" data-pf-action="axis-example" data-pf-axis="${i}">使用本项示例：${E(example)}</button>`:''}`:''}<p id="pf-scan-error-${i}" data-pf-axis-error="${i}" class="pf-config-error" role="alert">${E(error||'')}</p></article>`;
    }).join('')}</div><div class="pf-scan-overview" data-pf-scan-overview>${scanOverview()}</div>`;
  }
  function renderScanFeedback(){
    if(!experimentKinds.has(draft?.kind))return;const state=scanState();
    draft.scanAxes.forEach((_,i)=>{const el=document.querySelector?.('[data-pf-axis-error="'+i+'"]');if(el)el.textContent=state.errors[i]||'';});
    const overview=document.querySelector?.('[data-pf-scan-overview]');if(overview)overview.innerHTML=scanOverview();
  }
  function experimentForm(includeScan=true) {
    const x=draft.experiment;
    if(draft.kind==='singlePlateau')return (includeScan?scanEditor():'')+'<fieldset class="rs-fieldset"><legend>开发期稳定性门槛（所有候选相同）</legend><p class="rs-help">门槛用于本次开发比较。达到门槛仍需手动选定并冻结，再到全新的日期区间验证。</p><div class="rs-fields">'+field('邻域正收益比例下限（%）','experiment.minNeighborPositiveFraction',x.minNeighborPositiveFraction,'min="50" max="100"')+field('最少邻域参数点','experiment.minNeighborhoodSize',x.minNeighborhoodSize,'min="3" max="27"')+field('邻域收益标准差上限（百分点）','experiment.maxNeighborhoodStdPct',x.maxNeighborhoodStdPct,'min="0"')+field('最少已关闭持仓波段','experiment.minClosedTrades',x.minClosedTrades,'min="1" step="1"')+field('最大回撤上限（%）','experiment.maxDrawdownPct',x.maxDrawdownPct,'min="0" max="100"')+'</div></fieldset>';
    return `<fieldset class="rs-fieldset"><legend>滚动训练与样本外验证</legend><div class="rs-fields">${select('资金衔接方式','experiment.capitalMode',x.capitalMode||'independent',{independent:'逐折独立资金（稳健性诊断）',continuous:'附加连续资金账户（持仓不重置）'})}${field('每折训练日数','experiment.trainDays',x.trainDays,'min="14" max="2500"')}${field('每折样本外日数','experiment.testDays',x.testDays,'min="2"')}${field('滚动步长（交易日）','experiment.stepDays',x.stepDays,'min="2"')}${field('最多滚动折数','experiment.maxFolds',x.maxFolds,'min="1" max="8"')}${field('参数组合上限','experiment.maxTrials',x.maxTrials,'min="1" max="81"')}</div>${check('逐折扩大训练区间','experiment.expanding',x.expanding)}<p class="rs-help">每折先用训练段比较候选组合，再冻结选择交给后续样本外验证；这些日数是固定验证安排，不是扫描参数。第一折至少需要 ${Number(x.trainDays)+Number(x.testDays)} 个实际交易日，最多 ${x.maxFolds} 折需 ${Number(x.trainDays)+Number(x.testDays)+(Number(x.maxFolds)-1)*Number(x.stepDays)} 日；最终按行情日历核验。</p></fieldset>${includeScan?scanEditor():''}<fieldset class="rs-fieldset"><legend>如何判断形成收益平原</legend><details><summary>平原稳定性与样本外门槛</summary><div class="rs-fields">${field('邻域正收益比例下限（%）','experiment.minNeighborPositiveFraction',x.minNeighborPositiveFraction,'min="0" max="100"')}${field('最少邻域参数点','experiment.minNeighborhoodSize',x.minNeighborhoodSize,'min="2"')}${field('邻域收益标准差上限（百分点）','experiment.maxNeighborhoodStdPct',x.maxNeighborhoodStdPct,'min="0"')}${field('至少样本外年份数','experiment.minOosYears',x.minOosYears,'min="2"')}${field('至少样本外市场环境数','experiment.minOosRegimes',x.minOosRegimes,'min="2"')}</div></details></fieldset>`;
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
    return panel('公司行动与待到账权益',`<details class="rs-body" data-pf-rights ${detailAttrs("rights:"+(r.pagination?.path||""))}><summary>查看 ${n(r.pagination?.actionLedger?.total??r.actionLedger?.length,0)} 条权益记录与待到账金额</summary>${table(['日期','证券','登记／除权／到账','登记股数','现金或认购金额','新增股份'],(r.actionLedger||[]).map(a=>`<tr><td>${E(a.date)}</td><td>${stock(a)}</td><td>${E(labels[a.stage]||a.stage)}</td><td>${n(a.entitledQuantity,0)}</td><td>${n(a.cashReceivable??a.subscriptionPayable??a.amount)}</td><td>${n((a.bonusShares||0)+(a.rightsShares||0),0)}</td></tr>`))}<pre>${E(JSON.stringify({receivables:r.cashReceivables,payables:r.actionPayables,reservedCash:r.reservedActionCash,preExRights:r.preExRights},null,2))}</pre></details>`);
  }
  const workflow={review:null,signature:null,pending:false,error:'',serial:0,timer:null,promise:null,submitting:false};
  const stepLabels=['基础池与日期','策略与漏斗','固定交易规则','核对创建','查看结果'];
  const plateauStepLabels=['研究对象','扫描参数','固定条件','核对创建','查看结果'];
  const kindLabels={singleBacktest:'单策略固定参数回测',singlePlateau:'单策略关键参数平原',frozenValidation:'冻结方案新时段验证',poolPreview:'股票池筛选预览',portfolio:'高级组合回测',comparison:'多策略比较与持续性检验',walkForward:'滚动样本外验证',systemPlateau:'高级全系统参数扫描与收益平原'};
  const poolLabels={base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池',combined:'叠加筛选池'};
  const presetKey='atrade.strategy-presets.v1';
  const chosenPresets={};
  function currentStep(){return Math.max(1,Math.min(5,Number(draft?.workflowStep)||1));}
  function setStep(step,kind){if(kind)activateScope(scopeFor(kind));if(!draft)return;draft.workflowStep=Math.max(1,Math.min(5,Number(step)||1));save();}
  function fold(title,id,body){return '<details class="pf-advanced" '+detailAttrs('editor:'+id)+'><summary>'+E(title)+'</summary><div class="pf-advanced-body">'+body+'</div></details>';}
  function stepButton(step,label,primary=false){return '<button type="button" class="rs-button '+(primary?'rs-primary':'')+'" data-pf-action="workflow-step" data-pf-step="'+step+'">'+E(label)+'</button>';}
  function workflowSection(step,title,subtitle,body){
    return '<section class="pf-workflow-section" data-pf-step-panel="'+step+'" '+(currentStep()===step?'':'hidden')+'><header class="pf-step-heading"><span>'+step+'</span><div><h3 tabindex="-1">'+E(title)+'</h3><p>'+E(subtitle)+'</p></div></header>'+body+
      '<div class="rs-actions pf-step-footer">'+(step>1?stepButton(step-1,'上一步'):'')+(step<4?stepButton(step+1,'下一步：'+(scope==='plateau'?plateauStepLabels:stepLabels)[step],true):'')+'</div></section>';
  }
  function presetsFor(id){
    try{const rows=JSON.parse(localStorage.getItem(presetKey)||'{}')[id];return Array.isArray(rows)?rows.filter(p=>p&&typeof p.id==='string'&&typeof p.name==='string'&&p.params&&typeof p.params==='object'&&!Array.isArray(p.params)).slice(0,30):[];}catch{return [];}
  }
  function writePresets(id,rows){let saved={};try{saved=JSON.parse(localStorage.getItem(presetKey)||'{}');if(!saved||typeof saved!=='object'||Array.isArray(saved))saved={};}catch{}saved[id]=rows;localStorage.setItem(presetKey,JSON.stringify(saved));}
  function strategyPresets(s){
    const presets=presetsFor(s.id);
    return '<div class="pf-preset-toolbar"><label class="rs-field"><span>已保存参数预设</span><select data-pf-preset-select="'+E(s.id)+'"><option value="">当前参数 / 默认参数</option>'+presets.map(v=>'<option value="'+E(v.id)+'" '+(chosenPresets[s.id]===v.id?'selected':'')+'>'+E(v.name)+'</option>').join('')+'</select></label>'+
      '<label class="rs-field"><span>预设名称（可留空）</span><input data-pf-preset-name="'+E(s.id)+'" type="text" maxlength="60" placeholder="例如：较宽松的量能条件"></label><div class="rs-actions"><button type="button" class="rs-button" data-pf-action="save-strategy-preset" data-pf-strategy="'+E(s.id)+'">保存当前参数</button><button type="button" class="rs-button rs-quiet" data-pf-action="reset-strategy" data-pf-strategy="'+E(s.id)+'">恢复策略默认参数</button>'+
      (chosenPresets[s.id]?'<button type="button" class="rs-button rs-danger" data-pf-action="delete-strategy-preset" data-pf-strategy="'+E(s.id)+'">删除所选预设</button>':'')+'</div></div>';
  }
  function strategyCard(s){
    const enabled=draft.enabled[s.id],changed=s.parameters.some(v=>draft.params[s.id][v.key]!==v.default);
    return '<article class="pf-strategy"><header>'+check(s.name,'enabled.'+s.id,enabled)+(enabled?check('参与配权 / 比较','participates.'+s.id,draft.participates[s.id]!==false):'')+'</header><p class="rs-help">'+E(s.description)+'</p>'+
      (enabled?'<div class="rs-fields">'+select('使用股票池','pools.'+s.id,draft.pools[s.id],poolLabels)+'</div>'+fold('策略参数与预设 · '+(changed?'已修改':'使用默认参数'),'strategy:'+s.id,strategyPresets(s)+'<div class="rs-fields">'+s.parameters.map(v=>field(v.label+(isPercent(v.key)?'（%）':''),'params.'+s.id+'.'+v.key,draft.params[s.id][v.key],'min="'+display(v.key,v.min)+'" max="'+display(v.key,v.max)+'"')).join('')+'</div>'):'')+'</article>';
  }
  function pipeline(current){
    const stages=[['基础可交易池','M06'],['按策略风格配置漏斗','R03'],['单策略固定参数回测','R03'],['少量关键参数平原','R05'],['冻结后验证新时段','R10'],['组合与轮动','advanced']];
    return '<nav class="pf-research-pipeline" aria-label="研究阶段">'+stages.map(([label,route],i)=>'<button type="button" class="pf-pipeline-stage '+(current===i+1?'is-current':'')+'" '+(current===i+1?'aria-current="step" ':'')+(route==='advanced'?'data-pf-action="advanced"':route==='R05'?'data-pf-action="single-plateau"':route==='R03'?'data-pf-action="single-stage" data-pf-step="'+(i===1?2:1)+'"':'data-nav="'+route+'"')+'><span>'+String(i+1).padStart(2,'0')+'</span>'+E(label)+'</button>').join('')+'</nav>';
  }
  function selectSingleStrategy(id){
    if(!definition.strategies.some(s=>s.id===id))return;
    for(const s of definition.strategies){draft.enabled[s.id]=s.id===id;draft.participates[s.id]=s.id===id;}
    for(const c of Object.values(draft.combinations))c.enabled=false;
    draft.config.allocation.mode='single';draft.config.allocation.strategyIds=[id];
    if(draft.kind==='singlePlateau')seedPlateauAxes(draft);
  }
  function stylePreset(){
    return (definition.poolStyles||[]).find(style=>JSON.stringify(style.poolSettings)===JSON.stringify(draft.config.poolSettings));
  }
  function funnelControls(){
    const selected=definition.strategies.find(s=>draft.enabled[s.id]),preset=stylePreset(),styles=definition.poolStyles||[];
    return '<section class="pf-style-funnel"><h3>按这个策略的风格配置漏斗</h3><p class="rs-help">基础资格先过滤不可交易样本，漏斗只检查股票自身数据。买入和卖出由后面的策略信号决定。</p><div class="pf-style-presets">'+styles.map(style=>'<button type="button" class="pf-style-preset '+(preset?.id===style.id?'is-selected':'')+'" data-pf-action="apply-pool-style" data-pf-style="'+E(style.id)+'" aria-pressed="'+(preset?.id===style.id)+'"><strong>'+E(style.name)+'</strong><span>'+E(style.description)+'</span><small>'+((style.strategyKinds||[]).includes(selected?.id)?'与当前策略风格相符':'可按研究假设选用')+'</small></button>').join('')+'</div><p class="rs-help" data-pf-style-summary>当前：'+E(preset?.name||'自定义漏斗')+' · '+E((draft.config.poolSettings.combined.pools||[]).map(k=>poolLabels[k]).join(draft.config.poolSettings.combined.mode==='union'?' 或 ':' + ')||'仅基础可交易池')+'。</p>'+(preset?.notes?'<p class="rs-help">'+E(Array.isArray(preset.notes)?preset.notes.join('；'):preset.notes)+'</p>':'')+fold('查看或调整漏斗条件（修改后按自定义条件执行）','single-funnel',poolForm(false,true))+'</section>';
  }
  function singleStrategyForm(){
    const selected=definition.strategies.find(s=>draft.enabled[s.id])||definition.strategies[0];
    return '<div class="rs-fields">'+select('本次只研究一个策略','singleStrategy',selected.id,Object.fromEntries(definition.strategies.map(s=>[s.id,s.name])))+'</div><p class="rs-help">'+E(selected.description)+'</p>'+funnelControls()+'<section class="pf-single-parameters"><h3>固定策略参数</h3><p class="rs-help">先固定一套参数检查买卖逻辑。回测完成后，可直接带入关键参数平原，不必重新填写。</p>'+strategyPresets(selected)+'<div class="rs-fields">'+selected.parameters.map(v=>field(v.label+(isPercent(v.key)?'（%）':''),'params.'+selected.id+'.'+v.key,draft.params[selected.id][v.key],'min="'+display(v.key,v.min)+'" max="'+display(v.key,v.max)+'"')).join('')+'</div><p class="rs-help">当前策略使用 '+E(poolLabels[draft.pools[selected.id]]||draft.pools[selected.id])+'。选择上方风格预设后自动使用对应叠加筛选池。</p></section>';
  }
  function dataSourceForm(){
    const sources=definition.historySources||[],choices=Object.fromEntries(sources.map(s=>[s.id,(s.declaration?.universe||'历史数据包')+' · '+(s.coverage?.startDate||'')+' — '+(s.coverage?.endDate||'')+' · '+s.symbolCount+' 只']));
    const allA=draft.selection.mode==='all'&&draft.selection.markets.length===3&&draft.selection.boards.length===4;
    const markets=[['sh','沪市'],['sz','深市'],['bj','北交所']].map(([v,t])=>check(t,'market.'+v,draft.selection.markets.includes(v))).join('');
    const boards=[['main','主板'],['chinext','创业板'],['star','科创板'],['bse','北交所板块']].map(([v,t])=>check(t,'board.'+v,draft.selection.boards.includes(v))).join('');
    return '<div class="pf-pool-source"><strong>'+(draft.selection.source==='history'?'数据来源：已导入历史数据包':'数据来源：沿用系统设置中的本地日线')+'</strong><span data-pf-universe-summary>股票池范围：'+(allA?'全部 A 股 · 沪市、深市、北交所':draft.selection.mode==='explicit'?'自定义股票列表':'自定义市场 / 板块')+'</span><p class="rs-help">无需再次设置行情路径。全部 A 股指当前数据源覆盖的 A 股；实际覆盖数量将在筛选后显示。筛选条件与日期在下方调整。</p>'+(!allA||draft.selection.source!=='local'?'<button class="rs-button" type="button" data-pf-action="pool-all-a">恢复全局来源与全部 A 股</button>':'')+'</div>'+fold('高级：限定股票范围或使用历史数据包','pool-source','<div class="rs-fields">'+select('研究数据来源','selection.source',draft.selection.source||'local',{local:'沿用系统本地日线',history:'已导入的历史数据包'})+
      (draft.selection.source==='history'?select('历史数据包','selection.sourceId',draft.selection.sourceId,{'':'请选择数据包',...choices}):'')+
      select('股票池范围','selection.mode',draft.selection.mode,{all:'全部 A 股（所选市场与板块）',explicit:'指定股票列表'})+
      (draft.selection.mode==='explicit'?textField('股票代码，逗号或空格分隔','symbols',draft.symbols):'')+'</div>'+
      '<div class="pf-options">'+markets+'</div><div class="pf-options">'+boards+'</div><div class="rs-actions"><button class="rs-button" type="button" data-pf-action="import-history" '+(importingHistory?'disabled':'')+'>'+(importingHistory?'正在校验历史数据包…':'导入历史 JSON / JSONL 数据包')+'</button></div>'+
      '<p class="rs-help">'+(draft.selection.source==='history'?'历史包校验检查成分、交易状态、日历与权益的包内完整性，不替代对数据商来源的核验。':'本地现存文件代表当前覆盖，不能保证历史成分、退市与权益完整；正式样本外研究需要补齐这些历史事实。')+'</p>');
  }
  const finePoolFields={
    base:[['minAmplitude20','20 日平均振幅下限（%）',.03,'min="0" max="1000"','近 20 日 (最高价 − 最低价) / 收盘价的平均值。']],
    structure:[
      ['minMa10Above20Days','MA10 高于 MA20 的最少天数',5,'min="0" max="20" step="1"','使用上方选定的累计 / 连续口径；均线相等不算满足。'],
      ['minMa5Above10Days','MA5 高于 MA10 的最少天数',3,'min="0" max="20" step="1"','观察最近 20 天；完整比较需要至少 39 根日线。'],
      ['maxPeakAgeDays','窗口峰值距今最多交易日',3,'min="0" max="499" step="1"','峰值口径与回撤相同；相同峰值取最近一天，0 表示今天。上限须小于结构窗口。'],
      ['maxPriceVsMa20','距 MA20 的绝对偏离上限（%）',.08,'min="0" max="500"','绝对值 |收盘价 / MA20 − 1|；是否必须站上 MA20 由独立开关决定。']
    ],
    volume:[
      ['minVolumeChange20','20 日首尾成交量增幅下限（%）',.05,'min="-100" max="2000"','当日成交量 / 19 个交易日前成交量 − 1；这是首尾变化，不是回归斜率。'],
      ['minUpDownVolumeRatio','上涨日均量 / 下跌日均量下限（倍）',1.3,'min="0" max="100"','近 20 日按收盘价较前日涨跌分组，平盘归上涨组；任一组缺失时数据不足。'],
      ['maxDownVolumeRatio','下跌日均量 / 全期均量上限（倍）',.9,'min="0" max="100"','近 20 日下跌日的平均量相对全期平均量；没有下跌日时数据不足。']
    ]
  };
  const finePoolPath=(pool,key)=>'config.'+(pool==='base'?'universe.':'poolSettings.'+pool+'.')+key;
  function finePoolForm(pool){
    const values=pool==='base'?draft.config.universe:draft.config.poolSettings[pool];
    const optional=(finePoolFields[pool]||[]).map(([key,label,reference,attrs,help])=>{
      const path=finePoolPath(pool,key),enabled=numeric(values[key]);
      return `<div class="pf-optional-filter"><label class="pf-check"><input type="checkbox" data-pf-optional="${path}" ${enabled?'checked':''}>启用：${E(label)}</label>${field(label,path,values[key],attrs+' data-pf-nullable'+(enabled?'':' disabled'))}<p class="rs-help">${E(help)} 清空数值会关闭此条件。</p></div>`;
    }).join('');
    let body=optional,title='高级：振幅';
    if(pool==='trend'){
      title='高级：全池涨幅排名';
      const off=values.rankEnabled?'':' disabled';
      body=check('启用全池涨幅排名区间','config.poolSettings.trend.rankEnabled',values.rankEnabled)+`<div class="rs-fields">`+
        field('排名涨幅窗口（交易日）','config.poolSettings.trend.rankWindow',values.rankWindow,'min="2" max="500" step="1"'+off)+
        field('起始名次（含）','config.poolSettings.trend.rankStart',values.rankStart,'min="1" max="100000" step="1"'+off)+
        field('结束名次（含）','config.poolSettings.trend.rankEnd',values.rankEnd,'min="1" max="100000" step="1"'+off)+
        '</div><p class="rs-help">对全部通过基础资格且历史足够的股票，按窗口涨幅从高到低排名；同涨幅按代码升序。名次在其他分层条件、搜索与分页之前计算，不按策略打分，也不额外截断 400 只。</p>';
    }
    if(pool==='structure'){
      title='高级：均线持续性与回撤细节';
      body='<div class="rs-fields">'+select('回撤与峰值日期使用','config.poolSettings.structure.peakBasis',values.peakBasis,{close:'窗口内最高收盘价（原口径）',high:'窗口内最高价（参考口径）'})+
        select('均线持续性统计方式','config.poolSettings.structure.maDaysMode',values.maDaysMode,{count:'最近 20 天累计满足天数',consecutive:'截至当日连续满足天数（最多 20 天）'})+'</div>'+optional+
        check('要求收盘价高于 MA20','config.poolSettings.structure.requireAboveMa20',values.requireAboveMa20);
    }
    if(pool==='volume'){
      title='高级：量价关系与风险剔除';
      body+=check('剔除成交量突增收阴风险','config.poolSettings.volume.excludeBlowoff',values.excludeBlowoff)+
        '<p class="rs-help">近 20 日任一天成交量超过全期均量 2.5 倍，且收盘价 ≤ 开盘价。</p>'+
        check('剔除近期上涨缩量背离','config.poolSettings.volume.excludeDivergence',values.excludeDivergence)+
        '<p class="rs-help">当前收盘价高于 5 个交易日前，且最近 5 日均量低于此前 5 日均量的 90%。</p>'+
        check('剔除长上影收阴风险','config.poolSettings.volume.excludeUpperShadow',values.excludeUpperShadow)+
        '<p class="rs-help">近 5 日任一天上影线超过当日振幅的一半，且收盘价 ≤ 开盘价；最高价等于最低价的日线不计。</p>';
    }
    const count=(finePoolFields[pool]||[]).filter(([key])=>numeric(values[key])).length+(pool==='trend'&&values.rankEnabled?1:0)+(pool==='structure'&&values.requireAboveMa20?1:0)+(pool==='volume'?['excludeBlowoff','excludeDivergence','excludeUpperShadow'].filter(key=>values[key]).length:0);
    return fold(title+' · 已启用 '+count+' 项','pool-fine-'+pool,body);
  }
  function poolCard(key,title,description,fields){
    const count=screen.result?.poolCounts?.[key],stale=screen.result&&screen.signature!==screenSignature();
    return '<article class="pf-pool-card"><header><h4>'+E(title)+'</h4><span class="pf-pool-count">'+(count==null?'尚未筛选':n(count,0)+' 只'+(stale?' · 旧设置':''))+'</span></header><p class="rs-help">'+E(description)+'</p><div class="rs-fields">'+fields+'</div>'+
      finePoolForm(key)+'<button type="button" class="rs-button" data-pf-action="screen-pool" data-pf-pool="'+key+'" '+(!screen.result?'disabled':'')+'>查看这个池的股票</button></article>';
  }
  function poolForm(standalone=false,layersOnly=false,baseOnly=false){
    const c=draft.config;
    const period=(isPlateau()||isSingle()?'<div class="rs-field"><span>开发区间</span><strong>指定起止日期'+(isPlateau()?'（所有候选一致）':'')+'</strong><small>明确开发截止日，之后才可冻结并验证更晚的新时段。</small></div>':select('研究区间','periodMode',draft.periodMode,{recent:'最近 N 个交易日',dates:'指定起止日期'}))+
      (draft.periodMode==='recent'?field('交易日数量','config.lookbackDays',c.lookbackDays,'min="2"'):textField('开始日期','config.startDate',c.startDate,'date')+textField('结束日期 / 筛选截止日','config.endDate',c.endDate,'date'));
    const sourceSettings=dataSourceForm()+'<fieldset class="rs-fieldset"><legend>研究区间与基础资格</legend><div class="rs-fields">'+period+'</div><div class="pf-options">'+
      check('剔除已知历史 ST','config.universe.excludeSt',c.universe.excludeSt)+check('严格历史资格（缺历史事实则剔除）','config.universe.strictHistory',c.universe.strictHistory)+'</div>'+
      '<p class="rs-help">按下方股票池条件检查整个来源范围。入池资格由行情数据是否满足条件决定；列表按当日成交额从高到低排列。最近 N 日的实际日期由冻结后的行情日历确定。</p></fieldset>';
    const base=poolCard('base','基础可交易池','通过上市时间、流动性和所选历史资格检查。',field('最少上市交易日','config.universe.minListingDays',c.universe.minListingDays,'min="0"')+field('20 日平均成交额下限（万元）','config.universe.minAmount20',c.universe.minAmount20,'min="0"'));
    if(baseOnly)return sourceSettings+base+'<div class="rs-actions"><a class="rs-button" data-nav="M06">在独立股票池查看筛选结果</a></div>';
    const settings=(layersOnly?'':sourceSettings)+
      '<div class="pf-pool-settings"><h3>分层筛选条件</h3><p class="rs-help">先检查基础资格，再按趋势、回调结构与量能筛选。下方可自选交集／并集；结果区也能按四层累计漏斗查看。</p><div class="pf-pool-grid">'+
      (layersOnly?'':base)+
      poolCard('trend','趋势池','站上窗口均线，均线抬升，阶段涨幅达到下限。',field('趋势窗口','config.poolSettings.trend.window',c.poolSettings.trend.window)+field('趋势最低涨幅（%）','config.poolSettings.trend.minReturn',c.poolSettings.trend.minReturn))+
      poolCard('structure','回调结构池','距离窗口内'+(c.poolSettings.structure.peakBasis==='high'?'最高价':'最高收盘价')+'的回撤落在设定区间。',field('结构窗口','config.poolSettings.structure.window',c.poolSettings.structure.window)+field('结构最低回撤（%）','config.poolSettings.structure.minDrawdown',c.poolSettings.structure.minDrawdown)+field('结构最高回撤（%）','config.poolSettings.structure.maxDrawdown',c.poolSettings.structure.maxDrawdown))+
      poolCard('volume','量能活跃池','当日量达到此前窗口平均成交量的设定倍数。',field('量能观察窗口','config.poolSettings.volume.window',c.poolSettings.volume.window)+field('量能最低倍数','config.poolSettings.volume.minRatio',c.poolSettings.volume.minRatio))+'</div></div>';
    return (standalone?fold('筛选条件 · 数据范围、研究日期与分层参数','stock-pool-settings',settings):settings)+combinationControls()+'<div class="rs-actions pf-preview-actions"><button type="button" class="rs-button rs-primary" data-pf-action="screen-preview" '+(screen.busy?'disabled':'')+'>'+(screen.busy?'正在提交筛选…':'按当前设置筛选股票池')+'</button><span class="rs-help">取研究结束日收盘或行情最新日。完成后切换交集／并集可立即重查整池，无需重复创建任务。</span></div>'+screenView();
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
      (isSingle()?'<div class="rs-field"><span>研究账户</span><strong>单策略固定配置</strong><small>组合与轮动留到第 6 阶段。</small></div>':isPlateau()?'<div class="rs-field"><span>策略账户比较方式（研究协议）</span><p class="rs-help">逐组比较各策略和等权账户；持续性通过后，再运行软配权、硬切换对照。普通回测的配权模式不用于固定本次研究。</p></div>':select('策略之间的配权','config.allocation.mode',c.allocation.mode,{equal:'参与策略等权',single:'固定一个基础或组合策略',soft_rotation:'动态软配权',hard_rotation:'动态硬切换（对照）'}))+
      select('策略内股票权重','config.weighting',c.weighting,{equal:'股票等权',rank:'按排名分数加权'})+'</div></fieldset>'+
      '<fieldset class="rs-fieldset"><legend>持仓与调仓</legend><div class="rs-fields">'+select('调仓方式','config.rebalanceMode',c.rebalanceMode,{risk:'风险退出后补仓',rank:'按排名换仓',hybrid:'排名换仓 + 风险退出'})+
      field('补仓 / 排名调整间隔（交易日）','config.rebalanceEvery',c.rebalanceEvery,'min="1"')+
      (ranking?field('排名换仓最小分差（百分点）','config.switchThreshold',c.switchThreshold,'min="0" max="100"'):'')+
      field('止损（%，0 关闭）','config.stopLossPct',c.stopLossPct)+field('从持仓收盘峰值回撤（%，0 关闭）','config.trailingStopPct',c.trailingStopPct)+
      field('止盈（%，0 关闭）','config.takeProfitPct',c.takeProfitPct)+field('最长持仓交易日（0 关闭）','config.maxHoldingDays',c.maxHoldingDays)+'</div>'+
      '<p class="rs-help">股票池与策略评分每日重算；持仓是否调整由上述规则决定。风险每日收盘检查，次交易日开盘尝试执行。风险退出模式保留旧股股数，信号消失不会自动卖出。上述单股比例是新买目标上限，持仓涨跌后实际权重可能变化。已设为扫描的比例以每组候选值为准。Top 1 仅作对照。</p></fieldset>'+
      fold('交易成本与权益处理','costs','<div class="rs-fields">'+field('佣金（%）','config.commissionRate',c.commissionRate)+field('最低佣金（元）','config.minCommission',c.minCommission)+
      field('卖出印花税（%，空白按日期）','config.stampDutyRate',c.stampDutyRate)+field('过户费（%）','config.transferFeeRate',c.transferFeeRate)+
      field('单边滑点（基点）','config.slippageBps',c.slippageBps)+field('上一交易日成交量参与上限（%）','config.maxParticipationRate',c.maxParticipationRate)+
      select('配股处理','config.rightsPolicy',c.rightsPolicy||'skip',{skip:'不认购，记录放弃权益',subscribe:'资金足够时按公告认购'})+'</div><p class="rs-help">费用、滑点、T+1 与涨跌停成交约束计入账户。现金分红按数据包声明的税后金额，到账前不可用于买入。</p>')+
      (isSingle()?'':fold('策略轮动参数（动态配权与研究对照时生效）','rotation',rotationForm()));
  }
  function parameterFromConfig(config,path){
    const parts=path.split('.');
    if(parts[0]==='strategies')return config.strategies?.find(s=>s.id===parts[1])?.params?.[parts[3]];
    if(parts[0]==='combinations'){const c=config.combinations?.find(c=>c.id===parts[1]);return parts[2]==='children'?c?.children?.find(v=>v.id===parts[3])?.weight:c?.[parts[2]];}
    return parts.length===2?config[parts[0]]?.[parts[1]]:config[path];
  }
  function scanPlanView(config,axes=[],experiment={}){
    const scanned=new Set(axes.map(a=>a.path)),count=axes.reduce((n,a)=>n*a.values.length,1);
    const varying=axes.map(a=>'<tr data-pf-scan-path="'+E(a.path)+'"><td>'+E(parameterLabel(a.path))+'</td><td>'+E(axisValueText(a.path,parameterFromConfig(config,a.path)))+'<small>仅为基础参考，不自动加入测试</small></td><td><b>'+E(a.values.map(v=>axisValueText(a.path,v)).join('、'))+'</b></td></tr>');
    const fixed=[];const add=(path,label,value)=>{if(!scanned.has(path)&&value!==undefined)fixed.push('<tr data-pf-fixed-path="'+E(path)+'"><td>'+E(label)+'</td><td>'+E(typeof value==='number'?axisValueText(path,value):String(value))+'</td></tr>');};
    add('rebalanceMode','调仓方式',({risk:'风险退出后补仓',rank:'按排名换仓',hybrid:'排名换仓 + 风险退出'})[config.rebalanceMode]);
    add('weighting','策略内股票权重',({equal:'等权',rank:'按排名加权'})[config.weighting]);
    add('allocation.strategyIds','参与研究的策略',(config.allocation?.strategyIds||[]).map(strategyName).join('、'));
    add('strategyPools','各策略使用的股票池',(config.strategies||[]).map(s=>strategyName(s.kind)+'：'+(poolLabels[s.pool]||s.pool)).join('；'));
    for(const [key,label]of Object.entries({commissionRate:'佣金率（%）',minCommission:'最低佣金（元）',transferFeeRate:'过户费率（%）',slippageBps:'单边滑点（基点）',maxParticipationRate:'成交量参与上限（%）'}))add(key,label,config[key]);
    add('stampDutyRate','卖出印花税',config.stampDutyRate==null?'按历史日期':display('stampDutyRate',config.stampDutyRate)+'%');
    add('rightsPolicy','配股处理',config.rightsPolicy==='subscribe'?'资金足够时认购':'不认购');
    for(const key of ['topN','maxPositions','maxStockWeight','rebalanceEvery','switchThreshold','stopLossPct','takeProfitPct','trailingStopPct','maxHoldingDays'])add(key,parameterLabel(key),config[key]);
    if(!isSingle())for(const [key,value]of Object.entries(config.rotation||{}))add('rotation.'+key,parameterLabel('rotation.'+key),value);
    for(const s of config.strategies||[])for(const [key,value]of Object.entries(s.params))add(`strategies.${s.id}.params.${key}`,parameterLabel(`strategies.${s.id}.params.${key}`),value);
    for(const c of config.combinations||[]){add(`combinations.${c.id}.method`,parameterLabel(`combinations.${c.id}.method`),axisValueText('method',c.method));add(`combinations.${c.id}.threshold`,parameterLabel(`combinations.${c.id}.threshold`),c.threshold);for(const child of c.children)add(`combinations.${c.id}.children.${child.id}.weight`,parameterLabel(`combinations.${c.id}.children.${child.id}.weight`),child.weight);}
    return `<section data-pf-scan-summary><h4>本次变化的参数 · ${count} 组</h4><p class="rs-help">${axes.map(a=>a.values.length).join(' × ')} = ${count}。下面每项只使用候选值逐组替换，不另跑一组基础参考值。</p>${table(['扫描参数','基础参考值（被替换）','实际候选值'],varying)}</section><section data-pf-fixed-summary><h4>本次固定条件 · 所有候选共同使用</h4><p>研究区间：${E(config.startDate||'未填写')} — ${E(config.endDate||'未填写')}；初始资金 ${n(config.initialCapital/10000,4)} 万元。</p><p class="rs-help">股票池条件、策略所用池、交易成本与交易限制保持相同。${isSingle()?'整个区间都用于开发比较；新时段验证必须在选定并冻结配置后单独创建。':'轮动参数只在训练持续性通过后进入轮动账户；未通过时保留固定策略和等权对照，不认定四层平原有效。'}</p><details><summary>展开 ${fixed.length} 项固定参数明细</summary>${table(['未扫描参数','保持固定的值'],fixed)}</details>${isSingle()?'<p class="rs-help">本次运行 '+count+' 组单策略账户，没有组合、轮动或自动留出验证。</p>':`<p class="rs-help">验证安排固定为每折训练 ${n(experiment.trainDays,0)} 日、后续样本外 ${n(experiment.testDays,0)} 日、步长 ${n(experiment.stepDays,0)} 日，最多 ${n(experiment.maxFolds,0)} 折。${count} 是候选组合数，不是总回测次数；每组还需运行多策略账户及各验证折。</p>`}</section>`;
  }
  function effectiveView(){
    if(workflow.pending)return note('正在按后端规则校验当前设置…');
    if(workflow.error)return '<div class="pf-config-error" role="alert">'+E(workflow.error)+'</div><p class="rs-help">请修改对应设置；校验未通过时不会创建任务。</p>';
    const r=workflow.review;
    if(!r||workflow.signature!==requestSignature())return note('当前设置已变化，等待校验。创建任务时会再次核对。');
    const config=r.config,selection=r.selection;
    if(r.kind==='poolPreview')return '<p class="pf-config-ready" role="status">股票池条件校验通过</p><p>'+E(r.name)+'</p>'+configuration(config,selection,true);
    const params=config.strategies.map(s=>{const spec=definition.strategies.find(v=>v.id===s.kind);return '<tr><td>'+E(spec?.name||s.kind)+'</td><td>'+E(poolLabels[s.pool]||s.pool)+'</td><td>'+E(Object.entries(s.params).map(([key,value])=>{const field=spec?.parameters.find(v=>v.key===key);return (field?.label||key)+' '+display(key,value)+(isPercent(key)?'%':'');}).join('；'))+'</td></tr>';});
    const x=r.experiment;
    if(['systemPlateau','singlePlateau'].includes(r.kind))return '<p class="pf-config-ready" role="status">配置校验通过 · 将按扫描候选逐组测试</p><p><strong>'+E(r.name)+'</strong></p>'+scanPlanView(config,x.axes,x)+
      '<p class="rs-help">佣金 '+display('commissionRate',config.commissionRate)+'%，最低 '+n(config.minCommission)+' 元；印花税 '+(config.stampDutyRate==null?'按历史日期':display('stampDutyRate',config.stampDutyRate)+'%')+'；过户费 '+display('transferFeeRate',config.transferFeeRate)+'%；滑点 '+n(config.slippageBps)+' 基点。T 日收盘产生信号，下一交易日开盘尝试执行。</p>'+
      fold('完整基础配置存档（扫描项会被上表替换）','effective-config',configuration(config,selection));
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
    const plateau=isPlateau();
    if(isSingle())return '<div class="rs-fields"><div class="rs-field"><span>当前研究阶段</span><strong>'+E(kindLabels[draft.kind])+'</strong></div>'+textField('研究名称（可留空）','name',draft.name)+textField('业绩基准代码（可留空，如 sh000300）','selection.benchmarkSymbol',draft.selection.benchmarkSymbol)+'</div>'+note(plateau?'本次全部日期用于开发比较。选择具体参数点并冻结后，才能用更晚的新时段验证。':'本次只运行这一套固定参数。先查看操作与持仓是否符合研究假设，再将完成任务带入关键参数平原。')+'<section class="pf-effective-panel"><h4>'+(plateau?'核对扫描项与固定条件':'最终生效配置')+'</h4><div data-pf-effective>'+effectiveView()+'</div><button type="button" class="rs-button" data-pf-action="review-settings">重新校验配置</button></section><div class="rs-actions pf-submit"><button type="submit" class="rs-button rs-primary" '+(workflow.submitting?'disabled':'')+'>'+(workflow.submitting?'正在提交…':editingTaskId?'保存任务修改':plateau?'创建单策略平原':'创建固定参数回测')+'</button><button type="button" class="rs-button" data-pf-action="save">保存设置</button><button type="button" class="rs-button rs-quiet" data-pf-action="reset">重新开始此阶段</button></div>';
    return '<div class="rs-fields">'+(plateau?'<div class="rs-field"><span>任务类型</span><strong>四层全系统收益平原</strong></div>':select('任务类型','kind',draft.kind,Object.fromEntries(Object.entries(kindLabels).filter(([key])=>['portfolio','comparison','walkForward'].includes(key)))))+textField('研究名称（可留空，软件自动命名）','name',draft.name)+
      textField('业绩基准代码（可留空，如 sh000300）','selection.benchmarkSymbol',draft.selection.benchmarkSymbol)+'</div>'+
      (plateau?'<p class="rs-help">确认下方扫描项和固定条件后创建。日期按实际交易日历核验；结果将报告稳定区间及未通过原因，不保证一定产生推荐参数。</p>':'<p class="rs-help">高级组合回测可使用最近 N 个交易日；策略比较与滚动验证需要明确起止日期。高级四层平原保留独立草稿。</p><button type="button" class="rs-button" data-pf-action="advanced-plateau">打开高级四层平原</button>')+
      ((draft.kind==='comparison'||experimentKinds.has(draft.kind))&&(draft.periodMode!=='dates'||!draft.config.startDate||!draft.config.endDate)?'<div class="rs-actions">'+note('请先选择明确的研究起止日期。')+stepButton(1,'返回第 1 步设置日期')+'</div>':'')+
      (draft.kind==='comparison'?'<div class="rs-fields">'+textField('训练结束日期','study.trainEnd',draft.study.trainEnd,'date')+textField('样本外开始日期','study.testStart',draft.study.testStart,'date')+textField('样本外结束日期','study.testEnd',draft.study.testEnd,'date')+'</div>':'')+
      (!plateau&&(draft.kind==='comparison'||experimentKinds.has(draft.kind))?fold('持续性检验与轮动准入','persistence',diagnosticForm(false)):'')+
      (!plateau&&experimentKinds.has(draft.kind)?fold('高级研究：滚动验证与参数扫描','experiment',experimentForm()):'')+
      '<section class="pf-effective-panel"><h4>'+(plateau?'核对本次测试内容':'最终生效配置')+'</h4><div data-pf-effective>'+effectiveView()+'</div><button type="button" class="rs-button" data-pf-action="review-settings">重新校验配置</button></section>'+
      '<div class="rs-actions pf-submit"><button type="submit" class="rs-button rs-primary" '+(workflow.submitting?'disabled':'')+'>'+(workflow.submitting?'正在提交…':editingTaskId?'保存任务修改':'按生效配置创建任务')+'</button>'+
      (editorNote?'<button type="button" class="rs-button rs-quiet" data-pf-action="cancel-edit">结束编辑，恢复新建</button>':'')+'<button type="button" class="rs-button" data-pf-action="save">保存设置</button><button type="button" class="rs-button rs-quiet" data-pf-action="reset">'+(plateau?'恢复平原填写示例':'恢复测试预设')+'</button><span class="rs-help">任务在本机后台计算，可暂停、继续和停止。</span></div>';
  }
  function requestSignature(){try{return JSON.stringify(payload(draft,definition.strategies));}catch{return null;}}
  function renderEffective(){
    const target=document.querySelector?.('[data-pf-effective]');
    if(target)target.innerHTML=effectiveView();
  }
  function queueReview(){
    workflow.review=null;workflow.signature=null;workflow.error='';
    if(scope==='market')return;
    if(typeof hooks?.previewSettings!=='function')return;
    workflow.serial++;clearTimeout(workflow.timer);workflow.pending=true;renderEffective();
    workflow.timer=setTimeout(()=>{void reviewSettings().catch(()=>{});},400);
  }
  async function reviewSettings(){
    clearTimeout(workflow.timer);
    let request;
    try{if(experimentKinds.has(draft.kind)){const state=scanState();if(!state.valid)throw new Error(Object.values(state.errors)[0]||state.message);}request=payload(draft,definition.strategies);}catch(error){workflow.pending=false;workflow.error=error.message;renderEffective();throw error;}
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
    return poolPayload(draft);
  }
  function form() {
    const target=scope==='advanced'?'advanced':'research',changed=scope!==target;activateScope(target);if(changed)queueReview();
    if(!draft)return note('正在读取组合回测设置…');
    const active=currentStep();
    const nav=stepLabels.map((label,index)=>'<button type="button" class="pf-step-nav '+(active===index+1?'is-active':'')+'" data-pf-action="workflow-step" data-pf-step="'+(index+1)+'" '+(active===index+1?'aria-current="step"':'')+'><span>'+(index+1)+'</span>'+E(label)+'</button>').join('');
    if(active===5)return '<div class="pf-workbench">'+pipeline(isSingle()?3:6)+'<nav class="pf-workflow-nav" aria-label="策略与回测流程">'+nav+'</nav><p class="rs-help">下方选择研究任务，查看进度、收益、逐日持仓与买卖计划。返回前四步可调整设置，已有报告仍保留原配置。</p></div>';
    if(isSingle())return '<form data-pf-form class="pf-workbench" novalidate>'+pipeline(active===1?1:active===2?2:3)+(editorNote?note(editorNote):'')+'<div class="pf-intro"><strong>单策略固定参数回测</strong><p>先确定基础池，再为一个策略配置风格漏斗。策略信号与交易规则保持固定，运行后检查具体买卖行为。</p></div><nav class="pf-workflow-nav" aria-label="策略与回测流程">'+nav+'</nav>'+workflowSection(1,'基础可交易池与开发日期','沿用全局行情来源，默认全部 A 股。基础资格只检查可交易条件。',poolForm(false,false,true))+workflowSection(2,'选择一个策略，并配置对应风格漏斗','漏斗限制研究样本，策略决定具体买卖信号；各自的条件分别保存。',singleStrategyForm())+workflowSection(3,'固定资金与交易规则','这一阶段每个参数只有一个固定值。',tradingForm())+workflowSection(4,'核对并创建固定参数回测','完成后从报告直接进入关键参数平原。',taskForm())+'</form>';
    return '<form data-pf-form class="pf-workbench" novalidate>'+(editorNote?note(editorNote):'')+
      pipeline(6)+'<div class="pf-intro"><strong>高级研究 · 组合与轮动</strong><p>先完成多个单策略的冻结与新时段验证，再研究组合。这里使用独立草稿；新增策略、合并漏斗或改变配权都属于新的开发研究，不能沿用单策略的独立验证结论。</p></div><nav class="pf-workflow-nav" aria-label="策略与回测流程">'+nav+'</nav>'+
      workflowSection(1,'设置并查看股票池','沿用全局数据源，默认全部 A 股；按日期和分层条件筛选。','<div class="rs-actions"><button type="button" class="rs-button" data-nav="M06">打开独立股票池</button></div>'+poolForm())+
      workflowSection(2,'选择策略与组合','基础策略和组合策略都可以参与比较或配权。',combinationsForm())+
      workflowSection(3,'设置资金与交易规则','每天重新评分；按排名或风险退出规则调整持仓。',tradingForm())+
      workflowSection(4,'创建研究任务','核对最终生效配置，再交给本机后台计算。',taskForm())+
      (active===5?'<p class="rs-help">下方选择研究任务，查看进度、收益、逐日持仓与买卖计划。已有报告仍保留原设置。</p>':'')+'</form>';
  }
  function poolWorkspace(){
    if(!definition)return note('正在读取股票池设置…');
    activateScope('market');
    return '<form data-pf-pool-form class="pf-workbench pf-pool-workspace" novalidate>'+pipeline(1)+poolForm(true)+'<div class="rs-actions"><button type="button" class="rs-button rs-primary" data-pf-action="pool-to-research">带入条件，配置单策略漏斗</button></div></form>';
  }
  function plateauForm(){
    if(!definition)return note('正在读取收益平原设置…');
    const target=scope==='advancedPlateau'?'advancedPlateau':'plateau',changed=scope!==target;activateScope(target);if(changed){save();queueReview();}
    const active=currentStep(),nav=plateauStepLabels.map((label,index)=>'<button type="button" class="pf-step-nav '+(active===index+1?'is-active':'')+'" data-pf-action="workflow-step" data-pf-step="'+(index+1)+'" '+(active===index+1?'aria-current="step"':'')+'><span>'+(index+1)+'</span>'+E(label)+'</button>').join('');
    const heading=pipeline(isSingle()?4:6)+'<div class="pf-intro"><strong>'+(isSingle()?'单策略平原 · 少量参数 × 同一开发区间':'高级全系统平原 · 四层联合研究')+'</strong><p>'+(isSingle()?'从已完成的单策略回测带入配置，再选 1–3 个关键参数。所有候选仍属于开发样本；选定、冻结后再单独验证新时段。':'这是独立高级草稿，联合研究策略、组合、轮动与持仓。已有任务与旧设置仍然保留。')+'</p></div><nav class="pf-workflow-nav" aria-label="收益平原创建流程">'+nav+'</nav>';
    if(active===5)return '<div class="pf-workbench pf-plateau-workbench">'+heading+'<p class="rs-help">下方查看收益平原任务与结果；未通过检验时会显示原因，已有报告保留其创建时的参数。</p>'+stepButton(4,'返回核对创建')+'</div>';
    return '<form data-pf-form data-pf-plateau-form class="pf-workbench pf-plateau-workbench" novalidate>'+(editorNote?note(editorNote):'')+heading+
      workflowSection(1,'确定研究对象与日期','开发区间及股票池条件对所有候选保持相同。',
        '<div class="rs-actions"><button class="rs-button" type="button" data-pf-action="plateau-import">导入固定参数回测草稿</button></div>'+(isSingle()?poolForm(false,false,true)+fold('查看单策略、漏斗与基础参数','plateau-strategies',singleStrategyForm()):poolForm()+fold('选择参与研究的策略与组合','plateau-strategies',combinationsForm())))+
      workflowSection(2,'选择要测试的参数与候选值','填写多个候选值，软件逐组替换测试；每个基础参考值都标在对应扫描项下。',scanEditor())+
      workflowSection(3,'核对固定条件与稳定性门槛','未扫描的条件对所有候选相同；已扫描字段直接标出候选值，不再重复填写一个固定值。',tradingForm()+'<div class="rs-actions">'+stepButton(1,'查看或修改其余固定策略参数')+'</div>'+experimentForm(false)+(isSingle()?'':fold('持续性检验与轮动准入','persistence',diagnosticForm(false))))+
      workflowSection(4,'核对本次到底测试什么','扫描候选逐组变化，未扫描条件保持固定；通过配置校验后提交。',taskForm())+'</form>';
  }
  const reasonLabels = {stopLoss:'止损',takeProfit:'止盈',trailingStop:'回撤退出',maxHoldingDays:'最长持仓',insufficient:'证据不足',passed:'通过',failed:'未通过',complete:'已完成诊断',running:'计算中',eligible:'合格',unknown:'未知',up:'上行',down:'下行',mixed:'震荡',high:'高',normal:'中',medium:'中',low:'低',observed:'已观测',not_implemented:'旧报告尚未实现',skipped:'已跳过轮动',no_value:'未通过关键检验',unverified:'缺少认证证据',adjusted:'已调整',held:'维持预算',cooldown:'尚在冷却期',belowThreshold:'变化未达到门槛',insufficientHistory:'历史观察不足'};
  Object.assign(reasonLabels,{recentNetPerformanceAndKnownRegime:'近期净收益与当时市场环境',insufficientShadowHistory:'策略组合观察期不足',unreliableShadowValuation:'策略组合估值证据不足',belowWeightThreshold:'预算变化未达到门槛',evidence_integrity_failed:'行情隔离、陈旧估值或比较完整性检查失败',experiment_incomplete:'研究尚未完成',four_layer_system_plateau_not_requested:'本次为滚动验证，没有请求四层系统平原',training_neighborhood_not_stable_in_all_folds:'并非每个训练折都有稳定邻域',data_quality_not_certified:'历史成分或权益等数据质量尚未认证',training_persistence_not_passed_for_frozen_region:'冻结区域未全部通过训练持续性检验',training_persistence_not_passed_for_entire_grid:'参数网格未全部通过训练持续性检验',frozen_region_evidence_missing:'训练冻结区域的成员证据缺失',rotation_did_not_beat_both_baselines_in_every_fold:'轮动未在每折样本外同时超过两项基线',frozen_neighborhood_failed_out_of_sample:'训练冻结的邻域未通过样本外检验',no_common_training_region_across_folds:'不同训练折之间没有共同稳定区域',frozen_neighborhood_failed_years:'冻结邻域未通过分年份验证',frozen_neighborhood_failed_regimes:'冻结邻域未通过分环境验证',insufficient_out_of_sample_years:'样本外覆盖年份不足',insufficient_observed_market_regimes:'真实市场环境覆盖不足'});
  Object.assign(reasonLabels,{projected_intervals_contain_unverified_combinations:'参数区间交叉后存在未验证组合',an_axis_has_no_stable_width:'部分参数轴没有稳定宽度',numeric_region_contains_grid_gaps:'数值参数区域存在网格空洞',empty_region:'没有共同可验证区域',holdout_reused_with_changed_settings:'验证区间已用不同设置计算，现为开发样本'});
  Object.assign(reasonLabels,{insufficient_closed_trades:'已关闭持仓波段不足，未达到开发期样本门槛',drawdown_limit_exceeded_or_missing:'最大回撤超过设定上限或缺少回撤记录',source_fingerprint_mismatch:'行情来源与本次冻结快照不一致',data_quality_missing:'缺少数据质量检查记录',data_quality_checks_missing:'数据质量检查不完整',quarantined_symbols:'存在被隔离的异常证券',stale_valuation:'存在陈旧价格估值',unaccounted_corporate_action_exposure:'存在未纳入账本的公司行动影响'});
  const readable = v => typeof v==='string' ? reasonLabels[v]||v : JSON.stringify(v ?? null);
  function metricTable(results) {
    return table(['方案','累计收益','年化','最大回撤','夏普','完整持仓波段胜率','换手','费用（元）','滑点（元）','成本 / 初始资金'],results.map(([label,r])=>{const m=r?.metrics||{};return `<tr><td>${E(label)}</td><td>${p(m.totalReturnPct)}</td><td>${p(m.annualizedReturnPct)}</td><td>${p(m.maxDrawdownPct)}</td><td>${n(m.sharpe ?? m.sharpeRatio)}</td><td>${p(m.winRatePct)}<small>${numeric(m.closedTradeCount)?n(m.closedTradeCount,0)+' 个已关闭波段':'暂无关闭波段数'}</small></td><td>${p(m.turnoverPct)}</td><td>${n(m.totalFees ?? m.totalCosts)}</td><td>${n(m.totalSlippage)}</td><td>${p(m.costRatioPct)}</td></tr>`;}))+`<p class="rs-help pf-metric-note">胜率按完整关闭的持仓波段统计；费用与滑点均计入净收益，成本占比为两者合计除以初始资金，滑点已包含在成交价中。</p>`;
  }
  function configuration(config,selection={},poolOnly=false){
    if(!config)return '';
    const mode={single:'固定策略',equal:'策略等权',soft_rotation:'动态软配权',hard_rotation:'硬切换对照'},pools=poolLabels,p=config.poolSettings||{};
    const items=[['区间',config.startDate?config.startDate+' — '+config.endDate:'最近 '+config.lookbackDays+' 个交易日'],['初始资金',n(config.initialCapital)+' 元'],['股票池范围',selection.mode==='explicit'?'指定股票列表':selection.mode==='all'?'全部所选市场与板块':'见原任务来源'],['基础资格','最少上市 '+config.universe?.minListingDays+' 个交易日；20 日均额 ≥ '+n(config.universe?.minAmount20/10000)+' 万元；'+(config.universe?.excludeSt?'剔除已知 ST':'保留 ST')],['趋势池','窗口 '+p.trend?.window+' 日；最低涨幅 '+thisPercent(p.trend?.minReturn)],['回调结构池','窗口 '+p.structure?.window+' 日；回撤 '+thisPercent(p.structure?.minDrawdown)+' — '+thisPercent(p.structure?.maxDrawdown)],['量能池','此前 '+p.volume?.window+' 日均量；最低 '+n(p.volume?.minRatio)+' 倍'],['叠加筛选池',(p.combined?.pools||[]).map(key=>poolLabels[key]||key).join(p.combined?.mode==='union'?' 或 ':' + ')||'全部基础可交易池'],['组合预算',mode[config.allocation?.mode]||'—'],['候选与持仓','每策略 Top '+config.topN+'；最多 '+config.maxPositions+' 只；新买单股目标上限 '+thisPercent(config.maxStockWeight)],['调仓与风险',({risk:'风险退出后补仓',rank:'按排名换仓',hybrid:'排名与风险调仓'})[config.rebalanceMode]+'；每 '+config.rebalanceEvery+' 日调整；止损 '+thisPercent(config.stopLossPct)+'；止盈 '+thisPercent(config.takeProfitPct)+'；峰值回撤 '+thisPercent(config.trailingStopPct)]];
    for(const pool of ['base','trend','structure','volume']){
      const values=pool==='base'?config.universe||{}:p[pool]||{};
      const parts=(finePoolFields[pool]||[]).filter(([key])=>numeric(values[key])).map(([key,label])=>label+' '+display(key,values[key]));
      if(pool==='trend'&&values.rankEnabled)parts.unshift(values.rankWindow+' 日涨幅排名 '+values.rankStart+' — '+values.rankEnd+'（全基础合格范围）');
      if(pool==='structure'){
        parts.unshift('峰值使用'+(values.peakBasis==='high'?'最高价':'最高收盘价'));
        if(numeric(values.minMa10Above20Days)||numeric(values.minMa5Above10Days))parts.push(values.maDaysMode==='consecutive'?'截至当日连续满足（最多 20 天）':'近 20 天累计满足');
        if(values.requireAboveMa20)parts.push('收盘价高于 MA20');
      }
      if(pool==='volume')for(const [key,label] of [['excludeBlowoff','剔除成交量突增收阴'],['excludeDivergence','剔除上涨缩量背离'],['excludeUpperShadow','剔除长上影收阴']])if(values[key])parts.push(label);
      items.push(['细条件 · '+poolLabels[pool],parts.join('；')||'未启用']);
    }
    if(poolOnly)return panel('股票池条件',table(['设置','本次取值'],items.filter(([label])=>label.startsWith('细条件 · ')||['区间','股票池范围','基础资格','趋势池','回调结构池','量能池','叠加筛选池'].includes(label)).map(([label,value])=>`<tr><td>${E(label)}</td><td>${E(value)}</td></tr>`))+note('仅按截止日的股票池条件生成名单，按当日成交额降序、代码升序排列。'));
    if(config.strategies?.length){
      const used=new Set(['base',...config.strategies.map(s=>s.pool)]);if(used.has('combined'))for(const key of p.combined?.pools||[])used.add(key);
      const labels={趋势池:'trend',回调结构池:'structure',量能池:'volume',叠加筛选池:'combined'};
      for(const item of items){const layer=labels[item[0]]||Object.entries(poolLabels).find(([,label])=>item[0]==='细条件 · '+label)?.[0];if(layer&&!used.has(layer))item[1]='未用于本次策略筛选；仅保存条件：'+item[1];}
    }
    for(const node of config.strategies||[]){const def=definition.strategies.find(s=>s.id===node.kind);items.push([def?.name||node.id,(pools[node.pool]||node.pool)+' · '+Object.entries(node.params||{}).map(([key,value])=>(def?.parameters.find(v=>v.key===key)?.label||key)+' '+(isPercent(key)?thisPercent(value):n(value))).join('；')]);}
    for(const node of config.combinations||[])items.push([strategyName(node.id),({intersection:'子策略交集',union:'子策略并集',weighted:'标准分加权'})[node.method]+' · '+node.children.map(child=>strategyName(child.id)+' 权重 '+n(child.weight)).join('；')]);
    return panel('冻结配置与股票池参数',table(['设置','本次取值'],items.map(([label,value])=>`<tr><td>${E(label)}</td><td>${E(value)}</td></tr>`))+`<details class="rs-body"><summary>技术审计：完整参数与来源标识</summary><pre>${E(JSON.stringify({config,selection},null,2))}</pre></details>`);
  }
  const thisPercent=value=>numeric(value)?p(value*100):'—';
  function regimeTable(r) {
    const rows=(r.regimeMetrics||r.metrics?.regimeMetrics||[]).map(v=>`<tr><td>${E(String(v.regime).split('/').map(readable).join(' / '))}</td><td>${n(v.sampleDays,0)}</td><td>${p(v.subsampleReturnPct)}</td><td>${p(v.benchmarkReturnPct)}</td><td>${p(v.conditionalAnnualizedReturnPct??v.annualizedReturnPct)}</td><td>${p(v.maxDrawdownPct)}</td><td>${n(v.sharpe)}</td><td>${p(v.winRatePct)}<small>${n(v.closedTradeCount,0)} 个波段</small></td><td>${p(v.turnoverPct)}</td><td>${n(v.totalFees)}</td><td>${n(v.totalSlippage)}</td><td>${p(v.costRatioPct)}</td></tr>`);
    return panel('按真实市场环境分组的收益',table(['前一收盘市场环境','样本日','子样本复合收益','基准收益','条件年化','条件回撤','条件夏普','完整波段胜率','换手','费用（元）','滑点（元）','成本占比'],rows)+`<p class="rs-help pf-metric-note">环境由所选真实指数的前一收盘状态确定。分组可能跨越不连续日期，条件年化不是一条连续交易策略的年化收益；条件回撤与夏普按该子样本计算。完整交易按退出日环境归属，成本与换手以初始资金为分母。</p>`);
  }
  function equityChart(r,title='账户净值与回撤',date='',detail='') {
    const rows=r?.curve||[],equities=rows.flatMap(v=>[v.equity,v.benchmarkEquity].filter(numeric).map(Number));if(!equities.length)return note('尚无实际账户曲线。');
    const path=r.pagination?.path||'',index=Math.max(0,rows.findIndex(row=>row.date===date)),selected=rows[index];
    const low=Math.min(...equities),high=Math.max(...equities),span=Math.max(1,high-low),actualMaxDd=Math.max(0,...rows.map(v=>numeric(v.drawdownPct)?Number(v.drawdownPct):0)),maxDd=Math.max(1,actualMaxDd);
    const x=i=>48+i*920/Math.max(1,rows.length-1),y=v=>160-(v-low)/span*130;
    const line=(key,project)=>{let started=false;return rows.map((v,i)=>{if(!numeric(v[key])){started=false;return '';}const segment=`${started?'L':'M'}${x(i).toFixed(2)},${project(Number(v[key])).toFixed(2)}`;started=true;return segment;}).join(' ');};
    const nav=(action,label,disabled)=>`<button type="button" class="rs-button" data-pf-action="${action}" data-pf-path="${E(path)}" ${disabled?'disabled':''}>${label}</button>`;
    return panel(title,`<div class="rs-body"><div class="pf-curve-toolbar"><div><strong>点击曲线，查看当天做了什么</strong><p class="rs-help">选中日期后，下方直接显示成交、持仓和收盘计划；方向键也可切换日期。</p></div><div class="rs-actions">${nav('previous-day','上一日',index<=0)}<label class="pf-date-picker">查看日期<select data-pf-date-select data-pf-path="${E(path)}" aria-label="查看哪一天的操作">${rows.map(row=>`<option value="${E(row.date)}" ${row.date===date?'selected':''}>${E(row.date)}</option>`).join('')}</select></label>${nav('next-day','下一日',index>=rows.length-1)}</div></div><svg viewBox="0 0 1000 290" class="pf-equity" data-pf-chart="${E(path)}" role="slider" tabindex="0" aria-label="逐日账户净值与回撤，左右方向键查看当日操作" aria-valuemin="0" aria-valuemax="${rows.length-1}" aria-valuenow="${index}" aria-valuetext="${E(selected.date)}，净值 ${n(selected.equity)} 元"><text x="48" y="18" fill="currentColor">净值（元） · 蓝色账户 / 灰色已选真实基准</text><line x1="48" y1="160" x2="968" y2="160" stroke="currentColor" opacity=".2"/><path d="${line('equity',y)}" class="pf-account-curve" fill="none" stroke="var(--accent,#68a9ff)" stroke-width="2.5"/><path d="${line('benchmarkEquity',y)}" class="pf-benchmark-curve" fill="none" stroke="currentColor" opacity=".45" stroke-width="1.5"/><text x="48" y="190" fill="currentColor">回撤（%） · 最大 ${n(actualMaxDd)}%</text><path d="${line('drawdownPct',v=>205+v/maxDd*45)}" class="pf-drawdown-curve" fill="none" stroke="#e6ac69" stroke-width="2"/><line class="pf-selected-guide" x1="${x(index)}" x2="${x(index)}" y1="26" y2="256"/>${numeric(selected.equity)?`<circle class="pf-selected-point" cx="${x(index)}" cy="${y(Number(selected.equity))}" r="4.5"/>`:''}<text x="48" y="280" fill="currentColor">${E(rows[0].date)}</text><text x="968" y="280" text-anchor="end" fill="currentColor">${E(rows.at(-1).date)}</text><rect x="32" y="24" width="952" height="238" fill="transparent" class="pf-curve-hit"/></svg><p class="rs-help">净值范围 ${n(low)} — ${n(high)} 元；末日按收盘估值，未强行平仓。缺失点断开显示。</p></div>${detail}`);
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
  function riskExplanation(row){
    const v=row.riskEvidence;if(!v)return operationText(row.reason);
    const observed=v.observedDate?'（'+v.observedDate+' 收盘）':'';
    if(row.reason==='stopLoss')return `较风控基准价变动 ${thisPercent(v.gainPct)}，达到止损 ${thisPercent(v.stopLossPct)}${observed}`;
    if(row.reason==='trailingStop')return `较持仓收盘峰值回落 ${thisPercent(numeric(v.drawdownFromPeakPct)?Math.abs(v.drawdownFromPeakPct):null)}，达到回撤线 ${thisPercent(v.trailingStopPct)}${observed}`;
    if(row.reason==='takeProfit')return `较风控基准价上涨 ${thisPercent(v.gainPct)}，达到止盈 ${thisPercent(v.takeProfitPct)}${observed}`;
    if(row.reason==='maxHoldingDays')return `已持有 ${n(v.holdingDays,0)} 个交易日，达到上限 ${n(v.maxHoldingDays,0)} 日${observed}`;
    return operationText(row.reason);
  }
  function positionControlView(day,r,point){
    const c=day.positionControl,holdings=day.holdings||[],config=r.config||{};
    const equity=c?.equity??day.equity??point.equity,cash=c?.cash??day.cash??point.cash;
    const value=c?.stockMarketValue??(holdings.every(row=>numeric(row.marketValue))?holdings.reduce((sum,row)=>sum+Number(row.marketValue),0):null);
    const weight=c?.stockWeight??(numeric(value)&&numeric(equity)&&equity>0?value/equity:null);
    const amount=v=>numeric(v)?n(Number(v)/10000,4)+' 万元':'未记录';
    const card=(label,value,note)=>`<div class="pf-position-metric"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`;
    const mode=c?.rebalanceMode??config.rebalanceMode,modeText={risk:'风险退出后补仓',rank:'按排名调仓',hybrid:'排名调仓与风险退出'}[mode]||'原报告未记录';
    const maxPositions=c?.maxPositions??config.maxPositions,maxWeight=c?.maxStockWeight??config.maxStockWeight;
    const limits=`最多持有 ${n(maxPositions,0)} 只；新买单股目标上限 ${thisPercent(maxWeight)}。股价变动可能使实际仓位超过这个目标比例。`;
    let details=`<p class="rs-help">调仓方式：${E(modeText)}；${c?c.rebalanceDueToday?'今天到达定期调仓日。':'今天未到定期调仓日。':'历史报告未记录本日调仓状态。'}${c?.nextScheduledRebalanceDate?'下次定期调仓判断：'+E(c.nextScheduledRebalanceDate)+' 收盘。':''}</p>`;
    const weights=day.strategyBudgetWeights||day.strategyWeights;
    if(weights)details+=`<p class="rs-help">策略资金分配：${E(percentageWeights(weights))}。同股预算先合并，再检查单股目标上限和持股名额；未分配的预算保留为现金。</p>`;
    if(c&&(c.reservedActionCash||c.receivables||c.payables||c.preExRightsValue))details+=`<p class="rs-help">现金中预留 ${amount(c.reservedActionCash)}；应收 ${amount(c.receivables)}；应付 ${amount(c.payables)}；其他已登记权益 ${amount(c.preExRightsValue)}。预留资金与未到账权益不能用于新买入。</p>`;
    if(day.clippedTargets?.length)details+=`<p class="rs-help">本日选股中有 ${day.clippedTargets.length} 只因持股名额上限未纳入目标；候选排名不等于买入安排。</p>`;
    return `<section class="pf-day-section" data-pf-position-control><h4>仓位与资金控制</h4><div class="pf-position-grid">${card('账户总资产',amount(equity),'按本日收盘估值')}${card('实际股票仓位',thisPercent(weight),'股票市值 '+amount(value))}${card('可用于买入的现金',c?amount(c.availableCash):'旧报告未记录','现金余额 '+amount(cash))}${card('实际持股 / 数量上限',holdings.length+' / '+n(maxPositions,0)+' 只','新买单股目标上限 '+thisPercent(maxWeight))}</div><p class="pf-position-rule">${E(limits)}</p>${details}${(c?.valuationReliable??point.valuationReliable)===false?note('本日存在估值缺口，请结合价格日期和数据说明理解仓位比例。'):''}</section>`;
  }
  function nextSessionPlanView(plan,date,path){
    const rows=(plan.rows||[]).filter(row=>row.pending||row.currentQuantity>0),actions={open:'计划建仓',add:'计划加仓',reduce:'计划减仓',exit:'计划清仓',hold:'继续持有',rebalance:'开盘核算调整方向'};
    const pending=rows.filter(row=>row.pending),buy=pending.filter(row=>['open','add'].includes(row.action)),sell=pending.filter(row=>['reduce','exit'].includes(row.action)),review=pending.filter(row=>!['open','add','reduce','exit'].includes(row.action)),held=rows.filter(row=>!row.pending);
    const rowHtml=row=>{
      const frozen=numeric(row.targetQuantity),delta=row.quantityDelta;
      const quantity=frozen?`目标持有 <b>${n(row.targetQuantity,0)} 股</b><small>${delta>0?'拟买入 '+n(delta,0)+' 股':delta<0?'拟卖出 '+n(-delta,0)+' 股':'股数保持不变'}</small>`:`${numeric(row.targetWeight)?'目标占总资产 <b>'+thisPercent(row.targetWeight)+'</b>':'目标比例未记录'}<small>${numeric(row.targetBudget)?'本次预算 '+n(row.targetBudget)+' 元；':''}股数待开盘价格确定</small>`;
      const source=row.status==='carried'?'沿用 '+(row.signalDate||'此前')+' 收盘计划':row.status==='completed'?'此前目标股数已完成':row.status==='holding'?'本次没有待执行订单':(row.signalDate||date)+' 收盘生成';
      const status=row.executionStatus==='partial'?'上次只成交了一部分':row.executionStatus==='deferred'?'上次未能成交':row.pending?'尚未成交':'无待执行买卖';
      const blocked=row.lastBlockReason?`<small>最近尝试 ${E(row.lastAttemptDate||'日期未记录')}：${E(operationText(row.lastBlockReason))}；仍需等待可成交条件</small>`:'';
      const target=row.pending?quantity:`持有 ${n(row.currentQuantity,0)} 股<small>${row.status==='completed'?'既定股数已达到；无需重复买卖':'继续按退出规则管理'}</small>`;
      return `<tr data-pf-plan-row="${E(row.symbol)}"><td>${stock({...row,date})}</td><td><strong class="pf-operation-tag pf-operation-${row.action}">${E(actions[row.action]||'等待执行')}</strong><small>${E(status)}</small>${row.directionEstimated?'<small>方向按收盘估算</small>':''}</td><td>${n(row.currentQuantity,0)} 股<small>实际占总资产 ${thisPercent(row.currentWeight)}</small></td><td>${target}${row.pending&&frozen&&numeric(row.targetBudget)&&row.targetBudget>0?`<small>计划预算 ${n(row.targetBudget)} 元；执行以目标股数为准</small>`:''}</td><td>${E(riskExplanation(row))}<small>${E(source)}</small>${blocked}${row.valuationBasis==='lastAvailable'?`<small>估值沿用 ${E(row.priceDate||'日期未记录')}；方向仅供核对</small>`:''}</td></tr>`;
    };
    const group=(key,title,values,empty)=>`<section class="pf-plan-group" data-pf-plan-group="${key}"><h5>${title}<span>${values.length} 只</span></h5>${values.length?table(['股票','具体动作','当前持仓','执行目标','原因与计划状态'],values.map(rowHtml)):`<p class="pf-day-empty">${empty}</p>`}</section>`;
    const dateText=plan.nextSessionDate?'下一次开盘：'+plan.nextSessionDate:'下一交易日日期未包含在本报告日历中';
    return `<p class="pf-plan-date">${E(dateText)}${plan.nextSessionInBacktest===false?' · 回测区间外，尚无后续成交验证':''}</p><p class="rs-help">先尝试卖出，再用可用现金买入。已确定目标股数的延续订单保持原股数；新预算订单到开盘才核算股数。成交还需满足涨跌停、T+1、整手、资金及成交量限制。</p><div class="pf-plan-overview"><span>卖出 / 减仓 ${sell.length} 只</span><span>买入 / 加仓 ${buy.length} 只</span>${review.length?'<span>方向待定 '+review.length+' 只</span>':''}<span>继续持有 ${held.length} 只</span></div>${group('sell','① 计划减仓 / 清仓',sell,'下一交易日没有待执行的减仓或清仓。')}${group('buy','② 计划建仓 / 加仓',buy,'下一交易日没有待执行的建仓或加仓。')}${review.length?group('review','待开盘确认调整方向',review,''):''}${group('hold','③ 继续持有与管理',held,'没有需要继续持有的股票。')}${!pending.length?'<p class="rs-help">收盘计划快照中没有待执行买卖；已完成的历史目标不会再次列为新订单。</p>':''}`;
  }
  function dailyRightsView(day,date){
    if(!day.corporateActions?.length)return '';
    const names={record:'登记权益',ex:'除权及权益入账',cashReceived:'现金到账',cashPaid:'权益款项支付',subscriptionPaid:'配股认购支付',subscriptionDeclined:'未认购配股',refundReceivable:'认购退款待到账',unsupported:'权益资料不足'};
    return `<section class="pf-day-section" data-pf-day-rights><h4>分红、送配与权益变化</h4><p class="rs-help">下列是公司行动引起的账户变化，单独于买卖成交记录。</p>${table(['股票','权益事件','本日记录'],day.corporateActions.map(row=>{
      const parts=[];
      if(numeric(row.entitledQuantity))parts.push('登记基数 '+n(row.entitledQuantity,0)+' 股');
      if(row.bonusShares||row.rightsShares)parts.push('新增股份 '+n((row.bonusShares||0)+(row.rightsShares||0),0)+' 股');
      if(row.cashReceivable)parts.push('应收现金 '+n(row.cashReceivable)+' 元');
      if(row.subscriptionPayable)parts.push('认购应付 '+n(row.subscriptionPayable)+' 元');
      if(numeric(row.amount))parts.push('记录金额 '+n(row.amount)+' 元');
      return `<tr><td>${stock({...row,date})}</td><td>${E(names[row.stage]||operationText(row.stage))}</td><td>${parts.length?parts.map(v=>'<small>'+E(v)+'</small>').join(''):'见权益账本详细记录'}</td></tr>`;
    }))}</section>`;
  }
  function dayDetail(r,day,date,path) {
    const request=dayRequests.get(path),point=(r.curve||[]).find(row=>row.date===date)||{};
    const heading=`<div class="pf-day-heading"><div><span class="rs-help">曲线选中日期</span><h3>${E(date)} · 当日操作</h3></div><div class="pf-day-totals"><span>收盘净值 <b>${n(point.equity)} 元</b></span><span>当日收益 <b>${p(numeric(point.dailyReturn)?point.dailyReturn*100:null)}</b></span><span>回撤 <b>${p(point.drawdownPct)}</b></span></div></div>`;
    if(!day)return `<div class="pf-day-detail" data-pf-day-detail data-pf-path="${E(path)}" data-pf-date="${E(date)}" aria-live="polite" aria-busy="${!!request&&!request.error}">${heading}<p class="rs-help">${request?.error?'当日明细加载失败，请重试。':request?'正在加载这一天的成交、持仓和计划…':'这份报告没有该日明细，不能据此判断当天是否交易。'}</p>${request?.error?`<button type="button" class="rs-button" data-pf-action="select-day" data-pf-path="${E(path)}" data-pf-date="${E(date)}">重新加载当日明细</button>`:''}</div>`;
    const fills=day.fills||(r.trades||[]).filter(row=>row.date===date),activities=day.activities||(r.decisions||[]).filter(row=>row.date===date),holdings=day.holdings||[];
    const complete=Array.isArray(day.fills)&&Array.isArray(day.activities)||
      (!r.pagination?.eventDate||r.pagination.eventDate===date)&&(r.pagination?.trades?.total??Infinity)<=(r.trades?.length||0)&&(r.pagination?.decisions?.total??Infinity)<=(r.decisions?.length||0);
    const latestTargets=new Map();
    for(const row of activities){if(row.action!=='target')continue;if(['superseded','cancelled'].includes(row.status))latestTargets.delete(row.symbol);else if(row.status==='queued')latestTargets.set(row.symbol,row);}
    const bought=fills.filter(row=>row.side==='buy'),sold=fills.filter(row=>row.side==='sell'),queued=[...activities.filter(row=>row.status==='queued'&&row.action!=='target'),...latestTargets.values()];
    const empty=text=>`<p class="pf-day-empty">${E(text)}</p>`;
    const section=(title,body)=>`<section class="pf-day-section"><h4>${title}</h4>${body}</section>`;
    const fillRows=fills.map(row=>{
      const end=holdings.find(v=>v.symbol===row.symbol),partial=activities.find(v=>v.status==='filled'&&v.symbol===row.symbol&&v.action===row.side&&v.quantity===row.quantity&&v.unfilledQuantity>0);
      const amount=numeric(row.amount)?Number(row.amount):numeric(row.quantity)&&numeric(row.price)?row.quantity*row.price:null;
      const cashChange=numeric(amount)&&numeric(row.fee)?(row.side==='buy'?-amount-row.fee:amount-row.fee):null;
      const unfilled=row.unfilledQuantity??partial?.unfilledQuantity;
      const action=({open:'建仓买入',add:'加仓买入',reduce:'减仓卖出',exit:'清仓卖出'})[row.positionAction]||(row.side==='buy'?'买入':'卖出');
      const beforeAfter=numeric(row.quantityBefore)&&numeric(row.quantityAfter)?`<b>${n(row.quantityBefore,0)} → ${n(row.quantityAfter,0)} 股</b><small>本笔成交前 → 成交后</small>`:'<small>旧记录未保存本笔前后股数</small>';
      return `<tr data-pf-fill-row="${E(row.symbol)}"><td>${stock({...row,date})}</td><td><strong class="pf-operation-tag pf-operation-${E(row.positionAction||row.side)}">${action}</strong><small>已模拟成交</small></td><td>${n(row.quantity,0)} 股 × ${n(row.price,4)} 元<small>成交额 ${n(amount)} 元 · 费用 ${n(row.fee)} 元</small><small>资金${cashChange<0?'净支出':'净流入'} ${n(numeric(cashChange)?Math.abs(cashChange):null)} 元</small>${numeric(row.cashAfter)?`<small>本笔成交后现金 ${n(row.cashAfter)} 元</small>`:''}${row.side==='sell'&&numeric(row.realizedPnl)?`<small>本笔已实现盈亏 ${n(row.realizedPnl)} 元</small>`:''}</td><td>${beforeAfter}<small>${end?'收盘持有 '+n(end.quantity,0)+' 股':'收盘已无持仓'}</small>${unfilled>0?`<small>本笔仍有 ${n(unfilled,0)} 股未成交</small>`:''}</td><td>${E(riskExplanation(row))}<small>${E(row.signalDate||'未提供')} 收盘信号 → ${E(date)} 开盘成交</small></td></tr>`;
    });
    const holdingRows=holdings.map(row=>{
      const buy=bought.some(v=>v.symbol===row.symbol),sell=sold.some(v=>v.symbol===row.symbol),exit=queued.find(v=>v.symbol===row.symbol&&(v.action==='sell'||v.action==='target'&&v.targetWeight===0));
      const plan=day.nextSessionPlan?.rows?.find(v=>v.symbol===row.symbol),willExit=plan?plan.pending&&plan.action==='exit':exit;
      return `<tr data-pf-holding-row="${E(row.symbol)}"><td>${stock({...row,date})}</td><td>${n(row.quantity,0)} 股${numeric(row.nextSessionSellableQuantity)?`<small>下个已知交易日可卖 ${n(row.nextSessionSellableQuantity,0)} 股</small>`:''}</td><td>${numeric(row.costPrice)?n(row.costPrice,4)+' 元':'未记录'}<small>估值价 ${numeric(row.markPrice)?n(row.markPrice,4)+' 元':'未记录'}</small>${row.priceDate&&row.priceDate!==date?`<small>沿用 ${E(row.priceDate)} 价格</small>`:''}</td><td>${n(row.marketValue)} 元<small><b>实际仓位 ${thisPercent(row.weight)}</b></small></td><td>${numeric(row.unrealizedPnl)?n(row.unrealizedPnl)+' 元':'未记录'}<small>浮动收益率 ${p(row.unrealizedPnlPct)}</small>${numeric(row.holdingDays)?`<small>已持有 ${n(row.holdingDays,0)} 个交易日</small>`:''}</td><td>${buy&&sell?'本日买卖后仍持有':buy?'本日买入后持有':sell?'本日卖出部分后仍持有':complete?'继续持有，本日无买卖成交':'已持仓；当日成交记录可能不完整'}${willExit?'<small>收盘已计划退出，尚未卖出</small>':''}</td></tr>`;
    });
    // A target is a close-time budget, not a filled order; risk exits take priority.
    const plans=queued.filter(row=>!(row.action==='target'&&row.targetWeight===0&&queued.some(v=>v.symbol===row.symbol&&v.action==='sell')));
    const planRows=plans.map(row=>{
      const held=holdings.find(v=>v.symbol===row.symbol),target=row.action==='target';
      const action=row.action==='sell'||target&&row.targetWeight===0?'计划退出持仓':target?(held?'调整持仓目标':'计划建仓'):operationText(row.action);
      return `<tr><td>${stock({...row,date})}</td><td>${E(action)}<small>尚未成交</small></td><td>${target?'目标占总资产 '+thisPercent(row.targetWeight):held?'拟卖出持仓 '+n(held.quantity,0)+' 股':'按退出规则执行'}<small>${held?'当前持有 '+n(held.quantity,0)+' 股':'当前未持仓'}；下次开盘核算可成交股数</small></td><td>${E(operationText(row.reason))}<small>信号产生于 ${E(row.signalDate||row.date||date)} 收盘</small></td></tr>`;
    });
    const issues=activities.filter(row=>!['filled','queued'].includes(row.status)&&row.action!=='hold'&&!(row.action==='valuation'&&row.status==='held'));
    const issueRows=issues.map(row=>`<tr><td>${stock({...row,date})}</td><td>${E(operationText(row.action))}<small>${E(operationText(row.status))}</small></td><td>${E(operationText(row.reason))}${numeric(row.requestedQuantity)?`<small>本次尝试 ${n(row.requestedQuantity,0)} 股，未成交</small>`:''}${numeric(row.unfilledQuantity)?`<small>未完成 ${n(row.unfilledQuantity,0)} 股</small>`:''}${row.signalDate?`<small>原信号日期 ${E(row.signalDate)}</small>`:''}</td></tr>`);
    const next=(r.curve||[]).find(row=>row.date>date)?.date;
    const summary=complete?(fills.length?`买入 ${bought.length} 笔 · 卖出 ${sold.length} 笔 · 收盘持有 ${holdings.length} 只股票`:`本日没有买入或卖出成交；${holdings.length?'继续持有 '+holdings.length+' 只股票':'收盘为空仓'}`):'旧报告当前页可能未包含全部当日操作，请查看当日完整操作路径。';
    const planBody=day.nextSessionPlan?nextSessionPlanView(day.nextSessionPlan,date,path):
      note('旧报告未保存完整的跨日计划快照。以下仅列当日新增记录；此前尚未完成的订单可能未列出，重新回测可获得完整计划。')+
      `<p class="rs-help">${next?'最早于 '+E(next)+' 开盘尝试执行。':'最早于回测区间之后的下一交易日开盘尝试；本报告未包含后续成交。'}目标比例是股票市值占账户总资产的比例；实际股数按开盘价格、整手、资金和交易限制核算。</p>`+
      (planRows.length?table(['股票','收盘决定','计划内容','为什么这样安排'],planRows):empty(complete?'本日没有新增买卖计划。':'当前页没有计划记录，完整情况请查看操作路径。'))+
      (!plans.length&&day.targetSignalDate?`<p class="rs-help">系统保留 ${E(day.targetSignalDate)} 的收盘目标；它可能已经完成，是否还需买卖以执行记录为准。</p>`:'');
    return `<div class="pf-day-detail" data-pf-day-detail data-pf-path="${E(path)}" data-pf-date="${E(date)}" aria-live="polite">${heading}<p class="pf-day-summary">${E(summary)}</p>${positionControlView(day,r,point)}${section('今日已成交',fillRows.length?table(['股票','具体操作','成交与资金变化','本笔持仓变化','操作原因与信号来源'],fillRows):empty(complete?'今天没有成交。':'当前页没有成交记录，不能确认全天无交易。'))}${section('收盘持仓（实际持仓）',holdingRows.length?table(['股票','持股与可卖数量','账面成本 / 估值价','市值与实际仓位','浮盈亏 / 持有天数','本日变化'],holdingRows)+'<p class="rs-help">成本按本次回测账本计算；浮动盈亏尚未实现。可卖股数仅反映持仓锁定规则，实际成交还受停牌、涨跌停等限制。</p>':empty('收盘为空仓，没有持仓股票。'))}${dailyRightsView(day,date)}${section('下一交易日计划',planBody)}${issues.length?section('未成交、计划变更与数据说明',table(['股票','记录状态','原因与数量'],issueRows)):''}<div class="pf-day-footer"><button type="button" class="rs-button" data-pf-action="operations-day" data-pf-date="${E(date)}" data-pf-path="${E(path)}">查看当日完整操作路径</button><span class="rs-help">以上均为本次回测的模拟记录。</span></div></div>`;
  }
  function focusChart(path) { [...(document.querySelectorAll?.('[data-pf-chart]')||[])].find(el=>el.dataset.pfChart===path)?.focus?.({preventScroll:true}); }
  function selectDay(path,date,focus=false) {
    const r=chartRuns.get(path);if(!r||!(r.curve||[]).some(row=>row.date===date))return;
    selectedDates.set(path,date);
    const local=(r.daily||[]).findIndex(row=>row.date===date);
    if(local>=0){dayIndices.set(path,local);selectedDaily.set(path,r.daily[local]);dayRequests.delete(path);hooks.refresh();if(focus)focusChart(path);return;}
    if(!hooks?.dailyPage){hooks.refresh();return;}
    const index=r.curve.findIndex(row=>row.date===date),size=r.pagination?.daily?.pageSize||20,request={date};
    dayRequests.set(path,request);hooks.refresh();if(focus)focusChart(path);
    Promise.resolve().then(()=>dayRequests.get(path)===request?hooks.dailyPage(Math.floor(index/size)*size,path):null).then(reply=>{
      if(dayRequests.get(path)!==request)return;
      // A second chart can become the active server page while this request is
      // in flight. Keep this chart's requested day without replacing that page.
      let run=reply?.result;for(const key of path?path.split('.'):[])run=run?.[key];
      const loaded=run?.daily?.find(row=>row.date===date)||chartRuns.get(path)?.daily?.find(row=>row.date===date);
      if(loaded)selectedDaily.set(path,loaded);
      if(selectedDaily.get(path)?.date===date)dayRequests.delete(path);else request.error=true;
      hooks.refresh();if(focus)focusChart(path);
    }).catch(error=>{if(dayRequests.get(path)===request){request.error=true;hooks.error(error);hooks.refresh();if(focus)focusChart(path);}});
  }
  function engineResult(r,{nested=false,label='当前组合'}={}) {
    const daily=r.daily||[],path=r.pagination?.path||'',curve=r.curve||[];
    chartRuns.set(path,r);
    let index=Math.max(0,Math.min(dayIndices.get(path)||0,daily.length-1));
    const previous=selectedDates.get(path),date=curve.some(row=>row.date===previous)?previous:daily[index]?.date||curve[0]?.date||'';
    selectedDates.set(path,date);
    const loaded=daily.find(row=>row.date===date);if(loaded)selectedDaily.set(path,loaded);
    const day=loaded||(selectedDaily.get(path)?.date===date?selectedDaily.get(path):null),strategies=day?.strategies||day?.topNByStrategy||{};
    if(loaded)dayIndices.set(path,daily.indexOf(loaded));
    const rows=[];
    for(const [id,value] of Object.entries(strategies)) {
      const stocks=Array.isArray(value)?value:value.topN||value.candidates||[];
      stocks.forEach((v,i)=>{
        const weight=day?.targetWeights?.[v.symbol],fresh=(day?.activities||[]).some(row=>row.symbol===v.symbol&&row.action==='target'&&row.status==='queued');
        const plan=weight>0?`${fresh?'本日收盘目标':'沿用收盘目标'}：占总资产 ${p(weight*100)}<small>${fresh?'尚未成交，开盘再核算股数':E(day.targetSignalDate||'日期未提供')+' 的目标；是否还需交易见执行记录'}</small>`:'候选观察，尚非买入目标<small>进入策略排名不代表组合会买入</small>';
        rows.push(`<tr><td>${E(strategyName(id))}</td><td>${i+1}</td><td>${stock({...v,date})}</td><td>${n(v.rawScore,5)}<small>排名标准分 ${n(v.score,5)}</small></td><td>${plan}</td></tr>`);
      });
    }
    const weights=day?.strategyBudgetWeights||day?.strategyWeights||day?.allocationWeights||{};
    const coverage=r.universeCoverage||r.quality?.coverage?.sourceCoverage||r.quality?.universeCoverage||{};
    const page=r.pagination?.daily||{offset:0,total:daily.length,hasMore:false};
    const action=(name,title,offset,disabled)=>`<button class="rs-button" type="button" data-pf-action="${name}" data-pf-path="${E(path)}" data-pf-offset="${offset}" ${disabled?'disabled':''}>${title}</button>`;
    const regime=day?.regime,status=regime?.status==='observed'?`趋势 ${readable(regime.trend)} · 波动 ${readable(regime.volatility)}`:'未知（缺少所选真实指数的足够历史）';
    const dailyBody=day?`<div class="rs-body"><p><strong>${E(date)} 收盘选股</strong> · 以下是选股依据，实际成交与持仓见上方当日操作。</p><p class="rs-help">预算权重：${E(percentageWeights(weights))}。预算用于入场分配，实际持仓权重另列。原始分是策略评分，排名标准分用于统一排序，均不是预期收益。</p><p class="rs-help">基础可交易 ${n(day?.coverage?.eligibleSecurities,0)} 只 · 趋势 ${n(day?.poolCounts?.trend,0)} 只 · 回调结构 ${n(day?.poolCounts?.structure,0)} 只 · 量能 ${n(day?.poolCounts?.volume,0)} 只。</p><p class="rs-help">市场环境：${E(status)}。${day?.rotationDecision?'本日轮动：'+E(readable(day.rotationDecision.reason||day.rotationDecision.status)):'当日没有轮动调整记录。'}</p></div>${table(['策略','排名','股票（点击看日线）','策略评分','是否纳入组合目标'],rows)}<div class="rs-body rs-actions"><span class="rs-help">已加载第 ${daily.length?page.offset+1:0} — ${page.offset+daily.length} 日，共 ${page.total} 日；可直接在曲线上选择任意日期。</span>${action('previous-page','前一页记录',Math.max(0,page.offset-(page.pageSize||daily.length||20)),!hooks?.dailyPage||page.offset<=0)}${action('next-page','后一页记录',page.offset+daily.length,!hooks?.dailyPage||!page.hasMore)}</div><details class="rs-body"><summary>技术审计（JSON，可选）</summary><pre>${E(JSON.stringify(day,null,2))}</pre></details>`:`<div class="rs-body">${note('选中日期的明细尚未加载。')}</div>`;
    const body=(!nested&&r.rotation?.enabled&&r.rotation?.diagnosticOnly?note('本任务为轮动执行诊断，尚未经过持续性准入与样本外双基线验收；不能据此认定轮动有效。'):'')+metricTable([[label,r],...(r.benchmarkMetrics?[["所选业绩基准",{metrics:r.benchmarkMetrics}]]:[])])+equityChart(r,label+' · 账户净值与回撤',date,dayDetail(r,day,date,path))+panel('收盘选股依据 · 与曲线日期联动',nested?`<details ${detailAttrs('daily:'+path)}><summary class="rs-body">展开逐日候选与策略预算</summary>${dailyBody}</details>`:dailyBody)+rotationLog(r);
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
    return (r.config?.strategies&&r.experiment?panel('本次测试的参数与固定条件',`<div class="rs-body">${scanPlanView(r.config,r.experiment.axes,r.experiment)}</div>`):'')+panel((r.kind||r.type)==='systemPlateau'?'全系统收益平原':'滚动样本外验证',`<div class="rs-body"><p>参数组合总数：${n(r.trialCount,0)} · 逐折候选评估：${n(r.actualCandidateEvaluations,0)} / ${n(r.plannedCandidateEvaluations,0)} · 账户模拟次数：${n(r.engineRunCount,0)} · 滚动折数：${n(folds.length,0)}</p>${note(r.conclusion||plateau.conclusion||'等待完成预设研究；阶段结果不能作为最终结论。')}<p class="rs-help">各折仅用训练数据选择设置，后续样本外冻结。没有通过跨年份、跨市场环境和数据质量门槛时，不给出推荐参数区间。</p></div>`)+panel('参数邻域与平原区域',`<div class="rs-body">${heatmap(plateau,r.experiment?.axes)}</div>`)+panel('推荐参数区间与样本外证据',`<div class="rs-body">${note(plateau.certified?'通过本次预设验证门槛。':'尚未获得经过验证的收益平原。')}${(plateau.reasons||[]).map(reason=>note(readable(reason))).join('')}${ranges.length?table(['参数层','参数','验证通过的取值区间'],rows):'<p>暂无推荐区间。</p>'}<p class="rs-help">推荐区间仅在全部离散网格交叉组合都已验证时输出；未扫描的连续插值不算已验证。</p><details><summary>查看分年份、分环境及邻域验证记录</summary><pre>${E(JSON.stringify(plateau,null,2))}</pre></details></div>`)+panel('逐折训练、检验与样本外账户',foldPanels||note('尚无已完成的滚动折。'))+panel('完整研究设置与数据边界',`<details class="rs-body"><summary>展开冻结配置、研究规模与警告</summary><pre>${E(JSON.stringify({experiment:r.experiment,config:r.config,quality:r.quality,warnings:r.warnings,protocol:r.protocol},null,2))}</pre></details>`);
  }
  function singlePlateauResult(r){
    const candidates=r.candidates||[],regions=r.regions||[],axes=r.axes||r.experiment?.axes||[],x=r.experiment||{},period=r.developmentPeriod||r.config||{};
    return panel('关键参数平原 · 开发期比较','<div class="rs-body">'+note('这份报告只比较开发样本。请选择具体参数点并冻结，再使用开发截止日之后的新时段验证；不会自动采用收益最高点。')+'<p>已完成 '+n(r.completedTrials??candidates.length,0)+' / '+n(r.plannedTrials??axes.reduce((count,axis)=>count*axis.values.length,1),0)+' 组 · 扫描 '+axes.length+' 个关键参数 · '+(r.status==='completed'?'全部计算完成':'计算进行中')+'。</p><p>开发区间：<strong>'+E(period.startDate||'未记录')+' — '+E(period.endDate||'未记录')+'</strong>。其余股票池、成本和交易规则保持固定。</p><p class="rs-help">邻域通过表示该点附近的已计算候选达到本次固定门槛，不等于未来有效；没有满足门槛的区域也会如实保留。</p><details data-pf-single-thresholds><summary>本次固定的开发期判定门槛与扫描轴</summary>'+table(['条件','本次设定'],[['每点最少已关闭持仓波段',n(x.minClosedTrades,0)+' 个'],['每点最大回撤上限',p(x.maxDrawdownPct)],['邻域最少合格参数点',n(x.minNeighborhoodSize,0)+' 点'],['邻域正收益比例下限',p(numeric(x.minNeighborPositiveFraction)?x.minNeighborPositiveFraction*100:null)],['邻域收益标准差上限',n(x.maxNeighborhoodStdPct)+' 个百分点'],...axes.map(axis=>[parameterLabel(axis.path),(axis.values||[]).map(value=>axisValueText(axis.path,value)).join('、')])].map(([label,value])=>'<tr><td>'+E(label)+'</td><td>'+E(value)+'</td></tr>'))+'<p class="rs-help">每条轴最多取 3 个相邻的已测试值，交叉形成邻域；边界向内平移。不补造未测试点，也不把两点比较标成稳定平原。未通过数据检查或单点门槛的候选不计为合格点。</p></details></div>')+
      panel('查看邻近参数，再明确选择冻结点','<div class="rs-body pf-single-candidates">'+candidates.map(candidate=>{const m=candidate.metrics||{},a=candidate.neighborhood||{},value=numeric(m.totalReturnPct)?Number(m.totalReturnPct):null;return '<article class="pf-candidate '+(a.passed?'is-stable':'')+'" data-pf-candidate="'+E(candidate.candidateId)+'"><header><strong>'+E(candidate.candidateId)+'</strong><span>'+(a.passed?'开发邻域通过':'开发邻域未通过')+'</span></header><p class="pf-candidate-values">'+Object.entries(candidate.params||{}).map(([path,v])=>'<span>'+E(parameterLabel(path))+'<b>'+E(axisValueText(path,v))+'</b></span>').join('')+'</p><div class="pf-candidate-metrics"><div><span>本点净收益</span><strong>'+p(value)+'</strong></div><div><span>最大回撤</span><strong>'+p(m.maxDrawdownPct)+'</strong></div><div><span>已关闭波段</span><strong>'+n(m.closedTradeCount,0)+'</strong></div></div><p class="rs-help">邻域 '+n(a.size,0)+' 点 · 平均净收益 '+p(a.meanNetReturnPct)+' · 标准差 '+n(a.stdNetReturnPct)+' 个百分点 · 正收益占比 '+p(numeric(a.positiveFraction)?a.positiveFraction*100:null)+'</p>'+((candidate.qualityFailures||[]).length?'<p class="pf-config-error">'+E(candidate.qualityFailures.map(readable).join('；'))+'</p>':'')+'<details><summary>查看将冻结的完整配置</summary>'+configuration(candidate.config||r.config||{},r.selection||{})+'</details><button type="button" class="rs-button '+(a.passed?'rs-primary':'')+'" data-pf-action="freeze-candidate" data-pf-candidate="'+E(candidate.candidateId)+'" '+(!hooks?.freeze||r.status!=='completed'?'disabled':'')+'>'+(r.status==='completed'?'冻结这个参数点':'全部计算完成后可冻结')+'</button><button type="button" class="rs-button" data-pf-action="replay-candidate" data-pf-candidate="'+E(candidate.candidateId)+'" '+(!hooks?.replayCandidate||r.status!=='completed'?'disabled':'')+'>查看该点逐日买卖</button></article>';}).join('')+'</div>')+
      panel('开发期相邻区域','<div class="rs-body">'+(regions.length?table(['区域','已计算参数点','邻域均值','区域说明'],regions.map(region=>'<tr><td>'+E(region.regionId)+'</td><td>'+E((region.candidateIds||[]).join('、'))+'</td><td>'+p(region.meanNetReturnPct)+'</td><td>'+E(region.intervalEligible?'离散网格完整，仍需冻结后验证新时段':'仅显示已计算点，不将空洞或未扫描插值当成稳定区间')+'</td></tr>')):'<p>当前没有满足门槛的相邻区域。可以保留这次结果，重新提出假设；新增测试仍算开发研究。</p>')+'<a class="rs-button" data-nav="R10">查看冻结方案与新时段验证</a></div>');
  }
  function result(r) {
    if(!r)return note('正在准备数据或等待计算结果。');
    const taskId=hooks?.activeTaskId?.()||r.dataDigest||'';
    if(taskId!==resultTaskId){resultTaskId=taskId;dayIndices.clear();selectedDates.clear();selectedDaily.clear();dayRequests.clear();chartRuns.clear();}
    displayNames=r.securityNames||{};
    if((r.kind||r.type)==='poolPreview'){activateScope('research');screen.task=hooks?.activeTaskId?.()||screen.task;screen.result=r;return screenView(r,true);}
    if((r.kind||r.type)==='singlePlateau')return singlePlateauResult(r);
    if(experimentKinds.has(r.kind||r.type))return refinementEvidence(r)+experimentResult(r);
    if(r.kind==='comparison'||r.testing)return refinementEvidence(r)+studyResult(r);
    return refinementEvidence(r)+engineResult(r,{label:(r.kind||r.type)==='singleBacktest'?'单策略固定参数':(r.kind||r.type)==='frozenValidation'?'冻结方案 · 新时段':'当前组合'})+rightsTable(r)+regimeTable(r);
  }
  function set(path,value) {
    const keys=path.split('.');
    if (['market','board'].includes(keys[0])) {
      const field=keys[0]==='market'?'markets':'boards', current=draft.selection[field];
      draft.selection[field]=value?[...new Set([...current,keys[1]])]:current.filter(v=>v!==keys[1]);return;
    }
    let target=draft;for(const k of keys.slice(0,-1))target=target[k];target[keys.at(-1)]=value;
  }
  function capture(el) {
    if(!el?.dataset?.pfField||!draft)return;
    const path=el.dataset.pfField,match=path.match(/^scanAxes\.(\d+)\.path$/);
    if(path==='singleStrategy'){selectSingleStrategy(el.value);save();queueReview();return;}
    if(match&&draft.scanAxes[Number(match[1])]?.path!==el.value)draft.scanAxes[Number(match[1])].text='';
    set(path,el.dataset.pfType==='boolean'?el.checked:el.dataset.pfType==='text'?el.value:stored(path,el.value));save();queueReview();renderScanFeedback();
  }
  document.addEventListener('toggle',event=>{const key=event.target?.dataset?.pfDetail;if(key){event.target.open?expandedDetails.add(key):expandedDetails.delete(key);}},true);
  document.addEventListener('input',event=>{if(event.target.matches?.('[data-pf-field]'))capture(event.target);});
  document.addEventListener('change',event=>{if(event.target.dataset?.pfDateSelect!==undefined)selectDay(event.target.dataset.pfPath||'',event.target.value);});
  document.addEventListener('click',event=>{
    const svg=event.target.closest?.('[data-pf-chart]');if(svg?.dataset?.pfChart===undefined)return;
    const matrix=svg.getScreenCTM?.();if(!matrix)return;
    const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
    const local=point.matrixTransform(matrix.inverse());if(local.y<24||local.y>262)return;
    const rows=chartRuns.get(svg.dataset.pfChart)?.curve||[],index=Math.max(0,Math.min(rows.length-1,Math.round((local.x-48)/920*(rows.length-1))));
    if(rows[index]){event.preventDefault();selectDay(svg.dataset.pfChart,rows[index].date,true);}
  });
  document.addEventListener('keydown',event=>{
    if(event.target.dataset?.pfChart!==undefined&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      const path=event.target.dataset.pfChart,rows=chartRuns.get(path)?.curve||[],current=Math.max(0,rows.findIndex(row=>row.date===selectedDates.get(path)));
      const index=event.key==='Home'?0:event.key==='End'?rows.length-1:Math.max(0,Math.min(rows.length-1,current+(event.key==='ArrowLeft'?-1:1)));
      event.preventDefault();if(rows[index])selectDay(path,rows[index].date,true);return;
    }
    if(event.key!=='Enter'||!event.target.closest?.('[data-pf-form],[data-pf-pool-form]'))return;
    if(event.target.matches?.('[data-pf-screen-query]')){event.preventDefault();screen.query=event.target.value.trim();screen.offset=0;void loadScreen().catch(hooks.error);return;}
    if(scope==='market'){event.preventDefault();return;}
    if(currentStep()!==4){event.preventDefault();setStep(Math.min(4,currentStep()+1));hooks.refresh();}
  });
  document.addEventListener('change',event=>{const el=event.target;if(el.dataset?.pfHeatSlice){heatSlices.set(el.dataset.pfHeatSlice,el.value);hooks?.refresh();return;}
    if(el.dataset?.pfScanMethod!==undefined){
      const axis=draft.scanAxes[Number(el.dataset.pfScanMethod)],names={交集:'intersection',并集:'union',加权:'weighted'};
      const values=new Set(String(axis.text||'').split(/[\s,，;；、]+/).filter(Boolean).map(v=>names[v]||v));el.checked?values.add(el.value):values.delete(el.value);
      axis.text=[...values].join(', ');save();queueReview();hooks.refresh();return;
    }
    if(el.dataset?.pfOptional){
      const path=el.dataset.pfOptional;
      const spec=Object.entries(finePoolFields).flatMap(([pool,rows])=>rows.map(row=>({path:finePoolPath(pool,row[0]),reference:row[2]}))).find(row=>row.path===path);
      if(spec){set(path,el.checked?spec.reference:null);save();queueReview();hooks.refresh();}return;
    }
    if(el.dataset?.pfPoolLayer||el.dataset?.pfPoolMode!==undefined){
      const value=draft.config.poolSettings.combined;
      if(el.dataset.pfPoolLayer){const chosen=new Set(value.pools);el.checked?chosen.add(el.dataset.pfPoolLayer):chosen.delete(el.dataset.pfPoolLayer);value.pools=['trend','structure','volume'].filter(key=>chosen.has(key));}
      else value.mode=el.value==='union'?'union':'intersection';
      screen.pool='combined';screen.offset=0;save();queueReview();hooks.refresh();void loadScreen().catch(hooks.error);return;
    }
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
    if(el.tagName==='SELECT'||el.type==='checkbox'||el.dataset.pfNullable!==undefined)hooks?.refresh();
  });
  document.addEventListener('submit',event=>{
    if(scope==='market'){if(event.target.matches?.('[data-pf-pool-form]')){event.preventDefault();event.stopImmediatePropagation();}return;}
    if(!event.target.matches?.('[data-pf-form]'))return;event.preventDefault();event.stopImmediatePropagation();
    for(const el of event.target.querySelectorAll('[data-pf-field]'))capture(el);
    if(workflow.submitting)return;
    try {
      if(experimentKinds.has(draft.kind)){const state=scanState();if(!state.valid)throw new Error(Object.values(state.errors)[0]||state.message);}
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
    if(el.dataset.pfAction==='advanced'){advanced();AT.navigate?.('R03');hooks.refresh();return;}
    if(el.dataset.pfAction==='single-stage'){activateScope('research');setStep(el.dataset.pfStep);queueReview();AT.navigate?.('R03');hooks.refresh();return;}
    if(el.dataset.pfAction==='single-plateau'){activateScope('plateau');queueReview();AT.navigate?.('R05');hooks.refresh();return;}
    if(el.dataset.pfAction==='advanced-plateau'){activateScope('advancedPlateau');queueReview();AT.navigate?.('R05');hooks.refresh();return;}
    if(el.dataset.pfAction==='apply-pool-style'){
      const style=definition.poolStyles?.find(row=>row.id===el.dataset.pfStyle);if(!style)return;
      draft.config.poolSettings=structuredClone(style.poolSettings);for(const strategy of definition.strategies)if(draft.enabled[strategy.id])draft.pools[strategy.id]='combined';
      screen.pool='combined';screen.offset=0;save();queueReview();hooks.refresh();return;
    }
    if(el.dataset.pfAction==='freeze-candidate'){if(!hooks?.freeze)return;el.disabled=true;void Promise.resolve(hooks.freeze({id:hooks.activeTaskId?.(),candidateId:el.dataset.pfCandidate})).catch(hooks.error).finally(()=>{el.disabled=false;});return;}
    if(el.dataset.pfAction==='replay-candidate'){if(!hooks?.replayCandidate)return;el.disabled=true;void Promise.resolve(hooks.replayCandidate({id:hooks.activeTaskId?.(),candidateId:el.dataset.pfCandidate})).catch(hooks.error).finally(()=>{el.disabled=false;});return;}
    if(el.dataset.pfAction==='workflow-step'){setStep(el.dataset.pfStep);hooks.refresh();document.querySelector?.('.pf-workbench')?.scrollIntoView?.({block:'start',behavior:'instant'});return;}
    if(el.dataset.pfAction==='review-settings'){void reviewSettings().catch(()=>{});return;}
    if(['scan-example','plateau-example'].includes(el.dataset.pfAction)){
      if(!seedPlateauAxes(draft)){hooks.error(new Error(isSingle()?'请先选择一个策略。':'四层示例需要至少两个已启用的基础策略，请先到第 1 步选择策略。'));return;}
      save();queueReview();hooks.feedback(isSingle()?'已填入一个关键参数的邻近候选；其余配置保持固定。示例不是盈利推荐。':'已填入 16 组填写示例，并启用组合参与研究；固定参数保持原设置。示例不是盈利推荐。');hooks.refresh();return;
    }
    if(el.dataset.pfAction==='axis-example'){const axis=draft.scanAxes[Number(el.dataset.pfAxis)];if(axis?.path){axis.text=exampleValues(axis.path);save();queueReview();hooks.refresh();}return;}
    if(el.dataset.pfAction==='plateau-import'){
      const source=scopes[scope==='advancedPlateau'?'advanced':'research']?.draft;if(!source){hooks.error(new Error('请先在对应的固定参数或高级回测工作区保存研究草稿。'));return;}
      const targetKind=scope==='advancedPlateau'?'systemPlateau':'singlePlateau';
      draft=structuredClone(source);draft.kind=targetKind;draft.periodMode='dates';draft.name='';draft.workflowStep=1;
      draft.scanAxes=Array.from({length:targetKind==='singlePlateau'?3:6},()=>({path:'',text:''}));seedPlateauAxes(draft);editingTaskId=null;
      editorNote='已复制固定参数草稿。尚未完成回测的草稿不能代替对买卖逻辑的检查；原草稿保持独立。';save();queueReview();hooks.refresh();return;
    }
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
      if(screen.busy)return;
      try{const request=screenPayload(),signature=screenSignature(),target=screen,value=draft,name=scope;target.busy=true;hooks.refresh();void hooks.screenCreate(request).then(created=>{if(!created?.id)return;target.serial++;Object.assign(target,{task:created.id,meta:created,result:null,offset:0,query:'',signature});saveScope(name,value,target);if(target===screen)void loadScreen().catch(hooks.error);}).catch(hooks.error).finally(()=>{target.busy=false;hooks.refresh();});}catch(error){screen.busy=false;hooks.error(error);}return;
    }
    if(el.dataset.pfAction==='pool-all-a'){draft.selection={...draft.selection,mode:'all',source:'local',sourceId:null,symbols:[],markets:['sh','sz','bj'],boards:['main','chinext','star','bse']};draft.symbols='';save();queueReview();hooks.refresh();return;}
    if(el.dataset.pfAction==='pool-to-research'){
      const source=structuredClone(draft);activateScope('research');
      draft.selection=source.selection;draft.symbols=source.symbols;draft.periodMode='dates';
      for(const key of ['universe','poolSettings','startDate','endDate','lookbackDays'])draft.config[key]=source.config[key];
      if(source.periodMode!=='dates')draft.config.startDate=draft.config.endDate=null;
      for(const strategy of definition.strategies)if(draft.enabled[strategy.id])draft.pools[strategy.id]='combined';
      editingTaskId=null;editorNote='已带入独立股票池条件，当前单策略使用叠加筛选池；回测按每个交易日重新筛选。'+(source.periodMode==='dates'?'':'原股票池使用最近交易日，请先明确开发起止日期。');setStep(source.periodMode==='dates'?2:1);queueReview();AT.navigate?.('R03');return;
    }
    if(['screen-funnel','screen-combined'].includes(el.dataset.pfAction)){
      if(el.dataset.pfAction==='screen-funnel'){const index=['base','trend','structure','volume'].indexOf(el.dataset.pfStage);draft.config.poolSettings.combined={pools:['trend','structure','volume'].slice(0,Math.max(0,index)),mode:'intersection'};}
      screen.pool='combined';screen.offset=0;save();queueReview();hooks.refresh();void loadScreen().catch(hooks.error);return;
    }
    if(el.dataset.pfAction==='screen-pool'){screen.pool=el.dataset.pfPool;screen.offset=0;void loadScreen().then(()=>document.querySelector?.('[data-pf-screen-query]')?.scrollIntoView?.({block:'center'})).catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-page'){screen.offset=Number(el.dataset.pfOffset);void loadScreen().catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-retry'){void loadScreen().catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-search'){screen.query=document.querySelector('[data-pf-screen-query]')?.value.trim()||'';screen.offset=0;void loadScreen().catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-clear'){screen.query='';screen.offset=0;void loadScreen().catch(hooks.error);return;}
    if(el.dataset.pfAction==='screen-task'){hooks.openTask(screen.task);return;}
    if(el.dataset.pfAction==='operations-day'){hooks.operationsDay(el.dataset.pfDate,el.dataset.pfPath||'');return;}
    if(el.dataset.pfAction==='cancel-edit'){newTask(draft.kind);hooks.refresh();return;}
    if(el.dataset.pfAction==='import-history'){
      if(typeof window.atradeDesktop?.importResearchHistory!=='function'){hooks.error(new Error('请在桌面应用中导入历史数据包。'));return;}
      const targetDraft=draft,targetScreen=screen,targetScope=scope;
      importingHistory=true;hooks.refresh();
      void window.atradeDesktop.importResearchHistory().then(response=>{
        if(!response.ok)throw new Error(response.error||'历史数据包校验失败。');
        if(response.canceled)return;
        definition.historySources=response.data.sources;
        targetDraft.selection.source='history';targetDraft.selection.sourceId=response.data.imported.id;
        targetDraft.periodMode='dates';targetDraft.config.startDate=response.data.imported.coverage.startDate;targetDraft.config.endDate=response.data.imported.coverage.endDate;
        targetDraft.config.universe.strictHistory=true;saveScope(targetScope,targetDraft,targetScreen);hooks.feedback('历史包已校验并保存，已在发起导入的股票池中选择其覆盖日期。请核对股票池与权益模型后开始研究。');
      }).catch(hooks.error).finally(()=>{importingHistory=false;hooks.refresh();});return;
    }
    if(el.dataset.pfAction==='configure-plateau'){activateScope('plateau');queueReview();AT.navigate?.('R05');return;}
    if(el.dataset.pfAction==='reset'){newTask(draft.kind);hooks.refresh();}
    if(el.dataset.pfAction==='save'){save();hooks.feedback((isPlateau()?'收益平原':'回测')+'设置已保存在本机。');}
    if(el.dataset.pfAction==='rotation-page'){hooks?.rotationPage?.(Number(el.dataset.pfOffset),el.dataset.pfPath||'');}
    if(el.dataset.pfAction==='select-day')selectDay(el.dataset.pfPath||'',el.dataset.pfDate);
    if(['previous-day','next-day'].includes(el.dataset.pfAction)){
      const path=el.dataset.pfPath||'',rows=chartRuns.get(path)?.curve||[],current=Math.max(0,rows.findIndex(row=>row.date===selectedDates.get(path)));
      const index=current+(el.dataset.pfAction==='previous-day'?-1:1);if(rows[index])selectDay(path,rows[index].date);
    }
    if(['previous-page','next-page'].includes(el.dataset.pfAction)){
      const path=el.dataset.pfPath||'',row=chartRuns.get(path)?.curve?.[Number(el.dataset.pfOffset)];if(row)selectDay(path,row.date);
    }
  });
  function newTask(kind='singleBacktest'){activateScope(scopeFor(kind));draft=ensureCombined(makeDraft(definition,singleKinds.has(kind)));draft.kind=kind;if(['systemPlateau','singlePlateau'].includes(kind)){draft.periodMode='dates';seedPlateauAxes(draft);}editingTaskId=null;editorNote='';save();queueReview();}
  function advanced(){activateScope('advanced');queueReview();}
  function startSingle(task){if(task){editTask({...task,kind:'singleBacktest'},'copy');selectSingleStrategy(task.config.strategies[0].kind);}else activateScope('research');setStep(1);queueReview();}
  function startPlateau(task){
    if(!task?.config?.strategies||task.config.strategies.length!==1||task.config.combinations?.length)throw new Error('请先完成一个单策略固定参数回测，再带入关键参数平原。');
    editTask({...task,kind:'singlePlateau'},'copy');draft.kind='singlePlateau';draft.periodMode='dates';draft.experiment={...defaultsSingleExperiment(),...definition.singleExperimentDefaults};draft.name=(task.name+' · 关键参数平原').slice(0,100);selectSingleStrategy(task.config.strategies[0].kind);draft.sourceTaskId=task.id;seedPlateauAxes(draft);draft.workflowStep=2;editingTaskId=null;editorNote='已从已完成单策略回测复制股票池、策略、日期与交易规则。只调整关键参数候选；本次区间仍属于开发样本。';save();queueReview();
  }
  window.PortfolioWorkbench={configure,form,poolWorkspace,result,configuration,helpers,editTask,currentStep,setStep,reviewSettings,newTask,screenRequest,advanced,startSingle,startPlateau,selectScreenTask(id){activateScope('research');screen.task=id;screen.pool='base';screen.offset=0;screen.query='';},screenRefresh:()=>screen.task?loadScreen():Promise.resolve(),available:()=>!!definition,
    selectStrategy(id){activateScope('research');if(!draft)return;selectSingleStrategy(id);setStep(2);queueReview();},
    plateau:plateauForm};
})();
