const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-research-reading-'));
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require('electron'), [path.join(root, 'tests/research-reading-smoke-runner.cjs'), directory], {env, stdio:'inherit', windowsHide:true});
const timer = setTimeout(() => {child.kill(); console.error('Research reading smoke timeout:', directory); process.exitCode = 1;}, 180000);
child.on('error', error => {clearTimeout(timer); console.error(error); process.exitCode = 1;});
child.on('exit', code => {
  clearTimeout(timer);
  if (code !== 0) {console.error('Preserved isolated UI fixture:', directory); process.exitCode = 1;}
  else console.log('PASS 研究阅读：820/1080/1440px、大字号/紧凑表格、连续重绘与返回后的横向阅读位置；仅使用 UI fixtures，不验证回测收益。');
});
