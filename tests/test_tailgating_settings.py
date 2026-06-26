import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from src.web_server import TailgatingSettingsPayload, WebCameraProcessor


class _StubModel:
    """Stands in for a loaded YOLO model so tests never touch the real weights."""

    names = {0: "person"}


def _base_config(tmp: Path) -> dict:
    return {
        "camera": {"name": "Main Entrance", "source": "0", "source_mode": "webcam",
                   "require_focus_area": False},
        "detection": {"model": "yolo11n.pt"},
        "counting_line": {"points": [[0.1, 0.55], [0.9, 0.55]]},
        "focus_area": {"enabled": False, "points": []},
        "tailgating": {
            "enabled": True,
            "detection_mode": "entry_burst",
            "minimum_people": 2,
            "token_valid_seconds": 6,
            "max_people_per_token": 1,
            "tailgating_time_window_seconds": 4,
            "snapshot_dir": str(tmp / "captures" / "tailgating"),
            "alert_cooldown_seconds": 5,
        },
        "door_zone": {"enabled": False, "points": []},
        "gate_zone": {"enabled": False, "points": []},
        "entry_capture": {"dir": str(tmp / "captures" / "entries")},
        "logging": {
            "people_count_csv": str(tmp / "people.csv"),
            "security_events_csv": str(tmp / "security.csv"),
            "gate_events_csv": str(tmp / "gate.csv"),
            "event_db": str(tmp / "data" / "gym_sentry.db"),
        },
    }


class TailgatingSettingsTests(unittest.TestCase):
    def _make_processor(self, tmp: Path) -> WebCameraProcessor:
        config = _base_config(tmp)
        config_path = tmp / "config.yaml"
        with config_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
        with patch("src.web_server.YOLO", return_value=_StubModel()):
            processor = WebCameraProcessor(config_path, config)
        self.addCleanup(processor.close)
        return processor

    def test_settings_apply_and_persist_to_config(self) -> None:
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            processor = self._make_processor(tmp)
            processor.update_tailgating_settings(
                TailgatingSettingsPayload(
                    enabled=True,
                    detection_mode="access_token",
                    minimum_people=4,
                    tailgating_time_window_seconds=7,
                    token_valid_seconds=9,
                    max_people_per_token=3,
                )
            )

            # Live state updated.
            self.assertEqual(processor.detection_mode, "access_token")
            self.assertEqual(processor.minimum_people, 4)
            self.assertEqual(processor.entry_burst_detector.minimum_people, 4)
            self.assertEqual(processor.token_store.token_valid_seconds, 9)
            self.assertEqual(processor.token_store.max_people_per_token, 3)

            # Persisted to disk.
            with (tmp / "config.yaml").open(encoding="utf-8") as handle:
                saved = yaml.safe_load(handle)
            self.assertEqual(saved["tailgating"]["detection_mode"], "access_token")
            self.assertEqual(saved["tailgating"]["minimum_people"], 4)
            self.assertEqual(saved["tailgating"]["max_people_per_token"], 3)

    def test_invalid_mode_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp_name:
            processor = self._make_processor(Path(tmp_name))
            with self.assertRaises(ValueError):
                processor.update_tailgating_settings(
                    TailgatingSettingsPayload(detection_mode="mindreading")
                )

    def test_mode_change_resets_transient_state_but_keeps_counts_and_history(self) -> None:
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            processor = self._make_processor(tmp)

            # Simulate accumulated live state and history.
            processor.total_in = 12
            processor.total_out = 5
            processor.token_store.add_token("Main Entrance")
            processor.event_store.record_event(
                "security", "Main Entrance",
                datetime(2026, 6, 21, tzinfo=timezone.utc),
                event_type="TAILGATING_DETECTED",
            )
            self.assertEqual(processor.token_store.available_count("Main Entrance"), 1)

            processor.update_tailgating_settings(
                TailgatingSettingsPayload(detection_mode="access_token")
            )

            # Counts and persistent history survive the mode switch.
            self.assertEqual(processor.total_in, 12)
            self.assertEqual(processor.total_out, 5)
            self.assertEqual(processor.event_store.query(category="security")["total"], 1)
            # Transient authorization tokens are cleared.
            self.assertEqual(processor.token_store.available_count("Main Entrance"), 0)


if __name__ == "__main__":
    unittest.main()
