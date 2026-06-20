from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app.integrations.frigate_event_bridge import FrigateEventBridge
from app.models.analytics_event import AnalyticsEvent


def create_frigate_router(
    bridge: FrigateEventBridge,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/frigate", tags=["frigate"])

    @router.post("/events")
    async def publish_frigate_event(
        event: AnalyticsEvent,
    ) -> dict[str, object]:
        return await asyncio.to_thread(bridge.handle_event, event)

    return router
