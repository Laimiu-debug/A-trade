const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-stock-table-charts-'));
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require('electron'), [path.join(root, 'tests/stock-table-charts-smoke-runner.cjs'), directory], {
  env, stdio:'inherit', windowsHide:true,
});
const timer = setTimeout(() => {
  child.kill();
  console.error('Stock table charts smoke timeout:', directory);
  process.exitCode = 1;
}, 150000);
child.on('error', error => {clearTimeout(timer); console.error(error); process.exitCode = 1;});
child.on('exit', code => {
  clearTimeout(timer);
  if (code !== 0) {console.error('Preserved isolated fixture:', directory); process.exitCode = 1;}
  else console.log('PASS 股票表格真实日K、历史截止日期、无行情提示、懒加载及去重、键盘打开完整K线；合成数据仅用于功能验证。');
});
