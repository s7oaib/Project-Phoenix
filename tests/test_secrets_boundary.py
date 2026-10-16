"""Synthetic values and a fake WinAPI only; never access real OS credentials."""

from __future__ import annotations

import ast
import asyncio
import builtins
import copy
import ctypes
import dataclasses
import importlib
import inspect
import json
import logging
import os
import pickle
import socket
import sqlite3
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Never, cast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventType
from phoenix.memory.schema import MemoryRecord
from phoenix.memory.service import PuddingMemoryService
from phoenix.models.embeddings import EmbeddingProvider
from phoenix.rag.citations import CitationRenderRecord, ProvenanceCitationService
from phoenix.rag.ingestion import DocumentChunkRecord, DocumentIngestionService
from phoenix.rag.retrieval import ContextItem, RetrievalContextService
from phoenix.rag.vector_store import VectorStore
from phoenix.security import secrets as module
from phoenix.security.audit import AuditRecord, AuditTrail
from phoenix.security.permissions import (
    DecisionReason,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionPolicyRule,
    PermissionRequest,
    PermissionStatus,
    UserPermissionDecision,
)
from phoenix.security.secrets import (
    SecretError,
    SecretErrorCode,
    SecretMaterial,
    SecretReference,
    SecretStore,
    WindowsCredentialStore,
    redact_known_secrets,
)
from phoenix.tools.registry import ToolDefinition, ToolRegistry, ToolRiskLevel

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=UTC)
PRIVATE = "synthetic-only π🙂 value\n with whitespace "
BAD = (None, True, 1, 1.5, b"text", object(), "", "   ")


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("Unexpected boundary side effect")


@pytest.fixture(autouse=True)
def no_real_winapi(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even a broken test/implementation cannot silently load the real DLL.
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)


def reference() -> SecretReference:
    return SecretReference(secret_id="Service.A")


def safe_error(error: BaseException, code: SecretErrorCode) -> None:
    assert type(error) is SecretError and error.error_code is code
    assert str(error) == code.value
    assert PRIVATE not in repr(error)
    assert PRIVATE not in "".join(traceback.format_exception(error))


