"""Authoritative metadata owner; intentional model imports register domain tables."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared SQLAlchemy 2 base with initially empty metadata."""
