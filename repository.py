"""SQLite persistence. Every operation owns and closes its connection."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from models import TRANSITIONS, Status, StatusChange, TicketCreate


class MissingTicket(Exception):
    pass


class Conflict(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Repository:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL CHECK(priority IN ('low','normal','high')),
                    status TEXT NOT NULL CHECK(status IN ('open','in_progress','resolved','cancelled')),
                    version INTEGER NOT NULL CHECK(version > 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
                    kind TEXT NOT NULL,
                    old_status TEXT,
                    new_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tickets_status_id ON tickets(status, id);
                CREATE INDEX IF NOT EXISTS events_ticket_id ON events(ticket_id, id);
            """)

    def create(self, request: TicketCreate) -> dict:
        stamp = now()
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO tickets(title,description,priority,status,version,created_at,updated_at) "
                "VALUES (?,?,?,'open',1,?,?)",
                (request.title, request.description, request.priority.value, stamp, stamp),
            )
            ticket_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO events(ticket_id,kind,new_status,created_at) VALUES (?,'created','open',?)",
                (ticket_id, stamp),
            )
            return dict(
                connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            )

    def get(self, ticket_id: int) -> dict:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if row is None:
                raise MissingTicket
            return dict(row)

    def list_tickets(self, status: Status | None, limit: int, offset: int) -> dict:
        clause = " WHERE status=?" if status else ""
        params = (status.value,) if status else ()
        with self.connection() as connection:
            # Read count and rows from the same snapshot.
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*) FROM tickets" + clause, params).fetchone()[
                0
            ]
            rows = connection.execute(
                "SELECT * FROM tickets" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def change_status(self, ticket_id: int, request: StatusChange) -> dict:
        with self.connection() as connection:
            # Serialize writers before reading the version; prevent lost updates.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if row is None:
                raise MissingTicket
            if row["version"] != request.expected_version:
                raise Conflict("Stale version; fetch the ticket again before retrying.")
            old = Status(row["status"])
            if request.status not in TRANSITIONS[old]:
                raise Conflict(f"Transition {old.value} -> {request.status.value} is not allowed.")
            stamp = now()
            connection.execute(
                "UPDATE tickets SET status=?, version=version+1, updated_at=? WHERE id=?",
                (request.status.value, stamp, ticket_id),
            )
            connection.execute(
                "INSERT INTO events(ticket_id,kind,old_status,new_status,created_at) "
                "VALUES (?,'status_changed',?,?,?)",
                (ticket_id, old.value, request.status.value, stamp),
            )
            return dict(
                connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            )

    def events(self, ticket_id: int) -> list[dict]:
        self.get(ticket_id)
        with self.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM events WHERE ticket_id=? ORDER BY id", (ticket_id,)
                )
            ]

    def stats(self) -> dict:
        with self.connection() as connection:
            counts = {status.value: 0 for status in Status}
            for row in connection.execute(
                "SELECT status,COUNT(*) AS count FROM tickets GROUP BY status"
            ):
                counts[row["status"]] = row["count"]
            return {"total": sum(counts.values()), "by_status": counts}
