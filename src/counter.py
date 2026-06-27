from __future__ import annotations

from dataclasses import dataclass
import math
import time


Point = tuple[int, int]


def line_side(point: Point, start: Point, end: Point) -> float:
    return (end[0] - start[0]) * (point[1] - start[1]) - (
        end[1] - start[1]
    ) * (point[0] - start[0])


@dataclass(frozen=True)
class Crossing:
    tracker_id: int
    direction: str


class LineZoneCounter:
    def __init__(
        self,
        start: Point,
        end: Point,
        in_side: str = "positive",
        deadband_pixels: float = 8,
        crossing_cooldown_seconds: float = 1,
        same_direction_cooldown_seconds: float = 8,
        rearm_distance_pixels: float = 24,
        minimum_travel_pixels: float = 20,
        tracker_state_ttl_seconds: float = 30,
        max_jump_pixels: float = 0,
        segment_margin_pixels: float = 0,
    ):
        if start == end:
            raise ValueError("Counting line needs two different points")
        if in_side not in {"positive", "negative"}:
            raise ValueError("in_side must be positive or negative")
        self.start = start
        self.end = end
        self.in_sign = 1 if in_side == "positive" else -1
        # Only count crossings that fall between the two drawn endpoints (the
        # marks), not along the infinite extension of the line. The margin (in
        # pixels) optionally extends the segment a little past each endpoint.
        self.segment_margin_pixels = float(segment_margin_pixels)
        self.deadband_pixels = float(deadband_pixels)
        self.crossing_cooldown_seconds = float(crossing_cooldown_seconds)
        self.same_direction_cooldown_seconds = float(
            same_direction_cooldown_seconds
        )
        self.rearm_distance_pixels = float(rearm_distance_pixels)
        self.minimum_travel_pixels = float(minimum_travel_pixels)
        self.tracker_state_ttl_seconds = float(tracker_state_ttl_seconds)
        self.max_jump_pixels = float(max_jump_pixels)
        self._last_side: dict[int, int] = {}
        self._last_crossing: dict[int, float] = {}
        self._last_direction_crossing: dict[tuple[int, str], float] = {}
        self._direction_armed: dict[tuple[int, str], bool] = {}
        self._last_anchor: dict[int, Point] = {}
        self._last_distance: dict[int, float] = {}
        self._last_seen: dict[int, float] = {}

    def update(
        self, tracker_id: int, anchor: Point, now: float | None = None
    ) -> Crossing | None:
        checked_at = now if now is not None else time.monotonic()
        side_value = line_side(anchor, self.start, self.end)
        line_length = max(
            1.0,
            ((self.end[0] - self.start[0]) ** 2 + (self.end[1] - self.start[1]) ** 2)
            ** 0.5,
        )
        distance = abs(side_value) / line_length
        signed_in_distance = side_value / line_length * self.in_sign
        self._last_distance[tracker_id] = signed_in_distance
        self._last_seen[tracker_id] = checked_at
        if distance <= self.deadband_pixels:
            return None

        current_side = 1 if side_value > 0 else -1
        self._rearm_directions(
            tracker_id, current_side, distance, checked_at
        )
        previous_side = self._last_side.get(tracker_id)
        previous_anchor = self._last_anchor.get(tracker_id)
        self._last_side[tracker_id] = current_side
        self._last_anchor[tracker_id] = anchor
        if previous_side is None or previous_side == current_side:
            return None

        # Only count when the crossing happens between the two endpoints. A pass
        # beyond the ends of the drawn line (its infinite extension) is ignored.
        if not self._within_segment(anchor):
            return None

        # A real walker moves a modest distance per frame. A huge jump means the
        # box bottom snapped (feet left the frame) or tracker IDs were reused —
        # not a genuine crossing. Re-baseline the side and skip the count.
        if (
            self.max_jump_pixels > 0
            and previous_anchor is not None
            and math.dist(anchor, previous_anchor) > self.max_jump_pixels
        ):
            return None

        if (
            self.minimum_travel_pixels > 0
            and previous_anchor is not None
            and math.dist(anchor, previous_anchor) < self.minimum_travel_pixels
        ):
            return None

        last_crossing = self._last_crossing.get(tracker_id, float("-inf"))
        if checked_at - last_crossing < self.crossing_cooldown_seconds:
            return None

        direction = "IN" if current_side == self.in_sign else "OUT"
        direction_key = (tracker_id, direction)
        if not self._direction_armed.get(direction_key, True):
            return None
        last_same_direction = self._last_direction_crossing.get(
            direction_key, float("-inf")
        )
        if (
            checked_at - last_same_direction
            < self.same_direction_cooldown_seconds
        ):
            return None

        self._last_crossing[tracker_id] = checked_at
        self._last_direction_crossing[direction_key] = checked_at
        self._direction_armed[direction_key] = False
        return Crossing(tracker_id=tracker_id, direction=direction)

    def track_status(
        self, tracker_id: int, now: float | None = None
    ) -> dict[str, object]:
        """Return compact calibration diagnostics for one anonymous tracker."""
        checked_at = now if now is not None else time.monotonic()
        signed_distance = self._last_distance.get(tracker_id, 0.0)
        if abs(signed_distance) <= self.deadband_pixels:
            side = "ON LINE"
        else:
            side = "IN SIDE" if signed_distance > 0 else "OUT SIDE"
        last_crossing = self._last_crossing.get(tracker_id)
        cooldown_remaining = (
            0.0
            if last_crossing is None
            else max(
                0.0,
                self.crossing_cooldown_seconds
                - (checked_at - last_crossing),
            )
        )
        return {
            "line_side": side,
            "line_distance": round(abs(signed_distance), 1),
            "crossing_state": (
                f"WAIT {cooldown_remaining:.1f}s"
                if cooldown_remaining > 0
                else "READY"
            ),
        }

    def expire_inactive(
        self,
        active_tracker_ids: set[int],
        now: float | None = None,
    ) -> None:
        """Forget stale tracker state so tracker-ID reuse starts cleanly."""
        checked_at = now if now is not None else time.monotonic()
        stale = {
            tracker_id
            for tracker_id, last_seen in self._last_seen.items()
            if tracker_id not in active_tracker_ids
            and checked_at - last_seen >= self.tracker_state_ttl_seconds
        }
        for tracker_id in stale:
            self._last_side.pop(tracker_id, None)
            self._last_crossing.pop(tracker_id, None)
            self._last_anchor.pop(tracker_id, None)
            self._last_distance.pop(tracker_id, None)
            self._last_seen.pop(tracker_id, None)
            for direction in ("IN", "OUT"):
                key = (tracker_id, direction)
                self._last_direction_crossing.pop(key, None)
                self._direction_armed.pop(key, None)

    def _rearm_directions(
        self,
        tracker_id: int,
        current_side: int,
        distance: float,
        checked_at: float,
    ) -> None:
        if distance < self.rearm_distance_pixels:
            return
        source_sides = {
            "IN": -self.in_sign,
            "OUT": self.in_sign,
        }
        for direction, source_side in source_sides.items():
            if current_side != source_side:
                continue
            key = (tracker_id, direction)
            last = self._last_direction_crossing.get(
                key, float("-inf")
            )
            if (
                checked_at - last
                >= self.same_direction_cooldown_seconds
            ):
                self._direction_armed[key] = True

    def _within_segment(self, anchor: Point) -> bool:
        """True when the anchor projects onto the drawn segment (plus margin)."""
        sx, sy = self.start
        ex, ey = self.end
        dx, dy = ex - sx, ey - sy
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq <= 0:
            return True
        t = ((anchor[0] - sx) * dx + (anchor[1] - sy) * dy) / seg_len_sq
        margin = self.segment_margin_pixels / (seg_len_sq ** 0.5)
        return -margin <= t <= 1 + margin

    def set_line(self, start: Point, end: Point) -> None:
        if start == end:
            raise ValueError("Counting line needs two different points")
        self.start = start
        self.end = end
        self.reset()

    def reset(self) -> None:
        self._last_side.clear()
        self._last_crossing.clear()
        self._last_direction_crossing.clear()
        self._direction_armed.clear()
        self._last_anchor.clear()
        self._last_distance.clear()
        self._last_seen.clear()


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    """Ray-casting polygon containment with no OpenCV dependency."""
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        intersects = (yi > y) != (yj > y) and x < (
            (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside
