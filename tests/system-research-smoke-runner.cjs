const {app,protocol,dialog,session}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const directory=process.argv[2];if(!directory)throw new Error('Missing isolated smoke directory');
const root=path.resolve(__dirname,'..'),dataDirectory=path.join(directory,'workspace'),fixtureDirectory=path.join(directory,'tdx-fixture');
app.setPath('userData',path.join(directory,'electron-profile'));protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
dialog.showMessageBox=async(_window,options)=>{throw new Error(`Unexpected dialog: ${options.message}`);};
let win;const errors=[],ids={};
const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw new Error(`${error.message} [${code.slice(0,180)}]`);});
async function wait(expression,label,timeout=300000){const started=Date.now();while(Date.now()-started<timeout){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`${label}: ${await evaluate('document.querySelector("main").innerText.slice(-4000)')}`);}
const click=selector=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el||el.disabled)throw new Error('Missing enabled '+${JSON.stringify(selector)});el.click();})()`);
async function fill(name,value){await evaluate(`(()=>{const el=document.querySelector('[data-pf-field="${name}"]');if(!el)throw new Error('Missing field ${name}');if(el.type==='checkbox')el.checked=${JSON.stringify(value)};else el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(el.tagName==='SELECT'||el.type==='checkbox'?'change':'input',{bubbles:true}));})()`);}
async function navigate(page){await evaluate(`AT.navigate(${JSON.stringify(page)});true`);await wait(`!!document.querySelector('[data-research-page="${page}"]')`,`navigate ${page}`);}
async function screenshot(name,selector){await fs.mkdir(path.join(root,'artifacts'),{recursive:true});if(selector)await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});const target=el.closest('.rs-panel')||el;target.style.scrollMarginTop='90px';target.scrollIntoView({block:'start',behavior:'instant'});return true;})()`);await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await new Promise(resolve=>setTimeout(resolve,180));if(selector)await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});const target=el.closest('.rs-panel')||el;target.style.scrollMarginTop='90px';target.scrollIntoView({block:'start',behavior:'instant'});return true;})()`);await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await fs.writeFile(path.join(root,'artifacts',name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function fixture(){
 const dates=[];let day=new Date('2023-01-02T00:00:00Z');while(dates.length<340){if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));day=new Date(day.getTime()+86400000);}
 await fs.mkdir(path.join(fixtureDirectory,'vipdoc','sh','lday'),{recursive:true});
 for(let stock=0;stock<6;stock++){
  const bytes=Buffer.alloc(dates.length*32);let previous=1000;
  for(let i=0;i<dates.length;i++){const close=Math.round(1000+i*.3+65*Math.sin(i/10+stock*.5)),open=Math.round(previous*.999),at=i*32,volume=2000000+100000*Math.round(3+Math.sin(i/7+stock));bytes.writeUInt32LE(Number(dates[i].replaceAll('-','')),at);bytes.writeUInt32LE(open,at+4);bytes.writeUInt32LE(Math.max(open,close)+3,at+8);bytes.writeUInt32LE(Math.min(open,close)-3,at+12);bytes.writeUInt32LE(close,at+16);bytes.writeFloatLE(close/100*volume,at+20);bytes.writeUInt32LE(volume,at+24);previous=close;}
  await fs.writeFile(path.join(fixtureDirectory,'vipdoc','sh','lday','sh'+(600000+stock)+'.day'),bytes);
 }return dates;
}
async function complete(kind){
 await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await wait('!!document.querySelector(".pf-config-ready")','effective '+kind,15000);
 await evaluate('document.querySelector("[data-pf-form]").requestSubmit()');await wait('AT.state.page==="R03"&&PortfolioWorkbench.currentStep()===5','create '+kind,15000);
 const id=await evaluate('document.querySelector("[data-rs-task]").value');ids[kind]=id;
 await wait('document.querySelector(".rs-status-completed")','complete '+kind);
 const response=await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)}})`);assert.equal(response.ok,true,response.error);assert.equal(response.data.task.status,'completed');assert.equal(response.data.task.kind,kind);return response.data.result;
}
app.whenReady().then(async()=>{
 const dates=await fixture();session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
 win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',(_event,details)=>{if(details.level==='error')errors.push(details.message);});window.webContents.on('preload-error',(_event,_file,error)=>errors.push(error.message));}});
 await evaluate('window.__systemErrors=[];window.addEventListener("error",event=>__systemErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__systemErrors.push(String(event.reason)));');
 await wait('!!window.PortfolioWorkbench&&!!window.ResearchModule','modules ready');
 if(process.argv[3]==='review'){
  const saved=JSON.parse(await fs.readFile(path.join(directory,'tasks.json'),'utf8'));
  await navigate('G02');await wait(`!!document.querySelector('[data-rs-action="task-open"][data-rs-id="${saved.systemPlateau}"]')`,'saved plateau');await click(`[data-rs-action="task-open"][data-rs-id="${saved.systemPlateau}"]`);await navigate('R05');await wait('!!document.querySelector(".pf-heatmap")','saved heatmap');await screenshot('system-plateau-grid.png','.pf-heatmap');
  await navigate('G02');await click(`[data-rs-action="task-open"][data-rs-id="${saved.portfolio}"]`);await wait('!!document.querySelector("[data-pf-detail]")','saved rotation');await evaluate(`document.querySelector('[data-pf-detail="rotation:"]').open=true;true`);await screenshot('system-rotation-log.png','[data-pf-detail="rotation:"]');
  assert.deepEqual(await evaluate('__systemErrors'),[]);assert.deepEqual(errors,[]);console.log('PASS reviewed persisted system research screenshots');win.close();setTimeout(()=>app.exit(0),300);return;
 }

 const configured=await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`);assert.equal(configured.ok,true,configured.error);
 const scanned=await evaluate("atradeDesktop.market('market.scan')");assert.equal(scanned.ok,true,scanned.error);
 await navigate('R03');await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await wait('!!document.querySelector("[data-pf-form]")','form ready');
 await fill('periodMode','dates');await fill('selection.mode','explicit');
 for(const [name,value] of Object.entries({name:'Smoke · 持续性门控',symbols:'600000 600001 600002 600003 600004 600005','config.startDate':dates[260],'config.endDate':dates[315],'config.universe.minListingDays':'0','config.universe.minAmount20':'0','params.momentum.lookback':'5','params.momentum.trendWindow':'8','params.momentum.minReturn':'0','config.maxPositions':'3','config.topN':'2','config.maxStockWeight':'35','config.maxHoldingDays':'5','config.rotation.warmupDays':'40','config.rotation.lookbackDays':'5'}))await fill(name,value);
 await fill('kind','comparison');for(const [name,value] of Object.entries({'study.trainEnd':dates[294],'study.testStart':dates[295],'study.testEnd':dates[315]}))await fill(name,value);
 const compared=await complete('comparison');assert.equal(compared.rotationGate.status,'skipped');assert.equal(compared.testing.softRotation,undefined);assert.ok(compared.testing.fixed);assert.ok(compared.testing.equalWeight);assert.equal(compared.training.persistence.passed,false);
 await screenshot('system-comparison-gate.png','.rs-status-completed');
 await evaluate('for(const el of document.querySelectorAll(".pf-run-detail, .pf-run-detail details[data-pf-detail]"))el.open=true;true');
 await click('[data-pf-action="next-page"][data-pf-path="testing.equalWeight"]');await wait('document.querySelector("main").innerText.includes("第 21 — 21 日")','isolated nested daily paging');
 await navigate('R03');await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await fill('kind','walkForward');for(const [name,value] of Object.entries({name:'Smoke · 滚动样本外','experiment.trainDays':'35','experiment.testDays':'10','experiment.stepDays':'10','experiment.maxFolds':'2'}))await fill(name,value);
 const walk=await complete('walkForward');assert.equal(walk.folds.length,2);assert.equal(walk.trialCount,1);assert.ok(walk.folds.every(f=>f.testing.fixed&&f.testing.equalWeight));assert.equal(walk.plateau.certified,false);
 await navigate('R06');await wait('!!document.querySelector("[data-rs-result-path]")','fold account selector');await evaluate('(()=>{const el=document.querySelector("[data-rs-result-path]");el.value="folds.1.testing.equalWeight";el.dispatchEvent(new Event("change",{bubbles:true}));})()');await wait('document.querySelector("[data-rs-result-path]").value==="folds.1.testing.equalWeight"','select second fold');
 await navigate('R03');await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await fill('kind','systemPlateau');await fill('name','Smoke · 四层十六组合');await fill('combinations.weighted.enabled',true);
 const axes=[['strategies.momentum.params.lookback','5, 8'],['combinations.combo_weighted.method','union, weighted'],['rotation.lookbackDays','5, 8'],['topN','2, 3']];
 for(let i=0;i<axes.length;i++){await fill('scanAxes.'+i+'.path',axes[i][0]);await fill('scanAxes.'+i+'.text',axes[i][1]);}
 await screenshot('system-research-settings.png','[data-pf-field="experiment.trainDays"]');
 const plateau=await complete('systemPlateau');assert.equal(plateau.trialCount,16);assert.equal(plateau.folds.length,2);assert.equal(plateau.plateau.heatmap.length,16);assert.equal(plateau.plateau.certified,false);assert.deepEqual(plateau.plateau.recommendedRanges,[]);assert.ok(plateau.engineRunCount>0);
 await navigate('R05');assert.equal(await evaluate('document.querySelectorAll(".pf-heat-cell").length'),4);assert.equal(await evaluate('document.querySelectorAll("[data-pf-heat-slice]").length'),2);await screenshot('system-plateau-grid.png','.pf-heatmap');
 await navigate('R03');await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await fill('kind','portfolio');await fill('name','Smoke · 软配权执行诊断');await fill('config.allocation.mode','soft_rotation');await fill('config.rebalanceMode','rank');await fill('config.rotation.minWeightChange','0');await fill('config.rotation.cooldownDays','2');
 const rotation=await complete('portfolio');assert.equal(rotation.rotation.enabled,true);assert.equal(rotation.rotation.diagnosticOnly,true);assert.ok(rotation.rotationLog.length>0);assert.ok(rotation.daily.some(row=>row.rotationDecision));await evaluate(`document.querySelector('[data-pf-detail="rotation:"]').open=true;true`);await screenshot('system-rotation-log.png','[data-pf-detail="rotation:"]');
 await fs.writeFile(path.join(directory,'tasks.json'),JSON.stringify(ids));assert.deepEqual(await evaluate('__systemErrors'),[]);assert.deepEqual(errors,[]);console.log('PASS system comparison, walk-forward, 16-grid plateau, soft rotation, nested paging and fold path selection');win.close();setTimeout(()=>app.exit(0),300);
}).catch(error=>{console.error(error.stack);if(win)win.destroy();app.exit(1);});
