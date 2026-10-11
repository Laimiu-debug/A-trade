const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-market-smoke-'));
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

function run(phase) {
  return new Promise((resolve, reject) => {
    const child = spawn(require('electron'), [path.join(root, 'tests/market-smoke-runner.cjs'), directory, phase], {
      env, stdio: 'inherit', windowsHide: true,
    });
    const timeout = setTimeout(() => { child.kill(); reject(new Error(`行情桌面验收 ${phase} 超时`)); }, 120000);
    child.on('error', error => { clearTimeout(timeout); reject(error); });
    child.on('exit', code => { clearTimeout(timeout); code === 0 ? resolve() : reject(new Error(`行情桌面验收 ${phase} 退出码 ${code}`)); });
  });
}

(async () => {
  await run('write');
  assert.ok(fs.statSync(path.join(directory, 'workspace/market.sqlite3')).size > 0);
  const state = JSON.parse(fs.readFileSync(path.join(directory, 'workspace/workspace-state.json'), 'utf8')).state;
  assert.equal(state.stock, 'sz000001', '市场及前导零写入状态文件');
  assert.equal(state.page, 'G01');
  await run('read');
  console.log('PASS 行情实际界面：空配置、目录选择/取消/校验、真实日线、同码跨市场、历史修订、坏文件、离线缓存及跨进程恢复');
  console.log('截图：artifacts/market-settings.png / market-list.png / market-daily.png / market-offline.png');
})().catch(error => {
  console.error(error.stack);
  console.error(`验收数据保留在：${directory}`);
  process.exitCode = 1;
});
