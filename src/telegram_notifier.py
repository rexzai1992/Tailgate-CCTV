from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any

import requests


class TelegramError(RuntimeError):
    pass


class TelegramNotifier:
    """Persisted Telegram Bot API settings and media delivery."""

    def __init__(self, settings_path: str | Path):
        self.settings_path = Path(settings_path)
        self._lock = RLock()
        self.enabled = False
        self.bot_token = ""
        self.chat_id = ""
        self.bot_username = ""
        self.last_success_at: str | None = None
        self.last_error = ""
        self._load()

    def safe_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "configured": bool(self.bot_token and self.chat_id),
                "token_saved": bool(self.bot_token),
                "chat_id": self.chat_id,
                "bot_username": self.bot_username,
                "last_success_at": self.last_success_at,
                "last_error": self.last_error,
            }

    def save(
        self,
        *,
        enabled: bool,
        chat_id: str,
        bot_token: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            cleaned_token = (bot_token or "").strip()
            if cleaned_token:
                self.bot_token = cleaned_token
            self.chat_id = str(chat_id).strip()
            self.enabled = bool(enabled)
            self._persist_locked()
            return self.safe_status()

    def discover_chat(self, bot_token: str | None = None) -> dict[str, object]:
        token = self._token_or_raise(bot_token)
        body = self._request("getUpdates", token=token, timeout=20)
        updates = body.get("result") or []
        for update in reversed(updates):
            message = (
                update.get("message")
                or update.get("channel_post")
                or update.get("edited_message")
                or update.get("edited_channel_post")
            )
            chat = (message or {}).get("chat")
            if chat and chat.get("id") is not None:
                chat_id = str(chat["id"])
                with self._lock:
                    if bot_token and bot_token.strip():
                        self.bot_token = bot_token.strip()
                    # Add the discovered chat to the recipient list (dedup)
                    # rather than replacing the existing recipients.
                    recipients = self._recipients()
                    if chat_id not in recipients:
                        recipients.append(chat_id)
                    self.chat_id = ", ".join(recipients)
                    self._persist_locked()
                return {
                    **self.safe_status(),
                    "added_chat_id": chat_id,
                    "chat_title": (
                        chat.get("title")
                        or chat.get("username")
                        or chat.get("first_name")
                        or "Telegram chat"
                    ),
                }
        raise TelegramError(
            "No Telegram chat found. Open the bot in Telegram, send /start, then try again."
        )

    def discover_chats(self, bot_token: str | None = None) -> dict[str, object]:
        """Return every distinct chat that has messaged the bot recently."""
        token = self._token_or_raise(bot_token)
        if bot_token and bot_token.strip():
            with self._lock:
                self.bot_token = bot_token.strip()
                self._persist_locked()
        body = self._request("getUpdates", token=token, timeout=20)
        updates = body.get("result") or []
        found: dict[str, str] = {}
        for update in updates:
            message = (
                update.get("message")
                or update.get("channel_post")
                or update.get("edited_message")
                or update.get("edited_channel_post")
            )
            chat = (message or {}).get("chat")
            if chat and chat.get("id") is not None:
                chat_id = str(chat["id"])
                found[chat_id] = (
                    chat.get("title")
                    or chat.get("username")
                    or chat.get("first_name")
                    or "Telegram chat"
                )
        if not found:
            raise TelegramError(
                "No chats found. Open the bot (or add it to your group), send "
                "/start, then try again."
            )
        return {
            "chats": [
                {"chat_id": chat_id, "chat_title": title}
                for chat_id, title in found.items()
            ]
        }

    def test(self) -> dict[str, object]:
        token, recipients = self._token_or_raise(), self._recipients_or_raise()
        me = self._request("getMe", token=token, timeout=15).get("result") or {}
        username = str(me.get("username") or "")
        text = (
            "✅ CCTV Tailgate Telegram connection is working.\n"
            "Tailgating alerts will include an evidence image and event video."
        )
        last_error = ""
        for chat_id in recipients:
            try:
                self._request(
                    "sendMessage",
                    token=token,
                    data={"chat_id": chat_id, "text": text},
                    timeout=20,
                )
            except Exception as exc:
                last_error = str(exc)
        with self._lock:
            self.bot_username = username
            self._mark_success_locked()
            self._persist_locked()
        if last_error:
            self._mark_error(last_error)
            raise TelegramError(last_error)
        return self.safe_status()

    def send_alert(self, image_path: str, caption: str) -> bool:
        if not self._is_ready():
            return False
        token = self.bot_token
        recipients = self._recipients()
        if not recipients:
            self._mark_error("Telegram chat ID is not configured.")
            return False
        image = Path(image_path) if image_path else None
        use_photo = bool(image and image.is_file())
        any_sent = False
        last_error = ""
        for chat_id in recipients:
            try:
                if use_photo:
                    with image.open("rb") as handle:
                        self._request(
                            "sendPhoto",
                            token=token,
                            data={"chat_id": chat_id, "caption": caption[:1024]},
                            files={"photo": (image.name, handle, "image/jpeg")},
                            timeout=45,
                        )
                else:
                    self._request(
                        "sendMessage",
                        token=token,
                        data={"chat_id": chat_id, "text": caption[:4096]},
                        timeout=25,
                    )
                any_sent = True
            except Exception as exc:
                last_error = str(exc)
        if any_sent:
            self._mark_success()
        if last_error:
            self._mark_error(last_error)
        return any_sent

    def send_video(self, video_path: str, caption: str) -> bool:
        if not self._is_ready():
            return False
        path = Path(video_path)
        if not path.is_file() or path.stat().st_size == 0:
            self._mark_error(f"Event video is unavailable: {path}")
            return False
        token = self.bot_token
        recipients = self._recipients()
        if not recipients:
            self._mark_error("Telegram chat ID is not configured.")
            return False
        any_sent = False
        last_error = ""
        for chat_id in recipients:
            try:
                with path.open("rb") as handle:
                    self._request(
                        "sendVideo",
                        token=token,
                        data={
                            "chat_id": chat_id,
                            "caption": caption[:1024],
                            "supports_streaming": "true",
                        },
                        files={"video": (path.name, handle, "video/mp4")},
                        timeout=120,
                    )
                any_sent = True
            except Exception as exc:
                last_error = str(exc)
        if any_sent:
            self._mark_success()
        if last_error:
            self._mark_error(last_error)
        return any_sent

    def _request(
        self,
        method: str,
        *,
        token: str,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: int,
    ) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{token}/{method}"
        try:
            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=timeout,
            )
            body = response.json()
        except requests.RequestException as exc:
            raise TelegramError(f"Telegram network error: {exc}") from exc
        except ValueError as exc:
            raise TelegramError("Telegram returned an invalid response") from exc
        if not response.ok or not body.get("ok"):
            description = body.get("description") or response.text or "Telegram request failed"
            raise TelegramError(str(description))
        return body

    def _token_or_raise(self, override: str | None = None) -> str:
        token = (override or "").strip()
        with self._lock:
            token = token or self.bot_token
        if not token:
            raise TelegramError("Enter the BotFather bot token first.")
        return token

    def _recipients(self) -> list[str]:
        """Parse the configured chat id(s) into a list.

        Supports a single chat/group id or several recipients separated by
        commas, semicolons, or newlines (e.g. a group plus individual users)."""
        with self._lock:
            raw = self.chat_id or ""
        seen: list[str] = []
        for part in re.split(r"[,;\n]+", raw):
            cleaned = part.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen

    def _recipients_or_raise(self) -> list[str]:
        recipients = self._recipients()
        if not recipients:
            raise TelegramError("Telegram chat ID is not configured.")
        return recipients

    def _is_ready(self) -> bool:
        with self._lock:
            return self.enabled and bool(self.bot_token and self.chat_id)

    def _load(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.enabled = bool(data.get("enabled", False))
        self.bot_token = str(data.get("bot_token", ""))
        self.chat_id = str(data.get("chat_id", ""))
        self.bot_username = str(data.get("bot_username", ""))
        self.last_success_at = data.get("last_success_at")
        self.last_error = str(data.get("last_error", ""))

    def _persist_locked(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": self.enabled,
            "bot_token": self.bot_token,
            "chat_id": self.chat_id,
            "bot_username": self.bot_username,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }
        self.settings_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(self.settings_path, 0o600)
        except OSError:
            pass

    def _mark_success(self) -> None:
        with self._lock:
            self._mark_success_locked()
            self._persist_locked()
    def _mark_success_locked(self) -> None:
        self.last_success_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.last_error = ""

    def _mark_error(self, message: str) -> None:
        with self._lock:
            self.last_error = message
            self._persist_locked()
