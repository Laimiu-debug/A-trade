const { app, protocol } = require('electron');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-master-slips-smoke-'));
require('node:child_process').execFileSync(
  require('../desktop/business-service.cjs').pythonCommand(),
  ['-B', path.join(__dirname, 'finaltrade-fixture.py'), directory],
  { windowsHide: true },
);
app.setPath('userData', path.join(directory, 'profile'));
app.on('window-all-closed', () => {});
protocol.registerSchemesAsPrivileged([{ scheme: 'atrade', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

let win;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  const consoleErrors = [];
  try {
    win = await require('../desktop/runtime.cjs').createDesktop({
      dataDirectory: path.join(directory, 'workspace'),
      show: false,
      onWindow(w) {
        w.webContents.setBackgroundThrottling(false);
        w.webContents.on('console-message', (_e, level, message) => {
          if (level >= 2) consoleErrors.push(message);
        });
      },
    });
    const evaluate = code => win.webContents.executeJavaScript(code);
    const wait = async (fn, label, timeout = 30000) => {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        if (await fn()) return;
        await delay(120);
      }
      throw new Error('Timeout: ' + label + ' | ' + await evaluate('document.body.innerText.slice(-1500)'));
    };

    const fixture = JSON.parse(fs.readFileSync(path.join(directory, 'fixture.json'), 'utf8'));
    const cfg = await evaluate(`atradeDesktop.market('market.configure', { path: ${JSON.stringify(path.join(directory, 'market'))} })`);
    assert.equal(cfg.ok, true, JSON.stringify(cfg));
    const scan = await evaluate(`atradeDesktop.market('market.scan', {})`);
    assert.equal(scan.ok, true, JSON.stringify(scan));

    // Navigate to T11
    await evaluate('AT.navigate("T11"); true');
    await wait(() => evaluate('!!document.querySelector("[data-ms-page=T11]") && !!document.querySelector("[data-ms-action=new-master]")'), 'T11 empty onboarding');

    // Create a master profile through the real UI
    await evaluate('document.querySelector("[data-ms-action=new-master]").click(); true');
    await wait(() => evaluate('!!document.querySelector("[data-ms-master-form]")'), 'master form');
    await evaluate(`(() => {
      const set = (k, v) => { const el = document.querySelector('[data-ms-mfield="' + k + '"]'); el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); };
      set('name', '推土机测试大佬');
      set('styleTag', '连板+趋势回踩');
      set('initialCapital', '1000000');
      set('sourceDesc', '实盘连续交割单');
      document.querySelector('[data-ms-action=save-master]').click();
      return true;
    })()`);
    await wait(() => evaluate('!!document.querySelector(".ms-master-card.active")'), 'master created');

    // Seed realistic trades via real IPC into the created master
    const masterId = await evaluate('document.querySelector(".ms-master-card.active").dataset.msSelect');
    const bars0 = await evaluate(`atradeDesktop.market('market.bars', { symbol: 'sh600000', limit: 60 })`);
    assert.equal(bars0.ok, true);
    const rows0 = bars0.data.rows;
    const d1 = rows0[rows0.length - 12], d2 = rows0[rows0.length - 10], d3 = rows0[rows0.length - 8];
    const bars1 = await evaluate(`atradeDesktop.market('market.bars', { symbol: 'sh600007', limit: 60 })`);
    const rows1 = bars1.data.rows;
    const d4 = rows1[rows1.length - 6], d5 = rows1[rows1.length - 5];

    const commit = await evaluate(`atradeDesktop.market('market.masters.import.commit', ${JSON.stringify({
      masterId,
      source: 'ocr',
      imageCount: 2,
      rows: [
        { tradeDate: d1.date, tradeTime: '09:38:00', side: 'buy', code: '600000', quantity: 10000, price: d1.close },
        { tradeDate: d2.date, tradeTime: '10:05:00', side: 'buy', code: '600000', quantity: 5000, price: d2.close },
        { tradeDate: d3.date, tradeTime: '14:20:00', side: 'sell', code: '600000', quantity: 15000, price: +(d3.close * 1.04).toFixed(2) },
        { tradeDate: d4.date, tradeTime: '09:42:00', side: 'buy', code: '600007', quantity: 10000, price: d4.close },
        { tradeDate: d5.date, tradeTime: '09:33:00', side: 'sell', code: '600007', quantity: 10000, price: +(d5.close * 0.96).toFixed(2) },
      ],
    })})`);
    assert.equal(commit.ok, true, JSON.stringify(commit));
    assert.equal(commit.data.inserted, 5);

    // Refresh analysis through real UI
    await evaluate('document.querySelector("[data-ms-action=refresh-analysis]").click(); true');
    await wait(() => evaluate('!!document.querySelector(".ms-chart-svg")'), 'equity curve SVG rendered');

    // Cycle through all 6 tabs and verify key elements render cleanly
    for (const [tab, check] of [
      ['environment', 'document.querySelector("#main").innerText.includes("情绪周期四阶段")'],
      ['selection', 'document.querySelector("#main").innerText.includes("同日全市场正负样本对照")'],
      ['position', 'document.querySelector("#main").innerText.includes("完整交易战役闭环")'],
      ['coach', 'document.querySelector("#main").innerText.includes("不能实时盯盘专用改造方案")'],
      ['database', 'document.querySelector("#main").innerText.includes("原始交割单数据库")'],
      ['overview', '!!document.querySelector(".ms-chart-svg")'],
    ]) {
      await evaluate(`document.querySelector('[data-ms-tab="${tab}"]').click(); true`);
      await wait(() => evaluate(check), `tab ${tab}`);
    }

    // Verify Page AI context hook works cleanly on T11
    const aiCtx = await evaluate('AT.masterSlips.getAIContext()');
    assert.equal(aiCtx.pageId, 'T11');
    assert.equal(aiCtx.evidence.summary.closedEpisodes, 2);

    // Verify self-account comparison IPC works cleanly
    await evaluate('document.querySelector("[data-ms-tab=coach]").click(); true');
    await wait(() => evaluate('!!document.querySelector("[data-ms-action=compare-self]")'), 'compare-self button');
    await evaluate('document.querySelector("[data-ms-action=compare-self]").click(); true');
    await wait(() => evaluate('document.querySelector("#main").innerText.includes("已对比")'), 'self comparison table rendered');

    assert.deepEqual(consoleErrors, [], 'Renderer console errors: ' + consoleErrors.join(' | '));
    console.log('PASS 大佬交割单模块(T11)：Electron桌面端建档、SQLite入库、通达信行情对齐、曲线/6大视图切换、实盘对比与AI上下文全部通过');
  } finally {
    if (win) win.destroy();
    await delay(400);
    try { fs.rmSync(directory, { recursive: true, force: true }); } catch {}
  }
  app.exit(0);
}).catch(error => {
  console.error(error.stack || error);
  if (win) win.destroy();
  app.exit(1);
});
