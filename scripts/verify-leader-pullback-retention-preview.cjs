// Presentation QA only; no signals, account execution or source-data mutation.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process'),{pathToFileURL}=require('node:url');
const {chromium}=require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),family=path.join(root,'artifacts/strategy-search/leader-pullback-retention-v1');
const python='C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const wrapper='C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const attempt=process.argv[2]||'0001';if(!/^\d{4}$/.test(attempt))throw Error('Four-digit exclusive attempt');
const out=path.join(family,'browser-qa','attempt-'+attempt);
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const check=(x,m)=>{if(!x)throw Error(m);};
const normalizeIdentity=(value,field)=>{
 if(!['决策日起','决策日止'].includes(field))return value;
 const date=/^(\d{4})年(\d{1,2})月(\d{1,2})日$/.exec(value);
 return date?`${date[1]}-${date[2].padStart(2,'0')}-${date[3].padStart(2,'0')}`:value;
};
const save=(name,x)=>fs.writeFileSync(path.join(out,name),JSON.stringify(x,null,2)+'\n',{flag:'wx'});
async function geometry(locator){
 const b=await locator.evaluate(e=>({width:innerWidth,scroll:document.documentElement.scrollWidth,right:e.getBoundingClientRect().right}));
 check(b.scroll<=b.width+1&&b.right<=b.width+1,'Outer horizontal overflow');return b;
}
(async()=>{
 fs.mkdirSync(path.dirname(out),{recursive:true});fs.mkdirSync(out);
 const receipt=read(path.join(family,'render-receipt.json')),receiptSha=sha(path.join(family,'render-receipt.json'));
 const order=['comparisonChart','comparisonSources'],payloads={};
 check(JSON.stringify(receipt.deliveryOrder)===JSON.stringify(order),'Delivery order');
 for(const key of order){
  const p=receipt.payloads[key],f=receipt.fragments[key];
  check(sha(path.join(family,p.path))===p.sha256&&sha(path.join(family,f.path))===f.sha256,'Input binding');
  payloads[key]=read(path.join(family,p.path));
  const r=spawnSync(python,['-X','utf8','-B',wrapper,path.join(family,f.path),path.join(out,key+'.html')],{encoding:'utf8',timeout:30000});
  save(key+'-wrapper.json',{status:r.status,stdout:r.stdout,stderr:r.stderr});check(r.status===0,'Wrapper failed');
 }
 const browser=await chromium.launch({headless:true,channel:'msedge'}),images=[],chartViews=[],tableChecks=[],interactions=[],errors=[];
 try{
  const page=await browser.newPage({viewport:{width:780,height:900},reducedMotion:'reduce'});page.setDefaultTimeout(12000);
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  for(const stem of ['comparison']){
   const payload=payloads[stem+'Chart'],nonzero=payload.rows.filter(r=>r[payload.chart.y]!==0).length;
   for(const [width,colorScheme] of [[780,'light'],[360,'light'],[780,'dark']]){
    await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme,reducedMotion:'reduce'});
    await page.goto(pathToFileURL(path.join(out,stem+'Chart.html')).href);
    const frame=page.frameLocator('iframe'),chart=frame.locator('.data-inline-react-root');
    await chart.locator('svg.recharts-surface').first().waitFor();await page.waitForTimeout(200);
    const bounds=await geometry(chart);
    const marks=chart.locator('.recharts-bar-rectangle');
    const actual=await marks.evaluateAll(es=>es.filter(e=>{const b=e.getBoundingClientRect();return b.width>0&&b.height>0;}).length);
    check(actual===nonzero,'Chart marks differ from source');
    const text=await chart.innerText();check(text.includes(payload.title)&&text.includes(payload.chart.xLabel),'Chart title/units');
    const categories=await chart.locator('svg text').evaluateAll(es=>es.filter(e=>e.querySelector('title')).map(e=>[...e.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('')));
    for(const x of payload.chart.categoryOrder)check(categories.includes(x),'Truncated category '+x);
    if(stem==='comparison'){
     const zero=chart.locator('.recharts-reference-line-line[data-annotation-mark="zero-difference"]');
     check(await zero.count()===1,'Missing zero reference');
     check(await zero.evaluate(e=>Number(e.getAttribute('x1'))===Number(e.getAttribute('x2'))&&Math.abs(Number(e.getAttribute('y2'))-Number(e.getAttribute('y1')))>100&&getComputedStyle(e).stroke!=='none'),'Zero reference not painted');
    }
    const img=path.join(out,stem+'-'+width+'-'+colorScheme+'.png');await chart.screenshot({path:img});
    images.push({path:path.relative(root,img).replaceAll('\\','/'),sha256:sha(img)});
    chartViews.push({stem,width,colorScheme,marks:actual,...bounds});
    if(width===780&&colorScheme==='light'){
     let hoverVerified=false;
     for(let i=0;i<await marks.count();i++){
      const b=await marks.nth(i).boundingBox();if(!b||b.width<4||b.height<4)continue;
      await marks.nth(i).hover();const tip=chart.locator('.recharts-tooltip-wrapper');await tip.waitFor({state:'visible'});
      const heading=await tip.locator('strong').innerText();
      const observed=await tip.locator('.chart-tooltip > span').evaluateAll(es=>es.map(e=>({series:[...e.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim(),value:Number(e.querySelector('b').innerText.replaceAll(',',''))})));
      const expected=payload.rows.filter(r=>r[payload.chart.x]===heading);
      check(expected.length===2&&observed.length===2&&expected.every(r=>observed.some(v=>v.series===r[payload.chart.series]&&Math.abs(v.value-r[payload.chart.y])<=.011)),'Tooltip numeric mismatch');hoverVerified=true;break;
     }
     const toggles=frame.getByRole('button',{name:/^Toggle /});check(await toggles.count()===2,'Two legend controls');
     await toggles.first().click();await page.waitForTimeout(100);
     const remaining=await chart.locator('.recharts-bar-rectangle').evaluateAll(es=>es.filter(e=>{const b=e.getBoundingClientRect();return b.width>0&&b.height>0;}).length);
     check(remaining===payload.rows.filter(r=>r[payload.chart.series]!==payload.chart.seriesOrder[0]&&r[payload.chart.y]!==0).length,'Legend toggle failed');await toggles.first().click();
     await frame.getByRole('button',{name:payload.title+' actions',exact:true}).click();await frame.getByRole('menuitem',{name:'Edit chart',exact:true}).click();
     const cancel=frame.getByRole('button',{name:'Cancel',exact:true});await cancel.click();check(await cancel.count()===0,'Editor Cancel');
     check(nonzero===0||hoverVerified,'A nonzero chart needs verified tooltip values');
     interactions.push({stem,hover:hoverVerified,hoverApplicable:nonzero>0,legendHideRestore:true,editorCancel:true});
    }
   }
   await page.setViewportSize({width:780,height:900});await page.emulateMedia({colorScheme:'light'});
   await page.goto(pathToFileURL(path.join(out,stem+'Sources.html')).href);
   const frame=page.frameLocator('iframe'),receiptRoot=frame.locator('.sources-receipt');await receiptRoot.waitFor();await receiptRoot.locator('.receipt-disclosure').click();
   const sources=payloads[stem+'Sources'];
   for(let n=0;n<sources.items.length;n++){
    const item=sources.items[n],card=receiptRoot.locator('.receipt-card').nth(n);
    if(await card.getAttribute('data-open')!=='true')await card.locator('.receipt-card-toggle').click();
    await card.getByRole('tab',{name:'Data preview',exact:true}).click();const panel=card.getByRole('tabpanel');
    check(await panel.locator('table').count()===item.queries.length,'Source tables missing');
    for(let q=0;q<item.queries.length;q++){
     const query=item.queries[q],fields=query.columns.map(c=>typeof c==='string'?c:c.field),table=panel.locator('table').nth(q);
     const ids=fields.filter(f=>['板块','时期','板块与退出','退出','规则','区间','条件','对照','决策日起','决策日止'].includes(f));
     check(ids.length>=2,'Source row identity');
     const positions=ids.map(f=>fields.indexOf(f)),seen=[],expected=query.rows.map(r=>ids.map(f=>String(r[f])));
     const pager=query.rows.length>50?panel.locator('.receipt-pagination').nth(item.queries.slice(0,q).filter(x=>x.rows.length>50).length):null;
     for(let offset=0;offset<query.rows.length;offset+=50){
      const count=Math.min(50,query.rows.length-offset);check(await table.locator('tbody tr').count()===count,'Table row count');
      seen.push(...await table.locator('tbody tr').evaluateAll((rs,is)=>rs.map(r=>is.map(i=>r.querySelectorAll('td')[i].innerText.trim())),positions));
      if(offset+count<query.rows.length){await pager.getByRole('button',{name:'Next page',exact:true}).click();await pager.getByRole('status').filter({hasText:`Rows ${offset+51}–${Math.min(offset+100,query.rows.length)} of ${query.rows.length}`}).waitFor();}
     }
     const normalized=seen.map(row=>row.map((value,i)=>normalizeIdentity(value,ids[i])));
     check(JSON.stringify(normalized)===JSON.stringify(expected),'Source identities differ: '+query.id);tableChecks.push({stem,query:query.id,rows:seen.length});
    }
   }
   await geometry(receiptRoot);
   const img=path.join(out,stem+'-sources.png');await page.screenshot({path:img,fullPage:false});images.push({path:path.relative(root,img).replaceAll('\\','/'),sha256:sha(img)});
  }
  check(errors.length===0,'Browser errors: '+errors.join(';'));
  check(sha(path.join(family,'render-receipt.json'))===receiptSha,'Render changed during QA');
  save('preview-verification.json',{schema:'leader-pullback-retention-preview-v1',passed:true,createdAt:new Date().toISOString(),renderReceiptSha256:receiptSha,
   scriptSha256:sha(__filename),images,chartViews,tableChecks,interactions,errors,actualMarketRuns:0,analysisArtifactsModified:false,
   scope:'Local sandbox iframe preview at narrow/wide widths and light/dark themes; not live-host insertion proof.',screenshotsVisuallyInspected:false});
  console.log(JSON.stringify({passed:true,images:images.length,report:path.join(out,'preview-verification.json')}));
 }finally{await browser.close();}
})().catch(error=>{if(fs.existsSync(out)&&!fs.existsSync(path.join(out,'failure.json')))save('failure.json',{passed:false,message:error.message,stack:error.stack});console.error(error);process.exitCode=1;});
