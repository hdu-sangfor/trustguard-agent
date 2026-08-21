import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import tailwindcss from "@tailwindcss/vite";
import checker from "vite-plugin-checker";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const envDir = path.resolve(__dirname, "..");
  const env = loadEnv(mode, envDir, "");
  const rawPort = process.env.AGENT_FRONTEND_HOST_PORT ?? env.AGENT_FRONTEND_HOST_PORT ?? process.env.VITE_DEV_PORT ?? env.VITE_DEV_PORT ?? "8199";
  const parsedPort = Number(rawPort);
  const frontendPort = Number.isInteger(parsedPort) && parsedPort > 0 && parsedPort <= 65535 ? parsedPort : 8199;
  const gatewayTarget = process.env.VITE_GATEWAY_PROXY_TARGET ?? env.VITE_GATEWAY_PROXY_TARGET ?? "http://localhost:8100";

  return {
    envDir,
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
      port: frontendPort,
      strictPort: true,
      open: true, // 自动打开
      host: true,  // 允许局域网访问
      proxy: {
        '/api': {
          target: gatewayTarget,
          changeOrigin: true,
        },
        '/health': {
          target: gatewayTarget,
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
  };
});
