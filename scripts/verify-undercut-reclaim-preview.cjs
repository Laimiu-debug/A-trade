// Sources-only standalone sandboxed-iframe QA. Does not modify analysis data.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const family = path.join(__dirname, '../artifacts/strategy-search/strong-undercut-reclaim-timing-v1');
const render = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const argv = process.argv.slice(2);
if (argv.length % 2) throw new Error('Expected --sources-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({ length: argv.length / 2 }, (_, i) => [argv[2 * i], argv[2 * i + 1]]));
if (Object.keys(args).some(k => !['--sources-sha', '--attempt'].includes(k))) throw new Error('Unknown argument');
if (!/^[a-f0-9]{64}$/.test(args['--sources-sha'] || '')) throw new Error('Missing frozen Sources SHA256');
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw new Error('Attempt must be four digits');
const out = path.join(family, 'sources-browser-qa', 'attempt-' + attempt);
const fragment = { file: path.join(family, 'inline/undercut-reclaim-sources.html'), sha256: args['--sources-sha'] };
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const requireTrue = (condition, message) => { if (!condition) throw new Error(message); };

async function geometry(locator) {
  return locator.evaluate(element => {
    const box = element.getBoundingClientRect();
    return { width: innerWidth, left: box.left, right: box.right, contentWidth: element.scrollWidth,
      documentWidth: document.documentElement.scrollWidth };
  });
}
function checkGeometry(bounds, label) {
  requireTrue(bounds.left >= -1 && bounds.right <= bounds.width + 1 &&
    bounds.contentWidth <= bounds.width + 1 && bounds.documentWidth <= bounds.width + 1,
    'Horizontal overflow: ' + JSON.stringify({ label, ...bounds }));
}

(async () => {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.mkdirSync(out); // Preserve prior attempts; never overwrite an existing directory.
  const scriptSha256 = sha(__filename);
  requireTrue(sha(fragment.file) === fragment.sha256, 'Fragment hash mismatch before QA');
  requireTrue(fs.statSync(fragment.file).size < 1024 * 1024, 'Fragment exceeds 1 MiB');
  const wrapped = spawnSync(python, ['-X', 'utf8', '-B', render, fragment.file, path.join(out, 'sources-preview.html')],
    { encoding: 'utf8', timeout: 30000 });
  save('wrapper-log.json', { status: wrapped.status, stdout: wrapped.stdout, stderr: wrapped.stderr });
  requireTrue(wrapped.status === 0, 'Standalone Sources wrapper failed');
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], views = [], screenshots = [];
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const capture = async (locator, name) => {
      const filename = path.join(out, name + '.png');
      await locator.screenshot({ path: filename });
      const record = { path: filename, sha256: sha(filename) };
      screenshots.push(record);
      return record;
    };
    for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await page.goto(pathToFileURL(path.join(out, 'sources-preview.html')).href);
      const frame = page.frameLocator('iframe');
      const receipt = frame.locator('.sources-receipt');
      await receipt.waitFor({ timeout: 15000 });
      const disclosure = receipt.locator('.receipt-disclosure');
      await disclosure.click();
      requireTrue(await disclosure.getAttribute('aria-expanded') === 'true', 'Sources disclosure did not open');
      requireTrue(await receipt.locator('.receipt-card').count() === 2, 'Expected two Sources finding cards');
      for (let i = 0; i < 2; i++) {
        const card = receipt.locator('.receipt-card').nth(i);
        const toggle = card.locator('.receipt-card-toggle');
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
        requireTrue(await toggle.getAttribute('aria-expanded') === 'true', 'Finding card did not open');
        const title = await toggle.getAttribute('aria-label');
        const states = [];
        for (const tab of ['Overview', 'Data preview', 'Evidence flow']) {
          const button = card.getByRole('tab', { name: tab, exact: true });
          await button.click();
          requireTrue(await button.getAttribute('aria-selected') === 'true', 'Sources tab not selected: ' + tab);
          const panel = card.getByRole('tabpanel');
          await panel.waitFor({ state: 'visible' });
          const panelText = (await panel.innerText()).trim();
          requireTrue(panelText.length > 0, 'Empty panel: ' + title + '/' + tab);
          let previewRows = null, verificationExpanded = null;
          if (tab === 'Data preview') {
            previewRows = await panel.locator('table tbody tr').count();
            requireTrue(previewRows > 0, 'No recorded preview rows: ' + title);
            const tableRows = await panel.locator('table').evaluateAll(tables => tables.map(t => t.querySelectorAll('tbody tr').length));
            requireTrue(JSON.stringify(tableRows) === JSON.stringify(i === 0 ? [4, 6, 6] : [12, 24, 48, 6]),
              'Complete preview row counts differ: ' + JSON.stringify(tableRows));
            if (i === 1 && width === 780 && colorScheme === 'light') {
              await panel.locator('table').nth(2).locator('tbody tr').last().scrollIntoViewIfNeeded();
              await capture(card, 'phase-last-rows-780-light');
              save('phase-preview-check.json', { passed: true, tableRows, screenshot: 'phase-last-rows-780-light.png',
                scope: 'All twelve year, twenty-four halfyear, forty-eight phase and six coverage rows rendered; final phase rows scrolled into view.' });
            }
          }
          if (tab === 'Evidence flow') {
            const verification = panel.getByText('Verification', { exact: true });
            await verification.click();
            verificationExpanded = await verification.evaluate(e => e.closest('details')?.open);
            requireTrue(verificationExpanded === true, 'Verification did not open');
          }
          const bounds = await geometry(receipt);
          checkGeometry(bounds, `${width} ${colorScheme} card${i + 1} ${tab}`);
          states.push({ tab, previewRows, verificationExpanded, ...bounds,
            screenshot: await capture(card, `card${i + 1}-${width}-${colorScheme}-${tab.replaceAll(' ', '-').toLowerCase()}`) });
          if (tab === 'Evidence flow') {
            const verification = panel.getByText('Verification', { exact: true });
            await verification.click();
            requireTrue(await verification.evaluate(e => e.closest('details')?.open) === false, 'Verification did not close');
          }
        }
        await toggle.click();
        requireTrue(await toggle.getAttribute('aria-expanded') === 'false', 'Finding card did not close');
        views.push({ width, colorScheme, card: i + 1, title, states, opened: true, closed: true });
      }
      await disclosure.click();
      requireTrue(await disclosure.getAttribute('aria-expanded') === 'false', 'Sources disclosure did not close');
      console.log(JSON.stringify({ viewport: width, colorScheme, sourceCards: 2, passed: true }));
    }
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    requireTrue(sha(fragment.file) === fragment.sha256, 'Fragment changed during QA');
    requireTrue(sha(__filename) === scriptSha256, 'QA script changed during execution');
    const report = { schema: 'strong-undercut-reclaim-timing-sources-qa-v1', passed: true, createdAt: new Date().toISOString(),
      scope: 'Standalone sandboxed iframe preview only; not live host insertion.', fragment, scriptSha256,
      previewWrapperToolSha256: sha(render), views, screenshots, sourceCards: 2, errors,
      horizontalOverflow: false, sourceBytesUnchanged: true, screenshotsVisuallyInspected: false,
      actualMarketRuns: 0, analysisArtifactsModified: false };
    save('preview-verification.json', report);
    console.log(JSON.stringify({ passed: true, sourceCards: 2, screenshots: screenshots.length,
      report: path.join(out, 'preview-verification.json'), reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (fs.existsSync(out) && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error);
  process.exitCode = 1;
});


