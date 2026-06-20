from datetime import datetime, timedelta, timezone
import unittest

from src.counter import LineZoneCounter
from src.tailgating_detector import EntryBurstDetector


class PhaseTwoDoorwayScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counter = LineZoneCounter(
            (0, 50),
            (100, 50),
            deadband_pixels=3,
            crossing_cooldown_seconds=0.5,
            same_direction_cooldown_seconds=8,
            rearm_distance_pixels=18,
            minimum_travel_pixels=18,
            max_jump_pixels=80,
        )
        self.detector = EntryBurstDetector(
            minimum_people=2,
            time_window_seconds=4,
            alert_cooldown_seconds=5,
        )
        self.wall_start = datetime.now(timezone.utc)

    def cross_in(self, tracker_id: int, second: float):
        self.counter.update(tracker_id, (50, 25), now=second)
        return self.counter.update(
            tracker_id, (50, 75), now=second + 0.5
        )

    def test_ten_separated_single_entries_create_no_alerts(self) -> None:
        alerts = []
        for index in range(10):
            second = index * 6
            crossing = self.cross_in(index + 1, second)
            self.assertIsNotNone(crossing)
            result = self.detector.handle_in_crossing(
                index + 1,
                self.wall_start + timedelta(seconds=second + 0.5),
            )
            alerts.append(result.is_tailgating)

        self.assertEqual(alerts, [False] * 10)

    def test_close_follower_with_different_id_creates_alert(self) -> None:
        first = self.cross_in(1, 0)
        second = self.cross_in(2, 2)

        first_result = self.detector.handle_in_crossing(
            first.tracker_id, self.wall_start + timedelta(seconds=0.5)
        )
        second_result = self.detector.handle_in_crossing(
            second.tracker_id, self.wall_start + timedelta(seconds=2.5)
        )

        self.assertFalse(first_result.is_tailgating)
        self.assertTrue(second_result.is_tailgating)

    def test_person_stopping_on_line_is_not_counted(self) -> None:
        self.counter.update(1, (50, 25), now=0)
        self.assertIsNone(self.counter.update(1, (50, 49), now=1))
        self.assertIsNone(self.counter.update(1, (50, 25), now=2))

    def test_person_turning_around_before_crossing_is_not_counted(self) -> None:
        self.counter.update(1, (50, 25), now=0)
        self.assertIsNone(self.counter.update(1, (50, 43), now=1))
        self.assertIsNone(self.counter.update(1, (50, 25), now=2))

    def test_one_person_line_jitter_cannot_create_tailgating(self) -> None:
        first = self.cross_in(46, 0)
        self.assertIsNotNone(first)
        self.detector.handle_in_crossing(
            first.tracker_id, self.wall_start + timedelta(seconds=0.5)
        )

        self.counter.update(46, (50, 25), now=1.5)
        duplicate = self.counter.update(46, (50, 75), now=2.5)

        self.assertIsNone(duplicate)

    def test_two_people_side_by_side_are_distinct_entries(self) -> None:
        first = self.cross_in(11, 0)
        second = self.cross_in(12, 0.2)

        self.detector.handle_in_crossing(
            first.tracker_id, self.wall_start + timedelta(seconds=0.5)
        )
        result = self.detector.handle_in_crossing(
            second.tracker_id, self.wall_start + timedelta(seconds=0.7)
        )

        self.assertTrue(result.is_tailgating)


if __name__ == "__main__":
    unittest.main()
