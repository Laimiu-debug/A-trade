import { fileURLToPath } from 'node:url'
export default {
  root: fileURLToPath(new URL('../vendor/finaltrade/frontend/', import.meta.url)),
  resolve: { alias: { '@': fileURLToPath(new URL('../vendor/finaltrade/frontend/src/', import.meta.url)) } },
  test: { environment: 'node', include: ['src/test/plateauSafety.test.ts'] },
}
