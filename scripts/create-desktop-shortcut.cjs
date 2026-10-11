const { app, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const root = path.resolve(__dirname, '..');
app.setPath('userData', path.join(os.tmpdir(), `atrade-shortcut-${process.pid}`));

app.whenReady().then(() => {
  if (process.platform !== 'win32') throw new Error('This shortcut is for Windows.');
  const shortcut = path.join(app.getPath('desktop'), 'A-trade.lnk');
  const icon = path.join(root, 'desktop/assets/app-icon.ico');
  const options = {
    target: process.execPath,
    args: `"${root}"`,
    cwd: root,
    description: 'A-trade 交易研究与复盘工作台',
    icon,
    iconIndex: 0,
    appUserModelId: 'local.atrade.desktop',
  };
  if (!fs.existsSync(icon)) throw new Error('Build the application icon first.');
  const exists = fs.existsSync(shortcut);
  if (exists) {
    const current = shell.readShortcutLink(shortcut);
    if (current.target.toLowerCase() !== options.target.toLowerCase() || current.args !== options.args) {
      throw new Error(`A different application already uses ${shortcut}`);
    }
  }
  if (!shell.writeShortcutLink(shortcut, exists ? 'update' : 'create', options)) {
    throw new Error('Windows could not write the desktop shortcut.');
  }
  const saved = shell.readShortcutLink(shortcut);
  for (const key of ['target', 'args', 'cwd', 'icon', 'iconIndex', 'appUserModelId']) {
    if (saved[key] !== options[key]) throw new Error(`Shortcut verification failed: ${key}`);
  }
  console.log(`Desktop shortcut verified: ${shortcut}`);
  console.log(`Icon: ${saved.icon}`);
  app.exit(0);
}).catch(error => { console.error(error.stack); app.exit(1); });
