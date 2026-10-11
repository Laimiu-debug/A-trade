const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-portfolio-smoke-'));
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
function run(phase){return new Promise((resolve,reject)=>{const child=spawn(require('electron'),[path.join(root,'tests/portfolio-smoke-runner.cjs'),directory,phase],{env,stdio:'inherit',windowsHide:true});const timer=setTimeout(()=>{child.kill();reject(new Error(`portfolio ${phase} timeout`));},180000);child.on('error',error=>{clearTimeout(timer);reject(error);});child.on('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error(`portfolio ${phase} exit ${code}`));});});}
(async()=>{await run('write');await run('read');console.log('PASS 隔离 Electron + IPC + Python + SQLite：136 股全池、配置提交、每日候选与持仓、风险退出、重开恢复及真实结果截图。夹具结果不代表投资有效性。');console.log('Screenshots: artifacts/portfolio-workbench.png, portfolio-result.png, portfolio-reopened.png');})().catch(error=>{console.error(error.stack);console.error(`Preserved fixture: ${directory}`);process.exitCode=1;});
