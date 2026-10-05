"""Pure provenance presentation; citations establish no truth or permission.

PHX-034 evidence is the snapshot boundary. References remain opaque and labels
remain untrusted plain text; downstream UI owns any future interaction policy.
"""

from datetime import UTC, datetime
from math import isfinite
from typing import Annotated, Literal, Self, TypeVar, cast

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_validator,
    model_validator,
)

from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    build_default_agent_registry,
)
from phoenix.memory.schema import MemoryType
from phoenix.rag.retrieval import (
    ContextItem,
    RetrievalContextResult,
    RetrievalDiagnostics,
)

_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Ordinal = Annotated[int, Field(strict=True, ge=1, le=100)]
_Hash = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
]
_Model = TypeVar("_Model", bound=BaseModel)


class CitationError(ValueError):
    """Safe explicit presentation failure without private input details."""


class CitationInputError(CitationError):
    """Invalid exact retrieval, citation or registry evidence."""


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract or not isinstance(value, BaseModel):
        raise ValueError("Expected an exact citation boundary contract")
    return {**vars(value), **(value.__pydantic_extra__ or {})}


def _validated(value: object, contract: type[_Model]) -> _Model:
    return contract.model_validate(_snapshot(value, contract))


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        return handler(_snapshot(value, cls) if isinstance(value, BaseModel) else value)

    @field_validator("as_of", "source_timestamp", mode="before", check_fields=False)
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        if value is not None and not isinstance(value, (datetime, str)):
            raise ValueError("Expected an aware timestamp or ISO representation")
        return value

    @field_validator("as_of", "source_timestamp", check_fields=False)
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("path", "section", "label", check_fields=False)
    @classmethod
    def meaningful(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Expected meaningful provenance text")
        return value


class _Numbered(_Contract):
    citation_id: _Text
    ordinal: _Ordinal
    marker: _Text

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (
            self.citation_id != f"citation-{self.ordinal}"
            or self.marker != f"[{self.ordinal}]"
        ):
            raise ValueError("Citation identity must match its ordinal exactly")
        return self


class CitationEntry(_Numbered):
    retrieval_id: _Id
    vector_record_id: _Id
    source_kind: Literal["memory", "document_chunk"]
    source_id: _Id
    source_hash: _Hash
    authoritative: bool = Field(strict=True)
    source_ref: _Id | None = None
    document_id: _Id | None = None
    path: _Text | None = None
    page: Annotated[int, Field(strict=True, ge=1)] | None = None
    section: _Text | None = None
    source_timestamp: AwareDatetime | None = None
    parser_version: _Id | None = None
    chunk_index: Annotated[int, Field(strict=True, ge=0)] | None = None
    memory_type: MemoryType | None = None
    memory_scope: _Id | None = None
    memory_confidence: Annotated[float, Field(strict=True, ge=0, le=1)] | None = None

    @field_validator("memory_type", mode="before")
    @classmethod
    def textual_type(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError("Expected an existing textual memory type")
        return value

    @field_validator("memory_confidence", mode="before")
    @classmethod
    def ordinary_confidence(cls, value: object) -> object:
        if value is not None:
            if type(value) not in (int, float):
                raise ValueError("Expected ordinary confidence evidence")
            try:
                if not isfinite(cast(int | float, value)):
                    raise ValueError("Expected finite confidence evidence")
            except OverflowError:
                raise ValueError("Expected finite confidence evidence") from None
        return value

    @model_validator(mode="after")
    def provenance(self) -> Self:
        memory = (self.memory_type, self.memory_scope, self.memory_confidence)
        document = (
            self.document_id,
            self.path,
            self.page,
            self.section,
            self.source_timestamp,
            self.parser_version,
            self.chunk_index,
        )
        if self.source_kind == "memory":
            if (
                self.authoritative
                or any(v is not None for v in document)
                or any(v is None for v in memory)
            ):
                raise ValueError("Contradictory memory provenance")
        elif any(v is not None for v in memory) or any(
            v is None
            for v in (
                self.document_id,
                self.source_ref,
                self.parser_version,
                self.chunk_index,
            )
        ):
            raise ValueError("Contradictory document provenance")
        return self


class CitationBundle(_Contract):
    retrieval_id: _Id
    provider_id: _Id
    model_id: _Id
    as_of: AwareDatetime
    citations: Annotated[tuple[CitationEntry, ...], Field(max_length=100)]

    @field_validator("citations", mode="before")
    @classmethod
    def ordered(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Citations require an ordered sequence")
        return tuple(value)

    @model_validator(mode="after")
    def coverage(self) -> Self:
        vectors: set[str] = set()
        for ordinal, entry in enumerate(self.citations, 1):
            if (
                entry.ordinal != ordinal
                or entry.retrieval_id != self.retrieval_id
                or entry.vector_record_id in vectors
            ):
                raise ValueError("Contradictory citation order or correlation")
            vectors.add(entry.vector_record_id)
        return self


class CitationRenderRecord(_Numbered):
    label: _Text

    @field_validator("label")
    @classmethod
    def one_line(cls, value: str) -> str:
        if value != " ".join(value.split()):
            raise ValueError("Label must be canonical single-line plain text")
        return value


class ProvenanceCitationService:
    """Stateless synchronous build; registry metadata creates no runtime authority."""

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        try:
            selected = build_default_agent_registry() if registry is None else registry
            fields = _snapshot(selected, AgentRegistry)
            fields["agents"] = tuple(
                _validated(agent, AgentDefinition) for agent in selected.agents
            )
            AgentRegistry.model_validate(fields).require("knowledge_rag")
        except (ValueError, TypeError, LookupError, AttributeError, RecursionError):
            raise CitationInputError(
                "Registry requires knowledge_rag internal ID"
            ) from None

    @property
    def agent_id(self) -> str:
        return "knowledge_rag"

    def build(self, result: RetrievalContextResult) -> CitationBundle:
        try:
            fields = _snapshot(result, RetrievalContextResult)
            if not isinstance(result.items, (tuple, list)):
                raise ValueError("Expected ordered context evidence")
            fields["items"] = tuple(
                _validated(item, ContextItem) for item in result.items
            )
            fields["diagnostics"] = _validated(result.diagnostics, RetrievalDiagnostics)
            validated = RetrievalContextResult.model_validate(fields)
        except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
            raise CitationInputError("Invalid retrieval context evidence") from None
        citations = tuple(
            CitationEntry.model_validate(
                {
                    name: getattr(item, name)
                    for name in CitationEntry.model_fields
                    if name not in ("citation_id", "ordinal", "marker", "retrieval_id")
                }
                | dict(
                    citation_id=f"citation-{ordinal}",
                    ordinal=ordinal,
                    marker=f"[{ordinal}]",
                    retrieval_id=validated.retrieval_id,
                )
            )
            for ordinal, item in enumerate(validated.items, 1)
        )
        return CitationBundle(
            retrieval_id=validated.retrieval_id,
            provider_id=validated.provider_id,
            model_id=validated.model_id,
            as_of=validated.as_of,
            citations=citations,
        )


class CitationRenderer:
    """Plain-text records; opaque source strings cannot alter structural markers."""

    def render(self, bundle: CitationBundle) -> tuple[CitationRenderRecord, ...]:
        try:
            fields = _snapshot(bundle, CitationBundle)
            if not isinstance(bundle.citations, (tuple, list)):
                raise ValueError("Expected ordered citation evidence")
            fields["citations"] = tuple(
                _validated(entry, CitationEntry) for entry in bundle.citations
            )
            validated = CitationBundle.model_validate(fields)
        except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
            raise CitationInputError("Invalid citation bundle evidence") from None
        rendered: list[CitationRenderRecord] = []
        for entry in validated.citations:
            if entry.source_kind == "memory":
                source = (
                    entry.source_ref
                    if entry.source_ref is not None
                    else entry.source_id
                )
                label = (
                    f"Memory · type={entry.memory_type} · scope={entry.memory_scope}"
                    f" · source={source}"
                )
            else:
                label = (
                    f"Document · source={entry.source_ref}"
                    f" · document={entry.document_id}"
                )
                for name in ("page", "section", "path", "chunk_index"):
                    value = getattr(entry, name)
                    if value is not None:
                        display_value = " ".join(str(value).split())
                        label += (
                            f" · {'chunk' if name == 'chunk_index' else name}="
                            f"{display_value}"
                        )
            rendered.append(
                CitationRenderRecord(
                    citation_id=entry.citation_id,
                    ordinal=entry.ordinal,
                    marker=entry.marker,
                    label=" ".join(label.split()),
                )
            )
        return tuple(rendered)
