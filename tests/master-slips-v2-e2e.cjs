const { app, protocol } = require('electron');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-master-unified-e2e-'));
execFileSync(
  require('../desktop/business-service.cjs').pythonCommand(),
  ['-B', path.join(__dirname, 'finaltrade-fixture.py'), directory],
  { windowsHide: true }
);

app.setPath('userData', path.join(directory, 'profile'));
// The test owns shutdown so closing its last window cannot mask a failed assertion.
app.on('window-all-closed', () => {});
protocol.registerSchemesAsPrivileged([
  { scheme: 'atrade', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

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
        w.webContents.on('console-message', event => {
          if (event.level === 'error') consoleErrors.push(event.message);
        });
      },
    });
    const evaluate = code => win.webContents.executeJavaScript(code);
    const wait = async (fn, label, timeout = 60000) => {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        if (await fn()) return;
        await delay(150);
      }
      throw new Error('Timeout: ' + label + ' | ' + await evaluate('document.body.innerText.slice(-1500)'));
    };

    const fixture = JSON.parse(fs.readFileSync(path.join(directory, 'fixture.json'), 'utf8'));
    const configured = await evaluate(
      `atradeDesktop.market('market.configure', { path: ${JSON.stringify(path.join(directory, 'market'))} })`
    );
    assert.equal(configured.ok, true, configured.error);

    // 1. T11: Pure Delivery-Slip Archive + 6 Masters Preset Install + Deep Links
    await evaluate('AT.navigate("T11"); true');
    await wait(() => evaluate('!!document.querySelector("[data-ms-action=install-presets]")'), 'T11 preset install button');
    await evaluate('document.querySelector("[data-ms-action=install-presets]").click(); true');
    await wait(() => evaluate('document.querySelectorAll(".ms-master-card").length === 6'), '6 preset masters installed');
    await wait(() => evaluate('!!document.querySelector(".ms-chart-svg")'), 'Wujiang archive equity curve SVG rendered');

    const hasLegacyBtTab = await evaluate('!!document.querySelector("[data-ms-tab=backtest]")');
    assert.equal(hasLegacyBtTab, false, 'Standalone backtest tab must be removed from T11');

    await evaluate('document.querySelector("[data-ms-tab=articles]").click(); true');
    await wait(
      () => evaluate('!!document.querySelector(".ms-report-box table") && document.querySelector(".ms-report-box").innerText.includes("东方海洋")'),
      'Deep Wujiang article & tables rendered'
    );

    // 2. Deep-link from T11 -> R12 (Strategy Run + Master Mode Tomorrow Prediction)
    await evaluate('document.querySelector("[data-ms-action=goto-strategy-signals]").click(); true');
    assert.equal(await evaluate('AT.state.page'), 'R12', 'T11 deep-link must navigate shell to R12');

    let frame;
    for (let i = 0; i < 400; i++) {
      frame = win.webContents.mainFrame.frames.find(f => f.url.startsWith('atrade://reference/'));
      if (frame && await frame.executeJavaScript('!!document.querySelector("h1")').catch(() => false)) break;
      await delay(100);
    }
    assert.ok(frame, 'Strategy iframe mounted on R12');

    const waitFrame = async (expr, label, timeout = 60000) => {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        if (await frame.executeJavaScript(expr).catch(() => false)) return;
        await delay(150);
      }
      throw new Error('Frame timeout: ' + label + ' | ' + await frame.executeJavaScript('document.body.innerText.slice(-1800)'));
    };

    await waitFrame(
      'document.querySelector("h1")?.innerText === "策略运行" && !!document.querySelector("[data-research=\\"master-plan-panel\\"]")',
      'R12 Strategy Run + MasterTraderPlanPanel rendered'
    );
    await waitFrame(
      'document.querySelector("[data-research=\\"master-plan-panel\\"]").innerText.includes("明日分时段执行时间表")',
      'Master Mode tomorrow battle plan generated in R12'
    );

    // Verify all 9 strategies (including 6 Master Modes) exist in API
    const catalogue = await frame.executeJavaScript('fetch("/api/strategies").then(r => r.json())');
    assert.equal(catalogue.items.length, 9, 'Expected 9 unified strategies');

    // Click manual refresh of tomorrow prediction
    await frame.executeJavaScript('document.querySelector("[data-research=\\"master-plan-refresh\\"]").click(); true');
    await waitFrame(
      '!document.querySelector("[data-research=\\"master-plan-refresh\\"]").classList.contains("ant-btn-loading") && document.querySelector("[data-research=\\"master-plan-panel\\"]").innerText.includes("如果要新开仓位怎么做")',
      'Tomorrow prediction refresh succeeded'
    );

    // 3. Jump from R12 Master Plan -> R03 Strategy Backtest with same Master Mode
    await frame.executeJavaScript(`(() => {
      const input = document.querySelector('input[aria-label="推演止损百分比"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '7.5');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.blur();
      return true;
    })()`);
    await frame.executeJavaScript(
      `localStorage.setItem('tdx-trend-backtest-form-v2', ${JSON.stringify(JSON.stringify({
        mode: 'full_market',
        strategy_id: 'mainline_halfway_v1',
        date_from: fixture.start,
        date_to: fixture.end,
        window_days: 60,
        min_score: 0,
        min_event_count: 0,
        max_symbols: 20,
        enable_advanced_analysis: false,
      }))}); true`
    );
    await frame.executeJavaScript('document.querySelector("[data-research=\\"master-plan-goto-backtest\\"]").click(); true');
    await waitFrame(
      'document.querySelector("h1")?.innerText === "策略回测" && document.querySelector("[data-research=\\"backtest-config\\"]")?.innerText.includes("大佬模式同源回测")',
      'Navigated to R03 Strategy Backtest with Master Mode banner'
    );
    await waitFrame(
      'JSON.parse(localStorage.getItem("tdx-trend-backtest-form-v2"))?.stop_loss === 0.075',
      'Backtest retains the stop loss selected in the rehearsal'
    );

    // Click 开始回测 and verify completion
    await waitFrame(
      '!![...document.querySelectorAll("button")].find(el => el.innerText.replace(/\\s/g, "") === "开始回测" && !el.disabled)',
      'Backtest submit button enabled'
    );
    await frame.executeJavaScript(`(() => {
      const btn = [...document.querySelectorAll('button')].find(el => el.innerText.replace(/\\s/g, '') === '开始回测' && !el.disabled);
      btn.click();
      return true;
    })()`);
    await waitFrame(
      `fetch('/api/backtest/tasks').then(r => r.json()).then(d => {
        const t = d.items?.[0];
        if (t?.status === 'failed') throw new Error(t.error);
        return t?.status === 'succeeded';
      })`,
      'Master Mode unified backtest succeeded in R03',
      90000
    );

    assert.deepEqual(consoleErrors, [], 'Renderer console errors: ' + consoleErrors.join(' | '));
    console.log('PASS 大佬模式统一架构全链路验收：T11纯交割单档案 -> R12策略运行与明日实战预测 -> R03同源策略回测全部零报错通过！');
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
