# UI ADRs (C, 2026-11-05)

## ADR-310 HUD replaces the chat shell
The default `App` export is now `Hud.tsx`: orb, per-agent HUD frames, live preview, command bar, TRUST badge, tabs Agents / Sites / Activity / System. The former workspace (setup, settings, voice, memory, files, permissions, diagnostics) is `LegacyApp` under the System tab, re-themed by CSS variables. No new dependency; the orb is CSS/SVG.

## ADR-311 Proxy allowlist grows by nine routes and one segment type
`/sites`, `/sites/{slug}`, `/sites/{slug}/edit|undo|redo`, `/command`, `/trust` (GET/POST). `{slug}` is 1..64 chars of `[a-z0-9-]`, not starting with `-`. `GET /sites/{slug}/preview` is deliberately NOT admitted: the proxy only returns JSON, so an HTML body would fail as `InvalidResponse`.

## ADR-312 Preview is rendered client-side from the spec
`GET /sites/{id}` returns the spec; `renderSite()` builds the page. The srcdoc inherits the shell CSP (no inline script or style), so the iframe is `sandbox="allow-same-origin"` without scripts, the stylesheet is injected through the CSSOM and the parent handles clicks (section highlight, `section_id`). The backend's own HTML preview needs a JSON-wrapped response and a CSP change before it could be used.

## ADR-313 One switch between mock and real
`createSitesApi()` in `adapters/sites.ts`: the native shell gets the real adapter, a plain browser or `?mock` gets the in-memory mock. A selected section routes the next text through `POST /sites/{id}/edit {instruction, section_id}`; everything else goes through `POST /command`.

## ADR-314 Trust badge is off by default and always one click from off
OFF to ON needs an inline confirm; ON to OFF is immediate (kill switch). The typed phrase "trust mode on/off" is handled in the command bar. State is read from `GET /trust` every 4 s.
