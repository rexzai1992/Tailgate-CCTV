# CCTV Tailgate

CCTV Tailgate is a local, one-camera people counter and tailgating detector for
gyms, offices, and controlled entrances.

It detects and tracks people crossing a configurable line, records suspicious
group entries, saves local evidence, and can send Telegram alerts. It does
**not** recognize faces or identify people.

## Features

- Anonymous IN/OUT people counting with YOLO tracking
- Browser webcam, server-side local camera, and direct RTSP camera support
- Configurable doorway focus area and a counting line that only counts between
  its endpoints
- Group-entry and access-token tailgating modes, configurable from the dashboard
- Tailgating snapshots, body crops, optional face crops, and event clips
- Persistent SQLite event history with a review/export workflow
- Gate or turnstile movement detection
- On-demand live search for standard objects and basic shirt colors
- Telegram alerts to one or many recipients (including groups), with a captioned
  photo for every event and a captioned clip per incident
- CSV logs and a live local dashboard with a settings panel
- Local HTTP API for access-control and camera-system plugins
- Runs from source, in Docker, or as a packaged Windows app

## Quick start

Python 3.11 or 3.12 is recommended.

```bash
git clone https://github.com/rexzai1992/Tailgate-CCTV.git
cd Tailgate-CCTV

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env
python -m src.main
```

Open:

- Dashboard: <http://127.0.0.1:8080/>
- API documentation: <http://127.0.0.1:8080/docs>
- Health check: <http://127.0.0.1:8080/health>

Ultralytics downloads the configured YOLO model on first launch if it is not
already present.

## Run with Docker

A `Dockerfile`, `docker-compose.yml`, and `.dockerignore` are included. The
container runs `python -m src.main` and exposes the app on port `8080`.

Before the first run, create your local configuration and secrets:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

Then build and start the service:

```bash
docker compose up --build
```

The dashboard is then available at <http://127.0.0.1:8080/>.

The Compose file binds the port to `127.0.0.1:8080:8080` so the service is
reachable only from the host by default. It also mounts the following paths
from the project directory into the container so your configuration and data
persist across rebuilds:

| Mounted path | Purpose |
|---|---|
| `config.yaml` | Runtime configuration |
| `.env` | Environment variables (for example, `TZ`) |
| `captures/` | Saved snapshots, crops, and clips |
| `logs/` | CSV activity logs |
| `data/` | SQLite event history |
| `secrets/` | Telegram credentials |

