// Real Electron/IPC/Python/cache; only the public HTTP provider is a fixture.
const {app,protocol,shell,dialog}=require('electron');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const directory=require('node:fs').mkdtempSync(path.join(os.tmpdir(),'atrade-information-'));
const source=path.join(directory,'tdx'), profile=path.join(directory,'profile');
const guard=path.join(directory,'provider'), offline=path.join(guard,'offline');
const errors=[],opened=[];let win;
app.setPath('userData',profile);
protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
shell.openExternal=async url=>{opened.push(url);};
dialog.showMessageBox=async(_win,options)=>{throw Error('Unexpected dialog '+options.message);};
const evaluate=code=>win.webContents.executeJavaScript(code);
async function wait(code,label=code){const start=Date.now();while(Date.now()-start<20000){if(await evaluate(code))return;await new Promise(r=>setTimeout(r,70));}throw Error('Timeout '+label);}
const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click();true`);
const fill=(selector,value)=>evaluate(`(() => {const el=document.querySelector(${JSON.stringify(selector)});el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const styleSelector='[data-market-style-chart]';
const styleWindow=()=>evaluate(`(() => {const chart=document.querySelector(${JSON.stringify(styleSelector)});return {start:Number(chart.dataset.styleStart),count:Number(chart.dataset.styleCount),total:Number(chart.dataset.styleTotal)};})()`);
async function stylePoint(ratio=.5){
  await evaluate(`document.querySelector(${JSON.stringify(styleSelector)}).scrollIntoView({block:'center',behavior:'instant'});true`);
  return evaluate(`(() => {const rect=document.querySelector(${JSON.stringify(styleSelector)}).getBoundingClientRect();return {x:Math.round(rect.left+rect.width*${ratio}),y:Math.round(rect.top+rect.height*.5)};})()`);
}
async function mouseInput(event){
  win.webContents.sendInputEvent(event);
  await new Promise(resolve=>setTimeout(resolve,25));
  await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
}
function checkStyleBounds(value){assert.ok(value.count>=Math.min(10,value.total)&&value.count<=value.total,'时间轴缩放数量有效');assert.ok(value.start>=0&&value.start+value.count<=value.total,'时间轴没有越界');}
async function wheelStyle(deltaY,ratio=.5,changed=true){
  const before=await styleWindow(),point=await stylePoint(ratio);
  const prevented=await evaluate(`(() => {const event=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:${deltaY},clientX:${point.x},clientY:${point.y}});document.querySelector(${JSON.stringify(styleSelector)}).dispatchEvent(event);return event.defaultPrevented;})()`);
  await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
  if(changed)await wait(`Number(document.querySelector(${JSON.stringify(styleSelector)}).dataset.styleCount)!==${before.count}`,'滚轮缩放市场风格时间轴');
  const after=await styleWindow();checkStyleBounds(after);assert.equal(prevented,true,'时间轴内滚轮用于缩放');
  return {before,after};
}
async function dragStyle(button,distance,changed=true){
  const before=await styleWindow(),from=await stylePoint(.45),to=await stylePoint(.45+distance);
  await mouseInput({type:'mouseMove',...from});
  await mouseInput({type:'mouseDown',...from,button,clickCount:1});
  try{
    for(let step=1;step<=3;step++)await mouseInput({type:'mouseMove',x:Math.round(from.x+(to.x-from.x)*step/3),y:from.y,modifiers:[`${button}ButtonDown`]});
    assert.equal(await evaluate('document.documentElement.classList.contains("market-style-dragging")'),true,'按住移动进入风格时间轴拖动');
  }finally{await mouseInput({type:'mouseUp',...to,button,clickCount:1});}
  const after=await styleWindow();checkStyleBounds(after);assert.equal(after.count,before.count,'平移保持可见交易日数量');
  assert.equal(await evaluate('document.documentElement.classList.contains("market-style-dragging")'),false,'松开鼠标结束风格时间轴拖动');
  if(changed)assert.ok(distance>0?after.start<before.start:after.start>before.start,`${button}键平移方向正确 ${JSON.stringify({before,after})}`);
  await mouseInput({type:'mouseMove',...from});assert.deepEqual(await styleWindow(),after,'松开鼠标后的悬停不继续平移');
  return {before,after};
}
async function selectStyleDate(day){
  await evaluate(`(() => {const input=document.querySelector('[data-market-style-date]');input.value=${JSON.stringify(day)};input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await wait(`!!document.querySelector('[data-market-style-day="${day}"]')`,'所选日期移入可视范围');
  assert.ok((await evaluate('document.querySelector(".market-analysis-date").innerText')).includes(day));
}
async function screenshot(name){const [width,height]=win.getSize();win.setSize(width+1,height);await new Promise(r=>setTimeout(r,60));win.setSize(width,height);await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await new Promise(r=>setTimeout(r,160));await fs.mkdir(path.join(root,'artifacts'),{recursive:true});await fs.writeFile(path.join(root,'artifacts',name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function fixture(){
  await fs.mkdir(guard,{recursive:true});
  const daily=path.join(source,'vipdoc/sh/lday');await fs.mkdir(daily,{recursive:true});
  const rows=[],day=new Date('2025-01-01T00:00:00Z');
  while(rows.length<280){if(day.getUTCDay()!==0&&day.getUTCDay()!==6){const row=Buffer.alloc(32),value=1000+rows.length;row.writeUInt32LE(Number(day.toISOString().slice(0,10).replaceAll('-','')),0);for(const offset of [4,8,12,16])row.writeUInt32LE(value,offset);row.writeFloatLE(1000000,20);row.writeUInt32LE(100000,24);rows.push(row);}day.setUTCDate(day.getUTCDate()+1);}
  await fs.writeFile(path.join(daily,'sh600900.day'),Buffer.concat(rows));
  await fs.writeFile(path.join(guard,'sitecustomize.py'), `
import os
import urllib.request
from pathlib import Path
from service import market_information as info
def fake(endpoint, params):
    if Path(os.environ['ATRADE_INFORMATION_TEST_OFFLINE']).exists():
        raise ValueError('测试来源暂时断连')
    if endpoint == 'news':
        return {'code':'1','data':{'fastNewsList':[{'code':'202601100000000'+str(i).zfill(2),'title':'快讯夹具 '+str(i).zfill(2)+' 宏观','summary':'<b>公开摘要</b><script>unexpected()</script>','showTime':'2026-01-10 10:'+str(i).zfill(2)+':00','stockList':['1.600900']} for i in range(26)]}}
    if endpoint == 'announcements':
        return {'success':1,'data':{'list':[{'art_code':'AN20260110000000'+str(i),'title':'公告夹具 '+str(i),'display_time':'2026-01-10 11:00:00','notice_date':'2026-01-10','codes':[{'stock_code':'600900','market_code':'1'}]} for i in range(3)]}}
    raise ValueError('本测试未开放其他提供方')
def no_network(*args, **kwargs):
    raise ValueError('测试禁止真实网络请求')
info.fetch_json = fake
urllib.request.OpenerDirector.open = no_network
`, 'utf8');
  process.env.PYTHONPATH=guard+path.delimiter+root;
  process.env.ATRADE_INFORMATION_TEST_OFFLINE=offline;
}
app.whenReady().then(async()=>{
  await fixture();
  win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory:path.join(directory,'workspace'),show:false,onWindow(w){w.webContents.setBackgroundThrottling(false);w.webContents.on('console-message',event=>{if(event.level==='error')errors.push(event.message);});}});
  assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(source)}})`)).ok,true);
  assert.equal((await evaluate("atradeDesktop.market('market.scan')")).ok,true);
  await evaluate("AT.marketUI.refresh();AT.navigate('M01');true");
  await wait('document.querySelectorAll("[data-market-style-day]").length===250','250天风格');
  const earliest=await evaluate('document.querySelector("[data-market-style-day]").dataset.marketStyleDay');
  const latest=await evaluate('Array.from(document.querySelectorAll("[data-market-style-day]")).at(-1).dataset.marketStyleDay');
  await click(`[data-market-style-day="${earliest}"]`);
  assert.ok((await evaluate('document.querySelector(".market-analysis-date").innerText')).includes(earliest));
  assert.equal(await evaluate('document.querySelector("[data-market-leader-evidence] [data-leader-day]").dataset.leaderDay'),earliest,'证据与所选日期同步');
  assert.equal(await evaluate('document.querySelectorAll(".market-leader-evidence-grid article").length'),3,'分开展示接力、趋势与风险');
  assert.deepEqual(await styleWindow(),{start:0,count:250,total:250},'默认展示完整历史');
  const styleDetail=await evaluate('document.querySelector(".market-analysis-lead").innerText+document.querySelector(".market-analysis-facts").innerText');
  const zoomed=await wheelStyle(-240,.5);
  assert.ok(zoomed.after.count<zoomed.before.count,'向上滚轮放大时间轴');
  assert.ok(Math.abs((zoomed.after.start+zoomed.after.count*.5)-(zoomed.before.start+zoomed.before.count*.5))<=2,'缩放保留鼠标位置对应交易日');
  await dragStyle('left',.08);
  await dragStyle('right',-.08);
  assert.equal(await evaluate('document.querySelector(".market-analysis-lead").innerText+document.querySelector(".market-analysis-facts").innerText'),styleDetail,'缩放和平移不会改写所选日风格及统计');
  const expanded=await wheelStyle(120,.5);assert.ok(expanded.after.count>expanded.before.count,'向下滚轮缩小时间轴');
  for(let step=0;step<30&&(await styleWindow()).count>10;step++)await click('[data-market-style-zoom="in"]');
  assert.equal((await styleWindow()).count,10,'最小展示10个交易日');
  const minimum=await styleWindow();await click('[data-market-style-zoom="in"]');assert.deepEqual(await styleWindow(),minimum,'继续放大不越过最小范围');
  await selectStyleDate(earliest);assert.equal((await styleWindow()).start,0,'日期选择能够回到最早记录');
  const atStart=await dragStyle('left',.2,false);assert.equal(atStart.after.start,0,'向最早端之外拖动不越界');
  await selectStyleDate(latest);assert.equal((await styleWindow()).start,240,'日期选择能够回到最新记录');
  await click('[data-market-style-step="-1"]');
  assert.equal(await evaluate('document.querySelector("[data-market-style-date]").value'),await evaluate('document.querySelectorAll("[data-market-style-day]")[8].dataset.marketStyleDay'),'前一日在缩放窗口中可见');
  await click('[data-market-style-step="1"]');assert.equal(await evaluate('document.querySelector("[data-market-style-date]").value'),latest,'后一日可回到最新记录');
  const outside=await evaluate('(() => {const event=new WheelEvent("wheel",{bubbles:true,cancelable:true,deltaY:120});document.querySelector(".market-style-history-controls").dispatchEvent(event);return event.defaultPrevented;})()');
  assert.equal(outside,false,'时间轴外滚轮保留页面滚动');
  await click('[data-market-style-zoom="reset"]');assert.deepEqual(await styleWindow(),{start:0,count:250,total:250},'重置恢复全部已载入历史');
  await click('[data-market-style-zoom="in"]');assert.ok((await styleWindow()).count<250);
  const beforeZoomOut=await styleWindow();await click('[data-market-style-zoom="out"]');assert.ok((await styleWindow()).count>beforeZoomOut.count,'缩小按钮扩大可视窗口');
  await click('[data-market-style-zoom="in"]');
  await click('[data-market-history-days="60"]');
  await wait('document.querySelectorAll("[data-market-style-day]").length===60','60天风格');
  assert.deepEqual(await styleWindow(),{start:0,count:60,total:60},'切换历史范围重置缩放');
  await click('[data-market-style-step="-1"]');
  assert.equal(await evaluate('document.querySelector("[data-market-style-date]").value'),await evaluate('document.querySelectorAll("[data-market-style-day]")[58].dataset.marketStyleDay'));
  await click('[data-market-history-days="1000"]');
  await wait('document.querySelectorAll("[data-market-style-day]").length===280','实际历史不足1000不填充');
  assert.deepEqual(await styleWindow(),{start:0,count:280,total:280},'较长范围展示全部实际记录');
  await screenshot('market-style-history.png');
  const leaderDay=await evaluate('document.querySelector("[data-leader-day]").dataset.leaderDay');
  await click('[data-leader-day]');
  await wait(`AT.state.page==='T08'&&document.querySelector('[data-analysis-group="research"][data-analysis-field="asOf"]')?.value===${JSON.stringify(leaderDay)}&&document.querySelector('.leader-research-heading p')?.textContent!==''`,'从市场风格进入同日连板龙头');
  await wait('!!document.querySelector("[data-leader-chart]")','同日历史证据加载');
  assert.match(await evaluate('document.querySelector("[data-leader-research]").innerText'),/昨日连板|趋势候选/);
  await evaluate("AT.navigate('M05');true");
  await wait('document.querySelectorAll(".market-news-item").length===20','公开资讯');
  assert.match(await evaluate('document.querySelector(".market-news-pagination").innerText'),/29/);
  assert.equal(await evaluate('document.querySelectorAll(".market-news-item script").length'),0);
  await click('[data-market-news-page="1"]');
  await wait('document.querySelectorAll(".market-news-item").length===9','资讯分页');
  await click('[data-market-news-source="announcements"]');
  await wait('document.querySelectorAll(".market-news-item").length===3','独立公告源');
  await click('[data-market-news-url]');
  await wait('true');assert.equal(opened.length,1);assert.match(opened[0],/^https:\/\/data\.eastmoney\.com\/notices\/detail\/600900\//);
  assert.equal((await evaluate("atradeDesktop.openExternal('file:///C:/Windows/notepad.exe')")).ok,false);
  assert.equal(opened.length,1);
  await fill('[data-market-news-input="query"]','公告夹具 1');
  await fill('[data-market-news-input="symbol"]','600900');
  await evaluate('document.querySelector("#market-news-search").requestSubmit();true');
  await wait('document.querySelectorAll(".market-news-item").length===1','股票与关键词筛选');
  await click('[data-market-news-action="clear"]');
  await wait('document.querySelectorAll(".market-news-item").length===3','清空条件');
  await fs.writeFile(offline,'offline');
  await click('[data-market-news-action="refresh"]');
  await wait('document.querySelector(".market-news-sources").innerText.includes("旧缓存")','断线保留旧资讯');
  assert.equal(await evaluate('document.querySelectorAll(".market-news-item").length'),3);
  await screenshot('market-news-desktop.png');
  win.setSize(900,700);await new Promise(r=>setTimeout(r,100));
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth && document.querySelector("main").scrollWidth<=document.querySelector("main").clientWidth+1'),true);
  await screenshot('market-news-narrow.png');
  assert.deepEqual(errors,[]);
  console.log('PASS 长历史范围、滚轮缩放、左右键拖动、日期定位、资讯提供方、IPC、过滤分页、原文安全与断线缓存');
  win.once('closed',()=>app.exit(0));win.close();
}).catch(async error=>{console.error(error.stack);if(win&&!win.isDestroyed()){await screenshot('market-news-failure.png').catch(()=>{});win.destroy();}app.exit(1);});
