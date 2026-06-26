import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from src.event_store import EventStore


def _ts() -> datetime:
    return datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)


class EventStoreTests(unittest.TestCase):
    def test_database_file_is_created(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nested" / "gym_sentry.db"
            store = EventStore(db_path)
            self.addCleanup(store.close)
            self.assertTrue(db_path.exists())

    def test_records_persist_across_reopen(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "gym_sentry.db"
            store = EventStore(db_path)
            store.record_event(
                category="security",
                camera_name="Main Entrance",
                timestamp=_ts(),
                event_type="TAILGATING_DETECTED",
                tracker_id=7,
                reason="2_PEOPLE_ENTERED_WITHIN_WINDOW",
            )
            store.close()

            reopened = EventStore(db_path)
            self.addCleanup(reopened.close)
            result = reopened.query()
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["event_type"], "TAILGATING_DETECTED")
            self.assertEqual(result["items"][0]["tracker_id"], 7)

    def test_category_filter_and_totals(self) -> None:
        with TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "gym_sentry.db")
            self.addCleanup(store.close)
            store.record_event("crossing", "Main", _ts(), event_type="IN")
            store.record_event("crossing", "Main", _ts(), event_type="OUT")
            store.record_event("security", "Main", _ts(), event_type="TAILGATING_DETECTED")
            store.record_event("gate", "Main", _ts(), event_type="gate_open")

            self.assertEqual(store.query(category="crossing")["total"], 2)
            self.assertEqual(store.query(category="security")["total"], 1)
            self.assertEqual(store.query(category="gate")["total"], 1)
            self.assertEqual(store.totals(), {
                "security": 1,
                "crossing": 2,
                "gate": 1,
                "all": 4,
            })

    def test_query_rejects_unknown_category(self) -> None:
        with TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "gym_sentry.db")
            self.addCleanup(store.close)
            with self.assertRaises(ValueError):
                store.query(category="bogus")
            with self.assertRaises(ValueError):
                store.record_event("bogus", "Main", _ts())

    def test_pagination_orders_newest_first(self) -> None:
        with TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "gym_sentry.db")
            self.addCleanup(store.close)
            for index in range(5):
                store.record_event(
                    "crossing", "Main", _ts(), tracker_id=index
                )
            page1 = store.query(limit=2, offset=0)
            page2 = store.query(limit=2, offset=2)
            self.assertEqual(page1["total"], 5)
            self.assertEqual([row["tracker_id"] for row in page1["items"]], [4, 3])
            self.assertEqual([row["tracker_id"] for row in page2["items"]], [2, 1])

    def test_limit_is_clamped(self) -> None:
        with TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "gym_sentry.db")
            self.addCleanup(store.close)
            self.assertEqual(store.query(limit=0)["limit"], 1)
            self.assertEqual(store.query(limit=10_000)["limit"], 500)

    def test_clip_path_update(self) -> None:
        with TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "gym_sentry.db")
            self.addCleanup(store.close)
            event_id = store.record_event(
                "security", "Main", _ts(), event_type="TAILGATING_DETECTED"
            )
            self.assertIsNone(store.query()["items"][0]["clip_path"])
            store.update_clip_path(event_id, "captures/tailgating/clip.mp4")
            updated = store.query()["items"][0]
            self.assertEqual(updated["clip_path"], "captures/tailgating/clip.mp4")

    def test_concurrent_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "gym_sentry.db")
            self.addCleanup(store.close)

            def writer(index: int) -> None:
                store.record_event("crossing", "Main", _ts(), tracker_id=index)

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(writer, range(200)))

            self.assertEqual(store.query()["total"], 200)


if __name__ == "__main__":
    unittest.main()
