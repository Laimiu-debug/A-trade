const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawnSync}=require('node:child_process'),{pathToFileURL}=require('node:url');
const {chromium}=require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),folder=path.join(root,'artifacts/strategy-search/final-research-review-v1');
const out=path.join(folder,'preview-qa-'+(process.argv[2]||'0001'));
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const check=(v,m)=>{if(!v)throw Error(m);};
(async()=>{
 fs.mkdirSync(out);const errors=[],views=[],sourceScrollChecks=[];
 const chartInput=JSON.parse(fs.readFileSync(path.join(folder,'selected-chart.json'),'utf8'));
 const previousInput=JSON.parse(fs.readFileSync(path.join(folder,'presentation-before-label-repair/selected-chart.json'),'utf8'));
 const originalRows=chartInput.rows.map(({displayCategory,...row})=>row);
 check(JSON.stringify(originalRows)===JSON.stringify(previousInput.rows),'Reviewed fourteen data rows changed');
 const sourceInput=JSON.parse(fs.readFileSync(path.join(folder,'selected-sources.json'),'utf8'));
 check(JSON.stringify(sourceInput.items[0].queries[0].rows)===JSON.stringify(previousInput.rows),'Source evidence changed');
 const expectedCategories=[...new Set(chartInput.rows.map(r=>r.displayCategory))];
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:780,height:900},reducedMotion:'reduce'});
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  for(const kind of ['chart','sources']){
   const source=path.join(folder,'selected-'+kind+'.html'),preview=path.join(out,kind+'.html');
   const wrapped=spawnSync('C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe',[
    '-X','utf8','-B','C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py',source,preview],{encoding:'utf8'});
   check(wrapped.status===0,wrapped.stderr);
   for(const [width,colorScheme] of [[780,'light'],[360,'light'],[320,'light'],[780,'dark']]){
    await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme,reducedMotion:'reduce'});
    await page.goto(pathToFileURL(preview).href);const frame=page.frameLocator('iframe');
    let categoryLabels=[];
    if(kind==='chart'){
     await frame.locator('.recharts-bar-rectangle').first().waitFor();
     check(await frame.locator('.recharts-bar-rectangle').count()===14,'All fourteen marks required');
     check(await frame.getByText('2022–2023',{exact:true}).count()>0,'Older-period legend missing');
     check(await frame.getByText('2024–2025',{exact:true}).count()>0,'Later-period legend missing');
     categoryLabels=await frame.locator('svg text[data-axis-layout]').evaluateAll(nodes=>nodes.map(node=>({
      text:[...node.children].filter(n=>n.tagName.toLowerCase()!=='title').map(n=>n.textContent.trim()).join(' ').trim(),
      box:(()=>{const r=node.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height};})()
     })));
     check(categoryLabels.length===7,'Seven distinct strategy/board labels required');
     check(JSON.stringify(categoryLabels.map(r=>r.text))===JSON.stringify(expectedCategories),'Category label truncated or changed');
     check(categoryLabels.every(r=>!r.text.includes('…')),'Ellipsized category label');
     for(let i=1;i<categoryLabels.length;i++)check(categoryLabels[i-1].box.x+categoryLabels[i-1].box.width<=categoryLabels[i].box.x+1,'Overlapping category labels');
    }else{
     await frame.locator('.receipt-disclosure').click();
     const card=frame.locator('.receipt-card');if(await card.getAttribute('data-open')!=='true')await card.locator('.receipt-card-toggle').click();
     await card.getByRole('tab',{name:'Data preview',exact:true}).click();
     check(await card.locator('tbody tr').count()===14,'All fourteen evidence rows required');
    }
    const geometry=await frame.locator('body').evaluate(e=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    check(geometry.scroll<=geometry.width+1,'Outer horizontal overflow');
    const screenshot=path.join(out,kind+'-'+width+'-'+colorScheme+'.png');await page.screenshot({path:screenshot,fullPage:true});
    views.push({kind,width,colorScheme,geometry,categoryLabels,image:{path:path.relative(root,screenshot).replaceAll('\\','/'),sha256:sha(screenshot)}});
    if(kind==='sources'){
     const scroll=await frame.locator('.table-wrap').evaluate(e=>{const states=[];while(e){const s=getComputedStyle(e);if(['auto','scroll'].includes(s.overflowY))e.scrollTop=e.scrollHeight;if(['auto','scroll'].includes(s.overflowX))e.scrollLeft=e.scrollWidth;states.push({className:e.className,scrollLeft:e.scrollLeft,scrollTop:e.scrollTop});e=e.parentElement;}return states;});
     const last=frame.locator('tbody tr').last();check((await last.innerText()).includes('支撑收回'),'Last source row missing');
     const image=path.join(out,'sources-'+width+'-'+colorScheme+'-last-values.png');await page.screenshot({path:image,fullPage:true});
     sourceScrollChecks.push({width,colorScheme,scroll,image:{path:path.relative(root,image).replaceAll('\\','/'),sha256:sha(image)}});
    }
   }
  }
  check(errors.length===0,errors.join(';'));
  fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify({passed:true,createdAt:new Date().toISOString(),views,sourceScrollChecks,errors,
   dataIntegrity:{reviewedRows:14,allOriginalFieldsUnchanged:true,sourceRowsUnchanged:true,change:'Presentation-only category alias with word boundary'},
   inputs:['selected-chart.json','selected-chart.html','selected-sources.json','selected-sources.html'].map(name=>({path:name,sha256:sha(path.join(folder,name))})),
   scope:'Local iframe preview: fourteen unchanged chart/source rows, seven complete distinct category labels without overlap at desktop/narrow widths; not live host insertion.'},null,2));
  console.log(JSON.stringify({passed:true,views:views.length,output:out}));
 }finally{await browser.close();}
})().catch(e=>{fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({message:e.message}));console.error(e);process.exitCode=1;});
