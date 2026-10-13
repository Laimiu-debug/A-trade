const {app, protocol, dialog, session} = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const directory = process.argv[2];
if (!directory) throw new Error('Missing isolated page AI smoke directory');
const root = path.resolve(__dirname, '..');
const dataDirectory = path.join(directory, 'workspace');
const fixtureDirectory = path.join(directory, 'tdx-fixture');
const guardDirectory = path.join(directory, 'network-guard');
const testKey = 'page-ai-isolated-test-key';
const requests = [], plans = [], errors = [], blocked = [];
let win, server;
app.setPath('userData', path.join(directory, 'profile'));
protocol.registerSchemesAsPrivileged([{scheme:'atrade', privileges:{standard:true, secure:true, supportFetchAPI:true}}]);
dialog.showMessageBox = async (_window, options) => {throw new Error(`Unexpected dialog: ${options.message}`);};

const evaluate = code => win.webContents.executeJavaScript(code).catch(error => {throw new Error(`${error.message} [${code.slice(0, 180)}]`);});
async function wake() {
  await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true});
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
}
async function until(expression, label, timeout = 18000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await wake();
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  throw new Error(`${label}: ${await evaluate('(document.querySelector("#page-ai-panel")?.innerText||"")+document.querySelector("main")?.innerText.slice(-1800)')}`);
}
const click = selector => evaluate(`(()=>{const el=[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.getClientRects().length);if(!el||el.disabled)throw new Error('Missing active '+${JSON.stringify(selector)});el.click();})()`);
const action = name => click(`[data-page-ai="${name}"]`);
const question = value => evaluate(`(()=>{const el=document.querySelector('#page-ai-question');if(!el||el.disabled||el.readOnly)throw new Error('Question is unavailable');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const visiblePanel = '!document.querySelector("#page-ai-panel")?.hidden && !!document.querySelector("#page-ai-panel")?.getClientRects().length';
const answerText = () => evaluate('Array.from(document.querySelectorAll("#page-ai-panel .page-ai-answer"),el=>el.textContent).join("\\n")');
async function open() {if (!await evaluate(visiblePanel)) await action('open'); await until(visiblePanel, '打开页面 AI');}
async function navigate(page) {
  await evaluate(`AT.navigate(${JSON.stringify(page)});true`);
  await until(`AT.state.page===${JSON.stringify(page)}&&!!document.querySelector('[data-analysis-page="${page}"]')`, '打开 '+page);
}
async function requestCount(count, label) {
  const start = Date.now();
  while (Date.now() - start < 15000) {
    if (requests.length >= count) {assert.equal(requests.length, count, label); return requests.at(-1);}
    await wake();
    await new Promise(resolve => setTimeout(resolve, 60));
  }
  throw new Error('Local provider did not receive '+label);
}
async function expectAnswer(marker) {
  await until(`Array.from(document.querySelectorAll('.page-ai-answer')).some(el=>el.textContent.includes(${JSON.stringify(marker)}))&&!document.querySelector('#page-ai-question').readOnly`, '回答 '+marker);
}
function respond(res, text) {
  if (res.destroyed) return;
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify({id:'isolated-chat', choices:[{message:{role:'assistant', content:text}, finish_reason:'stop'}], usage:{prompt_tokens:10, completion_tokens:10, total_tokens:20}}));
}
async function startProvider() {
  server = http.createServer(async (req, res) => {
    try {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const record = {url:req.url, body:JSON.parse(raw), authorization:req.headers.authorization, closed:false};
      requests.push(record);
      res.on('close', () => {record.closed = true;});
      const plan = plans.shift();
      if (!plan) {res.statusCode = 500; res.end('Unexpected automatic provider request'); return;}
      if (plan.type === 'error') {res.statusCode = 500; res.end(JSON.stringify({error:{message:'isolated provider failure'}})); return;}
      if (plan.type === 'hold') {plan.release = () => respond(res, plan.text); plan.record = record; return;}
      respond(res, plan.text);
    } catch (error) {errors.push(error.message); res.statusCode = 500; res.end('Test fixture failed');}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}/v1`;
}
async function fixture() {
  const dates = [];
  for (let day = new Date('2026-05-04T00:00:00Z'); dates.length < 90; day = new Date(day.getTime() + 86400000)) {
    if (day.getUTCDay() > 0 && day.getUTCDay() < 6) dates.push(day.toISOString().slice(0, 10));
  }
  await fs.mkdir(path.join(fixtureDirectory, 'vipdoc', 'sh', 'lday'), {recursive:true});
  await fs.mkdir(path.join(fixtureDirectory, 'T0002', 'hq_cache'), {recursive:true});
  const entries = [['600001','PAGE AI LADDER'], ['600900','PAGE AI TREND']];
  const tnf = Buffer.alloc(50 + entries.length * 360);
  for (const [index, [code, name]] of entries.entries()) {
    tnf.write(code, 50 + index * 360, 'ascii'); tnf.write(name, 50 + index * 360 + 31, 'ascii');
    const bytes = Buffer.alloc(dates.length * 32); let close = 1000;
    for (let i = 0; i < dates.length; i++) {
      const open = close, rate = code === '600001' && i >= dates.length - 3 ? .10 : .004;
      close = Math.round(close * (1 + rate)); const at = i * 32, volume = i >= dates.length - 3 ? 350000 : 100000;
      bytes.writeUInt32LE(Number(dates[i].replaceAll('-', '')), at);
      bytes.writeUInt32LE(open, at + 4); bytes.writeUInt32LE(Math.max(open, close) + 1, at + 8);
      bytes.writeUInt32LE(Math.min(open, close) - 1, at + 12); bytes.writeUInt32LE(close, at + 16);
      bytes.writeFloatLE(close / 100 * volume, at + 20); bytes.writeUInt32LE(volume, at + 24);
    }
    await fs.writeFile(path.join(fixtureDirectory, 'vipdoc', 'sh', 'lday', `sh${code}.day`), bytes);
  }
  await fs.writeFile(path.join(fixtureDirectory, 'T0002', 'hq_cache', 'shs.tnf'), tnf);
  await fs.mkdir(guardDirectory, {recursive:true});
  await fs.writeFile(path.join(guardDirectory, 'sitecustomize.py'), "import urllib.request\ndef blocked(self, request, *args, **kwargs):\n raise RuntimeError('Public network is disabled in isolated page AI smoke')\nurllib.request.OpenerDirector.open = blocked\n");
  process.env.PYTHONPATH = [guardDirectory, root].join(path.delimiter);
  return dates;
}
async function screenshot(name, width = 1480) {
  win.setSize(width, 1000); await wake();
  const [w, h] = win.getSize(); win.setSize(w + 1, h); await wake(); win.setSize(w, h); await wake();
  await fs.mkdir(path.join(root, 'artifacts'), {recursive:true});
  await fs.writeFile(path.join(root, 'artifacts', name), (await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true})).toPNG());
  const fit = await evaluate('(()=>{const panel=document.querySelector("#page-ai-panel"),rect=panel.getBoundingClientRect();return {left:rect.left,right:rect.right,width:innerWidth,scroll:panel.scrollWidth,client:panel.clientWidth};})()');
  assert.ok(fit.left >= -1 && fit.right <= fit.width + 1 && fit.scroll <= fit.client + 1, `AI 面板适配 ${width}px：${JSON.stringify(fit)}`);
}
async function key(keyCode, modifiers = [], character = '') {
  win.webContents.focus();
  win.webContents.sendInputEvent({type:'keyDown', keyCode, modifiers});
  if (character) win.webContents.sendInputEvent({type:'char', keyCode:character, modifiers});
  win.webContents.sendInputEvent({type:'keyUp', keyCode, modifiers});
  await wake();
}
function evidence(record, page, date) {
  assert.equal(record.url, '/v1/chat/completions');
  assert.equal(record.authorization, `Bearer ${testKey}`);
  assert.equal(record.body.model, 'page-ai-smoke-model');
  assert.equal(record.body.messages[0].role, 'system');
  assert.match(JSON.stringify(record.body.messages[0].content), /页面|数据|证据/);
  const text = JSON.stringify(record.body.messages);
  assert.ok(text.includes(page), `请求含当前页 ${page}`);
  assert.ok(text.includes(date), '请求保留真实数据截止日');
  assert.ok(!text.includes(testKey) && !text.includes(dataDirectory), '上下文不携密钥或本机数据目录');
  return text;
}

