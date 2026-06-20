from datetime import datetime, timedelta, timezone
import unittest

from src.tailgating_detector import EntryBurstDetector


class EntryBurstDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.detector = EntryBurstDetector(
            minimum_people=2,
            time_window_seconds=4,
            alert_cooldown_seconds=5,
        )

    def test_first_person_is_normal(self) -> None:
        first = self.detector.handle_in_crossing(1, self.now)

        self.assertFalse(first.is_tailgating)

    def test_second_person_inside_window_is_captured(self) -> None:
        self.detector.handle_in_crossing(1, self.now)
        second = self.detector.handle_in_crossing(
            2, self.now + timedelta(seconds=2)
        )

        self.assertEqual(second.event_type, "TAILGATING_DETECTED")
        self.assertEqual(second.reason, "2_PEOPLE_ENTERED_WITHIN_WINDOW")

    def test_slow_entries_do_not_form_a_group(self) -> None:
        self.detector.handle_in_crossing(1, self.now)
        second = self.detector.handle_in_crossing(
            2, self.now + timedelta(seconds=5)
        )

        self.assertFalse(second.is_tailgating)

    def test_same_tracker_twice_is_not_tailgating(self) -> None:
        self.detector.handle_in_crossing(46, self.now)
        duplicate = self.detector.handle_in_crossing(
            46, self.now + timedelta(seconds=2)
        )

        self.assertFalse(duplicate.is_tailgating)
        self.assertEqual(duplicate.reason, "DUPLICATE_TRACKER_IGNORED")


if __name__ == "__main__":
    unittest.main()
