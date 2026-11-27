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

test('G01 overlays strategy, master-backtest (建/加/减/损) and master-slip markers and runs single-stock sim',async()=>{
 const escape=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const stratData={source:'market',symbol:'sh600101',as_of_date:'2026-10-15',signal:null,strategies:[],runs:[],warnings:[],annotation:null,force_statistics:force(),
  trades:[{source_id:'t1',source_type:'task',strategy_id:'s1',strategy_name:'主线半路V1',run_name:'策略回测1',entry_date:'2026-10-10',exit_date:'2026-10-14',entry_price:10.5,exit_price:12.0,quantity:1000,pnl_ratio:0.142,exit_reason:'take_profit',position_state:'closed'}],signals:[]};
 const masterData={symbol:'sh600101',masters:[{id:'m1',name:'乌江望月',styleTag:'动态主线',count:2}],
  masterSlips:[
   {id:'s1',masterId:'m1',masterName:'乌江望月',styleTag:'动态主线',date:'2026-10-10',time:'09:35:00',side:'buy',legType:'slip_buy',legLabel:'实盘买入',quantity:2000,price:10.6,amount:21200,note:''},
   {id:'s2',masterId:'m1',masterName:'乌江望月',styleTag:'动态主线',date:'2026-10-13',time:'09:42:00',side:'sell',legType:'slip_sell',legLabel:'实盘卖出',quantity:2000,price:12.2,amount:24400,note:''},
  ],
  masterBacktestRuns:[{taskId:'bt1',taskName:'高手回测·乌江',styleKey:'wujiang_dynamic',styleName:'乌江望月',startDate:'2026-10-01',endDate:'2026-10-15',tradeCount:3}],
  masterBacktestTrades:[
   {taskId:'bt1',taskName:'高手回测·乌江',styleKey:'wujiang_dynamic',styleName:'乌江望月',date:'2026-10-10',side:'buy',actionType:'open_buy',actionLabel:'首仓建仓',quantity:1000,price:10.5,beforeStockWeightPct:0,afterStockWeightPct:15,reason:'信号首仓'},
   {taskId:'bt1',taskName:'高手回测·乌江',styleKey:'wujiang_dynamic',styleName:'乌江望月',date:'2026-10-11',side:'buy',actionType:'add_win',actionLabel:'浮盈加仓',quantity:1000,price:11.6,beforeStockWeightPct:15,afterStockWeightPct:30,reason:'封板加仓'},
   {taskId:'bt1',taskName:'高手回测·乌江',styleKey:'wujiang_dynamic',styleName:'乌江望月',date:'2026-10-13',side:'sell',actionType:'trim_profit',actionLabel:'冲高减半',quantity:1000,price:12.3,beforeStockWeightPct:30,afterStockWeightPct:15,returnPct:11.8,reason:'冲高未板减半'},
  ],
  researchTrades:[],availableStyles:[{key:'wujiang_dynamic',shortName:'乌江望月',name:'乌江望月 · 动态主线'}]};
 const simResult={symbol:'sh600101',styleKey:'wujiang_dynamic',styleName:'乌江望月',shortStyleName:'乌江望月',totalTrades:2,summary:{totalReturnPct:12.5,winRatePct:100},
  trades:[{date:'2026-10-10',side:'buy',actionType:'open_buy',actionLabel:'首仓建仓',quantity:1000,price:10.5,beforeStockWeightPct:0,afterStockWeightPct:15,reason:'单股首仓'},{date:'2026-10-14',side:'sell',actionType:'cut_stop',actionLabel:'硬止损清',quantity:1000,price:9.9,beforeStockWeightPct:15,afterStockWeightPct:0,returnPct:-5.7,reason:'硬止损'}]};
 const handlers={};
 let openedDay=null;
 const context={URLSearchParams,queueMicrotask,localStorage:{getItem:()=>null,setItem(){}},
  document:{addEventListener(type,fn){(handlers[type]??=[]).push(fn);},querySelector:()=>null},
  AT:{isDesktop:true,escape,state:{page:'G01',stock:'sh600101',startCapital:100000},pages:{T11:{title:'大佬交割单'}},
   marketUI:{refreshVisible(){},openDay(s,d){openedDay={s,d};}},navigate(){}},
  atradeDesktop:{
   startResearch:async()=>({ok:true}),
   researchRequest:async()=>({ok:true,data:stratData}),
   market:async(method)=>method==='market.masters.stockMarkers'?{ok:true,data:masterData}:method==='market.masters.stockSimulate'?{ok:true,data:simResult}:{ok:false},
  }};
 context.window=context;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/stock-research.js'),'utf8'),context);
 const input={symbol:'sh600101',version:'v1',source:'market'};
 context.AT.stockResearch.render(input);
 await new Promise(resolve=>setImmediate(resolve));
 await new Promise(resolve=>setImmediate(resolve));

 const rows=[
  {date:'2026-10-10',open:10.4,high:11.0,low:10.2,close:10.8},
  {date:'2026-10-11',open:11.2,high:11.9,low:11.0,close:11.8},
  {date:'2026-10-13',open:12.0,high:12.5,low:11.8,close:12.2},
  {date:'2026-10-14',open:11.9,high:12.1,low:11.5,close:11.7},
 ];
 const svg=context.AT.stockResearch.overlay({symbol:'sh600101',rows,start:0,count:4,left:24,step:80,top:26,bottom:274,priceY:v=>26+(13-v)/3*248});
 for(const cls of ['sr-buy','sr-sell','sr-m-open','sr-m-add','sr-m-trim','sr-slip-buy','sr-slip-sell']){
  assert.ok(svg.includes(cls),'expected SVG marker class '+cls);
 }
 // Same-day buy badges on 2026-10-10 (策略买 + 高手建 + 大佬实买) should be stacked at distinct Y coordinates
 const ys=[...svg.matchAll(/data-stock-research-day="2026-10-10"[\s\S]*?<rect[^>]*y="([0-9.]+)"/g)].map(m=>m[1]);
 assert.equal(ys.length,3,'expected 3 stacked buy markers on 2026-10-10');
 assert.equal(new Set(ys).size,3,'stacked markers must not overlap on identical y');

 const legendHtml=context.AT.stockResearch.legend({symbol:'sh600101',date:'2026-10-10'});
 assert.ok(legendHtml.includes('策略回测 (1)') && legendHtml.includes('大佬回测 (3)') && legendHtml.includes('大佬实盘 (2)'));
 assert.ok(legendHtml.includes('data-stock-master-sim'));
 assert.ok(legendHtml.includes('[策略买]') && legendHtml.includes('[大佬回测·首仓建仓]') && legendHtml.includes('[大佬实盘·乌江望月]'));

 // Clicking single-stock simulate button runs market.masters.stockSimulate and shows sr-m-stop badge
 handlers.click.forEach(fn=>fn({target:{closest:()=>({dataset:{},disabled:false,hasAttribute:a=>a==='data-stock-master-sim'})},preventDefault(){}}));
 await new Promise(resolve=>setImmediate(resolve));
 await new Promise(resolve=>setImmediate(resolve));
 const svgAfterSim=context.AT.stockResearch.overlay({symbol:'sh600101',rows,start:0,count:4,left:24,step:80,top:26,bottom:274,priceY:v=>26+(13-v)/3*248});
 assert.ok(svgAfterSim.includes('sr-m-stop'),'expected single-stock sim stop marker');
 assert.ok(context.AT.stockResearch.openFromMaster('sh600101','2026-10-13',{tab:'backtest',backtestTaskId:'bt1'}));
 assert.deepEqual(openedDay,{s:'sh600101',d:'2026-10-13'});
});
