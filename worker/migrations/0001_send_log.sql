-- One row per forecast send attempt (including test sends and failures).
CREATE TABLE sends (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  sent_at         TEXT NOT NULL,      -- UTC ISO 8601
  slot            TEXT NOT NULL CHECK (slot IN ('morning', 'evening')),
  status          TEXT NOT NULL,      -- sent | send_failed | error
  test_mode       INTEGER NOT NULL,   -- 0/1
  recipient       TEXT NOT NULL,
  resend_id       TEXT,
  subject         TEXT,
  error           TEXT,
  location_name   TEXT,
  latitude        REAL,
  longitude       REAL,
  position_source TEXT,               -- ais_live | ais_stale | default
  forecast_hours  INTEGER,
  wind_alert      INTEGER,            -- 0/1, any time block over threshold
  forecast_json   TEXT,               -- hourly weather + marine values per model, as sent
  chart_na_sha256 TEXT REFERENCES charts (sha256),
  chart_eu_sha256 TEXT REFERENCES charts (sha256)
);
CREATE INDEX sends_sent_at ON sends (sent_at);

-- Chart images as embedded in the email (compressed JPEG), stored once per content hash.
CREATE TABLE charts (
  sha256     TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,           -- na | eu
  fetched_at TEXT,
  jpeg       BLOB NOT NULL
);
