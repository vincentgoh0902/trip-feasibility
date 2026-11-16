import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // a self-contained server for the Docker image (Dockerfile): no node_modules at runtime
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
