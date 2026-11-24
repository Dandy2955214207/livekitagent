import { defineConfig } from 'vite';

export default defineConfig({
  build: { rolldownOptions: { input: { main: 'index.html', deviceCheck: 'device-check.html',
    mobileTestReport: 'mobile-test-report.html' } } },
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
});
