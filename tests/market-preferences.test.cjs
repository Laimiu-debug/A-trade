const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const schema = require('../preview/market-preferences.js');
const source = fs.readFileSync(path.join(__dirname, '../preview/market-preferences.js'), 'utf8');
function client(state = {}) {
  let saves = 0;
  const AT = {state, persist() {saves++;}};
  vm.runInNewContext(source, {window:{AT}});
  return {AT, saves: () => saves};
}
test('market display preferences survive a fresh renderer and omit ephemeral data', () => {
  const first = client();
  first.AT.marketPreferences.saveList('stocks:stocks', {query:'长江', board:'main', pool:'gainers', size:30, sort:'return20', order:'desc', userSort:true, filtersOpen:true, compact:false, detailsOpen:true, offset:120, timer:99});
  first.AT.marketPreferences.saveList('stocks:sh880001', {query:'银行', size:60});
  first.AT.marketPreferences.saveStudies({ma:false, force:true, signals:false, box:true});
  const reopened = client(JSON.parse(JSON.stringify(first.AT.state)));
  assert.equal(reopened.AT.marketPreferences.getList('stocks:stocks').query, '长江');
  assert.equal(reopened.AT.marketPreferences.getList('stocks:stocks').size, 30);
  assert.equal(reopened.AT.marketPreferences.getList('stocks:sh880001').size, 60);
  assert.equal(reopened.AT.marketPreferences.getStudies().box, true);
  assert.equal(reopened.AT.marketPreferences.getStudies().ma, false);
  assert.equal(Object.hasOwn(first.AT.state.marketPreferences.lists['stocks:stocks'], 'timer'), false);
  assert.equal(Object.hasOwn(first.AT.state.marketPreferences.lists['stocks:stocks'], 'offset'), false);
  assert.equal(first.saves(), 3);
});
test('preferences sanitize browser data, bound saved lists and reset only the chosen list', () => {
  const app = client({marketPreferences:{lists:{'stocks:stocks':{query:'x'.repeat(101),size:200,above:1,sort:'bad',pool:'all'}}, studies:{ma:'yes',box:true}}});
  assert.deepEqual(JSON.parse(JSON.stringify(app.AT.marketPreferences.getList('stocks:stocks'))), {pool:'all'});
  for (let i = 0; i < 105; i++) app.AT.marketPreferences.saveList(`stocks:sh${String(i).padStart(6,'0')}`, {size:15});
  assert.equal(Object.keys(app.AT.state.marketPreferences.lists).length, 100);
  app.AT.marketPreferences.resetList('stocks:sh000104');
  assert.equal(Object.keys(app.AT.state.marketPreferences.lists).length, 99);
  assert.equal(app.AT.marketPreferences.getStudies().box, true);
});
test('the shared desktop schema rejects invalid fields rather than persisting hidden runtime state', () => {
  for (const lists of [{'stocks:stocks':{timer:123}}, {'stocks:stocks':{size:120}}, {'stocks:stocks':{compact:1}}, {'bad':{query:'x'}}]) {
    assert.throws(() => schema.validate({version:1, lists, studies:{}}));
  }
  assert.throws(() => schema.validate({version:1,lists:{},studies:{ma:'false'}}));
  assert.equal(schema.validate({version:1,lists:{'sectors:all':{category:'行业',size:60}},studies:{box:false}}), true);
});

test('browser schema validates bootstrapped preferences before data initialization without a dependency cycle', () => {
  const sandbox = {AT:{},TextEncoder}; sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../preview/settings-state.js'),'utf8'), context);
  assert.equal(vm.runInContext(`AT.settingsState.validate({version:1,notes:{},tasks:[],marketPreferences:{version:1,lists:{'stocks:stocks':{size:30}},studies:{box:true}}}).includes('marketPreferences')`, context), true);
  vm.runInContext(`AT.state={marketPreferences:{version:1,lists:{},studies:{ma:false}}}`,context);
  assert.equal(sandbox.AT.marketPreferences.getStudies().ma,false);
});

test('chart study controls apply saved switches and persist a toggle for the next renderer', () => {
  const app = client({marketPreferences:{version:1,lists:{},studies:{ma:false,force:false,signals:true,box:true}}});
  const listeners = new Map(), AT = app.AT;
  AT.escape = value=>String(value); AT.render = ()=>{};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preview/market-analysis-ui.js'),'utf8'), {AT,window:{addEventListener(){}},document:{addEventListener(type,handler){listeners.set(type,handler);}},performance:{now:()=>0}});
  assert.match(AT.marketAnalysis.controls(),/data-market-study="ma" aria-pressed="false"/);
  assert.match(AT.marketAnalysis.controls(),/data-market-study="box" aria-pressed="true"/);
  listeners.get('click')({target:{closest:selector=>selector==='[data-market-study]'?{dataset:{marketStudy:'ma'}}:null}});
  assert.equal(client(JSON.parse(JSON.stringify(AT.state))).AT.marketPreferences.getStudies().ma,true);
});
