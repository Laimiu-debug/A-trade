/* Research dates: preserve unfinished text, normalize only on commit. */
(() => {
  'use strict';
  const pad=value=>String(value).padStart(2,'0');
  function normalize(value) {
    const text=String(value??'').trim();
    const match=/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(text)||/^(\d{4})(\d{2})(\d{2})$/.exec(text);
    if(!match)return '';
    const year=Number(match[1]),month=Number(match[2]),day=Number(match[3]);
    const leap=year%4===0&&(year%100!==0||year%400===0);
    const days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
    if(year<1||month<1||month>12||day<1||day>days[month-1])return '';
    return `${match[1]}-${pad(month)}-${pad(day)}`;
  }
  function range(start,end,label='起止日期') {
    const startDate=normalize(start),endDate=normalize(end);
    if(!startDate||!endDate)throw new Error(`请填写有效的${label}，例如 2024-01-31；日期必须真实存在。`);
    if(startDate>endDate)throw new Error(`${label}：开始日期不能晚于结束日期。`);
    return {startDate,endDate};
  }
  function nextDay(value) {
    const date=normalize(value);if(!date)return '';
    const [year,month,day]=date.split('-').map(Number),time=new Date(0);
    time.setUTCFullYear(year,month-1,day+1);time.setUTCHours(0,0,0,0);
    return time.getUTCFullYear()>9999?'':time.toISOString().slice(0,10);
  }
  const E=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function field(label,value,{key,attrs='',min='',max=''}={}) {
    const id='research-date-'+String(key).replace(/[^a-zA-Z0-9_-]/g,'-');
    return `<div class="rs-field rd-field" data-date-control="${E(key)}"><label for="${E(id)}">${E(label)}</label><div class="rd-input-row"><input id="${E(id)}" type="text" ${attrs} value="${E(value)}" data-research-date data-date-label="${E(label)}" data-date-min="${E(min)}" data-date-max="${E(max)}" placeholder="YYYY-MM-DD" maxlength="10" autocomplete="off" spellcheck="false" aria-describedby="${E(id)}-help"><button type="button" class="rd-open" data-date-action="open" aria-label="${E(label)}：打开日历" aria-expanded="false" title="选择日期">▦</button></div><small id="${E(id)}-help" data-date-help>可输入 2024-01-31 或 20240131</small><div class="rd-calendar" data-date-calendar hidden></div></div>`;
  }
  const helpers={normalize,range,nextDay,field};
  if(typeof module==='object'&&module.exports)module.exports=helpers;
  if(typeof window==='undefined')return;
  window.ResearchDates=helpers;
  if(typeof document==='undefined')return;
  let openControl=null,view=null;
  const inputOf=control=>control?.querySelector('[data-research-date]');
  const emit=input=>{input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));};
  function check(input,required=false) {
    const text=input.value.trim(),date=normalize(text);
    let error='';
    if(text&&!date)error='日期无效，请填写真实日期，例如 2024-01-31。';
    else if(required&&!text)error='请填写'+input.dataset.dateLabel+'。';
    else if(date&&input.dataset.dateMin&&date<input.dataset.dateMin)error='日期须为 '+input.dataset.dateMin+' 或之后。';
    else if(date&&input.dataset.dateMax&&date>input.dataset.dateMax)error='日期须为 '+input.dataset.dateMax+' 或之前。';
    input.setCustomValidity(error);input.setAttribute('aria-invalid',String(!!error));
    const help=input.closest('[data-date-control]').querySelector('[data-date-help]');
    help.textContent=error||'可输入 2024-01-31 或 20240131';help.classList.toggle('rs-error',!!error);
    return !error;
  }
  function commit(input) {
    const date=normalize(input.value);
    if(date&&date!==input.value){input.value=date;emit(input);}
    check(input);
  }
  const idle=()=>document.dispatchEvent(new Event('research-date-idle'));
  function close() {
    if(openControl){openControl.querySelector('[data-date-calendar]').hidden=true;openControl.querySelector('[data-date-action="open"]').setAttribute('aria-expanded','false');}
    openControl=null;view=null;idle();
  }
  function position() {
    if(!openControl?.isConnected)return;
    const popup=openControl.querySelector('[data-date-calendar]'),rect=openControl.querySelector('.rd-input-row').getBoundingClientRect();
    popup.style.left=Math.max(12,Math.min(rect.left,window.innerWidth-popup.offsetWidth-12))+'px';
    popup.style.top=Math.max(12,rect.bottom+popup.offsetHeight+12>window.innerHeight?rect.top-popup.offsetHeight-6:rect.bottom+6)+'px';
  }
  function calendar() {
    if(!openControl?.isConnected){close();return;}
    const input=inputOf(openControl),[year,month]=view,selected=normalize(input.value);
    const first=new Date(0);first.setUTCFullYear(year,month-1,1);first.setUTCHours(0,0,0,0);
    const offset=(first.getUTCDay()+6)%7,days=Number(nextDay(`${String(year).padStart(4,'0')}-${pad(month)}-28`).slice(8))===29?29:28;
    const length=month===2?days:[4,6,9,11].includes(month)?30:31;
    const weeks=['一','二','三','四','五','六','日'].map(day=>`<span>${day}</span>`).join('');
    const cells=Array.from({length:offset},()=>'<span></span>').join('')+Array.from({length},(_,index)=>{
      const day=index+1,date=`${String(year).padStart(4,'0')}-${pad(month)}-${pad(day)}`;
      const disabled=(input.dataset.dateMin&&date<input.dataset.dateMin)||(input.dataset.dateMax&&date>input.dataset.dateMax);
      return `<button type="button" data-date-action="day" data-date-value="${date}" aria-label="${date}" aria-pressed="${date===selected}" ${disabled?'disabled':''}>${day}</button>`;
    }).join('');
    openControl.querySelector('[data-date-calendar]').innerHTML=`<div class="rd-month"><button type="button" data-date-action="previous" aria-label="上个月" ${year===1&&month===1?'disabled':''}>‹</button><label>年<input type="number" data-date-year value="${year}" min="1" max="9999" aria-label="日历年份"></label><label>月<select data-date-month aria-label="日历月份">${Array.from({length:12},(_,i)=>`<option value="${i+1}" ${month===i+1?'selected':''}>${i+1}</option>`).join('')}</select></label><button type="button" data-date-action="next" aria-label="下个月" ${year===9999&&month===12?'disabled':''}>›</button></div><div class="rd-week">${weeks}</div><div class="rd-days">${cells}</div><div class="rd-calendar-actions"><button type="button" data-date-action="today">今天</button><button type="button" data-date-action="clear">清空</button><button type="button" data-date-action="close">关闭</button></div>`;
    position();
  }
  function open(control) {
    if(openControl===control){close();return;}
    close();openControl=control;
    const input=inputOf(control),now=new Date(),date=normalize(input.value)||normalize(input.dataset.dateMin);
    view=date?date.split('-').slice(0,2).map(Number):[now.getFullYear(),now.getMonth()+1];
    control.querySelector('[data-date-calendar]').hidden=false;control.querySelector('[data-date-action="open"]').setAttribute('aria-expanded','true');calendar();
  }
  helpers.isEditing=root=>!!(root?.contains(document.activeElement)&&document.activeElement.closest?.('[data-date-control]')||openControl?.isConnected&&root?.contains(openControl));
  helpers.close=close;
  window.addEventListener('resize',position);
  document.addEventListener('scroll',position,true);
  document.addEventListener('input',event=>{const input=event.target;if(input.matches?.('[data-research-date]')){input.setCustomValidity('');input.removeAttribute('aria-invalid');}});
  document.addEventListener('change',event=>{
    const input=event.target;if(input.matches?.('[data-research-date]')){commit(input);return;}
    if(!openControl||!input.closest?.('[data-date-calendar]'))return;
    if(input.hasAttribute('data-date-year')){const year=Number(input.value);if(Number.isInteger(year)&&year>=1&&year<=9999)view[0]=year;}
    if(input.hasAttribute('data-date-month'))view[1]=Number(input.value);
    calendar();
  });
  document.addEventListener('focusout',event=>{
    if(event.target.matches?.('[data-research-date]'))commit(event.target);
    if(event.target.closest?.('[data-date-control]'))requestAnimationFrame(idle);
  });
  document.addEventListener('click',event=>{
    const target=event.target.closest?.('[data-date-action]');
    if(!target){if(openControl&&!openControl.contains(event.target))close();return;}
    if(target.disabled)return;event.preventDefault();
    const action=target.dataset.dateAction,control=target.closest('[data-date-control]'),input=inputOf(control);
    if(action==='open'){open(control);return;}
    if(action==='previous'||action==='next'){view[1]+=action==='next'?1:-1;if(view[1]===0){view=[view[0]-1,12];}if(view[1]===13){view=[view[0]+1,1];}calendar();return;}
    if(action==='day'||action==='today'||action==='clear'){
      const today=new Date();input.value=action==='clear'?'':action==='day'?target.dataset.dateValue:`${today.getFullYear()}-${pad(today.getMonth()+1)}-${pad(today.getDate())}`;
      emit(input);check(input);close();input.focus({preventScroll:true});return;
    }
    if(action==='close'){close();input.focus({preventScroll:true});}
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&openControl){event.preventDefault();const input=inputOf(openControl);close();input.focus({preventScroll:true});}});
  document.addEventListener('submit',event=>{
    const inputs=[...event.target.querySelectorAll('[data-research-date]')];
    for(const input of inputs){commit(input);if(!check(input,true)){event.preventDefault();event.stopImmediatePropagation();input.focus();return;}}
    if(openControl)close();
    if(inputs.includes(document.activeElement))document.activeElement.blur();
  },true);
})();
