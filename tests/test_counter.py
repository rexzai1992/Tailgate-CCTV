import unittest

from src.counter import LineZoneCounter, point_in_polygon


class CounterTests(unittest.TestCase):
    def test_downward_crossing_is_in_for_left_to_right_line(self) -> None:
        counter = LineZoneCounter((0, 100), (200, 100), deadband_pixels=2)

        self.assertIsNone(counter.update(7, (100, 80), now=0))
        crossing = counter.update(7, (100, 120), now=2)

        self.assertIsNotNone(crossing)
        self.assertEqual(crossing.direction, "IN")

    def test_reverse_crossing_is_out(self) -> None:
        counter = LineZoneCounter((0, 100), (200, 100), deadband_pixels=2)

        counter.update(7, (100, 120), now=0)
        crossing = counter.update(7, (100, 80), now=2)

        self.assertEqual(crossing.direction, "OUT")

    def test_polygon_containment(self) -> None:
        polygon = [(0, 0), (100, 0), (100, 100), (0, 100)]
        self.assertTrue(point_in_polygon((50, 50), polygon))
        self.assertFalse(point_in_polygon((150, 50), polygon))

    def test_teleport_jump_is_not_counted(self) -> None:
        counter = LineZoneCounter(
            (0, 50), (100, 50), deadband_pixels=2,
            crossing_cooldown_seconds=0, max_jump_pixels=40,
        )
        self.assertIsNone(counter.update(1, (50, 90), now=0.0))
        # Feet leaving the frame snap the box bottom up across the line.
        self.assertIsNone(counter.update(1, (50, 10), now=0.2))

    def test_normal_step_still_counts_with_guard(self) -> None:
        counter = LineZoneCounter(
            (0, 50), (100, 50), in_side="negative", deadband_pixels=2,
            crossing_cooldown_seconds=0, max_jump_pixels=40,
        )
        self.assertIsNone(counter.update(1, (50, 65), now=0.0))
        self.assertIsNotNone(counter.update(1, (50, 40), now=0.2))

    def test_same_tracker_cannot_count_in_twice_during_cooldown(self) -> None:
        counter = LineZoneCounter(
            (0, 50),
            (100, 50),
            deadband_pixels=2,
            crossing_cooldown_seconds=0,
            same_direction_cooldown_seconds=8,
            rearm_distance_pixels=10,
            minimum_travel_pixels=10,
        )
        counter.update(46, (50, 25), now=0)
        first = counter.update(46, (50, 75), now=1)
        counter.update(46, (50, 25), now=2)
        duplicate = counter.update(46, (50, 75), now=3)

        self.assertEqual(first.direction, "IN")
        self.assertIsNone(duplicate)

    def test_tracker_can_reenter_after_cooldown_and_full_rearm(self) -> None:
        counter = LineZoneCounter(
            (0, 50),
            (100, 50),
            deadband_pixels=2,
            crossing_cooldown_seconds=0,
            same_direction_cooldown_seconds=8,
            rearm_distance_pixels=10,
            minimum_travel_pixels=10,
        )
        counter.update(7, (50, 25), now=0)
        self.assertIsNotNone(counter.update(7, (50, 75), now=1))
        counter.update(7, (50, 25), now=10)
        reentry = counter.update(7, (50, 75), now=11)

        self.assertIsNotNone(reentry)
        self.assertEqual(reentry.direction, "IN")

    def test_small_anchor_jitter_is_not_a_crossing(self) -> None:
        counter = LineZoneCounter(
            (0, 50),
            (100, 50),
            deadband_pixels=2,
            crossing_cooldown_seconds=0,
            minimum_travel_pixels=20,
        )
        counter.update(3, (50, 43), now=0)
        self.assertIsNone(counter.update(3, (50, 57), now=1))

    def test_crossing_beyond_the_marks_is_not_counted(self) -> None:
        # Line segment spans x in [0, 200]. A crossing at x=350 is past the end
        # mark (on the line's infinite extension) and must not count.
        counter = LineZoneCounter((0, 100), (200, 100), deadband_pixels=2)
        self.assertIsNone(counter.update(7, (350, 80), now=0))
        self.assertIsNone(counter.update(7, (350, 120), now=2))

    def test_crossing_between_the_marks_still_counts(self) -> None:
        counter = LineZoneCounter((0, 100), (200, 100), deadband_pixels=2)
        self.assertIsNone(counter.update(7, (100, 80), now=0))
        self.assertIsNotNone(counter.update(7, (100, 120), now=2))

    def test_segment_margin_extends_the_marks(self) -> None:
        # With a 60px margin, a crossing just past the x=200 end mark counts.
        counter = LineZoneCounter(
            (0, 100), (200, 100), deadband_pixels=2, segment_margin_pixels=60
        )
        self.assertIsNone(counter.update(7, (240, 80), now=0))
        self.assertIsNotNone(counter.update(7, (240, 120), now=2))

    def test_track_status_reports_calibration_state(self) -> None:
        counter = LineZoneCounter((0, 50), (100, 50))
        counter.update(9, (50, 75), now=0)

        status = counter.track_status(9, now=0)

        self.assertEqual(status["line_side"], "IN SIDE")
        self.assertEqual(status["crossing_state"], "READY")


if __name__ == "__main__":
    unittest.main()
