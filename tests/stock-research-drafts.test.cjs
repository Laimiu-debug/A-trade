const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function runtime(storage=new Map()){
 const handlers={},requests=[],label={textContent:''};
 const context={URLSearchParams,queueMicrotask,MouseEvent:class{},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
  document:{addEventListener(type,handler){(handlers[type]??=[]).push(handler);},querySelector:()=>null},
  AT:{isDesktop:true,escape:String,state:{page:'G01',stock:'sh600001'},marketUI:{refreshVisible(){}}},
  atradeDesktop:{startResearch:async()=>({ok:true}),researchRequest:request=>new Promise(resolve=>requests.push({request,resolve}))}};
 context.window=context;
 const source=fs.readFileSync(path.join(__dirname,'../preview/stock-research.js'),'utf8').replace(/\}\)\(\);\s*$/,'AT.testStockDrafts={entryFor,cache};})();');
 vm.runInNewContext(source,context);
 const entry=context.AT.testStockDrafts.entryFor({symbol:'sh600001',version:'first',source:'market'});
 entry.data={annotation:null};if(!entry.draft)entry.draft={symbol:entry.symbol,start_date:'2025-01-06',stage:'Mid',trend_class:'Unknown',decision:'保留',notes:'',updated_by:'manual'};
 const form={dataset:{stockResearchNote:entry.symbol},querySelector:()=>label,closest:()=>form};
 const input=(name,value)=>handlers.input.forEach(handler=>handler({target:{name,value,closest:()=>form}}));
 const submit=()=>handlers.submit.forEach(handler=>handler({target:form,preventDefault(){}}));
 return {context,entry,requests,input,submit,storage};
}
test('unsaved stock notes survive a new renderer and remain scoped to the symbol',()=>{
 const first=runtime();first.input('notes','尚未提交的研究笔记');
 const second=runtime(first.storage);
 const restored=second.context.AT.testStockDrafts.entryFor({symbol:'sh600001',version:'second',source:'market'});
 assert.equal(restored.draft.notes,'尚未提交的研究笔记');assert.equal(restored.dirty,true);
 const other=second.context.AT.testStockDrafts.entryFor({symbol:'sz000001',version:'second',source:'market'});
 assert.equal(other.dirty,false);assert.equal(other.draft,null);
});
test('editing during annotation save keeps the new draft when the older save completes',async()=>{
 const state=runtime();state.input('notes','已提交的版本');state.submit();await settle();
 assert.equal(state.requests.length,1);state.input('notes','继续编辑的新版本');
 state.requests[0].resolve({ok:true,data:{annotation:state.requests[0].request.body}});await settle();
 assert.equal(state.entry.data.annotation.notes,'已提交的版本');assert.equal(state.entry.draft.notes,'继续编辑的新版本');
 assert.equal(state.entry.dirty,true);assert.match(state.entry.feedback,/未保存/);
 assert.equal(await state.context.AT.stockResearch.flush(),true);
});
test('successful save clears the pending draft and a failed save retains it',async()=>{
 const state=runtime();state.input('notes','已保存');state.submit();await settle();
 state.requests[0].resolve({ok:true,data:{annotation:state.requests[0].request.body}});await settle();
 assert.equal(state.entry.dirty,false);assert.equal(JSON.parse([...state.storage.values()][0]).sh600001,undefined);
 state.input('notes','保留失败后的输入');state.submit();await settle();
 state.requests[1].resolve({ok:false,error:'写入失败'});await settle();
 assert.equal(state.entry.dirty,true);assert.equal(state.entry.draft.notes,'保留失败后的输入');
 assert.equal(JSON.parse([...state.storage.values()][0]).sh600001.notes,'保留失败后的输入');
});
