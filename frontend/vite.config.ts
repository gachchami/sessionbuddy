import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const outputDirectory = resolve(__dirname, "../src/sessionbuddy/static/app");

function contentAddressReviewerAssets(): Plugin {
  return {
    name: "sessionbuddy-content-address-reviewer-assets",
    closeBundle() {
      const indexPath = resolve(outputDirectory, "index.html");
      let html = readFileSync(indexPath, "utf8");
      for (const asset of ["reviews.js", "reviews.css"]) {
        const path = resolve(outputDirectory, "assets", asset);
        const digest = createHash("sha256").update(readFileSync(path)).digest("hex").slice(0, 12);
        html = html.replace(`/app/assets/${asset}`, `/app/assets/${asset}?v=${digest}`);
      }
      writeFileSync(indexPath, html, "utf8");
    },
  };
}

export default defineConfig({
  root: resolve(__dirname),
  base: "/app/",
  plugins: [react(), contentAddressReviewerAssets()],
  build: {
    outDir: outputDirectory,
    emptyOutDir: true,
    rollupOptions: { output: {
      entryFileNames: "assets/reviews.js",
      chunkFileNames: "assets/[name].js",
      assetFileNames: (asset) => asset.name?.endsWith(".css") ? "assets/reviews.css" : "assets/[name][extname]"
    }}
  }
});
