import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, type Plugin } from 'vite'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

const devPort = Number(process.env.VITE_DEV_PORT ?? 5174)
const backendTarget = process.env.VITE_BACKEND_TARGET ?? 'http://127.0.0.1:8081'

function unityWebglHeaders(): Plugin {
  const unityAssetPattern = /^\/unity\/.*\.(?:wasm|data|js|symbols\.json)$/i
  return {
    name: 'unity-webgl-headers',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url?.split('?')[0] ?? ''
        if (unityAssetPattern.test(url)) {
          res.setHeader('Cache-Control', 'no-store')
          res.setHeader('Cross-Origin-Resource-Policy', 'same-origin')
          if (url.endsWith('.wasm')) res.setHeader('Content-Type', 'application/wasm')
          if (url.endsWith('.data')) res.setHeader('Content-Type', 'application/octet-stream')
          if (url.endsWith('.js')) res.setHeader('Content-Type', 'application/javascript; charset=utf-8')
        }
        next()
      })
    },
    configurePreviewServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url?.split('?')[0] ?? ''
        if (unityAssetPattern.test(url)) {
          res.setHeader('Cache-Control', 'no-store')
          res.setHeader('Cross-Origin-Resource-Policy', 'same-origin')
          if (url.endsWith('.wasm')) res.setHeader('Content-Type', 'application/wasm')
          if (url.endsWith('.data')) res.setHeader('Content-Type', 'application/octet-stream')
          if (url.endsWith('.js')) res.setHeader('Content-Type', 'application/javascript; charset=utf-8')
        }
        next()
      })
    },
  }
}

export default defineConfig(({ command }) => ({
  plugins: [
    vue(),
    unityWebglHeaders(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      dts: command === 'serve' ? 'src/auto-imports.d.ts' : false,
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: command === 'serve' ? 'src/components.d.ts' : false,
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: Number.isFinite(devPort) ? devPort : 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        ws: true,
        configure(proxy) {
          proxy.on('proxyReq', (proxyReq, req) => {
            if (req.url?.startsWith('/api/runtime-control/commands')) {
              console.log(
                `[vite-proxy] -> ${req.method} ${req.url} target=${backendTarget} host=${proxyReq.getHeader('host') ?? ''}`,
              )
            }
          })
          proxy.on('proxyRes', (proxyRes, req) => {
            if (req.url?.startsWith('/api/runtime-control/commands')) {
              console.log(`[vite-proxy] <- ${req.method} ${req.url} status=${proxyRes.statusCode}`)
            }
          })
          proxy.on('error', (error, req) => {
            if (req.url?.startsWith('/api/runtime-control/commands')) {
              console.error(`[vite-proxy] !! ${req.method} ${req.url} ${error.message}`)
            }
          })
        },
      },
    },
  },
}))
