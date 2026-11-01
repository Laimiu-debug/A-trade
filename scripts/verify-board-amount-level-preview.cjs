// Minimal QA for the independent amount Sources; no core presentation changes.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process'),{pathToFileURL}=require('node:url');
const {chromium}=require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'../artifacts/strategy-search/board-environment-context-v1');
const python='C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const wrapper='C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const argsv=process.argv.slice(2);if(argsv.length%2)throw Error('Expected --receipt-sha SHA [--attempt 0001]');
const args=Object.fromEntries(Array.from({length:argsv.length/2},(_,i)=>[argsv[2*i],argsv[2*i+1]]));
if(Object.keys(args).some(k=>!['--receipt-sha','--attempt'].includes(k))||!/^[a-f0-9]{64}$/.test(args['--receipt-sha']||''))throw Error('Frozen receipt SHA required');
const attempt=args['--attempt']||'0001';if(!/^\d{4}$/.test(attempt))throw Error('Four-digit attempt required');
const out=path.join(root,'amount-browser-qa','attempt-'+attempt),sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8')),check=(x,m)=>{if(!x)throw Error(m);};
const save=(name,x)=>fs.writeFileSync(path.join(out,name),JSON.stringify(x,null,2)+'\n',{flag:'wx'});let started=false;
(async()=>{
 fs.mkdirSync(path.dirname(out),{recursive:true});fs.mkdirSync(out);started=true;
 const receiptPath=path.join(root,'amount-render-receipt.json'),receipt=read(receiptPath),renderReceiptSha256=sha(receiptPath),scriptSha256=sha(__filename);
 check(renderReceiptSha256===args['--receipt-sha'],'Receipt changed');
 const fragment=receipt.fragments.sources,payloadRef=receipt.payloads.sources,payload=read(path.join(root,payloadRef.path));
 check(sha(path.join(root,fragment.path))===fragment.sha256&&sha(path.join(root,payloadRef.path))===payloadRef.sha256,'Binding mismatch');
 check(JSON.stringify(receipt.tableRows)==='[[6,3]]'&&payload.items.length===1&&JSON.stringify(payload.items[0].queries.map(q=>q.rows.length))==='[6,3]','Complete amount evidence required');
 const corePath=path.join(root,'render-receipt.json'),coreSha=sha(corePath),core=read(corePath);
 const coreFiles=Object.values(core.fragments).map(ref=>({path:ref.path,sha256:sha(path.join(root,ref.path))}));
 const result=spawnSync(python,['-X','utf8','-B',wrapper,path.join(root,fragment.path),path.join(out,'sources-preview.html')],{encoding:'utf8',timeout:30000});
 save('wrapper-log.json',{status:result.status,stdout:result.stdout,stderr:result.stderr});check(result.status===0,'Wrapper failed');
 const browser=await chromium.launch({headless:true,channel:'msedge'}),images=[],tables=[],errors=[];
 try{
  const page=await browser.newPage({viewport:{width:360,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(10000);
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  const capture=async(target,name)=>{await target.evaluate(e=>e.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));await page.waitForTimeout(300);const targetBounds=await target.boundingBox();check(targetBounds&&targetBounds.height>0&&targetBounds.y<1000&&targetBounds.y+targetBounds.height>0,'Target outside screenshot');const file=path.join(out,name+'.png');await page.screenshot({path:file,fullPage:false});const ref={path:path.relative(root,file).replaceAll('\\','/'),sha256:sha(file),capture:'page-viewport',targetBounds};images.push(ref);return ref;};
  await page.goto(pathToFileURL(path.join(out,'sources-preview.html')).href);
  let frame=page.frameLocator('iframe'),receiptRoot=frame.locator('.sources-receipt');await receiptRoot.waitFor({timeout:15000});await receiptRoot.locator('.receipt-disclosure').click();
  let card=receiptRoot.locator('.receipt-card');check(await card.count()===1&&await card.getAttribute('data-open')==='true','Single Sources not open');
  await card.getByRole('tab',{name:'Overview',exact:true}).click();let panel=card.getByRole('tabpanel');
  const caveats=panel.locator('.receipt-qualification-list li');check(await caveats.count()>=6,'Required limitations absent');
  check((await panel.innerText()).includes('484')&&(await panel.innerText()).includes('事后')&&(await panel.innerText()).includes('2025-12-30'),'Clock/basket limitations absent');
  await capture(caveats.last(),'amount-caveat-360-light');
  await page.setViewportSize({width:1280,height:1000});await card.getByRole('tab',{name:'Data preview',exact:true}).click();panel=card.getByRole('tabpanel');
  check(await panel.locator('table').count()===2,'Two preview tables required');
  for(let i=0;i<2;i++){
   const query=payload.items[0].queries[i],table=panel.locator('table').nth(i),fields=i===0?['board','interval']:['board'];
   check(await table.locator('tbody tr').count()===query.rows.length,'Preview row count differs');
   const cols=fields.map(k=>query.columns.findIndex(c=>c.field===k));
   const actual=await table.locator('tbody tr').evaluateAll((rs,is)=>rs.map(r=>is.map(i=>r.querySelectorAll('td')[i].innerText.trim())),cols);
   check(JSON.stringify(actual)===JSON.stringify(query.rows.map(r=>fields.map(k=>String(r[k])))),'Complete identities differ');
   check(await table.locator('tbody td').evaluateAll(es=>es.every(e=>e.innerText.trim().length>0)),'Blank table cell');
   tables.push({queryId:query.id,rows:actual.length,allIdentitiesMatched:true});
   if(i===0)await capture(table.locator('thead'),'amount-epochs-header-1280-light');
   else await capture(table.locator('tbody tr').last(),'amount-comparison-last-row-1280-light');
  }
  await card.getByRole('tab',{name:'Evidence flow',exact:true}).click();panel=card.getByRole('tabpanel');const verify=panel.getByText('Verification',{exact:true});await verify.click();check(await verify.evaluate(e=>e.closest('details')?.open)===true,'Verification did not expand');
  check(errors.length===0&&images.length===3,'Scoped QA failed');
  check(sha(corePath)===coreSha&&coreFiles.every(ref=>sha(path.join(root,ref.path))===ref.sha256),'Core presentation changed');
  check(sha(receiptPath)===renderReceiptSha256&&sha(path.join(root,fragment.path))===fragment.sha256&&sha(__filename)===scriptSha256,'Amount presentation changed');
  save('preview-verification.json',{schema:'board-amount-level-preview-v1',passed:true,scope:'Separate Sources standalone iframe; allnine rows, narrow caveat andwide numeric first/last views. Not live host insertion.',
   renderReceiptSha256,scriptSha256,fragments:receipt.fragments,payloads:receipt.payloads,deliveryOrder:['sources'],tableRows:[[6,3]],images,tables,errors,
   coreRenderReceiptSha256:coreSha,corePresentationUnchanged:true,screenshotsVisuallyInspected:false,actualMarketRuns:0,analysisArtifactsModified:false});
  console.log(JSON.stringify({passed:true,report:path.join(out,'preview-verification.json'),reportSha256:sha(path.join(out,'preview-verification.json')),images:3}));
 }finally{await browser.close();}
})().catch(e=>{if(started&&!fs.existsSync(path.join(out,'failure.json'))&&!fs.existsSync(path.join(out,'preview-verification.json')))save('failure.json',{passed:false,message:e.message,stack:e.stack});console.error(e);process.exitCode=1;});
