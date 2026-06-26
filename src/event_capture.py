from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import time

import cv2
import numpy as np


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "camera"


@dataclass
class PendingClip:
    path: Path
    frames: list[bytes]
    tracker_id: int
    started_at: float
    last_seen: float
    max_until: float


class EventCapture:
    def __init__(
        self,
        output_dir: str | Path,
        capture_snapshot: bool = True,
        capture_body_closeup: bool = False,
        body_padding_percent: float = 0.12,
        capture_face_closeup: bool = False,
        face_padding_percent: float = 0.3,
        min_face_pixels: int = 36,
        require_eye_confirmation: bool = True,
        min_face_sharpness: float = 35,
        save_event_clip: bool = False,
        clip_fps: float = 10,
        pre_seconds: float = 5,
        post_seconds: float = 5,
        exit_grace_seconds: float = 1.5,
        max_clip_seconds: float = 30,
        capture_entry_face: bool = False,
        entry_dir: str | Path = "captures/entries",
    ):
        self.output_dir = Path(output_dir)
        self.capture_snapshot_enabled = capture_snapshot
        self.capture_body_closeup_enabled = capture_body_closeup
        self.body_padding_percent = max(0.0, float(body_padding_percent))
        self.capture_face_closeup_enabled = capture_face_closeup
        self.face_padding_percent = max(0.0, float(face_padding_percent))
        self.min_face_pixels = max(20, int(min_face_pixels))
        self.require_eye_confirmation = bool(require_eye_confirmation)
        self.min_face_sharpness = max(0.0, float(min_face_sharpness))
        self.save_event_clip = save_event_clip
        self.clip_fps = max(1.0, float(clip_fps))
        self.pre_seconds = float(pre_seconds)
        self.post_seconds = float(post_seconds)
        self.exit_grace_seconds = max(0.0, float(exit_grace_seconds))
        self.max_clip_seconds = max(1.0, float(max_clip_seconds))
        self.capture_entry_face_enabled = capture_entry_face
        self.entry_dir = Path(entry_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "clips").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "bodies").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "faces").mkdir(parents=True, exist_ok=True)
        (self.entry_dir / "faces").mkdir(parents=True, exist_ok=True)
        self._frontal_face = self._load_cascade(
            "haarcascade_frontalface_default.xml"
        )
        self._profile_face = self._load_cascade(
            "haarcascade_profileface.xml"
        )
        self._eye = self._load_cascade("haarcascade_eye.xml")
        self._buffer: deque[tuple[float, bytes]] = deque()
        self._pending: list[PendingClip] = []
        self._last_buffered_at = 0.0
        # Best (sharpest, largest) face crop seen per tracker, so captures use a
        # stable frame instead of whatever single frame the crossing landed on.
        self._best_faces: dict[int, dict] = {}

    def update(
        self,
        frame: np.ndarray,
        monotonic_now: float | None = None,
        active_ids: set[int] | None = None,
    ) -> list[str]:
        if not self.save_event_clip:
            return []
        now = monotonic_now if monotonic_now is not None else time.monotonic()
        active = active_ids or set()
        # Keep the recording alive as long as the suspect tracker is on screen.
        for pending in self._pending:
            if pending.tracker_id in active:
                pending.last_seen = now
        if now - self._last_buffered_at < 1.0 / self.clip_fps:
            return self._finalize_due(now)

        self._last_buffered_at = now
        stamped = self._stamp_timestamp(frame.copy(), datetime.now().astimezone())
        encoded_ok, encoded = cv2.imencode(
            ".jpg", stamped, [cv2.IMWRITE_JPEG_QUALITY, 85]
        )
        if not encoded_ok:
            return self._finalize_due(now)
        stored = encoded.tobytes()
        self._buffer.append((now, stored))
        cutoff = now - self.pre_seconds
        while self._buffer and self._buffer[0][0] < cutoff:
            self._buffer.popleft()
        for pending in self._pending:
            pending.frames.append((now, stored))
        return self._finalize_due(now)

    def capture_event(
        self,
        frame: np.ndarray,
        camera_name: str,
        tracker_id: int,
        bbox: tuple[int, int, int, int] | None,
        event_time: datetime | None = None,
        monotonic_now: float | None = None,
        save_clip: bool = True,
    ) -> str:
        current = event_time or datetime.now().astimezone()
        stamp = current.strftime("%Y%m%d_%H%M%S")
        base = f"tailgating_{stamp}_{slugify(camera_name)}_id{tracker_id}"
        snapshot_path = self.output_dir / f"{base}.jpg"

        if self.capture_snapshot_enabled:
            annotated = frame.copy()
            if bbox is not None:
                x1, y1, x2, y2 = bbox
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 4)
                label = "TAILGATING"
                scale, thick = 0.55, 2
                (label_w, label_h), _ = cv2.getTextSize(
                    label, cv2.FONT_HERSHEY_SIMPLEX, scale, thick
                )
                label_x = x2 + 8
                if label_x + label_w > annotated.shape[1]:
                    label_x = max(0, x1 - label_w - 8)
                label_y = max(label_h + 4, y1 + label_h)
                cv2.putText(
                    annotated,
                    label,
                    (label_x, label_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    scale,
                    (0, 0, 255),
                    thick,
                    cv2.LINE_AA,
                )
            self._stamp_timestamp(annotated, current)
            if not cv2.imwrite(str(snapshot_path), annotated):
                snapshot_path = Path()

        if self.save_event_clip and save_clip:
            now = monotonic_now if monotonic_now is not None else time.monotonic()
            self._pending.append(
                PendingClip(
                    path=self.output_dir / "clips" / f"{base}.mp4",
                    frames=list(self._buffer),
                    tracker_id=tracker_id,
                    started_at=now,
                    last_seen=now,
                    max_until=now + self.max_clip_seconds,
                )
            )

        return (
            str(snapshot_path)
            if self.capture_snapshot_enabled and snapshot_path != Path()
            else ""
        )

    def pending_clip_path(
        self,
        camera_name: str,
        tracker_id: int,
        event_time: datetime | None = None,
    ) -> str:
        """Deterministic path of the clip ``capture_event`` will finalize."""
        if not self.save_event_clip:
            return ""
        stamp = (event_time or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S")
        base = f"tailgating_{stamp}_{slugify(camera_name)}_id{tracker_id}"
        return str(self.output_dir / "clips" / f"{base}.mp4")

    def capture_body_closeup(
        self,
        frame: np.ndarray,
        camera_name: str,
        tracker_id: int,
        bbox: tuple[int, int, int, int] | None,
        event_time: datetime | None = None,
    ) -> str:
        """Save the visible suspected person's body inside the tracker box."""
        if not self.capture_body_closeup_enabled or bbox is None:
            return ""
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = self._clamp_bbox(bbox, width, height)
        box_width = x2 - x1
        box_height = y2 - y1
        if box_width < 24 or box_height < 48:
            return ""

        pad_x = round(box_width * self.body_padding_percent)
        pad_y = round(box_height * self.body_padding_percent)
        crop_x1 = max(0, x1 - pad_x)
        crop_y1 = max(0, y1 - pad_y)
        crop_x2 = min(width, x2 + pad_x)
        crop_y2 = min(height, y2 + pad_y)
        body_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
        if body_crop.size == 0:
            return ""

        largest_side = max(body_crop.shape[:2])
        if largest_side < 640:
            scale = min(2.5, 640 / max(1, largest_side))
            body_crop = cv2.resize(
                body_crop,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_LANCZOS4,
            )

        current = event_time or datetime.now().astimezone()
        stamp = current.strftime("%Y%m%d_%H%M%S")
        filename = (
            f"tailgating_{stamp}_{slugify(camera_name)}_"
            f"id{tracker_id}_body.jpg"
        )
        path = self.output_dir / "bodies" / filename
        self._stamp_timestamp(body_crop, current)
        return str(path) if cv2.imwrite(str(path), body_crop) else ""

    def capture_face_closeup(
        self,
        frame: np.ndarray,
        camera_name: str,
        tracker_id: int,
        bbox: tuple[int, int, int, int] | None,
        event_time: datetime | None = None,
    ) -> str:
        """Save a close-up only when a face is visible inside the suspect box."""
        if not self.capture_face_closeup_enabled or bbox is None:
            return ""
        current = event_time or datetime.now().astimezone()
        stamp = current.strftime("%Y%m%d_%H%M%S")
        filename = (
            f"tailgating_{stamp}_{slugify(camera_name)}_"
            f"id{tracker_id}_face.jpg"
        )
        return self._save_best_or_current_face(
            frame, bbox, tracker_id, self.output_dir / "faces" / filename, current
        )

    def capture_entry_face(
        self,
        frame: np.ndarray,
        camera_name: str,
        tracker_id: int,
        bbox: tuple[int, int, int, int] | None,
        event_time: datetime | None = None,
    ) -> str:
        """Save a face close-up for an ordinary entry, when a face is visible."""
        if not self.capture_entry_face_enabled or bbox is None:
            return ""
        current = event_time or datetime.now().astimezone()
        stamp = current.strftime("%Y%m%d_%H%M%S")
        filename = (
            f"entry_{stamp}_{slugify(camera_name)}_id{tracker_id}_face.jpg"
        )
        return self._save_best_or_current_face(
            frame, bbox, tracker_id, self.entry_dir / "faces" / filename, current
        )

    def observe_face(
        self,
        frame: np.ndarray,
        tracker_id: int,
        bbox: tuple[int, int, int, int] | None,
        when: datetime,
    ) -> None:
        """Score this frame's face for the tracker and keep it if it's the best."""
        if not (
            self.capture_face_closeup_enabled or self.capture_entry_face_enabled
        ):
            return
        crop = self._extract_face_crop(frame, bbox)
        if crop is None:
            return
        score = self._face_quality(crop)
        stored = self._best_faces.get(tracker_id)
        if stored is None or score > stored["score"]:
            self._best_faces[tracker_id] = {
                "score": score,
                "crop": crop,
                "when": when,
            }

    def active_clip_tracker_ids(self) -> set[int]:
        """Tracker IDs that currently have a clip recording (the suspects)."""
        return {pending.tracker_id for pending in self._pending}

    def retain_faces(self, active_ids: set[int]) -> None:
        """Drop stored best faces for trackers no longer on screen."""
        for tracker_id in list(self._best_faces):
            if tracker_id not in active_ids:
                del self._best_faces[tracker_id]

    def _save_best_or_current_face(
        self,
        frame: np.ndarray,
        bbox: tuple[int, int, int, int] | None,
        tracker_id: int,
        path: Path,
        when: datetime | None,
    ) -> str:
        stored = self._best_faces.get(tracker_id)
        if stored is not None:
            return self._write_face_image(stored["crop"], path, stored["when"])
        crop = self._extract_face_crop(frame, bbox)
        if crop is None:
            return ""
        return self._write_face_image(crop, path, when)

    def _extract_face_crop(
        self,
        frame: np.ndarray,
        bbox: tuple[int, int, int, int] | None,
    ) -> np.ndarray | None:
        """Detect the largest face inside the person box and return a padded crop."""
        if bbox is None:
            return None
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = self._clamp_bbox(bbox, width, height)
        if x2 <= x1 or y2 <= y1:
            return None
        person_crop = frame[y1:y2, x1:x2]
        if person_crop.size == 0:
            return None

        # A standing person's face is in the upper body. Only search the top
        # portion of the box so the detector cannot mistake legs, hands, or
        # patterned clothing lower down for a face.
        head_height = max(1, int(person_crop.shape[0] * 0.55))
        head_region = person_crop[0:head_height, :]
        candidates = [
            candidate
            for face in self._detect_faces(head_region)
            if (
                candidate := self._validated_face_candidate(
                    head_region,
                    face,
                    person_crop.shape[1],
                    person_crop.shape[0],
                )
            )
            is not None
        ]
        if not candidates:
            return None
        face_x, face_y, face_w, face_h, _ = max(
            candidates, key=lambda item: item[4]
        )
        pad_x = round(face_w * self.face_padding_percent)
        pad_y = round(face_h * self.face_padding_percent)
        crop_x1 = max(0, face_x - pad_x)
        crop_y1 = max(0, face_y - pad_y)
        crop_x2 = min(person_crop.shape[1], face_x + face_w + pad_x)
        crop_y2 = min(person_crop.shape[0], face_y + face_h + pad_y)
        face_crop = person_crop[crop_y1:crop_y2, crop_x1:crop_x2]
        if face_crop.size == 0:
            return None

        largest_side = max(face_crop.shape[:2])
        if largest_side < 320:
            scale = min(3.0, 320 / max(1, largest_side))
            face_crop = cv2.resize(
                face_crop,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_LANCZOS4,
            )
        return face_crop

    @staticmethod
    def _face_quality(crop: np.ndarray) -> float:
        """Higher is better: sharpness (Laplacian variance) weighted by size."""
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        area = float(crop.shape[0] * crop.shape[1])
        return sharpness * (area ** 0.5)

    def _validated_face_candidate(
        self,
        head_region: np.ndarray,
        face: tuple[int, int, int, int],
        person_width: int,
        person_height: int,
    ) -> tuple[int, int, int, int, float] | None:
        x, y, width, height = (int(value) for value in face)
        region_height, region_width = head_region.shape[:2]
        if width < self.min_face_pixels or height < self.min_face_pixels:
            return None
        if x < 0 or y < 0 or x + width > region_width or y + height > region_height:
            return None

        aspect = width / max(1.0, float(height))
        if not 0.68 <= aspect <= 1.45:
            return None
        relative_width = width / max(1.0, float(person_width))
        relative_height = height / max(1.0, float(person_height))
        if not 0.14 <= relative_width <= 0.9:
            return None
        if not 0.1 <= relative_height <= 0.52:
            return None
        center_y = (y + height / 2) / max(1.0, float(person_height))
        if center_y > 0.42:
            return None

        face_region = head_region[y : y + height, x : x + width]
        if face_region.size == 0:
            return None
        gray = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if sharpness < self.min_face_sharpness:
            return None

        eyes = self._detect_eyes(face_region)
        if self.require_eye_confirmation and not eyes:
            return None
        eye_bonus = min(2, len(eyes)) * 1_000_000
        score = eye_bonus + width * height + sharpness
        return x, y, width, height, score

    def _write_face_image(
        self, crop: np.ndarray, path: Path, when: datetime | None
    ) -> str:
        annotated = crop.copy()
        self._stamp_timestamp(annotated, when or datetime.now().astimezone())
        return str(path) if cv2.imwrite(str(path), annotated) else ""

    def close(self) -> None:
        for pending in list(self._pending):
            self._write_clip(pending)
        self._pending.clear()

    def _finalize_due(self, now: float) -> list[str]:
        completed: list[str] = []
        # Finish once the suspect has been gone past the grace window, or the
        # hard maximum length is reached (so a lingering person can't run forever).
        due = [
            pending
            for pending in self._pending
            if now - pending.last_seen >= self.exit_grace_seconds
            or now >= pending.max_until
        ]
        for pending in due:
            if self._write_clip(pending):
                completed.append(str(pending.path))
            self._pending.remove(pending)
        return completed

    def _write_clip(self, pending: PendingClip) -> bool:
        if not pending.frames:
            return False
        timestamps = [item[0] for item in pending.frames]
        encoded_frames = [item[1] for item in pending.frames]
        first_frame = self._decode_frame(encoded_frames[0])
        if first_frame is None:
            return False
        height, width = first_frame.shape[:2]
        # Play back at the real captured rate so the clip is not fast-forwarded.
        # (Frames are buffered as fast as the pipeline runs, often < clip_fps.)
        span = timestamps[-1] - timestamps[0]
        if len(timestamps) > 1 and span > 0:
            playback_fps = (len(timestamps) - 1) / span
        else:
            playback_fps = self.clip_fps
        playback_fps = max(1.0, min(float(self.clip_fps), playback_fps))
        writer = cv2.VideoWriter(
            str(pending.path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            playback_fps,
            (width, height),
        )
        if not writer.isOpened():
            return False
        try:
            for encoded in encoded_frames:
                frame = self._decode_frame(encoded)
                if frame is None:
                    continue
                if frame.shape[1] != width or frame.shape[0] != height:
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()
        return pending.path.is_file() and pending.path.stat().st_size > 0

    @staticmethod
    def _decode_frame(encoded: bytes) -> np.ndarray | None:
        return cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)

    def _detect_faces(
        self, person_crop: np.ndarray
    ) -> list[tuple[int, int, int, int]]:
        gray = cv2.cvtColor(person_crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        kwargs = {
            "scaleFactor": 1.08,
            "minNeighbors": 5,
            "minSize": (self.min_face_pixels, self.min_face_pixels),
        }
        faces: list[tuple[int, int, int, int]] = []
        if self._frontal_face is not None:
            faces.extend(
                tuple(int(value) for value in face)
                for face in self._frontal_face.detectMultiScale(gray, **kwargs)
            )
        if self._profile_face is not None:
            faces.extend(
                tuple(int(value) for value in face)
                for face in self._profile_face.detectMultiScale(gray, **kwargs)
            )
            flipped = cv2.flip(gray, 1)
            for face in self._profile_face.detectMultiScale(flipped, **kwargs):
                x, y, w, h = (int(value) for value in face)
                faces.append((gray.shape[1] - x - w, y, w, h))
        return faces

    def _detect_eyes(
        self, face_crop: np.ndarray
    ) -> list[tuple[int, int, int, int]]:
        if self._eye is None or face_crop.size == 0:
            return []
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        search_height = max(1, round(gray.shape[0] * 0.72))
        upper_face = gray[:search_height, :]
        minimum = max(8, round(min(gray.shape[:2]) * 0.12))
        detected = self._eye.detectMultiScale(
            upper_face,
            scaleFactor=1.08,
            minNeighbors=5,
            minSize=(minimum, minimum),
        )
        valid: list[tuple[int, int, int, int]] = []
        for eye in detected:
            x, y, width, height = (int(value) for value in eye)
            aspect = width / max(1.0, float(height))
            relative_width = width / max(1.0, float(gray.shape[1]))
            center_y = (y + height / 2) / max(1.0, float(gray.shape[0]))
            if 0.65 <= aspect <= 1.8 and 0.08 <= relative_width <= 0.55 and center_y <= 0.68:
                valid.append((x, y, width, height))
        return valid

    @staticmethod
    def _stamp_timestamp(image: np.ndarray, when: datetime) -> np.ndarray:
        """Burn a date + time label into the bottom-right corner, in place."""
        if image is None or image.size == 0:
            return image
        text = when.strftime("%Y-%m-%d %H:%M:%S")
        height, width = image.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = max(0.4, min(1.0, width / 900))
        thickness = max(1, round(scale * 2))
        (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
        margin = max(4, round(width * 0.012))
        x = max(margin, width - text_w - margin)
        y = max(text_h + margin, height - margin)
        cv2.rectangle(
            image,
            (x - 4, y - text_h - 4),
            (min(width, x + text_w + 4), min(height, y + baseline + 2)),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            image, text, (x, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA
        )
        return image

    @staticmethod
    def _clamp_bbox(
        bbox: tuple[int, int, int, int], width: int, height: int
    ) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = bbox
        return (
            max(0, min(width, int(x1))),
            max(0, min(height, int(y1))),
            max(0, min(width, int(x2))),
            max(0, min(height, int(y2))),
        )

    @staticmethod
    def _load_cascade(filename: str) -> cv2.CascadeClassifier | None:
        path = Path(cv2.data.haarcascades) / filename
        classifier = cv2.CascadeClassifier(str(path))
        return None if classifier.empty() else classifier
