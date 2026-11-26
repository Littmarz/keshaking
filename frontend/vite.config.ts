import { defineConfig } from 'vite';

const proxy = {
  '/api': {
    target: 'http://127.0.0.1:8000',
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
};

export default defineConfig({
  server: { port: 5173, proxy },
  preview: { port: 5173, proxy },
});
