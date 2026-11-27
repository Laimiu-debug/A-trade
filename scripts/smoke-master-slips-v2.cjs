const { spawn } = require('node:child_process');
const path = require('node:path');

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(require('electron'), [path.resolve(__dirname, '../tests/master-slips-v2-e2e.cjs')], {
  env, stdio: 'inherit', windowsHide: true,
});
const timeout = setTimeout(() => {
  child.kill();
  console.error('大佬交割单三大新产品桌面端E2E验收超时');
  process.exit(1);
}, 120000);
child.on('exit', code => {
  clearTimeout(timeout);
  process.exit(code ?? 1);
});
