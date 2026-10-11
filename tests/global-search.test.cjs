const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

// Exercise the search controller without booting the unrelated chart/application shell.
const app=fs.readFileSync(path.join(__dirname,'../preview/app.js'),'utf8');
const controller=app.slice(app.indexOf('  const searchRequests='),app.indexOf('  function search(){'));
function setup(desktop=true){
  let nextTimer=0;
  const timers=new Map(),requests=[],input={value:''},pageInput={value:''};
  const target={innerHTML:'',isConnected:true,closest:()=>null},pageTarget={...target};
  const elements={'#search-results':target,'[data-global-search]':input,'#page-search-results':pageTarget,'[data-page-search]':pageInput};
  const AT={isDesktop:desktop,state:{page:'T02'},stocks:[{name:'演示股票',code:'123456',board:'主板'}],pages:{T02:{id:'T02',title:'情绪估值',icon:'chart'},M04:{id:'M04',title:'个股行情',icon:'market'}}};
  const context={$:selector=>elements[selector]||null,AT,E:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),I:()=>'',setTimeout:callback=>{const id=++nextTimer;timers.set(id,callback);return id;},clearTimeout:id=>timers.delete(id),window:{atradeDesktop:{market(method,payload){return new Promise(resolve=>requests.push({method,payload,resolve}));}}}};
  vm.runInNewContext(controller+'\nthis.searchController={renderSearch,cancelSearch,searchComposing};',context);
  const api=context.searchController;
  return {AT,input,target,pageInput,pageTarget,elements,requests,api,
    search(value,page=false){(page?pageInput:input).value=value;api.renderSearch(value,page);},
    run(){const jobs=[...timers.values()];timers.clear();return Promise.all(jobs.map(job=>job()));}};
}
const response=items=>({ok:true,data:{items}});
const bank={symbol:'sz000001',code:'000001',name:'平安银行',market:'sz',kind:'stock'};

test('desktop search uses real stocks with market-qualified identities and keeps page results',async()=>{
  const h=setup();h.search('平安');const work=h.run();
  assert.equal(h.requests[0].method,'market.search');
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].payload)),{query:'平安',kind:'stock',limit:8});
  h.requests[0].resolve(response([bank,{...bank,symbol:'sh000001',kind:'index'}]));await work;
  assert.match(h.target.innerHTML,/data-stock="sz000001"/);assert.match(h.target.innerHTML,/平安银行/);assert.doesNotMatch(h.target.innerHTML,/sh000001|演示股票/);
  h.search('行情');const pageWork=h.run();h.requests[1].resolve(response([]));await pageWork;
  assert.match(h.target.innerHTML,/data-nav="M04"/);assert.match(h.target.innerHTML,/未找到股票/);
});

test('desktop empty search clears pending results without displaying demo stocks',async()=>{
  const h=setup();h.search('平安');const work=h.run();h.search('');
  h.requests[0].resolve(response([bank]));await work;await h.run();
  assert.equal(h.requests.length,1);assert.match(h.target.innerHTML,/输入股票名称/);assert.doesNotMatch(h.target.innerHTML,/平安银行|演示股票/);
});

test('slow responses cannot replace newer results, closed search or changed pages',async()=>{
  const h=setup();h.search('平安');const first=h.run();h.search('茅台');const second=h.run();
  h.requests[1].resolve(response([{...bank,symbol:'sh600519',code:'600519',name:'贵州茅台'}]));await second;
  h.requests[0].resolve(response([bank]));await first;assert.match(h.target.innerHTML,/贵州茅台/);assert.doesNotMatch(h.target.innerHTML,/平安银行/);
  for(const invalidate of [()=>h.api.cancelSearch(false),()=>{h.elements['#search-results']={...h.target};},()=>{h.AT.state.page='M04';}]){
    h.elements['#search-results']=h.target;h.AT.state.page='T02';h.search('平安');const work=h.run(),pending=h.requests.at(-1);const before=h.target.innerHTML;
    invalidate();pending.resolve(response([bank]));await work;assert.equal(h.target.innerHTML,before);
  }
});

test('composition cancels half-entered search and source errors do not fall back to fake stocks',async()=>{
  const h=setup();h.search('pin');h.api.searchComposing.add(h.input);await h.run();assert.equal(h.requests.length,0);
  h.api.searchComposing.delete(h.input);h.search('行情');const work=h.run();h.requests[0].resolve({ok:false,error:'请先选择通达信目录'});await work;
  assert.match(h.target.innerHTML,/请先选择通达信目录/);assert.match(h.target.innerHTML,/data-nav="M04"/);assert.doesNotMatch(h.target.innerHTML,/演示股票/);
});

test('search response labels are escaped and page search stays independent',async()=>{
  const h=setup();h.search('银行',true);const work=h.run();h.search('');h.requests[0].resolve(response([{...bank,name:'<img src=x>'}]));await work;
  assert.match(h.pageTarget.innerHTML,/&lt;img src=x>/);assert.doesNotMatch(h.pageTarget.innerHTML,/<img/);
  assert.match(h.target.innerHTML,/输入股票名称/);
});

test('browser preview retains local demonstration search without calling desktop API',async()=>{
  const h=setup(false);h.search('演示');await h.run();assert.match(h.target.innerHTML,/演示股票/);assert.equal(h.requests.length,0);
});
