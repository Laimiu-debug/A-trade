const assert=require('node:assert/strict');

module.exports=async({frame,wait,clickText})=>{
 const evaluate=code=>frame.executeJavaScript(code);
 const table='[data-research="trade-table"]', storageKey='atrade.table-columns.v1.backtest-trades';
 await wait(`!!document.querySelector('${table} .ant-table-row')`,'transaction rows loaded');
 const saved=await evaluate(`localStorage.getItem(${JSON.stringify(storageKey)})`);
 const write=async(label,value)=>{
  await evaluate(`(()=>{const el=[...document.querySelectorAll('input[aria-label="${label}"]')].find(el=>el.getClientRects().length);if(!el)throw Error('Missing input: ${label}');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(String(value))});el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
 };
 const headers=()=>evaluate(`[...document.querySelectorAll('${table} thead th')].map(el=>el.innerText)`);
 const rowText=()=>evaluate(`[...document.querySelectorAll('${table} tbody tr.ant-table-row')].map(el=>el.innerText)`);
 const checkDates=async()=>{
  const dates=await evaluate(`[...document.querySelectorAll('${table} tbody tr.ant-table-row .research-date-cell')].map(el=>{const range=document.createRange();range.selectNodeContents(el);return {text:el.textContent,lines:range.getClientRects().length,width:el.clientWidth,scroll:el.scrollWidth}})`);
  assert.ok(dates.length>0,'real trade dates exist');
  for(const date of dates){assert.equal(date.lines,1,JSON.stringify(date));assert.ok(date.scroll<=date.width,JSON.stringify(date));}
 };
 try{
  await clickText('表头设置');await clickText('恢复默认表头');await clickText('保存设置');
  await checkDates();const originalRows=await rowText(),originalHeaders=await headers();
  await clickText('表头设置');
  await write('买入日列名','买入日期');
  await wait(`!!document.querySelector('[aria-label="买入日期列宽"]')`,'edited column name');
  await write('买入日期列宽',150);
  await evaluate(`document.querySelector('button[aria-label="显示质量分"]').click();document.querySelector('button[aria-label="上移买入日期"]').click();true`);
  await clickText('保存设置');
  const customized=await headers();assert.equal(customized[2],'买入日期');assert.equal(customized.includes('质量分'),false);
  await checkDates();
  await clickText('表头设置');await write('买入日列名','应取消的修改');await clickText('取消');assert.deepEqual(await headers(),customized);
  await clickText('表头设置');await clickText('恢复默认表头');await clickText('取消');assert.deepEqual(await headers(),customized);
  await clickText('表头设置');
  await evaluate(`(()=>{for(const key of [...document.querySelectorAll('.research-column-settings-row')].map(el=>el.dataset.columnKey)){const el=document.querySelector('.research-column-settings-row[data-column-key="'+key+'"] button[role=switch]');if(el.getAttribute('aria-checked')==='true'&&!el.disabled)el.click()}return true})()`);
  await wait(`document.querySelectorAll('.research-column-settings-row button[role=switch][aria-checked=true]').length===1`,'one visible column retained');
  assert.equal(await evaluate(`document.querySelector('.research-column-settings-row button[role=switch][aria-checked=true]').disabled`),true);
  await clickText('取消');
  await evaluate(`document.querySelector('[role=separator][aria-label="调整买入日期列宽"]').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));true`);
  await wait(`JSON.parse(localStorage.getItem('${storageKey}')).find(c=>c.key==='entry_date').width===160`,'keyboard resize saved');
  await clickText('表头设置');await clickText('恢复默认表头');await clickText('保存设置');
  assert.deepEqual(await headers(),originalHeaders);assert.deepEqual(await rowText(),originalRows,'view settings preserve all trade values');
  console.log('PASS real trade dates stay on one line; header rename, hide, reorder, width, cancel, reset and minimum visibility');
 }finally{
  await evaluate(`(()=>{const key='${storageKey}';const saved=${JSON.stringify(saved)};if(saved===null)localStorage.removeItem(key);else localStorage.setItem(key,saved);window.dispatchEvent(new CustomEvent('atrade-table-preferences-changed',{detail:key}));return true})()`);
 }
};
