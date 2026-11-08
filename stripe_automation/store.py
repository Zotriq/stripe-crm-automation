"""SQLite-backed event log and action outbox.

Why an outbox: Stripe expects a 2xx within seconds and retries for days if it
doesn't get one. HubSpot, Slack or SMTP being slow or down must not lose a
sale or send a customer two welcome emails. So the webhook only records the
event and the actions it needs, then each action is executed and retried on
its own with exponential backoff.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,          -- Stripe event id: makes processing idempotent
    type        TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    kind            TEXT NOT NULL,         -- crm_upsert | email | slack
    payload         TEXT NOT NULL,         -- JSON
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | done | dead
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error      TEXT,
    UNIQUE (event_id, kind)
);
"""

MAX_ATTEMPTS = 6
BASE_DELAY_SECONDS = 30  # 30s, 1m, 2m, 4m, 8m, then dead-lettered


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Store:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def record_event(self, event_id: str, event_type: str, actions: list[tuple[str, dict]]) -> bool:
        """Store the event and its actions atomically. Returns False if the event was already seen."""
        now = utcnow().isoformat()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("INSERT INTO events (id, type, received_at) VALUES (?, ?, ?)", (event_id, event_type, now))
            self.conn.executemany(
                "INSERT INTO actions (event_id, kind, payload, next_attempt_at) VALUES (?, ?, ?, ?)",
                [(event_id, kind, json.dumps(payload), now) for kind, payload in actions],
            )
            self.conn.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            self.conn.execute("ROLLBACK")
            return False

    def due_actions(self, now: dt.datetime | None = None) -> list[sqlite3.Row]:
        now = (now or utcnow()).isoformat()
        return self.conn.execute(
            "SELECT * FROM actions WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY id", (now,)
        ).fetchall()

    def mark_done(self, action_id: int) -> None:
        self.conn.execute("UPDATE actions SET status = 'done', attempts = attempts + 1, last_error = NULL WHERE id = ?", (action_id,))

    def mark_failed(self, action_id: int, error: str, now: dt.datetime | None = None) -> str:
        row = self.conn.execute("SELECT attempts FROM actions WHERE id = ?", (action_id,)).fetchone()
        attempts = row["attempts"] + 1
        if attempts >= MAX_ATTEMPTS:
            status, next_at = "dead", None
        else:
            status = "pending"
            next_at = ((now or utcnow()) + dt.timedelta(seconds=BASE_DELAY_SECONDS * 2 ** (attempts - 1))).isoformat()
        self.conn.execute(
            "UPDATE actions SET status = ?, attempts = ?, last_error = ?, next_attempt_at = COALESCE(?, next_attempt_at) WHERE id = ?",
            (status, attempts, error[:500], next_at, action_id),
        )
        return status

    def summary(self) -> dict:
        rows = self.conn.execute("SELECT status, count(*) AS n FROM actions GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def dead_letters(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT a.id, a.event_id, e.type, a.kind, a.attempts, a.last_error FROM actions a "
            "JOIN events e ON e.id = a.event_id WHERE a.status = 'dead' ORDER BY a.id"
        ).fetchall()
        return [dict(r) for r in rows]

    def requeue(self, action_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE actions SET status = 'pending', attempts = 0, next_attempt_at = ? WHERE id = ? AND status = 'dead'",
            (utcnow().isoformat(), action_id),
        )
        return cur.rowcount == 1
