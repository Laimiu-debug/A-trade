const {app, protocol, dialog, session} = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {BusinessService} = require('../desktop/business-service.cjs');
const {AIService} = require('../desktop/ai-service.cjs');
const directory = process.argv[2];
if (!directory) throw new Error('Missing isolated stock pool workspace directory');
const root = path.resolve(__dirname, '..');
const dataDirectory = path.join(directory, 'workspace');
const fixtureDirectory = path.join(directory, 'tdx-fixture');
const creates = [], reads = [], errors = [], aiRequests = [];
let win, configureCount = 0;
app.setPath('userData', path.join(directory, 'profile'));
protocol.registerSchemesAsPrivileged([{scheme:'atrade', privileges:{standard:true, secure:true, supportFetchAPI:true}}]);
dialog.showMessageBox = async (_window, options) => {throw new Error(`Unexpected dialog: ${options.message}`);};
// Observe the real desktop service without replacing the IPC, Python worker,
// local source preparation, screening calculations, or returned results.
const request = BusinessService.prototype.request;
BusinessService.prototype.request = function(method, payload = {}, ...rest) {
  if (method === 'market.configure') configureCount++;
  const watched = method === 'research.create' && payload.kind === 'poolPreview' ? creates : method === 'research.get' ? reads : null;
  const entry = watched ? {payload:structuredClone(payload), response:null} : null;
  if (entry) watched.push(entry);
  const result = request.call(this, method, payload, ...rest);
  return entry ? Promise.resolve(result).then(response => {entry.response = response; return response;}) : result;
};
const aiDispatch = AIService.prototype.dispatch;
AIService.prototype.dispatch = function(method, payload) {
  if (method === 'page.ask') aiRequests.push(method);
  return aiDispatch.call(this, method, payload);
};
const evaluate = code => win.webContents.executeJavaScript(code).catch(error => {throw new Error(`${error.message} [${code.slice(0, 180)}]`);});
async function wake() {
  await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true});
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
}
async function until(expression, label, timeout = 90000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await wake();
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  throw new Error(`${label}: ${await evaluate('document.querySelector("main")?.innerText.slice(-4000)')}`);
}
const click = selector => evaluate(`(()=>{const el=[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length);if(!el||el.disabled)throw new Error('Missing active '+${JSON.stringify(selector)});el.click();})()`);
async function input(selector, value) {
  await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el||el.disabled)throw new Error('Missing enabled '+${JSON.stringify(selector)});if(el.type==='checkbox')el.checked=${JSON.stringify(value)};else el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(el.tagName==='SELECT'||el.type==='checkbox'?'change':'input',{bubbles:true}));})()`);
}
async function reveal(selector) {
  await evaluate(`(()=>{let el=document.querySelector(${JSON.stringify(selector)});if(!el)throw new Error('Missing '+${JSON.stringify(selector)});const details=[];for(let parent=el.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')details.unshift(parent);for(const detail of details)if(!detail.open)detail.querySelector(':scope > summary').click();})()`);
  await wake();
}
async function screenshot(name, selector) {
  if (selector) await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start',behavior:'instant'});window.scrollBy({top:-95,behavior:'instant'});true`);
  const [width, height] = win.getSize(); win.setSize(width + 1, height); await wake(); win.setSize(width, height); await wake();
  await fs.mkdir(path.join(root, 'artifacts'), {recursive:true});
  await fs.writeFile(path.join(root, 'artifacts', name), (await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true})).toPNG());
}
async function checkPoolEvidence(rows, {excluded = false, expand = false, layers = ['base','trend','structure','volume']} = {}) {
  await until(`document.querySelectorAll('[data-pf-pool-evidence]').length===${rows.length}`, '每只股票展示可审查的条件依据');
  await wake();
  const layout = await evaluate(`(()=>{const cell=document.querySelector('[data-pf-pool-evidence]'),box=cell.getBoundingClientRect(),clip=cell.closest('.rs-table-wrap').getBoundingClientRect();return {left:box.left,right:box.right,width:box.width,clipLeft:clip.left,clipRight:Math.min(clip.right,innerWidth)};})()`);
  assert.ok(layout.left>=layout.clipLeft-1&&layout.right<=layout.clipRight+1&&layout.width>=240, '默认桌面视图须完整显示可读的入池依据列：'+JSON.stringify(layout));
  const displayed = await evaluate(`(()=>{const cells=[...document.querySelectorAll('[data-pf-pool-evidence]')],table=cells[0].closest('table');return {headers:[...table.tHead.rows[0].cells].map(cell=>cell.textContent),rows:cells.map(cell=>({summary:cell.querySelector('[data-pf-evidence-summary]')?.textContent||'',text:cell.textContent}))};})()`);
  assert.ok(displayed.headers.includes(excluded ? '未通过的基础条件' : '入池依据（实际值 / 条件）'));
  assert.ok(displayed.headers.includes('所属股票池'));
  assert.ok(!displayed.headers.some(label=>/策略|评分|排名|股票池归属 \/ 排除原因/.test(label)), '池条件依据不混入策略评分列');
  if (!excluded) assert.ok(!displayed.headers.some(label=>/排除|未通过的基础条件/.test(label)), '合格池不展示排除原因列');
  rows.forEach((row,index)=>{
    assert.equal(Object.hasOwn(row,'matchedStrategies'), false, '筛选结果不附加策略匹配及评分');
    assert.ok(row.poolEvidence?.base?.conditions?.length, '基础条件证据来自真实筛选结果');
    assert.ok(displayed.rows[index].summary.length > 15 && /\d/.test(displayed.rows[index].summary), '摘要包含条件实际数值，不只显示池名称');
    assert.doesNotMatch(displayed.rows[index].summary, /matchedStrategies|rawScore|strategyIds/);
  });
  if (expand) {
    await click('[data-pf-evidence-details] > summary');
    await until('document.querySelector("[data-pf-evidence-details]")?.open===true', '点击入池依据展开完整条件');
    const expandedLayout = await evaluate(`(()=>{const body=document.querySelector('.pf-pool-evidence-body'),cell=body.closest('td');return {height:body.getBoundingClientRect().height,scrolls:body.scrollHeight>body.clientHeight,aligned:[...cell.parentElement.cells].every(td=>getComputedStyle(td).verticalAlign==='top')};})()`);
    assert.ok(expandedLayout.height<=430&&expandedLayout.scrolls&&expandedLayout.aligned, '展开长依据在局部滚动，股票与K线保持顶部对齐：'+JSON.stringify(expandedLayout));
    const fullText = await evaluate('document.querySelector("[data-pf-pool-evidence]").innerText');
    for (const layer of excluded ? ['base'] : layers) {
      for (const condition of rows[0].poolEvidence[layer]?.conditions || []) {
        if (condition.enabled !== false) assert.ok(fullText.includes(condition.label), `展开依据显示 ${condition.label}`);
      }
    }
  }
  return displayed;
}
async function fixture() {
  const dates = [];
  for (let day = new Date('2025-10-01T00:00:00Z'); dates.length < 260; day = new Date(day.getTime() + 86400000)) {
    if (day.getUTCDay() > 0 && day.getUTCDay() < 6) dates.push(day.toISOString().slice(0, 10));
  }
  const entries = [], marketEntries = {sh:[],sz:[],bj:[]};
  for (let i = 0; i < 136; i++) {
    const market = ['sh','sz','sz','sh','bj'][i % 5];
    const code = String([600000,1,300001,688001,920001][i % 5] + i).padStart(6, '0');
    const group = Math.floor(i / 34);
    const entry = {symbol:market + code, market, code, group, name:`POOL ${['BOTH','TREND','VOLUME','NONE'][group]} ${String(i).padStart(3, '0')}`};
    entries.push(entry); marketEntries[market].push(entry);
  }
  await fs.mkdir(path.join(fixtureDirectory, 'T0002', 'hq_cache'), {recursive:true});
  for (const [market, stocks] of Object.entries(marketEntries)) {
    await fs.mkdir(path.join(fixtureDirectory, 'vipdoc', market, 'lday'), {recursive:true});
    const tnf = Buffer.alloc(50 + stocks.length * 360);
    for (const [index, stock] of stocks.entries()) {
      tnf.write(stock.code, 50 + index * 360, 'ascii'); tnf.write(stock.name, 50 + index * 360 + 31, 'ascii');
      const bytes = Buffer.alloc(dates.length * 32); let close = 1400;
      for (let i = 0; i < dates.length; i++) {
        const open = close; close += stock.group < 2 ? 2 : -1;
        const volume = i === dates.length - 1 && [0,2].includes(stock.group) ? 4000000 : 2000000, at = i * 32;
        bytes.writeUInt32LE(Number(dates[i].replaceAll('-', '')), at);
        bytes.writeUInt32LE(open, at + 4); bytes.writeUInt32LE(Math.max(open, close) + 1, at + 8);
        bytes.writeUInt32LE(Math.min(open, close) - 1, at + 12); bytes.writeUInt32LE(close, at + 16);
        bytes.writeFloatLE(close / 100 * volume, at + 20); bytes.writeUInt32LE(volume, at + 24);
      }
      await fs.writeFile(path.join(fixtureDirectory, 'vipdoc', market, 'lday', stock.symbol + '.day'), bytes);
    }
    await fs.writeFile(path.join(fixtureDirectory, 'T0002', 'hq_cache', market + 's.tnf'), tnf);
  }
  const guardDirectory = path.join(directory, 'network-guard'); await fs.mkdir(guardDirectory, {recursive:true});
  await fs.writeFile(path.join(guardDirectory, 'sitecustomize.py'), "import urllib.request\ndef blocked(self, request, *args, **kwargs):\n raise RuntimeError('Public network disabled in isolated stock pool smoke')\nurllib.request.OpenerDirector.open = blocked\n");
  process.env.PYTHONPATH = [guardDirectory, root].join(path.delimiter);
  return {dates,entries};
}

