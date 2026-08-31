import type { NextConfig } from "next";

/**
 * Proxy the API through this app so the browser sees one origin.
 *
 * The console and the API are deployed separately — Vercel and Cloud Run — and
 * that makes them different *sites*, not just different origins. A `SameSite`
 * cookie will not cross that boundary, so signing in appeared to work and then
 * every following request came back unauthenticated.
 *
 * Rewriting `/api/v1/*` to the API removes the problem rather than working
 * around it: to the browser these are same-origin requests, so the session
 * cookie is sent, no preflight happens, and no CORS relaxation is needed for
 * ordinary traffic.
 *
 * Rewrites to an external destination are handled by Vercel's routing layer and
 * invoke no function, so the 4.5 MB function payload limit does not apply — the
 * documented constraint on a proxied request is a 120s timeout. That matters
 * because uploads run to 25 MB, and it is verified against the deployed app
 * rather than taken on faith.
 */
const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    if (!API_ORIGIN) return [];
    return [{ source: "/api/v1/:path*", destination: `${API_ORIGIN}/v1/:path*` }];
  },
};

export default nextConfig;
