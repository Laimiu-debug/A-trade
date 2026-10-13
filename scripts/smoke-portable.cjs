const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function reservePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function fixture(directory) {
  const dates = [];
  for (let day = new Date('2023-01-02T00:00:00Z'); dates.length < 340; day = new Date(+day + 86400000)) {
    if (day.getUTCDay() > 0 && day.getUTCDay() < 6) dates.push(day.toISOString().slice(0, 10));
  }
  const destination = path.join(directory, '行情样本', 'vipdoc', 'sh', 'lday');
  await fs.mkdir(destination, { recursive: true });
  for (let stock = 0; stock < 136; stock++) {
    const bytes = Buffer.alloc(dates.length * 32);
    let previous = 1000;
    for (let i = 0; i < dates.length; i++) {
      const close = Math.round(1000 + stock + i * (.5 + stock * .002)), at = i * 32, volume = 2000000;
      bytes.writeUInt32LE(Number(dates[i].replaceAll('-', '')), at);
      bytes.writeUInt32LE(previous, at + 4);
      bytes.writeUInt32LE(Math.max(previous, close) + 2, at + 8);
      bytes.writeUInt32LE(Math.min(previous, close) - 2, at + 12);
      bytes.writeUInt32LE(close, at + 16);
      bytes.writeFloatLE(close / 100 * volume, at + 20);
      bytes.writeUInt32LE(volume, at + 24);
      previous = close;
    }
    await fs.writeFile(path.join(destination, `sh${600000 + stock}.day`), bytes);
  }
  return { dates, source: path.join(directory, '行情样本') };
}
async function launch(application, directory) {
  const port = await reservePort();
  const env = { ...process.env, ATRADE_USER_DATA: path.join(directory, '独立数据'),
    ATRADE_PYTHON: 'Z:\\missing-host-python.exe', PYTHONHOME: 'Z:\\missing-home', PYTHONPATH: 'Z:\\missing-site',
    PATH: path.join(process.env.SystemRoot, 'System32') };
  delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(path.join(application, 'A-trade.exe'), ['--start-hidden', `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1'],
    { cwd: directory, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let tail = '', exitCode, socket;
  child.stdout.on('data', data => { tail = (tail + data).slice(-4000); });
  child.stderr.on('data', data => { tail = (tail + data).slice(-4000); });
  const exited = new Promise(resolve => child.once('exit', code => { exitCode = code; resolve(code); }));
  child.once('error', error => { tail += error.message; exitCode = -1; });
  try {
    let target;
    const started = Date.now();
    while (Date.now() - started < 60000) {
      if (exitCode !== undefined) throw new Error(`Packaged app exited ${exitCode}: ${tail}`);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(2000) });
        target = (await response.json()).find(item => item.type === 'page' && item.url.startsWith('atrade://app/'));
      } catch { /* Chromium's local inspection endpoint is not ready yet. */ }
      if (target) break;
      await delay(150);
    }
    if (!target) throw new Error('Packaged application did not finish startup: ' + tail);
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
    let sequence = 0;
    const pending = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data), item = pending.get(message.id);
      if (!item) return;
      pending.delete(message.id); clearTimeout(item.timer);
      if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result);
    });
    socket.addEventListener('close', () => { for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error('Inspection connection closed')); } pending.clear(); });
    const command = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(new Error(`Timeout: ${method}`)); }, 60000);
      pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async expression => {
      const response = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
      return response.result.value;
    };
    const wait = async (expression, label, timeout = 90000) => {
      const began = Date.now();
      while (Date.now() - began < timeout) { if (await evaluate(expression)) return; await delay(150); }
      throw new Error('Timed out waiting for ' + label);
    };
    await wait('typeof AT !== "undefined" && !!window.ResearchModule && !!window.PortfolioWorkbench && !!document.querySelector("main")', 'packaged UI');
    await evaluate('window.__portableErrors=[];window.addEventListener("error",event=>__portableErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__portableErrors.push(String(event.reason)));');
    return { evaluate, wait, command, child, async close() {
      try { await evaluate('document.querySelector("[data-window-action=close]").click();true'); } catch { /* normal target shutdown */ }
      const code = await Promise.race([exited, delay(12000).then(() => { throw new Error('Packaged app did not close safely.'); })]);
      socket.close(); assert.equal(code, 0, 'normal app exit');
    }, kill() { socket.close(); child.kill(); } };
  } catch (error) { socket?.close(); child.kill(); throw error; }
}
async function main() {
  const application = path.resolve(process.argv[2] || JSON.parse(await fs.readFile(path.join(root, 'dist/latest-portable.json'), 'utf8')).directory);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'atrade-portable-'));
  console.log('Testing packaged exe with no host Python/Node on PATH, in an isolated workspace.');
  const sample = await fixture(directory);
  let session;
  try {
    session = await launch(application, directory);
    const { evaluate, wait } = session;
    const diagnostic = await evaluate('atradeDesktop.diagnostics()');
    assert.equal(diagnostic.ok, true); assert.deepEqual(diagnostic.data.services, { account: true, market: true, research: true });
    assert.equal(path.resolve(await evaluate('atradeDesktop.bootstrap.storage.directory')),
      path.join(directory, '独立数据', 'workspace'), 'all packaged smoke writes must stay in the temporary workspace');
    assert.equal(await evaluate('atradeDesktop.bootstrap.app.packaged'), true);
    assert.match(await evaluate('document.title'), /便携版/);
    assert.equal(await evaluate('typeof require === "undefined" && typeof process === "undefined"'), true);
    assert.equal(await evaluate('!!AT.pageAI && typeof AT.pageAIContext?.capture === "function" && !!document.querySelector("[data-page-ai=open]")'), true, 'packaged page assistant assets');
    const unconfiguredAI = await evaluate("atradeDesktop.ai('page.ask',{requestId:'portable-page-ai',question:'解读此页',context:{page:{id:'T08',title:'连板龙头'},tables:[]}})");
    assert.equal(unconfiguredAI.ok, false);
    assert.match(unconfiguredAI.error, /请先.*文本 AI/, 'packaged assistant route loads without sending to a provider');
    const saved = await evaluate(`atradeDesktop.business('account.save',{name:'便携版隔离验收',kind:'real',startDate:'2023-01-02',openingCash:100000,goalPercent:30,stages:[]})`);
    assert.equal(saved.ok, true, saved.error);
    const configured = await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(sample.source)}})`);
    assert.equal(configured.ok, true, configured.error);
    const scanned = await evaluate("atradeDesktop.market('market.scan')"); assert.equal(scanned.ok, true, scanned.error);
    await evaluate('AT.navigate("M06");true');
    await wait('!!document.querySelector("[data-pf-pool-form]")', 'standalone market stock pool');
    assert.equal(await evaluate('document.querySelector("[data-pf-field=\\"config.universe.minAmount20\\"]").value'), '1000', 'packaged turnover input uses wan yuan');
    assert.equal(await evaluate('document.querySelectorAll("[data-pf-pool-layer]").length'), 3);
    await evaluate('AT.navigate("R03");true');
    await wait('!!document.querySelector("[data-pf-form]")', 'single-strategy editor');
    assert.deepEqual(await evaluate('[...new Set([...document.querySelectorAll("[data-pf-action=workflow-step]")].map(el=>Number(el.dataset.pfStep)))].sort()'), [1,2,3,4,5]);
    assert.equal(await evaluate('document.querySelectorAll(".pf-research-pipeline .pf-pipeline-stage").length'), 6);
    await evaluate('document.querySelector("[data-pf-action=workflow-step][data-pf-step=\\"1\\"]").click();true');
    const singleDraft = await evaluate('JSON.parse(localStorage.getItem("atrade.single-editor.v1")).draft');
    assert.equal(singleDraft.kind, 'singleBacktest');
    assert.equal(singleDraft.periodMode, 'dates');
    assert.equal(Object.values(singleDraft.enabled).filter(Boolean).length, 1);
    assert.equal(singleDraft.config.allocation.mode, 'single');
    assert.equal(singleDraft.selection.mode, 'all');
    await evaluate('document.querySelector("[data-pf-action=advanced]").click();true');
    await wait('document.querySelector(".pf-intro")?.innerText.includes("高级研究")', 'explicit advanced editor');
    await evaluate('document.querySelector("[data-pf-action=workflow-step][data-pf-step=\\"1\\"]").click();true');
    assert.equal(await evaluate('JSON.parse(localStorage.getItem("atrade.portfolio-editor.v1")).draft.kind'), 'portfolio');
    assert.deepEqual(await evaluate('JSON.parse(localStorage.getItem("atrade.single-editor.v1")).draft'), singleDraft,
      'advanced research keeps the single-strategy draft intact');
    const payload = { kind: 'portfolio', name: '便携版 · 39日多策略全池验收',
      config: { startDate: sample.dates[300], endDate: sample.dates[338], initialCapital: 100000, topN: 5,
        maxPositions: 5, maxStockWeight: .2, rebalanceMode: 'risk', maxHoldingDays: 5,
        universe: { minListingDays: 0, minAmount20: 0 },
        strategies: [{ id: 'momentum', kind: 'momentum', pool: 'base', params: { lookback: 5, trendWindow: 8, minReturn: 0 } },
          { id: 'forceGoldenCross', kind: 'forceGoldenCross', pool: 'base', params: {} }] }, selection: { mode: 'all', source: 'local' } };
    const effective = await evaluate(`atradeDesktop.business('research.previewSettings',${JSON.stringify(payload)})`);
    assert.equal(effective.ok, true, effective.error);
    const preview = await evaluate(`atradeDesktop.business('research.create',${JSON.stringify({ ...payload, kind: 'poolPreview' })})`);
    assert.equal(preview.ok, true, preview.error);
    const poolId = preview.data.id;
    await wait(`(async()=>{const r=await atradeDesktop.business('research.get',{id:${JSON.stringify(poolId)}});if(r.data?.task.status==='failed')throw new Error(r.data.task.error);return r.data?.task.status==='completed';})()`, 'full pool preview');
    const pool = await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(poolId)}})`);
    assert.equal(pool.data.task.symbolCount, 136, 'full pool is not truncated at 120');
    const overlap = await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(poolId)},pool:'combined',pools:['trend'],poolMode:'intersection'})`);
    assert.equal(overlap.ok, true, overlap.error);
    assert.equal(overlap.data.result.poolSelection.matchedCount, pool.data.result.poolCounts.trend);
    assert.equal(overlap.data.result.poolSelection.funnelStages.length, 4);
    assert.ok(overlap.data.result.screenPools.combined[0].amount20 > 0, 'packaged service includes actual 20-day turnover');
    assert.equal(overlap.data.result.protocol.strategyScoring, false, 'stock pool never runs strategy scoring');
    assert.equal(overlap.data.result.protocol.sortOrder, 'amount_desc_symbol_asc');
    assert.ok(overlap.data.result.screenPools.combined[0].poolEvidence.trend.conditions.length > 0, 'packaged per-stock pool conditions');
    assert.ok(overlap.data.result.screenPools.combined.every(row => !Object.hasOwn(row, 'matchedStrategies')), 'no strategy hits in pool results');
    const catalogue = await evaluate("atradeDesktop.business('research.catalogue')");
    assert.equal(catalogue.ok, true, catalogue.error);
    const styles = catalogue.data.portfolio.poolStyles;
    assert.deepEqual(styles.map(style => style.id).sort(), ['base', 'breakout', 'momentum', 'pullback', 'volume']);
    assert.equal(styles.find(style => style.id === 'breakout').poolSettings.structure.minDrawdown, 0);
    assert.equal(catalogue.data.portfolio.singleExperimentLimits.maxTrials, 27);
    const singlePayload = { ...payload, kind: 'singleBacktest', name: '便携版 · 20日固定单策略',
      config: { ...payload.config, endDate: sample.dates[319],
        strategies: [payload.config.strategies[0]], combinations: [],
        allocation: { mode: 'single', strategyIds: ['momentum'] },
        poolSettings: styles.find(style => style.id === 'base').poolSettings } };
    const fixed = await evaluate(`atradeDesktop.business('research.create',${JSON.stringify(singlePayload)})`);
    assert.equal(fixed.ok, true, fixed.error);
    const fixedId = fixed.data.id;
    await wait(`(async()=>{const r=await atradeDesktop.business('research.get',{id:${JSON.stringify(fixedId)}});if(r.data?.task.status==='failed')throw new Error(r.data.task.error);return r.data?.task.status==='completed';})()`, 'packaged fixed single strategy');
    const fixedReport = await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(fixedId)},dailyPageSize:200})`);
    assert.equal(fixedReport.data.task.kind, 'singleBacktest');
    assert.equal(fixedReport.data.task.symbolCount, 136);
    assert.equal(fixedReport.data.result.daily.length, 20);
    assert.ok(fixedReport.data.result.trades.length > 0);
    const frozenResponse = await evaluate(`atradeDesktop.business('research.freeze',{id:${JSON.stringify(fixedId)},name:'便携版 · 固定参数冻结'})`);
    assert.equal(frozenResponse.ok, true, frozenResponse.error);
    const frozen = frozenResponse.data.freeze;
    assert.equal(frozen.immutable, true);
    assert.equal(frozen.developmentEnd, sample.dates[319]);
    assert.deepEqual(frozen.config, fixedReport.data.task.config);
    assert.equal(frozen.evidence.certified, false, 'freezing does not certify synthetic research');
    const validationPayload = { kind: 'frozenValidation', freezeId: frozen.id, name: '便携版 · 冻结后19日验证',
      startDate: sample.dates[320], endDate: sample.dates[338] };
    const validation = await evaluate(`atradeDesktop.business('research.create',${JSON.stringify(validationPayload)})`);
    assert.equal(validation.ok, true, validation.error);
    const validationId = validation.data.id;
    await wait(`(async()=>{const r=await atradeDesktop.business('research.get',{id:${JSON.stringify(validationId)}});if(r.data?.task.status==='failed')throw new Error(r.data.task.error);return r.data?.task.status==='completed';})()`, 'packaged later-period validation');
    const validationReport = await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(validationId)},dailyPageSize:200})`);
    assert.equal(validationReport.data.result.daily.length, 19);
    assert.equal(validationReport.data.result.freeze.parameterHash, frozen.parameterHash);
    assert.equal(validationReport.data.result.validationAudit.status, 'fresh');
    assert.equal(validationReport.data.result.validationAudit.independent, true);
    assert.deepEqual({ ...validationReport.data.task.config, startDate: frozen.config.startDate, endDate: frozen.config.endDate },
      frozen.config, 'later-period validation must retain every frozen non-date setting');
    await evaluate('AT.navigate("R10");true');
    await wait('!!document.querySelector("[data-workflow-validation]")', 'packaged frozen-plan library');
    assert.equal(await evaluate('document.querySelector("[data-workflow-freeze]").value'), frozen.id);
    assert.deepEqual(await evaluate('[...document.querySelectorAll("[data-workflow-validation] input")].map(el=>el.dataset.workflowField)'),
      ['name', 'startDate', 'endDate'], 'frozen validation offers only the name and new dates');
    const created = await evaluate(`atradeDesktop.business('research.create',${JSON.stringify(payload)})`);
    assert.equal(created.ok, true, created.error);
    const id = created.data.id;
    await wait(`(async()=>{const r=await atradeDesktop.business('research.get',{id:${JSON.stringify(id)}});if(r.data?.task.status==='failed')throw new Error(r.data.task.error);return r.data?.task.status==='completed';})()`, '39-day portfolio');
    const report = await evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)},dailyPageSize:200})`);
    assert.equal(report.ok, true, report.error);
    assert.equal(report.data.task.symbolCount, 136);
    assert.equal(report.data.result.daily.length, 39);
    assert.equal(report.data.result.config.strategies.length, 2);
    assert.ok(report.data.result.trades.length > 0);
    assert.ok(report.data.result.daily.some(day => day.holdings.length > 1));
    await evaluate('AT.navigate("G02");true');
    await wait(`!!document.querySelector('[data-rs-action="task-open"][data-rs-id="${id}"]')`, 'task list');
    await evaluate(`document.querySelector('[data-rs-action="task-open"][data-rs-id="${id}"]').click();true`);
    await wait('!!document.querySelector(".pf-account-curve")', 'same-page result');
    await fs.mkdir(path.join(root, 'artifacts'), { recursive: true });
    const picture = await session.command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await fs.writeFile(path.join(root, 'artifacts/portable-smoke.png'), Buffer.from(picture.data, 'base64'));
    assert.deepEqual(await evaluate('__portableErrors'), []);
    await session.close(); session = null;
    session = await launch(application, directory);
    const reopened = await session.evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(id)}})`);
    assert.equal(reopened.data.task.status, 'completed');
    assert.deepEqual(reopened.data.result.curve, report.data.result.curve);
    const reopenedFreezes = await session.evaluate("atradeDesktop.business('research.freezes')");
    assert.equal(reopenedFreezes.ok, true, reopenedFreezes.error);
    const reopenedFreeze = reopenedFreezes.data.freezes.find(plan => plan.id === frozen.id);
    assert.ok(reopenedFreeze, 'frozen plan persists across packaged exe restart');
    assert.equal(reopenedFreeze.parameterHash, frozen.parameterHash);
    assert.equal(reopenedFreeze.versionCompatible, true);
    for (const [taskId, kind, expected] of [[fixedId, 'singleBacktest', fixedReport], [validationId, 'frozenValidation', validationReport]]) {
      const persisted = await session.evaluate(`atradeDesktop.business('research.get',{id:${JSON.stringify(taskId)}})`);
      assert.equal(persisted.ok, true, persisted.error);
      assert.equal(persisted.data.task.status, 'completed');
      assert.equal(persisted.data.task.kind, kind);
      assert.deepEqual(persisted.data.result.curve, expected.data.result.curve);
    }
    const workspace = await session.evaluate("atradeDesktop.business('workspace')");
    assert.equal(workspace.data.accounts[0].name, '便携版隔离验收');
    await session.close(); session = null;
    const evidence = { passed: true, directory: application, testedAt: new Date().toISOString(), version: diagnostic.data.app.version,
      fullPoolSize: 136, tradingDays: 39, strategies: 2, singleDevelopmentDays: 20, frozenValidationDays: 19,
      frozenPlanPersisted: true, validationStatus: validationReport.data.result.validationAudit.status,
      hostPythonDisabled: true, restarted: true, isolatedWorkspace: directory };
    await fs.writeFile(path.join(root, 'artifacts/portable-smoke.json'), JSON.stringify(evidence, null, 2) + '\n');
    console.log('PASS packaged launch, built-in services, six-stage single-strategy workflow, 136-stock pool, frozen later-period validation, 39-day advanced portfolio and restart persistence.');
  } finally { session?.kill(); }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
