/* Actual release EXE, isolated storage, real read RPCs, no scans or replays. */
'use strict';
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const assert = require('node:assert/strict'), net = require('node:net');
const { spawn } = require('node:child_process');
const ROOT = path.resolve(__dirname, '..');
const IDS = ['contractionNoProgress5', 'persistentLeaderTrail10', 'strongReclaimVolatilityTrail'];
const PROTECTED = ['accounts.sqlite3', 'market.sqlite3', 'research.sqlite3', 'workspace-state.json', 'workspace-state.previous.json'];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const sha = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const pin = p => ({ path: path.resolve(p), sha256: sha(p) });
const writeNew = (p, value) => fs.writeFileSync(p, JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });

function options(args) {
  const out = {};
  for (let i = 0; i < args.length; i++) {
    const key = args[i];
    if (['--self-test', '--help'].includes(key)) out[key.slice(2)] = true;
    else {
      assert.ok(['--exe', '--build-info', '--evidence', '--output-root', '--expected-version'].includes(key), 'Unknown option: ' + key);
      assert.ok(args[i + 1] && !args[i + 1].startsWith('--'), 'Missing value: ' + key);
      out[key.slice(2)] = args[++i];
    }
  }
  return out;
}
function protectedRoots(verifiedWorkspace, env = process.env) {
  // Codex's MSIX APPDATA alias may differ from the normal desktop APPDATA.
  const values = [verifiedWorkspace,
    env.APPDATA && path.join(env.APPDATA, 'A-trade', 'workspace'),
    env.USERPROFILE && path.join(env.USERPROFILE, 'AppData', 'Roaming', 'A-trade', 'workspace')].filter(Boolean);
  return [...new Map(values.map(p => [path.resolve(p).toLowerCase(), path.resolve(p)])).values()];
}
function snapshot(roots) {
  return Object.fromEntries(roots.flatMap(root => PROTECTED.flatMap(name => {
    const p = path.join(root, name);
    return [p, p + '-wal', p + '-shm'].map(file => [file, fs.existsSync(file) ? sha(file) : null]);
  })));
}
function taskInventory(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).filter(x => x.isDirectory()).map(x => x.name).sort();
}
function pairedSavedEvidence(archived, stored) {
  assert.deepEqual(stored, archived, 'Readable archive and stored compact JSON must have identical content');
  assert.equal(stored.strategies.length, 3);
  assert.deepEqual(stored.strategies.map(s => s.strategyId), IDS);
  assert.deepEqual(stored.strategies.map(s => s.candidates.length), [0, 0, 15]);
  return stored.strategies.flatMap(s => s.candidates.map(c => c.symbol));
}
function selfTest() {
  assert.deepEqual(options(['--exe', 'a.exe', '--expected-version', '0.4.5']), { exe: 'a.exe', 'expected-version': '0.4.5' });
  assert.throws(() => options(['--exe']), /Missing/); assert.throws(() => options(['--unknown']), /Unknown/);
  const example = { strategies: IDS.map((strategyId, i) => ({ strategyId, candidates: Array.from({ length: i === 2 ? 15 : 0 }, (_, j) => ({ symbol: 'sz' + j })) })) };
  assert.equal(pairedSavedEvidence(JSON.parse(JSON.stringify(example, null, 2)), example).length, 15);
  const changed = structuredClone(example); changed.strategies[2].candidates.pop();
  assert.throws(() => pairedSavedEvidence(example, changed));
  assert.equal(protectedRoots('G:/example/workspace', { APPDATA: 'G:/example', USERPROFILE: 'C:/other' }).length, 3);
  assert.equal(protectedRoots('G:/example/A-trade/workspace', { APPDATA: 'G:/example' }).length, 1);
  console.log(JSON.stringify({ passed: true, checks: 7, filesWritten: 0, actualAppLaunches: 0 }));
}
async function reservePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port;
}
async function captureProcess(executable, args, config) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { ...config, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    const timer = setTimeout(() => { child.kill(); reject(Error('Read-only embedded Python probe timed out')); }, 30000);
    child.stdout.on('data', x => { stdout += x; }); child.stderr.on('data', x => { stderr += x; });
    child.once('error', e => { clearTimeout(timer); reject(e); });
    child.once('exit', code => { clearTimeout(timer); if (code !== 0) reject(Error('Embedded Python probe failed: ' + stderr)); else resolve(stdout); });
  });
}
async function launch(executable, directory, env, log) {
  const port = await reservePort(), started = Date.now();
  const child = spawn(executable, ['--start-hidden', '--force-device-scale-factor=1', `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1'],
    { cwd: directory, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let exitCode, tail = '', socket;
  const exited = new Promise(resolve => child.once('exit', code => { exitCode = code; resolve(code); }));
  child.once('error', error => { exitCode = -1; tail += error.stack; });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', data => { log.write(data); tail = (tail + data).slice(-4000); });
  try {
    let target;
    while (Date.now() - started < 180000) {
      if (exitCode !== undefined) throw Error(`Release launcher exited ${exitCode}: ${tail}`);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(1500) });
        target = (await response.json()).find(x => x.type === 'page' && x.url.startsWith('atrade://app/'));
      } catch { /* Only the localhost debug endpoint is polled during cold extraction. */ }
      if (target) break; await delay(200);
    }
    if (!target) throw Error('Release application startup timed out: ' + tail);
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
    let sequence = 0; const pending = new Map(), errors = [];
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
      const item = pending.get(message.id); if (!item) return;
      pending.delete(message.id); clearTimeout(item.timer);
      if (message.error) item.reject(Error(message.error.message)); else item.resolve(message.result);
    });
    socket.addEventListener('close', () => { for (const p of pending.values()) { clearTimeout(p.timer); p.reject(Error('CDP closed')); } pending.clear(); });
    const command = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
      const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(Error('CDP timeout: ' + method)); }, 45000);
      pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
    const evaluate = async expression => {
      const reply = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (reply.exceptionDetails) throw Error(reply.exceptionDetails.exception?.description || reply.exceptionDetails.text);
      return reply.result.value;
    };
    const wait = async (expression, label) => {
      const began = Date.now();
      while (Date.now() - began < 45000) { if (await evaluate(expression)) return; await delay(100); }
      throw Error('Timeout ' + label + ': ' + await evaluate('document.querySelector("#main")?.innerText.slice(0,1800)'));
    };
    const click = selector => evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled||!e.getClientRects().length)throw Error('Missing visible enabled '+${JSON.stringify(selector)});e.click();return true})()`);
    await command('Runtime.enable');
    await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await wait('typeof AT!=="undefined" && !!document.querySelector("[data-nav-group=R]")', 'complete application UI');
    return { command, evaluate, click, wait, errors, startupMs: Date.now() - started,
      async close() {
        try { await evaluate('document.querySelector("[data-window-action=close]").click();true'); } catch { /* Target may close before response. */ }
        let timer;
        try { assert.equal(await Promise.race([exited, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('Release EXE did not close normally')), 15000); })]), 0); }
        finally { clearTimeout(timer); socket.close(); }
      },
      async abort() {
        try { await evaluate('atradeDesktop.windowAction("close")'); } catch {}
        await Promise.race([exited, delay(3000)]); socket?.close(); if (exitCode === undefined) child.kill();
      }
    };
  } catch (error) { socket?.close(); if (exitCode === undefined) child.kill(); throw error; }
}

