import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时前端 5173、后端 8000，用 Vite 代理把 /api 转发过去。
// 好处：前端代码里永远只写相对路径 '/api/xxx'，
// 开发 / Docker(nginx 反代) / 同域部署 三种环境零改动。
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // ECharts 占了近 1MB，单独拆一个 chunk：业务代码改了也不影响它的浏览器缓存
        manualChunks: {
          echarts: ['echarts'],
          vendor: ['react', 'react-dom'],
        },
      },
    },
  },
})
