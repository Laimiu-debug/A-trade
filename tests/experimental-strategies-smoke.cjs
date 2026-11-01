/* Isolated Electron UI fixture. No business service, account engine or market input is opened. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'..');
if(!process.versions.electron){
  const base=path.join(root,'artifacts','experimental-strategies-ui');fs.mkdirSync(base,{recursive:true});
  let n=1;while(fs.existsSync(path.join(base,`attempt-${String(n).padStart(4,'0')}`)))n++;
  const directory=path.join(base,`attempt-${String(n).padStart(4,'0')}`);fs.mkdirSync(directory);
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
  const child=require('node:child_process').spawn(require('electron'),[__filename,directory],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  const log=fs.createWriteStream(path.join(directory,'run.log'),{flags:'wx'});
  for(const stream of [child.stdout,child.stderr])stream.on('data',chunk=>{log.write(chunk);process.stdout.write(chunk)});
  child.on('exit',code=>{log.end();process.exitCode=code||0});
}else{
  const {app,BrowserWindow,session}=require('electron');
  const directory=process.argv[2];if(!directory)throw Error('Isolated output directory required');
  app.setPath('userData',path.join(directory,'profile'));app.commandLine.appendSwitch('force-device-scale-factor','1');
  let win;const errors=[],checks=[],images=[];
  const sha=p=>require('node:crypto').createHash('sha256').update(fs.readFileSync(p)).digest('hex');
  const evaluate=code=>win.webContents.executeJavaScript(code);
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const wait=async(expression,label)=>{for(let i=0;i<150;i++){if(await evaluate(expression))return;await pause(30)}throw Error('Timed out: '+label)};
  const click=selector=>evaluate(`(()=>{const x=document.querySelector(${JSON.stringify(selector)});if(!x||x.disabled)throw Error('Not enabled: '+${JSON.stringify(selector)});x.click()})()`);
  async function shot(name,selector,width=1280,height=1000){
    win.setContentSize(width,height);await pause(80);
    await evaluate(selector?`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start'});window.scrollBy(0,-42)`:'window.scrollTo(0,0)');
    await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
    await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await pause(120);
    const file=path.join(directory,name);const image=await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
    fs.writeFileSync(file,image.toPNG(),{flag:'wx'});images.push({path:file,sha256:sha(file),width:image.getSize().width,height:image.getSize().height});
  }
  function receipt(passed,error){
    const files=['preview/experimental-strategies.js','preview/experimental-strategies.css','tests/experimental-strategies-smoke.cjs'];
    fs.writeFileSync(path.join(directory,'ui-verification.json'),JSON.stringify({passed,scope:'Isolated Electron UI with explicit synthetic task fixtures; no real account, new strategy trial, or market scan execution',newAccounts:0,newScans:0,checks,images,errors,error:error?.stack||null,sourcePins:files.map(p=>({path:p,sha256:sha(path.join(root,p))}))},null,2)+'\n',{flag:'wx'});
  }
  app.whenReady().then(async()=>{
    try{
      session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
      const manifest=JSON.parse(fs.readFileSync(path.join(root,'docs','experimental-strategies-v1.json'),'utf8'));
      const catalogue={researchOverview:manifest.researchOverview,strategies:manifest.strategies.map((s,i)=>({...s,
        name:s.name+'（界面夹具）',sources:s.sources.map(x=>({id:x.id,label:x.label,period:x.period,board:x.board,start:x.start,end:x.end,initialCapital:x.initialCapital,fees:x.fees,available:true})),
        researchResults:s.sources.map((x,j)=>({period:x.period,board:x.board,metrics:{accountReturnPct:j===0?null:j%2?-10:12,maxDrawdownPct:20,closed:10,profitFactor:1.1,meanWinPct:15,meanLossPct:-7,openUnrealizedPnlAmount:0}}))}))};
      const fixture=path.join(directory,'fixture.html');
      fs.writeFileSync(fixture,`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>实验策略界面夹具</title><link rel="stylesheet" href="${pathToFileURL(path.join(root,'preview','tokens.css'))}"><link rel="stylesheet" href="${pathToFileURL(path.join(root,'preview','experimental-strategies.css'))}"><style>body{margin:0;background:#f5f7fb;font:14px/1.6 "Microsoft YaHei UI",sans-serif}#fixture-label{position:sticky;top:0;background:#263244;color:white;padding:6px 20px;z-index:10;font-size:12px}main{padding:24px} @media(max-width:700px){main{padding:14px}}</style><div id="fixture-label">UI 功能夹具 · 合成任务 / 数值 · 未执行真实账户或扫描</div><main></main><script src="${pathToFileURL(path.join(root,'preview','experimental-strategies.js'))}"></script></html>`,{flag:'wx'});
      win=new BrowserWindow({show:false,width:1280,height:1000,webPreferences:{contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
      win.webContents.on('console-message',e=>{if(e.level==='error')errors.push(e.message)});
      await win.loadFile(fixture);
      const scanResult={requestedDate:'2026-10-10',expectedAsOfDate:'2026-10-09',asOfDate:'2026-10-09',stale:false,lagSessions:0,source:{benchmarkLatestDate:'2026-10-09'},
        coverage:{loaded:100,asOfCount:90,asOfPct:90,failed:2,boardCounts:{main:50,chinext:30,star:20}},
        environment:{label:'合成环境观察',summary:'用于验证页面呈现，不是实际行情结论。',metrics:[{label:'20日上涨广度',value:56.2,unit:'%',denominator:90},{label:'持续在榜下界',value:42.3,unit:'%',denominator:10}],
          boards:[{board:'main',label:'合成板块',summary:'仅界面样本。',metrics:[{label:'有效股票',value:45,unit:'只'}]}],methodology:'使用截止日已完成日线；环境不改变策略规则。'},
        strategies:catalogue.strategies.map((s,i)=>({strategyId:s.id,name:s.name,counts:{signals:i===1?0:1,evaluated:100},environmentFit:{label:'描述性观察',summary:'不把环境分数作为新增门槛。'},boards:[{board:'main',signalCount:i===1?0:1,evaluatedCount:50,status:'已评估'}],limitations:['合成界面数据，不代表研究表现。'],candidates:i===1?[]:[{symbol:i?'sz300001':'sh600001',name:i?'合成创业股票':'合成主板股票',board:i?'chinext':'main',signalDate:'2026-10-09',rank:1,close:12.34,reason:'已满足本策略固定条件（界面样本）',evidence:[{label:'收盘 / 平台',value:'12.34 / 12.00',unit:'元'},{label:'排名',value:1,unit:'名'}],warnings:['收盘信号不等于下一开盘一定成交']}]})),warnings:['两只证券读取失败，未被当作规则不满足。'],executionNotice:'只生成候选，不下单、不创建账户。'};
      await evaluate(`window.AT={state:{page:'R16'}};window.fixture={catalogue:${JSON.stringify(catalogue)},scanResult:${JSON.stringify(scanResult)},calls:[],tasks:[],scans:[],failNext:false,holdCreate:false};
        window.atradeDesktop={business:async(method,payload)=>{const f=window.fixture;f.calls.push({method,payload});const action=method.slice('research.experimental.'.length);
          if(action==='scan.list')return {ok:true,data:{tasks:structuredClone(f.scans)}};
          if(action==='scan.create'){if(f.holdScan)await new Promise(r=>f.releaseScan=r);if(f.failScan){f.failScan=false;return {ok:false,error:{message:'合成扫描失败'}}};const task={id:'scan-'+(f.scans.length+1),kind:'currentScan',status:'running',createdAt:'2026-10-17T00:00:00Z',progress:{phase:'signals',completed:1,total:3}};f.scans.unshift(task);return {ok:true,data:structuredClone(task)}}
          if(action==='scan.get'){const task=f.scans.find(x=>x.id===payload.id);return {ok:true,data:{task:structuredClone(task),...(task.status==='completed'?{result:structuredClone(f.scanResult),freshness:f.freshnessUnavailable?{available:false,stale:null}:{available:true,checkedAt:'2026-10-17T00:00:00Z',expectedAsOfDate:'2026-10-16',stale:true,lagSessions:5}}:{})}}}
          if(action==='scan.stop'){const task=f.scans.find(x=>x.id===payload.id);task.status='stopped';return {ok:true,data:structuredClone(task)}}
          if(action==='catalogue')return {ok:true,data:f.catalogue};if(action==='list')return {ok:true,data:{tasks:structuredClone(f.tasks)}};
          if(action==='create'){if(f.holdCreate)await new Promise(r=>f.release=r);if(f.failNext){f.failNext=false;return {ok:false,error:{message:'合成创建失败 <unsafe>'}}};const task={id:'fixture-'+(f.tasks.length+1),name:'合成复现任务',strategyId:payload.strategyId,sourceId:payload.sourceId,createdAt:'2026-10-17T00:00:00Z',status:'running',progress:{phase:'account',completed:25,total:100}};f.tasks.unshift(task);return {ok:true,data:structuredClone(task)}}
          if(action==='get'){const task=f.tasks.find(x=>x.id===payload.id);return {ok:true,data:{task:structuredClone(task),...(task.status==='completed'?{summary:{accountReturnPct:-3.25,maxDrawdownPct:9,closed:0,profitFactor:null,openPositions:1,openUnrealizedPnlAmount:-16250,feesPaid:0,meanExposurePct:20,quality:{valuationUnreliableDates:['2025-01-01'],integrityIssues:[],numericUnknownPaths:[]},concentration:{top5ShareOfGrossProfitPct:null}},resultRef:{path:'synthetic/result.json.gz'}}:{})}}}
          if(action==='stop'){const task=f.tasks.find(x=>x.id===payload.id);task.status='stopped';return {ok:true,data:structuredClone(task)}}throw Error('Unexpected RPC '+method)}};
        document.querySelector('main').innerHTML=ExperimentalStrategies.render();ExperimentalStrategies.mount(document.querySelector('main'));true`);
      await wait('document.querySelectorAll("[data-exp-strategy]").length===3','catalogue');
      assert.equal(await evaluate('fixture.calls.some(x=>x.method.endsWith(".create"))'),false);checks.push('Opening directory never starts a real or fixture replay');
      assert.equal(await evaluate('document.querySelector("h1").textContent'),'当前环境与选股');
      assert.equal(await evaluate('document.querySelector("[data-exp-detail=historical]").open'),false);checks.push('Current scan is the primary action; historical replay starts collapsed');
      await evaluate('fixture.holdScan=true');await evaluate('document.querySelector("[data-exp-action=scan-create]").click();document.querySelector("[data-exp-action=scan-create]").click();true');
      await wait('typeof fixture.releaseScan==="function"','held scan');
      assert.equal(await evaluate('fixture.calls.filter(x=>x.method.endsWith("scan.create")).length'),1);
      assert.deepEqual(Object.keys(await evaluate('fixture.calls.find(x=>x.method.endsWith("scan.create")).payload')),['requestId']);
      await evaluate('fixture.holdScan=false;fixture.releaseScan();true');await wait('fixture.scans.length===1','scan queued');
      await evaluate('fixture.scans[0].status="completed";document.querySelector("[data-exp-detail=scan-history]").open=true');await click('[data-exp-action=scan-refresh]');
      await wait('!!document.querySelector(".exp-stale")','stale result');
      assert.ok(await evaluate('document.querySelector("#exp-scan-result-title").textContent.includes("2026-10-09")'));
      assert.ok(await evaluate('document.querySelector(".exp-scan-result header").innerText.includes("沪深300实际截止 2026-10-09")'));
      assert.ok(await evaluate('document.querySelector(".exp-stale").innerText.includes("5 个交易日")'));
      assert.equal(await evaluate('fixture.scanResult.stale'),false);checks.push('Saved-fresh scan becomes visibly stale using read-time freshness without changing saved result');
      assert.equal(await evaluate('document.querySelectorAll("[data-exp-scan-strategy]").length'),3);
      assert.ok(await evaluate('document.querySelector("[data-exp-scan-strategy=persistentLeaderTrail10]").innerText.includes("暂无符合")'));
      await evaluate('document.querySelector(".exp-candidate details").open=true');
      checks.push('One explicit scan only; actual cutoff/staleness/coverage/three strategy results and zero-candidate state are visible');
      await shot('01-current-environment.png','.exp-scanner');await shot('02-current-candidates.png','.exp-scan-strategies');
      await evaluate('fixture.freshnessUnavailable=true');await click('[data-exp-action=scan-refresh]');await wait('document.querySelector(".exp-stale").innerText.includes("无法核验当前时效")','unverifiable clock');
      await evaluate('fixture.freshnessUnavailable=false');await click('[data-exp-action=scan-refresh]');await wait('document.querySelector(".exp-stale").innerText.includes("5 个交易日")','clock restored');checks.push('Unavailable current calendar produces unknown freshness, never a current/fresh claim');
      await evaluate('fixture.failScan=true');await click('[data-exp-action=scan-create]');await wait('document.querySelector(".exp-scanner").innerText.includes("合成扫描失败")','scan failure');
      await click('[data-exp-action=scan-create]');await wait('fixture.scans.length===2','retry scan');
      const scanAttempts=await evaluate('fixture.calls.filter(x=>x.method.endsWith("scan.create")).map(x=>x.payload)');assert.equal(scanAttempts[1].requestId,scanAttempts[2].requestId);
      await click('[data-exp-action=scan-stop]');await wait('fixture.scans[0].status==="stopped"','scan stopped');
      await click('[data-exp-scan="scan-1"]');await wait('!!document.querySelector(".exp-stale")','prior scan restored');
      checks.push('Scan retry is idempotent; stop preserves previous scan and its actual cutoff');
      await shot('03-narrow-current.png','.exp-scanner',600,1000);await shot('04-narrow-candidates.png','.exp-scan-strategies',600,1000);
      assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false);
      await evaluate('document.querySelector("[data-exp-detail=historical]").open=true');
      assert.equal(await evaluate('document.querySelectorAll("input[type=date]").length'),0);checks.push('Historical sources are fixed, with no arbitrary-date input');
      for(const strategy of catalogue.strategies){await click(`[data-exp-strategy="${strategy.id}"]`);assert.equal(await evaluate('document.querySelector("#exp-source").options.length'),strategy.sources.length)}
      checks.push('Three catalogue entries expose every fixed source (2/6/6)');
      await click('[data-exp-strategy="persistentLeaderTrail10"]');
      await evaluate('document.querySelector("[data-exp-detail=strategy-research]").open=true');
      assert.equal(await evaluate('document.querySelectorAll(".exp-research tbody tr").length'),6);checks.push('All six reviewed sources, including missing and negative values, remain inspectable');
      await shot('05-strategy-rules-and-research.png','.exp-strategy-detail');
      await evaluate('fixture.holdCreate=true');
      await evaluate('document.querySelector("[data-exp-action=create]").click();document.querySelector("[data-exp-action=create]").click();true');
      await wait('typeof fixture.release==="function"','held create');
      assert.equal(await evaluate('fixture.calls.filter(x=>x.method==="research.experimental.create").length'),1);checks.push('Double click dispatches one create with fixed strategy/source');
      await evaluate('fixture.holdCreate=false;fixture.release();true');await wait('!!document.querySelector("[data-exp-action=stop]")','running task');
      await click('[data-exp-action=refresh-tasks]');await wait('document.querySelector(".exp-task-detail").innerText.includes("当前阶段 25%")','phase progress');
      await click('[data-exp-action=stop]');await wait('document.querySelector(".exp-task-detail").innerText.includes("已停止")','stop');
      assert.equal(await evaluate('fixture.tasks.length'),1);checks.push('Stop retains task history and does not delete results');
      await evaluate('fixture.failNext=true');await click('[data-exp-action=create]');
      await wait('!!document.querySelector("[role=alert]")','create error');assert.equal(await evaluate('!!document.querySelector("unsafe")'),false);
      await click('[data-exp-action=create]');await wait('fixture.tasks.length===2','retry task');
      const attempts=await evaluate('fixture.calls.filter(x=>x.method==="research.experimental.create").map(x=>x.payload)');
      assert.equal(attempts[1].requestId,attempts[2].requestId);checks.push('A failed create retry uses the same idempotency key and escapes the error');
      await evaluate('fixture.tasks[0].status="completed"');await click('[data-exp-action=refresh-tasks]');
      await wait('document.querySelector(".exp-task-detail").innerText.includes("-3.25%")','completed result');
      assert.ok(await evaluate('document.querySelector(".exp-task-detail").innerText.includes("期末未平 1 笔")'));checks.push('Completed summary separates account loss, zero closed trades and open unrealized PnL');
      await shot('06-completed-task.png','.exp-history');
      win.setContentSize(600,1000);await pause(100);
      assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false);checks.push('600px content viewport has no page horizontal overflow');
      assert.deepEqual(errors,[]);receipt(true);console.log(JSON.stringify({passed:true,directory,checks:checks.length,images:images.map(x=>x.path)},null,2));
      await evaluate('ExperimentalStrategies.unmount()');win.destroy();app.exit(0);
    }catch(error){console.error(error.stack);try{if(win)await shot('failure.png',null)}catch{}receipt(false,error);win?.destroy();app.exit(1)}
  });
}
