const { contextBridge, ipcRenderer } = require('electron');

// One bounded startup read lets the existing deferred scripts load in order.
// File writes remain asynchronous. Never expose ipcRenderer or arbitrary paths.
const bootstrap = ipcRenderer.sendSync('atrade:bootstrap');
const windowActions = new Set(['minimize', 'toggle-maximize', 'close']);
const publicWindowState = state => state && typeof state.maximized === 'boolean' &&
  typeof state.fullscreen === 'boolean' && Number.isSafeInteger(state.revision)
  ? { maximized: state.maximized, fullscreen: state.fullscreen, revision: state.revision }
  : null;
contextBridge.exposeInMainWorld('atradeDesktop', {
  bootstrap,
  startReference: () => ipcRenderer.invoke('atrade:reference-start'),
  startResearch: () => ipcRenderer.invoke('atrade:reference-start'),
  researchRequest: request => ipcRenderer.invoke('atrade:research-request', request),
  business: (method, payload = {}) => ipcRenderer.invoke('atrade:business', method, payload),
  market: (method, payload = {}) => ipcRenderer.invoke('atrade:market', method, payload),
  openExternal: url => ipcRenderer.invoke('atrade:open-external', url),
  chooseTDXDirectory: () => ipcRenderer.invoke('atrade:choose-tdx-directory'),
  importResearchHistory: () => ipcRenderer.invoke('atrade:import-research-history'),
  exportResearchReport: id => ipcRenderer.invoke('atrade:export-research-report', id),
  ai: (method, payload = {}) => ipcRenderer.invoke('atrade:ai', method, payload),
  saveState: state => ipcRenderer.invoke('atrade:save-state', state),
  diagnostics: (copy = false) => ipcRenderer.invoke('atrade:diagnostics', copy === true),
  openDataFolder: () => ipcRenderer.invoke('atrade:open-data-folder'),
  windowAction: action => windowActions.has(action)
    ? ipcRenderer.invoke('atrade:window-action', action)
    : Promise.resolve({ ok: false }),
  getWindowState: async () => publicWindowState(await ipcRenderer.invoke('atrade:get-window-state')),
  onWindowState: listener => {
    if (typeof listener !== 'function') throw new TypeError('Window state listener must be a function');
    const receive = (_event, state) => {
      const value = publicWindowState(state);
      if (value) listener(value);
    };
    ipcRenderer.on('atrade:window-state', receive);
    return () => ipcRenderer.removeListener('atrade:window-state', receive);
  },
});
