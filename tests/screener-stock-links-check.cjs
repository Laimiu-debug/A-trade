const assert=require('node:assert/strict');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
module.exports=async({evaluate,frame,wait})=>{
 const parentWait=async(expression,label)=>{for(let i=0;i<450;i++){if(await evaluate(expression))return;await delay(100);}throw Error('Timed out: '+label)};
 const state=()=>frame.executeJavaScript(`(()=>{const rows=[...document.querySelectorAll('.ant-table-row')].filter(el=>el.getClientRects().length);return {active:document.querySelector('.screener-pool-card--active')?.innerText,page:document.querySelector('.ant-pagination-item-active')?.getAttribute('title'),rows:rows.map(el=>el.dataset.rowKey),checks:rows.map(el=>el.querySelector('input[type=checkbox]')?.checked??false),filters:[...document.querySelectorAll('.ant-table-filter-trigger.active')].map(el=>el.innerText)}})()`);
 await wait(`document.querySelectorAll('.ant-table-row [data-screener-stock-link="symbol"]').length>=2`,'clickable stock codes');
 // Check actual table filtering and a non-default page, which a remount would reset.
 await frame.executeJavaScript(`document.querySelector('thead th:first-child .ant-table-filter-trigger').click();true`);
 await wait(`!!document.querySelector('input[placeholder="例如: 600519 / 茅台"]')`,'code filter');
 await frame.executeJavaScript(`(()=>{const input=document.querySelector('input[placeholder="例如: 600519 / 茅台"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'600');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
 await frame.executeJavaScript(`(()=>{const apply=[...document.querySelectorAll('.ant-table-filter-dropdown button')].find(el=>el.textContent.trim()==='应用');apply.click();return true})()`);
 await wait(`document.querySelector('thead th:first-child .ant-table-filter-trigger')?.classList.contains('active')`,'active code filter');
 await frame.executeJavaScript(`(()=>{const next=document.querySelector('.ant-pagination-item-2');if(next)next.click();return true})()`);
 await wait(`document.querySelector('.ant-pagination-item-active')?.getAttribute('title')==='2'`,'second table page');
 for(const [field,index] of [['symbol',0],['name',1]]){
  const before=await state();
  const symbol=await frame.executeJavaScript(`(()=>{const el=[...document.querySelectorAll('.ant-table-row [data-screener-stock-link="${field}"]')].filter(el=>el.getClientRects().length)[${index}];if(!el)throw Error('Stock link missing');const symbol=el.dataset.screenerStockSymbol;el.click();return symbol})()`);
  await parentWait(`AT.state.page==='G01'&&AT.state.stock===${JSON.stringify(symbol)}&&!!document.querySelector('[data-market-chart]')`,'native stock from '+field);
  assert.equal(await evaluate('document.querySelector("#strategy-workspace-root").hidden'),true);
  assert.equal(await frame.executeJavaScript('location.pathname'),'/screener','native navigation keeps the funnel mounted');
  const context=await evaluate(`document.querySelector('.stock-research-context')?.innerText||''`);
  assert.match(context,/选股/);
  await evaluate(`document.querySelector('[data-action=back]').click();true`);
  await parentWait(`AT.state.page==='R11'&&!document.querySelector('#strategy-workspace-root').hidden`,'visible funnel after back');
  await wait(`document.querySelector('h1')?.innerText==='选股漏斗'`,'return to exact funnel');
  await wait(`!![...document.querySelectorAll('.ant-table-row')].find(el=>el.getClientRects().length)`,'visible funnel rows');
  assert.deepEqual(await state(),before,'stock '+field+' preserves pool, rows, pagination, filters and selection');
 }
 console.log('PASS funnel stock code/name navigate to the correct native chart and preserve table filter, page and pool');
};
