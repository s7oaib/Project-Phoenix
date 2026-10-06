"""Explicit trusted-use secrets; no permission, tool or normal-data integration.

Python cannot erase every immutable interpreter copy. Mutable buffer wiping is
best effort, not cryptographic proof or protection against process introspection.
Windows targets are case-insensitive even though reference spelling is preserved.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Never, Protocol, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    ModelWrapValidatorHandler,
    field_validator,
    model_validator,
)

__all__ = (
    "SecretReference",
    "SecretMaterial",
    "SecretStore",
    "WindowsCredentialStore",
    "SecretError",
    "SecretErrorCode",
    "redact_known_secrets",
)

_MAX_BLOB_BYTES = 2560
_GENERIC = 1
_LOCAL_MACHINE = 2
_NOT_FOUND = 1168
_NO_LOGON_SESSION = 1312
_REDACTED = "[REDACTED]"


class SecretErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    INVALID_REFERENCE = "invalid_reference"
    INVALID_MATERIAL = "invalid_material"
    STORE_FAILURE = "store_failure"
    DECODE_FAILURE = "decode_failure"


class SecretError(ValueError):
    """Only a closed safe code; no input, target, blob or OS diagnostic."""

    def __init__(self, code: SecretErrorCode) -> None:
        if not any(code is member for member in SecretErrorCode):
            raise ValueError("Invalid secret error code")
        super().__init__(code.value)
        self.error_code = code


class SecretReference(BaseModel):
    """One nonsecret identifier. Possession proves neither authority nor existence."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        hide_input_in_errors=True,
    )
    secret_id: str

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            if type(value) is not cls:
                raise ValueError("Expected exact secret reference")
            extras = value.__pydantic_extra__
            if extras is not None and type(extras) is not dict:
                raise ValueError("Invalid reference extras")
            value = {**vars(value), **(extras or {})}
        return handler(value)

    @field_validator("secret_id", mode="before")
    @classmethod
    def identifier(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("Expected strict identifier text")
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError("Identifier contains control characters")
        identity = value.strip()
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        if (
            not 1 <= len(identity) <= 128
            or identity[0] not in alphabet
            or any(char not in alphabet + "_.-" for char in identity)
        ):
            raise ValueError("Invalid secret identifier")
        return identity


def _target(reference: SecretReference) -> str:
    try:
        if type(reference) is not SecretReference:
            raise ValueError("Expected exact reference")
        accepted = SecretReference.model_validate(reference)
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise SecretError(SecretErrorCode.INVALID_REFERENCE) from None
    return "PHOENIX/" + accepted.secret_id


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


class SecretMaterial:
    """Runtime-only UTF-8 buffer; explicit trusted reveal creates an immutable copy.

    Callers own authorization, admission policy and lifetime. No lookup or coercion
    is performed. Equality/hash retain object identity rather than secret content.
    """

    __slots__ = ("_buffer", "_closed")

    def __init__(self, text: str) -> None:
        if hasattr(self, "_buffer") or type(text) is not str or not text:
            raise SecretError(SecretErrorCode.INVALID_MATERIAL)
        try:
            self._buffer = bytearray(text, "utf-8")
        except UnicodeError:
            raise SecretError(SecretErrorCode.INVALID_MATERIAL) from None
        self._closed = False

    def __init_subclass__(cls) -> Never:
        raise TypeError("SecretMaterial cannot be subclassed")

    @property
    def closed(self) -> bool:
        return self._closed

    def reveal_text_for_trusted_use(self) -> str:
        _material(self)
        try:
            return self._buffer.decode("utf-8")
        except UnicodeError:
            raise SecretError(SecretErrorCode.INVALID_MATERIAL) from None

    def close(self) -> None:
        self._closed = True
        _wipe(self._buffer)

    def __enter__(self) -> Self:
        _material(self)
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "SecretMaterial([REDACTED])"

    def __str__(self) -> str:
        return "[REDACTED]"

    def __reduce__(self) -> Never:
        raise TypeError("SecretMaterial serialization is forbidden")

    def __reduce_ex__(self, protocol: object) -> Never:
        raise TypeError("SecretMaterial serialization is forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("SecretMaterial serialization is forbidden")

    def __copy__(self) -> Never:
        raise TypeError("SecretMaterial copying is forbidden")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        raise TypeError("SecretMaterial copying is forbidden")


def _material(value: SecretMaterial) -> None:
    if (
        type(value) is not SecretMaterial
        or getattr(value, "_closed", None) is not False
        or type(getattr(value, "_buffer", None)) is not bytearray
        or not value._buffer
    ):
        raise SecretError(SecretErrorCode.INVALID_MATERIAL)


class SecretStore(Protocol):
    """Trusted synchronous storage only; this protocol grants no authorization."""

    def put(self, reference: SecretReference, material: SecretMaterial) -> None: ...

    def resolve(self, reference: SecretReference) -> SecretMaterial: ...

    def delete(self, reference: SecretReference) -> bool: ...


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _Credential(ctypes.Structure):
    # Win32 DWORD/BOOL remain 32 bits, including on 64-bit Windows.
    _fields_ = [
        ("Flags", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FileTime),
        ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", ctypes.c_uint32),
        ("AttributeCount", ctypes.c_uint32),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


class _API(Protocol):
    CredWriteW: Callable[..., int]
    CredReadW: Callable[..., int]
    CredDeleteW: Callable[..., int]
    CredFree: Callable[..., None]


def _load_api() -> _API:
    if sys.platform != "win32":
        raise SecretError(SecretErrorCode.UNAVAILABLE)
    try:
        # Explicit system DLL search; never load a caller-selected backend/path.
        dll = ctypes.WinDLL("advapi32.dll", use_last_error=True, winmode=0x800)
        pointer = ctypes.POINTER(_Credential)
        dll.CredWriteW.argtypes = [pointer, ctypes.c_uint32]
        dll.CredReadW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(pointer),
        ]
        dll.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        for function in (dll.CredWriteW, dll.CredReadW, dll.CredDeleteW):
            function.restype = ctypes.c_int32
        dll.CredFree.argtypes = [ctypes.c_void_p]
        dll.CredFree.restype = None
        return cast(_API, dll)
    except (OSError, AttributeError):
        raise SecretError(SecretErrorCode.UNAVAILABLE) from None


def _failure() -> SecretError:
    error = ctypes.get_last_error()
    return SecretError(
        SecretErrorCode.NOT_FOUND
        if error == _NOT_FOUND
        else SecretErrorCode.UNAVAILABLE
        if error == _NO_LOGON_SESSION
        else SecretErrorCode.STORE_FAILURE
    )


class WindowsCredentialStore:
    """Fixed PHOENIX/<ID> GENERIC credentials for the current Windows user.

    UTF-8 blobs have no terminator and a 2,560-byte OS limit. Persistence is
    LOCAL_MACHINE (same user's later logons on this machine, without roaming).
    Construction performs no I/O. Only explicit operations load/call WinAPI.
    """

    __slots__ = ()

    def put(self, reference: SecretReference, material: SecretMaterial) -> None:
        target = _target(reference)
        _material(material)
        if len(material._buffer) > _MAX_BLOB_BYTES:
            raise SecretError(SecretErrorCode.INVALID_MATERIAL)
        temporary = bytearray(material._buffer)
        try:
            api = _load_api()
            blob = (ctypes.c_ubyte * len(temporary)).from_buffer(temporary)
            credential = _Credential(
                Type=_GENERIC,
                TargetName=target,
                Persist=_LOCAL_MACHINE,
                CredentialBlobSize=len(temporary),
                CredentialBlob=blob,
            )
            if not api.CredWriteW(ctypes.byref(credential), 0):
                error = _failure()
                # NOT_FOUND is meaningful only for read/delete, not write.
                raise (
                    SecretError(SecretErrorCode.STORE_FAILURE)
                    if error.error_code == SecretErrorCode.NOT_FOUND
                    else error
                )
        except SecretError:
            raise
        except Exception:
            raise SecretError(SecretErrorCode.STORE_FAILURE) from None
        finally:
            _wipe(temporary)

    def resolve(self, reference: SecretReference) -> SecretMaterial:
        target = _target(reference)
        credential = ctypes.POINTER(_Credential)()
        result: SecretMaterial | None = None
        try:
            api = _load_api()
            try:
                if not api.CredReadW(target, _GENERIC, 0, ctypes.byref(credential)):
                    raise _failure()
                if not credential:
                    raise SecretError(SecretErrorCode.DECODE_FAILURE)
                evidence = credential.contents
                size = evidence.CredentialBlobSize
                if (
                    evidence.Type != _GENERIC
                    or not evidence.TargetName
                    or evidence.TargetName.lower() != target.lower()
                    or not 1 <= size <= _MAX_BLOB_BYTES
                    or not evidence.CredentialBlob
                ):
                    raise SecretError(SecretErrorCode.DECODE_FAILURE)
                # Decode only the bounded blob, never username/comment/attributes.
                temporary = bytearray(size)
                try:
                    blob = (ctypes.c_ubyte * size).from_buffer(temporary)
                    ctypes.memmove(blob, evidence.CredentialBlob, size)
                    try:
                        result = SecretMaterial(temporary.decode("utf-8"))
                    except (UnicodeError, SecretError):
                        raise SecretError(SecretErrorCode.DECODE_FAILURE) from None
                finally:
                    _wipe(temporary)
            finally:
                if credential:
                    try:
                        evidence = credential.contents
                        if (
                            evidence.CredentialBlob
                            and 1 <= evidence.CredentialBlobSize <= _MAX_BLOB_BYTES
                        ):
                            ctypes.memset(
                                evidence.CredentialBlob, 0, evidence.CredentialBlobSize
                            )
                    finally:
                        api.CredFree(credential)
        except SecretError:
            if result is not None:
                result.close()
            raise
        except Exception:
            if result is not None:
                result.close()
            raise SecretError(SecretErrorCode.STORE_FAILURE) from None
        if result is None:
            raise SecretError(SecretErrorCode.DECODE_FAILURE)
        return result

    def delete(self, reference: SecretReference) -> bool:
        target = _target(reference)
        try:
            api = _load_api()
            if api.CredDeleteW(target, _GENERIC, 0):
                return True
            error = _failure()
            if error.error_code == SecretErrorCode.NOT_FOUND:
                return False
            raise error
        except SecretError:
            raise
        except Exception:
            raise SecretError(SecretErrorCode.STORE_FAILURE) from None


def redact_known_secrets(text: str, materials: Sequence[SecretMaterial]) -> str:
    """Exact supplied values only; no unknown-secret discovery or admission policy.

    Longer values claim overlapping occurrences first; equal lengths follow the
    supplied order. Markers are never rescanned and no regex cache retains values.
    """
    if type(text) is not str or type(materials) not in (tuple, list):
        raise SecretError(SecretErrorCode.INVALID_MATERIAL)
    values = sorted(
        (
            SecretMaterial.reveal_text_for_trusted_use(material)
            for material in materials
        ),
        key=len,
        reverse=True,
    )
    occupied = bytearray(len(text))
    replacements: dict[int, int] = {}
    for value in values:
        start = text.find(value)
        while start != -1:
            end = start + len(value)
            if not any(occupied[start:end]):
                replacements[start] = len(value)
                for index in range(start, end):
                    occupied[index] = 1
            start = text.find(value, start + 1)
    output: list[str] = []
    position = 0
    while position < len(text):
        length = replacements.get(position)
        if length is None:
            output.append(text[position])
            position += 1
        else:
            output.append(_REDACTED)
            position += length
    return "".join(output)
