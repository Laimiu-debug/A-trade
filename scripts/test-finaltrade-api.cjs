const {FinaltradeService}=require('../desktop/finaltrade-service.cjs');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-reference-api-'));
const service=new FinaltradeService(directory);
(async()=>{
 try{
  await service.start();
  assert.equal((await fetch(service.url+'/api/strategies')).status,403,'direct local port requires the private handshake');
  assert.equal((await fetch(service.url+'/assets/index.js')).status,403,'assets also require the handshake');
  const request=new Request('atrade://reference/api/strategies');
  const catalogue=await (await service.proxy(request)).json();assert.equal(catalogue.items.length,9);
  for(const sid of ['wujiang_mainline_v1','mainline_halfway_v1','first_to_second_v1','highboard_sniper_v1','ruhan_halfway_v1','strong_trend_dip_v1']){assert.ok(catalogue.items.some(i=>i.strategy_id===sid),'missing '+sid);}
  const masterPlan=await service.proxy(new Request('atrade://reference/api/atrade/master-plan',{method:'POST',headers:{'Content-Type':'application/json',Origin:'atrade://reference'},body:JSON.stringify({strategy_id:'wujiang_mainline_v1',mode:'full_market'})}));
  assert.equal(masterPlan.status,200,'master-plan endpoint generates fallback rehearsal plan cleanly');
  const planBody=await masterPlan.json();assert.equal(planBody.styleKey,'wujiang_dynamic');assert.ok(Array.isArray(planBody.timelineChecklist)&&planBody.timelineChecklist.length>0);
  const params={markets:['sh','sz'],mode:'strict',return_window_days:40,top_n:500,turnover_threshold:.05,amount_threshold:5e8,amplitude_threshold:.03};
  const response=await service.proxy(new Request('atrade://reference/api/screener/run',{method:'POST',headers:{'Content-Type':'application/json',Origin:'atrade://reference'},body:JSON.stringify(params)}));
  assert.equal(response.status,404,'no real market cannot return the source demo stocks');
  assert.equal((await response.json()).code,'MARKET_DATA_UNAVAILABLE');
  const signals=await service.proxy(new Request('atrade://reference/api/signals?mode=full_market'));
  assert.ok([400,404].includes(signals.status),'full-market signals also reject missing real data');
  assert.ok((await signals.json()).message.includes('通达信日线'));
  const config=await (await service.proxy(new Request('atrade://reference/api/config'))).json();
  assert.equal(config.market_data_source,'tdx_only');assert.equal(config.api_key,'');
  assert.ok(config.tdx_data_path.startsWith(directory));
  assert.ok(fs.existsSync(path.join(directory,'finaltrade','app-state.json')));
  const independentConfig=await service.proxy(new Request('atrade://reference/api/config',{method:'POST',headers:{'Content-Type':'application/json',Origin:'atrade://reference'},body:JSON.stringify(config)}));
  assert.equal(independentConfig.status,403);assert.equal((await independentConfig.json()).code,'PROJECT_SETTINGS_ONLY');
  require('node:child_process').execFileSync(require('../desktop/business-service.cjs').pythonCommand(),['-c','import sqlite3,json,sys; db=sqlite3.connect(sys.argv[1]); db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)"); db.execute("INSERT INTO meta VALUES(?,?)",("path",json.dumps(sys.argv[2]))); db.commit(); db.close()',path.join(directory,'market.sqlite3'),path.join(directory,'changed-source')],{windowsHide:true});
  const changed=await service.proxy(new Request('atrade://reference/api/strategies'));
  assert.equal(changed.status,200);assert.equal((await changed.json()).items.length,9);
  const synced=await (await service.proxy(new Request('atrade://reference/api/config'))).json();assert.equal(synced.tdx_data_path,path.join(directory,'changed-source'));
  const status=await (await service.proxy(new Request('atrade://reference/api/atrade/status'))).json();assert.equal(status.available,false);assert.equal(status.source_changed,false);
  console.log('PASS private API, 9 unified strategies, master-plan endpoint, no-data rejection, isolated configuration and source synchronization without restarting.');
 }finally{service.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
