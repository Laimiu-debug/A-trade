const {spawn}=require('node:child_process'),path=require('node:path');
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(require('electron'),[path.join(__dirname,'../tests/finaltrade-desktop-smoke.cjs')],{env,stdio:'inherit',windowsHide:true});
child.once('exit',code=>process.exitCode=code);child.once('error',error=>{console.error(error);process.exitCode=1;});
