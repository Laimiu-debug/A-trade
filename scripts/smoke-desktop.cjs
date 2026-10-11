const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const testDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-desktop-smoke-'));
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

function run(phase) {
  return new Promise((resolve, reject) => {
    const child = spawn(require('electron'), [
      path.join(root, 'tests/desktop-smoke-runner.cjs'), testDirectory, phase,
    ], { env, stdio: 'inherit', windowsHide: true });
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error(`桌面验收 ${phase} 超时`));
    }, 60000);
    child.on('error', error => { clearTimeout(timeout); reject(error); });
    child.on('exit', code => {
      clearTimeout(timeout);
      if (code === 0) resolve();
      else reject(new Error(`桌面验收 ${phase} 退出码 ${code}`));
    });
  });
}

(async () => {
  await run('write');
  const stateFile = path.join(testDirectory, 'workspace/workspace-state.json');
  let stored = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  assert.equal(stored.state.notes.A12, '桌面验收：输入后立即关闭，保留最后一个字。');
  await run('read');
  stored = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  assert.equal(stored.state.notes.A12, '桌面验收：输入后立即关闭，保留最后一个字。');
  console.log('PASS 桌面启动与隔离、窗口最小化/最大化/还原、自定义关闭按钮保存、重启恢复、保存失败重试、浏览器预览兼容');
  console.log('截图：artifacts/desktop-smoke.png');
})().catch(error => {
  console.error(error.message);
  console.error(`验收数据保留在：${testDirectory}`);
  process.exitCode = 1;
});
