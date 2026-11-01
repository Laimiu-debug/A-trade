const { BrowserWindow } = require('electron');
const path = require('node:path');

function createAppWindow() {
  const win = new BrowserWindow({
    width: 1480, height: 1000, minWidth: 820, minHeight: 620,
    show: false, title: 'A-trade', icon: path.join(__dirname, 'assets/app-icon.ico'),
    frame: false, backgroundColor: '#f4f7fb', autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true, webviewTag: false,
    },
  });
  win.removeMenu();
  return win;
}
module.exports = { createAppWindow };
