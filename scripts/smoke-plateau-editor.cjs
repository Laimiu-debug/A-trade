const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-plateau-editor-'));
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.join(root,'tests/plateau-editor-smoke-runner.cjs'),directory],{env,stdio:'inherit',windowsHide:true});
const timer=setTimeout(()=>{child.kill();console.error('Plateau editor smoke timed out. Fixture:',directory);process.exitCode=1;},420000);
child.on('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
child.on('exit',code=>{clearTimeout(timer);if(code!==0){console.error('Preserved fixture:',directory);process.exitCode=1;}else console.log('PASS 独立收益平原：草稿隔离、候选取值与固定条件、行内错误、16 组实际任务及认证边界；合成数据仅用于功能验证。');});
