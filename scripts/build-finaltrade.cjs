const fs=require('node:fs'),path=require('node:path'),{pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'..'),frontend=path.join(root,'vendor/finaltrade/frontend');
(async()=>{
 const dependencies=fs.existsSync(path.join(frontend,'node_modules/vite'))?frontend:path.resolve(process.argv[2]||'G:/CODE/Final trade/frontend');
 const {build}=await import(pathToFileURL(path.join(dependencies,'node_modules/vite/dist/node/index.js')).href);
 const {default:react}=await import(pathToFileURL(path.join(dependencies,'node_modules/@vitejs/plugin-react/dist/index.js')).href);
 const alias=[{find:'@/app/App',replacement:path.join(root,'desktop/finaltrade-app.tsx')},{find:'@/app/providers',replacement:path.join(root,'desktop/research-providers.tsx')},{find:'@/shared/components/PageHeader',replacement:path.join(root,'desktop/research-page-header.tsx')},{find:'@/shared/api/client',replacement:path.join(root,'desktop/research-api-client.ts')},{find:'@',replacement:path.join(frontend,'src')}];
 const pinnedDependencies={name:'pinned-reference-dependencies',enforce:'pre',async resolveId(id,importer){if(!importer||id.startsWith('.')||id.startsWith('/')||id.startsWith('@/')||id.includes(':')||id.startsWith('\0')||id.startsWith('vite/')||importer.replaceAll('\\','/').includes('/node_modules/'))return null;return this.resolve(id,path.join(dependencies,'src/main.tsx'),{skipSelf:true});}};
 const typography={name:'reference-typography',enforce:'pre',resolveId(source,importer){if(['./Ellipsis','./Ellipsis.js'].includes(source)&&/\/node_modules\/antd\/(es|lib)\/typography\/Base\/index(?:\.js|\.mjs)?$/.test((importer||'').replaceAll('\\','/')))return path.join(frontend,'src/patches/antdEllipsisMeasure.tsx');}};
 const offlineFonts={name:'desktop-fonts',enforce:'pre',transform(code,id){if(id.endsWith('.css'))return {code:code.replace(/@import\s+url\([^;]*fonts\.googleapis\.com[^;]*;\s*/g,''),map:null};}};
 const notices={name:'bundled-library-notices',generateBundle(){
  const packages=new Map();
  for(const id of this.getModuleIds()){
   if(id.includes('\0'))continue;
   const match=/^(.*\/node_modules\/)((?:@[^/]+\/)?[^/]+)/.exec(id.replaceAll('\\','/'));if(!match)continue;
   const directory=match[1]+match[2];if(packages.has(directory))continue;
   try{const pkg=JSON.parse(fs.readFileSync(path.join(directory,'package.json'),'utf8'));
    const texts=fs.readdirSync(directory).filter(name=>/^(licen[cs]e|copying|notice)(\.|$)/i.test(name)&&fs.statSync(path.join(directory,name)).isFile()).sort().map(name=>`${name}\n${fs.readFileSync(path.join(directory,name),'utf8')}`);
    packages.set(directory,{name:pkg.name,version:pkg.version,text:`${pkg.name}@${pkg.version}\nLicense: ${typeof pkg.license==='string'?pkg.license:'See package notices'}\n${texts.join('\n\n')}`});
   }catch(error){throw Error('Cannot collect bundled library notice: '+directory+': '+error.message);}
  }
  const source=[...packages.values()].sort((a,b)=>(a.name+'@'+a.version).localeCompare(b.name+'@'+b.version)).map(pkg=>pkg.text).join('\n\n'+'='.repeat(72)+'\n\n');
  this.emitFile({type:'asset',fileName:'THIRD-PARTY-NOTICES.txt',source:'A-trade bundled frontend library notices\n\n'+source+'\n'});
 }};
 const integration={name:'project-workflow',enforce:'pre',transform(code,id){
  code=code.replaceAll('\r\n','\n');
  if(id.replaceAll('\\','/').endsWith('/pages/screener/ScreenerPage.tsx')){
   const first=code.indexOf('      <Alert\n        type={runMeta?.runId'),last=code.indexOf('      <Card className="glass-card"',first);
   if(first<0||last<0)throw Error('Screener status component changed; review the adaptation');
   code=code.slice(0,first)+`      <Alert type={runMeta?.degraded ? 'warning' : 'info'} showIcon title={runMeta?.runId ? '筛选结果已载入' : '先加载行情，再逐步筛选'} description={runMeta?.runId ? (runMeta.degraded ? '部分股票的基础数据不完整，请结合表格中的来源状态核对。' : '结果已与策略运行和回测共用，可以继续筛选或使用当前股票池。') : '点击加载输入池，然后按第 1～4 步运行；每一步旁边的设置按钮可以调整条件。'} />\n\n`+code.slice(last);
   code+=`\nwindow.addEventListener('atrade-market-source-changed',()=>{memoryScreenerCache=null;try{const raw=JSON.parse(localStorage.getItem(SCREENER_CACHE_KEY)||'null');if(raw){raw.pools=emptyStepPools;raw.raw_input_pool=[];raw.run_meta=null;raw.executed_step=0;raw.active_pool='input';raw.input_pool_key='';localStorage.setItem(SCREENER_CACHE_KEY,JSON.stringify(raw));}localStorage.removeItem(SIGNAL_RUN_CACHE_KEY);const form=JSON.parse(localStorage.getItem(BACKTEST_FORM_CACHE_KEY)||'null');if(form){form.run_id='';localStorage.setItem(BACKTEST_FORM_CACHE_KEY,JSON.stringify(form));}}catch{}})\n`;
  }
  if(id.replaceAll('\\','/').endsWith('/pages/backtest/BacktestPage.tsx')){
   code=`import { useLocation as useProjectLocation } from 'react-router-dom'\nimport { desktopContext } from '${path.join(root,'desktop/research-bridge.ts').replaceAll('\\','/')}'\n`+code;
   code=code.replace('  initialCapital: 1_000_000,','  get initialCapital(){return desktopContext.initialCapital ?? 1_000_000},').replace('  feeBps: 10,','  get feeBps(){return desktopContext.fees?.commissionRate ?? 10},');
   code=code.replace('    date_from: dateFrom,','    date_from: desktopContext.researchDates?.dateFrom ?? dateFrom,').replace('    date_to: dateTo,','    date_to: desktopContext.researchDates?.dateTo ?? dateTo,');
   code=code.replace('Number(value || TRADE_BACKTEST_DEFAULTS.feeBps)','Number(value ?? TRADE_BACKTEST_DEFAULTS.feeBps)').replace('Number(value || TRADE_BACKTEST_DEFAULTS.minScore)','Number(value ?? TRADE_BACKTEST_DEFAULTS.minScore)');
   code=code.replaceAll("app_name: 'Final Trade'","app_name: 'A-trade'");
   code=code.replaceAll('旧路径','逐日模拟').replaceAll("{ label: '矩阵路径', value: 'matrix' }","{ label: '矩阵加速', value: 'matrix' }");
   code=code.replace("'执行路径（当前策略固定逐日模拟）' : `执行路径（默认 ${defaultExecutionPathPreference}）`","'计算方式（逐日模拟）' : '计算方式'");
   code=code.replace(/`id=\$\{selectedStrategy\.strategy_id\}[^`]+`/,"'参数与策略中心共用，结果保留所用配置。'");
   const reportEffect=`  const requestedProjectReport=new URLSearchParams(useProjectLocation().search).get('report')\n  useEffect(()=>{if(requestedProjectReport){setSelectedReportId(requestedProjectReport);loadReportMutation.mutate(requestedProjectReport)}},[requestedProjectReport,loadReportMutation.mutate])\n\n`;
   if(!code.includes('  const deleteReportMutation = useMutation('))throw Error('Report loader changed; review the adaptation');
   code=code.replace('  const deleteReportMutation = useMutation(',reportEffect+'  const deleteReportMutation = useMutation(');
  }
  if(id.replaceAll('\\','/').endsWith('/pages/chart/ChartPage.tsx')){
   code=`import { notifyDesktop } from '${path.join(root,'desktop/research-bridge.ts').replaceAll('\\','/')}'\n`+code;
   code=code.replace('analyzeMutation.mutate()','notifyDesktop(\'ai-open\',{question:\'请根据当前股票的日线、量价和策略信号，分析其结构与风险。\'})');
   code=code.replace('        {intradayQuery.data?.degraded ? (',`        {intradayQuery.isPending ? <p role="status">正在读取真实分时数据…</p> : null}\n        {intradayQuery.error ? <Alert type="warning" showIcon title="该日期没有可用分时" description={(intradayQuery.error as Error).message} /> : null}\n        {intradayQuery.data?.degraded ? (`).replace('title="分时图包含近似数据"','title="部分分时字段暂缺"');
  }
  if(/\/pages\/(screener\/ScreenerPage|signals\/SignalsPage)\.tsx$/.test(id.replaceAll('\\','/'))){
   code=code.replaceAll('加入待买单','加入交易计划').replaceAll('已加入待买单：','已加入交易计划：').replaceAll('待买单已更新：','待确认计划已更新：').replaceAll('去交易页','填写交易计划');
  }
  return {code,map:null};
 }};
 const output=path.join(root,'preview/finaltrade');
 await build({root:frontend,configFile:false,plugins:[pinnedDependencies,typography,offlineFonts,integration,notices,react()],resolve:{alias},define:{'import.meta.env.VITE_ENABLE_MSW':'"false"'},build:{outDir:output,emptyOutDir:false,sourcemap:false,manifest:true}});
 // A running editor can still import its previous build's lazy chunks.
 // Clean generated assets only during an explicitly requested offline build.
 if(process.env.ATRADE_CLEAN_STRATEGY_ASSETS!=='1')return;
 const manifest=JSON.parse(fs.readFileSync(path.join(output,'.vite/manifest.json'),'utf8')),keep=new Set();
 const collect=value=>{if(typeof value==='string'&&value.startsWith('assets/'))keep.add(value);else if(value&&typeof value==='object')Object.values(value).forEach(collect);};collect(manifest);
 const assets=path.resolve(output,'assets');
 for(const entry of fs.readdirSync(assets,{withFileTypes:true})){
  if(!entry.isFile()||!['.js','.css'].includes(path.extname(entry.name))||keep.has('assets/'+entry.name))continue;
  const target=path.resolve(assets,entry.name),relative=path.relative(assets,target);
  if(relative.startsWith('..')||path.isAbsolute(relative))throw Error('Generated asset escaped the build directory');
  fs.unlinkSync(target);
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
