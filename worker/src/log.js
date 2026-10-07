// POST /log — records one forecast send in D1. Called by run_forecast.py
// (src/send_log.py) after each send, authenticated with LOG_TOKEN.

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function b64ToBytes(b64) {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

export async function handleLog(request, env) {
  const url = new URL(request.url);
  if (request.method !== "POST" || url.pathname !== "/log") {
    return new Response("Not found", { status: 404 });
  }
  if (!env.LOG_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.LOG_TOKEN}`) {
    return new Response("Unauthorised", { status: 401 });
  }

  const s = await request.json();
  const statements = [];
  const chartHashes = {};

  // Charts are stored once per content hash — DWD charts update less often than we send.
  for (const chart of s.charts ?? []) {
    const jpeg = b64ToBytes(chart.jpeg_b64);
    const hash = await sha256Hex(jpeg);
    chartHashes[chart.kind] = hash;
    statements.push(
      env.DB.prepare(
        "INSERT OR IGNORE INTO charts (sha256, kind, fetched_at, jpeg) VALUES (?, ?, ?, ?)",
      ).bind(hash, chart.kind, chart.fetched_at ?? null, jpeg),
    );
  }

  statements.push(
    env.DB.prepare(
      `INSERT INTO sends (sent_at, slot, status, test_mode, recipient, resend_id, subject, error,
         location_name, latitude, longitude, position_source, forecast_hours, wind_alert,
         forecast_json, chart_na_sha256, chart_eu_sha256)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      s.sent_at, s.slot, s.status, s.test_mode ? 1 : 0, s.recipient,
      s.resend_id ?? null, s.subject ?? null, s.error ?? null,
      s.location_name ?? null, s.latitude ?? null, s.longitude ?? null,
      s.position_source ?? null, s.forecast_hours ?? null,
      s.wind_alert == null ? null : s.wind_alert ? 1 : 0,
      s.forecast ? JSON.stringify(s.forecast) : null,
      chartHashes.na ?? null, chartHashes.eu ?? null,
    ),
  );

  const results = await env.DB.batch(statements);
  const sendId = results.at(-1).meta.last_row_id;
  return Response.json({ send_id: sendId, charts: chartHashes });
}
