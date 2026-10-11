const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const runner = path.join(root, 'tests/settings-smoke-runner.cjs');
if (!fs.existsSync(runner)) throw new Error('Settings smoke runner is missing');
const testDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-settings-smoke-'));
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require('electron'), [runner, testDirectory], {env, stdio: 'inherit', windowsHide: true});
const timeout = setTimeout(() => {
  child.kill();
  console.error('设置页面验收超时');
  process.exitCode = 1;
}, 90000);
child.on('error', error => {clearTimeout(timeout); console.error(error.message); process.exitCode = 1;});
child.on('exit', code => {
  clearTimeout(timeout);
  if (code !== 0) {
    console.error(`设置验收数据保留在：${testDirectory}`);
    process.exitCode = 1;
  }
});
