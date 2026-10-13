const {app,protocol,dialog,session}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),{spawnSync}=require('node:child_process');
const directory=process.argv[2];if(!directory)throw new Error('Missing isolated smoke directory');
const root=path.resolve(__dirname,'..'),dataDirectory=path.join(directory,'workspace'),fixtureDirectory=path.join(directory,'tdx-fixture');
app.setPath('userData',path.join(directory,'profile'));protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
dialog.showMessageBox=async(_window,options)=>{throw new Error(`Unexpected dialog: ${options.message}`);};
let win;const errors=[];
const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw new Error(`${error.message} [${code.slice(0,160)}]`);});
async function wait(expression,label,timeout=90000){const started=Date.now();while(Date.now()-started<timeout){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`${label}: ${await evaluate('document.querySelector("main").innerText.slice(-4500)')}`);}
const click=selector=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el||el.disabled)throw new Error('Missing enabled '+${JSON.stringify(selector)});el.click();})()`);
async function fill(name,value){await evaluate(`(()=>{const el=document.querySelector('[data-pf-field="${name}"]');if(!el)throw new Error('Missing field ${name}');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));})()`);}
async function input(selector,value){await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
async function navigate(page){await evaluate(`AT.navigate(${JSON.stringify(page)});true`);await wait(`!!document.querySelector('[data-research-page="${page}"]')`,'navigate '+page);}
async function screenshot(name,selector,exact=false){await fs.mkdir(path.join(root,'artifacts'),{recursive:true});const scroll=()=>selector?evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});(${exact?"el.closest('.pf-day-section')||el":"el.closest('.rs-panel')||el"}).scrollIntoView({block:'start',behavior:'instant'});${exact?"window.scrollBy({top:-95,behavior:'instant'});":''}})()`):Promise.resolve();await scroll();await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await new Promise(resolve=>setTimeout(resolve,180));await scroll();await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await fs.writeFile(path.join(root,'artifacts',name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function curveDay(date){
 await wait(`(()=>{const detail=document.querySelector('[data-pf-day-detail]');return detail?.dataset.pfDate===${JSON.stringify(date)}&&detail.innerText.includes('今日已成交')&&detail.innerText.includes('下一交易日计划');})()`,'curve selected day '+date,20000);
 const state=await evaluate(`(()=>{const chart=document.querySelector('.pf-equity[data-pf-chart]'),detail=document.querySelector('[data-pf-day-detail]'),select=document.querySelector('select[data-pf-date-select]');return {date:select.value,path:select.dataset.pfPath,chartPath:chart.dataset.pfChart,detailPath:detail.dataset.pfPath,index:Number(chart.getAttribute('aria-valuenow')),selectedIndex:select.selectedIndex,below:detail.previousElementSibling?.contains(chart),text:detail.innerText};})()`);
 assert.equal(state.date,date,'曲线和日期选择器保持同步');assert.equal(state.path,state.chartPath);assert.equal(state.detailPath,state.chartPath);assert.equal(state.index,state.selectedIndex,'曲线标记跟随选中日期');assert.equal(state.below,true,'当日明细紧跟在收益曲线下方');
 for(const heading of ['今日已成交','收盘持仓','下一交易日计划'])assert.ok(state.text.includes(heading),'当日明细包含 '+heading);
 return state.text;
}
async function curveKey(key,date){
 await evaluate(`document.querySelector('.pf-equity[data-pf-chart]').focus()`);
 win.webContents.sendInputEvent({type:'keyDown',keyCode:key});win.webContents.sendInputEvent({type:'keyUp',keyCode:key});
 await curveDay(date);
}
async function checkCurveDayDetails(dates){
 const options=await evaluate(`[...document.querySelector('select[data-pf-date-select]').options].map(option=>option.value)`);
 assert.deepEqual(options,dates,'曲线日期覆盖完整回测区间');assert.ok(options.length>20,'覆盖需要跨页加载的日期');
 await evaluate(`document.querySelector('.pf-equity[data-pf-chart]').scrollIntoView({block:'center',behavior:'instant'})`);
 await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
 await evaluate(`document.querySelector('.pf-equity[data-pf-chart]').scrollIntoView({block:'center',behavior:'instant'});new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
 const point=await evaluate(`(()=>{const chart=document.querySelector('.pf-equity[data-pf-chart]'),line=chart.querySelector('.pf-account-curve'),last=line.getPointAtLength(line.getTotalLength()),point=new DOMPoint(last.x,last.y).matrixTransform(chart.getScreenCTM());return {x:Math.round(point.x),y:Math.round(point.y)};})()`);
 assert.equal(await evaluate(`document.elementFromPoint(${point.x},${point.y})?.closest('[data-pf-chart]')===document.querySelector('.pf-equity[data-pf-chart]')`),true,'实际鼠标坐标命中曲线绘图区 '+JSON.stringify(point));
 for(const event of [{type:'mouseMove',...point},{type:'mouseDown',...point,button:'left',clickCount:1},{type:'mouseUp',...point,button:'left',clickCount:1}]){win.webContents.sendInputEvent(event);await new Promise(resolve=>setTimeout(resolve,25));await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});}
 await curveDay(dates.at(-1));await screenshot('research-curve-day-details.png','.pf-equity[data-pf-chart]');
 await curveKey('Left',dates.at(-2));await curveKey('Home',dates[0]);
 assert.match(await curveDay(dates[0]),/今天没有成交。/,'无成交日明确说明没有成交');
 await evaluate(`(()=>{const select=document.querySelector('select[data-pf-date-select]');select.value=${JSON.stringify(dates[1])};select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
 await curveDay(dates[1]);
}
async function selectCurveDay(date){
 await evaluate(`(()=>{const select=document.querySelector('select[data-pf-date-select]');select.value=${JSON.stringify(date)};select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
 return curveDay(date);
}
const formatted=(value,digits=2)=>value.toLocaleString('zh-CN',{minimumFractionDigits:0,maximumFractionDigits:digits});
async function readResult(id){
 const reply=await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)},dailyPageSize:200,pageSize:200})`);
 assert.equal(reply.ok,true,JSON.stringify(reply.error));return reply.data;
}
async function createScenario(config,name){
 // This uses the real local preparation, scorer, engine, task queue and IPC.
 // The only scenario difference is one synthetic OHLC opening and a small
 // participation cap; no decisions, fills or renderer results are injected.
 const created=await evaluate(`atradeDesktop.business('research.create',${JSON.stringify({kind:'portfolio',name,selection:{mode:'explicit',source:'local',symbols:['sh600000']},config})})`);
 assert.equal(created.ok,true,JSON.stringify(created.error));const id=(created.data.task||created.data).id;
 let data;const started=Date.now();
 do{data=await readResult(id);assert.notEqual(data.task.status,'failed',JSON.stringify(data.task.error));if(data.task.status==='completed')break;await new Promise(resolve=>setTimeout(resolve,150));}while(Date.now()-started<30000);
 assert.equal(data.task.status,'completed','真实小样本回测完成');
 await navigate('G02');await wait(`!!document.querySelector('[data-rs-action="task-open"][data-rs-id="${id}"]')`,'scenario in report library');
 await click(`[data-rs-action="task-open"][data-rs-id="${id}"]`);await wait(`document.querySelector('[data-rs-task]')?.value===${JSON.stringify(id)}&&!!document.querySelector('.pf-account-curve')`,'open real scenario report');
 return {id,result:data.result};
}
function checkDayFacts(day){
 const c=day.positionControl;assert.ok(c,'日级快照提供真实资金与仓位');
 const stockValue=day.holdings.reduce((sum,row)=>sum+row.marketValue,0);
 assert.ok(Math.abs(c.stockMarketValue-stockValue)<1e-7,'股票市值等于实际持仓合计');
 assert.ok(Math.abs(c.equity-c.cash-stockValue)<1e-7,'无权益事件样本中总资产等于现金加股票市值');
 assert.ok(Math.abs(c.stockWeight-stockValue/c.equity)<1e-10,'实际仓位不是配置的目标比例');
 assert.equal(c.holdCount,day.holdings.length);assert.equal(c.maxPositions,1);assert.equal(c.maxStockWeight,.5);
 for(const held of day.holdings){
  assert.ok(held.holdingDays>=1);
  if(c.nextSessionDate)assert.equal(held.nextSessionSellableQuantity,held.quantity);
  else assert.equal(held.nextSessionSellableQuantity,undefined,'源日历结束时不伪造下一日可卖数量');
  assert.ok(Math.abs(held.unrealizedPnl-(held.marketValue-held.costBasis))<1e-7);
  assert.ok(Math.abs(held.unrealizedPnlPct-(held.marketValue/held.costBasis-1)*100)<1e-8,'持仓浮盈百分比使用百分点');
 }
}
async function checkDayRendering(day){
 const text=await selectCurveDay(day.date);checkDayFacts(day);
 const state=await evaluate(`(()=>{const d=document.querySelector('[data-pf-day-detail]');return {control:d.querySelector('[data-pf-position-control]')?.innerText||'',fills:[...d.querySelectorAll('[data-pf-fill-row]')].map(row=>({symbol:row.dataset.pfFillRow,text:row.innerText})),holdings:[...d.querySelectorAll('[data-pf-holding-row]')].map(row=>row.innerText),plans:[...d.querySelectorAll('[data-pf-plan-row]')].map(row=>({symbol:row.dataset.pfPlanRow,group:row.closest('[data-pf-plan-group]').dataset.pfPlanGroup,text:row.innerText})),groups:[...d.querySelectorAll('[data-pf-plan-group]')].map(el=>el.dataset.pfPlanGroup)};})()`);
 for(const label of ['实际股票仓位','可用于买入的现金','数量上限','新买单股目标上限'])assert.ok(state.control.includes(label),'资金控制明确说明 '+label);
 assert.ok(state.control.includes(formatted(day.positionControl.stockWeight*100)+'%'),'实际仓位与真实持仓一致');
 assert.ok(state.control.includes(formatted(day.positionControl.availableCash/10000,4)+' 万元'),'可用现金以明确万元单位展示');
 assert.equal(state.fills.length,day.fills.length,'仅显示真实成交笔数');
 day.fills.forEach((fill,i)=>{
  const row=state.fills[i];assert.equal(row.symbol,fill.symbol);
  assert.ok(row.text.includes({open:'建仓买入',add:'加仓买入',reduce:'减仓卖出',exit:'清仓卖出'}[fill.positionAction]),'区分建仓/加仓/减仓/清仓');
  assert.ok(row.text.includes(`${formatted(fill.quantityBefore,0)} → ${formatted(fill.quantityAfter,0)} 股`),'逐笔成交前后股数可核对');
  assert.ok(row.text.includes(fill.signalDate)&&row.text.includes(day.date),'原信号日与实际执行日均显示');
  assert.ok(row.text.includes(fill.side==='buy'?'资金净支出':'资金净流入'),'成交的现金方向清晰');
  if(fill.unfilledQuantity)assert.ok(row.text.includes(`仍有 ${formatted(fill.unfilledQuantity,0)} 股未成交`),'部分成交有未完成股数');
 });
 assert.equal(state.holdings.length,day.holdings.length);
 day.holdings.forEach((held,i)=>{for(const label of ['实际仓位','浮动收益率','已持有'])assert.ok(state.holdings[i].includes(label));assert.ok(state.holdings[i].includes(`${formatted(held.quantity,0)} 股`));});
 assert.deepEqual(state.groups,['sell','buy','hold'],'下一交易日先卖再买，继续持有单独显示');
 const plans=day.nextSessionPlan.rows.filter(row=>row.pending||row.currentQuantity>0);
 assert.equal(state.plans.length,plans.length,'完整快照包含沿用计划，不仅取当天新增事件');
 for(const plan of plans){
  const row=state.plans.find(item=>item.symbol===plan.symbol);assert.ok(row,'计划有对应股票');
  assert.equal(row.group,!plan.pending?'hold':['reduce','exit'].includes(plan.action)?'sell':'buy');
  if(plan.status==='carried')assert.ok(row.text.includes('沿用 '+plan.signalDate+' 收盘计划'),'跨日计划保留最初信号日期');
  if(plan.pending&&plan.targetQuantity===null)assert.ok(row.text.includes('股数待开盘价格确定'),'未开盘时不伪造确定股数');
  if(plan.pending&&plan.targetQuantity!==null){assert.ok(row.text.includes(`目标持有 ${formatted(plan.targetQuantity,0)} 股`));assert.ok(row.text.includes(`${plan.quantityDelta>0?'拟买入':'拟卖出'} ${formatted(Math.abs(plan.quantityDelta),0)} 股`));}
  if(plan.executionStatus==='partial')assert.ok(row.text.includes('上次只成交了一部分'));
  if(plan.executionStatus==='deferred'){assert.ok(row.text.includes('上次未能成交'));assert.ok(row.text.includes(plan.lastAttemptDate));assert.match(row.text,/开盘涨停/);}
  if(plan.reason==='maxHoldingDays'){assert.equal(plan.riskEvidence.holdingDays,4);assert.equal(plan.riskEvidence.maxHoldingDays,4);assert.ok(row.text.includes('已持有 4 个交易日，达到上限 4 日'),'清仓原因展示实际持有天数和上限');}
  if(!plan.pending){assert.ok(row.text.includes('无待执行买卖'));assert.ok(row.text.includes('无需重复买卖'));}
 }
 return {text,state};
}
async function checkExecutionPath(day){
 await click('[data-pf-action="operations-day"]');await wait(`AT.state.page==='R06'&&document.querySelector('main').innerText.includes(${JSON.stringify(day.date+' 的操作记录')})`,'dated readable execution path');
 const rows=await evaluate(`[...document.querySelectorAll('[data-rs-operation-kind]')].map(row=>({kind:row.dataset.rsOperationKind,stage:row.dataset.rsOperationStage,text:row.innerText,date:row.querySelector('[data-research-stock-date]')?.dataset.researchStockDate}))`);
 const fills=rows.filter(row=>row.kind==='fill');assert.equal(fills.length,day.fills.length,'操作路径每笔实际成交只出现一次');
 assert.ok(!rows.some(row=>row.kind==='decision'&&row.text.includes('已模拟成交')),'已匹配成交不重复显示为一条执行决定');
 for(const [index,row] of fills.entries()){
  const fill=day.fills[index];assert.equal(row.stage,'execution');assert.equal(row.date,day.date);assert.match(row.text,/现金净(流出|流入)/);
  assert.ok(row.text.includes(`持仓 ${formatted(fill.quantityBefore,0)} → ${formatted(fill.quantityAfter,0)} 股`));
 }
 assert.ok(rows.every(row=>!row.text.includes('— 股')),'未知计划股数不呈现为破折号股');
 const lastExecution=rows.map(row=>row.stage).lastIndexOf('execution'),firstPlan=rows.findIndex(row=>row.stage==='plan');
 if(firstPlan>=0)assert.ok(firstPlan>lastExecution,'同日开盘执行先于收盘计划');
 await screenshot('research-readable-path.png','[data-rs-field="pathQuery"]');await navigate('G03');await curveDay(day.date);
}
async function checkOperationScenarios(baseConfig,dates){
 const config={...baseConfig,startDate:dates[60],endDate:dates[68],initialCapital:10000,topN:1,maxPositions:1,maxStockWeight:.5,rebalanceMode:'risk',rebalanceEvery:20,maxParticipationRate:.0001,maxHoldingDays:4,stopLossPct:0,takeProfitPct:0,trailingStopPct:0,commissionRate:0,minCommission:0,transferFeeRate:0,stampDutyRate:0,slippageBps:0,strategies:[{id:'momentum',kind:'momentum',pool:'base',params:{lookback:5,trendWindow:8,minReturn:0}}],allocation:{mode:'single',strategyIds:['momentum']},universe:{strictHistory:false,excludeSt:false,minListingDays:0,minAmount20:0}};
 const {result}=await createScenario(config,'UI · 成交与延续计划');
 assert.equal(result.daily.length,9);assert.equal(result.universeCoverage?.loadedCount??result.quality.coverage.sourceCoverage.loadedCount,1);
 const days=result.daily;
 assert.deepEqual(days.flatMap(day=>day.fills).map(fill=>[fill.positionAction,fill.quantityBefore,fill.quantity,fill.quantityAfter,fill.unfilledQuantity]),[['open',0,200,200,200],['add',200,200,400,0],['reduce',400,200,200,200],['exit',200,200,0,0]],'真实成交覆盖建仓、加仓、部分减仓、清仓');
 assert.equal(days[0].nextSessionPlan.rows[0].targetQuantity,null);
 assert.equal(days[1].nextSessionPlan.rows[0].executionStatus,'deferred');
 assert.equal(days[2].nextSessionPlan.rows[0].targetQuantity,400);assert.equal(days[2].nextSessionPlan.rows[0].quantityDelta,200);
 assert.equal(days[4].fills.length,0);assert.equal(days[4].holdings[0].quantity,400);assert.equal(days[4].nextSessionPlan.hasPendingOrders,false);
 assert.equal(days[5].nextSessionPlan.rows[0].action,'exit');assert.equal(days[6].nextSessionPlan.rows[0].status,'carried');
 assert.equal(days[7].holdings.length,0);assert.equal(days[8].fills.length,0);assert.equal(days[8].nextSessionPlan.rows.length,0);
 for(let i=0;i<days.length;i++){
  const day=days[i],rendered=await checkDayRendering(day);
  if(i===0)assert.match(rendered.text,/今天没有成交。.*收盘为空仓/s);
  if(i===1)await screenshot('research-plan-deferred.png','[data-pf-plan-group="buy"]',true);
  if(i===2){await screenshot('research-position-control.png','[data-pf-position-control]',true);await screenshot('research-readable-operations.png','[data-pf-fill-row]',true);await screenshot('research-plan-carried.png','[data-pf-plan-group="buy"]',true);await checkExecutionPath(day);}
  if(i===4){assert.match(rendered.text,/继续持有，本日无买卖成交/);assert.equal(rendered.state.plans[0].group,'hold');}
  if(i===6)await screenshot('research-plan-reduce.png','[data-pf-plan-group="sell"]',true);
  if(i===8){assert.match(rendered.text,/收盘为空仓/);assert.match(rendered.text,/快照中没有待执行买卖/);}
 }
 const terminal=await createScenario({...config,endDate:dates[66]},'UI · 区间外未完成计划');const last=terminal.result.daily.at(-1);
 assert.equal(last.nextSessionPlan.hasPendingOrders,true);assert.equal(last.nextSessionPlan.nextSessionInBacktest,false);assert.equal(last.nextSessionPlan.rows[0].quantityDelta,-200);
 const rendered=await checkDayRendering(last);assert.match(rendered.text,/回测区间外，尚无后续成交验证/);assert.equal(last.holdings[0].quantity,200,'期末不强制清仓或伪造后续成交');
 await screenshot('research-plan-terminal.png','[data-pf-plan-group="sell"]',true);
 console.log('PASS report scenarios: real fills open/add/reduce/exit, actual cash and exposure, upper-limit deferral, original signal carry, frozen partial order, completed hold, empty account, and terminal pending exit');
}
async function fixture(){
 const dates=[];let day=new Date('2024-01-02T00:00:00Z');while(dates.length<100){if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));day=new Date(day.getTime()+86400000);}
 await fs.mkdir(path.join(fixtureDirectory,'vipdoc','sh','lday'),{recursive:true});
 for(let stock=0;stock<136;stock++){const bytes=Buffer.alloc(dates.length*32);let previous=1000;for(let i=0;i<dates.length;i++){const close=Math.round(1000+stock+i*(.5+stock*.002)),open=stock===0&&i===61?Math.round(previous*1.1):previous,at=i*32,volume=i===99?4000000:2000000;bytes.writeUInt32LE(Number(dates[i].replaceAll('-','')),at);bytes.writeUInt32LE(open,at+4);bytes.writeUInt32LE(Math.max(open,close)+2,at+8);bytes.writeUInt32LE(Math.min(open,close)-2,at+12);bytes.writeUInt32LE(close,at+16);bytes.writeFloatLE(close/100*volume,at+20);bytes.writeUInt32LE(volume,at+24);previous=close;}await fs.writeFile(path.join(fixtureDirectory,'vipdoc','sh','lday','sh'+(600000+stock)+'.day'),bytes);}return dates;
}
async function complete(){await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await wait('!!document.querySelector(".pf-config-ready")','effective settings');await screenshot('research-effective-settings.png','.pf-effective-panel');await evaluate('document.querySelector("[data-pf-form]").requestSubmit()');await wait('AT.state.page==="R03"&&PortfolioWorkbench.currentStep()===5','task create');const id=await evaluate('document.querySelector("[data-rs-task]").value');await wait('!!document.querySelector(".rs-status-completed")&&!!document.querySelector(".pf-account-curve")','account completed');return id;}
app.whenReady().then(async()=>{
 const dates=await fixture();session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
 win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',(_event,details)=>{if(details.level==='error')errors.push(details.message);});window.webContents.on('preload-error',(_event,_file,error)=>errors.push(error.message));}});
 await evaluate('window.__usabilityErrors=[];window.addEventListener("error",event=>__usabilityErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__usabilityErrors.push(String(event.reason)));');
 assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`)).ok,true);assert.equal((await evaluate("atradeDesktop.market('market.scan')")).ok,true);
 const seed=spawnSync(require('../desktop/business-service.cjs').pythonCommand(),['-B','-c',`import sqlite3,sys,pathlib\np=pathlib.Path(sys.argv[1])/'market.sqlite3'\nwith sqlite3.connect(p) as db:\n for i in range(136):db.execute('UPDATE securities SET name=? WHERE symbol=?',('隔离样本'+str(i),f'sh{600000+i}'))`,dataDirectory],{encoding:'utf8',windowsHide:true});assert.equal(seed.status,0,seed.stderr);
 await navigate('R01');await wait('!!document.querySelector("[data-rs-id=momentum]")','catalogue');await click('[data-rs-action="strategy-use"][data-rs-id="momentum"]');await wait('!!document.querySelector("[data-pf-form]")','editor');
 const fields={name:'UI · 全池研究',periodMode:'dates','config.startDate':dates[60],'config.endDate':dates[99],'config.universe.minListingDays':'0','config.universe.minAmount20':'0','params.momentum.lookback':'5','params.momentum.trendWindow':'8','params.momentum.minReturn':'0','config.poolSettings.trend.window':'5','config.poolSettings.volume.window':'5','config.poolSettings.volume.minRatio':'1.2','config.topN':'4','config.maxPositions':'3','config.maxStockWeight':'20','config.maxHoldingDays':'5'};
 for(const [name,value] of Object.entries(fields))await fill(name,value);
 await click('[data-pf-action="workflow-step"][data-pf-step="1"]');await screenshot('research-workflow-pools.png','[data-pf-form]');
 await click('[data-pf-action="workflow-step"][data-pf-step="2"]');await screenshot('research-workflow-strategies.png','[data-pf-form]');
 await click('[data-pf-detail="editor:strategy:momentum"] > summary');
 await input('[data-pf-preset-name="momentum"]','UI · 参数预设');await click('[data-pf-action="save-strategy-preset"][data-pf-strategy="momentum"]');
 await click('[data-pf-action="reset-strategy"][data-pf-strategy="momentum"]');assert.notEqual(await evaluate(`document.querySelector('[data-pf-field="params.momentum.lookback"]').value`),'5');
 await evaluate(`(()=>{const el=document.querySelector('[data-pf-preset-select="momentum"]');const option=[...el.options].find(o=>o.textContent==='UI · 参数预设');if(!option)throw new Error('Saved preset missing');el.value=option.value;el.dispatchEvent(new Event('change',{bubbles:true}));})()`);assert.equal(await evaluate(`document.querySelector('[data-pf-field="params.momentum.lookback"]').value`),'5');
 await screenshot('research-strategy-preset.png','[data-pf-form]');await click('[data-pf-action="delete-strategy-preset"][data-pf-strategy="momentum"]');
 await click('[data-pf-action="workflow-step"][data-pf-step="3"]');await screenshot('research-workflow-rules.png','[data-pf-form]');
 await click('[data-pf-action="workflow-step"][data-pf-step="1"]');
 await click('[data-pf-action="screen-preview"]');await wait('document.querySelector("main").innerText.includes("股票池筛选结果")','screen complete');assert.match(await evaluate('document.querySelector("main").innerText'),/来源证券 136/);assert.match(await evaluate('document.querySelector("main").innerText'),/隔离样本/);
 await click('[data-pf-action="screen-page"][data-pf-offset="100"]');await wait('document.querySelector("main").innerText.includes("当前 101 — 136")','pool second page');
 await input('[data-pf-screen-query]','隔离样本135');await click('[data-pf-action="screen-search"]');await wait('document.querySelector("main").innerText.includes("匹配 1 只")','search full pool');assert.match(await evaluate('document.querySelector("main").innerText'),/sh600135/);
 await screenshot('research-pool-preview.png','[data-pf-screen-query]');await click('[data-pf-action="screen-clear"]');await fill('config.poolSettings.volume.minRatio','3');await click('[data-pf-action="screen-preview"]');await wait('document.querySelector("main").innerText.includes("量能活跃池 0")','changed pool parameter');
 const id=await complete();await checkCurveDayDetails(dates.slice(60));await wait('document.querySelector("main").innerText.includes("已模拟成交")','readable daily fill');await screenshot('research-readable-operations.png','[data-pf-action="operations-day"]');
 const stockDay=await evaluate('document.querySelector(".rs-stock[data-research-stock-date]").dataset.researchStockDate');await click('.rs-stock[data-research-stock-date]');await wait('AT.state.page==="G01"','dated stock navigation');assert.ok(stockDay);
 await navigate('G03');await click('[data-pf-action="operations-day"]');await wait('AT.state.page==="R06"&&document.querySelector("main").innerText.includes("的操作记录")','date filtered execution path');await screenshot('research-readable-path.png','[data-rs-field="pathQuery"]');
 await navigate('G02');await click(`[data-rs-action="task-rename"][data-rs-id="${id}"]`);await input('[data-rs-edit-name]','可读报告 · 已改名');await click('[data-rs-action="task-rename-save"]');await wait('document.querySelector("main").innerText.includes("任务名称已保存")','rename');await input('[data-rs-task-query]','已改名');assert.equal(await evaluate('document.querySelectorAll("[data-rs-action=task-open]").length'),1);await screenshot('research-task-management.png','[data-rs-task-query]');
 await click(`[data-rs-action="task-copy"][data-rs-id="${id}"]`);await wait('AT.state.page==="R03"','reuse configuration');assert.equal(await evaluate(`document.querySelector('[data-pf-field="config.poolSettings.volume.minRatio"]').value`),'3');assert.match(await evaluate(`document.querySelector('[data-pf-field="name"]').value`),/副本/);await fill('name','UI · 配置副本');const copy=await complete();assert.notEqual(copy,id);
 await navigate('G02');await input('[data-rs-task-query]','');await click(`[data-rs-action="task-delete"][data-rs-id="${copy}"]`);assert.match(await evaluate('document.querySelector("main").innerText'),/确认删除/);await click('[data-rs-action="task-delete-confirm"]');await wait('!document.querySelector(\'[data-rs-action="task-open"][data-rs-id="'+copy+'"]\')','deleted record');const original=await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)}})`);assert.equal(original.ok,true);assert.equal(original.data.task.name,'可读报告 · 已改名');assert.equal(original.data.task.status,'completed');
 await checkOperationScenarios(original.data.result.config,dates);
 assert.deepEqual(await evaluate('__usabilityErrors'),[]);assert.deepEqual(errors,[]);console.log('PASS usability: full pool136 and paging/search, changed thresholds, names, dated stock links, native curve click across daily pages, keyboard and date picker, daily actions and persistent CRUD');win.close();setTimeout(()=>app.exit(0),300);
}).catch(error=>{console.error(error.stack);if(win)win.destroy();app.exit(1);});
