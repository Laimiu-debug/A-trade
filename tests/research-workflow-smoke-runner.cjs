/* Actual Electron workflow, using only a temporary workspace and generated bars. */
const {app,protocol,dialog,session}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const [directory,phase]=process.argv.slice(2);
if(!directory||!['write','read'].includes(phase))throw new Error('Missing workflow smoke arguments');
const root=path.resolve(__dirname,'..'),dataDirectory=path.join(directory,'workspace'),fixtureDirectory=path.join(directory,'tdx-fixture');
app.setPath('userData',path.join(directory,'profile'));
protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
dialog.showMessageBox=async(_window,options)=>{throw new Error(`Unexpected dialog: ${options.message}`);};
let win;const errors=[];
const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw new Error(`${error.message} [${code.slice(0,180)}]`);});
async function wait(expression,label,timeout=30000){const start=Date.now();while(Date.now()-start<timeout){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`${label}: ${await evaluate('document.querySelector("main")?.innerText.slice(-6500)')}`);}
const click=selector=>evaluate(`(()=>{const el=[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length);if(!el||el.disabled)throw new Error('Missing enabled '+${JSON.stringify(selector)});el.click();})()`);
const text=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).innerText`);
async function fill(name,value){await evaluate(`(()=>{const choices=[...document.querySelectorAll('[data-pf-field="${name}"]')],el=choices.find(el=>el.getClientRects().length)||choices[0];if(!el)throw new Error('Missing field ${name}');if(el.type==='checkbox')el.checked=${JSON.stringify(value)};else el.value=${JSON.stringify(String(value))};el.dispatchEvent(new Event(el.tagName==='SELECT'||el.type==='checkbox'?'change':'input',{bubbles:true}));})()`);}
async function workflowFill(name,value){await evaluate(`(()=>{const el=document.querySelector('[data-workflow-field="${name}"]');if(!el)throw new Error('Missing workflow ${name}');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
async function navigate(page){await evaluate(`AT.navigate(${JSON.stringify(page)});true`);await wait(`!!document.querySelector('[data-research-page="${page}"]')`,'navigate '+page);}
const step=index=>click(`[data-pf-action="workflow-step"][data-pf-step="${index}"]`);
const draft=key=>evaluate(`JSON.parse(localStorage.getItem(${JSON.stringify(key)})).draft`);
async function call(route,payload={}){const response=await evaluate(`atradeDesktop.business(${JSON.stringify(route)},${JSON.stringify(payload)})`);assert.equal(response.ok,true,JSON.stringify(response.error));return response.data;}
async function taskDone(id,timeout=180000){const start=Date.now();while(Date.now()-start<timeout){const data=await call('research.get',{id});if(data.task.status==='completed')return data;if(data.task.status==='failed')throw new Error(JSON.stringify(data.task.error));await new Promise(resolve=>setTimeout(resolve,120));}throw new Error('Task timeout '+id);}
async function submitted(kind,before){const start=Date.now();while(Date.now()-start<30000){const item=(await call('research.list')).tasks.find(row=>row.kind===kind&&!before.includes(row.id));if(item)return taskDone(item.id);await new Promise(resolve=>setTimeout(resolve,100));}throw new Error('No submitted '+kind+': '+await text('main'));}
const taskIds=async()=>((await call('research.list')).tasks.map(row=>row.id));
async function submitResearch(kind){await step(4);await wait('!!document.querySelector(".pf-config-ready")','valid '+kind);const before=await taskIds();await click('[data-pf-form] button[type="submit"]');return submitted(kind,before);}
async function screenshot(name,selector='main'){
 await fs.mkdir(path.join(root,'artifacts'),{recursive:true});
 const scroll=()=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(el){el.style.scrollMarginTop='95px';el.scrollIntoView({block:'start',behavior:'instant'});}})()`);
 await scroll();
 await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
 await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
 await new Promise(resolve=>setTimeout(resolve,120));
 await scroll();
 await fs.writeFile(path.join(root,'artifacts','research-workflow-'+name+'.png'),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());
}
async function fixture(){
 const dates=[];let day=new Date('2023-01-02T00:00:00Z');while(dates.length<500){if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));day=new Date(day.getTime()+86400000);}
 await fs.mkdir(path.join(fixtureDirectory,'vipdoc','sh','lday'),{recursive:true});
 for(let stock=0;stock<6;stock++){
  const bytes=Buffer.alloc(dates.length*32);let previous=1000;
  for(let i=0;i<dates.length;i++){const close=Math.round(1000+i*1.5+50*Math.sin(i/9+stock*.55)),open=Math.round(previous*.999),at=i*32,volume=2200000+100000*Math.round(3+Math.sin(i/7+stock));bytes.writeUInt32LE(Number(dates[i].replaceAll('-','')),at);bytes.writeUInt32LE(open,at+4);bytes.writeUInt32LE(Math.max(open,close)+3,at+8);bytes.writeUInt32LE(Math.min(open,close)-3,at+12);bytes.writeUInt32LE(close,at+16);bytes.writeFloatLE(close/100*volume,at+20);bytes.writeUInt32LE(volume,at+24);previous=close;}
  await fs.writeFile(path.join(fixtureDirectory,'vipdoc','sh','lday','sh'+(600000+stock)+'.day'),bytes);
 }return dates;
}
async function writePhase(){
 const dates=await fixture();
 assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`)).ok,true);
 assert.equal((await evaluate("atradeDesktop.market('market.scan')")).ok,true);
 await navigate('R03');await wait('!!document.querySelector("[data-pf-form]")','single form');
 await step(2);
 let single=await draft('atrade.single-editor.v1');
 assert.equal(single.kind,'singleBacktest');assert.equal(Object.values(single.enabled).filter(Boolean).length,1);
 assert.equal(single.config.allocation.mode,'single');assert.equal(single.selection.mode,'all');
 assert.equal(await evaluate('document.querySelectorAll(".pf-research-pipeline .pf-pipeline-stage").length'),6);
 await fill('singleStrategy','trendBreakout');
 await click('[data-pf-action="apply-pool-style"][data-pf-style="breakout"]');
 single=await draft('atrade.single-editor.v1');
 assert.equal(single.config.poolSettings.structure.minDrawdown,0);assert.deepEqual(single.config.poolSettings.combined.pools,['trend','structure']);
 assert.match(await text('[data-pf-style-summary]'),/突破候选/);
 await screenshot('breakout-funnel','.pf-style-funnel');
 await fill('singleStrategy','momentum');await click('[data-pf-action="apply-pool-style"][data-pf-style="momentum"]');
 single=await draft('atrade.single-editor.v1');assert.deepEqual(single.config.poolSettings.combined.pools,['trend']);assert.equal(single.config.poolSettings.trend.rankEnabled,true);
 const beforeAdvanced=structuredClone(single);
 await click('[data-pf-action="advanced"]');
 await step(4);await fill('name','Smoke · 高级草稿独立保留');await step(2);
 const advanced=await draft('atrade.portfolio-editor.v1');assert.equal(advanced.kind,'portfolio');assert.ok(Object.values(advanced.enabled).filter(Boolean).length>1);
 await screenshot('advanced-entry','[data-pf-form]');
 await click('[data-pf-action="single-stage"][data-pf-step="1"]');
 assert.deepEqual(await draft('atrade.single-editor.v1'),{...beforeAdvanced,workflowStep:1},'advanced draft does not overwrite the single strategy');
 await step(1);if(await evaluate('!!document.querySelector("[data-pf-field=periodMode]")'))await fill('periodMode','dates');await fill('config.startDate',dates[300]);await fill('config.endDate',dates[389]);
 await step(2);await fill('params.momentum.lookback','10');await fill('params.momentum.trendWindow','30');await fill('params.momentum.minReturn','0');
 await step(3);await fill('config.maxHoldingDays','5');await fill('config.maxPositions','3');await fill('config.maxStockWeight','30');
 await step(4);await fill('name','Smoke · 固定单策略');await screenshot('single-review','[data-pf-step-panel="4"]');
 const fixed=await submitResearch('singleBacktest');
 assert.equal(fixed.task.config.strategies.length,1);assert.equal(fixed.task.config.strategies[0].kind,'momentum');assert.equal(fixed.task.config.strategies[0].pool,'combined');
 assert.equal(fixed.task.config.combinations.length,0);assert.equal(fixed.task.config.allocation.mode,'single');
 await wait('!!document.querySelector("[data-workflow-action=plateau]")','next plateau action');
 await screenshot('fixed-result','[data-workflow-action="plateau"]');
 await click('[data-workflow-action="plateau"]');await wait('AT.state.page==="R05"&&!!document.querySelector("[data-pf-form]")','carry fixed config to single plateau');
 let plateau=await draft('atrade.single-plateau-editor.v1');assert.equal(plateau.kind,'singlePlateau');
 assert.deepEqual(plateau.config.poolSettings,fixed.task.config.poolSettings);assert.equal(plateau.config.maxHoldingDays,5);
 await step(2);await fill('scanAxes.0.path','strategies.momentum.params.lookback');await fill('scanAxes.0.text','8, 10, 12');
 for(let i=1;i<3;i++)await fill('scanAxes.'+i+'.path','');
 await screenshot('single-scan','[data-pf-step-panel="2"]');
 await step(4);await fill('name','Smoke · 三点关键参数平原');const scanned=await submitResearch('singlePlateau');
 assert.equal(scanned.result.plannedTrials,3);assert.equal(scanned.result.candidates.length,3);
 assert.deepEqual(scanned.result.experiment.axes,[{path:'strategies.momentum.params.lookback',values:[8,10,12]}]);
 assert.equal(scanned.result.certified,false,'synthetic short development data is never certification');
 assert.equal(scanned.result.config.poolSettings.trend.rankEnabled,true);
 assert.equal(scanned.result.experiment.minClosedTrades,3,'real evidence thresholds were not lowered for the smoke');
 await wait('document.querySelectorAll(".pf-candidate").length===3&&!!document.querySelector("[data-pf-action=freeze-candidate]:not(:disabled)")','three completed candidates ready to freeze');
 const candidateId='c002';await screenshot('plateau-result','.pf-single-candidates');
 const beforeReplay=await taskIds();
 await click(`[data-pf-action="replay-candidate"][data-pf-candidate="${candidateId}"]`);
 const replay=await submitted('singleBacktest',beforeReplay);
 assert.equal(replay.task.parentId,scanned.task.id);assert.equal(replay.task.candidateId,candidateId);
 assert.equal(replay.task.fingerprint,scanned.task.fingerprint,'candidate detail reuses the frozen development input');
 const originalMetrics=scanned.result.candidates.find(row=>row.candidateId===candidateId).metrics;
 for(const key of ['totalReturnPct','maxDrawdownPct','finalEquity','totalFees','totalSlippage','turnoverPct','closedTradeCount','tradeCount'])assert.ok(Math.abs(replay.result.metrics[key]-originalMetrics[key])<1e-10,'candidate detail reproduces its original account metric '+key);
 await wait('AT.state.page==="R04"&&!!document.querySelector("[data-pf-chart]")','candidate account curve');
 const replayDay=replay.result.curve[10].date;
 await evaluate(`(()=>{const el=document.querySelector('[data-pf-date-select]');el.value=${JSON.stringify(replayDay)};el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
 await wait(`!!document.querySelector('[data-pf-day-detail][data-pf-date="${replayDay}"] .pf-day-summary')`,'selected-day operations');
 assert.match(await text('[data-pf-day-detail]'),/今日已成交/);assert.match(await text('[data-pf-day-detail]'),/收盘持仓/);assert.match(await text('[data-pf-day-detail]'),/下一交易日计划/);
 await screenshot('candidate-daily-detail','[data-pf-day-detail]');
 await navigate('R05');
 await evaluate(`(()=>{const el=document.querySelector('[data-rs-task]');el.value=${JSON.stringify(scanned.task.id)};el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
 await wait('document.querySelectorAll(".pf-candidate").length===3&&!!document.querySelector("[data-pf-action=freeze-candidate]:not(:disabled)")','return to original plateau');
 await click(`[data-pf-action="freeze-candidate"][data-pf-candidate="${candidateId}"]`);
 await wait('AT.state.page==="R10"&&!!document.querySelector("[data-workflow-validation]")','frozen later-period page');
 const freezeId=await evaluate('document.querySelector("[data-workflow-freeze]").value');
 const frozen=(await call('research.freezes')).freezes.find(row=>row.id===freezeId);
 assert.equal(frozen.candidateId,candidateId);assert.equal(frozen.config.strategies[0].params.lookback,10);assert.equal(frozen.immutable,true);
 assert.equal(frozen.developmentEnd,dates[389]);
 assert.deepEqual(await evaluate('[...document.querySelectorAll("[data-workflow-validation] input")].map(el=>el.dataset.workflowField)'),['name','startDate','endDate']);
 assert.equal(await evaluate('document.querySelectorAll("[data-workflow-validation] [data-pf-field]").length'),0);
 await workflowFill('name','Smoke · 冻结后新时段');await workflowFill('startDate',dates[390]);await workflowFill('endDate',dates[439]);
 await screenshot('frozen-validation','[data-workflow-validation]');
 const before=await taskIds();await click('[data-workflow-validation] button[type="submit"]');const validated=await submitted('frozenValidation',before);
 assert.equal(validated.result.freeze.id,freezeId);assert.equal(validated.result.freeze.parameterHash,frozen.parameterHash);
 assert.deepEqual({...validated.task.config,startDate:frozen.config.startDate,endDate:frozen.config.endDate},frozen.config);
 assert.equal(validated.result.validationAudit.status,'fresh');
 await wait('AT.state.page==="R04"','new interval result route');await screenshot('validation-result','[data-pf-validation-audit]');
 await navigate('R10');await wait('!!document.querySelector("[data-workflow-validation]")','repeat interval form');
 await workflowFill('name','Smoke · 同区间复现');await workflowFill('startDate',dates[390]);await workflowFill('endDate',dates[439]);
 const beforeRepeat=await taskIds();await click('[data-workflow-validation] button[type="submit"]');const repeated=await submitted('frozenValidation',beforeRepeat);
 assert.equal(repeated.result.validationAudit.status,'reproduction','same settings and interval are not fresh evidence');
 // Preserve a task from the existing advanced engine alongside the new stages.
 const legacyTask=await call('research.create',{kind:'portfolio',name:'Smoke · 旧流程报告保留',config:fixed.task.config,selection:fixed.task.selection});
 const legacy=await taskDone(legacyTask.id);
 const record={fixedId:fixed.task.id,scannedId:scanned.task.id,replayId:replay.task.id,validationId:validated.task.id,repeatId:repeated.task.id,legacyId:legacy.task.id,freezeId,parameterHash:frozen.parameterHash,candidateId,developmentEnd:dates[389],validationStart:dates[390],validationEnd:dates[439]};
 await fs.writeFile(path.join(directory,'workflow.json'),JSON.stringify(record,null,2));
 await fs.writeFile(path.join(root,'artifacts','research-workflow-result.json'),JSON.stringify({synthetic:true,...record,trialCount:3,certified:scanned.result.certified,validationStatus:validated.result.validationAudit.status,repeatStatus:repeated.result.validationAudit.status},null,2));
}
async function readPhase(){
 const record=JSON.parse(await fs.readFile(path.join(directory,'workflow.json'),'utf8'));
 await navigate('R10');await wait('!!document.querySelector("[data-workflow-freeze]")','restored freeze list');
 assert.equal(await evaluate('document.querySelector("[data-workflow-freeze]").value'),record.freezeId);
 const frozen=(await call('research.freezes')).freezes.find(row=>row.id===record.freezeId);assert.equal(frozen.parameterHash,record.parameterHash);assert.equal(frozen.candidateId,record.candidateId);assert.equal(frozen.versionCompatible,true);
 for(const [key,kind] of [['fixedId','singleBacktest'],['scannedId','singlePlateau'],['replayId','singleBacktest'],['validationId','frozenValidation'],['repeatId','frozenValidation'],['legacyId','portfolio']]){const saved=await call('research.get',{id:record[key]});assert.equal(saved.task.status,'completed');assert.equal(saved.task.kind,kind);assert.ok(saved.result);}
 assert.equal((await call('research.get',{id:record.repeatId})).result.validationAudit.status,'reproduction');
 await screenshot('restored-freeze','[data-workflow-freeze]');
 await navigate('R05');
 await evaluate(`(()=>{const el=document.querySelector('[data-rs-task]');el.value=${JSON.stringify(record.scannedId)};el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
 await wait('document.querySelectorAll(".pf-candidate").length===3','restored completed parameter points');
 await new Promise(resolve=>setTimeout(resolve,1100));
 await screenshot('plateau-result','.pf-candidate[data-pf-candidate="c002"]');
}
app.whenReady().then(async()=>{
 session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
 win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',(_event,details)=>{if(details.level==='error')errors.push(details.message);});window.webContents.on('preload-error',(_event,_file,error)=>errors.push(error.message));}});
 await evaluate('window.__workflowErrors=[];window.addEventListener("error",event=>__workflowErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__workflowErrors.push(String(event.reason)));');
 if(phase==='write')await writePhase();else await readPhase();
 assert.deepEqual(await evaluate('__workflowErrors'),[]);assert.deepEqual(errors,[]);
 console.log('PASS research workflow '+phase);win.close();setTimeout(()=>app.exit(0),300);
}).catch(async error=>{console.error(error.stack);try{await screenshot('failure-'+phase);}catch{}if(win)win.destroy();app.exit(1);});
