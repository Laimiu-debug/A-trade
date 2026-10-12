const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-research-refinements-'));
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.join(root,'tests/research-refinements-smoke-runner.cjs'),directory],{env,stdio:'inherit',windowsHide:true});
const timer=setTimeout(()=>{child.kill();console.error('Refinement smoke timeout:',directory);process.exitCode=1;},240000);
child.on('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
child.on('exit',code=>{clearTimeout(timer);if(code!==0){console.error('Preserved fixture:',directory);process.exitCode=1;}else console.log('PASS 历史 JSONL 原生导入、分红账本、连续资金滚动、样本外重复标记及完整报告保存。合成夹具不代表真实投资效果。');});
