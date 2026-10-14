const {app,protocol,dialog}=require('electron'),assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const [directory,phase]=process.argv.slice(2),root=path.resolve(__dirname,'..'),marketPath=path.join(directory,'chosen-market'),emptyPath=path.join(directory,'empty-market');
if(!directory||!['write','read','skipped'].includes(phase))throw Error('Missing isolated onboarding fixture');
app.setPath('userData',path.join(directory,'profile'));protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
let picker={canceled:true,filePaths:[]},win,heldRead,hold=false;const calls=[],aiCalls=[],errors=[];
dialog.showOpenDialog=async()=>picker;dialog.showMessageBox=async()=>{throw Error('Unexpected confirmation')};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function fixture(){
 for(const folder of [marketPath,emptyPath]){await fs.mkdir(path.join(folder,'vipdoc/sh/lday'),{recursive:true});await fs.mkdir(path.join(folder,'T0002/hq_cache'),{recursive:true});}
 const dates=[];for(let day=new Date('2026-05-04T00:00:00Z');dates.length<90;day=new Date(+day+86400000))if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));
 const bytes=Buffer.alloc(dates.length*32);for(const [index,date] of dates.entries()){const offset=index*32,opening=1000+index;for(const [n,value] of [Number(date.replaceAll('-','')),opening,opening+10,opening-10,opening+5].entries())bytes.writeUInt32LE(value,offset+n*4);bytes.writeFloatLE((opening+5)*1000,offset+20);bytes.writeUInt32LE(100000,offset+24);}
 await fs.writeFile(path.join(marketPath,'vipdoc/sh/lday/sh600999.day'),bytes);const names=Buffer.alloc(50+301*360);
 for(let i=0;i<300;i++){names.write(String(600001+i),50+i*360);names.write('NAME ONLY '+i,81+i*360)}
 names.write('600999',50+300*360);names.write('STARTER STOCK',81+300*360);await fs.writeFile(path.join(marketPath,'T0002/hq_cache/shs.tnf'),names);
}
app.whenReady().then(async()=>{
 try{
  if(phase==='write')await fixture();
  const guard=path.join(directory,'guard');await fs.mkdir(guard,{recursive:true});await fs.writeFile(path.join(guard,'sitecustomize.py'),"import urllib.request\ndef blocked(*args, **kwargs):\n raise RuntimeError('Public network disabled in onboarding smoke')\nurllib.request.OpenerDirector.open = blocked\n");process.env.PYTHONPATH=[guard,root].join(path.delimiter);
  const {MarketService}=require('../desktop/market-service.cjs'),request=MarketService.prototype.request;
  MarketService.prototype.request=function(method,payload){calls.push(method);if(hold&&method==='market.bars'){hold=false;return new Promise(resolve=>{heldRead=()=>request.call(this,method,payload).then(resolve)})}return request.call(this,method,payload)};
  const {AIService}=require('../desktop/ai-service.cjs'),dispatch=AIService.prototype.dispatch;AIService.prototype.dispatch=function(method,payload){aiCalls.push(method);return dispatch.call(this,method,payload)};
  win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory:path.join(directory,'workspace'),show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',event=>{if(event.level==='error')errors.push(event.message)})}});
  const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw Error(error.message+' ['+code.slice(0,160)+']')});
  const wait=async(expression,label)=>{for(let i=0;i<250;i++){if(await evaluate(expression))return;await delay(60)}throw Error(label+': '+await evaluate('document.querySelector("#main")?.innerText.slice(-2600)'))};
  const click=async selector=>{const target=`Array.from(document.querySelectorAll(${JSON.stringify(selector)})).find(el=>!el.disabled&&el.getClientRects().length>0)`;await wait(`!!(${target})`,'active '+selector);await evaluate(`(${target}).click();true`)};
  const action=name=>click(`[data-onboarding-action="${name}"]`),marketAction=name=>click(`[data-market-action="${name}"]`);
  if(phase==='write'){
   await wait('AT.state.page==="C08"&&!!document.querySelector("[data-onboarding-page]")','new-user entry');await wait('!!AT.marketUI.status','source status');
   assert.equal(await evaluate('document.querySelector("[data-onboarding-action=verify]").disabled'),true);assert.equal(calls.includes('market.bars'),false);assert.equal(calls.includes('market.research'),false);
   await marketAction('choose');await wait('!AT.marketUI.setupBusy','cancel picker');assert.equal(await evaluate('AT.marketUI.status.configured'),false);
   picker={canceled:false,filePaths:[marketPath]};await marketAction('choose');await wait(`document.querySelector('#market-source-path')?.value===${JSON.stringify(marketPath)}`,'chosen directory');
   await marketAction('configure');await wait('AT.marketUI.status.symbolCount===1&&!AT.marketUI.setupBusy','real index');assert.equal(await evaluate('AT.onboarding?document.querySelector(".onboarding-count").textContent.trim().startsWith("1"):false'),true);
   hold=true;await action('verify');for(let i=0;i<150&&!heldRead;i++)await delay(40);assert.ok(heldRead);await action('stop');heldRead();await delay(150);assert.equal(await evaluate('AT.state.onboarding.sample'),null,'late bars cannot complete a cancelled check');
   const beforeReads=calls.filter(method=>method==='market.bars').length;
   await evaluate('document.querySelector("[data-onboarding-action=verify]").click();document.querySelector("[data-onboarding-action=verify]").click();true');await wait('!!document.querySelector("[data-onboarding-sample]")&&!document.querySelector("[data-onboarding-action=verify]").disabled','actual daily validation');
   assert.equal(calls.filter(method=>method==='market.bars').length,beforeReads+1,'double click reads just one stock');
   assert.equal(await evaluate('AT.state.onboarding.sample.count'),90);assert.equal(await evaluate('document.querySelector("[data-onboarding-action=finish]").disabled'),true,'a connection alone does not claim the study step');
   await evaluate('window.__persistOnboarding=AT.persist;AT.persist=async()=>false;true');await click('[data-onboarding-entry="R11"]');await wait('!!document.querySelector(".onboarding-feedback.error")','save failure');assert.equal(await evaluate('AT.state.page'),'C08');
   await evaluate('AT.persist=window.__persistOnboarding;true');await action('save');await wait('!document.querySelector(".onboarding-feedback.error")','save retry');await click('[data-onboarding-entry="R11"]');await wait('AT.state.page==="R11"&&!document.querySelector("#onboarding-resume").hidden','resumable workflow');
   await click('#onboarding-resume [data-nav="C08"]');await wait('!!document.querySelector("[data-onboarding-page]")','resume setup');
   for(const width of [1480,820]){win.setSize(width,1000);await delay(80);assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false,'no overflow '+width);}
   win.setSize(1480,1000);await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await fs.writeFile(path.join(root,'artifacts/onboarding-native.png'),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());
   await evaluate('AT.navigate("M04");AT.persist()');
  }else if(phase==='read'){
   assert.equal(await evaluate('AT.state.page'),'M04','restart preserves chosen page');assert.equal(await evaluate('AT.state.onboarding.status'),'active');assert.equal(await evaluate('AT.state.onboarding.sample.count'),90);
   await click('#onboarding-resume [data-nav="C08"]');await wait('!!document.querySelector("[data-onboarding-sample]")','saved check');await action('finish');await wait('AT.state.onboarding.status==="complete"&&AT.state.page==="R11"','finish guide');assert.equal(await evaluate('document.querySelector("#onboarding-resume").hidden'),true);
   assert.equal(JSON.parse(await fs.readFile(path.join(directory,'workspace/workspace-state.json'),'utf8')).state.onboarding.status,'complete');
   await evaluate('AT.navigate("C08");true');await click('[data-onboarding-step="0"]');
   await marketAction('scan');await wait('!AT.marketUI.setupBusy&&!document.querySelector("[data-onboarding-sample]")','new scan invalidates old proof');assert.equal(await evaluate('document.querySelector("[data-onboarding-action=finish]").disabled'),true);
   picker={canceled:false,filePaths:[emptyPath]};await marketAction('choose');await wait(`document.querySelector('#market-source-path')?.value===${JSON.stringify(emptyPath)}`,'choose empty data');await marketAction('configure');await wait('!AT.marketUI.setupBusy&&AT.marketUI.status.symbolCount===0','empty directory');assert.equal(await evaluate('document.querySelector("[data-onboarding-action=verify]").disabled'),true);
   await action('dismiss');await wait('AT.state.onboarding.status==="dismissed"&&AT.state.page!=="C08"','skip persists');assert.equal(await evaluate('!!document.querySelector("#onboarding-resume:not([hidden])")'),false);
  }else{
   assert.equal(await evaluate('AT.state.onboarding.status'),'dismissed');assert.notEqual(await evaluate('AT.state.page'),'C08');assert.equal(await evaluate('!!document.querySelector("#onboarding-resume:not([hidden])")'),false);
   await evaluate('AT.navigate("C08");true');await wait('!!document.querySelector("[data-onboarding-page]")','reopen skipped guide');assert.equal(await evaluate('document.querySelector("[data-onboarding-action=verify]").disabled'),true);
  }
  assert.equal(aiCalls.filter(method=>['page.ask','test','review','recognize'].includes(method)).length,0,'guide never invokes a model');
  assert.deepEqual(errors,[]);console.log('PASS onboarding '+phase+': native UI/IPC, actual market data and durable progress');
  await evaluate('AT.persist()');const closed=new Promise(resolve=>win.once('closed',resolve));win.close();await closed;app.exit(0);
 }catch(error){console.error(error.stack);win?.destroy();app.exit(1)}
});
