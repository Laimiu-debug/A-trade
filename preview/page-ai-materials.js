/* Read only the securities explicitly requested, and only after Send. */
(() => {
  const pick=(row,keys)=>Object.fromEntries(keys.filter(key=>row?.[key]!==undefined).map(key=>[key,row[key]]));
  function validDate(value) {
    if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return false;
    const parsed=new Date(value+'T00:00:00Z');return Number.isFinite(+parsed)&&parsed.toISOString().slice(0,10)===value;
  }
  function pageDate(context) {
    const filters=context.filters||{},source=context.evidence?.source||{};
    return [filters.questionAsOf,filters.asOf,filters.selectedDay,filters.endDate,source.requestedAsOf,source.asOf,
      ...[...(context.dataDates||[])].sort().reverse()].find(validDate)||'';
  }
  function dateIntent(question,context,today) {
    const found=[...question.matchAll(/(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?/g)].map(match=>`${match[1]}-${match[2].padStart(2,'0')}-${match[3].padStart(2,'0')}`);
    if(found.some(date=>!validDate(date)))throw Error('问题中的日期无效，请使用 YYYY-MM-DD。');
    const dates=[...new Set(found)].sort();
    if(dates.length>2)throw Error('一次请指定一个截止日或一段日期范围。');
    const end=dates.at(-1)||pageDate(context)||today;
    if(end>today)throw Error('提问日期晚于今天，请选择已经发生的日期。');
    return {start:dates.length===2?dates[0]:'',end,explicit:dates.length>0};
  }
  function knownStocks(context) {
    const rows=new Map();let visited=0;
    function walk(value,depth=0) {
      if(!value||typeof value!=='object'||depth>6||++visited>1500)return;
      if(/^(?:sh|sz|bj)\d{6}$/.test(value.symbol||''))rows.set(value.symbol,pick(value,['symbol','code','name']));
      if(Array.isArray(value))value.slice(0,80).forEach(row=>walk(row,depth+1));
      else for(const [key,row] of Object.entries(value))if(!/history|timeline|dailyBars/i.test(key))walk(row,depth+1);
    }
    walk(context.evidence);return [...rows.values()];
  }
  async function collect({question,context,market,focus,previous=[],previousDate='',today=new Date().toLocaleDateString('en-CA'),check=()=>{},progress=()=>{}}) {
    if(/【股票/.test(question))throw Error('请把示例中的【股票名称或代码】替换成你要分析的股票。');
    const refersBack=/(这只|该股|它的|它们|这两只|上述股票|那天|当时)/.test(question);
    const date=dateIntent(question,refersBack&&validDate(previousDate)?{...context,filters:{asOf:previousDate}}:context,today);
    progress('正在识别问题中的股票…');
    const resolved=await market('market.resolve',{question});check();
    if(resolved.ambiguous?.length)throw Error('股票名称或代码有多个匹配：'+resolved.ambiguous.map(group=>`${group.mention}（${group.items.map(row=>row.symbol+' '+row.name).join('、')}）`).join('；')+'。请补充完整市场代码后发送。');
    if(resolved.unknown?.length)throw Error('本地证券目录未找到：'+resolved.unknown.join('、')+'。请检查代码或扫描行情目录后重试。');
    let stocks=resolved.items||[];
    if(!stocks.length) {
      stocks=knownStocks(context).filter(stock=>stock.name&&question.includes(stock.name)||question.toLowerCase().includes(stock.symbol)||stock.code&&question.includes(stock.code));
      if(!stocks.length&&focus)stocks=[focus];
      if(!stocks.length&&refersBack)stocks=previous;
      if(!stocks.length&&context.page?.id==='G01'&&context.evidence?.selectedStock?.symbol)stocks=[context.evidence.selectedStock];
    }
    stocks=[...new Map(stocks.map(stock=>[stock.symbol,stock])).values()];
    if(stocks.length>3)throw Error('一次最多补充 3 只股票的日线，请缩小范围。');
    let snapshot=structuredClone(context);
    const originalDate=pageDate(context);
    if(!originalDate&&stocks.length||originalDate&&date.end!==originalDate||context.page?.id==='G01'&&context.evidence?.source?.latestLoadedDate>date.end) {
      if(!stocks.length)throw Error('问题日期与页面日期不同。请先切换页面日期，或在问题中指定要分析的股票名称或代码。');
      // Page metrics and strategy results can include later events; discard all
      // of them rather than pretending a date label makes them historical.
      snapshot={page:snapshot.page,capturedAt:snapshot.capturedAt,filters:{asOf:date.end},summary:`本次以 ${date.end} 为截止日；未附带页面中可能包含后续数据的指标、筛选结果或交易结果。`,tables:[],evidence:{},limitations:['本次仅补充指定股票在问题截止日及以前的日线；未重算该日的情绪、题材、策略信号或回测。']};
      if(!originalDate)snapshot.limitations.push('页面没有可核对的统一数据日期；本次按指定日期或今天为查询上限，实际行情范围以逐股日线的 from/to 为准。');
    }
    if(!stocks.length)return {context:snapshot,stocks:[]};
    snapshot.evidence ||= {};snapshot.limitations ||= [];
    const materials=[];
    for(const stock of stocks) {
      check();progress(`正在读取 ${stock.name||stock.symbol} 的日线（截至 ${date.end}）…`);
      const result=await market('market.bars',{symbol:stock.symbol,end:date.end,...(date.start?{start:date.start}:{}),limit:120});check();
      if(result.symbol!==stock.symbol)throw Error('日线证券与提问对象不一致，本次未发送 AI。');
      const rows=(result.rows||[]).filter(row=>validDate(row.date)&&row.date<=date.end&&(!date.start||row.date>=date.start))
        .sort((a,b)=>a.date.localeCompare(b.date)).slice(-120).map(row=>pick(row,['date','open','high','low','close','volume','amount']));
      if(!rows.length)throw Error(`${stock.name||stock.symbol} 在指定日期内没有可用日线，本次未发送 AI。`);
      materials.push({stock:pick({...stock,name:result.name||stock.name},['symbol','code','name']),requestedStart:date.start||null,requestedEnd:date.end,
        from:rows[0].date,to:rows.at(-1).date,count:rows.length,dailyBars:rows,
        source:pick(result,['adjustment','volumeUnit','amountUnit','cached','stale','warnings'])});
    }
    snapshot.filters={...snapshot.filters,questionAsOf:date.end};snapshot.dataDates=[date.end];
    if(materials.length===1) {
      const material=materials[0];snapshot.evidence.selectedStock=material.stock;snapshot.evidence.dailyBars=material.dailyBars;
      snapshot.evidence.dailySource=material.source;
      snapshot.evidence.requestedStockRange=pick(material,['requestedStart','requestedEnd','from','to','count']);
    } else snapshot.evidence.requestedStocks=materials;
    snapshot.summary+=' 本次按问题补充：'+materials.map(item=>`${item.stock.name||item.stock.symbol}（${item.stock.symbol}）${item.from} 至 ${item.to}，${item.count} 根日线`).join('；')+'。';
    snapshot.limitations.push('仅按本次问题读取最多 3 只股票、每只最近 120 根日线，长区间并非全量；未自动获取新闻、封单或资金主体信息。',
      '日线保留来源复权口径、单位与缓存状态；截止日无交易时以最后实际交易日为准，不能补造缺失行情。当前名称无法还原历史名称或退市样本。');
    return {context:snapshot,stocks:materials.map(item=>item.stock)};
  }
  const api={validDate,pageDate,dateIntent,knownStocks,collect};
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(typeof window!=='undefined'&&typeof AT!=='undefined')AT.pageAIMaterials=api;
})();
