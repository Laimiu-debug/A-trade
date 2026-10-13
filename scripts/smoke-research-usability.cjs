const {spawn}=require('node:child_process'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-research-usability-')),env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.join(root,'tests/research-usability-smoke-runner.cjs'),directory],{env,stdio:'inherit',windowsHide:true});
const timer=setTimeout(()=>{child.kill();console.error('Research usability smoke timeout:',directory);process.exitCode=1;},300000);
child.on('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
child.on('exit',code=>{clearTimeout(timer);if(code!==0){console.error('Preserved fixture:',directory);process.exitCode=1;}else console.log('PASS 全池筛选、曲线日期交互、建加减清仓、实际仓位与现金、延期及跨日计划、期末未完成订单、路径去重和任务管理；合成数据仅用于功能验证。');});
