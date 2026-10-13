const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-stock-pool-workspace-'));
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require('electron'), [path.join(root, 'tests/stock-pool-workspace-smoke-runner.cjs'), directory], {env, stdio:'inherit', windowsHide:true});
const timer = setTimeout(() => {child.kill(); console.error('Stock pool workspace smoke timeout:', directory); process.exitCode = 1;}, 240000);
child.on('error', error => {clearTimeout(timer); console.error(error); process.exitCode = 1;});
child.on('exit', code => {
  clearTimeout(timer);
  if (code !== 0) {console.error('Preserved isolated fixture:', directory); process.exitCode = 1;}
  else console.log('PASS 独立股票池：全 A 股、万元换算、AND/OR、全池排名与搜索、均线持续性、量价风险和可选门槛、逐项依据、K 线和页面 AI；隔离合成数据仅用于功能验证。');
});
