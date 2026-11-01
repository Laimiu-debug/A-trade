/* Actual EXE: migrate an old draft, select step1, run the whole pool and opt in to a cap. */
'use strict';
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { launch } = require('./smoke-experimental-portable.cjs');
const { fixture } = require('./smoke-backtest-history.cjs');
const ROOT = path.resolve(__dirname, '..');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const sha = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function liquidity(source, count) {
  const directory = path.join(source, 'vipdoc/sh/lday');
  for (const name of fs.readdirSync(directory)) {
    const file = path.join(directory, name), bytes = fs.readFileSync(file);
    for (let at = 0; at < bytes.length; at += 32) {
      const close = bytes.readUInt32LE(at + 16);
      bytes.writeUInt32LE(Math.round(close * 1.02), at + 8);
      bytes.writeUInt32LE(Math.round(close * .98), at + 12);
    }
    fs.writeFileSync(file, bytes);
  }
  const fields = [['SC', 1], ['GPDM', 6], ['LTAG', 12]], header = 32 + fields.length * 32 + 1, size = 20;
  const bytes = Buffer.alloc(header + count * size + 1, 32);
  bytes.fill(0, 0, header); bytes[0] = 3; bytes.writeUInt32LE(count, 4);
  bytes.writeUInt16LE(header, 8); bytes.writeUInt16LE(size, 10);
  fields.forEach(([name, length], i) => { const at = 32 + i * 32; bytes.write(name, at, 'ascii'); bytes[at + 11] = name === 'LTAG' ? 78 : 67; bytes[at + 16] = length; });
  bytes[header - 1] = 13;
  for (let i = 0; i < count; i++) bytes.write(' ' + '1' + String(600001 + i) + '10000'.padStart(12), header + i * size, size, 'ascii');
  bytes[bytes.length - 1] = 26;
  const meta = path.join(source, 'T0002/hq_cache'); fs.mkdirSync(meta, { recursive: true });
  fs.writeFileSync(path.join(meta, 'base.dbf'), bytes, { flag: 'wx' });
  // Complete local names keep this offline fixture out of the live quote-name
  // fallback, which would otherwise spend time retrying hundreds of requests.
  const names = Buffer.alloc(50 + count * 360);
  for (let i = 0; i < count; i++) {
    const at = 50 + i * 360;
    names.write(String(600001 + i), at, 6, 'ascii');
    names.write(`PoolCase${String(i + 1).padStart(3, '0')}`, at + 31, 16, 'ascii');
  }
  fs.writeFileSync(path.join(meta, 'shs.tnf'), names, { flag: 'wx' });
}

function frameReader(app) {
  let targetId, sessionId;
  return async expression => {
    const targets = await app.command('Target.getTargets');
    const child = targets.targetInfos.find(x => x.type === 'iframe' && x.url.startsWith('atrade://reference/'));
    let reply;
    if (child) {
      if (child.targetId !== targetId) {
        const attached = await app.command('Target.attachToTarget', { targetId: child.targetId, flatten: true });
        targetId = child.targetId; sessionId = attached.sessionId;
      }
      reply = await app.command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, sessionId);
    } else {
      const tree = await app.command('Page.getFrameTree');
      const find = node => node.frame.url.startsWith('atrade://reference/') ? node.frame : (node.childFrames || []).map(find).find(Boolean);
      const frame = find(tree.frameTree); if (!frame) throw Error('Research frame not loaded');
      const world = await app.command('Page.createIsolatedWorld', { frameId: frame.id, worldName: 'full-pool-verification', grantUniveralAccess: true });
      reply = await app.command('Runtime.evaluate', { expression, contextId: world.executionContextId, returnByValue: true, awaitPromise: true });
    }
    if (reply.exceptionDetails) throw Error(reply.exceptionDetails.exception?.description || reply.exceptionDetails.text);
    return reply.result.value;
  };
}

