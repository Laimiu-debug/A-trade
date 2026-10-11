'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const MAX_BYTES = 5 * 1024 * 1024;
const SCHEMA_VERSION = 1;
const SCOPE = 'desktop-demo-workspace';
const {validate: validateState, validateJSON, isObject, failure} = require('../preview/settings-state.js');

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
