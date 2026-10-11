const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-account-smoke-'));
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

function run(phase) {
  return new Promise((resolve, reject) => {
    const targetDirectory = phase === 'optimize' ? path.join(directory, 'optimization-cases') : directory;
    const child = spawn(require('electron'), [path.join(root, 'tests/account-smoke-runner.cjs'), targetDirectory, phase], {
      env, stdio: 'inherit', windowsHide: true,
    });
    const timeout = setTimeout(() => { child.kill(); reject(new Error(`账户桌面验收 ${phase} 超时`)); }, 120000);
    child.on('error', error => { clearTimeout(timeout); reject(error); });
    child.on('exit', code => { clearTimeout(timeout); code === 0 ? resolve() : reject(new Error(`账户桌面验收 ${phase} 退出码 ${code}`)); });
  });
}

(async () => {
  await run('write');
  assert.ok(fs.statSync(path.join(directory, 'workspace/accounts.sqlite3')).size > 0, '真实 SQLite 文件已创建');
  await run('read');
  await run('optimize');
  console.log('PASS 账户实际界面：创建/切换隔离、手工买卖、OCR 待核对导入、24 项复盘、交易计划、资产快照、跨进程恢复和复盘草稿');
  console.log('截图：artifacts/account-overview.png / account-review.png / account-import.png');
})().catch(error => {
  console.error(error.stack);
  console.error(`验收数据保留在：${directory}`);
  process.exitCode = 1;
});
