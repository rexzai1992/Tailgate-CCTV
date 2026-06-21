# Gym Sentry Plugin Integration Guide

This guide explains how to connect Gym Sentry to an existing access-control,
membership, kiosk, camera, or building-management system.

Gym Sentry runs as a local HTTP service. Your system can integrate it through a
small plugin or adapter that:

1. checks that Gym Sentry is healthy;
2. sends successful access authorizations;
3. optionally sends camera frames;
4. reads counts, security state, and recent events.

No Python import is required. Any language that can make HTTP requests can be
used.

## Start Gym Sentry

```bash
source .venv/bin/activate
python -m src.main
```

The default base URL is:

```text
http://127.0.0.1:8080
```

Useful built-in pages:

- Dashboard: `http://127.0.0.1:8080/`
- Interactive API documentation: `http://127.0.0.1:8080/docs`
- OpenAPI schema: `http://127.0.0.1:8080/openapi.json`

## Recommended plugin flow

For an access-control integration:

```text
Member scans card, QR code, fingerprint, or face
                    |
                    v
Your system confirms access is allowed
                    |
                    v
POST /access-event to Gym Sentry
                    |
                    v
Gym Sentry creates one short-lived authorization token
                    |
                    v
The next matching IN crossing consumes that token
```

The `camera_name` sent by the plugin must exactly match `camera.name` in
`config.yaml`.

## Enable authorization-token mode

The default `entry_burst` mode detects two or more distinct people entering
inside a rolling time window. It does not use authorization tokens.

To connect an external access system, change the mode in `config.yaml` and
restart Gym Sentry:

```yaml
tailgating:
  detection_mode: access_token
  token_valid_seconds: 6
  max_people_per_token: 1
```

- `token_valid_seconds` controls how long an authorization remains valid.
- `max_people_per_token` controls how many IN crossings one authorization
  permits.
- Tokens are kept in memory and are cleared when Gym Sentry restarts.

## Core API

### Health check

```http
GET /health
```

Example:

```bash
curl http://127.0.0.1:8080/health
```

Response:

```json
{
  "ok": true,
  "service": "gym-sentry-web"
}
```

Plugins should call this endpoint during startup and before retrying a failed
request.

### Send an authorized access event

Call this only after your system has approved the person.

```http
POST /access-event
Content-Type: application/json
```

Payload:

```json
{
  "camera_name": "Main Entrance",
  "event_type": "face_id_authorized",
  "person_ref": "member-12345",
  "timestamp": "2026-06-21T12:30:00+08:00"
}
```

Fields:

| Field | Required | Description |
|---|---:|---|
| `camera_name` | Yes | Must match the configured Gym Sentry camera name. |
| `event_type` | Yes | Currently must be `face_id_authorized`. The name is retained for compatibility even when the source is a card, QR code, fingerprint, or other access system. |
| `person_ref` | No | Your internal member or transaction reference. Avoid sending unnecessary personal data. |
| `timestamp` | No | ISO 8601 authorization time. Omit it to use the Gym Sentry server time. |

Example:

```bash
curl -X POST http://127.0.0.1:8080/access-event \
  -H "Content-Type: application/json" \
  -d '{
    "camera_name": "Main Entrance",
    "event_type": "face_id_authorized",
    "person_ref": "member-12345"
  }'
```

Success response:

```json
{
  "ok": true,
  "tokens_available": 1,
  "message": "Access token added"
}
```

Important behavior:

- Send one request for each successful access decision.
- Do not send a request when access is denied.
- Do not retry a successful request unless your plugin uses its own unique
  event deduplication; otherwise one scan may create multiple tokens.
- A `200` response means Gym Sentry accepted the authorization.
- A `400` response means the payload or event type was rejected.

### Read status and events

```http
GET /status
```

Example:

```bash
curl http://127.0.0.1:8080/status
```

Common response fields:

| Field | Description |
|---|---|
| `total_in` | Total accepted IN crossings since the last reset. |
| `total_out` | Total OUT crossings since the last reset. |
| `current_inside` | `total_in - total_out`, never below zero. |
| `tokens_available` | Current unexpired authorization-token count. |
| `security` | Current display state, such as `NORMAL` or `TAILGATING DETECTED`. |
| `security_alert` | Whether the alert banner is currently active. |
| `recent_events` | Recent security events with local evidence URLs. |
| `recent_entries` | Recent ordinary entry face captures, when enabled. |
| `recent_gate_events` | Recent gate movement events. |
| `last_access_event` | Most recently accepted external authorization. |
| `camera_mode` | `browser` or `ip`. |
| `calibration_ready` | Whether detection is ready to process crossings. |
| `last_frame_at` | Time of the most recently processed frame. |

