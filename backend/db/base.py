"""SQLAlchemy DeclarativeBase with metadata naming conventions.

All models inherit from this Base class. The naming convention
ensures Alembic auto-generates predictable constraint names.
"""

from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    pass
