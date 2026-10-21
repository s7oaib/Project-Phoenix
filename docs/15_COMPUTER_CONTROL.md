# 15 — Computer Control

Owner: **KAKASHI**.

## Control preference

1. Direct API/DOM
2. Native/accessibility automation
3. Vision interpretation
4. Mouse/keyboard fallback

## Capabilities

- launch/focus/close apps
- browser automation
- keyboard/mouse
- window management
- screenshots
- clipboard
- terminal invocation through tool boundary
- visible UI inspection

## Safety

- Every action has a risk class.
- Sensitive actions require approval.
- Computer control never bypasses ToolRegistry/PermissionEngine.
- Screenshots may contain sensitive information and are scoped accordingly.
- Vision output is advisory until verified by actual UI/tool state.

## Verification

Prefer observable evidence such as process/window existence, DOM state, file state, or command exit code.

## Mission steps (PHX-112)

`computer_control` (KAKASHI) steps run in PHOENIX missions when the runtime has
a `ControlStepRunner` (`phoenix/agents/control_steps.py`, ADR-150–154):

- The model proposes up to four browser, Windows UI, screenshot or terminal
  actions; PHOENIX builds every call and permission request itself.
- PHOENIX pre-checks known actions before any effect; each tool decides again
  through ToolRegistry and PermissionEngine and audits P3/P4 itself. Approval
  uses the approve → re-run loop (see `docs/08_PERMISSION_MATRIX.md`).
- The browser is PHOENIX's own Playwright Chromium with a fresh isolated
  profile; its binary lives in `D:\Phoenix\browsers`, installed only by
  `scripts/install_control_browser.py --install`.
- Windows are targeted by exact title, resolved by one permissioned listing;
  the model never supplies handles or process IDs.
- Cancel trips the mission's kill switch: no further action starts, the
  in-flight call ends on its tool's timeout and is discarded, and the browser
  is closed after it.
- Page, window, screenshot and command content is untrusted `TOOL_OUTPUT`
  data; LEVI checks the tool evidence before its model check.
- The control prompts are a draft pending owner approval; the backend does not
  enable control steps until then (ADR-152).
