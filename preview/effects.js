// Decorative effects are independent of market colors and chart values.
(() => {
  const defaults = Object.freeze({
    enabled: true, glass: true, blur: 18, opacity: 78,
    glow: true, glowStrength: 42, particles: true, density: 24, speed: 35,
    gradient: true, gradientStrength: 48, angle: 125,
    colorA: '#4778f5', colorB: '#9d7aee'
  });
  const limits = {blur:[0,32],opacity:[55,100],glowStrength:[0,100],density:[0,60],speed:[0,100],gradientStrength:[0,100],angle:[0,360]};
  const palettes = {iris:['#4778f5','#9d7aee'],ocean:['#3479ee','#1ca9ad'],sunrise:['#8070db','#dd8b8e']};
  const presets = {
    clear: {...defaults},
    luminous: {...defaults,blur:24,opacity:65,glowStrength:70,density:42,speed:45,gradientStrength:75},
    focus: {...defaults,glass:false,glow:false,particles:false,gradient:false}
  };
  function normalize(value) {
    const source=value&&typeof value==='object'?value:{}, result={...defaults};
    for(const key of Object.keys(defaults)) {
      if(typeof defaults[key]==='boolean') result[key]=typeof source[key]==='boolean'?source[key]:defaults[key];
      else if(limits[key]&&Number.isFinite(source[key])) result[key]=Math.round(Math.max(limits[key][0],Math.min(limits[key][1],source[key])));
      else if(/^color[AB]$/.test(key)&&/^#[0-9a-f]{6}$/i.test(source[key]||'')) result[key]=source[key];
    }
    return result;
  }
  AT.state.effects=normalize(AT.state.effects);
  let layer, particleKey='', saveTimer;
  const motionQuery=window.matchMedia('(prefers-reduced-motion: reduce)');
  const rgb=hex=>[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(' ');
  const same=(a,b)=>Object.keys(defaults).every(k=>a[k]===b[k]);
  function particles(count) {
    return Array.from({length:count},(_,i)=>`<i class="fx-particle" style="--px:${(i*37.1+8)%100}%;--py:${(i*61.7+12)%100}%;--size:${i%5===0?5:2+i%3}px;--delay:${-(i*1.73)}s;--drift:${i%2?1:-1};--period:${13+i%7}s"></i>`).join('');
  }
  function ensureLayer() {
    if(layer)return;
    layer=document.createElement('div');layer.className='fx-atmosphere';layer.setAttribute('aria-hidden','true');
    layer.innerHTML='<div class="fx-color-wash"></div><div class="fx-halo fx-halo-a"></div><div class="fx-halo fx-halo-b"></div><div class="fx-particle-field"></div>';
    document.body.prepend(layer);
  }
  function syncControls() {
    const fx=AT.state.effects;
    document.querySelectorAll('[data-fx]').forEach(input=>{
      const key=input.dataset.fx;
      if(input.type==='checkbox')input.checked=fx[key];else input.value=fx[key];
      if(input.type==='range')input.style.setProperty('--range-fill',`${(fx[key]-Number(input.min))/(Number(input.max)-Number(input.min))*100}%`);
    });
    document.querySelectorAll('[data-fx-output]').forEach(el=>{
      const key=el.dataset.fxOutput;el.textContent=key==='speed'&&fx.speed===0?'静止':`${fx[key]}${el.dataset.unit||''}`;
    });
    document.querySelectorAll('[data-fx-controls]').forEach(el=>el.disabled=!fx.enabled||!fx[el.dataset.fxControls]);
    document.querySelectorAll('[data-fx-preset]').forEach(el=>el.setAttribute('aria-pressed',same(fx,presets[el.dataset.fxPreset])));
    document.querySelectorAll('[data-fx-palette]').forEach(el=>el.setAttribute('aria-pressed',palettes[el.dataset.fxPalette][0]===fx.colorA&&palettes[el.dataset.fxPalette][1]===fx.colorB));
    const status=document.querySelector('[data-fx-status]');
    if(status)status.textContent=!fx.enabled?'视觉效果已关闭':same(fx,presets.focus)?'专注阅读':same(fx,presets.clear)?'清透日常':same(fx,presets.luminous)?'流光氛围':'自定义效果';
    const motion=document.querySelector('[data-fx-motion-note]');
    if(motion)motion.textContent=AT.state.reduced||motionQuery.matches?'减少动态效果已生效：粒子与光效保持静态。':fx.speed===0?'粒子速度为 0：保留静态光点。':'动态效果在页面切到后台时暂停。';
  }
  function mount() {
    ensureLayer();const fx=AT.state.effects;
    const count=fx.enabled&&fx.particles?fx.density:0;
    if(particleKey!==String(count)) {layer.querySelector('.fx-particle-field').innerHTML=particles(count);particleKey=String(count);}
    document.querySelectorAll('.pass-panel,.fx-live-sample').forEach(host=>{
      let field=host.querySelector('.fx-local-particles');
      if(!field){field=document.createElement('div');field.className='fx-local-particles';field.setAttribute('aria-hidden','true');host.prepend(field);}
      const localCount=Math.ceil(count/2);
      if(field.dataset.count!==String(localCount)){field.innerHTML=particles(localCount);field.dataset.count=String(localCount);}
    });
    syncControls();
  }
  function apply() {
    const fx=AT.state.effects,root=document.documentElement,body=document.body;
    body.classList.toggle('fx-enabled',fx.enabled);
    for(const key of ['glass','glow','particles','gradient'])body.classList.toggle(`fx-${key}`,fx.enabled&&fx[key]);
    body.classList.toggle('fx-static',AT.state.reduced||motionQuery.matches);
    body.classList.toggle('fx-particles-still',fx.speed===0);
    body.classList.toggle('fx-inactive',document.hidden);
    const vars={
      '--fx-blur':fx.blur+'px','--fx-surface-alpha':fx.opacity/100,
      '--fx-glow':fx.glowStrength/100,'--fx-gradient':fx.gradientStrength/100,
      '--fx-angle':fx.angle+'deg','--fx-a':fx.colorA,'--fx-b':fx.colorB,
      '--fx-a-rgb':rgb(fx.colorA),'--fx-b-rgb':rgb(fx.colorB),
      '--fx-speed-factor':2.2-fx.speed*.018
    };
    for(const [key,value] of Object.entries(vars))root.style.setProperty(key,value);
    mount();
  }
  const toggle=(key,title,desc)=>`<label class="fx-effect-heading"><span><strong>${title}</strong><small>${desc}</small></span><input class="switch" type="checkbox" data-fx="${key}" aria-label="${title}" ${AT.state.effects[key]?'checked':''}></label>`;
  const range=(key,title,unit='%',hint='')=>`<label class="fx-range"><span>${title}<output data-fx-output="${key}" data-unit="${unit}">${AT.state.effects[key]}${unit}</output></span><input type="range" data-fx="${key}" min="${limits[key][0]}" max="${limits[key][1]}" step="1" value="${AT.state.effects[key]}" aria-label="${title}">${hint?`<small>${hint}</small>`:''}</label>`;
  // Use the shared heading so settings navigation stays above the content cards.
  AT.effectsSettings=()=>AT.head('显示与交互','给研究工作台一点光，调成适合自己的样子。',`<button class="btn" data-fx-reset>${AT.icon('refresh')}恢复视觉默认</button>`)+`
    <section class="panel fx-presets-panel"><div class="fx-presets-title"><div><h2>视觉氛围</h2><p>即时生效 · ${AT.isDesktop?'自动保存到本机':'自动保存到当前浏览器'}</p></div><label class="fx-master"><span>视觉效果总开关</span><input class="switch" type="checkbox" data-fx="enabled" aria-label="视觉效果总开关"></label></div>
    <div class="fx-presets">${[['clear','清透日常','柔和玻璃 · 淡淡流光'],['luminous','流光氛围','彩色光晕 · 漂浮微粒'],['focus','专注阅读','纯净工作面 · 关闭装饰']].map(([id,name,desc])=>`<button class="fx-preset fx-preset-${id}" data-fx-preset="${id}" aria-pressed="false"><span class="fx-preset-art" aria-hidden="true"><i></i><i></i><i></i></span><span><strong>${name}</strong><small>${desc}</small></span>${AT.icon('check')}</button>`).join('')}</div></section>
    <div class="fx-settings-grid"><div class="fx-controls-column">
      <section class="panel fx-control-card">${toggle('glass','磨砂玻璃','让卡片、导航和弹窗呈现通透质感')}<fieldset data-fx-controls="glass">${range('blur','模糊程度','px')}${range('opacity','玻璃不透明度','%','数值越高，卡片越实；最低 55% 以保留文字可读性。')}</fieldset></section>
      <section class="panel fx-control-card">${toggle('glow','柔和光效','背景光晕、边缘高光与里程碑 HUD 流光')}<fieldset data-fx-controls="glow">${range('glowStrength','光效强度')}</fieldset></section>
      <section class="panel fx-control-card">${toggle('particles','漂浮粒子','少量微光点缀背景与通行证')}<fieldset data-fx-controls="particles">${range('density','粒子数量','')}${range('speed','粒子速度','%','调至 0 保留静态光点；减少动态效果也会暂停运动。')}</fieldset></section>
      <section class="panel fx-control-card">${toggle('gradient','渐变颜色','背景、主按钮和通行证轨道共享配色')}<fieldset data-fx-controls="gradient"><div class="fx-palette-row">${[['iris','鸢尾蓝紫'],['ocean','海盐蓝青'],['sunrise','暮光粉紫']].map(([id,name])=>`<button type="button" class="fx-palette" data-fx-palette="${id}" aria-label="${name}" aria-pressed="false" style="--swatch-a:${palettes[id][0]};--swatch-b:${palettes[id][1]}"><i aria-hidden="true"></i>${name}</button>`).join('')}</div><div class="fx-colors"><label>起始颜色<input type="color" data-fx="colorA" value="${AT.state.effects.colorA}"></label><label>结束颜色<input type="color" data-fx="colorB" value="${AT.state.effects.colorB}"></label></div>${range('gradientStrength','渐变浓度')}${range('angle','渐变方向','°')}</fieldset></section>
      <section class="panel fx-reading"><div class="panel-head"><h2>阅读与动态</h2></div>${[['compact','紧凑表格','缩小表格行间距。'],['largeText','大号文字','放大按钮、列表和表格文字。'],['reduced','减少动态效果','暂停粒子与光效动画，保留静态质感。']].map(([id,title,desc])=>`<label class="setting-row"><div><h3>${title}</h3><p>${desc}</p></div><input class="switch right" type="checkbox" data-pref="${id}" aria-label="${title}" ${AT.state[id]?'checked':''}></label>`).join('')}</section>
    </div><aside class="fx-preview-column"><section class="panel fx-preview-panel"><div class="panel-head"><h2>实时预览</h2><span class="tag blue" data-fx-status>清透日常</span></div><div class="fx-preview-scene"><div class="fx-live-sample"><span class="fx-preview-eyebrow">A-TRADE · YOUR NEXT WAVE</span><div class="fx-sample-icon">${AT.icon('strategy')}</div><h3>让每一次研究<br>沉淀成下一步。</h3><p>材质、光线与色彩，即刻呈现。</p><div class="fx-sample-line"><i></i><b></b></div><button class="btn primary" data-nav="A02">回到总览看效果 ${AT.icon('chevronRight')}</button></div></div><div class="fx-preview-notes"><p>所有调整同步应用到整个预览界面。</p><p>配色作用于装饰层；行情涨跌色与图表含义保持固定。</p><p data-fx-motion-note></p></div></section></aside></div>`;
  function edit(event) {
    const input=event.target,key=input.dataset.fx;if(!key||!(key in defaults))return;
    AT.state.effects=normalize({...AT.state.effects,[key]:input.type==='checkbox'?input.checked:input.type==='range'?Number(input.value):input.value});
    apply();clearTimeout(saveTimer);
    if(event.type==='change')AT.persist();else saveTimer=setTimeout(()=>AT.persist(),150);
  }
  document.addEventListener('input',edit);document.addEventListener('change',edit);
  document.addEventListener('click',event=>{
    const el=event.target.closest('[data-fx-preset],[data-fx-palette],[data-fx-reset]');if(!el)return;
    if(el.hasAttribute('data-fx-reset'))AT.state.effects={...defaults};
    else if(el.dataset.fxPreset)AT.state.effects={...presets[el.dataset.fxPreset]};
    else {const [colorA,colorB]=palettes[el.dataset.fxPalette];Object.assign(AT.state.effects,{colorA,colorB});}
    apply();AT.persist();
  });
  document.addEventListener('visibilitychange',()=>document.body.classList.toggle('fx-inactive',document.hidden));
  motionQuery.addEventListener('change',apply);
  AT.effects={apply,mount,normalize,defaults};
})();
