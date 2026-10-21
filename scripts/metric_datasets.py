"""PHOENIX metric datasets and deterministic scorers (PHX-117, ADR-115-ADR-118).

Loads and validates the fixed, versioned sets under ``benchmarks/metrics/``
and scores outputs against them. Pure: no models, network or runtime. Each
loader raises ValueError on any invalid entry; each set's file name must match
its major version, and tests pin every file's sha256 with its version.

- ``verification-v1.toml``: fault-injected missions for LEVI's catch rate.
- ``interventions-v1.toml``: workspace missions for the intervention count.
- ``memory-v1.toml``: retrieval QA over a fixture corpus (PHX-110).
- ``research-v1.toml``: evidence-quality rubric over fixture sources (PHX-111).
- ``computer-control-v1.toml`` + pinned fixture pages (PHX-112).
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
METRICS_DIR = ROOT / "benchmarks" / "metrics"
SETS = {
    "verification": METRICS_DIR / "verification-v1.toml",
    "interventions": METRICS_DIR / "interventions-v1.toml",
    "memory": METRICS_DIR / "memory-v1.toml",
    "research": METRICS_DIR / "research-v1.toml",
    "computer_control": METRICS_DIR / "computer-control-v1.toml",
}
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_FILE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.(?:txt|html)")
_URL = re.compile(r"https?://[^\s)\]>\"']+")
MAX_TEXT = 1000
MAX_STEPS = 4  # RuntimeLimits.max_plan_steps default
DOMAINS = ("intelligence", "documents")

FAULTS = (
    "none",
    "wrong_answer",
    "ungrounded_claim",
    "injected_tool_evidence",
    "forged_tool_evidence",
    "forged_verdict",
)
TARGETS = ("none", "step", "final_answer", "tool_result")
DETECTORS = ("none", "deterministic", "model")
FORGERIES = ("call_id", "foreign_request", "foreign_grant")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(text: str) -> str:
    """Case- and whitespace-insensitive form used by every text match."""
    return " ".join(text.split()).casefold()


def _line(value: object, where: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-empty string")
    if "\n" in value or len(value) > limit:
        raise ValueError(f"{where}: text must be one bounded line")
    return value


def _ident(value: object, where: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"{where}: invalid id {value!r}")
    return value


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where}: expected a list of strings")
    return tuple(value)


def _keys(
    raw: object, required: set[str], optional: set[str], where: str
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a table")
    keys = set(raw)
    if not required <= keys or not keys <= required | optional:
        raise ValueError(
            f"{where}: needs {sorted(required)} (optional {sorted(optional)})"
        )
    return raw


def _unique(ids: list[str], what: str) -> None:
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate {what} id")


@dataclass(frozen=True)
class Header:
    suite_id: str
    version: str


def _header(path: Path, data: dict[str, Any], stem: str, suite_id: str) -> Header:
    version = data.get("version")
    match = _VERSION.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise ValueError("version must be MAJOR.MINOR.PATCH")
    if path.name != f"{stem}-v{match.group(1)}.toml":
        raise ValueError("file name must match the major version")
    if data.get("suite_id") != suite_id:
        raise ValueError(f"suite_id must be {suite_id!r}")
    return Header(suite_id=suite_id, version=str(version))


def _load(path: Path, top: set[str]) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) != top:
        raise ValueError(f"set needs exactly {sorted(top)}")
    return data


# ---- verification catch rate


@dataclass(frozen=True)
class VerificationCase:
    case_id: str
    fault: str
    target: str
    detector: str
    request: str
    steps: tuple[str, ...]
    domains: tuple[str, ...]
    step_outputs: tuple[str, ...]
    final: str
    document: str | None
    document_text: str | None
    forgery: str | None
    rationale: str


@dataclass(frozen=True)
class VerificationSet:
    header: Header
    cases: tuple[VerificationCase, ...]


def _plan(raw: dict[str, Any], where: str) -> tuple[tuple[str, ...], ...]:
    steps = _strings(raw["steps"], f"{where}.steps")
    domains = _strings(raw["domains"], f"{where}.domains")
    outputs = _strings(raw["step_outputs"], f"{where}.step_outputs")
    if not 1 <= len(steps) <= MAX_STEPS or not len(steps) == len(domains) == len(
        outputs
    ):
        raise ValueError(f"{where}: 1-{MAX_STEPS} steps, one domain and output each")
    for index, (step, domain, output) in enumerate(
        zip(steps, domains, outputs, strict=True)
    ):
        _line(step, f"{where}.steps[{index}]")
        _line(output, f"{where}.step_outputs[{index}]")
        if domain not in DOMAINS:
            raise ValueError(f"{where}: unknown domain {domain!r}")
    return steps, domains, outputs


def load_verification_set(path: Path = SETS["verification"]) -> VerificationSet:
    data = _load(path, {"suite_id", "version", "case"})
    header = _header(path, data, "verification", "phoenix-verification")
    required = {
        "id",
        "fault",
        "target",
        "detector",
        "request",
        "steps",
        "domains",
        "step_outputs",
        "final",
        "rationale",
    }
    cases: list[VerificationCase] = []
    for index, item in enumerate(data["case"]):
        where = f"case[{index}]"
        raw = _keys(item, required, {"document", "document_text", "forgery"}, where)
        case_id = _ident(raw["id"], where)
        steps, domains, outputs = _plan(raw, case_id)
        fault, target, detector = raw["fault"], raw["target"], raw["detector"]
        if fault not in FAULTS or target not in TARGETS or detector not in DETECTORS:
            raise ValueError(f"{case_id}: unknown fault, target or detector")
        if (fault == "none") != (target == "none") or (fault == "none") != (
            detector == "none"
        ):
            raise ValueError(f"{case_id}: a control has no target and no detector")
        if (fault == "forged_tool_evidence") != (target == "tool_result"):
            raise ValueError(f"{case_id}: forged tool evidence targets the tool result")
        if fault in ("forged_tool_evidence", "forged_verdict") and detector != (
            "deterministic"
        ):
            raise ValueError(f"{case_id}: forgeries are caught deterministically")
        if fault in ("wrong_answer", "ungrounded_claim", "injected_tool_evidence") and (
            detector != "model"
        ):
            raise ValueError(f"{case_id}: semantic faults need the model verifier")
        forgery = raw.get("forgery")
        if (forgery is not None) != (fault == "forged_tool_evidence"):
            raise ValueError(f"{case_id}: forgery belongs to forged tool evidence only")
        if forgery is not None and forgery not in FORGERIES:
            raise ValueError(f"{case_id}: unknown forgery")
        has_documents = "documents" in domains
        document, text = raw.get("document"), raw.get("document_text")
        if has_documents != (document is not None) or (document is None) != (
            text is None
        ):
            raise ValueError(f"{case_id}: document steps need document and text")
        if document is not None:
            if not isinstance(document, str) or _FILE.fullmatch(document) is None:
                raise ValueError(f"{case_id}: invalid document name")
            _line(text, f"{case_id}.document_text")
        if fault == "injected_tool_evidence" and not has_documents:
            raise ValueError(f"{case_id}: injected evidence comes from a document")
        if forgery is not None and not has_documents:
            raise ValueError(f"{case_id}: a forged result needs a document step")
        cases.append(
            VerificationCase(
                case_id=case_id,
                fault=fault,
                target=target,
                detector=detector,
                request=_line(raw["request"], f"{case_id}.request"),
                steps=steps,
                domains=domains,
                step_outputs=outputs,
                final=_line(raw["final"], f"{case_id}.final"),
                document=document,
                document_text=text,
                forgery=forgery,
                rationale=_line(raw["rationale"], f"{case_id}.rationale", 200),
            )
        )
    _unique([case.case_id for case in cases], "case")
    if not {case.fault for case in cases} == set(FAULTS):
        raise ValueError("every fault kind, and controls, must be represented")
    return VerificationSet(header=header, cases=tuple(cases))


def verification_composition(cases: tuple[VerificationCase, ...]) -> dict[str, int]:
    counts = dict.fromkeys(FAULTS, 0)
    for case in cases:
        counts[case.fault] += 1
    return counts


VERIFICATION_OUTCOMES = (
    "caught",
    "escaped",
    "accepted",
    "false_rejection",
    "inconclusive",
)


def verification_outcome(
    case: VerificationCase,
    *,
    mission_kind: str,
    failure_code: str | None,
    verdicts: list[str],
) -> str:
    """One mission's closed outcome for the catch-rate scorer.

    LEVI rejected it: the mission failed ``verification_failed`` with at least
    one FAILED verdict. LEVI accepted it: the mission completed with every
    verdict PASSED. Anything else (a provider or schema failure before LEVI
    decided, a permission wait) is inconclusive and leaves the rates.
    """
    rejected = (
        mission_kind == "failed"
        and failure_code == "verification_failed"
        and "failed" in verdicts
    )
    accepted = (
        mission_kind == "completed"
        and bool(verdicts)
        and all(v == "passed" for v in verdicts)
    )
    if case.fault == "none":
        return (
            "accepted"
            if accepted
            else "false_rejection"
            if rejected
            else "inconclusive"
        )
    return "caught" if rejected else "escaped" if accepted else "inconclusive"


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize_verification(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Catch rate over decided fault cases; controls give the false rejections."""
    counts = dict.fromkeys(VERIFICATION_OUTCOMES, 0)
    by_fault: dict[str, dict[str, int]] = {}
    by_detector: dict[str, dict[str, int]] = {}
    for row in rows:
        counts[row["outcome"]] += 1
        if row["fault"] == "none":
            continue
        for table, key in ((by_fault, row["fault"]), (by_detector, row["detector"])):
            item = table.setdefault(key, {"caught": 0, "escaped": 0, "inconclusive": 0})
            item[row["outcome"]] += 1
    decided = counts["caught"] + counts["escaped"]
    controls = counts["accepted"] + counts["false_rejection"]

    def rated(table: dict[str, dict[str, int]]) -> dict[str, dict[str, Any]]:
        return {
            key: {
                **item,
                "catch_rate": _rate(item["caught"], item["caught"] + item["escaped"]),
            }
            for key, item in sorted(table.items())
        }

    return {
        "cases": len(rows),
        "fault_cases": sum(row["fault"] != "none" for row in rows),
        "control_cases": sum(row["fault"] == "none" for row in rows),
        "outcomes": counts,
        "catch_rate": _rate(counts["caught"], decided),
        "false_rejection_rate": _rate(counts["false_rejection"], controls),
        "by_fault": rated(by_fault),
        "by_detector": rated(by_detector),
    }


