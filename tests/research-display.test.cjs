const {test}=require('node:test'),assert=require('node:assert/strict');
const display=require('../preview/research-display.js');
test('names come from declared records with explicit absence and escaped stock links',()=>{
 assert.match(display.stock({symbol:'sh600000'}, {sh600000:'隔离样本银行'}),/隔离样本银行/);
 assert.match(display.stock({symbol:'sh600000',name:'sh600000'}),/名称未提供/);
 const html=display.stock({symbol:'sh600000',name:'<img src=x>',date:'2024-01-02'});assert.doesNotMatch(html,/<img/);assert.match(html,/data-research-stock-date="2024-01-02"/);
});
test('execution states, pool exclusions and risk reasons have human labels',()=>{
 assert.match(display.text('strictHistoryMissing'),/历史资料不足/);assert.match(display.text('openAtLowerLimitOrUncertainST'),/无法卖出/);assert.match(display.operation({side:'sell',targetWeight:.1}),/减仓/);assert.match(display.text('newCloseTarget'),/新收盘计划/);
});

test('operation rows merge matching filled occurrences and preserve unmatched or paged-out decisions',()=>{
 const fill={date:'2025-01-03',signalDate:'2025-01-02',symbol:'sh600000',side:'sell',quantity:200,price:10};
 const matched={...fill,side:undefined,action:'sell',status:'filled',unfilledQuantity:100};
 const decisions=[matched,{...matched,signalDate:'2025-01-01'},{...matched,quantity:300},{...matched,symbol:'sh600001'}];
 const rows=display.operationRows(decisions,[fill]);
 assert.equal(rows.length,4);assert.equal(rows.filter(row=>row.event==='fill').length,1);
 assert.equal(rows.find(row=>row.event==='fill').unfilledQuantity,100);
 assert.equal(fill.unfilledQuantity,undefined,'input is not mutated');
 assert.equal(display.operationRows([matched,matched],[fill]).length,2,'only one occurrence is merged');
 assert.equal(display.operationRows([matched],[]).length,1,'missing fill does not erase its decision');
});

test('operation rows place opening execution before closing holdings and plans without inventing exact timestamps',()=>{
 const common={date:'2025-01-03',symbol:'sh600000'};
 const decisions=[{...common,action:'target',status:'queued'},{...common,action:'hold',status:'held'},{...common,action:'buy',status:'deferred'}];
 const rows=display.operationRows(decisions,[{...common,side:'sell',quantity:100,price:10}]);
 assert.deepEqual(rows.map(row=>display.operationPhase(row).key),['execution','execution','holding','plan']);
 assert.equal(display.operationPhase({date:'2025-01-03',action:'corporateAction'}).key,'record');
});

test('target summaries show asset allocation and signal budget rather than missing fill quantities',()=>{
 const view=display.operationSummary({date:'2025-01-03',action:'target',status:'queued',targetWeight:.2,signalEquity:123456,reason:'scheduledRebalance'});
 assert.match(view.quantityLines.join(' '),/20.00%/);assert.match(view.quantityLines.join(' '),/24,691.20 元/);
 assert.match(view.quantityLines.join(' '),/尚未成交/);assert.doesNotMatch(view.quantityLines.join(' '),/— 股|成交价/);
 assert.match(view.sourceLines.join(' '),/2025-01-03 收盘形成计划/);
 const zero=display.operationSummary({action:'target',status:'queued',targetWeight:0,signalEquity:100000});
 assert.match(zero.quantityLines.join(' '),/0.00%/);
});

test('fill summaries use stored before and after quantities and net cash including fees exactly once',()=>{
 const view=display.operationSummary({date:'2025-01-03',signalDate:'2025-01-02',side:'sell',quantity:400,price:10,amount:4000,fee:5,slippage:4,cashAfter:9000,realizedPnl:-105,positionAction:'reduce',quantityBefore:1000,quantityAfter:600,unfilledQuantity:100});
 assert.equal(view.action,'卖出减仓');assert.equal(view.status,'已模拟成交');
 assert.deepEqual(view.positionLines,['持仓 1,000 → 600 股']);
 assert.match(view.cashLines.join(' '),/现金净流入 3,995.00 元/);assert.match(view.cashLines.join(' '),/本笔后现金 9,000.00 元/);
 assert.match(view.cashLines.join(' '),/滑点成本 4.00 元（已含成交价）/);
 assert.match(view.quantityLines.join(' '),/尚有 100 股未成交/);
 assert.match(view.sourceLines.join(' '),/2025-01-02 收盘信号/);
 const buy=display.operationSummary({side:'buy',quantity:100,price:10,fee:5,positionAction:'open'});
 assert.equal(buy.action,'买入建仓');assert.match(buy.cashLines.join(' '),/现金净流出 1,005.00 元/);
 assert.deepEqual(buy.positionLines,['原报告未保存逐笔持仓变化']);
 const missingFee=display.operationSummary({side:'buy',quantity:100,price:10});
 assert.doesNotMatch(missingFee.cashLines.join(' '),/现金净/,'missing fee cannot be silently assumed zero');
});

test('queued exits and blocked execution are never rendered as completed fills',()=>{
 const plan=display.operationSummary({date:'2025-01-03',action:'sell',status:'queued',reason:'stopLoss',observedClose:9});
 assert.equal(plan.action,'计划卖出');assert.doesNotMatch(plan.sourceLines.join(' '),/模拟成交/);
 assert.match(plan.quantityLines.join(' '),/待开盘/);
 const blocked=display.operationSummary({date:'2025-01-03',action:'buy',status:'deferred',requestedQuantity:200,reason:'tPlusOne'});
 assert.equal(blocked.phase.key,'execution');assert.match(blocked.quantityLines.join(' '),/本次尝试 200 股/);
 assert.match(blocked.cashLines.join(' '),/不是成交/);
 assert.equal(display.operationSummary({action:'buy',status:'rejected'}).status,'未成交，已拒绝');
});
