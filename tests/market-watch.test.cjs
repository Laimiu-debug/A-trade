const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function client(handler) {
  const requests = [], events = [], listeners = new Map();
  const AT = {isDesktop:true,escape:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};
  const document = {addEventListener(type, fn){listeners.set(type,fn);}, dispatchEvent(event){events.push(event);}};
  const window = {AT,atradeDesktop:{async market(method,payload){requests.push([method,JSON.parse(JSON.stringify(payload))]);return handler(method,payload);}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/market-watch.js'),'utf8'), {AT,window,document,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}});
  return {AT,requests,events};
}
test('quick watch appends only the new reason and preserves backend-owned status, tags and prior note', async () => {
  const old = {symbol:'sh600001',note:'原观察',status:'holding',tags:['趋势']};
  const app = client(async (_method,payload) => ({ok:true,data:{...old,note:old.note+'\n\n'+payload.appendNote}}));
  const result = await app.AT.marketWatch.saveObservation('sh600001', '  等待放量确认  ');
  assert.deepEqual(app.requests, [['market.watchlist.save',{symbol:'sh600001',appendNote:'等待放量确认'}]]);
  assert.equal(result.note, '原观察\n\n等待放量确认');
  assert.equal(app.events[0].type, 'market-watchlist-changed');
});
test('empty quick observation never clears an existing note or status', async () => {
  const app = client(async () => ({ok:true,data:{symbol:'sh600001'}}));
  await app.AT.marketWatch.saveObservation('sh600001','');
  assert.deepEqual(app.requests[0][1], {symbol:'sh600001'});
});
test('invalid and failed observations do not broadcast a successful change', async () => {
  const app = client(async () => ({ok:false,error:'原备注已保留'}));
  await assert.rejects(app.AT.marketWatch.saveObservation('600001','x'), /代码无效/);
  await assert.rejects(app.AT.marketWatch.saveObservation('sh600001','x'.repeat(2001)), /最多/);
  assert.equal(app.requests.length, 0);
  await assert.rejects(app.AT.marketWatch.saveObservation('sh600001','x'), /原备注/);
  assert.equal(app.events.length, 0);
});
test('quick-watch buttons retain full market identity, escape text and omit indices', () => {
  const app = client(async () => ({ok:true,data:{}}));
  assert.match(app.AT.marketWatch.button({symbol:'sz000001',name:'<银行>'}), /data-market-watch="sz000001"/);
  assert.doesNotMatch(app.AT.marketWatch.button({symbol:'sz000001',name:'<银行>'}), /<银行>/);
  assert.equal(app.AT.marketWatch.button({symbol:'sh000001',kind:'index'}), '');
});
