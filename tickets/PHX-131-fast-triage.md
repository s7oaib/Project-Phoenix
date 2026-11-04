# PHX-131 — Faster chat (pre-triage rule)

Status: DONE (local branch, not pushed)

Goal: simple chat answers in ~2 s instead of ~4 s by skipping the triage
generation for clearly simple requests (ADR-225 to ADR-227).

- `rule_decides_direct` in `phoenix/agents/triage.py`; `_triage` in
  `runtime.py` calls it first and records `triage_outcome = rule`.
- Migration `0010_triage_rule`; schemas, persistence, desktop adapter/label.
- Tests: `tests/test_triage_rule.py` (routing-v1: 19/60 decided, 0 complex).
- Routing benchmark `--dry-run` passes offline; model triage unchanged.
