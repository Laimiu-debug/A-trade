const { app, protocol, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const [testDirectory, phase] = process.argv.slice(2);
if (!testDirectory || !['write', 'read'].includes(phase)) throw new Error('Missing smoke test arguments');
const dataDirectory = path.join(testDirectory, 'workspace');
const root = path.resolve(__dirname, '..');
app.setPath('userData', path.join(testDirectory, 'electron-profile'));
protocol.registerSchemesAsPrivileged([
  { scheme: 'atrade', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

let win;
const rendererErrors = [];
const evaluate = code => win.webContents.executeJavaScript(code);
const check = async (expression, label) => assert.equal(await evaluate(expression), true, label);
const windowEvent = name => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error(`窗口事件 ${name} 超时`)), 4000);
  win.once(name, () => { clearTimeout(timer); resolve(); });
});

app.whenReady().then(async () => {
  const { createDesktop } = require('../desktop/runtime.cjs');
  win = await createDesktop({ dataDirectory, show: false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', event => {
      if (event.level === 'error') rendererErrors.push(event.message);
    });
    window.webContents.on('preload-error', (_event, _file, error) => rendererErrors.push(error.message));
  } });
  await check('!!document.querySelector("#main h1")', '主页面完成渲染');
  await check('typeof require === "undefined" && typeof process === "undefined"', '页面无 Node 权限');
  await check('!!window.atradeDesktop && !window.atradeDesktop.send && !window.atradeDesktop.invoke', '受限桥接');
  await check('!!document.querySelector(".demo-label").textContent', '页面标识数据来源');
  await check('document.title.includes("桌面开发版")', '窗口标题区分桌面版');
  await check('document.querySelectorAll("[data-window-action]").length === 3', '自定义窗口控件可用');
  await check('AT.state.tasks.every(t => !["running", "queued"].includes(t.status))', '任务启动后不自动演示');
  await check('AT.storage ? true : !!window.atradeDesktop', '桌面存储可用');

  if (phase === 'write') {
    await evaluate(`
      document.querySelector('[data-nav="C05"]').click();
      const compact = document.querySelector('[data-pref="compact"]');
      compact.checked = true;
      compact.dispatchEvent(new Event('change', { bubbles: true }));
    `);
    await check('AT.state.compact === true && AT.state.page === "C05"', '设置通过页面修改');
    await evaluate(`
      // Seed a legacy workspace note and open its supported migration surface.
      AT.state.notes.A12 = '桌面验收：输入后立即关闭，保留最后一个字。';
      AT.businessUI.handleAction('data-info');
      document.querySelector('[data-business="legacy-notes"]').click();
    `);
    await check('document.querySelector(".business-legacy-text").textContent.includes("保留最后一个字")', '升级前笔记在历史入口中可见');
    await check('!document.querySelector("#workspace-root").hidden && document.querySelector("#main").hidden && !document.querySelector(".overlay")', '历史笔记使用页内工作区');
    // Close without an explicit persist; the production handshake saves UI state.
    // New review inputs are covered by the separate real-account smoke test.
  } else {
    await check('AT.state.notes.A12 === "桌面验收：输入后立即关闭，保留最后一个字。"', '跨进程重启恢复笔记');
    await check('AT.state.compact === true && document.body.classList.contains("dense") && AT.state.page === "C05"', '重启恢复显示配置与页面');
    const maximized = windowEvent('maximize');
    await evaluate('document.querySelector("[data-window-action=toggle-maximize]").click()');
    await maximized;
    assert.equal(win.isMaximized(), true, '自定义按钮最大化');
    const restored = windowEvent('unmaximize');
    await evaluate('document.querySelector("[data-window-action=toggle-maximize]").click()');
    await restored;
    assert.equal(win.isMaximized(), false, '自定义按钮还原');
    const minimized = windowEvent('minimize');
    await evaluate('document.querySelector("[data-window-action=minimize]").click()');
    await minimized;
    assert.equal(win.isMinimized(), true, '自定义按钮最小化');
    win.restore();
    // Keep the restored test window painting; minimizing then hiding can suspend
    // requestAnimationFrame even with background timer throttling disabled.
    win.showInactive();
    assert.equal((await evaluate('atradeDesktop.windowAction("unsupported-action")')).ok, false, '窗口动作白名单');
    await evaluate(`AT.businessUI.handleAction('data-info'); document.querySelector('[data-business="legacy-notes"]').click()`);
    await check('document.querySelector(".business-legacy-text").textContent === AT.state.notes.A12', '历史笔记在 UI 中恢复');
    await check('!document.querySelector("#workspace-root").hidden && document.querySelector("#main").hidden && !document.querySelector(".overlay")', '重启后的历史笔记仍在页面内显示');

    // Make the real workspace path temporarily unwritable, without touching user data.
    // SQLite keeps the directory open on Windows. Block only the UI-state file.
    const stateFile = path.join(dataDirectory, 'workspace-state.json');
    const heldFile = path.join(dataDirectory, 'workspace-state.test-held');
    await fs.rename(stateFile, heldFile);
    await fs.mkdir(stateFile);
    try {
      await evaluate('AT.state.notes.failureProbe = "save failure probe"');
      assert.equal(await evaluate('AT.persist()'), false, '保存失败返回 false');
      await check('/失败|未保存/.test(document.querySelector("#save-status").textContent)', '失败状态真实显示');
    } finally {
      await fs.rmdir(stateFile);
      await fs.rename(heldFile, stateFile);
    }
    await evaluate('delete AT.state.notes.failureProbe');
    assert.equal(await evaluate('AT.persist()'), true, '恢复可写后可重试');
    await check('/已保存/.test(document.querySelector("#save-status").textContent)', '成功保存状态');

    await fs.mkdir(path.join(root, 'artifacts'), { recursive: true });
    // Hidden windows can leave entrance animations at their first frame.
    // Finish finite workspace animations without touching ambient effects.
    await evaluate('document.querySelector("#workspace-root").getAnimations({ subtree: true }).filter(animation => Number.isFinite(animation.effect?.getTiming().iterations)).forEach(animation => animation.finish())');
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    const screenshot = await win.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
    await fs.writeFile(path.join(root, 'artifacts/desktop-smoke.png'), screenshot.toPNG());

    // Keep the file:// preview usable with localStorage and no desktop bridge.
    const browser = new BrowserWindow({ show: false, webPreferences: {
      nodeIntegration: false, contextIsolation: true, sandbox: true,
    } });
    await browser.loadFile(path.join(root, 'preview/index.html'));
    assert.equal(await browser.webContents.executeJavaScript(`
      typeof window.atradeDesktop === 'undefined' && !!document.querySelector('#main h1') && AT.persist() === true
    `), true, '浏览器预览保留同步 localStorage 保存');
    browser.destroy();
  }

  assert.deepEqual(rendererErrors, [], '桌面无渲染或 preload 错误');
  win.once('closed', () => app.exit(0));
  await evaluate('document.querySelector("[data-window-action=close]").click()');
}).catch(error => {
  console.error(error.stack);
  console.error(rendererErrors);
  if (win && !win.isDestroyed()) win.destroy();
  app.exit(1);
});
