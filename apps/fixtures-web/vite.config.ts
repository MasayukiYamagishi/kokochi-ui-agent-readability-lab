import react from '@vitejs/plugin-react'
import { copyFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'

export default defineConfig({
  appType: 'spa',
  plugins: [
    react(),
    {
      name: 'static-host-spa-fallback',
      closeBundle() {
        copyFileSync(
          resolve(import.meta.dirname, 'dist/index.html'),
          resolve(import.meta.dirname, 'dist/404.html'),
        )
      },
    },
  ],
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
})
