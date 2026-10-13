(() => {
  const E=AT.escape,M=AT.money,I=AT.icon;
  const empty=text=>`<p class="day-empty">${I('calendar')}${E(text)}</p>`;
  const fact=(label,value)=>`<div><dt>${label}</dt><dd>${value}</dd></div>`;
  AT.dayDetailTitle=context=>({account:'账户当天操作',research:'策略当天记录',stock:'个股当天行情',market:'市场当天快照'})[context.scope];
  AT.dayContextLabel=context=>context.scope==='account'?context.account:context.scope==='stock'?`${AT.findStock(context.code).name} ${context.code}`:context.title;
  function tradeView(t,research){
    return `<article class="day-trade"><header><span class="tag ${t.action==='买入'?'red':'green'}">${E(t.action)}</span>${AT.stockLink(t.code)}<span class="day-time">${research?'次日模拟成交':E(t.time||'当日成交')}</span></header><dl class="day-facts">${fact('成交数量',M(t.qty,0)+' 股')}${fact('成交价格','¥ '+M(t.price))}${fact('成交金额','¥ '+M(t.qty*t.price))}${fact('信号日期',E(t.signal))}${Number.isFinite(t.position)?fact('操作后持仓',M(t.position,0)+' 股'):''}</dl><p class="day-reason">${E(t.reason)}</p></article>`;
  }
  function accountContent(context,date){
    const d=AT.dayRecords(context,date);
    return `<section class="day-section"><h3>当天交易 <span>${d.trades.length} 笔</span></h3>${d.trades.length?d.trades.map(t=>tradeView(t,false)).join(''):empty('当天没有交易操作记录')}</section><section class="day-section"><h3>资金流水</h3>${d.cash.length?d.cash.map(c=>`<div class="day-cash"><span>${E(c.title)}</span><strong>+¥ ${M(c.amount)}</strong></div>`).join(''):empty('当天没有入出金记录')}</section><section class="day-section"><h3>日期事件与复盘</h3>${d.events.length?d.events.map(r=>`<article class="day-event"><span>${E(r.time||'晚间')} · ${E(r.category||'阶段事件')}</span><h4>${E(r.title)}</h4><p>${E(r.note)}</p>${r.code?AT.stockLink(r.code):''}</article>`).join(''):empty('当天没有已关联的日期事件')}</section>`;
  }
  function researchContent(context,date){
    if(context.configMissing)return empty('这个旧演示任务未保存完整策略配置，无法按日期追溯。请新建研究后查看。');
    const d=AT.dayRecords(context,date),conflict=context.strategyIds.includes('trend')&&context.strategyIds.includes('revert');
    return `<div class="day-parameters"><span>止盈 <b>${context.params.take}%</b></span><span>止损 <b>${context.params.stop}%</b></span><span>首次减仓 <b>${context.params.reduce}%</b></span></div><p class="day-context-note">${context.taskId?'任务 '+E(context.taskId):'独立交互样例'} · 策略版本 V1.0 · ${context.source==='plateau'?'当前热力图参数组合':'配置快照'}</p><section class="day-section"><h3>当天模拟执行 <span>${d.trades.length} 笔</span></h3><p class="day-section-note">依据前一观察日收盘信号，展示次日的模拟成交。</p>${d.trades.length?d.trades.map(t=>tradeView(t,true)).join(''):empty('当天没有买入、减仓或卖出')}</section><section class="day-section"><h3>当天收盘判断 <span>收盘后</span></h3><div class="day-strategies">${context.strategyIds.map(id=>`<div><strong>${E(AT.strategies.find(s=>s.id===id)?.name||id)}</strong><span>${conflict?'组合条件冲突':d.signals.length?'规则触发示例':'观察 · 无新指令'}</span></div>`).join('')||empty('尚未选择策略')}</div>${d.signals.map(t=>`<article class="day-signal"><span class="tag blue">次日计划 · ${t.date}</span><h4>${E(t.action)} ${M(t.qty,0)} 股</h4>${AT.stockLink(t.code)}<p>${E(t.reason)}</p><small>本日只有信号，成交列在 ${t.date} 的操作记录中。</small></article>`).join('')||empty(conflict?'冲突策略未生成可执行样例':'当天没有新交易信号，继续观察')}</section>`;
  }
  function intraday(day){
    const points=AT.intradayPoints(day),x=i=>42+(i<25?i*8.4:228+(i-25)*8.4),y=p=>24+(day.high-p)/(day.high-day.low)*142;
    const path=items=>items.map(({p,i},j)=>`${j?'L':'M'}${x(i)},${y(p.price)}`).join(' ');
    const line=(start,end)=>path(points.map((p,i)=>({p,i})).slice(start,end));
    return `<svg class="intraday-chart" viewBox="0 0 530 228" role="img" aria-label="${day.date} 分时合成示例，09:30 至 11:30、13:00 至 15:00。未接入真实历史分时"><rect x="246" y="16" width="24" height="172" fill="var(--subtle)"/><text x="258" y="213" text-anchor="middle">午休</text>${[day.high,(day.high+day.low)/2,day.low].map(v=>`<line x1="42" x2="472" y1="${y(v)}" y2="${y(v)}" stroke="var(--border)"/><text x="33" y="${y(v)+4}" text-anchor="end">${M(v)}</text>`).join('')}<path d="${line(0,25)} ${line(25,50)}" stroke="var(--primary)" stroke-width="2" fill="none"/>${points.map((p,i)=>`<rect x="${x(i)-1.5}" y="178" width="3" height="12" fill="var(--primary)" opacity=".22"/>`).join('')}<text x="42" y="213">09:30</text><text x="242" y="201" text-anchor="end">11:30</text><text x="274" y="201">13:00</text><text x="472" y="213" text-anchor="end">15:00</text></svg>`;
  }
  function stockContent(context,date){
    const day=AT.stockDays(context.code).find(d=>d.date===date);
    if(!day)return empty('此日期暂无日线和分时数据');
    return `<div class="day-stock-title">${AT.stockLink(context.code)}<strong class="${day.close>=day.previousClose?'up':'down'}">${M(day.close)} <small>${AT.sign((day.close/day.previousClose-1)*100)}%</small></strong></div><dl class="day-facts day-ohlc">${fact('开盘',M(day.open))}${fact('收盘',M(day.close))}${fact('最高',M(day.high))}${fact('最低',M(day.low))}${fact('成交量（示例）',M(day.volume)+' 万手')}${fact('昨收',M(day.previousClose))}</dl><section class="day-section"><h3>当天分时 <span>合成示例</span></h3>${intraday(day)}<p class="day-section-note">分时为交互示例，开收高低与所选日 K 一致；真实历史分时数据尚未接入。</p></section>`;
  }
  AT.dayDetailContent=payload=>{
    const {context,points,index}=payload,date=points[index].date;
    const content=context.scope==='account'?accountContent(context,date):context.scope==='research'?researchContent(context,date):context.scope==='stock'?stockContent(context,date):`<section class="day-section"><h3>所选日期指数变动</h3><strong class="balance-hero">${AT.sign(points[index].value)}%</strong>${empty('该日资讯、风格识别及板块资金数据尚未接入')}</section>`;
    return `<div class="day-context"><span class="eyebrow">${E(AT.dayContextLabel(context))}</span><h3>${date.replaceAll('-','.')} <span>当日回看</span></h3></div><p class="day-demo-note">预览演示 · 日期为样例序列，未校验交易日历。曲线与操作用于评审交互，未进行真实收益结算。</p>${content}<p class="day-data-version">数据版本 ${E(context.dataVersion)}</p>`;
  };
  AT.researchRecordsTable=(context=AT.chartContext('research'))=>{
    const records=AT.researchTrace(context);
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>收盘信号</th><th>次日执行</th><th>股票名称 / 代码</th><th>操作</th><th class="num">数量</th><th></th></tr></thead><tbody>${records.map(t=>`<tr data-stock-chart-date="${E(t.date)}"><td>${t.signal}</td><td>${t.date}</td><td>${AT.stockLink(t.code)}</td><td>${t.action}</td><td class="num">${M(t.qty,0)}</td><td><button class="text-button" data-research-day="${t.date}" data-context="${E(JSON.stringify(context))}">当天详情</button></td></tr>`).join('')||'<tr><td colspan="6">暂无可执行的策略记录示例</td></tr>'}</tbody></table></div>`;
  };
  AT.pathView=()=>{const context=AT.ui.pathContext||AT.chartContext('research');return AT.head('完整操作路径','当前研究配置 · 收盘判断与次日执行分别追溯',AT.button('返回研究','back','data-nav="R03"'))+`<section class="panel"><div class="panel-head"><h2>${E(context.title)}</h2><span class="tag">独立策略演示记录</span></div>${AT.researchRecordsTable(context)}</section><div class="section-space">${AT.panelChart('研究收益与操作位置',context.source==='plateau'?'plateau':'research',242,context)}</div>`};
})();
