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
    window.webContents.on('console-message', (_event, details) => {
      if (details.level === 'error') rendererErrors.push(details.message);
    });
    window.webContents.on('preload-error', (_event, _file, error) => rendererErrors.push(error.message));
  } });
  await check('!!document.querySelector("#main h1")', '主页面完成渲染');
  await check('typeof require === "undefined" && typeof process === "undefined"', '页面无 Node 权限');
  await check('!!window.atradeDesktop && !window.atradeDesktop.send && !window.atradeDesktop.invoke', '受限桥接');
  await check('document.querySelector(".demo-label").textContent.includes("演示")', '保留业务演示标识');
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
      document.querySelector('[data-action="note"]').click();
      const note = document.querySelector('[data-note="A12"]');
      note.value = '桌面验收：输入后立即关闭，保留最后一个字。';
      note.dispatchEvent(new Event('input', { bubbles: true }));
    `);
    // Close before the preview's 300 ms note debounce. The production close
    // handler must flush the current state through the real IPC/file path.
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
    await evaluate(`document.querySelector('[data-action="note"]').click()`);
    await check('document.querySelector("[data-note=A12]").value === AT.state.notes.A12', '笔记在 UI 中恢复');

    // Make the real workspace path temporarily unwritable, without touching user data.
    const heldDirectory = path.join(testDirectory, 'workspace-held');
    await fs.rename(dataDirectory, heldDirectory);
    await fs.writeFile(dataDirectory, 'smoke test write failure');
    try {
      await evaluate('AT.state.notes.failureProbe = "save failure probe"');
      assert.equal(await evaluate('AT.persist()'), false, '保存失败返回 false');
      await check('/失败|未保存/.test(document.querySelector("#save-status").textContent)', '失败状态真实显示');
    } finally {
      await fs.unlink(dataDirectory);
      await fs.rename(heldDirectory, dataDirectory);
    }
    await evaluate('delete AT.state.notes.failureProbe');
    assert.equal(await evaluate('AT.persist()'), true, '恢复可写后可重试');
    await check('/已保存/.test(document.querySelector("#save-status").textContent)', '成功保存状态');

    await fs.mkdir(path.join(root, 'artifacts'), { recursive: true });
    // Hidden windows can leave entrance animations at their first frame.
    // Finish only the modal's finite animation for a deterministic screenshot.
    await evaluate('document.querySelector(".modal").getAnimations().forEach(animation => animation.finish())');
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
  if (win && !win.isDestroyed()) win.destroy();
  app.exit(1);
});
