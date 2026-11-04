# Trust mode ADRs (agent B, range 305-309). Draft; lead merges into DECISIONS.md.

## ADR-305: Trust mode is in-memory state read by PermissionEngine
Status: proposed, 2026-11-05. `phoenix/security/trust.py` holds a process-wide
TrustState (default OFF, `since`, allow/deny lists, per-task P4 approvals,
minted grant ids). PermissionEngine.evaluate reads it on every call, so tools
that build their own engine snapshot are covered and the kill switch is
immediate. While ON, a P1-P3 request inside the file scope is matched by a
PermissionGrant minted per request (reason `trust_mode`; ONCE, or TASK/SESSION
where the request has ids; P3 needs one of them). It is an ordinary
MATCHING_GRANT decision, so audit validation and the audit table's reason-code
constraint are unchanged (no migration). The grant matches regardless of request
wording only while ON and only for ids this process minted. Explicit user denial
and P5 are decided before trust is consulted.
Consequence: callers that check `matched_grant_id in their own grant ids`
(tool_steps, capability_steps, research_steps, control_steps verification) do not
yet know trust grants; they must also accept `TRUST.grant_ids`.

## ADR-306: P4 is approved once per task, never for payments or messages
Status: proposed. The existing approve flow is reused: while ON, the
POST /permissions/{id}/approve route records the task's approval in TrustState
when the approved request is P4. Later P4 requests of the same task evaluate as
EXPLICIT_USER_APPROVAL with that approval id. Requests whose tool or action
tokens include pay/purchase/checkout/buy/send/email/mail/message/sms/transfer are
never auto-approved at any risk level. A request without a task id is not batched.

## ADR-307: Deletes under trust go to the Recycle Bin
Status: proposed. FilesystemTool delete_file uses `trust.recycle` (PowerShell
Microsoft.VisualBasic SendToRecycleBin) while trust is ON; it refuses on other
platforms. With trust OFF, the explicit-approval delete is unchanged.

## ADR-308: Trust file scope
Status: proposed. Allow: Desktop, Documents, Downloads, project folders, the
workspace. Deny always wins and is checked on both the lexical long-name path and
the real path (symlinks, junctions, 8.3 names, `..`, case, trailing dots/spaces);
UNC and alternate data streams are refused; relative means workspace.
AppData is denied under the profile, other users by being under the Users parent.

## ADR-309: Trust routes
Status: proposed. GET/POST /v1/trust in routes/permissions.py. POST false is the
kill switch and clears all trust grants and task approvals. Trust is not persisted.
