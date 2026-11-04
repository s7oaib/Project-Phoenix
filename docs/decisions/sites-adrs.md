# Website designer ADRs (300-304), for the lead to merge into DECISIONS.md

## ADR-300: Site spec is the source of truth
SiteSpec (phoenix/sites/spec.py) is frozen pydantic data. Every stored version is a valid spec; edits build a new dict and re-validate, so hero-first, footer-last, 3 to 8 sections and stable ids hold by construction. Model JSON is repaired (clamped, ids assigned, unreadable text colour fixed) before validation.

## ADR-301: One self-contained HTML render, pure string templates
render.py emits one file with inline CSS and system fonts, no JS frameworks and no new dependency. Every string passes html.escape. Types set tone (radius, density, CTA), layouts a/b/c set composition per section kind, themes are colours plus one of four fonts. The preview adds a small click-to-select script (postMessage phoenix-select); the exported index.html has no script. Images are embedded as base64 data URIs.

## ADR-302: Versioned storage with a head pointer
<workspace_root>/outputs/sites/<slug>/versions/vNNNN.json, meta.json {head, max}, site/index.html. Undo/redo move head; a new edit after undo drops the redo tail. Slugs match [a-z0-9-]{1,40} and the resolved path must stay directly under the sites root. set_image copies the file into site/assets after a file-scope check (allowed roots, protected names and folders, image types, 5 MB); local rules until phoenix.security.trust scope lands.

## ADR-303: Model first, deterministic rules second, never a visible failure
Spec: qwen3.5:4b through the existing provider abstraction (think=False twin), one retry, then a keyword fallback spec. Edit: model ops, validated all-or-nothing; on model failure or invalid ops, a rule-based planner handles simple English (title, colour, font, add/remove/move section, layout). Rejected edits leave the spec untouched and return a clear message. Needed later: the provider request has no format=json or keep_alive fields (ollama.py is pinned), so both are prompt-only for now.

## ADR-304: Routes, intent router and one job at a time
/sites and /command are mounted like the existing routes (no /v1 prefix; the desktop proxy owns the version). Builds and edits are in-memory jobs (planning, spec, render, ready), one at a time (409 when busy). phoenix.sites.command.handle_command routes create/open/undo/redo/edit by rules and returns passthrough for everything else; a bare edit verb is not enough, the text must also name the site or a look. site_status(site_id) exposes {status, sections} for voice. routes/__init__.py is re-pinned (two-commit pattern).
