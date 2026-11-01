const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { launch } = require('./smoke-portable.cjs');

async function main() {
  const baseline = process.argv.includes('--baseline');
  const root = path.resolve(__dirname, '..');
  const build = JSON.parse(await fs.readFile(path.join(root, 'dist/latest-single-exe.json'), 'utf8'));
  const application = process.argv.slice(2).find(value => value !== '--baseline') || build.file;
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'atrade-startup-'));
  const cacheDirectory = path.join(directory, '运行缓存');
  const timings = [];
  let session;
  try {
    let cached;
    for (const phase of ['cold', 'warm']) {
      session = await launch(application, directory, { cacheDirectory });
      timings.push({ phase, milliseconds: session.startupMs });
      if (!baseline) {
        const names = (await fs.readdir(cacheDirectory)).filter(name => name === build.cacheKey);
        assert.equal(names.length, 1, 'one complete runtime per build');
        const file = path.join(cacheDirectory, names[0], 'resources/app/desktop/main.cjs');
        const stat = await fs.stat(file);
        const marker = await fs.readFile(path.join(cacheDirectory, names[0], '.atrade-runtime-ready'), 'utf8');
        assert.equal(marker, build.cacheKey);
        if (cached) assert.deepEqual({ ino: stat.ino, mtimeMs: stat.mtimeMs }, cached, 'warm launch reuses extracted files');
        cached = { ino: stat.ino, mtimeMs: stat.mtimeMs };
        if (phase === 'warm') await session.duplicate();
      }
      await session.close(); session = null;
      console.log(`${phase}: ${timings.at(-1).milliseconds} ms`);
    }
    if (!baseline) {
      // A missing entrypoint must cause a fresh, staged extraction, not a broken launch.
      const cachedRoot = path.join(cacheDirectory, build.cacheKey);
      await fs.unlink(path.join(cachedRoot, 'resources/app/desktop/main.cjs'));
      const staging = cachedRoot + '.staging';
      await fs.mkdir(staging, { recursive: true });
      await fs.writeFile(path.join(staging, 'interrupted-extraction'), 'partial');
      session = await launch(application, directory, { cacheDirectory });
      timings.push({ phase: 'repair', milliseconds: session.startupMs });
      assert.equal(await fs.readFile(path.join(cachedRoot, '.atrade-runtime-ready'), 'utf8'), build.cacheKey);
      await assert.rejects(fs.stat(staging), { code: 'ENOENT' });
      await session.close(); session = null;
      // Different workspaces can start together and share the same immutable runtime.
      const concurrentCache = path.join(directory, '并发首次准备缓存');
      const [first, second] = await Promise.allSettled([
        launch(application, path.join(directory, '同时启动一'), { cacheDirectory: concurrentCache }),
        launch(application, path.join(directory, '同时启动二'), { cacheDirectory: concurrentCache }),
      ]);
      const sessions = [first, second].filter(result => result.status === 'fulfilled').map(result => result.value);
      try { assert.equal(sessions.length, 2, 'concurrent launches both reach their own workspace'); }
      finally { await Promise.all(sessions.map(item => item.close())); }
    }
    const evidence = { passed: true, baseline, application, testedAt: new Date().toISOString(), directory, timings,
      cacheReuse: !baseline, missingFileRecovery: !baseline, interruptedStagingRecovery: !baseline, concurrentLaunch: !baseline };
    await fs.mkdir(path.join(root, 'artifacts'), { recursive: true });
    await fs.writeFile(path.join(root, `artifacts/startup-${baseline ? 'baseline' : 'optimized'}.json`), JSON.stringify(evidence, null, 2) + '\n');
    console.log('PASS startup timing' + (baseline ? '' : ', cache reuse, duplicate launch, recovery and concurrency'));
  } finally { session?.kill(); }
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
