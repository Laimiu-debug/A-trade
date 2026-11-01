// Presentation-only browser verification. Every failed attempt remains intact.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const family = path.resolve(__dirname, '../artifacts/strategy-search/reclaim-relative-feedback-context-v1');
const wrapper = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const boards = ['main', 'chinext', 'star'];
const boardLabels = { main: '主板', chinext: '创业板', star: '科创板' };
const states = ['stronger', 'weaker', 'equal', 'unknown'];
const expectedTables = [[16, 8, 4, 4, 2, 32, 24, 4, 16]];
const deliveryOrder = boards.flatMap(b => [b + 'Chart', b + 'Sources']);
const valueLabel = '已闭仓贡献（初始资金百分点）';
const argv = process.argv.slice(2);
if (argv.length % 2) throw new Error('Expected --receipt-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({ length: argv.length / 2 }, (_, i) => [argv[2 * i], argv[2 * i + 1]]));
if (Object.keys(args).some(k => !['--receipt-sha', '--attempt'].includes(k))) throw new Error('Unknown argument');
if (!/^[a-f0-9]{64}$/.test(args['--receipt-sha'] || '')) throw new Error('Missing reviewed render receipt SHA');
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw new Error('Attempt must be four digits');
const out = path.join(family, 'browser-qa', 'attempt-' + attempt);
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const requireTrue = (condition, message) => { if (!condition) throw new Error(message); };
let attemptCreated = false;

async function geometry(locator) {
  const result = await locator.evaluate(element => {
    const box = element.getBoundingClientRect();
    return { width: innerWidth, left: box.left, right: box.right,
      contentWidth: element.scrollWidth, documentWidth: document.documentElement.scrollWidth };
  });
  requireTrue(result.left >= -1 && result.right <= result.width + 1 &&
    result.contentWidth <= result.width + 1 && result.documentWidth <= result.width + 1,
    'Horizontal document overflow: ' + JSON.stringify(result));
  return result;
}

(async () => {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.mkdirSync(out);
  attemptCreated = true;
  const receiptPath = path.join(family, 'render-receipt.json');
  const receiptSha256 = sha(receiptPath), scriptSha256 = sha(__filename);
  requireTrue(receiptSha256 === args['--receipt-sha'], 'Render receipt changed');
  const receipt = read(receiptPath), payloads = {}, payloadPins = {};
  requireTrue(JSON.stringify(receipt.deliveryOrder) === JSON.stringify(deliveryOrder) &&
    JSON.stringify(Object.keys(receipt.fragments)) === JSON.stringify(deliveryOrder), 'Board chart/source delivery order differs');
  requireTrue(receipt.actualMarketRuns === 0 && receipt.analysisArtifactsModified === false, 'Presentation scope differs');
  for (const key of deliveryOrder) {
    const fragment = receipt.fragments[key], payload = receipt.payloads[key];
    const fragmentPath = path.join(family, fragment.path), payloadPath = path.join(family, payload.path);
    requireTrue(sha(fragmentPath) === fragment.sha256 && fs.statSync(fragmentPath).size < 1_000_000, key + ' fragment changed/oversized');
    requireTrue(sha(payloadPath) === payload.sha256, key + ' payload changed');
    payloads[key] = read(payloadPath); payloadPins[key] = payload.sha256;
    const wrap = spawnSync(python, ['-X', 'utf8', '-B', wrapper, fragmentPath, path.join(out, key + '-preview.html')],
      { encoding: 'utf8', timeout: 30000 });
    save(key + '-wrapper-log.json', { status: wrap.status, stdout: wrap.stdout, stderr: wrap.stderr });
    requireTrue(wrap.status === 0, key + ' preview wrapper failed');
  }
  for (const board of boards) {
    const chart = payloads[board + 'Chart'], sources = payloads[board + 'Sources'];
    const tableRows = sources.items.map(item => item.queries.map(q => q.rows.length));
    requireTrue(JSON.stringify(tableRows) === JSON.stringify(expectedTables) &&
      JSON.stringify(receipt.tableRows[board]) === JSON.stringify(expectedTables), board + ' source matrix incomplete');
    requireTrue(chart.rows.length === 16 && sources.items.length === 1 && chart.chart.categoryOrder.length === 8 &&
      chart.chart.seriesOrder.length === 2, board + ' full period/policy/state chart missing');
    requireTrue(chart.chart.y === valueLabel && chart.chart.xLabel === valueLabel && chart.chart.showValues === true,
      board + ' contribution measure/axis missing');
    requireTrue(new Set(chart.rows.map(row => row.state)).size === 4 &&
      states.every(state => chart.rows.filter(row => row.state === state).length === 4), board + ' unknown/empty states omitted');
    requireTrue(chart.rows.every(row => typeof row[valueLabel] === 'number' && Number.isFinite(row[valueLabel])),
      board + ' contribution is not a real finite total');
    for (const key of ['closedPnlAmount', 'openUnrealizedPnlAmount', 'openRealizedPnlAmount', 'totalPnlAmount', 'population'])
      requireTrue(sources.items[0].queries[0].columns.some(c => c.field === key), board + ' complete context missing ' + key);
    requireTrue(sources.items[0].title.includes(boardLabels[board]) &&
      sources.items[0].queries.every(q => q.source.label.includes(boardLabels[board]) && q.columns.length <= 8),
      board + ' board-scoped preview identity missing');
    const payoff = sources.items[0].queries[8];
    requireTrue(JSON.stringify(payoff.columns.map(c => c.field)) === JSON.stringify([
      'period', 'policyLabel', 'stateLabel', 'closed', 'winRatePct', 'meanWinPct', 'meanLossPct', 'top5ShareOfGrossProfitPct']) &&
      payoff.rows.length === 16 && payoff.rows.every(row => row.closed > 0 || row.winRatePct === null),
      board + ' complete closed-payoff distribution or null conditional rates missing');
  }
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], images = [], chartViews = [], legendChecks = [], tableChecks = [], sourceViews = [], finalItems = [];
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const capture = async (locator, name, viewport = false) => {
      let targetBounds;
      if (viewport) {
        await locator.scrollIntoViewIfNeeded();
        // Allow layout and rasterization to complete after a nested table scroll.
        await locator.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await page.waitForTimeout(100);
        targetBounds = await locator.boundingBox();
        requireTrue(targetBounds && targetBounds.height > 0 && targetBounds.y < 900 &&
          targetBounds.y + targetBounds.height > 0, 'Target outside captured viewport: ' + name);
      }
      const file = path.join(out, name + '.png');
      if (viewport) await page.screenshot({ path: file, fullPage: false });
      else await locator.screenshot({ path: file });
      const result = { path: path.relative(family, file).replaceAll('\\', '/'), sha256: sha(file),
        capture: viewport ? 'page-viewport' : 'chart-element', ...(targetBounds ? { targetBounds } : {}) };
      images.push(result); return result;
    };
    for (const board of boards) {
      const key = board + 'Chart', payload = payloads[key];
      const nonzeroRows = payload.rows.filter(row => row[valueLabel] !== 0);
      for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
        await page.goto(pathToFileURL(path.join(out, key + '-preview.html')).href);
        const frame = page.frameLocator('iframe'), chart = frame.locator('.data-inline-react-root');
        await chart.waitFor({ timeout: 15000 });
        await chart.locator('svg.recharts-surface').first().waitFor();
        await page.waitForTimeout(200);
        const bounds = await geometry(chart);
        const painted = await chart.evaluate(element => ({
          bars: element.querySelectorAll('.recharts-bar-rectangle').length,
          nonzeroBars: [...element.querySelectorAll('.recharts-bar-rectangle')].filter(e => {
            const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0;
          }).length,
        }));
        requireTrue(painted.bars === nonzeroRows.length && painted.nonzeroBars === nonzeroRows.length,
          board + ' observed nonzero marks differ from all reviewed totals');
        const visibleCategories = await chart.locator('svg text').evaluateAll(nodes => nodes.filter(node => node.querySelector('title'))
          .map(node => [...node.childNodes].filter(child => child.nodeType === Node.TEXT_NODE).map(child => child.textContent).join('')));
        for (const category of payload.chart.categoryOrder)
          requireTrue(visibleCategories.includes(category), board + ' actual visible category truncated/missing: ' + category);
        requireTrue(await chart.getByText(payload.title, { exact: true }).isVisible() && payload.title.includes(boardLabels[board]),
          board + ' chart title missing');
        const axis = chart.locator('.chart-axis-label');
        requireTrue(await axis.count() === 1 && await axis.isVisible() && (await axis.innerText()).trim() === valueLabel,
          board + ' initial-capital percentage-point unit not visible');
        const axisBounds = await axis.evaluate(element => {
          const b = element.getBoundingClientRect(), p = element.closest('.data-inline-react-root').getBoundingClientRect();
          return { left: b.left, right: b.right, top: b.top, bottom: b.bottom,
            parentLeft: p.left, parentRight: p.right, parentTop: p.top, parentBottom: p.bottom,
            scrollWidth: element.scrollWidth, clientWidth: element.clientWidth };
        });
        requireTrue(axisBounds.left >= axisBounds.parentLeft - 1 && axisBounds.right <= axisBounds.parentRight + 1 &&
          axisBounds.top >= axisBounds.parentTop - 1 && axisBounds.bottom <= axisBounds.parentBottom + 1 &&
          axisBounds.scrollWidth <= axisBounds.clientWidth + 1, board + ' axis title clipped');
        const toggles = frame.getByRole('button', { name: /^Toggle / });
        requireTrue(await toggles.count() === 2, 'Both exit policies must be visible');
        for (const policy of payload.chart.seriesOrder)
          requireTrue(await toggles.filter({ hasText: policy }).count() === 1, 'Exit policy legend missing: ' + policy);
        chartViews.push({ board, width, colorScheme, ...bounds, ...painted, visibleCategories,
          reviewedRows: 16, exactZeroRows: 16 - nonzeroRows.length, nativeZeroRectanglesOmitted: true,
          axisBounds, image: await capture(chart, `${board}-chart-${width}-${colorScheme}`) });
        if (width === 780 && colorScheme === 'light') {
          await toggles.first().click(); await page.waitForTimeout(100);
          const hidden = await chart.locator('.recharts-bar-rectangle').count();
          await toggles.first().click(); await page.waitForTimeout(100);
          const restored = await chart.locator('.recharts-bar-rectangle').count();
          requireTrue(hidden === nonzeroRows.filter(row => row[payload.chart.series] !== payload.chart.seriesOrder[0]).length &&
            restored === nonzeroRows.length, board + ' policy legend hide/restore failed');
          legendChecks.push({ board, passed: true, hidden, restored });
        }
      }
      const sourceKey = board + 'Sources', source = payloads[sourceKey];
      await page.setViewportSize({ width: 360, height: 900 });
      await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
      await page.goto(pathToFileURL(path.join(out, sourceKey + '-preview.html')).href);
      const frame = page.frameLocator('iframe'), root = frame.locator('.sources-receipt');
      await root.waitFor({ timeout: 15000 });
      const disclosure = root.locator('.receipt-disclosure'); await disclosure.click();
      requireTrue(await disclosure.getAttribute('aria-expanded') === 'true', 'Sources did not open');
      const card = root.locator('.receipt-card');
      requireTrue(await card.count() === 1 && await card.getAttribute('data-open') === 'true' &&
        await card.locator('.receipt-card-toggle').count() === 0, 'Single finding compatibility failed');
      for (const tab of ['Overview', 'Data preview', 'Evidence flow']) {
        const button = card.getByRole('tab', { name: tab, exact: true }); await button.click();
        requireTrue(await button.getAttribute('aria-selected') === 'true', 'Sources tab did not select');
        const panel = card.getByRole('tabpanel');
        requireTrue((await panel.innerText()).trim().length > 0, 'Empty Sources panel');
        if (tab === 'Overview') {
          const last = panel.locator('.receipt-qualification-list li').last();
          requireTrue(await last.count() === 1, 'Material qualifications missing');
          finalItems.push({ board, kind: 'lastCaveat', text: await last.innerText(),
            image: await capture(last, `${board}-sources-last-caveat-360-light`, true) });
        }
        if (tab === 'Data preview') {
          requireTrue(await panel.locator('table').count() === 9, 'Nine complete evidence tables required');
          for (let q = 0; q < 9; q++) {
            const query = source.items[0].queries[q], table = panel.locator('table').nth(q);
            requireTrue(await table.locator('tbody tr').count() === query.rows.length, 'Table row count mismatch: ' + query.id);
            const fields = ['period', 'boardLabel', 'policyLabel', 'stateLabel', 'formationYear', 'epochLabel', 'phaseId']
              .filter(field => query.columns.some(column => column.field === field));
            requireTrue(fields.length >= (q === 4 ? 1 : 2), 'Evidence identity fields incomplete');
            const columns = fields.map(field => query.columns.findIndex(column => column.field === field));
            const visible = await table.locator('tbody tr').evaluateAll((rows, indices) => rows.map(row =>
              indices.map(index => row.querySelectorAll('td')[index].innerText.trim())), columns);
            requireTrue(JSON.stringify(visible) === JSON.stringify(query.rows.map(row => fields.map(field => String(row[field])))),
              'Complete evidence identities/order differ: ' + query.id);
            tableChecks.push({ board, queryId: query.id, rows: query.rows.length, allIdentitiesMatched: true });
            if (q === 6) {
              const last = table.locator('tbody tr').last();
              finalItems.push({ board, kind: 'lastEpochState', text: await last.innerText(),
                image: await capture(last, `${board}-sources-last-epoch-state-360-light`, true) });
            }
          }
        }
        if (tab === 'Evidence flow') {
          const verification = panel.getByText('Verification', { exact: true }); await verification.click();
          requireTrue(await verification.evaluate(element => element.closest('details')?.open), 'Verification checkpoint did not open');
        }
        sourceViews.push({ board, tab, width: 360, colorScheme: 'light', ...await geometry(root) });
      }
      await disclosure.click();
      requireTrue(await disclosure.getAttribute('aria-expanded') === 'false', 'Sources did not close');
    }
    requireTrue(images.length === 15 && tableChecks.length === 27 &&
      tableChecks.reduce((n, row) => n + row.rows, 0) === 330 && finalItems.length === 6,
      'Scoped preview verification incomplete');
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    for (const key of deliveryOrder) {
      requireTrue(sha(path.join(family, receipt.fragments[key].path)) === receipt.fragments[key].sha256 &&
        sha(path.join(family, receipt.payloads[key].path)) === payloadPins[key], key + ' changed during QA');
    }
    requireTrue(sha(receiptPath) === receiptSha256 && sha(__filename) === scriptSha256, 'QA receipt/script changed');
    save('preview-verification.json', { schema: 'reclaim-relative-feedback-context-preview-qa-v1', passed: true,
      createdAt: new Date().toISOString(), renderReceiptSha256: receiptSha256, scriptSha256,
      fragments: receipt.fragments, payloads: receipt.payloads, deliveryOrder, tableRows: receipt.tableRows,
      images, chartViews, legendChecks, tableChecks, sourceViews, finalItems, errors,
      scope: 'Standalone sandboxed iframe; all three board charts at two widths and two themes, all 330 source rows including payoff distributions. Not live host insertion.',
      wrapperSha256: sha(wrapper), screenshotsVisuallyInspected: false, horizontalOverflow: false,
      sourceBytesUnchanged: true, actualMarketRuns: 0, analysisArtifactsModified: false });
    console.log(JSON.stringify({ passed: true, images: images.length, report: path.join(out, 'preview-verification.json'),
      reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (attemptCreated && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error); process.exitCode = 1;
});
