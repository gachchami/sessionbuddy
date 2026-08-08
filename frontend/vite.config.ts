import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: resolve(__dirname),
  base: "/app/",
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, "../src/sessionbuddy/static/app"),
    emptyOutDir: true,
    rollupOptions: { output: {
      entryFileNames: "assets/reviews.js",
      chunkFileNames: "assets/[name].js",
      assetFileNames: (asset) => asset.name?.endsWith(".css") ? "assets/reviews.css" : "assets/[name][extname]"
    }}
  }
});