async function checkWorkspace(dates, entries) {
  const lastDate = dates.at(-1), amountField = '[data-pf-field="config.universe.minAmount20"]';
  await click('[data-nav-group="M"]');
  await until('!!document.querySelector("[data-nav=M06]")?.getClientRects().length', '市场导航直接提供股票池入口');
  await click('[data-nav="M06"]');
  await until(`AT.state.page==='M06'&&!!document.querySelector(${JSON.stringify(amountField)})`, '独立股票池表单');
  assert.equal(await evaluate('document.querySelector("#main h1")?.textContent.includes("股票池")'), true);
  assert.equal(await evaluate('Array.from(document.querySelectorAll("#market-source-path,[data-pf-action=import-history]")).filter(el=>el.getClientRects().length&&!el.closest("details:not([open])")).length'), 0, '股票池复用全局源，无可见重复目录配置或导入要求');
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(amountField)}).closest('label').textContent.includes('万元')`), true);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(amountField)}).value`), '1000', '默认 1000 万元以用户单位显示');
  const checked = await evaluate(`Array.from(document.querySelectorAll('[data-pf-field^="market."],[data-pf-field^="board."]'),el=>({field:el.dataset.pfField,checked:el.checked}))`);
  assert.deepEqual(checked.filter(item=>item.checked).map(item=>item.field).sort(), ['board.bse','board.chinext','board.main','board.star','market.bj','market.sh','market.sz'], '默认覆盖沪深北及所有 A 股交易板');
  await input(amountField, '1000');
  for (const [layer, enabled] of Object.entries({trend:true,structure:false,volume:true})) await input(`[data-pf-pool-layer="${layer}"]`, enabled);
  await input('[data-pf-pool-mode]', 'intersection');
  await screenshot('stock-pool-workspace-settings.png');

  async function resultAfter(since, expectedTotal, offset = 0, query = '', pool = 'combined', accept = () => true) {
    const start = Date.now();
    while (Date.now() - start < 90000) {
      const match = reads.slice(since).reverse().find(entry => {
        const result = entry.response?.data?.result, page = result?.poolPagination?.[pool];
        return entry.response?.ok && entry.payload.pool === pool && page?.total === expectedTotal && page.offset === offset && (entry.payload.poolQuery || '') === query && accept(result);
      });
      if (match) return match.response.data.result;
      await wake(); await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw new Error(`No ${pool} pool reply ${expectedTotal}/${offset}/${query}: ${await evaluate('document.querySelector("main").innerText.slice(-2000)')}`);
  }
  const initialRead = reads.length;
  await click('[data-pf-action="screen-preview"]');
  const intersection = await resultAfter(initialRead, 34);
  await until('AT.state.page==="M06"&&document.querySelector("main").innerText.includes("匹配 34 只")', '交集 34 只原地显示');
  assert.equal(creates.length, 1);
  const created = creates[0].payload;
  assert.equal(created.selection.source, 'local'); assert.equal(created.selection.mode, 'all');
  assert.deepEqual([...created.selection.markets].sort(), ['bj','sh','sz']);
  assert.deepEqual([...created.selection.boards].sort(), ['bse','chinext','main','star']);
  assert.deepEqual(created.selection.symbols, []);
  for (const key of ['strategies','combinations','allocation','strategyIds','strategyParams','topN']) assert.equal(Object.hasOwn(created.config,key), false, '筛选请求不携带策略或排名配置：'+key);
  assert.equal(created.config.universe.minAmount20, 10000000, '1000 万元提交为 10000000 元');
  assert.equal(created.config.universe.minAmplitude20,null);
  assert.equal(created.config.poolSettings.trend.rankEnabled,false, '新增排名默认关闭，原筛选保持不变');
  assert.equal(created.config.poolSettings.structure.minMa10Above20Days,null);
  assert.equal(created.config.poolSettings.volume.minVolumeChange20,null);
  assert.equal(created.config.poolSettings.volume.excludeBlowoff,false);
  assert.equal(intersection.coverage.totalSecurities, 136, '先枚举完整来源');
  assert.equal(intersection.poolCounts.base, 136); assert.equal(intersection.poolCounts.trend, 68); assert.equal(intersection.poolCounts.volume, 68);
  assert.equal(intersection.poolSelection.matchedCount, 34);
  assert.equal(intersection.screenPools.combined.length, 34);
  assert.ok(intersection.screenPools.combined.every(row => entries.find(entry=>entry.symbol===row.symbol)?.group===0), 'AND 仅包含同时符合趋势和量能的 34 只');
  assert.ok(intersection.screenPools.combined.every(row=>row.memberships.includes('trend')&&row.memberships.includes('volume')));
  assert.equal(intersection.protocol.strategyScoring, false);
  assert.equal(intersection.protocol.sortOrder, 'amount_desc_symbol_asc');
  for (const row of intersection.screenPools.combined) {
    assert.equal(row.poolEvidence.base.passed, true); assert.equal(row.poolEvidence.trend.passed, true); assert.equal(row.poolEvidence.volume.passed, true);
    const amount = row.poolEvidence.base.conditions.find(condition=>condition.key==='amount20');
    assert.equal(amount.actual,row.amount20); assert.equal(amount.threshold,10000000); assert.equal(amount.unit,'元'); assert.equal(amount.passed,true);
  }
  await checkPoolEvidence(intersection.screenPools.combined);
  await screenshot('stock-pool-workspace-intersection.png', '[data-pf-screen-query]');

  const beforeUnion = reads.length;
  await input('[data-pf-pool-mode]', 'union');
  const union = await resultAfter(beforeUnion, 102);
  await until('document.querySelector("main").innerText.includes("匹配 102 只")', '并集保留完整 102 只统计');
  assert.equal(creates.length, 1, 'AND/OR 切换只查询已冻结的完整池，不重复创建任务');
  assert.equal(union.screenPools.combined.length, 100, '并集只将前 100 行传给当前页');
  assert.equal(union.poolPagination.combined.unfilteredTotal, 102); assert.equal(union.poolPagination.combined.hasMore, true);
  const beforeNext = reads.length;
  await click('[data-pf-action="screen-page"][data-pf-offset="100"]');
  const second = await resultAfter(beforeNext, 102, 100);
  await until('document.querySelector("main").innerText.includes("101 — 102")', '并集第二页');
  assert.equal(second.screenPools.combined.length, 2);
  assert.deepEqual(new Set([...union.screenPools.combined,...second.screenPools.combined].map(row=>row.symbol)), new Set(entries.filter(row=>row.group<3).map(row=>row.symbol)), '两页恰好包含完整 OR 集合，没有漏项或重复');
  const allUnion = [...union.screenPools.combined,...second.screenPools.combined];
  assert.deepEqual(allUnion.map(row=>row.symbol), [...allUnion].sort((a,b)=>(b.amount||0)-(a.amount||0)||a.symbol.localeCompare(b.symbol)).map(row=>row.symbol), '池结果仅按成交额和代码稳定排序');
  const searched = second.screenPools.combined[0], beforeSearch = reads.length;
  await input('[data-pf-screen-query]', searched.symbol);
  await click('[data-pf-action="screen-search"]');
  const search = await resultAfter(beforeSearch, 1, 0, searched.symbol);
  await until('document.querySelector("main").innerText.includes("匹配 1 只")', '搜索命中原第二页证券');
  assert.equal(search.poolPagination.combined.unfilteredTotal, 102);
  assert.equal(search.screenPools.combined[0].symbol, searched.symbol);
  assert.equal(search.poolSelection.matchedCount, 102); assert.equal(search.poolSelection.filteredCount, 1);
  const searchEvidence = await checkPoolEvidence(search.screenPools.combined, {expand:true});
  assert.match(searchEvidence.rows[0].text, /成交|量能|趋势/);
  assert.match(searchEvidence.rows[0].summary, /满足任一条件/);
  assert.ok(searchEvidence.rows[0].text.includes((searched.amount20/10000).toLocaleString('zh-CN',{maximumFractionDigits:4})+' 万元'), '展开依据的20日均额等于这只股票的实际统计值');
  assert.ok(searchEvidence.rows[0].text.includes('1,000 万元'), '展开依据明确显示本次基础成交额门槛');
  await screenshot('stock-pool-workspace-evidence.png', '[data-pf-pool-evidence]');
  await click('[data-pf-evidence-details] > summary');
  await screenshot('stock-pool-workspace-search.png', '[data-pf-screen-query]');
  assert.equal(await evaluate('AT.state.page'), 'M06', '分页搜索始终留在股票池页');

  await evaluate('document.querySelector("[data-stock-kline]").scrollIntoView({block:"center",behavior:"instant"});true');
  await until('document.querySelector("[data-stock-kline]")?.querySelectorAll("[data-kline-candle]").length===30', '股票池结果显示真实 30 根日 K');
  assert.equal(await evaluate('document.querySelector("[data-stock-kline]").dataset.stockKlineDate'), lastDate);
  await click('[data-page-ai="open"]');
  await until('!document.querySelector("#page-ai-panel").hidden&&document.querySelector(".page-ai-context").textContent.includes("股票池")', '新页 AI 入口绑定股票池上下文');
  const context = await evaluate('AT.pageAIContext.capture()');
  assert.equal(context.page.id, 'M06'); assert.ok(context.dataDates.includes(lastDate));
  assert.ok(JSON.stringify(context).includes(searched.symbol), 'AI 当前页材料包含搜索结果证券');
  assert.equal(await evaluate('AT.state.page'), 'M06');
  await click('[data-page-ai="close"]');
  await click('[data-stock-kline]');
  await until(`AT.state.page==='G01'&&AT.state.stock===${JSON.stringify(searched.symbol)}&&document.querySelector('[data-market-date]')?.value===${JSON.stringify(lastDate)}`, '股票池日 K 打开完整图并定位筛选日期');
  await evaluate('AT.navigate("M06");true');
  await until('AT.state.page==="M06"&&!!document.querySelector("[data-pf-screen-query]")', '返回独立股票池');
  assert.equal(await evaluate('document.querySelector("[data-pf-screen-query]").value'), searched.symbol, '返回保留本页搜索结果');

  let since = reads.length;
  await click('[data-pf-action="screen-clear"]');
  await resultAfter(since, 102);
  since = reads.length;
  await click('[data-pf-action="screen-pool"][data-pf-pool="base"]');
  const base = await resultAfter(since, 136, 0, '', 'base');
  await until('document.querySelector("[data-pf-pool-result-title]")?.textContent==="基础可交易池"&&document.querySelector("[data-pf-pool-pagination]")?.textContent.includes("匹配 136 只")', '单独基础池按其全量分页');
  assert.equal(base.screenPools.base.length, 100); assert.equal(base.poolPagination.base.hasMore, true);
  const baseOnly = entries.find(entry=>entry.group===3).symbol;
  since = reads.length;
  await input('[data-pf-screen-query]', baseOnly); await click('[data-pf-action="screen-search"]');
  const baseSearch = await resultAfter(since, 1, 0, baseOnly, 'base');
  await until('document.querySelector("[data-pf-pool-pagination]")?.textContent.includes("匹配 1 只")', '基础池搜索不受自选叠加条件限制');
  assert.equal(baseSearch.screenPools.base[0].symbol, baseOnly);
  assert.equal(baseSearch.poolPagination.base.unfilteredTotal, 136);
  since = reads.length;
  await click('[data-pf-action="screen-pool"][data-pf-pool="excluded"]');
  const excluded = await resultAfter(since, 0, 0, baseOnly, 'excluded');
  await until('document.querySelector("[data-pf-pool-result-title]")?.textContent==="基础资格排除"&&document.querySelector("[data-pf-pool-pagination]")?.textContent.includes("匹配 0 只")', '排除池显示独立空结果');
  assert.deepEqual(excluded.screenPools.excluded, []);
  since = reads.length;
  await input('[data-pf-screen-query]', 'POOL NONE'); await click('[data-pf-action="screen-search"]');
  await resultAfter(since, 0, 0, 'POOL NONE', 'excluded');
  await until('document.querySelector("[data-pf-screen-query]")?.value==="POOL NONE"&&!document.querySelector("main [data-stock-kline]")', '排除池搜索无结果不泄漏其他池股票');
  since = reads.length;
  await click('[data-pf-action="screen-clear"]'); await resultAfter(since, 0, 0, '', 'excluded');
  since = reads.length;
  await click('[data-pf-action="screen-combined"]'); await resultAfter(since, 102);
  await until('document.querySelector("[data-pf-pool-result-title]")?.textContent==="叠加筛选池"&&document.querySelector("[data-pf-pool-pagination]")?.textContent.includes("匹配 102 只")', '从单独池返回自选并集');
  for (const [index, stage] of ['base','trend','structure','volume'].entries()) {
    since = reads.length;
    await click(`[data-pf-action="screen-funnel"][data-pf-stage="${stage}"]`);
    await resultAfter(since, [136,68,0,0][index]);
    await until(`document.querySelector('[data-pf-action="screen-funnel"][data-pf-stage="${stage}"]')?.getAttribute('aria-pressed')==='true'&&document.querySelector('[data-pf-pool-pagination]')?.textContent.includes('匹配 ${[136,68,0,0][index]} 只')`, `累计漏斗 ${stage} 点击生效`);
    assert.deepEqual(await evaluate('Array.from(document.querySelectorAll("[data-pf-pool-layer]:checked"),el=>el.dataset.pfPoolLayer)'), ['trend','structure','volume'].slice(0,index));
    assert.equal(await evaluate('document.querySelector("[data-pf-pool-mode]").value'), 'intersection');
  }
  assert.equal(creates.length, 1, '单独池、搜索和累计漏斗始终重查同一份完整结果');
  assert.equal(await evaluate('AT.state.page'), 'M06');

  // A second explicit screening request exercises actual base failures without
  // changing the 136-stock AND/OR, paging, or navigation fixture above.
  await input(amountField, '10000');
  since = reads.length;
  await click('[data-pf-action="screen-preview"]');
  const strict = await resultAfter(since, 0, 0, '', 'combined', result=>result.config?.universe.minAmount20===100000000);
  assert.equal(creates.length, 2, '只有显式修改基础条件并重新筛选才新建任务');
  assert.equal(creates[1].payload.config.universe.minAmount20,100000000);
  assert.equal(strict.poolCounts.base,0); assert.equal(strict.poolCounts.excluded,136);
  await until('document.querySelector("[data-pf-action=screen-pool][data-pf-pool=excluded]")?.textContent.includes("136")', '新高门槛筛选显示全部基础排除');
  since = reads.length;
  await click('[data-pf-action="screen-pool"][data-pf-pool="excluded"]');
  const rejected = await resultAfter(since, 136, 0, '', 'excluded');
  await until('document.querySelector("[data-pf-pool-result-title]")?.textContent==="基础资格排除"', '展示高门槛排除池');
  const rejectedEvidence = await checkPoolEvidence(rejected.screenPools.excluded, {excluded:true});
  for (const row of rejected.screenPools.excluded) {
    assert.equal(row.eligible,false); assert.equal(row.poolEvidence.base.reason,'amount20BelowMinimum');
    const condition = row.poolEvidence.base.conditions.find(item=>item.key==='amount20');
    assert.equal(condition.actual,row.amount20); assert.equal(condition.threshold,100000000); assert.equal(condition.passed,false);
  }
  assert.match(rejectedEvidence.rows[0].summary, /20|均额|成交额/);
  assert.match(rejectedEvidence.rows[0].summary.replaceAll(',',''), /10000/);
  assert.match(rejectedEvidence.rows[0].summary, /万元/);
  await screenshot('stock-pool-workspace-excluded.png', '[data-pf-screen-query]');

  const field = key => `[data-pf-field="config.poolSettings.${key}"]`;
  async function newScreen(total, accept) {
    const start=reads.length, count=creates.length;
    await click('[data-pf-action="screen-preview"]');
    const result=await resultAfter(start,total,0,'','combined',accept);
    assert.equal(creates.length,count+1);
    await wake();
    await until(`document.querySelector('[data-pf-pool-pagination]')?.textContent.includes('匹配 ${total} 只')`, '新增精细条件实际重新筛选');
    return result;
  }
  async function chooseLayer(selected) {
    for (const layer of ['trend','structure','volume']) await input(`[data-pf-pool-layer="${layer}"]`,layer===selected);
    await input('[data-pf-pool-mode]','intersection');
  }
  const condition=(row,layer,key)=>row.poolEvidence[layer].conditions.find(item=>item.key===key);

  await reveal(amountField); await input(amountField,'1000');
  await chooseLayer('trend');
  await reveal(field('trend.rankEnabled'));
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(field('trend.rankEnd'))}).disabled`),true);
  await input(field('trend.rankEnabled'),true);
  await input(field('trend.rankWindow'),'40'); await input(field('trend.rankStart'),'1'); await input(field('trend.rankEnd'),'10');
  await screenshot('stock-pool-advanced-ranking-settings.png','[data-pf-detail="editor:pool-fine-trend"]');
  const ranked=await newScreen(10,result=>result.config.poolSettings.trend.rankEnabled&&result.config.poolSettings.trend.rankEnd===10&&result.config.universe.minAmount20===10000000);
  assert.equal(ranked.poolCounts.base,136); assert.equal(ranked.poolCounts.trend,10);
  const rankedRows=ranked.screenPools.combined;
  assert.deepEqual(rankedRows.map(row=>condition(row,'trend','returnRank').actual).sort((a,b)=>a-b),[1,2,3,4,5,6,7,8,9,10]);
  for (const row of rankedRows) {
    const evidence=row.poolEvidence.trend,rank=condition(row,'trend','returnRank');
    assert.equal(evidence.rankUniverseCount,136); assert.equal(evidence.rankWindow,40); assert.equal(evidence.tiePolicy,'return-desc-symbol-asc');
    assert.equal(rank.passed,true); assert.deepEqual(rank.threshold,[1,10]);
  }
  const rankTarget=rankedRows.find(row=>condition(row,'trend','returnRank').actual===7);
  since=reads.length; await input('[data-pf-screen-query]',rankTarget.symbol); await click('[data-pf-action="screen-search"]');
  const rankSearch=await resultAfter(since,1,0,rankTarget.symbol);
  await until('document.querySelector("[data-pf-pool-pagination]")?.textContent.includes("匹配 1 只")','排名条件之后搜索');
  assert.deepEqual(condition(rankSearch.screenPools.combined[0],'trend','returnRank'),condition(rankTarget,'trend','returnRank'),'搜索不重新给单只证券排名');
  assert.equal(rankSearch.screenPools.combined[0].poolEvidence.trend.rankUniverseCount,136);
  await checkPoolEvidence(rankSearch.screenPools.combined,{expand:true});
  const rankingText=await evaluate('document.querySelector("[data-pf-pool-evidence]").innerText');
  assert.match(rankingText,/排名/); assert.match(rankingText,/7/); assert.match(rankingText,/136/);
  await screenshot('stock-pool-advanced-ranking-evidence.png','[data-pf-pool-evidence]');

  await reveal(field('structure.minDrawdown'));
  await chooseLayer('structure');
  await input(field('trend.rankEnabled'),false);
  await input(field('structure.minDrawdown'),'0'); await input(field('structure.maxDrawdown'),'20');
  const maOptional='[data-pf-optional="config.poolSettings.structure.minMa10Above20Days"]';
  await reveal(maOptional);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(field('structure.minMa10Above20Days'))}).disabled`),true);
  await input(maOptional,true); await input(field('structure.minMa10Above20Days'),'1');
  await input(field('structure.maDaysMode'),'count');
  const counted=await newScreen(68,result=>result.config.poolSettings.structure.minMa10Above20Days===1&&result.config.poolSettings.structure.maDaysMode==='count');
  assert.equal(counted.poolCounts.base,136);
  for (const row of counted.screenPools.combined) {
    assert.equal(row.poolEvidence.structure.maDaysMode,'count');
    assert.equal(condition(row,'structure','ma10Above20Days').actual,20); assert.equal(condition(row,'structure','ma10Above20Days').threshold,1);
    assert.ok(entries.find(entry=>entry.symbol===row.symbol).group<2,'只有上行样本通过均线持续性');
  }
  await reveal(field('structure.maDaysMode'));
  await input(field('structure.maDaysMode'),'consecutive'); await input(field('structure.minMa10Above20Days'),'20');
  await input(field('structure.requireAboveMa20'),true);
  await screenshot('stock-pool-advanced-ma-settings.png','[data-pf-detail="editor:pool-fine-structure"]');
  const consecutive=await newScreen(68,result=>result.config.poolSettings.structure.maDaysMode==='consecutive'&&result.config.poolSettings.structure.minMa10Above20Days===20);
  for (const row of consecutive.screenPools.combined) {
    assert.equal(row.poolEvidence.structure.maDaysMode,'consecutive');
    assert.equal(condition(row,'structure','ma10Above20Days').actual,20); assert.equal(condition(row,'structure','ma10Above20Days').threshold,20);
    assert.equal(condition(row,'structure','priceAboveMa20').enabled,true); assert.equal(condition(row,'structure','priceAboveMa20').passed,true);
  }

  await chooseLayer('volume');
  const volumeOptional='[data-pf-optional="config.poolSettings.volume.minVolumeChange20"]';
  await reveal(volumeOptional);
  await input(volumeOptional,true); await input(field('volume.minVolumeChange20'),'150');
  for (const risk of ['excludeBlowoff','excludeDivergence','excludeUpperShadow']) await input(field('volume.'+risk),true);
  await screenshot('stock-pool-advanced-volume-settings.png','[data-pf-detail="editor:pool-fine-volume"]');
  const volumeStrict=await newScreen(0,result=>result.config.poolSettings.volume.minVolumeChange20===1.5&&result.config.poolSettings.volume.excludeUpperShadow);
  assert.equal(volumeStrict.poolCounts.base,136); assert.equal(volumeStrict.poolCounts.volume,0,'150%首尾量增门槛排除原100%放量样本');
  assert.equal(creates.at(-1).payload.config.poolSettings.volume.minVolumeChange20,1.5,'150%在提交时换算为1.5');
  since=reads.length; await click('[data-pf-action="screen-pool"][data-pf-pool="base"]');
  const volumeBase=await resultAfter(since,136,0,'','base');
  const doubled=volumeBase.screenPools.base.find(row=>entries.find(entry=>entry.symbol===row.symbol).group===0);
  const growth=condition(doubled,'volume','volumeChange20');
  assert.equal(growth.actual,1); assert.equal(growth.threshold,1.5); assert.equal(growth.passed,false);
  for (const risk of ['blowoffRisk','divergenceRisk','upperShadowRisk']) {
    const value=condition(doubled,'volume',risk); assert.equal(value.enabled,true); assert.equal(value.actual,false); assert.equal(value.passed,true);
  }
  await reveal(volumeOptional); await input(volumeOptional,false);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(field('volume.minVolumeChange20'))}).disabled`),true,'取消可选门槛后输入框禁用');
  await input(volumeOptional,true);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(field('volume.minVolumeChange20'))}).value`),'5','重新启用使用5%参考值，不恢复已取消的150%');
  await input(field('volume.minVolumeChange20'),'');
  await evaluate(`document.querySelector(${JSON.stringify(field('volume.minVolumeChange20'))}).dispatchEvent(new Event('change',{bubbles:true}));true`);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(volumeOptional)}).checked`),false,'清空数值并失焦关闭可选条件');
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(field('volume.minVolumeChange20'))}).disabled`),true,'清空之后界面与null配置同步');
  await chooseLayer('volume');
  const volumeRestored=await newScreen(68,result=>result.config.poolSettings.volume.minVolumeChange20===null&&result.config.poolSettings.volume.excludeUpperShadow);
  assert.equal(volumeRestored.poolCounts.volume,68,'取消可选量增门槛恢复原量能池，不遗留旧数字');
  assert.equal(creates.at(-1).payload.config.poolSettings.volume.minVolumeChange20,null);
  assert.equal(await evaluate('AT.state.page'),'M06');
}

