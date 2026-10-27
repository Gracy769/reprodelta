const SOURCE_HOSTS = new Set(["github", "gitlab"]);

export default {
  async fetch(request, env) {
    const publicUrl = new URL(request.url);
    const origin = new URL(env.ORIGIN_URL);
    if (origin.protocol !== "https:" || origin.pathname !== "/" || origin.search || origin.hash) {
      return new Response("Cloudflare origin is misconfigured", { status: 503 });
    }

    if (publicUrl.pathname === "/__edge/health") {
      try {
        const upstream = await fetch(new URL("/health", origin), { redirect: "manual" });
        return Response.json(
          { edge: "ok", api: upstream.ok ? "ok" : "unavailable", firebaseProjectId: env.FIREBASE_PROJECT_ID },
          { status: upstream.ok ? 200 : 503, headers: { "Cache-Control": "no-store" } },
        );
      } catch {
        return Response.json({ edge: "ok", api: "unavailable" }, { status: 503 });
      }
    }

    // Existing OAuth Apps are registered for the Railway callback. Start the
    // flow there so its state cookie and callback remain on the same host.
    const oauthStart = /^\/oauth\/([^/]+)\/start$/.exec(publicUrl.pathname);
    if (oauthStart && SOURCE_HOSTS.has(oauthStart[1])) {
      const destination = new URL(publicUrl.pathname + publicUrl.search, origin);
      return Response.redirect(destination, 303);
    }

    const upstreamUrl = new URL(publicUrl.pathname + publicUrl.search, origin);
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("x-reprodelta-edge-host");
    headers.set("x-reprodelta-edge-host", publicUrl.host);
    const upstream = await fetch(new Request(upstreamUrl, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
    }));
    const response = new Response(upstream.body, upstream);
    const location = response.headers.get("location");
    if (location) {
      const target = new URL(location, origin);
      if (target.origin === origin.origin) {
        target.protocol = publicUrl.protocol;
        target.host = publicUrl.host;
        response.headers.set("location", target.href);
      }
    }
    response.headers.set("X-ReproDelta-Edge", "cloudflare");
    return response;
  },
};
