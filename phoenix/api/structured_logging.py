"""PHX-115 structured JSON logs with redaction and rotating files (ADR-145).

Every record is one JSON object per line with a closed set of keys:

- ``ts`` (UTC ISO 8601), ``level``, ``subsystem`` (the logger name) and
  ``message`` (redacted, control characters removed, at most 1,000 chars);
- the safe correlation fields ``correlation_id``, ``mission_id``, ``task_id``,
  ``step_id``, ``agent_id`` and ``event_type``, taken only from a record's
  ``extra`` and only when the value is a short identifier;
- for an exception, ``error_type`` (the class name) and ``error_frames``
  (``file:line:function`` of the innermost frames), never the exception's
  message, arguments or locals, which may hold model or tool content.

Any other ``extra`` key is dropped. ``redact`` removes credential shapes
(bearer tokens, ``key=value`` secrets, private key blocks, well-known token
prefixes) and every secret registered with ``register_secret`` (the launcher
registers the per-launch API token). HTTP client libraries that log request
URLs (``QUIET_LOGGERS``) keep only warnings (PHX-123, ADR-180). Redaction is
defence in depth: PHOENIX code logs IDs, closed codes and timings only (RULES
security 6, INV-018).

``configure_logging`` writes to stderr and to a size-rotated file in the data
directory (``logs/phoenix.jsonl``, 5 MiB x 5 backups) through a queue, so the
event loop never waits on disk; ``LoggingHandle.close`` restores the root
logger exactly.
"""

import json
import logging
import logging.handlers
import queue
import re
import threading
import traceback
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TextIO

LOG_DIRECTORY: Final = "logs"
LOG_FILE: Final = "phoenix.jsonl"
MAX_LOG_BYTES: Final = 5 * 1024 * 1024
LOG_BACKUPS: Final = 5
MAX_MESSAGE: Final = 1000
MAX_FRAMES: Final = 12
REDACTED: Final = "[REDACTED]"
# PHX-123 (ADR-180): third-party HTTP clients log full request URLs (query
# strings included) at INFO; PHOENIX keeps only their warnings (INV-018).
QUIET_LOGGERS: Final = ("httpx", "httpcore", "hpack", "urllib3")
SAFE_FIELDS: Final = (
    "correlation_id",
    "mission_id",
    "task_id",
    "step_id",
    "agent_id",
    "event_type",
)
_SAFE_VALUE: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Credential shapes. Signature fragments are concatenated so this file does not
# itself look like a credential to scanners.
_PATTERNS: Final = (
    re.compile(
        "-----BEGIN [A-Z ]*"
        + "PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
        re.DOTALL,
    ),
    re.compile(r"(?i)\bbearer\s+[^\s\"',;]+"),
    # PHX-123 (ADR-182): a quoted key ({"api_key": "..."}) and an authorization
    # scheme with its credential (Basic, Digest, ...) are redacted whole.
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret"
        r"|client[_-]?secret|password|passwd|pwd|authorization|cookie)"
        r"([\"']?\s*[:=]\s*)"
        r"(\"[^\"]*\"|'[^']*'|(?:(?:basic|digest|negotiate|ntlm)\s+)?[^\s,;&]+)"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_" + r"pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_secrets: set[str] = set()
_secrets_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Redact this exact value from every later log message (min 8 chars)."""
    if len(value) >= 8:
        with _secrets_lock:
            _secrets.add(value)


def forget_secret(value: str) -> None:
    with _secrets_lock:
        _secrets.discard(value)


def _key_value(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{REDACTED}"


def redact(text: str) -> str:
    """Remove secrets and control characters; bound the length."""
    with _secrets_lock:
        known = sorted(_secrets, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups == 3:
            text = pattern.sub(_key_value, text)
        else:
            text = pattern.sub(REDACTED, text)
    text = _CONTROL.sub(" ", text)
    if len(text) > MAX_MESSAGE:
        text = text[: MAX_MESSAGE - 1] + "…"
    return text


def safe_fields(values: Iterable[tuple[str, object]]) -> dict[str, str]:
    """Only the closed correlation fields, and only identifier-shaped values."""
    fields: dict[str, str] = {}
    for key, value in values:
        if (
            key in SAFE_FIELDS
            and isinstance(value, str)
            and _SAFE_VALUE.fullmatch(value) is not None
        ):
            fields[key] = value
    return fields


def _frames(error: BaseException) -> list[str]:
    frames = traceback.extract_tb(error.__traceback__)[-MAX_FRAMES:]
    return [
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames
    ]


def log_entry(record: logging.LogRecord) -> dict[str, object]:
    """The JSON-safe entry for one record (see the module docstring)."""
    try:
        message = record.getMessage()
    except Exception:  # a bad format string is not worth losing the record
        message = str(record.msg)
    entry: dict[str, object] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
        "level": record.levelname.lower(),
        "subsystem": redact(record.name)[:128],
        "message": redact(message),
    }
    entry.update(safe_fields((key, getattr(record, key, None)) for key in SAFE_FIELDS))
    error = record.exc_info[1] if record.exc_info else None
    if error is not None:
        entry["error_type"] = type(error).__name__[:128]
        entry["error_frames"] = _frames(error)
    return entry


_ENTRY: Final = "phoenix_log_entry"


class JsonLogFormatter(logging.Formatter):
    """One redacted JSON object per record; never the exception text."""

    def format(self, record: logging.LogRecord) -> str:
        entry = getattr(record, _ENTRY, None)
        if not isinstance(entry, dict):
            entry = log_entry(record)
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


class _EntryQueueHandler(logging.handlers.QueueHandler):
    """Builds the safe entry on the logging thread, before the stdlib handler
    would merge arguments and drop the exception; only the entry crosses."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        prepared = logging.makeLogRecord(
            {"name": record.name, "levelno": record.levelno, "msg": ""}
        )
        prepared.levelname = record.levelname
        setattr(prepared, _ENTRY, log_entry(record))
        return prepared


