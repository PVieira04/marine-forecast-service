-- Resend webhook events (delivered, bounced, complained, ...), joined to sends on resend_id.
CREATE TABLE email_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  resend_id   TEXT,
  type        TEXT NOT NULL,      -- e.g. email.delivered, email.bounced
  event_at    TEXT,
  recipients  TEXT,               -- JSON array
  detail      TEXT NOT NULL       -- raw webhook body
);
CREATE INDEX email_events_resend_id ON email_events (resend_id);
