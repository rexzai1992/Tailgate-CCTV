import json
import unittest

from app.integrations.mqtt_publisher import AnalyticsMqttPublisher
from app.models.analytics_event import AnalyticsEvent


class FakePublishInfo:
    rc = 0

    def wait_for_publish(self, timeout=None):
        self.timeout = timeout


class FakeMqttClient:
    def __init__(self):
        self.messages = []

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def connect(self, host, port, keepalive):
        self.connection = (host, port, keepalive)

    def loop_start(self):
        pass

    def publish(self, topic, payload, qos, retain):
        self.messages.append((topic, json.loads(payload), qos, retain))
        return FakePublishInfo()

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


class AnalyticsMqttPublisherTests(unittest.TestCase):
    def test_payload_contains_required_fields(self) -> None:
        client = FakeMqttClient()
        publisher = AnalyticsMqttPublisher(
            host="mqtt",
            username="frigate",
            password="secret",
            client=client,
        )
        event = AnalyticsEvent(
            event_id="one",
            camera="gate01",
            event_type="TAILGATING_DETECTED",
            start_ts=1,
            end_ts=2,
            track_ids=[7],
        )

        result = publisher.publish_event(event)

        self.assertTrue(result["ok"])
        topic, payload, qos, retain = client.messages[0]
        self.assertEqual(topic, "analytics/events/gate01")
        self.assertEqual(payload["event_id"], "one")
        self.assertIn("frigate_event_id", payload)
        self.assertIn("frigate_export_id", payload)
        self.assertIn("frigate_clip_url", payload)
        self.assertEqual(qos, 1)
        self.assertFalse(retain)


if __name__ == "__main__":
    unittest.main()
