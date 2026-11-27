const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const escape = value => String(value ?? '').replace(/[&<>"']/g, value => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[value]));
function runtime() {
  const listeners = new Map();
  const document = {documentElement:{classList:{remove(){}}},querySelector(){return null;},addEventListener(name, listener){const list=listeners.get(name)||[];list.push(listener);listeners.set(name,list);}};
  const AT = {escape,state:{page:'M01'},render(){}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/market-analysis-ui.js'),'utf8'),{AT,document,window:{addEventListener(){}},performance:{now:()=>0},requestAnimationFrame:fn=>fn()});
  return {render:data=>AT.marketAnalysis.overview({data,busy:false,historyDays:250}),selected:data=>AT.marketAnalysis.selectedObservation(data),select(day){const target={dataset:{marketStyleDay:day},closest:selector=>selector==='[data-market-style-day]'?target:null};for(const listener of listeners.get('click')||[])listener({target});}};
}
function profile(prefix = '') {
  return {version:1,status:'observed',headline:`${prefix}趋势修复 · 接力分歧`,summary:`${prefix}趋势广度改善，接力反馈尚未同步。`,dimensions:[
    ['trend','趋势结构','趋势修复','positive'],['breadth','市场广度','普涨扩散','positive'],['activity','成交活跃度','温和放量','neutral'],['relay','连板接力','接力分歧','warning'],['preference','交易偏好','趋势占优','neutral'],['risk','下行风险','风险平稳','neutral']
  ].map(([id,title,label,tone])=>({id,title,key:id,label:prefix+label,tone,status:'observed',reason:`${prefix}${title}的当日判定依据`,sampleCount:120,evidence:[{label:'MA20之上占比',value:.63,unit:'ratio',threshold:'≥55%时视为趋势扩散'}],streakDays:3,durationCapped:false})),changes:[{id:'trend',title:'趋势结构',from:`${prefix}震荡`,to:`${prefix}趋势修复`}]};
}
function dataFor(profiles = [profile()]) {
  const timeline = profiles.map((item,index)=>({date:`2026-01-0${index+1}`,key:'rotation',label:'轮动观察',reason:'已确认的大类',profile:item,metrics:{sampleCount:120,expectedCount:150,coverage:.8,breadth:.63}}));
  return {asOf:timeline.at(-1).date,timeline,style:timeline.at(-1),warnings:[]};
}

test('six market dimensions explain the selected day with separate evidence, duration and rule details',()=>{
  const html=runtime().render(dataFor());
  assert.match(html,/<h3[^>]*>趋势修复 · 接力分歧<\/h3>/);
  assert.equal((html.match(/data-style-dimension=/g)||[]).length,6);
  assert.match(html,/趋势广度改善，接力反馈尚未同步/);
  assert.match(html,/data-profile-date="2026-01-01"/);
  assert.match(html,/连续 3 日/);
  assert.match(html,/>63\.00%<\/dd>/);
  assert.match(html,/<summary>查看判定规则<\/summary>/);
  assert.match(html,/≥55%时视为趋势扩散/);
  assert.match(html,/时间轴大类/);
  assert.match(html,/轮动观察/);
  assert.match(html,/趋势结构<\/span><b>震荡<\/b>/);
  assert.match(html,/title="2026-01-01 · 趋势修复 · 接力分歧 · 时间轴大类 轮动观察/);
});

test('choosing a historical day changes all dimensions and transitions without borrowing the latest profile',()=>{
  const app=runtime(),data=dataFor([profile('过去：'),profile('最新：')]);
  let html=app.render(data);assert.match(html,/data-profile-date="2026-01-02"/);
  app.select('2026-01-01');html=app.render(data);
  assert.equal(app.selected(data).day,'2026-01-01');assert.equal(app.selected(data).current,data.timeline[0],'AI and UI share the exact selected observation');
  const panel=html.slice(html.indexOf('<div class="market-analysis-lead">'),html.indexOf('<div data-market-style-explorer>'));
  assert.match(panel,/data-profile-date="2026-01-01"/);
  assert.match(panel,/过去：趋势修复/);assert.match(panel,/过去：震荡/);
  assert.doesNotMatch(panel,/最新：/);
  assert.match(html,/data-market-style-chart/);
  const changedSource=dataFor([profile('其他来源：')]);changedSource.asOf='2026-02-03';changedSource.timeline[0].date='2026-02-03';
  assert.equal(app.selected(changedSource).day,'2026-02-03','a missing selected date falls back to the new source date');
});

test('market dimension units distinguish ratios, returns, percentage points and genuinely missing measurements',()=>{
  const item=profile();item.dimensions[0].evidence=[
    {label:'ratio',value:.125,unit:'ratio'},{label:'percent',value:2.4,unit:'percent'},
    {label:'points',value:3.2,unit:'points'},{label:'count',value:0,unit:'count'},
    {label:'multiple',value:1.3,unit:'multiple'},{label:'missing',value:null,unit:'ratio'},
    {label:'absent',unit:'percent'}
  ];
  const html=runtime().render(dataFor([item]));
  for(const [label,value] of [['ratio','12.50%'],['percent','2.40%'],['points','3.20 个百分点'],['count','0'],['multiple','1.30 倍'],['missing','—'],['absent','—']])assert.ok(html.includes(`<dt>${label}</dt><dd title="${label}：`)&&html.includes(`>${value}</dd>`),label);
});

test('profile threshold evidence keeps two display decimals and exposes the detailed underlying reading',()=>{
  const item=profile();item.dimensions[0].evidence=[
    {label:'占比边界',value:.0199,unit:'ratio',threshold:'至少2%'},
    {label:'涨幅边界',value:.27,unit:'percent',threshold:'大于0.3%'},
    {label:'精细变化',value:1.234567890123,unit:'points'},
    {label:'缺失',value:null,unit:'percent'}
  ];
  const html=runtime().render(dataFor([item]));
  assert.match(html,/<dd title="占比边界：1\.99%" aria-label="占比边界：1\.99%">1\.99%<\/dd>/);
  assert.match(html,/<dd title="涨幅边界：0\.27%" aria-label="涨幅边界：0\.27%">0\.27%<\/dd>/);
  assert.match(html,/<dd title="精细变化：1\.234567890123 个百分点" aria-label="精细变化：1\.234567890123 个百分点">1\.23 个百分点<\/dd>/);
  assert.match(html,/<dd title="缺失：—" aria-label="缺失：—">—<\/dd>/);
  assert.match(html,/判定按原始值/);
});

test('old snapshots ask for recalculation and never fabricate detailed classifications',()=>{
  const app=runtime(),data=dataFor([undefined,profile('最新：')]);app.render(data);app.select('2026-01-01');
  const html=app.render(data);
  assert.match(html,/这份快照尚未包含细分风格/);
  assert.doesNotMatch(html,/data-style-dimension=/);
  assert.match(html,/>轮动观察<\/h3>/);
  assert.match(html,/data-market-style-chart/);
});

test('insufficient samples remain explicit and capped durations never claim an exact full-history length',()=>{
  const item=profile();item.status='insufficient';item.headline='样本不足';item.changes=[];
  item.dimensions[0]={...item.dimensions[0],status:'insufficient',tone:'unknown',label:'暂不判断',sampleCount:4,streakDays:0};
  item.dimensions[1].streakDays=20;item.dimensions[1].durationCapped=true;
  const html=runtime().render(dataFor([item]));
  assert.match(html,/data-profile-status="insufficient"/);
  assert.match(html,/样本不足 · 4/);
  assert.match(html,/样本不足，暂不判断风格变化/);
  assert.match(html,/至少 20 日/);
  assert.doesNotMatch(html,/连续 0 日/);
});

test('all profile text is escaped and unexpected tones cannot inject attributes or styles',()=>{
  const item=profile();item.headline='<img src=x>';item.summary='<script>summary</script>';
  item.dimensions[0]={...item.dimensions[0],id:'x" onclick="bad',title:'<b>title</b>',label:'<svg>label</svg>',reason:'<script>reason</script>',tone:'positive" onclick="bad',evidence:[{label:'<img>evidence',value:1,unit:'count',threshold:'<script>rule</script>'}]};
  item.changes=[{title:'<em>change</em>',from:'<iframe>before</iframe>',to:'<img>after'}];
  const html=runtime().render(dataFor([item]));
  for(const token of ['&lt;img','&lt;script&gt;summary','&lt;b&gt;title','&lt;svg&gt;label','&lt;script&gt;reason','&lt;script&gt;rule','&lt;em&gt;change','&lt;iframe&gt;before'])assert.ok(html.includes(token),token);
  assert.match(html,/market-profile-card market-profile-unknown/);
  assert.doesNotMatch(html,/<img|<script>|<iframe| onclick="bad/);
});

test('dynamic style radar renders offense thermometer, dual axes, shift alerts, strategy guide and escapes text',()=>{
  const data = dataFor();
  data.timeline[0].dynamic = {
    version: 'market-style-dynamic-v2',
    status: 'observed',
    regimeKey: 'main_10cm_relay',
    regimeLabel: '主板10cm题材抱团',
    regimeTone: 'amber',
    regimeColor: '#d97706',
    headline: '资金聚焦主板 10cm 题材前排',
    summary: '进攻温度 58.2/100',
    confidencePct: 82,
    streakDays: 2,
    offenseScore: 58.2,
    offenseEma: 56.5,
    offenseVelocity: 6.4,
    offensePercentile30: 71,
    momentumLabel: '温和改善',
    boardAxis: -34.5,
    boardAxisDelta5: -22.0,
    boardLabel: '10cm主板主导',
    boardMetrics: {growth20MedianRet5: -2.5, growth20Breadth: 0.34, main10MedianRet5: 2.1, main10Breadth: 0.58, growth20AmountShare: 0.31},
    structureAxis: 28.0,
    structureLabel: '短线连板接力主导',
    structureMetrics: {coreExcess5: -1.2, coreExcess20: -2.0, maxBoardHeight: 6, multiBoardApprox: 22, promotionRate: 0.48},
    liquidity: {label: '温和放量活跃', totalAmountYi: 19200, totalAmountRatio20: 1.16},
    shiftAlerts: [{id: 'track_shift_to_10cm', severity: 'warning', title: '赛道切换预警<script>x</script>', detail: '资金切向主板10cm', actionHint: '切至主板轨'}],
    strategyGuide: {
      matchedStrategies: [{id: 'wujiang_mainline_v1', name: '乌江双轨主线核心', fit: '首选', note: '主板封板与首阴反包'}],
      positionPlan: {totalRange: '45%–65%', slots: '2–3 只', maxSingle: '15%–20%', riskHint: '严控旧科技抄底'},
    },
  };
  data.style = data.timeline[0];
  const html = runtime().render(data);
  assert.match(html, /data-market-dynamic-style/);
  assert.match(html, /主板10cm题材抱团/);
  assert.match(html, /58\.20|58\.2/);
  assert.match(html, /10cm主板主导/);
  assert.match(html, /短线连板接力主导/);
  assert.match(html, /乌江双轨主线核心/);
  assert.match(html, /赛道切换预警&lt;script&gt;x&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>x<\/script>/);
});
