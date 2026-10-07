# 18 — Media Integrations

Owner: **BROOK**.

## Spotify

PHX-070 V1 provides permission-gated Web API tools: P1 `spotify.search`,
`spotify.playback_state`, `spotify.devices`; P2 `spotify.play`, `spotify.pause`,
`spotify.next`, `spotify.previous`, `spotify.transfer`, `spotify.volume`.
Search covers tracks/artists/albums/playlists (limit 1–10 per type). Play resumes,
selects one track, or selects an album/artist/playlist context. Transfer selects
one explicit Connect device; supported volume is an integer 0–100.

Caller-owned manual Authorization Code with PKCE uses only
`user-read-playback-state` and `user-modify-playback-state`. Existing SecretStore
holds tokens; no frontend/API callback or automatic login is supplied. HTTP targets
are fixed Spotify endpoints. Only 401 permits one refresh and one replay.

Playback occurs on a Spotify-controlled client/device, with Premium required for
playback controls. API acceptance does not prove physical playback. No Spotify
audio streaming, playlist mutation, catalog cache, background monitor or long-term
memory. Third-party names are inert untrusted data. See
[the PHX-070 contract](../tickets/PHX-070-spotify-integration.md) and ADR-063.

## YouTube

PHX-071 V1 provides P1 public search/player-state/queue-state and P2
cue/play/pause/seek/enqueue/remove/clear tools. Public video/channel/playlist search
uses one fixed YouTube Data API request with a SecretStore API-key header, bounded
inert metadata and no pagination/retry. Player commands use a borrowed provider-neutral
adapter; the concrete embedded iframe/Tauri/frontend player is deferred to Phase 8.
Command acceptance does not prove audible playback, rendering or user viewing.

The session-only queue holds at most 50 canonical video references, permits duplicates
and never auto-plays. Explicit revisions protect removal from stale queue indices.
External-open exposes only a canonical watch URL; PHOENIX must separately permission
the existing browser.navigate tool. No OAuth/private-account data, transcripts,
downloads, stream extraction, media history or memory/RAG ingestion. See
[the PHX-071 contract](../tickets/PHX-071-youtube-integration.md) and ADR-064.

## Rule

BROOK reasons about media requests. Spotify and YouTube are tool integrations, not separate agents.
