from datetime import datetime, timedelta, timezone
import unittest

from src.access_tokens import AccessTokenStore
from src.tailgating_detector import TailgatingDetector


class TailgatingDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.store = AccessTokenStore(token_valid_seconds=6)
        self.detector = TailgatingDetector(
            self.store,
            "Main Entrance",
            tailgating_time_window_seconds=4,
            alert_cooldown_seconds=5,
        )

    def test_one_scan_one_person_is_authorized(self) -> None:
        self.store.add_token("Main Entrance", timestamp=self.now)

        result = self.detector.handle_in_crossing(12, self.now)

        self.assertTrue(result.authorized)
        self.assertFalse(result.is_tailgating)

    def test_second_person_without_token_is_tailgating(self) -> None:
        self.store.add_token("Main Entrance", timestamp=self.now)
        first = self.detector.handle_in_crossing(12, self.now)
        second = self.detector.handle_in_crossing(
            13, self.now + timedelta(seconds=2)
        )

        self.assertTrue(first.authorized)
        self.assertEqual(second.event_type, "TAILGATING_DETECTED")
        self.assertEqual(second.reason, "CLOSE_FOLLOWING_WITHOUT_TOKEN")
        self.assertTrue(second.show_alert)

    def test_zero_scan_one_person_is_tailgating(self) -> None:
        result = self.detector.handle_in_crossing(23, self.now)

        self.assertEqual(result.event_type, "TAILGATING_DETECTED")
        self.assertEqual(result.reason, "NO_AUTHORIZATION_TOKEN")

    def test_alert_cooldown_does_not_suppress_event_logging(self) -> None:
        first = self.detector.handle_in_crossing(1, self.now)
        second = self.detector.handle_in_crossing(
            2, self.now + timedelta(seconds=1)
        )

        self.assertTrue(first.is_tailgating)
        self.assertTrue(second.is_tailgating)
        self.assertTrue(first.show_alert)
        self.assertFalse(second.show_alert)

    def test_new_over_capacity_door_group_triggers_once(self) -> None:
        result = self.detector.check_door_zone([4, 5], self.now)
        duplicate = self.detector.check_door_zone([4, 5], self.now)

        self.assertIsNotNone(result)
        self.assertEqual(result.event_type, "POSSIBLE_TAILGATING")
        self.assertIsNone(duplicate)


if __name__ == "__main__":
    unittest.main()
