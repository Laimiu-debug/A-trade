// Presentation-only QA. Never reads raw prices or runs models/accounts.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process'),{pathToFileURL}=require('node:url');
const {chromium}=require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const family=path.resolve(__dirname,'../artifacts/strategy-search/common-support-switch-v1');
const python='C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const wrapper='C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const argv=process.argv.slice(2);
if(argv.length%2)throw Error('Expected --receipt-sha SHA [--attempt 0001]');
const args=Object.fromEntries(Array.from({length:argv.length/2},(_,i)=>[argv[2*i],argv[2*i+1]]));
if(Object.keys(args).some(k=>!['--receipt-sha','--attempt'].includes(k))||!/^[a-f0-9]{64}$/.test(args['--receipt-sha']||''))throw Error('Exact receipt SHA required');
const attempt=args['--attempt']||'0001';if(!/^\d{4}$/.test(attempt))throw Error('Four-digit exclusive attempt required');
const out=path.join(family,'browser-qa','attempt-'+attempt);
const order=['oldChart','oldSources','newChart','newSources'];
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const save=(name,x)=>fs.writeFileSync(path.join(out,name),JSON.stringify(x,null,2)+'\n',{flag:'wx'});
const check=(x,m)=>{if(!x)throw Error(m);};let started=false;
async function geometry(locator){
  const x=await locator.evaluate(e=>{const b=e.getBoundingClientRect();return {left:b.left,right:b.right,width:innerWidth,scrollWidth:e.scrollWidth,documentWidth:document.documentElement.scrollWidth};});
  check(x.left>=-1&&x.right<=x.width+1&&x.scrollWidth<=x.width+1&&x.documentWidth<=x.width+1,'Outer horizontal overflow: '+JSON.stringify(x));return x;
}
(async()=>{
  fs.mkdirSync(path.dirname(out),{recursive:true});fs.mkdirSync(out);started=true;
  const receiptPath=path.join(family,'render-receipt.json'),receipt=read(receiptPath),renderReceiptSha256=sha(receiptPath),scriptSha256=sha(__filename);
  check(renderReceiptSha256===args['--receipt-sha'],'Receipt changed');
  check(JSON.stringify(receipt.deliveryOrder)===JSON.stringify(order)&&JSON.stringify(Object.keys(receipt.fragments))===JSON.stringify(order),'Two chart/Sources pairs required');
  check(receipt.chartRows===24&&receipt.tableRows.length===2,'Full coverage chart matrix missing');
  check(JSON.stringify(receipt.tableRows)===JSON.stringify([[42,24,84,168,336,12],[42,24,84,168,336,84,12]]),'Complete period-scoped Sources matrix changed');
  check(Array.isArray(receipt.presentationHelpers)&&receipt.presentationHelpers.length===3,'Presentation helper refs missing');
  for(const ref of receipt.presentationHelpers)check(sha(ref.path)===ref.sha256,'Presentation helper changed');
  const payloads={};
  for(const name of order){
    const p=receipt.payloads[name],f=receipt.fragments[name];
    check(sha(path.join(family,p.path))===p.sha256&&sha(path.join(family,f.path))===f.sha256,'Payload binding differs: '+name);
    payloads[name]=read(path.join(family,p.path));
    const r=spawnSync(python,['-X','utf8','-B',wrapper,path.join(family,f.path),path.join(out,name+'-preview.html')],{encoding:'utf8',timeout:30000});
    save(name+'-wrapper-log.json',{status:r.status,stdout:r.stdout,stderr:r.stderr});check(r.status===0,'Wrapper failed: '+name);
  }
  const browser=await chromium.launch({headless:true,channel:'msedge'}),images=[],chartViews=[],sourceViews=[],tableChecks=[],interactions=[],errors=[];
  try{
    const page=await browser.newPage({viewport:{width:780,height:900},reducedMotion:'reduce'});page.setDefaultTimeout(10000);
    page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    const capture=async(locator,name,viewport=false)=>{
      if(viewport){await locator.evaluate(e=>e.scrollIntoView({block:'center',behavior:'instant'}));await page.waitForTimeout(250);}
      const file=path.join(out,name+'.png');
      if(viewport)await page.screenshot({path:file,fullPage:false});else await locator.screenshot({path:file});
      const ref={path:path.relative(family,file).replaceAll('\\','/'),sha256:sha(file),capture:viewport?'page-viewport':'chart-element'};images.push(ref);return ref;
    };
    for(const [epochIndex,epoch] of ['old','new'].entries()){
      const chartKey=epoch+'Chart',sourceKey=epoch+'Sources',chartPayload=payloads[chartKey],sources=payloads[sourceKey];
      const values=chartPayload.rows.map(r=>r[chartPayload.chart.y]);
      check(values.length===12&&values.every(v=>Number.isFinite(v)),'All twelve signed return differences must remain');
      check(chartPayload.chart.type==='horizontalBar'&&chartPayload.chart.startAtZero===true&&chartPayload.chart.showValues===false&&chartPayload.chart.stackable===false,'Native coverage chart settings differ');
      check(chartPayload.chart.xLabel==='账户收益差（百分点）'&&chartPayload.chart.seriesOrder.length===2&&chartPayload.chart.categoryOrder.length===6,'Units/series/categories differ');
      check(JSON.stringify(chartPayload.chart.categoryOrder)===JSON.stringify(['主固定','主波动','创固定','创波动','科固定','科波动']),'Six distinguishable category names required');
      check(chartPayload.rows.every(r=>r['完整板块']&&r['完整退出规则']),'Full reviewed row identities missing');
      check(JSON.stringify(sources.items.flatMap(i=>i.queries.map(q=>q.rows.length)))===JSON.stringify(receipt.tableRows[epochIndex]),'Sources receipt count differs');
      const nonzero=values.filter(x=>x!==0).length;
      for(const [width,colorScheme] of [[780,'light'],[360,'light'],[780,'dark']]){
        await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme,reducedMotion:'reduce'});
        await page.goto(pathToFileURL(path.join(out,chartKey+'-preview.html')).href);
        const frame=page.frameLocator('iframe'),chart=frame.locator('.data-inline-react-root');
        await chart.waitFor({timeout:15000});await chart.locator('svg.recharts-surface').first().waitFor();await page.waitForTimeout(200);
        const bounds=await geometry(chart),painted=await chart.evaluate(e=>({bars:e.querySelectorAll('.recharts-bar-rectangle').length,
          nonzeroBars:[...e.querySelectorAll('.recharts-bar-rectangle')].filter(x=>{const b=x.getBoundingClientRect();return b.width>0&&b.height>0;}).length,
          categories:[...e.querySelectorAll('svg text')].filter(x=>x.querySelector('title')).map(x=>[...x.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('')),
          ticks:[...e.querySelectorAll('svg text')].filter(x=>!x.querySelector('title')).map(x=>x.textContent.trim()).filter(x=>x!==''&&Number.isFinite(Number(x.replaceAll(',',''))))}));
        check(painted.nonzeroBars===nonzero&&painted.bars>=nonzero&&painted.bars<=12,'Native marks differ from nonzero signed contrasts: '+JSON.stringify(painted));
        for(const name of chartPayload.chart.categoryOrder)check(painted.categories.includes(name),'Visible category missing/truncated: '+name);
        const zero=chart.locator('.recharts-reference-line-line[data-annotation-mark="zero-return-difference"]');
        check(await zero.count()===1,'Native zero reference line missing');
        const zeroGeometry=await zero.evaluate(e=>({x:Number(e.getAttribute('x')),x1:Number(e.getAttribute('x1')),x2:Number(e.getAttribute('x2')),height:Math.abs(Number(e.getAttribute('y2'))-Number(e.getAttribute('y1'))),stroke:getComputedStyle(e).stroke,strokeWidth:Number.parseFloat(getComputedStyle(e).strokeWidth),visibility:getComputedStyle(e).visibility,display:getComputedStyle(e).display}));
        check(zeroGeometry.x===0&&zeroGeometry.x1===zeroGeometry.x2&&zeroGeometry.height>100&&zeroGeometry.stroke!=='none'&&zeroGeometry.strokeWidth>0&&zeroGeometry.visibility!=='hidden'&&zeroGeometry.display!=='none','Native zero benchmark not painted');
        check((await chart.innerText()).includes('0：收益相同'),'Visible zero benchmark label missing');
        const axis=chart.locator('.chart-axis-label');check(await axis.count()===1&&await axis.isVisible()&&(await axis.innerText()).trim()===chartPayload.chart.xLabel,'Visible percentage unit missing');
        check(await chart.getByText(chartPayload.title,{exact:true}).isVisible(),'Visible chart title missing');
        const toggles=frame.getByRole('button',{name:/^Toggle /});check(await toggles.count()===2,'Two model legends required');
        chartViews.push({epoch,width,colorScheme,...bounds,...painted,reviewedRows:12,zeroRows:12-nonzero,image:await capture(chart,epoch+'-chart-'+width+'-'+colorScheme)});
        if(width===780&&colorScheme==='light'){
          const marks=chart.locator('.recharts-bar-rectangle');let hoverChecked=false;
          for(let m=0;m<await marks.count();m++){
            const box=await marks.nth(m).boundingBox();if(!box||box.width<4||box.height<4)continue;
            await marks.nth(m).hover();const tooltip=chart.locator('.recharts-tooltip-wrapper');await tooltip.waitFor({state:'visible'});
            const heading=await tooltip.locator('strong').innerText();
            const observed=await tooltip.locator('.chart-tooltip > span').evaluateAll(es=>es.map(e=>({series:[...e.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim(),value:Number(e.querySelector('b').innerText.replaceAll(',',''))})));
            const expected=chartPayload.rows.filter(r=>r[chartPayload.chart.x]===heading);
            check(expected.length===2&&observed.length===2&&expected.every(r=>observed.some(v=>v.series===r[chartPayload.chart.series]&&Number.isFinite(v.value)&&Math.abs(v.value-r[chartPayload.chart.y])<=.011)),'Native grouped tooltip series/value mismatch');hoverChecked=true;break;
          }
          check(hoverChecked||nonzero===0,'Could not inspect an available native tooltip');
          await toggles.first().click();await page.waitForTimeout(120);
          const actual=await chart.locator('.recharts-bar-rectangle').evaluateAll(es=>es.filter(e=>{const b=e.getBoundingClientRect();return b.width>0&&b.height>0;}).length);
          const expected=chartPayload.rows.filter(r=>r[chartPayload.chart.series]!==chartPayload.chart.seriesOrder[0]&&r[chartPayload.chart.y]!==0).length;
          check(actual===expected,'Native legend did not preserve other sources');await toggles.first().click();
          await frame.getByRole('button',{name:chartPayload.title+' actions',exact:true}).click();await frame.getByRole('menuitem',{name:'Edit chart',exact:true}).click();
          const cancel=frame.getByRole('button',{name:'Cancel',exact:true});await cancel.waitFor();await cancel.click();check(await cancel.count()===0,'Editor Cancel did not close');
          interactions.push({epoch,legendHideRestore:true,editorOpenCancel:true,nativeGroupedHoverValues: hoverChecked,noAppliedChanges:true});
        }
      }
      await page.setViewportSize({width:780,height:900});await page.emulateMedia({colorScheme:'light',reducedMotion:'reduce'});
      await page.goto(pathToFileURL(path.join(out,sourceKey+'-preview.html')).href);
      const frame=page.frameLocator('iframe'),sourceRoot=frame.locator('.sources-receipt');await sourceRoot.waitFor({timeout:15000});await sourceRoot.locator('.receipt-disclosure').click();
      const cards=sourceRoot.locator('.receipt-card');check(await cards.count()===sources.items.length,'Finding count differs');
      for(let itemIndex=0;itemIndex<sources.items.length;itemIndex++){
        const card=cards.nth(itemIndex),item=sources.items[itemIndex];
        if(await card.getAttribute('data-open')!=='true')await card.locator('.receipt-card-toggle').click();
        await card.getByRole('tab',{name:'Overview',exact:true}).click();let panel=card.getByRole('tabpanel');
        if(itemIndex===0){const text=await panel.innerText();for(const value of ['126','20','回退','未平','2026','收益','主固定表示主板的固定10%跟踪','主波动表示主板的波动幅度跟踪','创固定表示创业板的固定10%跟踪','创波动表示创业板的波动幅度跟踪','科固定表示科创板的固定10%跟踪','科波动表示科创板的波动幅度跟踪'])check(text.includes(value),'Visible scope/category mapping missing: '+value);}
        await card.getByRole('tab',{name:'Data preview',exact:true}).click();panel=card.getByRole('tabpanel');
        check(await panel.locator('table').count()===item.queries.length,'Query tables missing');
        for(let q=0;q<item.queries.length;q++){
          const query=item.queries[q],table=panel.locator('table').nth(q);
          const fields=['period','boardLabel','exitLabel','candidateLabel','controlLabel','id'].filter(k=>query.columns.some(c=>c.field===k));
          check(fields.length>=3,'Insufficient inspectable row identity');
          const positions=fields.map(k=>query.columns.findIndex(c=>c.field===k)),expected=query.rows.map(r=>fields.map(k=>String(r[k]))),seen=[],pages=[];
          const pager=query.rows.length>50?panel.locator('.receipt-pagination').nth(item.queries.slice(0,q).filter(x=>x.rows.length>50).length):null;
          for(let offset=0;offset<query.rows.length;offset+=50){
            const count=Math.min(50,query.rows.length-offset);check(await table.locator('tbody tr').count()===count,'Table page count differs');
            seen.push(...await table.locator('tbody tr').evaluateAll((rs,is)=>rs.map(r=>is.map(i=>r.querySelectorAll('td')[i].innerText.trim())),positions));pages.push(count);
            check(await table.locator('tbody td').evaluateAll(es=>es.every(e=>e.innerText.trim().length>0)),'Empty source cell');
            if(offset+count<query.rows.length){await pager.getByRole('button',{name:'Next page',exact:true}).click();await pager.getByRole('status').filter({hasText:`Rows ${offset+51}–${Math.min(offset+100,query.rows.length)} of ${query.rows.length}`}).waitFor();}
          }
          check(JSON.stringify(seen)===JSON.stringify(expected),'Source row identities/order differ: '+query.id);
          tableChecks.push({epoch,finding:item.id,query:query.id,rows:seen.length,pages,allIdentitiesMatched:true});
          if(itemIndex===sources.items.length-1&&q===item.queries.length-1)await capture(table.locator('tbody tr').last(),epoch+'-sources-last-row-780-light',true);
        }
      }
      sourceViews.push({epoch,width:780,tables:sources.items.reduce((n,i)=>n+i.queries.length,0),...await geometry(sourceRoot)});
    }
    check(images.length===8&&errors.length===0,'Scoped QA incomplete or browser errors: '+JSON.stringify(errors));
    check(sha(receiptPath)===renderReceiptSha256&&sha(__filename)===scriptSha256,'Presentation changed during QA');
    for(const ref of receipt.presentationHelpers)check(sha(ref.path)===ref.sha256,'Helper changed during QA');
    for(const key of order)check(sha(path.join(family,receipt.fragments[key].path))===receipt.fragments[key].sha256&&sha(path.join(family,receipt.payloads[key].path))===receipt.payloads[key].sha256,'Reviewed artifact changed');
    save('preview-verification.json',{schema:'common-support-switch-preview-v1',passed:true,createdAt:new Date().toISOString(),
      scope:'Standalone sandbox iframe; two signed account-contrast charts at three widths/themes and all source table identities/pages. Not a live host insertion or human visual review.',
      renderReceiptSha256,scriptSha256,fragments:receipt.fragments,payloads:receipt.payloads,deliveryOrder:order,tableRows:receipt.tableRows,
      presentationHelpers:receipt.presentationHelpers,images,chartViews,sourceViews,tableChecks,interactions,errors,
      screenshotsVisuallyInspected:false,actualMarketRuns:0,analysisArtifactsModified:false});
    console.log(JSON.stringify({passed:true,report:path.join(out,'preview-verification.json'),reportSha256:sha(path.join(out,'preview-verification.json')),images:images.length}));
  }finally{await browser.close();}
})().catch(error=>{if(started&&!fs.existsSync(path.join(out,'failure.json'))&&!fs.existsSync(path.join(out,'preview-verification.json')))save('failure.json',{passed:false,message:error.message,stack:error.stack});console.error(error);process.exitCode=1;});
