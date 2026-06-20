from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.telegram_notifier import TelegramNotifier


class FakeResponse:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = str(body)

    def json(self) -> dict:
        return self._body


class TelegramNotifierTests(unittest.TestCase):
    def test_settings_are_persisted_without_exposing_token(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "telegram.json"
            notifier = TelegramNotifier(path)
            status = notifier.save(
                enabled=True,
                chat_id="-100123",
                bot_token="123:secret",
            )

            self.assertTrue(path.exists())
            self.assertTrue(status["token_saved"])
            self.assertNotIn("bot_token", status)
            reloaded = TelegramNotifier(path)
            self.assertEqual(reloaded.chat_id, "-100123")
            self.assertEqual(reloaded.bot_token, "123:secret")

    @patch("src.telegram_notifier.requests.post")
    def test_discovers_chat_after_start_message(self, post) -> None:
        post.return_value = FakeResponse(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 1,
                        "message": {
                            "chat": {"id": 98765, "first_name": "Gym Owner"}
                        },
                    }
                ],
            }
        )
        with TemporaryDirectory() as directory:
            notifier = TelegramNotifier(Path(directory) / "telegram.json")
            status = notifier.discover_chat("123:secret")

            self.assertEqual(status["chat_id"], "98765")
            self.assertEqual(status["chat_title"], "Gym Owner")

    @patch("src.telegram_notifier.requests.post")
    def test_sends_photo_and_video(self, post) -> None:
        post.return_value = FakeResponse({"ok": True, "result": {}})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "event.jpg"
            video = root / "event.mp4"
            photo.write_bytes(b"jpeg")
            video.write_bytes(b"mp4")
            notifier = TelegramNotifier(root / "telegram.json")
            notifier.save(
                enabled=True,
                chat_id="12345",
                bot_token="123:secret",
            )

            self.assertTrue(notifier.send_alert(str(photo), "Alert"))
            self.assertTrue(notifier.send_video(str(video), "Video"))
            methods = [call.args[0].split("/")[-1] for call in post.call_args_list]
            self.assertEqual(methods, ["sendPhoto", "sendVideo"])


if __name__ == "__main__":
    unittest.main()
