const test = require('node:test');
const assert = require('node:assert/strict');
const {externalDocument} = require('../desktop/external-links.cjs');

test('publisher documents preserve their URL without permitting commands or local destinations', () => {
  assert.equal(externalDocument('https://finance.sina.com.cn/stock/article.shtml?a=1'), 'https://finance.sina.com.cn/stock/article.shtml?a=1');
  for (const value of ['file:///C:/Windows/cmd.exe', 'javascript:alert(1)', 'ftp://example.com/a', 'https://a:b@example.com', 'https://localhost/a', 'http://127.0.0.1/a', 'https://[::1]/a', 'https://news.local/a', 'https://example.com\n/a', 'https://example.com\\x', {}, '', null]) assert.equal(externalDocument(value), null, String(value));
});