# ---- user intervention count


@dataclass(frozen=True)
class InterventionCase:
    case_id: str
    request: str
    steps: tuple[str, ...]
    domains: tuple[str, ...]
    documents: tuple[str, ...]
    step_outputs: tuple[str, ...]
    final: str
    min_interventions: int
    rationale: str


@dataclass(frozen=True)
class InterventionSet:
    header: Header
    max_rounds: int
    files: dict[str, str]
    cases: tuple[InterventionCase, ...]


def load_intervention_set(path: Path = SETS["interventions"]) -> InterventionSet:
    data = _load(path, {"suite_id", "version", "max_rounds", "file", "case"})
    header = _header(path, data, "interventions", "phoenix-interventions")
    max_rounds = data["max_rounds"]
    if type(max_rounds) is not int or not 1 <= max_rounds <= 8:
        raise ValueError("max_rounds must be an integer in 1..8")
    files: dict[str, str] = {}
    for index, item in enumerate(data["file"]):
        raw = _keys(item, {"name", "text"}, set(), f"file[{index}]")
        name = raw["name"]
        if not isinstance(name, str) or _FILE.fullmatch(name) is None or name in files:
            raise ValueError(f"file[{index}]: invalid or duplicate name")
        files[name] = _line(raw["text"], name)
    required = {
        "id",
        "request",
        "steps",
        "domains",
        "documents",
        "step_outputs",
        "final",
        "min_interventions",
        "rationale",
    }
    cases: list[InterventionCase] = []
    for index, item in enumerate(data["case"]):
        raw = _keys(item, required, set(), f"case[{index}]")
        case_id = _ident(raw["id"], f"case[{index}]")
        steps, domains, outputs = _plan(raw, case_id)
        documents = _strings(raw["documents"], f"{case_id}.documents")
        if len(documents) != len(steps):
            raise ValueError(f"{case_id}: one documents entry per step")
        for domain, document in zip(domains, documents, strict=True):
            if (domain == "documents") != bool(document):
                raise ValueError(f"{case_id}: document steps name a file, others none")
            if document and document not in files:
                raise ValueError(f"{case_id}: unknown file {document!r}")
        minimum = raw["min_interventions"]
        if type(minimum) is not int or minimum != len({d for d in documents if d}):
            raise ValueError(f"{case_id}: min_interventions is one per distinct file")
        cases.append(
            InterventionCase(
                case_id=case_id,
                request=_line(raw["request"], f"{case_id}.request"),
                steps=steps,
                domains=domains,
                documents=documents,
                step_outputs=outputs,
                final=_line(raw["final"], f"{case_id}.final"),
                min_interventions=minimum,
                rationale=_line(raw["rationale"], f"{case_id}.rationale", 200),
            )
        )
    _unique([case.case_id for case in cases], "case")
    return InterventionSet(
        header=header, max_rounds=max_rounds, files=files, cases=tuple(cases)
    )


