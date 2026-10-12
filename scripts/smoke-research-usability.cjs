const {spawn}=require('node:child_process'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..'),directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-research-usability-')),env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.join(root,'tests/research-usability-smoke-runner.cjs'),directory],{env,stdio:'inherit',windowsHide:true});
const timer=setTimeout(()=>{child.kill();console.error('Research usability smoke timeout:',directory);process.exitCode=1;},240000);
child.on('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
child.on('exit',code=>{clearTimeout(timer);if(code!==0){console.error('Preserved fixture:',directory);process.exitCode=1;}else console.log('PASS 全池筛选与参数、名称及日线跳转、清晰操作、任务查询/改名/复制/删除；合成数据仅用于功能验证。');});
