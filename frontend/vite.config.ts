import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/frontend/" : "/",
  plugins: [react()],
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
      "/static": "http://127.0.0.1:8010",
    },
  },
}));
