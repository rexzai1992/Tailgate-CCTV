from __future__ import annotations

import os
import time
from threading import Event, Thread
from typing import Any

import cv2


def _normalize_source(source: Any) -> int | str:
    if isinstance(source, int):
        return source
    text = str(source)
    return int(text) if text.isdigit() else text


class IpCameraStream:
    """Opens a camera/RTSP source server-side, runs it through the processor,
    and keeps the latest annotated JPEG for the dashboard's MJPEG view."""

    def __init__(
        self,
        processor: Any,
        source: Any,
        transport: str = "tcp",
        target_fps: float = 12,
    ):
        self.processor = processor
        self.source = _normalize_source(source)
        self.transport = transport
        self.target_fps = max(1.0, float(target_fps))
        self.connected = False
        self.last_error = ""
        self._latest_jpeg = b""
        self._stop = Event()
        self._thread = Thread(target=self._run, name="ip-camera", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3)

    def latest_jpeg(self) -> bytes:
        return self._latest_jpeg

    def status(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "source": str(self.source),
            "last_error": self.last_error,
        }

    def _open(self) -> cv2.VideoCapture:
        source = self.source
        if isinstance(source, str) and source.lower().startswith("rtsp"):
            # Prefer TCP transport — far fewer torn frames than UDP on RTSP.
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                f"rtsp_transport;{self.transport}"
            )
            return cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        return cv2.VideoCapture(source)

    def _run(self) -> None:
        min_interval = 1.0 / self.target_fps
        capture: cv2.VideoCapture | None = None
        last_processed = 0.0
        while not self._stop.is_set():
            if capture is None or not capture.isOpened():
                if capture is not None:
                    capture.release()
                capture = self._open()
                if not capture.isOpened():
                    self.connected = False
                    self.last_error = f"Unable to open camera source: {self.source}"
                    self._stop.wait(2.0)
                    continue
                self.connected = True
                self.last_error = ""

            ok, frame = capture.read()
            if not ok or frame is None:
                self.connected = False
                self.last_error = "Lost connection to the camera stream"
                capture.release()
                capture = None
                self._stop.wait(1.0)
                continue

            now = time.monotonic()
            # Read every frame (drains the buffer to keep latency low) but only
            # run detection at the configured target FPS.
            if now - last_processed < min_interval:
                continue
            last_processed = now
            try:
                result = self.processor.process_array(frame)
                annotated = self.processor.render_annotated(frame, result)
                ok_enc, buffer = cv2.imencode(
                    ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80]
                )
                if ok_enc:
                    self._latest_jpeg = buffer.tobytes()
            except Exception as exc:  # keep the stream alive on transient errors
                self.last_error = f"Processing error: {exc}"

        if capture is not None:
            capture.release()