def summarize_interventions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row["completed"]]
    total = sum(row["interventions"] for row in rows)
    return {
        "missions": len(rows),
        "completed": len(completed),
        "completion_rate": _rate(len(completed), len(rows)),
        "interventions": total,
        "permission_prompts": total,
        "clarifications": 0,  # PHOENIX v1 has no clarification turn (ADR-118)
        "interventions_per_mission": total / len(rows) if rows else None,
        "minimum_interventions": sum(row["min_interventions"] for row in rows),
        "excess_interventions": sum(
            max(0, row["interventions"] - row["min_interventions"]) for row in rows
        ),
        "missions_at_minimum": sum(
            row["completed"] and row["interventions"] == row["min_interventions"]
            for row in rows
        ),
    }


# ---- memory retrieval accuracy (PHX-110)


@dataclass(frozen=True)
class MemoryCase:
    case_id: str
    category: str
    question: str
    sources: tuple[str, ...]
    answer: str


@dataclass(frozen=True)
class MemorySet:
    header: Header
    k: int
    documents: dict[str, tuple[str, str]]  # id -> (title, text)
    cases: tuple[MemoryCase, ...]
    calibration: tuple[dict[str, Any], ...]


MEMORY_CATEGORIES = ("direct", "paraphrase", "distractor", "multi_source")


