// Presentation-only standalone iframe QA. No source computation or account runs.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const family = path.resolve(__dirname, '../artifacts/strategy-search/board-environment-context-v1');
const wrapper = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const boards = ['main', 'chinext', 'star'], expectedRows = [[16,16,16,3,32],[16,16,16,3,32],[16,16,16,3,32]];
const argsv = process.argv.slice(2);
if (argsv.length % 2) throw new Error('Expected --receipt-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({length:argsv.length/2},(_,i)=>[argsv[2*i],argsv[2*i+1]]));
if (Object.keys(args).some(k=>!['--receipt-sha','--attempt'].includes(k))) throw new Error('Unknown argument');
if (!/^[a-f0-9]{64}$/.test(args['--receipt-sha']||'')) throw new Error('Frozen receipt SHA required');
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw new Error('Four-digit attempt required');
const out = path.join(family, 'browser-qa', 'attempt-'+attempt);
const sha = f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
const read = f=>JSON.parse(fs.readFileSync(f,'utf8'));
const check = (v,m)=>{if(!v)throw new Error(m);};
const save = (name,obj)=>fs.writeFileSync(path.join(out,name),JSON.stringify(obj,null,2)+'\n',{flag:'wx'});
let started = false;
async function geometry(locator) {
  const g=await locator.evaluate(e=>{const b=e.getBoundingClientRect();return {left:b.left,right:b.right,width:innerWidth,scrollWidth:e.scrollWidth,documentWidth:document.documentElement.scrollWidth};});
  check(g.left>=-1&&g.right<=g.width+1&&g.scrollWidth<=g.width+1&&g.documentWidth<=g.width+1,'Horizontal overflow '+JSON.stringify(g));
  return g;
}
(async()=>{
  fs.mkdirSync(path.dirname(out),{recursive:true});fs.mkdirSync(out);started=true;
  const scriptSha256=sha(__filename), receiptPath=path.join(family,'render-receipt.json');
  const renderReceiptSha256=sha(receiptPath), receipt=read(receiptPath);
  check(renderReceiptSha256===args['--receipt-sha'],'Receipt changed');
  const deliveryOrder=boards.flatMap(b=>[b+'Chart',b+'Sources']);
  check(JSON.stringify(receipt.deliveryOrder)===JSON.stringify(deliveryOrder)&&JSON.stringify(receipt.tableRows)===JSON.stringify(expectedRows),'Delivery matrix differs');
  const payloads={}, originalHashes={};
  for(const key of deliveryOrder){
    const fragment=receipt.fragments[key], payload=receipt.payloads[key];
    check(sha(path.join(family,fragment.path))===fragment.sha256&&sha(path.join(family,payload.path))===payload.sha256,'Fragment/payload mismatch '+key);
    check(fs.statSync(path.join(family,fragment.path)).size<1000000,'Oversized fragment');
    payloads[key]=read(path.join(family,payload.path));originalHashes[payload.path]=payload.sha256;
    const result=spawnSync(python,['-X','utf8','-B',wrapper,path.join(family,fragment.path),path.join(out,key+'-preview.html')],{encoding:'utf8',timeout:30000});
    save(key+'-wrapper-log.json',{status:result.status,stdout:result.stdout,stderr:result.stderr});check(result.status===0,'Preview wrapper failed');
  }
  for(const board of boards){
    const chart=payloads[board+'Chart'], sources=payloads[board+'Sources'];
    check(chart.rows.length===32&&chart.chart.type==='horizontalBar'&&chart.chart.showValues===false&&chart.chart.startAtZero===true,'Full native bar chart required');
    check(chart.chart.xLabel==='阶段日均（%）'&&chart.chart.seriesOrder.length===2&&chart.chart.categoryOrder.length===16,'Two metrics/sixteen phases required');
    check(JSON.stringify(chart.chart.categoryOrder)===JSON.stringify(payloads.mainChart.chart.categoryOrder),'Cross-board phase order differs');
    check(sources.items.length===1&&JSON.stringify(sources.items[0].queries.map(q=>q.rows.length))===JSON.stringify(expectedRows[0]),'Full five source tables required');
    check(chart.rows.every(r=>r[chart.chart.y]===null||Number.isFinite(r[chart.chart.y])),'Nonfinite chart value');
  }
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  const images=[], chartViews=[], sourceViews=[], tableChecks=[], finalItems=[], errors=[];
  let legendCheck,editorCheck;
  try{
    const page=await browser.newPage({viewport:{width:780,height:900},reducedMotion:'reduce'});page.setDefaultTimeout(10000);
    page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    const capture=async(locator,name,viewport=false)=>{
      let targetBounds=null;
      if(viewport){await locator.evaluate(e=>e.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));await page.waitForTimeout(300);targetBounds=await locator.boundingBox();check(targetBounds&&targetBounds.height>0&&targetBounds.y<900&&targetBounds.y+targetBounds.height>0,'Screenshot target outside viewport');}
      const file=path.join(out,name+'.png');if(viewport)await page.screenshot({path:file,fullPage:false});else await locator.screenshot({path:file});
      const item={path:path.relative(family,file).replaceAll('\\','/'),sha256:sha(file),capture:viewport?'page-viewport':'chart-element',...(targetBounds?{targetBounds}:{})};images.push(item);return item;
    };
    for(const board of boards){
      const payload=payloads[board+'Chart'], value=payload.chart.y;
      const nonzero=payload.rows.filter(r=>r[value]!==null&&r[value]!==0);
      for(const [width,colorScheme] of [[780,'light'],[360,'light'],[780,'dark']]){
        await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme,reducedMotion:'reduce'});
        await page.goto(pathToFileURL(path.join(out,board+'Chart-preview.html')).href);
        const frame=page.frameLocator('iframe'), chart=frame.locator('.data-inline-react-root');await chart.waitFor({timeout:15000});await chart.locator('svg.recharts-surface').first().waitFor();await page.waitForTimeout(200);
        const bounds=await geometry(chart);
        const marks=await chart.locator('.recharts-bar-rectangle').evaluateAll(es=>({containers:es.length,visible:es.filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;}).length}));
        check(marks.visible===nonzero.length,'Visible quantitative marks differ '+board+' '+JSON.stringify(marks));
        const labels=await chart.locator('svg text').evaluateAll(ns=>ns.filter(n=>n.querySelector('title')).map(n=>[...n.childNodes].filter(c=>c.nodeType===Node.TEXT_NODE).map(c=>c.textContent).join('')));
        for(const label of payload.chart.categoryOrder)check(labels.includes(label),'Phase label truncated '+label);
        const axis=chart.locator('.chart-axis-label');check(await axis.count()===1&&await axis.isVisible()&&(await axis.innerText()).trim()===payload.chart.xLabel,'Percent units missing');
        const ticks=await chart.locator('.recharts-xAxis .recharts-cartesian-axis-tick-value').allTextContents();
        const toggles=frame.getByRole('button',{name:/^Toggle /});check(await toggles.count()===2,'Two visible metric legends required');
        for(const series of payload.chart.seriesOrder)check(await toggles.filter({hasText:series}).count()===1,'Metric legend missing');
        chartViews.push({board,width,colorScheme,...bounds,...marks,visibleCategories:labels,numericAxisTicks:ticks,zeroOrigin:true,fixedMaximumClaimed:false,
          ...((board==='main'||width===360)?{image:await capture(chart,board+'-chart-'+width+'-'+colorScheme)}:{})});
        if(board==='main'&&width===780&&colorScheme==='light'){
          await toggles.first().click();await page.waitForTimeout(120);const hidden=await chart.locator('.recharts-bar-rectangle').count();await toggles.first().click();await page.waitForTimeout(120);const restored=await chart.locator('.recharts-bar-rectangle').count();
          check(hidden<marks.containers&&restored===marks.containers,'Legend hide/restore failed');legendCheck={passed:true,hidden,restored};
          await frame.getByRole('button',{name:payload.title+' actions',exact:true}).click();await frame.getByRole('menuitem',{name:'Edit chart',exact:true}).click();const cancel=frame.getByRole('button',{name:'Cancel',exact:true});await cancel.waitFor();await cancel.click();
          check(await cancel.count()===0,'Editor did not cancel');editorCheck={passed:true,action:'open-and-cancel'};
        }
      }
      await page.setViewportSize({width:360,height:900});await page.emulateMedia({colorScheme:'light',reducedMotion:'reduce'});
      await page.goto(pathToFileURL(path.join(out,board+'Sources-preview.html')).href);
      const frame=page.frameLocator('iframe'), root=frame.locator('.sources-receipt');await root.waitFor({timeout:15000});await root.locator('.receipt-disclosure').click();
      const card=root.locator('.receipt-card');check(await card.count()===1&&await card.getAttribute('data-open')==='true','Single source finding not open');
      for(const tab of ['Overview','Data preview','Evidence flow']){
        const button=card.getByRole('tab',{name:tab,exact:true});await button.click();check(await button.getAttribute('aria-selected')==='true','Tab did not open');
        const panel=card.getByRole('tabpanel');check((await panel.innerText()).trim().length>0,'Empty source panel');
        if(tab==='Overview'&&board==='main'){
          const last=panel.locator('.receipt-qualification-list li').last();check(await last.count()===1,'Caveat missing');finalItems.push({board,type:'caveat',text:await last.innerText(),image:await capture(last,'main-sources-last-caveat-360-light',true)});
        }
        if(tab==='Data preview'){
          const queries=payloads[board+'Sources'].items[0].queries;check(await panel.locator('table').count()===5,'Five tables required');
          for(let q=0;q<5;q++){
            const table=panel.locator('table').nth(q), query=queries[q], identity=q===3?['epoch']:q===4?['phase','policy']:['phase'];
            const indices=identity.map(k=>query.columns.findIndex(c=>c.field===k));check(indices.every(n=>n>=0),'Identity field missing');
            check(await table.locator('tbody tr').count()===query.rows.length,'Source row count differs');
            const actual=await table.locator('tbody tr').evaluateAll((rs,is)=>rs.map(r=>is.map(i=>r.querySelectorAll('td')[i].innerText.trim())),indices);
            check(JSON.stringify(actual)===JSON.stringify(query.rows.map(r=>identity.map(k=>String(r[k])))),'Complete source identity mismatch');
            tableChecks.push({board,queryId:query.id,rows:query.rows.length,allIdentitiesMatched:true});
            if(q===4){const last=table.locator('tbody tr').last();finalItems.push({board,type:'old-account-final-phase',text:await last.innerText(),image:await capture(last,board+'-sources-account-last-row-360-light',true)});}
          }
        }
        if(tab==='Evidence flow'){const verification=panel.getByText('Verification',{exact:true});await verification.click();check(await verification.evaluate(e=>e.closest('details')?.open)===true,'Verification did not expand');}
        sourceViews.push({board,tab,...await geometry(root)});
      }
    }
    check(chartViews.length===9&&tableChecks.length===15&&tableChecks.reduce((n,r)=>n+r.rows,0)===249&&images.length===9,'Scoped coverage incomplete');
    check(errors.length===0,'Browser error '+JSON.stringify(errors));
    for(const key of deliveryOrder)check(sha(path.join(family,receipt.fragments[key].path))===receipt.fragments[key].sha256,'Fragment changed during QA');
    for(const [name,value] of Object.entries(originalHashes))check(sha(path.join(family,name))===value,'Payload changed');
    check(sha(receiptPath)===renderReceiptSha256&&sha(__filename)===scriptSha256,'Receipt/script changed');
    save('preview-verification.json',{schema:'board-environment-context-preview-v1',passed:true,createdAt:new Date().toISOString(),
      scope:'Standalone sandboxed iframe; three boards in three viewport/themes, all15 Sources tables/249 rows. Not live host insertion.',
      renderReceiptSha256,scriptSha256,fragments:receipt.fragments,payloads:receipt.payloads,deliveryOrder,tableRows:expectedRows,images,
      chartViews,sourceViews,tableChecks,finalItems,legendCheck,editorCheck,errors,horizontalOverflow:false,screenshotsVisuallyInspected:false,
      actualMarketRuns:0,analysisArtifactsModified:false});
    console.log(JSON.stringify({passed:true,images:images.length,report:path.join(out,'preview-verification.json'),reportSha256:sha(path.join(out,'preview-verification.json'))}));
  }finally{await browser.close();}
})().catch(e=>{if(started&&!fs.existsSync(path.join(out,'failure.json'))&&!fs.existsSync(path.join(out,'preview-verification.json')))save('failure.json',{passed:false,message:e.message,stack:e.stack});console.error(e);process.exitCode=1;});
