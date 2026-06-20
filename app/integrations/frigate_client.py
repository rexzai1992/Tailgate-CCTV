from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote, urlencode

import httpx


logger = logging.getLogger(__name__)


def _error(operation: str, message: str) -> dict[str, Any]:
    return {"ok": False, "operation": operation, "error": message}


class FrigateClient:
    """Fault-tolerant client for the small Frigate API surface we use."""

    def __init__(
        self,
        base_url: str | None = None,
        public_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("FRIGATE_BASE_URL", "http://localhost:5000/api")
        ).rstrip("/")
        self.public_url = (
            public_url
            or os.getenv("FRIGATE_PUBLIC_URL", "http://localhost:8971")
        ).rstrip("/")
        self.username = (
            username
            if username is not None
            else os.getenv("FRIGATE_USERNAME", "")
        )
        self.password = (
            password
            if password is not None
            else os.getenv("FRIGATE_PASSWORD", "")
        )
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("FRIGATE_REQUEST_TIMEOUT_SECONDS", "8")
        )
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=False,
        )
        self._logged_in = False

    def login(self) -> dict[str, Any]:
        if not self.username or not self.password:
            return _error("login", "Frigate username or password is not configured")
        try:
            response = self._client.post(
                f"{self.base_url}/login",
                json={"user": self.username, "password": self.password},
            )
            if response.status_code == 404 and "disabled" in response.text.lower():
                self._logged_in = True
                return {
                    "ok": True,
                    "operation": "login",
                    "authentication_enabled": False,
                }
            response.raise_for_status()
            self._logged_in = True
            token = self._json(response).get("access_token")
            if token:
                self._client.headers["Authorization"] = f"Bearer {token}"
            return {
                "ok": True,
                "operation": "login",
                "authentication_enabled": True,
            }
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Frigate login failed: %s", exc)
            return _error("login", str(exc))

    def create_manual_event(
        self,
        camera: str,
        label: str,
        duration: int | None,
        include_recording: bool,
        sub_label: str | None,
        score: float | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "duration": duration,
            "include_recording": include_recording,
            "sub_label": sub_label,
            "score": score if score is not None else 0,
            "draw": metadata or {},
        }
        return self._request(
            "create_manual_event",
            "POST",
            (
                f"/events/{quote(camera, safe='')}/"
                f"{quote(label, safe='')}/create"
            ),
            json=payload,
        )

    def end_manual_event(self, event_id: str) -> dict[str, Any]:
        return self._request(
            "end_manual_event",
            "PUT",
            f"/events/{quote(event_id, safe='')}/end",
            json={},
        )

    def export_recording(
        self,
        camera: str,
        start_ts: float,
        end_ts: float,
        name: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"source": "recordings"}
        if name:
            payload["name"] = name
        return self._request(
            "export_recording",
            "POST",
            (
                f"/export/{quote(camera, safe='')}/start/"
                f"{self._timestamp(start_ts)}/end/{self._timestamp(end_ts)}"
            ),
            json=payload,
        )

    def recording_clip_url(
        self,
        camera: str,
        start_ts: float,
        end_ts: float,
    ) -> str:
        return (
            f"{self.public_url}/api/{quote(camera, safe='')}/start/"
            f"{self._timestamp(start_ts)}/end/"
            f"{self._timestamp(end_ts)}/clip.mp4"
        )

    def event_url(
        self,
        event_id: str | None = None,
        camera: str | None = None,
        ts: float | None = None,
    ) -> str:
        if event_id:
            return (
                f"{self.public_url}/api/events/"
                f"{quote(event_id, safe='')}"
            )
        if camera and ts is not None:
            query = urlencode(
                {"camera": camera, "time": self._timestamp(ts)}
            )
            return f"{self.public_url}/review?{query}"
        return self.public_url

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        operation: str,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, f"{self.base_url}{path}", **kwargs
            )
            if response.status_code in {401, 403} and not self._logged_in:
                login_result = self.login()
                if not login_result["ok"]:
                    return _error(operation, login_result["error"])
                response = self._client.request(
                    method, f"{self.base_url}{path}", **kwargs
                )
            response.raise_for_status()
            data = self._json(response)
            if data.get("success") is False:
                message = str(data.get("message") or "Frigate request failed")
                logger.warning("%s failed: %s", operation, message)
                return _error(operation, message)
            return {
                "ok": True,
                "operation": operation,
                "status_code": response.status_code,
                **data,
            }
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Frigate %s failed: %s", operation, exc)
            return _error(operation, str(exc))

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {"data": data}

    @staticmethod
    def _timestamp(value: float) -> str:
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
