const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-market-live-'));
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;

function run(phase) {
  return new Promise((resolve, reject) => {
    const child = spawn(require('electron'), [path.join(root, 'tests/market-live-smoke-runner.cjs'), directory, phase], {
      env, stdio: 'inherit', windowsHide: true,
    });
    const timer = setTimeout(() => {child.kill(); reject(new Error(`MCP 桌面验收 ${phase} 超时`));}, 120000);
    child.on('error', error => {clearTimeout(timer); reject(error);});
    child.on('exit', code => {clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`MCP 桌面验收 ${phase} 退出码 ${code}`));});
  });
}

(async () => {
  await run('write');
  assert.ok(fs.statSync(path.join(directory, 'workspace/market.sqlite3')).size > 0);
  const state = JSON.parse(fs.readFileSync(path.join(directory, 'workspace/workspace-state.json'), 'utf8')).state;
  assert.equal(state.page, 'G01');
  assert.equal(state.stock, 'sh600900');
  await run('read');
  console.log('PASS MCP 实际界面：配置、握手与工具、指数快照、板块成分、双击分时、分钟 K、失败缓存及跨进程恢复');
  console.log('仅 TQ 提供方使用本地测试夹具，Electron/IPC/Python/MCP 协议均为真实链路；外部网络已阻断。');
  console.log('截图：artifacts/market-live-settings.png / market-live-overview.png / market-live-minute-k.png');
})().catch(error => {
  console.error(error.stack);
  console.error(`验收数据保留在：${directory}`);
  process.exitCode = 1;
});
