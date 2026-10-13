const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {FinaltradeService}=require('../desktop/finaltrade-service.cjs');

test('reference proxy rejects foreign origins and destinations before starting Python',async()=>{
 const service=new FinaltradeService(fs.mkdtempSync(path.join(os.tmpdir(),'atrade-reference-proxy-')));
 for(const request of [new Request('atrade://reference/api/config',{headers:{Origin:'https://foreign.example'}}),new Request('https://foreign.example/api/config'),new Request('atrade://app/api/config')])assert.equal((await service.proxy(request)).status,403);
 assert.equal(service.child,null);service.close();await assert.rejects(service.start(),/已关闭/);
});

test('failed Python startup can be retried and close prevents later startup',async()=>{
 const prior=process.env.ATRADE_PYTHON;
 const service=new FinaltradeService(fs.mkdtempSync(path.join(os.tmpdir(),'atrade-reference-failure-')));
 try{
  process.env.ATRADE_PYTHON=path.join(service.directory,'missing-python.exe');
  await assert.rejects(service.start(),/无法启动|握手失败|已退出/);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(service.ready,null);
  await assert.rejects(service.start(),/无法启动|握手失败|已退出/);
 }finally{service.close();if(prior===undefined)delete process.env.ATRADE_PYTHON;else process.env.ATRADE_PYTHON=prior;}
 await assert.rejects(service.start(),/已关闭/);
});
