const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');
const { pythonCommand } = require('../desktop/business-service.cjs');

const root = path.resolve(__dirname, '..');
const PYTHON_VERSION = '3.13.16';
// Published in python.org/ftp/python/3.13.16/windows-3.13.16.json.
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embeddable-amd64.zip`;
const PYTHON_SHA256 = '589dc1e9d02549ca5f680710307ff9b77f6e3ffc4aa3efccd7fa54eeeafac94b';
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
async function run(command, args, options = {}) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: root, windowsHide: true, stdio: 'inherit', ...options });
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error(`Build command exited with ${code}`)));
  });
}
async function copy(source, target) {
  await fs.cp(source, target, { recursive: true, errorOnExist: true, force: false,
    filter: file => !path.relative(source,file).split(path.sep).some(part => ['__pycache__','node_modules','.git','.venv','.pytest_cache'].includes(part)||/^\.env(?:\.|$)/.test(part)) && !/\.(?:pyc|log|sqlite3?|db)$/.test(file) });
}
async function fingerprint(directory, relative = '') {
  const output = {};
  for (const entry of await fs.readdir(path.join(directory, relative), { withFileTypes: true })) {
    const item = path.join(relative, entry.name);
    if (entry.isDirectory()) Object.assign(output, await fingerprint(directory, item));
    else output[item.replaceAll('\\', '/')] = sha256(await fs.readFile(path.join(directory, item)));
  }
  return output;
}
async function main() {
  if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('此构建脚本需要 Windows x64。');
  const buildPython = pythonCommand();
  const pythonMinor = execFileSync(buildPython, ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], { encoding: 'utf8', windowsHide: true }).trim();
  if (pythonMinor !== '3.13') throw new Error('构建依赖需要 Python 3.13，避免安装错误版本的二进制扩展。');
  const pkg = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
  await fs.access(path.join(root, 'preview/finaltrade/.vite/manifest.json'));
  await fs.access(path.join(root, 'preview/finaltrade/THIRD-PARTY-NOTICES.txt'));
  const dist = path.join(root, 'dist');
  await fs.mkdir(dist, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '').replace('T', '-');
  const output = path.join(dist, `A-trade-${pkg.version}-win32-x64-portable-${stamp}`);
  // Never replace an existing release or its data.
  await fs.mkdir(output);
  console.log('Copying the pinned Electron runtime and application allowlist...');
  const electronRoot = path.join(root, 'node_modules/electron/dist');
  const electronVersion = (await fs.readFile(path.join(electronRoot, 'version'), 'utf8')).trim().replace(/^v/, '');
  const expectedElectron = require('../node_modules/electron/package.json').version;
  if (electronVersion !== expectedElectron) throw new Error('Electron runtime does not match the dependency lock.');
  for (const entry of await fs.readdir(electronRoot)) await copy(path.join(electronRoot, entry), path.join(output, entry));
  await fs.unlink(path.join(output, 'resources/default_app.asar')).catch(error => { if(error.code!=='ENOENT')throw error; });
  await fs.rename(path.join(output, 'electron.exe'), path.join(output, 'A-trade.exe'));
  const appRoot = path.join(output, 'resources/app');
  await fs.mkdir(appRoot);
  for (const folder of ['desktop', 'preview', 'service', 'licenses', 'vendor']) await copy(path.join(root, folder), path.join(appRoot, folder));
  await fs.copyFile(path.join(root, 'requirements-portable.lock.txt'), path.join(appRoot, 'requirements-portable.lock.txt'));
  await fs.writeFile(path.join(appRoot, 'package.json'), JSON.stringify({ name: pkg.name, productName: pkg.productName,
    version: pkg.version, description: pkg.description, main: pkg.main, private: true }, null, 2) + '\n');
  const { resedit } = await import('@electron/packager/resedit');
  await resedit(path.join(output, 'A-trade.exe'), { iconPath: path.join(root, 'desktop/assets/app-icon.ico'),
    productVersion: pkg.version, fileVersion: pkg.version, productName: pkg.productName,
    win32Metadata: { CompanyName: 'A-trade', FileDescription: 'A-trade 股票研究与回测', ProductName: pkg.productName,
      OriginalFilename: 'A-trade.exe', InternalName: 'A-trade' } });
  const cache = path.join(dist, 'runtime-cache');
  await fs.mkdir(cache, { recursive: true });
  const archive = path.join(cache, `python-${PYTHON_VERSION}-embeddable-amd64.zip`);
  let archiveBytes;
  try { archiveBytes = await fs.readFile(archive); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!archiveBytes) {
    console.log('Downloading the official embedded Python runtime...');
    const temporaryArchive = archive + '.' + crypto.randomUUID() + '.tmp';
    try {
      await run(path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'),
        ['-NoProfile', '-NonInteractive', '-Command', "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:ATRADE_BUILD_URL -OutFile $env:ATRADE_BUILD_ARCHIVE -TimeoutSec 180"],
        { env: { ...process.env, ATRADE_BUILD_URL: PYTHON_URL, ATRADE_BUILD_ARCHIVE: temporaryArchive } });
      archiveBytes = await fs.readFile(temporaryArchive);
      if (sha256(archiveBytes) !== PYTHON_SHA256) throw new Error('Python archive checksum mismatch.');
      await fs.writeFile(archive, archiveBytes);
    } finally { await fs.unlink(temporaryArchive).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
  }
  if (sha256(archiveBytes) !== PYTHON_SHA256) throw new Error('Cached Python archive checksum mismatch.');
  const runtime = path.join(output, 'resources/python');
  await run(buildPython, ['-c', 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', archive, runtime]);
  await fs.writeFile(path.join(runtime, 'python313._pth'), 'python313.zip\n.\nLib/site-packages\nLib/site-packages/win32\nLib/site-packages/win32/lib\nLib/site-packages/Pythonwin\n../app\nimport site\n');
  console.log('Installing the locked account/research/market dependencies...');
  await run(buildPython, ['-m', 'pip', 'install', '--disable-pip-version-check', '--ignore-installed', '--only-binary=:all:', '--no-compile',
    '--target', path.join(runtime, 'Lib/site-packages'), '--requirement', path.join(root, 'requirements-portable.lock.txt')]);
  let revision = null, sourceDirty = null;
  try {
    revision = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8', windowsHide: true }).trim();
    sourceDirty = Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8', windowsHide: true }).trim());
  } catch { /* source archives may not have a git checkout */ }
  const metadata = { schema: 'atrade-portable-build-v1', version: pkg.version, builtAt: new Date().toISOString(),
    platform: 'win32', arch: 'x64', electronVersion, pythonVersion: PYTHON_VERSION, pythonUrl: PYTHON_URL,
    pythonArchiveSha256: PYTHON_SHA256, sourceRevision: revision, sourceDirty, bundledRuntime: true,
    dataLocation: '%APPDATA%/A-trade/workspace' };
  await fs.writeFile(path.join(appRoot, 'portable-build.json'), JSON.stringify(metadata, null, 2) + '\n');
  console.log('Verifying bundled imports without the host Python environment...');
  const validationEnv = { ...process.env, PYTHONHOME: 'Z:\\unused-python', PYTHONPATH: 'Z:\\unused-site', PYTHONNOUSERSITE: '1' };
  await run(path.join(runtime, 'python.exe'), ['-B', '-c',
    'import sys,sqlite3,ssl,mcp,numpy,pandas,win32api,fastapi,uvicorn; from service.portfolio_engine import PortfolioEngine; from service.research_service import ResearchService; from service.finaltrade_runtime import verify_source; verify_source(); assert sys.flags.isolated; print("PASS bundled Python, SQLite, SSL, MCP, numpy, pandas, win32, FastAPI and fixed reference source")'],
    { cwd: output, env: validationEnv });
  await fs.copyFile(path.join(root, 'docs/portable-release.md'), path.join(output, '使用说明.md'));
  await fs.writeFile(path.join(output, 'build-info.json'), JSON.stringify({ ...metadata,
    applicationFiles: await fingerprint(appRoot), executableSha256: sha256(await fs.readFile(path.join(output, 'A-trade.exe'))) }, null, 2) + '\n');
  await fs.writeFile(path.join(dist, 'latest-portable.json'), JSON.stringify({ directory: output, version: pkg.version }, null, 2) + '\n');
  console.log(`PORTABLE_DIRECTORY=${output}`);
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
