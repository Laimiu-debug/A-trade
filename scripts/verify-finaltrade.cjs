const {spawn}=require('node:child_process');
const path=require('node:path');
const {pythonCommand}=require('../desktop/business-service.cjs');
const child=spawn(pythonCommand(),['-B',path.join(__dirname,'verify-finaltrade.py'),...process.argv.slice(2)],{windowsHide:true,stdio:'inherit',env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONDONTWRITEBYTECODE:'1'}});
child.once('error',error=>{console.error(error);process.exitCode=1;});
child.once('exit',code=>process.exitCode=code===0?0:1);
