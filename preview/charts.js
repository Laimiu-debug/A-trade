(() => {
  const E=AT.escape;
  const name=context=>context.scope==='account'?context.account:context.scope==='stock'?`${AT.findStock(context.code).name} ${context.code}`:context.title;
  const attributes=context=>'data-chart-context="'+E(JSON.stringify(context))+'"';
  const read=wrap=>JSON.parse(wrap.dataset.chartContext);
  const hint='<p class="chart-drill-hint">点击日期查看当天记录 · 方向键选日，Enter 打开 <span>● 有操作</span></p>';
  AT.chart=(kind='account',height=242,source=kind==='drawdown'?'account':kind)=>{
    const context=typeof source==='string'?AT.chartContext(source):source;
    return `<div class="chart-wrap" data-chart="${kind}" ${attributes(context)} style="min-height:${height}px"><svg class="line-chart" style="height:${height}px"></svg><div class="chart-tooltip hidden"></div></div>${hint}`;
  };
  AT.candles=()=>'<div class="chart-wrap" data-candles '+attributes(AT.chartContext('stock'))+'><svg class="candle-chart"></svg><div class="chart-tooltip hidden"></div></div><p class="chart-drill-hint">点击日 K 查看当天分时 · 方向键选日，Enter 打开</p>';
  AT.spark=(seed=1,positive=true,code=AT.state.stock)=>'<svg class="sparkline" data-spark '+attributes(AT.chartContext('stock',{code}))+' viewBox="0 0 80 28"></svg>';
  AT.selectChartDate=(context,date)=>{
    AT.ui.chartDates[AT.contextKey(context)]=date;
    document.querySelectorAll('[data-chart-context]').forEach(wrap=>{if(AT.contextKey(read(wrap))===AT.contextKey(context))wrap.selectDate?.(date)});
  };
  function bind(wrap,svg,context,points,{w,left,right,x,y,format}){
    const key=AT.contextKey(context),tooltip=wrap.querySelector('.chart-tooltip');
    let selected=points.findIndex(p=>p.date===AT.ui.chartDates[key]);
    let current=selected<0?points.length-1:selected;
    svg.setAttribute('tabindex','0');svg.setAttribute('role','button');
    function label(){svg.setAttribute('aria-label',name(context)+' · '+points[current].date+' · '+format(points[current])+'。演示数据，左右键选日，Enter 查看当天详情')}
    function mark(index,visible=true){
      const guide=svg.querySelector('.guide'),point=svg.querySelector('.hover-point');
      if(guide){guide.setAttribute('x1',x(index));guide.setAttribute('x2',x(index));guide.setAttribute('visibility',visible?'visible':'hidden')}
      if(point){point.setAttribute('cx',x(index));point.setAttribute('cy',y(points[index]));point.setAttribute('visibility',visible?'visible':'hidden')}
    }
    function select(index){current=selected=index;AT.ui.chartDates[key]=points[index].date;mark(index);label()}
    wrap.selectDate=date=>{const index=points.findIndex(p=>p.date===date);if(index>=0)select(index)};
    const hit=e=>{const rect=svg.getBoundingClientRect(),px=(e.clientX-rect.left)*w/rect.width;return Math.max(0,Math.min(points.length-1,Math.round((px-left)/(right-left)*(points.length-1))))};
    svg.onpointermove=e=>{
      current=hit(e);mark(current);label();
      if(tooltip){const rect=svg.getBoundingClientRect();tooltip.innerHTML='<span>'+E(name(context))+'</span><b>'+points[current].date+'</b><span>'+E(format(points[current]))+'</span><span>点击查看当天'+(context.scope==='stock'?'分时':'记录')+' · 演示</span>';tooltip.style.left=Math.max(0,Math.min(e.clientX-rect.left+18,wrap.clientWidth-220))+'px';tooltip.classList.remove('hidden')}
    };
    svg.onpointerleave=()=>{tooltip?.classList.add('hidden');current=selected<0?points.length-1:selected;mark(current,selected>=0);label()};
    svg.onclick=e=>{
      if(e.detail)select(hit(e));else select(current);
      tooltip?.classList.add('hidden');svg.focus({preventScroll:true});
      AT.openDayDetail({context:structuredClone(context),points:structuredClone(points),index:current});
    };
    svg.onkeydown=e=>{
      if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
        e.preventDefault();select(e.key==='Home'?0:e.key==='End'?points.length-1:Math.max(0,Math.min(points.length-1,current+(e.key==='ArrowLeft'?-1:1))));
      }else if(e.key==='Enter'||e.key===' '){e.preventDefault();select(current);AT.openDayDetail({context:structuredClone(context),points:structuredClone(points),index:current})}
    };
    label();if(selected>=0)mark(selected);
  }
  AT.drawCharts=()=>document.querySelectorAll('[data-chart]').forEach((wrap,idx)=>{
    const svg=wrap.querySelector('svg'),kind=wrap.dataset.chart,context=read(wrap),points=AT.curvePoints(context,kind);
    const w=Math.max(300,wrap.clientWidth-24),h=parseInt(svg.style.height)||242,isDD=kind==='drawdown';
    const values=points.flatMap(p=>isDD?[p.value]:[p.value,p.benchmark]),min=isDD?Math.min(...values)*1.15:Math.min(0,...values)-2,max=isDD?1:Math.max(...values)*1.14+1;
    const l=48,r=29,t=19,b=33,pw=w-l-r,ph=h-t-b,x=i=>l+i/(points.length-1)*pw,y=v=>t+(max-v)/(max-min||1)*ph;
    const path=key=>points.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+','+y(p[key]).toFixed(1)).join(' ');
    const color=isDD?'var(--down)':kind==='plateau'?(points.at(-1).value>=0?'var(--up)':'var(--down)'):'var(--primary)',id='gradient-'+idx;
    let grid='';
    for(let j=0;j<5;j++){const v=min+(max-min)*j/4,yy=y(v);grid+=`<line class="grid" x1="${l}" x2="${w-r}" y1="${yy}" y2="${yy}"/><text x="${l-11}" y="${yy+3}" text-anchor="end">${v.toFixed(0)}%</text>`}
    for(let j=0;j<5;j++){const i=Math.round((points.length-1)*j/4);grid+=`<text x="${x(i)}" y="${h-10}" text-anchor="${j===0?'start':j===4?'end':'middle'}">${points[i].date.slice(5)}</text>`}
    const dates=AT.operationDates(context),markers=points.map((p,i)=>dates.has(p.date)?`<circle class="operation-marker" cx="${x(i)}" cy="${y(p.value)}" r="3.8" fill="${color}" stroke="var(--surface)" stroke-width="1.5"/>`:'').join('');
    svg.setAttribute('viewBox','0 0 '+w+' '+h);
    svg.innerHTML=`<defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop stop-color="${color}" stop-opacity=".13"/><stop offset="1" stop-color="${color}" stop-opacity=".005"/></linearGradient></defs>${grid}<path d="${path('value')} L${x(points.length-1)},${y(isDD?0:min)} L${l},${y(isDD?0:min)} Z" fill="url(#${id})"/>${isDD?'':`<path d="${path('benchmark')}" stroke="var(--color-axis)" stroke-width="1.5" stroke-dasharray="4 5" fill="none"/>`}<path d="${path('value')}" stroke="${color}" stroke-width="2.3" fill="none" stroke-linejoin="round"/>${markers}<line class="guide" y1="${t}" y2="${h-b}" stroke="var(--color-zero)" stroke-dasharray="3 4" visibility="hidden"/><circle class="hover-point" r="5" fill="var(--surface)" stroke="${color}" stroke-width="2" visibility="hidden"/><rect x="${l}" y="${t}" width="${pw}" height="${ph}" fill="transparent" class="chart-click"/>`;
    bind(wrap,svg,context,points,{w,left:l,right:w-r,x,y:p=>y(p.value),format:p=>(isDD?'回撤':context.scope==='market'?'指数变动':'累计收益')+' '+AT.sign(p.value)+'%'});
  });
  AT.drawCandles=()=>document.querySelectorAll('[data-candles]').forEach(wrap=>{
    const context=read(wrap),data=AT.visibleStockDays(context.code),svg=wrap.querySelector('svg'),w=Math.max(300,wrap.clientWidth-24),h=375,l=48,r=43,t=28,ph=238;
    const min=Math.min(...data.map(d=>d.low))*.99,max=Math.max(...data.map(d=>d.high))*1.01,step=(w-l-r)/data.length,y=v=>t+(max-v)/(max-min)*ph,x=i=>l+i*step+step/2;
    let grid='';for(let j=0;j<5;j++){const v=min+(max-min)*j/4,yy=y(v);grid+=`<line class="grid" x1="${l}" x2="${w-r}" y1="${yy}" y2="${yy}"/><text x="${w-r+8}" y="${yy+4}">${v.toFixed(2)}</text>`}
    for(let j=0;j<5;j++){const i=Math.round((data.length-1)*j/4);grid+=`<text x="${x(i)}" y="${h-7}" text-anchor="${j===4?'end':j===0?'start':'middle'}">${data[i].date.slice(5)}</text>`}
    const bars=data.map((d,i)=>{const c=d.close>=d.open?'var(--up)':'var(--down)';return `<line x1="${x(i)}" x2="${x(i)}" y1="${y(d.high)}" y2="${y(d.low)}" stroke="${c}"/><rect x="${x(i)-step*.29}" width="${step*.58}" y="${Math.min(y(d.open),y(d.close))}" height="${Math.max(1,Math.abs(y(d.open)-y(d.close)))}" fill="${c}"/><rect x="${x(i)-step*.3}" width="${step*.6}" y="${h-25-d.volume*.63}" height="${d.volume*.63}" fill="${c}" opacity=".45"/>`}).join('');
    const ma=(n,c)=>'<path d="'+data.map((d,i)=>(i?'L':'M')+x(i)+','+y(d['ma'+n])).join(' ')+'" stroke="'+c+'" stroke-width="1.25" fill="none"/>';
    svg.setAttribute('viewBox','0 0 '+w+' '+h);svg.innerHTML=`${grid}<text x="${l}" y="15">价格（元）</text><text x="${l}" y="299">成交量（示例）</text>${bars}${ma(5,'var(--color-compare-3)')}${ma(10,'var(--color-compare-1)')}${ma(20,'var(--color-compare-2)')}<line class="guide" y1="${t}" y2="${h-25}" stroke="var(--color-zero)" stroke-dasharray="3 4" visibility="hidden"/><rect x="${l}" y="${t}" width="${w-l-r}" height="${h-50}" fill="transparent"/>`;
    bind(wrap,svg,context,data,{w,left:x(0),right:x(data.length-1),x,y:p=>y(p.close),format:p=>'开 '+p.open.toFixed(2)+' · 收 '+p.close.toFixed(2)+' · 高 '+p.high.toFixed(2)+' · 低 '+p.low.toFixed(2)});
  });
  AT.drawSparks=()=>document.querySelectorAll('[data-spark]').forEach(svg=>{
    const context=read(svg),points=AT.stockDays(context.code).slice(-25),values=points.map(p=>p.close),min=Math.min(...values),max=Math.max(...values),x=i=>2+i/24*76,y=p=>25-(p.close-min)/(max-min)*22;
    const color=values.at(-1)>=values[0]?'var(--up)':'var(--down)';
    svg.innerHTML='<path d="'+points.map((p,i)=>(i?'L':'M')+x(i)+','+y(p)).join(' ')+'" fill="none" stroke="'+color+'" stroke-width="1.5"/><circle class="hover-point" r="2.5" fill="'+color+'" visibility="hidden"/>';
    bind(svg,svg,context,points,{w:80,left:2,right:78,x,y,format:p=>'收盘 '+p.close.toFixed(2)+' 元'});
  });
})();