async function main() {
  const info = read(path.join(ROOT, 'dist/latest-single-exe.json'));
  assert.equal(info.version, read(path.join(ROOT, 'package.json')).version); assert.equal(sha(info.file), info.sha256);
  const base = path.join(ROOT, 'artifacts/full-pool-exe'); fs.mkdirSync(base, { recursive: true });
  let n = 1; while (fs.existsSync(path.join(base, `attempt-${String(n).padStart(4, '0')}`))) n++;
  const directory = path.join(base, `attempt-${String(n).padStart(4, '0')}`); fs.mkdirSync(directory);
  const sample = fixture(path.join(directory, '全池行情样本'), 136); liquidity(sample.source, sample.symbols);
  const userData = path.join(directory, '独立数据'), cache = path.join(directory, '运行缓存'), temp = path.join(directory, '临时解包'); fs.mkdirSync(temp);
  const env = { ...process.env, ATRADE_USER_DATA: userData, ATRADE_RUNTIME_CACHE: cache, TEMP: temp, TMP: temp,
    ATRADE_PYTHON: 'Z:\\missing-host-python.exe', PYTHONHOME: 'Z:\\missing-home', PYTHONPATH: 'Z:\\missing-site',
    PATH: path.join(process.env.SystemRoot, 'System32'), PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' };
  delete env.ELECTRON_RUN_AS_NODE;
  const receipt = { schema: 'full-pool-packaged-ui-smoke-v1', passed: false, version: info.version, executable: info.file,
    sha256: info.sha256, directory, userData, sample, startedAt: new Date().toISOString(), checks: [], tasks: [] };
  const log = fs.createWriteStream(path.join(directory, 'run.log'), { flags: 'wx' }); let app;
  try {
    app = await launch(info.file, directory, env, log);
    assert.equal(await app.evaluate('atradeDesktop.bootstrap.app.packaged'), true);
    assert.equal(path.resolve(await app.evaluate('atradeDesktop.bootstrap.storage.directory')), path.join(userData, 'workspace'));
    const api = async request => {
      const reply = await app.evaluate(`atradeDesktop.researchRequest(${JSON.stringify(request)})`);
      assert.equal(reply.ok, true, reply.error); return reply.data;
    };
    const configured = await app.evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(sample.source)}})`);
    assert.equal(configured.ok, true, configured.error);
    const params = { markets: ['sh'], mode: 'strict', as_of_date: '2026-04-01', return_window_days: 40,
      top_n: 500, turnover_threshold: .01, amount_threshold: 5e7, amplitude_threshold: .01 };
    const start = await api({ path: '/api/screener/run', method: 'POST', body: params });
    const pool = await api({ path: '/api/screener/runs/' + start.run_id });
    assert.equal(pool.step_pools.step1.length, 136); receipt.selectedPoolMembers = pool.step_pools.step1.length;
    const strategy = frameReader(app);
    async function wait(expression, label, timeout = 60000) {
      const began = Date.now(); let last;
      while (Date.now() - began < timeout) {
        try { if (await strategy(expression)) return; } catch (error) { last = error.message; }
        await delay(200);
      }
      throw Error('Timeout: ' + label + (last ? ': ' + last : ''));
    }
    await app.evaluate('AT.navigate("R11");true');
    await wait('document.readyState === "complete"', 'research origin');
    const oldDraft = { mode: 'trend_pool', trend_step: 'step1', run_id: start.run_id,
      pool_roll_mode: 'weekly', date_from: '2026-04-01', date_to: '2026-04-03',
      strategy_id: 'score_only_rank_v1', window_days: 20, min_score: 0, min_event_count: 0,
      max_symbols: 120, enable_advanced_analysis: false, execution_path_preference: 'legacy' };
    await strategy(`localStorage.setItem('tdx-trend-backtest-form-v2',${JSON.stringify(JSON.stringify(oldDraft))});true`);
    await app.evaluate('AT.navigate("R03");true');
    await wait('document.querySelector("[data-testid=backtest-universe-label]")?.textContent === "所选股票池全部股票"', 'default full selected pool');
    assert.equal(await strategy('document.querySelector("[aria-label=限制回测股票数量]").getAttribute("aria-checked")'), 'false');
    assert.equal(await strategy('document.querySelector("[aria-label=回测股票数量上限]") === null'), true);
    await wait('JSON.parse(localStorage.getItem("tdx-trend-backtest-form-v2")).max_symbols === 0', 'old draft migration');
    const migrated = await strategy('JSON.parse(localStorage.getItem("tdx-trend-backtest-form-v2"))');
    assert.equal(migrated.date_from, oldDraft.date_from); assert.equal(migrated.date_to, oldDraft.date_to);
    assert.equal(migrated.run_id, start.run_id); assert.equal(migrated.trend_step, 'step1');
    assert.equal(migrated.stock_limit_enabled, false);
    receipt.checks.push('Old 120-stock draft becomes full pool while retaining dates, strategy, run and selected step1');
    async function screenshot(name) {
      await strategy('document.querySelector("[data-testid=backtest-universe-label]").scrollIntoView({block:"center"});true');
      await delay(250); const shot = await app.command('Page.captureScreenshot', { format: 'png' });
      const file = path.join(directory, name); fs.writeFileSync(file, Buffer.from(shot.data, 'base64'), { flag: 'wx' });
      receipt.screenshot = { path: file, sha256: sha(file) };
    }
    await screenshot('01-full-selected-pool.png');
    async function runFromUi(expectedCount, expectedLimit) {
      const prior = new Set((await api({ path: '/api/backtest/tasks' })).items.map(t => t.task_id));
      const button = '[...document.querySelectorAll("button")].find(e=>e.textContent.replace(/\\s/g,"")==="开始回测" && !e.disabled)';
      await wait(`!!(${button})`, 'enabled start button'); await strategy(`(${button}).click();true`);
      const began = Date.now(); let report;
      while (Date.now() - began < 240000) {
        const list = await api({ path: '/api/backtest/tasks' });
        const task = list.items.find(t => !prior.has(t.task_id));
        if (task) {
          report = await api({ path: '/api/backtest/tasks/' + task.task_id });
          if (report.status === 'failed') throw Error(report.error);
          if (report.status === 'succeeded') break;
        }
        await delay(500);
      }
      assert.equal(report?.status, 'succeeded', 'real UI-created backtest completes');
      const result = report.result;
      assert.equal(result.effective_run_request.mode, 'trend_pool');
      assert.equal(result.effective_run_request.trend_step, 'step1');
      assert.equal(result.effective_run_request.max_symbols, expectedLimit);
      const note = result.notes.find(text => text.includes('滚动股票池并集数量:'));
      assert.ok(note?.includes(`并集数量: ${expectedCount}，`), note);
      receipt.tasks.push({ id: report.task_id, status: report.status, selectedPool: 'step1',
        members: expectedCount, limit: expectedLimit, note, equityPoints: result.equity_curve.length });
      fs.writeFileSync(path.join(directory, `task-${expectedCount}.json`), JSON.stringify(report, null, 2), { flag: 'wx' });
    }
    await runFromUi(136, 0);
    await strategy('document.querySelector("[aria-label=限制回测股票数量]").click();true');
    await wait('document.querySelector("[aria-label=回测股票数量上限]")?.value === "120"', 'explicit count limit');
    await runFromUi(120, 120);
    await strategy('document.querySelector("[aria-label=限制回测股票数量]").click();true');
    await wait('JSON.parse(localStorage.getItem("tdx-trend-backtest-form-v2")).stock_limit_enabled === false', 'return to full pool');
    await app.evaluate('AT.navigate("R11");true'); await delay(250); await app.evaluate('AT.navigate("R03");true');
    await wait('document.querySelector("[data-testid=backtest-universe-label]")?.textContent === "所选股票池全部股票"', 'full-pool setting persists');
    receipt.checks.push('Actual UI backtests the entire 136-member step1 pool, opt-in limit selects 120, disabling it persists across page navigation');
    receipt.passed = true; await app.close(); app = null;
  } catch (error) {
    receipt.error = error.stack;
    if (app) { await app.abort(); app = null; }
  } finally {
    await new Promise(resolve => log.end(resolve)); receipt.completedAt = new Date().toISOString();
    fs.writeFileSync(path.join(directory, 'verification.json'), JSON.stringify(receipt, null, 2), { flag: 'wx' });
  }
  console.log(JSON.stringify(receipt, null, 2)); if (!receipt.passed) process.exitCode = 1;
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
