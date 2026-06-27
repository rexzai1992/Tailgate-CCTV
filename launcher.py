"""Windows desktop launcher for CCTV Tailgate.

Packaged with PyInstaller (see ``cctv-tailgate.spec``). On launch it:

1. chooses a writable per-user data folder (so it works even when installed to
   Program Files);
2. seeds ``config.yaml`` and the YOLO model there on first run;
3. starts the web service bound to ``127.0.0.1``; and
4. opens the dashboard in the default browser.

The bundled default config uses server-side ``local`` camera capture, so the
app opens the camera itself (the OS grants permission once) and the browser
never asks for camera access.

Everything the app needs is bundled — Python, all libraries, and the detection
model — so a target PC needs nothing pre-installed and no internet access.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _bundle_dir() -> Path:
    """Directory of files bundled with --add-data (``sys._MEIPASS`` when frozen)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _data_dir() -> Path:
    """Writable folder for config and runtime output.

    When frozen we use ``%LOCALAPPDATA%\\CCTV Tailgate`` so the app works even
    when installed under Program Files (which is read-only for normal users).
    In development we use the project directory.
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "CCTV Tailgate"
    return Path(__file__).resolve().parent


def _seed_file(data_dir: Path, name: str, from_bundle: str | None = None) -> None:
    target = data_dir / name
    if target.exists():
        return
    source = _bundle_dir() / (from_bundle or name)
    if source.exists():
        shutil.copyfile(source, target)


def main() -> int:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir)
    for folder in ("captures", "logs", "data", "secrets"):
        (data_dir / folder).mkdir(parents=True, exist_ok=True)

    # Keep Ultralytics/matplotlib config inside our writable folder and avoid any
    # network calls, so a locked-down offline PC works on first run.
    os.environ.setdefault("YOLO_CONFIG_DIR", str(data_dir / "data" / "ultralytics"))
    os.environ.setdefault("MPLCONFIGDIR", str(data_dir / "data" / "mpl"))
    os.environ.setdefault("YOLO_OFFLINE", "1")
    os.environ.setdefault("ULTRALYTICS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    Path(os.environ["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

    _seed_file(data_dir, "config.yaml", from_bundle="windows_config.yaml")
    _seed_file(data_dir, "yolo11n.pt")

    # Import heavy modules after chdir/env setup so they read from our folder.
    import uvicorn

    from src.web_server import create_web_app

    config_path = data_dir / "config.yaml"
    app = create_web_app(config_path)
    config = app.state.gym_sentry_config
    api_config = config.get("api", {})
    host = str(api_config.get("host", "127.0.0.1"))
    port = int(api_config.get("port", 8080))
    url = f"http://{host}:{port}/"

    def _open_browser() -> None:
        time.sleep(2.0)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"CCTV Tailgate running at {url}")
    print(f"Data folder: {data_dir}")
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
