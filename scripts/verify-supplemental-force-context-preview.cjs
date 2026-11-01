// Bounded check of the three generated fragments in standalone iframe previews.
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('C:/Users/25647/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out = path.resolve(__dirname, '../artifacts/strategy-search/supplemental-force-context-visuals-v1');

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const errors = [], views = [];
  try {
    const page = await browser.newPage({ viewport: { width: 780, height: 850 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    for (const name of ['curve', 'curve-sources', 'findings-sources']) {
      await page.goto(pathToFileURL(path.join(out, `${name}-preview.html`)).href);
      const frame = page.frameLocator('iframe');
      const selector = name === 'curve' ? '.data-inline-react-root' : '.sources-receipt';
      await frame.locator(selector).waitFor({ timeout: 15000 });
      if (name !== 'curve') {
        await frame.locator('.receipt-disclosure').click();
        if (name === 'findings-sources') await frame.locator('.receipt-card-toggle').first().click();
      }
      for (const [width, colorScheme] of [[780, 'light'], [360, 'light'], [780, 'dark']]) {
        await page.setViewportSize({ width, height: 850 });
        await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
        await page.waitForTimeout(250);
        const view = await frame.locator(selector).evaluate(root => ({
          innerWidth, contentWidth: root.scrollWidth, documentWidth: document.documentElement.scrollWidth,
          lines: root.querySelectorAll('.recharts-line-curve').length,
          cards: root.querySelectorAll('.receipt-card').length, text: root.innerText.slice(0, 1200),
        }));
        if (view.contentWidth > view.innerWidth + 1 || view.documentWidth > view.innerWidth + 1)
          throw new Error('Horizontal overflow: ' + JSON.stringify({ name, ...view }));
        if (name === 'curve' && view.lines !== 4) throw new Error('Expected four real chart lines');
        if (name !== 'curve' && view.cards !== (name === 'curve-sources' ? 1 : 4))
          throw new Error('Missing source cards');
        await page.screenshot({ path: path.join(out, `${name}-${width}-${colorScheme}.png`), fullPage: true });
        views.push({ name, width, colorScheme, ...view });
      }
      if (name === 'curve') {
        const button = frame.getByRole('button', { name: /^Toggle / }).first();
        await button.click();
        await page.waitForTimeout(200);
        if (await frame.locator('.recharts-line-curve').count() !== 3) throw new Error('Legend toggle failed');
        await button.click();
        await page.waitForTimeout(200);
        if (await frame.locator('.recharts-line-curve').count() !== 4) throw new Error('Legend restoration failed');
      }
    }
    if (errors.length) throw new Error(JSON.stringify(errors));
    const report = { passed: true, scope: 'Standalone sandboxed iframe previews; not live host insertion.',
      chartSeries: 4, sourceCards: 5, legendToggleAndRestore: true, views, errors };
    fs.writeFileSync(path.join(out, 'preview-verification.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
    console.log(JSON.stringify({ passed: true, views: views.length, chartSeries: 4, sourceCards: 5 }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
