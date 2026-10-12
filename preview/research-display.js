/* Shared research labels. Display names never change historical eligibility. */
(() => {
  'use strict';
  const labels={base:'基础可交易池',trend:'趋势池',structure:'回调结构池',volume:'量能活跃池',excluded:'基础资格排除',
    buy:'买入',sell:'卖出',hold:'继续持有',target:'目标仓位',rebalance:'调仓计划',valuation:'估值记录',dataQuality:'数据检查',corporateAction:'权益处理',
    queued:'待下一开盘执行',filled:'已模拟成交',deferred:'暂未成交，延后执行',held:'继续持有',cancelled:'已取消',superseded:'已被新计划替代',stale:'估值暂缺',quarantined:'已隔离交易',unaccounted:'权益核算不完整',
    eligible:'基础资格通过',strictHistoryMissing:'严格历史资料不足',suspendedOrZeroVolume:'当日停牌或没有成交',amount20BelowMinimum:'20 日平均成交额不足',historicalSt:'历史 ST，已剔除',st:'历史 ST，已剔除',notHistoricalMember:'当日不属于历史股票池',notListed:'当时尚未上市',delisted:'当时已退市',
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
  function operation(row){const action=row.side||row.action;return action==='sell'&&row.targetWeight>0?'卖出 / 减仓':text(action);}
  const api={labels,text,name,stock,operation};
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(typeof window!=='undefined'){
    window.ResearchDisplay=api;
    if(typeof document!=='undefined')document.addEventListener('click',event=>{const el=event.target.closest?.('[data-research-stock-date]');if(el&&typeof AT!=='undefined'&&AT.marketUI?.openDay?.(el.dataset.stock,el.dataset.researchStockDate)){event.preventDefault();event.stopImmediatePropagation();}});
  }
})();
