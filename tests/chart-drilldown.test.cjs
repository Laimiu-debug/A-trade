const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function fixture(){
  const context=vm.createContext({localStorage:{getItem:()=>null},structuredClone,Intl,console,document:{addEventListener(){}},matchMedia:()=>({matches:false,addEventListener(){}})});
  context.window=context;
  for(const file of ['icons','data','milestones','effects','chart-data','charts','views','account-views','chart-details'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../preview',file+'.js'),'utf8'),context,{filename:file+'.js'});
  context.AT.ui.taskSelection=new Set();
  context.AT.ui.researchParams={take:15,stop:6,reduce:50};
  context.AT.ui.activeTask=null;
  return context.AT;
}
test('date and OHLC stay stable across zoom; intraday agrees with its daily candle',()=>{
  const a=fixture(),date='2026-10-08';
  const before=a.visibleStockDays('600900').find(p=>p.date===date);
  a.ui.chartZoom=3;
  assert.deepEqual(a.visibleStockDays('600900').find(p=>p.date===date),before);
  const minute=a.intradayPoints(before);
  assert.equal(minute[0].price,before.open);assert.equal(minute.at(-1).price,before.close);
  assert.equal(Math.max(...minute.map(p=>p.price)),before.high);
  assert.equal(Math.min(...minute.map(p=>p.price)),before.low);
  assert.equal(minute[24].time,'11:30');assert.equal(minute[25].time,'13:00');assert.equal(minute.at(-1).time,'15:00');
});
test('account drilldown filters the account and exact date, including events and empty days',()=>{
  const a=fixture(),real=a.chartContext('account'),sim=a.chartContext('account',{account:'模拟研究账户'});
  a.trades.push({account:'模拟研究账户',date:'2026-10-09',code:'000001',qty:100,price:12,action:'买入'});
  assert.equal(a.dayRecords(real,'2026-10-09').trades.length,2);
  assert.equal(a.dayRecords(sim,'2026-10-09').trades.length,1);
  assert.equal(a.dayRecords(sim,'2026-10-08').events.length,0);
  assert.equal(a.dayRecords(real,'2026-10-08').events[0].title,'晚间研究记录');
  assert.equal(a.dayRecords(real,'2026-01-07').trades.length,0);
  assert.equal(a.dayRecords(real,'2026-01-05').cash.length,1);
  assert.equal(a.dayRecords(sim,'2026-01-05').cash.length,0);
});
test('each research trace uses its frozen task configuration, never account records',()=>{
  const a=fixture();
  a.state.tasks=[{id:'test-a',name:'A',strategies:['trend'],pool:['600900'],params:{take:12,stop:4,reduce:30},mode:'single'},{id:'test-b',name:'B',strategies:['volume'],pool:['688981'],params:{take:20,stop:8,reduce:70},mode:'single'}];
  a.ui.activeTask='test-a';const first=a.chartContext('research');
  a.ui.activeTask='test-b';const second=a.chartContext('research');
  assert.equal(first.taskId,'test-a');assert.equal(first.params.reduce,30);
  assert.ok(a.researchTrace(first).every(t=>t.code==='600900'&&t.taskId==='test-a'));
  assert.ok(a.researchTrace(second).every(t=>t.code==='688981'&&t.taskId==='test-b'));
  const trace=a.researchTrace(first);a.trades=[];assert.deepEqual(a.researchTrace(first),trace);
  assert.deepEqual(a.researchTrace({...first,source:'compare'}),trace);
  a.state.tasks[0].params.take=99;assert.equal(first.params.take,12);
});
test('signals precede executions, partial exits keep a consistent position, conflicts have no fills',()=>{
  const a=fixture(),context=a.chartContext('research'),records=a.researchTrace(context);
  for(const t of records){
    assert.equal(a.demoDates.indexOf(t.date),a.demoDates.indexOf(t.signal)+1);
    assert.ok(a.dayRecords(context,t.signal).signals.some(s=>s.code===t.code&&s.action===t.action));
    assert.ok(!a.dayRecords(context,t.signal).trades.some(s=>s.date===t.date));
  }
  assert.equal(records[1].position,records[0].qty-records[1].qty);assert.equal(records[2].position,0);
  assert.equal(a.researchTrace({...context,strategyIds:['trend','revert']}).length,0);
});
test('research drawdown uses research equity; changing date range does not reset the history',()=>{
  const a=fixture(),c=a.chartContext('research'),returns=a.curvePoints(c,'research','year'),drawdown=a.curvePoints(c,'drawdown','year');
  let peak=0;returns.forEach((p,i)=>{peak=Math.max(peak,p.value);assert.equal(drawdown[i].value,(p.value-peak)/(100+peak)*100);assert.equal(drawdown[i].date,p.date)});
  for(const p of a.curvePoints(c,'drawdown','month'))assert.equal(p.value,drawdown.find(q=>q.date===p.date).value);
  assert.notEqual(drawdown[70].value,a.curvePoints(a.chartContext('account'),'drawdown','year')[70].value);
});
test('plateau selection captures strategy and all three parameters without an unrelated task',()=>{
  const a=fixture();a.ui.heat=[1,2];a.ui.plateauRatio=70;a.ui.plateauStrategies=['trend'];
  const c=a.chartContext('plateau');assert.equal(c.taskId,null);
  assert.equal(c.params.take,12);assert.equal(c.params.stop,4);assert.equal(c.params.reduce,70);assert.equal(c.strategyIds.join(','),'trend');
  a.ui.heat=[4,7];a.ui.plateauRatio=30;assert.equal(c.params.take,12);assert.equal(c.params.reduce,70);
});
test('all pages and day detail variants render without missing helpers',()=>{
  const a=fixture();
  for(const page of Object.keys(a.pages)){a.state.page=page;assert.equal(typeof a.view(page),'string',page)}
  for(const source of ['account','research','plateau','stock','market']){
    const context=a.chartContext(source),points=source==='stock'?a.stockDays(context.code):a.curvePoints(context);
    for(const index of [0,40,points.length-1])assert.ok(a.dayDetailContent({context,points,index}).includes(points[index].date.replaceAll('-','.')));
  }
});
