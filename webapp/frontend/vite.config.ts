import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// `npm run dev` proxies the API to a local `uvicorn webapp.server:app --port 8765`.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8765', '/health': 'http://localhost:8765' },
  },
  build: {
    outDir: 'dist', emptyOutDir: true,
    rollupOptions: { output: { manualChunks(id: string) {
      if (!id.includes('node_modules')) return undefined;
      if (/recharts|d3-|victory-vendor|decimal\.js|es-toolkit|immer|reselect|redux/.test(id)) return 'charts';
      return 'vendor';
    } } },
  },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
