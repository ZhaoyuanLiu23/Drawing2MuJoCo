import path from "node:path";
import { fileURLToPath } from "node:url";

const nextConfig = {
  // Leave multipart overhead above the backend's exact 10 MiB file limit.
  experimental: { proxyClientMaxBodySize: "11mb", proxyTimeout: 620000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
  },
  turbopack: {
    root: path.dirname(fileURLToPath(import.meta.url)),
  },
};

export default nextConfig;
