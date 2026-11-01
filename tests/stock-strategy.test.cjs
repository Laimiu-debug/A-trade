const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
test('unified stock evidence separates signals, completed fills and open positions',()=>{
 execFileSync(require('../desktop/business-service.cjs').pythonCommand(),['-B','-m','unittest','tests.test_stock_strategy'],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:'pipe',timeout:60000});
});

async function stockHtml(statistics) {
 const escape=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const data={source:'market',symbol:'sh600001',as_of_date:'2025-04-28',signal:null,strategies:[],trades:[],signals:[],runs:[],warnings:[],annotation:null,force_statistics:statistics};
 const context={URLSearchParams,queueMicrotask,localStorage:{getItem:()=>null,setItem(){}},
  document:{addEventListener(){},querySelector:()=>null},
  AT:{isDesktop:true,escape,state:{page:'G01',stock:'sh600001'},marketUI:{refreshVisible(){}}},
  atradeDesktop:{startResearch:async()=>({ok:true}),researchRequest:async()=>({ok:true,data})}};
 context.window=context;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/stock-research.js'),'utf8'),context);
 const input={symbol:'sh600001',version:'test',source:'market'};
 context.AT.stockResearch.render(input);
 await new Promise(resolve=>setImmediate(resolve));
 return context.AT.stockResearch.render(input);
}

const force=()=>({version:'force-statistics-v1',metadata:{calendarBasis:'observedBars',inputBarCount:80,validBarCount:80,
 requiredBars:{pressure5:25,pressure20:40}},current:{date:'2025-04-28',barStatus:'valid',status:'complete',
 pressure5:.125,pressure20:-.2,support20:.4,positiveShare20:.55,volumeRatio20:1.25,diagnostics:[]}});

test('stock detail displays shared force statistics with signed pressure and observed-bar scope',async()=>{
 const html=await stockHtml(force());
 for(const text of ['主散量能 · 量价统计','+12.50','-20.00','40.00%','55.00%','1.25 倍','80 / 80 根','截至 2025-04-28',
  '未核验完整交易日历','不代表真实主力 / 散户持仓','至少需要 25 根','至少需要 40 根'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/净流入|资金流入|买入建议/);
 for(const marker of ['data-stock-research-note','data-stock-research-tab="trades"','data-stock-research-show-scan'])assert.ok(html.includes(marker),'existing stock workflow retains '+marker);
});

test('unknown or missing force statistics do not display fabricated zeroes',async()=>{
 const statistics=force();Object.assign(statistics.current,{status:'unknown',barStatus:'invalid',pressure5:null,pressure20:undefined,support20:false,positiveShare20:'',volumeRatio20:NaN});
 const html=await stockHtml(statistics),card=html.match(/<section data-stock-force-statistics[\s\S]*?<\/section>/)[0];
 assert.equal((card.match(/<strong>—<\/strong>/g)||[]).length,5);
 assert.doesNotMatch(card,/0\.00/);assert.match(card,/数据缺失或无效/);
 const warmup=force();Object.assign(warmup.current,{status:'partial',pressure5:null,pressure20:null,support20:null,positiveShare20:null,volumeRatio20:null});
 assert.match(await stockHtml(warmup),/历史不足或窗口内有缺值/);
 assert.match(await stockHtml(undefined),/暂未返回量价统计/);
});

test('force statistics escape source labels and preserve measured zero as distinct from unknown',async()=>{
 const statistics=force();statistics.version='<img src=x onerror=alert(1)>';statistics.current.date='<script>bad</script>';
 Object.assign(statistics.current,{pressure5:0,support20:0,diagnostics:['<img src=x>','priceUp20PressureNegative']});
 const html=await stockHtml(statistics);
 assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);
 assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/<strong>0\.00<\/strong>/);assert.match(html,/<strong>0\.00%<\/strong>/);
 assert.match(html,/价格上涨而量价压力为负/);
});
