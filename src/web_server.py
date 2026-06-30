from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import errno
import os
from pathlib import Path
import tempfile
from threading import RLock
import time
from typing import Any

import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
from pydantic import BaseModel, Field
import torch
import yaml
from ultralytics import YOLO

from .access_tokens import AccessTokenStore
from .api_server import AccessEvent
from .counter import LineZoneCounter, point_in_polygon
from .event_capture import EventCapture
from .event_store import EventStore
from .gate_detector import GateMotionDetector
from .ip_camera import IpCameraStream
from .search_detector import (
    SearchSpec,
    estimate_shirt_color,
    inactive_search,
    object_inside_person,
    parse_search_query,
)
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
    source_mode: str | None = None
    mode: str | None = None
    source: str = "0"
    rtsp_transport: str = "tcp"
    target_fps: float = 12


class SearchPayload(BaseModel):
    query: str = ""


class TelegramSettingsPayload(BaseModel):
    enabled: bool = True
    chat_id: str = ""
    bot_token: str | None = None


class TelegramDiscoverPayload(BaseModel):
    bot_token: str | None = None


class TailgatingSettingsPayload(BaseModel):
    enabled: bool = True
    detection_mode: str = "entry_burst"
    minimum_people: int = Field(default=2, ge=2, le=10)
    tailgating_time_window_seconds: float = Field(default=4, gt=0, le=60)
    token_valid_seconds: float = Field(default=6, gt=0, le=120)
    max_people_per_token: int = Field(default=1, ge=1, le=10)
    inference_device: str = "auto"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration not found: {path}")
    if path.is_dir():
        raise IsADirectoryError(
            f"Configuration path is a directory, not a YAML file: {path}"
        )
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
    classes = boxes.cls.int().cpu().tolist()
    coordinates = boxes.xyxy.int().cpu().tolist()
    return {
        int(tracker_id): tuple(int(value) for value in bbox)
        for tracker_id, class_id, bbox in zip(ids, classes, coordinates)
        if int(class_id) == 0
    }


