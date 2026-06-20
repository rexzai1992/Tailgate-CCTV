from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import RLock
import time
from typing import Any

import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
from pydantic import BaseModel, Field
import yaml
from ultralytics import YOLO

from .access_tokens import AccessTokenStore
from .api_server import AccessEvent
from .counter import LineZoneCounter, point_in_polygon
from .event_capture import EventCapture
from .gate_detector import GateMotionDetector
from .ip_camera import IpCameraStream
from .security_logger import (
    GateEventLogger,
    PeopleCountLogger,
    SecurityEventLogger,
)
from .tailgating_detector import (
    EntryBurstDetector,
    TailgatingDetector,
    TailgatingResult,
)
from .telegram_notifier import TelegramError, TelegramNotifier


Point = tuple[int, int]
BBox = tuple[int, int, int, int]


class SetupPayload(BaseModel):
    camera_device_id: str | None = None
    camera_device_label: str | None = None
    focus_points: list[list[float]] = Field(default_factory=list)
    focus_enabled: bool = False
    line_points: list[list[float]]
    count_out: bool = False
    door_points: list[list[float]] = Field(default_factory=list)
    door_enabled: bool = False
    gate_points: list[list[float]] = Field(default_factory=list)
    gate_enabled: bool = False


class CameraSettings(BaseModel):
    mode: str = "browser"
    source: str = "0"
    rtsp_transport: str = "tcp"
    target_fps: float = 12


class TelegramSettingsPayload(BaseModel):
    enabled: bool = True
    chat_id: str = ""
    bot_token: str | None = None


class TelegramDiscoverPayload(BaseModel):
    bot_token: str | None = None


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalized_to_pixels(
    points: list[list[float]], width: int, height: int
) -> list[Point]:
    return [
        (round(float(x) * width), round(float(y) * height))
        for x, y in points
    ]


def extract_tracks(result: Any) -> dict[int, BBox]:
    boxes = result.boxes
    if boxes is None or boxes.id is None:
        return {}
    ids = boxes.id.int().cpu().tolist()
    coordinates = boxes.xyxy.int().cpu().tolist()
    return {
        int(tracker_id): tuple(int(value) for value in bbox)
        for tracker_id, bbox in zip(ids, coordinates)
    }


