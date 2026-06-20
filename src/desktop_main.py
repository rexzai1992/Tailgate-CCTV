from __future__ import annotations

import argparse
from datetime import datetime
import math
from pathlib import Path
import sys
import time
from typing import Any

import cv2
import numpy as np
import yaml
from ultralytics import YOLO

from .access_tokens import AccessTokenStore
from .api_server import ApiServer
from .counter import LineZoneCounter, point_in_polygon
from .event_capture import EventCapture
from .security_logger import PeopleCountLogger, SecurityEventLogger
from .tailgating_detector import TailgatingDetector, TailgatingResult


Point = tuple[int, int]
BBox = tuple[int, int, int, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Legacy desktop camera counter with tailgating detection"
    )
    parser.add_argument("--config", default="config.yaml", help="YAML configuration path")
    parser.add_argument(
        "--source",
        help="Override camera source (integer webcam index, video file, or stream URL)",
    )
    parser.add_argument("--no-api", action="store_true", help="Do not start the access API")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    return config


def source_value(value: object) -> int | str:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text) if text.isdigit() else text


def points_to_pixels(raw_points: list[list[float]], width: int, height: int) -> list[Point]:
    points: list[Point] = []
    for raw_x, raw_y in raw_points:
        if 0 <= raw_x <= 1 and 0 <= raw_y <= 1:
            points.append((round(raw_x * width), round(raw_y * height)))
        else:
            points.append((round(raw_x), round(raw_y)))
    return points


def points_to_normalized(points: list[Point], width: int, height: int) -> list[list[float]]:
    return [
        [round(x / max(1, width), 5), round(y / max(1, height), 5)]
        for x, y in points
    ]


class SetupController:
    def __init__(self, line_points: list[Point], door_points: list[Point]):
        self.line_points = line_points
        self.door_points = door_points
        self.mode: str | None = None
        self.message = ""

    def start_line(self) -> None:
        self.mode = "line"
        self.line_points = []
        self.message = "LINE SETUP: click two points"

    def start_door(self) -> None:
        self.mode = "door"
        self.door_points = []
        self.message = "DOOR ZONE: left-click points, right-click or Enter to finish"

    def finish_door(self) -> bool:
        if len(self.door_points) < 3:
            self.message = "Door zone needs at least 3 points"
            return False
        self.mode = None
        self.message = "Door zone ready - press S to save"
        return True

    def mouse(self, event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if self.mode == "line" and event == cv2.EVENT_LBUTTONDOWN:
            self.line_points.append((x, y))
            if len(self.line_points) == 2:
                self.mode = None
                self.message = "Counting line ready - press S to save"
        elif self.mode == "door":
            if event == cv2.EVENT_LBUTTONDOWN:
                self.door_points.append((x, y))
            elif event == cv2.EVENT_RBUTTONDOWN:
                self.finish_door()


def save_setup(
    path: Path,
    config: dict[str, Any],
    setup: SetupController,
    width: int,
    height: int,
    door_enabled: bool,
) -> None:
    config.setdefault("counting_line", {})["points"] = points_to_normalized(
        setup.line_points, width, height
    )
    config.setdefault("door_zone", {})["points"] = points_to_normalized(
        setup.door_points, width, height
    )
    config["door_zone"]["enabled"] = door_enabled
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)


def draw_polygon(frame: np.ndarray, points: list[Point], color: tuple[int, int, int]) -> None:
    if not points:
        return
    for point in points:
        cv2.circle(frame, point, 5, color, -1)
    if len(points) >= 2:
        contour = np.array(points, dtype=np.int32)
        cv2.polylines(frame, [contour], len(points) >= 3, color, 2)


def draw_panel(
    frame: np.ndarray,
    total_in: int,
    total_out: int,
    tokens: int,
    security_text: str,
    security_alert: bool,
) -> None:
    inside = max(0, total_in - total_out)
    cv2.rectangle(frame, (12, 12), (580, 128), (20, 20, 20), -1)
    cv2.putText(
        frame,
        f"IN: {total_in} | OUT: {total_out} | INSIDE: {inside}",
        (28, 45),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        f"ACCESS TOKENS: {tokens}",
        (28, 78),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (80, 220, 255),
        2,
        cv2.LINE_AA,
    )
    color = (0, 0, 255) if security_alert else (80, 220, 80)
    cv2.putText(
        frame,
        f"SECURITY: {security_text}",
        (28, 111),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        color,
        2,
        cv2.LINE_AA,
    )


