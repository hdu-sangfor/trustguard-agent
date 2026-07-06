import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import tailwindcss from "@tailwindcss/vite";
import checker from "vite-plugin-checker";

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    checker({
      typescript: {
        tsconfigPath: "tsconfig.json",
      },
      enableBuild: true,
    }),
  ],
  server: {
    port: 3000,
    open: true, // 自动打开
    host: true,  // 允许局域网访问
    proxy: {
      '/api': {
        target: 'http://localhost:18080',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:18080',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query', 'sonner'],
          'vendor-three': ['three'],
          'vendor-gsap': ['gsap'],
          'vendor-motion': ['motion'],
        },
      },
    },
  },
});
