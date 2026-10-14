/* Record the shipped source bytes and retain the original hash for each local fix. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../vendor/finaltrade'),file=path.join(root,'source-manifest.json');
const manifest=JSON.parse(fs.readFileSync(file,'utf8')),previous=new Map(manifest.files.map(entry=>[entry.path,entry]));
const paths=[];
function walk(relative){for(const entry of fs.readdirSync(path.join(root,relative),{withFileTypes:true})){
 if(['node_modules','__pycache__','.git'].includes(entry.name))continue;
 const item=relative+'/'+entry.name;if(entry.isDirectory())walk(item);else if(/\.(py|tsx?|css|json|md|html|svg|png|ico|ttf|woff2?)$/.test(entry.name))paths.push(item);
}}
for(const folder of ['backend/app','frontend/src','frontend/public'])walk(folder);
paths.push('frontend/index.html','frontend/package.json','frontend/package-lock.json');
manifest.files=paths.sort((a,b)=>a.localeCompare(b)).map(relative=>{
 const bytes=fs.readFileSync(path.join(root,relative)),sha256=crypto.createHash('sha256').update(bytes).digest('hex'),old=previous.get(relative);
 const entry={path:relative,sha256,bytes:bytes.length};
 if(!old||old.sha256!==sha256||'upstreamSha256' in old)entry.upstreamSha256=old&&('upstreamSha256' in old?old.upstreamSha256:old.sha256)||null;
 return entry;
});
manifest.upstreamSourceState=manifest.upstreamSourceState||manifest.sourceState;
manifest.sourceState='working-tree-with-atrade-fixes';
manifest.atradePatchRevision='strategy-safety-grid-workspace-v3';
fs.writeFileSync(file,JSON.stringify(manifest,null,2)+'\n');
console.log(`Recorded ${manifest.files.length} files; ${manifest.files.filter(entry=>'upstreamSha256' in entry).length} locally maintained files.`);
