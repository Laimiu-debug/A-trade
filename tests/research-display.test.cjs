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
