const {app,protocol,session}=require('electron'),fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),requests=[],plans=[],errors=[];let win,server,directory;
protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function fixture(){
 directory=await fs.mkdtemp(path.join(os.tmpdir(),'atrade-emotion-ai-'));
 app.setPath('userData',path.join(directory,'profile'));
 const market=path.join(directory,'market'),dates=[];
 for(let day=new Date('2026-04-01T00:00:00Z');dates.length<90;day=new Date(day.getTime()+86400000))if(day.getUTCDay()>0&&day.getUTCDay()<6)dates.push(day.toISOString().slice(0,10));
 await fs.mkdir(path.join(market,'vipdoc/sh/lday'),{recursive:true});await fs.mkdir(path.join(market,'T0002/hq_cache'),{recursive:true});
 const tnf=Buffer.alloc(50+30*360);
 for(let stock=0;stock<30;stock++){
  const code=String(600001+stock),name=stock===0?'LEADER A':stock===1?'LEADER B':'SAMPLE '+stock;
  tnf.write(code,50+stock*360,'ascii');tnf.write(name,50+stock*360+31,'ascii');
  const bytes=Buffer.alloc(dates.length*32);let close=1000+stock*10;
  for(let i=0;i<dates.length;i++){
   const open=close;close=Math.round(close*(stock<2&&i>=dates.length-4?1.1:1.003));const offset=i*32,volume=i>=dates.length-4?350000:100000;
   for(const [n,value] of [Number(dates[i].replaceAll('-','')),open,Math.max(open,close)+1,Math.min(open,close)-1,close].entries())bytes.writeUInt32LE(value,offset+n*4);
   bytes.writeFloatLE(close/100*volume,offset+20);bytes.writeUInt32LE(volume,offset+24);
  }
  await fs.writeFile(path.join(market,'vipdoc/sh/lday','sh'+code+'.day'),bytes);
 }
 await fs.writeFile(path.join(market,'T0002/hq_cache/shs.tnf'),tnf);
 const guard=path.join(directory,'guard');await fs.mkdir(guard);
 await fs.writeFile(path.join(guard,'sitecustomize.py'),"import urllib.request\ndef blocked(self, request, *args, **kwargs):\n raise RuntimeError('Public network disabled in emotion AI test')\nurllib.request.OpenerDirector.open = blocked\n");
 process.env.PYTHONPATH=[guard,root].join(path.delimiter);return {market,dates};
}
function reply(res,text){if(!res.destroyed)res.end(JSON.stringify({choices:[{message:{role:'assistant',content:text}}],usage:{prompt_tokens:10,completion_tokens:10}}));}
app.whenReady().then(async()=>{
 try{
  const {market,dates}=await fixture(),latest=dates.at(-1),historical=dates.at(-2);
  server=http.createServer(async(req,res)=>{let body='';for await(const chunk of req)body+=chunk;requests.push(JSON.parse(body));const plan=plans.shift();res.setHeader('Content-Type','application/json');if(!plan){errors.push('Unexpected automatic AI request');res.statusCode=500;res.end('{}');return;}if(plan.type==='hold'){plan.release=()=>reply(res,plan.text);return;}if(plan.type==='error'){res.statusCode=500;res.end('{}');return;}reply(res,plan.text);});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const baseUrl=`http://127.0.0.1:${server.address().port}/v1`;
  session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(_details,callback)=>callback({cancel:true}));
  win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory:path.join(directory,'workspace'),show:false,onWindow(w){w.webContents.setBackgroundThrottling(false)}});
  const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw Error(error.message+' ['+code.slice(0,250)+']')});
  const wait=async(expression,label)=>{for(let i=0;i<300;i++){if(await evaluate(expression))return;await delay(100)}throw Error('Timeout '+label+' '+await evaluate('document.querySelector("#main").innerText.slice(-1300)'))};
  const click=async selector=>{await wait(`!!document.querySelector(${JSON.stringify(selector)})&&!document.querySelector(${JSON.stringify(selector)}).disabled`,'click '+selector);await evaluate(`document.querySelector(${JSON.stringify(selector)}).click();true`)};
  const panel='#page-ai-panel',first='[data-emotion-leader-ai="sh600001"]',second='[data-emotion-leader-ai="sh600002"]';
  const action=name=>click(`[data-page-ai="${name}"]`);
  const ask=async text=>{await evaluate(`(()=>{const input=document.querySelector('#page-ai-question');input.value=${JSON.stringify(text)};input.dispatchEvent(new Event('input',{bubbles:true}));})()`);await action('send')};
  assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(market)}})`)).ok,true);assert.equal((await evaluate(`atradeDesktop.market('market.scan')`)).ok,true);
  await evaluate('AT.marketUI.refresh()');await evaluate('AT.navigate("T10");true');await wait(`document.querySelectorAll('[data-emotion-leader-ai]').length>=2`,'real leader rows');
  await delay(200);assert.equal(requests.length,0,'entering never sends AI');
  await click(first);await wait(`!document.querySelector('${panel}').hidden&&document.querySelector('${panel}')?.innerText.includes('配置并启用文本 AI')`,'missing model guidance');assert.equal(requests.length,0);
  assert.equal(await evaluate('document.querySelectorAll(".emotion-ai-panel").length'),0,'one shared panel');
  await action('settings');await wait(`!!document.querySelector('[data-ai-profile=text]')`,'AI settings');
  await evaluate(`(()=>{const form=document.querySelector('[data-ai-profile=text]');for(const [name,value] of Object.entries({baseUrl:${JSON.stringify(baseUrl)},model:'emotion-ai-test-model',apiKey:'emotion-ai-isolated-test-key'})){form.elements[name].value=value;form.elements[name].dispatchEvent(new Event('input',{bubbles:true}));}form.elements.enabled.checked=true;form.elements.enabled.dispatchEvent(new Event('input',{bubbles:true}));form.querySelector('[data-ai-action=save]').click();return true})()`);
  await wait('AT.aiSettings.ready("text")&&!AT.aiSettings.hasUnsaved()','saved test model');await evaluate('AT.navigate("T10");true');assert.equal(requests.length,0);
  plans.push({text:'FIRST_LEADER **证据分析**\n<img src=x onerror=run()>\n<script>run()</script>'});
  await click(first);await click(first);await wait(`document.querySelector('${panel} .page-ai-answer')?.textContent.includes('FIRST_LEADER')`,'first clicked leader');assert.equal(requests.length,1,'double click sends once');
  assert.equal(await evaluate(`document.querySelectorAll('${panel} .page-ai-answer img,${panel} .page-ai-answer script').length`),0);
  const held={type:'hold',text:'LATE_SECOND'};plans.push(held);await click(second);await wait(`!document.querySelector('[data-page-ai=stop]').hidden`,'second pending');
  for(let i=0;i<100&&!held.release;i++)await delay(50);assert.equal(requests.length,2);
  await action('stop');held.release();await delay(150);assert.doesNotMatch(await evaluate(`document.querySelector('${panel}').innerText`),/FIRST_LEADER|LATE_SECOND/);
  plans.push({type:'error'});await action('send');await wait(`!!document.querySelector('${panel} [role=alert]')&&document.querySelector('[data-page-ai=stop]').hidden`,'provider failure');
  plans.push({text:'SECOND_LEADER'});await action('send');await wait(`document.querySelector('${panel} .page-ai-answer')?.textContent.includes('SECOND_LEADER')`,'retry succeeds');
  await click(first);assert.equal(requests.length,4,'view first cached result');assert.match(await evaluate(`document.querySelector('${panel}').innerText`),/FIRST_LEADER/);
  await evaluate(`document.querySelector('[data-emotion-day="${historical}"]').click();true`);await wait(`AT.emotionCycle.getAIContext().dataDates[0]==='${historical}'&&!document.querySelector('${panel}').innerText.includes('FIRST_LEADER')`,'historical date');assert.equal(requests.length,4);
  plans.push({text:'HISTORICAL_LEADER'});await click(first);await wait(`document.querySelector('${panel} .page-ai-answer')?.textContent.includes('HISTORICAL_LEADER')`,'historical clicked leader');assert.equal(requests.length,5);
  for(const [index,request] of requests.entries()){
   const content=request.messages[1].content,packet=JSON.parse(content.slice(content.indexOf('{'))),context=packet.pageContext;
   assert.equal(context.page.id,'T10');assert.equal(context.evidence.selectedStock.symbol,index>=1&&index<=3?'sh600002':'sh600001');
   const cutoff=index===4?historical:latest;assert.deepEqual(context.dataDates,[cutoff]);assert.ok(context.evidence.dailyBars.every(row=>row.date<=cutoff));assert.equal(context.evidence.dailyBars.at(-1).date,cutoff);
   assert.equal(context.evidence.leaders.some(row=>Array.isArray(row.dailyBars)),false);
   assert.doesNotMatch(content,/emotion-ai-isolated-test-key|dataVersion|dataAsOf/);assert.ok(!content.includes(directory));
  }
  await action('page-topic');assert.doesNotMatch(await evaluate(`document.querySelector('${panel}').innerText`),/HISTORICAL_LEADER/);
  plans.push({text:'GLOBAL_COMPARISON'});await ask('比较 LEADER A 和 sh600002 的量价表现');
  await wait(`document.querySelector('${panel} .page-ai-answer')?.textContent.includes('GLOBAL_COMPARISON')`,'global name/code comparison');assert.equal(requests.length,6);
  let packet=JSON.parse(requests[5].messages[1].content.slice(requests[5].messages[1].content.indexOf('{')));
  assert.deepEqual(packet.pageContext.evidence.requestedStocks.map(row=>row.stock.symbol),['sh600002','sh600001']);
  plans.push({text:'EARLIER_DATE'});await ask(`LEADER A 在 ${dates.at(-5)} 的表现如何`);
  await wait(`Array.from(document.querySelectorAll('${panel} .page-ai-answer')).some(row=>row.textContent.includes('EARLIER_DATE'))`,'question date override');
  packet=JSON.parse(requests[6].messages[1].content.slice(requests[6].messages[1].content.indexOf('{')));
  assert.equal(packet.pageContext.evidence.dailyBars.at(-1).date,dates.at(-5));assert.equal(packet.pageContext.evidence.current,undefined);
  assert.equal(packet.conversationHistory.length,0,'later answers excluded from earlier question');
  await ask('分析999999');await wait(`document.querySelector('${panel} [role=alert]')?.innerText.includes('未找到')`,'unknown code guidance');assert.equal(requests.length,7);
  await evaluate(`window.__originalPageAiCollect=AT.pageAIMaterials.collect;window.__pendingPageAiRead=null;AT.pageAIMaterials.collect=options=>__originalPageAiCollect({...options,market:(method,payload)=>method==='market.bars'?new Promise(resolve=>{window.__pendingPageAiRead=()=>options.market(method,payload).then(resolve)}):options.market(method,payload)});true`);
  await ask('分析sh600001');await wait('!!window.__pendingPageAiRead','reading material');await action('stop');
  await evaluate('window.__pendingPageAiRead();AT.pageAIMaterials.collect=__originalPageAiCollect;true');await delay(250);assert.equal(requests.length,7);
  for(const width of [1366,1024]){win.setSize(width,900);await delay(150);assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false,'no page overflow '+width);}
  await evaluate(`document.querySelector('${panel}').scrollIntoView({block:'center'});true`);
  await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
  await delay(150);
  await fs.writeFile(path.join(root,'artifacts/emotion-leader-ai-native.png'),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());
  await evaluate(`AT.marketUI.openDay('sh600001','${dates.at(-5)}');true`);
  await wait(`document.querySelector('[data-market-date]')?.value==='${dates.at(-5)}'`,'native stock historical focus');
  await evaluate('AT.pageAI.refresh();true');assert.equal(requests.length,7,'opening a stock fetches no AI');
  plans.push({text:'STOCK_CHART_HISTORY'});await ask('这只股量能如何');
  await wait(`document.querySelector('${panel} .page-ai-answer')?.textContent.includes('STOCK_CHART_HISTORY')`,'global stock context');
  packet=JSON.parse(requests[7].messages[1].content.slice(requests[7].messages[1].content.indexOf('{')));
  assert.equal(packet.pageContext.page.id,'G01');assert.equal(packet.pageContext.evidence.dailyBars.at(-1).date,dates.at(-5));assert.equal(packet.pageContext.tables.length,0,'later chart/strategy results excluded');
  assert.deepEqual(errors,[]);console.log('PASS unified AI: shared panel, name/code comparison, historical questions and chart focus, separate histories, zero automatic calls, cancellation during data reads/provider work, retries, privacy and responsive layout');console.log('Isolated fixture: '+directory);
 }finally{win?.destroy();server?.closeAllConnections();server?.close()}app.exit(0);
}).catch(error=>{console.error(error.stack);console.error('Isolated fixture: '+directory);win?.destroy();server?.closeAllConnections();server?.close();app.exit(1)});
