const { isIP } = require('node:net');

// Only web documents opened by an explicit renderer click may leave the app.
function externalDocument(value) {
  if (typeof value !== 'string' || !value || value.length > 4096 || /[\u0000-\u0020\u007f\\]/.test(value)) return null;
  try {
    const url = new URL(value), host = url.hostname.toLowerCase();
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || !host.includes('.')) return null;
    if (isIP(host.replace(/^\[|\]$/g, '')) || /(^|\.)(localhost|local|internal)$/.test(host)) return null;
    return url.href;
  } catch { return null; }
}

module.exports = { externalDocument };
