"""Authoritative metadata owner; domain models belong to later tickets."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared SQLAlchemy 2 base with initially empty metadata."""
