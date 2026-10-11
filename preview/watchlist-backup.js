/* Backup only the real observation pool; market caches and research jobs stay separate. */
(() => {
  if(!AT.isDesktop)return;
  const bridge=window.atradeDesktop,E=AT.escape,B=AT.button;
  let candidate=null,busy=false,readSerial=0;
  const pending=new Set(),$=selector=>document.querySelector(selector);
  const button=(label,action,primary=false)=>B(label,action==='export'?'export':'backup',`data-watchlist-backup="${action}"`,primary?'primary':'');
  function panel(){return `<section class="panel section-space"><div class="panel-body"><h2>真实跟踪池</h2><p class="field-hint section-space">备份 T09 跟踪池中的证券、备注、标签、观察状态与时间，包含已归档记录。此文件不是行情全库备份，不包含行情缓存、研究任务、账户账本或 AI 配置。</p>${button('导出跟踪池备份','export')}<label class="field section-space"><span>从跟踪池备份恢复</span><input type="file" data-watchlist-backup-file accept=".json,application/json"></label><p class="field-hint">选择文件后先校验并查看记录数量。确认后替换整个跟踪池，同时下载恢复前副本。</p><p class="business-error" data-watchlist-backup-error role="alert" hidden></p></div></section>`;}
  async function call(action,payload={}){
    if(typeof bridge.market!=='function')throw new Error('行情服务尚未启动，请重新打开桌面应用。');
    const work=Promise.resolve(bridge.market(`market.watchlist.backup.${action}`,payload)).then(result=>{if(!result?.ok)throw new Error(typeof result?.error==='string'?result.error:result?.error?.message||'跟踪池备份操作失败。');return result.data;});
    pending.add(work);try{return await work;}finally{pending.delete(work);}
  }
  function download(data,prefix='跟踪池备份'){
    const blob=new Blob([JSON.stringify(data)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=`A-trade-${prefix}-${new Date().toLocaleDateString('sv-SE')}.json`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
  }
  function error(error){const message=error?.message||'跟踪池备份操作失败。',target=$('[data-watchlist-backup-error]');if(target){target.textContent=message;target.hidden=false;}AT.toast?.(message);}
  function setBusy(value){busy=value;document.querySelectorAll('[data-watchlist-backup],[data-watchlist-backup-file]').forEach(el=>el.disabled=value);}
  async function read(file){
    const serial=++readSerial;candidate=null;
    if(file.size>20*1024*1024)throw new Error('跟踪池备份文件不能超过 20 MiB。');
    let backup;try{backup=JSON.parse(await file.text());}catch{throw new Error('无法读取跟踪池 JSON 备份。');}
    const summary=await call('preview',{backup});if(serial!==readSerial)return;
    candidate=backup;
    AT.openBusinessModal('恢复真实跟踪池',`<div class="report-text"><p>文件：${E(file.name)}</p><p>备份包含 <strong>${summary.total}</strong> 条记录，其中 ${summary.archived} 条已归档；将替换当前 <strong>${summary.currentTotal}</strong> 条跟踪记录。</p><p>备份时间：${E(new Date(summary.exportedAt).toLocaleString('zh-CN'))}</p><p>确认后重新校验整份文件，一次性替换跟踪池并下载恢复前副本。行情缓存、账户账本与研究任务保持原状。</p><p class="business-error" data-watchlist-backup-error role="alert" hidden></p></div>`,{className:'business-modal',footer:button('取消','cancel')+button('确认恢复跟踪池','restore',true)});
  }
  document.addEventListener('change',event=>{const input=event.target;if(!input.matches('[data-watchlist-backup-file]')||!input.files?.[0]||busy)return;const file=input.files[0];input.value='';setBusy(true);read(file).catch(error).finally(()=>setBusy(false));});
  document.addEventListener('click',event=>{
    const el=event.target.closest('[data-watchlist-backup]');if(!el)return;event.preventDefault();
    const action=el.dataset.watchlistBackup;if(busy)return;
    if(action==='cancel'){candidate=null;readSerial++;AT.closeBusinessModal();return;}
    setBusy(true);
    (async()=>{
      if(action==='export'){download(await call('export'));AT.toast('跟踪池备份已提交下载。');}
      if(action==='restore'&&candidate){
        const result=await call('restore',{backup:candidate});candidate=null;
        AT.analysisTools?.invalidateSource();AT.closeBusinessModal();AT.render();
        download(result.previousBackup,'跟踪池恢复前副本');AT.toast(`已恢复 ${result.total} 条跟踪记录，并下载恢复前副本。`);
      }
    })().catch(error).finally(()=>setBusy(false));
  });
  AT.watchlistBackup={panel,async flush(){const results=await Promise.allSettled([...pending]);return results.every(result=>result.status==='fulfilled');}};
})();
