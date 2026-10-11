(() => {
  const groups = [
    ['A','账户与复盘','account','A02', [['A01','每日工作台','daily'],['A02','账户总览','account'],['A03','账户管理','account'],['A04','交易流水','ledger'],['A05','导入与对账','import'],['A06','资金流水','cash'],['A07','资产快照','snapshot'],['A08','每日复盘','calendar'],['A09','周期复盘','calendar'],['A10','统计分析','chart'],['A11','分享卡片','share'],['A12','灵感闪记','note'],['A13','次日交易计划','calendar'],['A14','执行对照','compare'],['A15','通行证历程','route']]],
    ['M','市场与行情','market','M01',[['M01','市场总览','market'],['M02','板块行情','boards'],['M03','板块详情','boards'],['M04','个股行情','market'],['M05','资讯中心','report']]],
    ['R','策略与回测','strategy','R03',[['R01','策略库','folder'],['R02','策略详情','strategy'],['R03','回测工作区','strategy'],['R04','回测结果','chart'],['R05','收益平原','plateau'],['R06','操作路径','route'],['R07','研究对比','compare'],['R08','报告库','folder'],['R09','报告详情','report']]],
    ['T','分析工具','tools','T01',[['T01','工具首页','tools'],['T02','情绪估值','gauge'],['T03','跨行业估值筛选','filter'],['T04','趋势龙头','crown'],['T05','板块资金动向','boards'],['T06','异动追踪','pulse'],['T07','断板再涨停','market'],['T08','连板龙头','crown'],['T09','统一跟踪池','eye']]],
    ['C','设置','settings','C01',[['C01','数据源','database'],['C02','账户费率','cash'],['C03','交易与仓位规则','shield'],['C04','备份与恢复','backup'],['C05','显示与交互','settings'],['C07','AI 模型','tools'],['C06','应用与诊断','info']]],
    ['G','全局工作区','tasks','G02',[['G01','个股日线与分析','market'],['G02','任务中心','tasks'],['G03','任务详情','tasks'],['G04','数据状态与质量','shield'],['G05','首次使用引导','account'],['G06','全局搜索','search']]]
  ];
  AT.groups = groups.map(([id,label,icon,home,pages])=>({id,label,icon,home,pages:pages.map(([id,title,icon])=>({id,title,icon,group:id[0]}))}));
  AT.pages = Object.fromEntries(AT.groups.flatMap(g=>g.pages.map(p=>[p.id,p])));
  // Navigation follows the business workspaces in final-trade; detail routes stay contextual.
  Object.entries({A04:'交易管理',A08:'复盘统计',A11:'复盘分享',A15:'里程碑历程',R01:'策略中心',T06:'异动监管',T08:'连板龙头'}).forEach(([id,title])=>AT.pages[id].title=title);
  AT.accountEntries={
    A01:{page:'A08'},A03:{page:'A02',dialog:'account-settings'},A05:{page:'A04',dialog:'import-records'},
    A06:{page:'A04',ledger:'cash'},A07:{page:'A02',dialog:'asset-snapshots'},A09:{page:'A08',note:'A09',section:'review-notes'},
    A10:{page:'A08',section:'review-performance'},A12:{page:'A08',note:'A12',section:'review-notes'},
    A13:{page:'A04',section:'trade-plan'},A14:{page:'A08',section:'review-execution'},A15:{page:'A02'}
  };
  const groupedNavigation={
    A:[['A02','A03','A07','A15'],['A04','A05','A06','A13'],['A08','A01','A09','A10','A12','A14'],['A11']],
    M:[['M01'],['M02','M03'],['M04'],['M05']],
    R:[['R01','R02'],['R03','R04','R05','R06','R07'],['R08','R09']],
    T:[['T02','T03'],['T04'],['T08'],['T05'],['T06','T07'],['T09']],
    C:[['C01','C02','C03','C04','C05','C07','C06']]
  };
  AT.navPages=group=>groupedNavigation[group.id]?.map(pages=>({...AT.pages[pages[0]],title:group.id==='C'?'系统设置':AT.pages[pages[0]].title,pages}))||group.pages;
  AT.pageParentFor=page=>AT.navPages(AT.groups.find(g=>g.id===page[0])).find(p=>p.pages?.includes(page)||p.id===page);
  AT.stocks = [
    {code:'600900',name:'长江电力',board:'主板',sector:'公用事业',price:28.64,change:1.78,qty:12000,cost:26.18,weight:27.31},
    {code:'300750',name:'宁德时代',board:'创业板',sector:'新能源',price:285.60,change:2.36,qty:1200,cost:260.50,weight:27.23},
    {code:'601899',name:'紫金矿业',board:'主板',sector:'有色金属',price:18.92,change:-.84,qty:6000,cost:20.10,weight:9.02},
    {code:'688981',name:'中芯国际',board:'科创板',sector:'半导体',price:91.36,change:3.12,qty:2000,cost:85.40,weight:14.52},
    {code:'000333',name:'美的集团',board:'主板',sector:'家用电器',price:78.32,change:.67,qty:0,cost:0},
    {code:'300059',name:'东方财富',board:'创业板',sector:'金融科技',price:26.18,change:1.82,qty:0,cost:0},
    {code:'688008',name:'澜起科技',board:'科创板',sector:'半导体',price:92.76,change:2.41,qty:0,cost:0},
    {code:'000001',name:'平安银行',board:'主板',sector:'银行',price:12.38,change:-.24,qty:0,cost:0}
  ];
  AT.strategies = [
    {id:'trend',name:'趋势回踩',tag:'趋势延续',icon:'crown',description:'以均线方向识别趋势，观察缩量回踩后的日线确认。',color:'blue'},
    {id:'volume',name:'量价突破',tag:'放量扩张',icon:'market',description:'研究箱体上沿突破与成交量扩张，关注不同市场环境下的差异。',color:'teal'},
    {id:'revert',name:'均值回归',tag:'区间震荡',icon:'pulse',description:'研究价格偏离后的回归；与强趋势条件可能发生冲突。',color:'purple'}
  ];
  AT.sectors = [{name:'半导体',change:3.42,flow:18.62,trend:'量价扩张',width:78},{name:'新能源',change:2.16,flow:12.45,trend:'趋势延续',width:64},{name:'公用事业',change:1.28,flow:6.83,trend:'低波动',width:48},{name:'有色金属',change:-.72,flow:-3.27,trend:'高位分化',width:27}];
  AT.initialTasks = [
    {id:'t1',name:'趋势回踩 × 量价突破',type:'多策略交集',status:'running',progress:64.2,stock:'600900',date:'2018.01 — 2026.09',created:'20:42',step:'日线模拟',count:3},
    {id:'t2',name:'持仓与止盈参数扫描',type:'收益平原',status:'running',progress:38.6,stock:'300750',date:'2020.01 — 2026.09',created:'20:43',step:'参数扫描',count:240},
    {id:'t3',name:'趋势回踩 · 环境对照',type:'单策略回测',status:'queued',progress:0,stock:'688981',date:'2021.01 — 2026.09',created:'20:45',step:'等待计算额度',count:1},
    {id:'t4',name:'均值回归 · 稳定区间',type:'收益平原',status:'paused',progress:52.8,stock:'601899',date:'2018.01 — 2026.09',created:'19:18',step:'参数扫描',count:160},
    {id:'t5',name:'趋势回踩 · 基础研究',type:'单策略回测',status:'complete',progress:100,stock:'600900',date:'2018.01 — 2026.09',created:'18:36',step:'报告已生成',count:1},
    {id:'t6',name:'量价突破 · 行业对照',type:'多策略交集',status:'complete',progress:100,stock:'300750',date:'2020.01 — 2026.09',created:'17:22',step:'报告已生成',count:3}
  ];
  AT.trades = [
    {date:'2026-10-09',signal:'2026-10-08',code:'600900',action:'买入',qty:2000,price:28.16,reason:'趋势回踩 · 收盘确认'},
    {date:'2026-10-09',signal:'2026-10-08',code:'601899',action:'减仓',qty:1000,price:19.08,reason:'仓位控制 · 分批退出'},
    {date:'2026-09-30',signal:'2026-09-29',code:'688981',action:'买入',qty:1000,price:85.40,reason:'量价突破 · 次日执行'},
    {date:'2026-09-29',signal:'2026-09-28',code:'300750',action:'减仓',qty:200,price:280.60,reason:'分段止盈 · 保留底仓'},
    {date:'2026-09-25',signal:'2026-09-24',code:'600900',action:'买入',qty:4000,price:26.58,reason:'趋势确认 · 初始仓位'}
  ];
  AT.news = [
    ['产业观察','半导体与新能源板块的风格对照','比较板块热度、趋势与量能的阅读视图示例。','20:15','688981'],
    ['公司公告','经营数据与财务报告入口','公告正文、来源与关联复盘的排版示例。','18:30','600900'],
    ['研究笔记','当量价突破遇到市场风格切换','从操作路径回看策略条件与退出计划。','17:42','300750']
  ];
  const defaults = {version:1,goal:8,stages:20,startCapital:1000000,account:'实盘主账户',compact:false,reduced:false,largeText:false,concurrency:2,tasks:AT.initialTasks,taskTrash:[],watch:['600900','300750','688981'],notes:{},reports:[],page:'A02',stock:'600900'};
  const desktop = window.atradeDesktop;
  AT.isDesktop = Boolean(desktop);
  AT.storageInfo = desktop?.bootstrap?.storage || null;
  AT.storageWarning = desktop?.bootstrap?.warning || null;
  let saved=null;
  // Desktop state is supplied before page scripts run. Never import browser state implicitly.
  try { saved=AT.isDesktop?structuredClone(desktop.bootstrap.state):JSON.parse(localStorage.getItem('atrade.preview.v1')||'null'); } catch {}
  AT.state = saved && saved.version===1 ? structuredClone({...defaults,...saved,tasks:Array.isArray(saved.tasks)&&saved.tasks.every(t=>t&&typeof t.id==='string'&&Number.isFinite(t.progress))?saved.tasks:AT.initialTasks}) : structuredClone(defaults);
  // Until the user chooses a width, retain the responsive navigation default.
  if(typeof AT.state.sidebarCollapsed!=='boolean')delete AT.state.sidebarCollapsed;
  AT.state.taskTrash=Array.isArray(AT.state.taskTrash)?AT.state.taskTrash.filter(entry=>entry?.task&&typeof entry.task.id==='string'&&Number.isFinite(entry.task.progress)):[];
  if(AT.isDesktop)for(const task of AT.state.tasks)if(['running','queued'].includes(task.status)){task.status='paused';task.step='演示已暂停 · 手动继续'}
  if(Array.isArray(AT.state.customTrades))AT.trades.unshift(...AT.state.customTrades.filter(t=>t&&AT.stocks.some(s=>s.code===t.code)&&Number.isFinite(t.qty)&&Number.isFinite(t.price)));
  AT.ui = {range:'year',metric:'return',taskFilter:'all',board:'全部',query:'',selected:new Set(),strategyIds:['trend','volume'],researchMode:'intersection',stockPool:['600900','300750','688981'],activeTask:AT.state.tasks[0]?.id||null,chartZoom:1,resultTab:'curve',pathIndex:0,heat:[3,4],group:'A',history:[],positions:{},passPosition:null,showAmounts:true};
  AT.findStock = code => AT.stocks.find(s=>s.code===code)||AT.stocks[0];
  AT.escape = text => String(text ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  AT.money = (n,d=2) => Number(n).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
  AT.sign = (n,d=2) => (n>=0?'+':'')+Number(n).toFixed(d);
  AT.stockLink = (code,vertical=false) => {const s=AT.findStock(code);return `<button type="button" class="stock-link ${vertical?'vertical':''}" data-stock="${s.code}" aria-label="查看${s.name} ${s.code}日线与分析"><b>${s.name}</b><span>${s.code}</span></button>`};
  AT.statusNames = {running:'运行中',queued:'排队中',paused:'已暂停',stopped:'已停止',complete:'已完成'};
  AT.statusTag = status => `<span class="tag ${status==='running'?'blue':status==='complete'?'green':status==='stopped'?'red':''}">${AT.icon({running:'play',queued:'clock',paused:'pause',stopped:'stop',complete:'check'}[status])}${AT.statusNames[status]}</span>`;
  const saveStatus=(text,state)=>{const el=document.getElementById('save-status');if(el){el.textContent=text;el.dataset.saveState=state}};
  let saveQueue=Promise.resolve(),saveSequence=0;
  AT.persist = (value=AT.state) => {
    if(value===AT.state&&(AT.ui?.restoringBackup||AT.ui?.committingSettings))return AT.isDesktop?Promise.resolve(false):false;
    if(!AT.isDesktop){try{localStorage.setItem('atrade.preview.v1',JSON.stringify(value));saveStatus('预览状态已保存至浏览器','saved');return true}catch{saveStatus('浏览器未允许保存，当前会话仍可使用','error');return false}}
    const sequence=++saveSequence;
    let snapshot;
    try{snapshot=structuredClone(value)}catch{saveStatus('保存失败，当前更改尚未写入本机','error');return Promise.resolve(false)}
    saveStatus('正在保存到本机…','saving');
    // Snapshot each request now and serialize writes, so an older response cannot overwrite newer edits.
    const pending=saveQueue.then(async()=>{
      try{
        const result=await desktop.saveState(snapshot),ok=result?.ok===true;
        if(sequence===saveSequence)saveStatus(ok?'已保存到本机':'保存失败，当前更改尚未写入本机',ok?'saved':'error');
        return ok;
      }catch{if(sequence===saveSequence)saveStatus('保存失败，当前更改尚未写入本机','error');return false}
    });
    saveQueue=pending.then(()=>undefined,()=>undefined);
    return pending;
  };
  AT.assetTotal=1258640;
})();
