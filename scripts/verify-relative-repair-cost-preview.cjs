// Read-only browser QA of the rendered cost comparison and Sources fragments.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const family = path.join(root, 'artifacts/strategy-search/relative-repair-cost-visuals-v1');
const render = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => {
  if (index % 2 === 0) pairs.push([value, all[index + 1]]);
  return pairs;
}, []));
if (process.argv.slice(2).length % 2 || Object.keys(args).some(k => !['--chart-sha', '--sources-sha', '--sources-file', '--attempt', '--title', '--x-labels'].includes(k)))
  throw new Error('Expected --chart-sha SHA --sources-sha SHA [--attempt 0001] [--title TITLE] [--x-labels comma,list]');
for (const key of ['--chart-sha', '--sources-sha'])
  if (!/^[a-f0-9]{64}$/.test(args[key] || '')) throw new Error('Missing frozen fragment hash: ' + key);
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw new Error('Attempt must be four digits');
const out = path.join(family, 'browser-qa', 'attempt-' + attempt);
const sourceName = args['--sources-file'] || 'relative-repair-cost-sources-v1.html';
if (!/^relative-repair-cost-sources-v\d+\.html$/.test(sourceName)) throw new Error('Invalid Sources fragment filename');
const fragments = {
  comparison: { file: path.join(family, 'inline/relative-repair-cost-comparison-v1.html'), sha256: args['--chart-sha'] },
  sources: { file: path.join(family, 'inline', sourceName), sha256: args['--sources-sha'] },
};
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const requireTrue = (test, message) => { if (!test) throw new Error(message); };
const wait = page => page.waitForTimeout(250);

async function geometry(locator) {
  return locator.evaluate(element => {
    const box = element.getBoundingClientRect();
    return { width: innerWidth, left: box.left, right: box.right, contentWidth: element.scrollWidth,
      documentWidth: document.documentElement.scrollWidth };
  });
}
function checkGeometry(value, label) {
  requireTrue(value.left >= -1 && value.right <= value.width + 1 &&
    value.contentWidth <= value.width + 1 && value.documentWidth <= value.width + 1,
  'Horizontal overflow: ' + JSON.stringify({ label, ...value }));
}

