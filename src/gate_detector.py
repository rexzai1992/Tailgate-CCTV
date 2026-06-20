from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


Point = tuple[int, int]


@dataclass(frozen=True)
class GateEvent:
    event_type: str  # GATE_MOVEMENT_START | GATE_MOVEMENT_END
    motion_ratio: float
    duration_seconds: float | None = None


class GateMotionDetector:
    """Detect gate movement by frame-differencing inside a drawn gate polygon.

    This sees *movement*, not an open/closed latch: a moving gate leaf, arm, or
    turnstile (and anything else changing inside the zone) raises the motion
    ratio. A movement episode starts when the ratio crosses ``motion_threshold``
    and ends after ``idle_seconds`` of stillness, which debounces brief pauses
    mid-swing.
    """

    def __init__(
        self,
        motion_threshold: float = 0.02,
        idle_seconds: float = 1.0,
        min_pixels: int = 40,
        diff_threshold: int = 25,
    ):
        self.motion_threshold = float(motion_threshold)
        self.idle_seconds = float(idle_seconds)
        self.min_pixels = int(min_pixels)
        self.diff_threshold = int(diff_threshold)
        self._prev_gray: np.ndarray | None = None
        self.moving = False
        self.last_motion_ratio = 0.0
        self._motion_started_at: float | None = None
        self._last_motion_at: float | None = None

    @property
    def state(self) -> str:
        return "MOVING" if self.moving else "STILL"

    def reset(self) -> None:
        self._prev_gray = None
        self.moving = False
        self.last_motion_ratio = 0.0
        self._motion_started_at = None
        self._last_motion_at = None

    def update(
        self, frame: np.ndarray, polygon: list[Point], now: float
    ) -> GateEvent | None:
        ratio, changed, ok = self._motion_ratio(frame, polygon)
        if not ok:
            return None
        self.last_motion_ratio = ratio
        moving_now = ratio >= self.motion_threshold and changed >= self.min_pixels

        if moving_now:
            self._last_motion_at = now
            if not self.moving:
                self.moving = True
                self._motion_started_at = now
                return GateEvent("GATE_MOVEMENT_START", ratio)
            return None

        if (
            self.moving
            and self._last_motion_at is not None
            and now - self._last_motion_at >= self.idle_seconds
        ):
            duration = (
                now - self._motion_started_at
                if self._motion_started_at is not None
                else 0.0
            )
            self.moving = False
            return GateEvent("GATE_MOVEMENT_END", ratio, round(duration, 2))
        return None

    def _motion_ratio(
        self, frame: np.ndarray, polygon: list[Point]
    ) -> tuple[float, int, bool]:
        if frame is None or len(polygon) < 3:
            return 0.0, 0, False
        height, width = frame.shape[:2]
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        x1 = max(0, min(xs))
        y1 = max(0, min(ys))
        x2 = min(width, max(xs))
        y2 = min(height, max(ys))
        if x2 - x1 < 2 or y2 - y1 < 2:
            return 0.0, 0, False

        roi = frame[y1:y2, x1:x2]
        gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        mask = np.zeros(gray.shape, dtype=np.uint8)
        local = np.array(
            [[point[0] - x1, point[1] - y1] for point in polygon], dtype=np.int32
        )
        cv2.fillPoly(mask, [local], 255)
        area = int(cv2.countNonZero(mask))

        prev = self._prev_gray
        self._prev_gray = gray
        if area <= 0 or prev is None or prev.shape != gray.shape:
            return 0.0, 0, False

        diff = cv2.absdiff(gray, prev)
        _, thresh = cv2.threshold(diff, self.diff_threshold, 255, cv2.THRESH_BINARY)
        thresh = cv2.bitwise_and(thresh, mask)
        changed = int(cv2.countNonZero(thresh))
        return changed / area, changed, True
