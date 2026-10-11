const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

module.exports = async function checkAccountOptimizations(ui) {
  const {evaluate,click,button,wait,fill,save,navigate,state,uploadText,screenshot,directory,win}=ui;
  const api=async(method,payload={})=>{
    const result=await evaluate(`atradeDesktop.business(${JSON.stringify(method)},${JSON.stringify(payload)})`);
    assert.equal(result.ok,true,result.error);return result.data;
  };
  const refresh=accountId=>evaluate(`AT.businessUI.refresh(${JSON.stringify(accountId)})`);
  const account=await api('account.save',{name:'优化回归账户',kind:'paper',startDate:'2026-01-01',openingCash:100000});
  const accountId=account.accountId;
  await refresh(accountId);

  // Conflicting source figures cannot be silently normalized during the renderer's second preview.
  await navigate('A04');await button('import');
  await click('[data-business="import-tab"][data-tab="file"]');
  await uploadText('[data-business-import-file]','金额费用冲突.csv','成交日期,证券代码,买卖方向,成交数量,成交价格,成交金额,费用合计,佣金\n20260102,600000,买入,300,10.123,3037,20,5\n','text/csv');
  await wait('document.querySelectorAll("[data-reconciliation]").length===2','保留金额与费用差异');
  assert.equal(await evaluate('document.querySelector("[data-fee-confirm]").checked'),false,'费用冲突不能默认已经核对');
  await evaluate('document.querySelector("[data-import-selected]").checked=true');
  await button('import-commit');
  assert.equal((await state()).ledger.length,0,'未解决差异不写账本');
  await evaluate(`(()=>{for(const el of document.querySelectorAll('select[name^="reconcile:"]'))el.value='source';document.querySelector('[data-fee-confirm]').checked=true;document.querySelector('[data-import-selected]').checked=true;})()`);
  await button('import-validate');
  await wait('document.querySelectorAll("[data-reconciliation]").length===2 && [...document.querySelectorAll("[data-reconciliation]")].every(el=>el.textContent.includes("已选择"))','明确采用来源值');
  await button('import-commit');
  await wait('AT.businessUI.workspace.ledger.length===1 && !document.querySelector("[data-import-row]")','来源金额与费用写入');
  const imported=(await state()).ledger[0];
  assert.equal(imported.amount,3037);assert.equal(imported.fees,20);
  assert.ok(imported.sourceValues,'入账记录保留来源数字');
  assert.equal((await state()).summary.cash,96943);

  // Signed share adjustments must be accepted through the actual form, not only the backend.
  await button('ledger-new');
  await fill({type:'split',date:'2026-01-03',code:'600000',quantity:-50,reason:'合股数量调整'});
  assert.equal(await evaluate('document.querySelector("#business-form").reportValidity()'),true,'负股数调整通过前端校验');
  await save('AT.businessUI.workspace.ledger.length===2','手工缩股保存');
  assert.equal((await state()).positions[0].quantity,250);

  // Seed a second trade, then verify two independent unsaved trade reviews.
  await api('ledger.save',{accountId,type:'buy',date:'2026-01-04',code:'000001',quantity:100,price:10});
  await refresh(accountId);await navigate('A08');
  const trades=(await state()).ledger.filter(item=>item.type==='buy');
  const a=trades[0].id,b=trades[1].id;
  const openTrade=id=>click(`[data-business="review-trade"][data-id="${id}"]`);
  const writeDraft=text=>evaluate(`(()=>{const field=document.querySelector('#business-form textarea[name^="field:"]');field.value=${JSON.stringify(text)};field.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  const firstText=()=>evaluate('document.querySelector("#business-form textarea[name^=\\"field:\\"]").value');
  await openTrade(a);await writeDraft('交易A独立未提交草稿');await button('close');
  await openTrade(b);await writeDraft('交易B独立未提交草稿');await button('close');
  await openTrade(a);assert.equal(await firstText(),'交易A独立未提交草稿');await button('close');
  await openTrade(b);assert.equal(await firstText(),'交易B独立未提交草稿');await button('close');

  // Deleted imports can be re-imported while a surviving import is still deduplicated.
  const reviewFile={reviews:[{kind:'daily',date:'2026-01-04',title:'可恢复档案',fields:{learning:'保留原文'},scores:{}}]};
  const preview=await api('import.preview',{accountId,kind:'reviews',filename:'恢复复盘.json',text:JSON.stringify(reviewFile)});
  const committed=await api('import.commit',{accountId,kind:'reviews',rows:preview.rows});
  await api('review.delete',{accountId,id:committed.reviews.find(r=>r.title==='可恢复档案').id});
  const again=await api('import.preview',{accountId,kind:'reviews',filename:'恢复复盘.json',text:JSON.stringify(reviewFile)});
  assert.equal(again.rows[0].duplicate,false,'删除复盘不留下阻止恢复的去重标记');

  // A real local TDX fixture supplies historical prices without any remote request.
  const fixture=path.join(directory,'行情样本');
  for(const [symbol,prices] of [['sh600000',[10,11,12]],['sz000001',[20,21,22]]]) {
    const file=path.join(fixture,'vipdoc',symbol.slice(0,2),'lday',symbol+'.day');
    await fs.mkdir(path.dirname(file),{recursive:true});
    const bytes=Buffer.alloc(96);
    [20260102,20260105,20260106].forEach((date,index)=>{const offset=index*32,price=prices[index]*100;bytes.writeUInt32LE(date,offset);for(const shift of [4,8,12,16])bytes.writeUInt32LE(price,offset+shift);bytes.writeFloatLE(100000,offset+20);bytes.writeUInt32LE(10000,offset+24);});
    await fs.writeFile(file,bytes);
  }
  const configured=await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixture)}})`);
  assert.equal(configured.ok,true,configured.error);
  const scanned=await evaluate("atradeDesktop.market('market.scan',{})");
  assert.equal(scanned.ok,true,scanned.error);
  const historical=await api('account.valuation',{accountId,date:'2026-01-02'});
  assert.equal(historical.positions.length,1,'历史估值使用历史持仓');
  assert.equal(historical.prices['600000'],10,'不读取未来日线');
  assert.equal(historical.priceDetails['600000'].priceDate,'2026-01-02');
  assert.equal((await api('workspace',{accountId})).snapshots.length,0,'读取行情不会隐式写快照');
  await refresh(accountId);
  await navigate('A02');await button('snapshots');await button('snapshot-new');
  await fill({date:'2026-01-02'});
  await wait('!document.querySelector("#business-form").dataset.loading && document.querySelectorAll("[data-snapshot-price]").length===1','快照表单按历史日期加载');
  await button('snapshot-market');
  await wait('document.querySelector("[data-snapshot-price=\\"600000\\"]").value==="10"','界面读取真实TDX价格');
  assert.ok(await evaluate('document.querySelector("[data-price-source]").textContent.includes("2026-01-02")'));
  await save('AT.businessUI.workspace.snapshots.length===1','行情快照人工确认保存');

  // All history remains discoverable; selection must survive changing filters and pages.
  await api('import.commit',{accountId,rows:Array.from({length:105},(_,i)=>({date:'2026-02-02',time:`10:${String(Math.floor(i/60)).padStart(2,'0')}:${String(i%60).padStart(2,'0')}`,type:'buy',code:'600000',quantity:1,price:10,commission:0,stampTax:0,transferFee:0,otherFees:0,externalId:'history-'+i}))});
  await refresh(accountId);await navigate('A08');
  const historyInput=(key,field,value)=>evaluate(`(()=>{const el=document.querySelector('[data-history-key="${key}"][data-history-filter="${field}"]');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await historyInput('review-trades','to','2026-01-02');
  await wait(`!!document.querySelector('[data-business="review-trade"][data-id="${a}"]')`,'检索100笔之前的逐笔复盘');
  await openTrade(a);assert.equal(await firstText(),'交易A独立未提交草稿');await button('close');
  await button('review-new');
  await wait('document.querySelector("[name=\\"field:accountFacts\\"]")?.value.includes("账本事实")','快速复盘自动事实材料');
  await fill({date:'2026-01-02',title:'跨页关联与实时评分'});
  await wait('document.querySelector("[name=\\"field:accountFacts\\"]").value.includes("账本事实 · 2026-01-02")','切换日期更新未手改的事实');
  await fill({'field:accountFacts':'人工核对的历史事实，请保留',date:'2026-01-03'});
  assert.equal(await evaluate('document.querySelector("[name=\\"field:accountFacts\\"]").value'),'人工核对的历史事实，请保留','切换日期不覆盖手工事实');
  await fill({date:'2026-01-02'});
  await click('[data-review-section="trades"]');
  await evaluate(`(()=>{const el=document.querySelector('input[data-trade-selection="relatedTradeIds"]');el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await historyInput('relatedTradeIds','to','2026-01-02');
  await wait(`!!document.querySelector('input[data-trade-selection="relatedTradeIds"][value="${a}"]')`,'关联选择可检索最早成交');
  await evaluate(`(()=>{const el=document.querySelector('input[data-trade-selection="relatedTradeIds"][value="${a}"]');el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  assert.equal(await evaluate('new FormData(document.querySelector("#business-form")).getAll("relatedTradeIds").length'),2,'筛选后隐藏选择仍保留');
  await click('[data-review-section="scores"]');
  await evaluate(`(()=>{const radio=document.querySelector('input[type="radio"][name^="score:"][value="4"]');radio.checked=true;radio.dispatchEvent(new Event('change',{bubbles:true}));const evidence=document.querySelector('[name="evidence:'+radio.name.slice(6)+'"]');evidence.value='已核对真实成交与原计划';evidence.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await wait('document.querySelector("[data-live-score]")?.textContent.includes("4.2 / 100")','草稿评分及时更新');
  await save('AT.businessUI.workspace.reviews.some(r=>r.title==="跨页关联与实时评分")','跨页关联正式保存');
  assert.equal((await state()).reviews.find(r=>r.title==='跨页关联与实时评分').relatedTradeIds.length,2);

  // Execution facts are distinct from the manually chosen plan status.
  const planState=await api('plan.save',{accountId,date:'2026-01-01',code:'000001',action:'buy',quantity:200,status:'ready',linkedTradeIds:[b],reason:'买入两批各100股'});
  const plan=planState.plans[0];
  assert.equal(plan.execution.status,'partial');assert.equal(plan.execution.quantity,100);assert.equal(plan.execution.completionPct,50);assert.equal(plan.status,'ready');
  await refresh(accountId);await navigate('A04');
  assert.ok(await evaluate('document.querySelector(".plan-execution").textContent.includes("50.00%")'),'计划卡显示实际执行比例');

  // Persist source metadata and provide interactive snapshot dates.
  await api('snapshot.save',{accountId,date:'2026-01-02',prices:historical.prices,priceDetails:historical.priceDetails});
  await api('snapshot.save',{accountId,date:'2026-01-05',prices:{'600000':11,'000001':21}});
  await refresh(accountId);await navigate('A08');
  await click('[data-business="curve-mode"][data-mode="return"]');
  await click('[data-business="curve-mode"][data-mode="drawdown"]');
  await click('[data-business="curve-mode"][data-mode="equity"]');
  await click('[data-business="performance-date"][data-date="2026-01-02"]');
  assert.ok(await evaluate('document.querySelector("#workspace-root").textContent.includes("当日记录")'),'点击曲线日期打开真实当日记录');
  await button('close');
  assert.ok((await state()).snapshots[0].priceDetails['600000'].source);
  await navigate('A08');
  assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false);
  await screenshot('account-optimization-review.png');
  win.setSize(900,760);
  await new Promise(resolve=>setTimeout(resolve,250));
  assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false,'窄屏账户复盘无整页横向溢出');
  await screenshot('account-optimization-narrow.png');
  win.setSize(1480,1000);
  console.log('PASS 账户优化：导入差异、逐笔草稿隔离、缩股、复盘重导、真实TDX历史估值');
};
