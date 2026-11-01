// Public product pages and explicit share capabilities; no app or connector API.
const publicPaths = new Set([
  "/", "/privacy", "/terms", "/website/style.css", "/website/icon.svg",
  "/share-assets/share.css", "/share-assets/share.js", "/share-assets/icon.svg",
]);
const sharedPath = /^\/p\/[A-Za-z0-9_-]{32}(?:\/images\/[a-f0-9]{16,64}|\/venue)?$/;
const sharedImage = /^\/p\/[A-Za-z0-9_-]{32}\/images\/[a-f0-9]{16,64}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const isShare = sharedPath.test(url.pathname);
    if (url.hostname !== env.PUBLIC_DOMAIN || (!publicPaths.has(url.pathname) && !isShare)) {
      return new Response("Not found", { status: 404 });
    }
    if (!["GET", "HEAD"].includes(request.method)) {
      return new Response("Method not allowed", {
        status: 405, headers: { Allow: "GET, HEAD", "Cache-Control": "no-store" },
      });
    }
    if (url.protocol !== "https:") {
      return Response.redirect(`https://${env.PUBLIC_DOMAIN}${url.pathname}`, 308);
    }
    // Construct a fresh public request: cookies, credentials and query strings
    // from the visitor never travel to the app backend.
    try {
      const origin = new URL(env.BACKEND_ORIGIN);
      if (origin.protocol !== "https:" || !origin.hostname.endsWith(".run.app")) {
        throw new Error("Invalid website origin");
      }
      const forwarded = { Accept: "text/html,text/css,image/svg+xml,application/json,image/png" };
      if (sharedImage.test(url.pathname) && request.headers.get("X-UpToDate-Photo-Attribution") === "1") forwarded["X-UpToDate-Photo-Attribution"] = "1";
      const upstream = await fetch(new URL(url.pathname, origin), {
        method: request.method,
        headers: forwarded,
        redirect: "manual",
        cf: { cacheEverything: !isShare, cacheTtlByStatus: { "200": isShare ? 0 : 300, "400-599": 0 } },
      });
      if (upstream.status >= 500 || upstream.status < 200 || (upstream.status >= 300 && upstream.status < 400)) throw new Error("Website unavailable");
      const headers = new Headers(upstream.headers);
      headers.delete("set-cookie");
      headers.set("Cache-Control", isShare || upstream.status !== 200 ? "private,no-store" : "public,max-age=300");
      return new Response(request.method === "HEAD" ? null : upstream.body, {
        status: upstream.status, headers,
      });
    } catch {
      return new Response("Website temporarily unavailable. Please try again.", {
        status: 503, headers: { "Cache-Control": "no-store" },
      });
    }
  },
};
