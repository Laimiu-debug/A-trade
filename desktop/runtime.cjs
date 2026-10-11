const { BrowserWindow, clipboard, dialog, ipcMain, protocol, session, shell, safeStorage } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { StateStore } = require('./state-store.cjs');
const { BusinessService } = require('./business-service.cjs');
const { MarketService } = require('./market-service.cjs');
const { AIService } = require('./ai-service.cjs');
const { createAccountValuation } = require('./account-valuation.cjs');
const { buildReviewContext } = require('./review-context.cjs');
const { externalDocument } = require('./external-links.cjs');

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
  const business = new BusinessService(dataDirectory, { mode: 'account' });
  const workspace = await business.request('workspace');
  if (!workspace.ok) { business.close(); const error = new Error(workspace.error); error.businessReason = workspace.error; throw error; }
  const ai = new AIService(dataDirectory, safeStorage);
  const marketBusiness = new MarketService(dataDirectory);
  const researchBusiness = new BusinessService(dataDirectory, { mode: 'research' });
  const accountValuation = createAccountValuation({ business, market: marketBusiness });
  const market = await marketBusiness.request('market.status');
  const bootstrap = { ...initial, storage: store.paths, business: workspace.data, ai: ai.publicConfig(), market };
  let lastStateSave = { state: 'not-checked', savedAt: null };
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
  ipcMain.handle('atrade:business', async (event, method, payload) => {
    if (!trusted(event)) return { ok: false, error: '此页面无权操作账户数据。' };
    if (typeof method === 'string' && method.startsWith('market.')) return { ok: false, error: '请使用行情接口。' };
    if (typeof method === 'string' && method.startsWith('research.')) return researchBusiness.request(method, payload);
    if (method === 'account.valuation') return accountValuation(payload);
    return business.request(method, payload);
  });
  const marketMethods = new Set(['market.status', 'market.configure', 'market.scan', 'market.search', 'market.bars',
    'market.online.status', 'market.online.configure', 'market.online.test', 'market.quotes',
    'market.intraday', 'market.sectors', 'market.sector', 'market.research', 'market.snapshot',
    'market.research.status', 'market.research.cancel',
  'market.news.list', 'market.finance.snapshot', 'market.finance.screen', 'market.funds.sectors',
  'market.finance.benchmark', 'market.finance.context', 'market.funds.history', 'market.sector.history',
  'market.funds.activity.list', 'market.funds.activity.history', 'market.regulatory.list',
  'market.analysis.overview', 'market.analysis.trends', 'market.analysis.moves', 'market.analysis.rebounds', 'market.analysis.ladder',
  'market.watchlist.list', 'market.watchlist.save', 'market.watchlist.delete',
  'market.watchlist.save-many', 'market.watchlist.export', 'market.watchlist.import',
  'market.watchlist.backup.export', 'market.watchlist.backup.preview', 'market.watchlist.backup.restore']);
  ipcMain.handle('atrade:market', async (event, method, payload = {}) => {
    if (!trusted(event) || !marketMethods.has(method)) return { ok: false, error: '不支持此行情请求。' };
    return marketBusiness.request(method, payload);
  });
  ipcMain.handle('atrade:open-external', async (event, value) => {
    const url = trusted(event) && externalDocument(value);
    if (!url) return {ok: false, error: '原文链接不是有效的公开网页地址。'};
    try { await shell.openExternal(url); return {ok: true}; }
    catch { return {ok: false, error: '无法打开浏览器，请稍后重试。'}; }
  });
  let choosingTDX = false;
  ipcMain.handle('atrade:choose-tdx-directory', async event => {
    if (!trusted(event)) return { ok: false, error: '此页面无权选择数据目录。' };
    if (choosingTDX) return { ok: false, error: '目录选择窗口已打开。' };
    choosingTDX = true;
    try {
      const result = await dialog.showOpenDialog(win, {
        title: '选择通达信数据目录', buttonLabel: '选择目录', properties: ['openDirectory'],
      });
      if (result.canceled || !result.filePaths.length) return { ok: true, canceled: true };
      return { ok: true, canceled: false, path: result.filePaths[0] };
    } catch { return { ok: false, error: '无法打开目录选择窗口，请直接填写目录路径。' }; }
    finally { choosingTDX = false; }
  });
  ipcMain.handle('atrade:ai', async (event, method, payload = {}) => {
    if (!trusted(event)) return { ok: false, error: '此页面无权调用 AI。' };
    try {
      if (!payload || typeof payload !== 'object' || Array.isArray(payload) || Buffer.byteLength(JSON.stringify(payload)) > 18 * 1024 * 1024) return { ok: false, error: 'AI 请求内容过大或格式无效。' };
      if (method === 'review') {
        const data = await business.request('workspace', { accountId: payload.accountId });
        if (!data.ok) return data;
        if (!payload.accountId || data.data.accountId !== payload.accountId || !payload.review?.date) return { ok: false, error: '请先选择有效账户和复盘日期。' };
        payload = { review: payload.review, context: buildReviewContext(data.data, payload.review) };
      }
      return await ai.dispatch(method, payload);
    } catch { return { ok: false, error: 'AI 请求处理失败，原始记录未更改。' }; }
  });
  ipcMain.handle('atrade:save-state', (event, state) => {
    if (!trusted(event)) return { ok: false, error: '此页面无权保存本地数据。' };
    try {
      const result = store.save(state);
      bootstrap.state = structuredClone(state);
      lastStateSave = { state: 'saved', savedAt: result.savedAt };
      return { ok: true, ...result };
    } catch {
      lastStateSave = { ...lastStateSave, state: 'error' };
      return { ok: false, error: '本地保存失败，请检查数据目录权限、可用磁盘空间或数据格式，然后重试。' };
    }
  });
  ipcMain.handle('atrade:diagnostics', async (event, copy = false) => {
    if (!trusted(event)) return { ok: false, error: '此页面无权读取诊断信息。' };
    try {
      const result = await marketBusiness.request('market.status');
      const source = result.ok ? result.data : null, online = source?.online;
      const timestamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : null;
      // Export only known status fields. Never include paths, account data, endpoint URLs or keys.
      const data = {
        app: { version: require('../package.json').version, platform: process.platform, arch: process.arch, electron: process.versions.electron, node: process.versions.node },
        checkedAt: new Date().toISOString(),
        services: { account: business.child?.exitCode === null, market: result.ok === true, research: researchBusiness.child?.exitCode === null },
        storage: { ...lastStateSave, recoveryWarning: Boolean(initial.warning) },
        local: { configured: source ? Boolean(source.configured) : null, available: source ? source.available !== false : null, lastScanAt: timestamp(source?.lastScannedAt) },
        online: { enabled: online ? Boolean(online.enabled) : null, connected: online ? Boolean(online.connected) : null, available: online ? Boolean(online.available) : null, lastCheckedAt: timestamp(online?.lastCheckedAt), followLocal: online ? Boolean(online.followLocal) : null },
        ai: Object.fromEntries(['text','vision'].map(target => { const p=ai.publicConfig().profiles[target]; return [target,{enabled:p.enabled,configured:Boolean(p.baseUrl&&p.model&&p.hasKey)}]; })),
      };
      if (copy === true) clipboard.writeText(JSON.stringify(data, null, 2));
      return { ok: true, data };
    } catch { return { ok: false, error: '诊断信息暂时无法读取，请稍后重试。' }; }
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
  win.on('close', async event => {
    if (allowClose) return;
    event.preventDefault();
    if (closing) return;
    closing = true;
    let preflightFailed = false;
    const preflight = async code => {
      let timer;
      try { return await Promise.race([win.webContents.executeJavaScript(code),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Close preflight timed out')),8000);})]); }
      finally { clearTimeout(timer); }
    };
    try {
      const aiDraft = await preflight('Boolean(window.AT?.aiSettings?.hasUnsaved())');
      if (aiDraft) {
        const { response } = await dialog.showMessageBox(win, {
          type: 'question', title: 'AI 配置尚未保存', message: 'AI 模型设置中有未保存的修改。',
          detail: '保存后再退出，或返回设置继续编辑。临时密钥不会写入普通工作区备份。',
          buttons: ['返回编辑', '保存配置并退出', '放弃 AI 修改并退出'], defaultId: 0, cancelId: 0, noLink: true,
        });
        if (response === 0) { await preflight('AT.navigate("C07")'); closing = false; return; }
        const resolved = await preflight(response === 1 ? 'AT.aiSettings.saveAll()' : 'AT.aiSettings.discardAll()');
        if (!resolved) { await preflight('AT.navigate("C07")'); closing = false; return; }
      }
    } catch { preflightFailed = true; }
    win.setEnabled(false);
    // The renderer already updates note state on each input. Await its last write,
    // including pending debounced edits, before destroying the renderer process.
    let timeout;
    const deadline = new Promise(resolve => { timeout = setTimeout(() => resolve(false), 8000); });
    const save = preflightFailed ? Promise.resolve(false) : win.webContents.executeJavaScript(
      '(async () => { if (typeof AT === "undefined") return false; if (window.ResearchModule?.flush && await window.ResearchModule.flush() === false) return false; if (AT.analysisTools?.flush && await AT.analysisTools.flush() === false) return false; if (AT.marketUI?.flush && await AT.marketUI.flush() === false) return false; if (AT.watchlistBackup?.flush && await AT.watchlistBackup.flush() === false) return false; if (AT.marketWatch?.flush && await AT.marketWatch.flush() === false) return false; if (AT.aiSettings?.flush && await AT.aiSettings.flush() === false) return false; if (AT.accountModule?.flush && await AT.accountModule.flush() === false) return false; return typeof AT.persist === "function" ? await AT.persist() : false; })()',
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
    ipcMain.removeHandler('atrade:diagnostics');
    ipcMain.removeHandler('atrade:open-data-folder');
    ipcMain.removeHandler('atrade:get-window-state');
    ipcMain.removeHandler('atrade:window-action');
    ipcMain.removeHandler('atrade:business');
    ipcMain.removeHandler('atrade:market');
    ipcMain.removeHandler('atrade:open-external');
    ipcMain.removeHandler('atrade:choose-tdx-directory');
    ipcMain.removeHandler('atrade:ai');
    ai.close();
    marketBusiness.close();
    researchBusiness.close();
    business.close();
    protocol.unhandle('atrade');
  });
  onWindow?.(win);
  await win.loadURL(APP_URL);
  if (show) win.show();
  return win;
}

module.exports = { createDesktop, isAppDocument };
