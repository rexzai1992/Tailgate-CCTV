import unittest

import numpy as np

from src.gate_detector import GateMotionDetector


class GateMotionDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.polygon = [(10, 10), (90, 10), (90, 90), (10, 90)]

    def test_identical_frames_report_no_motion(self) -> None:
        detector = GateMotionDetector(motion_threshold=0.02, min_pixels=20)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.assertIsNone(detector.update(frame, self.polygon, now=0.0))
        self.assertIsNone(detector.update(frame, self.polygon, now=0.1))
        self.assertEqual(detector.state, "STILL")

    def test_movement_start_then_end_after_idle(self) -> None:
        detector = GateMotionDetector(
            motion_threshold=0.02, idle_seconds=1.0, min_pixels=20
        )
        dark = np.zeros((100, 100, 3), dtype=np.uint8)
        bright = np.zeros((100, 100, 3), dtype=np.uint8)
        bright[10:90, 10:90] = 255

        detector.update(dark, self.polygon, now=0.0)  # prime previous frame
        start = detector.update(bright, self.polygon, now=0.2)
        self.assertIsNotNone(start)
        self.assertEqual(start.event_type, "GATE_MOVEMENT_START")
        self.assertEqual(detector.state, "MOVING")

        # Same frame again => no motion, but still within idle window.
        self.assertIsNone(detector.update(bright, self.polygon, now=0.4))
        self.assertEqual(detector.state, "MOVING")

        end = detector.update(bright, self.polygon, now=1.5)
        self.assertIsNotNone(end)
        self.assertEqual(end.event_type, "GATE_MOVEMENT_END")
        self.assertGreater(end.duration_seconds, 0)
        self.assertEqual(detector.state, "STILL")

    def test_reset_clears_state(self) -> None:
        detector = GateMotionDetector()
        bright = np.full((100, 100, 3), 255, dtype=np.uint8)
        detector.update(np.zeros((100, 100, 3), dtype=np.uint8), self.polygon, 0.0)
        detector.update(bright, self.polygon, 0.2)
        detector.reset()
        self.assertEqual(detector.state, "STILL")
        # After reset the next frame just primes again (no event).
        self.assertIsNone(detector.update(bright, self.polygon, 0.4))


if __name__ == "__main__":
    unittest.main()
