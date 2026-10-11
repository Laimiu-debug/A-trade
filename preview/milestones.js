// Stage journals are local preview fixtures, not a reconstruction of an account.
(() => {
  const E=AT.escape,M=AT.money;
  const trade=(date,time,code,action,qty,price,fee,position,signal,reason)=>({kind:'trade',date,time,code,action,qty,price,fee,position,signal,reason});
  const event=(date,time,title,note,category='阶段事件',code='')=>({kind:'event',date,time,title,note,category,code});
  const journals={
    1:{start:'2026-01-05',end:'2026-03-31',records:[
      event('2026-01-05','20:00','阶段开启','记录本阶段起始资产与交易计划。'),
      trade('2026-01-06','09:35','600900','买入',2000,24.80,14.88,2000,'2026-01-05','趋势回踩确认，建立首笔观察仓位。'),
      event('2026-02-12','21:10','阶段复盘','回看持仓曲线与首次入场依据，补充后续观察点。','复盘记录','600900'),
      trade('2026-03-10','09:40','600900','减仓',1000,26.70,21.36,1000,'2026-03-09','按前一晚的分段止盈计划减仓，保留剩余持仓。'),
      event('2026-03-31','20:30','阶段归档','保存这一阶段的账户快照、交易记录与复盘笔记。')
    ]},
    2:{start:'2026-04-01',end:'2026-06-30',records:[
      event('2026-04-01','20:00','进入下一阶段','延续已有持仓，重新记录本阶段计划。'),
      trade('2026-04-02','09:38','300750','买入',600,234.60,42.23,600,'2026-04-01','量价条件在收盘后确认，次日分批建仓。'),
      event('2026-05-07','21:15','市场风格观察','记录新能源板块量能与整体市场风格的变化。','市场观察','300750'),
      trade('2026-05-08','09:45','688981','买入',500,79.20,11.88,500,'2026-05-07','记录板块联动下的示例操作与执行价格。'),
      trade('2026-06-18','09:32','300750','减仓',200,265.50,42.48,400,'2026-06-17','分段止盈，记录减仓后剩余数量。'),
      event('2026-06-30','20:40','阶段归档','归档阶段操作，并整理下一阶段需要关注的事项。')
    ]},
    3:{start:'2026-07-01',end:null,records:[
      event('2026-07-01','20:00','本阶段开启','开始持续记录本阶段的交易、复盘与市场事件。'),
      event('2026-09-28','21:00','持仓复盘','记录分段止盈条件及次日需要核对的执行项目。','复盘记录','300750'),
      event('2026-10-08','20:30','晚间研究记录','保存趋势回踩研究结果，并关联次日操作计划。','研究记录','600900'),
      event('2026-10-09','20:10','执行对照','补充成交价格和持仓变化，对照晚间计划记录差异。','复盘记录')
    ]}
  };
  AT.state.milestoneEvents=AT.state.milestoneEvents&&typeof AT.state.milestoneEvents==='object'&&!Array.isArray(AT.state.milestoneEvents)?AT.state.milestoneEvents:{};
  AT.milestoneEventKey=n=>`${AT.state.account}|${n}`;
  AT.accountHistory=account=>{
    const records=account==='实盘主账户'?Object.values(journals).flatMap(j=>j.records):[];
    records.push(...AT.trades.filter(t=>(t.account||'实盘主账户')===account).map(t=>({...t,kind:'trade'})));
    for(const [key,events] of Object.entries(AT.state.milestoneEvents)){
      if(key.startsWith(account+'|')&&Array.isArray(events))records.push(...events.filter(e=>e?.kind==='event'));
    }
    return records.sort((a,b)=>(a.date+(a.time||'')).localeCompare(b.date+(b.time||'')));
  };
  AT.milestoneData=n=>{
    const startAsset=AT.state.startCapital*(1+AT.state.goal/100)**(n-1),target=startAsset*(1+AT.state.goal/100);
    const status=AT.assetTotal>=target?'done':AT.assetTotal>=startAsset||n===1?'current':'future';
    const journal=status==='future'?null:journals[n];
    const records=journal?[...journal.records]:[];
    if(n===3&&journal)records.push(...AT.trades.filter(t=>t.date>=journal.start&&t.date<='2026-10-09').map((t,i)=>({...t,kind:'trade',time:['09:35','10:05','09:40','09:32','09:38'][i%5],fee:Number((t.qty*t.price*.0003).toFixed(2)),position:t.code==='600900'?(t.date==='2026-09-25'?10000:12000):AT.findStock(t.code).qty})));
    const own=AT.state.milestoneEvents[AT.milestoneEventKey(n)];
    if(status!=='future'&&Array.isArray(own))records.push(...own.filter(r=>r&&r.kind==='event'&&/^\d{4}-\d{2}-\d{2}$/.test(r.date)&&typeof r.title==='string'&&typeof r.note==='string'));
    records.sort((a,b)=>(a.date+' '+a.time).localeCompare(b.date+' '+b.time));
    return {n,status,startAsset,target,start:journal?.start,end:journal?.end,records,tradeCount:records.filter(r=>r.kind==='trade').length,eventCount:records.filter(r=>r.kind==='event').length};
  };
  function recordView(record) {
    if(record.kind==='trade')return `<article class="milestone-record trade-record"><header><span class="record-time">${E(record.time)}</span><span class="record-kind ${record.action==='买入'?'buy':'sell'}">${E(record.action)}</span>${AT.stockLink(record.code)}</header><dl class="trade-facts"><div><dt>成交数量</dt><dd>${M(record.qty,0)} 股</dd></div><div><dt>成交价格</dt><dd>¥ ${M(record.price)}</dd></div><div><dt>成交金额</dt><dd>¥ ${M(record.qty*record.price)}</dd></div><div><dt>费用（示例）</dt><dd>¥ ${M(record.fee)}</dd></div><div><dt>操作后持仓</dt><dd>${M(record.position,0)} 股</dd></div><div><dt>信号日期</dt><dd>${E(record.signal)}</dd></div></dl><p class="record-reason"><span>操作依据</span>${E(record.reason)}</p></article>`;
    return `<article class="milestone-record event-record"><header><span class="record-time">${E(record.time||'晚间')}</span><span class="record-category">${E(record.category||'个人备注')}</span>${record.local?'<span class="record-local">本地记录</span>':''}</header><h4>${E(record.title)}</h4><p>${E(record.note)}</p>${record.code&&AT.stocks.some(s=>s.code===record.code)?AT.stockLink(record.code):''}</article>`;
  }
  AT.milestoneContent=(n,filter='all')=>{
    const d=AT.milestoneData(n),rows=d.records.filter(r=>filter==='all'||r.kind===filter),dates=[...new Set(rows.map(r=>r.date))];
    const status={done:'已达成',current:'进行中',future:'尚未开始'}[d.status];
    return `<div id="milestone-detail" data-stage="${n}"><div class="milestone-detail-summary"><div class="milestone-detail-meta"><span class="stage-state ${d.status}">${status}</span><span>第 ${n} / ${AT.state.stages} 阶段</span><span class="demo-record-tag">演示记录</span></div><h3>${d.start?`${d.start.replaceAll('-','.')} <span>—</span> ${d.status==='done'&&d.end?d.end.replaceAll('-','.'):'持续记录'}`:'等待阶段记录'}</h3><p>按日期回看这一阶段的操作与事件。</p><div class="milestone-detail-numbers"><div><span>阶段起点</span><strong>¥ ${M(d.startAsset,0)}</strong></div><div><span>目标资产 · ${AT.state.goal}%</span><strong>¥ ${M(d.target,0)}</strong></div></div></div>
      <div class="milestone-journal-toolbar"><div class="milestone-filters" role="group" aria-label="里程碑记录类型">${[['all','全部',d.records.length],['trade','交易操作',d.tradeCount],['event','日期事件',d.eventCount]].map(([id,title,count])=>`<button data-milestone-filter="${id}" aria-pressed="${filter===id}">${title}<span>${count}</span></button>`).join('')}</div><button class="text-button" data-action="milestone-add-event" ${d.status==='future'?'disabled':''}>${AT.icon('plus')}记录事件</button></div>
      <form class="milestone-event-form" id="milestone-event-form" hidden><h4>补记日期事件</h4><div class="field-row"><label class="field"><span>事件日期</span><input name="event-date" type="date" value="${d.end||'2026-10-09'}" required></label><label class="field"><span>事件类型</span><select name="event-category" aria-label="事件类型"><option>复盘记录</option><option>市场观察</option><option>个人备注</option></select></label></div><label class="field"><span>事件标题</span><input name="event-title" placeholder="例如：阶段复盘、市场风格变化" maxlength="60" required></label><label class="field"><span>事件内容</span><textarea name="event-note" rows="3" placeholder="记录当时发生了什么，以及你的观察。" maxlength="1200" required></textarea></label><label class="field"><span>关联股票（可选）</span><select name="event-stock" aria-label="关联股票（可选）"><option value="">不关联股票</option>${AT.stocks.map(s=>`<option value="${s.code}">${s.name} ${s.code}</option>`).join('')}</select></label><div class="event-form-actions"><button type="button" class="btn" data-action="milestone-cancel-event">取消</button><button type="submit" class="btn primary">${AT.icon('save')}保存事件</button></div></form>
      <div class="milestone-date-log">${dates.length?dates.map(date=>`<section class="milestone-day"><h3><time datetime="${date}">${date.replaceAll('-','.')}</time><span>${rows.filter(r=>r.date===date).length} 条记录</span></h3><div class="milestone-day-records">${rows.filter(r=>r.date===date).map(recordView).join('')}</div></section>`).join(''):`<div class="milestone-empty">${AT.icon('calendar')}<h3>${d.status==='future'?'这一阶段尚未开始':'暂无'+(filter==='trade'?'交易操作':filter==='event'?'日期事件':'关联记录')}</h3><p>${d.status==='future'?'开始后，在这里查看交易操作和日期事件。':'可以切换记录类型，或补记自己的日期事件。'}</p></div>`}</div>
      <p class="milestone-journal-note">交易与阶段日期为预览示例；${AT.isDesktop?'手动补记的事件自动保存到本机。':'手动补记的事件保存在当前浏览器。'}</p></div>`;
  };
})();
