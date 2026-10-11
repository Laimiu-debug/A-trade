const {app, protocol, dialog, clipboard} = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');

const testDirectory = process.argv[2];
if (!testDirectory || path.dirname(path.resolve(testDirectory)) !== path.resolve(os.tmpdir()) || !path.basename(testDirectory).startsWith('atrade-settings-smoke-')) throw new Error('Expected an isolated settings smoke directory');
const root = path.resolve(__dirname, '..'), dataDirectory = path.join(testDirectory, 'workspace');
app.setPath('userData', path.join(testDirectory, 'electron-profile'));
protocol.registerSchemesAsPrivileged([{scheme: 'atrade', privileges: {standard: true, secure: true, supportFetchAPI: true}}]);
let win;
const errors = [], checks = [];
const evaluate = code => win.webContents.executeJavaScript(code);
async function check(expression, label) {assert.equal(await evaluate(expression), true, label); checks.push(label);}
async function until(expression, label, timeout = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw new Error(`等待超时：${label}`);
}
async function navigate(page) {
  await evaluate(`AT.navigate(${JSON.stringify(page)})`);
  await until(`AT.state.page === ${JSON.stringify(page)} && !!document.querySelector('#main h1')`, `打开 ${page}`);
}
async function fillRules(values) {
  await evaluate(`(() => {const form=document.querySelector('#rule-form');for(const [name,value] of Object.entries(${JSON.stringify(values)})){form.elements[name].value=String(value);form.elements[name].dispatchEvent(new Event('input',{bubbles:true}));}form.requestSubmit();})()`);
}
async function screenshot(name, width = 1480) {
  win.setSize(width, 1000);
  await until(`!document.querySelector('.toast.show')`, '截图前提示消退', 5000);
  await evaluate(`window.scrollTo(0,0);document.querySelectorAll('#main *').forEach(element=>element.getAnimations().filter(animation=>Number.isFinite(animation.effect?.getTiming().iterations)).forEach(animation=>animation.finish()));new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
  await check('document.documentElement.scrollWidth <= innerWidth + 1', `${name} 页面无横向溢出`);
  if (name === 'settings-display-820.png') await check(`document.querySelector('.fx-reading').getBoundingClientRect().top < document.querySelector('.fx-preview-panel').getBoundingClientRect().top && document.querySelector('.fx-presets-panel').getBoundingClientRect().top < document.querySelector('.fx-preview-panel').getBoundingClientRect().top`, '820 窄宽先显示阅读和预设控件');
  const image = await win.webContents.capturePage(undefined, {stayHidden: true, stayAwake: true});
  assert.equal(image.isEmpty(), false, `${name} 截图有效`);
  await fs.mkdir(path.join(root, 'artifacts'), {recursive: true});
  await fs.writeFile(path.join(root, 'artifacts', name), image.toPNG());
}

app.whenReady().then(async () => {
  const {createDesktop} = require('../desktop/runtime.cjs');
  win = await createDesktop({dataDirectory, show: false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', event => {if (event.level === 'error') errors.push(event.message);});
    window.webContents.on('preload-error', (_event, _file, error) => errors.push(error.message));
  }});
  await check('!!AT.settingsState && !!AT.settingsPage && !!AT.workspaceBackup', '设置模块加载');
  await evaluate(`window.__settingsSmokeErrors=[];window.addEventListener('error',event=>window.__settingsSmokeErrors.push(event.message));window.addEventListener('unhandledrejection',event=>window.__settingsSmokeErrors.push(String(event.reason?.message||event.reason)));`);

  await navigate('C02');
  await fillRules({commissionRate: 0, minCommission: 0});
  await until(`document.querySelector('[data-rule-feedback]')?.textContent === '参数已保存。'`, '保存零费率');
  await check('AT.state.fees.commissionRate === 0 && AT.state.fees.minCommission === 0', '费率允许保存零');
  await navigate('C03');
  await fillRules({singleStockMax: 0, totalMax: 0});
  await until(`document.querySelector('[data-rule-feedback]')?.textContent === '参数已保存。'`, '保存零仓位');
  await check('AT.state.positionRules.singleStockMax === 0 && AT.state.positionRules.totalMax === 0 && AT.state.fees.commissionRate === 0', '仓位零值与费率独立');
  await fillRules({singleStockMax: 101, totalMax: 100});
  await check(`document.querySelector('[name=singleStockMax]').validity.rangeOverflow && AT.state.positionRules.totalMax === 0`, '拒绝超过 100% 的仓位');
  await fillRules({singleStockMax: 80, totalMax: 50});
  await check(`document.querySelector('[data-rule-feedback]').textContent.includes('单股仓位上限不能高于') && AT.state.positionRules.singleStockMax === 0`, '拒绝单股上限高于总量');
  await fillRules({singleStockMax: 20, totalMax: 75});
  await until(`document.querySelector('[data-rule-feedback]')?.textContent === '参数已保存。'`, '保存正常仓位');
  await navigate('C02');
  await check(`document.querySelector('[name=commissionRate]').value === '0' && document.querySelector('[name=minCommission]').value === '0' && AT.state.positionRules.totalMax === 75`, '切页后费率与仓位不串值');
  await evaluate(`window.__settingsOriginalPersist=AT.persist;AT.persist=async()=>false;true`);
  try {
    await fillRules({commissionRate: 8, minCommission: 0});
    await until(`document.querySelector('[data-rule-feedback]')?.textContent.includes('保存失败')`, '模拟费率保存失败');
    await check(`AT.state.fees.commissionRate === 0 && AT.state.fees.minCommission === 0 && document.querySelector('[name=commissionRate]').value === '8'`, '费率保存失败保留原状态与可重试草稿');
  } finally {await evaluate(`AT.persist=window.__settingsOriginalPersist;delete window.__settingsOriginalPersist`);}
  await evaluate(`document.querySelector('[data-rule-discard=fees]').click()`);
  await check(`document.querySelector('[name=commissionRate]').value === '0' && AT.state.fees.commissionRate === 0`, '保存失败后放弃恢复原参数');
  assert.equal(await evaluate('AT.persist()'), true);
  const afterDiscard = JSON.parse(await fs.readFile(path.join(dataDirectory, 'workspace-state.json'), 'utf8'));
  assert.equal(afterDiscard.state.fees.commissionRate, 0, '后续保存不会写入已经放弃的费率草稿');
  assert.equal(afterDiscard.state.fees.minCommission, 0);
  checks.push('放弃的失败费率不会被后续保存写入磁盘');

  await navigate('C07');
  await evaluate(`(() => {const form=document.querySelector('[data-ai-profile=text]');for(const [name,value] of Object.entries({baseUrl:'https://fixture.example/v1',model:'fixture-model',apiKey:'fixture-memory-only-key'})){form.elements[name].value=value;form.elements[name].dispatchEvent(new Event('input',{bubbles:true}));}})()`);
  await check('AT.aiSettings.hasUnsaved()', 'AI 输入被识别为草稿');
  await navigate('C02');
  await navigate('C07');
  await check(`(() => {const form=document.querySelector('[data-ai-profile=text]');return form.elements.model.value === 'fixture-model' && form.elements.apiKey.value === 'fixture-memory-only-key' && !document.querySelector('#main').innerHTML.includes('fixture-memory-only-key') && !JSON.stringify(AT.state).includes('fixture-memory-only-key');})()`, 'AI 切页保留草稿且密钥不进入 HTML 或工作区');
  const originalDialog = dialog.showMessageBox;
  const draftPrompts = [];
  dialog.showMessageBox = async (_window, options) => {draftPrompts.push(options.title); return {response: 0};};
  try {
    await navigate('C02');
    win.close();
    await until('AT.state.page === "C07"', '退出返回 AI 编辑');
    assert.deepEqual(draftPrompts, ['AI 配置尚未保存']);
    await check('AT.aiSettings.hasUnsaved()', '退出拦截后草稿仍在');
  } finally {dialog.showMessageBox = originalDialog;}
  await evaluate(`document.querySelector('[data-ai-profile=text] [data-ai-action=discard]').click()`);
  await check(`!AT.aiSettings.hasUnsaved() && document.querySelector('[data-ai-profile=text] [name=apiKey]').value === ''`, '显式放弃清除 AI 草稿');
  const originalExecute = win.webContents.executeJavaScript;
  const failedPreflightPrompts = [];
  let resolveFailurePrompt;
  const failurePrompt = new Promise(resolve => {resolveFailurePrompt = resolve;});
  win.webContents.executeJavaScript = function(code, ...args) {
    return code === 'Boolean(window.AT?.aiSettings?.hasUnsaved())'
      ? Promise.reject(new Error('Fixture AI close preflight failed.'))
      : originalExecute.call(this, code, ...args);
  };
  dialog.showMessageBox = async (_window, options) => {failedPreflightPrompts.push({title: options.title, type: options.type}); resolveFailurePrompt(); return {response: 0};};
  let failureTimeout;
  try {
    win.close();
    await Promise.race([failurePrompt, new Promise((_, reject) => {failureTimeout = setTimeout(() => reject(new Error('退出检查失败没有进入保存失败对话框')), 5000);})]);
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(failedPreflightPrompts, [{title: '尚有更改未保存', type: 'error'}]);
    assert.equal(win.isDestroyed(), false, '失败对话框选择保留后窗口未关闭');
    assert.equal(win.isEnabled(), true, '失败后窗口可继续操作');
    checks.push('AI 退出预检查异常进入保存失败对话框并保留窗口');
  } finally {
    clearTimeout(failureTimeout);
    win.webContents.executeJavaScript = originalExecute;
    dialog.showMessageBox = originalDialog;
  }

  await navigate('C05');
  await check(`!!document.querySelector('.fx-controls-column > .fx-reading:first-child') && !!document.querySelector('.fx-reading + .fx-presets-panel')`, '阅读选项位于视觉装饰之前');
  await check(`!!document.querySelector('details.fx-advanced') && !document.querySelector('details.fx-advanced').open`, '高级视觉设置默认折叠');
  await check(`!!document.querySelector('.fx-practical-preview .data-table') && !!document.querySelector('.fx-practical-preview .field input')`, '预览包含真实表格与表单组件');
  await evaluate(`for(const key of ['compact','largeText','reduced']){const input=document.querySelector('[data-pref="'+key+'"]');input.checked=true;input.dispatchEvent(new Event('change',{bubbles:true}));}AT.state.sidebarCollapsed=true;AT.state.businessReviewDrafts={'fixture-account|new:daily':{kind:'daily',date:'2026-10-11',title:'隔离验收复盘草稿',fields:{quickObservation:'需要保留的草稿内容'},scores:{},relatedTradeIds:[]}};AT.persist()`);
  await check(`document.body.classList.contains('dense') && document.body.classList.contains('reading-large') && document.body.classList.contains('reduce-motion')`, '阅读选项立即生效');
  await screenshot('settings-display.png');
  await screenshot('settings-display-820.png', 820);

  await navigate('C04');
  await check(`!!document.querySelector('#restore-file') && document.querySelector('#main').textContent.includes('复盘草稿') && !!document.querySelector('[data-business-restore]')`, '账本与界面恢复入口分开');
  const exportFile = path.join(testDirectory, 'interface-export.json');
  const download = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('界面导出下载超时')), 10000);
    win.webContents.session.once('will-download', (_event, item) => {
      item.setSavePath(exportFile);
      item.once('done', (_done, state) => {clearTimeout(timeout); state === 'completed' ? resolve() : reject(new Error('界面导出未完成'));});
    });
  });
  await evaluate(`document.querySelector('[data-action=backup]').click()`);
  await download;
  const exportedText = await fs.readFile(exportFile, 'utf8'), exported = JSON.parse(exportedText);
  assert.equal(exported.format, 'a-trade.interface-state');
  assert.equal(exported.state.compact, true);
  assert.equal(exported.state.businessReviewDrafts['fixture-account|new:daily'].title, '隔离验收复盘草稿');
  assert.equal(exportedText.includes('fixture-memory-only-key'), false);
  await evaluate(`AT.state.compact=false;AT.state.largeText=false;AT.state.reduced=false;AT.state.sidebarCollapsed=false;AT.state.businessReviewDrafts={};AT.state.fees={commissionRate:9,minCommission:8};AT.state.positionRules={singleStockMax:10,totalMax:40};AT.persist();AT.render();`);
  await evaluate(`(() => {const transfer=new DataTransfer();transfer.items.add(new File([${JSON.stringify(exportedText)}],'interface-export.json',{type:'application/json'}));const input=document.querySelector('#restore-file');input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`!!document.querySelector('[data-action="confirm-restore"]')`, '界面恢复确认摘要');
  await check(`document.querySelector('.modal').textContent.includes('1 份复盘草稿') && AT.state.compact === false`, '恢复前显示摘要且尚未替换状态');
  await evaluate(`document.querySelector('[data-action="confirm-restore"]').click()`);
  await until(`AT.state.compact === true && !document.querySelector('[data-action="confirm-restore"]')`, '恢复后成功落盘');
  await check(`AT.state.largeText && AT.state.reduced && AT.state.sidebarCollapsed && AT.state.businessReviewDrafts['fixture-account|new:daily'].fields.quickObservation === '需要保留的草稿内容' && AT.state.fees.commissionRate === 0 && AT.state.positionRules.totalMax === 75`, '偏好、复盘草稿、独立规则完整往返');
  const stored = JSON.parse(await fs.readFile(path.join(dataDirectory, 'workspace-state.json'), 'utf8'));
  assert.equal(stored.state.businessReviewDrafts['fixture-account|new:daily'].title, '隔离验收复盘草稿');
  await screenshot('settings-backup-820.png', 820);

  // A tiny isolated TDX fixture verifies that diagnosis preserves real scan timestamps.
  const tdxRoot = path.join(testDirectory, 'fixture-tdx'), tdxDaily = path.join(tdxRoot, 'vipdoc/sh/lday');
  await fs.mkdir(tdxDaily, {recursive: true});
  const record = Buffer.alloc(32);
  [20261009, 1000, 1100, 900, 1050].forEach((value, index) => record.writeUInt32LE(value, index * 4));
  record.writeFloatLE(1000000, 20); record.writeUInt32LE(10000, 24);
  await fs.writeFile(path.join(tdxDaily, 'sh600000.day'), record);
  assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(tdxRoot)}})`)).ok, true);
  const scan = await evaluate(`atradeDesktop.market('market.scan')`);
  assert.equal(scan.ok, true);
  await evaluate(`atradeDesktop.ai('config.save',{target:'text',baseUrl:'https://diagnostic-private.example/v1',model:'private-fixture-model',enabled:false})`);
  await navigate('C06');
  await until(`document.querySelector('.settings-diagnostics')?.textContent.includes('版本 0.') && !document.querySelector('[data-diagnostics=refresh]').disabled`, '读取诊断');
  const first = await evaluate(`atradeDesktop.diagnostics(false)`);
  assert.equal(first.ok, true);
  const originalCopy = clipboard.writeText;
  let copied = null;
  clipboard.writeText = text => {copied = text;};
  try {
    await evaluate(`document.querySelector('[data-diagnostics=refresh]').click()`);
    await until(`!document.querySelector('[data-diagnostics=refresh]').disabled`, '刷新诊断');
    await evaluate(`document.querySelector('[data-diagnostics=copy]').click()`);
    await until(`document.querySelector('#toast').textContent.includes('已复制脱敏诊断')`, '复制脱敏状态');
    assert.equal(typeof copied, 'string');
  } finally {clipboard.writeText = originalCopy;}
  const diagnosis = JSON.parse(copied);
  assert.ok(Date.parse(diagnosis.checkedAt) >= Date.parse(first.data.checkedAt));
  assert.equal(Date.parse(diagnosis.local.lastScanAt), Date.parse(scan.data.lastScannedAt), '诊断显示实际本地扫描时间');
  for (const forbidden of [dataDirectory, 'diagnostic-private.example', 'private-fixture-model', '隔离验收复盘草稿', 'fixture-memory-only-key']) assert.equal(copied.includes(forbidden), false, '诊断不含私有配置或业务内容');
  const forbiddenKeys = new Set(['directory', 'path', 'root', 'baseUrl', 'model', 'apiKey', 'encryptedKey', 'accountId']);
  const inspectKeys = value => {if (!value || typeof value !== 'object') return; for (const [key, nested] of Object.entries(value)) {assert.equal(forbiddenKeys.has(key), false, '诊断使用允许的公开字段'); inspectKeys(nested);}};
  inspectKeys(diagnosis);
  checks.push('诊断刷新与复制使用脱敏白名单');
  await screenshot('settings-diagnostics.png');
  await screenshot('settings-diagnostics-820.png', 820);
  await check('window.__settingsSmokeErrors.length === 0', '页面无未处理错误');
  assert.deepEqual(errors, [], '无 renderer 或 preload 错误');
  assert.equal(await evaluate('AT.persist()'), true, '最终状态可保存');
  console.log(`PASS 设置页集成验收：${checks.length} 项检查；费率/仓位、AI 草稿与退出拦截、备份往返、显示布局、脱敏诊断。`);
  console.log('截图：artifacts/settings-display.png、settings-display-820.png、settings-backup-820.png、settings-diagnostics.png、settings-diagnostics-820.png');
  win.once('closed', () => app.exit(0));
  win.close();
}).catch(error => {
  console.error(error.stack);
  if (errors.length) console.error(errors);
  if (win && !win.isDestroyed()) win.destroy();
  app.exit(1);
});
