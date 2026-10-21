"""PHX-117: fixed metric datasets, their validators and deterministic scorers."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "benchmarks" / "metrics"
# PHX-117 (ADR-115): every set is fixed. Any change to a file (or to a
# computer-control fixture page, pinned by the set) needs a new version and
# file and a new pin here.
PINS = {
    "verification-v1.toml": (
        "phoenix-verification",
        "1.0.0",
        "000cf1df072ac987927fd5e192058b1a825c87241b32cf08d432681ac7db09b1",
    ),
    "interventions-v1.toml": (
        "phoenix-interventions",
        "1.0.0",
        "3442bf3622ed4bb39242c09bccdae8072de12e0dddb7528d19398ed739a326aa",
    ),
    "memory-v1.toml": (
        "phoenix-memory",
        "1.0.0",
        "862ebc6849bec243a280cf44faf58ffc2cdcb9ece92af96bc9fd055eea64c700",
    ),
    "research-v1.toml": (
        "phoenix-research",
        "1.0.0",
        "fdeecdb9755a7e9a034a0198f6c5570696b042779107dbedf91e933e2116f3ad",
    ),
    "computer-control-v1.toml": (
        "phoenix-computer-control",
        "1.0.0",
        "68828d30a32555a36b01f924819fb4f08a5d37a08e49038e3319b478b4c329b7",
    ),
}


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


md = _load("metric_datasets", "metric_datasets.py")
LOADERS = {
    "verification-v1.toml": md.load_verification_set,
    "interventions-v1.toml": md.load_intervention_set,
    "memory-v1.toml": md.load_memory_set,
    "research-v1.toml": md.load_research_set,
    "computer-control-v1.toml": md.load_control_set,
}


# ---- pins: fixed, versioned, checksummed


@pytest.mark.parametrize("name", sorted(PINS))
def test_every_set_is_fixed_versioned_and_checksummed(name: str) -> None:
    suite_id, version, sha = PINS[name]
    path = METRICS / name
    loaded = LOADERS[name](path)
    assert (loaded.header.suite_id, loaded.header.version) == (suite_id, version)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
    match = re.fullmatch(r"[a-z-]+-v(\d+)\.toml", name)
    assert match and version.split(".")[0] == match.group(1)


def test_metric_directory_holds_only_the_pinned_sets() -> None:
    files = {p.name for p in METRICS.iterdir() if p.is_file()}
    assert files == set(PINS)
    assert {p.name for p in METRICS.iterdir() if p.is_dir()} == {"computer-control-v1"}
    assert md.SETS == {
        "verification": METRICS / "verification-v1.toml",
        "interventions": METRICS / "interventions-v1.toml",
        "memory": METRICS / "memory-v1.toml",
        "research": METRICS / "research-v1.toml",
        "computer_control": METRICS / "computer-control-v1.toml",
    }


def test_fixture_pages_are_pinned_by_the_set_and_local_only() -> None:
    controls = md.load_control_set()
    directory = METRICS / "computer-control-v1"
    for name, sha in controls.fixtures.items():
        page = directory / name
        assert hashlib.sha256(page.read_bytes()).hexdigest() == sha
        text = page.read_text("utf-8")
        assert "http" not in text and "src=" not in text and "<link" not in text
        assert b"\r\n" not in page.read_bytes()


# ---- composition


def test_verification_composition() -> None:
    verification = md.load_verification_set()
    assert len(verification.cases) == 25
    assert md.verification_composition(verification.cases) == {
        "none": 5,
        "wrong_answer": 7,
        "ungrounded_claim": 5,
        "injected_tool_evidence": 3,
        "forged_tool_evidence": 3,
        "forged_verdict": 2,
    }
    detectors = {case.detector for case in verification.cases if case.fault != "none"}
    assert detectors == {"deterministic", "model"}
    assert sorted(c.forgery for c in verification.cases if c.forgery) == [
        "call_id",
        "foreign_grant",
        "foreign_request",
    ]
    for case in verification.cases:
        if case.document_text is not None:
            assert case.document == "notes.txt"


def test_intervention_composition() -> None:
    interventions = md.load_intervention_set()
    assert len(interventions.cases) == 8 and interventions.max_rounds == 4
    assert set(interventions.files) == {"notes.txt", "team.txt", "budget.txt"}
    minimums = [case.min_interventions for case in interventions.cases]
    assert sorted(minimums) == [0, 0, 1, 1, 1, 1, 2, 2]


def test_memory_research_and_control_composition() -> None:
    memory = md.load_memory_set()
    assert (len(memory.documents), len(memory.cases), memory.k) == (10, 20, 3)
    categories = [case.category for case in memory.cases]
    assert {c: categories.count(c) for c in set(categories)} == {
        "direct": 6,
        "paraphrase": 6,
        "distractor": 6,
        "multi_source": 2,
    }
    research = md.load_research_set()
    assert (len(research.sources), len(research.cases)) == (8, 8)
    for url, _ in research.sources.values():
        assert url.startswith("https://fixture.phoenix.test/")
    controls = md.load_control_set()
    assert (len(controls.fixtures), len(controls.cases)) == (6, 10)
    assert {case.category for case in controls.cases} == set(md.CONTROL_CATEGORIES)


@pytest.mark.parametrize("name", ["memory", "research", "computer_control"])
def test_scorers_reproduce_their_calibration_vectors(name: str) -> None:
    assert md.calibration_failures(name) == []


# ---- validators reject invalid sets

V_HEAD = 'suite_id = "phoenix-verification"\nversion = "1.0.0"\n'


def _vcase(case_id: str, fault: str, **fields: str) -> str:
    target, detector = {
        "none": ("none", "none"),
        "wrong_answer": ("final_answer", "model"),
        "ungrounded_claim": ("step", "model"),
        "injected_tool_evidence": ("step", "model"),
        "forged_tool_evidence": ("tool_result", "deterministic"),
        "forged_verdict": ("step", "deterministic"),
    }[fault]
    values = {
        "id": case_id,
        "fault": fault,
        "target": target,
        "detector": detector,
        "request": "Q?",
        "final": "A",
        "rationale": "R.",
    }
    domains = '["intelligence"]'
    if fault in ("injected_tool_evidence", "forged_tool_evidence"):
        domains = '["documents"]'
        values |= {"document": "notes.txt", "document_text": "Text."}
    if fault == "forged_tool_evidence":
        values["forgery"] = "call_id"
    values |= fields
    lines = [f'{key} = "{value}"' for key, value in values.items()]
    lines += ['steps = ["Do it."]', f"domains = {domains}", 'step_outputs = ["Done."]']
    return "[[case]]\n" + "\n".join(lines) + "\n"


def _vset(*replace: tuple[str, str]) -> str:
    body = V_HEAD + "".join(
        _vcase(f"c{index}", fault) for index, fault in enumerate(md.FAULTS)
    )
    for old, new in replace:
        assert old in body
        body = body.replace(old, new, 1)
    return body


def test_minimal_valid_verification_set_loads(tmp_path: Path) -> None:
    path = tmp_path / "verification-v1.toml"
    path.write_text(_vset(), encoding="utf-8")
    assert len(md.load_verification_set(path).cases) == len(md.FAULTS)


@pytest.mark.parametrize(
    ("name", "replace"),
    [
        ("verification-v2.toml", ()),  # name and version disagree
        ("verification-v1.toml", (('fault = "none"', 'fault = "typo"'),)),
        ("verification-v1.toml", (('id = "c1"', 'id = "c0"'),)),  # duplicate id
        ("verification-v1.toml", (('target = "none"', 'target = "step"'),)),
        (
            "verification-v1.toml",
            (('detector = "model"', 'detector = "deterministic"'),),
        ),
        (
            "verification-v1.toml",
            (('detector = "deterministic"', 'detector = "model"'),),
        ),
        ("verification-v1.toml", (('forgery = "call_id"', 'forgery = "effect"'),)),
        (
            "verification-v1.toml",
            (('document = "notes.txt"', 'document = "../x.txt"'),),
        ),
        ("verification-v1.toml", (('request = "Q?"', 'request = "a\\nb"'),)),
        (
            "verification-v1.toml",
            (('domains = ["intelligence"]', 'domains = ["web"]'),),
        ),
        ("verification-v1.toml", (('step_outputs = ["Done."]', "step_outputs = []"),)),
        ("verification-v1.toml", (('final = "A"', 'final = "A"\nextra = "x"'),)),
        ("verification-v1.toml", (('version = "1.0.0"', 'version = "1.0"'),)),
    ],
)
def test_invalid_verification_sets_are_rejected(
    tmp_path: Path, name: str, replace: tuple[tuple[str, str], ...]
) -> None:
    path = tmp_path / name
    path.write_text(_vset(*replace), encoding="utf-8")
    with pytest.raises(ValueError):
        md.load_verification_set(path)


def test_verification_set_must_cover_every_fault(tmp_path: Path) -> None:
    path = tmp_path / "verification-v1.toml"
    path.write_text(V_HEAD + _vcase("a", "none") + _vcase("b", "wrong_answer"), "utf-8")
    with pytest.raises(ValueError, match="every fault"):
        md.load_verification_set(path)


def _copy(tmp_path: Path, name: str, old: str, new: str) -> Path:
    text = (METRICS / name).read_text("utf-8")
    assert old in text, old
    path = tmp_path / name
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("loader", "name", "old", "new"),
    [
        (
            "intervention",
            "interventions-v1.toml",
            "min_interventions = 2",
            "min_interventions = 1",
        ),
        (
            "intervention",
            "interventions-v1.toml",
            'documents = ["notes.txt"]',
            'documents = ["x.txt"]',
        ),
        ("intervention", "interventions-v1.toml", "max_rounds = 4", "max_rounds = 0"),
        ("memory", "memory-v1.toml", 'answer = "February"', 'answer = "March"'),
        (
            "memory",
            "memory-v1.toml",
            'sources = ["bike-maintenance"]',
            'sources = ["nope"]',
        ),
        (
            "memory",
            "memory-v1.toml",
            'category = "direct"',
            'category = "multi_source"',
        ),
        ("research", "research-v1.toml", '{ text = "1934"', '{ text = "1943"'),
        ("research", "research-v1.toml", 'research/lake-forum"', 'research/elsewhere"'),
        (
            "research",
            "research-v1.toml",
            'sources = ["lake-survey", "lake-forum"]',
            'sources = ["lake-survey"]',
        ),
        (
            "control",
            "computer-control-v1.toml",
            'expect = { status = "Saved" }',
            'expect = { nothing = "Saved" }',
        ),
        ("control", "computer-control-v1.toml", "max_steps = 2", "max_steps = 0"),
        (
            "control",
            "computer-control-v1.toml",
            'category = "click"',
            'category = "drag"',
        ),
    ],
)
def test_invalid_copies_of_the_sets_are_rejected(
    tmp_path: Path, loader: str, name: str, old: str, new: str
) -> None:
    path = _copy(tmp_path, name, old, new)
    if loader == "control":  # its fixture pages sit next to the set
        target = tmp_path / "computer-control-v1"
        target.mkdir()
        for page in (METRICS / "computer-control-v1").iterdir():
            (target / page.name).write_bytes(page.read_bytes())
    with pytest.raises(ValueError):
        getattr(md, f"load_{loader}_set")(path)


def _control_copy(tmp_path: Path) -> Path:
    path = tmp_path / "computer-control-v1.toml"
    path.write_bytes((METRICS / "computer-control-v1.toml").read_bytes())
    target = tmp_path / "computer-control-v1"
    target.mkdir()
    for page in (METRICS / "computer-control-v1").iterdir():
        (target / page.name).write_bytes(page.read_bytes())
    return path


def test_control_set_copy_loads_and_rejects_tampered_or_extra_pages(
    tmp_path: Path,
) -> None:
    path = _control_copy(tmp_path)
    assert len(md.load_control_set(path).cases) == 10
    page = tmp_path / "computer-control-v1" / "counter.html"
    original = page.read_bytes()
    page.write_bytes(original.replace(b"Tally", b"Tally!"))
    with pytest.raises(ValueError, match="sha256"):
        md.load_control_set(path)
    page.write_bytes(original)
    (tmp_path / "computer-control-v1" / "extra.html").write_text("<p>x</p>", "utf-8")
    with pytest.raises(ValueError, match="exactly"):
        md.load_control_set(path)


@pytest.mark.parametrize(
    "snippet",
    [
        '<script src="x.js"></script>',
        '<img src="data:,">',
        '<a href="https://example.com">x</a>',
        '<form action="/send"></form>',
        "<script>fetch('/x')</script>",
        '<link rel="stylesheet" href="s.css">',
    ],
)
def test_fixture_pages_with_remote_or_loading_content_are_rejected(
    tmp_path: Path, snippet: str
) -> None:
    path = _control_copy(tmp_path)
    page = tmp_path / "computer-control-v1" / "counter.html"
    tampered = page.read_text("utf-8").replace("</body>", snippet + "\n</body>")
    page.write_text(tampered, encoding="utf-8", newline="\n")
    sha = hashlib.sha256(page.read_bytes()).hexdigest()
    text = path.read_text("utf-8")
    old = "2761759f32fb186b729dedc74f9f9f88fe4dc7cf316ef4918deaac1311da899a"
    path.write_text(text.replace(old, sha), encoding="utf-8")
    with pytest.raises(ValueError, match="local only"):
        md.load_control_set(path)


# ---- scorers


def test_verification_outcomes_follow_levi_evidence_only() -> None:
    verification = md.load_verification_set()
    fault = next(c for c in verification.cases if c.fault == "wrong_answer")
    control = next(c for c in verification.cases if c.fault == "none")
    outcome = md.verification_outcome
    assert (
        outcome(
            fault,
            mission_kind="failed",
            failure_code="verification_failed",
            verdicts=["passed", "failed"],
        )
        == "caught"
    )
    assert (
        outcome(
            fault,
            mission_kind="completed",
            failure_code=None,
            verdicts=["passed", "passed"],
        )
        == "escaped"
    )
    # A failure before LEVI decided says nothing about LEVI.
    assert (
        outcome(
            fault, mission_kind="failed", failure_code="responder_output", verdicts=[]
        )
        == "inconclusive"
    )
    assert (
        outcome(fault, mission_kind="completed", failure_code=None, verdicts=[])
        == "inconclusive"
    )
    assert (
        outcome(
            control, mission_kind="completed", failure_code=None, verdicts=["passed"]
        )
        == "accepted"
    )
    assert (
        outcome(
            control,
            mission_kind="failed",
            failure_code="verification_failed",
            verdicts=["failed"],
        )
        == "false_rejection"
    )


def test_verification_summary_rates() -> None:
    rows = [
        {"fault": "wrong_answer", "detector": "model", "outcome": "caught"},
        {"fault": "wrong_answer", "detector": "model", "outcome": "escaped"},
        {"fault": "forged_verdict", "detector": "deterministic", "outcome": "caught"},
        {"fault": "ungrounded_claim", "detector": "model", "outcome": "inconclusive"},
        {"fault": "none", "detector": "none", "outcome": "accepted"},
        {"fault": "none", "detector": "none", "outcome": "false_rejection"},
    ]
    s = md.summarize_verification(rows)
    assert s["catch_rate"] == pytest.approx(2 / 3)
    assert s["false_rejection_rate"] == pytest.approx(0.5)
    assert (s["fault_cases"], s["control_cases"]) == (4, 2)
    assert s["by_detector"]["model"]["catch_rate"] == pytest.approx(0.5)
    assert s["by_fault"]["ungrounded_claim"]["catch_rate"] is None
    assert md.summarize_verification([])["catch_rate"] is None


def test_intervention_summary() -> None:
    rows = [
        {"interventions": 0, "min_interventions": 0, "completed": True},
        {"interventions": 2, "min_interventions": 1, "completed": True},
        {"interventions": 1, "min_interventions": 1, "completed": False},
    ]
    s = md.summarize_interventions(rows)
    assert (s["interventions"], s["minimum_interventions"]) == (3, 2)
    assert (s["excess_interventions"], s["missions_at_minimum"]) == (1, 1)
    assert s["interventions_per_mission"] == pytest.approx(1.0)
    assert s["completion_rate"] == pytest.approx(2 / 3)
    assert s["clarifications"] == 0


def test_retrieval_scorer_and_summary() -> None:
    memory = md.load_memory_set()
    case = next(c for c in memory.cases if c.case_id == "may-trip-month")
    score = md.score_retrieval(case, ["garden-herbs", "trip-lisbon"], None, memory.k)
    assert score == {
        "hit_at_1": False,
        "hit_at_k": True,
        "reciprocal_rank": 0.5,
        "answer_correct": None,
    }
    s = md.summarize_retrieval(
        [score, md.score_retrieval(case, ["trip-porto"], "may", memory.k)]
    )
    assert (s["hit_at_1"], s["hit_at_k"], s["mrr"]) == (0.5, 1.0, 0.75)
    assert s["answer_accuracy"] == 1.0  # only answered cases count


def test_research_rubric_flags_each_failure_independently() -> None:
    research = md.load_research_set()
    case = next(c for c in research.cases if c.case_id == "bridge-opening")
    url = research.sources["harbor-bridge-official"][0]
    good = md.score_research(
        research,
        case,
        "Opened in 1934 with a 420 metres span.",
        [{"url": url, "quote": "opened to traffic in 1934"}],
    )
    assert good["pass"] and good["fact_coverage"] == 1.0
    # The 420 metres fact is stated but no citation's source supports it.
    blog = research.sources["harbor-bridge-blog"][0]
    partial = md.score_research(
        research, case, "1934, and 420 metres.", [{"url": blog, "quote": "travel blog"}]
    )
    assert not partial["citation_present"] and partial["no_fabricated_url"]
    s = md.summarize_research([good, partial])
    assert s["pass_rate"] == 0.5 and s["citation_present_rate"] == 0.5


def test_control_scorer_requires_state_budget_and_scope() -> None:
    controls = md.load_control_set()
    case = next(c for c in controls.cases if c.case_id == "multi-step-dark-mode")
    state = {"panel": "Display settings", "status": "Accent Green, dark mode on"}
    ok = md.score_control(
        controls,
        case,
        {"final_state": state, "steps": 5, "visited": ["settings-panel.html"]},
    )
    assert ok == {
        "completed": True,
        "state_matched": True,
        "within_steps": True,
        "in_scope": True,
    }
    bad = md.score_control(controls, case, {"final_state": state, "steps": "5"})
    assert not bad["completed"] and not bad["within_steps"]
    s = md.summarize_control([ok, bad])
    assert s["completion_rate"] == 0.5 and s["over_step_budget"] == 1
