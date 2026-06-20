from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from threading import RLock


SECURITY_FIELDS = [
    "timestamp",
    "camera_name",
    "event_type",
    "tracker_id",
    "authorized_tokens_available",
    "total_in",
    "total_out",
    "current_inside",
    "snapshot_path",
]

COUNT_FIELDS = [
    "timestamp",
    "camera_name",
    "direction",
    "tracker_id",
    "total_in",
    "total_out",
    "current_inside",
]

GATE_FIELDS = [
    "timestamp",
    "camera_name",
    "event_type",
    "motion_ratio",
    "duration_seconds",
]


def local_iso_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    return current.isoformat(timespec="seconds")


class CsvLogger:
    def __init__(self, path: str | Path, fields: list[str]):
        self.path = Path(path)
        self.fields = fields
        self._lock = RLock()
        self._ensure_header()

    def write(self, row: dict[str, object]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.fields)
                writer.writerow({field: row.get(field, "") for field in self.fields})

    def _ensure_header(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists() or self.path.stat().st_size == 0:
                with self.path.open("w", newline="", encoding="utf-8") as handle:
                    csv.DictWriter(handle, fieldnames=self.fields).writeheader()


class SecurityEventLogger(CsvLogger):
    def __init__(self, path: str | Path = "logs/security_events.csv"):
        super().__init__(path, SECURITY_FIELDS)

    def log(
        self,
        *,
        camera_name: str,
        event_type: str,
        tracker_id: int,
        authorized_tokens_available: int,
        total_in: int,
        total_out: int,
        current_inside: int,
        snapshot_path: str = "",
        timestamp: datetime | None = None,
    ) -> None:
        self.write(
            {
                "timestamp": local_iso_timestamp(timestamp),
                "camera_name": camera_name,
                "event_type": event_type,
                "tracker_id": tracker_id,
                "authorized_tokens_available": authorized_tokens_available,
                "total_in": total_in,
                "total_out": total_out,
                "current_inside": current_inside,
                "snapshot_path": snapshot_path,
            }
        )

class GateEventLogger(CsvLogger):
    def __init__(self, path: str | Path = "logs/gate_events.csv"):
        super().__init__(path, GATE_FIELDS)

    def log(
        self,
        *,
        camera_name: str,
        event_type: str,
        motion_ratio: float,
        duration_seconds: float | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        self.write(
            {
                "timestamp": local_iso_timestamp(timestamp),
                "camera_name": camera_name,
                "event_type": event_type,
                "motion_ratio": round(float(motion_ratio), 4),
                "duration_seconds": (
                    "" if duration_seconds is None else duration_seconds
                ),
            }
        )


class PeopleCountLogger(CsvLogger):
    def __init__(self, path: str | Path = "logs/people_count_log.csv"):
        super().__init__(path, COUNT_FIELDS)

    def log(
        self,
        *,
        camera_name: str,
        direction: str,
        tracker_id: int,
        total_in: int,
        total_out: int,
        current_inside: int,
        timestamp: datetime | None = None,
    ) -> None:
        self.write(
            {
                "timestamp": local_iso_timestamp(timestamp),
                "camera_name": camera_name,
                "direction": direction,
                "tracker_id": tracker_id,
                "total_in": total_in,
                "total_out": total_out,
                "current_inside": current_inside,
            }
        )
