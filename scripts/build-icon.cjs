// Rasterize the existing vector mark into Windows icon sizes; no image service.
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const root = path.resolve(__dirname, '..');
app.setPath('userData', path.join(os.tmpdir(), `atrade-icon-build-${process.pid}`));

app.whenReady().then(async () => {
  const svg = await fs.readFile(path.join(root, 'preview/assets/app-mark.svg'), 'utf8');
  const window = new BrowserWindow({ show: false, webPreferences: {
    nodeIntegration: false, contextIsolation: true, sandbox: true,
  } });
  await window.loadURL('about:blank');
  const sizes = [16,20,24,32,40,48,64,128,256];
  const data = await window.webContents.executeJavaScript(`(async () => {
    const image = new Image();
    image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(${JSON.stringify(svg)});
    await image.decode();
    return ${JSON.stringify(sizes)}.map(size => {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = size;
      canvas.getContext('2d').drawImage(image,0,0,size,size);
      return canvas.toDataURL('image/png').split(',')[1];
    });
  })()`);
  const images = data.map(value => Buffer.from(value,'base64'));
  const directory = Buffer.alloc(6 + sizes.length * 16);
  directory.writeUInt16LE(1,2);
  directory.writeUInt16LE(sizes.length,4);
  let offset = directory.length;
  sizes.forEach((size,index) => {
    const entry = 6 + index * 16;
    directory[entry] = directory[entry+1] = size === 256 ? 0 : size;
    directory.writeUInt16LE(1,entry+4);
    directory.writeUInt16LE(32,entry+6);
    directory.writeUInt32LE(images[index].length,entry+8);
    directory.writeUInt32LE(offset,entry+12);
    offset += images[index].length;
  });
  const output = path.join(root,'desktop/assets');
  await fs.mkdir(output,{recursive:true});
  await fs.writeFile(path.join(output,'app-icon.ico'),Buffer.concat([directory,...images]));
  await fs.writeFile(path.join(output,'app-icon.png'),images.at(-1));
  console.log('Generated A-trade Windows icon: 16–256 px');
  window.destroy();
  app.exit(0);
}).catch(error => { console.error(error.stack); app.exit(1); });
