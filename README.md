# Gym Sentry

Gym Sentry is a local, one-camera people counter and tailgating detector for
gyms, offices, and controlled entrances.

It detects and tracks people crossing a configurable line, records suspicious
group entries, saves local evidence, and can send Telegram alerts. It does
**not** recognize faces or identify people.

## Features

- Anonymous IN/OUT people counting with YOLO tracking
- Browser webcam, USB camera, and direct RTSP camera support
- Configurable doorway focus area and directional counting line
- Group-entry and access-token tailgating modes
- Tailgating snapshots, body crops, optional face crops, and short clips
- Gate or turnstile movement detection
- On-demand live search for standard objects and basic shirt colors
- Telegram photo, video, and test notifications
- CSV logs and a live local dashboard
- Local HTTP API for access-control and camera-system plugins

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

Use this mode when Gym Sentry is connected to a membership, card, QR,
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
is unavailable, Gym Sentry pauses instead of silently selecting another one.

### Direct IP or RTSP camera

Gym Sentry can read a network camera directly on the server:

```yaml
camera:
  source_mode: direct_rtsp
  source: "rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102"
  rtsp_transport: tcp
  target_fps: 12
```

For Hikvision cameras, channel `101` is commonly the main stream and `102` the
lighter sub-stream. Verify the URL in VLC before configuring Gym Sentry.

The server reconnects automatically after a stream interruption. Keep camera
credentials private because `config.yaml` contains the complete URL.

## Dashboard controls

- **Start camera** requests browser camera permission or restarts capture.
- **Camera selector** switches between browser-visible cameras.
- **Refresh cameras** rescans built-in and USB devices.
- **Draw focus area** limits all processing and evidence to the doorway.
- **Show full camera** removes the configured focus crop.
- **Draw counting line** creates the directional crossing line.
- **Show OUT arrow** enables or disables OUT counting.
- **Diagnostics** shows tracker and crossing-state details.
- **Find in camera** searches for objects such as phones, bags, bottles, and
  laptops, people holding visible objects, or basic shirt colors.
- **Draw door zone** creates an optional restricted-entry polygon.
- **Draw gate zone** outlines a moving gate, door, or turnstile component.
- **Reset counts** clears IN/OUT totals and recent runtime state.
- **Reset tracking** clears tracker IDs without changing IN/OUT totals.
- **Save setup** persists the current geometry in `config.yaml`.

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

Gym Sentry uses face **detection only** for optional close-up evidence. It does
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

Face candidates are sampled while a person is tracked. Gym Sentry saves the
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

## Telegram alerts

The dashboard can send a snapshot immediately after a tailgating event and the
MP4 clip after recording finishes.

1. Create a Telegram bot with `@BotFather`.
2. Open the new bot and send `/start`.
3. Paste the bot token into the **Telegram alerts** panel.
4. Click **Find chat ID**.
5. Enable alerts and save.
6. Click **Send test notification**.

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

Draw a tight polygon around a gate, turnstile arm, or door leaf. Gym Sentry uses
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

## Configuration

Start from [config.example.yaml](config.example.yaml). Important sections:

- `camera` — camera name, source mode, stream, and target FPS
- `detection` — YOLO model, confidence, IoU, tracker, device, and image size
- `counting_line` — direction, cooldown, travel, and jump protection
- `focus_area` — doorway crop and privacy mask
- `tailgating` — detection mode, thresholds, evidence, clips, and alerts
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
- Confirm the camera and Gym Sentry are on reachable networks.
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
- Gym Sentry is a local monitoring tool, not an identity or biometric access
  system.
