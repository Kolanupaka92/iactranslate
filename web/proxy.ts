import { NextResponse, type NextRequest } from "next/server";

/**
 * A strict Content-Security-Policy, with a fresh nonce per request.
 *
 * The config-level headers in next.config.ts could only carry the parts of a
 * CSP that are the same on every response. `script-src` is not: Next.js
 * hydration relies on inline scripts, and the only way to allow *those* while
 * refusing every other inline script — which is the entire point — is a nonce
 * that changes per request. Next.js reads the nonce out of this header and
 * stamps it onto its own script tags; anything injected without it does not
 * run. That closes the class of attack the partial CSP left open.
 *
 * Costs, stated plainly: pages served through this become dynamic, because the
 * nonce differs per request. The landing page was statically prerendered; it
 * is now rendered on each request at the edge. For a marketing page at this
 * traffic that is not measurable, and it is the price every nonce-based CSP
 * pays. `'strict-dynamic'` lets scripts the nonced ones load (Next's chunks)
 * run without listing each host, and makes the `'self'` fallback apply only to
 * browsers too old to understand it.
 */
export function proxy(request: NextRequest) {
  const nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))));

  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    // 'unsafe-inline' for *styles* only, and deliberately. React sets style
    // attributes (the cost-bar widths, for one), and a nonce cannot cover an
    // attribute — only a <style> element. The alternative is per-attribute
    // hashes, which change with every data value. CSS injection is a far
    // weaker attack class than script injection; every strict-CSP deployment
    // that renders data-driven styles makes this same trade. Scripts stay
    // nonce-only, which is where the security actually lives.
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "img-src 'self' data:",
    // Every API call goes through the same-origin /api/v1 rewrite.
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "upgrade-insecure-requests",
  ].join("; ");

  // Set on the *request* so Next.js can read the nonce while rendering, and
  // on the response so the browser enforces it.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    // Everything except Next's own static assets and the API rewrite, which
    // is proxied JSON and needs no policy of its own.
    {
      source: "/((?!_next/static|_next/image|favicon.ico|api/).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
