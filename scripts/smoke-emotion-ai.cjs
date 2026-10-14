const {spawn}=require('node:child_process'),path=require('node:path');
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.resolve('tests/emotion-ai-smoke.cjs')],{env,windowsHide:true,stdio:'inherit'});
const timer=setTimeout(()=>{child.kill();console.error('Emotion AI smoke timeout');process.exitCode=1},120000);
child.on('exit',code=>{clearTimeout(timer);process.exitCode=code??1});
child.on('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1});
