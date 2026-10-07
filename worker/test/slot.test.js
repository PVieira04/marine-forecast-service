import { test } from "node:test";
import assert from "node:assert/strict";
import { slotFor } from "../src/slot.js";

test("summer (CEST): 05/17 UTC dispatch, 06/18 UTC skip", () => {
  assert.equal(slotFor(new Date("2026-07-15T05:00:00Z")), "morning");
  assert.equal(slotFor(new Date("2026-07-15T06:00:00Z")), null);
  assert.equal(slotFor(new Date("2026-07-15T17:00:00Z")), "evening");
  assert.equal(slotFor(new Date("2026-07-15T18:00:00Z")), null);
});

test("winter (CET): 06/18 UTC dispatch, 05/17 UTC skip", () => {
  assert.equal(slotFor(new Date("2026-12-15T05:00:00Z")), null);
  assert.equal(slotFor(new Date("2026-12-15T06:00:00Z")), "morning");
  assert.equal(slotFor(new Date("2026-12-15T17:00:00Z")), null);
  assert.equal(slotFor(new Date("2026-12-15T18:00:00Z")), "evening");
});

test("DST change days (2026-10-25 to CET, 2027-03-28 to CEST)", () => {
  assert.equal(slotFor(new Date("2026-10-25T06:00:00Z")), "morning");
  assert.equal(slotFor(new Date("2026-10-25T18:00:00Z")), "evening");
  assert.equal(slotFor(new Date("2027-03-28T05:00:00Z")), "morning");
  assert.equal(slotFor(new Date("2027-03-28T17:00:00Z")), "evening");
});

test("a late firing within the hour still matches", () => {
  assert.equal(slotFor(new Date("2026-07-15T05:00:42Z")), "morning");
});
