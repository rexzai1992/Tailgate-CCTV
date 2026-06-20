import unittest

import httpx

from app.integrations.frigate_client import FrigateClient


class FrigateClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if "/events/" in request.url.path and request.url.path.endswith(
                "/create"
            ):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "message": "created",
                        "event_id": "frigate-event-1",
                    },
                )
            if "/export/" in request.url.path:
                return httpx.Response(
                    202,
                    json={
                        "success": True,
                        "message": "queued",
                        "export_id": "gate01_export1",
                        "status": "queued",
                    },
                )
            if request.url.path.endswith("/end"):
                return httpx.Response(200, json={"success": True})
            return httpx.Response(404, json={"message": "not found"})

        self.client = FrigateClient(
            base_url="http://frigate:5000/api",
            public_url="http://frigate:8971",
            transport=httpx.MockTransport(handler),
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_create_manual_event_uses_current_schema(self) -> None:
        result = self.client.create_manual_event(
            camera="gate01",
            label="tailgating",
            duration=8,
            include_recording=True,
            sub_label="TAILGATING_DETECTED",
            score=0.9,
            metadata={"analytics_event_id": "one"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["event_id"], "frigate-event-1")
        payload = __import__("json").loads(self.requests[-1].content)
        self.assertEqual(payload["duration"], 8)
        self.assertTrue(payload["include_recording"])
        self.assertEqual(payload["draw"]["analytics_event_id"], "one")

    def test_export_returns_export_id(self) -> None:
        result = self.client.export_recording(
            "gate01", 100.25, 110.75, "test export"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["export_id"], "gate01_export1")
        self.assertIn(
            "/api/export/gate01/start/100.25/end/110.75",
            str(self.requests[-1].url),
        )

    def test_recording_clip_url_uses_public_api(self) -> None:
        self.assertEqual(
            self.client.recording_clip_url("gate01", 1, 2),
            "http://frigate:8971/api/gate01/start/1/end/2/clip.mp4",
        )

    def test_connection_error_returns_error_object(self) -> None:
        client = FrigateClient(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectError("offline", request=request)
                )
            )
        )
        try:
            result = client.export_recording("gate01", 1, 2)
        finally:
            client.close()

        self.assertFalse(result["ok"])
        self.assertIn("offline", result["error"])


if __name__ == "__main__":
    unittest.main()
