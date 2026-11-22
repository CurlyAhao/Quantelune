import { fileURLToPath } from "node:url";
import path from "node:path";

import { defineConfig } from "vitest/config";

// Mirror the tsconfig "@" alias so component tests can import app sources.
const srcDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "src");

export default defineConfig({
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/lib/**/*.ts"],
    },
  },
  resolve: {
    alias: {
      "@": srcDir,
    },
  },
  oxc: {
    jsx: {
      runtime: "automatic",
      importSource: "react",
    },
  },
});
