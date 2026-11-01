// Bounded independent preview QA; reads existing fragments and never edits them.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'artifacts/strategy-search/relative-repair-visuals-v1/browser-qa-v3');
const vis = 'C:/Users/25647/.codex/visualizations/2026/10/15/01a13f1e-b930-7a00-8f49-552e7d86f79a';
const render = 'C:/Users/25647/.codex/plugins/cache/openai-bundled/visualize/1.0.49/skills/visualize/scripts/render.py';
const fragments = {
  comparison: { file: 'relative-repair-comparison-v1.html', sha256: 'b27fcbb0d16b275e4d0d5edcc49dc8d6be609adc28cbf1efc14b569caa1cd55c' },
  sources: { file: 'relative-repair-sources-v2.html', sha256: 'c63d710f75a005aa95c8b34e4d19f9192cbe58269a9403eb6a07a1d155f7ef70' },
};
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });

(async () => {
  fs.mkdirSync(out, { recursive: false });
  for (const [name, item] of Object.entries(fragments)) {
    const input = path.join(vis, item.file);
    if (sha(input) !== item.sha256) throw new Error('Fragment hash mismatch: ' + name);
    const output = path.join(out, `${name}-preview.html`);
    const run = spawnSync('python', ['-X', 'utf8', '-B', render, input, output], { encoding: 'utf8', timeout: 30000 });
    save(`${name}-wrapper-log.json`, { status: run.status, stdout: run.stdout, stderr: run.stderr });
    if (run.status !== 0 || !fs.existsSync(output)) throw new Error('Preview wrapper creation failed: ' + name);
  }
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], views = [];
  let legend, fourthCard, fourthCardData;
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 900 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    for (const name of Object.keys(fragments)) {
      await page.goto(pathToFileURL(path.join(out, `${name}-preview.html`)).href);
      const frame = page.frameLocator('iframe');
      const selector = name === 'comparison' ? '.data-inline-react-root' : '.sources-receipt';
      await frame.locator(selector).waitFor({ timeout: 15000 });
      if (name === 'sources') {
        await frame.locator('.receipt-disclosure').click();
        if (await frame.locator('.receipt-card').count() !== 4) throw new Error('Expected four Sources cards');
        const button = frame.locator('.receipt-card-toggle').nth(3);
        await button.click();
        fourthCard = { title: await frame.locator('.receipt-card').nth(3).getAttribute('aria-label'), expanded: await button.getAttribute('aria-expanded') };
        if (!fourthCard.title.includes('事件贡献与期末持仓') || fourthCard.expanded !== 'true')
          throw new Error('Fourth Sources card failed to expand: ' + JSON.stringify(fourthCard));
      }
      for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
        await page.waitForTimeout(300);
        const view = await frame.locator(selector).evaluate(element => ({
          innerWidth, contentWidth: element.scrollWidth, documentWidth: document.documentElement.scrollWidth,
          bars: element.querySelectorAll('.recharts-bar-rectangle').length,
          cards: element.querySelectorAll('.receipt-card').length,
          xLabels: Array.from(element.querySelectorAll('.recharts-cartesian-axis-tick-label text:not(.recharts-cartesian-axis-tick-value)')).map(e => {
            const r = e.getBoundingClientRect();
            const copy = e.cloneNode(true); copy.querySelectorAll('title').forEach(t => t.remove());
            return { text: copy.textContent, left: r.left, right: r.right };
          }),
          text: element.innerText.slice(0, 1800),
        }));
        if (view.contentWidth > view.innerWidth + 1 || view.documentWidth > view.innerWidth + 1)
          throw new Error('Horizontal overflow: ' + JSON.stringify({ name, width, colorScheme, ...view }));
        if (name === 'comparison') {
          if (view.bars !== 6) throw new Error('Expected six data bars: ' + view.bars);
          if (view.xLabels.map(r => r.text).join(',') !== '全部,配对,对照') throw new Error('Missing short x-axis labels');
          for (let i = 1; i < view.xLabels.length; i++)
            if (view.xLabels[i].left < view.xLabels[i - 1].right + 4) throw new Error('X-axis labels overlap');
        } else if (view.cards !== 4) throw new Error('Missing source cards');
        const screenshot = `${name}-${width}-${colorScheme}.png`;
        if (name === 'sources') await frame.locator(selector).screenshot({ path: path.join(out, screenshot) });
        else await page.screenshot({ path: path.join(out, screenshot), fullPage: true });
        views.push({ name, width, colorScheme, screenshot, screenshotSha256: sha(path.join(out, screenshot)), ...view });
      }
      if (name === 'comparison') {
        const button = frame.getByRole('button', { name: /^Toggle / }).first();
        const label = await button.getAttribute('aria-label');
        await button.click(); await page.waitForTimeout(200);
        const hidden = await frame.locator('.recharts-bar-rectangle').count();
        await button.click(); await page.waitForTimeout(200);
        const restored = await frame.locator('.recharts-bar-rectangle').count();
        if (hidden !== 3 || restored !== 6) throw new Error('Legend toggle/restore failed');
        legend = { label, hidden, restored, passed: true };
      } else {
        await page.setViewportSize({ width: 780, height: 900 });
        await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
        const card = frame.locator('.receipt-card').nth(3);
        await page.waitForTimeout(250);
        await card.screenshot({ path: path.join(out, 'fourth-card-overview-780-light.png') });
        await card.getByRole('tab', { name: 'Data preview', exact: true }).click();
        await page.waitForTimeout(250);
        fourthCardData = await card.evaluate(element => ({
          tables: element.querySelectorAll('table').length,
          headers: [...element.querySelectorAll('th')].map(e => e.textContent),
          tableRows: element.querySelectorAll('tbody tr').length,
          horizontalTableContainers: [...element.querySelectorAll('*')].filter(e =>
            e.scrollWidth > e.clientWidth + 2 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowX)).map(e =>
            ({ className: e.className, clientWidth: e.clientWidth, scrollWidth: e.scrollWidth })),
        }));
        if (fourthCardData.tables < 1 || fourthCardData.tableRows < 1) throw new Error('Fourth card has no visible data table');
        await card.screenshot({ path: path.join(out, 'fourth-card-data-780-light.png') });
        await card.evaluate(element => {
          for (const e of element.querySelectorAll('*'))
            if (e.scrollWidth > e.clientWidth + 2 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowX))
              e.scrollLeft = e.scrollWidth;
        });
        await page.waitForTimeout(100);
        await card.screenshot({ path: path.join(out, 'fourth-card-data-right-780-light.png') });
      }
    }
    if (errors.length) throw new Error(JSON.stringify(errors));
    for (const item of Object.values(fragments))
      if (sha(path.join(vis, item.file)) !== item.sha256) throw new Error('Fragment changed during preview');
    const report = { schema: 'relative-repair-preview-qa-v1', passed: true,
      scope: 'Independent standalone sandboxed iframe previews; not live host insertion.',
      createdAt: new Date().toISOString(), scriptSha256: sha(__filename), fragments,
      previewWrapperToolSha256: sha(render), chartBars: 6, sourceCards: 4,
      views, legend, fourthCard, fourthCardData, errors, horizontalOverflow: false, sourceBytesUnchanged: true,
      actualMarketRuns: 0, screenshotsVisuallyInspected: false };
    save('preview-verification.json', report);
    console.log(JSON.stringify({ passed: true, views: views.length, legend, fourthCard,
      report: path.join(out, 'preview-verification.json'), reportSha256: sha(path.join(out, 'preview-verification.json')) }));
  } finally { await browser.close(); }
})().catch(error => {
  if (fs.existsSync(out) && !fs.existsSync(path.join(out, 'failure.json')))
    save('failure.json', { message: error.message, stack: error.stack });
  console.error(error); process.exitCode = 1;
});
