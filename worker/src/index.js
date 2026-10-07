import { slotFor } from "./slot.js";
import { handleLog } from "./log.js";
import { handleWebhook } from "./webhook.js";
import { checkSlot } from "./watchdog.js";
import { sendAlert } from "./alert.js";

// Triggers forecast.yml via workflow_dispatch. GitHub's own `schedule` events
// were starting 4-8h late; dispatched runs start within seconds.
export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (request.method === "POST" && pathname === "/resend-webhook") return handleWebhook(request, env);
    return handleLog(request, env);
  },

  async scheduled(controller, env) {
    // "30 ..." cron = watchdog half an hour after each slot.
    if (controller.cron.startsWith("30 ")) {
      await checkSlot(env, controller.scheduledTime);
      return;
    }

    const slot = slotFor(new Date(controller.scheduledTime));
    if (!slot) {
      console.log(`Skipping ${controller.cron}: DST partner, not 07:00/19:00 Madrid`);
      return;
    }

    const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/forecast.yml/dispatches`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "marine-weather-agent-cron",
      },
      body: JSON.stringify({ ref: "main", inputs: { forecast_type: slot } }),
    });

    if (!res.ok) {
      const detail = `Dispatch failed (${res.status}): ${await res.clone().text()}`;
      await sendAlert(env, `Could not start the ${slot} forecast`, `${detail}\n\nCheck the Worker's GH_TOKEN.`);
      // Throwing marks the invocation as failed in the Cloudflare dashboard.
      throw new Error(`Dispatch failed (${res.status}): ${await res.text()}`);
    }
    console.log(`Dispatched ${slot} forecast`);
  },
};
