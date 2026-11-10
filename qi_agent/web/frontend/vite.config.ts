import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发：Vite dev server（proxy /ws → web 后端；HMR）
// 生产：vite build → dist/（FastAPI StaticFiles 挂载）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/ws': {
        target: 'ws://127.0.0.1:9000',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
