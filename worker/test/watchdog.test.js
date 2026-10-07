import { test } from "node:test";
import assert from "node:assert/strict";
import { checkSlot } from "../src/watchdog.js";

function fakeEnv(n) {
  const calls = { binds: [], alerts: [] };
  const env = {
    GITHUB_REPO: "o/r",
    ALERT_EMAIL: "a@example.com",
    RESEND_API_KEY: "k",
    RESEND_FROM_EMAIL: "f@example.com",
    DB: { prepare: () => ({ bind: (...a) => (calls.binds.push(a), { first: async () => ({ n }) }) }) },
  };
  globalThis.fetch = async (url, init) => (calls.alerts.push(JSON.parse(init.body)), new Response("{}"));
  return { env, calls };
}
const t = (iso) => new Date(iso).getTime();

test("summer evening sent: no alert, checks the 17:00 UTC slot", async () => {
  const { env, calls } = fakeEnv(1);
  assert.equal(await checkSlot(env, t("2026-07-15T17:30:00Z")), "ok");
  assert.deepEqual(calls.binds[0], ["evening", "2026-07-15T17:00:00.000Z"]);
  assert.equal(calls.alerts.length, 0);
});

test("winter morning missing: alerts", async () => {
  const { env, calls } = fakeEnv(0);
  assert.equal(await checkSlot(env, t("2026-12-15T06:30:00Z")), "alerted");
  assert.match(calls.alerts[0].subject, /No morning forecast sent/);
  assert.deepEqual(calls.alerts[0].to, ["a@example.com"]);
});

test("DST partner hour: no query, no alert", async () => {
  const { env, calls } = fakeEnv(0);
  assert.equal(await checkSlot(env, t("2026-07-15T06:30:00Z")), null);
  assert.equal(calls.binds.length + calls.alerts.length, 0);
});
