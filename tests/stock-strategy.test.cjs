const {test}=require('node:test');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
test('unified stock evidence separates signals, completed fills and open positions',()=>{
 execFileSync(require('../desktop/business-service.cjs').pythonCommand(),['-B','-m','unittest','tests.test_stock_strategy'],{cwd:path.join(__dirname,'..'),windowsHide:true,stdio:'pipe',timeout:60000});
});
