/* Page questions are sent only by the user. Conversation state stays in this window. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function markdown(value) {
    const inline = text => escape(text).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/`([^`\n]+)`/g, '<code>$1</code>');
    let code = false;
    return String(value || '').split('\n').map(line => {
      if (/^\s*```/.test(line)) {code=!code;return code?'<pre>':'</pre>';}
      if (code) return escape(line)+'\n';
      const heading = /^#{1,4}\s+(.+)$/.exec(line);
      if (heading) return `<h4>${inline(heading[1])}</h4>`;
      const bullet = /^\s*(?:[-*]|\d+[.、])\s+(.+)$/.exec(line);
      if (bullet) return `<p class="page-ai-bullet">${inline(bullet[1])}</p>`;
      return line.trim()?`<p>${inline(line)}</p>`:'<div class="page-ai-paragraph-gap"></div>';
    }).join('')+(code?'</pre>':'');
  }
  function suggestions(page = {}) {
    if(page.id==='T10')return [
      ['判断当前情绪','根据当前页面，分析情绪阶段、最高板竞争、昨日高标反馈及跟风与补涨证据。区分空间龙头与人气判断，说明缺失资料和后续观察条件。'],
      ['分析一只龙头','分析【股票名称或代码】近期的量价和龙头表现，结合本页情绪与题材证据，列出分歧、延续和走弱的观察条件。'],
      ['比较两只股票','比较【股票甲】和【股票乙】的量价、空间高度与同题材地位，引用具体日期和反例。']
    ];
    if (page.id==='T08') return [
      ['分析近期连板龙头','结合当前页面的数据，分析近期连板龙头怎么看：梳理高度梯队、昨日高标晋级与断板反馈，比较连板与趋势活跃度，说明当前阶段、分歧以及接下来应观察的变化。请引用具体日期、股票和数据。'],
      ['寻找接力规律','从当前页提供的历史样本寻找连板接力与高标更替的规律。请给出支持样本、反例、样本数和适用条件；区分已观察到的现象与待验证假设，并说明缺少哪些数据才能验证，不能把相关性当因果。'],
      ['总结最近趋势','请总结当前数据截止日之前最近一段时间的连板高度、晋级率、高标反馈及趋势成交占比如何变化。比较最近 5 个与前 5 个可观察交易日；样本不足就按实际范围总结，说明分母、缺失数据和失效信号。']
    ];
    if (page.id==='T04') return [
      ['总结趋势强弱','结合当前筛选和数据日期，总结趋势候选的强弱、量价配合及分化。用具体股票和指标佐证，说明当前分页覆盖，不把候选样本代表为全市场。'],
      ['寻找共同特征','这些趋势候选有哪些共同特征和差异？请比较涨幅、量比、成交额及已有均线指标，列出反例和后续验证方法，避免用筛选条件本身证明规律。'],
      ['列出观察重点','根据当前页证据整理下一阶段的观察清单：什么变化支持趋势延续，什么变化说明走弱？使用条件情景，说明目前无法判断的部分。']
    ];
    return [
      ['解读当前页面','请解读当前页面：先概括数据日期和范围，再分析关键指标之间的关系，指出最有证据的结论、异常与后续观察重点。'],
      ['寻找规律与反例','请从当前页数据寻找值得验证的规律，引用具体记录，区分事实与假设，列出反例、样本限制和验证办法。'],
      ['总结近期变化','根据当前页实际提供的历史数据总结近期变化和可能原因。没有跨日证据时明确说明，不能仅用一个截面推断时间趋势。']
    ];
  }
  function historyFor(messages) {
    const history=messages.slice(-16).map(message=>({role:message.role,content:((message.role==='assistant'?`[回答依据：${message.source||'当前页'}]\n`:'')+message.content).slice(0,4000)}));
    while(history.reduce((sum,item)=>sum+item.content.length,0)>30000)history.splice(0,2);
    return history;
  }
  function materialSummary(snapshot) {
    const evidence=snapshot.evidence||{},single=evidence.selectedStock&&evidence.requestedStockRange;
    const stocks=single?[{stock:evidence.selectedStock,...evidence.requestedStockRange,source:evidence.dailySource}]:evidence.requestedStocks||[];
    const requested=stocks.length?`<p><strong>按本次问题补充的日线：</strong></p><ul>${stocks.map(item=>`<li>${escape(item.stock.name||item.stock.symbol)}（${escape(item.stock.symbol)}）：${escape(item.from)} 至 ${escape(item.to)}，${escape(item.count)} 根${item.source?.stale?' · 缓存未更新':''}</li>`).join('')}</ul>`:'';
    const timeline=snapshot.evidence?.timeline;
    const rows=(snapshot.tables||[]).map(table=>`<li>${escape(table.title)}：附带 ${table.rows.length} 行${table.totalRows!=null?' / 筛选共 '+escape(table.totalRows)+' 行':''}</li>`).join('');
    const history=timeline?`<p><strong>历史证据：</strong>${escape(timeline.from||'未提供起始日')} 至 ${escape(timeline.to||'未提供截止日')}，附带 ${escape(timeline.shownDays||0)} 个交易日。</p>`:'';
    return `${requested}<p>${escape(snapshot.summary||'当前页面暂无可读取的正文。')}</p>${history}${rows?'<ul>'+rows+'</ul>':''}<p class="page-ai-material-limit">${escape((snapshot.limitations||[]).slice(0,4).join(' '))}</p>`;
  }
  if (typeof module === 'object' && module.exports) module.exports = {markdown,suggestions,historyFor};
  if (typeof window === 'undefined' || typeof AT === 'undefined') return;
  const sessions = new Map();
  let panel, trigger, current, context, opened=false, timer, composing=false, focus=null;
  const $ = selector => panel.querySelector(selector);
  const ready = () => AT.isDesktop && typeof window.atradeDesktop?.ai==='function' && AT.aiSettings?.ready('text');
  const dates = snapshot => {
    const items=Array.isArray(snapshot?.dataDates)?[...new Set(snapshot.dataDates.filter(item=>typeof item==='string'))].sort():[];
    return items.length>3?`${items[0]} — ${items.at(-1)}`:items.join('、');
  };
  const scope = snapshot => JSON.stringify([snapshot.page?.scope || snapshot.page?.id,snapshot.page?.title,
    AT.pageAIMaterials.pageDate(snapshot),AT.marketUI?.status?.path||'',AT.marketUI?.status?.lastScannedAt||'',
    snapshot.page?.id==='T10'?JSON.stringify(snapshot.filters):'',
    snapshot.page?.id==='G01'?AT.state.stock:'',(/^A\d/.test(snapshot.page?.id||'')||document.querySelector('#workspace-root:not([hidden]) .business-modal'))?AT.state.businessAccountId||AT.state.account:'',
    document.querySelector('#main:not([hidden]) [data-rs-task],#workspace-root:not([hidden]) [data-rs-task]')?.value||'',
    document.querySelector('#workspace-root:not([hidden]) #workspace-title')?.textContent || '']);
  function sessionFor(snapshot) {
    const base=scope(snapshot);
    if(focus?.base!==base)focus=null;
    const key=base+(focus?'|'+focus.stock.symbol:'');
    if (!sessions.has(key)) sessions.set(key,{key,draft:'',messages:[],request:null,error:'',notice:'',stocks:[],material:null,stage:''});
    if (sessions.size>20) for(const [id,item] of sessions){if(id!==key&&!item.request){sessions.delete(id);break;}}
    return sessions.get(key);
  }
  function controls() {
    const busy=!!current?.request, configured=ready();
    $('#page-ai-question').readOnly=busy;
    $('[data-page-ai="send"]').disabled=busy||!configured||!current?.draft.trim()||!context;
    $('[data-page-ai="send"]').hidden=busy;
    $('[data-page-ai="stop"]').hidden=!busy;
    $('[data-page-ai="clear"]').disabled=busy||!current?.messages.length;
    for(const button of panel.querySelectorAll('[data-page-ai-suggestion]'))button.disabled=busy;
    const status=$('.page-ai-status');
    status.textContent=current?.error || (busy?current.stage||'正在分析本次页面数据…':current?.notice||'');
    status.dataset.tone=current?.error?'error':'neutral';
    status.setAttribute('role',current?.error?'alert':'status');
    $('.page-ai-setup').hidden=!!configured;
    $('.page-ai-model').textContent=configured?`文本 AI · ${AT.aiSettings.config.profiles.text.model}`:'使用「设置 → AI 模型」中的文本 AI';
    trigger.classList.toggle('page-ai-working',[...sessions.values()].some(item=>item.request));
    $('[data-page-ai="page-topic"]').hidden=!focus;
    $('.page-ai-topic').textContent=focus?`分析对象：${focus.stock.name||focus.stock.symbol}（${focus.stock.symbol}）`:'当前页面对话';
    $('.page-ai-material-state').textContent=busy?current.stage:current?.material?'下方展示最近一次实际发送的材料。':'发送时识别名称、代码和日期；仅补充所问股票的日线。';
  }
  function messages(scroll=true) {
    const list=$('.page-ai-messages');
    list.innerHTML=current.messages.length?current.messages.map((item,index)=>
      `<article class="page-ai-message page-ai-${item.role}"><header><strong>${item.role==='user'?'你':'AI'}</strong>${item.role==='assistant'?`<button type="button" data-page-ai-copy="${index}" aria-label="复制回答">复制</button>`:''}</header>${item.role==='assistant'?`<div class="page-ai-answer">${markdown(item.content)}</div><small class="page-ai-answer-source">${escape(item.source||'')} · ${escape(item.model||'文本 AI')}</small>`:`<p class="page-ai-question-text">${escape(item.content)}</p>`}</article>`).join(''):
      '<div class="page-ai-welcome"><span class="page-ai-welcome-symbol" aria-hidden="true">✦</span><h3>围绕这一页，继续追问</h3><p>直接输入股票名称或代码，也可以比较两三只股票。发送后才读取对应日线；没有指定日期时使用本页截止日。</p><small>页面、观察日期与个股话题分别保留。打开面板不会调用 AI。</small></div>';
    if(scroll)list.scrollTop=list.scrollHeight;
  }
  function refresh() {
    if(!opened)return;
    try {
      context=AT.pageAIContext.capture();
      const next=sessionFor(context),changed=current!==next;
      current=next;
      if(changed){$('#page-ai-question').value=current.draft;messages();$('.page-ai-evidence').hidden=true;}
      const shown=current.material||context,source=dates(shown);
      $('.page-ai-context').textContent=`${shown.page.title}${source?' · '+source:' · 日期以页面说明为准'}`;
      showMaterial(current.material||context);
      const choices=suggestions(context.page),signature=context.page.id;
      if($('.page-ai-suggestions').dataset.page!==signature){
        $('.page-ai-suggestions').dataset.page=signature;
        $('.page-ai-suggestions').innerHTML=choices.map(([label,question])=>`<button type="button" data-page-ai-suggestion="${escape(question)}">${escape(label)}</button>`).join('');
      }
      controls();
    } catch {
      context=null;
      if(!current)current={draft:'',messages:[],error:'',request:null};
      current.error='当前页面材料暂未就绪，请等待页面加载后重试。';controls();
    }
  }
  function showMaterial(snapshot) {
    $('.page-ai-material-summary').innerHTML=materialSummary(snapshot);
    $('.page-ai-evidence pre').textContent=JSON.stringify(snapshot,null,2);
  }
  function open(question='') {
    opened=true;panel.hidden=false;trigger.setAttribute('aria-expanded','true');document.body.classList.add('page-ai-open');
    refresh();messages();if(typeof question==='string'&&question.trim()&&!current?.draft.trim())current.draft=question.slice(0,4000);$('#page-ai-question').value=current?.draft||'';controls();$('#page-ai-question').focus({preventScroll:true});
  }
  function close() {
    opened=false;panel.hidden=true;trigger.setAttribute('aria-expanded','false');document.body.classList.remove('page-ai-open');trigger.focus({preventScroll:true});
  }
  function analyzeStock(stock) {
    open();if(!context||!stock?.symbol)return;
    const base=scope(context);focus={base,stock};current=sessionFor(context);
    refresh();messages();
    if(current.request||current.messages.length){$('#page-ai-question').value=current.draft;controls();return;}
    if(current.draft.trim()&&!current.autoDraft){current.notice='已保留这只股票的待发送问题，点击发送后分析。';$('#page-ai-question').value=current.draft;controls();return;}
    if(!current.draft.trim()){current.draft=`请分析 ${stock.name||stock.symbol}（${stock.symbol}）截至 ${AT.pageAIMaterials.pageDate(context)||'本页截止日'} 的龙头表现。结合实际日线说明涨幅、回撤、成交量与成交额变化，比较同日高标、跟风和补涨证据；区分空间高度与人气推测，给出延续、分歧和走弱的观察条件，不虚构封单、新闻或带动因果。`;current.autoDraft=true;}
    $('#page-ai-question').value=current.draft;controls();void send();
  }
  async function send() {
    const before=current;
    refresh();
    if(before&&before!==current){current.notice='页面已切换，请为当前页面输入问题。';controls();return;}
    if(!current||current.request||!ready()||!context)return;
    const question=current.draft.trim();if(!question)return;
    if(question.length>4000){current.error='问题最多 4000 字，请适当缩短。';controls();return;}
    const item=current, snapshot=structuredClone(context), requestId=crypto.randomUUID(), base=scope(snapshot);
    item.request=requestId;item.sent=false;item.error='';item.notice='';item.stage='正在整理本次材料…';controls();
    try {
      const check=()=>{
        if(item.request!==requestId)throw Error('已停止');
        if(scope(AT.pageAIContext.capture())!==base)throw Error('页面、观察日期或数据源已变化，本次未发送 AI。请在当前范围重新提问。');
      };
      const market=async(method,payload)=>{const result=await window.atradeDesktop.market(method,payload);if(!result?.ok)throw Error(typeof result?.error==='string'?result.error:result?.error?.message||'读取行情失败，请检查行情目录。');return result.data;};
      const selected=focus?.stock||(snapshot.page.id==='G01'&&/^(sh|sz|bj)\d{6}$/.test(AT.state.stock||'')?{symbol:AT.state.stock}:null);
      const material=await AT.pageAIMaterials.collect({question,context:snapshot,market,focus:selected,previous:item.stocks,previousDate:item.material?AT.pageAIMaterials.pageDate(item.material):'',check,
        progress:text=>{if(item.request!==requestId)return;item.stage=text;if(current===item)controls();}});
      check();item.material=material.context;item.stage='正在分析本次页面数据…';item.sent=true;
      if(current===item){showMaterial(item.material);controls();}
      const asOf=AT.pageAIMaterials.pageDate(material.context),history=historyFor(item.messages.filter(message=>!message.asOf||message.asOf<=asOf));
      const result=await window.atradeDesktop.ai('page.ask',{requestId,question,context:material.context,history});
      if(item.request!==requestId)return;
      if(!result?.ok)throw new Error(typeof result?.error==='string'?result.error:'本次分析未完成，请重试。');
      if(typeof result.data?.answer!=='string'||!result.data.answer.trim())throw new Error('AI 未返回可显示的回答，请重试。');
      item.messages.push({role:'user',content:question,asOf},{role:'assistant',content:result.data.answer,asOf,model:result.data.model,
        source:material.context.page.title+(dates(material.context)?' · '+dates(material.context):'')+(material.stocks.length?' · '+material.stocks.map(stock=>stock.name||stock.symbol).join('、'):''),generatedAt:result.data.generatedAt});
      item.stocks=material.stocks;
      item.messages=item.messages.slice(-16);item.draft='';
      if(current===item&&opened){$('#page-ai-question').value='';messages();}
    } catch(error) {if(item.request===requestId)item.error=error.message||'连接失败，问题已保留，请重试。';}
    finally {if(item.request===requestId){item.request=null;item.stage='';}controls();}
  }
  async function stop() {
    const item=current,id=item?.request;if(!id)return;
    item.request=null;item.stage='';item.notice='已停止，问题已保留。';controls();
    if(item.sent)try{await window.atradeDesktop.ai('page.cancel',{requestId:id});}catch{/* Late responses are ignored by request identity. */}
    $('#page-ai-question').focus({preventScroll:true});
  }
  function mount() {
    trigger=document.querySelector('[data-page-ai="open"]');if(!trigger)return;
    panel=document.createElement('aside');panel.id='page-ai-panel';panel.className='page-ai-panel';panel.dataset.pageAiPanel='';panel.hidden=true;
    panel.setAttribute('aria-labelledby','page-ai-title');
    panel.innerHTML=`<header class="page-ai-head"><div><h2 id="page-ai-title">✦ 页面 AI 助手</h2><p class="page-ai-model"></p></div><button type="button" class="icon-button" data-page-ai="close" aria-label="关闭页面 AI">${AT.icon('close')}</button></header>
      <div class="page-ai-source"><button type="button" data-page-ai="context" aria-expanded="false"><span class="page-ai-context"></span><span class="page-ai-context-hint">查看材料</span></button><div class="page-ai-topic-row"><span class="page-ai-topic"></span><button type="button" data-page-ai="page-topic" hidden>返回本页对话</button></div><p class="page-ai-material-state"></p></div>
      <div class="page-ai-evidence" hidden><strong>本次分析材料</strong><p>发送时补充所问股票的日线。回答后保留该次实际材料；历史回答按各自的日期和范围理解。</p><div class="page-ai-material-summary"></div><details><summary>查看附带数据明细</summary><pre></pre></details></div>
      <div class="page-ai-setup"><p>${AT.isDesktop?'请先配置并启用文本 AI，即可分析当前页面。':'请在桌面应用中配置并使用页面 AI。'}</p><button type="button" data-page-ai="settings">前往 AI 模型设置 →</button></div>
      <div class="page-ai-messages" role="log" aria-label="本页 AI 对话"></div>
      <div class="page-ai-compose"><div class="page-ai-suggestions" aria-label="当前页面的建议问题"></div><label class="page-ai-input-label" for="page-ai-question">询问当前页面</label><textarea id="page-ai-question" rows="3" maxlength="4000" placeholder="输入股票名称或代码，或询问本页数据；日期可写 YYYY-MM-DD。"></textarea><p class="page-ai-status" role="status" aria-live="polite"></p><div class="page-ai-compose-actions"><button type="button" data-page-ai="clear">新话题</button><span>Enter 发送 · Shift + Enter 换行</span><button type="button" class="page-ai-send" data-page-ai="send">发送 ↑</button><button type="button" class="page-ai-stop" data-page-ai="stop" hidden>停止</button></div></div>`;
    document.body.append(panel);
    trigger.addEventListener('click',()=>opened?close():open());
    panel.addEventListener('click',async event=>{
      const suggestion=event.target.closest('[data-page-ai-suggestion]');
      if(suggestion&&!suggestion.disabled){current.draft=suggestion.dataset.pageAiSuggestion;current.autoDraft=false;$('#page-ai-question').value=current.draft;current.error='';controls();$('#page-ai-question').focus();return;}
      const copy=event.target.closest('[data-page-ai-copy]');
      if(copy){try{await navigator.clipboard.writeText(current.messages[Number(copy.dataset.pageAiCopy)].content);copy.textContent='已复制';}catch{current.error='复制未完成，可以选中回答后手动复制。';controls();}return;}
      const action=event.target.closest('[data-page-ai]');if(!action||action.disabled)return;
      if(action.dataset.pageAi==='close')close();
      if(action.dataset.pageAi==='send')send();
      if(action.dataset.pageAi==='stop')stop();
      if(action.dataset.pageAi==='clear'){current.messages=[];current.draft='';current.stocks=[];current.material=null;$('#page-ai-question').value='';current.error='';current.notice='已开始新话题。';messages();refresh();}
      if(action.dataset.pageAi==='page-topic'){focus=null;refresh();messages();}
      if(action.dataset.pageAi==='context'){const evidence=$('.page-ai-evidence');evidence.hidden=!evidence.hidden;action.setAttribute('aria-expanded',String(!evidence.hidden));}
      if(action.dataset.pageAi==='settings'){close();AT.navigate('C07');}
    });
    $('#page-ai-question').addEventListener('input',event=>{if(!current)return;current.draft=event.target.value;current.autoDraft=false;current.error='';current.notice='';controls();});
    $('#page-ai-question').addEventListener('compositionstart',()=>{composing=true;});
    $('#page-ai-question').addEventListener('compositionend',()=>{composing=false;});
    document.addEventListener('keydown',event=>{
      if(!opened)return;
      if(event.key==='Escape'&&!document.querySelector('.overlay')){event.preventDefault();event.stopImmediatePropagation();close();return;}
      if(event.target.id==='page-ai-question'&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&!composing&&event.keyCode!==229){event.preventDefault();event.stopImmediatePropagation();send();}
    },true);
    const observer=new MutationObserver(()=>{if(!opened)return;clearTimeout(timer);timer=setTimeout(refresh,120);});
    for(const element of document.querySelectorAll('#main,#workspace-root'))observer.observe(element,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden']});
    AT.pageAI={open,close,refresh,analyzeStock,renderMarkdown:markdown};
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
