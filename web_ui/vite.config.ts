/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev: Vite 5173 代理 /history + /health → aiohttp 8766（同源免 CORS）
// prod: vite build → dist/，由 aiohttp web/static 托管
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/history": { target: "http://127.0.0.1:8766", changeOrigin: true },
      "/task": { target: "http://127.0.0.1:8766", changeOrigin: true },
      "/skills": { target: "http://127.0.0.1:8766", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8766", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