async function main(opt) {
  assert.equal(process.platform, 'win32', 'The actual release smoke requires Windows');
  const infoPath = path.resolve(opt['build-info'] || path.join(ROOT, 'dist/latest-single-exe.json'));
  const info = read(infoPath), executable = path.resolve(opt.exe || info.file);
  assert.equal(info.schema, 'atrade-single-exe-build-v2'); assert.equal(sha(executable), info.sha256, 'Release executable pin');
  assert.equal(info.version, opt['expected-version'] || read(path.join(ROOT, 'package.json')).version, 'Do not accidentally test an older EXE');
  assert.match(info.cacheKey, /^[0-9A-Za-z._-]+$/);
  const evidence = path.resolve(opt.evidence || path.join(ROOT, 'artifacts/strategy-search/final-research-review-v1/current-scan-validation-0002'));
  const verificationPath = path.join(evidence, 'verification.json'), verification = read(verificationPath);
  assert.equal(verification.passed, true); assert.equal(verification.task.status, 'completed');
  const sourceDir = path.join(verification.workspace, 'experimental-scans', verification.task.id);
  const sourceTask = path.join(sourceDir, 'task.json'), sourceResult = path.join(sourceDir, 'result.json'), archivedResult = path.join(evidence, 'result.json');
  assert.deepEqual(read(sourceTask), verification.task);
  const saved = read(sourceResult), expectedSymbols = pairedSavedEvidence(read(archivedResult), saved);
  const base = path.resolve(opt['output-root'] || path.join(ROOT, 'artifacts/experimental-strategies-portable'));
  fs.mkdirSync(base, { recursive: true }); let attempt = 1;
  while (fs.existsSync(path.join(base, `attempt-${String(attempt).padStart(4, '0')}`))) attempt++;
  const directory = path.join(base, `attempt-${String(attempt).padStart(4, '0')}`); fs.mkdirSync(directory);
  const userData = path.join(directory, '独立数据'), workspace = path.join(userData, 'workspace');
  const cache = path.join(directory, '运行缓存'), temp = path.join(directory, '临时解包');
  const roots = protectedRoots(verification.workspace), protectedBefore = snapshot(roots);
  assert.ok(!roots.some(p => workspace.toLowerCase().startsWith(p.toLowerCase() + path.sep)), 'Isolated workspace must not nest inside a real workspace');
  fs.mkdirSync(temp, { recursive: true }); const target = path.join(workspace, 'experimental-scans', verification.task.id); fs.mkdirSync(target, { recursive: true });
  fs.copyFileSync(sourceTask, path.join(target, 'task.json'), fs.constants.COPYFILE_EXCL);
  fs.copyFileSync(sourceResult, path.join(target, 'result.json'), fs.constants.COPYFILE_EXCL);
  const env = { ...process.env, ATRADE_USER_DATA: userData, ATRADE_RUNTIME_CACHE: cache, TEMP: temp, TMP: temp,
    ATRADE_PYTHON: 'Z:\\missing-host-python.exe', PYTHONHOME: 'Z:\\missing-home', PYTHONPATH: 'Z:\\missing-site',
    PATH: path.join(process.env.SystemRoot, 'System32'), PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' };
  delete env.ELECTRON_RUN_AS_NODE;
  const receipt = { schema: 'experimental-portable-readonly-smoke-v1', passed: false, startedAt: new Date().toISOString(),
    executable: pin(executable), buildInfo: pin(infoPath), script: pin(__filename), version: info.version,
    inputPins: [verificationPath, sourceTask, sourceResult, archivedResult].map(pin), directory, userData, workspace,
    protectedBefore, protectedWorkspaces: roots, checks: [], images: [], newScans: null, newAccounts: null,
    scope: 'Actual packaged EXE and its embedded Python; copied verified saved scan only; no scan or historical replay commands' };
  const log = fs.createWriteStream(path.join(directory, 'run.log'), { flags: 'wx' }); let app;
  const screenshot = async (name, selector) => {
    if (selector) await app.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start'});window.scrollBy(0,-95)`);
    await app.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))'); await delay(200);
    const captured = await app.command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const file = path.join(directory, name); fs.writeFileSync(file, Buffer.from(captured.data, 'base64'), { flag: 'wx' });
    const bytes = fs.readFileSync(file); assert.equal(bytes.readUInt32BE(16), 1440); assert.equal(bytes.readUInt32BE(20), 1000);
    receipt.images.push({ ...pin(file), width: 1440, height: 1000 });
  };
  try {
    app = await launch(executable, directory, env, log); receipt.startupMs = app.startupMs;
    const bootstrap = await app.evaluate('atradeDesktop.bootstrap');
    assert.equal(bootstrap.app.packaged, true); assert.equal(path.resolve(bootstrap.storage.directory).toLowerCase(), workspace.toLowerCase());
    assert.equal(await app.evaluate('typeof require'), 'undefined'); assert.equal(await app.evaluate('typeof process'), 'undefined');
    const diagnostics = await app.evaluate('atradeDesktop.diagnostics()'); assert.equal(diagnostics.ok, true);
    for (const key of ['account', 'market', 'research']) assert.equal(diagnostics.data.services[key], true);
    receipt.checks.push('Real packaged EXE bootstraps the isolated workspace with all embedded services and no renderer Node access');
    const runtime = path.join(cache, info.cacheKey), appRoot = path.join(runtime, 'resources/app');
    const runtimeManifestPath = path.join(runtime, 'resources/cache-manifest.json');
    assert.equal(sha(runtimeManifestPath), info.runtimeManifestSha256); assert.equal(read(path.join(appRoot, 'package.json')).version, info.version);
    const runtimeInfo = read(path.join(runtime, 'build-info.json')), manifest = read(runtimeManifestPath);
    const required = ['docs/experimental-strategies-v1.json', 'service/experimental_strategies.py', 'service/experimental_strategy_service.py', 'service/experimental_scan.py', 'service/experimental_scan_service.py', 'preview/experimental-strategies.js', 'preview/experimental-strategies.css', 'preview/app.js', 'preview/index.html'];
    receipt.runtimePins = required.map(file => {
      const actual = path.join(appRoot, file), digest = sha(actual);
      assert.equal(digest, runtimeInfo.applicationFiles[file], 'Build application file pin: ' + file);
      assert.equal(digest, manifest.files.find(x => x.path === 'resources/app/' + file)?.sha256, 'Runtime manifest pin: ' + file);
      return pin(actual);
    });
    const python = path.join(runtime, 'resources/python/python.exe');
    assert.equal(sha(python), manifest.files.find(x => x.path === 'resources/python/python.exe')?.sha256);
    const probe = JSON.parse(await captureProcess(python, ['-B', '-c', 'import json,sys; from service.experimental_strategies import catalogue,MANIFEST; c=catalogue(); print(json.dumps({"executable":sys.executable,"manifest":str(MANIFEST),"strategies":[{"id":s["id"],"sources":len(s["sources"]),"available":sum(bool(x["available"]) for x in s["sources"])} for s in c["strategies"]]}))'], { cwd: directory, env }));
    assert.equal(path.resolve(probe.executable).toLowerCase(), python.toLowerCase());
    assert.equal(path.resolve(probe.manifest).toLowerCase(), path.join(appRoot, 'docs/experimental-strategies-v1.json').toLowerCase());
    assert.deepEqual(probe.strategies.map(x => x.sources), [2, 6, 6]); assert.deepEqual(probe.strategies.map(x => x.available), [0, 0, 0]);
    receipt.runtime = { directory: runtime, python: pin(python), manifest: pin(runtimeManifestPath), probe };
    receipt.checks.push('Actual extracted embedded Python and manifest match the SHA-256 release file inventory; host Python override is unusable');
    const { evaluate, click, wait } = app;
    if (!await evaluate('document.querySelector("[data-nav=R16]")?.getClientRects().length>0')) await click('[data-nav-group="R"]');
    await click('[data-nav="R16"]');
    await wait('AT.state.page==="R16" && document.querySelectorAll(".exp-candidate").length===15', 'saved current scan in R16');
    assert.equal(await evaluate('document.querySelector("#main h1").textContent'), '当前环境与选股');
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".exp-stock")].map(e=>e.dataset.stock)'), expectedSymbols);
    assert.equal(await evaluate('document.querySelectorAll("[data-exp-scan-strategy]").length'), 3);
    const detail = await evaluate(`atradeDesktop.business('research.experimental.scan.get',{id:${JSON.stringify(verification.task.id)}})`);
    assert.equal(detail.ok, true); assert.deepEqual(detail.data.result, saved); assert.equal(detail.data.freshness.available, true);
    assert.ok(await evaluate(`document.querySelector('.exp-scan-result header').innerText.includes(${JSON.stringify(saved.asOfDate)})`));
    assert.ok(await evaluate(`document.querySelector('.exp-scan-result header').innerText.includes(${JSON.stringify(detail.data.freshness.expectedAsOfDate)})`));
    if (detail.data.freshness.stale) assert.ok(await evaluate('document.querySelector(".exp-stale")?.innerText.includes("不是今日信号")'));
    assert.equal(await evaluate('document.querySelector("[data-exp-detail=historical]").open'), false);
    receipt.freshness = detail.data.freshness; receipt.checks.push('R16 shows the exact saved 0/0/15 candidates, actual cutoff and live-read freshness; historical replay is initially collapsed');
    await screenshot('01-packaged-current-environment.png', '.exp-scanner');
    await screenshot('02-packaged-saved-candidates.png', '[data-exp-scan-strategy="strongReclaimVolatilityTrail"]');
    await evaluate('document.querySelector(".exp-candidate details").open=true');
    const texts = await evaluate('[...document.querySelectorAll(".exp-candidate")].map(e=>e.innerText)');
    await evaluate('document.querySelector("[data-exp-detail=scan-history]").open=true'); await click('[data-exp-action="scan-refresh"]');
    await wait('!document.querySelector("[data-exp-action=scan-refresh]").disabled', 'read-only refresh');
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".exp-candidate")].map(e=>e.innerText)'), texts);
    const symbol = expectedSymbols[0]; await click('.exp-stock'); await wait('AT.state.page==="G01"', 'daily chart route');
    assert.equal(await evaluate('AT.state.stock'), symbol);
    receipt.stockNavigation = { symbol, page: 'G01', localDataConfigured: false, meaning: 'Stock daily page routing; isolated workspace deliberately has no TDX configuration, so this does not assert a newly loaded chart series' };
    if (!await evaluate('document.querySelector("[data-nav=R16]")?.getClientRects().length>0')) await click('[data-nav-group="R"]');
    await click('[data-nav="R16"]'); await wait('document.querySelectorAll(".exp-candidate").length===15', 'return to saved scan');
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".exp-stock")].map(e=>e.dataset.stock)'), expectedSymbols);
    receipt.checks.push('Read-only refresh and candidate full-symbol G01 navigation/return preserve the selected saved scan');
    await evaluate('document.querySelector("[data-exp-detail=historical]").open=true'); await wait('document.querySelectorAll("[data-exp-strategy]").length===3', 'packaged catalogue');
    const catalogue = await evaluate('atradeDesktop.business("research.experimental.catalogue",{})'); assert.equal(catalogue.ok, true);
    assert.deepEqual(catalogue.data.strategies.map(x => x.id), IDS);
    for (const [index, id] of IDS.entries()) {
      await click(`[data-exp-strategy="${id}"]`);
      assert.equal(await evaluate('document.querySelector("#exp-source").options.length'), [2, 6, 6][index]);
      await evaluate('document.querySelector("[data-exp-detail=strategy-research]").open=true');
      assert.equal(await evaluate('document.querySelectorAll(".exp-research tbody tr").length'), [2, 6, 6][index]);
      assert.equal(await evaluate('document.querySelector("[data-exp-action=create]").disabled'), true);
      assert.ok(await evaluate('[...document.querySelector("#exp-source").options].every(x=>x.textContent.includes("当前不可用"))'));
    }
    receipt.catalogue = catalogue.data.strategies.map(x => ({ id: x.id, sources: x.sources.length, researchRows: x.researchResults.length, availableSources: x.sources.filter(s => s.available).length }));
    receipt.checks.push('All 3 frozen strategy rules, 14 source definitions and 14 research rows remain visible; absent large historical caches disable replay clearly');
    await screenshot('03-packaged-strategy-rules-and-results.png', '.exp-strategy-detail');
    const scans = await evaluate('atradeDesktop.business("research.experimental.scan.list",{})'), replays = await evaluate('atradeDesktop.business("research.experimental.list",{})');
    assert.equal(scans.ok, true); assert.equal(replays.ok, true);
    assert.deepEqual(scans.data.tasks.map(x => x.id), [verification.task.id]); assert.equal(replays.data.tasks.length, 0);
    assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'), false); assert.deepEqual(app.errors, []);
    await app.close(); app = null;
    assert.deepEqual(taskInventory(path.join(workspace, 'experimental-scans')), [verification.task.id]);
    assert.deepEqual(taskInventory(path.join(workspace, 'experimental-strategies')), []);
    assert.equal(sha(path.join(target, 'task.json')), sha(sourceTask)); assert.equal(sha(path.join(target, 'result.json')), sha(sourceResult));
    receipt.protectedAfter = snapshot(roots); assert.deepEqual(receipt.protectedAfter, protectedBefore);
    for (const ref of receipt.inputPins) assert.equal(sha(ref.path), ref.sha256, 'Original scan evidence unchanged');
    receipt.newScans = 0; receipt.newAccounts = 0; receipt.checks.push('Normal EXE shutdown; zero new persisted scan/replay tasks; copied results and real/MSIX account preferences are unchanged');
    receipt.passed = true;
  } catch (error) {
    receipt.error = error.stack;
    if (app) { try { await screenshot('failure.png', null); } catch (captureError) { receipt.captureError = captureError.message; } await app.abort(); app = null; }
    receipt.protectedAfter = snapshot(roots);
  } finally {
    receipt.completedAt = new Date().toISOString();
    await new Promise(resolve => log.end(resolve));
    writeNew(path.join(directory, 'portable-experimental-verification.json'), receipt);
  }
  console.log(JSON.stringify({ passed: receipt.passed, directory, receipt: pin(path.join(directory, 'portable-experimental-verification.json')), images: receipt.images, error: receipt.error }, null, 2));
  if (!receipt.passed) process.exitCode = 1;
}
module.exports = { launch };
if (require.main === module) {
  const opt = options(process.argv.slice(2));
  if (opt.help) console.log('node scripts/smoke-experimental-portable.cjs [--exe FILE --build-info FILE] [--evidence DIR] [--output-root DIR] [--expected-version VERSION] | --self-test');
  else if (opt['self-test']) selfTest();
  else main(opt).catch(error => { console.error(error.stack); process.exitCode = 1; });
}
