from datetime import datetime, timedelta, timezone
import unittest

from src.access_tokens import AccessTokenStore


class AccessTokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)

    def test_token_is_consumed_once(self) -> None:
        store = AccessTokenStore(token_valid_seconds=6, max_people_per_token=1)
        store.add_token("Main Entrance", timestamp=self.now)

        self.assertIsNotNone(store.consume("Main Entrance", self.now))
        self.assertIsNone(store.consume("Main Entrance", self.now))
        self.assertEqual(store.available_count("Main Entrance", self.now), 0)

    def test_token_can_have_multiple_uses(self) -> None:
        store = AccessTokenStore(token_valid_seconds=6, max_people_per_token=2)
        store.add_token("Main Entrance", timestamp=self.now)

        self.assertIsNotNone(store.consume("Main Entrance", self.now))
        self.assertEqual(store.available_count("Main Entrance", self.now), 1)
        self.assertIsNotNone(store.consume("Main Entrance", self.now))
        self.assertEqual(store.available_count("Main Entrance", self.now), 0)

    def test_expired_token_is_not_consumed(self) -> None:
        store = AccessTokenStore(token_valid_seconds=6)
        store.add_token("Main Entrance", timestamp=self.now)

        self.assertIsNone(
            store.consume("Main Entrance", self.now + timedelta(seconds=7))
        )

    def test_tokens_are_camera_scoped(self) -> None:
        store = AccessTokenStore()
        store.add_token("Side Door", timestamp=self.now)

        self.assertIsNone(store.consume("Main Entrance", self.now))
        self.assertEqual(store.available_count("Side Door", self.now), 1)

    def test_future_dated_token_is_not_yet_valid(self) -> None:
        store = AccessTokenStore(token_valid_seconds=6)
        store.add_token(
            "Main Entrance", timestamp=self.now + timedelta(seconds=10)
        )

        self.assertIsNone(store.consume("Main Entrance", self.now))


if __name__ == "__main__":
    unittest.main()