def load_memory_set(path: Path = SETS["memory"]) -> MemorySet:
    data = _load(path, {"suite_id", "version", "k", "document", "case", "calibration"})
    header = _header(path, data, "memory", "phoenix-memory")
    k = data["k"]
    if type(k) is not int or not 1 <= k <= 10:
        raise ValueError("k must be an integer in 1..10")
    documents: dict[str, tuple[str, str]] = {}
    for index, item in enumerate(data["document"]):
        raw = _keys(item, {"id", "title", "text"}, set(), f"document[{index}]")
        doc_id = _ident(raw["id"], f"document[{index}]")
        if doc_id in documents:
            raise ValueError("duplicate document id")
        documents[doc_id] = (
            _line(raw["title"], doc_id, 200),
            _line(raw["text"], doc_id),
        )
    cases: list[MemoryCase] = []
    for index, item in enumerate(data["case"]):
        raw = _keys(
            item,
            {"id", "category", "question", "sources", "answer"},
            set(),
            f"case[{index}]",
        )
        case_id = _ident(raw["id"], f"case[{index}]")
        if raw["category"] not in MEMORY_CATEGORIES:
            raise ValueError(f"{case_id}: unknown category")
        sources = _strings(raw["sources"], f"{case_id}.sources")
        answer = _line(raw["answer"], f"{case_id}.answer", 200)
        if not sources or len(set(sources)) != len(sources):
            raise ValueError(f"{case_id}: needs distinct sources")
        for source in sources:
            if source not in documents:
                raise ValueError(f"{case_id}: unknown source {source!r}")
            if _norm(answer) not in _norm(documents[source][1]):
                raise ValueError(f"{case_id}: source {source!r} lacks the answer")
        if (raw["category"] == "multi_source") != (len(sources) > 1):
            raise ValueError(f"{case_id}: only multi_source cases have several sources")
        cases.append(
            MemoryCase(
                case_id=case_id,
                category=raw["category"],
                question=_line(raw["question"], f"{case_id}.question"),
                sources=sources,
                answer=answer,
            )
        )
    _unique([case.case_id for case in cases], "case")
    by_id = {case.case_id for case in cases}
    calibration = tuple(
        _calibration(
            item,
            index,
            by_id,
            {
                "retrieved",
                "answer",
                "hit_at_1",
                "hit_at_k",
                "reciprocal_rank",
                "answer_correct",
            },
        )
        for index, item in enumerate(data["calibration"])
    )
    return MemorySet(
        header=header,
        k=k,
        documents=documents,
        cases=tuple(cases),
        calibration=calibration,
    )


