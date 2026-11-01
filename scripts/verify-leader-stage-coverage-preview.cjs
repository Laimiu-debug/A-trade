// Verify the native Sources receipt without changing analytical evidence.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process'),{pathToFileURL}=require('node:url');
const {chromium}=require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),family=path.join(root,'artifacts/strategy-search/leader-stage-coverage-v1');
const python='C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const wrapper='C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const attempt=process.argv[2]||'0001';if(!/^\d{4}$/.test(attempt))throw Error('Four-digit exclusive attempt required');
const out=path.join(family,'browser-qa','attempt-'+attempt);
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const check=(ok,message)=>{if(!ok)throw Error(message);};
const save=(name,value)=>fs.writeFileSync(path.join(out,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});
const normalize=value=>{
 const d=/^(\d{4})年(\d{1,2})月(\d{1,2})日$/.exec(value);
 return d?`${d[1]}-${d[2].padStart(2,'0')}-${d[3].padStart(2,'0')}`:value;
};
(async()=>{
 fs.mkdirSync(path.dirname(out),{recursive:true});fs.mkdirSync(out);
 const receipt=read(path.join(family,'render-receipt.json')),receiptSha=sha(path.join(family,'render-receipt.json'));
 const payloadPath=path.join(root,receipt.payload.path),fragmentPath=path.join(root,receipt.fragment.path);
 check(sha(payloadPath)===receipt.payload.sha256&&sha(fragmentPath)===receipt.fragment.sha256,'Render input binding');
 const payload=read(payloadPath),preview=path.join(out,'sources-preview.html');
 const wrapped=spawnSync(python,['-X','utf8','-B',wrapper,fragmentPath,preview],{encoding:'utf8',timeout:30000});
 save('wrapper.json',{exitCode:wrapped.status,stdout:wrapped.stdout,stderr:wrapped.stderr});check(wrapped.status===0,'Wrapper failed');
 const browser=await chromium.launch({headless:true,channel:'msedge'}),images=[],tableChecks=[],errors=[],views=[];
 try{
  const page=await browser.newPage({viewport:{width:780,height:900},reducedMotion:'reduce'});page.setDefaultTimeout(12000);
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  for(const [width,colorScheme] of [[780,'light'],[360,'light'],[780,'dark']]){
   await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme,reducedMotion:'reduce'});
   await page.goto(pathToFileURL(preview).href);
   const frame=page.frameLocator('iframe'),receiptRoot=frame.locator('.sources-receipt');await receiptRoot.waitFor();
   await receiptRoot.locator('.receipt-disclosure').click();
   for(let n=0;n<payload.items.length;n++){
    const item=payload.items[n],card=receiptRoot.locator('.receipt-card').nth(n);
    if(await card.getAttribute('data-open')!=='true')await card.locator('.receipt-card-toggle').click();
    await card.getByRole('tab',{name:'Data preview',exact:true}).click();const panel=card.getByRole('tabpanel');
    check(await panel.locator('table').count()===item.queries.length,'Missing source table');
    if(width===780&&colorScheme==='light')for(let q=0;q<item.queries.length;q++){
     const query=item.queries[q],fields=query.columns.map(c=>typeof c==='string'?c:c.field),table=panel.locator('table').nth(q);
     const ids=fields.filter(f=>['时期','板块','阶段','日期','股票'].includes(f));check(ids.length>=2,'Missing row identity');
     const positions=ids.map(f=>fields.indexOf(f)),seen=[],expected=query.rows.map(r=>ids.map(f=>String(r[f])));
     const pager=query.rows.length>50?panel.locator('.receipt-pagination').nth(item.queries.slice(0,q).filter(x=>x.rows.length>50).length):null;
     for(let offset=0;offset<query.rows.length;offset+=50){
      const count=Math.min(50,query.rows.length-offset);check(await table.locator('tbody tr').count()===count,'Table page count');
      seen.push(...await table.locator('tbody tr').evaluateAll((rows,cols)=>rows.map(r=>cols.map(c=>r.querySelectorAll('td')[c].innerText.trim())),positions));
      if(offset+count<query.rows.length){await pager.getByRole('button',{name:'Next page',exact:true}).click();await pager.getByRole('status').filter({hasText:`Rows ${offset+51}–${Math.min(offset+100,query.rows.length)} of ${query.rows.length}`}).waitFor();}
     }
     check(JSON.stringify(seen.map(r=>r.map(normalize)))===JSON.stringify(expected),'Source identities differ: '+query.id);
     tableChecks.push({id:query.id,rows:seen.length});
    }
   }
   const geometry=await receiptRoot.evaluate(e=>({width:innerWidth,scroll:document.documentElement.scrollWidth,right:e.getBoundingClientRect().right}));
   check(geometry.scroll<=geometry.width+1&&geometry.right<=geometry.width+1,'Outer horizontal overflow');
   const imagePath=path.join(out,`sources-${width}-${colorScheme}.png`);await page.screenshot({path:imagePath,fullPage:false});
   images.push({path:path.relative(root,imagePath).replaceAll('\\','/'),sha256:sha(imagePath)});views.push({width,colorScheme,...geometry});
  }
  check(errors.length===0,'Browser errors: '+errors.join(';'));
  check(sha(path.join(family,'render-receipt.json'))===receiptSha,'Receipt changed');
  save('preview-verification.json',{schema:'leader-stage-coverage-preview-v1',passed:true,createdAt:new Date().toISOString(),
   renderReceiptSha256:receiptSha,scriptSha256:sha(__filename),images,tableChecks,views,errors,
   actualMarketRuns:0,analysisArtifactsModified:false,screenshotsVisuallyInspected:false,
   scope:'Local sandbox iframe preview; no live-host insertion claim.'});
  console.log(JSON.stringify({passed:true,images:images.length,report:path.join(out,'preview-verification.json')}));
 }finally{await browser.close();}
})().catch(error=>{if(fs.existsSync(out)&&!fs.existsSync(path.join(out,'failure.json')))save('failure.json',{passed:false,message:error.message,stack:error.stack});console.error(error);process.exitCode=1;});
