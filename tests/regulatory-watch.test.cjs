const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {statusOf,safeUrl,records,countsFor,deadline,selectRows,cycleSort}=require('../preview/regulatory-watch.js');
const escape=value=>String(value??'').replace(/[&<>"']/g,value=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[value]));
const record=(extra={})=>({id:'a',symbol:'sz000001',code:'000001',name:'测试证券',market:'sz',category:'focus',securityType:'stock',listed:true,status:'active',startDate:'2026-10-01',endDate:'2026-10-20',endDateKind:'expected',remainingSessions:7,remainingCalendarDays:9,sourceName:'名单来源',sourceUrl:'https://example.org/list',...extra});
function runtime() {
  const listeners=new Map(),opened=[],host={outerHTML:''};
  const document={activeElement:null,querySelector:selector=>selector==='[data-regulatory-panel]'?host:null,addEventListener:(name,listener)=>listeners.set(name,listener)};
  const AT={escape,state:{page:'T06'}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/regulatory-watch.js'),'utf8'),{AT,document,window:{atradeDesktop:{openExternal:async url=>{opened.push(url);return {ok:true};}}},URL,Date,Promise});
  const click=dataset=>{const target={dataset,disabled:false,hasAttribute:key=>key==='data-regulatory-sort-reset'&&'regulatorySortReset'in dataset};listeners.get('click')({target:{closest:()=>target},preventDefault(){}});};
  const input=value=>listeners.get('input')({target:{value,matches:selector=>selector==='[data-regulatory-search]'}});
  return {api:AT.regulatoryWatch,AT,host,opened,click,input};
}
const ids=html=>[...html.matchAll(/data-regulatory-row="([^"]+)"/g)].map(match=>match[1]);

test('deadline distinguishes today from zero future trading sessions and never invents an expiry',()=>{
  assert.equal(deadline(record({endDate:'2026-10-11',remainingSessions:0}),'2026-10-11').primary,'今日预计截止');
  assert.equal(deadline(record({endDate:'2026-10-12',remainingSessions:0}),'2026-10-11').primary,'0 个交易日');
  assert.equal(deadline(record({endDateKind:'disclosed',endDate:'2026-10-11',remainingSessions:0}),'2026-10-11').primary,'今日来源截止');
  assert.match(deadline(record({remainingSessions:null,remainingCalendarDays:3}),'2026-10-11').primary,/3 个自然日/);
  assert.match(deadline(record({remainingSessions:null,remainingCalendarDays:3}),'2026-10-11').secondary,/不能当作交易日/);
  for(const date of [null,'2026-99-99','2026-02-30',''])assert.equal(deadline(record({endDate:date}),'2026-10-11').primary,'期限待核验');
});

test('elapsed and removed source records never become a confirmed regulatory release',()=>{
  const elapsed=deadline(record({status:'elapsed',endDate:'2026-10-01'}),'2026-10-11');
  assert.equal(elapsed.primary,'预计期限已届');assert.match(elapsed.secondary,/待.*核验/);
  assert.equal(deadline(record({status:'elapsed',endDateKind:'disclosed'}),'2026-10-11').primary,'来源期限已届');
  const removed=record({listed:false,status:'active',remainingSessions:0});
  assert.equal(statusOf(removed),'unlisted');assert.match(deadline(removed,'2026-10-11').secondary,/不代表监管已解除/);
  assert.equal(deadline(record({status:'unknown',remainingSessions:0}),'2026-10-11').primary,'期限待核验');
});

test('summary counts records separately from unique securities and retains both regulatory categories',()=>{
  const items=records([record({id:'focus',status:'expiring'}),record({id:'severe',category:'severe'}),record({id:'upcoming',symbol:'sh600002',status:'upcoming'}),record({id:'expired',symbol:'sh600003',status:'elapsed'}),record({category:'ordinary'}),record({category:'delisting'})]);
  assert.equal(items.length,4);assert.deepEqual(countsFor(items),{active:2,expiring:1,upcoming:1,verify:1,total:4,stocks:3});
  assert.equal(selectRows(items,{category:'severe'}).length,1);
  assert.deepEqual(selectRows(items,{status:'active'}).map(item=>item.id),['focus','severe']);
  assert.deepEqual(selectRows(items,{status:'verify'}).map(item=>item.id),['expired']);
});

test('sort cycles ascending, descending, default with nulls last and deadline priority restored',()=>{
  const items=[record({id:'missing',status:'unknown',remainingSessions:null,endDate:null}),record({id:'far',remainingSessions:8}),record({id:'due',status:'expiring',remainingSessions:2}),record({id:'expired',status:'elapsed',remainingSessions:null})];
  let settings=cycleSort({},'remainingSessions');assert.equal(settings.order,'asc');assert.deepEqual(selectRows(items,settings).map(item=>item.id),['due','far','missing','expired']);
  settings=cycleSort(settings,'remainingSessions');assert.equal(settings.order,'desc');assert.deepEqual(selectRows(items,settings).map(item=>item.id),['far','due','missing','expired']);
  settings=cycleSort(settings,'remainingSessions');assert.equal(settings.sort,null);assert.deepEqual(selectRows(items,settings).map(item=>item.id),['due','far','missing','expired']);
  assert.equal(cycleSort({sort:'endDate',order:'desc'},'name').order,'asc');
});

test('panel preserves stale warnings, source distinctions, funds and verified expiry labels',()=>{
  const rt=runtime();
  const html=rt.api.panel({data:{status:'ready',asOf:'2026-10-11',stale:true,receivedAt:'2026-10-10T08:00:00Z',sourceName:'两个公开来源',sourceUrl:'https://example.org/list',sources:[{sourceName:'来源甲',status:'ready',sourceUrl:'https://example.org/source-a'},{sourceName:'来源乙',status:'unavailable',sourceUrl:'https://example.org/source-b'}],items:[record({id:'fund',securityType:'fund',name:'基金例子',status:'expiring',title:'交易所重点监控提示函',stale:true,receivedAt:'2026-10-09T08:00:00Z'}),record({id:'severe',category:'severe',status:'elapsed',endDateKind:'disclosed',periodChanged:true,previousEndDate:'2026-10-02',title:'股票交易严重异常波动公告'})],warnings:['期限需核验']}});
  assert.match(html,/当前显示旧缓存/);assert.match(html,/regulatory-watch-security-type">基金/);
  assert.match(html,/严重异动记录不等于已进入重点监控/);assert.match(html,/来源披露截止，非解除确认/);assert.match(html,/预计截止，非解除确认/);
  assert.match(html,/期限已调整 · 原 2026-10-02/);assert.match(html,/2 条来源记录 · 1 只证券/);assert.match(html,/来源乙 · 暂不可用/);
  assert.match(html,/交易所重点监控提示函/);assert.match(html,/股票交易严重异常波动公告/);assert.match(html,/该来源旧缓存/);
  assert.equal((html.match(/该来源旧缓存/g)||[]).length,1,'only the stale row is labelled stale');
  assert.match(html,/data-regulatory-url="https:\/\/example.org\/source-b"/,'an unavailable source can still be opened for verification');
  assert.match(html,/data-analysis-action="watch-add"/);
});

test('search, category, market and sort operate locally without opening or fetching a source',()=>{
  const rt=runtime(),data={status:'ready',asOf:'2026-10-11',items:[record({id:'a',name:'科技一',remainingSessions:8}),record({id:'b',symbol:'sh600002',code:'600002',market:'sh',name:'科技二',category:'severe',remainingSessions:2}),record({id:'c',symbol:'sz000003',name:'消费'})]};
  rt.api.panel({data});rt.input('科技');assert.deepEqual(ids(rt.host.outerHTML),['a','b']);
  rt.click({regulatorySort:'remainingSessions'});assert.deepEqual(ids(rt.host.outerHTML),['b','a']);
  rt.click({regulatoryCategory:'focus'});assert.deepEqual(ids(rt.host.outerHTML),['a']);
  rt.click({regulatoryCategory:'all'});rt.click({regulatoryMarket:'sh'});assert.deepEqual(ids(rt.host.outerHTML),['b']);
  assert.equal(rt.opened.length,0);
});

test('external links require an explicit click and unsafe schemes and remote text cannot execute',async()=>{
  const rt=runtime();
  const html=rt.api.panel({data:{status:'ready',asOf:'2026-10-11',items:[record({name:'<img src=x onerror=alert(1)>',sourceUrl:'javascript:alert(1)',noticeUrl:'https://example.org/notice?a=1&b=2',sourceName:'<script>x</script>'})]}});
  assert.doesNotMatch(html,/<img|<script>|data-regulatory-url="javascript:/);assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;/);assert.equal(rt.opened.length,0);
  assert.equal(safeUrl('https://user:pass@example.org/secret'),'');assert.equal(safeUrl('file:///secret'),'');
  rt.click({regulatoryUrl:'https://example.org/notice?a=1&b=2'});await new Promise(setImmediate);assert.deepEqual(rt.opened,['https://example.org/notice?a=1&b=2']);
});

test('empty or failed coverage does not claim no regulated securities and invalidation drops previous data',()=>{
  const rt=runtime();let html=rt.api.panel({data:{status:'ready',asOf:'2026-10-11',items:[]}});assert.match(html,/本次来源未收录/);assert.match(html,/不代表市场中没有被监管的证券/);
  html=rt.api.panel({data:{status:'unavailable',items:[],errors:[{message:'来源读取失败'}]}});assert.match(html,/暂未取得可用名单/);assert.match(html,/来源读取失败/);assert.match(html,/<strong>—<small> 条/);
  rt.api.panel({data:{status:'ready',items:[record({name:'旧来源记录'})]}});rt.api.invalidate();html=rt.api.panel({});assert.doesNotMatch(html,/旧来源记录/);
});
