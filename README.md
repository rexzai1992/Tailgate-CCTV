# Tailgate-CCTV

Gym Sentry is a one-camera, anonymous people IN/OUT counter with group-entry
capture. The camera detects and tracks people; it does **not** recognize faces
or identify anyone.

The default detection rule is:

- 1 person enters = normal
- A second, distinct tracked person follows within 4 seconds = capture them
- Additional people entering inside the same rolling window are also captured
- Repeated crossings from the same tracker cannot form a tailgating group

Change `minimum_people` or `tailgating_time_window_seconds` in `config.yaml` to
adjust this rule.

## Install

Python 3.11 or 3.12 is recommended because computer-vision package support can
lag behind the newest Python release.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Ultralytics downloads the configured person-detection model on first launch if
the model is not already present.

## Run

Run the local web server:

```bash
python -m src.main
```

Open:

<http://127.0.0.1:8080/>

The browser asks for camera permission and displays the live camera on the
dashboard. Use the camera selector to switch between built-in, USB, or other
browser-visible cameras. Sampled frames are sent only to the local Python
server for anonymous YOLO tracking.

## Web controls

- **Start camera** — request browser camera permission or restart the selected camera
- **Camera selector** — switch between cameras exposed by the browser
- **Refresh cameras** — rescan USB cameras; newly connected cameras are also
  detected automatically and selected when possible
- **Draw focus area** — click two opposite doorway corners for a box, or click
  three or more points for an arbitrary doorway shape, then **Finish focus**; the
  dashboard, person detection, and event snapshots use only this region and
  anything outside the shape is masked out (never detected)
- **Show full camera** — remove the crop and return to the full camera image
- **Reset counts** — reset IN/OUT totals
- **Reset tracking** — clear tracker IDs and detection windows (entry burst and
  door-zone memory) without changing the IN/OUT totals
- **Draw counting line** — click two points over the live video. A green **IN**
  arrow and "IN — TOWARD CAMERA" label show the entry direction
- **Show OUT arrow** — toggle OUT counting on or off. While off (the default),
  only IN crossings are counted, no OUT arrow is drawn, and OUT crossings are
  ignored; turning it on draws the red **OUT** arrow and counts OUT crossings
- **Diagnostics** — show tracker ID, line side, distance, and crossing readiness
- **Draw door zone** — click polygon points and then **Finish zone**
- **Draw gate zone** / **Clear gate** — click 3+ points tightly around the
  gate/turnstile to detect its movement (see *Gate movement detection*)
- **Save setup** — persist line and zone coordinates to `config.yaml`

Camera selection and completed drawing changes are saved automatically.
**Save setup** also provides an explicit save. The selected camera is remembered
in both `config.yaml` and browser storage so a page refresh reconnects to the
same USB camera when it is available. If that camera is missing, Gym Sentry
pauses instead of silently opening a different camera.

The counting line is directional. With the default left-to-right horizontal
line and a front-facing camera, movement toward the lower side of the image
(toward the camera) is IN. This uses `counting_line.in_side: positive`.

Draw the focus area before positioning the counting line. Once focus is
enabled, the camera preview is cropped to that doorway region and the server
never receives the rest of the frame for detection or event capture.
Counting and tailgating detection remain paused while the required focus area
is disabled.

## Security evidence and logs

Only a tailgating event causes a screenshot to be saved. The full camera frame
inside the configured focus area is stored with a red box around the suspected
anonymous tracker:

```text
captures/tailgating/tailgating_YYYYMMDD_HHMMSS_main-entrance_id23.jpg
```

When a usable frontal or profile face is visible inside the suspected
follower's person box, Gym Sentry also saves an enlarged close-up:

```text
captures/tailgating/faces/tailgating_YYYYMMDD_HHMMSS_main-entrance_id23_face.jpg
```

The detected follower's visible body/person box is saved separately:

```text
captures/tailgating/bodies/tailgating_YYYYMMDD_HHMMSS_main-entrance_id23_body.jpg
```

This is face **detection only**. It does not recognize, identify, enroll, or
compare faces. If the person is turned away, blurred, too small, or poorly lit,
no face close-up is saved and the normal event screenshot remains available.

