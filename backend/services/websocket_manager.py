"""WebSocket device connection manager — ported from V1.

Manages persistent WebSocket connections from IoT devices (cameras/laptops).
Each device registers with a room_id, and the orchestrator broadcasts
CAPTURE signals to all devices in a room.

V2: Same API, now used by the async FastAPI lifespan.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class DeviceConnectionManager:
    """Manages active WebSocket connections per device per room."""

    def __init__(self) -> None:
        self.active_devices: dict[int, WebSocket] = {}
        self.room_to_devices: dict[int, set[int]] = defaultdict(set)

    async def connect(
        self, device_id: int, room_id: int, websocket: WebSocket
    ) -> None:
        """Accept and register a device WebSocket connection."""
        await websocket.accept()
        self.active_devices[device_id] = websocket
        self.room_to_devices[room_id].add(device_id)
        logger.info(
            "ws: device %d connected to room %d", device_id, room_id
        )

    def disconnect(self, device_id: int, room_id: int | None = None) -> None:
        """Remove a device WebSocket connection."""
        self.active_devices.pop(device_id, None)
        if room_id is not None:
            room_devices = self.room_to_devices.get(room_id)
            if room_devices and device_id in room_devices:
                room_devices.remove(device_id)
        logger.info("ws: device %d disconnected", device_id)

    async def send_capture(self, room_id: int, payload: dict) -> int:
        """Broadcast a CAPTURE signal to all devices in a room.

        Returns the number of devices successfully notified.
        """
        sent = 0
        targets = list(self.room_to_devices.get(room_id, set()))
        for device_id in targets:
            ws = self.active_devices.get(device_id)
            if not ws:
                continue
            try:
                await ws.send_text(json.dumps(payload))
                sent += 1
            except Exception:
                self.disconnect(device_id, room_id)
        return sent

    @property
    def connected_count(self) -> int:
        """Number of currently connected devices."""
        return len(self.active_devices)

    def room_device_count(self, room_id: int) -> int:
        """Number of devices connected to a specific room."""
        return len(self.room_to_devices.get(room_id, set()))


# Module-level singleton
ws_manager = DeviceConnectionManager()
