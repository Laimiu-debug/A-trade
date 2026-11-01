/* Use electron-builder's pinned NSIS toolchain with a persistent portable cache. */
const fs=require('node:fs/promises'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {getMakeNsisPath}=require('app-builder-lib/out/toolsets/windows');
const {nsisEscapeString}=require('app-builder-lib/out/targets/nsis/nsisScriptGenerator');
const root=path.resolve(__dirname,'..');
const sha256=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
async function prepare(){await new Promise((resolve,reject)=>{const child=spawn(process.execPath,[path.join(__dirname,'build-portable.cjs')],{cwd:root,windowsHide:true,stdio:'inherit'});child.once('error',reject);child.once('exit',code=>code===0?resolve():reject(Error('Portable runtime preparation failed: '+code)))});return JSON.parse(await fs.readFile(path.join(root,'dist/latest-portable.json'),'utf8')).directory;}
async function main(){
 if(process.platform!=='win32'||process.arch!=='x64')throw Error('单文件便携包需要 Windows x64 构建环境。');
 const application=path.resolve(process.argv[2]||await prepare()),pkg=require('../package.json');
 const info=JSON.parse(await fs.readFile(path.join(application,'build-info.json'),'utf8'));
 if(info.version!==pkg.version||!info.bundledRuntime)throw Error('准备目录版本不匹配或缺少内置运行环境。');
 if(sha256(await fs.readFile(path.join(application,'A-trade.exe')))!==info.executableSha256)throw Error('Prepared executable checksum mismatch.');
 for(const [file,digest] of Object.entries(info.applicationFiles))if(sha256(await fs.readFile(path.join(application,'resources/app',file)))!==digest)throw Error('Application checksum mismatch: '+file);
 await fs.access(path.join(application,'resources/python/python.exe'));
 const stamp=new Date().toISOString().replace(/[-:]/g,'').replace(/\..+/,'').replace('T','-'),output=path.join(root,'dist',`single-exe-${pkg.version}-${stamp}`);
 await fs.mkdir(output);
 const executable=path.join(output,`A-trade-${pkg.version}-win32-x64.exe`);
 const runtimeManifestSha256=sha256(await fs.readFile(path.join(application,'resources/cache-manifest.json')));
 const cacheKey=pkg.version+'-'+runtimeManifestSha256.slice(0,20);
 const tool=await getMakeNsisPath();
 const defines={ATRADE_APPLICATION:application,ATRADE_OUTPUT:executable,ATRADE_ICON:path.join(root,'desktop/assets/app-icon.ico'),ATRADE_VERSION:pkg.version,ATRADE_CACHE_KEY:cacheKey};
 const args=['-WX','-V2','-INPUTCHARSET','UTF8',...Object.entries(defines).map(([key,value])=>`-D${key}=${nsisEscapeString(value)}`),path.join(__dirname,'cached-portable.nsi')];
 await new Promise((resolve,reject)=>{const child=spawn(tool.path,args,{cwd:root,env:{...process.env,...tool.env},windowsHide:true,stdio:'inherit'});child.once('error',reject);child.once('exit',code=>code===0?resolve():reject(Error('Portable launcher build failed: '+code)))});
 const bytes=await fs.readFile(executable),digest=sha256(bytes);
 const result={schema:'atrade-single-exe-build-v2',file:executable,version:pkg.version,bytes:bytes.length,sha256:digest,
  builtAt:new Date().toISOString(),sourceRevision:info.sourceRevision,sourceDirty:info.sourceDirty,preparedDirectory:application,
  extraction:'content-addressed shared cache with NTFS compression and inactive-version collection',cacheKey,runtimeManifestSha256,cacheLocation:'%LOCALAPPDATA%/A-trade/Runtime',dataLocation:info.dataLocation};
 await fs.writeFile(executable+'.sha256',digest+'  '+path.basename(executable)+'\n');
 await fs.writeFile(path.join(output,'build-info.json'),JSON.stringify(result,null,2)+'\n');
 await fs.writeFile(path.join(root,'dist/latest-single-exe.json'),JSON.stringify(result,null,2)+'\n');
 console.log('SINGLE_EXE='+executable);console.log('SHA256='+digest);
}
main().catch(error=>{console.error(error.stack);process.exitCode=1});
