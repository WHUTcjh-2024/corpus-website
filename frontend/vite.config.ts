import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/frontend/" : "/",
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/api/types.ts", "src/test/**", "src/**/*.test.{ts,tsx}"],
      thresholds: {
        branches: 70,
        functions: 80,
        lines: 80,
        statements: 80,
      },
    },
  },
  build: {
    outDir: "../backend/static/frontend",
    emptyOutDir: true,
    rollupOptions: {
      input: "src/main.tsx",
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: "assets/[name].[ext]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8010",
      "/accounts": "http://127.0.0.1:8010",
      "/admin": "http://127.0.0.1:8010",
      "/corpora": "http://127.0.0.1:8010",
      "/search": "http://127.0.0.1:8010",
      "/parallel": "http://127.0.0.1:8010",
      "/statistics": "http://127.0.0.1:8010",
      "/exports": "http://127.0.0.1:8010",
      "/feedback": "http://127.0.0.1:8010",
      "/management": "http://127.0.0.1:8010",
      "/static": "http://127.0.0.1:8010",
    },
  },
}));