def _calibration(
    item: object, index: int, case_ids: set[str], fields: set[str]
) -> dict[str, Any]:
    raw = _keys(item, {"case", *fields}, set(), f"calibration[{index}]")
    if raw["case"] not in case_ids:
        raise ValueError(f"calibration[{index}]: unknown case")
    return raw


def score_retrieval(
    case: MemoryCase, retrieved: list[str], answer: str | None, k: int
) -> dict[str, Any]:
    """hit@1, hit@k, reciprocal rank of the first source, answer containment."""
    rank = next(
        (position for position, doc in enumerate(retrieved, 1) if doc in case.sources),
        None,
    )
    return {
        "hit_at_1": rank == 1,
        "hit_at_k": rank is not None and rank <= k,
        "reciprocal_rank": 0.0 if rank is None else 1 / rank,
        "answer_correct": None
        if answer is None
        else bool(answer.strip()) and _norm(case.answer) in _norm(answer),
    }


def summarize_retrieval(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answered = [row for row in rows if row["answer_correct"] is not None]
    n = len(rows)
    return {
        "cases": n,
        "hit_at_1": _rate(sum(row["hit_at_1"] for row in rows), n),
        "hit_at_k": _rate(sum(row["hit_at_k"] for row in rows), n),
        "mrr": sum(row["reciprocal_rank"] for row in rows) / n if n else None,
        "answer_accuracy": _rate(
            sum(row["answer_correct"] for row in answered), len(answered)
        ),
    }


# ---- research evidence quality (PHX-111)


@dataclass(frozen=True)
class ResearchCase:
    case_id: str
    question: str
    sources: tuple[str, ...]
    facts: tuple[tuple[str, str], ...]  # (fact text, supporting source id)


@dataclass(frozen=True)
class ResearchSet:
    header: Header
    sources: dict[str, tuple[str, str]]  # id -> (url, text)
    cases: tuple[ResearchCase, ...]
    calibration: tuple[dict[str, Any], ...]


RESEARCH_CHECKS = (
    "citation_present",
    "cited_source_supports_claim",
    "no_fabricated_url",
)
FIXTURE_URL = "https://fixture.phoenix.test/research/"


def load_research_set(path: Path = SETS["research"]) -> ResearchSet:
    data = _load(path, {"suite_id", "version", "source", "case", "calibration"})
    header = _header(path, data, "research", "phoenix-research")
    sources: dict[str, tuple[str, str]] = {}
    for index, item in enumerate(data["source"]):
        raw = _keys(item, {"id", "url", "text"}, set(), f"source[{index}]")
        source_id = _ident(raw["id"], f"source[{index}]")
        url = raw["url"]
        if source_id in sources or url != FIXTURE_URL + source_id:
            raise ValueError(f"{source_id}: duplicate, or URL is not its fixture URL")
        sources[source_id] = (url, _line(raw["text"], source_id))
    cases: list[ResearchCase] = []
    for index, item in enumerate(data["case"]):
        raw = _keys(
            item, {"id", "question", "sources", "facts"}, set(), f"case[{index}]"
        )
        case_id = _ident(raw["id"], f"case[{index}]")
        allowed = _strings(raw["sources"], f"{case_id}.sources")
        if len(allowed) < 2 or len(set(allowed)) != len(allowed):
            raise ValueError(f"{case_id}: needs at least two distinct sources")
        if not set(allowed) <= set(sources):
            raise ValueError(f"{case_id}: unknown source")
        facts: list[tuple[str, str]] = []
        if not isinstance(raw["facts"], list) or not raw["facts"]:
            raise ValueError(f"{case_id}: needs facts")
        for fact in raw["facts"]:
            entry = _keys(fact, {"text", "source"}, set(), f"{case_id}.facts")
            text = _line(entry["text"], f"{case_id}.fact", 200)
            if entry["source"] not in allowed:
                raise ValueError(f"{case_id}: fact source is not a case source")
            if _norm(text) not in _norm(sources[entry["source"]][1]):
                raise ValueError(f"{case_id}: fact {text!r} is not in its source")
            facts.append((text, entry["source"]))
        cases.append(
            ResearchCase(
                case_id=case_id,
                question=_line(raw["question"], f"{case_id}.question"),
                sources=allowed,
                facts=tuple(facts),
            )
        )
    _unique([case.case_id for case in cases], "case")
    by_id = {case.case_id for case in cases}
    calibration = tuple(
        _calibration(
            item,
            index,
            by_id,
            {"answer", "citations", *RESEARCH_CHECKS, "fact_coverage"},
        )
        for index, item in enumerate(data["calibration"])
    )
    return ResearchSet(
        header=header, sources=sources, cases=tuple(cases), calibration=calibration
    )


def score_research(
    research: ResearchSet,
    case: ResearchCase,
    answer: str,
    citations: list[dict[str, str]],
) -> dict[str, Any]:
    """The deterministic evidence rubric for one research output."""
    by_url = {
        research.sources[source][0]: research.sources[source][1]
        for source in case.sources
    }
    cited = [(c["url"], c.get("quote", "")) for c in citations]
    stated = [text for text, _ in case.facts if _norm(text) in _norm(answer)]
    covered = all(
        any(url in by_url and _norm(fact) in _norm(by_url[url]) for url, _ in cited)
        for fact in stated
    )
    supports = all(
        url in by_url and bool(quote.strip()) and _norm(quote) in _norm(by_url[url])
        for url, quote in cited
    )
    written = [match.rstrip(".,;:") for match in _URL.findall(answer)]
    no_fabricated = all(url in by_url for url, _ in cited) and all(
        url in by_url for url in written
    )
    checks = {
        "citation_present": bool(cited) and covered,
        "cited_source_supports_claim": supports,
        "no_fabricated_url": no_fabricated,
    }
    return {
        **checks,
        "pass": all(checks.values()),
        "fact_coverage": len(stated) / len(case.facts),
    }


def summarize_research(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "cases": n,
        "pass_rate": _rate(sum(row["pass"] for row in rows), n),
        **{
            f"{check}_rate": _rate(sum(row[check] for row in rows), n)
            for check in RESEARCH_CHECKS
        },
        "mean_fact_coverage": sum(row["fact_coverage"] for row in rows) / n
        if n
        else None,
    }


# ---- computer-control completion (PHX-112)


@dataclass(frozen=True)
class ControlCase:
    case_id: str
    category: str
    fixture: str
    instruction: str
    expect: dict[str, str]
    max_steps: int


@dataclass(frozen=True)
class ControlSet:
    header: Header
    fixtures: dict[str, str]  # name -> sha256
    cases: tuple[ControlCase, ...]
    calibration: tuple[dict[str, Any], ...]


CONTROL_CATEGORIES = ("click", "form", "selection", "keyboard", "multi_step")
# Anything that could load or send outside the page.
_REMOTE = re.compile(
    r"https?:|//[a-z0-9]|<\s*(?:link|img|iframe|object|embed|base)\b|\bsrc\s*=|\baction\s*=|"
    r"\bfetch\s*\(|XMLHttpRequest|WebSocket|\bimport\s*\(",
    re.IGNORECASE,
)


def fixture_dir(path: Path) -> Path:
    return path.with_suffix("")


def load_control_set(path: Path = SETS["computer_control"]) -> ControlSet:
    data = _load(path, {"suite_id", "version", "fixture", "case", "calibration"})
    header = _header(path, data, "computer-control", "phoenix-computer-control")
    directory = fixture_dir(path)
    fixtures: dict[str, str] = {}
    pages: dict[str, str] = {}
    for index, item in enumerate(data["fixture"]):
        raw = _keys(item, {"name", "sha256"}, set(), f"fixture[{index}]")
        name = raw["name"]
        if (
            not isinstance(name, str)
            or not name.endswith(".html")
            or _FILE.fullmatch(name) is None
            or name in fixtures
        ):
            raise ValueError(f"fixture[{index}]: invalid or duplicate name")
        page = directory / name
        if not page.is_file() or sha256_file(page) != raw["sha256"]:
            raise ValueError(f"{name}: missing, or sha256 does not match")
        text = page.read_text(encoding="utf-8")
        if _REMOTE.search(text):
            raise ValueError(f"{name}: fixture pages must be local only")
        fixtures[name], pages[name] = raw["sha256"], text
    on_disk = {p.name for p in directory.iterdir()} if directory.is_dir() else set()
    if on_disk != set(fixtures):
        raise ValueError("fixture directory must hold exactly the listed pages")
    cases: list[ControlCase] = []
    for index, item in enumerate(data["case"]):
        raw = _keys(
            item,
            {"id", "category", "fixture", "instruction", "expect", "max_steps"},
            set(),
            f"case[{index}]",
        )
        case_id = _ident(raw["id"], f"case[{index}]")
        if raw["category"] not in CONTROL_CATEGORIES:
            raise ValueError(f"{case_id}: unknown category")
        if raw["fixture"] not in fixtures:
            raise ValueError(f"{case_id}: unknown fixture")
        expect = raw["expect"]
        if not isinstance(expect, dict) or not expect:
            raise ValueError(f"{case_id}: expect needs element ids")
        for element, text in expect.items():
            _line(text, f"{case_id}.expect", 200)
            if f'id="{element}"' not in pages[raw["fixture"]]:
                raise ValueError(f"{case_id}: no element {element!r} in the fixture")
        steps = raw["max_steps"]
        if type(steps) is not int or not 1 <= steps <= 20:
            raise ValueError(f"{case_id}: max_steps must be in 1..20")
        cases.append(
            ControlCase(
                case_id=case_id,
                category=raw["category"],
                fixture=raw["fixture"],
                instruction=_line(raw["instruction"], f"{case_id}.instruction"),
                expect=dict(expect),
                max_steps=steps,
            )
        )
    _unique([case.case_id for case in cases], "case")
    by_id = {case.case_id for case in cases}
    calibration = tuple(
        _calibration(item, index, by_id, {"observation", "completed"})
        for index, item in enumerate(data["calibration"])
    )
    return ControlSet(
        header=header, fixtures=fixtures, cases=tuple(cases), calibration=calibration
    )


def score_control(
    controls: ControlSet, case: ControlCase, observation: dict[str, Any]
) -> dict[str, Any]:
    """COMPLETED: expected texts observed, within max_steps, fixture pages only."""
    state = observation.get("final_state") or {}
    steps = observation.get("steps")
    visited = observation.get("visited") or []
    state_ok = all(
        isinstance(state.get(element), str) and state[element].strip() == text
        for element, text in case.expect.items()
    )
    steps_ok = type(steps) is int and 0 <= steps <= case.max_steps
    scope_ok = all(page in controls.fixtures for page in visited)
    return {
        "completed": state_ok and steps_ok and scope_ok,
        "state_matched": state_ok,
        "within_steps": steps_ok,
        "in_scope": scope_ok,
    }


def summarize_control(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "tasks": n,
        "completion_rate": _rate(sum(row["completed"] for row in rows), n),
        "out_of_scope": sum(not row["in_scope"] for row in rows),
        "over_step_budget": sum(not row["within_steps"] for row in rows),
    }


# ---- calibration: every scorer reproduces its fixed vectors


def calibration_failures(name: str) -> list[str]:
    """Calibration vectors whose recomputed score differs (empty when sound)."""
    failures: list[str] = []
    if name == "memory":
        memory = load_memory_set()
        cases = {case.case_id: case for case in memory.cases}
        for index, item in enumerate(memory.calibration):
            got = score_retrieval(
                cases[item["case"]], item["retrieved"], item["answer"], memory.k
            )
            want = {key: item[key] for key in got}
            if got != want:
                failures.append(f"memory calibration[{index}]: {got} != {want}")
    elif name == "research":
        research = load_research_set()
        cases_r = {case.case_id: case for case in research.cases}
        for index, item in enumerate(research.calibration):
            got = score_research(
                research, cases_r[item["case"]], item["answer"], item["citations"]
            )
            want = {key: item[key] for key in (*RESEARCH_CHECKS, "fact_coverage")}
            if {key: got[key] for key in want} != want:
                failures.append(f"research calibration[{index}]: {got} != {want}")
    elif name == "computer_control":
        controls = load_control_set()
        cases_c = {case.case_id: case for case in controls.cases}
        for index, item in enumerate(controls.calibration):
            got = score_control(controls, cases_c[item["case"]], item["observation"])
            if got["completed"] != item["completed"]:
                failures.append(f"computer_control calibration[{index}]: {got}")
    return failures
