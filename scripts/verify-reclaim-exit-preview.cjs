// Scoped standalone-iframe presentation QA. No signals, accounts or analytics run.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const family = path.resolve(__dirname, '../artifacts/strategy-search/reclaim-exit-transfer-v1');
const wrapper = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.50/skills/visualize/scripts/render.py';
const python = 'C:/Users/25647/AppData/Local/Programs/Python/Python313/python.exe';
const argv = process.argv.slice(2);
if (argv.length % 2) throw new Error('Expected --receipt-sha SHA [--attempt 0001]');
const args = Object.fromEntries(Array.from({ length: argv.length / 2 }, (_, i) => [argv[2 * i], argv[2 * i + 1]]));
if (Object.keys(args).some(k => !['--receipt-sha', '--attempt'].includes(k))) throw new Error('Unknown argument');
if (!/^[a-f0-9]{64}$/.test(args['--receipt-sha'] || '')) throw new Error('A reviewed render receipt SHA256 is required');
const attempt = args['--attempt'] || '0001';
if (!/^\d{4}$/.test(attempt)) throw new Error('Attempt must be four digits');
const out = path.join(family, 'browser-qa', 'attempt-' + attempt);
const expectedRows = { primary: [[18, 18, 18, 18, 6, 18, 36, 72, 144, 18]], secondary: [[6, 12, 12]] };
const expectedKeys = ['primary-chart', 'primary-sources', 'secondary-chart', 'secondary-sources'];
const titles = { primary: '去掉第五日退出：净收益差', secondary: '前十日低点替代百分比跟踪：净收益差' };
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
const requireTrue = (value, message) => { if (!value) throw new Error(message); };
let created = false;

async function geometry(locator) {
  const b = await locator.evaluate(element => {
    const box = element.getBoundingClientRect();
    return { width: innerWidth, left: box.left, right: box.right, contentWidth: element.scrollWidth,
      documentWidth: document.documentElement.scrollWidth };
  });
  requireTrue(b.left >= -1 && b.right <= b.width + 1 && b.contentWidth <= b.width + 1 && b.documentWidth <= b.width + 1,
    'Horizontal overflow: ' + JSON.stringify(b));
  return b;
}

