// Scoped existing-fragment browser QA. No account, analysis, or registry writes.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const family = path.resolve(__dirname, '../artifacts/strategy-search/reclaim-exit-mechanism-v1');
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const wrapper = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const argv = process.argv.slice(2);
if (argv.length % 2) throw Error('Expected --receipt-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({ length: argv.length / 2 }, (_, i) => [argv[i * 2], argv[i * 2 + 1]]));
if (Object.keys(args).some(k => !['--receipt-sha', '--attempt'].includes(k)) || !/^[a-f0-9]{64}$/.test(args['--receipt-sha'] || '')) throw Error('Invalid arguments');
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw Error('Attempt must be four digits');
const out = path.join(family, 'browser-qa', 'attempt-' + attempt);
const expectedTables = [[6, 30, 54, 60, 45, 18, 18, 12, 12]];
const valueLabel = '对净盈亏差的贡献（万元）';
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const req = (ok, message) => { if (!ok) throw Error(message); };
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
let created = false;
async function geometry(locator) {
  const value = await locator.evaluate(e => { const b = e.getBoundingClientRect(); return {
    viewport: innerWidth, left: b.left, right: b.right, scrollWidth: e.scrollWidth, documentWidth: document.documentElement.scrollWidth }; });
  req(value.left >= -1 && value.right <= value.viewport + 1 && value.scrollWidth <= value.viewport + 1 && value.documentWidth <= value.viewport + 1,
    'Document overflow: ' + JSON.stringify(value)); return value;
}
(async () => {
  fs.mkdirSync(path.dirname(out), { recursive: true }); fs.mkdirSync(out); created = true;
  const receiptPath = path.join(family, 'render-receipt.json'), receipt = read(receiptPath), receiptSha256 = sha(receiptPath), scriptSha256 = sha(__filename);
  req(receiptSha256 === args['--receipt-sha'] && JSON.stringify(receipt.deliveryOrder) === JSON.stringify(['chart', 'sources']), 'Presentation receipt differs');
  req(receipt.chartRows === 6 && receipt.chartRecords === 24 && JSON.stringify(receipt.tableRows) === JSON.stringify(expectedTables), 'Six categories/four components or table matrix differs');
  const payloads = {};
  for (const key of ['chart', 'sources']) {
    const f = receipt.fragments[key], p = receipt.payloads[key];
    req(sha(path.join(family, f.path)) === f.sha256 && sha(path.join(family, p.path)) === p.sha256, 'Fragment/payload hash differs');
    payloads[key] = read(path.join(family, p.path));
    const run = spawnSync(python, ['-X', 'utf8', '-B', wrapper, path.join(family, f.path), path.join(out, key + '-preview.html')], { encoding: 'utf8', timeout: 30000 });
    save(key + '-wrapper-log.json', { status: run.status, stdout: run.stdout, stderr: run.stderr }); req(run.status === 0, 'Preview wrapper failed');
  }
  const chartPayload = payloads.chart, sources = payloads.sources;
  req(chartPayload.rows.length === 24 && chartPayload.chart.categoryOrder.length === 6 && chartPayload.chart.seriesOrder.length === 4 &&
    chartPayload.chart.xLabel === valueLabel && chartPayload.chart.y === valueLabel, 'Four complete signed components/units required');
  req(sources.items.length === 1 && JSON.stringify(sources.items.map(i => i.queries.map(q => q.rows.length))) === JSON.stringify(expectedTables), 'All255 source rows required');
  req(chartPayload.rows.every(row => typeof row[valueLabel] === 'number' && Number.isFinite(row[valueLabel])), 'Unknown chart total');
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], images = [], chartViews = [], tableChecks = [], sourceViews = [];
  let legendCheck;
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    page.on('pageerror', e => errors.push(e.message)); page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    const capture = async (locator, name, scope, viewport = false) => {
      if (viewport) { await locator.evaluate(e => e.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' })); await page.waitForTimeout(250); }
      const file = path.join(out, name + '.png');
      if (viewport) await page.screenshot({ path: file, fullPage: false }); else await locator.screenshot({ path: file });
      const record = { path: path.relative(family, file).replaceAll('\\', '/'), sha256: sha(file), scope,
        capture: viewport ? 'page-viewport' : 'chart-element' }; images.push(record); return record;
    };
    const nonzero = chartPayload.rows.filter(row => row[valueLabel] !== 0);
    for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
      await page.setViewportSize({ width, height: 900 }); await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await page.goto(pathToFileURL(path.join(out, 'chart-preview.html')).href);
      const frame = page.frameLocator('iframe'), chart = frame.locator('.data-inline-react-root');
      await chart.waitFor({ timeout: 15000 }); await chart.locator('svg.recharts-surface').first().waitFor(); await page.waitForTimeout(200);
      const bounds = await geometry(chart), marks = await chart.locator('.recharts-bar-rectangle').count();
      req(marks === nonzero.length, 'Visible nonzero component marks differ; zero components must remain in reviewed data');
      const categories = await chart.locator('svg text').evaluateAll(nodes => nodes.filter(n => n.querySelector('title'))
        .map(n => [...n.childNodes].filter(c => c.nodeType === Node.TEXT_NODE).map(c => c.textContent).join('')));
      req(chartPayload.chart.categoryOrder.every(c => categories.includes(c)), 'Actual visible category truncated/missing');
      req(await chart.getByText(chartPayload.title, { exact: true }).isVisible(), 'Title not visible');
      const axis = chart.locator('.chart-axis-label');
      req(await axis.count() === 1 && await axis.isVisible() && (await axis.innerText()).trim() === valueLabel, 'Monetary axis unit not visible');
      const toggles = frame.getByRole('button', { name: /^Toggle / }); req(await toggles.count() === 4, 'Four visible component legends required');
      for (const component of chartPayload.chart.seriesOrder) req(await toggles.filter({ hasText: component }).count() === 1, 'Missing component legend');
      chartViews.push({ width, colorScheme, ...bounds, marks, exactZeroRecords: 24 - nonzero.length, categories,
        image: await capture(chart, `chart-${width}-${colorScheme}`, 'Complete six-category chart with four component series') });
      if (width === 780 && colorScheme === 'light') {
        await toggles.first().click(); await page.waitForTimeout(100); const hidden = await chart.locator('.recharts-bar-rectangle').count();
        await toggles.first().click(); await page.waitForTimeout(100); const restored = await chart.locator('.recharts-bar-rectangle').count();
        req(hidden === nonzero.filter(r => r.componentLabel !== chartPayload.chart.seriesOrder[0]).length && restored === nonzero.length, 'Legend hide/restore differs');
        legendCheck = { passed: true, hidden, restored };
      }
    }
    await page.setViewportSize({ width: 360, height: 900 }); await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
    await page.goto(pathToFileURL(path.join(out, 'sources-preview.html')).href);
    let frame = page.frameLocator('iframe'), root = frame.locator('.sources-receipt'); await root.waitFor({ timeout: 15000 });
    await root.locator('.receipt-disclosure').click(); let card = root.locator('.receipt-card');
    req(await card.count() === 1 && await card.getAttribute('data-open') === 'true' && await card.locator('.receipt-card-toggle').count() === 0, 'Single-finding compatibility');
    for (const tab of ['Overview', 'Data preview', 'Evidence flow']) {
      const button = card.getByRole('tab', { name: tab, exact: true }); await button.click();
      const panel = card.getByRole('tabpanel'); req(await button.getAttribute('aria-selected') === 'true' && (await panel.innerText()).length > 0, 'Source tab unavailable');
      if (tab === 'Overview') await capture(panel.locator('.receipt-qualification-list li').last(), 'sources-last-caveat-360-light', 'Last caveat and surrounding viewport only', true);
      if (tab === 'Data preview') {
        req(await panel.locator('table').count() === 9, 'Nine evidence tables required');
        for (let q = 0; q < 9; q++) {
          const query = sources.items[0].queries[q], table = panel.locator('table').nth(q);
          const ids = ['period', 'boardLabel', 'policyLabel', 'groupLabel', 'maturityLabel', 'formationYear', 'formationEpochLabel', 'closedPairTimingLabel', 'baselineOutcomeLabel', 'phaseIdLabel']
            .filter(k => query.columns.some(c => c.field === k));
          const indexes = ids.map(k => query.columns.findIndex(c => c.field === k));
          const pagerIndex = sources.items[0].queries.slice(0, q).filter(x => x.rows.length > 50).length;
          const pager = query.rows.length > 50 ? panel.locator('.receipt-pagination').nth(pagerIndex) : null;
          const actual = [], pageCounts = [];
          for (let offset = 0; offset < query.rows.length; offset += 50) {
            const count = Math.min(50, query.rows.length - offset); req(await table.locator('tbody tr').count() === count, 'Preview page row count differs');
            actual.push(...await table.locator('tbody tr').evaluateAll((rows, cols) => rows.map(row => cols.map(i => row.querySelectorAll('td')[i].innerText.trim())), indexes)); pageCounts.push(count);
            if (offset + count < query.rows.length) { await pager.getByRole('button', { name: 'Next page', exact: true }).click(); await page.waitForTimeout(100); }
          }
          req(JSON.stringify(actual) === JSON.stringify(query.rows.map(r => ids.map(k => String(r[k])))), 'All row identities/order differ: ' + query.id);
          tableChecks.push({ queryId: query.id, rows: actual.length, pageCounts, allIdentitiesMatched: true });
          if (q === 8) await capture(table.locator('tbody tr').last(), 'sources-last-curve-row-360-light', 'Final continuous-curve row and surrounding viewport; not all255 rows in one image', true);
          if (pager) { const previous = pager.getByRole('button', { name: 'Previous page', exact: true }); while (await previous.isEnabled()) await previous.click(); }
        }
      }
      if (tab === 'Evidence flow') { const step = panel.getByText('Verification', { exact: true }); await step.click(); req(await step.evaluate(e => e.closest('details')?.open), 'Verification checkpoint unavailable'); }
      sourceViews.push({ width: 360, tab, ...await geometry(root) });
    }
    // One targeted numeric view. Native scrolling preserves clipping semantics;
    // this image is explicitly the table header/first rows, not the full18 rows.
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.goto(pathToFileURL(path.join(out, 'sources-preview.html')).href);
    frame = page.frameLocator('iframe'); root = frame.locator('.sources-receipt'); await root.waitFor();
    await root.locator('.receipt-disclosure').click(); card = root.locator('.receipt-card');
    await card.getByRole('tab', { name: 'Data preview', exact: true }).click(); await page.waitForTimeout(300);
    await capture(card.getByRole('tabpanel').locator('table').nth(5).locator('thead'), 'sources-closed-pair-timing-top-1280-light',
      'Closed-pair timing table header and first visible numeric rows; full18-row identities checked separately', true);
    req(tableChecks.length === 9 && tableChecks.reduce((n, r) => n + r.rows, 0) === 255, 'Complete255-row evidence not inspected');
    req(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    for (const key of ['chart', 'sources']) req(sha(path.join(family, receipt.fragments[key].path)) === receipt.fragments[key].sha256 && sha(path.join(family, receipt.payloads[key].path)) === receipt.payloads[key].sha256, 'Presentation changed');
    req(sha(receiptPath) === receiptSha256 && sha(__filename) === scriptSha256, 'Receipt or QA script changed');
    save('preview-verification.json', { schema: 'reclaim-exit-mechanism-preview-qa-v1', passed: true, createdAt: new Date().toISOString(),
      renderReceiptSha256: receiptSha256, scriptSha256, fragments: receipt.fragments, payloads: receipt.payloads,
      deliveryOrder: receipt.deliveryOrder, tableRows: expectedTables, images, chartViews, tableChecks, sourceViews, legendCheck, errors,
      scope: 'Standalone sandboxed iframe. Three chart viewport/themes; all255 table rows including pagination; three scoped Sources views. Not live host insertion.',
      screenshotsVisuallyInspected: false, actualMarketRuns: 0, analysisArtifactsModified: false, sourceBytesUnchanged: true });
    console.log(JSON.stringify({ passed: true, images: images.length, report: path.join(out, 'preview-verification.json'), reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (created && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error); process.exitCode = 1;
});
