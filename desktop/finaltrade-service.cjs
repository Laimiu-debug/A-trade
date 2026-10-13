const {spawn}=require('node:child_process');
const {randomBytes}=require('node:crypto');
const path=require('node:path');
const {pythonCommand}=require('./business-service.cjs');

class FinaltradeService {
 constructor(directory){this.directory=directory;this.token=randomBytes(32).toString('hex');this.child=null;this.ready=null;this.url=null;this.closed=false;}
 start(){
  if(this.closed)return Promise.reject(Error('策略服务已关闭。'));
  if(this.ready)return this.ready;
  this.ready=new Promise((resolve,reject)=>{
   const child=this.child=spawn(pythonCommand(),['-B','-u',path.join(__dirname,'../service/finaltrade_runtime.py'),this.directory],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'}});
   let buffer='';
   const fail=message=>{clearTimeout(timer);if(this.child===child){this.child=null;this.url=null;this.ready=null;}if(child.exitCode===null)child.kill();reject(Error(message));};
   const timer=setTimeout(()=>fail('策略服务启动超时，请检查运行环境。'),45000);
   child.stdout.setEncoding('utf8');child.stdout.on('data',chunk=>{buffer+=chunk;for(;;){const index=buffer.indexOf('\n');if(index<0)break;const line=buffer.slice(0,index);buffer=buffer.slice(index+1);try{const data=JSON.parse(line);if(this.child===child&&!this.closed&&data.ready&&Number.isInteger(data.port)&&data.port>0&&data.port<=65535){clearTimeout(timer);this.url='http://127.0.0.1:'+data.port;resolve({ready:true});}}catch{}}});
   child.stderr.on('data',()=>{});
   child.once('error',()=>fail('策略服务无法启动。'));
   child.once('exit',()=>fail('策略服务已退出，请重新打开。'));
   child.stdin.on('error',()=>fail('策略服务握手失败，请重新打开。'));
   child.stdin.end(this.token+'\n');
  });const ready=this.ready;ready.catch(()=>{if(this.ready===ready)this.ready=null;});return ready;
 }
 async proxy(request){
  const input=new URL(request.url),headers={'X-Atrade-Reference-Token':this.token};
  const origin=request.headers.get('origin');
  if(input.protocol!=='atrade:'||input.host!=='reference'||input.username||input.password||(origin&&origin!=='atrade://reference')||!['GET','HEAD','POST','PATCH','PUT','DELETE','OPTIONS'].includes(request.method))return new Response('',{status:403});
  const unavailable=()=>new Response(JSON.stringify({code:'STRATEGY_SERVICE_UNAVAILABLE',message:'策略服务已结束，请重新打开桌面应用。'}),{status:503,headers:{'Content-Type':'application/json'}});
  if(this.closed)return unavailable();
  await this.start();
  const contentType=request.headers.get('content-type');if(contentType)headers['Content-Type']=contentType;
  const options={method:request.method,headers};
  if(!['GET','HEAD'].includes(request.method))options.body=Buffer.from(await request.arrayBuffer());
  try{return await fetch(this.url+input.pathname+input.search,{...options,redirect:'manual'});}catch{return unavailable();}
 }
 async request(input){
  if(!input||typeof input!=='object'||Array.isArray(input)||typeof input.path!=='string'||!/^\/api\/(?:strategies|screener|signals|backtest|config|atrade)(?:[/?]|$)/.test(input.path)||input.path.includes('..')||input.path.includes('\\')||input.path.length>4096)throw Error('策略接口请求无效。');
  const method=input.method||'GET';if(!['GET','POST','PATCH','PUT','DELETE'].includes(method))throw Error('策略接口操作无效。');
  const body=input.body===undefined?undefined:JSON.stringify(input.body);if(body&&Buffer.byteLength(body)>32*1024*1024)throw Error('策略请求过大。');
  const response=await this.proxy(new Request('atrade://reference'+input.path,{method,headers:{Origin:'atrade://reference','Content-Type':'application/json'},body}));
  const data=await response.json();return response.ok?{ok:true,data}:{ok:false,error:data.message||'策略请求未完成。',code:data.code};
 }
 close(){this.closed=true;if(this.child&&this.child.exitCode===null)this.child.kill();}
}
module.exports={FinaltradeService};
