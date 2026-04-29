"""Debug frame store — ring buffer for last N annotated frames.

Used by the dashboard to display the most recent detection results
without storing full images in the database.
"""

from collections import deque


class DebugFrameStore:
    """Thread-safe ring buffer for debug frames."""

    def __init__(self, max_items: int = 5):
        self._frames: deque[dict] = deque(maxlen=max_items)

    def push(self, frame: dict) -> None:
        """Push a new annotated frame dict to the front."""
        self._frames.appendleft(frame)

    def latest(self) -> dict | None:
        """Return the most recent frame, or None."""
        return self._frames[0] if self._frames else None

    def list_items(self) -> list[dict]:
        """Return all stored frames (newest first)."""
        return list(self._frames)


# Module-level singleton
debug_frame_store = DebugFrameStore()
