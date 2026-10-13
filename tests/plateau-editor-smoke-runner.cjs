const {app,protocol,dialog,session}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const directory=process.argv[2];if(!directory)throw new Error('Missing isolated smoke directory');
const root=path.resolve(__dirname,'..'),dataDirectory=path.join(directory,'workspace'),fixtureDirectory=path.join(directory,'tdx-fixture');
app.setPath('userData',path.join(directory,'profile'));protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
dialog.showMessageBox=async(_window,options)=>{throw new Error(`Unexpected dialog: ${options.message}`);};
let win;const errors=[];
const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw new Error(`${error.message} [${code.slice(0,180)}]`);});
async function wait(expression,label,timeout=90000){const started=Date.now();while(Date.now()-started<timeout){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`${label}: ${await evaluate('document.querySelector("main").innerText.slice(-6000)')}`);}
const click=selector=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el||el.disabled)throw new Error('Missing enabled '+${JSON.stringify(selector)});el.click();})()`);
async function fill(name,value){await evaluate(`(()=>{const el=document.querySelector('[data-pf-step-panel]:not([hidden]) [data-pf-field="${name}"]')||document.querySelector('[data-pf-field="${name}"]');if(!el)throw new Error('Missing field ${name}');if(el.type==='checkbox')el.checked=${JSON.stringify(value)};else el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(el.tagName==='SELECT'||el.type==='checkbox'?'change':'input',{bubbles:true}));})()`);}
async function navigate(page){await evaluate(`AT.navigate(${JSON.stringify(page)});true`);await wait(`!!document.querySelector('[data-research-page="${page}"]')`,'navigate '+page);}
const step=index=>click(`[data-pf-action="workflow-step"][data-pf-step="${index}"]`);
const draft=key=>evaluate(`JSON.parse(localStorage.getItem(${JSON.stringify(key)})).draft`);
const text=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).innerText`);
async function screenshot(name,selector){await fs.mkdir(path.join(root,'artifacts'),{recursive:true});const scroll=()=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.style.scrollMarginTop='95px';el.scrollIntoView({block:'start',behavior:'instant'});})()`);await scroll();await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await new Promise(resolve=>setTimeout(resolve,180));await scroll();await fs.writeFile(path.join(root,'artifacts',name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function fixture(){
 const dates=[];let day=new Date('2023-01-02T00:00:00Z');while(dates.length<340){if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));day=new Date(day.getTime()+86400000);}
 await fs.mkdir(path.join(fixtureDirectory,'vipdoc','sh','lday'),{recursive:true});
 for(let stock=0;stock<6;stock++){
  const bytes=Buffer.alloc(dates.length*32);let previous=1000;
  for(let i=0;i<dates.length;i++){const close=Math.round(1000+i*.3+65*Math.sin(i/10+stock*.5)),open=Math.round(previous*.999),at=i*32,volume=2000000+100000*Math.round(3+Math.sin(i/7+stock));bytes.writeUInt32LE(Number(dates[i].replaceAll('-','')),at);bytes.writeUInt32LE(open,at+4);bytes.writeUInt32LE(Math.max(open,close)+3,at+8);bytes.writeUInt32LE(Math.min(open,close)-3,at+12);bytes.writeUInt32LE(close,at+16);bytes.writeFloatLE(close/100*volume,at+20);bytes.writeUInt32LE(volume,at+24);previous=close;}
  await fs.writeFile(path.join(fixtureDirectory,'vipdoc','sh','lday','sh'+(600000+stock)+'.day'),bytes);
 }return dates;
}
app.whenReady().then(async()=>{
 const dates=await fixture();session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
 win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',(_event,details)=>{if(details.level==='error')errors.push(details.message);});window.webContents.on('preload-error',(_event,_file,error)=>errors.push(error.message));}});
 await evaluate('window.__plateauErrors=[];window.addEventListener("error",event=>__plateauErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__plateauErrors.push(String(event.reason)));');
 assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`)).ok,true);assert.equal((await evaluate("atradeDesktop.market('market.scan')")).ok,true);
 await navigate('R03');await step(4);await fill('name','普通回测草稿保持不变');await fill('config.topN','7');await fill('config.initialCapital','230000');
 const researchBefore=await draft('atrade.portfolio-editor.v1');
 assert.equal(await evaluate(`!!document.querySelector('[data-pf-field="kind"] option[value="systemPlateau"]')`),false,'普通回测任务类型不再混入收益平原');
 await navigate('R05');await wait('!!document.querySelector("[data-pf-form]")','independent plateau editor');
 assert.equal(await evaluate('!!document.querySelector("#navigation [data-nav=R05].active")'),true,'收益平原在侧栏有独立且选中的入口');
 assert.equal(await evaluate('!!document.querySelector("#navigation [data-nav=R03].active")'),false,'普通回测入口不被选中');
 const breadcrumb=await text('#breadcrumb');assert.match(breadcrumb,/策略与回测/);assert.match(breadcrumb,/收益平原/);assert.doesNotMatch(breadcrumb,/回测工作区/);
 assert.match(await text('[data-pf-form]'),/扫描/);await fill('name','UI · 独立收益平原');
 await step(2);
 let plateauDraft=await draft('atrade.plateau-editor.v1');assert.equal(plateauDraft.kind,'systemPlateau');
 const initialAxes=plateauDraft.scanAxes.filter(row=>row.path);assert.equal(initialAxes.length,4,'首次打开即有四层可填写示例');assert.ok(initialAxes.every(row=>String(row.text).trim()));
 assert.equal(await evaluate(`PortfolioWorkbench.helpers.scanAxes(${JSON.stringify(initialAxes)},'systemPlateau',81).reduce((count,row)=>count*row.values.length,1)`),16,'首次示例是 16 组可理解的小网格');
 await screenshot('plateau-editor-scans.png','[data-pf-step-panel="2"]');
 // Check the user's original one-value failure immediately in the affected row.
 await fill('scanAxes.0.text','5');
 await wait(`!!document.querySelector('[data-pf-axis-error="0"]')?.textContent`,'inline scan validation',15000);
 assert.match(await text('[data-pf-axis-error="0"]'),/2|两个|不同/);
 await screenshot('plateau-editor-error.png','[data-pf-step-panel="2"]');
 await click('[data-pf-action="plateau-example"]');
 plateauDraft=await draft('atrade.plateau-editor.v1');assert.equal(await evaluate(`PortfolioWorkbench.helpers.scanAxes(${JSON.stringify(plateauDraft.scanAxes)},'systemPlateau',81).reduce((count,row)=>count*row.values.length,1)`),16);
 await step(1);
 for(const [name,value] of Object.entries({'selection.mode':'explicit','symbols':'600000 600001 600002 600003 600004 600005','config.startDate':dates[260],'config.endDate':dates[315],'config.universe.minListingDays':'0','config.universe.minAmount20':'0'}))await fill(name,value);
 await step(2);
 const axes=[['strategies.momentum.params.lookback','5, 8'],['combinations.combo_union.method','intersection, union'],['rotation.lookbackDays','5, 8'],['topN','2, 3']];
 for(let i=0;i<axes.length;i++){await fill('scanAxes.'+i+'.path',axes[i][0]);if(i!==1)await fill('scanAxes.'+i+'.text',axes[i][1]);}
 // Enumerated combination methods are selected with visible checkboxes.
 for(const value of ['intersection','union','weighted'])await evaluate(`(()=>{const checkbox=document.querySelector('[data-pf-scan-method="1"][value="${value}"]');if(!checkbox)throw new Error('Missing combination candidate ${value}');const wanted=${JSON.stringify(['intersection','union'].includes(value))};if(checkbox.checked!==wanted){checkbox.checked=wanted;checkbox.dispatchEvent(new Event('change',{bubbles:true}));}})()`);
 await step(3);
 for(const [name,value] of Object.entries({'config.initialCapital':'120000','config.maxPositions':'3','config.maxStockWeight':'35','config.maxHoldingDays':'5','config.rotation.warmupDays':'40','params.momentum.trendWindow':'8','params.momentum.minReturn':'0','experiment.trainDays':'35','experiment.testDays':'10','experiment.stepDays':'10','experiment.maxFolds':'2'}))await fill(name,value);
 for(const field of ['params.momentum.lookback','config.rotation.lookbackDays','config.topN']){
  assert.equal(await evaluate(`!!document.querySelector('[data-pf-field="${field}"]')`),false,'被扫描字段不再重复作为固定设置：'+field);
  assert.match(await evaluate(`document.querySelector('[data-pf-scanned-field="${field}"]').textContent`),/扫描项|候选值/);
 }
 await screenshot('plateau-editor-fixed.png','[data-pf-step-panel="3"]');
 await step(4);await wait('!!document.querySelector(".pf-config-ready")','plateau effective preview',30000);
 assert.equal(await evaluate('document.querySelectorAll("[data-pf-scan-summary] tbody tr").length'),4,'核对表单独列出四项扫描参数');
 assert.match(await text('[data-pf-scan-summary]'),/16 组/);assert.match(await text('[data-pf-scan-summary]'),/基础参考值/);assert.match(await text('[data-pf-fixed-summary]'),/12 万元/);
 await screenshot('plateau-editor-review.png','[data-pf-step-panel="4"]');
 plateauDraft=await draft('atrade.plateau-editor.v1');
 assert.equal(plateauDraft.config.initialCapital,120000);assert.equal(plateauDraft.experiment.minOosYears,2);assert.equal(plateauDraft.experiment.minOosRegimes,2,'不为通过短样本降低认证门槛');
 await navigate('R03');await step(4);const researchAfter=await draft('atrade.portfolio-editor.v1');assert.deepEqual(researchAfter,researchBefore,'收益平原编辑未污染普通回测草稿');
 assert.equal(await evaluate(`document.querySelector('[data-pf-field="name"]').value`),'普通回测草稿保持不变');
 await navigate('R05');await step(4);assert.deepEqual(await draft('atrade.plateau-editor.v1'),plateauDraft,'往返页面保留独立收益平原草稿');
 await wait('!!document.querySelector(".pf-config-ready")','reentered effective preview',30000);
 await evaluate('document.querySelector("[data-pf-form]").requestSubmit()');await wait('AT.state.page==="R05"&&PortfolioWorkbench.currentStep()===5','create on independent plateau route',20000);
 const id=await evaluate('document.querySelector("[data-rs-task]").value');
 await wait('!!document.querySelector(".rs-status-completed")','real 16-combination plateau',300000);
 const response=await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)}})`);assert.equal(response.ok,true,JSON.stringify(response.error));
 const {task,result}=response.data;assert.equal(task.kind,'systemPlateau');assert.equal(task.status,'completed');assert.equal(result.trialCount,16);assert.equal(result.folds.length,2);assert.equal(result.config.initialCapital,120000);assert.equal(result.config.maxPositions,3);
 assert.deepEqual(result.experiment.axes.map(axis=>({path:axis.path,values:axis.values})),axes.map(([axisPath,value])=>({path:axisPath,values:value.split(', ').map(v=>isNaN(Number(v))?v:Number(v))})),'实际任务使用用户选择的候选值');
 assert.equal(result.plateau.certified,false);assert.deepEqual(result.plateau.recommendedRanges,[],'短样本只用于操作验证，不伪造认证平原');
 assert.equal(result.plateau.heatmap.length,16);assert.ok(result.engineRunCount>0);
 const candidates=result.protocol.candidateConfigs;assert.equal(candidates.length,16);assert.equal(new Set(candidates.map(row=>row.configSignature)).size,16,'各组候选实际形成不同的冻结配置');
 for(const axis of result.experiment.axes)assert.deepEqual([...new Set(candidates.map(row=>row.params[axis.path]))].sort(),[...axis.values].sort(),'实际候选覆盖扫描轴 '+axis.path);
 for(const fold of result.folds){assert.equal(Object.keys(fold.training).length,16,'每折实际计算 16 组训练候选');for(const candidate of candidates)assert.deepEqual(fold.training[candidate.candidateId].params,candidate.params,'训练记录中的参数与冻结候选一致');}
 assert.match(await text('main'),/本次测试的参数与固定条件/,'完成的报告保留独立参数解释面板');
 assert.equal(result.config.strategies.find(row=>row.id==='momentum').params.lookback,5,'扫描项的校验参考值使用首个候选');assert.equal(result.config.topN,2);assert.equal(result.config.maxPositions,3,'固定参数没有被扫描候选覆盖');
 await screenshot('plateau-editor-result.png','.pf-heatmap');
 await navigate('R03');assert.deepEqual(await draft('atrade.portfolio-editor.v1'),researchBefore,'创建平原后普通回测草稿仍完整保留');
 assert.deepEqual(await evaluate('__plateauErrors'),[]);assert.deepEqual(errors,[]);
 await fs.writeFile(path.join(directory,'task.json'),JSON.stringify({id,trialCount:result.trialCount,engineRunCount:result.engineRunCount,certified:result.plateau.certified}));
 console.log('PASS independent plateau editor, draft isolation, 16-combination example, inline value error, fixed/scanned separation, real candidate overrides and uncertified short-sample result');win.close();setTimeout(()=>app.exit(0),300);
}).catch(error=>{console.error(error.stack);if(win)win.destroy();app.exit(1);});
