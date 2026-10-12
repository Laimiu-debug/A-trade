const {app,protocol,dialog,session}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const directory=process.argv[2];if(!directory)throw new Error('Missing isolated directory');
const root=path.resolve(__dirname,'..'),dataDirectory=path.join(directory,'workspace'),input=path.join(directory,'fixture.jsonl'),output=path.join(directory,'complete-report.json');
app.setPath('userData',path.join(directory,'electron-profile'));protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
let opened=0,saved=0,win;const errors=[];
dialog.showMessageBox=async(_window,options)=>{throw new Error(`Unexpected dialog: ${options.message}`);};
dialog.showOpenDialog=async(_window,options)=>{assert.ok(options.filters[0].extensions.includes('jsonl'));opened++;return {canceled:false,filePaths:[input]};};
dialog.showSaveDialog=async()=>{saved++;return {canceled:false,filePath:output};};
const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw new Error(`${error.message} [${code.slice(0,160)}]`);});
async function wait(expression,label,timeout=120000){const started=Date.now();while(Date.now()-started<timeout){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`${label}: ${await evaluate('document.querySelector("main").innerText.slice(-4500)')}`);}
const click=selector=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el||el.disabled)throw new Error('Missing enabled '+${JSON.stringify(selector)});el.click();})()`);
async function fill(name,value){await evaluate(`(()=>{const el=document.querySelector('[data-pf-field="${name}"]');if(!el)throw new Error('Missing field ${name}');if(el.type==='checkbox')el.checked=${JSON.stringify(value)};else el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(el.tagName==='SELECT'||el.type==='checkbox'?'change':'input',{bubbles:true}));})()`);}
async function navigate(page){await evaluate(`AT.navigate(${JSON.stringify(page)});true`);await wait(`!!document.querySelector('[data-research-page="${page}"]')`,'navigate '+page);}
async function screenshot(name,selector){await fs.mkdir(path.join(root,'artifacts'),{recursive:true});if(selector)await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'});true`);await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await fs.writeFile(path.join(root,'artifacts',name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function fixture(){
 const dates=[];let day=new Date('2024-01-02T00:00:00Z');while(dates.length<95){if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));day=new Date(day.getTime()+86400000);}
 const symbols=['sh600000','sh600001','sh600002'];
 const header={schema:'historical-package-v1',coverage:{startDate:dates[0],endDate:dates.at(-1)},declaration:{universe:'隔离合成股票池（非真实历史行情）',source:'smoke fixture',calendarSource:'isolated-independent-calendar',completeMembership:true,completeActions:true},dataset:{calendar:dates,adjustment:'raw',benchmark:{symbol:'sh000300',bars:dates.map((date,i)=>({date,close:3000+i}))}}};
 const lines=[JSON.stringify(header)];
 for(let stock=0;stock<3;stock++){
  const bars=[],precloseByDate={},stByDate={},tradeStatusByDate={};let previous=10+stock*.02;
  for(let i=0;i<dates.length;i++){const price=10+stock*.02+i*(.012+stock*.0002)-(i>=71?.5:0),date=dates[i];bars.push({date,open:price,close:price,high:price+.02,low:price-.02,volume:2000000,amount:price*2000000});precloseByDate[date]=previous-(i===71?.5:0);stByDate[date]=false;tradeStatusByDate[date]=1;previous=price;}
  lines.push(JSON.stringify({security:{symbol:symbols[stock],name:'fixture-'+stock,listingDate:'2000-01-01',bars,precloseByDate,stByDate,tradeStatusByDate,corporateActionsComplete:true,corporateActions:[{id:'dividend',announcementDate:dates[60],recordDate:dates[70],exDate:dates[71],cashPayDate:dates[73],cashNetPerShare:.5,source:'isolated announcement fixture',cashTaxModel:'declared_net'}]}}));
 }
 for(const date of dates)lines.push(JSON.stringify({date,members:symbols}));await fs.writeFile(input,lines.join('\n'));return dates;
}
async function complete(){await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await wait('!!document.querySelector(".pf-config-ready")','effective settings');await evaluate('document.querySelector("[data-pf-form]").requestSubmit()');await wait('AT.state.page==="R03"&&PortfolioWorkbench.currentStep()===5','create',15000);const id=await evaluate('document.querySelector("[data-rs-task]").value');await wait('!!document.querySelector(".rs-status-completed")','complete');const response=await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)}})`);assert.equal(response.ok,true,response.error);return {id,...response.data};}
app.whenReady().then(async()=>{
 const dates=await fixture();session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
 win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',(_event,details)=>{if(details.level==='error')errors.push(details.message);});window.webContents.on('preload-error',(_event,_file,error)=>errors.push(error.message));}});
 await evaluate('window.__refinementErrors=[];window.addEventListener("error",event=>__refinementErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__refinementErrors.push(String(event.reason)));');
 await navigate('R03');await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await wait('!!document.querySelector("[data-pf-form]")','form');await click('[data-pf-action="import-history"]');
 await wait(`document.querySelector('[data-pf-field="selection.source"]')?.value==="history"`,'native import');assert.equal(opened,1);
 assert.equal(await evaluate(`document.querySelector('[data-pf-field="config.startDate"]').value`),dates[0]);
 await screenshot('research-history-import.png','[data-pf-action="import-history"]');
 const denied=await evaluate(`atradeDesktop.business('research.history.importFile',{path:${JSON.stringify(input)}})`);assert.equal(denied.ok,false,'private file path cannot bypass native picker');
 const fields={name:'Smoke · 连续权益账本',kind:'walkForward','selection.benchmarkSymbol':'sh000300','config.startDate':dates[20],'config.endDate':dates[94],'config.universe.minListingDays':'0','config.universe.minAmount20':'0','params.momentum.lookback':'5','params.momentum.trendWindow':'8','params.momentum.minReturn':'0','config.topN':'2','config.maxPositions':'3','config.maxStockWeight':'35','config.maxHoldingDays':'0','config.stopLossPct':'0','config.takeProfitPct':'0','config.trailingStopPct':'0','config.rebalanceMode':'risk','experiment.trainDays':'35','experiment.testDays':'20','experiment.stepDays':'20','experiment.maxFolds':'2','experiment.capitalMode':'continuous'};
 for(const [name,value] of Object.entries(fields))await fill(name,value);
 const first=await complete();assert.equal(first.result.continuous.status,'complete');assert.equal(first.result.validationAudit.status,'fresh');assert.equal(first.result.continuous.protocol.capitalResetAtFoldBoundary,false);
 assert.equal(first.result.continuous.runs.equalWeight.daily.length,20);assert.equal(first.result.continuous.runs.equalWeight.quality.certified,true);assert.ok(first.result.continuous.runs.equalWeight.actionLedger.some(row=>row.stage==='cashReceived'));
 assert.ok(first.result.continuous.retrainLog.some(row=>Object.keys(row.positionsCarried).length));
 await screenshot('research-continuous-ledger.png','[data-pf-continuous]');
 await navigate('R06');await wait('!!document.querySelector("[data-rs-result-path]")','path selector');
 await evaluate('(()=>{const el=document.querySelector("[data-rs-result-path]");el.value="continuous.runs.equalWeight";el.dispatchEvent(new Event("change",{bubbles:true}));})()');
 await wait('document.querySelector("[data-rs-result-path]").value==="continuous.runs.equalWeight"','continuous path');
 await navigate('R09');await click('[data-rs-action="export-complete"]');await wait('document.querySelector("main").innerText.includes("完整报告已保存")','native complete export');assert.equal(saved,1);
 const whole=JSON.parse(await fs.readFile(output,'utf8'));assert.equal(whole.export.paginationApplied,false);assert.equal(whole.task.status,'completed');assert.equal(whole.result.continuous.runs.equalWeight.daily.length,40);assert.equal(whole.result.continuous.runs.equalWeight.actionLedger.filter(row=>row.stage==='cashReceived').length,first.result.continuous.runs.equalWeight.actionLedger.filter(row=>row.stage==='cashReceived').length);
 await evaluate('(()=>{const text=[...document.querySelectorAll("summary")].find(el=>el.textContent.includes("权益记录"));if(text){text.parentElement.open=true;const account=text.closest(".pf-run-detail");if(account)account.open=true;}return true;})()');
 await screenshot('research-rights-ledger.png','[data-pf-rights]');
 await navigate('R03');await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await fill('name','Smoke · 已使用区间改参');await fill('config.topN','3');const repeated=await complete();assert.equal(repeated.result.validationAudit.status,'developmentSample');assert.equal(repeated.result.continuous.rotationGate.passed,false);assert.deepEqual(repeated.result.plateau.recommendedRanges,[]);
 await screenshot('research-reused-holdout.png','[data-pf-validation-audit]');assert.deepEqual(await evaluate('__refinementErrors'),[]);assert.deepEqual(errors,[]);
 console.log('PASS refinement native import, actual dividends and carried positions, continuous path, complete save and reused holdout');win.close();setTimeout(()=>app.exit(0),300);
}).catch(error=>{console.error(error.stack);if(win)win.destroy();app.exit(1);});
