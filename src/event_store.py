from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

# Persistent event categories. Kept small and explicit so the /events API can
# validate the ``category`` query parameter against a known set.
CATEGORIES = ("security", "crossing", "gate")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    event_type TEXT,
    camera_name TEXT NOT NULL,
    tracker_id INTEGER,
    reason TEXT,
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL,
    snapshot_path TEXT,
    body_path TEXT,
    face_path TEXT,
    clip_path TEXT,
    total_in INTEGER,
    total_out INTEGER,
    current_inside INTEGER,
    motion_ratio REAL,
    duration_seconds REAL
);
CREATE INDEX IF NOT EXISTS idx_events_category_id ON events(category, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id DESC);
"""


class EventStore:
    """Thread-safe SQLite history of crossing, security, and gate events.

    External ``person_ref`` values are intentionally never written here; the
    store keeps only operational metadata and local evidence paths. Resetting
    the live counters must not touch this table, so there is no delete helper
    for normal operation.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        # check_same_thread=False because the camera capture thread, the API
        # request threads, and the Telegram executor all write through the same
        # connection guarded by ``self._lock``.
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record_event(
        self,
        category: str,
        camera_name: str,
        timestamp: datetime,
        event_type: str | None = None,
        tracker_id: int | None = None,
        reason: str | None = None,
        snapshot_path: str | None = None,
        body_path: str | None = None,
        face_path: str | None = None,
        clip_path: str | None = None,
        total_in: int | None = None,
        total_out: int | None = None,
        current_inside: int | None = None,
        motion_ratio: float | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown event category: {category}")
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        row = (
            category,
            event_type,
            camera_name,
            tracker_id,
            reason,
            timestamp.isoformat(timespec="seconds"),
            created_at,
            _clean_path(snapshot_path),
            _clean_path(body_path),
            _clean_path(face_path),
            _clean_path(clip_path),
            total_in,
            total_out,
            current_inside,
            motion_ratio,
            duration_seconds,
        )
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO events (
                    category, event_type, camera_name, tracker_id, reason,
                    timestamp, created_at, snapshot_path, body_path, face_path,
                    clip_path, total_in, total_out, current_inside,
                    motion_ratio, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def update_clip_path(self, event_id: int, clip_path: str) -> None:
        """Attach a video clip path to an event once it finishes encoding."""
        cleaned = _clean_path(clip_path)
        if not cleaned:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE events SET clip_path = ? WHERE id = ?",
                (cleaned, event_id),
            )
            self._conn.commit()

    def query(
        self,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        if category is not None and category not in CATEGORIES:
            raise ValueError(f"Unknown event category: {category}")
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        # Inclusive date-range filter on the YYYY-MM-DD prefix of the timestamp,
        # so timezone offsets in the stored ISO strings do not affect matching.
        if start:
            clauses.append("substr(timestamp, 1, 10) >= ?")
            params.append(str(start)[:10])
        if end:
            clauses.append("substr(timestamp, 1, 10) <= ?")
            params.append(str(end)[:10])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM events {where}", params
                ).fetchone()[0]
            )
            rows = self._conn.execute(
                f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def totals(self) -> dict[str, int]:
        """Lifetime persisted counts per category, used by /status."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT category, COUNT(*) AS n FROM events GROUP BY category"
            ).fetchall()
        counts = {category: 0 for category in CATEGORIES}
        for row in rows:
            counts[str(row["category"])] = int(row["n"])
        counts["all"] = sum(counts[category] for category in CATEGORIES)
        return counts

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _clean_path(path: str | None) -> str | None:
    if not path:
        return None
    return str(Path(path))
