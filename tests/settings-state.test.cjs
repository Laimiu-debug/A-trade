const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const schema=require('../preview/settings-state.js');
const {StateStore}=require('../desktop/state-store.cjs');
const state=()=>({version:1,notes:{},tasks:[]});

test('legacy shared rules remain inspectable without guessing their category',()=>{
  const legacy={...state(),ruleA:2.5,ruleB:5};
  const result=schema.normalizeRules(legacy);
  assert.deepEqual(result.fees,{commissionRate:2.5,minCommission:5});
  assert.deepEqual(result.positionRules,{singleStockMax:30,totalMax:80});
  assert.equal(result.feesNeedsReview,true);
  assert.equal(result.positionRulesNeedsReview,true);
  assert.equal(result.ruleA,2.5);
  assert.equal(legacy.fees,undefined);
  assert.deepEqual(schema.normalizeRules(result),result);
});

test('independent fee and position settings preserve zero through disk restart',t=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-settings-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const store=new StateStore(directory), value=schema.normalizeRules(state());
  value.fees={commissionRate:0,minCommission:0};
  value.positionRules={singleStockMax:20,totalMax:70};
  store.save(value);
  const restored=new StateStore(directory).load().state;
  assert.deepEqual(restored.fees,{commissionRate:0,minCommission:0});
  assert.deepEqual(restored.positionRules,{singleStockMax:20,totalMax:70});
  assert.deepEqual(schema.normalizeRules(restored),restored);
});

test('invalid or incomplete rules are rejected before touching the saved workspace',t=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'atrade-settings-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const store=new StateStore(directory), value=schema.normalizeRules(state());
  store.save(value);const before=fs.readFileSync(store.paths.file,'utf8');
  for(const rules of [{singleStockMax:101,totalMax:100},{singleStockMax:50,totalMax:30},{singleStockMax:-1,totalMax:80},{singleStockMax:NaN,totalMax:80},{totalMax:80}]){
    assert.throws(()=>store.save({...value,positionRules:rules}));
    assert.equal(fs.readFileSync(store.paths.file,'utf8'),before);
  }
  assert.throws(()=>store.save({...value,fees:{commissionRate:'2.5',minCommission:0}}));
  assert.equal(fs.readFileSync(store.paths.file,'utf8'),before);
});
