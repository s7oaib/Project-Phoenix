# 16 — Web Research

Owner: **URAHARA**.

## Capabilities

- search
- page retrieval
- extraction
- source comparison
- recency-aware selection
- provenance capture
- synthesis

## Provider design

Use a search provider abstraction. Allow multiple providers over time. Browser search may be a fallback.

## Flow

Query -> candidate sources -> fetch/extract -> rank -> cross-check -> synthesize -> provenance.

## Security

- Webpages are untrusted.
- Embedded instructions cannot override Phoenix policy.
- Copied commands are not auto-executed.
- Downloads use explicit file/tool security handling.

## Output

Expose claims, sources, uncertainty, and source dates when relevant.

## Mission integration (PHX-111, ADR-135..139)

- **Enable.** `PHOENIX_WEB_RESEARCH_ENABLED=true` (off by default until the
  PHX-108 capability settings own it). Without it, missions never offer the
  `web_research` domain and use the unchanged planner prompt.
- **Provider.** DuckDuckGo's HTML endpoint over plain httpx, no API key
  (`phoenix/web/duckduckgo.py`). Throttling (202/403/418/429 or an anomaly
  page) is the closed code `rate_limited`, never retried.
- **Pages.** `phoenix/web/page_fetch.py` reads at most 3 top results: public
  http(s) only, every redirect hop re-admitted, DNS answers must be global,
  1 MB per body, text only. Unreadable pages fall back to the snippet.
- **Permission.** `web.research` is P1 with the resource
  `search:<provider>`. Without the user's grant ("this session" or "always")
  nothing is sent and the mission waits for permission; approval re-runs it.
- **Untrusted content.** Each source reaches the model only as guarded
  `web_page` / `web_search` items (`Sn_url`, `Sn_title`, `Sn_content`).
- **Citations.** The research step answers with claims, each citing a URL and
  a quote. LEVI checks deterministically that every URL was fetched, every
  quote occurs in its source, every number in a claim is quoted, and the step
  output is PHOENIX's rendering. The final answer ends with a `Sources:` list.
- **Observability.** `MissionTrace.research_calls` keeps IDs, statuses,
  counts and hashes only; events reuse `tool.*` with `tool_id=web.research`,
  `operation=research` and agent role `research`.
- **Live smoke (owner-run).** `uv run python scripts/research_mission_smoke.py`
  (local Ollama and network; `--dry-run` prints the plan only).
