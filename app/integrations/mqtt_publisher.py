from __future__ import annotations

import json
import logging
import os
from threading import RLock
from typing import Any
from uuid import uuid4

from app.models.analytics_event import AnalyticsEvent


logger = logging.getLogger(__name__)


class AnalyticsMqttPublisher:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        topic_prefix: str | None = None,
        *,
        client: Any | None = None,
    ):
        self.host = host or os.getenv("MQTT_HOST", "localhost")
        self.port = int(port or os.getenv("MQTT_PORT", "1883"))
        self.username = (
            username
            if username is not None
            else os.getenv("MQTT_USERNAME", "")
        )
        self.password = (
            password
            if password is not None
            else os.getenv("MQTT_PASSWORD", "")
        )
        self.topic_prefix = (
            topic_prefix
            or os.getenv(
                "MQTT_ANALYTICS_TOPIC_PREFIX", "analytics/events"
            )
        ).strip("/")
        self._client = client
        self._connected = False
        self._loop_started = False
        self._lock = RLock()
        self._import_error = ""

        if self._client is None:
            try:
                import paho.mqtt.client as mqtt

                self._client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"gym-sentry-{uuid4().hex[:10]}",
                )
            except (ImportError, AttributeError) as exc:
                self._import_error = str(exc)

        if self._client is not None and self.username:
            self._client.username_pw_set(self.username, self.password)

    def publish_event(
        self,
        event: AnalyticsEvent,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        topic = f"{self.topic_prefix}/{event.camera}"
        payload = event.model_dump(mode="json")
        if extra:
            payload.update(extra)
        try:
            with self._lock:
                if self._client is None:
                    raise RuntimeError(
                        self._import_error or "MQTT client is unavailable"
                    )
                if not self._connected:
                    self._client.connect(self.host, self.port, keepalive=30)
                    self._connected = True
                if not self._loop_started and hasattr(
                    self._client, "loop_start"
                ):
                    self._client.loop_start()
                    self._loop_started = True
                info = self._client.publish(
                    topic,
                    json.dumps(payload, separators=(",", ":")),
                    qos=1,
                    retain=False,
                )
                if hasattr(info, "wait_for_publish"):
                    info.wait_for_publish(timeout=2)
                rc = getattr(info, "rc", 0)
                if rc not in (0, None):
                    raise RuntimeError(f"MQTT publish returned code {rc}")
            return {"ok": True, "topic": topic}
        except Exception as exc:
            logger.warning("Analytics MQTT publish failed: %s", exc)
            self._connected = False
            return {"ok": False, "topic": topic, "error": str(exc)}

    def close(self) -> None:
        with self._lock:
            if self._client is None:
                return
            try:
                if self._loop_started:
                    self._client.loop_stop()
                if self._connected:
                    self._client.disconnect()
            except Exception:
                logger.debug("MQTT shutdown failed", exc_info=True)
            finally:
                self._connected = False
                self._loop_started = False