def extract_detections(
    result: Any,
    class_names: dict[int, str],
) -> list[dict[str, object]]:
    boxes = result.boxes
    if boxes is None:
        return []
    classes = boxes.cls.int().cpu().tolist()
    coordinates = boxes.xyxy.int().cpu().tolist()
    confidences = boxes.conf.cpu().tolist()
    ids: list[int | None]
    if boxes.id is None:
        ids = [None] * len(classes)
    else:
        ids = [int(value) for value in boxes.id.int().cpu().tolist()]
    return [
        {
            "class_id": int(class_id),
            "label": str(class_names.get(int(class_id), class_id)),
            "bbox": tuple(int(value) for value in bbox),
            "confidence": float(confidence),
            "tracker_id": tracker_id,
        }
        for class_id, bbox, confidence, tracker_id in zip(
            classes, coordinates, confidences, ids
        )
    ]


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
        legacy_mode = str(camera_config.get("mode", "browser")).lower()
        configured_source_mode = str(
            camera_config.get("source_mode", "")
        ).lower()
        self.source_mode = configured_source_mode or (
            "webcam" if legacy_mode == "browser" else "direct_rtsp"
        )
        self.camera_mode = (
            "browser" if self.source_mode == "webcam" else "ip"
        )
        self.camera_source = camera_config.get("source", 0)
        self.rtsp_transport = str(camera_config.get("rtsp_transport", "tcp"))
        self.target_fps = max(
            1.0, min(30.0, float(camera_config.get("target_fps", 12) or 12))
        )
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
            require_eye_confirmation=bool(
                tailgating_config.get("require_eye_confirmation", True)
            ),
            min_face_sharpness=float(
                tailgating_config.get("min_face_sharpness", 35)
            ),
            save_event_clip=bool(
                tailgating_config.get("save_event_clip", False)
            ),
            clip_fps=float(tailgating_config.get("clip_fps", 10)),
            pre_seconds=float(tailgating_config.get("clip_pre_seconds", 4)),
            post_seconds=float(tailgating_config.get("clip_post_seconds", 6)),
            # clip_post_seconds is how long recording continues after the suspect
            # leaves frame (the real post-roll knob). Recording also continues the
            # whole time they remain visible, up to clip_max_seconds.
            exit_grace_seconds=float(
                tailgating_config.get("clip_post_seconds", 6)
            ),
            max_clip_seconds=float(
                tailgating_config.get("clip_max_seconds", 30)
            ),
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
        self.pending_clip_events: dict[str, dict[str, object]] = {}
        # Maps a pending clip path to the persisted event row id so the DB
        # record can be updated once the clip finishes encoding.
        self.pending_clip_db_ids: dict[str, int] = {}
        # Last time a Telegram photo alert was sent (monotonic). Used by the
        # optional telegram_cooldown_seconds throttle (default 0 = every event).
        self._last_telegram_at = 0.0
        self.event_store = EventStore(
            logging_config.get("event_db", "data/gym_sentry.db")
        )

        self.model = YOLO(str(detection_config.get("model", "yolo11n.pt")))
        self.search_spec: SearchSpec = inactive_search()
        self.search_matches: list[dict[str, object]] = []
        self.search_last_checked_at: str | None = None
        # Optional inference tuning. "auto" prefers CUDA, then MPS, then CPU.
        self.inference_device_setting = str(
            detection_config.get("device", "auto") or "auto"
        )
        self.inference_device = self._resolve_inference_device(
            self.inference_device_setting
        )
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
                self.search_matches = []
                self.search_last_checked_at = wall_now.isoformat(
                    timespec="seconds"
                )
                self._buffer_clip_frame(frame, None, monotonic_now)
                return {
                    **self._status_locked(wall_now),
                    "frame_width": width,
                    "frame_height": height,
                    "tracks": [],
                }

            search_class_ids = (
                self.search_spec.class_ids
                if self.search_spec.active
                else ()
            )
            detection_classes = list(dict.fromkeys((0, *search_class_ids)))
            track_kwargs: dict[str, Any] = {
                "persist": True,
                "classes": detection_classes,
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
            model_result = results[0] if results else None
            tracks = extract_tracks(model_result) if model_result is not None else {}
            detections = (
                extract_detections(model_result, self.model.names)
                if model_result is not None
                else []
            )
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
                self.event_store.record_event(
                    category="crossing",
                    camera_name=self.camera_name,
                    timestamp=wall_now,
                    event_type=crossing.direction,
                    tracker_id=tracker_id,
                    reason="LINE_CROSSING",
                    total_in=self.total_in,
                    total_out=self.total_out,
                    current_inside=max(0, self.total_in - self.total_out),
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

            self._update_search_matches(
                frame,
                detections,
                tracks,
                wall_now,
            )
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

    def query_events(
        self,
        category: str | None,
        limit: int,
        offset: int,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, object]:
        try:
            result = self.event_store.query(
                category=category,
                limit=limit,
                offset=offset,
                start=start,
                end=end,
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return {
            **result,
            "items": [self._event_with_urls(row) for row in result["items"]],
        }

    @staticmethod
    def _event_with_urls(row: dict[str, object]) -> dict[str, object]:
        """Replace internal evidence paths with dashboard-usable URLs."""
        path_fields = {"snapshot_path", "body_path", "face_path", "clip_path"}
        enriched = {
            key: value for key, value in row.items() if key not in path_fields
        }
        to_url = WebCameraProcessor._snapshot_url
        enriched["snapshot_url"] = to_url(str(row.get("snapshot_path") or ""))
        enriched["body_url"] = to_url(str(row.get("body_path") or ""))
        enriched["face_url"] = to_url(str(row.get("face_path") or ""))
        enriched["clip_url"] = to_url(str(row.get("clip_path") or ""))
        return enriched

    def update_tailgating_settings(
        self, payload: TailgatingSettingsPayload
    ) -> dict[str, object]:
        mode = (payload.detection_mode or "entry_burst").lower()
        if mode not in {"entry_burst", "access_token"}:
            raise ValueError(
                "detection_mode must be entry_burst or access_token"
            )
        window = float(payload.tailgating_time_window_seconds)
        device_setting = (payload.inference_device or "auto").strip().lower()
        resolved_device = self._resolve_inference_device(device_setting)
        with self.lock:
            mode_changed = mode != self.detection_mode
            self.detection_mode = mode
            self.minimum_people = int(payload.minimum_people)
            tailgating = self.config.setdefault("tailgating", {})
            detection = self.config.setdefault("detection", {})
            tailgating["enabled"] = bool(payload.enabled)
            tailgating["detection_mode"] = mode
            tailgating["minimum_people"] = self.minimum_people
            tailgating["tailgating_time_window_seconds"] = window
            tailgating["token_valid_seconds"] = float(payload.token_valid_seconds)
            tailgating["max_people_per_token"] = int(payload.max_people_per_token)
            self.tailgating_config = tailgating
            detection["device"] = device_setting
            self.detection_config = detection
            self.inference_device_setting = device_setting
            self.inference_device = resolved_device

            alert_cooldown = float(tailgating.get("alert_cooldown_seconds", 5))
            self.detector.enabled = bool(payload.enabled)
            self.detector.window = timedelta(seconds=window)
            self.entry_burst_detector = EntryBurstDetector(
                minimum_people=self.minimum_people,
                time_window_seconds=window,
                alert_cooldown_seconds=alert_cooldown,
            )
            self.token_store.token_valid_seconds = float(
                payload.token_valid_seconds
            )
            self.token_store.max_people_per_token = int(
                payload.max_people_per_token
            )

            if mode_changed:
                # Switching modes clears authorization tokens and transient
                # detection state, but counts and event history are preserved.
                self.token_store.clear()
                self.detector.reset()
                self.entry_burst_detector.reset()
                self.alert_until = 0.0
                self.alert_tracker = None
                self.alert_text = "NORMAL"

            self._save_config_locked()
            return self._status_locked(datetime.now().astimezone())

    def _save_config_locked(self) -> None:
        """Persist the in-memory config to ``config_path``.

        Normal files are replaced atomically. Docker single-file bind mounts can
        reject ``os.replace`` with EBUSY because the destination is the mount
        point, so those fall back to rewriting the mounted file in place.
        """
        directory = self.config_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(directory),
            suffix=".tmp",
            delete=False,
        )
        temp_path = handle.name
        try:
            with handle:
                yaml.safe_dump(self.config, handle, sort_keys=False)
            try:
                os.replace(temp_path, self.config_path)
            except OSError as exc:
                if exc.errno not in {errno.EBUSY, errno.EXDEV, errno.EPERM}:
                    raise
                with self.config_path.open("w", encoding="utf-8") as target:
                    yaml.safe_dump(self.config, target, sort_keys=False)
                    target.flush()
                    os.fsync(target.fileno())
        except BaseException:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    def start_camera_stream(self) -> None:
        """(Re)start the server-side capture thread to match the current mode."""
        if self.ip_stream is not None:
            self.ip_stream.stop()
            self.ip_stream = None
        if self.source_mode != "webcam":
            self.ip_stream = IpCameraStream(
                self,
                self.effective_camera_source(),
                transport=self.rtsp_transport,
                target_fps=self.target_fps,
            )
            self.ip_stream.start()

    def effective_camera_source(self) -> str | int:
        return self.camera_source

    def update_camera_settings(
        self,
        source_mode: str,
        source: str,
        rtsp_transport: str,
        target_fps: float,
    ) -> dict[str, object]:
        cleaned_mode = (source_mode or "webcam").lower()
        if cleaned_mode not in {"webcam", "direct_rtsp", "local"}:
            raise ValueError(
                "source_mode must be webcam, local, or direct_rtsp"
            )
        with self.lock:
            self.source_mode = cleaned_mode
            self.camera_mode = (
                "browser" if cleaned_mode == "webcam" else "ip"
            )
            cleaned = str(source).strip()
            # In "local" mode the app captures a physical device server-side, so
            # the source is a camera index (default 0) rather than a stream URL.
            if cleaned_mode == "local":
                self.camera_source = cleaned if cleaned.isdigit() else "0"
            else:
                self.camera_source = cleaned if cleaned else 0
            self.rtsp_transport = (rtsp_transport or "tcp").strip() or "tcp"
            self.target_fps = max(1.0, min(30.0, float(target_fps or 12)))
            camera = self.config.setdefault("camera", {})
            camera["mode"] = self.camera_mode
            camera["source_mode"] = self.source_mode
            camera["source"] = self.camera_source
            camera["rtsp_transport"] = self.rtsp_transport
            camera["target_fps"] = self.target_fps
            self._save_config_locked()
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

    def update_search(self, query: str) -> dict[str, object]:
        with self.lock:
            self.search_spec = parse_search_query(
                query,
                self.model.names,
            )
            self.search_matches = []
            self.search_last_checked_at = None
            return self._status_locked(datetime.now().astimezone())

    def _reset_model_tracker(self) -> None:
        """Reset YOLO/ByteTrack internal state so tracker IDs restart from 1."""
        predictor = getattr(self.model, "predictor", None)
        trackers = getattr(predictor, "trackers", None) if predictor else None
        for tracker in trackers or []:
            reset = getattr(tracker, "reset", None)
            if callable(reset):
                reset()

    @staticmethod
    def _mps_available() -> bool:
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        is_available = getattr(mps, "is_available", None)
        return bool(is_available and is_available())

    @classmethod
    def _available_inference_devices(cls) -> dict[str, object]:
        cuda_available = torch.cuda.is_available()
        cuda_count = torch.cuda.device_count() if cuda_available else 0
        return {
            "cpu": True,
            "cuda": cuda_available,
            "cuda_device_count": cuda_count,
            "mps": cls._mps_available(),
        }

    @classmethod
    def _resolve_inference_device(cls, setting: object) -> str | int | None:
        value = str(setting or "auto").strip().lower()
        cuda_available = torch.cuda.is_available()
        cuda_count = torch.cuda.device_count() if cuda_available else 0
        if value in {"auto", ""}:
            if cuda_available:
                return 0
            if cls._mps_available():
                return "mps"
            return None
        if value in {"cpu", "cpu_only"}:
            return "cpu"
        if value in {"gpu", "gpu_only", "cuda"}:
            if not cuda_available:
                raise ValueError("GPU-only detection requires CUDA to be available")
            return 0
        if value in {"multi_gpu", "gpu_gpu", "gpu_and_gpu", "cuda_multi"}:
            if cuda_count < 2:
                raise ValueError("Multi-GPU detection requires at least two CUDA GPUs")
            return ",".join(str(index) for index in range(cuda_count))
        if value.isdigit():
            index = int(value)
            if not cuda_available or index >= cuda_count:
                raise ValueError(f"CUDA GPU {index} is not available")
            return index
        if "," in value and all(part.strip().isdigit() for part in value.split(",")):
            indexes = [int(part.strip()) for part in value.split(",")]
            if not cuda_available or any(index >= cuda_count for index in indexes):
                raise ValueError(f"CUDA GPUs {value} are not available")
            return ",".join(str(index) for index in indexes)
        raise ValueError(
            "inference_device must be auto, cpu, gpu, multi_gpu, or CUDA indexes"
        )

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
            self._save_config_locked()
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
        self.event_store.close()

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

    def discover_telegram_chats(
        self, payload: TelegramDiscoverPayload
    ) -> dict[str, object]:
        return self.telegram.discover_chats(payload.bot_token)

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
            segment_margin_pixels=float(
                self.line_config.get("segment_margin_pixels", 0)
            ),
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
        if draw_guides:
            for match in result.get("search", {}).get("matches", []):
                x1, y1, x2, y2 = (int(v) for v in match["bbox"])
                color = (0, 210, 255)
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 4)
                label = str(match.get("label", "MATCH")).upper()
                cv2.putText(
                    img,
                    label,
                    (x1 + 3, max(20, y1 - 8)),
                    font,
                    0.68,
                    color,
                    2,
                    cv2.LINE_AA,
                )
                object_bbox = match.get("object_bbox")
                if object_bbox:
                    ox1, oy1, ox2, oy2 = (int(v) for v in object_bbox)
                    cv2.rectangle(
                        img, (ox1, oy1), (ox2, oy2), (255, 184, 59), 2
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
        self.event_store.record_event(
            category="gate",
            camera_name=self.camera_name,
            timestamp=wall_now,
            event_type=gate_event.event_type,
            reason="GATE_MOTION",
            motion_ratio=round(gate_event.motion_ratio, 3),
            duration_seconds=gate_event.duration_seconds,
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
        event_id = self.event_store.record_event(
            category="security",
            camera_name=self.camera_name,
            timestamp=wall_now,
            event_type=result.event_type or "TAILGATING_DETECTED",
            tracker_id=result.tracker_id,
            reason=result.reason,
            snapshot_path=snapshot_path,
            body_path=body_path,
            face_path=face_path,
            total_in=self.total_in,
            total_out=self.total_out,
            current_inside=max(0, self.total_in - self.total_out),
        )
        if clip_path:
            normalized_clip = str(Path(clip_path))
            self.pending_clip_events[normalized_clip] = event
            self.pending_clip_db_ids[normalized_clip] = event_id
        caption = self._telegram_event_caption(result, wall_now)
        # Send a captioned photo alert for EVERY tailgating event. An optional
        # telegram_cooldown_seconds (default 0) can throttle floods if needed.
        telegram_cooldown = float(
            self.tailgating_config.get("telegram_cooldown_seconds", 0)
        )
        if monotonic_now - self._last_telegram_at >= telegram_cooldown:
            self._last_telegram_at = monotonic_now
            self.notification_executor.submit(
                self._send_telegram_photo,
                event,
                snapshot_path,
                face_path,
                caption,
            )
        # Send one captioned clip video per incident (show_alert honours
        # alert_cooldown_seconds) to avoid uploading a video per person.
        if result.show_alert and clip_path:
            self.pending_clip_notifications[str(Path(clip_path))] = {
                "event": event,
                "caption": caption,
            }
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
            "tailgating_settings": {
                "enabled": bool(self.tailgating_config.get("enabled", True)),
                "detection_mode": self.detection_mode,
                "minimum_people": self.minimum_people,
                "tailgating_time_window_seconds": float(
                    self.tailgating_config.get(
                        "tailgating_time_window_seconds", 4
                    )
                ),
                "token_valid_seconds": float(
                    self.tailgating_config.get("token_valid_seconds", 6)
                ),
                "max_people_per_token": int(
                    self.tailgating_config.get("max_people_per_token", 1)
                ),
            },
            "event_totals": self.event_store.totals(),
            "security": self.alert_text,
            "security_alert": security_alert,
            "search": self._search_status_locked(),
            "camera_mode": self.camera_mode,
            "source_mode": self.source_mode,
            "camera_source": str(self.camera_source),
            "camera_effective_source": str(
                self.effective_camera_source()
            ),
            "rtsp_transport": self.rtsp_transport,
            "target_fps": self.target_fps,
            "inference_device": (
                str(self.inference_device)
                if self.inference_device is not None
                else "cpu"
            ),
            "inference_device_setting": self.inference_device_setting,
            "available_inference_devices": self._available_inference_devices(),
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

    def _search_status_locked(self) -> dict[str, object]:
        spec = self.search_spec
        count = len(self.search_matches)
        if not spec.supported:
            message = spec.message
        elif not spec.active:
            message = spec.message
        elif count:
            noun = "match" if count == 1 else "matches"
            message = f"Found {count} {noun} for {spec.target}."
        else:
            message = spec.message
        return {
            "active": spec.active,
            "supported": spec.supported,
            "query": spec.query,
            "mode": spec.mode,
            "target": spec.target,
            "message": message,
            "count": count,
            "found": count > 0,
            "last_checked_at": self.search_last_checked_at,
            "matches": list(self.search_matches),
        }

    def _update_search_matches(
        self,
        frame: np.ndarray,
        detections: list[dict[str, object]],
        people: dict[int, BBox],
        wall_now: datetime,
    ) -> None:
        spec = self.search_spec
        matches: list[dict[str, object]] = []
        if spec.active and spec.mode == "object":
            for detection in detections:
                if int(detection["class_id"]) not in spec.class_ids:
                    continue
                matches.append(
                    {
                        "bbox": list(detection["bbox"]),
                        "label": str(detection["label"]),
                        "confidence": round(
                            float(detection["confidence"]), 3
                        ),
                        "tracker_id": detection["tracker_id"],
                    }
                )
        elif spec.active and spec.mode == "person_with_object":
            objects = [
                detection
                for detection in detections
                if int(detection["class_id"]) in spec.class_ids
            ]
            for tracker_id, person_bbox in people.items():
                for detection in objects:
                    object_bbox = detection["bbox"]
                    if not object_inside_person(object_bbox, person_bbox):
                        continue
                    matches.append(
                        {
                            "bbox": list(person_bbox),
                            "object_bbox": list(object_bbox),
                            "label": (
                                f"person with {detection['label']}"
                            ),
                            "confidence": round(
                                float(detection["confidence"]), 3
                            ),
                            "tracker_id": tracker_id,
                        }
                    )
                    break
        elif spec.active and spec.mode == "shirt_color":
            for tracker_id, person_bbox in people.items():
                color, score = estimate_shirt_color(frame, person_bbox)
                if color != spec.color:
                    continue
                matches.append(
                    {
                        "bbox": list(person_bbox),
                        "label": f"{color} shirt",
                        "confidence": round(score, 3),
                        "tracker_id": tracker_id,
                    }
                )
        self.search_matches = matches
        self.search_last_checked_at = wall_now.isoformat(timespec="seconds")

    def _complete_event_clip(self, clip_path: str) -> None:
        path = str(Path(clip_path))
        notification = self.pending_clip_notifications.pop(path, None)
        event = self.pending_clip_events.pop(path, None)
        event_id = self.pending_clip_db_ids.pop(path, None)
        if event is not None:
            event["clip_url"] = self._snapshot_url(path)
            event["clip_pending"] = False
        if event_id is not None:
            self.event_store.update_clip_path(event_id, path)
        if notification is not None:
            notification_event = notification["event"]
            self.notification_executor.submit(
                self._send_telegram_video,
                notification_event,
                path,
                str(notification["caption"]),
            )

    def _send_telegram_photo(
        self,
        event: dict[str, object],
        snapshot_path: str,
        face_path: str,
        caption: str,
    ) -> None:
        sent = self.telegram.send_alert(snapshot_path, caption)
        # Send the face close-up when one was captured; skip a message otherwise
        # so rapid events do not spam a "no face" note.
        if face_path:
            self.telegram.send_alert(face_path, "👤 Face close-up")
        with self.lock:
            event["telegram_photo_sent"] = sent

    def _send_telegram_video(
        self,
        event: dict[str, object],
        clip_path: str,
        caption: str,
    ) -> None:
        # The clip carries the full event caption so it is self-explanatory even
        # if viewed on its own.
        sent = self.telegram.send_video(clip_path, caption)
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


def create_web_app(
    config_path: Path,
    processor: "WebCameraProcessor | None" = None,
) -> FastAPI:
    if processor is None:
        config = load_config(config_path)
        processor = WebCameraProcessor(config_path, config)
    else:
        config = processor.config
    dashboard_path = Path(__file__).with_name("web_dashboard.html")
    captures_dir = Path("captures").resolve()
    captures_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="CCTV Tailgate",
        version="2.0",
        description=(
            "CCTV Tailgate detection service. Counts entries across a virtual "
            "line, detects tailgating in either group-entry (`entry_burst`) or "
            "external-authorization (`access_token`) mode, captures evidence, and "
            "persists a queryable event history.\n\n"
            "**Integrating a plugin?** Depend only on the endpoints tagged "
            "**Plugin API** plus this published schema. The `Controls`, "
            "`Telegram`, and `Dashboard` endpoints are operational tooling and may "
            "change.\n\n"
            "The service has no built-in authentication and defaults to "
            "`127.0.0.1`. Place it behind an authenticated proxy or VPN if another "
            "machine must reach it."
        ),
        openapi_tags=[
            {
                "name": "Plugin API",
                "description": (
                    "Stable contract for external integrations: health, status, "
                    "event history, access authorizations, and frame processing."
                ),
            },
            {
                "name": "Controls",
                "description": (
                    "Administrative controls for camera source, detection "
                    "settings, live search, and counters. Intended for trusted "
                    "setup tools, not member scans."
                ),
            },
            {
                "name": "Telegram",
                "description": "Configure, discover, and test Telegram alert delivery.",
            },
            {
                "name": "Dashboard",
                "description": "Browser-facing dashboard page and live MJPEG stream.",
            },
        ],
    )
    app.state.gym_sentry_config = config
    app.state.processor = processor

    processor.start_camera_stream()

    app.mount(
        "/captures",
        StaticFiles(directory=str(captures_dir), check_dir=False),
        name="captures",
    )

    @app.get("/", tags=["Dashboard"], summary="Dashboard page", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(
            dashboard_path,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/health", tags=["Plugin API"], summary="Health check")
    def health() -> dict[str, object]:
        """Liveness probe. Returns `{"ok": true}` when the service is running.

        Call this on plugin startup and before retrying a failed request."""
        return {"ok": True, "service": "cctv-tailgate"}

    @app.get("/status", tags=["Plugin API"], summary="Live status snapshot")
    def status() -> dict[str, object]:
        """Current counts, camera and security state, active tailgating
        settings, persisted event totals, and short recent-event windows.

        Poll every 1–5 seconds; the service has no outbound webhooks."""
        return processor.status()

    @app.get("/events", tags=["Plugin API"], summary="Query event history")
    def events(
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, object]:
        """Persistent crossing, security, and gate events (newest first).

        - `category`: `security`, `crossing`, or `gate` (omit for all)
        - `limit`: page size, 1–500 (default 50)
        - `offset`: records to skip (default 0)
        - `start` / `end`: inclusive `YYYY-MM-DD` date range filter

        Returns `{items, total, limit, offset}`. Each item carries local
        evidence URLs where available. Survives restarts and counter resets."""
        try:
            return processor.query_events(
                category=category,
                limit=limit,
                offset=offset,
                start=start,
                end=end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/video-feed", tags=["Dashboard"], summary="Live MJPEG stream")
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

    @app.post("/control/camera", tags=["Controls"], summary="Set camera source")
    def control_camera(settings: CameraSettings) -> dict[str, object]:
        source_mode = settings.source_mode
        if not source_mode:
            source_mode = (
                "webcam"
                if (settings.mode or "browser").lower() == "browser"
                else "direct_rtsp"
            )
        try:
            return processor.update_camera_settings(
                source_mode=source_mode,
                source=settings.source,
                rtsp_transport=settings.rtsp_transport,
                target_fps=settings.target_fps,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/access-event", tags=["Plugin API"], summary="Submit an authorized access event")
    def access_event(event: AccessEvent) -> dict[str, object]:
        """Register one approved authorization (card, QR, face, fingerprint…).

        Send only after access is granted; `event_type` must be
        `face_id_authorized`. Creates one short-lived token the next IN crossing
        consumes (`access_token` mode). Do not retry a `200` or you may create
        duplicate tokens."""
        try:
            return processor.add_access_event(event)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/process-frame", tags=["Plugin API"], summary="Process a camera frame")
    async def process_frame(request: Request) -> dict[str, object]:
        """Run detection on a single JPEG frame supplied by the plugin.

        Body is raw JPEG bytes (`Content-Type: image/jpeg`), max 3 MB. Send
        frames sequentially in order; 8–12 fps is a practical rate. Returns the
        status fields plus `frame_width`, `frame_height`, and `tracks`."""
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

    @app.post("/control/reset", tags=["Controls"], summary="Reset counts")
    def reset() -> dict[str, object]:
        """Zero IN/OUT counts and clear detection state. Event history is kept."""
        return processor.reset_counts()

    @app.post("/control/reset-tracking", tags=["Controls"], summary="Reset tracking")
    def reset_tracking() -> dict[str, object]:
        """Restart tracker IDs and detection windows without changing counts."""
        return processor.reset_tracking()

    @app.post("/control/search", tags=["Controls"], summary="Set live search")
    def control_search(payload: SearchPayload) -> dict[str, object]:
        """Start or clear a live object / clothing-color search overlay."""
        return processor.update_search(payload.query)

    @app.post("/control/tailgating", tags=["Controls"], summary="Configure tailgating detection")
    def control_tailgating(
        payload: TailgatingSettingsPayload,
    ) -> dict[str, object]:
        """Set detection mode and thresholds; applied live and saved to config.

        Changing `detection_mode` clears tokens and transient state but keeps
        counts and history. Out-of-range values return `422`; an invalid mode
        returns `400`."""
        try:
            return processor.update_tailgating_settings(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/control/setup", tags=["Controls"], summary="Save camera geometry")
    def save_setup(payload: SetupPayload) -> dict[str, object]:
        """Persist focus area, counting line, door zone, and gate zone."""
        try:
            return processor.update_setup(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/telegram/settings", tags=["Telegram"], summary="Save Telegram settings")
    def telegram_settings(
        payload: TelegramSettingsPayload,
    ) -> dict[str, object]:
        """Save the bot token, chat ID, and enabled flag for alert delivery."""
        return processor.save_telegram_settings(payload)

    @app.post("/telegram/discover-chat", tags=["Telegram"], summary="Discover Telegram chat")
    def telegram_discover_chat(
        payload: TelegramDiscoverPayload,
    ) -> dict[str, object]:
        """Find the chat ID of a conversation that recently sent `/start`."""
        try:
            return processor.discover_telegram_chat(payload)
        except TelegramError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/telegram/discover-chats", tags=["Telegram"], summary="Discover all Telegram chats")
    def telegram_discover_chats(
        payload: TelegramDiscoverPayload,
    ) -> dict[str, object]:
        """List every distinct chat/group that recently messaged the bot."""
        try:
            return processor.discover_telegram_chats(payload)
        except TelegramError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/telegram/test", tags=["Telegram"], summary="Send test notification")
    def telegram_test() -> dict[str, object]:
        """Send a test message to verify Telegram delivery."""
        try:
            return processor.test_telegram()
        except TelegramError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.on_event("shutdown")
    def shutdown() -> None:
        processor.close()

    return app
