// Scoped presentation QA only; no source, strategy, model or account execution.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const {spawnSync} = require('node:child_process'), {pathToFileURL} = require('node:url');
const {chromium} = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const family = path.resolve(__dirname, '../artifacts/strategy-search/online-feedback-entry-v1');
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const wrapper = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const argv = process.argv.slice(2);
if (argv.length % 2) throw Error('Expected --receipt-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({length: argv.length / 2}, (_, i) => [argv[i * 2], argv[i * 2 + 1]]));
if (Object.keys(args).some(k => !['--receipt-sha', '--attempt'].includes(k)) || !/^[a-f0-9]{64}$/.test(args['--receipt-sha'] || '')) throw Error('Exact render receipt SHA required');
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw Error('Four-digit exclusive attempt required');
const out = path.join(family, 'browser-qa', 'attempt-' + attempt);
const order = ['trail10Chart', 'trail10Sources', 'volatilityTrailChart', 'volatilityTrailSources'];
const expectedRows = [[18,18,18,18,12,12,36,72,144,18], [18,18,18,18,12,12,36,72,144,18]];
const sha = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const save = (name, x) => fs.writeFileSync(path.join(out, name), JSON.stringify(x, null, 2) + '\n', {flag: 'wx'});
const check = (x, message) => {if (!x) throw Error(message);};
let started = false;

async function geometry(locator) {
  const value = await locator.evaluate(e => {
    const b = e.getBoundingClientRect();
    return {left:b.left,right:b.right,width:innerWidth,scrollWidth:e.scrollWidth,documentWidth:document.documentElement.scrollWidth};
  });
  check(value.left >= -1 && value.right <= value.width + 1 && value.scrollWidth <= value.width + 1 && value.documentWidth <= value.width + 1,
    'Horizontal outer overflow: ' + JSON.stringify(value));
  return value;
}

(async () => {
  fs.mkdirSync(path.dirname(out), {recursive:true}); fs.mkdirSync(out); started = true;
  const receiptPath = path.join(family, 'render-receipt.json'), receipt = read(receiptPath);
  const renderReceiptSha256 = sha(receiptPath), scriptSha256 = sha(__filename);
  check(renderReceiptSha256 === args['--receipt-sha'], 'Render receipt changed');
  check(JSON.stringify(receipt.deliveryOrder) === JSON.stringify(order) && JSON.stringify(Object.keys(receipt.fragments)) === JSON.stringify(order), 'Two chart/source pairs required');
  check(JSON.stringify(receipt.tableRows) === JSON.stringify(expectedRows) && receipt.chartRows === 36, 'Complete evidence matrix differs');
  check(Array.isArray(receipt.presentationHelpers) && receipt.presentationHelpers.length === 3, 'Three presentation helper references required');
  check(receipt.presentationHelpers.some(r => path.resolve(r.path) === path.resolve(wrapper)), 'QA wrapper reference missing');
  for (const ref of receipt.presentationHelpers) check(sha(ref.path) === ref.sha256, 'Presentation helper binding differs: ' + ref.path);
  const payloads = {};
  for (const key of order) {
    const p = receipt.payloads[key], f = receipt.fragments[key];
    check(sha(path.join(family,p.path)) === p.sha256 && sha(path.join(family,f.path)) === f.sha256, 'Payload/fragment binding differs: ' + key);
    payloads[key] = read(path.join(family,p.path));
    const r = spawnSync(python, ['-X','utf8','-B',wrapper,path.join(family,f.path),path.join(out,key+'-preview.html')], {encoding:'utf8',timeout:30000});
    save(key+'-wrapper-log.json',{status:r.status,stdout:r.stdout,stderr:r.stderr}); check(r.status === 0, 'Wrapper failed: '+key);
  }
  const browser = await chromium.launch({headless:true,channel:'msedge'});
  const images=[], chartViews=[], sourceViews=[], tableChecks=[], interactions=[], errors=[];
  try {
    const page = await browser.newPage({viewport:{width:780,height:900},reducedMotion:'reduce'});
    page.setDefaultTimeout(10000);
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    const capture = async (locator, name, viewport=false) => {
      let targetBounds;
      if (viewport) {
        await locator.evaluate(e=>e.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
        await page.waitForTimeout(300); targetBounds=await locator.boundingBox();
        check(targetBounds && targetBounds.height>0 && targetBounds.y<900 && targetBounds.y+targetBounds.height>0, 'Screenshot target outside viewport');
      }
      const file=path.join(out,name+'.png');
      if(viewport) await page.screenshot({path:file,fullPage:false}); else await locator.screenshot({path:file});
      const ref={path:path.relative(family,file).replaceAll('\\','/'),sha256:sha(file),capture:viewport?'page-viewport':'chart-element',...(targetBounds?{targetBounds}:{})};
      images.push(ref); return ref;
    };
    for (const [index,exit] of ['trail10','volatilityTrail'].entries()) {
      const chartKey=exit+'Chart', sourceKey=exit+'Sources', chartPayload=payloads[chartKey], sources=payloads[sourceKey];
      const values=chartPayload.rows.map(r=>r[chartPayload.chart.y]);
      check(values.length===18 && values.every(Number.isFinite), 'All eighteen signed returns must be retained');
      check(chartPayload.chart.type==='horizontalBar' && chartPayload.chart.startAtZero===true && chartPayload.chart.showValues===false && chartPayload.chart.stackable===false, 'Reviewed native chart configuration differs');
      check(chartPayload.chart.xLabel==='全期账户净收益（%）' && chartPayload.chart.seriesOrder.length===3 && chartPayload.chart.categoryOrder.length===6, 'Units/categories/policies differ');
      check(chartPayload.columns.includes('平均仓位（%）') && chartPayload.columns.includes('估值不可靠日'), 'Risk/quality projection missing');
      check(sources.items.length===1 && JSON.stringify(sources.items[0].queries.map(q=>q.rows.length))===JSON.stringify(expectedRows[index]), 'Exit-specific Sources incomplete');
      const nonzero=values.filter(v=>v!==0).length;
      for (const [width,colorScheme] of [[780,'light'],[360,'light'],[780,'dark']]) {
        await page.setViewportSize({width,height:900}); await page.emulateMedia({colorScheme,reducedMotion:'reduce'});
        await page.goto(pathToFileURL(path.join(out,chartKey+'-preview.html')).href);
        const frame=page.frameLocator('iframe'), chart=frame.locator('.data-inline-react-root');
        await chart.waitFor({timeout:15000}); await chart.locator('svg.recharts-surface').first().waitFor(); await page.waitForTimeout(200);
        const bounds=await geometry(chart);
        const painted=await chart.evaluate(e=>({bars:e.querySelectorAll('.recharts-bar-rectangle').length,
          nonzeroBars:[...e.querySelectorAll('.recharts-bar-rectangle')].filter(x=>{const b=x.getBoundingClientRect();return b.width>0&&b.height>0;}).length,
          categories:[...e.querySelectorAll('svg text')].filter(x=>x.querySelector('title')).map(x=>[...x.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('')),
          nativeTicks:[...e.querySelectorAll('svg text')].filter(x=>!x.querySelector('title')).map(x=>x.textContent.trim()).filter(x=>x!==''&&!Number.isNaN(Number(x.replaceAll(',',''))))}));
        check(painted.nonzeroBars===nonzero && painted.bars>=nonzero && painted.bars<=18, 'Native nonzero marks differ from reviewed accounts: '+JSON.stringify(painted));
        for(const name of chartPayload.chart.categoryOrder)check(painted.categories.includes(name),'Visible category missing or truncated: '+name);
        const zeroLine=chart.locator('.chart-annotation-mark--benchmark .recharts-reference-line-line');
        check(await zeroLine.count()===1,'Native zero reference line missing');
        const zeroGeometry=await chart.evaluate(e=>{
          const line=e.querySelector('.chart-annotation-mark--benchmark .recharts-reference-line-line');
          const marks=[...e.querySelectorAll('.recharts-bar-rectangle .recharts-rectangle')].map(x=>({origin:Number(x.getAttribute('x')),width:Number(x.getAttribute('width'))}));
          const style=getComputedStyle(line);
          return {x1:Number(line.getAttribute('x1')),x2:Number(line.getAttribute('x2')),y1:Number(line.getAttribute('y1')),y2:Number(line.getAttribute('y2')),
            stroke:style.stroke,strokeWidth:Number.parseFloat(style.strokeWidth),display:style.display,visibility:style.visibility,opacity:Number(style.opacity),marks};
        });
        check(Math.abs(zeroGeometry.y2-zeroGeometry.y1)>100 && zeroGeometry.stroke!=='none' && zeroGeometry.strokeWidth>0 && zeroGeometry.opacity>0 &&
          zeroGeometry.display!=='none' && zeroGeometry.visibility!=='hidden' && Math.abs(zeroGeometry.x1-zeroGeometry.x2)<.01 && zeroGeometry.marks.length>=nonzero &&
          zeroGeometry.marks.every(m=>Number.isFinite(m.origin)&&Math.abs(m.origin-zeroGeometry.x1)<.01),'Native zero line differs from signed bar origins');
        const zeroLabel=chart.locator('[data-chart-annotation="zero-return-origin"]');
        check(await zeroLabel.isVisible() && (await zeroLabel.textContent()).replace(/\s+/g,'').includes('0%盈亏分界'),'Visible mathematical zero label missing');
        const axis=chart.locator('.chart-axis-label');
        check(await axis.count()===1 && await axis.isVisible() && (await axis.innerText()).trim()===chartPayload.chart.xLabel,'Actual HTML unit title missing');
        check(await chart.getByText(chartPayload.title,{exact:true}).isVisible(),'Chart title not visible');
        const toggles=frame.getByRole('button',{name:/^Toggle /}); check(await toggles.count()===3,'All three native legend controls required');
        chartViews.push({exit,width,colorScheme,...bounds,...painted,zeroGeometry,reviewedRows:18,zeroRows:18-nonzero,image:await capture(chart,exit+'-chart-'+width+'-'+colorScheme)});
        if(width===780&&colorScheme==='light') {
          await toggles.first().click(); await page.waitForTimeout(120);
          const actual=await chart.locator('.recharts-bar-rectangle').evaluateAll(es=>es.filter(e=>{const b=e.getBoundingClientRect();return b.width>0&&b.height>0;}).length);
          const expected=chartPayload.rows.filter(r=>r[chartPayload.chart.series]!==chartPayload.chart.seriesOrder[0]&&r[chartPayload.chart.y]!==0).length;
          check(actual===expected,'Native legend hide differs'); await toggles.first().click(); await page.waitForTimeout(120);
          await frame.getByRole('button',{name:chartPayload.title+' actions',exact:true}).click();
          await frame.getByRole('menuitem',{name:'Edit chart',exact:true}).click();
          const cancel=frame.getByRole('button',{name:'Cancel',exact:true}); await cancel.waitFor(); await cancel.click();
          check(await cancel.count()===0,'Editor Cancel did not close'); interactions.push({exit,legendHideRestore:true,editorOpenCancel:true,noAppliedChanges:true});
        }
      }
      await page.setViewportSize({width:780,height:900}); await page.emulateMedia({colorScheme:'light',reducedMotion:'reduce'});
      await page.goto(pathToFileURL(path.join(out,sourceKey+'-preview.html')).href);
      const frame=page.frameLocator('iframe'), sourceRoot=frame.locator('.sources-receipt'); await sourceRoot.waitFor({timeout:15000});
      await sourceRoot.locator('.receipt-disclosure').click(); const card=sourceRoot.locator('.receipt-card');
      check(await card.count()===1&&await card.getAttribute('data-open')==='true'&&await card.locator('.receipt-card-toggle').count()===0,'Single scoped finding not open');
      await card.getByRole('tab',{name:'Overview',exact:true}).click(); let panel=card.getByRole('tabpanel');
      const overview=await panel.innerText();
      for(const text of ['126','20','回退','影子','未平','2026'])check(overview.includes(text),'Material caveat missing: '+text);
      await card.getByRole('tab',{name:'Data preview',exact:true}).click(); panel=card.getByRole('tabpanel');
      check(await panel.locator('table').count()===10,'Ten complete source tables required');
      for(let q=0;q<10;q++) {
        const query=sources.items[0].queries[q], table=panel.locator('table').nth(q);
        const fields=['period','boardLabel',q===1?'contrastLabel':'candidateLabel',...(q>=6?['intervalLabel']:[])];
        const columns=fields.map(k=>query.columns.findIndex(c=>c.field===k)); check(columns.every(i=>i>=0),'Identity columns missing');
        const expected=query.rows.map(r=>fields.map(k=>String(r[k]))), seen=[], pages=[];
        const pager=query.rows.length>50?panel.locator('.receipt-pagination').nth(sources.items[0].queries.slice(0,q).filter(x=>x.rows.length>50).length):null;
        for(let offset=0;offset<query.rows.length;offset+=50) {
          const count=Math.min(50,query.rows.length-offset); check(await table.locator('tbody tr').count()===count,'Table page row count differs');
          seen.push(...await table.locator('tbody tr').evaluateAll((rs,is)=>rs.map(r=>is.map(i=>r.querySelectorAll('td')[i].innerText.trim())),columns)); pages.push(count);
          check(await table.locator('tbody td').evaluateAll(es=>es.every(e=>e.innerText.trim().length>0)),'Empty source cell');
          if(offset+count<query.rows.length) {
            await pager.getByRole('button',{name:'Next page',exact:true}).click();
            await pager.getByRole('status').filter({hasText:`Rows ${offset+51}–${Math.min(offset+100,query.rows.length)} of ${query.rows.length}`}).waitFor();
          }
        }
        check(JSON.stringify(seen)===JSON.stringify(expected),'All row identities/order differ: '+query.id);
        tableChecks.push({exit,queryId:query.id,rows:seen.length,pages,allIdentitiesMatched:true});
        if(q===5)await capture(table.locator('tbody tr').last(),exit+'-sources-candidate-only-last-row-780-light',true);
        if(pager) {
          check(await pager.getByRole('button',{name:'Next page',exact:true}).isDisabled(),'Last page unavailable');
          const previous=pager.getByRole('button',{name:'Previous page',exact:true}); while(await previous.isEnabled())await previous.click();
          await pager.getByRole('status').filter({hasText:`Rows 1–50 of ${query.rows.length}`}).waitFor();
        }
      }
      await card.getByRole('tab',{name:'Evidence flow',exact:true}).click(); panel=card.getByRole('tabpanel');
      const verification=panel.getByText('Verification',{exact:true}); await verification.click();
      check(await verification.evaluate(e=>e.closest('details')?.open)===true,'Evidence verification did not expand');
      sourceViews.push({exit,width:780,colorScheme:'light',tables:10,rows:366,...await geometry(sourceRoot)});
    }
    check(images.length===8&&tableChecks.length===20&&errors.length===0,'Scoped QA incomplete or browser errors: '+JSON.stringify(errors));
    check(sha(receiptPath)===renderReceiptSha256&&sha(__filename)===scriptSha256,'QA input/script changed');
    for(const ref of receipt.presentationHelpers)check(sha(ref.path)===ref.sha256,'Presentation helper changed during QA');
    for(const key of order) {
      check(sha(path.join(family,receipt.fragments[key].path))===receipt.fragments[key].sha256,'Fragment changed');
      check(sha(path.join(family,receipt.payloads[key].path))===receipt.payloads[key].sha256,'Payload changed');
    }
    save('preview-verification.json',{schema:'online-feedback-entry-preview-v1',passed:true,createdAt:new Date().toISOString(),
      scope:'Standalone sandbox iframe; two charts at three viewport/theme settings, complete 732 Sources rows and paginated intervals, one candidate-only table viewport per exit. Not live host insertion.',
      renderReceiptSha256,scriptSha256,fragments:receipt.fragments,payloads:receipt.payloads,deliveryOrder:order,tableRows:expectedRows,
      presentationHelpers:receipt.presentationHelpers,
      images,chartViews,sourceViews,tableChecks,interactions,errors,screenshotsVisuallyInspected:false,actualMarketRuns:0,analysisArtifactsModified:false});
    console.log(JSON.stringify({passed:true,report:path.join(out,'preview-verification.json'),reportSha256:sha(path.join(out,'preview-verification.json')),images:images.length}));
  }finally {await browser.close();}
})().catch(error=>{
  if(started&&!fs.existsSync(path.join(out,'failure.json'))&&!fs.existsSync(path.join(out,'preview-verification.json')))
    save('failure.json',{passed:false,message:error.message,stack:error.stack});
  console.error(error);process.exitCode=1;
});
