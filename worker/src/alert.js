// Plain-text alert email to the maintainer via Resend.
export async function sendAlert(env, subject, text) {
  if (!env.RESEND_API_KEY || !env.RESEND_FROM_EMAIL) {
    console.error(`Alert not sent (Resend secrets missing): ${subject}`);
    return;
  }
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: `Marine Weather Watchdog <${env.RESEND_FROM_EMAIL}>`,
      to: [env.ALERT_EMAIL],
      subject: `[Marine forecast] ${subject}`,
      text,
    }),
  });
  if (!res.ok) console.error(`Alert send failed (${res.status}): ${await res.text()}`);
}
