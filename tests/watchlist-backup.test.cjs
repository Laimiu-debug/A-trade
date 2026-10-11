const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function fixture(handler){
  const events={},requests=[],downloads=[],errors={hidden:true,textContent:''},input={disabled:false,value:'fixture.json'},button={disabled:false};
  const AT={isDesktop:true,escape:value=>String(value).replaceAll('<','&lt;').replaceAll('>','&gt;'),button:(label,icon,attrs)=>`<button ${attrs}>${label}</button>`,toast(){},render(){},openBusinessModal(title,body,options){AT.modal={title,body,options}},closeBusinessModal(){AT.modal=null},analysisTools:{invalidateSource(){AT.invalidated=true}}};
  const document={addEventListener:(name,fn)=>events[name]=fn,querySelector:()=>errors,querySelectorAll:()=>[input,button],body:{append(){}},createElement:()=>({click(){downloads.push(this.download)},remove(){}})};
  const bridge={market:async(method,payload)=>{requests.push([method,payload]);return handler(method,payload)}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/watchlist-backup.js'),'utf8'),{AT,document,window:{atradeDesktop:bridge},Blob,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},setTimeout:fn=>fn(),console});
  return {AT,requests,downloads,errors,input,button,async read(data,name='fixture.json'){input.matches=()=>true;input.files=[{name,size:100,text:async()=>JSON.stringify(data)}];events.change({target:input});await new Promise(resolve=>setImmediate(resolve));},async click(action){events.click({target:{closest:()=>({dataset:{watchlistBackup:action}})},preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));}};
}
test('watchlist file is validated and summarized before an explicit replacement request',async()=>{
  const backup={format:'a-trade.watchlist',items:[]};
  const app=fixture(async method=>method.endsWith('.preview')?{ok:true,data:{total:0,currentTotal:3,archived:0,exportedAt:'2026-10-11T01:00:00Z'}}:{ok:true,data:{total:0,previousBackup:{format:'a-trade.watchlist',items:[{symbol:'sh600900'}]}}});
  assert.match(app.AT.watchlistBackup.panel(),/不是行情全库备份/);
  await app.read(backup,'<danger>.json');
  assert.deepEqual(app.requests.map(([method])=>method),['market.watchlist.backup.preview']);
  assert.match(app.AT.modal.body,/&lt;danger&gt;/);assert.match(app.AT.modal.body,/替换当前 <strong>3/);
  await app.click('restore');
  assert.equal(app.requests[1][0],'market.watchlist.backup.restore');assert.deepEqual(JSON.parse(JSON.stringify(app.requests[1][1])),{backup});
  assert.equal(app.AT.invalidated,true);assert.equal(app.AT.modal,null);assert.match(app.downloads[0],/跟踪池恢复前副本/);
});
test('failed validation or cancelled summary never requests restoration',async()=>{
  const bad=fixture(async()=>({ok:false,error:'备份格式错误'}));await bad.read({format:'wrong'});
  assert.equal(bad.AT.modal,undefined);assert.equal(bad.errors.hidden,false);assert.equal(bad.errors.textContent,'备份格式错误');assert.equal(bad.requests.length,1);
  const valid=fixture(async()=>({ok:true,data:{total:1,currentTotal:2,archived:0,exportedAt:'2026-10-11T01:00:00Z'}}));
  await valid.read({format:'a-trade.watchlist'});await valid.click('cancel');await valid.click('restore');
  assert.equal(valid.requests.length,1);assert.equal(valid.AT.modal,null);assert.equal(valid.downloads.length,0);
});
