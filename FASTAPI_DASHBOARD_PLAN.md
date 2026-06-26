# Gym Sentry FastAPI Tailgating Dashboard Plan

## Summary

Upgrade the existing FastAPI application instead of rebuilding it. Keep it
local at `127.0.0.1:8080`, webcam-first, single-camera, and anonymous. Support
both group-entry and access-token tailgating detection.

## Dashboard changes

- Organize the main view around the camera preview, IN/OUT/inside counts,
  detection mode, security status, and persistent alert history.
- Move search, gate setup, Telegram, diagnostics, RTSP settings, and camera
  calibration into collapsible Advanced sections.
- Add controls for selecting and configuring both tailgating modes.
- When the mode changes, clear authorization tokens and transient detection
  state while preserving counts and event history.
- Disable ordinary-entry face capture.
- Save snapshots, body crops, validated face crops, and clips only when a
  security alert occurs.

## Persistent history

- Add `data/gym_sentry.db` using SQLite with WAL mode and thread-safe access.
- Persist crossing, tailgating, and gate events with timestamps, reasons,
  tracker IDs, camera name, and evidence paths.
- Keep the existing CSV logs for backward compatibility.
- Do not automatically import old CSV records into SQLite.
- Update an event record when its asynchronously generated video clip becomes
  available.
- Resetting the live counters must not delete historical event records.
- Do not persist external `person_ref` values in SQLite.

## API changes

Preserve the existing plugin endpoints and payloads:

- `GET /health`
- `GET /status`
- `POST /access-event`
- `POST /process-frame`
- Existing `/control/*` and `/telegram/*` endpoints

Add:

### `POST /control/tailgating`

Configure and persist:

- `enabled`
- `detection_mode`: `entry_burst` or `access_token`
- `minimum_people`
- `tailgating_time_window_seconds`
- `token_valid_seconds`
- `max_people_per_token`

Validate bounded numeric values and save the settings atomically to
`config.yaml`.

### `GET /events`

Query parameters:

- `category`: `security`, `crossing`, or `gate`
- `limit`: default `50`
- `offset`: default `0`

Response:

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

Each event should include usable local evidence URLs where applicable.

### `GET /status`

Keep all current fields and add:

- Complete active tailgating settings
- Persistent event totals

Update the generated `/docs`, `README.md`, and `PLUGIN_INTEGRATION.md` to
describe the finished contract.

## Test plan

- Preserve and run all existing tests.
- Test SQLite creation, restart persistence, filtering, pagination, media-path
  updates, and concurrent writes.
- Test FastAPI endpoints with an injected fake processor so endpoint tests do
  not load YOLO.
- Test tailgating-setting validation and persistence.
- Confirm changing modes resets only transient detection state.
- Confirm normal entries do not create saved images.
- Confirm two distinct entries inside the configured group-entry window create
  one persistent alert.
- Confirm access-token mode consumes the configured authorization allowance and
  alerts on the next unauthorized entry.
- Confirm event history and evidence remain visible after restarting FastAPI.
- Confirm existing plugin requests remain compatible.

## Fixed assumptions

- Retain the current HTML, CSS, and JavaScript dashboard; do not introduce
  React.
- Do not implement face recognition, enrollment, or identity matching.
- Keep `entry_burst` as the default detection mode.
- Keep deployment local-only with no login or CORS.
- Preserve the existing uncommitted search, dashboard, and evidence-quality
  work.
- This document is planning only; implementation will be completed in a later
  session.
