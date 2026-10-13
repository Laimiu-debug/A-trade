const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../preview/finaltrade-workspace.js'),'utf8');
function load(startResearch){
 const listeners={},container={hidden:true,innerHTML:'',children:[],append(frame){this.children.push(frame);}},main={hidden:false};
 const document={addEventListener:(name,handler)=>listeners[name]=handler,getElementById:id=>id==='strategy-workspace-root'?container:id==='main'?main:null,querySelector:()=>null,createElement:()=>({dataset:{},addEventListener(){},contentWindow:{postMessage(){}}})};
 const context={document,setInterval:()=>1,clearInterval(){},addEventListener:(name,handler)=>listeners['window:'+name]=handler};
 context.window=context;context.AT={state:{page:'R11'},escape:value=>String(value),render(){void context.StrategyWorkspace.mount();}};
 if(startResearch)context.atradeDesktop={startResearch};
 vm.runInNewContext(source,context);return {context,listeners};
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
