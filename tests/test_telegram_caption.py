from datetime import datetime, timedelta, timezone
import unittest

from src.tailgating_detector import TailgatingResult
from src.web_server import WebCameraProcessor


class TelegramCaptionTests(unittest.TestCase):
    def test_caption_contains_date_time_and_timezone(self) -> None:
        processor = object.__new__(WebCameraProcessor)
        processor.camera_name = "Main Entrance"
        processor.total_in = 2
        processor.total_out = 0
        when = datetime(
            2026,
            6,
            19,
            10,
            22,
            7,
            tzinfo=timezone(timedelta(hours=8), "MYT"),
        )
        result = TailgatingResult(
            tracker_id=9,
            authorized=False,
            event_type="TAILGATING_DETECTED",
            reason="2_PEOPLE_ENTERED_WITHIN_WINDOW",
            tokens_available=0,
        )

        caption = processor._telegram_event_caption(result, when)

        self.assertIn("Date: 19 June 2026", caption)
        self.assertIn("Time: 10:22:07 AM", caption)
        self.assertIn("Timezone: MYT", caption)


if __name__ == "__main__":
    unittest.main()
