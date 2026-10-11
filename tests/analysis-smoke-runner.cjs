const {app,protocol,dialog,session}=require('electron');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const [directory,phase]=process.argv.slice(2);
if(!directory||!['write','read'].includes(phase))throw new Error('Missing analysis smoke arguments');
const root=path.resolve(__dirname,'..'),dataDirectory=path.join(directory,'workspace'),fixtureDirectory=path.join(directory,'研究日线'),guard=path.join(directory,'provider-fixture');
const errors=[],external=[];let win;
app.setPath('userData',path.join(directory,'electron-profile'));
protocol.registerSchemesAsPrivileged([{scheme:'atrade',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
dialog.showMessageBox=async(_window,options)=>{throw new Error(`Unexpected modal: ${options.message}`);};
const evaluate=code=>win.webContents.executeJavaScript(code).catch(error=>{throw new Error(`${error.message} [${code.slice(0,180)}]`);});
const wait=async(expression,label,timeout=18000)=>{const started=Date.now();while(Date.now()-started<timeout){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,50));}throw new Error(`${label} 超时: ${await evaluate('document.querySelector("main").innerText.slice(-2200)')}`);};
const click=selector=>evaluate(`(()=>{const el=[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length);if(!el||el.disabled)throw new Error('Missing active control: '+${JSON.stringify(selector)});if(typeof el.click==='function')el.click();else el.dispatchEvent(new MouseEvent('click',{bubbles:true}));})()`);
const fill=(group,key,value)=>evaluate(`(()=>{const el=document.querySelector('[data-analysis-group="${group}"][data-analysis-field="${key}"]');if(!el)throw new Error('Missing field');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const submit=type=>evaluate(`document.querySelector('[data-analysis-form="${type}"]').requestSubmit()`);
const navigate=async page=>{await evaluate(`AT.navigate(${JSON.stringify(page)});true`);await wait(`AT.state.page===${JSON.stringify(page)}&&!!document.querySelector('[data-analysis-page="${page}"]')`,`打开${page}`);};
async function screenshot(name){await fs.mkdir(path.join(root,'artifacts'),{recursive:true});await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});await new Promise(resolve=>setTimeout(resolve,130));await fs.writeFile(path.join(root,'artifacts',name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}

const providerFixture=String.raw`
import io,json,re,datetime,urllib.request,urllib.parse
from pathlib import Path
ROOT=Path(__file__).parent
class Response(io.BytesIO):
    def __init__(self,data):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        super().__init__(raw)
        self.headers={'Content-Length':str(len(raw))}
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
def reports(code):
    name={'600900':'POWER FIXTURE','600001':'MISSING CAP','000001':'BANK FIXTURE'}[code]
    profits={'600900':[200000000,300000000,100000000],'600001':[100000000,200000000,100000000],'000001':[-100000000,-200000000,-200000000]}[code]
    symbol=code+('.SZ' if code=='000001' else '.SH')
    return [{'SECUCODE':symbol,'SECURITY_CODE':code,'SECURITY_NAME_ABBR':name,'BOARD_NAME':'Banking' if code=='000001' else 'Utilities','REPORTDATE':date,'NOTICE_DATE':ann,'PARENT_NETPROFIT':profit} for date,ann,profit in zip(['2026-06-30','2025-12-31','2025-06-30'],['2026-08-25','2026-03-25','2025-08-25'],profits)]
def opened(self,request,*args,**kwargs):
    url=request.full_url if hasattr(request,'full_url') else request
    parts=urllib.parse.urlsplit(url);params=urllib.parse.parse_qs(parts.query)
    with ROOT.joinpath('calls.jsonl').open('a',encoding='utf-8') as stream: stream.write(json.dumps({'host':parts.hostname,'path':parts.path,'params':params})+'\n')
    if parts.hostname=='mobappconfig.securities.eastmoney.com':
        if ROOT.joinpath('regulatory-offline').exists(): raise urllib.error.URLError('offline fixture')
        return Response([{'MARKET':'1','STKCODE':'600900','STKNAME':'POWER FIXTURE','VALIDATESTARTDATE':'2026-10-08','VALIDATEENDDATE':'2026-10-12'}])
    if parts.hostname=='store.gf.com.cn':
        if ROOT.joinpath('regulatory-offline').exists(): raise urllib.error.URLError('offline fixture')
        return Response({'retcode':0,'msg':'succ','data':[{'stock_code':'600900','stock_name':'POWER FIXTURE','exchange_type_desc':'上海','key_monitor_stock_type':'交易所监管提示函','monitor_start_date':'2026-10-08','monitor_end_date':'2026-10-12','data_status':'有效'}, {'stock_code':'600001','stock_name':'MISSING CAP','exchange_type_desc':'上海','key_monitor_stock_type':'上海严重异常波动的股票','monitor_start_date':'2026-10-12','monitor_end_date':'2026-10-22','data_status':'有效'}, {'stock_code':'000001','stock_name':'ORDINARY EXCLUDED','exchange_type_desc':'深圳','key_monitor_stock_type':'普通异常波动的股票','monitor_start_date':'2026-10-08','monitor_end_date':'2026-10-12','data_status':'有效'}]})
    if parts.hostname=='datacenter-web.eastmoney.com':
        name=params.get('reportName',[''])[0]
        if name=='RPT_VALUEINDUSTRY_DET':
            query=params.get('filter',[''])[0]
            industry='800002' if '800002' in query else '800001'
            values=[{'BOARD_CODE':industry,'BOARD_NAME':'Banking' if industry=='800002' else 'Utilities','TRADE_DATE':(datetime.date(2026,9,30)-datetime.timedelta(days=i)).isoformat(),'PE_TTM':20+i/10} for i in range(250)]
            return Response({'success':True,'result':{'data':values,'count':250,'pages':1}})
        if name=='RPT_VALUEANALYSIS_DET':
            query=params.get('filter',[''])[0]
            codes=re.findall(r'"(\d{6})(?:\.[A-Z]{2})?"',query)
            complete_catalog=not any(key in query for key in ['BOARD_CODE','SECUCODE','SECURITY_CODE'])
            if complete_catalog: codes=['000001','600001','600900']
            if 'BOARD_CODE' in query: codes=['000001'] if '800002' in query else ['600900','600001','600002']
            values=[{'SECUCODE':code+('.SZ' if code=='000001' else '.SH'),'SECURITY_CODE':code,'SECURITY_NAME_ABBR':'PEER FIXTURE' if code=='600002' else reports(code)[0]['SECURITY_NAME_ABBR'],'BOARD_CODE':'800002' if code=='000001' else '800001','BOARD_NAME':'Banking' if code=='000001' else 'Utilities','TOTAL_MARKET_CAP':None if code=='600001' else 8000000000,'CLOSE_PRICE':20,'PE_TTM':-10 if code=='000001' else 35 if code=='600001' else 27.5 if code=='600002' else 20,'TRADE_DATE':'2026-09-30'} for code in codes]
            if not complete_catalog and 'TOTAL_MARKET_CAP' in params.get('columns',[''])[0]: values=[row for row in values if row['SECURITY_CODE']!='600001']
            total=len(values);size=int(params.get('pageSize',['500'])[0]);page=int(params.get('pageNumber',['1'])[0]);values=values[(page-1)*size:page*size]
            return Response({'success':True,'result':{'data':values,'count':total,'pages':(total+size-1)//size}})
        query=params.get('filter',[''])[0]
        match=re.search(r'SECUCODE="(\d{6})\.',query)
        rows=reports(match[1]) if match else [reports(code)[0] for code in ['600900','600001','000001']]
        if 'SECURITY_CODE in' in query:
            codes=re.findall(r'"(\d{6})"',query)
            dates=re.findall(r"'(\d{4}-\d{2}-\d{2})'",query)
            rows=[row for code in codes for row in reports(code) if row['REPORTDATE'] in dates]
        return Response({'success':True,'result':{'data':rows,'count':len(rows),'pages':1}})
    if parts.hostname=='push2his.eastmoney.com' and parts.path.endswith('/fflow/daykline/get'):
        code=params['secid'][0].split('.')[-1]
        days=[]
        day=datetime.date(2026,9,30)
        while len(days)<120:
            if day.weekday()<5: days.append(day.isoformat())
            day-=datetime.timedelta(days=1)
        rows=[]
        for i,day in enumerate(reversed(days)):
            value=(300000000 if i%3 else -100000000)*(1 if code=='BK0001' else -1)
            rows.append(','.join(map(str,[day,value,-value/2,-value/2,value*.3,value*.7,1,2,3,4,5,1000+i,2.5,'-','-'])))
        return Response({'rc':0,'data':{'code':code,'market':90,'name':'Fixture Utilities' if code=='BK0001' else 'Fixture Banks','klines':rows}})
    if parts.hostname=='push2.eastmoney.com' and parts.path.endswith('/stock/get'):
        code=params['secid'][0].split('.')[-1]
        return Response({'rc':0,'data':{'f57':code,'f58':reports(code)[0]['SECURITY_NAME_ABBR'],'f43':20,'f116':None if code=='600001' else 8000000000,'f164':20,'f124':1791531000}})
    if parts.hostname=='push2.eastmoney.com' and parts.path.endswith('/clist/get'):
        rows=[]
        for index in range(2):
            row={'f12':'BK000'+str(index+1),'f14':['Fixture Utilities','Fixture Banks'][index],'f124':1791531000}
            for key in ['f3','f109','f160']: row[key]=3-index*5
            for key in ['f184','f165','f175']: row[key]=10-index*20
            for key in ['f62','f66','f72','f78','f84']: row[key]=(300000000 if index==0 else -100000000)
            for key in ['f164','f166','f168','f170','f172']: row[key]=(800000000 if index==0 else -200000000)
            for key in ['f174','f176','f178','f180','f182']: row[key]=(-400000000 if index==0 else 100000000)
            rows.append(row)
        return Response({'rc':0,'data':{'total':2,'diff':rows}})
    ROOT.joinpath('unexpected-network').write_text(parts.hostname or '',encoding='utf-8')
    raise RuntimeError('Unexpected external endpoint blocked by analysis smoke')
urllib.request.OpenerDirector.open=opened
import service.regulatory_watch as regulatory_watch
regulatory_watch._today=lambda: '2026-10-11'
import service.valuation_context as valuation_context
valuation_context._now=lambda: datetime.datetime(2026,9,30,16,tzinfo=valuation_context.ZONE)
`;

async function fixture(){
  await fs.mkdir(path.join(fixtureDirectory,'T0002/hq_cache'),{recursive:true});
  const dates=[];
  for(let day=new Date('2026-05-01T00:00:00Z');day<=new Date('2026-09-30T00:00:00Z');day.setUTCDate(day.getUTCDate()+1)){if(day.getUTCDay()!==0&&day.getUTCDay()!==6)dates.push(day.toISOString().slice(0,10));}
  const entries={sh:[['600900','POWER FIXTURE'],['600001','MISSING CAP'],['881001','SECTOR ONE'],['881002','SECTOR TWO'],['881003','SECTOR THREE'],['881004','SECTOR FOUR'],['881005','SECTOR FIVE']],sz:[['000001','BANK FIXTURE']]};
  for(const [market,rows]of Object.entries(entries)){
    await fs.mkdir(path.join(fixtureDirectory,'vipdoc',market,'lday'),{recursive:true});
    const tnf=Buffer.alloc(50+rows.length*360);
    for(const [position,[code,name]]of rows.entries()){
      tnf.write(code,50+position*360,'ascii');tnf.write(name,50+position*360+31,'ascii');
      const bytes=Buffer.alloc(dates.length*32);let close=1000;
      for(let i=0;i<dates.length;i++){
        const previous=close;let rate=code==='000001'?-.001:.004;
        if(code==='600900'){if([dates.length-6,dates.length-5,dates.length-2].includes(i))rate=.10;else if(i===dates.length-4)rate=-.02;}
        if(code==='600001'&&i>=dates.length-3)rate=.10;
        close=Math.round(close*(1+rate));const at=i*32,volume=i>=dates.length-2?350000:100000;
        bytes.writeUInt32LE(Number(dates[i].replaceAll('-','')),at);
        bytes.writeUInt32LE(previous,at+4);bytes.writeUInt32LE(Math.max(previous,close)+1,at+8);bytes.writeUInt32LE(Math.min(previous,close)-1,at+12);bytes.writeUInt32LE(close,at+16);bytes.writeFloatLE(close/100*volume,at+20);bytes.writeUInt32LE(volume,at+24);
      }
      await fs.writeFile(path.join(fixtureDirectory,'vipdoc',market,'lday',market+code+'.day'),bytes);
    }
    await fs.writeFile(path.join(fixtureDirectory,'T0002/hq_cache',market+'s.tnf'),tnf);
  }
  await fs.writeFile(path.join(directory,'event-date'),dates.at(-2));
  const index=Buffer.alloc(32);index.writeUInt32LE(20260930,0);
  for(const offset of [4,8,12,16])index.writeUInt32LE(409000,offset);
  index.writeFloatLE(1e9,20);index.writeUInt32LE(1e6,24);
  await fs.writeFile(path.join(fixtureDirectory,'vipdoc/sh/lday/sh000001.day'),index);
}

app.whenReady().then(async()=>{
  if(phase==='write')await fixture();
  await fs.mkdir(guard,{recursive:true});await fs.writeFile(path.join(guard,'sitecustomize.py'),providerFixture,'utf8');process.env.PYTHONPATH=[guard,root].join(path.delimiter);
  session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(details,callback)=>{external.push(details.url);callback({cancel:true});});
  win=await require('../desktop/runtime.cjs').createDesktop({dataDirectory,show:false,onWindow(window){window.webContents.setBackgroundThrottling(false);window.webContents.on('console-message',(_event,details)=>{if(details.level==='error')errors.push(details.message);});window.webContents.on('preload-error',(_event,_file,error)=>errors.push(error.message));}});
  await evaluate('window.__analysisErrors=[];window.addEventListener("error",event=>__analysisErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__analysisErrors.push(String(event.reason)));');
  await wait('!!AT.analysisTools','分析工具加载');
  if(phase==='write'){
    const configured=await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`);assert.equal(configured.ok,true,configured.error);
    const scanned=await evaluate("atradeDesktop.market('market.scan')");assert.equal(scanned.ok,true,scanned.error);assert.equal(scanned.data.symbolCount,4);
    await evaluate('AT.marketUI.refresh()');
    await navigate('T01');await wait('document.querySelector(".analysis-tools-metrics").innerText.includes("2026-09-30")','本地研究概览');
    assert.doesNotMatch(await evaluate('document.querySelector("main").innerText'),/示例候选|合成样本|参数均为演示值/);await screenshot('analysis-overview.png');

    await navigate('T04');await wait('document.querySelectorAll(".analysis-tools-table tbody tr").length===2','趋势筛选2条');
    await fill('trends','minReturn20','1000');await submit('research');await wait('document.querySelector(".analysis-tools-empty")?.innerText.includes("没有符合")','趋势应用阈值');
    await fill('trends','minReturn20','0');await submit('research');await wait('document.querySelectorAll(".analysis-tools-table tbody tr").length===2','恢复趋势筛选');await screenshot('analysis-trends.png');
    await wait('document.querySelectorAll("[data-funds-activity-line]").length===2','龙头候选成交额走势');
    assert.equal(await evaluate('document.querySelector("[data-funds-activity-panel]").dataset.activityKind'),'stock');
    assert.match(await evaluate('document.querySelector("[data-funds-activity-panel]").innerText'),/不能表示主力净流入/);

    await navigate('T06');await wait('document.querySelectorAll("[data-regulatory-row]").length===3','监管来源名单');
    assert.doesNotMatch(await evaluate('document.querySelector("main").innerText'),/ORDINARY EXCLUDED|放量触发|涨跌幅触发/);
    await click('[data-regulatory-status="expiring"]');await wait('document.querySelectorAll("[data-regulatory-row]").length===2','临近期满记录');
    assert.match(await evaluate('document.querySelector("[data-regulatory-row]").innerText'),/1\s*个?交易日/);
    await click('[data-regulatory-status="all"]');
    await click('[data-regulatory-category="severe"]');await wait('document.querySelectorAll("[data-regulatory-row]").length===1','严重异动独立筛选');
    assert.match(await evaluate('document.querySelector("[data-regulatory-row]").innerText'),/待生效/);
    await click('[data-regulatory-category="all"]');await screenshot('analysis-regulatory.png');
    await fs.writeFile(path.join(guard,'regulatory-offline'),'offline');await click('[data-analysis-action="refresh"]');
    await wait('document.querySelector("[data-regulatory-panel]")?.innerText.includes("当前显示旧缓存")','监控源断线保留名单');
    assert.equal(await evaluate('document.querySelectorAll("[data-regulatory-row]").length'),3);
    assert.match(await evaluate('document.querySelector("[data-regulatory-row]").innerText'),/该来源旧缓存/);
    await fs.unlink(path.join(guard,'regulatory-offline'));
    await navigate('T07');await wait('!!document.querySelector("[data-analysis-stock-day]")','历史断板事件');
    const eventDate=(await fs.readFile(path.join(directory,'event-date'),'utf8')).trim();assert.equal(await evaluate('document.querySelector("[data-analysis-stock-day]").dataset.analysisStockDay'),eventDate);await screenshot('analysis-rebounds.png');
    await click('[data-analysis-stock-day]');await wait(`AT.state.page==='G01'&&document.querySelector('[data-market-date]')?.value===${JSON.stringify(eventDate)}`,'事件日K定位');
    await navigate('T08');await wait('document.querySelectorAll(".leader-research-candidate").length>0','当前最高板候选');
    assert.ok(await evaluate('document.querySelectorAll(".analysis-tools-ladder-row").length>0'));
    assert.ok(await evaluate('document.querySelectorAll("[data-leader-result]").length>0'),'保留昨日高标今日表现');
    assert.equal(await evaluate('document.querySelectorAll("[data-leader-line]").length'),4,'接力与趋势曲线共用日期');
    const leaderCount=Number(await evaluate('document.querySelector("[data-leader-chart]").dataset.leaderCount'));
    await click('[data-leader-zoom="in"]');assert.ok(Number(await evaluate('document.querySelector("[data-leader-chart]").dataset.leaderCount'))<leaderCount);
    await click('[data-leader-zoom="reset"]');assert.equal(Number(await evaluate('document.querySelector("[data-leader-chart]").dataset.leaderCount')),leaderCount);
    await click('[data-analysis-scope="ladder"][data-analysis-chip="board"][data-analysis-value="chinext"]');
    await wait('document.querySelector(".leader-research-candidates")?.innerText.includes("没有符合")','市场筛选空结果');
    await click('[data-analysis-scope="ladder"][data-analysis-chip="board"][data-analysis-value="all"]');
    await wait('document.querySelectorAll(".leader-research-candidate").length>0','恢复连板候选');await screenshot('analysis-ladder.png');
    await click(`[data-leader-day-target][data-analysis-day="${eventDate}"]`);await wait(`document.querySelector('.leader-research-leaders')&&document.querySelector('main').innerText.includes(${JSON.stringify(eventDate+' · 点击证券')})`,'历史梯队切日');

    await navigate('T02');await fill('financeInput','symbol','POWER');
    await wait('!!document.querySelector("[data-finance-candidate=sh600900]")','股票名称联想');
    assert.equal(await evaluate('document.querySelectorAll(".overlay,.modal").length'),0,'页内搜索无需弹窗');
    await click('[data-finance-candidate="sh600900"]');await wait('[...document.querySelectorAll(".analysis-tools-facts")].some(el=>el.innerText.includes("4.00 亿"))','选择股票后自动读取TTM');
    await wait('document.querySelector(".analysis-tools-benchmark-values")?.innerText.includes("27.50")','行业同日PE中位数');
    await wait('document.querySelector(".valuation-insight-verdict")?.innerText.includes("低于多数同行")','读取后自动给同行结论');
    assert.match(await evaluate('document.querySelector(".valuation-insight-metrics").innerText'),/27\.27/);
    await wait('document.querySelector(".valuation-insight-water")?.innerText.includes("4,090")','真实指数日线自动计算水位');
    assert.match(await evaluate('document.querySelector(".valuation-insight-water").innerText'),/1\.36/);
    assert.equal(await evaluate('document.querySelectorAll(\'[data-analysis-group="scenario"]\').length'),0,'默认直接分析无需填写参数');
    assert.match(await evaluate('document.querySelector(".analysis-tools-historical-pe").innerText'),/250 个交易日/);
    await screenshot('analysis-valuation-auto.png');
    await evaluate('document.querySelector(".analysis-tools-benchmark").scrollIntoView({block:"start",behavior:"instant"})');await screenshot('analysis-industry-pe.png');
    await click('[data-analysis-action="valuation-tab"][data-valuation-tab="scenario"]');
    assert.equal(await evaluate('document.querySelector(\'[data-analysis-group="scenario"][data-analysis-field="pe"]\').value'),'','自动结论不覆盖手动PE');
    await click('[data-analysis-action="benchmark-adopt"][data-benchmark-key="medianPE"]');
    assert.equal(await evaluate('document.querySelector(\'[data-analysis-group="scenario"][data-analysis-field="pe"]\').value'),'27.5');
    await fill('scenario','growth','1.2');await fill('scenario','pe','15');await fill('scenario','emotion','1.1');await wait('document.querySelector("[data-analysis-valuation-result]").innerText.includes("79.20 亿")','估值乘数单位一致');
    assert.match(await evaluate('document.querySelector("[data-analysis-valuation-result]").innerText'),/72.00 亿/);await screenshot('analysis-valuation.png');
    assert.match(await evaluate('document.querySelector(".valuation-insight-verdict").innerText'),/低于多数同行/,'手动参数不改变同日PE自动结论');
    await click('[data-analysis-action="water-adopt"]');
    assert.equal(await evaluate('document.querySelector(\'[data-analysis-group="scenario"][data-analysis-field="water"]\').value'),'1.3633','可显式采用真实上证水位');
    await fill('scenario','water','1');await wait('document.querySelector("[data-analysis-valuation-result]").innerText.includes("79.20 亿")','手动水位可独立修改');
    await fill('financeInput','symbol','600001');await submit('finance');await wait('document.querySelector("[data-analysis-valuation-result]").innerText.includes("实际市值缺失")','市值缺失不造隐含情绪');await screenshot('analysis-valuation-missing.png');
    await fill('financeInput','symbol','000001');await submit('finance');await wait('document.querySelector("[data-analysis-valuation-result]").innerText.includes("此乘数模型不适用")','亏损不出误估值');
    await navigate('T03');await wait('document.querySelectorAll(".analysis-tools-table tbody tr").length===2','真实财务筛选排除亏损');
    assert.match(await evaluate('document.querySelector(".analysis-tools-table").innerText'),/2026-09-30/,'市值来源日可见');
    assert.equal(await evaluate('[...document.querySelectorAll(".analysis-tools-table tbody tr")].find(row=>row.innerText.includes("POWER FIXTURE")).querySelector(".analysis-tools-col-actualPe").textContent.trim()'),'20.00');
    await fill('scenario','pe','25');await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("+50.00%")','真实市值参与情景底座偏离');
    await click('[data-analysis-chip="below"]');await wait('document.querySelectorAll(".analysis-tools-table tbody tr").length===1','跨行业低于情景底座筛选');await screenshot('analysis-screen.png');
    await click('[data-analysis-chip="below"]');
    await fill('screen','industry','Banking');await submit('screen');await wait('document.querySelector(".analysis-tools-empty")?.innerText.includes("没有符合")','行业筛选亏损排除');
    await click('[data-analysis-chip="positive"]');await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("BANK FIXTURE")','亏损可见但PE缺失');
    assert.equal(await evaluate('document.querySelector(".analysis-tools-col-actualPe:not(th)").textContent.trim()'),'—');
    await navigate('T05');
    await wait('document.querySelectorAll("[data-funds-activity-line]").length===5','本地板块成交额首屏');
    assert.equal(await evaluate('document.querySelector("[data-funds-activity-chart]").dataset.activityTotal'),'60');
    assert.equal(await evaluate('document.querySelectorAll("[data-analysis-flow-chart]").length'),0,'独立展示成交额与净流入口径');
    await click('[data-funds-activity-field="amount"]');assert.match(await evaluate('document.querySelector("[data-funds-activity-reading]").innerText'),/亿/);
    await click('[data-funds-activity-field="returnPercent"]');
    const base=await evaluate('document.querySelector("[data-funds-activity-chart]").dataset.activityBase');assert.ok(base);
    await evaluate(`(()=>{const el=document.querySelector('[data-funds-activity-chart]'),rect=el.getBoundingClientRect();el.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:rect.left+rect.width*.8,deltaY:-100}));})()`);
    assert.equal(await evaluate('document.querySelector("[data-funds-activity-chart]").dataset.activityCount'),'48');
    assert.equal(await evaluate('document.querySelector("[data-funds-activity-chart]").dataset.activityBase'),base);
    await click('[data-funds-activity-remove="sh881001"]');assert.equal(await evaluate('document.querySelectorAll("[data-funds-activity-line]").length'),4);
    await click('[data-funds-activity-toggle="sh881001"]');await wait('document.querySelectorAll("[data-funds-activity-line]").length===5','加入板块同图');
    await click('[data-funds-activity-period="750"]');await wait('document.querySelectorAll("[data-funds-activity-line]").length===5','历史不足保留已有日线');
    assert.ok(Number(await evaluate('document.querySelector("[data-funds-activity-chart]").dataset.activityTotal'))<750);
    await screenshot('analysis-funds-activity.png');
    await click('[data-analysis-chip="mode"][data-analysis-value="net"]');await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("3.00 亿")','当日资金');
    await wait('document.querySelector("[data-analysis-flow-chart]")?.dataset.count==="60"','逐日资金历史');
    await click('[data-analysis-sort="mainNetInflow"]');assert.match(await evaluate('document.querySelector(".analysis-tools-table tbody tr").innerText'),/Fixture Banks/);
    await click('[data-analysis-sort="mainNetInflow"]');assert.match(await evaluate('document.querySelector(".analysis-tools-table tbody tr").innerText'),/Fixture Utilities/);
    await click('[data-analysis-sort="mainNetInflow"]');assert.match(await evaluate('document.querySelector(".analysis-tools-sort-hint").innerText'),/当前为默认顺序/);
    await click('[data-analysis-action="funds-zoom-in"]');assert.equal(await evaluate('document.querySelector("[data-analysis-flow-chart]").dataset.count'),'45');
    await evaluate(`(()=>{const el=document.querySelector('[data-analysis-flow-chart]'),rect=el.getBoundingClientRect();el.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:rect.left+rect.width*.8,deltaY:-100}));})()`);
    assert.equal(await evaluate('document.querySelector("[data-analysis-flow-chart]").dataset.count'),'36');
    const start=await evaluate('Number(document.querySelector("[data-analysis-flow-chart]").dataset.start)');
    await evaluate(`(()=>{const el=document.querySelector('[data-analysis-flow-chart]'),rect=el.getBoundingClientRect();el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true,pointerId:41,button:2,buttons:2,clientX:rect.left+rect.width*.5}));document.documentElement.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,cancelable:true,pointerId:41,buttons:2,clientX:rect.left+rect.width*.7}));document.documentElement.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,cancelable:true,pointerId:41,button:2}));})()`);
    assert.ok(await evaluate('Number(document.querySelector("[data-analysis-flow-chart]").dataset.start)')<start,'右键拖动平移时间轴');
    await click('[data-analysis-action="funds-reset"]');assert.equal(await evaluate('document.querySelector("[data-analysis-flow-chart]").dataset.count'),'60');
    await click('[data-analysis-chip="limit"][data-analysis-value="250"]');await wait('document.querySelector("[data-analysis-flow-chart]")?.dataset.total==="120"','资金历史真实上限');
    assert.match(await evaluate('document.querySelector(".analysis-tools-funds-history").innerText'),/来源实际提供 120 日/);
    await click('[data-analysis-action="funds-select"][data-fund-id="BK0002"]');await wait('document.querySelector(".analysis-tools-funds-history h2").innerText.includes("Fixture Banks")&&document.querySelector("[data-analysis-flow-chart]")?.dataset.total==="120"','选择另一板块历史');
    await click('[data-analysis-chip="field"][data-analysis-value="largeNetInflow"]');assert.match(await evaluate('document.querySelector(".analysis-tools-flow-readout").innerText'),/大单当日净额/);await screenshot('analysis-funds-history.png');
    await click('[data-analysis-chip="period"][data-analysis-value="5d"]');await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("8.00 亿")','5日资金');await screenshot('analysis-funds.png');
    await click('[data-analysis-chip="period"][data-analysis-value="10d"]');await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("-4.00 亿")','10日资金');

    await navigate('T09');assert.equal(await evaluate('document.querySelectorAll("[data-watch-editor]").length'),0,'跟踪列表优先');await click('[data-analysis-action="watch-open"]');await fill('watch','symbol','600900');await fill('watch','note','跟踪现金流，等待业绩验证');await fill('watch','tags','公用事业，现金流');await click('[data-analysis-chip="status"][data-analysis-scope="watch"][data-analysis-value="planned"]');await submit('watch');
    await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("跟踪现金流，等待业绩验证")','观察保存');
    await click('[data-analysis-action="watch-edit"]');await fill('watch','note','跨重启保留：业绩核查');await click('[data-analysis-chip="status"][data-analysis-scope="watch"][data-analysis-value="holding"]');await submit('watch');await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("跨重启保留：业绩核查")','观察编辑');
    await click('[data-analysis-action="watch-open"]');await fill('watch','symbol','600001');await fill('watch','note','临时观察');await submit('watch');await wait('document.querySelectorAll(".analysis-tools-table tbody tr").length===2','第二条观察');
    await click('[data-analysis-action="watch-delete"][data-symbol="sh600001"]');assert.equal(await evaluate('document.querySelectorAll(".overlay,.modal").length'),0);await click('[data-analysis-action="watch-delete-confirm"]');await wait('document.querySelectorAll(".analysis-tools-table tbody tr").length===1','内联确认移除');await screenshot('analysis-watchlist.png');
    win.setSize(900,900);await new Promise(resolve=>setTimeout(resolve,120));
    for(const page of ['T02','T04','T05','T08','T09']){await navigate(page);await new Promise(resolve=>setTimeout(resolve,80));const sizes=await evaluate('({scroll:document.querySelector("main").scrollWidth,client:document.querySelector("main").clientWidth})');assert.ok(sizes.scroll<=sizes.client+1,`${page} horizontal overflow: ${JSON.stringify(sizes)}`);}
    await screenshot('analysis-watchlist-narrow.png');
  }else{
    await navigate('T09');await wait('document.querySelector(".analysis-tools-table")?.innerText.includes("跨重启保留：业绩核查")','重启恢复观察记录');
    const records=await evaluate("atradeDesktop.market('market.watchlist.list')");assert.equal(records.ok,true);assert.equal(records.data.items.length,1);assert.equal(records.data.items[0].symbol,'sh600900');assert.equal(records.data.items[0].status,'holding');assert.equal(records.data.items[0].note,'跨重启保留：业绩核查');assert.deepEqual(records.data.items[0].tags,['公用事业','现金流']);await screenshot('analysis-restarted.png');
  }
  assert.deepEqual(external,[],'renderer cannot request public network');assert.equal(await fs.access(path.join(guard,'unexpected-network')).then(()=>true,()=>false),false,'only provider-fixture endpoints requested');
  assert.deepEqual(errors,[]);assert.deepEqual(await evaluate('__analysisErrors'),[]);assert.equal(await evaluate('AT.analysisTools.flush()'),true);assert.equal(await evaluate('AT.persist()'),true);
  console.log(`PASS 分析工具桌面 ${phase}`);win.once('closed',()=>app.exit(0));await click('[data-window-action="close"]');
}).catch(async error=>{console.error(error.stack);if(win&&!win.isDestroyed()){try{await screenshot('analysis-failure.png');}catch{}win.destroy();}app.exit(1);});
