import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from src.api_server import AccessEvent
from src.event_store import EventStore
from src.web_server import WebCameraProcessor, create_web_app


def _ts() -> datetime:
    return datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)


class FakeProcessor:
    """A lightweight stand-in for WebCameraProcessor that avoids loading YOLO.

    It reuses the real EventStore so /events behaves exactly as in production,
    and mirrors the small slice of the processor contract the endpoints touch.
    """

    def __init__(self, db_path: Path):
        self.config = {"tailgating": {"detection_mode": "entry_burst"}}
        self.ip_stream = None
        self.target_fps = 12.0
        self.event_store = EventStore(db_path)
        self.last_settings = None
        self.access_events: list[AccessEvent] = []

    def start_camera_stream(self) -> None:
        pass

    def close(self) -> None:
        self.event_store.close()

    def status(self) -> dict[str, object]:
        return {"ok": True, "detection_mode": "entry_burst", "event_totals": self.event_store.totals()}

    def query_events(self, category, limit, offset, start=None, end=None):
        result = self.event_store.query(
            category=category, limit=limit, offset=offset, start=start, end=end
        )
        return {
            **result,
            "items": [WebCameraProcessor._event_with_urls(row) for row in result["items"]],
        }

    def update_tailgating_settings(self, payload):
        mode = (payload.detection_mode or "entry_burst").lower()
        if mode not in {"entry_burst", "access_token"}:
            raise ValueError("detection_mode must be entry_burst or access_token")
        self.last_settings = payload
        return {"ok": True, "detection_mode": mode, "minimum_people": payload.minimum_people}

    def add_access_event(self, event: AccessEvent):
        if event.event_type != "face_id_authorized":
            raise ValueError("Only event_type=face_id_authorized creates an entry token")
        self.access_events.append(event)
        return {"ok": True, "tokens_available": 1, "message": "Access token added"}


class WebEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.processor = FakeProcessor(Path(self._tmp.name) / "gym_sentry.db")
        app = create_web_app(Path("config.yaml"), processor=self.processor)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_status(self) -> None:
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event_totals", response.json())

    def test_events_pagination_and_filter(self) -> None:
        store = self.processor.event_store
        store.record_event("crossing", "Main", _ts(), event_type="IN")
        store.record_event("security", "Main", _ts(), event_type="TAILGATING_DETECTED",
                           snapshot_path="captures/tailgating/snap.jpg")

        all_events = self.client.get("/events").json()
        self.assertEqual(all_events["total"], 2)
        self.assertEqual(all_events["limit"], 50)

        security = self.client.get("/events", params={"category": "security"}).json()
        self.assertEqual(security["total"], 1)
        item = security["items"][0]
        self.assertEqual(item["snapshot_url"], "/captures/tailgating/snap.jpg")
        self.assertNotIn("snapshot_path", item)

    def test_events_rejects_unknown_category(self) -> None:
        response = self.client.get("/events", params={"category": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_control_tailgating_valid(self) -> None:
        response = self.client.post("/control/tailgating", json={
            "enabled": True,
            "detection_mode": "access_token",
            "minimum_people": 3,
            "tailgating_time_window_seconds": 5,
            "token_valid_seconds": 8,
            "max_people_per_token": 2,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["detection_mode"], "access_token")
        self.assertEqual(self.processor.last_settings.minimum_people, 3)

    def test_control_tailgating_invalid_mode(self) -> None:
        response = self.client.post("/control/tailgating", json={
            "detection_mode": "telepathy",
        })
        self.assertEqual(response.status_code, 400)

    def test_control_tailgating_out_of_bounds(self) -> None:
        response = self.client.post("/control/tailgating", json={
            "minimum_people": 99,
        })
        self.assertEqual(response.status_code, 422)

    def test_access_event_valid(self) -> None:
        response = self.client.post("/access-event", json={
            "camera_name": "Main Entrance",
            "event_type": "face_id_authorized",
            "person_ref": "member-1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_access_event_rejects_other_types(self) -> None:
        response = self.client.post("/access-event", json={
            "camera_name": "Main Entrance",
            "event_type": "card_swipe",
        })
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
