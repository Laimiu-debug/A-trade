(() => {
  const I=AT.icon,E=AT.escape,M=AT.money,B=AT.button;
  const {head,metric,notice,tabs,accountActions}=AT.viewParts;
  const sectionTitle=(title,detail='',actions='')=>`<div class="panel-head"><div><h2>${title}</h2>${detail?`<p class="subtitle">${detail}</p>`:''}</div>${actions}</div>`;
  const context=()=>`<div class="account-context"><span class="tag blue">${AT.state.account==='实盘主账户'?'实盘':'模拟'}</span><select class="select" aria-label="选择账户" data-account><option ${AT.state.account==='实盘主账户'?'selected':''}>实盘主账户</option><option ${AT.state.account==='模拟研究账户'?'selected':''}>模拟研究账户</option></select><span>截至 2026.10.09 · 演示数据</span></div>`;
  const localTabs=(items,active,attr,label)=>`<div class="local-tabs" role="tablist" aria-label="${label}">${items.map(([id,title,icon])=>`<button type="button" role="tab" id="${attr}-${id}" data-${attr}="${id}" aria-selected="${active===id}" aria-controls="${attr}-panel" tabindex="${active===id?0:-1}" class="local-tab ${active===id?'active':''}">${I(icon)}${title}</button>`).join('')}</div>`;

  AT.cashRecords=()=>`<div class="table-wrap"><table class="data-table"><thead><tr><th>日期</th><th>类型</th><th class="num">资金变动</th><th>说明</th></tr></thead><tbody><tr><td>2026-01-05</td><td><span class="tag blue">期初资金</span></td><td class="num">¥ ${M(AT.state.startCapital)}</td><td>账户演示起算资金</td></tr></tbody></table></div><p class="panel-body field-hint">资金流水记录入出金；交易产生的现金变化在交易流水中查看。</p>`;
  AT.snapshotRecords=()=>`<div class="snapshot-current"><span>2026.10.09 · 收盘资产快照</span><strong>¥ ${M(AT.assetTotal)}</strong><small>固定演示快照，不随手工流水自动结算</small></div><div class="table-wrap"><table class="data-table"><thead><tr><th>资产项目</th><th class="num">金额</th></tr></thead><tbody><tr><td>持仓市值</td><td class="num">¥ 982,640.00</td></tr><tr><td>可用现金</td><td class="num">¥ 276,000.00</td></tr><tr><td>总资产</td><td class="num">¥ 1,258,640.00</td></tr></tbody></table></div><p class="field-hint section-space">正式应用按交易日保留历史快照，并关联当天的成交与资金变动。</p>`;

  AT.tradeManagement=()=>{
    const ledger=AT.ui.ledgerMode||'trades';
    return head('交易管理','记录成交与资金变化，对照晚间计划安排下一交易日操作',B('导入与对账','import','data-action="import-records"')+B('记录交易','plus','data-action="add-trade"','primary'))
      +context()+`<section class="panel ledger-panel">${sectionTitle('账户流水','交易记录与入出金分开核对',B('导出当前流水','export','data-action="export-ledger"','small'))}
      ${localTabs([['trades','交易流水','ledger'],['cash','资金流水','cash']],ledger,'ledger-mode','流水类型')}
      <div id="ledger-mode-panel" role="tabpanel" aria-labelledby="ledger-mode-${ledger}">${ledger==='cash'?AT.cashRecords():AT.recordsTable()}</div></section>
      <section class="panel section-space" id="trade-plan">${sectionTitle('次日交易计划','2026.10.09 收盘研究 → 下一交易日复核','<span class="tag">计划示例 · 未下单</span>')}
      <div class="plan-list">${[
        ['600900','观察买入','趋势回踩确认','复核开盘价格、信号时效和账户仓位'],
        ['601899','观察减仓','风格转弱 · 分批退出','复核持仓风险与止盈条件']
      ].map(([code,action,reason,detail])=>`<article class="plan-row">${AT.stockLink(code,true)}<div><span class="tag ${action==='观察买入'?'blue':'orange'}">${action}</span><strong>${reason}</strong><p>${detail}</p></div>${B('日线分析','market',`data-stock="${code}"`,'small')}</article>`).join('')}</div>
      <div class="panel-body plan-footer"><span>预期收益与退出条件由所关联的有效研究提供，当前未接入。</span>${B('复核执行偏差','compare','data-nav="A14"','small')}</div></section>`;
  };

  const noteMeta={A08:['每日复盘','2026年10月9日','市场风格、今日操作、执行偏差与明日计划……'],A09:['周期复盘','2026年10月 · 周期回顾','阶段判断、策略适用环境、仓位与纪律的变化……'],A12:['灵感闪记','研究想法收集','先记下线索、观察与待验证的问题……']};
  AT.reviewNotebook=()=>{
    const key=AT.ui.reviewNote||'A08',[title,date,placeholder]=noteMeta[key];
    return `${localTabs([['A08','每日复盘','calendar'],['A09','周期复盘','route'],['A12','灵感闪记','note']],key,'review-note','复盘记录类型')}
      <div id="review-note-panel" role="tabpanel" aria-labelledby="review-note-${key}"><div class="notebook-date"><strong>${date}</strong><span class="tiny muted" id="note-state">${AT.isDesktop?'输入自动保存到本机':'输入自动保存到本地预览'}</span></div>
      <textarea class="note-editor" data-note="${key}" aria-label="${title}内容" placeholder="${placeholder}">${E(AT.state.notes[key]||'')}</textarea>
      <div class="notebook-footer"><span>关联观察</span>${AT.stockLink('600900')}${AT.stockLink('300750')}${B('保存记录','save','data-action="save-note"','small')}</div></div>`;
  };
  AT.reviewWorkspace=()=>head('复盘统计','从收益与回撤找到问题，把判断、成交和下一步行动放在一起',B('复盘分享','share','data-nav="A11"')+B('灵感闪记','note','data-action="note"'))
    +context()+`<section id="review-performance" class="review-performance"><div class="section-heading"><div><h2>收益与风险</h2><p>账户整体表现 · 2026.01 — 2026.10</p></div>${tabs([['month','近一月'],['quarter','近三月'],['year','今年']],AT.ui.range,'data-range')}</div>
    <div class="metrics">${metric('累计收益','+25.86%','含持仓浮盈亏 · 固定示例','up')}${metric('最大回撤','−5.18%','固定示例','down')}${metric('当前持仓','4 只','点击股票可追溯日线')}${metric('成交记录',String(AT.trades.length)+' 笔','含手工追加的预览记录')}</div>
    <div class="review-curves"><section class="panel">${sectionTitle('收益曲线','账户与基准对照')}${AT.chart('account',240)}</section><section class="panel">${sectionTitle('回撤曲线','相对历史净值高点')}${AT.chart('drawdown',240)}</section></div></section>
    <div class="review-layout section-space" id="review-notes"><section class="panel review-notebook">${sectionTitle('复盘笔记')}${AT.reviewNotebook()}</section><aside class="review-agenda"><span class="eyebrow">收盘后的三件事</span><h2>观察 · 复核 · 计划</h2>
      <div class="timeline"><div class="timeline-item"><strong>01　看市场环境</strong><p>确认风格与板块的变化。</p><button class="text-button" data-nav="M01">查看市场 ${I('chevronRight')}</button></div>
      <div class="timeline-item"><strong>02　查操作偏差</strong><p>把当时判断与实际成交对上。</p><button class="text-button" data-scroll-section="review-execution">查看执行对照 ${I('chevronRight')}</button></div>
      <div class="timeline-item"><strong>03　写下一步行动</strong><p>列出次日条件和风险边界。</p><button class="text-button" data-nav="A13">查看交易计划 ${I('chevronRight')}</button></div></div></aside></div>
    <section class="panel section-space" id="review-execution">${sectionTitle('操作记录与执行对照','收盘确认与次日成交并列，复核每次操作的依据',B('完整流水','ledger','data-nav="A04"','small'))}${AT.recordsTable(true)}</section>`;

  AT.shareWorkspace=()=>head('复盘分享','先整理回顾，再检查分享范围',B('返回复盘','back','data-nav="A08"')+B('导出摘要','export','data-action="export-account-summary"','primary'))
    +`<div class="share-layout"><section class="panel share-preview"><div class="panel-body"><div class="share-brand"><span class="app-logo"></span><span>A-trade · 我的投资回顾</span></div><span class="eyebrow">2026.01 — 2026.10 · 演示</span><h2>记录每一段成长</h2><div class="balance-hero up">+25.86%</div><p class="field-hint">账户累计收益</p>${AT.chart('account',270)}<div class="share-note">${E(AT.state.notes.A09||AT.state.notes.A08||'回顾判断，记录执行，让下一次操作有据可循。')}</div></div></section><aside class="share-explanation"><h2>分享内容</h2><p>账户收益曲线与复盘文字</p><div class="divider"></div><h3>默认隐藏</h3><p>账户姓名、金额、持仓数量</p><p class="field-hint section-space">当前可导出文本摘要，卡片用于展示样式。</p>${B('编辑复盘内容','note','data-nav="A08"')}</aside></div>`;

  AT.accountView=page=>{
    const canonical=AT.accountEntries[page]?.page||page;
    if(canonical==='A04')return AT.tradeManagement();
    if(canonical==='A08')return AT.reviewWorkspace();
    if(canonical==='A11')return AT.shareWorkspace();
    return AT.overview();
  };

  // Secondary destinations stay beside their parent work, not in the sidebar.
  const view=AT.view;
  AT.view=page=>{
    let html=view(page),links=[];
    if(['R03','R04','R05','R06','R07'].includes(page))links=[['R03','回测工作区'],['R05','收益平原'],['R07','研究对比']];
    if(['T02','T03'].includes(page))links=[['T02','个股估值'],['T03','跨行业筛选']];
    if(['T04','T08'].includes(page))links=[['T04','趋势龙头'],['T08','连板梯队']];
    if(['T06','T07'].includes(page))links=[['T06','异动追踪'],['T07','断板再涨停']];
    if(page.startsWith('C'))links=AT.groups.find(g=>g.id==='C').pages.map(p=>[p.id,p.title]);
    if(links.length)html=html.replace('</section>','</section>'+`<nav class="context-nav" aria-label="相关功能">${links.map(([id,title])=>`<button type="button" data-nav="${id}" ${page===id?'aria-current="page"':''}>${I(AT.pages[id].icon)}${title}</button>`).join('')}</nav>`);
    return html;
  };
})();
