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
/**
 * The deployed API. Hardcoded as the default rather than kept only in the host's
 * env store: a `NEXT_PUBLIC_*` value is public by construction — it ships in the
 * browser bundle — so there is nothing to protect by hiding it, and keeping it
 * only in the dashboard cost a deployment. The variable was set but stored
 * empty, and `??` does not fall back on an empty string, so the client silently
 * called `/v1` on the web app itself and every request 404'd into "not signed
 * in". `||` and a real default remove that failure mode.
 */
const DEFAULT_API_ORIGIN =
  "https://iactranslate-api-1084002294076.us-central1.run.app";

const API_ORIGIN = (process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_ORIGIN).replace(
  /\/$/,
  "",
);

/**
 * Security headers for the web surface.
 *
 * The API already sets these on its own responses; the web app — which is what
 * holds the session cookie and renders customer inventory — set only HSTS. A
 * sign-in page with no frame protection is a clickjacking target, and a page
 * rendering an estate with no referrer policy leaks project URLs to any link
 * a user clicks.
 *
 * Content-Security-Policy is set in proxy.ts, not here — it needs a nonce
 * that differs per request, which a static header cannot carry.
 */
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  // Content-Security-Policy is deliberately absent here: it lives in proxy.ts,
  // where a per-request nonce makes a strict script-src possible. Two CSP
  // headers on one response are intersected by the browser, so a static one
  // here would silently tighten the nonced one into something that breaks.
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/(.*)", headers: SECURITY_HEADERS }];
  },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API_ORIGIN}/v1/:path*` }];
  },
};

export default nextConfig;
