/* Exercise the released EXE's private API with an old interval and a 120-bar chart. */
'use strict';
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { launch } = require('./smoke-experimental-portable.cjs');
const ROOT = path.resolve(__dirname, '..');
const sha = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const delay = ms => new Promise(r => setTimeout(r, ms));

function fixture(root, symbolCount = 3) {
  const directory = path.join(root, 'vipdoc/sh/lday'); fs.mkdirSync(directory, { recursive: true });
  const dates = [];
  for (let day = new Date('2023-01-02T00:00:00Z'); day <= new Date('2026-10-16T00:00:00Z'); day = new Date(+day + 86400000)) {
    if (day.getUTCDay() > 0 && day.getUTCDay() < 6) dates.push(day.toISOString().slice(0, 10));
  }
  for (let stock = 0; stock < symbolCount; stock++) {
    const bytes = Buffer.alloc(dates.length * 32);
    for (let i = 0; i < dates.length; i++) {
      const at = i * 32, price = 30 + stock + i * .01 + Math.sin(i / 7), volume = 12000000;
      bytes.writeUInt32LE(Number(dates[i].replaceAll('-', '')), at);
      for (const [offset, value] of [[4, price], [8, price + .5], [12, price - .5], [16, price]]) bytes.writeUInt32LE(Math.round(value * 100), at + offset);
      bytes.writeFloatLE(price * volume, at + 20); bytes.writeUInt32LE(volume, at + 24);
    }
    fs.writeFileSync(path.join(directory, `sh${600001 + stock}.day`), bytes, { flag: 'wx' });
  }
  return { source: root, symbols: symbolCount, oldestDate: dates[0], latestDate: dates.at(-1), barsPerSymbol: dates.length };
}

async function main() {
  const info = read(path.join(ROOT, 'dist/latest-single-exe.json'));
  assert.equal(info.version, read(path.join(ROOT, 'package.json')).version);
  assert.equal(sha(info.file), info.sha256);
  const base = path.join(ROOT, 'artifacts/backtest-history-exe'); fs.mkdirSync(base, { recursive: true });
  let n = 1; while (fs.existsSync(path.join(base, `attempt-${String(n).padStart(4, '0')}`))) n++;
  const directory = path.join(base, `attempt-${String(n).padStart(4, '0')}`); fs.mkdirSync(directory);
  const sample = fixture(path.join(directory, '历史行情样本'));
  const userData = path.join(directory, '独立数据'), cache = path.join(directory, '运行缓存'), temp = path.join(directory, '临时解包');
  fs.mkdirSync(temp);
  const env = { ...process.env, ATRADE_USER_DATA: userData, ATRADE_RUNTIME_CACHE: cache, TEMP: temp, TMP: temp,
    ATRADE_PYTHON: 'Z:\\missing-host-python.exe', PYTHONHOME: 'Z:\\missing-home', PYTHONPATH: 'Z:\\missing-site',
    PATH: path.join(process.env.SystemRoot, 'System32'), PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' };
  delete env.ELECTRON_RUN_AS_NODE;
  const receipt = { schema: 'backtest-history-packaged-api-smoke-v1', passed: false, version: info.version,
    executable: info.file, sha256: info.sha256, directory, userData, sample, startedAt: new Date().toISOString(), tests: [] };
  const log = fs.createWriteStream(path.join(directory, 'run.log'), { flags: 'wx' }); let app;
  try {
    app = await launch(info.file, directory, env, log);
    assert.equal(await app.evaluate('atradeDesktop.bootstrap.app.packaged'), true);
    assert.equal(path.resolve(await app.evaluate('atradeDesktop.bootstrap.storage.directory')), path.join(userData, 'workspace'));
    const call = async request => {
      const reply = await app.evaluate(`atradeDesktop.researchRequest(${JSON.stringify(request)})`);
      assert.equal(reply.ok, true, reply.error); return reply.data;
    };
    const configured = await app.evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(sample.source)}})`);
    assert.equal(configured.ok, true, configured.error);
    const config = await call({ path: '/api/config' }); assert.equal(config.candles_window_bars, 120);
    receipt.chartBars = config.candles_window_bars;
    const request = { mode: 'full_market', date_from: '2026-04-01', date_to: '2026-06-01', max_symbols: 120,
      strategy_id: 'ths_main_force_golden_cross_v1', pool_roll_mode: 'weekly', window_days: 60, enable_advanced_analysis: false };
    async function task(pathname, body) {
      const start = await call({ path: pathname, method: 'POST', body });
      assert.ok(start.task_id);
      const began = Date.now();
      while (Date.now() - began < 180000) {
        const result = await call({ path: pathname + '/' + start.task_id });
        if (result.status === 'failed' || result.status === 'cancelled') throw Error(result.error || result.status);
        if (result.status === 'succeeded') return result;
        await delay(400);
      }
      throw Error('Packaged historical task timed out');
    }
    for (const engine of ['legacy', 'matrix']) {
      const body = { ...request, execution_path_preference: engine,
        strategy_id: engine === 'matrix' ? 'wyckoff_trend_v1' : request.strategy_id };
      const report = await task('/api/backtest/tasks', body), result = report.result;
      assert.ok(result); assert.equal(result.execution_path, engine);
      assert.equal(result.effective_run_request.date_from, request.date_from);
      assert.equal(result.effective_run_request.date_to, request.date_to);
      assert.ok(result.equity_curve.length);
      assert.ok(result.equity_curve.every(p => p.date >= request.date_from && p.date <= request.date_to));
      receipt.tests.push({ engine, taskId: report.task_id, status: report.status, equityPoints: result.equity_curve.length,
        firstDate: result.equity_curve[0].date, lastDate: result.equity_curve.at(-1).date });
      fs.writeFileSync(path.join(directory, engine + '-task.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
    }
    const plateau = await task('/api/backtest/plateau/tasks', { base_payload: { ...request, execution_path_preference: 'legacy' },
      sampling_mode: 'grid', stop_loss_list: [.04, .05], take_profit_list: [.15], max_points: 2, sample_points: 2 });
    assert.equal(plateau.result.evaluated_combinations, 2);
    assert.ok(plateau.result.points.every(p => p.error == null));
    receipt.tests.push({ engine: 'plateau', status: plateau.status, evaluatedPoints: plateau.result.evaluated_combinations });
    fs.writeFileSync(path.join(directory, 'plateau-task.json'), JSON.stringify(plateau, null, 2), { flag: 'wx' });
    assert.equal((await call({ path: '/api/config' })).candles_window_bars, 120);
    receipt.passed = true;
    await app.close(); app = null;
  } catch (error) {
    receipt.error = error.stack;
    if (app) { await app.abort(); app = null; }
  } finally {
    await new Promise(r => log.end(r)); receipt.completedAt = new Date().toISOString();
    fs.writeFileSync(path.join(directory, 'verification.json'), JSON.stringify(receipt, null, 2), { flag: 'wx' });
  }
  console.log(JSON.stringify(receipt, null, 2));
  if (!receipt.passed) process.exitCode = 1;
}
module.exports = { fixture };
if (require.main === module) main().catch(error => { console.error(error.stack); process.exitCode = 1; });
