import { slotFor } from "./slot.js";
import { sendAlert } from "./alert.js";

export const WATCHDOG_DELAY_MS = 30 * 60 * 1000;

// Runs 30 min after each candidate slot time. Alerts if D1 has no real
// (non-test) successful send for the slot — covers the Worker dispatch,
// the GitHub run and the send itself, unlike "did any run succeed".
export async function checkSlot(env, scheduledTime) {
  const slotStart = new Date(scheduledTime - WATCHDOG_DELAY_MS);
  const slot = slotFor(slotStart);
  if (!slot) return null; // DST partner hour

  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM sends WHERE slot = ? AND status = 'sent' AND test_mode = 0 AND sent_at >= ?",
  )
    .bind(slot, slotStart.toISOString())
    .first();

  if (row.n > 0) {
    console.log(`Watchdog: ${slot} forecast sent`);
    return "ok";
  }
  await sendAlert(
    env,
    `No ${slot} forecast sent`,
    `No successful ${slot} forecast is logged since ${slotStart.toISOString()}.\n\n` +
      `Gordon may not have received it. The late GitHub schedule fallback may still send it.\n\n` +
      `Runs: https://github.com/${env.GITHUB_REPO}/actions/workflows/forecast.yml\n` +
      `Send log: npx wrangler d1 execute marine-weather-log --remote --command "SELECT * FROM sends ORDER BY id DESC LIMIT 5"`,
  );
  return "alerted";
}
