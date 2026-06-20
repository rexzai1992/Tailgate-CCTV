# Frigate NVR integration

Frigate is the camera wall, 24/7 recorder, playback UI, and export service.
Gym Sentry remains the analytics service: OpenCV, YOLO11n, ByteTrack, line
counting, tailgating logic, local evidence, and Telegram are unchanged.

## Data flow

```text
Hikvision main stream -> go2rtc -> Frigate 24/7 recording
Hikvision sub stream  -> go2rtc -> rtsp://FRIGATE:8554/gate01_sub
                                      |
                                      v
                            Gym Sentry analytics
                                      |
               MQTT + manual event + recording export
                                      |
                       Frigate playback / camera wall
```

Gym Sentry never falls back to the direct Hikvision RTSP URL when
`source_mode: frigate_restream` is selected.

## Files and secrets

Create local files from the committed templates:

```bash
cp .env.example .env
cp frigate/config.yml.example frigate/config.yml
```

Edit `.env` and replace every `changeme`. The following remain local and are
ignored by Git:

- `.env`
- `frigate/config.yml`
- `mosquitto/config/passwords`
- Frigate recordings and Mosquitto data

If a Hikvision password contains URL-special characters such as `@`, `:`, `/`,
or `#`, URL-encode it before using it in an RTSP URL.

## Start Frigate and Mosquitto

```bash
docker compose --env-file .env -f docker-compose.frigate.yml up -d
docker compose --env-file .env -f docker-compose.frigate.yml ps
docker compose --env-file .env -f docker-compose.frigate.yml logs -f frigate
```

Open <https://localhost:8971>. Frigate creates an initial admin password in its
startup logs. Set the final Frigate admin credentials in `.env` if Gym Sentry
will use the authenticated `8971` API. The default `FRIGATE_BASE_URL` uses
Frigate's internal port `5000`, which Frigate treats as trusted/internal.

Port `5000` is intentionally exposed for the local bridge acceptance test. It
is unauthenticated; firewall it from untrusted networks or bind it to
`127.0.0.1` in production.

On macOS, AirPlay Receiver may already own port `5000`. Either disable AirPlay
Receiver or set both values to another host port:

```dotenv
FRIGATE_INTERNAL_PORT=5001
FRIGATE_BASE_URL=http://localhost:5001/api
```

The example uses `ffmpeg.hwaccel_args: preset-nvidia`, as requested. On a host
without an NVIDIA GPU, change it to `auto` or the correct Frigate hardware
acceleration preset before adding the camera.

## Verify the go2rtc restream

The example exposes:

```text
rtsp://localhost:8554/gate01_main
rtsp://localhost:8554/gate01_sub
```

Test the analytics stream with VLC or:

```bash
ffplay rtsp://localhost:8554/gate01_sub
```

If Gym Sentry runs in another container on the same Compose network, use
`frigate` instead of `localhost`.

## Configure Gym Sentry

Load the environment and start the existing FastAPI app:

```bash
set -a
source .env
set +a
python -m src.main
```

Use the dashboard's **Camera source** panel:

- Source type: `Frigate / go2rtc restream`
- Frigate host: `localhost` for a host-run Python app
- Frigate camera name: `gate01`

Equivalent `config.yaml`:

```yaml
camera:
  source_mode: frigate_restream
  frigate_host: localhost
  frigate_camera_name: gate01
  rtsp_transport: tcp
  target_fps: 12
```

The resulting analytics source is:

```text
rtsp://localhost:8554/gate01_sub
```

Other supported modes are `webcam` and `direct_rtsp`. Direct RTSP remains
available for installations without Frigate.

## Event bridge behavior

When a tailgating event finishes its local evidence clip, a separate worker:

1. Publishes JSON to `analytics/events/<camera>`.
2. Creates a Frigate manual event immediately.
3. Requests a Frigate recording export using the analytics timestamps plus the
   configured pre/post roll.
4. Builds the direct Frigate recording clip URL.
5. Stores the event, Frigate IDs, URL, and errors in
   `logs/analytics_events.jsonl`.

Manual Frigate events cannot be backdated. The manual event is therefore a
current marker; the export API is the source of truth for the requested
historical pre/post-roll window.

This work runs outside the video-processing and Telegram executors. Frigate or
MQTT downtime is recorded as an error and never interrupts counting, local
capture, or Telegram.

## Test the API and MQTT

Start Gym Sentry, then run:

```bash
python scripts/test_frigate_event.py --camera gate01
```

Or POST directly:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/frigate/events \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id": "manual-test-001",
    "camera": "gate01",
    "event_type": "TAILGATING_TEST",
    "start_ts": 1781900000,
    "end_ts": 1781900003,
    "track_ids": [1, 2],
    "metadata": {"synthetic": true}
  }'
```

Observe MQTT:

```bash
docker compose --env-file .env -f docker-compose.frigate.yml exec mosquitto \
  mosquitto_sub -h localhost -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD" \
  -t 'analytics/events/#' -v
```

The API response contains:

- `mqtt_published`
- `frigate_event_id`
- `frigate_export_id`
- `frigate_clip_url`
- `errors`

An export requires 24/7 recording data for the requested timestamps. A
synthetic event using a time before Frigate started recording will correctly
return an export error while MQTT and the manual event may still succeed.

## Troubleshooting

### Frigate does not start

```bash
docker compose --env-file .env -f docker-compose.frigate.yml config
docker compose --env-file .env -f docker-compose.frigate.yml logs frigate
```

Check `frigate/config.yml`, GPU preset compatibility, storage permissions, and
that all `.env` values are present.

### go2rtc returns no video

- Confirm both Hikvision URLs in VLC.
- Confirm channels `101` (main) and `102` (sub).
- URL-encode camera credentials.
- Open Frigate's System page and inspect go2rtc logs.
- Test `rtsp://localhost:8554/gate01_sub` directly.

### MQTT authentication fails

Delete the generated password file after changing MQTT credentials, then
restart Mosquitto so it is recreated:

```bash
rm -f mosquitto/config/passwords
docker compose --env-file .env -f docker-compose.frigate.yml up -d mosquitto
```

### Manual event succeeds but export fails

Continuous recording must be enabled and the requested time must overlap
stored footage. Confirm the Gym Sentry camera name exactly matches Frigate's
camera key (`gate01` in the example).

### Frigate is offline

No recovery action is required for Gym Sentry. Local snapshots, face/body
crops, local MP4 clips, counting, and Telegram continue. The failed bridge
attempt is recorded in `logs/analytics_events.jsonl`.
