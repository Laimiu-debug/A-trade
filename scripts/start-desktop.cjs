const { spawn } = require('node:child_process');
const path = require('node:path');

// Codex and other Electron hosts may export this; the child must run as an app.
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require('electron'), [path.resolve(__dirname, '..'), ...process.argv.slice(2)], {
  env,
  stdio: 'inherit',
  // This child is the interactive app; do not inherit a hidden startup window.
  windowsHide: false,
});
child.on('error', error => {
  console.error(`无法启动 A-trade：${error.message}`);
  process.exitCode = 1;
});
child.on('exit', code => { process.exitCode = code ?? 1; });