app.whenReady().then(async () => {
  const {dates, entries} = await fixture();
  session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']}, (_details, callback) => callback({cancel:true}));
  win = await require('../desktop/runtime.cjs').createDesktop({dataDirectory, show:false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', (_event, details) => {if (details.level === 'error') errors.push(details.message);});
    window.webContents.on('preload-error', (_event, _file, error) => errors.push(error.message));
  }});
  await evaluate('window.__poolErrors=[];window.__poolVisited=new Set([AT.state.page]);window.addEventListener("error",event=>__poolErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__poolErrors.push(String(event.reason)));new MutationObserver(()=>__poolVisited.add(AT.state.page)).observe(document.querySelector("main"),{childList:true});');
  assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`)).ok, true);
  const scan = await evaluate("atradeDesktop.market('market.scan')");
  assert.equal(scan.ok, true); assert.equal(scan.data.symbolCount, 136);
  await evaluate('AT.marketUI.refresh()');
  await checkWorkspace(dates, entries);
  assert.equal(configureCount, 1, '独立股票池复用已配置行情源');
  assert.deepEqual(aiRequests, [], '打开页面 AI 不自动请求模型');
  assert.equal(await evaluate('__poolVisited.has("R03")'), false, '独立股票池操作全程无需打开回测编辑页');
  assert.deepEqual(await evaluate('__poolErrors'), []); assert.deepEqual(errors, []);
  console.log('PASS stock pool workspace: market navigation, all markets and boards, amount units, 136 source stocks, intersection34/union102, full-result paging/search, pool condition evidence and exclusions, rank1..10 over136, MA count/consecutive, optional volume threshold and risk evidence, strategy-free requests and ordering, four funnel stages, K-line and page AI, no backtest navigation');
  win.close(); setTimeout(() => app.exit(0), 300);
}).catch(error => {console.error(error.stack);if(win&&!win.isDestroyed())win.destroy();app.exit(1);});
