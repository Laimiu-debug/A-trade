const {spawn}=require('node:child_process');
const path=require('node:path');
const env={...process.env}; delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.resolve('tests/emotion-grid-smoke.cjs')],{env,windowsHide:true,stdio:'inherit'});
const timer=setTimeout(()=>{child.kill();console.error('emotion/grid smoke timeout');process.exitCode=1},600000);
child.on('exit',code=>{clearTimeout(timer);process.exitCode=code??1});
