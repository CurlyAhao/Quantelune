import type { NextConfig } from "next";
import path from "node:path";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

const nextConfig = (phase: string): NextConfig => ({
  // Tauri still consumes the finite static shells in out/ from next build.
  // In next dev, real project/dataset IDs must resolve on demand.
  ...(phase === PHASE_DEVELOPMENT_SERVER ? {} : { output: "export" }),
  ...(process.env.STATFORGE_COMPACT_BUILD === "1" ? { webpack: (config) => {
    config.cache = false;
    // Next 15 prefixes these entry points with "./" even when shared dependencies are on another drive.
    for (const entry of ["next.js","app-next.js"]) {
      const target = require.resolve("next/dist/client/" + entry);
      if (path.isAbsolute(path.relative(process.cwd(), target))) {
        config.resolve.alias["./" + target.replaceAll("\\", "/") + "$"] = target;
      }
    }
    return config;
  } } : {}),
  images: { unoptimized: true },
});

export default nextConfig;
