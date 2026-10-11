const {spawn}=require('node:child_process');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-research-smoke-'));
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
function run(phase){return new Promise((resolve,reject)=>{const child=spawn(require('electron'),[path.join(root,'tests/research-smoke-runner.cjs'),directory,phase],{env,stdio:'inherit',windowsHide:true});const timer=setTimeout(()=>{child.kill();reject(new Error(`research ${phase} timeout`));},180000);child.on('error',error=>{clearTimeout(timer);reject(error);});child.on('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error(`research ${phase} exit ${code}`));});});}
(async()=>{await run('write');await run('read');console.log('PASS 真实 Electron + IPC + Python + SQLite：策略配置、回测曲线、决策分页、平原训练/留出边界、任务暂停继续停止、重启恢复及研究报告。行情为隔离 .day 测试夹具，结果不代表实盘验证。');console.log('Screenshots: artifacts/research-workspace.png, research-result.png, research-plateau.png, research-tasks.png');})().catch(error=>{console.error(error.stack);console.error(`Preserved fixture: ${directory}`);process.exitCode=1;});
