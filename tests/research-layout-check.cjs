const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');

// Run in the isolated native desktop fixture, including real results and saved drafts.
module.exports=async({win,frame,evaluate,wait})=>{
 const pages=[['R11','选股漏斗'],['R01','策略中心'],['R12','策略运行'],['R03','策略回测'],['R05','收益平原'],['R13','待确认交易计划'],['R08','报告库']];
 const ready={R11:'pool-grid',R01:'strategy-parameters',R12:'signal-config',R03:'backtest-config',R05:'plateau-config'};
 const evidence=[];
 const snapshot=()=>frame.executeJavaScript(`JSON.stringify(['tdx-trend-backtest-form-v2','tdx-trend-backtest-plateau-form-v2'].map(key=>JSON.parse(localStorage.getItem(key))))`);
 await evaluate('AT.navigate("R03");true');
 await wait('document.querySelector("h1")?.innerText==="策略回测"','backtest before responsive checks');
 // The asynchronous catalogue normalizes the saved draft once on cold startup.
 // Start comparing only after the real editor has applied the active profile.
 await wait('!!JSON.parse(localStorage.getItem("tdx-trend-backtest-form-v2")||"{}").event_judgment_profile_id','backtest catalogue applied before taking a draft snapshot');
 const draft=await snapshot();
 win.webContents.debugger.attach('1.3');
 try{
  for(const width of [1920,1366,1024]){
   await win.webContents.debugger.sendCommand('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
   for(const [page,heading] of pages){
    await evaluate(`AT.navigate(${JSON.stringify(page)});true`);
    for(let attempt=0;attempt<100;attempt++){
     if(await evaluate(`AT.state.page===${JSON.stringify(page)}&&!document.querySelector('#strategy-workspace-root').hidden`))break;
     if(attempt===99)throw Error('Strategy workspace did not become visible: '+await evaluate('JSON.stringify({page:AT.state.page,hidden:document.querySelector("#strategy-workspace-root").hidden})'));
     await new Promise(resolve=>setTimeout(resolve,100));
    }
    await wait(`document.querySelector('h1')?.innerText===${JSON.stringify(heading)}`,'responsive '+page);
    if(ready[page])await wait(`!!document.querySelector('[data-research="${ready[page]}"]')`,'loaded controls '+page);
    for(const expanded of [false,true]){
     await frame.executeJavaScript(`document.querySelectorAll('details.research-fold').forEach(el=>{el.open=${expanded}});scrollTo(0,0);true`);
     await new Promise(resolve=>setTimeout(resolve,150));
     const layout=await frame.executeJavaScript(`(()=>{const root=document.documentElement;const visible=el=>el.getClientRects().length>0;return {viewport:innerWidth,client:root.clientWidth,width:root.scrollWidth,height:root.scrollHeight,mainWidth:document.querySelector('.atrade-research-content').getBoundingClientRect().width,pools:[...document.querySelectorAll('[data-research="pool-grid"]>.ant-col')].filter(visible).map(el=>Math.round(el.getBoundingClientRect().top)),backtest:visible(document.querySelector('[data-research="backtest-config"]')||document.createElement('div')),plateau:visible(document.querySelector('[data-research="plateau-config"]')||document.createElement('div'))}})()`);
     evidence.push({windowWidth:width,page,expanded,...layout});
     if(layout.mainWidth===0)throw Error('Hidden strategy frame during layout check: '+await evaluate('JSON.stringify({page:AT.state.page,hidden:document.querySelector("#strategy-workspace-root").hidden,root:document.querySelector("#strategy-workspace-root").getBoundingClientRect().toJSON(),frame:document.querySelector("[data-strategy-frame]").getBoundingClientRect().toJSON()})'));
     assert.ok(layout.width<=layout.client+1,`${page} overflows at ${width}px (${expanded?'expanded':'collapsed'}): ${JSON.stringify(layout)}`);
     if(page==='R03'){assert.equal(layout.backtest,true);assert.equal(layout.plateau,false);}
     if(page==='R05'){assert.equal(layout.backtest,false);assert.equal(layout.plateau,true);}
     if(page==='R11'&&width>=1366){assert.equal(layout.pools.length,7);assert.equal(new Set(layout.pools).size,1,'wide funnel shows all seven stages in one row');}
    }
   }
  }
  await evaluate('AT.navigate("R03");true');
  await wait('document.querySelector("h1")?.innerText==="策略回测"','return to same backtest draft');
  assert.equal(await snapshot(),draft,'navigation, resize and expanding settings retain backtest and plateau drafts');
  await evaluate('AT.navigate("R05");true');
  await wait('document.querySelector("h1")?.innerText==="收益平原"','shared settings in plateau');
  await frame.executeJavaScript(`(()=>{const button=[...document.querySelectorAll('button')].find(el=>el.innerText==='调整回测设置');button.click();return true})()`);
  await wait('!document.querySelector("[data-research=backtest-config]").hidden','plateau exposes the real shared backtest form');
  assert.equal(await frame.executeJavaScript('document.querySelectorAll("[data-research=backtest-config] .ant-picker-range input").length'),2,'both shared dates remain editable');
  await frame.executeJavaScript(`(()=>{const button=[...document.querySelectorAll('button')].find(el=>el.innerText==='收起设置');button.click();return true})()`);
  assert.equal(await snapshot(),draft,'showing shared settings does not replace the draft');
  // Measure the native workspace after the iframe checks: Chromium does not
  // commit a hidden child frame's emulated viewport until it is painted again.
  await evaluate('AT.navigate("R15");true');
  for(const width of [1920,1366,1024]){
   await win.webContents.debugger.sendCommand('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
   await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
   const native=await evaluate(`(()=>{const root=document.documentElement;return {page:'R15',width:root.scrollWidth,client:root.clientWidth}})()`);
   evidence.push({windowWidth:width,...native});
   assert.ok(native.width<=native.client+1,'portfolio research stays inside the workspace');
  }
 }finally{
  await win.webContents.debugger.sendCommand('Emulation.clearDeviceMetricsOverride');
  win.webContents.debugger.detach();
  fs.writeFileSync(path.join(__dirname,'../artifacts/research-responsive-layout.json'),JSON.stringify(evidence,null,2));
 }
 console.log('PASS strategy workspace at 1920/1366/1024px, collapsed and expanded settings, focused views and retained drafts');
};