> **Browser webcams are not available inside the container.** For Docker
> deployments use `source_mode: direct_rtsp` (see [Camera sources](#camera-sources))
> or have a plugin submit frames to `POST /process-frame`.

### Docker security

- Do not expose port `8080` directly to the public internet.
- For remote access, use a VPN or an authenticated reverse proxy that
  terminates TLS in front of the container.
- Keep RTSP credentials (`config.yaml`) and Telegram secrets (`secrets/`) out of
  version control; both paths are already covered by `.gitignore` and
  `.dockerignore`.

## Build a Windows app

CCTV Tailgate can be packaged into a standalone Windows application with
[PyInstaller](https://pyinstaller.org/). The packaged app captures the camera
**server-side**, so the camera permission is granted once by Windows and the
browser never prompts for it.

### What the target PC needs: nothing

The installer is fully self-contained. A fresh Windows PC needs **no Python, no
package manager, and no internet connection**:

- Python and every library (OpenCV, PyTorch, Ultralytics) are bundled in the
  app.
- The YOLO model is bundled, so the first run works offline.
- The Visual C++ runtime is installed automatically by the installer if it is
  missing.

The end user just runs the installer and clicks **Launch**.

### Build the app (on a build machine)

Only the **build** machine needs Python 3.11 or 3.12. From the project root on
Windows:

```bat
build_windows.bat
```

This produces `dist\CCTV-Tailgate\CCTV-Tailgate.exe`. You can run that directly,
or wrap it in a one-click installer (below). When launched, the app:

- stores its data in `%LOCALAPPDATA%\CCTV Tailgate` (config, captures, logs,
  events, secrets) — writable for any user, even when installed in Program
  Files;
- seeds `config.yaml` and the model there on first launch;
- starts the service on `127.0.0.1:8080` and opens the dashboard; and
- opens the local camera directly (`source_mode: local`, `source: "0"`). Set a
  different **Camera number** in the dashboard's *Camera source* panel for a
  second or third camera.

> Build `yolo11n.pt` into the app by leaving it in the project root before
> building (it is downloaded automatically the first time you run the app from
> source). This guarantees the packaged app needs no internet.

### Build a one-click installer (optional)

To produce a single `CCTV-Tailgate-Setup.exe` that installs the app, creates
shortcuts, and installs the Visual C++ runtime if needed:

1. Build the app with `build_windows.bat` (above).
2. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) on the build machine.
3. Download the [VC++ 2015–2022 x64 redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)
   and save it as `packaging\redist\VC_redist.x64.exe`.
4. Compile [`packaging\installer.iss`](packaging/installer.iss) (open it in Inno
   Setup and press F9, or run `iscc packaging\installer.iss`).

The output is `packaging\Output\CCTV-Tailgate-Setup.exe`, which is the file you
distribute to fresh PCs.

The build is defined by [`cctv-tailgate.spec`](cctv-tailgate.spec) with
[`launcher.py`](launcher.py) as the entry point. PyInstaller builds are
platform-specific, so build the Windows app on Windows.

## First-time setup

1. Open the dashboard and allow camera access.
2. Select the correct built-in or USB camera.
3. Draw a focus area around the doorway if focus is required.
4. Draw the counting line across the entrance.
5. Confirm the green **IN** arrow points in the entry direction.
6. Optionally enable OUT counting, a door zone, or a gate zone.
7. Click **Save setup**.
8. Walk through the entrance in both directions and verify the counters.

Camera selection and completed drawing changes are also saved automatically.
The browser and `config.yaml` remember the selected USB camera.

For physical installation and acceptance testing, follow
[FIELD_TEST.md](FIELD_TEST.md).

## Calibration guide

Good counting accuracy depends far more on camera placement and line geometry
than on software settings. Calibrate once during installation and re-verify
whenever the camera is moved.

### Camera placement

- **Height:** mount the camera approximately **2.7–3.2 m** above the floor.
- **Angle:** tilt it **30–45 degrees** downward so people are seen from above
  rather than head-on. This separates individuals who walk close together and
  keeps the counting point (the bottom-center of each person) stable.
- Aim for a clean, evenly lit view of the doorway with minimal backlight.

### Draw the focus area

1. Start the camera and click **Setup tools → Draw focus area**.
2. Outline only the doorway and its immediate approach — either a two-corner
   rectangle or a polygon for irregular openings.
3. A tight focus area improves accuracy, masks bystanders outside the doorway,
   and limits saved evidence to the entrance region.

### Place the counting line

1. Click **Draw counting line** and draw it across the entrance, roughly
   perpendicular to the direction people walk.
2. Keep the line within the focus area and away from the frame edges, where
   tracking is least reliable.
3. A person is counted when the bottom-center of their box crosses the line.

### Verify the IN direction

1. Confirm the green **IN** arrow points toward the inside of the space.
2. If it is reversed, redraw the line in the opposite direction (or enable
   **Show OUT arrow** to confirm both directions), then click **Save setup**.

### Acceptance tests

Walk the entrance and confirm each result before going live:

| Test | Expected result |
|---|---|
| One person enters | `IN` increases by 1 |
| One person exits | `OUT` increases by 1 (if OUT counting is enabled) |
| Two people enter close together | Both counted; a tailgating event is recorded |
| Same person crosses back and forth | Repeated crossings do **not** form a group |

### Common mistakes

- **Counting line too close to a frame edge**, where people appear and
  disappear abruptly.
- **Poor or uneven lighting**, including strong backlight from the doorway.
- **Focus area drawn too wide**, which counts passers-by and adds noise.
- **Wrong IN direction**, which swaps entries and exits.
- **Crowd occlusion** from people overlapping under a shallow camera angle —
  raise the camera or steepen the downward tilt.

## Tailgating detection modes

### Entry burst mode

This is the default mode:

```yaml
tailgating:
  detection_mode: entry_burst
  minimum_people: 2
  tailgating_time_window_seconds: 4
```

The default rule is:

- The first distinct person entering is normal.
- A second person entering within four seconds triggers capture.
- Additional distinct people inside the same rolling window are captured.
- Repeated crossings by the same tracker do not form a group.

This mode does not use external authorization tokens.

### Access-token mode

Use this mode when CCTV Tailgate is connected to a membership, card, QR,
fingerprint, face-access, or other authorization system:

```yaml
tailgating:
  detection_mode: access_token
  token_valid_seconds: 6
  max_people_per_token: 1
```

After approving access, the external system sends:

```http
POST /access-event
```

One valid token permits the configured number of IN crossings. An entry without
a valid token is recorded as `TAILGATING_DETECTED`.

See [PLUGIN_INTEGRATION.md](PLUGIN_INTEGRATION.md) for payloads, Python and
JavaScript examples, frame submission, security guidance, and the compatibility
contract.

## Camera sources

### Browser or USB camera

The default `webcam` mode uses camera devices exposed by the browser:

```yaml
camera:
  source_mode: webcam
```

Use **Refresh cameras** after connecting a new USB camera. If the saved camera
is unavailable, CCTV Tailgate pauses instead of silently selecting another one.

Because the browser captures the camera in this mode, it requests camera
permission per session. For a fixed install that should only be approved once,
use **Local camera** mode below.

### Local camera (server-side)

In `local` mode the app opens a camera attached to the server directly, so the
operating system grants camera permission **once** and the browser never
prompts. This is the recommended mode for a fixed install or the packaged
Windows app.

```yaml
camera:
  source_mode: local
  source: "0"   # camera number: 0 = default device, 1 = next, ...
```

Select **Local camera (this device)** in the dashboard's *Camera source* panel
and set the **Camera number**. The dashboard then shows the server-rendered
feed instead of a browser preview.

### Direct IP or RTSP camera

CCTV Tailgate can read a network camera directly on the server:

```yaml
camera:
  source_mode: direct_rtsp
  source: "rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102"
  rtsp_transport: tcp
  target_fps: 12
```

For Hikvision cameras, channel `101` is commonly the main stream and `102` the
lighter sub-stream. Verify the URL in VLC before configuring CCTV Tailgate.

The server reconnects automatically after a stream interruption. Keep camera
credentials private because `config.yaml` contains the complete URL.

## Dashboard controls

Top bar:

- **Start camera** requests browser camera permission or restarts capture
  (browser webcam mode).
- **Camera selector** / **Refresh cameras** choose and rescan browser cameras.
- **⚙ Settings** opens the settings panel for **detection engine / tailgating
  mode and thresholds** and **Telegram alerts**.

The **Setup tools** toggle (above the camera) reveals the calibration buttons:

- **Draw focus area** limits all processing and evidence to the doorway, and
  **Show full camera** removes the crop.
- **Draw counting line** creates the directional crossing line; crossings only
  count between its endpoints. **Show OUT arrow** enables OUT counting.
- **Draw door zone** / **Draw gate zone** add an optional restricted-entry
  polygon and a gate-motion region.
- **Diagnostics** shows tracker and crossing-state details.
- **Reset counts** clears IN/OUT totals; **Reset tracking** clears tracker IDs
  without changing totals; **Save setup** persists the geometry to `config.yaml`.

The right-hand **Find in camera** panel searches for objects such as phones,
bags, bottles, and laptops, people holding visible objects, or basic shirt
colors.

The counting point is the bottom-center of each person box. The crossing guard
rejects implausibly large one-frame jumps and rate-limits repeated crossings by
the same tracker.

## Focus area and privacy

The focus area can be a two-corner rectangle or a polygon with three or more
points. When enabled:

- only the doorway region is sent to the detector;
- pixels outside an irregular polygon are masked;
- event snapshots and clips contain only the processed region;
- counting pauses if a required focus area is not configured.

CCTV Tailgate uses face **detection only** for optional close-up evidence. It does
not identify, enroll, compare, or recognize faces.

To disable ordinary entry face captures:

```yaml
entry_capture:
  capture_face: false
```

External identity authorization must come from the connected access system.

## Evidence and logs

Confirmed security events can create:

```text
captures/tailgating/
├── tailgating_YYYYMMDD_HHMMSS_main-entrance_id23.jpg
├── bodies/
├── faces/
└── clips/
```

Evidence behavior is controlled by:

```yaml
tailgating:
  capture_snapshot: true
  capture_body_closeup: true
  capture_face_closeup: true
  require_eye_confirmation: true
  min_face_sharpness: 35
  save_event_clip: true
  clip_pre_seconds: 3
  clip_post_seconds: 0
  clip_fps: 10
```

Face candidates are sampled while a person is tracked. CCTV Tailgate saves the
best available crop based on sharpness and size rather than relying only on the
crossing frame. A candidate must have plausible face geometry, appear in the
upper body, meet the sharpness threshold, and contain at least one detected eye.
This intentionally rejects uncertain crops instead of saving hair, clothing, or
background detail as a face. If no usable face is visible, the normal event
snapshot and body evidence remain available.

Runtime logs:

| File | Contents |
|---|---|
| `logs/people_count_log.csv` | IN and OUT crossing records |
| `logs/security_events.csv` | Tailgating and possible-tailgating events |
| `logs/gate_events.csv` | Gate movement start and end records |

Photos and video frames include a local date/time overlay.

## Event Review

Detected tailgating events can be reviewed after the fact from the evidence
saved locally on the server. Every confirmed event is persisted to the SQLite
history (`data/`) alongside its snapshot, body crop, optional face crop, and
short clip, so a reviewer can confirm what happened without watching the live
feed.

### Review workflow

1. **Filter events** by date and type. The history is queryable through
   `GET /events` using `category` (`security`, `crossing`, or `gate`), `limit`,
   and `offset`, returning newest-first records.
2. **Inspect the evidence** for each event: the snapshot, the body crop, the
   optional face crop, and the recorded clip. Evidence URLs are included with
   each event where available.
3. **Triage the event** by recording a disposition — **reviewed**, **ignored**,
   or **escalated** — and **notes** that capture context for an audit trail.
4. **Export selected evidence** — the underlying snapshot, crop, and clip files
   live under `captures/` and can be copied or archived for incident reports.

> Filtering and viewing evidence are built in: every event is available through
> `GET /events`, and its media is served from `captures/`. Disposition states
> and notes are not stored by CCTV Tailgate itself; record them in your own
> review process or a case-management system that consumes the
> [plugin API](PLUGIN_INTEGRATION.md). Export is performed by copying the
> relevant files from `captures/`.

### Where the evidence stays

All snapshots, crops, clips, logs, and the event database remain **local to the
machine running CCTV Tailgate**. Nothing leaves the host unless you explicitly
configure an outbound channel — Telegram alerts (see below) or an external
integration via the [plugin API](PLUGIN_INTEGRATION.md). Treat exported
evidence and any `person_ref` values as sensitive operational data.

## Telegram alerts

Open **⚙ Settings → Telegram alerts** in the dashboard to configure alerts.
Every tailgating event sends a **captioned snapshot** immediately, and each
incident's MP4 **clip is sent (also captioned)** once recording finishes.

1. Create a Telegram bot with `@BotFather`.
2. Open the new bot and send `/start` (and add it to your group if you want
   group alerts).
3. Paste the bot token into the **Telegram alerts** section.
4. Click **Find chat (after /start)**, or enter the chat ID(s) manually.
5. Enable alerts and save.
6. Click **Send test**.

**Multiple recipients:** the **Chat ID(s)** field accepts several recipients
separated by commas or newlines — a group ID alerts everyone in the group, and
you can also list individual user IDs. Add the bot to a group and use the
group's (negative) ID to notify the whole team.

**Throttling:** by default a photo is sent for every tailgating event. Set
`tailgating.telegram_cooldown_seconds` in `config.yaml` to a value above `0` to
rate-limit photos during large group entries.

Telegram settings are stored locally in:

```text
secrets/telegram.json
```

The `secrets/` directory is excluded from Git.

The notification endpoint can also be tested from the local machine:

```bash
curl -X POST http://127.0.0.1:8080/telegram/test
```

## Gate movement detection

Draw a tight polygon around a gate, turnstile arm, or door leaf. CCTV Tailgate uses
OpenCV frame differencing and reports `STILL`, `MOVING`, or `OFF`.

```yaml
gate_zone:
  enabled: false
  points: []
  motion_threshold: 0.02
  idle_seconds: 1.0
```

This detects movement, not the physical open/closed latch state. Keep the zone
tight because a person moving through it can also trigger motion.

## Local API

The main integration endpoints are:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Service health check |
| `GET` | `/status` | Counts, camera state, security state, active tailgating settings, persisted event totals, and recent events |
| `GET` | `/events` | Persistent event history (`category`, `limit`, `offset`) |
| `POST` | `/access-event` | Add an approved external authorization |
| `POST` | `/process-frame` | Process raw JPEG bytes from an external camera plugin |
| `POST` | `/control/tailgating` | Configure and persist tailgating mode and thresholds |
| `POST` | `/control/search` | Start or clear an on-demand live object search |
| `GET` | `/video-feed` | MJPEG output when direct RTSP mode is active |

Crossing, security, and gate events are persisted to a local SQLite database
(`data/gym_sentry.db`, configurable via `logging.event_db`) and remain
available through `/events` after restarts and counter resets. The existing CSV
logs are kept for backward compatibility. See
[PLUGIN_INTEGRATION.md](PLUGIN_INTEGRATION.md) for the full request and response
contract.

The API has no built-in authentication. It listens on `127.0.0.1` by default.
Do not expose port `8080` directly to the public internet. Use an authenticated
reverse proxy or private VPN if another machine must connect.

## Architecture

CCTV Tailgate is a single FastAPI service. `WebCameraProcessor`
([src/web_server.py](src/web_server.py)) is the orchestrator: it runs detection
on each frame, updates the line counter and tailgating detectors, captures
evidence, persists events, and serves the dashboard and HTTP API.

```text
 Camera frame ─► Detection (YOLO) ─► LineZoneCounter ─► Tailgating detectors
                                            │                    │
                                            ▼                    ▼
                                     EventCapture          EventStore (SQLite)
                                  (snapshot/crops/clip)         + CSV logs
                                            │                    │
                                            └──► TelegramNotifier (photo + clip)
```

| Module | Responsibility |
|---|---|
| [src/main.py](src/main.py) | Entry point; loads config and runs uvicorn |
| [src/web_server.py](src/web_server.py) | FastAPI app, routes, and the `WebCameraProcessor` orchestrator |
| [src/web_dashboard.html](src/web_dashboard.html) | Single-page dashboard UI (HTML/CSS/JS, no build step) |
| [src/counter.py](src/counter.py) | `LineZoneCounter` — segment-bounded line crossing; polygon test |
| [src/tailgating_detector.py](src/tailgating_detector.py) | `EntryBurstDetector` and `TailgatingDetector` (token mode) |
| [src/access_tokens.py](src/access_tokens.py) | `AccessTokenStore` — short-lived external authorizations |
| [src/event_capture.py](src/event_capture.py) | Snapshots, body/face crops, and event clips |
| [src/event_store.py](src/event_store.py) | `EventStore` — SQLite event history behind `/events` |
| [src/security_logger.py](src/security_logger.py) | CSV loggers (counts, security, gate) |
| [src/gate_detector.py](src/gate_detector.py) | `GateMotionDetector` — motion in the gate zone |
| [src/search_detector.py](src/search_detector.py) | Live object / shirt-color search parsing and matching |
| [src/telegram_notifier.py](src/telegram_notifier.py) | Multi-recipient Telegram delivery |
| [src/ip_camera.py](src/ip_camera.py) | `IpCameraStream` — server-side capture for local and RTSP cameras |
| [src/api_server.py](src/api_server.py) | `AccessEvent` model and the standalone access API |
| [src/desktop_main.py](src/desktop_main.py) | Legacy standalone OpenCV desktop counter |
| [launcher.py](launcher.py) | Windows app entry point (seeds data, starts server, opens browser) |

See [ARCHITECTURE.md](ARCHITECTURE.md) for the request/data flow, camera modes,
and deployment shapes (source, Docker, Windows app).

## Configuration

Start from [config.example.yaml](config.example.yaml). Important sections:

- `camera` — name, source mode (`webcam` / `local` / `direct_rtsp`), stream, FPS
- `detection` — YOLO model, confidence, IoU, tracker, device, and image size
- `counting_line` — direction, cooldown, travel/jump protection, and
  `segment_margin_pixels` (count only between the endpoints)
- `focus_area` — doorway crop and privacy mask
- `tailgating` — detection mode, thresholds, evidence, clip length
  (`clip_pre_seconds` / `clip_post_seconds` / `clip_max_seconds`), and alert /
  `telegram_cooldown_seconds` settings
- `entry_capture` — ordinary-entry face capture and sampling
- `door_zone` — optional restricted doorway polygon
- `gate_zone` — movement detection settings
- `api` — local host and port
- `logging` — CSV output paths and the SQLite `event_db` path

Performance tuning example:

```yaml
detection:
  model: yolo11n.pt
  device: cpu
  imgsz: 640

entry_capture:
  face_sample_every: 4
```

Supported inference devices depend on the installed PyTorch build and may
include `cpu`, `mps`, `cuda`, or a GPU index.

## Tests

Run the automated suite:

```bash
source .venv/bin/activate
python -m unittest discover -v
```

The suite covers authorization tokens, directional crossings, jitter and jump
guards, entry bursts, evidence capture, gate movement, Telegram behavior, and
setup validation.

## Troubleshooting

### Browser camera is unavailable

- Allow camera permission for `127.0.0.1`.
- Close Zoom, FaceTime, OBS, or another program using the camera.
- Reconnect the USB camera and click **Refresh cameras**.
- On macOS, check **System Settings → Privacy & Security → Camera**.

### RTSP stream does not connect

- Confirm the URL plays in VLC.
- Prefer the camera sub-stream for lower CPU usage.
- Confirm the camera and CCTV Tailgate are on reachable networks.
- Try `rtsp_transport: tcp`.
- Check that the username, password, IP address, and channel are correct.

### Detection is slow

- Lower `detection.imgsz`, for example from `640` to `480`.
- Increase `entry_capture.face_sample_every`.
- Use a lighter camera stream or lower `target_fps`.
- Configure the appropriate `mps` or `cuda` device when available.

## Limitations

- One camera cannot be perfectly accurate in every doorway.
- Crowds and heavy occlusion reduce tracker reliability.
- Camera angle, lighting, focus area, and counting-line placement matter.
- A useful starting position is approximately 2.7-3.2 m high and angled
  30-45 degrees downward.
- CCTV Tailgate is a local monitoring tool, not an identity or biometric access
  system.
