from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Iterable
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class AccessToken:
    token_id: str
    camera_name: str
    event_type: str
    person_ref: str | None
    created_at: datetime
    expires_at: datetime
    remaining_uses: int

    def is_valid(self, now: datetime) -> bool:
        return self.remaining_uses > 0 and self.created_at <= now <= self.expires_at


class AccessTokenStore:
    """Thread-safe, in-memory authorization tokens shared by camera and API."""

    def __init__(self, token_valid_seconds: float = 6, max_people_per_token: int = 1):
        if token_valid_seconds <= 0:
            raise ValueError("token_valid_seconds must be greater than zero")
        if max_people_per_token < 1:
            raise ValueError("max_people_per_token must be at least one")
        self.token_valid_seconds = float(token_valid_seconds)
        self.max_people_per_token = int(max_people_per_token)
        self._tokens: list[AccessToken] = []
        self._lock = RLock()

    def add_token(
        self,
        camera_name: str,
        event_type: str = "face_id_authorized",
        person_ref: str | None = None,
        timestamp: datetime | None = None,
    ) -> AccessToken:
        created_at = ensure_aware(timestamp)
        token = AccessToken(
            token_id=uuid4().hex,
            camera_name=camera_name,
            event_type=event_type,
            person_ref=person_ref,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=self.token_valid_seconds),
            remaining_uses=self.max_people_per_token,
        )
        with self._lock:
            self._purge_locked(utc_now())
            self._tokens.append(token)
        return token

    def consume(self, camera_name: str, now: datetime | None = None) -> AccessToken | None:
        checked_at = ensure_aware(now)
        with self._lock:
            self._purge_locked(checked_at)
            for token in self._tokens:
                if token.camera_name == camera_name and token.is_valid(checked_at):
                    token.remaining_uses -= 1
                    consumed = token
                    self._purge_locked(checked_at)
                    return consumed
        return None

    def available_count(self, camera_name: str | None = None, now: datetime | None = None) -> int:
        checked_at = ensure_aware(now)
        with self._lock:
            self._purge_locked(checked_at)
            return sum(
                1
                for token in self._tokens
                if (camera_name is None or token.camera_name == camera_name)
                and token.is_valid(checked_at)
            )

    def available_uses(self, camera_name: str | None = None, now: datetime | None = None) -> int:
        checked_at = ensure_aware(now)
        with self._lock:
            self._purge_locked(checked_at)
            return sum(
                token.remaining_uses
                for token in self._tokens
                if (camera_name is None or token.camera_name == camera_name)
                and token.is_valid(checked_at)
            )

    def clear(self, camera_name: str | None = None) -> None:
        with self._lock:
            if camera_name is None:
                self._tokens.clear()
            else:
                self._tokens = [
                    token for token in self._tokens if token.camera_name != camera_name
                ]

    def snapshot(self, now: datetime | None = None) -> Iterable[AccessToken]:
        checked_at = ensure_aware(now)
        with self._lock:
            self._purge_locked(checked_at)
            return tuple(self._tokens)

    def _purge_locked(self, now: datetime) -> None:
        self._tokens = [token for token in self._tokens if token.is_valid(now)]
