// Shared by desktop persistence and browser backup validation. No app data or credentials.
(function(root,factory){
  const api=factory(typeof module==='object'&&module.exports ? require('./market-preferences.js') : root.AT.marketPreferencesSchema);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.AT.settingsState=api;
})(globalThis,(marketPreferences)=>{
'use strict';
const MAX_BYTES=5*1024*1024;
const utf8=value=>new TextEncoder().encode(value).length;
const DANGEROUS_KEYS = new Set(['__proto__', 'prototype', 'constructor']);
const TASK_STATUSES = new Set(['running', 'queued', 'paused', 'stopped', 'complete']);

function failure(message, code = 'INVALID_STATE') {
  return Object.assign(new Error(message), { code });
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// Accept plain JSON only. In particular, do not invoke getters or toJSON hooks
// received from the renderer, and reject prototype-pollution keys at any depth.
function validateJSON(value) {
  const ancestors = new Set();
  let bytes = 0;
  const add = (amount) => {
    bytes += amount;
    if (bytes > MAX_BYTES) throw failure('本地工作区数据超过 5 MiB 限制。', 'STATE_TOO_LARGE');
  };
  const visit = (item, depth) => {
    if (depth > 64) throw failure('本地工作区数据嵌套过深。');
    if (item === null || typeof item === 'boolean') return add(5);
    if (typeof item === 'number') {
      if (!Number.isFinite(item)) throw failure('本地工作区不能包含无效数值。');
      return add(String(item).length);
    }
    if (typeof item === 'string') {
      if (item.length > MAX_BYTES) throw failure('本地工作区数据超过 5 MiB 限制。', 'STATE_TOO_LARGE');
      return add(utf8(JSON.stringify(item)));
    }
    if (typeof item !== 'object') throw failure('本地工作区只能包含 JSON 数据。');
    const proto = Object.getPrototypeOf(item);
    if (Array.isArray(item) ? proto !== Array.prototype : proto !== Object.prototype && proto !== null) {
      throw failure('本地工作区只能包含普通 JSON 对象。');
    }
    if (ancestors.has(item)) throw failure('本地工作区不能包含循环引用。');
    if (Object.getOwnPropertySymbols(item).length) throw failure('本地工作区不能包含 Symbol 字段。');
    ancestors.add(item);
    add(2);
    const keys = Object.getOwnPropertyNames(item).filter((key) => !(Array.isArray(item) && key === 'length'));
    if (Array.isArray(item) && keys.length !== item.length) throw failure('本地工作区数组不能包含空项或额外字段。');
    for (const key of keys) {
      if (DANGEROUS_KEYS.has(key)) throw failure('本地工作区包含不安全字段。');
      if (Array.isArray(item) && !/^(0|[1-9]\d*)$/.test(key)) throw failure('本地工作区数组包含额外字段。');
      const descriptor = Object.getOwnPropertyDescriptor(item, key);
      if (!Object.hasOwn(descriptor, 'value')) throw failure('本地工作区不能包含访问器字段。');
      if (!descriptor.enumerable) throw failure('本地工作区不能包含隐藏字段。');
      add(1 + (Array.isArray(item) ? 0 : utf8(JSON.stringify(key)) + 1));
      visit(descriptor.value, depth + 1);
    }
    ancestors.delete(item);
  };
  visit(value, 0);
}

function stringField(object, key, { required = false, max = 1000 } = {}) {
  if (!Object.hasOwn(object, key) && !required) return;
  if (typeof object[key] !== 'string' || object[key].length > max || (required && !object[key])) {
    throw failure(`本地工作区字段 ${key} 必须是有效文本。`);
  }
}

function numberField(object, key, min, max, integer = false) {
  if (!Object.hasOwn(object, key)) return;
  const value = object[key];
  if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    throw failure(`本地工作区字段 ${key} 超出允许范围。`);
  }
}

function validateTask(task) {
  if (!isObject(task)) throw failure('本地工作区任务格式无效。');
  for (const key of ['id', 'name']) stringField(task, key, { required: true });
  for (const key of ['type', 'stock', 'date', 'created', 'step']) stringField(task, key);
  if (!TASK_STATUSES.has(task.status) || !Number.isFinite(task.progress) || task.progress < 0 || task.progress > 100) {
    throw failure('本地工作区任务状态或进度无效。');
  }
  numberField(task, 'count', 1, Number.MAX_SAFE_INTEGER, true);
}

function validateState(state) {
  validateJSON(state);
  if (!isObject(state)) throw failure('本地工作区必须是一个对象。');
  if (Number.isInteger(state.version) && state.version > 1) {
    throw failure('工作区由更新版本的应用保存，请使用更新版本打开，当前版本不会覆盖。', 'FUTURE_VERSION');
  }
  if (state.version !== 1 || !isObject(state.notes) || !Array.isArray(state.tasks)) {
    throw failure('本地工作区版本、笔记或任务格式无效。');
  }
  if (Object.values(state.notes).some((note) => typeof note !== 'string')) throw failure('本地工作区笔记必须是文本。');
  if (Object.hasOwn(state, 'marketPreferences')) {
    try { marketPreferences.validate(state.marketPreferences); } catch (error) { throw failure(error.message); }
  }
  state.tasks.forEach(validateTask);
  if (new Set(state.tasks.map((task) => task.id)).size !== state.tasks.length) throw failure('本地工作区任务编号不能重复。');
  numberField(state, 'goal', 0.1, 500);
  numberField(state, 'stages', 1, 200, true);
  numberField(state, 'startCapital', 1, 1e12);
  numberField(state, 'concurrency', 1, 4, true);
  validateRules(state);
  numberField(state, 'ruleA', 0, 1e12);
  numberField(state, 'ruleB', 0, 1e12);
  for (const key of ['compact', 'reduced', 'largeText', 'sidebarCollapsed']) {
    if (Object.hasOwn(state, key) && typeof state[key] !== 'boolean') throw failure(`本地工作区字段 ${key} 必须为布尔值。`);
  }
  for (const key of ['account', 'page', 'stock']) stringField(state, key, { max: 200 });
  if (Object.hasOwn(state, 'page') && !/^[AMRTCG]\d{2}$/.test(state.page)) throw failure('本地工作区页面编号无效。');
  if (Object.hasOwn(state, 'stock') && !/^(?:(?:sh|sz|bj))?\d{6}$/.test(state.stock)) throw failure('本地工作区股票代码无效。');
  if (Object.hasOwn(state, 'watch') && (!Array.isArray(state.watch) || state.watch.some((code) => typeof code !== 'string' || !/^\d{6}$/.test(code)))) {
    throw failure('本地工作区跟踪池格式无效。');
  }
  for (const key of ['reports', 'customTrades']) {
    if (Object.hasOwn(state, key) && (!Array.isArray(state[key]) || state[key].some((entry) => !isObject(entry)))) {
      throw failure(`本地工作区字段 ${key} 必须是对象数组。`);
    }
  }
  if (Object.hasOwn(state, 'taskTrash')) {
    if (!Array.isArray(state.taskTrash)) throw failure('本地工作区任务回收站格式无效。');
    for (const entry of state.taskTrash) {
      if (!isObject(entry)) throw failure('本地工作区任务回收站格式无效。');
      validateTask(entry.task);
      numberField(entry, 'index', 0, Number.MAX_SAFE_INTEGER, true);
      stringField(entry, 'deletedAt');
    }
  }
  return JSON.stringify(state);
}


  function validateRules(state) {
    for (const [section, fields] of Object.entries({fees:{commissionRate:[0,10000],minCommission:[0,1e12]},positionRules:{singleStockMax:[0,100],totalMax:[0,100]}})) {
      if (!Object.hasOwn(state, section)) continue;
      if (!isObject(state[section])) throw failure('设置分类格式无效。');
      for (const [field, [min,max]] of Object.entries(fields)) {
        if (!Object.hasOwn(state[section],field)) throw failure('设置参数不完整。');
        numberField(state[section],field,min,max);
      }
    }
    if (state.positionRules && state.positionRules.singleStockMax > state.positionRules.totalMax) throw failure('单股仓位上限不能高于总仓位上限。');
  }
  function normalizeRules(state) {
    const result=structuredClone(state);
    // Legacy ruleA/B were shared by unrelated forms: their origin is unknowable.
    // Preserve them for review instead of guessing either category's intent.
    const legacy=Object.hasOwn(result,'ruleA')||Object.hasOwn(result,'ruleB');
    if (!result.fees) { result.fees={commissionRate:2.5,minCommission:5}; if(legacy)result.feesNeedsReview=true; }
    if (!result.positionRules) { result.positionRules={singleStockMax:30,totalMax:80}; if(legacy)result.positionRulesNeedsReview=true; }
    return result;
  }

return {validate:validateState,validateJSON,isObject,validateRules,normalizeRules,failure,MAX_BYTES};
});
