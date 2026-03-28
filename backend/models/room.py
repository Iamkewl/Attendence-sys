"""Room and Device models.

Upgraded from V1:
- Rooms now have a capacity field
- Devices store secret_key_hash (not plaintext), added type and rtsp_url
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class Room(Base):
    """Physical room where classes are held and cameras are installed."""

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    room_name: Mapped[str] = mapped_column(
        String(120), unique=True, nullable=False
    )
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    devices: Mapped[list["Device"]] = relationship(
        "Device", back_populates="room", cascade="all, delete-orphan"
    )
    schedules: Mapped[list["Schedule"]] = relationship(
        "Schedule", back_populates="room", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Room id={self.id} name={self.room_name}>"


class Device(Base):
    """IoT device (camera or laptop) registered to a room.

    Devices authenticate via HMAC-SHA256 signed payloads.
    secret_key_hash stores the bcrypt hash of the device secret.
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secret_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="camera"
    )
    rtsp_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ws_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    room: Mapped["Room"] = relationship("Room", back_populates="devices")

    def __repr__(self) -> str:
        return f"<Device id={self.id} room_id={self.room_id} type={self.type}>"


# Forward reference
from backend.models.course import Schedule  # noqa: E402, F811
