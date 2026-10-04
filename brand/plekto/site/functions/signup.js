// Cloudflare Pages Function — POST /signup
// Accepts the landing-page form submission, validates the email,
// writes it to the SIGNUPS KV namespace (one entry per email), and
// redirects to /thanks/ on success or /?error=... on failure.
//
// Requires a KV binding named SIGNUPS in the Pages project settings.
// Create the namespace in Cloudflare dashboard → Workers & Pages → KV,
// then bind it under Pages → your project → Settings → Functions → KV
// namespace bindings: Variable name = SIGNUPS, namespace = <your-ns>.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function onRequestPost(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  let email = "";
  try {
    const form = await request.formData();
    email = (form.get("email") || "").toString().trim().toLowerCase();
  } catch (_) {
    return Response.redirect(`${url.origin}/?error=invalid`, 303);
  }

  if (!email || email.length > 254 || !EMAIL_RE.test(email)) {
    return Response.redirect(`${url.origin}/?error=invalid`, 303);
  }

  if (!env.SIGNUPS) {
    // KV not bound — fail loud in logs, soft to user
    console.error("SIGNUPS KV binding missing");
    return Response.redirect(`${url.origin}/?error=server`, 303);
  }

  const key = `email:${email}`;
  const payload = JSON.stringify({
    email,
    ts: new Date().toISOString(),
    ua: request.headers.get("user-agent") || "",
    ref: request.headers.get("referer") || "",
    ip: request.headers.get("cf-connecting-ip") || "",
    country: request.cf?.country || "",
  });

  try {
    // Idempotent — don't overwrite existing signups
    const existing = await env.SIGNUPS.get(key);
    if (!existing) {
      await env.SIGNUPS.put(key, payload);
    }
  } catch (e) {
    console.error("KV write failed:", e);
    return Response.redirect(`${url.origin}/?error=server`, 303);
  }

  return Response.redirect(`${url.origin}/thanks/`, 303);
}

// Reject non-POST to /signup (e.g. someone hitting the URL directly)
export async function onRequest(context) {
  return Response.redirect(`${new URL(context.request.url).origin}/`, 303);
}
