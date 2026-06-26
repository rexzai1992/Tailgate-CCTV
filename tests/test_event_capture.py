from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from src.event_capture import EventCapture


class EventCaptureTests(unittest.TestCase):
    @staticmethod
    def _textured_person_frame() -> np.ndarray:
        frame = np.zeros((300, 300, 3), dtype=np.uint8)
        rng = np.random.default_rng(7)
        frame[40:240, 80:220] = rng.integers(
            30, 230, size=(200, 140, 3), dtype=np.uint8
        )
        return frame

    def test_clip_finalizes_when_tracker_leaves_frame(self) -> None:
        with TemporaryDirectory() as directory:
            capture = EventCapture(
                directory,
                save_event_clip=True,
                clip_fps=2,
                pre_seconds=2,
                exit_grace_seconds=1.0,
            )
            frame = np.zeros((120, 160, 3), dtype=np.uint8)
            capture.update(frame, 10.0, active_ids={5})
            capture.capture_event(
                frame,
                camera_name="Main Entrance",
                tracker_id=5,
                bbox=(20, 10, 100, 110),
                monotonic_now=10.0,
            )
            # Suspect still on screen — keep recording, do not finalize.
            self.assertEqual(capture.update(frame, 10.5, active_ids={5}), [])
            self.assertEqual(capture.update(frame, 11.0, active_ids={5}), [])
            # Suspect leaves; clip finalizes after the grace window.
            self.assertEqual(capture.update(frame, 11.4, active_ids=set()), [])
            completed = capture.update(frame, 12.2, active_ids=set())

            self.assertEqual(len(completed), 1)
            self.assertTrue(Path(completed[0]).exists())
            self.assertGreater(Path(completed[0]).stat().st_size, 0)

    def test_tailgating_body_crop_is_saved(self) -> None:
        with TemporaryDirectory() as directory:
            frame = np.zeros((300, 300, 3), dtype=np.uint8)
            frame[40:260, 80:220] = (60, 120, 210)
            capture = EventCapture(
                directory,
                capture_body_closeup=True,
                body_padding_percent=0.1,
            )
            path = capture.capture_body_closeup(
                frame,
                camera_name="Main Entrance",
                tracker_id=45,
                bbox=(80, 40, 220, 260),
                event_time=datetime.now().astimezone(),
            )

            image = cv2.imread(path)
            self.assertTrue(Path(path).exists())
            self.assertIsNotNone(image)
            self.assertIn("_body.jpg", path)

    def test_tiny_body_box_is_not_saved(self) -> None:
        with TemporaryDirectory() as directory:
            capture = EventCapture(directory, capture_body_closeup=True)
            path = capture.capture_body_closeup(
                np.zeros((100, 100, 3), dtype=np.uint8),
                camera_name="Main Entrance",
                tracker_id=1,
                bbox=(10, 10, 20, 30),
            )

            self.assertEqual(path, "")

    def test_tailgating_face_crop_is_saved_when_face_is_detected(self) -> None:
        with TemporaryDirectory() as directory:
            frame = self._textured_person_frame()
            capture = EventCapture(
                directory,
                capture_face_closeup=True,
                face_padding_percent=0.2,
            )
            with (
                patch.object(
                    capture, "_detect_faces", return_value=[(30, 20, 60, 70)]
                ),
                patch.object(
                    capture, "_detect_eyes", return_value=[(10, 12, 18, 16)]
                ),
            ):
                path = capture.capture_face_closeup(
                    frame,
                    camera_name="Main Entrance",
                    tracker_id=44,
                    bbox=(80, 40, 220, 240),
                    event_time=datetime.now().astimezone(),
                )

            image = cv2.imread(path)
            self.assertTrue(Path(path).exists())
            self.assertIsNotNone(image)
            self.assertIn("_face.jpg", path)

    def test_entry_face_crop_is_saved_when_face_is_detected(self) -> None:
        with TemporaryDirectory() as directory:
            entry_dir = Path(directory) / "entries"
            frame = self._textured_person_frame()
            capture = EventCapture(
                directory,
                capture_entry_face=True,
                entry_dir=entry_dir,
            )
            with (
                patch.object(
                    capture, "_detect_faces", return_value=[(30, 20, 60, 70)]
                ),
                patch.object(
                    capture, "_detect_eyes", return_value=[(10, 12, 18, 16)]
                ),
            ):
                path = capture.capture_entry_face(
                    frame,
                    camera_name="Main Entrance",
                    tracker_id=7,
                    bbox=(80, 40, 220, 240),
                    event_time=datetime.now().astimezone(),
                )

            self.assertTrue(Path(path).exists())
            self.assertIn("entry_", Path(path).name)
            self.assertEqual(Path(path).parent, entry_dir / "faces")

    def test_entry_face_disabled_saves_nothing(self) -> None:
        with TemporaryDirectory() as directory:
            capture = EventCapture(directory, capture_entry_face=False)
            path = capture.capture_entry_face(
                np.zeros((200, 200, 3), dtype=np.uint8),
                camera_name="Main Entrance",
                tracker_id=1,
                bbox=(20, 20, 180, 190),
            )
            self.assertEqual(path, "")

    def test_timestamp_is_burned_into_bottom_right(self) -> None:
        image = np.zeros((120, 320, 3), dtype=np.uint8)
        when = datetime(2026, 6, 19, 14, 30, 5).astimezone()
        EventCapture._stamp_timestamp(image, when)
        # The bottom-right region should now contain bright timestamp pixels.
        corner = image[90:120, 200:320]
        self.assertGreater(int(corner.max()), 200)
        # The top-left region should remain untouched (still black).
        self.assertEqual(int(image[0:30, 0:60].max()), 0)

    def test_no_face_does_not_save_closeup(self) -> None:
        with TemporaryDirectory() as directory:
            frame = np.zeros((200, 200, 3), dtype=np.uint8)
            capture = EventCapture(directory, capture_face_closeup=True)
            with patch.object(capture, "_detect_faces", return_value=[]):
                path = capture.capture_face_closeup(
                    frame,
                    camera_name="Main Entrance",
                    tracker_id=1,
                    bbox=(20, 20, 180, 190),
                )

            self.assertEqual(path, "")
            self.assertEqual(list((Path(directory) / "faces").glob("*.jpg")), [])

    def test_face_shape_without_detected_eye_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            frame = self._textured_person_frame()
            capture = EventCapture(directory, capture_face_closeup=True)
            with (
                patch.object(
                    capture, "_detect_faces", return_value=[(30, 20, 60, 70)]
                ),
                patch.object(capture, "_detect_eyes", return_value=[]),
            ):
                path = capture.capture_face_closeup(
                    frame,
                    camera_name="Main Entrance",
                    tracker_id=1,
                    bbox=(80, 40, 220, 240),
                )

            self.assertEqual(path, "")

    def test_blurry_face_candidate_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            frame = np.full((300, 300, 3), 120, dtype=np.uint8)
            capture = EventCapture(
                directory,
                capture_face_closeup=True,
                min_face_sharpness=35,
            )
            with (
                patch.object(
                    capture, "_detect_faces", return_value=[(30, 20, 60, 70)]
                ),
                patch.object(
                    capture, "_detect_eyes", return_value=[(10, 12, 18, 16)]
                ),
            ):
                path = capture.capture_face_closeup(
                    frame,
                    camera_name="Main Entrance",
                    tracker_id=1,
                    bbox=(80, 40, 220, 240),
                )

            self.assertEqual(path, "")

    def test_implausibly_wide_face_candidate_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            frame = self._textured_person_frame()
            capture = EventCapture(directory, capture_face_closeup=True)
            with (
                patch.object(
                    capture, "_detect_faces", return_value=[(10, 20, 120, 40)]
                ),
                patch.object(
                    capture, "_detect_eyes", return_value=[(10, 12, 18, 16)]
                ),
            ):
                path = capture.capture_face_closeup(
                    frame,
                    camera_name="Main Entrance",
                    tracker_id=1,
                    bbox=(80, 40, 220, 240),
                )

            self.assertEqual(path, "")


if __name__ == "__main__":
    unittest.main()
