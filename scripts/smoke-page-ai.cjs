const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-page-ai-'));
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require('electron'), [path.join(root, 'tests/page-ai-smoke-runner.cjs'), directory], {env, stdio:'inherit', windowsHide:true});
const timer = setTimeout(() => {child.kill(); console.error('Page AI smoke timeout:', directory); process.exitCode = 1;}, 180000);
child.on('error', error => {clearTimeout(timer); console.error(error); process.exitCode = 1;});
child.on('exit', code => {
  clearTimeout(timer);
  if (code !== 0) {console.error('Preserved isolated fixture:', directory); process.exitCode = 1;}
  else console.log('PASS 全局页面 AI：真实连板/趋势上下文、显式发送、追问与隔页、停止和失败重试、安全回答、键盘与会话内恢复；仅连接本机测试服务。');
});