app.whenReady().then(async () => {
  const dates = await fixture(), latest = dates.at(-1), baseUrl = await startProvider();
  session.defaultSession.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']}, (details, callback) => {blocked.push(details.url); callback({cancel:true});});
  win = await require('../desktop/runtime.cjs').createDesktop({dataDirectory, show:false, onWindow(window) {
    window.webContents.setBackgroundThrottling(false);
    window.webContents.on('console-message', (_event, details) => {if (details.level === 'error') errors.push(details.message);});
    window.webContents.on('preload-error', (_event, _file, error) => errors.push(error.message));
  }});
  await evaluate('window.__pageAiErrors=[];window.addEventListener("error",event=>__pageAiErrors.push(event.message));window.addEventListener("unhandledrejection",event=>__pageAiErrors.push(String(event.reason)));');
  assert.equal((await evaluate(`atradeDesktop.market('market.configure',{path:${JSON.stringify(fixtureDirectory)}})`)).ok, true);
  assert.equal((await evaluate("atradeDesktop.market('market.scan')")).ok, true);
  await evaluate('AT.marketUI.refresh()');
  await navigate('T08');
  await until('document.querySelectorAll(".leader-research-candidate").length>0', '真实本地日线计算连板候选');
  await open();
  assert.equal(requests.length, 0, '打开入口不发送网络请求');
  assert.equal(await evaluate('document.activeElement===document.querySelector("#page-ai-question")'), true, '打开后聚焦问题输入框');
  await until('!!document.querySelector("[data-page-ai-suggestion]")', '页面专属快捷问题');
  await click('[data-page-ai-suggestion]');
  assert.ok(await evaluate('document.querySelector("#page-ai-question").value.length>0'));
  assert.equal(requests.length, 0, '快捷问题只填入草稿');
  assert.equal(await evaluate('document.querySelector("[data-page-ai=send]").disabled'), true, '未配置时禁止发送');
  await action('settings');
  await until('AT.state.page==="C07"&&!!document.querySelector("[data-ai-profile=text]")', '从未配置提示跳到文本 AI 设置');
  await evaluate(`(()=>{const form=document.querySelector('[data-ai-profile=text]');for(const [name,value] of Object.entries({baseUrl:${JSON.stringify(baseUrl)},model:'page-ai-smoke-model',apiKey:${JSON.stringify(testKey)}})){form.elements[name].value=value;form.elements[name].dispatchEvent(new Event('input',{bubbles:true}));}form.elements.enabled.checked=true;form.elements.enabled.dispatchEvent(new Event('input',{bubbles:true}));form.querySelector('[data-ai-action=save]').click();})()`);
  await until('AT.aiSettings.ready("text")&&!AT.aiSettings.hasUnsaved()', '保存隔离文本服务配置');
  assert.equal(requests.length, 0, '保存配置不隐式测试服务');
  assert.ok(!(await fs.readFile(path.join(dataDirectory, 'ai-settings.json'), 'utf8')).includes(testKey), '测试密钥经实际 safeStorage 加密');

  await navigate('T08'); await open();
  await until(`document.querySelector('.page-ai-context')?.textContent.includes('连板龙头')&&document.querySelector('.page-ai-context')?.textContent.includes(${JSON.stringify(latest)})`, '上下文显示真实页面和截止日');
  await action('context');
  const firstQuestion = '解释当前连板候选和数据日期。';
  const firstAnswer = 'FIRST_T08：**连板候选仅来自本页数据。**\n<img src="https://invalid.example/pixel" onerror="window.__unsafePageAi=1">\n<script>window.__unsafePageAi=1</script>';
  plans.push({type:'ok', text:firstAnswer});
  await question(firstQuestion); await action('send');
  const first = await requestCount(1, '首次连板问答'); await expectAnswer('FIRST_T08');
  const firstEvidence = evidence(first, 'T08', latest);
  assert.match(firstEvidence, /600001|PAGE AI LADDER/, '请求包含真实连板候选');
  assert.ok(firstEvidence.includes(firstQuestion));
  assert.equal(await evaluate('document.querySelectorAll("#page-ai-panel img,#page-ai-panel script,#page-ai-panel iframe").length'), 0, '回答里的 HTML 不生成可执行元素');
  assert.equal(await evaluate('window.__unsafePageAi===undefined'), true);
  assert.equal(await evaluate('document.querySelector("#page-ai-question").value'), '', '成功后清空草稿');

  const followQuestion = '只根据刚才的连板数据继续说明。';
  plans.push({type:'ok', text:'FOLLOW_T08：这是当前页面会话的追问回答。'});
  await question(followQuestion); await evaluate('document.querySelector("#page-ai-question").focus()');
  await key('Enter', [], '\r');
  const follow = await requestCount(2, '键盘追问'); await expectAnswer('FOLLOW_T08');
  assert.ok(JSON.stringify(follow.body.messages).includes('FIRST_T08') && JSON.stringify(follow.body.messages).includes('assistant'), '追问的业务数据包携上轮助手回答和角色');
  assert.ok(JSON.stringify(follow.body.messages).includes(firstQuestion), '追问携上轮问题');
  await question('保留的连板草稿'); await action('close'); await open();
  assert.equal(await evaluate('document.querySelector("#page-ai-question").value'), '保留的连板草稿');
  assert.ok((await answerText()).includes('FOLLOW_T08'), '关闭重开保留本页问答');
  assert.equal(requests.length, 2);
  await screenshot('page-ai-safety-rendering.png');

  await navigate('T04');
  await until('document.querySelectorAll(".analysis-tools-table tbody tr").length===2', '真实趋势筛选'); await open();
  await until('document.querySelector(".page-ai-context")?.textContent.includes("趋势龙头")', '切页更新上下文');
  assert.ok(!(await answerText()).includes('FIRST_T08'), '趋势页不显示连板会话');
  assert.equal(await evaluate('document.querySelector("#page-ai-question").value'), '', '趋势页不继承连板草稿');
  plans.push({type:'ok', text:'FIRST_T04：这是趋势页独立会话。'});
  await question('比较本页两只趋势候选。'); await action('send');
  const trend = await requestCount(3, '趋势页问答'); await expectAnswer('FIRST_T04');
  const trendEvidence = evidence(trend, 'T04', latest);
  assert.ok(!trendEvidence.includes('FIRST_T08') && !trendEvidence.includes(firstQuestion), '跨页请求不混入历史对话');
  await screenshot('page-ai-trends-820.png', 820);

  await navigate('T08'); await open();
  await until('document.querySelector(".page-ai-context")?.textContent.includes("连板龙头")', '返回连板页');
  assert.ok((await answerText()).includes('FOLLOW_T08') && !(await answerText()).includes('FIRST_T04'));
  const held = {type:'hold', text:'STOPPED_T08：这条迟到回答不得显示。'}; plans.push(held);
  await question('这个回答稍后停止。'); await action('send');
  await requestCount(4, '可停止请求');
  assert.equal(await evaluate('document.querySelector("#page-ai-question").readOnly'), true, '请求中锁定草稿');
  await action('stop');
  await until('!document.querySelector("#page-ai-question").readOnly', '停止立即恢复输入');
  plans.push({type:'ok', text:'RECOVERED_T08：停止后可以再次发送。'});
  await question('停止后重新回答。'); await action('send');
  await requestCount(5, '停止后重发'); await expectAnswer('RECOVERED_T08');
  held.release(); await wake();
  assert.ok(!(await answerText()).includes('STOPPED_T08'), '已停止请求不能写入迟到答案');
  assert.equal(held.record.closed, true, '停止取消真实 HTTP 连接');

  const retryQuestion = '失败后保留这条问题用于重试。';
  plans.push({type:'error'}); await question(retryQuestion); await action('send'); await requestCount(6, '服务失败');
  await until('!document.querySelector("#page-ai-question").readOnly&&document.querySelector(".page-ai-status[data-tone=error]")?.textContent.includes("HTTP 500")', '失败提示和输入恢复');
  assert.equal(await evaluate('document.querySelector("#page-ai-question").value'), retryQuestion);
  plans.push({type:'ok', text:'RETRY_T08：重试成功。'}); await action('send');
  const retried = await requestCount(7, '保留草稿重试'); await expectAnswer('RETRY_T08');
  assert.ok(JSON.stringify(retried.body.messages).includes(retryQuestion));
  await action('clear');
  assert.equal(await answerText(), '', '新话题清空当前页问答');
  await question('第一行'); await evaluate('document.querySelector("#page-ai-question").focus()'); await key('Enter', ['shift'], '\r');
  assert.ok((await evaluate('document.querySelector("#page-ai-question").value')).includes('\n'), 'Shift+Enter 只换行');
  assert.equal(requests.length, 7);
  await evaluate('document.querySelector("#page-ai-question").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",isComposing:true,bubbles:true,cancelable:true}));true');
  assert.equal(requests.length, 7, '输入法确认不触发发送');
  await key('Escape'); await until('document.querySelector("#page-ai-panel").hidden', 'Escape 关闭面板');
  assert.equal(await evaluate('document.activeElement===document.querySelector("[data-page-ai=open]")'), true, '关闭恢复入口焦点');
  await open();
  plans.push({type:'ok', text:'NEW_TOPIC：没有旧问答的新话题。'});
  await question('新话题问题。'); await action('send');
  const fresh = await requestCount(8, '清空后的新话题'); await expectAnswer('NEW_TOPIC');
  assert.ok(!JSON.stringify(fresh.body.messages).includes('FIRST_T08') && !JSON.stringify(fresh.body.messages).includes('RETRY_T08'));
  const closedReply = {type:'hold', text:`## 连板与趋势解读（模拟验收）\n**数据范围**：截至 ${latest} 的本地合成日线，仅用于验证页面问答功能。\n\n### 当前结构\n- 最高连板高度为 3 板，当前最高梯队只有 PAGE AI LADDER 一只，不能据此判断市场整体持续转强。\n- 昨日连板样本今天 1 / 1 晋级，显示为 100%。分母只有一只，样本很小，需要继续跟踪同一批股票。\n- 趋势候选为 2 只，连续 60 日样本也为 2 只。成交占比反映这份合成样本的活跃结构，不代表资金净流入。\n\n### 接下来观察\n继续比较最高板能否延续、低位梯队是否补充，以及昨日高标断板后的表现；若接力下降且趋势成交占比同步回落，需要重新评估此前判断。\n\n**资料限制**：本地未复权日线没有封单、炸板次数和盘中回封信息，不能据此推断真实交易可成交性。`};
  plans.push(closedReply); await question('关闭面板后继续等待这条回答。'); await action('send');
  await requestCount(9, '关闭期间等待回答'); await action('close');
  assert.equal(await evaluate('document.querySelector("#page-ai-panel").hidden'), true);
  closedReply.release();
  await until('!document.querySelector("[data-page-ai=open]").classList.contains("page-ai-working")', '关闭期间回答完成后清除处理中标记');
  assert.equal(await evaluate('document.querySelector("#page-ai-panel").hidden'), true, '后台回答不会强行打开面板');
  await open(); await expectAnswer('连板与趋势解读（模拟验收）');
  assert.ok((await answerText()).includes('NEW_TOPIC'), '重开保留此前回答并显示关闭期间的新回答');
  assert.equal(await evaluate('document.querySelector("#page-ai-question").value'), '', '关闭期间成功发送的草稿在重开时清空');
  assert.equal(requests.length, 9, '重新打开不重复提交请求');
  if (await evaluate('!document.querySelector(".page-ai-evidence").hidden')) await action('context');
  await screenshot('page-ai-ladder.png');
  assert.equal(await evaluate('AT.persist()'), true);
  const stored = await fs.readFile(path.join(dataDirectory, 'workspace-state.json'), 'utf8');
  assert.ok(!stored.includes(firstQuestion) && !stored.includes('FIRST_T08') && !stored.includes('NEW_TOPIC'), '页面问答不写入持久工作区');
  assert.deepEqual(await evaluate('__pageAiErrors'), []); assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  console.log('PASS page AI: real T08/T04 evidence, explicit provider requests, isolated histories, safe answer rendering, native keyboard, drafts, stop, HTTP errors and retry, memory-only chats, desktop/narrow screenshots');
  server.closeAllConnections(); server.close(); win.close(); setTimeout(() => app.exit(0), 300);
}).catch(error => {console.error(error.stack);server?.closeAllConnections();server?.close();if(win&&!win.isDestroyed())win.destroy();app.exit(1);});
