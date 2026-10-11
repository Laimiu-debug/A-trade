/* Interface backups are separate from the account ledger and real watchlist. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.AT.workspaceBackup=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  'use strict';
  const FORMAT='a-trade.interface-state',SCHEMA_VERSION=1,MAX_FILE_BYTES=6*1024*1024;
  const fields=['version','goal','stages','startCapital','account','compact','reduced','largeText','sidebarCollapsed','concurrency','tasks','taskTrash','watch','notes','reports','customTrades','effects','milestoneEvents','businessReviewDrafts','fees','positionRules','feesNeedsReview','positionRulesNeedsReview','ruleA','ruleB','marketPreferences','analysisPresets'];
  const required=['version','goal','stages','startCapital','tasks','watch','notes','reports'];
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const text=value=>typeof value==='string';
  const fail=message=>{throw new Error(message);};
  function validateContent(state,settings){
    settings.validate(state);
    if(required.some(key=>!Object.hasOwn(state,key)))fail('界面备份字段不完整。');
    if(!Array.isArray(state.watch)||!Array.isArray(state.reports))fail('界面备份字段格式无效。');
    if(state.reports.some(item=>!object(item)||!text(item.name)||!text(item.date)||!text(item.stock)))fail('界面备份中的演示报告格式无效。');
    if(state.customTrades?.some(item=>!object(item)||!/^\d{6}$/.test(item.code)||!Number.isFinite(item.qty)||item.qty<=0||!Number.isFinite(item.price)||item.price<=0||!text(item.action)||!text(item.date)))fail('界面备份中的演示流水格式无效。');
    for(const key of ['feesNeedsReview','positionRulesNeedsReview'])if(Object.hasOwn(state,key)&&typeof state[key]!=='boolean')fail('规则迁移标记格式无效。');
    if(state.taskTrash?.some(entry=>!Number.isInteger(entry.index)||entry.index<0||!text(entry.deletedAt)||!Number.isFinite(Date.parse(entry.deletedAt))))fail('任务回收站记录格式无效。');
    if(state.effects!==undefined){
      if(!object(state.effects))fail('视觉效果格式无效。');
      const limits={blur:[0,32],opacity:[55,100],glowStrength:[0,100],density:[0,60],speed:[0,100],gradientStrength:[0,100],angle:[0,360]};
      for(const [key,value] of Object.entries(state.effects)){
        const valid=['enabled','glass','glow','particles','gradient'].includes(key)?typeof value==='boolean':limits[key]?Number.isFinite(value)&&value>=limits[key][0]&&value<=limits[key][1]:['colorA','colorB'].includes(key)&&text(value)&&/^#[0-9a-f]{6}$/i.test(value);
        if(!valid)fail('视觉效果参数无效。');
      }
    }
    if(state.milestoneEvents!==undefined&&(!object(state.milestoneEvents)||Object.values(state.milestoneEvents).some(events=>!Array.isArray(events)||events.some(item=>!object(item)||item.kind!=='event'||!text(item.date)||!text(item.title)||!text(item.note)))))fail('里程碑记录格式无效。');
    if(state.businessReviewDrafts!==undefined){
      if(!object(state.businessReviewDrafts))fail('复盘草稿格式无效。');
      for(const [key,draft] of Object.entries(state.businessReviewDrafts)){
        if(!key.includes('|')||!object(draft)||!['daily','weekly','monthly','trade','idea'].includes(draft.kind)||!text(draft.date)||!text(draft.title)||!object(draft.fields)||Object.values(draft.fields).some(value=>!text(value))||!object(draft.scores)||!Array.isArray(draft.relatedTradeIds)||draft.relatedTradeIds.some(value=>!text(value)))fail('复盘草稿内容不完整或格式无效。');
      }
    }
    return state;
  }
  function create(state,{settings}){
    const normalized=settings.normalizeRules(state);
    validateContent(normalized,settings);
    const snapshot=Object.fromEntries(fields.filter(key=>Object.hasOwn(normalized,key)).map(key=>[key,structuredClone(normalized[key])]));
    return {format:FORMAT,schemaVersion:SCHEMA_VERSION,exportedAt:new Date().toISOString(),state:snapshot};
  }
  function prepare(value,current,{settings,isDesktop=false}){
    if(!object(value))fail('请选择有效的界面状态备份。');
    let source=value;
    if(Object.hasOwn(value,'format')){
      if(value.format!==FORMAT||value.schemaVersion!==SCHEMA_VERSION||!object(value.state)||Object.keys(value).some(key=>!['format','schemaVersion','exportedAt','state'].includes(key)))fail('这不是受支持的界面状态备份，请选择对应的恢复入口。');
      source=value.state;
    }
    validateContent(source,settings);
    const normalized=settings.normalizeRules(source),candidate=structuredClone(current),legacy=!Object.hasOwn(value,'format');
    // Modern snapshots replace their full scope, including absent optional settings.
    // Older partial snapshots keep fields they never exported; unrelated modules stay intact.
    if(!legacy)for(const key of fields)delete candidate[key];
    for(const key of fields)if(Object.hasOwn(normalized,key))candidate[key]=structuredClone(normalized[key]);
    candidate.effects={...current.effects,...source.effects};
    if(!legacy){candidate.taskTrash??=[];candidate.milestoneEvents??={};}
    if(isDesktop)for(const task of candidate.tasks)if(['running','queued'].includes(task.status)){task.status='paused';task.step='演示已暂停 · 手动继续';}
    validateContent(candidate,settings);
    return {candidate,summary:{tasks:source.tasks.length,notes:Object.keys(source.notes).length,reviews:Object.keys(source.businessReviewDrafts||{}).length,customTrades:source.customTrades?.length||0,preferences:['compact','reduced','largeText','sidebarCollapsed'].filter(key=>Object.hasOwn(source,key)).length,legacy}};
  }
  return {FORMAT,SCHEMA_VERSION,MAX_FILE_BYTES,fields,create,prepare};
});
