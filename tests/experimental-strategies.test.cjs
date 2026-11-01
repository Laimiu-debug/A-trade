const test=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../preview/experimental-strategies.js');

test('missing research values never become zero, while observed zero is retained',()=>{
  assert.equal(ui.percent(null),'—');assert.equal(ui.percent(undefined),'—');
  assert.equal(ui.percent(NaN),'—');assert.equal(ui.percent('0'),'—');
  assert.equal(ui.percent(0),'0.00%');assert.equal(ui.percent(-4.5),'-4.50%');
});
test('all externally supplied labels and errors are escaped',()=>{
  assert.equal(ui.escape('<img src=x onerror="bad">'), '&lt;img src=x onerror=&quot;bad&quot;&gt;');
  const html=ui.resultTable([{label:'<script>bad()</script>',accountReturnPct:0}]);
  assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));
  assert.equal(ui.errorMessage({message:'明确的服务错误'}),'明确的服务错误');
});
test('flat service summary preserves closed and open distinction',()=>{
  const html=ui.summaryView({accountReturnPct:-3.25,maxDrawdownPct:10,closed:0,profitFactor:null,
    meanWinPct:null,meanLossPct:null,openPositions:1,openUnrealizedPnlAmount:-16250,feesPaid:0,
    meanExposurePct:20,concentration:{top5ShareOfGrossProfitPct:null},
    quality:{valuationUnreliableDates:['2025-01-01'],numericUnknownPaths:[],integrityIssues:[]}});
  assert.ok(html.includes('-3.25%'));assert.ok(html.includes('-16,250.00 元'));
  assert.ok(html.includes('期末未平 1 笔'));assert.ok(html.includes('20.00%'));
  assert.ok(html.includes('估值不可靠日'));assert.ok(html.includes('>1</dd>'));
  assert.ok(!html.includes('NaN'));assert.ok(!html.includes('undefined'));
});
test('research results keep all six sources including losses and unknown metrics',()=>{
  const rows=Array.from({length:6},(_,i)=>({period:i<3?'2022–2023':'2024–2025',board:['main','chinext','star'][i%3],metrics:{accountReturnPct:i===0?null:i-4}}));
  assert.deepEqual(ui.researchRows({researchResults:rows}),rows);
  const html=ui.resultTable(rows);assert.equal((html.match(/<th scope="row">/g)||[]).length,6);
  assert.ok(html.includes('-3.00%'));assert.ok(html.includes('>—</td>'));assert.ok(html.includes('科创板'));
});
test('progress represents the current backend stage rather than invented overall completion',()=>{
  assert.equal(ui.progressValue({progress:{phase:'account',completed:25,total:100}}),25);
  assert.equal(ui.progressLabel({progress:{phase:'account'}}),'执行历史账户');
  assert.equal(ui.progressValue({progress:{phase:'loadInputs',completed:0,total:0}}),null);
  assert.equal(ui.progressValue({progress:{completed:2,total:1}}),100);
});
test('only live lifecycle states expose stop and polling',()=>{
  for(const status of ['queued','pending','running','stopping'])assert.equal(ui.alive({status}),true);
  for(const status of ['completed','failed','stopped','interrupted',undefined])assert.equal(ui.alive({status}),false);
});
test('source identity remains explicit and does not invent dates',()=>{
  assert.equal(ui.sourceLabel({period:'2024–2025',board:'chinext',start:'2024-01-02',end:'2025-12-31'}),'2024–2025 · 创业板 · 2024-01-02 — 2025-12-31');
  assert.equal(ui.sourceLabel({label:'固定样本'}),'固定样本');
});
test('scan output always names its actual data cutoff, not the requested day',()=>{
  assert.equal(ui.scanTitle({requestedDate:'2026-10-17',asOfDate:'2025-12-31',stale:true}),'截至 2025-12-31 的信号');
  assert.equal(ui.evidenceValue({value:0,unit:'%'}),'0.00 %');
  assert.equal(ui.evidenceValue({value:null,unit:'%'}),'—');
});
test('candidate evidence preserves source date and escapes raw company names',()=>{
  const html=ui.candidateView({symbol:'sh600001',name:'<公司>',board:'main',rank:1,close:10.3,signalDate:'2025-12-31',reason:'收盘突破',evidence:[{label:'趋势',value:'<未知>'}],warnings:['容量有限']});
  assert.ok(html.includes('&lt;公司&gt;'));assert.ok(html.includes('2025-12-31'));
  assert.ok(html.includes('收盘突破'));assert.ok(html.includes('&lt;未知&gt;'));assert.ok(html.includes('容量有限'));
  assert.ok(html.includes('data-stock="sh600001"'));assert.ok(html.includes('aria-label="查看 &lt;公司&gt; 日线"'));
  assert.ok(html.includes('data-stock-day="2025-12-31"'));
});

test('candidate links retain the recorded signal date without inventing a date for legacy records',()=>{
  const legacy=ui.candidateView({symbol:'sh600001'});
  assert.ok(!legacy.includes('data-stock-day='));
  assert.ok(legacy.includes('信号日 —'));
  const future=ui.candidateView({symbol:'sh600001',signalDate:'2099-01-02'});
  assert.ok(future.includes('data-stock-day="2099-01-02"'));
  const escaped=ui.candidateView({symbol:'sh600001',signalDate:'2026-10-15" onclick="bad()'});
  assert.ok(escaped.includes('data-stock-day="2026-10-15&quot; onclick=&quot;bad()"'));
  assert.ok(!escaped.includes(' onclick="bad()'));
});
test('data unavailability is distinct from evaluated zero matches',()=>{
  assert.ok(ui.emptyCandidates({counts:{signals:0,evaluated:0}}).startsWith('数据不足'));
  assert.ok(ui.emptyCandidates({counts:{signals:0,evaluated:10}}).includes('已评估范围内暂无符合'));
  assert.ok(ui.emptyCandidates({counts:{signals:null,evaluated:10}}).includes('不可用'));
  assert.ok(ui.emptyCandidates({counts:{signals:0},boards:[{board:'main',evaluatedCount:0}]}).startsWith('数据不足'));
  assert.ok(ui.emptyCandidates({counts:{signals:0,evaluated:10},boards:[{board:'main',evaluatedCount:10,status:'evaluated'},{board:'star',evaluatedCount:0,status:'unavailable'}]}).includes('科创板'));
});
test('saved-fresh scan is compared with the new read-time clock',()=>{
  const result={stale:false,expectedAsOfDate:'2026-10-15',asOfDate:'2026-10-15'};
  const now=ui.currentFreshness(result,{available:true,stale:true,expectedAsOfDate:'2026-10-16',lagSessions:1});
  assert.equal(now.stale,true);assert.equal(now.expectedAsOfDate,'2026-10-16');assert.equal(now.lagSessions,1);
  assert.equal(result.stale,false);assert.equal(ui.currentFreshness(result,null).checked,false);
  assert.equal(ui.currentFreshness(result,{available:false}).stale,null);
});
