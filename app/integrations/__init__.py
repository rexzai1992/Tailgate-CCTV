"""External NVR and messaging integrations."""

from .frigate_client import FrigateClient
from .frigate_event_bridge import FrigateEventBridge
from .mqtt_publisher import AnalyticsMqttPublisher

__all__ = [
    "AnalyticsMqttPublisher",
    "FrigateClient",
    "FrigateEventBridge",
]
