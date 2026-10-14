const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {shouldStart,guideState,sourceKey,sampleValid,progress,verifySample}=require('../preview/onboarding.js');
const source={configured:true,available:true,path:'fixture',symbolCount:30,lastScannedAt:'2026-10-14T03:00:00Z',latestDate:'2026-10-13'};
const stock={symbol:'sh600001',code:'600001',name:'样本',kind:'stock',latestDate:'2026-10-13'};
const bars={symbol:stock.symbol,name:stock.name,rows:[{date:'2026-10-12',open:10,high:11,low:9,close:10.5},{date:'2026-10-13',open:10.5,high:12,low:10,close:11}]};
function harness(overrides={}){const calls=[];return {calls,market:async(method,payload)=>{calls.push({method,payload});if(overrides[method])return overrides[method](payload);return method==='market.status'?source:method==='market.search'?{items:[stock],total:1}:bars}}}

test('only a fresh desktop with no route or configured market/account starts the guide',()=>{
 assert.equal(shouldStart({state:null,business:{accounts:[]},market:{ok:true,data:{configured:false}}}),true);
 for(const bootstrap of [null,{state:{version:1}},{state:null,market:{data:{configured:true}}},{state:null,business:{accounts:[{id:'existing'}]}}])assert.equal(shouldStart(bootstrap),false);
 assert.equal(shouldStart({state:null},'#M04'),false);
});
test('opening a page or choosing a flow cannot fake a verified data connection',()=>{
 assert.equal(progress({version:1,status:'active',entry:'R11'},source).count,1);
 const guide={version:1,status:'active',entry:'R11',sample:{sourceKey:sourceKey(source),count:2,stale:false}};
 assert.equal(progress(guide,source).count,3);assert.equal(sampleValid(guide,{...source,lastScannedAt:'new scan'}),false);
 assert.equal(sampleValid(guide,{...source,available:false}),false);assert.equal(sampleValid({...guide,sample:{...guide.sample,stale:true}},source),false);
 assert.equal(progress(guide,null).count,0);assert.equal(guideState({version:2,status:'complete'}).status,'new');
});
test('explicit verification uses real status, one stock and its bars, without invoking research or AI',async()=>{
 const h=harness();assert.equal(h.calls.length,0);const sample=await verifySample(h);
 assert.deepEqual(h.calls.map(row=>row.method),['market.status','market.search','market.bars','market.status']);
 assert.deepEqual(h.calls[2].payload,{symbol:stock.symbol,limit:120});assert.equal(sample.count,2);assert.equal(sample.to,'2026-10-13');
 assert.equal(sampleValid({version:1,sample},source),true);assert.doesNotMatch(JSON.stringify(sample),/fixture|dailyBars|rows/);
});
test('empty data, ambiguous names, wrong securities, stale caches and malformed dates cannot pass verification',async()=>{
 for(const [overrides,pattern] of [
  [{'market.status':()=>({...source,symbolCount:0})},/空目录/],
  [{'market.search':()=>({total:2,items:[stock,{...stock,symbol:'sh600002'}]})},/唯一/],
  [{'market.bars':()=>({...bars,symbol:'sz000001'})},/不一致/],
  [{'market.bars':()=>({...bars,stale:true})},/缓存/],
  [{'market.bars':()=>({...bars,rows:[]})},/日线为空/],
  [{'market.bars':()=>({...bars,rows:[{...bars.rows[0],date:'2026-99-01'}]})},/字段不完整/]
 ])await assert.rejects(verifySample({...harness(overrides),query:'样本'}),pattern);
});
test('a directory change during the read or an explicit stop never creates accepted progress',async()=>{
 let count=0;await assert.rejects(verifySample(harness({'market.status':()=>++count===1?source:{...source,path:'another source'}})),/目录或索引已变化/);
 const h=harness();let checks=0;await assert.rejects(verifySample({...h,check:()=>{if(++checks===2)throw Error('已停止')}}),/已停止/);assert.deepEqual(h.calls.map(row=>row.method),['market.status','market.search']);
});
test('first-use progress survives the actual workspace store and cannot be completed by a stale saved sample',async t=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-onboarding-state-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 const {StateStore}=require('../desktop/state-store.cjs'),store=new StateStore(directory),sample=await verifySample(harness());
 store.save({version:1,notes:{},tasks:[],onboarding:{version:1,status:'active',sample,entry:'R03'}});
 const restored=new StateStore(directory).load().state.onboarding;assert.equal(progress(restored,source).count,3);assert.equal(progress(restored,{...source,lastScannedAt:'next scan'}).count,1);
});
