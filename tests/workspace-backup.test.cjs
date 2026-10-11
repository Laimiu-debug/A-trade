'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const settings=require('../preview/settings-state.js');
const backup=require('../preview/workspace-backup.js');
const options={settings,isDesktop:true};
function state(){return {version:1,goal:8,stages:20,startCapital:100000,account:'实盘主账户',compact:false,reduced:false,largeText:false,sidebarCollapsed:false,concurrency:2,tasks:[],taskTrash:[],watch:['600900'],notes:{A12:'观察'},reports:[],customTrades:[],effects:{enabled:true,opacity:78},milestoneEvents:{},businessReviewDrafts:{},fees:{commissionRate:2.5,minCommission:5},positionRules:{singleStockMax:30,totalMax:80},page:'C04',stock:'sh600900',businessAccountId:'current-account',unrelatedModule:{keep:true}};}
test('interface export and restore round-trip all supported preferences, rules and drafts',()=>{
  const original=state();Object.assign(original,{compact:true,reduced:true,largeText:true,sidebarCollapsed:true,concurrency:4,fees:{commissionRate:3,minCommission:6},positionRules:{singleStockMax:10,totalMax:60},businessReviewDrafts:{'account|new:daily':{kind:'daily',date:'2026-10-11',title:'未完成草稿',fields:{market:'观察记录'},scores:{},relatedTradeIds:[]}},customTrades:[{code:'600900',date:'2026-10-10',action:'买入',qty:100,price:23}]});
  const exported=backup.create(original,options),current=state();current.page='C05';current.stock='sz000001';
  const {candidate,summary}=backup.prepare(JSON.parse(JSON.stringify(exported)),current,options);
  for(const key of backup.fields)if(Object.hasOwn(exported.state,key))assert.deepEqual(candidate[key],exported.state[key],key);
  assert.equal(candidate.page,'C05');assert.equal(candidate.stock,'sz000001');assert.equal(candidate.businessAccountId,'current-account');assert.deepEqual(candidate.unrelatedModule,{keep:true});
  assert.equal(summary.reviews,1);assert.equal(summary.customTrades,1);
  assert.equal(exported.state.unrelatedModule,undefined);assert.equal(exported.state.businessAccountId,undefined);
});
test('analysis presets survive state validation and interface backup restore',()=>{
  const original=state();original.analysisPresets={T03:[{name:'高成交额',settings:{group:'screen',values:{page:1,minAmount:'100000000'},query:'',sort:'amount',order:'desc'}}]};
  assert.doesNotThrow(()=>settings.validate(original));
  const exported=backup.create(original,options);
  assert.deepEqual(exported.state.analysisPresets,original.analysisPresets);
  const restored=backup.prepare(JSON.parse(JSON.stringify(exported)),state(),options).candidate;
  assert.deepEqual(restored.analysisPresets,original.analysisPresets);
});
test('legacy backups migrate ambiguous rules and pause desktop demo tasks without mutating input',()=>{
  const old=state();delete old.fees;delete old.positionRules;delete old.businessReviewDrafts;old.ruleA=20;old.ruleB=70;
  old.tasks=[{id:'t1',name:'示例',type:'策略',stock:'600900',status:'running',progress:25}];
  const current=state();current.businessReviewDrafts={'account|new:idea':{kind:'idea',date:'',title:'',fields:{},scores:{},relatedTradeIds:[]}};
  const {candidate,summary}=backup.prepare(old,current,options);
  assert.equal(summary.legacy,true);assert.equal(candidate.tasks[0].status,'paused');assert.equal(old.tasks[0].status,'running');
  assert.equal(candidate.feesNeedsReview,true);assert.equal(candidate.positionRulesNeedsReview,true);assert.equal(candidate.ruleA,20);assert.deepEqual(candidate.businessReviewDrafts,current.businessReviewDrafts);
});
test('modern snapshots clear optional settings absent when exported, while legacy snapshots preserve them',()=>{
  const original=state();delete original.sidebarCollapsed;delete original.businessReviewDrafts;
  const current=state();current.sidebarCollapsed=true;current.feesNeedsReview=true;current.ruleA=20;
  current.businessReviewDrafts={'account|new:idea':{kind:'idea',date:'',title:'',fields:{},scores:{},relatedTradeIds:[]}};
  const modern=backup.prepare(backup.create(original,options),current,options).candidate;
  assert.equal(Object.hasOwn(modern,'sidebarCollapsed'),false);assert.equal(Object.hasOwn(modern,'businessReviewDrafts'),false);assert.equal(Object.hasOwn(modern,'ruleA'),false);assert.equal(Object.hasOwn(modern,'feesNeedsReview'),false);
  const legacy=backup.prepare(original,current,options).candidate;assert.equal(legacy.sidebarCollapsed,true);assert.deepEqual(legacy.businessReviewDrafts,current.businessReviewDrafts);
});
test('invalid, wrong-scope and unsafe backups fail before touching the current state',()=>{
  const current=state(),before=structuredClone(current);
  for(const change of [s=>{s.compact='yes'},s=>{s.positionRules.singleStockMax=90},s=>{s.effects.opacity=1},s=>{s.businessReviewDrafts={'bad':{}}},s=>{s.customTrades=[{}]},s=>{s.notes={A12:42}},s=>{s.taskTrash=[{task:{id:'t1',name:'x',status:'paused',progress:0}}]}]){
    const broken=state();change(broken);assert.throws(()=>backup.prepare(broken,current,options));assert.deepEqual(current,before);
  }
  assert.throws(()=>backup.prepare({format:'a-trade.accounts',schemaVersion:1},current,options));
  const unsafe=JSON.parse(JSON.stringify(state()).replace('"notes":{"A12":"观察"}','"notes":{"__proto__":"bad"}'));
  assert.throws(()=>backup.prepare(unsafe,current,options),/不安全/);assert.deepEqual(current,before);
});
function restoreHandler(persist){
  const code=fs.readFileSync(path.join(__dirname,'../preview/app.js'),'utf8');
  const start=code.indexOf("  actions['confirm-restore']=async()=>{"),end=code.indexOf("  document.addEventListener('keydown'",start);
  assert.ok(start>0&&end>start);
  const button={disabled:false},error={hidden:true,textContent:''},messages=[];
  const AT={state:state(),ui:{taskSelection:new Set()},trades:[],workspaceBackup:backup,settingsState:settings,isDesktop:true,persist};
  const context={AT,actions:{},$:selector=>selector.includes('confirm-restore')?button:error,applyPrefs(){context.applied=true},closeModal(){context.closed=true},render(){context.rendered=true},toast:message=>messages.push(message)};
  vm.runInNewContext(code.slice(start,end),context);
  return {AT,context,button,error,messages,restore:context.actions['confirm-restore']};
}
test('failed persistence retains current UI state and leaves the restore candidate retryable',async()=>{
  const app=restoreHandler(async()=>false),before=structuredClone(app.AT.state),incoming=state();incoming.compact=true;
  app.AT.ui.restoreCandidate=backup.create(incoming,options);await app.restore();
  assert.deepEqual(app.AT.state,before);assert.ok(app.AT.ui.restoreCandidate);assert.equal(app.error.hidden,false);assert.match(app.error.textContent,/当前界面状态未替换/);assert.equal(app.context.closed,undefined);assert.equal(app.button.disabled,false);
});
test('successful persistence precedes applying restored preferences and replacing demo trades',async()=>{
  let app;app=restoreHandler(async candidate=>{assert.equal(app.AT.state.compact,false);assert.equal(candidate.compact,true);return true;});
  const oldTrade={code:'600900',date:'2026-10-09',action:'买入',qty:100,price:20},baseTrade={code:'300750'};
  app.AT.state.customTrades=[oldTrade];app.AT.trades=[oldTrade,baseTrade];const incoming=state();incoming.compact=true;incoming.customTrades=[];
  app.AT.ui.restoreCandidate=backup.create(incoming,options);await app.restore();
  assert.equal(app.AT.state.compact,true);assert.deepEqual(app.AT.trades,[baseTrade]);assert.equal(app.context.applied,true);assert.equal(app.context.closed,true);assert.equal(app.AT.ui.restoreCandidate,null);
});
