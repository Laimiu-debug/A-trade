// Presentation-only QA: five-state day shares and complete existing-account evidence.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const family = path.resolve(__dirname, '../artifacts/strategy-search/joint-cohort-regime-v1');
const wrapper = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const argv = process.argv.slice(2);
if (argv.length % 2) throw new Error('Expected --receipt-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({ length: argv.length / 2 }, (_, i) => [argv[2 * i], argv[2 * i + 1]]));
if (Object.keys(args).some(k => !['--receipt-sha', '--attempt'].includes(k))) throw new Error('Unknown argument');
if (!/^[a-f0-9]{64}$/.test(args['--receipt-sha'] || '')) throw new Error('Missing frozen render receipt SHA256');
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw new Error('Attempt must be four digits');
const out = path.join(family, 'browser-qa', 'attempt-' + attempt);
const expectedRows = [[45, 240, 120], [60, 60, 120, 90, 480, 12, 12, 12, 96]];
const states = ['强弱两组均多数上涨', '仅原强组多数上涨', '仅原弱组多数上涨', '两组均未多数上涨', '证据不足'];
const categories = ['主板前', '主板后', '创业前', '创业后', '科创前', '科创后'];
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
  fs.mkdirSync(out); // Never overwrite a prior or failed attempt.
  attemptCreated = true;
  const scriptSha256 = sha(__filename);
  const renderReceiptPath = path.join(family, 'render-receipt.json');
  const chartPath = path.join(family, 'chart.json'), sourcesPath = path.join(family, 'sources.json');
  const renderReceiptSha256 = sha(renderReceiptPath), chartPayloadSha256 = sha(chartPath), sourcesPayloadSha256 = sha(sourcesPath);
  const receipt = read(renderReceiptPath), chartPayload = read(chartPath), sources = read(sourcesPath), fragments = receipt.fragments;
  requireTrue(renderReceiptSha256 === args['--receipt-sha'], 'Render receipt changed');
  requireTrue(JSON.stringify(Object.keys(fragments)) === JSON.stringify(['chart', 'sources']) &&
    JSON.stringify(receipt.deliveryOrder) === JSON.stringify(['chart', 'sources']), 'Single chart/source order required');
  requireTrue(fragments.chart.path === 'inline/joint-cohort-regime-chart.html' &&
    fragments.sources.path === 'inline/joint-cohort-regime-sources.html', 'Unexpected fragment paths');
  requireTrue(receipt.payloads.chart.path === 'chart.json' && receipt.payloads.sources.path === 'sources.json' &&
    receipt.payloads.chart.sha256 === chartPayloadSha256 && receipt.payloads.sources.sha256 === sourcesPayloadSha256,
    'Render payload binding differs');
  const originalTableRows = sources.items.map(item => item.queries.map(query => query.rows.length));
  requireTrue(JSON.stringify(originalTableRows) === JSON.stringify(expectedRows) &&
    JSON.stringify(receipt.tableRows) === JSON.stringify(expectedRows), 'Twelve complete source tables required');
  requireTrue(chartPayload.rows.length === 30 && sources.items.length === 2 && receipt.accounts === 12 &&
    receipt.formationCount === 8155 && receipt.recordCount === 16310, 'Full diagnostic scope required');
  requireTrue(chartPayload.chart.xLabel === '交易日占比（%）' && chartPayload.chart.type === 'horizontalStackedBar' &&
    chartPayload.chart.showValues === false, 'Audited percentages require a raw stack and no dense value labels');
  requireTrue(JSON.stringify(chartPayload.chart.seriesOrder) === JSON.stringify(states) &&
    JSON.stringify(chartPayload.chart.categoryOrder) === JSON.stringify(categories), 'Five states and six periods required');
  const value = chartPayload.chart.y;
  for (const category of categories) {
    const rows = chartPayload.rows.filter(row => row.category === category);
    requireTrue(rows.length === 5 && new Set(rows.map(row => row.stateLabel)).size === 5 &&
      rows.every(row => Number.isFinite(row[value]) && row[value] >= 0 && row[value] <= 100) &&
      Math.abs(rows.reduce((s, row) => s + row[value], 0) - 100) < 1e-7, 'All-state daily denominator differs');
  }
  requireTrue(sources.items[1].queries[1].columns.some(column => column.field === 'winRatePct'), 'Payoff win rate missing');
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
  let legendCheck, editorCheck;
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const capture = async (locator, name, viewport = false) => {
      let targetBounds = null;
      if (viewport) {
        await locator.evaluate(e => e.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' }));
        await page.waitForTimeout(300); targetBounds = await locator.boundingBox();
        requireTrue(targetBounds && targetBounds.height > 0 && targetBounds.y < 900 && targetBounds.y + targetBounds.height > 0,
          'Scrolled target outside viewport: ' + name);
      }
      const file = path.join(out, name + '.png');
      if (viewport) await page.screenshot({ path: file, fullPage: false });
      else await locator.screenshot({ path: file });
      const result = { path: path.relative(family, file).replaceAll('\\', '/'), sha256: sha(file),
        capture: viewport ? 'page-viewport' : 'chart-element', ...(targetBounds ? { targetBounds } : {}) };
      images.push(result); return result;
    };
    const nonzeroRows = chartPayload.rows.filter(row => row[value] !== 0);
    for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await page.goto(pathToFileURL(path.join(out, 'chart-preview.html')).href);
      const frame = page.frameLocator('iframe'), chart = frame.locator('.data-inline-react-root');
      await chart.waitFor({ timeout: 15000 }); await chart.locator('svg.recharts-surface').first().waitFor();
      await page.waitForTimeout(200);
      const bounds = await geometry(chart);
      const painted = await chart.evaluate(element => ({
        bars: element.querySelectorAll('.recharts-bar-rectangle').length,
        nonzeroBars: [...element.querySelectorAll('.recharts-bar-rectangle')].filter(e => {
          const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0;
        }).length,
      }));
      // Native stacked bars keep one DOM container per reviewed row, including
      // zero-area containers for true zero states. Their visible area stays zero.
      requireTrue(painted.bars === chartPayload.rows.length && painted.nonzeroBars === nonzeroRows.length,
        'Native stack containers/visible marks differ from reviewed states: ' + JSON.stringify(painted));
      requireTrue(await chart.getByText(chartPayload.title, { exact: true }).isVisible(), 'Chart title missing');
      const visibleCategories = await chart.locator('svg text').evaluateAll(nodes => nodes.filter(node => node.querySelector('title'))
        .map(node => [...node.childNodes].filter(child => child.nodeType === Node.TEXT_NODE).map(child => child.textContent).join('')));
      for (const label of categories) requireTrue(visibleCategories.includes(label), 'Actual category truncated: ' + label);
      const axisTitle = chart.locator('.chart-axis-label');
      requireTrue(await axisTitle.count() === 1 && await axisTitle.isVisible() &&
        (await axisTitle.innerText()).trim() === '交易日占比（%）', 'Percentage axis missing');
      const toggles = frame.getByRole('button', { name: /^Toggle / });
      requireTrue(await toggles.count() === 5, 'All five state legends required');
      for (const state of states) requireTrue(await toggles.filter({ hasText: state }).count() === 1, 'Legend missing: ' + state);
      chartViews.push({ width, colorScheme, ...bounds, ...painted, reviewedRows: 30,
        exactZeroRows: 30 - nonzeroRows.length, nativeZeroContainersRetained: true, visibleCategories,
        axisTitle: await axisTitle.innerText(), image: await capture(chart, `chart-${width}-${colorScheme}`) });
      if (width === 780 && colorScheme === 'light') {
        await toggles.first().click(); await page.waitForTimeout(150);
        const hidden = await chart.locator('.recharts-bar-rectangle').count();
        await toggles.first().click(); await page.waitForTimeout(150);
        const restored = await chart.locator('.recharts-bar-rectangle').count();
        requireTrue(hidden === chartPayload.rows.filter(row => row.stateLabel !== states[0]).length && restored === chartPayload.rows.length,
          'Native state legend failed');
        legendCheck = { passed: true, hidden, restored, allFiveStatesRetainedInPayload: true };
        await frame.getByRole('button', { name: chartPayload.title + ' actions', exact: true }).click();
        await frame.getByRole('menuitem', { name: 'Edit chart', exact: true }).click();
        const cancel = frame.getByRole('button', { name: 'Cancel', exact: true }); await cancel.waitFor(); await cancel.click();
        requireTrue(await cancel.count() === 0 && await chart.locator('.recharts-bar-rectangle').count() === chartPayload.rows.length,
          'Shared editor Cancel changed chart');
        editorCheck = { passed: true, action: 'open-and-cancel', noAppliedChanges: true };
      }
    }
    await page.setViewportSize({ width: 360, height: 900 });
    await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
    await page.goto(pathToFileURL(path.join(out, 'sources-preview.html')).href);
    const frame = page.frameLocator('iframe'), sourcesRoot = frame.locator('.sources-receipt');
    await sourcesRoot.waitFor({ timeout: 15000 });
    const disclosure = sourcesRoot.locator('.receipt-disclosure'); await disclosure.click();
    requireTrue(await disclosure.getAttribute('aria-expanded') === 'true', 'Sources did not open');
    requireTrue(await sourcesRoot.locator('.receipt-card').count() === 2, 'Two source findings required');
    for (let i = 0; i < 2; i++) {
      const card = sourcesRoot.locator('.receipt-card').nth(i), toggle = card.locator('.receipt-card-toggle');
      if (await card.getAttribute('data-open') !== 'true') await toggle.click();
      requireTrue(await card.getAttribute('data-open') === 'true', 'Source card did not open');
      for (const tab of ['Overview', 'Data preview', 'Evidence flow']) {
        const button = card.getByRole('tab', { name: tab, exact: true }); await button.click();
        requireTrue(await button.getAttribute('aria-selected') === 'true', 'Sources tab failed: ' + tab);
        const panel = card.getByRole('tabpanel'); requireTrue((await panel.innerText()).trim().length > 0, 'Empty source panel');
        if (tab === 'Overview') {
          const last = panel.locator('.receipt-qualification-list li').last(); requireTrue(await last.count() === 1, 'Last caveat missing');
          finalItems.push({ card: i + 1, type: 'caveat', text: await last.innerText(),
            image: await capture(last, `sources-card${i + 1}-last-caveat-360-light`, true) });
        }
        if (tab === 'Data preview') {
          requireTrue(await panel.locator('table').count() === expectedRows[i].length, 'Source table count differs');
          for (let q = 0; q < sources.items[i].queries.length; q++) {
            const query = sources.items[i].queries[q], table = panel.locator('table').nth(q);
            const identityFields = ['scopeLabel', ...(i === 1 ? ['policyLabel'] : q === 2 ? ['relativeLabel'] : []),
              ...(i === 0 || q <= 4 ? ['stateLabel'] : []), ...(i === 1 && q === 8 ? ['phaseId'] : [])];
            const columns = identityFields.map(field => query.columns.findIndex(column => column.field === field));
            requireTrue(columns.every(index => index >= 0), 'Identity column missing');
            const visible = [], pageCounts = [];
            const paginatedBefore = sources.items[i].queries.slice(0, q).filter(v => v.rows.length > 50).length;
            const pager = query.rows.length > 50 ? panel.locator('.receipt-pagination').nth(paginatedBefore) : null;
            if (pager) requireTrue(await pager.getByRole('button', { name: 'Previous page', exact: true }).isDisabled(), 'Not first page');
            for (let offset = 0; offset < query.rows.length; offset += 50) {
              const n = Math.min(50, query.rows.length - offset);
              requireTrue(await table.locator('tbody tr').count() === n, 'Preview page count: ' + query.id);
              visible.push(...await table.locator('tbody tr').evaluateAll((rows, cols) => rows.map(row =>
                cols.map(index => row.querySelectorAll('td')[index].innerText.trim())), columns));
              pageCounts.push(n);
              if (offset + n < query.rows.length) {
                await pager.getByRole('button', { name: 'Next page', exact: true }).click();
                const end = Math.min(offset + 100, query.rows.length);
                await pager.getByRole('status').filter({ hasText: `Rows ${offset + 51}–${end} of ${query.rows.length}` }).waitFor();
              }
            }
            if (pager) requireTrue(await pager.getByRole('button', { name: 'Next page', exact: true }).isDisabled(), 'Missing final page');
            requireTrue(JSON.stringify(visible) === JSON.stringify(query.rows.map(row => identityFields.map(field => String(row[field])))),
              'Complete row identities differ: ' + query.id);
            tableChecks.push({ card: i + 1, queryId: query.id, rows: visible.length, pageCounts, allIdentitiesMatched: true });
            if (i === 1 && q === 4) {
              const last = table.locator('tbody tr').last();
              finalItems.push({ card: i + 1, type: 'formation-phase-last-page', text: await last.innerText(),
                image: await capture(last, 'sources-formation-phase-last-page-360-light', true) });
            }
            if (pager) {
              const previous = pager.getByRole('button', { name: 'Previous page', exact: true });
              while (await previous.isEnabled()) await previous.click();
              await pager.getByRole('status').filter({ hasText: `Rows 1–50 of ${query.rows.length}` }).waitFor();
            }
          }
        }
        if (tab === 'Evidence flow') {
          const verification = panel.getByText('Verification', { exact: true }); await verification.click();
          requireTrue(await verification.evaluate(e => e.closest('details')?.open) === true, 'Verification did not open');
        }
        sourceViews.push({ width: 360, colorScheme: 'light', card: i + 1, tab, ...await geometry(sourcesRoot) });
      }
      await toggle.click();
    }
    await disclosure.click();
    requireTrue(images.length === 6 && tableChecks.length === 12 && tableChecks.reduce((s, row) => s + row.rows, 0) === 1347 &&
      finalItems.length === 3, 'Scoped presentation coverage incomplete');
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    for (const [kind, fragment] of Object.entries(fragments)) requireTrue(sha(path.join(family, fragment.path)) === fragment.sha256, kind + ' changed');
    requireTrue(sha(renderReceiptPath) === renderReceiptSha256 && sha(chartPath) === chartPayloadSha256 &&
      sha(sourcesPath) === sourcesPayloadSha256 && sha(__filename) === scriptSha256, 'QA input/script changed');
    save('preview-verification.json', { schema: 'joint-cohort-regime-preview-qa-v1', passed: true, createdAt: new Date().toISOString(),
      scope: 'Standalone sandboxed iframe preview; three chart widths/themes, two Sources cards and all1347 preview rows through pagination. Not live host insertion.',
      renderReceiptSha256, fragments, payloads: receipt.payloads, deliveryOrder: receipt.deliveryOrder, tableRows: expectedRows,
      scriptSha256, images, chartPayloadSha256, sourcesPayloadSha256, wrapperSha256: sha(wrapper), originalTableRows,
      chartViews, legendCheck, editorCheck, sourceViews, tableChecks, finalItems, errors, horizontalOverflow: false,
      sourceBytesUnchanged: true, screenshotsVisuallyInspected: false, actualMarketRuns: 0, analysisArtifactsModified: false });
    console.log(JSON.stringify({ passed: true, images: images.length, report: path.join(out, 'preview-verification.json'),
      reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (attemptCreated && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error); process.exitCode = 1;
});
