const { app, dialog, protocol } = require('electron');
const path = require('node:path');

app.setName('A-trade');
const customData = process.env.ATRADE_USER_DATA;
if (customData && !path.isAbsolute(customData)) throw new Error('ATRADE_USER_DATA 必须是完整路径。');
app.setPath('userData', customData || path.join(app.getPath('appData'), 'A-trade'));
protocol.registerSchemesAsPrivileged([
  { scheme: 'atrade', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

let mainWindow;
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  app.whenReady().then(async () => {
    app.setAppUserModelId('local.atrade.desktop');
    const { createDesktop } = require('./runtime.cjs');
    mainWindow = await createDesktop({
      dataDirectory: path.join(app.getPath('userData'), 'workspace'),
      show: !process.argv.includes('--start-hidden'),
    });
  }).catch(error => {
    dialog.showErrorBox('A-trade 启动失败',
      (error.businessReason || '无法安全读取本地工作区。原数据已保留，请检查文件格式、目录权限或磁盘空间。') + '\n' +
      `数据位置：${path.join(app.getPath('userData'), 'workspace')}`);
    app.exit(1);
  });
  app.on('window-all-closed', () => app.quit());
}
