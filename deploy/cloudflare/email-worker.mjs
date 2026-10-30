// Envelope routing only. Newsletter mail is never forwarded to personal inboxes.
const LIMIT = 2_000_000;
const PATH = "/webhooks/cloudflare/email";
const encoder = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");

export async function deliveryHeaders(secret, recipient, body, timestamp = String(Math.floor(Date.now() / 1000))) {
  const digest = hex(await crypto.subtle.digest("SHA-256", body));
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const value = `v1\nPOST\n${PATH}\n${timestamp}\n${recipient}\n${digest}`;
  const signature = hex(await crypto.subtle.sign("HMAC", key, encoder.encode(value)));
  return { "content-type": "message/rfc822", "x-uptodate-recipient": recipient,
    "x-uptodate-timestamp": timestamp, "x-uptodate-signature": signature };
}

export default {
  async email(message, env) {
    const recipient = message.to.toLowerCase();
    const domain = env.RECEIVING_DOMAIN;
    if (!recipient.endsWith("@" + domain) || !/^n_(?:[a-z2-7]{13}|[0-9a-f]{32})$/.test(recipient.split("@")[0])) {
      message.setReject("Unknown recipient");
      return;
    }
    if (message.rawSize > LIMIT) {
      message.setReject("Email exceeds the 2 MB receiving limit");
      return;
    }
    const origin = new URL(env.BACKEND_ORIGIN);
    if (origin.protocol !== "https:" || !origin.hostname.endsWith(".run.app") || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== "/") {
      throw new Error("Receiving backend configuration is invalid");
    }
    if (!env.INGEST_SECRET || env.INGEST_SECRET.length < 32) throw new Error("Receiving secret is missing");
    const reader = message.raw.getReader();
    const chunks = [];
    let size = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > LIMIT) {
        await reader.cancel();
        message.setReject("Email exceeds the 2 MB receiving limit");
        return;
      }
      chunks.push(value);
    }
    const body = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    // Retry transient delivery failures. A 202 means the backend has committed
    // the message to its durable inbox; model generation is a separate job.
    const headers = await deliveryHeaders(env.INGEST_SECRET, recipient, body);
    for (let attempt = 0; attempt < 3; attempt++) {
      let response;
      try {
        response = await fetch(new URL(PATH, origin), { method: "POST", redirect: "manual", body,
          headers, signal: AbortSignal.timeout(8000) });
      } catch { /* Retry without logging email content, addresses, or secrets. */ }
      if (response?.status === 202) return;
      if ([400, 404, 413, 422].includes(response?.status)) {
        message.setReject(response.status === 404 ? "Recipient unavailable" : "Email cannot be accepted");
        return;
      }
      if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
    }
    // Fail the delivery rather than report success and silently discard mail.
    throw new Error("Email delivery to the backend failed after retries");
  },
  fetch() {
    // No HTTP endpoint accepts mail or exposes addresses and secrets.
    return new Response("UpToDate email receiver", { status: 200 });
  }
};
