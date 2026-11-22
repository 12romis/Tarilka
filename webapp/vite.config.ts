import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

// У dev-режимі /api і /media проксуються на локальний uvicorn.
export default defineConfig({
  plugins: [preact()],
  build: { target: "es2020", outDir: "dist", assetsInlineLimit: 0 },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000", "/media": "http://127.0.0.1:8000" },
  },
});
