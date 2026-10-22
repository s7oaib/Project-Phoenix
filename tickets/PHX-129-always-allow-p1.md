# PHX-129 — Always allow for read-only requests

## Status
DONE (implemented and tested on `feature/PHX-129-always-allow-p1`; local only, not pushed)

## Phase
M2 desktop follow-up (owner-approved 2026-10-23)

## Objective
Fewer permission prompts without weakening safety: let the user remember a read-only
(P1) permission, and see and remove remembered permissions.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md`
- `DECISIONS.md` ADR-215, ADR-216, ADR-217; docs/13, docs/21

## Requirements
1. A pending P1 request offers "Allow this session" and "Always allow"; P2 keeps session
   only; P4 keeps one-time approval. Availability comes from the backend-reported risk.
2. The backend still refuses "always" for P2+ (existing validation, existing tests).
3. Settings → "Remembered permissions" lists active "always" grants (tool, resource,
   granted at) with "Remove", which revokes (sets `revoked_at`, never hard-deletes).
4. The proxy admits `POST /grants/{id}/revoke`; route count 31 → 32.

## Acceptance
- Adapter tests: "always" is sent only for a pending P1 request; P2/denied/unknown
  durations are refused before any call; the P1 panel renders for P1 only; Settings lists
  only active "always" grants and Remove calls the revoke route.
- Proxy tests: revoke admitted; `DELETE /grants/{id}`, `GET .../revoke`, non-UUID and
  query shapes refused; `ROUTES.len() == 32`.
- Backend: `test_document_create_request_is_p2_and_session_only` (P2 "always" → 422) and
  the P1 session/always approve test pass unchanged.

## Limitations
- App.tsx's Permission Center is byte-frozen, so the P1 choice sits in a panel above it;
  the card's "Approve this request" stays the session path.
- `control_grants` still admits PERSISTENT P2 control grants from a client that sends
  them (pre-existing, ADR-151); the desktop never offers it.
