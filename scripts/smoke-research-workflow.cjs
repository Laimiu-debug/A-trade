const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-research-workflow-'));
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
function run(phase){return new Promise((resolve,reject)=>{
 const child=spawn(require('electron'),[path.join(root,'tests/research-workflow-smoke-runner.cjs'),directory,phase],{env,stdio:'inherit',windowsHide:true});
 const timer=setTimeout(()=>{child.kill();reject(new Error(`Research workflow ${phase} timed out`));},300000);
 child.on('error',error=>{clearTimeout(timer);reject(error);});
 child.on('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error(`Research workflow ${phase} exit ${code}`));});
});}
(async()=>{await run('write');await run('read');console.log('PASS six-stage research workflow: isolated synthetic data, style funnels, single backtest, small parameter scan, explicit freeze, later validation, repeated interval and cross-process persistence.');console.log('Fixture:',directory);})().catch(error=>{console.error(error.stack);console.error('Preserved fixture:',directory);process.exitCode=1;});
