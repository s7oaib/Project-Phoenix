"""PHX-104 user-writable settings: one key-value row per setting (ADR-089).

The ``app_settings`` table accepts exactly two keys. ``workspace_root``
(PHX-104) holds the validated, normalized absolute path
(``phoenix.api.workspace``); this store does no validation of its own beyond
the schema. ``default_mission_path`` (PHX-108, ADR-111) holds the user's saved
mission path, ``auto``, ``direct`` or ``complex``; the schema refuses any other
value. Each method is one short synchronous transaction (call it through
``asyncio.to_thread``).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import CheckConstraint, PrimaryKeyConstraint, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from phoenix.persistence.base import Base
from phoenix.persistence.memory import UTCDateTime

WORKSPACE_ROOT_KEY = "workspace_root"
DEFAULT_MISSION_PATH_KEY = "default_mission_path"


class SettingRow(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        PrimaryKeyConstraint("key", name="pk_app_settings"),
        CheckConstraint(
            "key IN ('workspace_root', 'default_mission_path')",
            name="ck_app_settings_key",
        ),
        CheckConstraint("length(value) > 0", name="ck_app_settings_value"),
        CheckConstraint(
            "key <> 'default_mission_path' OR value IN ('auto', 'direct', 'complex')",
            name="ck_app_settings_mission_path",
        ),
    )

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class StoredSetting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str
    updated_at: datetime


class SettingsStore:
    """Synchronous access to ``app_settings``."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def get_workspace_root(self) -> StoredSetting | None:
        return self._get(WORKSPACE_ROOT_KEY)

    def set_workspace_root(self, value: str, now: datetime) -> None:
        self._set(WORKSPACE_ROOT_KEY, value, now)

    def get_default_mission_path(self) -> StoredSetting | None:
        return self._get(DEFAULT_MISSION_PATH_KEY)

    def set_default_mission_path(self, value: str, now: datetime) -> None:
        self._set(DEFAULT_MISSION_PATH_KEY, value, now)

    def _get(self, key: str) -> StoredSetting | None:
        with self._sessions() as session:
            row = session.get(SettingRow, key)
            if row is None:
                return None
            return StoredSetting(value=row.value, updated_at=row.updated_at)

    def _set(self, key: str, value: str, now: datetime) -> None:
        with self._sessions.begin() as session:
            row = session.get(SettingRow, key)
            if row is None:
                session.add(SettingRow(key=key, value=value, updated_at=now))
            else:
                row.value = value
                row.updated_at = now
