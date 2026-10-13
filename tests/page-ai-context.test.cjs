const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const context=require('../preview/page-ai-context.js');
const {sanitizeContext,focusFor}=require('../desktop/page-assistant-prompt.cjs');

function marketStyleFixture() {
  const observation=(date,label)=>({date,label:'趋势扩散',confirmationDays:2,reason:'普通大类连续两日确认',metrics:{sampleCount:4000,expectedCount:5000,coverage:.8,breadth:.6,medianReturn5:-2,medianReturn20:5,medianAmountRatio20:.75},
    profile:{version:'test-profile',status:'observed',headline:label,summary:'当日观察',dimensions:[{id:'trend',title:'趋势阶段',key:'trend_pullback',label,status:'observed',sampleCount:4000,streakDays:20,durationCapped:true,evidence:[{label:'5 日中位涨幅',value:-2,unit:'percent',threshold:'<-1%'}]},{id:'relay',label:'样本不足',status:'insufficient',sampleCount:1,evidence:[{label:'昨日溢价',value:null,unit:'percent'}]}],changes:[]}});
  return {version:'test',asOf:'2026-10-09',timeline:[observation('2026-10-07','趋势回调'),observation('2026-10-08','偏强整理'),observation('2026-10-09','FUTURE_STYLE')],style:observation('2026-10-09','FUTURE_STYLE'),warnings:['未复权日线'],rules:{profile:{trend:'5日和20日分别观察'}},source:'C:\\private-source',apiKey:'PRIVATE_KEY'};
}