(async () => {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.mkdirSync(out); created = true; // Never reuse an earlier attempt.
  const scriptSha256 = sha(__filename), receiptPath = path.join(family, 'render-receipt.json');
  const renderReceiptSha256 = sha(receiptPath);
  requireTrue(renderReceiptSha256 === args['--receipt-sha'], 'Render receipt changed');
  const receipt = read(receiptPath), fragments = receipt.fragments, payloads = {};
  requireTrue(JSON.stringify(Object.keys(fragments)) === JSON.stringify(expectedKeys) &&
    JSON.stringify(receipt.deliveryOrder) === JSON.stringify(expectedKeys), 'Four reviewed ordered fragments required');
  requireTrue(JSON.stringify(receipt.tableRows) === JSON.stringify(expectedRows), 'Complete table matrix differs');
  for (const key of expectedKeys) {
    const fragment = fragments[key], p = receipt.payloads[key];
    const expectedFragment = 'inline/reclaim-exit-transfer-' + key + '.html';
    requireTrue(fragment.path === expectedFragment && p.path === key + '.json', 'Unexpected presentation path');
    requireTrue(sha(path.join(family, fragment.path)) === fragment.sha256 &&
      sha(path.join(family, p.path)) === p.sha256, 'Presentation binding differs: ' + key);
    requireTrue(fs.statSync(path.join(family, fragment.path)).size < 1_000_000, 'Oversized fragment');
    payloads[key] = read(path.join(family, p.path));
    const result = spawnSync(python, ['-X', 'utf8', '-B', wrapper, path.join(family, fragment.path), path.join(out, key + '-preview.html')],
      { encoding: 'utf8', timeout: 30000 });
    save(key + '-wrapper-log.json', { status: result.status, stdout: result.stdout, stderr: result.stderr });
    requireTrue(result.status === 0, 'Standalone wrapper failed: ' + key);
  }
  for (const kind of ['primary', 'secondary']) {
    const chart = payloads[kind + '-chart'], sources = payloads[kind + '-sources'];
    requireTrue(chart.rows.length === 6 && sources.items.length === 1 &&
      JSON.stringify(sources.items.map(i => i.queries.map(q => q.rows.length))) === JSON.stringify(expectedRows[kind]),
      'Chart-scoped rows differ: ' + kind);
    requireTrue(chart.title === titles[kind] && chart.chart.xLabel === '累计净收益差（百分点）' && chart.chart.showValues === true,
      'Visible comparison/units/direct values required');
    requireTrue(['平均仓位差（百分点）', '候选估值不可靠日', '对照估值不可靠日'].every(x => chart.columns.includes(x)),
      'Exposure or valuation field missing');
    requireTrue(chart.rows.every(r => typeof r[chart.chart.y] === 'number' && Number.isFinite(r[chart.chart.y])), 'Finite actual differences required');
  }
  const primary = payloads['primary-sources'].items[0].queries;
  requireTrue(new Set(primary[0].rows.map(r => r.contrastLabel)).size === 3 &&
    new Set(primary[1].rows.map(r => r.policyLabel)).size === 3, 'Full three-policy evidence omitted');
  for (const field of ['triggered', 'notTriggered', 'unknown', 'skippedExistingExit'])
    requireTrue(primary[3].columns.some(c => c.field === field), 'Exit evidence state omitted: ' + field);
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], images = [], chartViews = [], sourceViews = [], tableChecks = [], finalItems = [], legendChecks = [];
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(10000);
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    const capture = async (locator, name, whole = false) => {
      let targetBounds = null;
      if (whole) {
        await locator.scrollIntoViewIfNeeded(); targetBounds = await locator.boundingBox();
        requireTrue(targetBounds && targetBounds.height > 0 && targetBounds.y < 900 && targetBounds.y + targetBounds.height > 0,
          'Target outside viewport: ' + name);
      }
      const file = path.join(out, name + '.png');
      if (whole) await page.screenshot({ path: file, fullPage: false }); else await locator.screenshot({ path: file });
      const item = { path: path.relative(family, file).replaceAll('\\', '/'), sha256: sha(file),
        capture: whole ? 'page-viewport' : 'chart-element', ...(targetBounds ? { targetBounds } : {}) };
      images.push(item); return item;
    };
    for (const kind of ['primary', 'secondary']) {
      const payload = payloads[kind + '-chart'];
      for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
        await page.setViewportSize({ width, height: 900 }); await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
        await page.goto(pathToFileURL(path.join(out, kind + '-chart-preview.html')).href);
        const frame = page.frameLocator('iframe'), chart = frame.locator('.data-inline-react-root');
        await chart.waitFor({ timeout: 15000 }); await chart.locator('svg.recharts-surface').first().waitFor();
        await page.waitForTimeout(200);
        const bounds = await geometry(chart);
        const painted = await chart.evaluate(element => ({
          bars: element.querySelectorAll('.recharts-bar-rectangle').length,
          nonzeroBars: [...element.querySelectorAll('.recharts-bar-rectangle')].filter(e => {
            const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0;
          }).length,
          svgText: [...element.querySelectorAll('svg text')].map(e => e.textContent),
        }));
        const nonzero = payload.rows.filter(r => r[payload.chart.y] !== 0), zero = payload.rows.length - nonzero.length;
        requireTrue(painted.bars === nonzero.length && painted.nonzeroBars === nonzero.length, 'Native nonzero mark count differs');
        requireTrue(await chart.getByText(payload.title, { exact: true }).isVisible(), 'Comparison title missing');
        const visibleCategories = await chart.locator('svg text').evaluateAll(nodes => nodes.filter(n => n.querySelector('title'))
          .map(n => [...n.childNodes].filter(x => x.nodeType === Node.TEXT_NODE).map(x => x.textContent).join('')));
        for (const label of ['主板', '创业板', '科创板']) requireTrue(visibleCategories.includes(label), 'Category truncated/missing: ' + label);
        const axis = chart.locator('.chart-axis-label');
        requireTrue(await axis.count() === 1 && await axis.isVisible() && (await axis.innerText()).trim() === '累计净收益差（百分点）',
          'Actual percentage-point axis title missing');
        const axisBounds = await axis.evaluate(e => {
          const b = e.getBoundingClientRect(), p = e.closest('.data-inline-react-root').getBoundingClientRect();
          return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, parentLeft: p.left, parentRight: p.right,
            parentTop: p.top, parentBottom: p.bottom, scrollWidth: e.scrollWidth, clientWidth: e.clientWidth };
        });
        requireTrue(axisBounds.left >= axisBounds.parentLeft - 1 && axisBounds.right <= axisBounds.parentRight + 1 &&
          axisBounds.top >= axisBounds.parentTop - 1 && axisBounds.bottom <= axisBounds.parentBottom + 1 &&
          axisBounds.scrollWidth <= axisBounds.clientWidth + 1, 'Axis title clipped');
        const toggles = frame.getByRole('button', { name: /^Toggle / });
        requireTrue(await toggles.count() === 2, 'Two periods required');
        for (const p of ['2022-2023', '2024-2025']) requireTrue(await toggles.filter({ hasText: p }).count() === 1, 'Period legend missing');
        chartViews.push({ kind, width, colorScheme, ...bounds, ...painted, reviewedRows: 6, exactZeroRows: zero,
          visibleCategories, axisBounds, image: await capture(chart, `${kind}-chart-${width}-${colorScheme}`) });
        if (width === 780 && colorScheme === 'light') {
          await toggles.first().click(); await page.waitForTimeout(150);
          const hidden = await chart.locator('.recharts-bar-rectangle').count();
          await toggles.first().click(); await page.waitForTimeout(150);
          const restored = await chart.locator('.recharts-bar-rectangle').count();
          requireTrue(hidden === nonzero.filter(r => r[payload.chart.series] !== payload.chart.seriesOrder[0]).length &&
            restored === nonzero.length, 'Period legend hide/restore failed');
          legendChecks.push({ kind, passed: true, hidden, restored });
        }
      }
    }
    for (const kind of ['primary', 'secondary']) {
      const sources = payloads[kind + '-sources'];
      await page.setViewportSize({ width: 360, height: 900 }); await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
      await page.goto(pathToFileURL(path.join(out, kind + '-sources-preview.html')).href);
      const frame = page.frameLocator('iframe'), root = frame.locator('.sources-receipt');
      await root.waitFor({ timeout: 15000 }); const disclosure = root.locator('.receipt-disclosure');
      await disclosure.click(); requireTrue(await disclosure.getAttribute('aria-expanded') === 'true', 'Sources did not open');
      requireTrue(await root.locator('.receipt-card').count() === 1, 'One scoped finding required');
      const card = root.locator('.receipt-card');
      requireTrue(await card.getAttribute('data-open') === 'true' && await card.locator('.receipt-card-toggle').count() === 0,
        'Single finding state incorrect');
      for (const tab of ['Overview', 'Data preview', 'Evidence flow']) {
        const button = card.getByRole('tab', { name: tab, exact: true }); await button.click();
        requireTrue(await button.getAttribute('aria-selected') === 'true', 'Sources tab failed');
        const panel = card.getByRole('tabpanel'); requireTrue((await panel.innerText()).trim().length > 0, 'Empty evidence panel');
        if (tab === 'Overview') {
          const last = panel.locator('.receipt-qualification-list li').last(); requireTrue(await last.count() === 1, 'Caveat missing');
          finalItems.push({ kind, type: 'caveat', text: await last.innerText(),
            image: await capture(last, `${kind}-sources-last-caveat-360-light`, true) });
        }
        if (tab === 'Data preview') {
          const queries = sources.items[0].queries;
          requireTrue(await panel.locator('table').count() === queries.length, 'Evidence tables missing');
          for (let q = 0; q < queries.length; q++) {
            const query = queries[q], table = panel.locator('table').nth(q);
            const fields = ['period', 'boardLabel', 'contrastLabel', 'policyLabel', 'intervalLabel']
              .filter(f => query.columns.some(c => c.field === f));
            requireTrue(fields.includes('period') && fields.includes('boardLabel'), 'Evidence identity fields missing');
            const indexes = fields.map(f => query.columns.findIndex(c => c.field === f));
            const seen = [], pages = [], preceding = queries.slice(0, q).filter(v => v.rows.length > 50).length;
            const pager = query.rows.length > 50 ? panel.locator('.receipt-pagination').nth(preceding) : null;
            if (pager) requireTrue(await pager.getByRole('button', { name: 'Previous page', exact: true }).isDisabled(), 'Preview not at first page');
            for (let offset = 0; offset < query.rows.length; offset += 50) {
              const count = Math.min(50, query.rows.length - offset);
              requireTrue(await table.locator('tbody tr').count() === count, 'Visible row count differs');
              seen.push(...await table.locator('tbody tr').evaluateAll((rows, columns) => rows.map(r => columns.map(i => r.querySelectorAll('td')[i].innerText.trim())), indexes));
              pages.push(count);
              if (offset + count < query.rows.length) {
                await pager.getByRole('button', { name: 'Next page', exact: true }).click();
                await pager.getByRole('status').filter({ hasText: `Rows ${offset + 51}–${Math.min(offset + 100, query.rows.length)} of ${query.rows.length}` }).waitFor();
              }
            }
            if (pager) requireTrue(await pager.getByRole('button', { name: 'Next page', exact: true }).isDisabled(), 'Final page inaccessible');
            requireTrue(JSON.stringify(seen) === JSON.stringify(query.rows.map(r => fields.map(f => String(r[f])))), 'Full evidence identities/order differ');
            tableChecks.push({ kind, queryId: query.id, rows: seen.length, pages, allIdentitiesMatched: true });
            if (kind === 'primary' && ['exit-evidence', 'policy-coverage', 'phases'].includes(query.id)) {
              const last = table.locator('tbody tr').last();
              finalItems.push({ kind, type: query.id, text: await last.innerText(),
                image: await capture(last, `${kind}-sources-${query.id}-last-row-360-light`, true) });
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
          requireTrue(await verification.evaluate(e => e.closest('details')?.open) === true, 'Verification does not expand');
        }
        sourceViews.push({ kind, width: 360, tab, ...await geometry(root) });
      }
      await disclosure.click(); requireTrue(await disclosure.getAttribute('aria-expanded') === 'false', 'Sources did not close');
    }
    requireTrue(images.length === 11 && tableChecks.length === 13 && finalItems.length === 5 && legendChecks.length === 2, 'QA coverage incomplete');
    requireTrue(errors.length === 0, 'Browser errors: ' + JSON.stringify(errors));
    for (const key of expectedKeys) requireTrue(sha(path.join(family, fragments[key].path)) === fragments[key].sha256 &&
      sha(path.join(family, receipt.payloads[key].path)) === receipt.payloads[key].sha256, 'Presentation changed during QA');
    requireTrue(sha(receiptPath) === renderReceiptSha256 && sha(__filename) === scriptSha256, 'Receipt/script changed during QA');
    const report = { schema: 'reclaim-exit-transfer-preview-qa-v1', passed: true, createdAt: new Date().toISOString(),
      scope: 'Standalone sandboxed iframe: both charts in three widths/themes, both Sources, all13 tables including all phase pages. Not live host insertion.',
      renderReceiptSha256, fragments, payloads: receipt.payloads, scriptSha256, wrapperSha256: sha(wrapper),
      originalTableRows: expectedRows, images, chartViews, sourceViews, tableChecks, finalItems, legendChecks, errors,
      horizontalOverflow: false, sourceBytesUnchanged: true, screenshotsVisuallyInspected: false,
      actualMarketRuns: 0, analysisArtifactsModified: false };
    save('preview-verification.json', report);
    console.log(JSON.stringify({ passed: true, images: images.length, report: path.join(out, 'preview-verification.json'),
      reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (created && !fs.existsSync(path.join(out, 'preview-verification.json')) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { passed: false, message: error.message, stack: error.stack });
  console.error(error); process.exitCode = 1;
});
