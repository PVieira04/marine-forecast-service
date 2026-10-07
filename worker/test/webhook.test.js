import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { verifySvix } from "../src/webhook.js";

const rawKey = Buffer.from("test-signing-key-material-123456");
const secret = `whsec_${rawKey.toString("base64")}`;
const body = '{"type":"email.delivered","data":{"email_id":"abc"}}';
const ts = "1760000000";
const sign = (id, t, b) => createHmac("sha256", rawKey).update(`${id}.${t}.${b}`).digest("base64");

test("accepts a valid v1 signature among several", async () => {
  const header = `v1,bogus v1,${sign("msg_1", ts, body)}`;
  assert.equal(await verifySvix(secret, "msg_1", ts, header, body, Number(ts)), true);
});

test("rejects tampered body, wrong id, stale timestamp, missing headers", async () => {
  const header = `v1,${sign("msg_1", ts, body)}`;
  assert.equal(await verifySvix(secret, "msg_1", ts, header, body + " ", Number(ts)), false);
  assert.equal(await verifySvix(secret, "msg_2", ts, header, body, Number(ts)), false);
  assert.equal(await verifySvix(secret, "msg_1", ts, header, body, Number(ts) + 600), false);
  assert.equal(await verifySvix(secret, null, ts, header, body, Number(ts)), false);
});
