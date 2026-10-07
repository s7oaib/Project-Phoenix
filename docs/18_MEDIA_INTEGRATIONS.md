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

- search/discovery
- built-in player
- play/pause/seek
- queue/list
- external-open fallback

## Rule

BROOK reasons about media requests. Spotify and YouTube are tool integrations, not separate agents.
