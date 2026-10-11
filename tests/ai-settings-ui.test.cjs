const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const empty = () => ({baseUrl: '', model: '', enabled: false, hasKey: false});
const initial = () => ({version: 2, profiles: {
  text: {baseUrl: 'https://text.example/v1', model: 'text-old', enabled: true, hasKey: true},
  vision: {baseUrl: 'https://vision.example/v1', model: 'vision-old', enabled: true, hasKey: true},
}});
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture({config = initial(), request} = {}) {
  const listeners = {}, forms = new Map(), observers = [], calls = [];
  let saved = structuredClone(config), focused = null;
  const node = () => ({dataset: {}, hidden: false, textContent: '', attributes: {}, classList: {toggle() {}}, setAttribute(name, value) {this.attributes[name] = value;}, removeAttribute(name) {delete this.attributes[name];}});
  const document = {
    addEventListener(name, callback) {(listeners[name] ||= []).push(callback);},
    querySelector(selector) {
      const match = /^form\[data-ai-profile="(\w+)"\]$/.exec(selector);
      return match ? forms.get(match[1]) || null : null;
    },
    querySelectorAll() {return [...forms.values()];},
  };
  const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  const AT = {escape, icon: () => '', button: (text, _icon, attrs) => `<button ${attrs}>${text}</button>`,
    isDesktop: true, state: {page: 'C07'}, groups: [{id: 'C', pages: [{id: 'C07', title: 'AI'}]}], head: () => '', view: () => ''};
  const bridge = {bootstrap: {ai: config}, async ai(method, payload) {
    calls.push({method, payload: structuredClone(payload)});
    if (request) return request(method, payload);
    if (method === 'test') return {ok: true, data: {message: 'Fixture connection passed.'}};
    const old = saved.profiles[payload.target] || empty();
    saved.profiles[payload.target] = {baseUrl: payload.baseUrl, model: payload.model, enabled: payload.enabled,
      hasKey: !!payload.apiKey || (!payload.clearKey && payload.baseUrl === old.baseUrl && old.hasKey)};
    return {ok: true, data: structuredClone(saved)};
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preview/ai-settings.js'), 'utf8'), {
    AT, document, window: {atradeDesktop: bridge}, structuredClone, URL,
    MutationObserver: class {constructor(callback) {observers.push(callback);} observe() {}},
  });
  function dispatch(name, event) {for (const callback of listeners[name] || []) callback(event);}
  function mount() {
    const html = AT.view('C07');
    forms.clear();
    for (const target of ['text', 'vision']) {
      const form = {...node(), dataset: {aiProfile: target}, elements: {}, nodes: {}, buttons: [],
        querySelector(selector) {return this.nodes[selector] ||= node();},
        querySelectorAll(selector) {return selector.startsWith('input') ? Object.values(this.elements) : this.buttons;},
        matches: selector => selector === 'form[data-ai-profile]',
      };
      for (const name of ['baseUrl', 'model', 'apiKey', 'enabled', 'clearKey']) {
        form.elements[name] = {...node(), name, value: '', checked: false, closest: () => form,
          focus() {focused = {target, name};}};
      }
      for (const action of ['save', 'test', 'discard']) {
        const button = {...node(), dataset: {aiAction: action}, closest: () => form};
        form.buttons.push(button); form.nodes[`[data-ai-action="${action}"]`] = button;
      }
      forms.set(target, form);
    }
    observers.forEach(callback => callback());
    return html;
  }
  function edit(target, name, value) {
    const input = forms.get(target).elements[name];
    if (typeof value === 'boolean') input.checked = value; else input.value = value;
    dispatch('input', {target: input});
  }
  function click(target, action) {
    const button = forms.get(target).nodes[`[data-ai-action="${action}"]`];
    dispatch('click', {target: {closest: () => button}, preventDefault() {}, stopPropagation() {}});
  }
  mount();
  return {AT, calls, forms, mount, edit, click, unmount: () => forms.clear(), get focused() {return focused;}};
}

test('AI drafts survive navigation, block close, and never serialize an unsaved key', async () => {
  const f = fixture();
  f.edit('text', 'baseUrl', 'https://new.example/v1');
  f.edit('text', 'model', 'new-text');
  f.edit('text', 'apiKey', 'fixture-session-secret');
  f.edit('text', 'enabled', false);
  f.unmount();
  assert.equal(f.AT.aiSettings.hasUnsaved(), true);
  assert.equal(await f.AT.aiSettings.flush(), false);
  const html = f.mount(), inputs = f.forms.get('text').elements;
  assert.equal(inputs.baseUrl.value, 'https://new.example/v1');
  assert.equal(inputs.model.value, 'new-text');
  assert.equal(inputs.enabled.checked, false);
  assert.equal(inputs.apiKey.value === 'fixture-session-secret', true);
  assert.equal(html.includes('fixture-session-secret'), false);
  assert.equal(JSON.stringify(f.AT.state).includes('fixture-session-secret'), false);
  assert.equal(JSON.stringify(f.AT.aiSettings.config).includes('fixture-session-secret'), false);
  assert.match(html, /有未保存的更改/);
});

