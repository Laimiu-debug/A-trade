const assert=require('node:assert/strict');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

module.exports=async({evaluate,frame,wait})=>{
 await evaluate('AT.state.reduced=true;AT.navigate("R15");true');
 for(let i=0;i<300;i++){
  if(await evaluate('!!ResearchModule.taskCounts().loaded'))break;
  if(i===299)throw Error('Legacy task polling was not initialized');
  await delay(100);
 }
 await evaluate('AT.navigate("R01");true');
 await wait('!!document.querySelector("[data-research=strategy-selector] .ant-select")','strategy selector loaded');
 await evaluate(`(()=>{window.__strategyLabels=[];const el=document.querySelector('.demo-label');window.__strategyLabelObserver=new MutationObserver(()=>__strategyLabels.push(el.textContent));__strategyLabelObserver.observe(el,{childList:true,subtree:true,characterData:true});return true})()`);
 const choose=async(selector,name)=>{
  await frame.executeJavaScript(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}));return true})()`);
  await wait(`(()=>{const pop=[...document.querySelectorAll('.ant-select-dropdown')].find(el=>!el.classList.contains('ant-select-dropdown-hidden'));if(!pop)return false;const r=pop.getBoundingClientRect();return r.width>100&&r.x>=0&&r.right<=innerWidth+1&&r.y>=0&&r.y<innerHeight&&getComputedStyle(pop).opacity==='1'})()`,'strategy popup is on screen under reduced motion');
  await frame.executeJavaScript(`(()=>{const option=[...document.querySelectorAll('.ant-select-item-option')].find(el=>el.title.startsWith(${JSON.stringify(name)}));if(!option)throw Error('Strategy option missing');option.click();return true})()`);
  await wait(`document.querySelector(${JSON.stringify(selector)})?.innerText.includes(${JSON.stringify(name)})`,'selected '+name);
 };
 await choose('[data-research=strategy-selector] .ant-select','主力金叉');
 const labels=await frame.executeJavaScript(`[...document.querySelectorAll('[data-research=strategy-parameters] [data-research=fields] .ant-typography')].map(el=>el.innerText)`);
 assert.ok(labels.includes('最低信号评分'));
 assert.equal(labels.some(label=>/score min|event count|Require|Health|Event grade/.test(label)),false,'parameter names are translated');
 await delay(6200);
 const changes=await evaluate('__strategyLabelObserver.disconnect();__strategyLabels');
 assert.equal(changes.some(text=>text!=='本地策略研究'),false,'legacy polling cannot overwrite the active strategy shell');
 await evaluate('AT.navigate("R12");true');
 await wait('!!document.querySelector("[data-research=signal-config] .ant-select")','signal strategy selector loaded');
 await choose('[data-research=signal-config] .ant-select','主力紫转黄');
 await evaluate('AT.navigate("R01");true');
 await wait('!!document.querySelector("[data-research=strategy-selector] .ant-select")','return to parameter editor');
 console.log('PASS stable shell label, visible strategy dropdowns, actual selection changes and Chinese parameter labels');
};
