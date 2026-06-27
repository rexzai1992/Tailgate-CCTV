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
    def test_discover_adds_to_existing_recipients(self, post) -> None:
        post.return_value = FakeResponse(
            {
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": 555, "title": "Ops"}}}
                ],
            }
        )
        with TemporaryDirectory() as directory:
            notifier = TelegramNotifier(Path(directory) / "telegram.json")
            notifier.save(enabled=True, chat_id="111, 222", bot_token="123:secret")

            status = notifier.discover_chat()
            self.assertEqual(status["added_chat_id"], "555")
            self.assertEqual(status["chat_id"], "111, 222, 555")

            # Finding the same chat again does not duplicate it.
            again = notifier.discover_chat()
            self.assertEqual(again["chat_id"], "111, 222, 555")

    @patch("src.telegram_notifier.requests.post")
    def test_discover_chats_lists_all_distinct(self, post) -> None:
        post.return_value = FakeResponse(
            {
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": 111, "title": "Ops"}}},
                    {"update_id": 2, "message": {"chat": {"id": 222, "first_name": "Alice"}}},
                    {"update_id": 3, "message": {"chat": {"id": 111, "title": "Ops"}}},
                ],
            }
        )
        with TemporaryDirectory() as directory:
            notifier = TelegramNotifier(Path(directory) / "telegram.json")
            notifier.save(enabled=True, chat_id="", bot_token="123:secret")
            result = notifier.discover_chats()
            ids = [chat["chat_id"] for chat in result["chats"]]
            self.assertEqual(ids, ["111", "222"])
            self.assertEqual(result["chats"][1]["chat_title"], "Alice")

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

    @patch("src.telegram_notifier.requests.post")
    def test_sends_to_multiple_recipients(self, post) -> None:
        post.return_value = FakeResponse({"ok": True, "result": {}})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "event.jpg"
            photo.write_bytes(b"jpeg")
            notifier = TelegramNotifier(root / "telegram.json")
            # A group id plus two individual users, separated by commas/newlines.
            notifier.save(
                enabled=True,
                chat_id="-1009999, 111\n222",
                bot_token="123:secret",
            )

            self.assertTrue(notifier.send_alert(str(photo), "Alert"))
            chat_ids = [call.kwargs["data"]["chat_id"] for call in post.call_args_list]
            self.assertEqual(chat_ids, ["-1009999", "111", "222"])


if __name__ == "__main__":
    unittest.main()
