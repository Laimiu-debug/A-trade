const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { AIService, endpoint, imageParts } = require('../desktop/ai-service.cjs');
const crypto = require('node:crypto');

const key = crypto.randomBytes(32);
// Test-only encryptor; production uses Windows safeStorage.
const secure = { isEncryptionAvailable: () => true,
  encryptString(value) { const iv = crypto.randomBytes(12); const cipher = crypto.createCipheriv('aes-256-gcm', key, iv); const result = Buffer.concat([cipher.update(value), cipher.final()]); return Buffer.concat([iv, cipher.getAuthTag(), result]); },
  decryptString(value) { const cipher = crypto.createDecipheriv('aes-256-gcm', key, value.subarray(0, 12)); cipher.setAuthTag(value.subarray(12, 28)); return Buffer.concat([cipher.update(value.subarray(28)), cipher.final()]).toString(); },
};
async function setup(t, responder, options) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'atrade-ai-test-'));
  const received = [];
  const server = http.createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    received.push({ body: JSON.parse(body), headers: req.headers, url: req.url });
    responder(res, received.at(-1));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}/v1`;
  const service = new AIService(directory, secure, options);
  service.save({ target: 'text', baseUrl, apiKey: 'test-private-key', model: 'text-model', enabled: true });
  const saved = service.save({ target: 'vision', baseUrl, apiKey: 'test-vision-key', model: 'vision-model', enabled: true });
  assert.equal(saved.profiles.text.hasKey, true);
  assert.equal(saved.profiles.vision.hasKey, true);
  t.after(() => { service.close(); server.closeAllConnections(); server.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  return { service, received, directory, baseUrl };
}
const complete = (res, data) => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ choices: [{ message: { content: JSON.stringify(data) } }] })); };
const png = { name: 'trade.png', mimeType: 'image/png', dataBase64: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==' };

test('AI credentials are encrypted on disk and never returned; changing endpoint clears old key', async t => {
  const { service, directory, baseUrl } = await setup(t, res => complete(res, 'OK'));
  const file = fs.readFileSync(service.file, 'utf8');
  assert.ok(!file.includes('test-private-key'));
  assert.ok(!file.includes('test-vision-key'));
  assert.ok(!JSON.stringify(service.publicConfig()).includes('encryptedKey'));
  const restored = new AIService(directory, secure);
  assert.equal((await restored.dispatch('test', { target: 'text' })).ok, true);
  assert.equal((await restored.dispatch('test', { target: 'vision' })).ok, true);
  restored.save({ target: 'text', baseUrl, model: 'updated-model', enabled: true, apiKey: '' });
  assert.equal(restored.publicConfig().profiles.text.hasKey, true);
  const visionBefore = restored.publicConfig().profiles.vision;
  restored.save({ target: 'text', baseUrl: 'https://other.example/v1', model: 'updated-model', enabled: true });
  assert.equal(restored.publicConfig().profiles.text.hasKey, false);
  assert.deepEqual(restored.publicConfig().profiles.vision, visionBefore);
  assert.equal((await restored.dispatch('test', { target: 'text' })).ok, false);
  assert.equal((await restored.dispatch('test', { target: 'vision' })).ok, true);
});

test('screenshot recognition preserves missing date/fees and sends no account context', async t => {
  const { service, received } = await setup(t, res => complete(res, { rows: [{ type: 'buy', code: '000001', quantity: 100, price: 10 }], warnings: [] }));
  const result = await service.dispatch('recognize', { images: [png], accountId: 'private-account' });
  assert.equal(result.ok, true);
  assert.equal(result.data.rows[0].date, null);
  assert.equal(result.data.rows[0].commission, null);
  assert.equal(result.data.rows[0].feesConfirmed, false);
  assert.equal(received[0].body.model, 'vision-model');
  assert.equal(received[0].body.store, false);
  assert.equal(received[0].url, '/v1/chat/completions');
  assert.ok(!JSON.stringify(received[0].body).includes('private-account'));
  const dated = await service.dispatch('recognize', { images: [png], defaultDate: '2026-10-09' });
  assert.equal(dated.data.rows[0].date, '2026-10-09');
  assert.equal(dated.data.rows[0].importKey, result.data.rows[0].importKey, 'dedupe identity is image based, not model response based');
  assert.ok(dated.data.rows[0].warnings.some(item => item.includes('手动补充')));
});

test('review returns separate provisional draft and bounds suggested scores', async t => {
  const { service } = await setup(t, res => complete(res, { summary: 'test', fields: { lessons: 'evidence' }, suggestedScores: { 'risk.event': { score: 9, comment: 'missing evidence' }, 'entry.trigger': { score: 3, comment: 'documented' } } }));
  const payload = { review: { date: '2026-10-09', scores: { 'risk.event': 1 } }, context: {} };
  const result = await service.dispatch('review', payload);
  assert.equal(result.ok, true);
  assert.equal(result.data.draft.suggestedScores['risk.event'].score, null);
  assert.equal(result.data.draft.suggestedScores['entry.trigger'].score, 3);
  assert.equal(payload.review.scores['risk.event'], 1);
  assert.equal(result.data.promptVersion, 'account-assistant-v1');
});

test('OCR identity stays with the same trade when the model reorders a partially imported batch', async t => {
  let count = 0;
  const first = { type: 'buy', code: '000001', quantity: 100, price: 10 };
  const second = { type: 'buy', code: '600000', quantity: 100, price: 11 };
  const { service } = await setup(t, res => complete(res, { rows: ++count === 1 ? [first, second, second] : [{ ...second, name: 'different wording' }, first, second] }));
  const a = (await service.dispatch('recognize', { images: [png] })).data;
  const b = (await service.dispatch('recognize', { images: [png] })).data;
  assert.deepEqual(a.rows.map(row => [row.code, row.importKey]), b.rows.map(row => [row.code, row.importKey]));
  assert.notEqual(a.rows[1].importKey, a.rows[2].importKey, 'identical legitimate fills retain distinct occurrence identities');
});

test('AI errors do not echo provider content or credentials', async t => {
  const { service } = await setup(t, res => { res.statusCode = 401; res.end('test-private-key raw-provider-error'); });
  const result = await service.dispatch('test', { target: 'text' });
  assert.equal(result.ok, false);
  assert.match(result.error, /HTTP 401/);
  assert.ok(!result.error.includes('test-private-key'));
  assert.ok(!result.error.includes('raw-provider-error'));
});

test('redirects refuse to forward credentials and request timeouts are recoverable', async t => {
  const { service, received } = await setup(t, res => { res.statusCode = 302; res.setHeader('Location', '/stolen'); res.end(); });
  assert.equal((await service.dispatch('test', { target: 'text' })).ok, false);
  assert.equal(received.length, 1);
  const slow = await setup(t, () => {}, { timeoutMs: 30 });
  assert.match((await slow.service.dispatch('test', { target: 'text' })).error, /超时/);
});

test('malformed model JSON and unsupported pictures fail without saving records', async t => {
  const { service } = await setup(t, res => res.end(JSON.stringify({ choices: [{ message: { content: 'no JSON' } }] })));
  assert.equal((await service.dispatch('recognize', { images: [png] })).ok, false);
  assert.throws(() => imageParts([{ ...png, mimeType: 'image/jpeg' }]));
  assert.throws(() => imageParts(Array(5).fill(png)));
  assert.throws(() => endpoint('http://example.com/v1'));
  assert.throws(() => endpoint('https://example.com/v1?key=secret'));
  assert.throws(() => endpoint('https://user:secret@example.com/v1'));
  assert.equal(endpoint('https://example.com/v1/chat/completions/'), 'https://example.com/v1');
});

test('text and vision use their own host, path, model and credentials across restart', async t => {
  const requests = [];
  const { directory, service } = await setup(t, res => complete(res, 'unused'));
  const profiles = {
    text: { baseUrl: 'https://text-provider.example/text/v1', model: 'reasoning-only', apiKey: 'private-text-only' },
    vision: { baseUrl: 'https://image-provider.example/vision/v2', model: 'image-reader', apiKey: 'private-image-only' },
  };
  for (const [target, profile] of Object.entries(profiles)) service.save({ target, ...profile, enabled: true });
  const restored = new AIService(directory, secure, { fetch: async (url, options) => {
    const body = JSON.parse(options.body);
    requests.push({ url, body, authorization: options.headers.Authorization, redirect: options.redirect });
    const data = body.model === profiles.vision.model ? { rows: [{ type: 'buy', code: '000001', quantity: 100, price: 10 }] } : { summary: 'text draft' };
    return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(data) } }] }));
  } });
  assert.equal((await restored.dispatch('review', { review: { date: '2026-10-09' }, context: {}, target: 'vision' })).ok, true);
  assert.equal((await restored.dispatch('recognize', { images: [png], target: 'text' })).ok, true);
  for (const target of ['text', 'vision']) {
    const result = await restored.dispatch('test', { target });
    assert.equal(result.ok, true);
    assert.equal(result.data.target, target);
    assert.equal(result.data.model, profiles[target].model);
  }
  assert.equal(requests.length, 4);
  for (const [index, target] of ['text', 'vision', 'text', 'vision'].entries()) {
    const profile = profiles[target];
    assert.equal(requests[index].url, `${profile.baseUrl}/chat/completions`);
    assert.equal(requests[index].body.model, profile.model);
    assert.equal(requests[index].authorization, `Bearer ${profile.apiKey}`);
    assert.equal(requests[index].redirect, 'error');
    assert.equal(requests[index].body.store, false);
  }
  assert.ok(Array.isArray(requests[1].body.messages[1].content), 'recognition sends the original images to the vision profile');
  const publicJSON = JSON.stringify(restored.publicConfig());
  for (const profile of Object.values(profiles)) assert.ok(!publicJSON.includes(profile.apiKey));
  assert.ok(!publicJSON.includes('encryptedKey'));
  assert.equal(restored.publicConfig().version, 2);
});

test('disabling or incompletely configuring a profile never falls back to the other profile', async t => {
  const { service, received, baseUrl } = await setup(t, res => complete(res, { rows: [], summary: 'draft' }));
  service.save({ target: 'text', baseUrl, model: 'text-model', enabled: false });
  const disabledText = await service.dispatch('review', { review: {}, context: {} });
  assert.equal(disabledText.ok, false);
  assert.match(disabledText.error, /设置.*文本/);
  assert.equal((await service.dispatch('recognize', { images: [png] })).ok, true);
  assert.equal(received.length, 1);
  assert.equal(received[0].body.model, 'vision-model');
  service.save({ target: 'text', baseUrl, model: 'text-model', enabled: true });
  service.save({ target: 'vision', baseUrl, model: 'vision-model', enabled: false });
  assert.equal((await service.dispatch('review', { review: {}, context: {} })).ok, true);
  assert.equal((await service.dispatch('recognize', { images: [png] })).ok, false);
  service.save({ target: 'vision', baseUrl, model: '', enabled: true });
  const missingModel = await service.dispatch('recognize', { images: [png] });
  assert.equal(missingModel.ok, false);
  assert.match(missingModel.error, /设置.*图片/);
  service.save({ target: 'vision', baseUrl, model: 'vision-model', enabled: true, clearKey: true });
  assert.equal((await service.dispatch('recognize', { images: [png] })).ok, false);
  assert.equal((await service.dispatch('review', { review: {}, context: {} })).ok, true);
  assert.equal(service.publicConfig().profiles.text.hasKey, true);
  assert.equal(service.publicConfig().profiles.vision.hasKey, false);
  assert.deepEqual(received.map(request => request.body.model), ['vision-model', 'text-model', 'text-model']);
});

test('each profile owns its key, including path changes and explicit replacements', async t => {
  const { service, baseUrl } = await setup(t, res => complete(res, 'OK'));
  const initial = JSON.parse(fs.readFileSync(service.file, 'utf8'));
  service.save({ target: 'vision', baseUrl: `${baseUrl}/different`, model: 'vision-model', enabled: true });
  let saved = JSON.parse(fs.readFileSync(service.file, 'utf8'));
  assert.equal(saved.profiles.vision.encryptedKey, '');
  assert.deepEqual(saved.profiles.text, initial.profiles.text);
  service.save({ target: 'vision', baseUrl, model: 'vision-model', enabled: true, apiKey: 'replacement-image-key' });
  saved = JSON.parse(fs.readFileSync(service.file, 'utf8'));
  assert.equal(secure.decryptString(Buffer.from(saved.profiles.vision.encryptedKey, 'base64')), 'replacement-image-key');
  assert.deepEqual(saved.profiles.text, initial.profiles.text);
  service.save({ target: 'text', baseUrl, model: 'text-model', enabled: true, clearKey: true });
  assert.equal(service.publicConfig().profiles.text.hasKey, false);
  assert.equal(service.publicConfig().profiles.vision.hasKey, true);
});

test('v1 settings migrate both effective routes in memory and become v2 only on save', async t => {
  const { service, directory, baseUrl } = await setup(t, res => complete(res, 'OK'));
  for (const visionModel of ['legacy-image', '']) {
    const old = JSON.stringify({ version: 1, baseUrl, model: 'legacy-text', visionModel, enabled: true,
      encryptedKey: secure.encryptString('legacy-private-key').toString('base64') });
    fs.writeFileSync(service.file, old);
    const migrated = new AIService(directory, secure);
    assert.deepEqual(migrated.publicConfig().profiles, {
      text: { baseUrl, model: 'legacy-text', enabled: true, hasKey: true },
      vision: { baseUrl, model: visionModel || 'legacy-text', enabled: true, hasKey: true },
    });
    for (const target of ['text', 'vision']) assert.equal((await migrated.dispatch('test', { target })).ok, true);
    assert.equal(fs.readFileSync(service.file, 'utf8'), old, 'reading/testing v1 preserves the original file byte for byte');
    migrated.save({ target: 'text', baseUrl: 'https://new-text.example/v1', model: 'next-text', enabled: true });
    const saved = JSON.parse(fs.readFileSync(service.file, 'utf8'));
    assert.equal(saved.version, 2);
    assert.equal(saved.profiles.text.encryptedKey, '');
    assert.equal(secure.decryptString(Buffer.from(saved.profiles.vision.encryptedKey, 'base64')), 'legacy-private-key');
    assert.ok(!JSON.stringify(saved).includes('legacy-private-key'));
    const restored = new AIService(directory, secure);
    assert.equal(restored.publicConfig().profiles.vision.model, visionModel || 'legacy-text');
    assert.equal((await restored.dispatch('test', { target: 'vision' })).ok, true);
  }
});

test('unknown targets and unsafe endpoints fail without changing either saved profile', async t => {
  const { service } = await setup(t, res => complete(res, 'OK'));
  const before = fs.readFileSync(service.file, 'utf8');
  for (const target of [undefined, null, '', 'other', '__proto__']) {
    assert.equal((await service.dispatch('test', { target })).ok, false);
    assert.equal((await service.dispatch('config.save', { target, baseUrl: 'https://example.com', model: 'x', enabled: true })).ok, false);
  }
  for (const target of ['text', 'vision']) {
    for (const baseUrl of ['http://example.com', 'https://example.com?key=secret', 'https://name:secret@example.com']) {
      assert.equal((await service.dispatch('config.save', { target, baseUrl, model: 'x', enabled: true })).ok, false);
    }
  }
  assert.equal(fs.readFileSync(service.file, 'utf8'), before);
});

test('encryption failure preserves both saved profiles and emits no sensitive error detail', async t => {
  const { service, directory, baseUrl } = await setup(t, res => complete(res, 'OK'));
  const before = fs.readFileSync(service.file, 'utf8');
  const unavailable = new AIService(directory, { ...secure, isEncryptionAvailable: () => false });
  const failed = await unavailable.dispatch('config.save', { target: 'vision', baseUrl, model: 'vision-new', enabled: true, apiKey: 'replacement-secret' });
  assert.equal(failed.ok, false);
  assert.match(failed.error, /密钥保护不可用/);
  assert.equal(fs.readFileSync(service.file, 'utf8'), before);
  assert.deepEqual(unavailable.publicConfig(), service.publicConfig());
  const undecryptable = new AIService(directory, { ...secure, decryptString() { throw new Error('secret-provider-data'); } });
  const result = await undecryptable.dispatch('test', { target: 'vision' });
  assert.equal(result.ok, false);
  assert.match(result.error, /无法解密/);
  assert.ok(!result.error.includes('secret-provider-data'));
});
