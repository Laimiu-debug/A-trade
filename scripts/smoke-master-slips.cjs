const { spawn } = require('node:child_process');
const path = require('node:path');

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(require('electron'), [path.resolve(__dirname, '../tests/master-slips-smoke.cjs')], {
  env, stdio: 'inherit', windowsHide: true,
});
const timeout = setTimeout(() => {
  child.kill();
  console.error('大佬交割单桌面验收超时');
  process.exit(1);
}, 90000);
child.on('exit', code => {
  clearTimeout(timeout);
  process.exit(code ?? 1);
});
