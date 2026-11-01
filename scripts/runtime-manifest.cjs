const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

async function writeRuntimeManifest(directory) {
  const root = path.resolve(directory), files = [];
  async function walk(relative = '') {
    for (const entry of await fs.readdir(path.join(root, relative), { withFileTypes: true })) {
      const item = path.join(relative, entry.name), name = item.replaceAll('\\', '/');
      if (entry.isDirectory()) await walk(item);
      else if (entry.isFile() && name !== 'resources/cache-manifest.json') {
        const data = await fs.readFile(path.join(root, item));
        files.push({ path: name, sha256: crypto.createHash('sha256').update(data).digest('hex'), bytes: data.length });
      }
    }
  }
  await walk();
  await fs.writeFile(path.join(root, 'resources/cache-manifest.json'), JSON.stringify({ schema: 'atrade-cache-files-v1', files }) + '\n');
}
module.exports = { writeRuntimeManifest };
