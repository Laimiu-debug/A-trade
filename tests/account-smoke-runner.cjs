const { app, protocol } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { AIService } = require('../desktop/ai-service.cjs');
let configSaveDelay = 0;
const dispatchAI = AIService.prototype.dispatch;
AIService.prototype.dispatch = async function(method,payload) {
  if(method==='config.save'&&payload?.target==='text'&&configSaveDelay)await new Promise(resolve=>setTimeout(resolve,configSaveDelay));
  return dispatchAI.call(this,method,payload);
};

const [directory, phase] = process.argv.slice(2);
if (!directory || !['write', 'read', 'optimize'].includes(phase)) throw new Error('Missing account smoke arguments');
const root = path.resolve(__dirname, '..');
const dataDirectory = path.join(directory, 'workspace');
app.setPath('userData', path.join(directory, 'electron-profile'));
protocol.registerSchemesAsPrivileged([{ scheme: 'atrade', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
let win;
const mockServers=[];
const connectionCounts={text:0,vision:0};
let recognitionCount = 0;
let reviewRequestCount = 0;
const mockErrors = [];
const rendererErrors = [];
const evaluate = code => win.webContents.executeJavaScript(code);
const state = () => evaluate('structuredClone(AT.businessUI.workspace)');
const click = selector => evaluate(`(() => { const scope=document.querySelector('.overlay')||document.querySelector('#workspace-root:not([hidden])')||document; const el=[...scope.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length)||[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length); if(!el)throw new Error('Missing visible button: '+${JSON.stringify(selector)}); if(el.disabled)throw new Error('Disabled button: '+${JSON.stringify(selector)}); el.click(); })()`);
const button = action => click(`[data-business="${action}"]`);
const wait = async (expression, label, timeout = 12000) => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  const errors = await evaluate(`[...document.querySelectorAll('[data-business-error],[data-import-error]')].filter(el=>!el.hidden).map(el=>el.textContent).join('; ')`);
  throw new Error(`${label} 超时${errors ? ': ' + errors : ''}`);
};
const fill = fields => evaluate(`(() => { const f=document.querySelector('#business-form'); if(!f)throw new Error('Missing form'); for(const [key,value] of Object.entries(${JSON.stringify(fields)})){const el=f.elements.namedItem(key); if(!el)throw new Error('Missing field: '+key); el.value=String(value);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));} })()`);
async function save(expression, label) {
  await button('save-form');
  await wait(`!document.querySelector('#business-form') && (${expression})`, label);
}
async function navigate(page) {
  const selector = `[data-nav="${page}"]`;
  await click(selector);
  await wait(`AT.state.page === ${JSON.stringify(page)}`, `打开 ${page}`);
}
async function screenshot(name) {
  await fs.mkdir(path.join(root, 'artifacts'), { recursive: true });
  await evaluate(`document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getTiming().iterations))try{a.finish()}catch{}})`);
  // A hidden window's first capture wakes its compositor and can contain the
  // previous painted frame. Warm it once, then capture the updated frame.
  await win.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
  await new Promise(resolve => setTimeout(resolve, 180));
  const capture = await win.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
  await fs.writeFile(path.join(root, 'artifacts', name), capture.toPNG());
}
async function uploadText(selector, fileName, text, type = 'application/json') {
  await evaluate(`(() => { const target=document.querySelector(${JSON.stringify(selector)}); if(!target)throw new Error('Missing file input'); const transfer=new DataTransfer(); transfer.items.add(new File([${JSON.stringify(text)}],${JSON.stringify(fileName)},{type:${JSON.stringify(type)}}));target.files=transfer.files;target.dispatchEvent(new Event('change',{bubbles:true})); })()`);
}
async function uploadImage() {
  // Deterministic valid 1x1 PNG fixture, used only against the in-process mock provider.
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1EAAAAASUVORK5CYII=';
  await evaluate(`(() => {const bytes=Uint8Array.from(atob(${JSON.stringify(png)}),c=>c.charCodeAt(0));const transfer=new DataTransfer();transfer.items.add(new File([bytes],'测试成交.png',{type:'image/png'}));const target=document.querySelector('[data-business-images]');target.files=transfer.files;target.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await wait('document.querySelector(".business-image-strip img") && !document.querySelector("[data-business=import-recognize]").disabled', '选择实际截图文件');
}
async function configureMock() {
  const urls={};
  for(const target of ['text','vision']) {
    const server=http.createServer((request,response)=>{
      let raw='';
      request.on('data',chunk=>{raw+=chunk});
      request.on('end',()=>{
        try {
          assert.equal(request.url,'/v1/chat/completions');
          assert.equal(request.headers.authorization,`Bearer account-smoke-${target}-key`);
          const body=JSON.parse(raw);
          assert.equal(body.model,`mock-${target}`);
          assert.equal(body.stream,false);
          assert.equal(body.store,false);
          let result;
          if(body.messages.length===1) {
            connectionCounts[target]++;
            result='OK';
          } else if(target==='vision') {
            recognitionCount++;
            assert.ok(Array.isArray(body.messages[1].content));
            assert.ok(body.messages[1].content.some(part=>part.type==='image_url'&&part.image_url.url.startsWith('data:image/png;base64,')));
            result={rows:[{date:null,time:'10:15:00',type:'buy',code:'600000',name:recognitionCount===1?'浦发银行':'浦发银行（再次识别）',quantity:100,price:11.5,commission:null,stampTax:null,transferFee:null,otherFees:null,externalId:'',warnings:['截图缺少完整日期和费用，请人工核对。']}],warnings:[]};
          } else {
            reviewRequestCount++;
            const payload=JSON.parse(body.messages[1].content);
            assert.equal(payload.review.title,'验收：交易依据与执行偏差');
            assert.equal(payload.context.marketDataAvailable,false);
            assert.equal(payload.context.ledger.length,1,'AI上下文仅包含当前周期/关联成交');
            result={summary:'本地验收生成的复盘草稿',fields:{nextChecklist:'下次先核对费用与触发条件。'},suggestedScores:{},missingData:['尚未接入真实行情']};
          }
          response.writeHead(200,{'Content-Type':'application/json'});
          response.end(JSON.stringify({choices:[{message:{content:JSON.stringify(result)}}]}));
        } catch(error) {
          mockErrors.push(error.message);
          response.writeHead(500,{'Content-Type':'application/json'});response.end('{}');
        }
      });
    });
    mockServers.push(server);
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    urls[target]=`http://127.0.0.1:${server.address().port}/v1`;
  }
  assert.notEqual(urls.text,urls.vision,'文本和图片分别使用不同服务');
  await click('[data-nav-group="C"]');
  await navigate('C01');
  await navigate('C07');
  await wait('document.querySelectorAll("form[data-ai-profile]").length===2','全局AI双配置页面');
  assert.equal(await evaluate('!!document.querySelector(".overlay")'),false,'全局设置直接显示在页面');
  for(const target of ['text','vision']) {
    await evaluate(`(()=>{const f=document.querySelector('form[data-ai-profile="${target}"]');for(const [key,value] of Object.entries(${JSON.stringify({baseUrl:urls[target],model:`mock-${target}`,apiKey:`account-smoke-${target}-key`})})){const el=f.elements.namedItem(key);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));}f.elements.enabled.checked=true;f.elements.enabled.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  }
  await click('form[data-ai-profile="text"] [data-ai-action="save"]');
  await wait('AT.aiSettings.config.profiles.text.hasKey','通过全局设置保存文本配置');
  assert.equal(await evaluate('document.querySelector("form[data-ai-profile=vision] [name=model]").value'),'mock-vision','保存文本配置保留未提交的图片设置');
  assert.equal(await evaluate('document.querySelector("form[data-ai-profile=vision] [name=apiKey]").value'),'account-smoke-vision-key','保存另一配置不会清空待保存密钥');
  await click('form[data-ai-profile="vision"] [data-ai-action="save"]');
  await wait('AT.aiSettings.config.profiles.vision.hasKey','通过全局设置保存图片配置');
  configSaveDelay=400;
  await evaluate('(()=>{const el=document.querySelector("form[data-ai-profile=text] [name=enabled]");el.checked=false;el.dispatchEvent(new Event("input",{bubbles:true}));})()');
  await click('form[data-ai-profile="text"] [data-ai-action="save"]');
  await navigate('C06');
  await navigate('C07');
  await wait('!AT.aiSettings.config.profiles.text.enabled','延迟保存完成');
  assert.equal(await evaluate('document.querySelector("form[data-ai-profile=text] [name=enabled]").checked'),false,'保存期间切页返回后表单同步最新配置');
  assert.equal(await evaluate('AT.aiSettings.config.profiles.vision.enabled'),true,'停用文本不影响图片');
  configSaveDelay=0;
  await evaluate('(()=>{const el=document.querySelector("form[data-ai-profile=text] [name=enabled]");el.checked=true;el.dispatchEvent(new Event("input",{bubbles:true}));})()');
  await click('form[data-ai-profile="text"] [data-ai-action="save"]');
  await wait('AT.aiSettings.config.profiles.text.enabled','重新启用文本配置');
  for(const target of ['text','vision']) {
    assert.equal(await evaluate(`document.querySelector('form[data-ai-profile="${target}"] [name=apiKey]').value`),'','保存后不回显密钥');
    await click(`form[data-ai-profile="${target}"] [data-ai-action="test"]`);
    const start=Date.now();while(!connectionCounts[target]&&Date.now()-start<5000)await new Promise(resolve=>setTimeout(resolve,40));
    assert.equal(connectionCounts[target],1,`${target} 独立连接测试`);
    await wait(`!document.querySelector('form[data-ai-profile="${target}"] [data-ai-action="test"]').disabled`,'连接测试完成');
  }
  const config=await evaluate("atradeDesktop.ai('config.get')");
  assert.equal(config.ok,true);
  assert.equal(config.data.profiles.text.baseUrl,urls.text);
  assert.equal(config.data.profiles.vision.baseUrl,urls.vision);
  assert.ok(!JSON.stringify(config).includes('account-smoke-text-key'));
  assert.ok(!JSON.stringify(config).includes('account-smoke-vision-key'));
  await screenshot('ai-global-settings.png');
  win.setSize(900,700);
  await new Promise(resolve=>setTimeout(resolve,250));
  assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false,'窄屏全局设置没有横向溢出');
  await screenshot('ai-global-settings-narrow.png');
  win.setSize(1480,1000);
  await new Promise(resolve=>setTimeout(resolve,250));
  await click('[data-nav-group="A"]');
  await navigate('A02');
}

app.whenReady().then(async () => {
  win = await require('../desktop/runtime.cjs').createDesktop({ dataDirectory, show: false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', (_event, details) => { if (details.level === 'error') rendererErrors.push(details.message); });
    window.webContents.on('preload-error', (_event, _file, error) => rendererErrors.push(error.message));
  } });
  await evaluate(`window.__accountSmokeErrors=[];window.addEventListener('error',event=>__accountSmokeErrors.push(event.message));window.addEventListener('unhandledrejection',event=>__accountSmokeErrors.push(String(event.reason)));`);
  await wait('!!AT.businessUI?.workspace', '账户工作区启动');
  await navigate('A02');

  if (phase === 'write') {
    await configureMock();
    assert.equal((await state()).accounts.length, 0, '新目录没有示例账户');
    await button('account-new');
    await fill({ name: '验收主账户', kind: 'paper', startDate: '2026-01-01', openingCash: 10000, goalPercent: 10 });
    await save('AT.businessUI.workspace.accounts.length===1', '真实创建第一账户');
    assert.equal(await evaluate('document.querySelectorAll("[data-business=ai-config],form[data-ai-profile]").length'),0,'账户总览不提供局部AI配置编辑');
    const firstId = (await state()).accountId;
    await button('accounts');
    await button('account-new');
    await fill({ name: '验收隔离账户', kind: 'real', startDate: '2026-01-01', openingCash: 500 });
    await save('AT.businessUI.workspace.accounts.length===2', '真实创建第二账户');
    assert.equal((await state()).summary.cash, 500);
    await evaluate(`const selector=document.querySelector('[data-business-account]');selector.value=${JSON.stringify(firstId)};selector.dispatchEvent(new Event('change',{bubbles:true}));`);
    await wait(`AT.businessUI.workspace.accountId===${JSON.stringify(firstId)}`, '账户切换');

    await button('ledger-new');
    await fill({ type: 'buy', date: '2026-01-02', time: '09:30:00', code: '600000', name: '浦发银行', quantity: 300, price: 10, commission: 5, reason: '验收：按计划分批买入' });
    await save('AT.businessUI.workspace.ledger.length===1', '记录买入');
    assert.equal((await state()).summary.cash, 6995);
    await button('ledger-new');
    await fill({ type: 'sell', date: '2026-01-03', time: '10:00:00', code: '600000', name: '浦发银行', quantity: 100, price: 12, commission: 5, stampTax: 0.6, reason: '验收：达到分批止盈条件' });
    await save('AT.businessUI.workspace.ledger.length===2', '记录部分卖出');
    assert.equal((await state()).summary.realizedPnl, 192.73);
    assert.equal((await state()).positions[0].quantity, 200);
    assert.equal((await state()).summary.equity, null, '缺价时不显示虚构资产');

    // Exercise real renderer -> IPC -> AI HTTP -> parser -> human confirmation -> SQLite, without external requests.
    await button('import');
    await uploadImage();
    await button('import-recognize');
    await wait('document.querySelectorAll("[data-import-row]").length===1', '识别草稿预览');
    assert.equal(recognitionCount, 1);
    assert.equal(await evaluate('document.querySelector("[data-import-row] [name=date]").value'), '', '模型缺日期时不猜今天');
    await evaluate(`(() => {const row=document.querySelector('[data-import-row]');row.querySelector('[name=date]').value='2026-01-04';row.querySelector('[name=commission]').value='4.2';row.querySelector('[data-import-selected]').checked=true;})()`);
    await button('import-commit');
    await wait('!document.querySelector("[data-import-error]").hidden', '未经核对拒绝导入');
    assert.equal((await state()).ledger.length, 2, '未经确认没有写入流水');
    await evaluate('document.querySelector("[data-import-row]").scrollIntoView({block:"center"})');
    await screenshot('account-import.png');
    await evaluate(`(() => {document.querySelector('[data-import-reviewed]').checked=true;const row=document.querySelector('[data-import-row]');row.querySelector('[name=commission]').value='4.2';row.querySelector('[data-fee-confirm]').checked=true;})()`);
    await button('import-commit');
    await wait('AT.businessUI.workspace.ledger.length===3 && !document.querySelector("[data-import-row]")', '人工核对后正式导入');
    assert.equal((await state()).summary.cash, 7035.2);
    const imported = (await state()).ledger.find(record => record.source === 'ocr');
    assert.match(imported.importKey, /^ocr:[a-f0-9]{64}:1$/);
    assert.match(imported.sourceDetails.imageHash, /^[a-f0-9]{64}$/);

    // The same image must remain a duplicate even when a second model response changes its prose.
    await button('import');
    await uploadImage();
    await evaluate('document.querySelector("[name=importDate]").value="2026-01-04"');
    await button('import-recognize');
    await wait('document.querySelectorAll("[data-import-row]").length===1', '再次识别同一截图');
    assert.equal(recognitionCount, 2);
    assert.equal(await evaluate('document.querySelector("[data-import-selected]").checked'), false, '同图记录默认不选');
    assert.ok(await evaluate('document.querySelector("[data-import-row]").textContent.includes("重复")'), '同图不同识别文字仍识别重复');
    await button('import-commit');
    await wait('!document.querySelector("[data-import-error]").hidden', '拒绝重复导入');
    assert.equal((await state()).ledger.length, 3);
    await button('close');

    await navigate('A04');
    await button('plan-new');
    await fill({ date: '2026-01-05', code: '600000', name: '浦发银行', action: 'sell', quantity: 100, status: 'ready', trigger: '收盘确认突破 12 元', stopLoss: '收盘跌破 10.8 元', takeProfit: '达到 13 元分批退出', reason: '只按已写明的触发条件执行', executionNotes: '待盘后核对' });
    await evaluate('document.querySelector("[name=linkedTradeIds]").checked=true');
    await save('AT.businessUI.workspace.plans.length===1', '交易计划及关联成交');

    await navigate('A02');
    await button('snapshots');
    await button('snapshot-new');
    await fill({ date: '2026-01-04' });
    await wait('!document.querySelector("#business-form").dataset.loading && !!document.querySelector("[data-snapshot-price=\\"600000\\"]")','历史快照持仓加载');
    await fill({ 'price:600000': 12.2 });
    await save('AT.businessUI.workspace.snapshots.length===1', '保存真实持仓资产快照');
    assert.equal((await state()).summary.equity, 10695.2);
    await screenshot('account-overview.png');

    await navigate('A08');
    await button('review-new');
    assert.equal(await evaluate('!!document.querySelector(".overlay")'), false, '复盘直接在页面中编辑');
    assert.equal(await evaluate('document.querySelectorAll("#workspace-root details").length'), 0, '复盘没有三角折叠项');
    await fill({ date: '2026-01-04', title: '验收：交易依据与执行偏差' });
    await evaluate(`(() => {const f=document.querySelector('#business-form');for(const el of f.querySelectorAll('[name^="field:"]')){el.value=el.name.endsWith('aiDraft')?'':'验收事实：有计划、有成交、有费用；下一步核对执行偏差。';el.dispatchEvent(new Event('input',{bubbles:true}));}for(const el of f.querySelectorAll('[name^="score:"][value="3"]'))el.checked=true;for(const el of f.querySelectorAll('[name^="evidence:"]'))el.value='已核对计划与真实成交记录';f.querySelector('[name=relatedTradeIds]').checked=true;})()`);
    assert.equal(await evaluate('new Set([...document.querySelectorAll("[name^=\\"score:\\"]")].map(el=>el.name)).size'), 24, '详细24项自评表');
    await click('[data-review-section="scores"]');
    await click('[data-score-dimension="2"]');
    assert.equal(await evaluate('document.querySelectorAll("[data-score-panel]:not([hidden])").length'), 1, '评分每次聚焦一个维度');
    assert.equal(await evaluate('new FormData(document.querySelector("#business-form")).getAll("relatedTradeIds").length'), 1, '隐藏分区的关联成交仍保留');
    await screenshot('account-review-scores.png');
    await button('review-discard');
    assert.ok(await evaluate('!!document.querySelector(".overlay")'), '丢弃仍需明确确认');
    await button('close');
    assert.equal(await evaluate('document.querySelector("#business-form [name=title]").value'), '验收：交易依据与执行偏差', '取消丢弃继续原稿');
    await click('[data-business="review-mode"][data-mode="full"]');
    await click('[data-review-section="section-0"]');
    await screenshot('account-review-editor.png');
    await click('[data-review-section="ai"]');
    await button('ai-review');
    await wait('!!document.querySelector(".business-ai-result")', 'AI复盘接口生成待采纳草稿');
    assert.equal(reviewRequestCount, 1);
    assert.equal((await state()).reviews.length, 0, 'AI结果不会自动写复盘');
    await button('ai-adopt');
    assert.ok(await evaluate('document.querySelector("[name=\\"field:aiDraft\\"]").value.includes("本地验收生成")'), '显式采纳填入附注');
    await save('AT.businessUI.workspace.reviews.length===1', '完整复盘保存');
    const saved = (await state()).reviews[0];
    assert.equal(saved.score.score, 75);
    assert.equal(saved.score.coverage, 100);
    assert.equal(saved.relatedTradeIds.length, 1);
    assert.ok(saved.fields.aiDraft.includes('本地验收生成的复盘草稿'));
    await screenshot('account-review.png');

    await button('review-new');
    await fill({title:'验收：应被明确丢弃的草稿'});
    await button('review-discard');
    await button('review-discard-confirm');
    await wait('!document.querySelector("#business-form")', '明确丢弃后退出编辑页');
    assert.equal(await evaluate('Object.values(AT.state.businessReviewDrafts||{}).some(r=>r.title==="验收：应被明确丢弃的草稿")'), false, '关闭编辑页不会重新生成已丢弃草稿');

    // A delayed file read must not replace another editor after the user has left import.
    await navigate('A04');
    await button('import');
    await click('[data-business="import-tab"][data-tab="file"]');
    await evaluate('window.__readFileOriginal=FileReader.prototype.readAsDataURL;FileReader.prototype.readAsDataURL=function(file){setTimeout(()=>__readFileOriginal.call(this,file),400)};true');
    await uploadText('[data-business-import-file]','异步测试.json',JSON.stringify({rows:[{date:'2026-01-06',type:'buy',code:'600000',quantity:100,price:10}]}));
    await navigate('A08');
    await button('review-edit');
    assert.equal(await evaluate('document.querySelectorAll(".review-content > [data-review-panel]").length===document.querySelectorAll("[data-review-panel]").length'), true, '已有评分的复盘分区仍在同一内容区');
    // Editing an existing review then immediately closing must persist the draft, without silently overwriting the saved review.
    await fill({ title: '验收：未提交草稿的最后一个字。' });
    await new Promise(resolve=>setTimeout(resolve,700));
    assert.equal(await evaluate('document.querySelector("#business-form [name=title]").value'), '验收：未提交草稿的最后一个字。', '迟到导入不会抢回页面或覆盖正在编辑的复盘');
    await evaluate('FileReader.prototype.readAsDataURL=__readFileOriginal;true');
  } else if (phase === 'read') {
    const restored = await state();
    const restoredAI = await evaluate("atradeDesktop.ai('config.get')");
    assert.equal(restoredAI.data.profiles.text.model,'mock-text','重启恢复全局文本配置');
    assert.equal(restoredAI.data.profiles.vision.model,'mock-vision','重启恢复全局图片配置');
    assert.notEqual(restoredAI.data.profiles.text.baseUrl,restoredAI.data.profiles.vision.baseUrl,'重启后仍保留两个独立接口');
    assert.equal(restoredAI.data.profiles.text.hasKey,true);
    assert.equal(restoredAI.data.profiles.vision.hasKey,true);
    assert.equal(await evaluate('JSON.stringify(AT.state).includes("account-smoke-text-key")||JSON.stringify(AT.state).includes("account-smoke-vision-key")'),false,'AI密钥未进入界面状态文件');
    assert.equal(restored.accounts.length, 2, '跨进程恢复两个账户');
    assert.equal(restored.ledger.length, 3, '跨进程恢复有效流水');
    assert.equal(restored.positions[0].quantity, 300);
    assert.equal(restored.summary.cash, 7035.2);
    assert.equal(restored.summary.equity, 10695.2);
    assert.equal(restored.reviews.length, 1);
    assert.equal(restored.reviews[0].score.score, 75);
    assert.equal(restored.plans.length, 1);
    assert.equal(restored.snapshots.length, 1);
    assert.equal(restored.reviews[0].title, '验收：交易依据与执行偏差', '未提交草稿没有覆盖正式复盘');
    const firstId = restored.accountId;
    const otherId = restored.accounts.find(a => a.id !== firstId).id;
    await evaluate(`const selector=document.querySelector('[data-business-account]');selector.value=${JSON.stringify(otherId)};selector.dispatchEvent(new Event('change',{bubbles:true}));`);
    await wait(`AT.businessUI.workspace.accountId===${JSON.stringify(otherId)}`, '重启后切换隔离账户');
    assert.equal((await state()).ledger.length, 0);
    assert.equal((await state()).reviews.length, 0);
    assert.equal((await state()).summary.cash, 500);
    await evaluate(`document.querySelector('[data-business-account]').value=${JSON.stringify(firstId)};document.querySelector('[data-business-account]').dispatchEvent(new Event('change',{bubbles:true}));`);
    await wait(`AT.businessUI.workspace.accountId===${JSON.stringify(firstId)}`, '返回原账户');
    await navigate('A08');
    await button('review-edit');
    assert.equal(await evaluate('document.querySelector("#business-form [name=title]").value'), '验收：未提交草稿的最后一个字。', '关窗后的复盘草稿恢复');
    await save('AT.businessUI.workspace.reviews[0].revision===2', '恢复草稿后显式保存');
  }

  if (phase === 'optimize') {
    await require('./account-optimization-checks.cjs')({ evaluate, click, button, wait, fill, save, navigate, state, uploadText, screenshot, directory, win });
  }
  assert.deepEqual(rendererErrors, [], '账户界面没有渲染或 preload 错误');
  assert.deepEqual(mockErrors, [], '本地AI模拟服务请求正确');
  assert.deepEqual(await evaluate('window.__accountSmokeErrors'), [], '账户交互没有未处理异常');
  assert.equal(await evaluate('AT.accountModule.flush()'), true, '关闭前业务草稿可保存');
  console.log(`PASS 账户桌面 ${phase} 阶段`);
  win.once('closed', () => { mockServers.forEach(server=>server.close()); app.exit(0); });
  await click('[data-window-action="close"]');
}).catch(async error => {
  console.error(error.stack);
  mockServers.forEach(server=>server.close());
  if (win && !win.isDestroyed()) {
    try { await screenshot('account-smoke-failure.png'); } catch {}
    win.destroy();
  }
  app.exit(1);
});
