const {spawnSync}=require('node:child_process');
const path=require('node:path');
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const result=spawnSync(require('electron'),[path.join(__dirname,'../tests/market-news-smoke-runner.cjs')],{env,stdio:'inherit',windowsHide:true});
process.exitCode=result.status??1;
