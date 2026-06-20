from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from .access_tokens import AccessTokenStore, ensure_aware


@dataclass(frozen=True)
class TailgatingResult:
    tracker_id: int
    authorized: bool
    event_type: str | None
    reason: str
    tokens_available: int
    show_alert: bool = False

    @property
    def is_tailgating(self) -> bool:
        return self.event_type is not None


class TailgatingDetector:
    """Compares anonymous IN crossings with external access authorizations."""

    def __init__(
        self,
        token_store: AccessTokenStore,
        camera_name: str,
        enabled: bool = True,
        tailgating_time_window_seconds: float = 4,
        alert_cooldown_seconds: float = 5,
    ):
        self.token_store = token_store
        self.camera_name = camera_name
        self.enabled = enabled
        self.window = timedelta(seconds=float(tailgating_time_window_seconds))
        self.alert_cooldown = timedelta(seconds=float(alert_cooldown_seconds))
        self._recent_entries: deque[tuple[datetime, int, bool]] = deque()
        self._last_alert_at: datetime | None = None
        self._last_door_signature: frozenset[int] = frozenset()

    def handle_in_crossing(
        self, tracker_id: int, now: datetime | None = None
    ) -> TailgatingResult:
        checked_at = ensure_aware(now)
        self._purge_recent(checked_at)

        if not self.enabled:
            return TailgatingResult(
                tracker_id=tracker_id,
                authorized=True,
                event_type=None,
                reason="TAILGATING_DISABLED",
                tokens_available=self.token_store.available_count(
                    self.camera_name, checked_at
                ),
            )

        token = self.token_store.consume(self.camera_name, checked_at)
        authorized = token is not None
        recent_authorized = any(item[2] for item in self._recent_entries)
        self._recent_entries.append((checked_at, tracker_id, authorized))
        tokens_available = self.token_store.available_count(self.camera_name, checked_at)

        if authorized:
            return TailgatingResult(
                tracker_id=tracker_id,
                authorized=True,
                event_type=None,
                reason="AUTHORIZED_TOKEN_CONSUMED",
                tokens_available=tokens_available,
            )

        reason = (
            "CLOSE_FOLLOWING_WITHOUT_TOKEN"
            if recent_authorized
            else "NO_AUTHORIZATION_TOKEN"
        )
        return TailgatingResult(
            tracker_id=tracker_id,
            authorized=False,
            event_type="TAILGATING_DETECTED",
            reason=reason,
            tokens_available=tokens_available,
            show_alert=self._claim_alert(checked_at),
        )

    def check_door_zone(
        self,
        tracker_ids: list[int],
        now: datetime | None = None,
    ) -> TailgatingResult | None:
        """Raise one possible event for a new over-capacity door-zone group."""
        checked_at = ensure_aware(now)
        signature = frozenset(tracker_ids)
        available_uses = self.token_store.available_uses(self.camera_name, checked_at)
        is_suspicious = self.enabled and len(signature) >= 2 and len(signature) > available_uses

        if not is_suspicious:
            self._last_door_signature = frozenset()
            return None
        if signature == self._last_door_signature:
            return None

        self._last_door_signature = signature
        suspect_id = tracker_ids[-1]
        return TailgatingResult(
            tracker_id=suspect_id,
            authorized=False,
            event_type="POSSIBLE_TAILGATING",
            reason="MULTIPLE_PEOPLE_IN_DOOR_ZONE",
            tokens_available=self.token_store.available_count(
                self.camera_name, checked_at
            ),
            show_alert=self._claim_alert(checked_at),
        )

    def reset(self) -> None:
        self._recent_entries.clear()
        self._last_door_signature = frozenset()
        self._last_alert_at = None

    def _purge_recent(self, now: datetime) -> None:
        cutoff = now - self.window
        while self._recent_entries and self._recent_entries[0][0] < cutoff:
            self._recent_entries.popleft()

    def _claim_alert(self, now: datetime) -> bool:
        if self._last_alert_at is None or now - self._last_alert_at >= self.alert_cooldown:
            self._last_alert_at = now
            return True
        return False


class EntryBurstDetector:
    """Detect distinct people entering inside a rolling time window."""

    def __init__(
        self,
        minimum_people: int = 3,
        time_window_seconds: float = 4,
        alert_cooldown_seconds: float = 5,
    ):
        if minimum_people < 2:
            raise ValueError("minimum_people must be at least two")
        if time_window_seconds <= 0:
            raise ValueError("time_window_seconds must be greater than zero")
        self.minimum_people = int(minimum_people)
        self.window = timedelta(seconds=float(time_window_seconds))
        self.alert_cooldown = timedelta(seconds=float(alert_cooldown_seconds))
        self._recent_entries: deque[tuple[datetime, int]] = deque()
        self._last_alert_at: datetime | None = None

    def handle_in_crossing(
        self, tracker_id: int, now: datetime | None = None
    ) -> TailgatingResult:
        checked_at = ensure_aware(now)
        cutoff = checked_at - self.window
        while self._recent_entries and self._recent_entries[0][0] < cutoff:
            self._recent_entries.popleft()
        if any(
            recent_tracker_id == tracker_id
            for _, recent_tracker_id in self._recent_entries
        ):
            return TailgatingResult(
                tracker_id=tracker_id,
                authorized=True,
                event_type=None,
                reason="DUPLICATE_TRACKER_IGNORED",
                tokens_available=0,
            )
        self._recent_entries.append((checked_at, tracker_id))
        group_size = len(self._recent_entries)

        if group_size < self.minimum_people:
            return TailgatingResult(
                tracker_id=tracker_id,
                authorized=True,
                event_type=None,
                reason=f"ENTRY_GROUP_SIZE_{group_size}",
                tokens_available=0,
            )

        show_alert = (
            self._last_alert_at is None
            or checked_at - self._last_alert_at >= self.alert_cooldown
        )
        if show_alert:
            self._last_alert_at = checked_at
        return TailgatingResult(
            tracker_id=tracker_id,
            authorized=False,
            event_type="TAILGATING_DETECTED",
            reason=f"{group_size}_PEOPLE_ENTERED_WITHIN_WINDOW",
            tokens_available=0,
            show_alert=show_alert,
        )

    def reset(self) -> None:
        self._recent_entries.clear()
        self._last_alert_at = None
