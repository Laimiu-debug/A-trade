const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { pythonCommand } = require('../desktop/business-service.cjs');
const root = path.resolve(__dirname, '..');
const target = path.join(root, '.venv');
const python = path.join(target, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
function run(command, args) {
  const result = spawnSync(command, args, {cwd:root,stdio:'inherit',windowsHide:true});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error('行情运行环境安装未完成。');
}
try {
  if (!fs.existsSync(python)) run(pythonCommand(), ['-m','venv',target]);
  run(python, ['-m','pip','install','-r',path.join(root,'requirements-market.txt')]);
  console.log('行情 MCP 运行环境已安装。重启 A-trade 后，在数据源设置中启用并测试连接。');
} catch (error) { console.error(error.message); process.exitCode=1; }
