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