class WebCameraProcessor:
    def __init__(self, config_path: Path, config: dict[str, Any]):
        self.config_path = config_path
        self.config = config
        camera_config = config.get("camera", {})
        detection_config = config.get("detection", {})
        tailgating_config = config.get("tailgating", {})
        entry_capture_config = config.get("entry_capture", {})
        logging_config = config.get("logging", {})

        self.camera_name = str(camera_config.get("name", "Main Entrance"))
        self.preferred_camera_device_id = str(
            camera_config.get("browser_device_id", "")
        )
        self.preferred_camera_device_label = str(
            camera_config.get("browser_device_label", "")
        )
        self.require_focus_area = bool(
            camera_config.get("require_focus_area", True)
        )
        # "browser" (default) = the dashboard pushes webcam frames; "ip" = the
        # server opens the camera/RTSP stream itself and streams it to the page.
        self.camera_mode = str(camera_config.get("mode", "browser")).lower()
        self.camera_source = camera_config.get("source", 0)
        self.rtsp_transport = str(camera_config.get("rtsp_transport", "tcp"))
        self.target_fps = float(camera_config.get("target_fps", 12))
        self.ip_stream: IpCameraStream | None = None
        self.detection_config = detection_config
        self.line_config = config.get("counting_line", {})
        self.focus_config = config.get("focus_area", {})
        self.door_config = config.get("door_zone", {})
        self.tailgating_config = tailgating_config
        self.detection_mode = str(
            tailgating_config.get("detection_mode", "entry_burst")
        )
        self.minimum_people = int(tailgating_config.get("minimum_people", 2))
        self.line_points = self._validated_line(self.line_config.get("points", []))
        self.count_out_enabled = bool(self.line_config.get("count_out", False))
        self.focus_points = self._validated_focus(
            self.focus_config.get("points", [])
        )
        self.focus_enabled = bool(
            self.focus_config.get("enabled", False) and self.focus_points
        )
        self.door_points = self._validated_polygon(self.door_config.get("points", []))
        self.door_enabled = bool(self.door_config.get("enabled", False))
        self.gate_config = config.get("gate_zone", {})
        self.gate_points = self._validated_polygon(self.gate_config.get("points", []))
        self.gate_enabled = bool(self.gate_config.get("enabled", False))

        self.token_store = AccessTokenStore(
            token_valid_seconds=float(
                tailgating_config.get("token_valid_seconds", 6)
            ),
            max_people_per_token=int(
                tailgating_config.get("max_people_per_token", 1)
            ),
        )
        self.detector = TailgatingDetector(
            token_store=self.token_store,
            camera_name=self.camera_name,
            enabled=bool(tailgating_config.get("enabled", True)),
            tailgating_time_window_seconds=float(
                tailgating_config.get("tailgating_time_window_seconds", 4)
            ),
            alert_cooldown_seconds=float(
                tailgating_config.get("alert_cooldown_seconds", 5)
            ),
        )
        self.entry_burst_detector = EntryBurstDetector(
            minimum_people=self.minimum_people,
            time_window_seconds=float(
                tailgating_config.get("tailgating_time_window_seconds", 4)
            ),
            alert_cooldown_seconds=float(
                tailgating_config.get("alert_cooldown_seconds", 5)
            ),
        )
        self.gate_detector = GateMotionDetector(
            motion_threshold=float(self.gate_config.get("motion_threshold", 0.02)),
            idle_seconds=float(self.gate_config.get("idle_seconds", 1.0)),
        )
        self.gate_state = "STILL"
        self.security_logger = SecurityEventLogger(
            logging_config.get("security_events_csv", "logs/security_events.csv")
        )
        self.gate_logger = GateEventLogger(
            logging_config.get("gate_events_csv", "logs/gate_events.csv")
        )
        self.count_logger = PeopleCountLogger(
            logging_config.get("people_count_csv", "logs/people_count_log.csv")
        )
        self.event_capture = EventCapture(
            output_dir=tailgating_config.get(
                "snapshot_dir", "captures/tailgating"
            ),
            capture_snapshot=bool(
                tailgating_config.get("capture_snapshot", True)
            ),
            capture_body_closeup=bool(
                tailgating_config.get("capture_body_closeup", True)
            ),
            body_padding_percent=float(
                tailgating_config.get("body_padding_percent", 0.12)
            ),
            capture_face_closeup=bool(
                tailgating_config.get("capture_face_closeup", True)
            ),
            face_padding_percent=float(
                tailgating_config.get("face_padding_percent", 0.3)
            ),
            min_face_pixels=int(
                tailgating_config.get("min_face_pixels", 36)
            ),
            save_event_clip=bool(
                tailgating_config.get("save_event_clip", False)
            ),
            clip_fps=float(tailgating_config.get("clip_fps", 10)),
            pre_seconds=float(tailgating_config.get("clip_pre_seconds", 2)),
            post_seconds=float(tailgating_config.get("clip_post_seconds", 3)),
            capture_entry_face=bool(
                entry_capture_config.get("capture_face", True)
            ),
            entry_dir=str(entry_capture_config.get("dir", "captures/entries")),
        )
        self.telegram = TelegramNotifier(
            config_path.parent / "secrets" / "telegram.json"
        )
        self.notification_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="telegram-alert",
        )
        self.pending_clip_notifications: dict[str, dict[str, object]] = {}

        self.model = YOLO(str(detection_config.get("model", "yolo11n.pt")))
        # Optional inference tuning: device ("cpu"/"mps"/"cuda"/0) and imgsz.
        self.inference_device = detection_config.get("device") or None
        self.inference_imgsz = detection_config.get("imgsz") or None
        # Face detection (Haar) is costly; only run it every Nth processed frame.
        self.face_sample_every = max(
            1, int(entry_capture_config.get("face_sample_every", 4))
        )
        self._frame_index = 0
        self._last_active_ids: set[int] = set()
        self.line_counter: LineZoneCounter | None = None
        self.frame_size: tuple[int, int] | None = None
        self.total_in = 0
        self.total_out = 0
        self.alert_until = 0.0
        self.alert_tracker: int | None = None
        self.alert_text = "NORMAL"
        self.last_frame_at: datetime | None = None
        self.last_access_event: dict[str, object] | None = None
        self.recent_events: deque[dict[str, object]] = deque(maxlen=20)
        self.recent_entries: deque[dict[str, object]] = deque(maxlen=20)
        self.recent_gate_events: deque[dict[str, object]] = deque(maxlen=20)
        self.lock = RLock()

    def process_frame(self, encoded_frame: bytes) -> dict[str, object]:
        if not encoded_frame:
            raise ValueError("Empty camera frame")
        frame = cv2.imdecode(
            np.frombuffer(encoded_frame, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            raise ValueError("Unable to decode camera frame")
        return self.process_array(frame)

    def process_array(self, frame: np.ndarray) -> dict[str, object]:
        with self.lock:
            height, width = frame.shape[:2]
            self._ensure_line_counter(width, height)
            assert self.line_counter is not None
            monotonic_now = time.monotonic()
            wall_now = datetime.now().astimezone()
            self.last_frame_at = wall_now

            if self.require_focus_area and not self.focus_enabled:
                self._buffer_clip_frame(frame, None, monotonic_now)
                return {
                    **self._status_locked(wall_now),
                    "frame_width": width,
                    "frame_height": height,
                    "tracks": [],
                }

            track_kwargs: dict[str, Any] = {
                "persist": True,
                "classes": [0],
                "conf": float(self.detection_config.get("confidence", 0.35)),
                "iou": float(self.detection_config.get("iou", 0.5)),
                "tracker": str(
                    self.detection_config.get("tracker", "bytetrack.yaml")
                ),
                "verbose": False,
            }
            if self.inference_device is not None:
                track_kwargs["device"] = self.inference_device
            if self.inference_imgsz is not None:
                track_kwargs["imgsz"] = int(self.inference_imgsz)
            results = self.model.track(frame, **track_kwargs)
            tracks = extract_tracks(results[0]) if results else {}
            anchors: dict[int, Point] = {}

            self._last_active_ids = set(tracks.keys())
            self.event_capture.retain_faces(set(tracks.keys()))
            self._frame_index += 1
            sample_faces = self._frame_index % self.face_sample_every == 0

            for tracker_id, bbox in tracks.items():
                x1, y1, x2, y2 = bbox
                anchor = ((x1 + x2) // 2, y2)
                anchors[tracker_id] = anchor
                if sample_faces:
                    self.event_capture.observe_face(
                        frame, tracker_id, bbox, wall_now
                    )
                crossing = self.line_counter.update(
                    tracker_id, anchor, monotonic_now
                )
                if crossing is None:
                    continue
                if crossing.direction == "IN":
                    self.total_in += 1
                    self._capture_entry_face(
                        frame, tracker_id, bbox, wall_now
                    )
                    security_result = (
                        self.entry_burst_detector.handle_in_crossing(
                            tracker_id, wall_now
                        )
                        if self.detection_mode == "entry_burst"
                        else self.detector.handle_in_crossing(
                            tracker_id, wall_now
                        )
                    )
                    if security_result.is_tailgating:
                        self._record_security_event(
                            security_result,
                            frame,
                            bbox,
                            wall_now,
                            monotonic_now,
                        )
                else:
                    if not self.count_out_enabled:
                        continue
                    self.total_out += 1
                self.count_logger.log(
                    camera_name=self.camera_name,
                    direction=crossing.direction,
                    tracker_id=tracker_id,
                    total_in=self.total_in,
                    total_out=self.total_out,
                    current_inside=max(0, self.total_in - self.total_out),
                    timestamp=wall_now,
                )

            self.line_counter.expire_inactive(
                set(tracks), monotonic_now
            )

            if (
                self.detection_mode != "entry_burst"
                and self.door_enabled
                and len(self.door_points) >= 3
            ):
                polygon = normalized_to_pixels(
                    self.door_points, width, height
                )
                door_trackers = [
                    tracker_id
                    for tracker_id, anchor in anchors.items()
                    if point_in_polygon(anchor, polygon)
                ]
                possible = self.detector.check_door_zone(
                    door_trackers, wall_now
                )
                if possible is not None:
                    self._record_security_event(
                        possible,
                        frame,
                        tracks.get(possible.tracker_id),
                        wall_now,
                        monotonic_now,
                    )
            else:
                self.detector.check_door_zone([], wall_now)

            self._update_gate(frame, width, height, wall_now, monotonic_now)

            if monotonic_now >= self.alert_until:
                self.alert_tracker = None
                self.alert_text = "NORMAL"

            result = {
                **self._status_locked(wall_now),
                "frame_width": width,
                "frame_height": height,
                "tracks": [
                    {
                        "tracker_id": tracker_id,
                        "bbox": list(bbox),
                        **self.line_counter.track_status(
                            tracker_id, monotonic_now
                        ),
                        "suspect": (
                            monotonic_now < self.alert_until
                            and tracker_id == self.alert_tracker
                        ),
                    }
                    for tracker_id, bbox in tracks.items()
                ],
            }
            # Buffer the *annotated* frame so saved clips show the boxes/arrows.
            self._buffer_clip_frame(frame, result, monotonic_now)
            return result

    def _buffer_clip_frame(
        self,
        frame: np.ndarray,
        result: dict[str, object] | None,
        monotonic_now: float,
    ) -> None:
        if result is None:
            clip_frame = frame
        else:
            # Clips show ONLY the tailgating suspect(s) — the tracker(s) with an
            # active recording — forced red for the whole clip. No green boxes,
            # no line/arrows.
            suspect_ids = self.event_capture.active_clip_tracker_ids()
            clip_tracks = [
                {**track, "suspect": True}
                for track in result.get("tracks", [])
                if track["tracker_id"] in suspect_ids
            ]
            clip_result = {**result, "tracks": clip_tracks}
            clip_frame = self.render_annotated(
                frame.copy(), clip_result, draw_guides=False
            )
        completed_clips = self.event_capture.update(
            clip_frame, monotonic_now, active_ids=self._last_active_ids
        )
        for completed_clip in completed_clips:
            self._complete_event_clip(completed_clip)

    def add_access_event(self, event: AccessEvent) -> dict[str, object]:
        if event.event_type != "face_id_authorized":
            raise ValueError(
                "Only event_type=face_id_authorized creates an entry token"
            )
        self.token_store.add_token(
            camera_name=event.camera_name,
            event_type=event.event_type,
            person_ref=event.person_ref,
            timestamp=event.timestamp,
        )
        received_at = datetime.now().astimezone()
        self.last_access_event = {
            "received_at": received_at.isoformat(timespec="seconds"),
            "camera_name": event.camera_name,
            "event_type": event.event_type,
            "person_ref": event.person_ref,
        }
        return {
            "ok": True,
            "tokens_available": self.token_store.available_count(
                event.camera_name
            ),
            "message": "Access token added",
        }

    def status(self) -> dict[str, object]:
        with self.lock:
            return self._status_locked(datetime.now().astimezone())

    def reset_counts(self) -> dict[str, object]:
        with self.lock:
            self.total_in = 0
            self.total_out = 0
            self.alert_until = 0.0
            self.alert_tracker = None
            self.alert_text = "NORMAL"
            self.recent_entries.clear()
            self.recent_gate_events.clear()
            self.detector.reset()
            self.entry_burst_detector.reset()
            self.gate_detector.reset()
            self.gate_state = "STILL"
            if self.line_counter is not None:
                self.line_counter.reset()
            return self._status_locked(datetime.now().astimezone())

    def start_camera_stream(self) -> None:
        """(Re)start the server-side capture thread to match the current mode."""
        if self.ip_stream is not None:
            self.ip_stream.stop()
            self.ip_stream = None
        if self.camera_mode == "ip":
            self.ip_stream = IpCameraStream(
                self,
                self.camera_source,
                transport=self.rtsp_transport,
                target_fps=self.target_fps,
            )
            self.ip_stream.start()

    def update_camera_settings(
        self, mode: str, source: str, rtsp_transport: str, target_fps: float
    ) -> dict[str, object]:
        with self.lock:
            self.camera_mode = (mode or "browser").lower()
            cleaned = str(source).strip()
            self.camera_source = cleaned if cleaned else 0
            self.rtsp_transport = (rtsp_transport or "tcp").strip() or "tcp"
            self.target_fps = max(1.0, float(target_fps or 12))
            camera = self.config.setdefault("camera", {})
            camera["mode"] = self.camera_mode
            camera["source"] = self.camera_source
            camera["rtsp_transport"] = self.rtsp_transport
            camera["target_fps"] = self.target_fps
            with self.config_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(self.config, handle, sort_keys=False)
        # Start/stop the capture thread outside the lock (the thread needs it).
        self.start_camera_stream()
        return self.status()

    def reset_tracking(self) -> dict[str, object]:
        """Clear tracker IDs and detection memory without touching IN/OUT totals."""
        with self.lock:
            self.alert_until = 0.0
            self.alert_tracker = None
            self.alert_text = "NORMAL"
            self.detector.reset()
            self.entry_burst_detector.reset()
            self.gate_detector.reset()
            self.gate_state = "STILL"
            if self.line_counter is not None:
                self.line_counter.reset()
            self._reset_model_tracker()
            return self._status_locked(datetime.now().astimezone())

    def _reset_model_tracker(self) -> None:
        """Reset YOLO/ByteTrack internal state so tracker IDs restart from 1."""
        predictor = getattr(self.model, "predictor", None)
        trackers = getattr(predictor, "trackers", None) if predictor else None
        for tracker in trackers or []:
            reset = getattr(tracker, "reset", None)
            if callable(reset):
                reset()

    def update_setup(self, payload: SetupPayload) -> dict[str, object]:
        focus_points = self._validated_focus(payload.focus_points)
        if payload.focus_enabled and not focus_points:
            raise ValueError("Focus area needs two corner points")
        line_points = self._validated_line(payload.line_points)
        door_points = self._validated_polygon(payload.door_points)
        gate_points = self._validated_polygon(payload.gate_points)
        with self.lock:
            self.preferred_camera_device_id = (
                payload.camera_device_id or ""
            )
            self.preferred_camera_device_label = (
                payload.camera_device_label or ""
            )
            self.focus_points = focus_points
            self.focus_enabled = payload.focus_enabled
            self.line_points = line_points
            self.count_out_enabled = payload.count_out
            self.door_points = door_points
            self.door_enabled = payload.door_enabled
            self.gate_points = gate_points
            self.gate_enabled = payload.gate_enabled
            self.gate_detector.reset()
            self.config.setdefault("camera", {})[
                "browser_device_id"
            ] = self.preferred_camera_device_id
            self.config["camera"][
                "browser_device_label"
            ] = self.preferred_camera_device_label
            self.config.setdefault("focus_area", {})["points"] = focus_points
            self.config["focus_area"]["enabled"] = self.focus_enabled
            self.config.setdefault("counting_line", {})["points"] = line_points
            self.config["counting_line"]["count_out"] = self.count_out_enabled
            self.config.setdefault("door_zone", {})["points"] = door_points
            self.config["door_zone"]["enabled"] = self.door_enabled
            self.config.setdefault("gate_zone", {})["points"] = gate_points
            self.config["gate_zone"]["enabled"] = self.gate_enabled
            with self.config_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(self.config, handle, sort_keys=False)
            self.line_counter = None
            if self.frame_size is not None:
                self._ensure_line_counter(*self.frame_size)
            return self._status_locked(datetime.now().astimezone())

    def close(self) -> None:
        if self.ip_stream is not None:
            self.ip_stream.stop()
        with self.lock:
            self.event_capture.close()
        self.notification_executor.shutdown(wait=False, cancel_futures=False)

    def save_telegram_settings(
        self, payload: TelegramSettingsPayload
    ) -> dict[str, object]:
        return self.telegram.save(
            enabled=payload.enabled,
            chat_id=payload.chat_id,
            bot_token=payload.bot_token,
        )

    def discover_telegram_chat(
        self, payload: TelegramDiscoverPayload
    ) -> dict[str, object]:
        return self.telegram.discover_chat(payload.bot_token)

    def test_telegram(self) -> dict[str, object]:
        return self.telegram.test()

    def _ensure_line_counter(self, width: int, height: int) -> None:
        if (
            self.line_counter is not None
            and self.frame_size == (width, height)
        ):
            return
        points = normalized_to_pixels(self.line_points, width, height)
        self.line_counter = LineZoneCounter(
            start=points[0],
            end=points[1],
            in_side=str(self.line_config.get("in_side", "positive")),
            deadband_pixels=float(
                self.line_config.get("deadband_pixels", 8)
            ),
            crossing_cooldown_seconds=float(
                self.line_config.get("crossing_cooldown_seconds", 1)
            ),
            same_direction_cooldown_seconds=float(
                self.line_config.get(
                    "same_direction_cooldown_seconds", 8
                )
            ),
            rearm_distance_pixels=float(
                self.line_config.get("rearm_distance_pixels", 24)
            ),
            minimum_travel_pixels=float(
                self.line_config.get("minimum_travel_pixels", 20)
            ),
            tracker_state_ttl_seconds=float(
                self.line_config.get("tracker_state_ttl_seconds", 30)
            ),
            max_jump_pixels=float(
                self.line_config.get("max_jump_percent", 0.5)
            )
            * height,
        )
        self.frame_size = (width, height)

    def _capture_entry_face(
        self,
        frame: np.ndarray,
        tracker_id: int,
        bbox: BBox | None,
        wall_now: datetime,
    ) -> None:
        """Save a face crop for an ordinary entry and surface it on the dashboard."""
        face_path = self.event_capture.capture_entry_face(
            frame=frame,
            camera_name=self.camera_name,
            tracker_id=tracker_id,
            bbox=bbox,
            event_time=wall_now,
        )
        if not face_path:
            return
        self.recent_entries.appendleft(
            {
                "timestamp": wall_now.isoformat(timespec="seconds"),
                "tracker_id": tracker_id,
                "face_url": self._snapshot_url(face_path),
                "member_status": "Not identified",
            }
        )

    def render_annotated(
        self,
        frame: np.ndarray,
        result: dict[str, object],
        draw_guides: bool = True,
    ) -> np.ndarray:
        """Draw person boxes and the alert banner onto a frame. When
        ``draw_guides`` is True (live view) the counting line, IN/OUT arrows and
        gate polygon are also drawn; saved clips pass False for a box-only view."""
        img = frame
        height, width = img.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        for track in result.get("tracks", []):
            suspect = bool(track.get("suspect"))
            # Clips (draw_guides=False) show only the red tailgating box.
            if not draw_guides and not suspect:
                continue
            x1, y1, x2, y2 = (int(v) for v in track["bbox"])
            color = (0, 0, 255) if suspect else (87, 220, 66)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 3 if suspect else 2)
            cv2.putText(
                img, f"ID {track['tracker_id']}", (x1 + 3, max(18, y1 - 6)),
                font, 0.6, color, 2, cv2.LINE_AA,
            )
        line_points = result.get("line_points") or []
        if draw_guides and len(line_points) == 2:
            sx, sy = int(line_points[0][0] * width), int(line_points[0][1] * height)
            ex, ey = int(line_points[1][0] * width), int(line_points[1][1] * height)
            cv2.line(img, (sx, sy), (ex, ey), (255, 184, 59), 2, cv2.LINE_AA)
            dx, dy = ex - sx, ey - sy
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            sign = 1 if result.get("in_side", "positive") == "positive" else -1
            nx, ny = sign * (-dy / length), sign * (dx / length)
            mx, my = (sx + ex) // 2, (sy + ey) // 2
            in_len = max(40, width // 9)
            cv2.arrowedLine(
                img, (mx, my), (int(mx + nx * in_len), int(my + ny * in_len)),
                (87, 220, 66), 3, tipLength=0.3,
            )
            cv2.putText(
                img, "IN", (int(mx + nx * (in_len + 6)), int(my + ny * (in_len + 6))),
                font, 0.6, (87, 220, 66), 2, cv2.LINE_AA,
            )
            if result.get("count_out"):
                out_len = max(30, width // 13)
                cv2.arrowedLine(
                    img, (mx, my),
                    (int(mx - nx * out_len), int(my - ny * out_len)),
                    (155, 142, 255), 3, tipLength=0.3,
                )
                cv2.putText(
                    img, "OUT",
                    (int(mx - nx * (out_len + 6)), int(my - ny * (out_len + 6))),
                    font, 0.6, (155, 142, 255), 2, cv2.LINE_AA,
                )
        gate_points = result.get("gate_points") or []
        if draw_guides and result.get("gate_enabled") and len(gate_points) >= 3:
            pts = np.array(
                [[int(p[0] * width), int(p[1] * height)] for p in gate_points],
                np.int32,
            )
            cv2.polylines(img, [pts], True, (230, 208, 57), 2, cv2.LINE_AA)
        if result.get("security_alert"):
            text = str(result.get("security", "ALERT"))
            (tw, th), _ = cv2.getTextSize(text, font, 0.9, 2)
            cx = max(6, (width - tw) // 2)
            cv2.rectangle(img, (cx - 12, 12), (cx + tw + 12, 12 + th + 16), (48, 24, 197), -1)
            cv2.putText(img, text, (cx, 12 + th + 4), font, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        return img

    def _update_gate(
        self,
        frame: np.ndarray,
        width: int,
        height: int,
        wall_now: datetime,
        monotonic_now: float,
    ) -> None:
        """Detect gate movement inside the gate zone and log start/end events."""
        if not (self.gate_enabled and len(self.gate_points) >= 3):
            self.gate_detector.reset()
            self.gate_state = "STILL"
            return
        polygon = normalized_to_pixels(self.gate_points, width, height)
        gate_event = self.gate_detector.update(frame, polygon, monotonic_now)
        self.gate_state = self.gate_detector.state
        if gate_event is None:
            return
        self.gate_logger.log(
            camera_name=self.camera_name,
            event_type=gate_event.event_type,
            motion_ratio=gate_event.motion_ratio,
            duration_seconds=gate_event.duration_seconds,
            timestamp=wall_now,
        )
        self.recent_gate_events.appendleft(
            {
                "timestamp": wall_now.isoformat(timespec="seconds"),
                "event_type": gate_event.event_type,
                "motion_ratio": round(gate_event.motion_ratio, 3),
                "duration_seconds": gate_event.duration_seconds,
            }
        )

    def _record_security_event(
        self,
        result: TailgatingResult,
        frame: np.ndarray,
        bbox: BBox | None,
        wall_now: datetime,
        monotonic_now: float,
    ) -> None:
        snapshot_path = self.event_capture.capture_event(
            frame=frame,
            camera_name=self.camera_name,
            tracker_id=result.tracker_id,
            bbox=bbox,
            event_time=wall_now,
            monotonic_now=monotonic_now,
            save_clip=result.event_type == "TAILGATING_DETECTED",
        )
        body_path = self.event_capture.capture_body_closeup(
            frame=frame,
            camera_name=self.camera_name,
            tracker_id=result.tracker_id,
            bbox=bbox,
            event_time=wall_now,
        )
        face_path = self.event_capture.capture_face_closeup(
            frame=frame,
            camera_name=self.camera_name,
            tracker_id=result.tracker_id,
            bbox=bbox,
            event_time=wall_now,
        )
        self.security_logger.log(
            camera_name=self.camera_name,
            event_type=result.event_type or "TAILGATING_DETECTED",
            tracker_id=result.tracker_id,
            authorized_tokens_available=result.tokens_available,
            total_in=self.total_in,
            total_out=self.total_out,
            current_inside=max(0, self.total_in - self.total_out),
            snapshot_path=snapshot_path,
            timestamp=wall_now,
        )
        clip_path = (
            self.event_capture.pending_clip_path(
                self.camera_name, result.tracker_id, wall_now
            )
            if result.event_type == "TAILGATING_DETECTED"
            else ""
        )
        event = {
            "timestamp": wall_now.isoformat(timespec="seconds"),
            "event_type": result.event_type,
            "tracker_id": result.tracker_id,
            "reason": result.reason,
            "snapshot_url": self._snapshot_url(snapshot_path),
            "body_url": self._snapshot_url(body_path),
            "face_url": self._snapshot_url(face_path),
            "clip_url": "",
            "clip_pending": bool(clip_path),
            "telegram_photo_sent": False,
            "telegram_video_sent": False,
        }
        self.recent_events.appendleft(event)
        # Send only one Telegram alert per incident: show_alert honours
        # alert_cooldown_seconds, so a burst of people produces a single message
        # instead of one per person.
        if result.show_alert:
            caption = self._telegram_event_caption(result, wall_now)
            if clip_path:
                self.pending_clip_notifications[str(Path(clip_path))] = {
                    "event": event,
                    "caption": caption,
                }
            self.notification_executor.submit(
                self._send_telegram_photo,
                event,
                snapshot_path,
                face_path,
                caption,
            )
        if result.show_alert and bool(
            self.tailgating_config.get("show_alert_on_screen", True)
        ):
            self.alert_until = monotonic_now + float(
                self.tailgating_config.get("alert_cooldown_seconds", 5)
            )
            self.alert_tracker = result.tracker_id
            self.alert_text = (
                "POSSIBLE TAILGATING"
                if result.event_type == "POSSIBLE_TAILGATING"
                else "TAILGATING DETECTED"
            )

    def _status_locked(self, now: datetime) -> dict[str, object]:
        security_alert = time.monotonic() < self.alert_until
        if not security_alert:
            self.alert_text = "NORMAL"
            self.alert_tracker = None
        return {
            "ok": True,
            "camera_name": self.camera_name,
            "preferred_camera_device_id": self.preferred_camera_device_id,
            "preferred_camera_device_label": self.preferred_camera_device_label,
            "total_in": self.total_in,
            "total_out": self.total_out,
            "current_inside": max(0, self.total_in - self.total_out),
            "tokens_available": self.token_store.available_count(
                self.camera_name, now
            ),
            "detection_mode": self.detection_mode,
            "minimum_people": self.minimum_people,
            "tailgating_time_window_seconds": float(
                self.tailgating_config.get(
                    "tailgating_time_window_seconds", 4
                )
            ),
            "security": self.alert_text,
            "security_alert": security_alert,
            "camera_mode": self.camera_mode,
            "camera_source": str(self.camera_source),
            "rtsp_transport": self.rtsp_transport,
            "target_fps": self.target_fps,
            "ip_camera": (
                self.ip_stream.status() if self.ip_stream is not None else None
            ),
            "line_points": self.line_points,
            "in_side": str(self.line_config.get("in_side", "positive")),
            "count_out": self.count_out_enabled,
            "focus_points": self.focus_points,
            "focus_enabled": self.focus_enabled,
            "focus_required": self.require_focus_area,
            "calibration_ready": (
                not self.require_focus_area or self.focus_enabled
            ),
            "detection_paused_reason": (
                "DRAW_FOCUS_AREA"
                if self.require_focus_area and not self.focus_enabled
                else ""
            ),
            "crossing_guard": {
                "same_direction_cooldown_seconds": float(
                    self.line_config.get(
                        "same_direction_cooldown_seconds", 8
                    )
                ),
                "rearm_distance_pixels": float(
                    self.line_config.get(
                        "rearm_distance_pixels", 24
                    )
                ),
                "minimum_travel_pixels": float(
                    self.line_config.get(
                        "minimum_travel_pixels", 20
                    )
                ),
            },
            "door_points": self.door_points,
            "door_enabled": self.door_enabled,
            "gate_points": self.gate_points,
            "gate_enabled": self.gate_enabled,
            "gate_state": self.gate_state if self.gate_enabled else "OFF",
            "last_frame_at": (
                self.last_frame_at.isoformat(timespec="seconds")
                if self.last_frame_at
                else None
            ),
            "last_access_event": self.last_access_event,
            "recent_events": list(self.recent_events),
            "recent_entries": list(self.recent_entries),
            "recent_gate_events": list(self.recent_gate_events),
            "telegram": self.telegram.safe_status(),
        }

    def _complete_event_clip(self, clip_path: str) -> None:
        path = str(Path(clip_path))
        pending = self.pending_clip_notifications.pop(path, None)
        if pending is None:
            return
        event = pending["event"]
        if isinstance(event, dict):
            event["clip_url"] = self._snapshot_url(path)
            event["clip_pending"] = False
        self.notification_executor.submit(
            self._send_telegram_video,
            event,
            path,
            str(pending["caption"]),
        )

    def _send_telegram_photo(
        self,
        event: dict[str, object],
        snapshot_path: str,
        face_path: str,
        caption: str,
    ) -> None:
        sent = self.telegram.send_alert(snapshot_path, caption)
        # Always report the face: send the close-up when captured, otherwise a
        # short note so it's clear no usable face was seen for this event.
        if face_path:
            self.telegram.send_alert(face_path, "👤 Face close-up")
        else:
            self.telegram.send_alert("", "👤 No face captured for this event.")
        with self.lock:
            event["telegram_photo_sent"] = sent

    def _send_telegram_video(
        self,
        event: dict[str, object],
        clip_path: str,
        caption: str,
    ) -> None:
        # Caption is already sent once with the photo alert; the follow-up video
        # goes out without a caption so the details are not duplicated.
        sent = self.telegram.send_video(clip_path, "")
        with self.lock:
            event["telegram_video_sent"] = sent

    def _telegram_event_caption(
        self,
        result: TailgatingResult,
        wall_now: datetime,
    ) -> str:
        return (
            "🚨 TAILGATING DETECTED\n"
            f"Camera: {self.camera_name}\n"
            f"Date: {wall_now.strftime('%d %B %Y')}\n"
            f"Time: {wall_now.strftime('%I:%M:%S %p')}\n"
            f"Timezone: {wall_now.tzname() or 'Asia/Kuala_Lumpur'}\n"
            f"Tracker: {result.tracker_id}\n"
            f"Reason: {result.reason}\n"
            f"IN: {self.total_in} | OUT: {self.total_out} | "
            f"Inside: {max(0, self.total_in - self.total_out)}"
        )

    @staticmethod
    def _validated_line(points: list[list[float]]) -> list[list[float]]:
        if len(points) != 2:
            return [[0.1, 0.55], [0.9, 0.55]]
        clean = WebCameraProcessor._clean_points(points)
        if clean[0] == clean[1]:
            raise ValueError("Counting line needs two different points")
        return clean

    @staticmethod
    def _validated_polygon(points: list[list[float]]) -> list[list[float]]:
        if not points:
            return []
        clean = WebCameraProcessor._clean_points(points)
        if len(clean) < 3:
            raise ValueError("Door zone needs at least three points")
        return clean

    @staticmethod
    def _validated_focus(points: list[list[float]]) -> list[list[float]]:
        if not points:
            return []
        clean = WebCameraProcessor._clean_points(points)
        if len(clean) == 2:
            x1 = min(clean[0][0], clean[1][0])
            y1 = min(clean[0][1], clean[1][1])
            x2 = max(clean[0][0], clean[1][0])
            y2 = max(clean[0][1], clean[1][1])
            if x2 - x1 < 0.05 or y2 - y1 < 0.05:
                raise ValueError("Focus area is too small")
            return [[x1, y1], [x2, y2]]
        if len(clean) >= 3:
            xs = [point[0] for point in clean]
            ys = [point[1] for point in clean]
            if max(xs) - min(xs) < 0.05 or max(ys) - min(ys) < 0.05:
                raise ValueError("Focus area is too small")
            return clean
        raise ValueError(
            "Focus area needs two opposite corners or at least three polygon points"
        )

    @staticmethod
    def _clean_points(points: list[list[float]]) -> list[list[float]]:
        clean: list[list[float]] = []
        for point in points:
            if len(point) != 2:
                raise ValueError("Each point must contain x and y")
            x, y = float(point[0]), float(point[1])
            if not 0 <= x <= 1 or not 0 <= y <= 1:
                raise ValueError("Setup coordinates must be normalized from 0 to 1")
            clean.append([round(x, 5), round(y, 5)])
        return clean

    @staticmethod
    def _snapshot_url(path: str) -> str:
        if not path:
            return ""
        normalized = Path(path).as_posix()
        marker = "captures/"
        if marker in normalized:
            return "/captures/" + normalized.split(marker, 1)[1]
        return ""


def create_web_app(config_path: Path) -> FastAPI:
    config = load_config(config_path)
    processor = WebCameraProcessor(config_path, config)
    dashboard_path = Path(__file__).with_name("web_dashboard.html")
    captures_dir = Path("captures").resolve()
    captures_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Gym Sentry Web", version="2.0")
    app.state.gym_sentry_config = config
    app.state.processor = processor

    processor.start_camera_stream()

    app.mount(
        "/captures",
        StaticFiles(directory=str(captures_dir), check_dir=False),
        name="captures",
    )

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(
            dashboard_path,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "service": "gym-sentry-web"}

    @app.get("/status")
    def status() -> dict[str, object]:
        return processor.status()

    @app.get("/video-feed")
    def video_feed() -> StreamingResponse:
        stream = processor.ip_stream
        if stream is None:
            raise HTTPException(
                status_code=404, detail="IP camera mode is not enabled"
            )
        frame_interval = 1.0 / max(1.0, processor.target_fps)

        def frames():
            while True:
                current = processor.ip_stream
                if current is None:
                    break
                jpeg = current.latest_jpeg()
                if jpeg:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                time.sleep(frame_interval)

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.post("/control/camera")
    def control_camera(settings: CameraSettings) -> dict[str, object]:
        return processor.update_camera_settings(
            mode=settings.mode,
            source=settings.source,
            rtsp_transport=settings.rtsp_transport,
            target_fps=settings.target_fps,
        )

    @app.post("/access-event")
    def access_event(event: AccessEvent) -> dict[str, object]:
        try:
            return processor.add_access_event(event)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/process-frame")
    async def process_frame(request: Request) -> dict[str, object]:
        content_length = int(request.headers.get("content-length", "0") or 0)
        if content_length > 3_000_000:
            raise HTTPException(status_code=413, detail="Camera frame is too large")
        encoded_frame = await request.body()
        try:
            return await asyncio.to_thread(
                processor.process_frame, encoded_frame
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/control/reset")
    def reset() -> dict[str, object]:
        return processor.reset_counts()

    @app.post("/control/reset-tracking")
    def reset_tracking() -> dict[str, object]:
        return processor.reset_tracking()

    @app.post("/control/setup")
    def save_setup(payload: SetupPayload) -> dict[str, object]:
        try:
            return processor.update_setup(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/telegram/settings")
    def telegram_settings(
        payload: TelegramSettingsPayload,
    ) -> dict[str, object]:
        return processor.save_telegram_settings(payload)

    @app.post("/telegram/discover-chat")
    def telegram_discover_chat(
        payload: TelegramDiscoverPayload,
    ) -> dict[str, object]:
        try:
            return processor.discover_telegram_chat(payload)
        except TelegramError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/telegram/test")
    def telegram_test() -> dict[str, object]:
        try:
            return processor.test_telegram()
        except TelegramError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.on_event("shutdown")
    def shutdown() -> None:
        processor.close()

    return app
