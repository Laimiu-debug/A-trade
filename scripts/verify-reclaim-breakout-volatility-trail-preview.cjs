// Scoped standalone-iframe QA for all eighteen complete accounts and three exit policies. No analysis reruns.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const family = path.resolve(__dirname, '../artifacts/strategy-search/reclaim-breakout-volatility-trail-v1');
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
let fragments;
const expectedRows = [[18, 18, 18, 18, 6, 18, 36, 72, 144, 18]];
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
  requireTrue(renderReceiptSha256 === args['--receipt-sha'], 'Render receipt changed');
  fragments = receipt.fragments;
  requireTrue(JSON.stringify(Object.keys(fragments)) === JSON.stringify(['chart', 'sources']) &&
    JSON.stringify(receipt.deliveryOrder) === JSON.stringify(['chart', 'sources']), 'Single chart/source order required');
  requireTrue(fragments.chart.path === 'inline/reclaim-breakout-volatility-trail-chart.html' &&
    fragments.sources.path === 'inline/reclaim-breakout-volatility-trail-sources.html', 'Unexpected fragment paths');
  requireTrue(receipt.payloads.chart.path === 'chart.json' && receipt.payloads.sources.path === 'sources.json' &&
    receipt.payloads.chart.sha256 === chartPayloadSha256 && receipt.payloads.sources.sha256 === sourcesPayloadSha256,
    'Render payload binding differs');
  const originalTableRows = sources.items.map(item => item.queries.map(query => query.rows.length));
  requireTrue(JSON.stringify(originalTableRows) === JSON.stringify(expectedRows) &&
    JSON.stringify(receipt.tableRows) === JSON.stringify(expectedRows), 'Ten complete source tables required');
  requireTrue(chartPayload.rows.length === 18 && sources.items.length === 1, 'Eighteen accounts and one finding required');
  requireTrue(chartPayload.columns.includes('平均仓位（%）') &&
    chartPayload.columns.includes('最大回撤（%）') && chartPayload.columns.includes('估值不可靠日'),
    'Reviewed exposure change and both valuation quality fields missing');
  requireTrue(chartPayload.chart.xLabel === '累计账户净收益（%）', 'Percentage-point units missing');
  requireTrue(chartPayload.title === '三种退出政策：完整账户净收益' && chartPayload.chart.showValues === false,
    'Explicit comparison title and dense-series value labels disabled required');
  requireTrue(sources.items[0].queries[0].rows.length === 18 &&
    new Set(sources.items[0].queries[0].rows.map(row => row.policyLabel)).size === 3 &&
    sources.items[0].queries[1].rows.length === 18 &&
    new Set(sources.items[0].queries[1].rows.map(row => row.contrastLabel)).size === 3,
    'Three policies and eighteen predeclared contrasts required');
  for (const field of ['knownWindows', 'unknownWindows', 'invalidDistanceWindows', 'triggered', 'notTriggered', 'unknown',
    'skippedExistingExit', 'skippedStandardExit', 'carriedThresholdTriggers', 'activatedEntryCount', 'unknownAnchorEntryCount', 'volatilityCandidateEvaluatedObservations'])
    requireTrue(sources.items[0].queries[3].columns.some(c => c.field === field), 'Exit/window field missing: ' + field);
  requireTrue(chartPayload.rows.every(row => typeof row[chartPayload.chart.y] === 'number' && Number.isFinite(row[chartPayload.chart.y])),
    'Finite signed primary differences required');
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
    const capture = async (locator, name, wholePage = false) => {
      let targetBounds = null;
      if (wholePage) {
        await locator.evaluate(e => e.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' }));
        await page.waitForTimeout(300);
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
      await chart.locator('svg.recharts-surface').first().waitFor();
      await page.waitForTimeout(200);
      const bounds = await geometry(chart);
      const painted = await chart.evaluate(element => ({
        bars: element.querySelectorAll('.recharts-bar-rectangle').length,
        nonzeroBars: [...element.querySelectorAll('.recharts-bar-rectangle')].filter(e => {
          const box = e.getBoundingClientRect(); return box.width > 0 && box.height > 0;
        }).length,
        svgText: [...element.querySelectorAll('svg text')].map(e => e.textContent),
      }));
      // The shared renderer omits rectangles for exact zero values. Preserve
      // all eighteen reviewed account returns and verify every nonzero mark, not fake bars.
      const nonzeroRows = chartPayload.rows.filter(r => r[chartPayload.chart.y] !== 0);
      const zeroRows = chartPayload.rows.filter(r => r[chartPayload.chart.y] === 0);
      requireTrue(painted.bars === nonzeroRows.length && painted.nonzeroBars === nonzeroRows.length &&
        nonzeroRows.length + zeroRows.length === 18,
        'Observed nonzero marks differ from complete reviewed differences: ' + JSON.stringify(painted));
      requireTrue(await chart.getByText(chartPayload.title, { exact: true }).isVisible(), 'Primary-comparison title not visible');
      const visibleCategories = await chart.locator('svg text').evaluateAll(nodes => nodes.filter(node => node.querySelector('title'))
        .map(node => [...node.childNodes].filter(child => child.nodeType === Node.TEXT_NODE).map(child => child.textContent).join('')));
      for (const label of ['主板', '创业板', '科创板'])
        requireTrue(visibleCategories.includes(label), 'Actual category label truncated or missing: ' + label);
      // The shared ChartFrame renders its horizontal title as HTML, not SVG.
      const axisTitle = chart.locator('.chart-axis-label');
      requireTrue(await axisTitle.count() === 1 && await axisTitle.isVisible() &&
        (await axisTitle.innerText()).trim() === '累计账户净收益（%）', 'Percentage-point axis title missing or incorrect');
      const axisBounds = await axisTitle.evaluate(element => {
        const b = element.getBoundingClientRect(), p = element.closest('.data-inline-react-root').getBoundingClientRect();
        return { left: b.left, right: b.right, top: b.top, bottom: b.bottom,
          parentLeft: p.left, parentRight: p.right, parentTop: p.top, parentBottom: p.bottom,
          scrollWidth: element.scrollWidth, clientWidth: element.clientWidth };
      });
      requireTrue(axisBounds.left >= axisBounds.parentLeft - 1 && axisBounds.right <= axisBounds.parentRight + 1 &&
        axisBounds.top >= axisBounds.parentTop - 1 && axisBounds.bottom <= axisBounds.parentBottom + 1 &&
        axisBounds.scrollWidth <= axisBounds.clientWidth + 1, 'Axis title clipped: ' + JSON.stringify(axisBounds));
      const toggles = frame.getByRole('button', { name: /^Toggle / });
      requireTrue(await toggles.count() === 6, 'Six period-policy legend controls required');
      for (const period of chartPayload.chart.seriesOrder)
        requireTrue(await toggles.filter({ hasText: period }).count() === 1, 'Historical-period legend missing: ' + period);
      chartViews.push({ width, colorScheme, ...bounds, ...painted,
        reviewedRows: 18, exactZeroRows: zeroRows.length, nativeZeroRectanglesOmitted: true,
        fullSourceAccounts: 18, fullSourceComparisons: 18, visibleCategories, axisTitle: await axisTitle.innerText(), axisBounds,
        image: await capture(chart, `chart-${width}-${colorScheme}`) });
      if (width === 780 && colorScheme === 'light') {
        await toggles.first().click(); await page.waitForTimeout(150);
        const hidden = await chart.locator('.recharts-bar-rectangle').count();
        await toggles.first().click(); await page.waitForTimeout(150);
        const restored = await chart.locator('.recharts-bar-rectangle').count();
        const firstSeries = chartPayload.chart.seriesOrder[0];
        const expectedHidden = nonzeroRows.filter(r => r[chartPayload.chart.series] !== firstSeries).length;
        requireTrue(hidden === expectedHidden && restored === nonzeroRows.length, 'Period legend hide/restore failed');
        legendCheck = { passed: true, hidden, restored, reviewedRows: 18, exactZeroRows: zeroRows.length };
        await frame.getByRole('button', { name: chartPayload.title + ' actions', exact: true }).click();
        await frame.getByRole('menuitem', { name: 'Edit chart', exact: true }).click();
        const cancel = frame.getByRole('button', { name: 'Cancel', exact: true });
        await cancel.waitFor();
        await cancel.click();
        requireTrue(await cancel.count() === 0 && await chart.locator('.recharts-bar-rectangle').count() === nonzeroRows.length,
          'Shared editor open/Cancel changed chart');
        editorCheck = { passed: true, action: 'open-and-cancel', noAppliedChanges: true };
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
    requireTrue(await sourcesRoot.locator('.receipt-card').count() === 1, 'One chart-scoped finding required');
    for (let i = 0; i < 1; i++) {
      const card = sourcesRoot.locator('.receipt-card').nth(i);
      // A single finding opens with Sources and has no per-card toggle.
      requireTrue(await card.getAttribute('data-open') === 'true' &&
        await card.locator('.receipt-card-toggle').count() === 0, 'Single Sources finding did not open');
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
            const identityFields = ['period', 'boardLabel',
              ...(q === 1 ? ['contrastLabel'] : q === 4 ? [] : ['policyLabel']),
              ...(q >= 6 ? ['intervalLabel'] : [])];
            const identityColumns = identityFields.map(field => query.columns.findIndex(column => column.field === field));
            requireTrue(identityColumns.every(index => index >= 0), 'Source identity column missing');
            const visible = [], pageCounts = [];
            const paginatedBefore = sources.items[i].queries.slice(0, q).filter(v => v.rows.length > 50).length;
            const pager = query.rows.length > 50 ? panel.locator('.receipt-pagination').nth(paginatedBefore) : null;
            if (pager) requireTrue(await pager.getByRole('button', { name: 'Previous page', exact: true }).isDisabled(), 'Not first preview page');
            for (let offset = 0; offset < query.rows.length; offset += 50) {
              const expectedCount = Math.min(50, query.rows.length - offset);
              requireTrue(await table.locator('tbody tr').count() === expectedCount, 'Preview page row count differs: ' + query.id);
              visible.push(...await table.locator('tbody tr').evaluateAll((rows, columns) => rows.map(row =>
                columns.map(index => row.querySelectorAll('td')[index].innerText.trim())), identityColumns));
              pageCounts.push(expectedCount);
              if (offset + expectedCount < query.rows.length) {
                await pager.getByRole('button', { name: 'Next page', exact: true }).click();
                const end = Math.min(offset + 100, query.rows.length);
                await pager.getByRole('status').filter({ hasText: `Rows ${offset + 51}–${end} of ${query.rows.length}` }).waitFor();
              }
            }
            if (pager) requireTrue(await pager.getByRole('button', { name: 'Next page', exact: true }).isDisabled(), 'Final preview page missing');
            const expected = query.rows.map(row => identityFields.map(field => String(row[field])));
            requireTrue(JSON.stringify(visible) === JSON.stringify(expected), 'Complete source identities/order differ: ' + query.id);
            tableChecks.push({ card: i + 1, queryId: query.id, rows: visible.length, pageCounts, allIdentitiesMatched: true });
            if ([3, 9].includes(q)) {
              const last = table.locator('tbody tr').last(), kind = q === 3 ? 'exit' : 'event-epoch';
              finalItems.push({ card: i + 1, type: kind, text: await last.innerText(),
                image: await capture(last, `sources-${kind}-last-row-360-light`, true) });
            }
            if (pager) {
              const previous = pager.getByRole('button', { name: 'Previous page', exact: true });
              while (await previous.isEnabled()) await previous.click();
              await pager.getByRole('status').filter({ hasText: `Rows 1–50 of ${query.rows.length}` }).waitFor();
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
    }
    await disclosure.click();
    requireTrue(await disclosure.getAttribute('aria-expanded') === 'false', 'Sources disclosure did not close');
    requireTrue(images.length === 6 && tableChecks.length === 10 && finalItems.length === 3, 'Scoped QA coverage incomplete');
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    for (const [kind, fragment] of Object.entries(fragments))
      requireTrue(sha(path.join(family, fragment.path)) === fragment.sha256, kind + ' changed during QA');
    requireTrue(sha(renderReceiptPath) === renderReceiptSha256 && sha(chartPath) === chartPayloadSha256 &&
      sha(sourcesPath) === sourcesPayloadSha256 && sha(__filename) === scriptSha256, 'QA input/script changed');
    const report = { schema: 'reclaim-breakout-volatility-trail-preview-qa-v1', passed: true, createdAt: new Date().toISOString(),
      scope: 'Standalone sandboxed iframe preview; chart three viewport/themes, one chart-scoped Sources card narrow-screen, all ten tables and all 72-row half-year and144-row phase pages. Not live host insertion.',
      renderReceiptSha256, fragments, payloads: receipt.payloads, scriptSha256, images, chartPayloadSha256, sourcesPayloadSha256,
      wrapperSha256: sha(wrapper), originalTableRows, chartViews, legendCheck, editorCheck, sourceViews, tableChecks, finalItems, errors,
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
