import { fileURLToPath } from 'node:url'
const frontend = fileURLToPath(new URL('../vendor/finaltrade/frontend/', import.meta.url))
const pinnedDependencies = {
  name: 'desktop-test-dependencies',
  enforce: 'pre',
  async resolveId(id, importer) {
    if (!importer || id.startsWith('.') || id.startsWith('/') || id.startsWith('@/') || id.includes(':') || id.startsWith('\0') || importer.replaceAll('\\', '/').includes('/node_modules/')) return null
    return this.resolve(id, fileURLToPath(new URL('../vendor/finaltrade/frontend/src/main.tsx', import.meta.url)), { skipSelf: true })
  },
}
export default {
  root: frontend,
  plugins: [pinnedDependencies],
  resolve: { alias: { '@': fileURLToPath(new URL('../vendor/finaltrade/frontend/src/', import.meta.url)) } },
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    include: ['../../../tests/research-tasks.test.tsx'],
    setupFiles: [fileURLToPath(new URL('../vendor/finaltrade/frontend/src/test/setup.ts', import.meta.url))],
  },
}
