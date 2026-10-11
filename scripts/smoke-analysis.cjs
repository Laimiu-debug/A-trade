const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-analysis-'));
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
function run(phase){return new Promise((resolve,reject)=>{const child=spawn(require('electron'),[path.join(root,'tests/analysis-smoke-runner.cjs'),directory,phase],{env,stdio:'inherit',windowsHide:true});const timer=setTimeout(()=>{child.kill();reject(new Error(`分析工具 ${phase} 验收超时`));},120000);child.on('error',error=>{clearTimeout(timer);reject(error);});child.on('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error(`分析工具 ${phase} 退出码 ${code}`));});});}
(async()=>{await run('write');assert.ok(fs.statSync(path.join(directory,'workspace/market.sqlite3')).size>0);await run('read');console.log('PASS 分析工具：9页真实IPC、研究筛选、TTM计算、缺失市值、同行与历史PE、显式采用参考、三态排序、板块/龙头成交额同图与独立净流入、资金历史缩放拖动与真实天数、跟踪池编辑删除与重启、事件日K定位。');console.log('本地通达信和外部HTTP响应为隔离测试夹具；Electron、IPC、Python计算、SQLite持久化均使用实际实现，公共网络已阻断。');console.log('截图：artifacts/analysis-industry-pe.png / analysis-funds-history.png / analysis-watchlist.png');})().catch(error=>{console.error(error.stack);console.error(`验收数据保留在：${directory}`);process.exitCode=1;});
