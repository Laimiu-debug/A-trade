const {test}=require('node:test');
const assert=require('node:assert/strict');
const Dates=require('../preview/research-date-input.js');
const {validationPayload,nextDay}=require('../preview/research-workflow.js');

test('date input accepts pasted compact dates and common delimiters without changing their day',()=>{
 for(const value of ['2024-02-29','20240229','2024/2/29','2024.2.29',' 2024-2-29 '])assert.equal(Dates.normalize(value),'2024-02-29');
 assert.deepEqual(Dates.range('20240101','2024/12/31'),{startDate:'2024-01-01',endDate:'2024-12-31'});
});
test('strict calendar rejects rollover, invalid leap days, incomplete years and invalid days',()=>{
 for(const value of ['2023-02-29','1900-02-29','2024-04-31','2024-13-01','2024-00-01','2024-01-00','2024-01-32','2024-0','202-01-01','20240-01-01','0000-01-01','',null])assert.equal(Dates.normalize(value),'',String(value));
 assert.equal(Dates.normalize('2000-02-29'),'2000-02-29');
 assert.throws(()=>Dates.range('2024-02-30','2024-03-01'),/真实存在/);
 assert.throws(()=>Dates.range('2024-03-02','2024-03-01'),/开始日期不能晚于结束日期/);
});
test('date markup preserves unfinished typing as text and provides an accessible calendar',()=>{
 const html=Dates.field('开始日期','2024-0',{key:'single:startDate',attrs:'data-pf-field="config.startDate"',min:'2024-01-01'});
 assert.match(html,/type="text"/);assert.match(html,/value="2024-0"/);assert.doesNotMatch(html,/type="date"/);
 assert.match(html,/data-date-action="open"/);assert.match(html,/aria-label="开始日期：打开日历"/);assert.match(html,/data-date-min="2024-01-01"/);
 assert.doesNotMatch(Dates.field('<script>','" onfocus=x',{key:'x'}),/<script>|value="" onfocus/);
});
test('next day handles leap years and boundaries without accepting invalid source dates',()=>{
 assert.equal(nextDay('2024-02-28'),'2024-02-29');assert.equal(nextDay('2024-02-29'),'2024-03-01');
 assert.equal(nextDay('2025-12-31'),'2026-01-01');assert.equal(nextDay('2024-02-30'),'');assert.equal(nextDay('9999-12-31'),'');
 assert.equal(nextDay('0099-12-31'),'0100-01-01');
});
test('later-date validation normalizes pasted dates before enforcing the source boundary',()=>{
 const plan={id:'saved',developmentEnd:'2024-02-29'};
 assert.deepEqual(validationPayload(plan,{startDate:'20240301',endDate:'2024/3/31'}),{kind:'frozenValidation',freezeId:'saved',startDate:'2024-03-01',endDate:'2024-03-31',name:''});
 assert.throws(()=>validationPayload(plan,{startDate:'20240229',endDate:'2024-03-31'}),/晚于原回测/);
});
