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
