const {test}=require('node:test');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
test('strategy registration, independent rhythm exits and strict plateau recommendations',()=>{
 execFileSync(require('../desktop/business-service.cjs').pythonCommand(),['-B','-m','unittest','tests.test_strategy_fixes'],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:'pipe',timeout:60000});
});