Every ordinary IN crossing also captures a face close-up when a face is visible,
saved under `captures/entries/faces/` and listed in the **Recent entries** panel.
This is still face **detection only** — captured entry faces are not matched
against members or identified. Set `entry_capture.capture_face: false` in
`config.yaml` to turn this off and keep ordinary entries fully anonymous.

Face close-ups (both tailgating and entry) are not taken from a single frame.
While a person is tracked, each frame's face is scored by sharpness (Laplacian
variance) and size, and the **best** frame seen is the one saved — so the
close-up is a stable, in-focus shot rather than whatever frame the crossing
happened to land on.

Security events are appended to `logs/security_events.csv`. All ordinary IN and
OUT crossings continue to be appended to `logs/people_count_log.csv`.

`save_event_clip: true` (enabled by default) saves a short clip for each
confirmed `TAILGATING_DETECTED` event under `captures/tailgating/clips/`. The
clip is roughly 5 seconds total — `clip_pre_seconds` (default 2) before the
event plus `clip_post_seconds` (default 3) after. To save disk space, the softer
`POSSIBLE_TAILGATING` door-zone events do not record a clip, and the rolling
buffer is sampled at `clip_fps` rather than full frame rate. Set
`save_event_clip: false` to turn clips off entirely.

Every saved photo and every video frame has the capture date and time burned
into the bottom-right corner. The dashboard shows snapshots, body and face
close-ups as inline thumbnails (click to open full size) and plays the event
clip directly in the **Recent security events** panel.

## Telegram alerts

The dashboard includes a **Telegram alerts** panel. When enabled, each
tailgating event sends an immediate notification with the evidence snapshot,
then sends the MP4 video after the post-event recording window finishes. Both
messages include the event date, time, and local timezone.

1. Create a bot with `@BotFather`.
2. Open the new bot in Telegram and send `/start`.
3. Paste the bot token into Gym Sentry.
4. Click **Find chat ID**, enable alerts, and click **Save**.
5. Click **Send test notification**.

The token is stored locally in `secrets/telegram.json` with restricted file
permissions. The `secrets/` directory is excluded from Git.

## Optional door zone

Use **Draw door zone** in the dashboard to enable the restricted entrance
polygon. If multiple tracked people occupy that zone while there are fewer
available authorization uses, Gym Sentry records a
`POSSIBLE_TAILGATING` event. An actual unauthorized IN crossing is always
recorded as `TAILGATING_DETECTED`.

## Gate movement detection

Use **Draw gate zone** to outline the gate, turnstile arm, or door leaf with a
tight polygon. Gym Sentry then detects **movement** inside that zone by
frame-differencing (OpenCV) — no extra model or hardware. The dashboard **Gate**
card shows `STILL`, `MOVING`, or `OFF`, and the **Gate activity** panel lists
each movement episode with its duration. Start/end events are appended to
`logs/gate_events.csv`.

This detects motion, not an open/closed latch: anything changing in the zone
(including a person walking through it) registers, so keep the polygon tight to
the moving part. Tune sensitivity in `config.yaml`:

```yaml
gate_zone:
  enabled: false
  points: []
  motion_threshold: 0.02   # fraction of zone pixels that must change
  idle_seconds: 1.0        # stillness before a movement is considered over
```

## Counting accuracy

The counting line uses the bottom-center of each person box (their feet). On a
single camera this point can jump — for example when the feet leave the frame
near the camera and the box bottom snaps upward — which would otherwise produce
a phantom reverse crossing. A guard ignores any "crossing" where the point jumps
more than `counting_line.max_jump_percent` (default `0.5`, i.e. 50% of frame
height) in one frame. Repeated crossings by the same tracker are also rate
limited so one lingering person cannot inflate the counts.

## Configuration

The requested defaults are in `config.yaml`:

