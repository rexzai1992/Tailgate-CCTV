import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.models.analytics_event import AnalyticsEvent, AnalyticsEventStore


class AnalyticsEventTests(unittest.TestCase):
    def test_mutable_defaults_are_isolated(self) -> None:
        first = AnalyticsEvent(
            event_id="one",
            camera="gate01",
            event_type="TAILGATING_DETECTED",
            start_ts=1,
            end_ts=2,
        )
        second = AnalyticsEvent(
            event_id="two",
            camera="gate01",
            event_type="TAILGATING_DETECTED",
            start_ts=1,
            end_ts=2,
        )

        first.track_ids.append(5)
        first.metadata["reason"] = "test"

        self.assertEqual(second.track_ids, [])
        self.assertEqual(second.metadata, {})

    def test_store_persists_frigate_references(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            store = AnalyticsEventStore(path)
            event = AnalyticsEvent(
                event_id="event-1",
                camera="gate01",
                event_type="TAILGATING_DETECTED",
                start_ts=1,
                end_ts=2,
                frigate_event_id="frigate-1",
            )

            store.append(event, {"ok": True})

            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                record["event"]["frigate_event_id"], "frigate-1"
            )
            self.assertTrue(record["bridge"]["ok"])


if __name__ == "__main__":
    unittest.main()
