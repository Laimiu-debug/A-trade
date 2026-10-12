const {app,protocol,dialog,session}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const [directory,phase]=process.argv.slice(2);if(!directory||!['write','read'].includes(phase))throw new Error('Missing smoke arguments');
const root=path.resolve(__dirname,'..'),dataDirectory=path.join(directory,'workspace'),fixtureDirectory=path.join(directory,'tdx-fixture');
app.setPath('userData',path.join(directory,'electron-profile'));protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
dialog.showMessageBox=async(_window,options)=>{throw new Error(`Unexpected dialog: ${options.message}`);};
let win;const errors=[];
const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw new Error(`${error.message} [${code.slice(0,180)}]`);});
async function wait(expression,label,timeout=90000){const started=Date.now();while(Date.now()-started<timeout){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`${label}: ${await evaluate('document.querySelector("main").innerText.slice(-3000)')}`);}
const click=selector=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el||el.disabled)throw new Error('Missing enabled '+${JSON.stringify(selector)});el.click();})()`);
async function fill(name,value){await evaluate(`(()=>{const el=document.querySelector('[data-pf-field="${name}"]');if(!el)throw new Error('Missing field ${name}');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));})()`);}
async function navigate(page){await evaluate(`AT.navigate(${JSON.stringify(page)});true`);await wait(`!!document.querySelector('[data-research-page="${page}"]')`,`navigate ${page}`);}
async function screenshot(name,selector){await fs.mkdir(path.join(root,'artifacts'),{recursive:true});await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');if(selector)await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});(el.closest('.rs-panel')||el).scrollIntoView({block:'start',behavior:'instant'});})()`);await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await new Promise(resolve=>setTimeout(resolve,180));await fs.writeFile(path.join(root,'artifacts',name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function fixture(){
 const dates=[];let day=new Date('2023-01-02T00:00:00Z');while(dates.length<340){if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));day=new Date(day.getTime()+86400000);}
 for(const market of ['sh','sz','bj'])await fs.mkdir(path.join(fixtureDirectory,'vipdoc',market,'lday'),{recursive:true});
 const symbols=[...Array.from({length:130},(_,i)=>'sh'+(600000+i)),...['sz000001','sz300001','sz300002','sh688001','sh688002','bj920001']];
 for(let stock=0;stock<symbols.length;stock++){
  const symbol=symbols[stock],bytes=Buffer.alloc(dates.length*32);let previous=1000;
  for(let i=0;i<dates.length;i++){const close=Math.round(1000+i*.3+35*Math.sin(i/10+stock*.01)),open=Math.round(previous*.999),at=i*32,volume=2000000;bytes.writeUInt32LE(Number(dates[i].replaceAll('-','')),at);bytes.writeUInt32LE(open,at+4);bytes.writeUInt32LE(Math.max(open,close)+3,at+8);bytes.writeUInt32LE(Math.min(open,close)-3,at+12);bytes.writeUInt32LE(close,at+16);bytes.writeFloatLE(close/100*volume,at+20);bytes.writeUInt32LE(volume,at+24);previous=close;}
  await fs.writeFile(path.join(fixtureDirectory,'vipdoc',symbol.slice(0,2),'lday',symbol+'.day'),bytes);
 }
 await fs.writeFile(path.join(directory,'dates.json'),JSON.stringify(dates));
}
app.whenReady().then(async()=>{
 if(phase==='write')await fixture();const dates=JSON.parse(await fs.readFile(path.join(directory,'dates.json'),'utf8'));
 session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
 win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',(_event,details)=>{if(details.level==='error')errors.push(details.message);});window.webContents.on('preload-error',(_event,_file,error)=>errors.push(error.message));}});
 await evaluate('window.__portfolioErrors=[];window.addEventListener("error",event=>__portfolioErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__portfolioErrors.push(String(event.reason)));');
 await wait('!!window.PortfolioWorkbench&&!!window.ResearchModule','modules ready');
 if(phase==='write'){
  const configured=await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`);assert.equal(configured.ok,true,configured.error);
  const scanned=await evaluate("atradeDesktop.market('market.scan')");assert.equal(scanned.ok,true,scanned.error);
  await navigate('R01');await wait('document.querySelectorAll(".rs-strategy").length===6','six strategy catalogue');
  await click('[data-rs-action="strategy-use"][data-rs-id="momentum"]');await wait('!!document.querySelector("[data-pf-form]")','portfolio form');
  await fill('periodMode','dates');
  for(const [name,value]of Object.entries({name:'Smoke · 全池风险组合','config.startDate':dates[260],'config.endDate':dates.at(-1),'params.momentum.lookback':'5','params.momentum.trendWindow':'8','params.momentum.minReturn':'0','config.topN':'4','config.maxPositions':'3','config.maxStockWeight':'35','config.maxHoldingDays':'5','config.takeProfitPct':'0','config.stopLossPct':'4'}))await fill(name,value);
  await click('[data-pf-action="workflow-step"][data-pf-step="4"]');await wait('!!document.querySelector(".pf-config-ready")','effective configuration');await screenshot('portfolio-workbench.png','[data-pf-form]');
  await evaluate('document.querySelector("[data-pf-form]").requestSubmit()');await wait('AT.state.page==="R03"&&PortfolioWorkbench.currentStep()===5','create task');
  await wait('document.querySelector(".rs-status-completed")&&document.querySelector(".pf-account-curve")','completed portfolio');
  const id=await evaluate('document.querySelector("[data-rs-task]").value');const response=await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)},dailyPageSize:200})`);assert.equal(response.ok,true,response.error);const {task,result}=response.data;
  assert.equal(task.kind,'portfolio');assert.equal(task.symbolCount,136);assert.equal(result.daily.length,80);assert.equal(result.config.topN,4);assert.equal(result.config.maxPositions,3);assert.equal(result.config.rebalanceMode,'risk');assert.equal(result.config.stopLossPct,.04);assert.equal(result.config.stampDutyRate,null);assert.equal(result.quality.coverage.sourceCoverage.loadedCount,136);assert.ok(result.trades.length>0);assert.ok(result.trades.some(t=>t.side==='sell'&&['maxHoldingDays','stopLoss','trailingStop'].includes(t.reason)),'real risk exit');assert.ok(result.daily.some(d=>d.holdings.length>0));assert.ok(result.daily.every(d=>d.holdings.length<=3));
  await click('[data-pf-action="next-day"]');assert.match(await evaluate('document.querySelector("main").innerText'),new RegExp(dates[261]));await screenshot('portfolio-result.png','.pf-account-curve');await click('[data-pf-action=next-page]');await wait('document.querySelector("main").innerText.includes("第 21 — 40 日")','daily second page');const displayIndex=result.daily.findIndex((d,i)=>i>=20&&d.holdings.length>0&&Object.values(d.topNByStrategy).some(rows=>rows.length));if(displayIndex>=20){for(let offset=20;offset<Math.floor(displayIndex/20)*20;offset+=20){await click('[data-pf-action=next-page]');await wait(`document.querySelector('main').innerText.includes('第 ${offset+21} —')`,'holdings page');}for(let i=0;i<displayIndex%20;i++)await click('[data-pf-action=next-day]');}await screenshot('portfolio-daily.png','[data-pf-action=previous-day]');
  await fs.writeFile(path.join(directory,'result.json'),JSON.stringify({id,curve:result.curve,tradeCount:result.metrics.tradeCount}));
 }else{
  const saved=JSON.parse(await fs.readFile(path.join(directory,'result.json'),'utf8'));await navigate('G02');await wait(`!!document.querySelector('[data-rs-action="task-open"][data-rs-id="${saved.id}"]')`,'persistent task');await click(`[data-rs-action="task-open"][data-rs-id="${saved.id}"]`);await wait('!!document.querySelector(".pf-account-curve")','restored real result');
  const response=await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(saved.id)}})`);assert.equal(response.data.task.status,'completed');assert.deepEqual(response.data.result.curve,saved.curve);assert.equal(response.data.result.metrics.tradeCount,saved.tradeCount);await screenshot('portfolio-reopened.png','.pf-account-curve');
  await navigate('R03');await click('[data-pf-action="workflow-step"][data-pf-step="4"]');assert.equal(await evaluate('document.querySelector("[data-pf-field=name]").value'),'Smoke · 全池风险组合');assert.equal(await evaluate('document.querySelector("[data-pf-field="+CSS.escape("config.maxPositions")+"]").value'),'3');
 }
 assert.deepEqual(await evaluate('__portfolioErrors'),[]);assert.deepEqual(errors,[]);console.log(`PASS portfolio ${phase}`);win.close();setTimeout(()=>app.exit(0),300);
}).catch(error=>{console.error(error.stack);if(win)win.destroy();app.exit(1);});
