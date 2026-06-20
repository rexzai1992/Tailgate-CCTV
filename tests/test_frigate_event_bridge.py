import unittest

from app.integrations.frigate_event_bridge import FrigateEventBridge
from app.models.analytics_event import AnalyticsEvent


class FakeMqtt:
    def __init__(self, calls: list[str], ok: bool = True):
        self.calls = calls
        self.ok = ok

    def publish_event(self, event, extra=None):
        self.calls.append("mqtt")
        return {"ok": self.ok, "error": "offline" if not self.ok else ""}

    def close(self):
        pass


class FakeFrigate:
    def __init__(self, calls: list[str], ok: bool = True):
        self.calls = calls
        self.ok = ok

    def create_manual_event(self, **kwargs):
        self.calls.append("manual")
        return (
            {"ok": True, "event_id": "frigate-event"}
            if self.ok
            else {"ok": False, "error": "offline"}
        )

    def export_recording(self, **kwargs):
        self.calls.append("export")
        return (
            {"ok": True, "export_id": "frigate-export"}
            if self.ok
            else {"ok": False, "error": "offline"}
        )

    def recording_clip_url(self, camera, start_ts, end_ts):
        self.calls.append("clip")
        return f"http://frigate/{camera}/{start_ts}/{end_ts}"

    def close(self):
        pass


class FakeStore:
    def __init__(self):
        self.records = []

    def append(self, event, result):
        self.records.append((event, result))


class FrigateEventBridgeTests(unittest.TestCase):
    def event(self):
        return AnalyticsEvent(
            event_id="analytics-1",
            camera="gate01",
            event_type="TAILGATING_DETECTED",
            start_ts=100,
            end_ts=103,
            track_ids=[1, 2],
        )

    def test_bridge_order_and_references(self) -> None:
        calls: list[str] = []
        store = FakeStore()
        bridge = FrigateEventBridge(
            frigate=FakeFrigate(calls),
            mqtt=FakeMqtt(calls),
            store=store,
            pre_roll_seconds=3,
            post_roll_seconds=5,
        )

        result = bridge.handle_event(self.event())

        self.assertEqual(calls, ["mqtt", "manual", "export", "clip"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["mqtt_published"])
        self.assertEqual(result["frigate_event_id"], "frigate-event")
        self.assertEqual(result["frigate_export_id"], "frigate-export")
        self.assertIn("/97.0/108.0", result["frigate_clip_url"])
        self.assertEqual(len(store.records), 1)

    def test_failures_are_returned_not_raised(self) -> None:
        calls: list[str] = []
        bridge = FrigateEventBridge(
            frigate=FakeFrigate(calls, ok=False),
            mqtt=FakeMqtt(calls, ok=False),
            store=FakeStore(),
        )

        result = bridge.handle_event(self.event())

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["errors"]), 3)


if __name__ == "__main__":
    unittest.main()