(async () => {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.mkdirSync(out); // Existing attempts are never reused or replaced.
  const scriptHash = sha(__filename);
  for (const [name, item] of Object.entries(fragments)) {
    requireTrue(sha(item.file) === item.sha256, 'Fragment hash mismatch: ' + name);
    requireTrue(fs.statSync(item.file).size < 1024 * 1024, 'Fragment exceeds 1 MiB');
    const result = spawnSync(python, ['-X', 'utf8', '-B', render, item.file, path.join(out, name + '-preview.html')],
      { encoding: 'utf8', timeout: 30000 });
    save(name + '-wrapper-log.json', { status: result.status, stdout: result.stdout, stderr: result.stderr });
    requireTrue(result.status === 0, 'Standalone wrapper failed: ' + name);
  }
  const errors = [], chartViews = [], sourceViews = [], screenshots = [];
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  let legend;
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const capture = async (locator, name) => {
      const filename = path.join(out, name + '.png');
      await locator.screenshot({ path: filename });
      const record = { path: filename, sha256: sha(filename) };
      screenshots.push(record);
      return record;
    };
    await page.goto(pathToFileURL(path.join(out, 'comparison-preview.html')).href);
    let frame = page.frameLocator('iframe');
    const chart = frame.locator('.data-inline-react-root');
    await chart.waitFor({ timeout: 15000 });
    if (args['--title']) requireTrue((await chart.innerText()).includes(args['--title']), 'Expected chart title missing');
    for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await wait(page);
      const bounds = await geometry(chart);
      checkGeometry(bounds, `comparison ${width} ${colorScheme}`);
      const data = await chart.evaluate(element => ({
        bars: element.querySelectorAll('.recharts-bar-rectangle').length,
        visibleBars: [...element.querySelectorAll('.recharts-bar-rectangle')].filter(e => {
          const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0;
        }).length,
        xLabels: [...element.querySelectorAll('.recharts-cartesian-axis-tick-label text:not(.recharts-cartesian-axis-tick-value)')].map(e => {
          const box = e.getBoundingClientRect(); const copy = e.cloneNode(true);
          copy.querySelectorAll('title').forEach(t => t.remove());
          return { text: copy.textContent, left: box.left, right: box.right };
        }),
        text: element.innerText.slice(0, 1200),
      }));
      requireTrue(data.bars === 6 && data.visibleBars === 6, 'Expected six real nonzero marks: ' + JSON.stringify(data));
      if (args['--x-labels']) requireTrue(data.xLabels.map(r => r.text).join(',') === args['--x-labels'], 'Wrong x-axis labels');
      for (let i = 1; i < data.xLabels.length; i++)
        requireTrue(data.xLabels[i].left >= data.xLabels[i - 1].right + 4, 'X-axis labels overlap');
      chartViews.push({ width, colorScheme, ...bounds, ...data,
        screenshot: await capture(chart, `comparison-${width}-${colorScheme}`) });
    }
    const buttons = frame.getByRole('button', { name: /^Toggle / });
    requireTrue(await buttons.count() === 2, 'Expected two period legend controls');
    const button = buttons.first();
    const label = await button.getAttribute('aria-label');
    await button.click(); await wait(page);
    const hidden = await chart.locator('.recharts-bar-rectangle').count();
    await button.click(); await wait(page);
    const restored = await chart.locator('.recharts-bar-rectangle').count();
    requireTrue(hidden === 3 && restored === 6, 'Legend must hide/restore one three-bar period');
    legend = { label, hidden, restored, passed: true };

    for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await page.goto(pathToFileURL(path.join(out, 'sources-preview.html')).href);
      frame = page.frameLocator('iframe');
      const receipt = frame.locator('.sources-receipt');
      await receipt.waitFor({ timeout: 15000 });
      const disclosure = receipt.locator('.receipt-disclosure');
      await disclosure.click();
      requireTrue(await receipt.locator('.receipt-card').count() === 3, 'Expected three Sources cards');
      for (let i = 0; i < 3; i++) {
        const card = receipt.locator('.receipt-card').nth(i);
        const toggle = card.locator('.receipt-card-toggle');
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
        const title = await toggle.getAttribute('aria-label');
        const states = [];
        for (const tab of ['Overview', 'Data preview', 'Evidence flow']) {
          await card.getByRole('tab', { name: tab, exact: true }).click();
          await wait(page);
          const panel = card.getByRole('tabpanel');
          requireTrue((await panel.innerText()).trim().length > 0, 'Empty Sources panel: ' + title + '/' + tab);
          if (tab === 'Data preview')
            requireTrue(await panel.locator('table tbody tr').count() > 0, 'Empty recorded preview table: ' + title);
          if (tab === 'Evidence flow') {
            const verification = panel.getByText('Verification', { exact: true });
            await verification.click(); await wait(page);
            const expanded = await verification.evaluate(e => e.closest('details')?.open);
            requireTrue(expanded === true, 'Verification checkpoint failed to open: ' + title);
          }
          const bounds = await geometry(receipt);
          checkGeometry(bounds, `Sources ${i + 1} ${tab} ${width} ${colorScheme}`);
          states.push({ tab, ...bounds, screenshot: await capture(card,
            `sources-card${i + 1}-${width}-${colorScheme}-${tab.replaceAll(' ', '-').toLowerCase()}`) });
          if (tab === 'Evidence flow') {
            const verification = panel.getByText('Verification', { exact: true });
            await verification.click();
            requireTrue(await verification.evaluate(e => e.closest('details')?.open) === false,
              'Verification checkpoint failed to close');
          }
        }
        await toggle.click();
        requireTrue(await toggle.getAttribute('aria-expanded') === 'false', 'Source card did not close');
        sourceViews.push({ width, colorScheme, card: i + 1, title, states, opened: true, closed: true });
      }
      await disclosure.click();
      requireTrue(await disclosure.getAttribute('aria-expanded') === 'false', 'Sources disclosure did not close');
    }
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    for (const [name, item] of Object.entries(fragments))
      requireTrue(sha(item.file) === item.sha256, 'Fragment changed during QA: ' + name);
    requireTrue(sha(__filename) === scriptHash, 'QA source changed during attempt');
    const report = { schema: 'relative-repair-cost-preview-qa-v1', passed: true, createdAt: new Date().toISOString(),
      scope: 'Standalone sandboxed iframe preview only; not live host insertion.', scriptSha256: scriptHash,
      fragments, previewWrapperToolSha256: sha(render), chartViews, legend, sourceViews, screenshots,
      chartBars: 6, sourceCards: 3, errors, horizontalOverflow: false, sourceBytesUnchanged: true,
      screenshotsVisuallyInspected: false, actualMarketRuns: 0 };
    save('preview-verification.json', report);
    console.log(JSON.stringify({ passed: true, errors, chartBars: 6, sourceCards: 3, legend,
      report: path.join(out, 'preview-verification.json'), reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (fs.existsSync(out) && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error); process.exitCode = 1;
});
