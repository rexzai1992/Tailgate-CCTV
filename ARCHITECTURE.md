# CCTV Tailgate — Architecture

CCTV Tailgate is a single, local FastAPI service that turns one camera into an
anonymous people counter and tailgating detector. It has no database server, no
login, and no cloud dependency; everything runs on the host and listens on
`127.0.0.1` by default.

## High-level shape

```text
┌────────────────────────── CCTV Tailgate (one process) ──────────────────────────┐
│                                                                                  │
│  Camera source ──► WebCameraProcessor.process_array(frame)                       │
│   (browser /        │                                                            │
│    local /          ├─ Detection (Ultralytics YOLO, persistent track IDs)        │
│    RTSP)            ├─ LineZoneCounter  (IN/OUT, only between the line endpoints) │
│                     ├─ Tailgating: EntryBurstDetector | TailgatingDetector       │
│                     ├─ GateMotionDetector (optional gate zone)                    │
│                     ├─ Search matching (optional)                                 │
│                     ├─ EventCapture  (snapshot, body/face crops, clip)           │
│                     ├─ EventStore    (SQLite history)  + CSV loggers             │
│                     └─ TelegramNotifier (captioned photo per event, clip/incident)│
│                                                                                  │
│  FastAPI routes ──► dashboard (HTML), /status, /events, /control/*, /telegram/*  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

The orchestrator is `WebCameraProcessor` in [src/web_server.py](src/web_server.py).
`create_web_app(config_path)` builds the processor and wires the routes; it also
accepts an injected processor so endpoint tests run without loading YOLO.

## Per-frame pipeline

`WebCameraProcessor.process_array(frame)` runs for every processed frame:

1. **Detect & track.** YOLO (`model.track`, ByteTrack) returns person boxes with
   persistent `tracker_id`s. If a focus area is required but unset, processing
   pauses.
2. **Count.** For each tracked person, `LineZoneCounter.update` decides IN/OUT
   crossings. Crossings are only counted **between the two line endpoints** (the
   marks), guarded against jitter, teleport jumps, and repeat counts.
3. **Tailgating.** Each IN crossing is passed to the active detector:
   - `EntryBurstDetector` — flags ≥ N distinct people entering within a rolling
     window (default mode).
   - `TailgatingDetector` — consumes short-lived `AccessTokenStore` tokens; an
     entry without a valid token is a tailgating event.
   The door zone path uses the same detector with polygon containment.
4. **Evidence.** On a security event, `EventCapture` writes a snapshot, optional
   body and face crops, and (asynchronously) an event clip. Filenames are stamped
   to the microsecond so rapid events never overwrite each other.
5. **Persist.** The event is written to `EventStore` (SQLite) and the CSV logs.
   Crossing and gate events are persisted too.
6. **Notify.** `TelegramNotifier` sends a captioned snapshot for **every** event
   and one captioned clip **per incident**, to one or more recipients.

Tracking IDs are the backbone: the counter, tailgating detectors, and evidence
capture all key off `tracker_id`.

## Camera modes

The processor supports three sources, selected by `camera.source_mode`:

- **`webcam`** — the browser captures the camera and POSTs frames to
  `/process-frame`. Used for quick desktop use; the browser asks for camera
  permission per session.
- **`local`** — the app opens a local device server-side via `IpCameraStream`
  (OpenCV `VideoCapture`). The OS grants camera permission once; the browser only
  views the rendered MJPEG at `/video-feed`. Recommended for fixed installs.
- **`direct_rtsp`** — the app opens a network RTSP/HTTP stream server-side via
  `IpCameraStream`.

In `local` and `direct_rtsp` modes the server owns the camera and the dashboard
shows the server-rendered feed; in `webcam` mode the browser draws the frames.

## Persistence

- **SQLite** ([src/event_store.py](src/event_store.py)) — `data/gym_sentry.db`,
  WAL mode, thread-safe. Holds `security`, `crossing`, and `gate` events with
  evidence paths; survives restarts and counter resets; never stores external
  `person_ref` values. Exposed via `GET /events`.
- **CSV logs** — `logs/*.csv`, kept for backward compatibility.
- **Evidence files** — `captures/tailgating/` (snapshots, `bodies/`, `faces/`,
  `clips/`) and `captures/entries/`.
- **Config** — `config.yaml`, written atomically when settings change.
- **Secrets** — `secrets/telegram.json` (chmod 600), Git-ignored.

## HTTP surface

Stable plugin contract: `GET /health`, `GET /status`, `GET /events`,
`POST /access-event`, `POST /process-frame`. Operational controls: `/control/*`
(camera, tailgating, search, setup, reset) and `/telegram/*`. The dashboard is
served at `/` and the live stream at `/video-feed`. Full schema at `/docs` and
`/openapi.json`; see [PLUGIN_INTEGRATION.md](PLUGIN_INTEGRATION.md).

## Concurrency

- The capture thread (`IpCameraStream`), API request threads, and the Telegram
  `ThreadPoolExecutor` all touch shared state guarded by `WebCameraProcessor.lock`
  (an `RLock`).
- `EventStore` and `TelegramNotifier` hold their own locks.
- Telegram and clip encoding run off the request path on the executor, so a slow
  upload never blocks detection.

## Deployment shapes

- **From source** — `python -m src.main` ([src/main.py](src/main.py)).
- **Docker** — `Dockerfile` + `docker-compose.yml` bind `127.0.0.1:8080` and
  mount config/secrets/output. Use `direct_rtsp` or `/process-frame` (a
  container cannot open a USB camera).
- **Windows app** — [launcher.py](launcher.py) packaged by
  [cctv-tailgate.spec](cctv-tailgate.spec) into a self-contained app; the
  optional Inno Setup installer ([packaging/installer.iss](packaging/installer.iss))
  bundles the VC++ runtime. Defaults to server-side `local` capture.

## Detection engine

Detection is currently Ultralytics YOLO, wired directly in
`WebCameraProcessor`. A planned pluggable `Detector` abstraction would allow an
optional SAM 3 engine (GPU-only) selectable by a dashboard toggle while keeping
YOLO the default — see the design notes referenced in the project plan.

## Testing

`python -m unittest discover -s tests` covers the counter, tailgating detectors,
access tokens, event store, evidence capture, gate detector, search, Telegram
delivery, and the FastAPI endpoints (via an injected fake processor, so no YOLO
weights are needed).
