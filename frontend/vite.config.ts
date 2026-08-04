import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const apiProxyTarget =
  process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";
const viteHost = process.env.VITE_HOST ?? "0.0.0.0";

export default defineConfig({
  plugins: [react()],
  server: {
    host: viteHost,
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      "/live": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      "/ready": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
