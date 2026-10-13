/* Freeze the user-authorized reference source without importing its data. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),source=path.resolve(process.argv[2]||'G:/CODE/Final trade'),target=path.join(root,'vendor/finaltrade');
if(fs.existsSync(path.join(target,'source-manifest.json')))throw Error('A reference snapshot already exists; review before replacing it.');
const files=[];
function copy(directory,destination){
 for(const entry of fs.readdirSync(directory,{withFileTypes:true})){
  if(['__pycache__','node_modules','.git'].includes(entry.name)||entry.name.endsWith('.pyc'))continue;
  const from=path.join(directory,entry.name),to=path.join(destination,entry.name);
  if(entry.isDirectory()){copy(from,to);continue;}
  if(!/\.(py|tsx?|css|json|md|html|svg|png|ico|ttf|woff2?)$/.test(entry.name))continue;
  const bytes=fs.readFileSync(from);fs.mkdirSync(path.dirname(to),{recursive:true});fs.writeFileSync(to,bytes);
  files.push({path:path.relative(target,to).replaceAll('\\','/'),sha256:crypto.createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length});
 }
}
copy(path.join(source,'backend/app'),path.join(target,'backend/app'));
copy(path.join(source,'frontend/src'),path.join(target,'frontend/src'));
copy(path.join(source,'frontend/public'),path.join(target,'frontend/public'));
for(const name of ['index.html','package.json','package-lock.json']){
 const bytes=fs.readFileSync(path.join(source,'frontend',name)),to=path.join(target,'frontend',name);fs.writeFileSync(to,bytes);
 files.push({path:'frontend/'+name,sha256:crypto.createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length});
}
const manifest={project:'Laimiu-debug/final-trade',baseCommit:execFileSync('git',['rev-parse','HEAD'],{cwd:source,encoding:'utf8'}).trim(),capturedAt:new Date().toISOString(),sourceState:'working-tree',files:files.sort((a,b)=>a.path.localeCompare(b.path))};
fs.writeFileSync(path.join(target,'source-manifest.json'),JSON.stringify(manifest,null,2)+'\n');
fs.writeFileSync(path.join(target,'NOTICE.md'),'# Finaltrade reference source\n\nCopied from the user-provided Laimiu-debug/final-trade working tree with explicit authorization to reproduce its screener and strategies. Original source is retained unchanged; source-manifest.json records the exact bytes. No account files, credentials, databases, market caches or runtime data are included. Integration code lives outside this snapshot.\n');
console.log(JSON.stringify({files:files.length,bytes:files.reduce((n,row)=>n+row.bytes,0),baseCommit:manifest.baseCommit}));
