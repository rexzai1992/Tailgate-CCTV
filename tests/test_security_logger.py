import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.security_logger import SecurityEventLogger


class SecurityLoggerTests(unittest.TestCase):
    def test_security_event_has_required_columns(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "security_events.csv"
            logger = SecurityEventLogger(path)
            logger.log(
                camera_name="Main Entrance",
                event_type="TAILGATING_DETECTED",
                tracker_id=23,
                authorized_tokens_available=0,
                total_in=55,
                total_out=12,
                current_inside=43,
                snapshot_path="captures/tailgating/example.jpg",
            )

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "TAILGATING_DETECTED")
            self.assertEqual(rows[0]["tracker_id"], "23")
            self.assertEqual(rows[0]["current_inside"], "43")


if __name__ == "__main__":
    unittest.main()
