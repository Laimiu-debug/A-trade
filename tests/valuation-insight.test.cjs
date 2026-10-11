const {test}=require('node:test');
const assert=require('node:assert/strict');
const {compute,historicalPosition,panel}=require('../preview/valuation-insight.js');

const snapshot={symbol:'sh600900',ttmNetProfit:4e8,status:'ready',reportDate:'2026-06-30',announcementDate:'2026-08-28'};
const history={metric:'provider_industry_pe_ttm',currentPE:30,items:[{date:'2026-10-05',peTTM:10},{date:'2026-10-06',peTTM:20},{date:'2026-10-07',peTTM:30},{date:'2026-10-08',peTTM:30},{date:'2026-10-09',peTTM:30}],startDate:'2026-10-05',endDate:'2026-10-09',status:'ready'};
const benchmark={symbol:'sh600900',industry:'电力',asOf:'2026-10-09',method:'same_day_positive_ttm_pe',subjectPE:20,medianPE:25,p25PE:22,p75PE:35,sampleCount:30,status:'ready',historical:history};
const context={indexPoints:4090,baselinePoints:3000,coefficient:4090/3000,floorCoefficient:.5,asOf:'2026-10-09',status:'ready',sourceName:'指数来源',sourceUrl:'https://example.com/index'};

test('automatic valuation compares same-source PE against peers without multiplying scenario or index values',()=>{
  const result=compute({...snapshot,marketCap:1e12,peTTM:2500},benchmark);
  assert.equal(result.available,true);assert.equal(result.position,'below');assert.equal(result.label,'低于多数同行');
  assert.equal(result.subjectPE,20);assert.ok(Math.abs(result.relativePercent+20)<1e-10);
  assert.match(panel({snapshot,benchmark,context}),/折价 20\.00%/);
  assert.match(panel({snapshot,benchmark,context}),/当前同行 PE 已包含市场环境，不再叠乘大盘水位/);
  assert.equal(compute(snapshot,{...benchmark,subjectPE:22}).position,'within');
  assert.equal(compute(snapshot,{...benchmark,subjectPE:35}).position,'within');
  assert.equal(compute(snapshot,{...benchmark,subjectPE:40}).position,'above');
});

test('missing, nonpositive and malformed comparison evidence does not manufacture a conclusion',()=>{
  for(const subjectPE of [null,undefined,'',0,-1,Infinity,NaN,false])assert.equal(compute(snapshot,{...benchmark,subjectPE}).available,false);
  for(const sampleCount of [null,0,2,2.9,'',Infinity])assert.equal(compute(snapshot,{...benchmark,sampleCount}).available,false);
  for(const change of [{medianPE:null},{p25PE:null},{p75PE:20},{p25PE:26},{method:'static_pe'},{asOf:null},{status:'unavailable'},{symbol:'sz000001'}])assert.equal(compute(snapshot,{...benchmark,...change}).available,false);
  assert.equal(compute(null,benchmark).available,true);
  assert.equal(compute(snapshot,null).available,false);
});

test('new loss-making financial statements block interpretation of stale positive PE',()=>{
  for(const profit of [0,-1,-4e8]){
    const result=compute({...snapshot,ttmNetProfit:profit,announcementDate:'2026-10-10'},benchmark);
    assert.equal(result.available,false);assert.equal(result.label,'正 PE 模型不适用');assert.match(result.reason,/不同财务时点/);
  }
  const result=compute({...snapshot,ttmNetProfit:null},benchmark);
  assert.equal(result.available,true);assert.match(result.warnings.join(''),/财报 TTM 尚未齐备/);
  const staleLoss=compute({...snapshot,ttmNetProfit:-4e8,stale:true},benchmark);
  assert.equal(staleLoss.available,true);assert.equal(staleLoss.stale,false);assert.match(staleLoss.warnings.join(''),/旧财报 TTM/);
  const failedRefresh=panel({snapshot:{...snapshot,ttmNetProfit:-4e8},benchmark,error:{snapshot:'财报刷新失败'}});
  assert.match(failedRefresh,/低于多数同行/);assert.doesNotMatch(failedRefresh,/原日期观察/);
});