test('reverting a field removes dirty state; explicit discard clears all draft fields', async () => {
  const f = fixture();
  f.edit('text', 'model', 'changed');
  f.edit('text', 'model', 'text-old');
  assert.equal(f.AT.aiSettings.hasUnsaved(), false);
  assert.equal(await f.AT.aiSettings.flush(), true);
  assert.match(f.mount(), /与已保存配置一致/);
  f.edit('text', 'apiKey', 'fixture-session-secret');
  f.edit('text', 'clearKey', true);
  assert.equal(f.forms.get('text').elements.apiKey.value, '');
  assert.equal(f.forms.get('text').elements.enabled.checked, false);
  f.click('text', 'discard');
  assert.equal(f.AT.aiSettings.hasUnsaved(), false);
  assert.equal(f.forms.get('text').elements.apiKey.value, '');
  assert.equal(f.forms.get('text').elements.enabled.checked, true);
  assert.equal(f.forms.get('text').elements.clearKey.checked, false);
});

test('saveAll saves a hidden draft without testing, and clears the session key', async () => {
  const f = fixture();
  f.edit('text', 'model', 'new-text');
  f.edit('text', 'apiKey', 'fixture-session-secret');
  f.unmount();
  assert.equal(await f.AT.aiSettings.saveAll(), true);
  assert.deepEqual(f.calls.map(call => call.method), ['config.save']);
  assert.equal(f.calls[0].payload.apiKey === 'fixture-session-secret', true);
  assert.equal(f.AT.aiSettings.config.profiles.text.model, 'new-text');
  assert.equal(f.AT.aiSettings.hasUnsaved(), false);
  f.mount();
  assert.equal(f.forms.get('text').elements.apiKey.value, '');
});

test('invalid hidden drafts and failed writes remain recoverable and block close', async () => {
  let fail = true;
  const f = fixture({request: async (_method, payload) => fail ? {ok: false, error: 'Fixture write failed.'} :
    {ok: true, data: {version: 2, profiles: {...initial().profiles, [payload.target]: {...payload, hasKey: true}}}}});
  f.edit('text', 'baseUrl', 'not-an-address');
  f.unmount();
  assert.equal(await f.AT.aiSettings.saveAll(), false);
  assert.equal(f.calls.length, 0);
  assert.match(f.mount(), /完整的 API 地址/);
  f.edit('text', 'baseUrl', 'https://text.example/v1');
  f.edit('text', 'model', 'new-text');
  assert.equal(await f.AT.aiSettings.saveAll(), false);
  assert.equal(await f.AT.aiSettings.flush(), false);
  assert.equal(f.forms.get('text').elements.model.value, 'new-text');
  fail = false;
  assert.equal(await f.AT.aiSettings.saveAll(), true);
});

test('concurrent independent saves merge only their own profile and prevent duplicate submits', async () => {
  const resolves = {};
  const f = fixture({request: (_method, payload) => new Promise(resolve => {resolves[payload.target] = resolve;})});
  f.edit('text', 'model', 'new-text'); f.edit('vision', 'model', 'new-vision');
  const saving = f.AT.aiSettings.saveAll();
  await tick();
  f.click('text', 'save');
  assert.equal(f.calls.length, 2);
  resolves.text({ok: true, data: {version: 2, profiles: {...initial().profiles, text: {...initial().profiles.text, model: 'new-text'}}}});
  await tick();
  assert.equal(f.AT.aiSettings.hasUnsaved(), true, 'the other profile remains unsaved');
  resolves.vision({ok: true, data: {version: 2, profiles: {...initial().profiles, vision: {...initial().profiles.vision, model: 'new-vision'}}}});
  assert.equal(await saving, true);
  assert.equal(f.AT.aiSettings.config.profiles.text.model, 'new-text');
  assert.equal(f.AT.aiSettings.config.profiles.vision.model, 'new-vision');
});

test('connection results are separate from saved and enabled state, and become stale after editing', async () => {
  let connected = false;
  const f = fixture({request: async (method, payload) => method === 'test' ?
    connected ? {ok: true, data: {message: 'Fixture passed.'}} : {ok: false, error: 'Fixture connection failed.'} :
    {ok: true, data: {version: 2, profiles: {...initial().profiles, [payload.target]: {...payload, hasKey: true}}}}});
  f.click('text', 'test'); await tick();
  assert.equal(f.AT.aiSettings.hasUnsaved(), false);
  assert.equal(f.AT.aiSettings.ready('text'), true);
  assert.equal(await f.AT.aiSettings.flush(), true);
  let html = f.mount();
  assert.match(html, /最近检测未通过 · \d{4}/);
  assert.match(html, /配置已保存，测试未通过/);
  connected = true;
  f.click('text', 'test'); await tick();
  assert.match(f.mount(), /最近检测通过/);
  f.edit('text', 'model', 'new-text');
  assert.match(f.mount(), /对应已保存配置/);
  assert.equal(await f.AT.aiSettings.saveAll(), true);
  html = f.mount();
  assert.match(html, /配置已改变，请重新检测/);
  assert.equal(f.calls.filter(call => call.method === 'test').length, 2);
});

test('discardAll works after leaving the AI page without writing any configuration', () => {
  const f = fixture();
  f.edit('text', 'apiKey', 'fixture-session-secret');
  f.edit('vision', 'model', 'new-vision');
  f.unmount();
  assert.equal(f.AT.aiSettings.discardAll(), true);
  assert.equal(f.AT.aiSettings.hasUnsaved(), false);
  assert.equal(f.calls.length, 0);
  assert.equal(f.mount().includes('new-vision'), false);
  assert.equal(f.forms.get('text').elements.apiKey.value, '');
});
