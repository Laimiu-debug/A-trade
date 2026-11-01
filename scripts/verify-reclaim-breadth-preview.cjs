// Shared chart and Sources standalone sandboxed-iframe QA. Does not modify analysis data.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const family = path.join(__dirname, '../artifacts/strategy-search/reclaim-breadth-entry-v1');
const render = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
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
const fragment = { file: path.join(family, 'inline/reclaim-breadth-sources.html'), sha256: args['--sources-sha'] };
const chartFragment = { file: path.join(family, 'inline/reclaim-breadth-returns.html'), sha256: args['--chart-sha'] };
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const requireTrue = (condition, message) => { if (!condition) throw new Error(message); };
let attemptCreated = false;

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
  attemptCreated = true;
  const scriptSha256 = sha(__filename);
  requireTrue(sha(fragment.file) === fragment.sha256, 'Fragment hash mismatch before QA');
  requireTrue(fs.statSync(fragment.file).size < 1024 * 1024, 'Fragment exceeds 1 MiB');
  requireTrue(sha(chartFragment.file) === chartFragment.sha256 && fs.statSync(chartFragment.file).size < 1024 * 1024, 'Chart fragment hash/size differs');
  const sourcePath = path.join(family, 'sources.json');
  const renderReceiptPath = path.join(family, 'render-receipt.json');
  const chartPath = path.join(family, 'chart.json');
  const chartPayload = JSON.parse(fs.readFileSync(chartPath, 'utf8'));
  const chartPayloadSha256 = sha(chartPath);
  const sourceSha256 = sha(sourcePath), renderReceiptSha256 = sha(renderReceiptPath);
  const source = JSON.parse(fs.readFileSync(sourcePath, 'utf8'));
  const renderReceipt = JSON.parse(fs.readFileSync(renderReceiptPath, 'utf8'));
  requireTrue(renderReceipt.payloadSha256 === sourceSha256 && renderReceipt.fragmentSha256 === fragment.sha256,
    'Recorded source rows are not bound to the reviewed fragment');
  requireTrue(renderReceipt.chartSha256 === chartFragment.sha256 && renderReceipt.chartPayloadSha256 === chartPayloadSha256, 'Chart payload/fragment binding differs');
  const originalTableRows = source.items.map(item => item.queries.map(query => query.rows.length));
  requireTrue(JSON.stringify(originalTableRows) === JSON.stringify([[10, 10, 10, 2, 10], [20, 40, 80, 10]]),
    'Complete recorded source row counts differ: ' + JSON.stringify(originalTableRows));
  const yearQuery = source.items[1].queries[2];
  const yearIdentityFields = ['period', 'policyLabel', 'intervalLabel'];
  const yearIdentityColumns = yearIdentityFields.map(field => yearQuery.columns.findIndex(column => column.field === field));
  requireTrue(yearIdentityColumns.every(index => index >= 0), 'Phase identity columns missing');
  const wrapped = spawnSync(python, ['-X', 'utf8', '-B', render, fragment.file, path.join(out, 'sources-preview.html')],
    { encoding: 'utf8', timeout: 30000 });
  save('wrapper-log.json', { status: wrapped.status, stdout: wrapped.stdout, stderr: wrapped.stderr });
  requireTrue(wrapped.status === 0, 'Standalone Sources wrapper failed');
  const chartWrapped = spawnSync(python, ['-X', 'utf8', '-B', render, chartFragment.file, path.join(out, 'chart-preview.html')],
    { encoding: 'utf8', timeout: 30000 });
  save('chart-wrapper-log.json', { status: chartWrapped.status, stdout: chartWrapped.stdout, stderr: chartWrapped.stderr });
  requireTrue(chartWrapped.status === 0, 'Standalone chart wrapper failed');
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], views = [], chartViews = [], screenshots = [], overviewChecks = [], phaseChecks = [], tableChecks = [];
  let legendCheck;
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
    const capturePage = async (locator, name) => {
      await locator.scrollIntoViewIfNeeded();
      const box = await locator.boundingBox();
      requireTrue(box && box.height > 0 && box.y < 900 && box.y + box.height > 0,
        'Scrolled target is outside the page viewport: ' + name);
      const filename = path.join(out, name + '.png');
      await page.screenshot({ path: filename, fullPage: false });
      const record = { path: filename, sha256: sha(filename), capture: 'page-viewport', targetBounds: box };
      screenshots.push(record);
      return record;
    };
    const checkYearPage = async (table, start, end) => {
      const visible = await table.locator('tbody tr').evaluateAll((rows, columns) => rows.map(row =>
        columns.map(index => row.querySelectorAll('td')[index].innerText.trim())), yearIdentityColumns);
      const expected = yearQuery.rows.slice(start, end).map(row => yearIdentityFields.map(field => String(row[field])));
      requireTrue(JSON.stringify(visible) === JSON.stringify(expected),
        'Phase page identities/order differ for source rows ' + (start + 1) + '-' + end);
      return visible;
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
      checkGeometry(bounds, `chart ${width} ${colorScheme}`);
      const painted = await chart.evaluate(element => ({
        bars: element.querySelectorAll('.recharts-bar-rectangle').length,
        nonzeroBars: [...element.querySelectorAll('.recharts-bar-rectangle')].filter(e => {
          const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
        }).length,
        svgText: [...element.querySelectorAll('svg text')].map(e => e.textContent),
      }));
      requireTrue(painted.bars === 10 && painted.nonzeroBars === chartPayload.rows.filter(r => r[chartPayload.chart.y] !== 0).length,
        'Ten genuine chart marks required: ' + JSON.stringify(painted));
      for (const name of [...new Set(chartPayload.rows.map(r => r.policyLabel))])
        requireTrue(painted.svgText.some(text => text.includes(name)), 'Chart category missing: ' + name);
      requireTrue(painted.svgText.some(text => text.includes('%')), 'Percent axis unit missing');
      const toggles = frame.getByRole('button', { name: /^Toggle / });
      requireTrue(await toggles.count() === 2, 'Two period legend controls required');
      chartViews.push({ width, colorScheme, ...bounds, ...painted,
        screenshot: await capture(chart, `chart-${width}-${colorScheme}`) });
      if (width === 780 && colorScheme === 'light') {
        const toggle = toggles.first();
        await toggle.click(); await page.waitForTimeout(150);
        const hidden = await chart.locator('.recharts-bar-rectangle').count();
        await toggle.click(); await page.waitForTimeout(150);
        const restored = await chart.locator('.recharts-bar-rectangle').count();
        requireTrue(hidden === 5 && restored === 10, 'Period legend hide/restore failed');
        legendCheck = { passed: true, hidden, restored };
      }
    }
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
          if (tab === 'Overview') {
            const finalItem = panel.locator('.receipt-qualification-list li').last();
            requireTrue(await finalItem.count() === 1, 'Overview final caveat is missing');
            overviewChecks.push({ width, colorScheme, card: i + 1,
              finalItemText: (await finalItem.innerText()).trim(),
              screenshot: await capturePage(finalItem, `overview-last-item-card${i + 1}-${width}-${colorScheme}`) });
          }
          if (tab === 'Data preview') {
            previewRows = await panel.locator('table tbody tr').count();
            requireTrue(previewRows > 0, 'No recorded preview rows: ' + title);
            const tableRows = await panel.locator('table').evaluateAll(tables => tables.map(t => t.querySelectorAll('tbody tr').length));
            requireTrue(JSON.stringify(tableRows) === JSON.stringify(i === 0 ? [10, 10, 10, 2, 10] : [20, 40, 50, 10]),
              'Initial preview page row counts differ: ' + JSON.stringify(tableRows));
            if (i === 1) {
              const yearTable = panel.locator('table').nth(2);
              const pager = panel.locator('.receipt-pagination');
              requireTrue(await pager.count() === 1, 'Expected one phase paginator');
              const previous = pager.getByRole('button', { name: 'Previous page', exact: true });
              const next = pager.getByRole('button', { name: 'Next page', exact: true });
              requireTrue(await previous.isDisabled() && await next.isEnabled(), 'Unexpected first-page controls');
              await checkYearPage(yearTable, 0, 50);
              await next.click();
              await pager.getByRole('status').filter({ hasText: 'Rows 51–80 of 80' }).waitFor();
              requireTrue(await previous.isEnabled() && await next.isDisabled(), 'Unexpected final-page controls');
              await checkYearPage(yearTable, 50, 80);
              const finalRow = yearTable.locator('tbody tr').last();
              phaseChecks.push({ width, colorScheme, card: 2, tableIndex: 2,
                recordedTableRows: originalTableRows[1], firstPageTableRows: tableRows,
                visitedPageRowCounts: [50, 30], originalRowsChecked: 80, rowIdentitiesAndOrderMatched: true,
                finalRowText: (await finalRow.innerText()).trim(),
                screenshot: await capturePage(finalRow, `phase-last-row-${width}-${colorScheme}`) });
              await previous.click();
              await pager.getByRole('status').filter({ hasText: 'Rows 1–50 of 80' }).waitFor();
              await checkYearPage(yearTable, 0, 50);
              requireTrue(await previous.isDisabled() && await next.isEnabled(), 'Pagination did not return to first page');
            }
          }
          if (tab === 'Data preview') {
            for (let q = 0; q < source.items[i].queries.length; q++) {
              if (i === 1 && q === 2) continue; // The 80-row phase table is checked through both pages above.
              const table = panel.locator('table').nth(q);
              const expected = source.items[i].queries[q].rows.length;
              requireTrue(await table.locator('tbody tr').count() === expected, 'Complete non-paginated table differs');
              const lastRow = table.locator('tbody tr').last();
              tableChecks.push({ width, colorScheme, card: i + 1, queryId: source.items[i].queries[q].id, rows: expected,
                finalRowText: (await lastRow.innerText()).trim(),
                screenshot: await capturePage(lastRow, `table-last-row-card${i+1}-table${q+1}-${width}-${colorScheme}`) });
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
    requireTrue(overviewChecks.length === 6 && phaseChecks.length === 3 && tableChecks.length === 24, 'Missing final-item viewport checks');
    save('overview-reading-check.json', { passed: true, checks: overviewChecks,
      scope: 'Both cards final Overview caveat scrolled into view in all three viewport/theme combinations; page screenshots require visual inspection.' });
    save('phase-preview-check.json', { passed: true, checks: phaseChecks,
      scope: 'All nine source tables retained. Phase pagination was clicked through 50+30 rows; all 80 identities/order matched the bound source, and every table final page/row was captured as a page viewport.', tableChecks });
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    requireTrue(sha(fragment.file) === fragment.sha256, 'Fragment changed during QA');
    requireTrue(sha(sourcePath) === sourceSha256 && sha(renderReceiptPath) === renderReceiptSha256,
      'Source payload or renderer binding changed during QA');
    requireTrue(sha(chartFragment.file) === chartFragment.sha256 && sha(chartPath) === chartPayloadSha256, 'Chart changed during QA');
    requireTrue(sha(__filename) === scriptSha256, 'QA script changed during execution');
    const report = { schema: 'reclaim-breadth-entry-preview-qa-v1', passed: true, createdAt: new Date().toISOString(),
      scope: 'Standalone sandboxed iframe preview only; not live host insertion.', fragment, scriptSha256,
      sourceSha256, chartPayloadSha256, renderReceiptSha256, originalTableRows,
      fragments: { chart: chartFragment, sources: fragment }, chartViews, legendCheck, tableChecks,
      previewWrapperToolSha256: sha(render), views, screenshots, overviewChecks, phaseChecks, sourceCards: 2, errors,
      horizontalOverflow: false, sourceBytesUnchanged: true, screenshotsVisuallyInspected: false,
      actualMarketRuns: 0, analysisArtifactsModified: false };
    save('preview-verification.json', report);
    console.log(JSON.stringify({ passed: true, sourceCards: 2, screenshots: screenshots.length,
      report: path.join(out, 'preview-verification.json'), reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (attemptCreated && fs.existsSync(out) && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error);
  process.exitCode = 1;
});


