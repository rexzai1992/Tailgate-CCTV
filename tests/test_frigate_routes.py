import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.frigate_routes import create_frigate_router


class FakeBridge:
    def handle_event(self, event):
        return {
            "ok": True,
            "mqtt_published": True,
            "frigate_event_id": "event-1",
            "frigate_export_id": "export-1",
            "frigate_clip_url": "http://frigate/clip.mp4",
            "errors": [],
        }


class FrigateRouteTests(unittest.TestCase):
    def test_post_event_calls_bridge(self) -> None:
        app = FastAPI()
        app.include_router(create_frigate_router(FakeBridge()))
        client = TestClient(app)

        response = client.post(
            "/api/v1/frigate/events",
            json={
                "event_id": "analytics-1",
                "camera": "gate01",
                "event_type": "TAILGATING_TEST",
                "start_ts": 1,
                "end_ts": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["frigate_export_id"], "export-1")


if __name__ == "__main__":
    unittest.main()
