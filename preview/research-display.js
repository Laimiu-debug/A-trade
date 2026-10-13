/* Shared research labels. Display names never change historical eligibility. */
(() => {
  'use strict';
  const labels={base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池',excluded:'基础资格排除',
    buy:'买入',sell:'卖出',hold:'继续持有',target:'目标仓位',rebalance:'调仓计划',valuation:'估值记录',dataQuality:'数据检查',corporateAction:'权益处理',
    queued:'待下一开盘执行',filled:'已模拟成交',deferred:'暂未成交，延后执行',rejected:'未成交，已拒绝',held:'继续持有',cancelled:'已取消',superseded:'已被新计划替代',stale:'估值暂缺',quarantined:'已隔离交易',unaccounted:'权益核算不完整',
    eligible:'基础资格通过',strictHistoryMissing:'严格历史资料不足',suspendedOrZeroVolume:'当日停牌或没有成交',amount20BelowMinimum:'20 日平均成交额不足',amplitude20BelowMinimum:'20 日平均振幅不足或历史不完整',historicalSt:'历史 ST，已剔除',historicalST:'历史 ST，计划退出持仓',st:'历史 ST，已剔除',notHistoricalMember:'当日不属于历史股票池',notListed:'当时尚未上市',delisted:'当时已退市',
    notObservedYet:'截止日尚未取得日线',missingDailyBar:'缺少当日日线',invalidHistory:'日线数据无效',historicalFactsUnknown:'历史资格资料不足',unknownHistoricalFacts:'历史资格资料不足',
    insufficientListingHistory:'上市交易日不足',newListing:'上市交易日不足',illiquid:'平均成交额不足',insufficientLiquidity:'平均成交额不足',liquidity:'平均成交额不足',
    suspended:'当日停牌或没有成交',missingBar:'缺少当日日线',noSignal:'尚未触发策略条件',poolFilter:'未通过所选分层池',signal:'触发策略条件',
    stopLoss:'达到止损线',takeProfit:'达到止盈线',trailingStop:'达到移动回撤线',maxHoldingDays:'达到持仓天数上限',partialTakeProfit:'达到部分止盈线',
    scheduledRebalance:'按调仓周期调整排名与仓位',targetRebalance:'按目标仓位调整',strategyRotation:'策略权重轮动',walkForwardRetrain:'滚动训练后的方案交接',
    newCloseTarget:'新收盘计划替代旧目标',trainingPolicyChanged:'新训练方案替代旧目标',riskRulesNotTriggered:'止损、止盈及回撤条件尚未触发',
    liquidityFilterFailedButRiskHoldingRetained:'流动性条件改变；风险退出模式仍保留持仓',baseEligibilityLost:'基础资格失效，取消买入',
    tPlusOne:'T+1 可卖数量不足',positionSlotsOccupied:'持仓名额未释放',previousVolumeOrLotMinimum:'历史成交量或最小交易单位限制',
    cashOrPreviousVolumeBelowMinimum:'可用资金或成交量不足',insufficientSellFeeCash:'现金不足以支付卖出费用',
    missingPreviousTradingBar:'缺少上一交易日有效日线',listingHistoryUnknown:'上市初期交易规则证据不足',unsupportedHistoricalRules:'此日期的交易规则尚未支持',
    openAtUpperLimitOrUncertainST:'开盘涨停或历史 ST 状态不确定，无法买入',openAtLowerLimitOrUncertainST:'开盘跌停或历史 ST 状态不确定，无法卖出',
    slippageBeyondLimitOrUncertainST:'计入滑点后超出限价或 ST 状态不确定',rawCloseDiscontinuity:'原始价格异常断点，已隔离',
    referenceChangedWithoutRightsLedger:'参考价改变，但权益账本不足',declaredHistoricalSuspensionMark:'按来源声明的停牌估值继续持有',missingOrSuspendedBar:'缺少可成交日线，沿用最近估值'};
  const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function text(value){if(value==null)return '—';return labels[value]||(/[\u3400-\u9fff]/.test(String(value))?String(value):'其他原因（见审计详情）');}
  function name(row,names={}){const symbol=row?.symbol||'';const value=row?.name&&row.name!==symbol&&row.name!==symbol.slice(2)?row.name:names[symbol];return value&&value!==symbol&&value!==symbol.slice(2)?value:'名称未提供';}
  function stock(row,names={}){const symbol=row?.symbol||'',label=name(row,names),day=/^\d{4}-\d{2}-\d{2}$/.test(row?.date||'')?` data-research-stock-date="${escape(row.date)}"`:'';const body=`<strong>${escape(label)}</strong><small>${escape(symbol)}</small>`;return /^(?:(sh|sz|bj))?\d{6}$/.test(symbol)?`<button type="button" class="rs-stock" data-stock="${escape(symbol)}"${day} title="${escape(label+' '+symbol+' · 查看日线')}">${body}</button>`:`<span class="rs-stock">${body}</span>`;}
  const finite=value=>typeof value==='number'&&Number.isFinite(value);
  const number=(value,digits=2)=>value.toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits});
  const isFill=row=>row.event==='fill'||row.status==='filled'||!row.status&&['buy','sell'].includes(row.side);
  function operation(row){
    const exact={open:'买入建仓',add:'买入加仓',reduce:'卖出减仓',exit:'卖出清仓'};
    if(isFill(row)&&exact[row.positionAction])return exact[row.positionAction];
    const action=row.side||row.action;
    return action==='sell'&&row.targetWeight>0?'卖出 / 减仓':text(action);
  }
  function operationPhase(row){
    if(isFill(row)||['deferred','rejected'].includes(row.status))return {key:'execution',order:0,label:'开盘执行'};
    if(row.action==='hold'||row.action==='valuation')return {key:'holding',order:2,label:'收盘持有 / 估值'};
    if(['queued','superseded','cancelled'].includes(row.status)||['target','rebalance'].includes(row.action))return {key:'plan',order:3,label:'收盘计划 / 变更'};
    return {key:'record',order:1,label:'权益 / 数据记录'};
  }
  function operationRows(decisions=[],trades=[]){
    const fills=trades.map(row=>({...row,event:'fill'})),used=new Set(),remaining=[];
    for(const row of decisions){
      // Remove only a matching occurrence. A paged-out fill cannot hide its decision.
      const index=row.status==='filled'&&finite(row.quantity)&&row.quantity>0&&row.date&&row.symbol
        ?fills.findIndex((fill,i)=>!used.has(i)&&fill.date===row.date&&fill.symbol===row.symbol&&fill.side===(row.side||row.action)&&fill.quantity===row.quantity
          &&(!row.signalDate||!fill.signalDate||row.signalDate===fill.signalDate)&&(!finite(row.price)||!finite(fill.price)||row.price===fill.price)):-1;
      if(index<0){remaining.push({...row,event:'decision'});continue;}
      used.add(index);
      if(!finite(fills[index].unfilledQuantity)&&finite(row.unfilledQuantity))fills[index].unfilledQuantity=row.unfilledQuantity;
    }
    return [...fills,...remaining].map((row,index)=>({row,index})).sort((a,b)=>
      String(a.row.date||a.row.signalDate||'').localeCompare(String(b.row.date||b.row.signalDate||''))
      ||operationPhase(a.row).order-operationPhase(b.row).order||a.index-b.index).map(item=>item.row);
  }
  function operationSummary(row){
    const filled=isFill(row),target=row.action==='target',side=row.side||row.action;
    const quantityLines=[],positionLines=[],cashLines=[],sourceLines=[];
    let action=operation(row);
    const status=filled?'已模拟成交':row.status?text(row.status):'状态未提供';
    if(target){
      action='调整目标仓位';
      quantityLines.push(finite(row.targetWeight)?`目标占总资产 ${number(row.targetWeight*100)}%`:'目标比例未提供');
      if(finite(row.targetWeight)&&finite(row.signalEquity))quantityLines.push(`按信号日总资产计算的目标市值 ${number(row.targetWeight*row.signalEquity)} 元`);
      quantityLines.push('实际股数待下一交易日开盘核算；尚未成交');
    }else if(filled){
      quantityLines.push(finite(row.quantity)?`成交 ${number(row.quantity,0)} 股`:'成交数量未提供');
      if(finite(row.price))quantityLines.push(`成交价 ${number(row.price,4)} 元`);
    }else{
      if(row.status==='queued'&&['buy','sell'].includes(side))action=side==='buy'?'计划买入':'计划卖出';
      if(finite(row.requestedQuantity))quantityLines.push(`本次尝试 ${number(row.requestedQuantity,0)} 股`);
      else if(finite(row.quantity))quantityLines.push(`${side==='hold'?'持有':'记录数量'} ${number(row.quantity,0)} 股`);
      else if(row.status==='queued')quantityLines.push('计划数量待开盘按持仓、资金及交易限制核算');
      else quantityLines.push('本条未记录成交数量');
      if(finite(row.observedClose))quantityLines.push(`判断时收盘价 ${number(row.observedClose,4)} 元`);
    }
    if(finite(row.unfilledQuantity)&&row.unfilledQuantity>0)quantityLines.push(`本次尚有 ${number(row.unfilledQuantity,0)} 股未成交`);
    if(finite(row.quantityBefore)&&finite(row.quantityAfter))positionLines.push(`持仓 ${number(row.quantityBefore,0)} → ${number(row.quantityAfter,0)} 股`);
    else{
      if(finite(row.quantityBefore))positionLines.push(`成交前持有 ${number(row.quantityBefore,0)} 股`);
      if(finite(row.quantityAfter))positionLines.push(`成交后持有 ${number(row.quantityAfter,0)} 股`);
      if(filled&&!positionLines.length)positionLines.push('原报告未保存逐笔持仓变化');
    }
    if(filled){
      const amount=finite(row.amount)?row.amount:finite(row.quantity)&&finite(row.price)?row.quantity*row.price:null;
      if(finite(amount))cashLines.push(`成交额 ${number(amount)} 元`);
      if(finite(row.fee))cashLines.push(`费用 ${number(row.fee)} 元`);
      if(finite(amount)&&finite(row.fee)&&['buy','sell'].includes(side)){
        const impact=(side==='buy'?-amount:amount)-row.fee;
        cashLines.push(`现金净${impact<0?'流出':'流入'} ${number(Math.abs(impact))} 元`);
      }
      if(finite(row.cashAfter))cashLines.push(`本笔后现金 ${number(row.cashAfter)} 元`);
      if(finite(row.slippage))cashLines.push(`滑点成本 ${number(row.slippage)} 元（已含成交价）`);
      if(finite(row.realizedPnl))cashLines.push(`本笔已实现盈亏 ${number(row.realizedPnl)} 元`);
    }else cashLines.push(target?'目标市值是配置预算，不是已经支出的现金':'本条不是成交，不计成交资金');
    if(row.signalDate)sourceLines.push(`${row.signalDate} 收盘信号`);
    else if(row.status==='queued'&&row.date)sourceLines.push(`${row.date} 收盘形成计划`);
    else if(filled)sourceLines.push('原信号日期未提供');
    if(row.date)sourceLines.push(`${row.date} ${filled?'开盘模拟成交':operationPhase(row).key==='execution'?'开盘执行记录':'记录'}`);
    return {action,status,phase:operationPhase(row),quantityLines,positionLines,cashLines,sourceLines,reason:row.reason?text(row.reason):'未提供具体原因'};
  }
  const api={labels,text,name,stock,operation,operationPhase,operationRows,operationSummary};
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(typeof window!=='undefined'){
    window.ResearchDisplay=api;
    if(typeof document!=='undefined')document.addEventListener('click',event=>{const el=event.target.closest?.('[data-research-stock-date]');if(el&&typeof AT!=='undefined'&&AT.marketUI?.openDay?.(el.dataset.stock,el.dataset.researchStockDate)){event.preventDefault();event.stopImmediatePropagation();}});
  }
})();