class FakeAPI:
    """Test-only native call boundary, with retained buffers to inspect cleanup."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.written: bytes | None = None
        self.read_blob = (ctypes.c_ubyte * len(PRIVATE.encode("utf-8")))(
            *PRIVATE.encode("utf-8")
        )
        self.credential = module._Credential(
            Type=1,
            TargetName="PHOENIX/Service.A",
            Persist=2,
            CredentialBlobSize=len(self.read_blob),
            CredentialBlob=self.read_blob,
        )
        self.success = True
        self.error = 1168
        self.raise_at: str | None = None
        self.null_result = False
        self.freed = False

    def _call(self, name: str, detail: object) -> int:
        self.calls.append((name, detail))
        if self.raise_at == name:
            raise OSError(PRIVATE)
        return int(self.success)

    def CredWriteW(self, pointer: object, flags: int) -> int:
        value = ctypes.cast(
            cast(ctypes.c_void_p, pointer), ctypes.POINTER(module._Credential)
        ).contents
        self.written = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize)
        return self._call(
            "write",
            (
                value.TargetName,
                value.Type,
                value.Persist,
                flags,
                value.Flags,
                value.AttributeCount,
                value.Comment,
                value.UserName,
                value.TargetAlias,
            ),
        )

    def CredReadW(self, target: str, kind: int, flags: int, output: object) -> int:
        result = self._call("read", (target, kind, flags))
        if result and not self.null_result:
            ctypes.cast(
                cast(ctypes.c_void_p, output),
                ctypes.POINTER(ctypes.POINTER(module._Credential)),
            )[0] = ctypes.pointer(self.credential)
        return result

    def CredDeleteW(self, target: str, kind: int, flags: int) -> int:
        return self._call("delete", (target, kind, flags))

    def CredFree(self, pointer: object) -> None:
        value = ctypes.cast(
            cast(ctypes.c_void_p, pointer), ctypes.POINTER(module._Credential)
        ).contents
        assert ctypes.addressof(value) == ctypes.addressof(self.credential)
        self._call("free", None)
        self.freed = True


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    fake = FakeAPI()
    monkeypatch.setattr(module, "_load_api", lambda: fake)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: fake.error, raising=False)
    return fake


@pytest.mark.parametrize(
    "value",
    BAD
    + (
        "/a",
        "a/b",
        "a\\b",
        "C:a",
        "a*b",
        "a?b",
        ".",
        "..",
        "-a",
        "_a",
        "a b",
        "a@b",
        "PHOENIX/a",
        "a|b",
        "a=a",
        "é",
        "a" * 129,
    ),
)
def test_invalid_reference(value: object) -> None:
    with pytest.raises(ValidationError):
        SecretReference.model_validate({"secret_id": value})


@pytest.mark.parametrize(
    "control", tuple(chr(x) for x in range(32)) + tuple(chr(x) for x in range(127, 160))
)
@pytest.mark.parametrize("position", ("before", "inside", "after"))
def test_reference_rejects_controls(control: str, position: str) -> None:
    value = {
        "before": control + "A",
        "inside": "A" + control + "B",
        "after": "A" + control,
    }[position]
    with pytest.raises(ValidationError):
        SecretReference(secret_id=value)


@pytest.mark.parametrize("identity", ("A", "a.b_C-9", "7", "A" * 128))
def test_reference_contract(identity: str) -> None:
    ref = SecretReference(secret_id="  " + identity + "  ")
    assert set(SecretReference.model_fields) == {"secret_id"}
    assert ref.secret_id == identity
    assert ref.model_dump() == {"secret_id": identity}
    assert json.loads(ref.model_dump_json()) == {"secret_id": identity}
    assert SecretReference.model_validate(ref) == ref
    with pytest.raises(ValidationError):
        ref.secret_id = "changed"
    with pytest.raises(ValidationError):
        SecretReference.model_validate({"secret_id": identity, "value": PRIVATE})


@pytest.mark.parametrize("operation", ("put", "resolve", "delete"))
@pytest.mark.parametrize(
    "forgery",
    ("extra", "value", "extras_container", "missing_extras", "subclass", "wrong_type"),
)
def test_exact_reference_revalidation_before_backend(
    operation: str, forgery: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = reference()
    if forgery == "extra":
        ref = ref.model_copy(update={"target": "other/target"})
    elif forgery == "value":
        ref = ref.model_copy(update={"secret_id": "other/target"})
    elif forgery == "extras_container":
        object.__setattr__(ref, "__pydantic_extra__", [PRIVATE])
    elif forgery == "missing_extras":
        object.__delattr__(ref, "__pydantic_extra__")
    elif forgery == "subclass":

        class Child(SecretReference):
            pass

        ref = Child(secret_id="A")
    else:
        ref = cast(SecretReference, {"secret_id": "A"})
    monkeypatch.setattr(module, "_load_api", forbidden)
    with pytest.raises(SecretError) as caught:
        args = (ref, SecretMaterial(PRIVATE)) if operation == "put" else (ref,)
        getattr(WindowsCredentialStore(), operation)(*args)
    safe_error(caught.value, SecretErrorCode.INVALID_REFERENCE)


@pytest.mark.parametrize(
    "text", (PRIVATE, " ", "\x00", "漢字", "🙂", "e\u0301", "a" * 3000)
)
def test_material_exact_lifetime_and_privacy(text: str) -> None:
    value = SecretMaterial(text)
    assert value.reveal_text_for_trusted_use() == text
    assert repr(value) == "SecretMaterial([REDACTED])" and str(value) == "[REDACTED]"
    assert not isinstance(value, BaseModel) and not dataclasses.is_dataclass(value)
    assert not hasattr(value, "__dict__")
    assert value != SecretMaterial(text) and hash(value) == object.__hash__(value)
    buffer = value._buffer
    assert type(buffer) is bytearray
    value.close()
    assert value.closed and not any(buffer)
    value.close()
    assert value._buffer is buffer and not any(buffer)
    with pytest.raises(SecretError) as caught:
        value.reveal_text_for_trusted_use()
    safe_error(caught.value, SecretErrorCode.INVALID_MATERIAL)


@pytest.mark.parametrize("value", BAD[:-1] + ("\ud800",))
def test_invalid_material(value: object) -> None:
    with pytest.raises(SecretError) as caught:
        SecretMaterial(cast(str, value))
    safe_error(caught.value, SecretErrorCode.INVALID_MATERIAL)


@pytest.mark.parametrize(
    "operation",
    (
        json.dumps,
        pickle.dumps,
        copy.copy,
        copy.deepcopy,
        dataclasses.asdict,
        dict,
        bytes,
        vars,
        lambda value: value.__getstate__(),
    ),
)
@pytest.mark.parametrize("closed", (False, True))
def test_serialization_and_copy_rejected(
    operation: Callable[..., object], closed: bool
) -> None:
    value = SecretMaterial(PRIVATE)
    if closed:
        value.close()
    with pytest.raises(TypeError) as caught:
        operation(value)
    assert PRIVATE not in str(caught.value)
    assert not hasattr(value, "model_dump") and not hasattr(value, "dict")
    with pytest.raises(ValueError):
        TypeAdapter(object).dump_json(value)


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_all_pickle_protocols_rejected(protocol: int) -> None:
    with pytest.raises(TypeError):
        pickle.dumps(SecretMaterial(PRIVATE), protocol=protocol)


def test_material_context_reinit_and_uninitialized() -> None:
    value = SecretMaterial(PRIVATE)
    with pytest.raises(SecretError):
        SecretMaterial.__init__(value, "changed")
    with pytest.raises(RuntimeError):
        with value as entered:
            assert entered is value and not value.closed
            raise RuntimeError("consumer failed")
    assert value.closed
    with pytest.raises(SecretError):
        value.__enter__()
    with pytest.raises(SecretError):
        object.__new__(SecretMaterial).reveal_text_for_trusted_use()


@pytest.mark.parametrize(
    "text,values,expected",
    (
        ("dummy", ("dummy",), "[REDACTED]"),
        ("dummy dummy", ("dummy",), "[REDACTED] [REDACTED]"),
        ("abcdef abc", ("abc", "abcdef"), "[REDACTED] [REDACTED]"),
        ("ababa", ("aba", "ab"), "[REDACTED]ba"),
        ("漢字🙂 twice 漢字🙂", ("漢字🙂",), "[REDACTED] twice [REDACTED]"),
        ("a  b", ("  ",), "a[REDACTED]b"),
        ("dummy", ("dummy", "RED"), "[REDACTED]"),
        ("unknown-token-looking-text", ("dummy",), "unknown-token-looking-text"),
        ("dummy", (), "dummy"),
        ("", ("dummy",), ""),
        ("dummy", ("dummy", "dummy"), "[REDACTED]"),
        ("a\x00b", ("\x00",), "a[REDACTED]b"),
    ),
)
def test_exact_redaction(text: str, values: tuple[str, ...], expected: str) -> None:
    materials = tuple(SecretMaterial(value) for value in values)
    assert redact_known_secrets(text, materials) == expected
    assert redact_known_secrets(text, list(reversed(materials))) == expected
    assert all(not value.closed for value in materials)


@pytest.mark.parametrize(
    "materials", (None, {}, set(), iter(()), ("dummy",), (object(),))
)
def test_redaction_requires_exact_ordered_materials(materials: object) -> None:
    with pytest.raises(SecretError):
        redact_known_secrets("dummy", cast(tuple[SecretMaterial, ...], materials))


@pytest.mark.parametrize("text", BAD[:6])
def test_redaction_strict_text(text: object) -> None:
    with pytest.raises(SecretError):
        redact_known_secrets(cast(str, text), ())


def test_closed_redaction_even_empty_text() -> None:
    value = SecretMaterial(PRIVATE)
    value.close()
    with pytest.raises(SecretError):
        redact_known_secrets("", (value,))


def test_store_protocol_and_no_other_public_capability() -> None:
    store: SecretStore = WindowsCredentialStore()
    assert tuple(name for name in vars(SecretStore) if not name.startswith("_")) == (
        "put",
        "resolve",
        "delete",
    )
    assert tuple(name for name in vars(type(store)) if not name.startswith("_")) == (
        "put",
        "resolve",
        "delete",
    )
    with pytest.raises(TypeError):
        WindowsCredentialStore(target="arbitrary")  # type: ignore[call-arg]


def test_put_exact_bytes_namespace_and_cleanup(
    api: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = SecretMaterial(PRIVATE)
    wiped: list[bytearray] = []
    original = module._wipe

    def wipe(buffer: bytearray) -> None:
        original(buffer)
        wiped.append(buffer)

    monkeypatch.setattr(module, "_wipe", wipe)
    WindowsCredentialStore().put(SecretReference(secret_id="  Service.A  "), value)
    assert api.written == PRIVATE.encode("utf-8")
    assert api.calls == [
        ("write", ("PHOENIX/Service.A", 1, 2, 0, 0, 0, None, None, None))
    ]
    assert len(wiped) == 1 and not any(wiped[0])
    assert value.reveal_text_for_trusted_use() == PRIVATE


@pytest.mark.parametrize("size", (1, 2560, 2561))
def test_put_os_blob_bound(size: int, api: FakeAPI) -> None:
    value = SecretMaterial("a" * size)
    if size > 2560:
        with pytest.raises(SecretError) as caught:
            WindowsCredentialStore().put(reference(), value)
        safe_error(caught.value, SecretErrorCode.INVALID_MATERIAL)
        assert not api.calls
    else:
        WindowsCredentialStore().put(reference(), value)
        assert api.written is not None and len(api.written) == size


@pytest.mark.parametrize(
    "material", (None, PRIVATE, object(), object.__new__(SecretMaterial))
)
def test_put_invalid_material_zero_backend(material: object, api: FakeAPI) -> None:
    with pytest.raises(SecretError):
        WindowsCredentialStore().put(reference(), cast(SecretMaterial, material))
    assert not api.calls


def test_put_closed_zero_backend(api: FakeAPI) -> None:
    value = SecretMaterial(PRIVATE)
    value.close()
    with pytest.raises(SecretError):
        WindowsCredentialStore().put(reference(), value)
    assert not api.calls


def test_resolve_independent_and_free_wipe(
    api: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiped: list[bytearray] = []
    original = module._wipe

    def wipe(buffer: bytearray) -> None:
        original(buffer)
        wiped.append(buffer)

    monkeypatch.setattr(module, "_wipe", wipe)
    value = WindowsCredentialStore().resolve(reference())
    assert value.reveal_text_for_trusted_use() == PRIVATE
    assert api.calls == [("read", ("PHOENIX/Service.A", 1, 0)), ("free", None)]
    assert api.freed and not any(api.read_blob)
    assert len(wiped) == 1 and not any(wiped[0])
    assert any(value._buffer)
    value.close()
    assert value.closed and not any(value._buffer)


@pytest.mark.parametrize(
    "malformed",
    (
        "null",
        "empty",
        "oversized",
        "no_blob",
        "bad_type",
        "bad_target",
        "missing_target",
        "invalid_utf8",
    ),
)
def test_malformed_read_free_and_safe_error(malformed: str, api: FakeAPI) -> None:
    if malformed == "null":
        api.null_result = True
    elif malformed == "empty":
        api.credential.CredentialBlobSize = 0
    elif malformed == "oversized":
        api.credential.CredentialBlobSize = 2561
    elif malformed == "no_blob":
        api.credential.CredentialBlob = ctypes.POINTER(ctypes.c_ubyte)()
    elif malformed == "bad_type":
        api.credential.Type = 2
    elif malformed == "bad_target":
        api.credential.TargetName = "OTHER/Service.A"
    elif malformed == "missing_target":
        api.credential.TargetName = None
    else:
        api.read_blob[0] = 255
    with pytest.raises(SecretError) as caught:
        WindowsCredentialStore().resolve(reference())
    safe_error(caught.value, SecretErrorCode.DECODE_FAILURE)
    assert api.freed == (malformed != "null")


def test_windows_case_alias_returned_target(api: FakeAPI) -> None:
    api.credential.TargetName = "phoenix/service.a"
    with WindowsCredentialStore().resolve(reference()) as value:
        assert value.reveal_text_for_trusted_use() == PRIVATE


@pytest.mark.parametrize("error", (1168, 1312, 5, 87, 0))
@pytest.mark.parametrize("operation", ("put", "resolve", "delete"))
def test_winapi_error_mapping_no_retry(
    error: int, operation: str, api: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.success = False
    api.error = error
    wiped: list[bytearray] = []
    original = module._wipe

    def wipe(buffer: bytearray) -> None:
        original(buffer)
        wiped.append(buffer)

    monkeypatch.setattr(module, "_wipe", wipe)
    args = (
        (reference(), SecretMaterial(PRIVATE)) if operation == "put" else (reference(),)
    )
    if operation == "delete" and error == 1168:
        assert WindowsCredentialStore().delete(reference()) is False
    else:
        expected = (
            SecretErrorCode.UNAVAILABLE
            if error == 1312
            else SecretErrorCode.NOT_FOUND
            if error == 1168 and operation == "resolve"
            else SecretErrorCode.STORE_FAILURE
        )
        with pytest.raises(SecretError) as caught:
            getattr(WindowsCredentialStore(), operation)(*args)
        safe_error(caught.value, expected)
    assert len(api.calls) == 1
    assert not api.freed
    assert operation != "put" or (len(wiped) == 1 and not any(wiped[0]))


def test_delete_exact_target_only(api: FakeAPI) -> None:
    assert WindowsCredentialStore().delete(reference()) is True
    assert api.calls == [("delete", ("PHOENIX/Service.A", 1, 0))]


@pytest.mark.parametrize("where", ("write", "read", "delete", "free"))
def test_native_exception_is_safe_and_cleanup(
    where: str, api: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.raise_at = where
    values: list[SecretMaterial] = []
    original = SecretMaterial.close

    def close(value: SecretMaterial) -> None:
        original(value)
        values.append(value)

    monkeypatch.setattr(SecretMaterial, "close", close)
    operation = {
        "write": "put",
        "read": "resolve",
        "free": "resolve",
        "delete": "delete",
    }[where]
    args = (
        (reference(), SecretMaterial(PRIVATE)) if operation == "put" else (reference(),)
    )
    with pytest.raises(SecretError) as caught:
        getattr(WindowsCredentialStore(), operation)(*args)
    safe_error(caught.value, SecretErrorCode.STORE_FAILURE)
    assert sum(name == where for name, _ in api.calls) == 1
    if where == "free":
        assert len(values) == 1 and values[0].closed and not any(values[0]._buffer)
        assert not any(api.read_blob)


@pytest.mark.parametrize("operation", ("put", "resolve", "delete"))
@pytest.mark.parametrize("platform", ("linux", "darwin"))
def test_unsupported_platform_explicit_unavailable(
    operation: str, platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    args = (
        (reference(), SecretMaterial(PRIVATE)) if operation == "put" else (reference(),)
    )
    with pytest.raises(SecretError) as caught:
        getattr(WindowsCredentialStore(), operation)(*args)
    safe_error(caught.value, SecretErrorCode.UNAVAILABLE)


def test_native_loader_signatures_without_real_dll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    functions = SimpleNamespace(
        **{
            name: SimpleNamespace()
            for name in ("CredWriteW", "CredReadW", "CredDeleteW", "CredFree")
        }
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def loader(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return functions

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    assert module._load_api() is functions
    assert calls == [(("advapi32.dll",), {"use_last_error": True, "winmode": 0x800})]
    pointer = ctypes.POINTER(module._Credential)
    assert functions.CredWriteW.argtypes == [pointer, ctypes.c_uint32]
    assert functions.CredReadW.argtypes == [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(pointer),
    ]
    assert functions.CredDeleteW.argtypes == [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    assert (
        functions.CredFree.argtypes == [ctypes.c_void_p]
        and functions.CredFree.restype is None
    )
    assert all(
        getattr(functions, name).restype is ctypes.c_int32
        for name in ("CredWriteW", "CredReadW", "CredDeleteW")
    )
    assert ctypes.sizeof(module._FileTime) == 8
    assert ctypes.sizeof(module._Credential) == (
        80 if ctypes.sizeof(ctypes.c_void_p) == 8 else 52
    )


@pytest.mark.parametrize("error", (OSError, AttributeError))
def test_dll_unavailable_safe(
    error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> Never:
        raise error(PRIVATE)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", fail, raising=False)
    with pytest.raises(SecretError) as caught:
        WindowsCredentialStore().resolve(reference())
    safe_error(caught.value, SecretErrorCode.UNAVAILABLE)


@pytest.mark.parametrize("approval", (None, "approved", "denied"))
@pytest.mark.parametrize("evidence", ("none", "grant", "policy", "both"))
def test_synthetic_p5_reveal_always_blocked(
    approval: str | None, evidence: str
) -> None:
    definition = ToolDefinition(
        tool_id="synthetic.secrets.reveal",
        description="Safe, approved reveal",
        input_schema={},
        output_schema={},
        risk_level=ToolRiskLevel.P5,
        permission_requirement="safe approved",
        timeout_seconds=1,
        cancellation_supported=False,
        audit_behavior="safe",
        supported_platforms=("Windows",),
        supported_environments=("test",),
    )
    data = dict(
        actor_id="actor",
        tool_id=definition.tool_id,
        action="reveal",
        resource="PHOENIX/Service.A",
        reason="approved safe",
    )
    request = PermissionRequest.model_validate(
        dict(request_id="request", requested_at=NOW, **data)
    )
    grants = (
        (
            PermissionGrant.model_validate(
                dict(
                    grant_id="grant",
                    risk_level="P2",
                    duration="persistent",
                    issued_at=NOW,
                    user_decision="approved",
                    **data,
                )
            ),
        )
        if evidence in ("grant", "both")
        else ()
    )
    rules = (
        (
            PermissionPolicyRule.model_validate(
                dict(rule_id="rule", risk_level="P2", **data)
            ),
        )
        if evidence in ("policy", "both")
        else ()
    )
    user = (
        UserPermissionDecision.model_validate(
            dict(
                decision_id="decision",
                request_id="request",
                decision=approval,
                decided_at=NOW,
            )
        )
        if approval is not None
        else None
    )
    result = PermissionEngine(
        ToolRegistry((definition,)), PermissionPolicy(rules=rules)
    ).evaluate(request, grants=grants, user_decision=user)
    assert (
        result.status is PermissionStatus.BLOCKED
        and result.reason_code is DecisionReason.P5_BLOCKED
    )
    assert (
        result.user_decision_id is None
        and result.matched_grant_id is None
        and result.matched_policy_rule_id is None
    )


@pytest.mark.parametrize("field", ("content", "metadata", "nested_metadata"))
def test_material_rejected_by_normal_memory(field: str) -> None:
    value = SecretMaterial(PRIVATE)
    data: dict[str, object] = dict(
        id="memory",
        type="working",
        content="ordinary",
        source_type="test",
        scope="test",
        confidence=0.5,
        importance=0.5,
        created_at=NOW,
        updated_at=NOW,
    )
    if field == "content":
        data["content"] = value
    else:
        data["metadata"] = {
            "value": value if field == "metadata" else [{"nested": value}]
        }
    with pytest.raises(ValidationError) as caught:
        MemoryRecord.model_validate(data)
    assert PRIVATE not in str(caught.value)
    assert caught.value.errors()[0]["loc"][0] == (
        "content" if field == "content" else "metadata"
    )


@pytest.mark.parametrize(
    "contract,field",
    (
        (PermissionRequest, "reason"),
        (PermissionRequest, "resource"),
        (AuditRecord, "correlation_id"),
        (AuditRecord, "tool_result_status"),
        (DocumentChunkRecord, "content"),
        (ContextItem, "content"),
        (CitationRenderRecord, "label"),
    ),
)
def test_material_rejected_by_normal_evidence(
    contract: type[BaseModel], field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        contract.model_validate({field: SecretMaterial(PRIVATE)})
    assert any(
        error["loc"] == (field,) and error["type"] != "missing"
        for error in caught.value.errors()
    )
    assert PRIVATE not in str(caught.value)


def guard_side_effects(guard: pytest.MonkeyPatch) -> None:
    for target, names in (
        (builtins, ("open", "print")),
        (
            Path,
            ("open", "read_text", "read_bytes", "write_text", "write_bytes", "mkdir"),
        ),
        (os, ("getenv", "open", "listdir", "scandir")),
        (type(os.environ), ("__getitem__", "get")),
        (socket, ("socket", "create_connection")),
        (sqlite3, ("connect",)),
        (subprocess, ("Popen",)),
        (threading.Thread, ("start",)),
        (asyncio, ("create_task",)),
        (Engine, ("connect",)),
        (sessionmaker, ("__call__",)),
        (EventBus, ("publish", "subscribe")),
        (PuddingMemoryService, ("create", "replace")),
        (DocumentIngestionService, ("ingest",)),
        (EmbeddingProvider, ("embed",)),
        (VectorStore, ("add", "add_many", "search")),
        (RetrievalContextService, ("retrieve",)),
        (ProvenanceCitationService, ("build",)),
        (PermissionEngine, ("evaluate",)),
        (AuditTrail, ("record_permission_decision", "record_tool_outcome")),
        (logging.Logger, ("_log",)),
        (ctypes, ("WinDLL",)),
    ):
        for name in names:
            guard.setattr(target, name, forbidden, raising=False)

    def ordinary_plugin_flag(name: str, default: object = None) -> object:
        # Pydantic consults its plugin switch during schema construction. Stub
        # that one nonsecret switch; all actual environment access stays denied.
        if name != "PYDANTIC_DISABLE_PLUGINS":
            forbidden()
        return default

    guard.setattr(os, "getenv", ordinary_plugin_flag)


@pytest.mark.parametrize("platform", ("win32", "linux", "darwin"))
def test_guarded_uncached_import_construction_and_redaction(
    platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phoenix.security

    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        resolved = name
        caller = args[0] if args else None
        if len(args) > 3 and args[3] and isinstance(caller, dict):
            resolved = importlib.util.resolve_name(
                "." * cast(int, args[3]) + name, caller["__package__"]
            )
        assert resolved in sys.modules
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as guard:
        guard.setattr(phoenix.security, "secrets", module)
        guard.delitem(sys.modules, "phoenix.security.secrets")
        guard.setattr(sys, "dont_write_bytecode", True)
        guard.setattr(sys, "platform", platform)
        guard_side_effects(guard)
        guard.setattr(builtins, "__import__", guarded_import)
        fresh = importlib.import_module("phoenix.security.secrets")
        assert fresh is not module
        ref = fresh.SecretReference(secret_id="Service.A")
        assert ref.secret_id == "Service.A"
        with fresh.SecretMaterial(PRIVATE) as value:
            fresh.WindowsCredentialStore()
            assert fresh.redact_known_secrets(PRIVATE, (value,)) == "[REDACTED]"
        assert value.closed


def test_fake_operations_have_no_integration_or_fallback(
    api: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = WindowsCredentialStore()
    ref = reference()
    value = SecretMaterial(PRIVATE)
    with monkeypatch.context() as guard:
        guard_side_effects(guard)
        store.put(ref, value)
        with store.resolve(ref) as result:
            assert result.reveal_text_for_trusted_use() == PRIVATE
        assert store.delete(ref)
        api.success = False
        api.error = 5
        with pytest.raises(SecretError):
            store.resolve(ref)
        assert redact_known_secrets(PRIVATE, (value,)) == "[REDACTED]"
    assert len(api.calls) == 5


def test_static_isolation_and_event_schema() -> None:
    tree = ast.parse(inspect.getsource(module))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            assert name not in {
                "eval",
                "exec",
                "compile",
                "__import__",
                "print",
                "open",
                "publish",
                "embed",
                "ingest",
                "search",
                "execute",
                "getenv",
                "run",
                "create_task",
                "start",
                "connect",
                "add",
                "add_many",
                "create",
                "replace",
            }
    assert imports <= {
        "__future__",
        "ctypes",
        "sys",
        "collections.abc",
        "enum",
        "typing",
        "pydantic",
    }
    assert not any(name.startswith("phoenix.") for name in imports)
    assert len(EventType) == 32
    assert ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_heads() == ["0006_missions"]
    assert set(module.__all__) == {
        "SecretReference",
        "SecretMaterial",
        "SecretStore",
        "WindowsCredentialStore",
        "SecretError",
        "SecretErrorCode",
        "redact_known_secrets",
    }


@pytest.mark.parametrize("code", tuple(SecretErrorCode))
def test_closed_error_vocabulary(code: SecretErrorCode) -> None:
    safe_error(SecretError(code), code)
    with pytest.raises(ValueError):
        SecretError(cast(SecretErrorCode, PRIVATE))


def test_later_longer_overlap_and_stable_equal_length_priority() -> None:
    assert (
        redact_known_secrets("abcde", (SecretMaterial("ab"), SecretMaterial("bcde")))
        == "a[REDACTED]"
    )
    first = (SecretMaterial("ab"), SecretMaterial("bc"))
    assert redact_known_secrets("abc", first) == "[REDACTED]c"
    assert redact_known_secrets("abc", tuple(reversed(first))) == "a[REDACTED]"
    assert redact_known_secrets("abc", first) == "[REDACTED]c"


def test_redaction_does_not_retain_material() -> None:
    material = SecretMaterial(PRIVATE)
    count = sys.getrefcount(material)
    assert redact_known_secrets(PRIVATE, (material,)) == "[REDACTED]"
    assert sys.getrefcount(material) == count


@pytest.mark.parametrize("failure", ("loader", "write", "decode", "free"))
def test_temporary_buffers_wiped_on_exceptions(
    failure: str, api: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffers: list[bytearray] = []
    original = module._wipe

    def wipe(buffer: bytearray) -> None:
        original(buffer)
        buffers.append(buffer)

    def fail() -> Never:
        raise OSError(PRIVATE)

    monkeypatch.setattr(module, "_wipe", wipe)
    if failure == "loader":
        monkeypatch.setattr(module, "_load_api", fail)
    elif failure in ("write", "free"):
        api.raise_at = failure
    else:
        api.read_blob[0] = 255
    with pytest.raises(SecretError):
        if failure in ("loader", "write"):
            WindowsCredentialStore().put(reference(), SecretMaterial(PRIVATE))
        else:
            WindowsCredentialStore().resolve(reference())
    assert buffers and all(not any(buffer) for buffer in buffers)
    assert failure not in ("decode", "free") or not any(api.read_blob)


@pytest.mark.parametrize("text", ("🙂" * 640, "🙂" * 641))
def test_blob_bound_counts_utf8_bytes(text: str, api: FakeAPI) -> None:
    value = SecretMaterial(text)
    if len(text.encode("utf-8")) <= 2560:
        WindowsCredentialStore().put(reference(), value)
        assert api.written == text.encode("utf-8")
    else:
        with pytest.raises(SecretError):
            WindowsCredentialStore().put(reference(), value)
        assert not api.calls


def test_fresh_independent_resolutions(monkeypatch: pytest.MonkeyPatch) -> None:
    first_api, second_api = FakeAPI(), FakeAPI()
    monkeypatch.setattr(module, "_load_api", lambda: first_api)
    first = WindowsCredentialStore().resolve(reference())
    monkeypatch.setattr(module, "_load_api", lambda: second_api)
    second = WindowsCredentialStore().resolve(reference())
    assert first is not second and first._buffer is not second._buffer
    first.close()
    assert second.reveal_text_for_trusted_use() == PRIVATE
    second.close()


def test_str_subclasses_and_coercions_rejected() -> None:
    class Text(str):
        pass

    class Coercion:
        __str__ = forbidden
        __repr__ = forbidden

    with pytest.raises(ValidationError):
        SecretReference(secret_id=Text("A"))
    for value in (Text(PRIVATE), Coercion()):
        with pytest.raises(SecretError):
            SecretMaterial(cast(str, value))
        with pytest.raises(SecretError):
            redact_known_secrets(cast(str, value), ())


@pytest.mark.parametrize("operation", ("put", "resolve", "delete"))
def test_uninitialized_reference_fails_safely(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "_load_api", forbidden)
    bad = SecretReference.model_construct()
    args = (bad, SecretMaterial(PRIVATE)) if operation == "put" else (bad,)
    with pytest.raises(SecretError) as caught:
        getattr(WindowsCredentialStore(), operation)(*args)
    safe_error(caught.value, SecretErrorCode.INVALID_REFERENCE)
