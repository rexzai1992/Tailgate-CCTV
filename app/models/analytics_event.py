from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field


class AnalyticsEvent(BaseModel):
    event_id: str
    camera: str
    label: str = "tailgating"
    event_type: str
    start_ts: float
    end_ts: float
    severity: str = "medium"
    confidence: float | None = None
    track_ids: list[int] = Field(default_factory=list)
    bbox: list[int] | None = None
    snapshot_path: str | None = None
    face_crop_path: str | None = None
    body_crop_path: str | None = None
    local_clip_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    frigate_event_id: str | None = None
    frigate_export_id: str | None = None
    frigate_clip_url: str | None = None


class AnalyticsEventStore:
    """Small append-only event ledger for bridge references and errors.

    The existing project stores operational data in files rather than a SQL
    database. JSONL keeps that local-first behavior while preserving complete
    Frigate results without trying to rewrite previous CSV rows.
    """

    def __init__(self, path: str | Path = "logs/analytics_events.jsonl"):
        self.path = Path(path)
        self._lock = RLock()

    def append(
        self,
        event: AnalyticsEvent,
        bridge_result: dict[str, Any],
    ) -> None:
        record = {
            "persisted_at": datetime.now(timezone.utc).isoformat(),
            "event": event.model_dump(mode="json"),
            "bridge": bridge_result,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