Gym Sentry currently exposes status through polling rather than outbound
webhooks. A normal polling interval is one to five seconds.

### Send a camera frame

Use this endpoint when your plugin owns the camera and wants Gym Sentry to
process individual frames.

```http
POST /process-frame
Content-Type: image/jpeg
```

The request body must be the raw JPEG bytes, not JSON or Base64. The maximum
request size is 3 MB.

```bash
curl -X POST http://127.0.0.1:8080/process-frame \
  -H "Content-Type: image/jpeg" \
  --data-binary @frame.jpg
```

The response includes the normal status fields plus:

```json
{
  "frame_width": 640,
  "frame_height": 360,
  "tracks": [
    {
      "tracker_id": 7,
      "bbox": [110, 42, 260, 350],
      "suspect": false
    }
  ]
}
```

Send frames sequentially. Avoid overlapping requests because tracking depends
on frame order. A practical starting rate is 8-12 frames per second.

## Plugin examples

### Python adapter

```python
from datetime import datetime, timezone

import requests


class GymSentryPlugin:
    def __init__(self, base_url="http://127.0.0.1:8080"):
        self.base_url = base_url.rstrip("/")

    def health(self):
        response = requests.get(f"{self.base_url}/health", timeout=3)
        response.raise_for_status()
        return response.json()

    def authorize(self, camera_name, person_ref=None):
        response = requests.post(
            f"{self.base_url}/access-event",
            json={
                "camera_name": camera_name,
                "event_type": "face_id_authorized",
                "person_ref": person_ref,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            timeout=3,
        )
        response.raise_for_status()
        return response.json()

    def status(self):
        response = requests.get(f"{self.base_url}/status", timeout=3)
        response.raise_for_status()
        return response.json()
```

Usage:

```python
gym_sentry = GymSentryPlugin()

if gym_sentry.health()["ok"]:
    result = gym_sentry.authorize(
        camera_name="Main Entrance",
        person_ref="member-12345",
    )
    print(result)
```

### JavaScript or Node.js adapter

```javascript
export async function authorizeGymEntry({
  baseUrl = "http://127.0.0.1:8080",
  cameraName = "Main Entrance",
  personRef = null
}) {
  const response = await fetch(`${baseUrl}/access-event`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      camera_name: cameraName,
      event_type: "face_id_authorized",
      person_ref: personRef,
      timestamp: new Date().toISOString()
    })
  });

  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || `Gym Sentry returned ${response.status}`);
  }
  return body;
}
```

## Administrative API

These endpoints are intended for trusted setup tools, not normal member scans:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/control/camera` | Select browser webcam or direct RTSP mode. |
| `POST` | `/control/setup` | Save focus area, counting line, door zone, and gate zone. |
| `POST` | `/control/reset` | Reset counts and detection state. |
| `POST` | `/control/reset-tracking` | Reset tracker IDs without changing counts. |
| `GET` | `/video-feed` | Read the MJPEG stream when direct RTSP mode is active. |
| `POST` | `/telegram/settings` | Save local Telegram alert settings. |
| `POST` | `/telegram/discover-chat` | Find a Telegram chat after `/start`. |
| `POST` | `/telegram/test` | Send a test Telegram notification. |

Use the generated `/docs` page for the current payload schema.

## Security

The current API has no built-in API key or user authentication. Its safe
default is to listen only on `127.0.0.1`, which limits access to the same
computer.

Recommended deployment:

- Keep `api.host: 127.0.0.1` when the plugin runs on the same computer.
- If another machine must connect, place Gym Sentry behind an authenticated
  reverse proxy or private VPN.
- Do not expose port `8080` directly to the public internet.
- Restrict access to `/control/*` and `/telegram/*` more strongly than
  `/health`, `/status`, and `/access-event`.
- Use HTTPS whenever requests cross a machine or network boundary.
- Treat evidence URLs and `person_ref` values as sensitive operational data.

Browser-based plugins on another origin also need a same-origin backend proxy;
Gym Sentry does not currently enable cross-origin browser requests.

## Reliability recommendations

- Use connection and response timeouts of about three seconds.
- Retry connection failures and `5xx` responses with exponential backoff.
- Do not automatically retry a `200` authorization response.
- Queue access events briefly if Gym Sentry is temporarily unavailable.
- Preserve your own access-event ID so your plugin can prevent duplicate sends.
- Monitor `/health` and the `last_frame_at` field from `/status`.
- Log response status codes, but never log camera credentials or Telegram
  tokens.

## Compatibility contract

An external plugin should depend only on:

- `GET /health`
- `POST /access-event`
- `GET /status`
- optionally `POST /process-frame`
- the published OpenAPI schema at `/openapi.json`

Internal Python modules and dashboard HTML are implementation details and may
change without notice.
