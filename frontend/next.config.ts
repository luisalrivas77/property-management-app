import type { NextConfig } from "next";

// Where the FastAPI app listens. The browser never sees this URL: it calls /api/* on the web
// origin and the Next server proxies (docs/phases/phase-1-step-3.md §9.3). Next proxies an
// external rewrite with the request's own headers (Origin and Cookie included; only Host is
// changed to the target's) and passes the API's Set-Cookie back unchanged, so the session
// cookie lands on the web origin and the API's Origin check (§6.6) sees the browser's Origin.
//
// The rewrite table is computed when the config loads at `next build` and stored in the
// build's routes manifest; the standalone server reads it from there. So HAUSBUCH_API_URL
// must be set at build time, not only when the server starts.
const apiUrl = (process.env.HAUSBUCH_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  output: "standalone", // the web container runs `node server.js` (§14)
  poweredByHeader: false,
  reactStrictMode: true,
  // The /api rewrite is a proxy, and Next's proxy buffers at most 10 MB of a request body and
  // gives up after 30 s by default: an upload above 10 MB reached the API cut short and then
  // hung until that timeout. Room for the API's 25 MiB upload limit plus multipart framing,
  // and two minutes for a slow phone connection (docs/phases/phase-1-step-3.md §10.8 item 3).
  // Proven by frontend/e2e/upload-limit.spec.ts.
  experimental: {
    proxyClientMaxBodySize: "26mb",
    proxyTimeout: 120_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
