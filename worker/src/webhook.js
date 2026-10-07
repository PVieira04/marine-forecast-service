import { sendAlert } from "./alert.js";

// POST /resend-webhook — Resend delivery events (Svix-signed), stored in D1.
// Bounces and spam complaints also alert the maintainer.
const TOLERANCE_S = 5 * 60;
const ALERT_TYPES = new Set(["email.bounced", "email.complained"]);

function b64ToBytes(b64) {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

export async function verifySvix(secret, id, timestamp, signatureHeader, body, nowS = Date.now() / 1000) {
  if (!id || !timestamp || !signatureHeader) return false;
  if (Math.abs(nowS - Number(timestamp)) > TOLERANCE_S) return false;
  const key = await crypto.subtle.importKey(
    "raw", b64ToBytes(secret.replace(/^whsec_/, "")), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${id}.${timestamp}.${body}`));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  return signatureHeader.split(" ").some((part) => {
    const [version, sig] = part.split(",");
    return version === "v1" && sig === expected;
  });
}

export async function handleWebhook(request, env) {
  const body = await request.text();
  const ok = env.RESEND_WEBHOOK_SECRET && (await verifySvix(
    env.RESEND_WEBHOOK_SECRET,
    request.headers.get("svix-id"),
    request.headers.get("svix-timestamp"),
    request.headers.get("svix-signature"),
    body,
  ));
  if (!ok) return new Response("Invalid signature", { status: 401 });

  const event = JSON.parse(body);
  const data = event.data ?? {};
  await env.DB.prepare(
    "INSERT INTO email_events (resend_id, type, event_at, recipients, detail) VALUES (?, ?, ?, ?, ?)",
  )
    .bind(data.email_id ?? null, event.type, event.created_at ?? null, JSON.stringify(data.to ?? []), body)
    .run();

  if (ALERT_TYPES.has(event.type)) {
    await sendAlert(
      env,
      `Email ${event.type.replace("email.", "")}: ${(data.to ?? []).join(", ")}`,
      `Resend reported ${event.type} for "${data.subject ?? "?"}" (id ${data.email_id ?? "?"}).\n\n` +
        `Bounces/complaints can make Resend suppress future sends to this address — check the Resend dashboard.\n\n` +
        body,
    );
  }
  return new Response("ok");
}