```yaml
tailgating:
  enabled: true
  detection_mode: "entry_burst"
  minimum_people: 2
  token_valid_seconds: 6
  max_people_per_token: 1
  tailgating_time_window_seconds: 4
  capture_snapshot: true
  capture_body_closeup: true
  body_padding_percent: 0.12
  capture_face_closeup: true
  face_padding_percent: 0.3
  min_face_pixels: 36
  save_event_clip: true
  clip_pre_seconds: 2
  clip_post_seconds: 3
  snapshot_dir: "captures/tailgating"
  alert_cooldown_seconds: 5
  show_alert_on_screen: true
  keyboard_test_key: "a"
```

In `entry_burst` mode, access tokens are not used for capture decisions.

The browser stores the doorway crop in:

```yaml
focus_area:
  enabled: true
  points:
    - [0.55, 0.15]
    - [0.85, 0.95]
```

## Performance and operations

The server keeps up with the camera by tuning a few knobs:

- **Face sampling** — face detection (Haar) is the heaviest per-frame cost, so
  it runs only every Nth processed frame. Set `entry_capture.face_sample_every`
  (default `4`); a crossing still grabs a face on the spot if none is buffered.
- **Inference device/size** — set `detection.device` (`cpu`, `mps` on Apple
  Silicon, `cuda`, or a GPU index) and `detection.imgsz` (e.g. `480` for faster,
  lower-resolution inference). Both are optional; omit them for auto defaults.

```yaml
detection:
  model: yolo11n.pt
  device: cpu      # or mps / cuda / 0
  imgsz: 640       # lower (e.g. 480) = faster, less accurate
entry_capture:
  face_sample_every: 4
```

The dashboard **auto-reconnects** the camera if the stream ends (laptop sleep,
USB unplug, or the OS reclaiming the device) — it retries shortly after the drop
instead of staying dark.

The live **AI processing FPS** is shown in the camera toolbar.

## IP / RTSP camera (Hikvision, etc.)

Gym Sentry can open a network camera **directly** — the server reads the stream,
runs detection, and streams the annotated video to the dashboard. No browser
webcam, no capture card, no extra software.

1. Put the camera on the same LAN (PoE switch or its 12V adapter + Ethernet).
2. Find its RTSP URL. Hikvision sub-stream (recommended — lighter for YOLO):
   ```
   rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102
   ```
   (Use SADP or the router's device list to find the IP; `101` is the main
   stream, `102` the sub-stream.) Confirm it plays in VLC first.
3. Switch Gym Sentry to IP mode in `config.yaml`:
   ```yaml
   camera:
     mode: ip          # browser (webcam) | ip (server pulls the stream)
     source: "rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102"
     rtsp_transport: tcp
     target_fps: 12
   ```
4. Restart (`python -m src.main`) and open the dashboard. The live view comes
   from `/video-feed`; counting, gate, clips, and Telegram all work as usual.
   Draw the counting line / zones directly over the streamed image.

The stream auto-reconnects if it drops. Keep credentials in `config.yaml`
private. Set `mode: browser` to return to the local webcam.

## External camera troubleshooting

If a USB camera is connected after the page opens, wait for automatic
detection or click **Refresh cameras**, then select it from the camera list.
If it still does not appear:

- Confirm the browser has camera permission for `127.0.0.1`.
- Close Zoom, FaceTime, OBS, or another app that may exclusively hold the camera.
- Unplug and reconnect the camera, then refresh the dashboard.
- On macOS, verify the browser under **System Settings → Privacy & Security →
  Camera**.

## Test the non-camera logic

The core token, crossing, polygon, and tailgating tests use only the Python
standard library:

```bash
python -m unittest discover -v
```

The automated suite includes normal entry, close follower, line stopping,
turnaround, same-person jitter, and side-by-side scenarios. Complete the real
camera acceptance checklist in [`FIELD_TEST.md`](FIELD_TEST.md) after mounting
or moving the entrance camera.

## Limitations and camera placement

- One camera cannot be 100% accurate.
- Crowded or heavily occluded entrances reduce tracking accuracy.
- Camera angle, lighting, and line placement strongly affect results.
- A strong setup is roughly 2.7–3.2 m high and angled 30–45° downward.
- This project has no face recognition. Any visible face is merely part of the
  camera frame; identity authorization must come from the external access
  system, API, keyboard mock, or another test input.
