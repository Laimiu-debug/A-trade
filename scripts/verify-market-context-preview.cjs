// Bounded preview check for the market-context comparison and its source receipts.
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out = path.resolve(__dirname, '../artifacts/strategy-search/market-context-visuals-v1');
const revision = process.argv[2] || 'v1';
if (!/^v\d+$/.test(revision)) throw new Error('Expected a preview revision such as v4');
const suffix = revision === 'v1' ? '' : `-${revision}`;

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], views = [];
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 850 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    for (const name of ['comparison', 'chart-sources', 'findings-sources']) {
      const previewName = name === 'comparison' ? `${name}${suffix}` : name;
      await page.goto(pathToFileURL(path.join(out, `${previewName}-preview.html`)).href);
      const frame = page.frameLocator('iframe');
      const selector = name === 'comparison' ? '.data-inline-react-root' : '.sources-receipt';
      await frame.locator(selector).waitFor({ timeout: 15000 });
      if (name !== 'comparison') {
        await frame.locator('.receipt-disclosure').click();
        if (name === 'findings-sources') await frame.locator('.receipt-card-toggle').first().click();
      }
      for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
        await page.setViewportSize({ width, height: 850 });
        await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
        await page.waitForTimeout(250);
        const view = await frame.locator(selector).evaluate(root => ({
          innerWidth, contentWidth: root.scrollWidth, documentWidth: document.documentElement.scrollWidth,
          bars: root.querySelectorAll('.recharts-bar-rectangle').length,
          cards: root.querySelectorAll('.receipt-card').length, text: root.innerText.slice(0, 1200),
        }));
        if (view.contentWidth > view.innerWidth + 1 || view.documentWidth > view.innerWidth + 1)
          throw new Error('Horizontal overflow: ' + JSON.stringify({ name, ...view }));
        if (name === 'comparison' && view.bars !== 10) throw new Error('Expected ten data bars: ' + view.bars);
        if (name !== 'comparison' && view.cards !== (name === 'chart-sources' ? 1 : 4))
          throw new Error('Missing source cards');
        await page.screenshot({ path: path.join(out, `${name}${suffix}-${width}-${colorScheme}.png`), fullPage: true });
        views.push({ name, width, colorScheme, ...view });
      }
      if (name === 'comparison') {
        const button = frame.getByRole('button', { name: /^Toggle / }).first();
        await button.click();
        await page.waitForTimeout(200);
        if (await frame.locator('.recharts-bar-rectangle').count() !== 5) throw new Error('Legend toggle failed');
        await button.click();
        await page.waitForTimeout(200);
        if (await frame.locator('.recharts-bar-rectangle').count() !== 10) throw new Error('Legend restoration failed');
      }
    }
    if (errors.length) throw new Error(JSON.stringify(errors));
    const report = { passed: true, revision, scope: 'Standalone sandboxed iframe previews; not live host insertion.',
      chartSeries: 2, chartBars: 10, sourceCards: 5, legendToggleAndRestore: true, views, errors };
    fs.writeFileSync(path.join(out, `preview-verification${suffix}.json`), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
    console.log(JSON.stringify({ passed: true, views: views.length, chartSeries: 2, chartBars: 10, sourceCards: 5 }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