test('peer identity, date and value must agree when underlying rows are provided',()=>{
  const items=[{symbol:'sh600900',asOf:'2026-10-09',peTTM:20},{symbol:'sh600901',asOf:'2026-10-09',peTTM:30},{symbol:'sh600902',asOf:'2026-10-09',peTTM:40}];
  assert.equal(compute(snapshot,{...benchmark,items,sampleCount:3}).available,true);
  for(const item of [{symbol:'sz000001',asOf:'2026-10-09',peTTM:20},{symbol:'sh600900',asOf:'2026-10-08',peTTM:20},{symbol:'sh600900',asOf:'2026-10-09',peTTM:21}])assert.equal(compute(snapshot,{...benchmark,items:[item,...items.slice(1)],sampleCount:3}).available,false);
  assert.equal(compute(snapshot,{...benchmark,items}).available,false);
  assert.equal(compute(snapshot,{...benchmark,items:[...items,items[0]],sampleCount:4}).available,false);
});

test('industry historical rank compares the industry to itself and handles ties and gaps',()=>{
  const result=historicalPosition(history);
  assert.equal(result.available,true);assert.equal(result.percentile,70);assert.equal(result.sampleCount,5);
  assert.equal(compute(snapshot,{...benchmark,subjectPE:999}).historical.percentile,70);
  assert.equal(historicalPosition({...history,items:[...history.items,{date:'2026-10-04',peTTM:0},{date:'2026-10-03',peTTM:null}]}).sampleCount,5);
  for(const change of [{metric:'stock_pe'},{currentPE:0},{currentPE:31},{endDate:'2026-10-08'},{items:history.items.slice(0,2)},{items:[...history.items,history.items[0]]}])assert.equal(historicalPosition({...history,...change}).available,false);
  assert.equal(compute(snapshot,{...benchmark,historical:null}).available,true);
});

test('source failures and stale data are prominently labeled while index failures do not suppress peer comparison',()=>{
  const failed=panel({snapshot,benchmark,context,error:{benchmark:'读取失败'}});
  assert.match(failed,/旧数据参考/);assert.match(failed,/原日期观察：低于多数同行/);assert.match(failed,/不能当作最新结论/);
  const stale=panel({snapshot,benchmark:{...benchmark,stale:true},context});assert.match(stale,/旧数据参考/);
  const cached=panel({snapshot,benchmark:{...benchmark,cached:true},context});assert.match(cached,/来源缓存/);assert.doesNotMatch(cached,/原日期观察/);
  const indexFailure=panel({snapshot,benchmark,context,error:{context:'指数失败'}});
  assert.match(indexFailure,/低于多数同行/);assert.doesNotMatch(indexFailure,/data-analysis-action="water-adopt"/);
  const financeFailure=panel({snapshot:null,benchmark,context,error:{snapshot:'财报失败'}});
  assert.match(financeFailure,/低于多数同行/);assert.doesNotMatch(financeFailure,/原日期观察/);assert.match(financeFailure,/财报失败/);
});

test('water coefficient explains its convention and offers adoption only for valid current data',()=>{
  const html=panel({snapshot,benchmark,context});assert.match(html,/1\.363/);assert.match(html,/4,090\.00 点 ÷ 3,000，最低取 0\.5/);assert.match(html,/data-analysis-action="water-adopt"/);
  assert.match(panel({snapshot,benchmark,context:{...context,indexPoints:1000,coefficient:.5}}),/0\.500/);
  for(const options of [{context:null},{context:{...context,stale:true}},{context:{...context,status:'unavailable'}},{context:{...context,coefficient:1}},{context,loading:{context:true}}])assert.doesNotMatch(panel({snapshot,benchmark,...options}),/data-analysis-action="water-adopt"/);
  assert.match(panel({snapshot,benchmark}),/缺少指数时不默认填 1/);
});

test('external strings remain escaped and unsupported URLs cannot become actions',()=>{
  const html=panel({snapshot,benchmark:{...benchmark,industry:'<script>industry</script>'},context:{...context,sourceName:'<img onerror="x">',sourceUrl:'javascript:alert(1)'},error:{benchmark:'<script>error</script>'}});
  assert.doesNotMatch(html,/<script>|<img|data-analysis-url="javascript/);
  assert.match(html,/&lt;script&gt;industry/);assert.match(html,/&lt;script&gt;error/);
});
