const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-system-research-'));
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.join(root,'tests/system-research-smoke-runner.cjs'),directory],{env,stdio:'inherit',windowsHide:true});
const timer=setTimeout(()=>{child.kill();console.error('System research smoke timed out. Fixture:',directory);process.exitCode=1;},420000);
child.on('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
child.on('exit',code=>{clearTimeout(timer);if(code!==0){console.error('Preserved fixture:',directory);process.exitCode=1;}else console.log('PASS 隔离系统研究：实际创建比较、滚动验证、四层平原与轮动诊断任务；夹具收益不表示投资有效性。');});
