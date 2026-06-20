from __future__ import annotations

import logging
import math
import os
from typing import Any

from app.models.analytics_event import AnalyticsEvent, AnalyticsEventStore

from .frigate_client import FrigateClient
from .mqtt_publisher import AnalyticsMqttPublisher


logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class FrigateEventBridge:
    """Fan analytics events out to MQTT and Frigate without raising upstream."""

    def __init__(
        self,
        frigate: FrigateClient | None = None,
        mqtt: AnalyticsMqttPublisher | None = None,
        store: AnalyticsEventStore | None = None,
        *,
        enabled: bool | None = None,
        use_mqtt: bool | None = None,
        use_manual_events: bool | None = None,
        use_exports: bool | None = None,
        pre_roll_seconds: float | None = None,
        post_roll_seconds: float | None = None,
    ):
        self.frigate = frigate or FrigateClient()
        self.mqtt = mqtt or AnalyticsMqttPublisher()
        self.store = store or AnalyticsEventStore(
            os.getenv(
                "ANALYTICS_EVENT_STORE_PATH",
                "logs/analytics_events.jsonl",
            )
        )
        self.enabled = (
            _env_bool("USE_FRIGATE_BRIDGE", True)
            if enabled is None
            else enabled
        )
        self.use_mqtt = (
            _env_bool("USE_MQTT_ANALYTICS_EVENTS", True)
            if use_mqtt is None
            else use_mqtt
        )
        self.use_manual_events = (
            _env_bool("USE_FRIGATE_MANUAL_EVENTS", True)
            if use_manual_events is None
            else use_manual_events
        )
        self.use_exports = (
            _env_bool("USE_FRIGATE_EXPORTS", True)
            if use_exports is None
            else use_exports
        )
        self.pre_roll_seconds = float(
            pre_roll_seconds
            if pre_roll_seconds is not None
            else os.getenv("FRIGATE_EVENT_PRE_ROLL_SECONDS", "3")
        )
        self.post_roll_seconds = float(
            post_roll_seconds
            if post_roll_seconds is not None
            else os.getenv("FRIGATE_EVENT_POST_ROLL_SECONDS", "5")
        )

    def handle_event(self, event: AnalyticsEvent) -> dict[str, Any]:
        errors: list[str] = []
        result: dict[str, Any] = {
            "ok": True,
            "mqtt_published": False,
            "frigate_event_id": None,
            "frigate_export_id": None,
            "frigate_clip_url": None,
            "errors": errors,
        }
        try:
            if not self.enabled:
                result["disabled"] = True
                return result

            export_start = max(0.0, event.start_ts - self.pre_roll_seconds)
            export_end = max(
                export_start + 0.001,
                event.end_ts + self.post_roll_seconds,
            )

            if self.use_mqtt:
                try:
                    mqtt_result = self.mqtt.publish_event(event)
                    result["mqtt_published"] = bool(
                        mqtt_result.get("ok")
                    )
                    if not mqtt_result.get("ok"):
                        errors.append(
                            "mqtt: "
                            + str(
                                mqtt_result.get(
                                    "error", "publish failed"
                                )
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        "Unexpected MQTT bridge failure: %s", exc
                    )
                    errors.append(f"mqtt: {exc}")

            if self.use_manual_events:
                duration = max(
                    1,
                    math.ceil(
                        event.end_ts
                        - event.start_ts
                        + self.post_roll_seconds
                    ),
                )
                try:
                    manual_result = self.frigate.create_manual_event(
                        camera=event.camera,
                        label=event.label,
                        duration=duration,
                        include_recording=True,
                        sub_label=event.event_type,
                        score=event.confidence,
                        metadata={
                            "analytics_event_id": event.event_id,
                            **event.metadata,
                        },
                    )
                    if manual_result.get("ok"):
                        event.frigate_event_id = manual_result.get(
                            "event_id"
                        )
                        result["frigate_event_id"] = (
                            event.frigate_event_id
                        )
                    else:
                        errors.append(
                            "manual_event: "
                            + str(
                                manual_result.get(
                                    "error", "request failed"
                                )
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        "Unexpected manual event failure: %s", exc
                    )
                    errors.append(f"manual_event: {exc}")

            if self.use_exports:
                try:
                    export_result = self.frigate.export_recording(
                        camera=event.camera,
                        start_ts=export_start,
                        end_ts=export_end,
                        name=f"{event.label}-{event.event_id}",
                    )
                    if export_result.get("ok"):
                        event.frigate_export_id = export_result.get(
                            "export_id"
                        )
                        result["frigate_export_id"] = (
                            event.frigate_export_id
                        )
                    else:
                        errors.append(
                            "export: "
                            + str(
                                export_result.get(
                                    "error", "request failed"
                                )
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        "Unexpected export failure: %s", exc
                    )
                    errors.append(f"export: {exc}")

            try:
                event.frigate_clip_url = (
                    self.frigate.recording_clip_url(
                        event.camera, export_start, export_end
                    )
                )
                result["frigate_clip_url"] = event.frigate_clip_url
            except Exception as exc:
                logger.warning(
                    "Unable to build Frigate clip URL: %s", exc
                )
                errors.append(f"clip_url: {exc}")
            result["ok"] = not errors
        except Exception as exc:
            logger.exception("Unexpected Frigate bridge failure")
            errors.append(f"bridge: {exc}")
            result["ok"] = False
        finally:
            self._persist(event, result)
        return result

    def close(self) -> None:
        self.mqtt.close()
        self.frigate.close()

    def _persist(
        self,
        event: AnalyticsEvent,
        result: dict[str, Any],
    ) -> None:
        try:
            self.store.append(event, result)
        except Exception:
            logger.warning(
                "Unable to persist analytics bridge result for %s",
                event.event_id,
                exc_info=True,
            )
