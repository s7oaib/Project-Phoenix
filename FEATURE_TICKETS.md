# PHOENIX V1 Feature Ticket List

Statuses: `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`.

## Phase 0 — Foundation
- PHX-001 Repository bootstrap — DONE
- PHX-002 FastAPI backend foundation — DONE
- PHX-003 Tauri + React desktop foundation — DONE
- PHX-004 Configuration system — DONE
- PHX-005 SQLite/SQLAlchemy/Alembic foundation — DONE
- PHX-006 Event bus foundation — DONE
- PHX-007 Hardware scanner/BLAZE-6 — DONE
- PHX-008 Repository line-ending policy — DONE

## Phase 1 — Model Runtime
- PHX-010 ModelProvider interface — DONE
- PHX-011 Ollama provider — DONE
- PHX-012 Model capability registry — DONE
- PHX-013 Model lifecycle manager — DONE
- PHX-014 EmbeddingGemma integration — DONE
- PHX-015 Colibri provider adapter — DONE
- PHX-016 Local model benchmark harness — DONE
- PHX-017 Ollama thinking control — DONE

## Phase 2 — Orchestration
- PHX-020 Agent registry — DONE
- PHX-021 PHOENIX orchestrator — DONE
- PHX-022 AIZEN planner — DONE
- PHX-023 Agent/model router — DONE
- PHX-024 Task graph/state machine — DONE
- PHX-025 Structured agent handoffs — DONE
- PHX-026 LEVI verification — DONE
- PHX-027 MAHORAGA recovery — DONE
- PHX-028 KAIDO fail-safe — DONE
- PHX-029 Model-backed orchestration runtime — DONE

## Phase 3 — Memory & RAG
- PHX-030 Memory schema — DONE
- PHX-031 PUDDING memory service — DONE
- PHX-032 Document ingestion — DONE
- PHX-033 VectorStore/sqlite adapter — DONE
- PHX-034 Retrieval/context assembly — DONE
- PHX-035 Provenance/citations — DONE

## Phase 4 — Tools, Permissions & Security
- PHX-040 ToolRegistry — DONE
- PHX-041 PermissionEngine P0-P5 — DONE
- PHX-042 Audit trail — DONE
- PHX-043 Filesystem tool — DONE
- PHX-044 Terminal tool — DONE
- PHX-045 Secrets boundary — DONE
- PHX-046 Permissioned tool steps in mission runtime — DONE
- PHX-047 Grounded document verification — DONE

## Phase 5 — Computer & Web
- PHX-050 Playwright browser tool — DONE
- PHX-051 Windows UI automation — DONE
- PHX-052 Screenshot/vision loop — DONE
- PHX-053 Search provider abstraction — DONE
- PHX-054 URAHARA research flow — DONE
- PHX-055 Prompt-injection defenses — DONE

Phase 5 status: COMPLETE.

## Phase 6 — Voice
- PHX-060 Wake-word provider — DONE
- PHX-061 VAD — DONE
- PHX-062 STT provider — DONE
- PHX-063 TTS provider — DONE
- PHX-064 Voice conversation/barge-in — DONE
- PHX-065 Rise-Phoenix startup health sequence — DONE

Phase 6 status: COMPLETE.

## Phase 7 — Media & Documents
- PHX-070 Spotify integration — DONE
- PHX-071 YouTube integration — DONE
- PHX-072 Upload/parser routing — DONE
- PHX-073 PDF/DOCX/Markdown generation — DONE

Phase 7 status: COMPLETE.

## Phase 8 — Frontend
- PHX-080 App shell/navigation — DONE
- PHX-081 Chat/voice UI — DONE
- PHX-082 Agent network — DONE
- PHX-083 Task graph — DONE
- PHX-084 Permission center — DONE
- PHX-085 Memory/files workspace — DONE
- PHX-086 Models/voice/settings — DONE
- PHX-087 Observability/developer view — DONE
- PHX-088 Native WebView2 keyboard focus wrap — DONE

Phase 8 status: COMPLETE — native WebView2 acceptance finished on 2026-10-14 (ADR-077): 67 PASS / 0 FAIL / 0 NOT VERIFIED / 1 CONDITIONAL_NOT_REACHABLE (A4, ruled satisfied by the owner). Live backend integration is not implemented; the frontend remains presentation-only by design.

## Phase 9 — Validation & Release
- PHX-090 Automated test matrix — DONE
- PHX-091 FYP benchmark suite — DONE (configuration 1 GPU baselines; configurations 2/3 in a follow-up)
- PHX-092 Packaging/install workflow — TODO
- PHX-093 Final demo scenarios — TODO
- PHX-094 Release evidence package — TODO