class Element {
  constructor(tag,attrs={},children=[]) {
    this.nodeType=1;this.tagName=tag.toUpperCase();this.attrs=attrs;this.dataset=Object.fromEntries(Object.entries(attrs).filter(([key])=>key.startsWith('data-')).map(([key,value])=>[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),value]));
    this.hidden=!!attrs.hidden;this.type=attrs.type;this.value=attrs.value||'';this.name=attrs.name||'';this.childNodes=[];this.colSpan=Number(attrs.colspan)||1;
    for(const child of children)this.append(child);
  }
  append(child){if(typeof child==='string')child={nodeType:3,nodeValue:child,textContent:child};child.parentElement=this;this.childNodes.push(child);return child;}
  getAttribute(key){return this.attrs[key]??null;}
  matches(selector){return selector.split(',').some(part=>{part=part.trim();const tag=part.match(/^[a-z]+/i)?.[0];if(tag&&this.tagName!==tag.toUpperCase())return false;const id=part.match(/#([\w-]+)/)?.[1];if(id&&this.attrs.id!==id)return false;for(const found of part.matchAll(/\.([\w-]+)/g))if(!String(this.attrs.class||'').split(/\s+/).includes(found[1]))return false;for(const found of part.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)){if(!(found[1]in this.attrs))return false;if(found[2]!==undefined&&String(this.attrs[found[1]])!==found[2])return false;}return true;});}
  closest(selector){for(let el=this;el;el=el.parentElement)if(el.matches?.(selector))return el;return null;}
  querySelectorAll(selector){const result=[];for(const child of this.childNodes){if(child.nodeType!==1)continue;if(child.matches(selector))result.push(child);result.push(...child.querySelectorAll(selector));}return result;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const el=(tag,attrs,...children)=>new Element(tag,attrs,children.flat());
function table(headers,rows) {
  const head=el('tr',{},headers.map(value=>typeof value==='string'?el('th',{},value):value));head.cells=head.childNodes;
  const bodyRows=rows.map(values=>{const row=el('tr',{},values.map(value=>typeof value==='string'?el('td',{},value):value));row.cells=row.childNodes;return row;});
  const thead=el('thead',{},head),tbody=el('tbody',{},bodyRows),result=el('table',{},thead,tbody);thead.rows=[head];tbody.rows=bodyRows;result.tHead=thead;result.tBodies=[tbody];return result;
}
function page(id,children=[],workspace=null,api={}) {
  const main=el('main',{id:'main'},children),body=el('body',{},main,...(workspace?[workspace]:[]));
  const AT={isDesktop:true,state:{page:id,stock:'sh600001'},pages:{[id]:{title:id==='T08'?'连板龙头':id==='T04'?'趋势龙头':id}},...api};
  return {AT,document:body,main};
}
function analysisUI(handler) {
  const events=new Map(),queue=[],calls=[];
  const AT={isDesktop:true,state:{page:'T08',stock:''},escape:value=>String(value??''),icon:()=>'',viewParts:{head:(title,sub)=>`<h1>${title}</h1>${sub}`},pages:Object.fromEntries(Array.from({length:9},(_,i)=>[`T0${i+1}`,{title:`工具${i+1}`}])),view:()=>'',render(){},leaderResearch:{panel:()=>'',deferRender:()=>false},marketUI:{status:{path:'C:\\private-source',latestDate:'2026-09-30'}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/analysis-tools.js'),'utf8'),{AT,window:{atradeDesktop:{market:async(method,params)=>{calls.push([method,params]);return {ok:true,data:await handler(method,params)};}},addEventListener(){}},document:{addEventListener:(type,listener)=>events.set(type,listener),querySelector:()=>null},queueMicrotask:callback=>queue.push(callback),setTimeout:()=>({unref(){}}),clearTimeout(){},console});
  return {AT,calls,input:(group,key,value)=>events.get('input')({target:{dataset:{analysisGroup:group,analysisField:key},value,matches:()=>false}}),click:dataset=>events.get('click')({target:{closest:()=>({dataset,disabled:false})},preventDefault(){}}),async render(id){AT.state.page=id;AT.view(id);while(queue.length){queue.shift()();await new Promise(resolve=>setImmediate(resolve));}},capture:()=>JSON.parse(JSON.stringify(AT.analysisTools.getAIContext()))};
}

test('T08 exposes current cached historical evidence, preserves missing observations and excludes future rows',async()=>{
  const metrics=(height,missing=0)=>({maxHeight:height,observedCount:20,promotion:{all:{eligible:4,observed:4-missing,missing,promoted:1,rate:missing===4?null:.25},firstToSecond:{eligible:3,observed:2,missing:1,promoted:1,rate:.5},secondToThird:{eligible:2,observed:2,missing:0,promoted:1,rate:.5}},trend:{eligible:20,count:3,amountShare:.3},premium:{eligible:3,observed:2,missing:1,median:null},leaders:[{symbol:'sh600001',name:'样本股票',height}]});
  const app=analysisUI(async()=>({asOf:'2026-09-29',source:'C:\\private-source',provider:'tdx_local_analysis',apiKey:'must-not-leak',coverage:{total:40,valid:20,stale:2},leaders:[{symbol:'sh600001',name:'样本股票',date:'2026-09-29',height:3},{symbol:'sh600002',name:'FUTURE',date:'2026-09-30',height:9}],yesterdayLeaders:[{symbol:'sh600001',name:'样本股票',previousHeight:2,status:'promoted'}],timeline:[{date:'2026-09-28',metrics:metrics(2,4)},{date:'2026-09-29',metrics:metrics(3)},{date:'2026-09-30',metrics:metrics(9)}],leadership:{metrics:metrics(9),rotationEvents:[{date:'2026-09-29',leaders:[{symbol:'sh600001'}],reason:'leader_change'},{date:'2026-09-30',leaders:[{symbol:'sh600002'}],reason:'future'}]},rules:{timelineScope:'全市场，不受候选筛选影响',leaderScope:'按交易板与ST筛选'}}));
  app.input('research','asOf','2026-09-29');await app.render('T08');const before=app.calls.length,result=app.capture();
  assert.equal(app.calls.length,before,'获取上下文不触发服务调用');assert.equal(result.pageId,'T08');assert.deepEqual(result.evidence.timeline.rows.map(row=>row.date),['2026-09-28','2026-09-29']);
  assert.equal(result.evidence.timeline.rows[0].promotion.missing,4);assert.equal(result.evidence.timeline.rows[0].promotion.rate,null);assert.equal(result.evidence.metrics.maxHeight,3);
  assert.equal(result.evidence.currentLeaders.rows.length,1);assert.equal(result.evidence.rotations.length,1);assert.equal(result.evidence.coverage.total,40);assert.doesNotMatch(JSON.stringify(result),/private-source|must-not-leak|FUTURE|"future"/);
  assert.equal(result.evidence.metrics.firstToSecond.rate,.5);assert.equal(result.evidence.metrics.secondToThird.observed,2);assert.match(result.evidence.metricUnits.promotionRate,/乘100/);
});

test('current T04 request owns pagination and changing page or filters never reuses another context',async()=>{
  const app=analysisUI(async(method,params)=>method.endsWith('.ladder')?{asOf:'2026-09-29',timeline:[{date:'2026-09-29',metrics:{maxHeight:8}}]}:{asOf:'2026-09-29',total:58,hasMore:true,items:[{symbol:'sh600003',name:'趋势候选',date:'2026-09-29',return20:24,return60:55}]});
  await app.render('T08');await app.render('T04');app.click({analysisAction:'research-next'});await app.render('T04');
  const result=app.capture();assert.equal(result.pageId,'T04');assert.equal(result.evidence.pagination.totalRows,58);assert.equal(result.evidence.pagination.from,21);assert.equal(result.evidence.pagination.to,21);assert.equal(result.evidence.currentRows[0].return20,24);assert.equal(result.evidence.timeline,undefined);
  app.input('research','asOf','2026-08-31');const before=app.calls.length,unloaded=app.capture();assert.equal(unloaded.status,'not-loaded');assert.equal(unloaded.evidence.currentRows,undefined);assert.equal(app.calls.length,before);
});

test('generic visible page capture excludes chat, hidden text, form contents, local paths and small K columns',()=>{
  const khead=el('th',{'data-stock-kline-heading':''},'日K线'),rows=Array.from({length:40},(_,i)=>[`${i+1} 样本`,el('td',{class:'stock-kline-cell'},'K_CANDLE_PRIVATE'),String(i)]);
  const grid=table(['证券',khead,'20日涨幅'],rows),section=el('section',{},el('h2',{},'趋势表'),grid,el('div',{class:'analysis-tools-pagination'},'第 21—60 项 / 80 项'));
  const env=page('M02',[el('p',{},'行情截至 2026-09-29 来源 C:\\Users\\private\\data 保留正文'),el('div',{hidden:true},'HIDDEN_SECRET'),el('input',{type:'password',value:'PASSWORD_SECRET'}),el('textarea',{},'DRAFT_SECRET'),el('div',{'data-page-ai-panel':''},'CHAT_SECRET'),section]);
  const result=context.capture(env),json=JSON.stringify(result);assert.equal(result.tables.length,1);assert.deepEqual(result.tables[0].columns,['证券','20日涨幅']);assert.equal(result.tables[0].rows.length,30);assert.equal(result.tables[0].loadedRows,40);assert.equal(result.tables[0].totalRows,null);assert.match(result.tables[0].pagination,/80/);assert.deepEqual(result.dataDates,['2026-09-29']);
  assert.doesNotMatch(json,/HIDDEN_SECRET|PASSWORD_SECRET|DRAFT_SECRET|CHAT_SECRET|K_CANDLE_PRIVATE|Users|private\\/);assert.match(json,/本地路径已省略/);
});

test('settings pages never inspect settings DOM or call the analytical provider',()=>{
  const env=page('C07',[],null,{analysisTools:{getAIContext(){throw new Error('must not read');}}});env.document.querySelector=()=>{throw new Error('must not inspect configuration');};
  const result=context.capture(env);assert.equal(result.tables.length,0);assert.match(result.summary,/不提供配置/);assert.deepEqual(result.filters,{});
});

test('an active workspace excludes the background main page and its loaded analysis data',()=>{
  let providerCalls=0;const workspace=el('section',{id:'workspace-root'},el('h1',{id:'workspace-title'},'当前研究任务'),el('p',{},'研究区间 2026-08-01 至 2026-09-01'));
  const env=page('T08',[el('p',{},'BACKGROUND_SECRET')],workspace,{analysisTools:{getAIContext(){providerCalls++;return {pageId:'T08',evidence:{secret:'BACKGROUND_SECRET'}};}}});
  const result=context.capture(env);assert.equal(providerCalls,0);assert.match(result.page.scope,/当前研究任务/);assert.match(result.page.title,/当前研究任务/);assert.doesNotMatch(JSON.stringify(result),/BACKGROUND_SECRET/);assert.match(result.summary,/研究区间/);
});

test('account context keeps visible numeric facts while omitting sensitive notes, account names and reason columns',()=>{
  const grid=table(['股票','数量','操作理由'],[['样本股票 sh600001','100','PRIVATE_REASON']]);
  const env=page('A08',[el('div',{class:'account-context'},'PRIVATE_ACCOUNT_NAME'),el('div',{class:'metrics'},'净损益 123.45'),el('div',{id:'review-notes'},'PRIVATE_REVIEW'),el('section',{},el('h2',{},'当前成交'),grid)]);
  const result=context.capture(env);assert.match(result.summary,/123\.45/);assert.deepEqual(result.tables[0].columns,['股票','数量']);assert.doesNotMatch(JSON.stringify(result),/PRIVATE_ACCOUNT_NAME|PRIVATE_REASON|PRIVATE_REVIEW/);
});

test('current structured context sets real totals and source dates without leaking disallowed fields',()=>{
  const grid=table(['证券','涨幅'],[['sh600003 趋势候选','24%']]);
  const env=page('T04',[el('section',{},el('h2',{},'趋势筛选'),grid)],null,{analysisTools:{getAIContext:()=>({pageId:'T04',status:'ready',dataDates:['2026-09-29'],filters:{query:'候选',password:'PRIVATE_PASSWORD'},summary:'已读取当前页',evidence:{pagination:{totalRows:58,loadedRows:1,offset:20,from:21,to:21,hasMore:true},filePath:'C:\\SECRET\\data',apiKey:'PRIVATE_KEY'},limitations:['当前分页不等于全量']})}});
  const result=context.capture(env);assert.equal(result.tables[0].totalRows,58);assert.equal(result.tables[0].from,21);assert.equal(result.tables[0].offset,20);assert.doesNotMatch(JSON.stringify(result),/PRIVATE_KEY|PRIVATE_PASSWORD|SECRET/);assert.deepEqual(result.dataDates,['2026-09-29']);
});

test('wide pages obey hard size limits and retain accurate shown row counts',()=>{
  const sections=Array.from({length:8},(_,section)=>el('section',{},el('h2',{},`表格${section}`),table(Array.from({length:24},(_,i)=>`字段${i}`),Array.from({length:50},()=>Array.from({length:24},()=>`字段内容${'长'.repeat(230)}`)))));
  const result=context.capture(page('M02',sections));assert.equal(result.tables.length,6);assert.ok(result.tables.every(table=>table.columns.length<=16&&table.rows.length<=30&&table.shownRows===table.rows.length));assert.ok(JSON.stringify(result).length<=context.MAX_CHARS);assert.match(result.limitations.join(' '),/大小上限/);
});

test('redaction preserves stock identities and dates but removes credential forms and URLs',()=>{
  const output=context.cleanText('sh600001 2026-09-29 API_KEY=private123 password:abcd Bearer abc123 sk-abcdefghijklmnop https://example.com/private /home/private/data');
  assert.match(output,/sh600001 2026-09-29/);assert.doesNotMatch(output,/private123|abcd|abc123|abcdefghijklmnop|example\.com|home\/private/);
});

test('historical requests reject an inconsistent future snapshot instead of attaching current candidates',async()=>{
  const app=analysisUI(async()=>({asOf:'2026-09-30',leaders:[{symbol:'sh600001',name:'FUTURE_ONLY'}],timeline:[{date:'2026-09-30',metrics:{maxHeight:9}}]}));
  app.input('research','asOf','2026-09-01');await app.render('T08');const result=app.capture();assert.equal(result.status,'date-mismatch');assert.equal(result.evidence.timeline,undefined);assert.doesNotMatch(JSON.stringify(result),/FUTURE_ONLY/);
});

test('T08 history is capped to the most recent sixty loaded dates and exposes both original and shown counts',async()=>{
  const timeline=Array.from({length:90},(_,i)=>({date:new Date(Date.UTC(2026,6,1+i)).toISOString().slice(0,10),metrics:{maxHeight:2,observedCount:20}}));
  const app=analysisUI(async()=>({asOf:timeline.at(-1).date,timeline}));await app.render('T08');const result=app.capture();assert.equal(result.evidence.timeline.loadedDays,90);assert.equal(result.evidence.timeline.shownDays,60);assert.equal(result.evidence.timeline.from,timeline[30].date);assert.equal(result.evidence.timeline.to,timeline.at(-1).date);assert.match(result.evidence.timeline.scope,/不受候选榜单/);
});

test('large T08 context reports the actual attached history after trimming and passes backend validation',async()=>{
  const leaders=Array.from({length:8},(_,index)=>({symbol:`sh${600001+index}`,name:`历史梯队样本${index+1}`,height:3,amount:123456789,changePercent:10,board:'main',status:'promoted'}));
  const bucket={eligible:10,observed:8,missing:2,promoted:3,broken:5,rate:.375};
  const timeline=Array.from({length:250},(_,index)=>({date:new Date(Date.UTC(2026,0,1+index)).toISOString().slice(0,10),metrics:{maxHeight:5,limitUpCount:20,multiBoardCount:10,observedCount:5000,promotion:{all:bucket,firstToSecond:bucket,secondToThird:bucket,high:bucket},trend:{eligible:4500,count:100,amountShare:.31,aboveMA20Ratio:.56,aboveMA60Ratio:.41},premium:{eligible:20,observed:18,missing:2,median:2.4,mean:3.1},leaders}}));
  const originalFrom=timeline.at(-60).date,asOf=timeline.at(-1).date;
  const rotations=timeline.slice(-20).map(row=>({date:row.date,previousDate:row.date,previousMaxHeight:3,maxHeight:4,reason:'leader_change',leaders,previousLeaders:leaders}));
  const app=analysisUI(async()=>({asOf,timeline,leaders,yesterdayLeaders:leaders,leadership:{rotationEvents:rotations}}));await app.render('T08');
  const sections=Array.from({length:6},(_,index)=>el('section',{},el('h2',{},`证据表${index+1}`),table(['股票','截止日','量价观察'],Array.from({length:30},(_,row)=>[`样本${row}`,asOf,'已有量价观察'.repeat(22)]))));
  const result=context.capture(page('T08',sections,null,{analysisTools:app.AT.analysisTools}));
  const attached=result.evidence.timeline;assert.ok(attached.rows.length<60);assert.equal(attached.loadedDays,250);assert.equal(attached.shownDays,attached.rows.length);assert.equal(attached.from,attached.rows[0].date);assert.equal(attached.to,attached.rows.at(-1).date);
  assert.ok(result.dataDates.includes(attached.from));assert.ok(result.dataDates.includes(attached.to));assert.ok(!result.dataDates.includes(originalFrom));assert.doesNotMatch(result.limitations.join(' '),/本次仅附最近 60 日/);assert.match(result.limitations.join(' '),new RegExp(`本次实际附带 ${attached.rows.length} 日`));
  assert.ok(JSON.stringify(result).length<=context.MAX_CHARS);const accepted=sanitizeContext(result);assert.equal(accepted.evidence.timeline.shownDays,attached.rows.length);assert.deepEqual(accepted.dataDates,result.dataDates);assert.ok(accepted.tables.every(table=>table.shownRows===table.rows.length));
});

test('M01 attaches the selected historical profile and excludes future style and live quote DOM',()=>{
  const supplied=context.marketStyle({data:marketStyleFixture(),day:'2026-10-07'});
  let otherProviderCalls=0;
  const env=page('M01',[el('p',{},'实时指数 2026-10-09 FUTURE_QUOTE')],null,{marketUI:{getAIContext:()=>supplied},analysisTools:{getAIContext(){otherProviderCalls++;return null;}}});
  const result=context.capture(env),accepted=sanitizeContext(result),json=JSON.stringify(result);
  assert.equal(otherProviderCalls,0);assert.equal(result.filters.selectedDay,'2026-10-07');assert.equal(result.evidence.source.asOf,'2026-10-07');
  assert.equal(result.evidence.profile.dimensions[0].label,'趋势回调');assert.equal(result.evidence.profile.dimensions[1].evidence[0].value,null);
  assert.equal(result.evidence.timeline.loadedDays,1);assert.equal(result.evidence.timeline.shownDays,1);assert.deepEqual(result.dataDates,['2026-10-07']);
  assert.equal(accepted.evidence.profile.dimensions[0].evidence[0].threshold,'<-1%');assert.equal(accepted.evidence.profile.dimensions[0].durationCapped,true);
  assert.doesNotMatch(json,/FUTURE|2026-10-09|private-source|PRIVATE_KEY/);
});

test('M01 context reports unavailable dates and legacy data without synthesizing profile labels',()=>{
  const data=marketStyleFixture(),missing=context.marketStyle({data,day:'2026-09-01'});
  assert.equal(missing.status,'date-mismatch');assert.equal(missing.evidence.profile,undefined);assert.doesNotMatch(JSON.stringify(missing),/FUTURE_STYLE/);
  delete data.timeline[0].profile;
  const legacy=context.marketStyle({data,day:'2026-10-07'});
  assert.equal(legacy.status,'legacy-profile');assert.equal(legacy.evidence.profile,null);assert.match(legacy.limitations.join(' '),/不能由大类标签推算/);
  const refreshing=context.marketStyle({data,day:'2026-10-08',busy:true});assert.equal(refreshing.status,'refreshing');
  const failed=context.marketStyle({data,day:'2026-10-08',error:'PRIVATE_SOURCE_ERROR'});assert.equal(failed.status,'stale-after-error');assert.doesNotMatch(JSON.stringify(failed),/PRIVATE_SOURCE_ERROR/);
  assert.equal(context.marketStyle().status,'not-loaded');
});

test('M01 long profiles retain honest date range after budget trimming and pass backend validation',()=>{
  const data=marketStyleFixture(),base=data.timeline[0];
  data.timeline=Array.from({length:100},(_,i)=>({...structuredClone(base),date:new Date(Date.UTC(2026,5,1+i)).toISOString().slice(0,10),profile:{...structuredClone(base.profile),dimensions:Array.from({length:6},(_,j)=>({...structuredClone(base.profile.dimensions[0]),id:String(j),label:'状态'.repeat(70)}))}}));
  data.asOf=data.timeline.at(-1).date;data.style=data.timeline.at(-1);
  const result=context.capture(page('M01',[],null,{marketUI:{getAIContext:()=>context.marketStyle({data,day:data.asOf})}}));
  const timeline=result.evidence.timeline;
  assert.ok(JSON.stringify(result).length<=context.MAX_CHARS);assert.equal(timeline.loadedDays,100);assert.equal(timeline.shownDays,timeline.rows.length);assert.ok(timeline.shownDays<60);
  assert.equal(timeline.from,timeline.rows[0].date);assert.ok(result.dataDates.includes(timeline.from));assert.equal(sanitizeContext(result).evidence.timeline.shownDays,timeline.shownDays);
  assert.match(focusFor({id:'M01',title:'市场总览'}),/共同股票队列/);assert.match(focusFor({id:'M01',title:'市场总览'}),/六维可以同时成立/);
});

test('dropping oversized evidence removes obsolete attached-history claims and dates',()=>{
  const result=context.limit({page:{id:'T08',title:'连板龙头'},capturedAt:'2026-10-12',filters:{},summary:'当前页',tables:[],dataDates:['2026-01-01','2026-09-30'],evidence:{timeline:{loadedDays:250,shownDays:60,from:'2026-01-01',to:'2026-09-30',rows:Array.from({length:10},()=>({date:'2026-09-30',detail:'长'.repeat(6000)}))}},limitations:['历史证据已载入 250 个实际交易日，本次仅附最近 60 日；缺失观察保留缺失计数。']});
  assert.equal(result.evidence.timeline,undefined);assert.deepEqual(result.dataDates,[]);assert.doesNotMatch(result.limitations.join(' '),/本次仅附最近 60 日/);assert.match(result.limitations.join(' '),/本次未附带历史序列/);assert.doesNotThrow(()=>sanitizeContext(result));
});

test('account workspace opened over a stock page uses account scope and omits nested private notes',()=>{
  const workspace=el('section',{id:'workspace-root'},el('section',{class:'page-workspace business-modal'},el('h1',{id:'workspace-title'},'编辑交易流水'),el('div',{class:'business-details'},'PRIVATE_WORKSPACE_NOTE'),el('div',{class:'business-context'},'PRIVATE_WORKSPACE_ACCOUNT'),el('h2',{},'成交明细'),table(['股票','数量','备注'],[['sh600001','100','PRIVATE_WORKSPACE_REASON']])));
  const result=context.capture(page('G01',[el('p',{},'BACKGROUND_STOCK')],workspace));assert.equal(result.page.id,'A04');assert.equal(result.page.originRoute,'G01');assert.equal(result.page.title,'编辑交易流水');assert.doesNotMatch(JSON.stringify(result),/PRIVATE_WORKSPACE|BACKGROUND_STOCK/);const accepted=sanitizeContext(result);assert.match(focusFor(accepted.page),/账户、持仓或复盘/);
});

test('page summaries retain their 6500 character budget instead of silently falling back to 1200',()=>{
  const retained=context.capture(page('M02',[el('p',{},'行情说明'.repeat(1000))]));assert.equal(retained.summary.length,4000);assert.equal(sanitizeContext(retained).summary.length,4000);
  const limited=context.capture(page('M02',[el('p',{},'行情说明'.repeat(2000))]));assert.equal(limited.summary.length,6000);assert.match(limited.limitations.join(' '),/摘要只附前6000字/);assert.equal(sanitizeContext(limited).summary.length,6000);
});
