const test=require('node:test');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
test('emotion cycle remains causal, validates dated membership and follows yesterday cohorts',()=>{
  execFileSync(require('../desktop/business-service.cjs').pythonCommand(),['-B','-m','unittest','tests.test_emotion_cycle'],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:'pipe',timeout:60000});
});
