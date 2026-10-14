const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../preview/finaltrade-workspace.js'),'utf8');
function load(startResearch){
 const listeners={},messages=[],container={hidden:true,innerHTML:'',children:[],append(frame){this.children.push(frame);}},main={hidden:false};
 const document={addEventListener:(name,handler)=>listeners[name]=handler,getElementById:id=>id==='strategy-workspace-root'?container:id==='main'?main:null,querySelector:()=>null,createElement:()=>({dataset:{},addEventListener(){},contentWindow:{postMessage(message){messages.push(message)}}})};
 const context={document,setInterval:()=>1,clearInterval(){},addEventListener:(name,handler)=>listeners['window:'+name]=handler};
 context.window=context;context.AT={state:{page:'R11'},escape:value=>String(value),render(){void context.StrategyWorkspace.mount();}};
 if(startResearch)context.atradeDesktop={startResearch};
 vm.runInNewContext(source,context);return {context,listeners,messages};
}
test('reference startup failure remains visible and waits for an explicit retry',async()=>{
 let calls=0;
 const {context,listeners}=load(async()=>++calls===1?{ok:false,error:'服务无法启动'}:{ok:true});
 await context.FinaltradeWorkspace.mount();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls,1,'a failed render must not restart the process in a loop');assert.ok(context.document.getElementById('strategy-workspace-root').innerHTML.includes('服务无法启动'));
 context.AT.render();await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,1);
 listeners.click({target:{closest:()=>true}});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls,2);assert.equal(context.document.getElementById('strategy-workspace-root').children.length,1);
});
test('browser preview displays the desktop requirement without recursive rendering',async()=>{
 const {context}=load();await context.FinaltradeWorkspace.mount();
 assert.ok(context.document.getElementById('strategy-workspace-root').innerHTML.includes('请在桌面应用中'));assert.equal(context.document.getElementById('strategy-workspace-root').children.length,0);
});
test('stock chart routes open the native market page and restore the precise research origin',async()=>{
 const {context,listeners,messages}=load(async()=>({ok:true}));
 context.AT.state.page='R05';await context.StrategyWorkspace.mount();
 const frame=context.document.getElementById('strategy-workspace-root').children[0];
 const route=value=>listeners['window:message']({origin:'atrade://reference',source:frame.contentWindow,data:{channel:'atrade.research.v1',action:'route',payload:{route:value}}});
 route('/backtest?section=plateau&report=saved-report');
 const opened=[];context.AT.stockResearch={open(symbol,origin){opened.push({symbol,origin});context.AT.state.page='G01';}};
 route('/stocks/sh600001/chart?focus_date=2025-01-03');
 assert.equal(context.AT.state.page,'G01');assert.equal(opened[0].symbol,'sh600001');
 assert.equal(opened[0].origin.sourceRoute,'/backtest?section=plateau&report=saved-report');
 assert.equal(messages.at(-1).data.route,'/backtest?section=plateau&report=saved-report');
 route('/backtest?section=plateau&report=saved-report');
 assert.equal(context.AT.state.page,'G01','the hidden frame acknowledgement cannot navigate the native stock page back');
});

test('strategy AI context includes only the current visible result page and preserves an explicit cutoff',async()=>{
 const {context,listeners}=load(async()=>({ok:true}));
 context.AT.pages={R11:{title:'选股漏斗'}};context.AT.pageAIContext=require('../preview/page-ai-context.js');
 await context.StrategyWorkspace.mount();const frame=context.document.getElementById('strategy-workspace-root').children[0];
 listeners['window:message']({origin:'atrade://reference',source:frame.contentWindow,data:{channel:'atrade.research.v1',action:'evidence',payload:{page:'screener',as_of_date:'2026-09-08',payload:{active_pool:'step1'},desktopVisible:{tables:[{title:'当前可见结果表1',columns:['代码','名称'],rows:[['600001','龙头甲']],shownRows:1,totalRows:null,scope:'仅已显示分页'}]}}}});
 const snapshot=context.StrategyWorkspace.aiContext();assert.equal(snapshot.tables[0].rows[0][1],'龙头甲');assert.equal(snapshot.filters.asOf,'2026-09-08');assert.equal(snapshot.evidence.desktopVisible,undefined);assert.equal(snapshot.evidence.payload.active_pool,'step1');
});
