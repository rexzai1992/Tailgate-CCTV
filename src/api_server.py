from __future__ import annotations

from datetime import datetime
from threading import Thread

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .access_tokens import AccessTokenStore


class AccessEvent(BaseModel):
    camera_name: str = Field(min_length=1)
    event_type: str = "face_id_authorized"
    person_ref: str | None = None
    timestamp: datetime | None = None


def create_app(token_store: AccessTokenStore) -> FastAPI:
    app = FastAPI(title="CCTV Tailgate Access Event API", version="1.0")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CCTV Tailgate Access Test</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #101418; color: #eef2f5;
           max-width: 560px; margin: 0 auto; padding: 40px 20px; }
    main { background: #1b2229; border: 1px solid #303a43; border-radius: 16px;
           padding: 28px; }
    h1 { margin-top: 0; }
    label { display: block; margin: 18px 0 8px; color: #b9c4cc; }
    input { box-sizing: border-box; width: 100%; padding: 12px; border-radius: 8px;
            border: 1px solid #47545f; background: #11171c; color: white; }
    button { width: 100%; margin-top: 18px; padding: 14px; border: 0;
             border-radius: 8px; background: #35c778; color: #07130c;
             font-weight: 700; font-size: 16px; cursor: pointer; }
    #result { min-height: 24px; margin-top: 18px; color: #77e7a6; }
    a { color: #72b7ff; }
  </style>
</head>
<body>
  <main>
    <h1>CCTV Tailgate</h1>
    <p>Camera access-event test console</p>
    <label for="camera">Camera name</label>
    <input id="camera" value="Main Entrance">
    <label for="person">Optional member reference</label>
    <input id="person" placeholder="member-123">
    <button id="authorize">Simulate authorized Face ID scan</button>
    <div id="result"></div>
    <p><a href="/docs">Open API documentation</a></p>
  </main>
  <script>
    document.getElementById("authorize").addEventListener("click", async () => {
      const result = document.getElementById("result");
      result.textContent = "Sending...";
      try {
        const response = await fetch("/access-event", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            camera_name: document.getElementById("camera").value,
            event_type: "face_id_authorized",
            person_ref: document.getElementById("person").value || null
          })
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Request failed");
        result.textContent = `${body.message}. Tokens available: ${body.tokens_available}`;
      } catch (error) {
        result.textContent = `Error: ${error.message}`;
      }
    });
  </script>
</body>
</html>
"""

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "service": "cctv-tailgate-access-api"}

    @app.post("/access-event")
    def access_event(event: AccessEvent) -> dict[str, object]:
        if event.event_type != "face_id_authorized":
            raise HTTPException(
                status_code=400,
                detail="Only event_type=face_id_authorized creates an entry token",
            )
        token_store.add_token(
            camera_name=event.camera_name,
            event_type=event.event_type,
            person_ref=event.person_ref,
            timestamp=event.timestamp,
        )
        return {
            "ok": True,
            "tokens_available": token_store.available_count(event.camera_name),
            "message": "Access token added",
        }

    return app


class ApiServer:
    def __init__(
        self,
        token_store: AccessTokenStore,
        host: str = "127.0.0.1",
        port: int = 8080,
    ):
        config = uvicorn.Config(
            create_app(token_store),
            host=host,
            port=int(port),
            log_level="warning",
        )
        self.server = uvicorn.Server(config)
        self.thread = Thread(target=self.server.run, name="access-api", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=3)
