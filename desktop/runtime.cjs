const { BrowserWindow, dialog, ipcMain, protocol, session, shell } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { StateStore } = require('./state-store.cjs');

const APP_URL = 'atrade://app/index.html';
const previewRoot = path.resolve(__dirname, '../preview');
const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
};

function isAppDocument(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'atrade:' && url.host === 'app' &&
      url.pathname === '/index.html' && !url.search;
  } catch { return false; }
}

async function installProtocol() {
  protocol.handle('atrade', async request => {
    try {
      const url = new URL(request.url);
      if (url.host !== 'app' || request.method !== 'GET') return new Response('', { status: 403 });
      const pathname = decodeURIComponent(url.pathname);
      const file = path.resolve(previewRoot, '.' + pathname);
      const relative = path.relative(previewRoot, file);
      const type = mimeTypes[path.extname(file)];
      if (relative.startsWith('..') || path.isAbsolute(relative) || !type) {
        return new Response('', { status: 403 });
      }
      return new Response(await fs.readFile(file), {
        headers: { 'Content-Type': type, 'X-Content-Type-Options': 'nosniff' },
      });
    } catch {
      return new Response('', { status: 404 });
    }
  });
}

async function createDesktop({ dataDirectory, show = true, onWindow } = {}) {
  const store = new StateStore(dataDirectory);
  const initial = store.load(); // Fail before opening a writable workspace if recovery is unsafe.
  const bootstrap = { ...initial, storage: store.paths };
  await installProtocol();
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);

  const win = new BrowserWindow({
    width: 1480,
    height: 1000,
    minWidth: 820,
    minHeight: 620,
    show: false,
    title: 'A-trade',
    icon: path.join(__dirname, 'assets/app-icon.ico'),
    frame: false,
    backgroundColor: '#f4f7fb',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
    },
  });
  win.removeMenu();
  win.webContents.on('before-input-event', (event, input) => {
    // A raw reload would bypass the close/save handshake while a note is pending.
    if (input.key === 'F5' || ((input.control || input.meta) && input.key.toLowerCase() === 'r')) {
      event.preventDefault();
    }
  });
  const trusted = event => event.sender === win.webContents &&
    event.senderFrame === win.webContents.mainFrame && isAppDocument(event.senderFrame.url);

  let windowStateRevision = 0;
  const readWindowState = () => ({
    maximized: win.isMaximized(),
    fullscreen: win.isFullScreen(),
    revision: windowStateRevision,
  });
  const publishWindowState = () => {
    windowStateRevision += 1;
    if (!win.isDestroyed() && !win.webContents.isDestroyed() && isAppDocument(win.webContents.getURL())) {
      win.webContents.send('atrade:window-state', readWindowState());
    }
  };
  for (const eventName of ['maximize', 'unmaximize', 'restore', 'enter-full-screen', 'leave-full-screen']) {
    win.on(eventName, publishWindowState);
  }
  ipcMain.handle('atrade:get-window-state', event => trusted(event) ? readWindowState() : null);
  ipcMain.handle('atrade:window-action', (event, action) => {
    if (!trusted(event)) return { ok: false };
    switch (action) {
      case 'minimize': win.minimize(); break;
      case 'toggle-maximize':
        if (win.isFullScreen()) win.setFullScreen(false);
        else if (win.isMaximized()) win.unmaximize();
        else win.maximize();
        break;
      // Always use close(), so the existing save handshake also protects this button.
      case 'close': win.close(); break;
      default: return { ok: false };
    }
    return { ok: true };
  });

  const handleBootstrap = event => {
    event.returnValue = trusted(event) ? bootstrap : null;
  };
  ipcMain.on('atrade:bootstrap', handleBootstrap);
  ipcMain.handle('atrade:save-state', (event, state) => {
    if (!trusted(event)) return { ok: false, error: '此页面无权保存本地数据。' };
    try {
      const result = store.save(state);
      bootstrap.state = structuredClone(state);
      return { ok: true, ...result };
    } catch {
      return { ok: false, error: '本地保存失败，请检查数据目录权限、可用磁盘空间或数据格式，然后重试。' };
    }
  });
  ipcMain.handle('atrade:open-data-folder', async event => {
    if (!trusted(event)) return { ok: false, error: '此页面无权打开数据目录。' };
    try {
      await fs.mkdir(dataDirectory, { recursive: true });
      const error = await shell.openPath(dataDirectory);
      return error ? { ok: false, error: '无法打开数据目录，请从设置中复制路径。' } : { ok: true };
    } catch { return { ok: false, error: '无法打开数据目录，请检查目录权限。' }; }
  });

  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event, url) => {
    if (!isAppDocument(url)) event.preventDefault();
  });
  win.webContents.on('will-attach-webview', event => event.preventDefault());

  let allowClose = false;
  let closing = false;
  win.on('close', event => {
    if (allowClose) return;
    event.preventDefault();
    if (closing) return;
    closing = true;
    win.setEnabled(false);
    // The renderer already updates note state on each input. Await its last write,
    // including pending debounced edits, before destroying the renderer process.
    let timeout;
    const deadline = new Promise(resolve => { timeout = setTimeout(() => resolve(false), 8000); });
    const save = win.webContents.executeJavaScript(
      'typeof AT !== "undefined" && typeof AT.persist === "function" ? Promise.resolve(AT.persist()) : false',
    ).catch(() => false);
    Promise.race([save, deadline]).then(async saved => {
      clearTimeout(timeout);
      if (win.isDestroyed()) return;
      if (saved === true) {
        allowClose = true;
        win.close();
        return;
      }
      win.setEnabled(true);
      const { response } = await dialog.showMessageBox(win, {
        type: 'error',
        title: '尚有更改未保存',
        message: '本地保存未完成，窗口已保留。',
        detail: '可以检查磁盘空间和数据目录权限后重试。放弃退出会丢失本次尚未保存的更改。',
        buttons: ['保留窗口', '重试保存并退出', '放弃未保存更改并退出'],
        defaultId: 0,
        cancelId: 0,
        noLink: true,
      });
      closing = false;
      if (response === 1) win.close();
      if (response === 2) { allowClose = true; win.close(); }
    }).catch(() => {
      closing = false;
      if (!win.isDestroyed()) win.setEnabled(true);
    });
  });
  win.once('closed', () => {
    ipcMain.removeListener('atrade:bootstrap', handleBootstrap);
    ipcMain.removeHandler('atrade:save-state');
    ipcMain.removeHandler('atrade:open-data-folder');
    ipcMain.removeHandler('atrade:get-window-state');
    ipcMain.removeHandler('atrade:window-action');
    protocol.unhandle('atrade');
  });
  onWindow?.(win);
  await win.loadURL(APP_URL);
  if (show) win.show();
  return win;
}

module.exports = { createDesktop, isAppDocument };
