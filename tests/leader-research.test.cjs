const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {promotionRate, trendRatio, rowsFor, pathFor} = require('../preview/leader-research.js');

const escape = value => String(value ?? '').replace(/[&<>"']/g, value => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[value]));
const bucket = (observed=4,promoted=2) => ({eligible:observed+1,observed,promoted,broken:observed-promoted,missing:1,rate:observed?promoted/observed:null});
const metrics = () => ({observedCount:50,maxHeight:5,multiBoardCount:8,limitUpCount:17,promotion:{all:bucket(),firstToSecond:bucket(12,4),secondToThird:bucket(3,1),high:bucket(1,1)},highBoardPromotion:bucket(1,1),premium:{eligible:20,observed:19,missing:1,mean:2,median:1.5},trend:{eligible:40,count:14,amountShare:.44,aboveMA20Ratio:.6,aboveMA60Ratio:.5},highElasticity:{count:2,amountShare:.1},risk:{limitDownCount:1,declineRatio:.3},brokenCount:2,leaderBreakCount:1,leaders:[{symbol:'sz000001',name:'高度候选',height:5}]});
function fixture(length=100) {
  const rows = Array.from({length},(_,i)=>({date:new Date(Date.UTC(2026,0,i+1)).toISOString().slice(0,10),metrics:metrics()}));
  const item = {symbol:'sz000001',name:'高度候选',boardHeight:5,previousHeight:4,sequenceComplete:false,boardStartDate:'2026-01-01',boardRunReturn:61,changePercent:10,amount:1e9};
  return {asOf:rows.at(-1).date,timeline:rows,leadership:{metrics:metrics(),rotationEvents:[]},leaders:[item],groups:[{height:5,items:[item]},{height:1,items:[{...item,name:'首板',boardHeight:1}]}],yesterdayLeaders:[{...item,previousHeight:4,currentHeight:5,status:'promoted'},{symbol:'sz000002',name:'待核实',previousHeight:3,currentHeight:null,status:'missing',missingReason:'当日无成交',changePercent:null}]};
}
function runtime(file='leader-research.js',page='T08') {
  const listeners = new Map(), windowListeners=new Map(), classes=new Set();
  let stopped=false, released=0;
  const root={classList:{add:value=>classes.add(value),remove:value=>classes.delete(value)},setPointerCapture(){},hasPointerCapture(){return true;},releasePointerCapture(){released++;}};
  const host={innerHTML:'',querySelector:()=>null}, reading={innerHTML:''}, line={setAttribute(){}};
  const chart={dataset:{},matches:selector=>selector==='[data-leader-chart]',closest:selector=>selector==='[data-leader-chart]'?chart:null,getBoundingClientRect:()=>({left:0,top:0,width:1000,height:375}),focus(){document.activeElement=chart;}};
  const document={documentElement:root,body:{},activeElement:null,hidden:false,querySelector:selector=>({'[data-leader-history]':host,'[data-leader-reading]':reading,'[data-leader-crosshair]':line,'[data-leader-chart]':chart}[selector]||null),addEventListener(name,fn,options){const list=listeners.get(name)||[];list.push({fn,capture:options===true});listeners.set(name,list);}};
  const AT={escape,state:{page},renderCount:0,render(){this.renderCount++;}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview',file),'utf8'),{AT,document,window:{addEventListener:(name,fn)=>windowListeners.set(name,fn)},setTimeout,Date,performance:{now:()=>Date.now()},requestAnimationFrame:fn=>fn()});
  function emit(name,event={}) {
    stopped=false;
    const defaults={target:chart,preventDefault(){this.prevented=true;},stopPropagation(){},stopImmediatePropagation(){stopped=true;}};
    const result=Object.assign(defaults,event);
    for(const {fn} of [...listeners.get(name)||[]].sort((a,b)=>b.capture-a.capture)){fn(result);if(stopped)break;}
    return result;
  }
  return {AT,api:AT.leaderResearch,document,chart,host,reading,emit,classes,get released(){return released;},windowListeners,get stopped(){return stopped;}};
}

test('promotion rates use the observable cohort and never treat zero denominator or missing as zero percent',()=>{
  assert.equal(promotionRate({eligible:5,observed:3,promoted:2,missing:2}),2/3);
  assert.equal(promotionRate({eligible:5,observed:0,promoted:0,missing:5,rate:0}),null);
  assert.equal(promotionRate({observed:3,promoted:null}),null);
  assert.equal(promotionRate({observed:3,promoted:4}),null);
  assert.equal(promotionRate({observed:4,promoted:0}),0);
  assert.equal(trendRatio({eligible:0,amountShare:0},'amountShare'),null);
  assert.equal(trendRatio({eligible:40,amountShare:null},'amountShare'),null);
  assert.equal(trendRatio({eligible:40,amountShare:0},'amountShare'),0);
});

test('the shared date model leaves unavailable rate and no-observation dates as gaps',()=>{
  const rows=rowsFor([{date:'2026-01-01',metrics:metrics()},{date:'2026-01-02',metrics:{...metrics(),observedCount:0,promotion:{all:bucket(0,0)},trend:{eligible:0,amountShare:0}}},{date:'2026-01-03',metrics:metrics()}]);
  assert.equal(rows[1].promotion,null);assert.equal(rows[1].trendShare,null);assert.equal(rows[1].maxHeight,null);
  assert.equal(pathFor(rows,'promotion',i=>i*10,value=>100-value*100),'M0.00,50.00 M20.00,50.00');
  assert.equal(rows[2].date,'2026-01-03');
});

test('leadership UI preserves boardHeight, dates, incomplete candidates, and missing predecessor results',()=>{
  const rt=runtime(),data=fixture(10);
  data.leadership.rotationEvents=[{date:'2026-01-02',previousLeaders:[{name:'旧候选'}],leaders:[{name:'新候选'}],previousMaxHeight:5,maxHeight:3,reason:'height_reset',incomplete:true,coverageNote:'昨日高标缺少可核验记录'}];
  const html=rt.api.panel({data,filters:{historyDays:'250',minHeight:'2',excludeST:true,board:'all'}});
  assert.match(html,/leader-research-height">≥5/);
  assert.match(html,/data-stock="sz000001" data-analysis-stock-day="2026-01-10"/);
  assert.match(html,/data-leader-result="missing"/);assert.match(html,/当日无成交/);
  assert.match(html,/观测不完整/);assert.match(html,/昨日高标缺少可核验记录/);
  assert.match(html,/观测起点/);assert.doesNotMatch(html,/<strong>首板<\/strong>/);
  assert.match(html,/全市场，含当前名称 ST/);assert.match(html,/连续 60 日有成交样本的总成交额/);
  assert.match(html,/data-analysis-chip="board" data-analysis-value="chinext"/);
  assert.equal((html.match(/data-leader-line=/g)||[]).length,4);
  assert.match(html,/首板 → 2 板/);assert.match(html,/2 板 → 3 板/);
});

test('untrusted candidate and missing-reason text is escaped in all new panels',()=>{
  const rt=runtime(),data=fixture(2);
  data.leaders[0].name='<img src=x onerror=alert(1)>';
  data.yesterdayLeaders[1].missingReason='"><script>bad</script>';
  const html=rt.api.panel({data,filters:{historyDays:'60',minHeight:'2'}});
  assert.match(html,/&lt;img/);assert.match(html,/&lt;script&gt;bad/);assert.doesNotMatch(html,/<img|<script>/);
});

test('wheel zoom and both mouse buttons pan the timeline while stable capture survives chart replacement',()=>{
  for(const button of [0,2]){
    const rt=runtime();rt.api.panel({data:fixture(),filters:{historyDays:'250'}});
    rt.emit('wheel',{deltaY:-100,clientX:520,ctrlKey:false,metaKey:false});
    assert.match(rt.host.innerHTML,/data-leader-count="80"/);
    rt.emit('pointerdown',{button,pointerId:1,pointerType:'mouse',clientX:520,clientY:100});
    assert.equal(rt.api.deferRender(),true);
    rt.emit('pointermove',{target:rt.document.documentElement,pointerId:1,buttons:button===0?1:2,clientX:650,clientY:100});
    assert.match(rt.host.innerHTML,/data-leader-start="0"/);assert.ok(rt.classes.has('leader-research-dragging'));
    rt.emit('pointerup',{pointerId:1});assert.ok(!rt.classes.has('leader-research-dragging'));assert.equal(rt.released,1);
    if(button===2)assert.equal(rt.emit('contextmenu',{target:rt.document.documentElement}).prevented,true,'right-drag release must not open a document context menu');
    rt.emit('click',{target:rt.document.documentElement});assert.equal(rt.stopped,true,'a completed drag must not navigate to a day');
    assert.equal(rt.api.deferRender(),false);
  }
});

test('browser zoom is retained and source invalidation releases a pending drag',()=>{
  const rt=runtime();rt.api.panel({data:fixture(),filters:{historyDays:'250'}});
  const event=rt.emit('wheel',{deltaY:-100,clientX:520,ctrlKey:true});assert.equal(event.prevented,undefined);assert.equal(rt.host.innerHTML,'');
  rt.emit('pointerdown',{button:2,pointerId:2,pointerType:'mouse',clientX:520,clientY:100});
  rt.api.invalidate();assert.equal(rt.released,1);assert.equal(rt.api.deferRender(),false);
  const html=rt.api.panel({busy:false,filters:{}});assert.doesNotMatch(html,/高度候选/);assert.match(html,/暂无历史证据/);
});

test('market overview uses the same-day shared evidence and keeps no-observation rates empty',()=>{
  const rt=runtime('market-analysis-ui.js','M01'),latest=metrics(),past=metrics();
  past.promotion.all={eligible:3,observed:0,promoted:0,missing:3};past.trend={eligible:0,amountShare:0};past.leaders=[];
  const timeline=[{date:'2026-01-01',key:'boards',label:'连板活跃',metrics:{leadership:past}},{date:'2026-01-02',key:'trend',label:'趋势扩散',metrics:{leadership:latest}}];
  const data={asOf:'2026-01-02',timeline,style:timeline[1]};
  let html=rt.AT.marketAnalysis.overview({data,busy:false,historyDays:250});assert.match(html,/data-market-leader-evidence/);assert.match(html,/data-leader-day="2026-01-02"/);assert.match(html,/高度候选 · 5 板/);
  const target={dataset:{marketStyleDay:'2026-01-01'},closest:selector=>selector==='[data-market-style-day]'?target:null};
  rt.emit('click',{target});
  html=rt.AT.marketAnalysis.overview({data,busy:false,historyDays:250});assert.match(html,/data-leader-day="2026-01-01"/);assert.doesNotMatch(html,/高度候选 · 5 板/);
  assert.match(html,/<strong>—<small> 昨日连板晋级率/);assert.match(html,/<strong>—<small> 候选成交占比/);
  assert.match(html,/data-market-style-chart/,'existing timeline remains present');
});
