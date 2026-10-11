// Dated UI fixtures only. These weekdays are not an exchange trading calendar.
(() => {
  const copy=value=>structuredClone(value);
  AT.demoDates=[];
  for(let d=new Date('2026-01-05T00:00:00Z');d<=new Date('2026-10-09T00:00:00Z');d.setUTCDate(d.getUTCDate()+1)){
    if(d.getUTCDay()!==0&&d.getUTCDay()!==6)AT.demoDates.push(d.toISOString().slice(0,10));
  }
  AT.chartContext=(source='account',overrides={})=>{
    let context={scope:source,source,dataVersion:'UI-DEMO-2026-10-09'};
    if(['research','compare','plateau'].includes(source)){
      const task=source==='plateau'||AT.state.page==='R09'?null:AT.state.tasks.find(t=>t.id===AT.ui.activeTask);
      const report=AT.state.page==='R09';
      const strategyIds=source==='plateau'?(AT.ui.plateauStrategies||['trend','volume']):report?['trend','volume']:task?(task.strategies||[]):AT.ui.strategyIds;
      context={...context,scope:'research',taskId:task?.id||null,configMissing:!!task&&(!task.strategies||!task.params),title:source==='plateau'?'收益平原 · 当前参数切片':report?'报告示范结构':task?.name||'未保存研究 · 交互样例',strategyIds:[...strategyIds],pool:[...(task?.pool|| (task?[task.stock]:AT.ui.stockPool))],params:copy(task?(task.params||{take:15,stop:6,reduce:50}):report?{take:15,stop:6,reduce:50}:AT.ui.researchParams||{take:15,stop:6,reduce:50}),mode:task?.mode||AT.ui.researchMode};
      if(source==='plateau')Object.assign(context,{heat:[...AT.ui.heat],params:{take:8+AT.ui.heat[1]*2,stop:3+AT.ui.heat[0],reduce:AT.ui.plateauRatio||50}});
    }else if(source==='account')context.account=AT.state.account;
    else if(source==='stock')context.code=AT.state.stock;
    else context.title=AT.state.page==='M03'?`${AT.ui.sector||'半导体'}板块`:'市场指数';
    return copy({...context,...overrides});
  };
  AT.contextKey=context=>JSON.stringify(context);
  AT.ui.chartDates={};
  AT.series=(end=25.864,n=90,seed=1)=>Array.from({length:n},(_,i)=>{const t=i/(n-1);return end*t+Math.sin(t*17+seed)*2.1*Math.sin(t*Math.PI)+Math.sin(t*49)*.85*Math.sin(t*Math.PI)-Math.exp(-(((t-.59)/.05)**2))*5.1});
  AT.curvePoints=(context,kind='account',range=AT.ui.range)=>{
    const end=context.source==='plateau'?AT.heatValue(...context.heat)*context.params.reduce/50:context.scope==='research'?42.8:context.scope==='market'?12.84:25.864;
    let values=AT.series(end,AT.demoDates.length,context.scope==='research'?3:1),peak=0;
    // Drawdown uses this context's entire equity history, before the viewport is sliced.
    if(kind==='drawdown')values=values.map(v=>{peak=Math.max(peak,v);return (v-peak)/(100+peak)*100});
    let points=values.map((value,i)=>({date:AT.demoDates[i],value,benchmark:i/(values.length-1)*9.46+Math.sin(i*.18)*1.2}));
    const from=range==='month'?'2026-09-10':range==='quarter'?'2026-07-10':AT.demoDates[0];
    return points.filter(p=>p.date>=from);
  };
  const candleCache=new Map();
  AT.stockDays=code=>{
    if(!candleCache.has(code)){
      const s=AT.findStock(code),count=AT.demoDates.length,seed=Number(s.code)%17;
      const data=AT.demoDates.map((date,i)=>{
        const close=s.price*(.83+i/count*.16+Math.sin(i*.58+seed)*.018+Math.sin(i*.18)*.035),open=close+s.price*Math.sin(i*2.3+seed)*.017;
        return {date,open,close,high:Math.max(open,close)+s.price*(.006+Math.abs(Math.sin(i))*.01),low:Math.min(open,close)-s.price*.009,volume:18+Math.abs(Math.sin(i*2.1))*55};
      });
      const factor=s.price/data.at(-1).close;
      data.forEach(d=>{for(const k of ['open','close','high','low'])d[k]*=factor});
      Object.assign(data.at(-1),{open:s.price*.985,close:s.price,high:s.price*1.023,low:s.price*.976});
      data.forEach((d,i)=>{d.previousClose=data[Math.max(0,i-1)].close;d.value=d.close;d.ma5=average(i,5);d.ma10=average(i,10);d.ma20=average(i,20)});
      function average(i,n){return data.slice(Math.max(0,i-n+1),i+1).reduce((sum,d)=>sum+d.close,0)/Math.min(i+1,n)}
      candleCache.set(code,data);
    }
    return candleCache.get(code);
  };
  AT.visibleStockDays=code=>AT.stockDays(code).slice(-Math.round(64/AT.ui.chartZoom));
  AT.intradayPoints=day=>Array.from({length:50},(_,i)=>{
    // Two separate sessions; anchors agree with the selected daily OHLC.
    const anchors=[[0,day.open],[12,day.high],[31,day.low],[49,day.close]];
    const end=anchors.findIndex(([x])=>x>=i),a=anchors[Math.max(0,end-1)],b=anchors[end],t=a[0]===b[0]?0:(i-a[0])/(b[0]-a[0]);
    const minute=i<25?570+i*5:780+(i-25)*5;
    return {time:String(Math.floor(minute/60)).padStart(2,'0')+':'+String(minute%60).padStart(2,'0'),price:a[1]+(b[1]-a[1])*t,volume:day.volume/50};
  });
  // Separate from account ledgers: never treat an account trade as a strategy fill.
  AT.researchTrace=context=>{
    if(context.configMissing||!context.strategyIds.length||!context.pool.length||context.strategyIds.includes('trend')&&context.strategyIds.includes('revert'))return [];
    const identity=JSON.stringify({task:context.taskId,strategies:context.strategyIds,pool:context.pool,params:context.params});
    const seed=[...identity].reduce((sum,c)=>sum+c.charCodeAt(0),0)%8,records=[];
    for(let cycle=0;cycle<3;cycle++){
      const code=context.pool[cycle%context.pool.length],start=18+cycle*58+seed,days=AT.stockDays(code),initial=1000;
      const reduced=Math.min(initial,Math.max(100,Math.round(initial*context.params.reduce/100/100)*100));
      const steps=[[start,'买入',initial,'组合条件交集通过，建立观察仓位'],[start+18,'减仓',reduced,`分段止盈规则演示触发：阈值 ${context.params.take}%，计划减仓 ${context.params.reduce}%`],[start+33,'卖出',initial-reduced,`退出规则演示触发：止损阈值 ${context.params.stop}% / 风格切换复核`]];
      let position=0;
      for(const [index,action,qty,reason] of steps){
        if(!qty)continue;
        position+=action==='买入'?qty:-qty;
        records.push({kind:'trade',date:AT.demoDates[index+1],signal:AT.demoDates[index],code,action,qty,position,price:days[index+1].open,reason,taskId:context.taskId,fee:Math.max(5,qty*days[index+1].open*.0003)});
      }
    }
    return records;
  };
  AT.dayRecords=(context,date)=>{
    if(context.scope==='research'){
      const all=AT.researchTrace(context);
      return {trades:all.filter(r=>r.date===date),signals:all.filter(r=>r.signal===date),events:[],cash:[]};
    }
    if(context.scope==='account'){
      const all=AT.accountHistory(context.account);
      return {trades:all.filter(r=>r.kind==='trade'&&r.date===date),signals:[],events:all.filter(r=>r.kind==='event'&&r.date===date),cash:date==='2026-01-05'&&context.account==='实盘主账户'?[{title:'期初资金',amount:AT.state.startCapital}]:[]};
    }
    return {trades:[],signals:[],events:[],cash:[]};
  };
  AT.operationDates=context=>new Set((context.scope==='research'?AT.researchTrace(context):context.scope==='account'?AT.accountHistory(context.account).filter(r=>r.kind==='trade'):[]).map(r=>r.date));
})();