def in_side_label_point(
    start: Point, end: Point, in_side: str, offset: float = 30
) -> Point:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = max(1.0, math.hypot(dx, dy))
    sign = 1 if in_side == "positive" else -1
    midpoint_x = (start[0] + end[0]) / 2
    midpoint_y = (start[1] + end[1]) / 2
    return (
        round(midpoint_x + sign * (-dy / length) * offset),
        round(midpoint_y + sign * (dx / length) * offset),
    )


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


def report_event(
    *,
    result: TailgatingResult,
    frame: np.ndarray,
    bbox: BBox | None,
    now: datetime,
    monotonic_now: float,
    camera_name: str,
    event_capture: EventCapture,
    security_logger: SecurityEventLogger,
    total_in: int,
    total_out: int,
) -> str:
    snapshot_path = event_capture.capture_event(
        frame=frame,
        camera_name=camera_name,
        tracker_id=result.tracker_id,
        bbox=bbox,
        event_time=now,
        monotonic_now=monotonic_now,
    )
    security_logger.log(
        camera_name=camera_name,
        event_type=result.event_type or "TAILGATING_DETECTED",
        tracker_id=result.tracker_id,
        authorized_tokens_available=result.tokens_available,
        total_in=total_in,
        total_out=total_out,
        current_inside=max(0, total_in - total_out),
        snapshot_path=snapshot_path,
        timestamp=now,
    )
    print(
        f"[SECURITY] {result.event_type} tracker={result.tracker_id} "
        f"reason={result.reason} snapshot={snapshot_path or 'disabled'}"
    )
    return snapshot_path


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        print(f"Configuration not found: {config_path}", file=sys.stderr)
        return 2
    config = load_config(config_path)

    camera_config = config.get("camera", {})
    detection_config = config.get("detection", {})
    line_config = config.get("counting_line", {})
    tailgating_config = config.get("tailgating", {})
    door_config = config.get("door_zone", {})
    api_config = config.get("api", {})
    logging_config = config.get("logging", {})

    camera_name = str(camera_config.get("name", "Main Entrance"))
    source = source_value(args.source if args.source is not None else camera_config.get("source", 0))
    token_store = AccessTokenStore(
        token_valid_seconds=float(tailgating_config.get("token_valid_seconds", 6)),
        max_people_per_token=int(tailgating_config.get("max_people_per_token", 1)),
    )
    detector = TailgatingDetector(
        token_store=token_store,
        camera_name=camera_name,
        enabled=bool(tailgating_config.get("enabled", True)),
        tailgating_time_window_seconds=float(
            tailgating_config.get("tailgating_time_window_seconds", 4)
        ),
        alert_cooldown_seconds=float(tailgating_config.get("alert_cooldown_seconds", 5)),
    )
    security_logger = SecurityEventLogger(
        logging_config.get("security_events_csv", "logs/security_events.csv")
    )
    count_logger = PeopleCountLogger(
        logging_config.get("people_count_csv", "logs/people_count_log.csv")
    )
    event_capture = EventCapture(
        output_dir=tailgating_config.get("snapshot_dir", "captures/tailgating"),
        capture_snapshot=bool(tailgating_config.get("capture_snapshot", True)),
        save_event_clip=bool(tailgating_config.get("save_event_clip", False)),
        clip_fps=float(tailgating_config.get("clip_fps", 10)),
    )

    api_server: ApiServer | None = None
    if bool(api_config.get("enabled", True)) and not args.no_api:
        api_server = ApiServer(
            token_store,
            host=str(api_config.get("host", "127.0.0.1")),
            port=int(api_config.get("port", 8080)),
        )
        api_server.start()
        print(
            f"Access API: http://{api_config.get('host', '127.0.0.1')}:"
            f"{api_config.get('port', 8080)}/access-event"
        )

    video = cv2.VideoCapture(source)
    if isinstance(source, int):
        video.set(cv2.CAP_PROP_FRAME_WIDTH, int(camera_config.get("width", 1280)))
        video.set(cv2.CAP_PROP_FRAME_HEIGHT, int(camera_config.get("height", 720)))
    if not video.isOpened():
        print(f"Unable to open camera/video source: {source}", file=sys.stderr)
        if api_server:
            api_server.stop()
        return 3

    line_counter: LineZoneCounter | None = None
    setup: SetupController | None = None
    door_enabled = bool(door_config.get("enabled", False))
    total_in = 0
    total_out = 0
    alert_until = 0.0
    alert_tracker: int | None = None
    alert_text = "NORMAL"
    keyboard_key = (
        str(tailgating_config.get("keyboard_test_key", "a")).lower()[:1] or "a"
    )

    try:
        model = YOLO(str(detection_config.get("model", "yolo11n.pt")))
        window_name = f"Gym Sentry - {camera_name}"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        while True:
            ok, frame = video.read()
            if not ok:
                break
            height, width = frame.shape[:2]
            monotonic_now = time.monotonic()
            wall_now = datetime.now().astimezone()
            event_capture.update(frame, monotonic_now)

            if setup is None:
                configured_line = points_to_pixels(
                    line_config.get("points", []), width, height
                )
                if len(configured_line) != 2:
                    configured_line = [
                        (round(width * 0.1), round(height * 0.55)),
                        (round(width * 0.9), round(height * 0.55)),
                    ]
                configured_door = points_to_pixels(
                    door_config.get("points", []), width, height
                )
                setup = SetupController(configured_line, configured_door)
                line_counter = LineZoneCounter(
                    start=configured_line[0],
                    end=configured_line[1],
                    in_side=str(line_config.get("in_side", "positive")),
                    deadband_pixels=float(line_config.get("deadband_pixels", 8)),
                    crossing_cooldown_seconds=float(
                        line_config.get("crossing_cooldown_seconds", 1)
                    ),
                )
                cv2.setMouseCallback(window_name, setup.mouse)

            assert setup is not None
            assert line_counter is not None
            if len(setup.line_points) == 2 and (
                line_counter.start != setup.line_points[0]
                or line_counter.end != setup.line_points[1]
            ):
                line_counter.set_line(setup.line_points[0], setup.line_points[1])

            results = model.track(
                frame,
                persist=True,
                classes=[0],
                conf=float(detection_config.get("confidence", 0.35)),
                iou=float(detection_config.get("iou", 0.5)),
                tracker=str(detection_config.get("tracker", "bytetrack.yaml")),
                verbose=False,
            )
            tracks = extract_tracks(results[0]) if results else {}
            anchors: dict[int, Point] = {}

            for tracker_id, (x1, y1, x2, y2) in tracks.items():
                anchor = ((x1 + x2) // 2, y2)
                anchors[tracker_id] = anchor
                crossing = (
                    None
                    if setup.mode == "line"
                    else line_counter.update(tracker_id, anchor, monotonic_now)
                )
                if crossing is not None:
                    if crossing.direction == "IN":
                        total_in += 1
                        tailgating = detector.handle_in_crossing(tracker_id, wall_now)
                        if tailgating.is_tailgating:
                            report_event(
                                result=tailgating,
                                frame=frame,
                                bbox=(x1, y1, x2, y2),
                                now=wall_now,
                                monotonic_now=monotonic_now,
                                camera_name=camera_name,
                                event_capture=event_capture,
                                security_logger=security_logger,
                                total_in=total_in,
                                total_out=total_out,
                            )
                            if tailgating.show_alert and bool(
                                tailgating_config.get("show_alert_on_screen", True)
                            ):
                                alert_until = monotonic_now + float(
                                    tailgating_config.get("alert_cooldown_seconds", 5)
                                )
                                alert_tracker = tracker_id
                                alert_text = "TAILGATING DETECTED"
                    else:
                        total_out += 1
                    count_logger.log(
                        camera_name=camera_name,
                        direction=crossing.direction,
                        tracker_id=tracker_id,
                        total_in=total_in,
                        total_out=total_out,
                        current_inside=max(0, total_in - total_out),
                        timestamp=wall_now,
                    )

            if door_enabled and len(setup.door_points) >= 3:
                door_trackers = [
                    tracker_id
                    for tracker_id, anchor in anchors.items()
                    if point_in_polygon(anchor, setup.door_points)
                ]
                possible = detector.check_door_zone(door_trackers, wall_now)
                if possible is not None:
                    report_event(
                        result=possible,
                        frame=frame,
                        bbox=tracks.get(possible.tracker_id),
                        now=wall_now,
                        monotonic_now=monotonic_now,
                        camera_name=camera_name,
                        event_capture=event_capture,
                        security_logger=security_logger,
                        total_in=total_in,
                        total_out=total_out,
                    )
                    if possible.show_alert and bool(
                        tailgating_config.get("show_alert_on_screen", True)
                    ):
                        alert_until = monotonic_now + float(
                            tailgating_config.get("alert_cooldown_seconds", 5)
                        )
                        alert_tracker = possible.tracker_id
                        alert_text = "POSSIBLE TAILGATING"
            else:
                detector.check_door_zone([], wall_now)

            security_alert = monotonic_now < alert_until
            if not security_alert:
                alert_tracker = None
                alert_text = "NORMAL"

            for tracker_id, (x1, y1, x2, y2) in tracks.items():
                is_suspect = security_alert and tracker_id == alert_tracker
                color = (0, 0, 255) if is_suspect else (60, 210, 60)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3 if is_suspect else 2)
                cv2.putText(
                    frame,
                    f"ID {tracker_id}",
                    (x1, max(24, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2,
                    cv2.LINE_AA,
                )

            if len(setup.line_points) == 2:
                cv2.arrowedLine(
                    frame,
                    setup.line_points[0],
                    setup.line_points[1],
                    (255, 180, 40),
                    3,
                    tipLength=0.03,
                )
                cv2.putText(
                    frame,
                    "IN side",
                    in_side_label_point(
                        setup.line_points[0],
                        setup.line_points[1],
                        str(line_config.get("in_side", "positive")),
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 180, 40),
                    2,
                    cv2.LINE_AA,
                )
            elif setup.line_points:
                cv2.circle(frame, setup.line_points[0], 6, (255, 180, 40), -1)
            if door_enabled or setup.mode == "door":
                draw_polygon(frame, setup.door_points, (180, 80, 255))
            if setup.message:
                cv2.rectangle(frame, (12, height - 54), (width - 12, height - 12), (20, 20, 20), -1)
                cv2.putText(
                    frame,
                    setup.message,
                    (24, height - 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )

            draw_panel(
                frame,
                total_in,
                total_out,
                token_store.available_count(camera_name, wall_now),
                alert_text,
                security_alert,
            )
            cv2.imshow(window_name, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q")):
                break
            if key == ord(keyboard_key.lower()) or key == ord(keyboard_key.upper()):
                token_store.add_token(camera_name=camera_name)
                print(
                    f"[ACCESS] Test token added. Available: "
                    f"{token_store.available_count(camera_name)}"
                )
            elif key in (ord("r"), ord("R")):
                total_in = 0
                total_out = 0
                line_counter.reset()
                detector.reset()
                alert_until = 0.0
                alert_tracker = None
                alert_text = "NORMAL"
                setup.message = "Counts reset"
            elif key in (ord("l"), ord("L")):
                setup.start_line()
            elif key in (ord("z"), ord("Z")):
                door_enabled = True
                setup.start_door()
            elif key in (13, 10) and setup.mode == "door":
                setup.finish_door()
            elif key in (ord("s"), ord("S")):
                if len(setup.line_points) == 2:
                    save_setup(
                        config_path,
                        config,
                        setup,
                        width,
                        height,
                        door_enabled,
                    )
                    setup.message = f"Setup saved to {config_path.name}"
    finally:
        event_capture.close()
        video.release()
        cv2.destroyAllWindows()
        if api_server:
            api_server.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
