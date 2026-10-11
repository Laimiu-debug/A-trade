'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const MAX_BYTES = 5 * 1024 * 1024;
const SCHEMA_VERSION = 1;
const SCOPE = 'desktop-demo-workspace';
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
      return add(Buffer.byteLength(JSON.stringify(item), 'utf8'));
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
      add(1 + (Array.isArray(item) ? 0 : Buffer.byteLength(JSON.stringify(key), 'utf8') + 1));
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
  state.tasks.forEach(validateTask);
  if (new Set(state.tasks.map((task) => task.id)).size !== state.tasks.length) throw failure('本地工作区任务编号不能重复。');
  numberField(state, 'goal', 0.1, 500);
  numberField(state, 'stages', 1, 200, true);
  numberField(state, 'startCapital', 1, 1e12);
  numberField(state, 'concurrency', 1, 4, true);
  numberField(state, 'ruleA', 0, 1e12);
  numberField(state, 'ruleB', 0, 1e12);
  for (const key of ['compact', 'reduced', 'largeText', 'sidebarCollapsed']) {
    if (Object.hasOwn(state, key) && typeof state[key] !== 'boolean') throw failure(`本地工作区字段 ${key} 必须为布尔值。`);
  }
  for (const key of ['account', 'page', 'stock']) stringField(state, key, { max: 200 });
  if (Object.hasOwn(state, 'page') && !/^[AMRTCG]\d{2}$/.test(state.page)) throw failure('本地工作区页面编号无效。');
  if (Object.hasOwn(state, 'stock') && !/^\d{6}$/.test(state.stock)) throw failure('本地工作区股票代码无效。');
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

class StateStore {
  constructor(directory) {
    if (typeof directory !== 'string' || !directory) throw new TypeError('工作区目录不能为空。');
    const absoluteDirectory = path.resolve(directory);
    this.paths = Object.freeze({
      directory: absoluteDirectory,
      file: path.join(absoluteDirectory, 'workspace-state.json'),
      backupFile: path.join(absoluteDirectory, 'workspace-state.previous.json'),
    });
  }

  _read(file) {
    let fd;
    try {
      fd = fs.openSync(file, 'r');
    } catch (error) {
      if (error.code === 'ENOENT') return { kind: 'missing' };
      throw error;
    }
    let raw;
    try {
      const stat = fs.fstatSync(fd);
      if (!stat.isFile()) throw failure('工作区路径不是普通文件。', 'INVALID_FILE_TYPE');
      if (stat.size > MAX_BYTES) return { kind: 'corrupt', error: failure('工作区文件超过 5 MiB 限制。', 'STATE_TOO_LARGE') };
      const buffer = Buffer.alloc(MAX_BYTES + 1);
      let length = 0;
      while (length < buffer.length) {
        const read = fs.readSync(fd, buffer, length, buffer.length - length, null);
        if (!read) break;
        length += read;
      }
      if (length > MAX_BYTES) return { kind: 'corrupt', error: failure('工作区文件超过 5 MiB 限制。', 'STATE_TOO_LARGE') };
      raw = buffer.subarray(0, length);
    } finally {
      fs.closeSync(fd);
    }
    try {
      const envelope = JSON.parse(raw.toString('utf8'));
      if (isObject(envelope) && Number.isInteger(envelope.schemaVersion) && envelope.schemaVersion > SCHEMA_VERSION) {
        throw failure('工作区文件来自更新版本的应用，当前版本不会覆盖。', 'FUTURE_VERSION');
      }
      validateJSON(envelope);
      if (!isObject(envelope) || envelope.schemaVersion !== SCHEMA_VERSION || envelope.scope !== SCOPE || typeof envelope.savedAt !== 'string' || !Number.isFinite(Date.parse(envelope.savedAt))) {
        throw failure('工作区文件格式无效。');
      }
      const stateJSON = validateState(envelope.state);
      return { kind: 'valid', raw, envelope, stateJSON };
    } catch (error) {
      if (error.code === 'FUTURE_VERSION') throw error;
      return { kind: 'corrupt', error };
    }
  }

  _atomicWrite(destination, content) {
    const temporary = path.join(this.paths.directory, `.${path.basename(destination)}.${randomUUID()}.tmp`);
    let fd;
    try {
      fd = fs.openSync(temporary, 'wx', 0o600);
      fs.writeFileSync(fd, content);
      fs.fsyncSync(fd);
      fs.closeSync(fd);
      fd = undefined;
      fs.renameSync(temporary, destination);
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
      try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }

  _current() {
    const main = this._read(this.paths.file);
    // A future-version backup must not be silently overwritten by an older app.
    const backup = this._read(this.paths.backupFile);
    if (main.kind === 'valid') {
      return { current: main, backup, warning: backup.kind === 'corrupt' ? '上一版本备份已损坏；当前工作区已正常读取，下次保存时将重建备份。' : null };
    }
    if (main.kind === 'missing' && backup.kind === 'missing') return { current: null, backup, warning: null };
    if (backup.kind !== 'valid') throw failure('本地工作区及可用备份均无法读取，已保留原文件；请先检查或恢复文件，避免丢失数据。', 'NO_VALID_STATE');
    let preserved = null;
    if (main.kind === 'corrupt') {
      preserved = path.join(this.paths.directory, `workspace-state.corrupt-${Date.now()}-${randomUUID()}.json`);
      fs.copyFileSync(this.paths.file, preserved, fs.constants.COPYFILE_EXCL);
    }
    this._atomicWrite(this.paths.file, backup.raw);
    return {
      current: backup,
      backup,
      warning: preserved
        ? `工作区文件损坏，已从上一有效版本恢复；损坏原件已保留为 ${path.basename(preserved)}。`
        : '工作区文件缺失，已从上一有效版本备份恢复。',
    };
  }

  load() {
    const { current, warning } = this._current();
    return { state: current ? current.envelope.state : null, warning };
  }

  save(state) {
    const stateJSON = validateState(state);
    const savedAt = new Date().toISOString();
    // Build from the validated snapshot so a caller cannot mutate it mid-write.
    const content = JSON.stringify({ schemaVersion: SCHEMA_VERSION, scope: SCOPE, savedAt, state: JSON.parse(stateJSON) });
    if (Buffer.byteLength(content, 'utf8') > MAX_BYTES) throw failure('本地工作区数据超过 5 MiB 限制。', 'STATE_TOO_LARGE');
    const { current, backup } = this._current();
    if (current?.stateJSON === stateJSON && backup.kind === 'valid') return { savedAt: current.envelope.savedAt };
    fs.mkdirSync(this.paths.directory, { recursive: true });
    // Write the valid predecessor first. Failed replacement leaves the old main
    // intact, with an independently readable predecessor on disk.
    this._atomicWrite(this.paths.backupFile, current ? current.raw : content);
    if (current?.stateJSON !== stateJSON) this._atomicWrite(this.paths.file, content);
    return { savedAt: current?.stateJSON === stateJSON ? current.envelope.savedAt : savedAt };
  }
}

module.exports = { StateStore };
