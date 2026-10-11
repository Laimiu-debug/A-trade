const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { pythonCommand } = require('../desktop/business-service.cjs');
const result = spawnSync(pythonCommand(), ['-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'], {
  cwd: path.resolve(__dirname, '..'), stdio: 'inherit', windowsHide: true,
  env: { ...process.env, PYTHONUTF8: '1' },
});
if (result.error) console.error('无法启动 Python 测试，请安装 Python 3.11 或以上版本。');
process.exitCode = result.status ?? 1;
