import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const typesPath = fileURLToPath(new URL("./src/index.d.ts", import.meta.url));

export default defineConfig({
  plugins: [react(), {
    name: "bhashavoice-types",
    buildStart() {
      this.addWatchFile(typesPath);
      this.emitFile({ type: "asset", fileName: "index.d.ts", source: readFileSync(typesPath, "utf8") });
    },
  }],
  build: {
    lib: {
      entry: fileURLToPath(new URL("./src/index.js", import.meta.url)),
      formats: ["es"],
      fileName: "index",
      cssFileName: "styles",
    },
    rolldownOptions: {
      external: (id) => /^(react|react-dom)(\/|$)/.test(id),
      // Bundlers can drop module directives. Keep the public Next.js boundary.
      output: { banner: '"use client";' },
    },
  },
});