@dataclass
class LoggingHandle:
    """Undo for ``configure_logging``: stops the queue and restores the root."""

    listener: logging.handlers.QueueListener
    queue_handler: logging.Handler
    targets: tuple[logging.Handler, ...]
    previous_level: int
    log_path: Path
    quiet_levels: dict[str, int] = field(default_factory=dict)
    _closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        root = logging.getLogger()
        root.removeHandler(self.queue_handler)
        root.setLevel(self.previous_level)
        for name, previous in self.quiet_levels.items():
            logging.getLogger(name).setLevel(previous)
        self.listener.stop()  # flushes every queued record first
        for handler in self.targets:
            handler.close()


def configure_logging(
    data_dir: Path,
    stream: TextIO,
    *,
    level: int = logging.INFO,
    max_bytes: int = MAX_LOG_BYTES,
    backups: int = LOG_BACKUPS,
) -> LoggingHandle:
    """JSON logs to ``stream`` and to ``<data_dir>/logs/phoenix.jsonl``."""
    directory = data_dir / LOG_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_FILE
    formatter = JsonLogFormatter()
    to_file = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8", delay=True
    )
    to_stream = logging.StreamHandler(stream)
    for handler in (to_file, to_stream):
        handler.setFormatter(formatter)
    records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = _EntryQueueHandler(records)
    listener = logging.handlers.QueueListener(
        records, to_file, to_stream, respect_handler_level=False
    )
    root = logging.getLogger()
    previous = root.level
    root.addHandler(queue_handler)
    root.setLevel(level)
    quiet_levels: dict[str, int] = {}
    for name in QUIET_LOGGERS:
        quiet = logging.getLogger(name)
        quiet_levels[name] = quiet.level
        quiet.setLevel(max(logging.WARNING, level))
    listener.start()
    return LoggingHandle(
        listener=listener,
        queue_handler=queue_handler,
        targets=(to_file, to_stream),
        previous_level=previous,
        log_path=path,
        quiet_levels=quiet_levels,
    )
