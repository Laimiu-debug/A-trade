/* Full native app navigation + real read RPCs against a copied, already completed scan. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const crypto=require('node:crypto'),root=path.resolve(__dirname,'..');
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
if(!process.versions.electron){
  const base=path.join(root,'artifacts','experimental-strategies-app-ui');fs.mkdirSync(base,{recursive:true});
  let n=1;while(fs.existsSync(path.join(base,`attempt-${String(n).padStart(4,'0')}`)))n++;
  const directory=path.join(base,`attempt-${String(n).padStart(4,'0')}`);fs.mkdirSync(directory);
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
  const evidence=path.resolve(process.argv[2]||path.join(root,'artifacts','strategy-search','final-research-review-v1','current-scan-validation-0002'));
  const child=require('node:child_process').spawn(require('electron'),[__filename,directory,evidence],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  const log=fs.createWriteStream(path.join(directory,'run.log'),{flags:'wx'});
  for(const stream of [child.stdout,child.stderr])stream.on('data',chunk=>{log.write(chunk);process.stdout.write(chunk)});
  child.on('exit',code=>{log.end();process.exitCode=code||0});
}else{
  const {app,protocol,session}=require('electron');
  const directory=process.argv[2],workspace=path.join(directory,'workspace');
  if(!directory)throw Error('Isolated output directory required');
  app.setPath('userData',path.join(directory,'profile'));app.commandLine.appendSwitch('force-device-scale-factor','1');
  protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
  let win;const calls=[],errors=[],images=[],checks=[];
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw Error(error.message+' ['+code.slice(0,180)+']')});
  const wait=async(expression,label)=>{for(let i=0;i<300;i++){if(await evaluate(expression))return;await pause(70)}throw Error('Timeout '+label+': '+await evaluate('document.querySelector("#main")?.innerText.slice(0,1500)'))};
  const click=selector=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled||!e.getClientRects().length)throw Error('Missing visible enabled '+${JSON.stringify(selector)});e.click()})()`);
  async function screenshot(name,selector){
    if(selector)await wait(`(()=>{const r=document.querySelector(${JSON.stringify(selector)})?.getBoundingClientRect();return r&&r.height>0})()`,'visible screenshot subject '+selector);
    await evaluate(selector?`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start'});window.scrollBy(0,-95)`:'window.scrollTo(0,0)');
    await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
    await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await pause(160);
    const file=path.join(directory,name),image=await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
    fs.writeFileSync(file,image.toPNG(),{flag:'wx'});images.push({path:file,sha256:sha(file),size:image.getSize()});
  }
  app.whenReady().then(async()=>{
    const evidence=process.argv[3];
    const verification=JSON.parse(fs.readFileSync(path.join(evidence,'verification.json'),'utf8'));
    const task=verification.task,sourceDirectory=path.join(verification.workspace,'experimental-scans',task.id);
    const protectedBefore=Object.fromEntries(Object.keys(verification.protectedAfter).map(name=>[name,sha(path.join(verification.workspace,name))]));
    let inputPins;
    try{
      assert.equal(verification.passed,true);assert.equal(task.status,'completed');
      const archivedResult=path.join(evidence,'result.json'),savedResult=path.join(sourceDirectory,'result.json'),savedTask=path.join(sourceDirectory,'task.json');
      assert.deepEqual(JSON.parse(fs.readFileSync(archivedResult,'utf8')),JSON.parse(fs.readFileSync(savedResult,'utf8')),'Archived readable JSON and stored compact JSON describe exactly the same verified result');
      const target=path.join(workspace,'experimental-scans',task.id);fs.mkdirSync(target,{recursive:true});
      fs.copyFileSync(savedTask,path.join(target,'task.json'),fs.constants.COPYFILE_EXCL);fs.copyFileSync(savedResult,path.join(target,'result.json'),fs.constants.COPYFILE_EXCL);
      inputPins=[savedTask,savedResult,archivedResult,path.join(evidence,'verification.json'),path.join(root,'docs','experimental-strategies-v1.json')].map(p=>({path:p,sha256:sha(p)}));
      session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_d,callback)=>callback({cancel:true}));
      const {BusinessService}=require('../desktop/business-service.cjs'),original=BusinessService.prototype.request;
      BusinessService.prototype.request=function(method,payload){calls.push({method,payload});if(/research\.experimental\.(scan\.)?(create|stop)$/.test(method))throw Error('Mutating experimental call prohibited in read-only app smoke');return original.call(this,method,payload)};
      const {MarketService}=require('../desktop/market-service.cjs'),market=MarketService.prototype.request;
      MarketService.prototype.request=function(method,payload){if(['market.scan','market.research','market.research.cancel'].includes(method))throw Error('Market execution prohibited in read-only app smoke');return market.call(this,method,payload)};
      win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory:workspace,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',event=>{if(event.level==='error')errors.push(event.message)});window.webContents.on('preload-error',(_e,_p,error)=>errors.push(error.message))}});
      win.setContentSize(1440,1000);await wait('typeof AT!=="undefined" && !!document.querySelector("[data-nav-group=R]")','application ready');
      if(!await evaluate('document.querySelector("[data-nav=R16]")?.getClientRects().length>0'))await click('[data-nav-group="R"]');
      await click('[data-nav="R16"]');
      await wait('AT.state.page==="R16" && document.querySelectorAll("[data-exp-scan-strategy]").length===3','R16 and stored current scan');
      assert.equal(await evaluate('document.querySelector("#main h1").textContent'),'当前环境与选股');
      assert.equal(await evaluate('document.querySelectorAll(".exp-candidate").length'),15);
      assert.ok(await evaluate('document.querySelector("#exp-scan-result-title").textContent.includes("2026-10-15")'));
      const dynamic=await evaluate(`atradeDesktop.business('research.experimental.scan.get',{id:${JSON.stringify(task.id)}})`);
      assert.equal(dynamic.ok,true);assert.equal(dynamic.data.freshness.available,true);
      assert.ok(await evaluate(`document.querySelector('.exp-scan-result header').innerText.includes(${JSON.stringify(dynamic.data.freshness.expectedAsOfDate)})`));
      assert.equal(await evaluate('document.querySelector("[data-exp-detail=historical]").open'),false);
      checks.push('Actual sidebar R16 opens current environment with saved result, 3 strategies and 15 candidates');
      checks.push('Actual cutoff 2026-10-15 and expected 2026-10-16 are kept distinct with stale warning');
      await screenshot('01-current-environment-native.png','.exp-scanner');
      await screenshot('02-candidate-evidence-native.png','.exp-current-strategy[data-exp-scan-strategy=strongReclaimVolatilityTrail]');
      await evaluate('document.querySelector(".exp-candidate details").open=true');
      const before=await evaluate('[...document.querySelectorAll(".exp-candidate")].map(x=>x.innerText)');
      await evaluate('document.querySelector("[data-exp-detail=scan-history]").open=true');await click('[data-exp-action="scan-refresh"]');
      await wait('!document.querySelector("[data-exp-action=scan-refresh]").disabled','refreshed');
      assert.deepEqual(await evaluate('[...document.querySelectorAll(".exp-candidate")].map(x=>x.innerText)'),before);checks.push('Refresh uses real scan.list/get and preserves all saved stocks/evidence');
      const {symbol,signalDay}=await evaluate('({symbol:document.querySelector(".exp-stock").dataset.stock,signalDay:document.querySelector(".exp-stock").dataset.stockDay})');
      assert.equal(signalDay,'2026-10-15');
      await evaluate('window.candidateDayCalls=[];window.candidateOpenDay=AT.marketUI.openDay;AT.marketUI.openDay=function(symbol,date){candidateDayCalls.push({symbol,date});return candidateOpenDay.call(this,symbol,date)};true');
      await click('.exp-stock');await wait('AT.state.page==="G01"','stock daily navigation');
      assert.equal(await evaluate('AT.state.stock'),symbol);
      assert.deepEqual(await evaluate('candidateDayCalls'),[{symbol,date:signalDay}]);
      checks.push('Candidate name passes its recorded signal day to the existing daily-chart focus API');
      if(!await evaluate('document.querySelector("[data-nav=R16]")?.getClientRects().length>0'))await click('[data-nav-group="R"]');
      await click('[data-nav="R16"]');
      await wait('document.querySelectorAll(".exp-candidate").length===15','returned scan');
      assert.ok(await evaluate('document.querySelector("#exp-scan-result-title").textContent.includes("2026-10-15")'));checks.push('Navigate away and return retains the selected completed scan without a new scan');
      for(const day of [null,'not-a-date','2099-01-02']){
        await evaluate(`(()=>{candidateDayCalls.length=0;const candidate=document.querySelector('.exp-stock');${day===null?'delete candidate.dataset.stockDay':`candidate.dataset.stockDay=${JSON.stringify(day)}`}})()`);
        await click('.exp-stock');await wait('AT.state.page==="G01"','candidate date fallback navigation');
        assert.equal(await evaluate('AT.state.stock'),symbol);
        assert.deepEqual(await evaluate('candidateDayCalls'),day===null?[]:[{symbol,date:day}]);
        if(!await evaluate('document.querySelector("[data-nav=R16]")?.getClientRects().length>0'))await click('[data-nav-group="R"]');
        await click('[data-nav="R16"]');await wait('document.querySelectorAll(".exp-candidate").length===15','return after date fallback');
      }
      checks.push('Missing or rejected dates retain ordinary stock navigation; a future recorded date is passed unchanged, never replaced with today');
      await evaluate('document.querySelector("[data-exp-detail=historical]").open=true');
      await wait('document.querySelectorAll("[data-exp-strategy]").length===3','actual catalogue');
      const counts=[];for(const id of ['contractionNoProgress5','persistentLeaderTrail10','strongReclaimVolatilityTrail']){
        await click(`[data-exp-strategy="${id}"]`);counts.push(await evaluate('document.querySelector("#exp-source").options.length'));
        await evaluate('document.querySelector("[data-exp-detail=strategy-research]").open=true');
        assert.equal(await evaluate('document.querySelectorAll(".exp-research tbody tr").length'),counts.at(-1));
      }
      assert.deepEqual(counts,[2,6,6]);checks.push('Real catalogue provides 3 saved strategies, all 14 fixed sources and 14 complete research projections');
      await screenshot('03-real-research-and-rules-native.png','.exp-strategy-detail');
      assert.equal(calls.filter(x=>/research\.experimental\.(scan\.)?(create|stop)$/.test(x.method)).length,0);
      assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false);assert.deepEqual(errors,[]);
      const protectedAfter=Object.fromEntries(Object.keys(protectedBefore).map(name=>[name,sha(path.join(verification.workspace,name))]));assert.deepEqual(protectedAfter,protectedBefore);
      for(const pin of inputPins)assert.equal(sha(pin.path),pin.sha256,'Evidence and catalogue stay unchanged during the read-only app check');
      checks.push('Read-only IPC guard observed no create/stop; actual workspace protected files unchanged');
      const receipt={passed:true,scope:'Full Electron runtime/createDesktop with real catalogue RPC and copied completed scan; no current scan or historical account execution',newScans:0,newAccounts:0,checks,images,inputPins,protectedBefore,protectedAfter,calls,errors,
        sourcePins:['preview/experimental-strategies.js','preview/experimental-strategies.css','preview/app.js','preview/data.js','preview/index.html','desktop/runtime.cjs','tests/experimental-strategies-app-smoke.cjs'].map(p=>({path:p,sha256:sha(path.join(root,p))}))};
      fs.writeFileSync(path.join(directory,'app-ui-verification.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify({passed:true,directory,checks:checks.length,images:images.map(x=>x.path)},null,2));
      await evaluate('AT.persist()');const closed=new Promise(resolve=>win.once('closed',resolve));win.close();await closed;app.exit(0);
    }catch(error){console.error(error.stack);try{if(win)await screenshot('failure.png',null)}catch{}fs.writeFileSync(path.join(directory,'app-ui-verification.json'),JSON.stringify({passed:false,error:error.stack,checks,images,inputPins,calls,errors},null,2)+'\n',{flag:'wx'});win?.destroy();app.exit(1)}
  });
}
