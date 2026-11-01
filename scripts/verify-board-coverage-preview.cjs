// Scoped standalone-iframe QA for the six-account board comparison. No analysis reruns.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const family = path.resolve(__dirname, '../artifacts/strategy-search/board-coverage-reclaim-v1');
const wrapper = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const argv = process.argv.slice(2);
if (argv.length % 2) throw new Error('Expected --chart-sha SHA --sources-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({ length: argv.length / 2 }, (_, i) => [argv[2 * i], argv[2 * i + 1]]));
if (Object.keys(args).some(k => !['--chart-sha', '--sources-sha', '--attempt'].includes(k))) throw new Error('Unknown argument');
for (const key of ['--chart-sha', '--sources-sha'])
  if (!/^[a-f0-9]{64}$/.test(args[key] || '')) throw new Error('Missing frozen fragment SHA256: ' + key);
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw new Error('Attempt must be four digits');
const out = path.join(family, 'browser-qa', 'attempt-' + attempt);
const fragments = {
  chart: { path: 'inline/board-coverage-returns.html', sha256: args['--chart-sha'] },
  sources: { path: 'inline/board-coverage-sources.html', sha256: args['--sources-sha'] },
};
const expectedRows = [[6, 6, 6], [12, 24, 48, 6]];
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const requireTrue = (condition, message) => { if (!condition) throw new Error(message); };
let attemptCreated = false;

async function geometry(locator) {
  const bounds = await locator.evaluate(element => {
    const box = element.getBoundingClientRect();
    return { width: innerWidth, left: box.left, right: box.right, contentWidth: element.scrollWidth,
      documentWidth: document.documentElement.scrollWidth };
  });
  requireTrue(bounds.left >= -1 && bounds.right <= bounds.width + 1 &&
    bounds.contentWidth <= bounds.width + 1 && bounds.documentWidth <= bounds.width + 1,
    'Horizontal overflow: ' + JSON.stringify(bounds));
  return bounds;
}

(async () => {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.mkdirSync(out); // Exclusive attempt: failures and previous attempts are retained.
  attemptCreated = true;
  const scriptSha256 = sha(__filename);
  const renderReceiptPath = path.join(family, 'render-receipt.json');
  const chartPath = path.join(family, 'chart.json'), sourcesPath = path.join(family, 'sources.json');
  const renderReceiptSha256 = sha(renderReceiptPath), chartPayloadSha256 = sha(chartPath), sourcesPayloadSha256 = sha(sourcesPath);
  const receipt = read(renderReceiptPath), chartPayload = read(chartPath), sources = read(sourcesPath);
  requireTrue(receipt.chartSha256 === fragments.chart.sha256 && receipt.sourceSha256 === fragments.sources.sha256,
    'Render fragment bindings differ');
  requireTrue(receipt.chartPayloadSha256 === chartPayloadSha256 && receipt.payloadSha256 === sourcesPayloadSha256,
    'Render payload bindings differ');
  const originalTableRows = sources.items.map(item => item.queries.map(query => query.rows.length));
  requireTrue(JSON.stringify(originalTableRows) === JSON.stringify(expectedRows) &&
    JSON.stringify(receipt.tableRows) === JSON.stringify(expectedRows), 'Seven complete source tables required');
  requireTrue(chartPayload.rows.length === 6, 'Six chart accounts required');
  for (const [kind, fragment] of Object.entries(fragments)) {
    const file = path.join(family, fragment.path);
    requireTrue(sha(file) === fragment.sha256 && fs.statSync(file).size < 1_000_000, kind + ' hash/size mismatch');
    const result = spawnSync(python, ['-X', 'utf8', '-B', wrapper, file, path.join(out, kind + '-preview.html')],
      { encoding: 'utf8', timeout: 30000 });
    save(kind + '-wrapper-log.json', { status: result.status, stdout: result.stdout, stderr: result.stderr });
    requireTrue(result.status === 0, kind + ' standalone wrapper failed');
  }
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], images = [], chartViews = [], sourceViews = [], tableChecks = [], finalItems = [];
  let legendCheck;
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const capture = async (locator, name, wholePage = false) => {
      let targetBounds = null;
      if (wholePage) {
        await locator.scrollIntoViewIfNeeded();
        targetBounds = await locator.boundingBox();
        requireTrue(targetBounds && targetBounds.height > 0 && targetBounds.y < 900 && targetBounds.y + targetBounds.height > 0,
          'Scrolled target is outside viewport: ' + name);
      }
      const file = path.join(out, name + '.png');
      if (wholePage) await page.screenshot({ path: file, fullPage: false });
      else await locator.screenshot({ path: file });
      const result = { path: path.relative(family, file).replaceAll('\\', '/'), sha256: sha(file),
        capture: wholePage ? 'page-viewport' : 'chart-element', ...(targetBounds ? { targetBounds } : {}) };
      images.push(result);
      return result;
    };
    for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await page.goto(pathToFileURL(path.join(out, 'chart-preview.html')).href);
      const frame = page.frameLocator('iframe');
      const chart = frame.locator('.data-inline-react-root');
      await chart.waitFor({ timeout: 15000 });
      await chart.locator('.recharts-bar-rectangle').first().waitFor();
      await page.waitForTimeout(200);
      const bounds = await geometry(chart);
      const painted = await chart.evaluate(element => ({
        bars: element.querySelectorAll('.recharts-bar-rectangle').length,
        nonzeroBars: [...element.querySelectorAll('.recharts-bar-rectangle')].filter(e => {
          const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0;
        }).length,
        svgText: [...element.querySelectorAll('svg text')].map(e => e.textContent),
      }));
      requireTrue(painted.bars === 6 && painted.nonzeroBars === chartPayload.rows.filter(r => r[chartPayload.chart.y] !== 0).length,
        'Six genuine chart marks required: ' + JSON.stringify(painted));
      for (const label of ['主板', '创业', '科创'])
        requireTrue(painted.svgText.some(text => text.includes(label)), 'Chart category missing: ' + label);
      requireTrue(painted.svgText.some(text => text.includes('%')), 'Percent axis missing');
      const toggles = frame.getByRole('button', { name: /^Toggle / });
      requireTrue(await toggles.count() === 2, 'Two period legend controls required');
      for (const period of ['2022-2023', '2024-2025'])
        requireTrue(await toggles.filter({ hasText: period }).count() === 1, 'Period legend missing: ' + period);
      chartViews.push({ width, colorScheme, ...bounds, ...painted,
        image: await capture(chart, `chart-${width}-${colorScheme}`) });
      if (width === 780 && colorScheme === 'light') {
        await toggles.first().click(); await page.waitForTimeout(150);
        const hidden = await chart.locator('.recharts-bar-rectangle').count();
        await toggles.first().click(); await page.waitForTimeout(150);
        const restored = await chart.locator('.recharts-bar-rectangle').count();
        requireTrue(hidden === 3 && restored === 6, 'Period legend hide/restore failed');
        legendCheck = { passed: true, hidden, restored };
      }
    }
    // Sources is intentionally scoped to narrow-screen reachability, full row
    // identities and final phase/coverage/caveat items, not every runtime feature.
    await page.setViewportSize({ width: 360, height: 900 });
    await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
    await page.goto(pathToFileURL(path.join(out, 'sources-preview.html')).href);
    const frame = page.frameLocator('iframe'), sourcesRoot = frame.locator('.sources-receipt');
    await sourcesRoot.waitFor({ timeout: 15000 });
    const disclosure = sourcesRoot.locator('.receipt-disclosure');
    await disclosure.click();
    requireTrue(await disclosure.getAttribute('aria-expanded') === 'true', 'Sources disclosure did not open');
    requireTrue(await sourcesRoot.locator('.receipt-card').count() === 2, 'Two Sources cards required');
    for (let i = 0; i < 2; i++) {
      const card = sourcesRoot.locator('.receipt-card').nth(i), toggle = card.locator('.receipt-card-toggle');
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      for (const tab of ['Overview', 'Data preview', 'Evidence flow']) {
        const button = card.getByRole('tab', { name: tab, exact: true });
        await button.click();
        requireTrue(await button.getAttribute('aria-selected') === 'true', 'Sources tab failed: ' + tab);
        const panel = card.getByRole('tabpanel');
        requireTrue((await panel.innerText()).trim().length > 0, 'Empty Sources panel');
        if (tab === 'Overview') {
          const last = panel.locator('.receipt-qualification-list li').last();
          requireTrue(await last.count() === 1, 'Final caveat missing');
          finalItems.push({ card: i + 1, type: 'caveat', text: await last.innerText(),
            image: await capture(last, `sources-card${i + 1}-last-caveat-360-light`, true) });
        }
        if (tab === 'Data preview') {
          requireTrue(await panel.locator('table').count() === expectedRows[i].length, 'Source table count differs');
          for (let q = 0; q < sources.items[i].queries.length; q++) {
            const query = sources.items[i].queries[q], table = panel.locator('table').nth(q);
            const identityFields = ['period', 'boardLabel', ...(i === 1 ? ['intervalLabel'] : [])];
            const identityColumns = identityFields.map(field => query.columns.findIndex(column => column.field === field));
            requireTrue(identityColumns.every(index => index >= 0), 'Source identity column missing');
            const visible = await table.locator('tbody tr').evaluateAll((rows, columns) => rows.map(row =>
              columns.map(index => row.querySelectorAll('td')[index].innerText.trim())), identityColumns);
            const expected = query.rows.map(row => identityFields.map(field => String(row[field])));
            requireTrue(JSON.stringify(visible) === JSON.stringify(expected), 'Complete source identities/order differ: ' + query.id);
            tableChecks.push({ card: i + 1, queryId: query.id, rows: visible.length, allIdentitiesMatched: true });
            if (q === 2) {
              const last = table.locator('tbody tr').last(), kind = i === 0 ? 'coverage' : 'phase';
              finalItems.push({ card: i + 1, type: kind, text: await last.innerText(),
                image: await capture(last, `sources-${kind}-last-row-360-light`, true) });
            }
          }
        }
        if (tab === 'Evidence flow') {
          const verification = panel.getByText('Verification', { exact: true });
          await verification.click();
          requireTrue(await verification.evaluate(e => e.closest('details')?.open) === true, 'Verification did not open');
        }
        sourceViews.push({ width: 360, colorScheme: 'light', card: i + 1, tab, ...await geometry(sourcesRoot) });
      }
      await toggle.click();
      requireTrue(await toggle.getAttribute('aria-expanded') === 'false', 'Sources card did not close');
    }
    await disclosure.click();
    requireTrue(await disclosure.getAttribute('aria-expanded') === 'false', 'Sources disclosure did not close');
    requireTrue(images.length === 7 && tableChecks.length === 7 && finalItems.length === 4, 'Scoped QA coverage incomplete');
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    for (const [kind, fragment] of Object.entries(fragments))
      requireTrue(sha(path.join(family, fragment.path)) === fragment.sha256, kind + ' changed during QA');
    requireTrue(sha(renderReceiptPath) === renderReceiptSha256 && sha(chartPath) === chartPayloadSha256 &&
      sha(sourcesPath) === sourcesPayloadSha256 && sha(__filename) === scriptSha256, 'QA input/script changed');
    const report = { schema: 'board-coverage-reclaim-preview-qa-v1', passed: true, createdAt: new Date().toISOString(),
      scope: 'Standalone sandboxed iframe preview; chart three viewport/themes, two Sources cards narrow-screen only. Not live host insertion.',
      renderReceiptSha256, fragments, scriptSha256, images, chartPayloadSha256, sourcesPayloadSha256,
      wrapperSha256: sha(wrapper), originalTableRows, chartViews, legendCheck, sourceViews, tableChecks, finalItems, errors,
      horizontalOverflow: false, sourceBytesUnchanged: true, screenshotsVisuallyInspected: false,
      actualMarketRuns: 0, analysisArtifactsModified: false };
    save('preview-verification.json', report);
    console.log(JSON.stringify({ passed: true, images: images.length, report: path.join(out, 'preview-verification.json'),
      reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (attemptCreated && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error);
  process.exitCode = 1;
});
