//! PHX-106 (ADR-091): the only way webview JavaScript reaches the backend.
//!
//! `api_request(method, path, body)` forwards one request to `http://127.0.0.1:<port>`
//! only if the method and path match the exact allowlist below, which is drawn from the
//! REST API v1 contract (`docs/13_API_CONTRACTS.md`). IDs must be canonical lowercase UUIDs
//! and only listed query keys with plain values pass. Rust adds the bearer token; the webview
//! never sees it. Responses carry only the status and the backend's JSON body; failures are
//! closed codes with no free text. No proxy from the environment, no redirects.
//!
//! PHX-122 (ADR-170, ADR-171): voice and knowledge (files and memory) routes, `DELETE` for the
//! two user-owned deletes, percent-encoded text values for exactly the `filename` and `q` query
//! keys, and one raw-body route: `POST /files/upload` takes `{"content_base64": "..."}`, which
//! Rust decodes and forwards as the raw file (at most `MAX_UPLOAD` bytes, no declared media type).

use std::sync::{Arc, OnceLock};
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;

use crate::backend::{Backend, Endpoint, LOOPBACK};

pub const MAX_PATH: usize = 512;
pub const MAX_BODY: usize = 64 * 1024;
pub const MAX_RESPONSE: u64 = 4 * 1024 * 1024;
/// PHX-122: the backend's own cap for `POST /files/upload` (`MAX_UPLOAD_BYTES`, ADR-133).
pub const MAX_UPLOAD: usize = 8 * 1024 * 1024;
pub const UPLOAD_ROUTE: &str = "/files/upload";
/// Query keys whose value is user text, percent-encoded by the webview (`encodeURIComponent`).
const TEXT_KEYS: &[&str] = &["filename", "q"];
const TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Method {
    Get,
    Post,
    Patch,
    Delete,
}

/// Exact (method, route) pairs; `{id}` is one canonical lowercase UUID segment. Destructive
/// routes no adapter uses (grant revoke, conversation deletes) are deliberately absent.
/// PHX-108 (ADR-110, ADR-112) adds the two read-only setup routes, `GET /onboarding` and
/// `GET /capabilities` (no query, no body).
pub const ROUTES: &[(Method, &str, &[&str])] = &[
    (Method::Get, "/health", &[]),
    (Method::Get, "/ready", &[]),
    (Method::Post, "/chat", &[]),
    (
        Method::Get,
        "/missions",
        &["conversation_id", "limit", "offset"],
    ),
    (Method::Get, "/missions/{id}", &[]),
    (Method::Post, "/missions/{id}/cancel", &[]),
    (Method::Post, "/missions/{id}/run-again", &[]),
    (Method::Get, "/permissions", &["limit", "offset"]),
    (Method::Get, "/permissions/{id}", &[]),
    (Method::Post, "/permissions/{id}/approve", &[]),
    (Method::Post, "/permissions/{id}/deny", &[]),
    (
        Method::Get,
        "/grants",
        &["include_inactive", "limit", "offset"],
    ),
    (Method::Get, "/conversations", &["limit", "offset"]),
    (
        Method::Get,
        "/conversations/{id}/messages",
        &["limit", "offset"],
    ),
    (Method::Get, "/agents", &[]),
    (Method::Get, "/models", &[]),
    (Method::Get, "/settings", &[]),
    (Method::Patch, "/settings", &[]),
    (Method::Get, "/onboarding", &[]),
    (Method::Get, "/capabilities", &[]),
    // PHX-115 (ADR-147): read-only runtime metrics, no query.
    (Method::Get, "/metrics", &[]),
    // PHX-122 (ADR-170): voice status and the mic toggle (no body).
    (Method::Get, "/voice/status", &[]),
    (Method::Post, "/voice/start", &[]),
    (Method::Post, "/voice/stop", &[]),
    // PHX-122 (ADR-170, ADR-171): the user's uploads and memory notes.
    (Method::Post, "/files/upload", &["filename"]),
    (Method::Get, "/files", &["limit", "offset"]),
    (Method::Delete, "/files/{id}", &[]),
    (Method::Get, "/memory", &["limit", "offset"]),
    (Method::Post, "/memory", &[]),
    (Method::Get, "/memory/search", &["q", "limit"]),
    (Method::Delete, "/memory/{id}", &[]),
];

/// Closed failure codes returned to the webview.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProxyError {
    RouteNotAllowed,
    InvalidBody,
    BackendUnavailable,
    TransportFailed,
    InvalidResponse,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ApiResponse {
    pub status: u16,
    pub body: Option<Value>,
}

/// A request that passed the allowlist: the method, the exact path (with query) and body bytes.
/// `raw` marks the decoded upload, which is sent without a JSON content type.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Admitted {
    pub method: Method,
    pub path: String,
    pub body: Option<Vec<u8>>,
    pub raw: bool,
}

fn is_uuid(segment: &str) -> bool {
    let bytes = segment.as_bytes();
    bytes.len() == 36
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => *byte == b'-',
            _ => byte.is_ascii_digit() || (b'a'..=b'f').contains(byte),
        })
}

fn route_matches(template: &str, path: &str) -> bool {
    let mut expected = template.split('/');
    let mut actual = path.split('/');
    loop {
        match (expected.next(), actual.next()) {
            (None, None) => return true,
            (Some("{id}"), Some(segment)) if is_uuid(segment) => {}
            (Some(want), Some(got)) if want != "{id}" && want == got => {}
            _ => return false,
        }
    }
}

/// `encodeURIComponent` output: unreserved bytes and complete `%XX` escapes only, so no raw
/// `&`, `=`, `+`, `#`, `/`, space or control byte reaches the backend's query parser.
/// PHX-123 (ADR-187): a `filename` is a display name, so escapes for `/`, `\`, `:` and
/// control bytes are refused here too (the backend refuses them as well).
fn encoded_text_ok(value: &str, filename: bool) -> bool {
    let bytes = value.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'%' {
            let Some(pair) = bytes.get(index + 1..index + 3) else {
                return false;
            };
            let Some(decoded) = std::str::from_utf8(pair)
                .ok()
                .and_then(|hex| u8::from_str_radix(hex, 16).ok())
                .filter(|_| pair.iter().all(u8::is_ascii_hexdigit))
            else {
                return false;
            };
            if filename && (decoded < 0x20 || decoded == 0x7f || b"/\\:".contains(&decoded)) {
                return false;
            }
            index += 3;
        } else if byte.is_ascii_alphanumeric() || b"-._~!*'()".contains(&byte) {
            index += 1;
        } else {
            return false;
        }
    }
    true
}

fn query_ok(query: &str, keys: &[&str]) -> bool {
    let mut seen: Vec<&str> = Vec::new();
    query.split('&').all(|pair| {
        let Some((key, value)) = pair.split_once('=') else {
            return false;
        };
        let fresh = !seen.contains(&key);
        seen.push(key);
        let value_ok = if TEXT_KEYS.contains(&key) {
            encoded_text_ok(value, key == "filename")
        } else {
            value.len() <= 64
                && value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-')
        };
        fresh && keys.contains(&key) && !value.is_empty() && value_ok
    })
}

fn sextet(byte: u8) -> Option<u32> {
    match byte {
        b'A'..=b'Z' => Some(u32::from(byte - b'A')),
        b'a'..=b'z' => Some(u32::from(byte - b'a') + 26),
        b'0'..=b'9' => Some(u32::from(byte - b'0') + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

/// Strict standard base64 (RFC 4648, padded, no whitespace, canonical); `None` otherwise.
pub fn decode_base64(text: &str) -> Option<Vec<u8>> {
    let bytes = text.as_bytes();
    if !bytes.len().is_multiple_of(4) {
        return None;
    }
    let quads = bytes.len() / 4;
    let mut out = Vec::with_capacity(quads * 3);
    for (number, quad) in bytes.as_chunks::<4>().0.iter().enumerate() {
        let pad = quad.iter().rev().take_while(|byte| **byte == b'=').count();
        if pad > 2 || (pad > 0 && number + 1 != quads) {
            return None;
        }
        let mut value = 0u32;
        for byte in &quad[..4 - pad] {
            value = (value << 6) | sextet(*byte)?;
        }
        value <<= 6 * pad as u32;
        let decoded = [(value >> 16) as u8, (value >> 8) as u8, value as u8];
        // Non-zero trailing bits (e.g. "QR==") are refused, so one text has one meaning.
        if decoded[3 - pad..].iter().any(|byte| *byte != 0) {
            return None;
        }
        out.extend_from_slice(&decoded[..3 - pad]);
    }
    Some(out)
}

/// The upload body: exactly `{"content_base64": "<1..=MAX_UPLOAD bytes, base64>"}`.
fn upload_bytes(body: Option<Value>) -> Result<Vec<u8>, ProxyError> {
    let Some(Value::Object(fields)) = body else {
        return Err(ProxyError::InvalidBody);
    };
    let text = match (fields.len(), fields.get("content_base64")) {
        (1, Some(Value::String(text))) => text,
        _ => return Err(ProxyError::InvalidBody),
    };
    if text.len() > MAX_UPLOAD.div_ceil(3) * 4 {
        return Err(ProxyError::InvalidBody);
    }
    match decode_base64(text) {
        Some(bytes) if !bytes.is_empty() && bytes.len() <= MAX_UPLOAD => Ok(bytes),
        _ => Err(ProxyError::InvalidBody),
    }
}

/// The allowlist decision. Anything not exactly listed is refused before any I/O.
pub fn admit(method: &str, path: &str, body: Option<Value>) -> Result<Admitted, ProxyError> {
    let method = match method {
        "GET" => Method::Get,
        "POST" => Method::Post,
        "PATCH" => Method::Patch,
        "DELETE" => Method::Delete,
        _ => return Err(ProxyError::RouteNotAllowed),
    };
    if path.len() > MAX_PATH || !path.starts_with('/') || !path.is_ascii() {
        return Err(ProxyError::RouteNotAllowed);
    }
    let (route, query) = match path.split_once('?') {
        Some((route, query)) => (route, Some(query)),
        None => (path, None),
    };
    let keys = ROUTES
        .iter()
        .find(|(want, template, _)| *want == method && route_matches(template, route))
        .map(|(_, _, keys)| *keys)
        .ok_or(ProxyError::RouteNotAllowed)?;
    if let Some(query) = query {
        if !query_ok(query, keys) {
            return Err(ProxyError::RouteNotAllowed);
        }
    }
    if method == Method::Post && route == UPLOAD_ROUTE {
        return Ok(Admitted {
            method,
            path: path.to_owned(),
            body: Some(upload_bytes(body)?),
            raw: true,
        });
    }
    let body = match (method, body) {
        (Method::Get | Method::Delete, None) => None,
        (Method::Get | Method::Delete, Some(_)) => return Err(ProxyError::InvalidBody),
        (_, None) => None,
        (_, Some(value)) => {
            if !value.is_object() {
                return Err(ProxyError::InvalidBody);
            }
            let bytes = serde_json::to_vec(&value).map_err(|_| ProxyError::InvalidBody)?;
            if bytes.len() > MAX_BODY {
                return Err(ProxyError::InvalidBody);
            }
            Some(bytes)
        }
    };
    Ok(Admitted {
        method,
        path: path.to_owned(),
        body,
        raw: false,
    })
}

fn agent() -> &'static ureq::Agent {
    static AGENT: OnceLock<ureq::Agent> = OnceLock::new();
    AGENT.get_or_init(|| {
        ureq::Agent::config_builder()
            .proxy(None) // never route the token through an environment proxy
            .max_redirects(0)
            .http_status_as_error(false)
            .timeout_global(Some(TIMEOUT))
            .build()
            .into()
    })
}

pub fn url(port: u16, path: &str) -> String {
    format!("http://{LOOPBACK}:{port}{path}")
}

pub fn bearer(endpoint: &Endpoint) -> String {
    format!("Bearer {}", endpoint.token.expose())
}

/// Sends one admitted request with the token and returns the backend's status and JSON body.
pub fn forward(endpoint: &Endpoint, request: &Admitted) -> Result<ApiResponse, ProxyError> {
    let url = url(endpoint.port, &request.path);
    let auth = bearer(endpoint);
    let agent = agent();
    let result = match (request.method, &request.body) {
        (Method::Get, _) => agent.get(&url).header("Authorization", &auth).call(),
        (Method::Delete, _) => agent.delete(&url).header("Authorization", &auth).call(),
        // PHX-122: the raw upload declares no media type; the backend infers it from the name.
        (Method::Post, Some(body)) if request.raw => agent
            .post(&url)
            .header("Authorization", &auth)
            .send(&body[..]),
        (Method::Post, None) => agent.post(&url).header("Authorization", &auth).send_empty(),
        (Method::Patch, None) => agent
            .patch(&url)
            .header("Authorization", &auth)
            .send_empty(),
        (Method::Post, Some(body)) => agent
            .post(&url)
            .header("Authorization", &auth)
            .header("Content-Type", "application/json")
            .send(&body[..]),
        (Method::Patch, Some(body)) => agent
            .patch(&url)
            .header("Authorization", &auth)
            .header("Content-Type", "application/json")
            .send(&body[..]),
    };
    drop(auth);
    let mut response = result.map_err(|_| ProxyError::TransportFailed)?;
    let status = response.status().as_u16();
    let bytes = response
        .body_mut()
        .with_config()
        .limit(MAX_RESPONSE)
        .read_to_vec()
        .map_err(|_| ProxyError::InvalidResponse)?;
    let body = if bytes.is_empty() {
        None
    } else {
        Some(serde_json::from_slice(&bytes).map_err(|_| ProxyError::InvalidResponse)?)
    };
    Ok(ApiResponse { status, body })
}

#[tauri::command]
pub async fn api_request(
    backend: tauri::State<'_, Arc<Backend>>,
    method: String,
    path: String,
    body: Option<Value>,
) -> Result<ApiResponse, ProxyError> {
    let request = admit(&method, &path, body)?;
    let endpoint = backend.endpoint().ok_or(ProxyError::BackendUnavailable)?;
    tauri::async_runtime::spawn_blocking(move || forward(&endpoint, &request))
        .await
        .map_err(|_| ProxyError::TransportFailed)?
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::Token;
    use serde_json::json;

    const ID: &str = "0f8fad5b-d9cb-469f-a165-70867728950e";

    #[test]
    fn every_documented_route_used_by_the_adapters_is_admitted() {
        for (method, path) in [
            ("GET", "/health".to_owned()),
            ("GET", "/ready".to_owned()),
            ("GET", format!("/missions/{ID}")),
            (
                "GET",
                format!("/missions?conversation_id={ID}&limit=50&offset=0"),
            ),
            ("POST", format!("/missions/{ID}/cancel")),
            ("POST", format!("/missions/{ID}/run-again")),
            ("GET", "/permissions?limit=64".to_owned()),
            ("GET", format!("/permissions/{ID}")),
            ("POST", format!("/permissions/{ID}/deny")),
            ("GET", "/grants?include_inactive=true".to_owned()),
            ("GET", "/conversations?limit=1&offset=3".to_owned()),
            (
                "GET",
                format!("/conversations/{ID}/messages?limit=64&offset=0"),
            ),
            ("GET", "/agents".to_owned()),
            ("GET", "/models".to_owned()),
            ("GET", "/settings".to_owned()),
            ("GET", "/onboarding".to_owned()),
            ("GET", "/capabilities".to_owned()),
            ("GET", "/metrics".to_owned()),
            ("GET", "/voice/status".to_owned()),
            ("POST", "/voice/start".to_owned()),
            ("POST", "/voice/stop".to_owned()),
            ("GET", "/files?limit=64&offset=0".to_owned()),
            ("DELETE", format!("/files/{ID}")),
            ("GET", "/memory?limit=64".to_owned()),
            (
                "GET",
                "/memory/search?q=caf%C3%A9%20notes&limit=5".to_owned(),
            ),
            // PHX-123: search text may still hold `/` and `:` (only filenames may not).
            ("GET", "/memory/search?q=a%2Fb%3Ac".to_owned()),
            ("DELETE", format!("/memory/{ID}")),
        ] {
            let admitted =
                admit(method, &path, None).unwrap_or_else(|e| panic!("{method} {path}: {e:?}"));
            assert_eq!(admitted.path, path);
            assert_eq!(admitted.body, None);
        }
        let chat = admit(
            "POST",
            "/chat",
            Some(json!({"text": "hi", "path": "complex"})),
        )
        .unwrap();
        assert_eq!(chat.method, Method::Post);
        assert_eq!(chat.body.unwrap(), br#"{"path":"complex","text":"hi"}"#);
        assert!(admit(
            "POST",
            &format!("/permissions/{ID}/approve"),
            Some(json!({"duration": "session"}))
        )
        .is_ok());
        assert!(admit(
            "PATCH",
            "/settings",
            Some(json!({"workspace_root": "D:\\work"}))
        )
        .is_ok());
        assert!(admit(
            "PATCH",
            "/settings",
            Some(json!({"default_mission_path": "direct"}))
        )
        .is_ok());
        assert!(admit(
            "POST",
            "/memory",
            Some(json!({"content": "I prefer short answers"}))
        )
        .is_ok());
        assert_eq!(ROUTES.len(), 31);
    }

    #[test]
    fn everything_else_is_refused_before_any_io() {
        let refused = [
            ("DELETE", "/conversations".to_owned()),
            ("DELETE", format!("/conversations/{ID}")),
            ("POST", format!("/grants/{ID}/revoke")),
            ("PUT", "/settings".to_owned()),
            ("get", "/health".to_owned()),
            ("GET", "/".to_owned()),
            ("GET", "/events".to_owned()),
            ("GET", "/docs".to_owned()),
            ("GET", "/openapi.json".to_owned()),
            ("GET", "/files/upload".to_owned()),
            ("POST", "/missions".to_owned()),
            ("GET", "/health/".to_owned()),
            ("GET", "//health".to_owned()),
            ("GET", "health".to_owned()),
            ("GET", "http://evil.example/health".to_owned()),
            ("GET", "/missions/../settings".to_owned()),
            ("GET", "/missions/%2e%2e/settings".to_owned()),
            ("GET", format!("/missions/{}", ID.to_uppercase())),
            ("GET", "/missions/not-a-uuid".to_owned()),
            ("GET", format!("/missions/{ID}/extra")),
            ("GET", format!("/missions/{ID}#x")),
            ("GET", "/missions?limit=1&limit=2".to_owned()),
            ("GET", "/missions?token=abc".to_owned()),
            ("GET", "/missions?limit=1%26x".to_owned()),
            ("GET", "/missions?limit=".to_owned()),
            ("GET", "/missions?limit".to_owned()),
            ("GET", "/agents?limit=1".to_owned()),
            // PHX-108: the setup routes are GET only, with no query.
            ("POST", "/onboarding".to_owned()),
            ("PATCH", "/onboarding".to_owned()),
            ("POST", "/capabilities".to_owned()),
            ("PATCH", "/capabilities".to_owned()),
            ("GET", "/onboarding?refresh=true".to_owned()),
            ("GET", "/capabilities?limit=1".to_owned()),
            ("GET", "/onboarding/".to_owned()),
            ("GET", "/capabilities/computer_control".to_owned()),
            ("GET", "/metrics?verbose=1".to_owned()),
            ("GET", "/metrics/".to_owned()),
            ("GET", format!("/metrics/{ID}")),
            ("POST", "/metrics".to_owned()),
            ("PATCH", "/metrics".to_owned()),
            // PHX-122: only the listed methods, exact paths and query keys.
            ("GET", "/voice/start".to_owned()),
            ("POST", "/voice/status".to_owned()),
            ("PATCH", "/voice/start".to_owned()),
            ("DELETE", "/voice/stop".to_owned()),
            ("GET", "/voice/status?verbose=1".to_owned()),
            ("POST", "/voice/start/x".to_owned()),
            ("DELETE", "/files".to_owned()),
            ("DELETE", "/memory".to_owned()),
            ("DELETE", "/files/not-a-uuid".to_owned()),
            ("DELETE", format!("/memory/{}", ID.to_uppercase())),
            ("DELETE", format!("/missions/{ID}")),
            ("DELETE", format!("/permissions/{ID}")),
            ("GET", format!("/files/{ID}")),
            ("GET", format!("/memory/{ID}")),
            ("POST", format!("/memory/{ID}")),
            ("PATCH", "/memory".to_owned()),
            ("POST", "/files".to_owned()),
            ("GET", "/files?filename=a".to_owned()),
            ("GET", "/memory/search?query=a".to_owned()),
            ("GET", "/memory/search?q=a&q=b".to_owned()),
            ("GET", "/memory/search?q=a+b".to_owned()),
            ("GET", "/memory/search?q=a b".to_owned()),
            ("GET", "/memory/search?q=a%2".to_owned()),
            ("GET", "/memory/search?q=a%zz".to_owned()),
            ("GET", "/memory/search?q=a#b".to_owned()),
            ("GET", "/memory/search?q=a/b".to_owned()),
            ("GET", "/memory/search?q=".to_owned()),
            ("GET", "/memory/search?limit=a%20b".to_owned()),
            ("GET", "/memory?q=a".to_owned()),
            ("GET", "/health\r\nX: y".to_owned()),
            ("GET", "/héalth".to_owned()),
            ("GET", format!("/missions?offset={}", "1".repeat(MAX_PATH))),
        ];
        for (method, path) in refused {
            assert_eq!(
                admit(method, &path, None),
                Err(ProxyError::RouteNotAllowed),
                "{method} {path}"
            );
        }
    }

    #[test]
    fn bodies_are_bounded_json_objects_on_write_routes_only() {
        assert_eq!(
            admit("GET", "/settings", Some(json!({}))),
            Err(ProxyError::InvalidBody)
        );
        assert_eq!(
            admit("GET", "/onboarding", Some(json!({}))),
            Err(ProxyError::InvalidBody)
        );
        assert_eq!(
            admit("POST", "/chat", Some(json!("text"))),
            Err(ProxyError::InvalidBody)
        );
        assert_eq!(
            admit("POST", "/chat", Some(json!([1]))),
            Err(ProxyError::InvalidBody)
        );
        let large = json!({"text": "x".repeat(MAX_BODY)});
        assert_eq!(
            admit("POST", "/chat", Some(large)),
            Err(ProxyError::InvalidBody)
        );
        assert_eq!(
            admit("POST", &format!("/permissions/{ID}/deny"), None)
                .unwrap()
                .body,
            None
        );
    }

    #[test]
    fn delete_routes_take_no_body() {
        assert_eq!(
            admit("DELETE", &format!("/files/{ID}"), Some(json!({}))),
            Err(ProxyError::InvalidBody)
        );
        let admitted = admit("DELETE", &format!("/memory/{ID}"), None).unwrap();
        assert_eq!(admitted.method, Method::Delete);
        assert_eq!((admitted.body, admitted.raw), (None, false));
    }

    #[test]
    fn upload_is_one_decoded_raw_body_within_the_backend_cap() {
        let path = "/files/upload?filename=notes%20(final).md";
        let admitted = admit("POST", path, Some(json!({"content_base64": "aGVsbG8="}))).unwrap();
        assert_eq!(admitted.path, path);
        assert_eq!(admitted.body.as_deref(), Some(&b"hello"[..]));
        assert!(admitted.raw);
        let full = "A".repeat(MAX_UPLOAD / 3 * 4) + "AAA=";
        assert_eq!(
            admit("POST", path, Some(json!({ "content_base64": full })))
                .unwrap()
                .body
                .unwrap()
                .len(),
            MAX_UPLOAD / 3 * 3 + 2
        );
        let over = "A".repeat((MAX_UPLOAD + 3) / 3 * 4);
        for body in [
            None,
            Some(json!("aGVsbG8=")),
            Some(json!({})),
            Some(json!({"content_base64": ""})),
            Some(json!({"content_base64": 7})),
            Some(json!({"content_base64": "aGVsbG8"})),
            Some(json!({"content_base64": "aGVs bG8="})),
            Some(json!({"content_base64": "aGVsbG8=", "media_type": "text/plain"})),
            Some(json!({ "content_base64": over })),
        ] {
            assert_eq!(admit("POST", path, body), Err(ProxyError::InvalidBody));
        }
        // Other routes never take the raw form; the upload takes no other method or key.
        assert!(
            !admit("POST", "/memory", Some(json!({"content": "x"})))
                .unwrap()
                .raw
        );
        for (method, path) in [
            ("GET", "/files/upload?filename=a"),
            ("POST", "/files/upload?name=a"),
            ("POST", "/files/upload?filename=a&filename=b"),
            ("POST", "/files/upload?filename=a&b"),
            ("POST", "/files/upload?filename=..%2F..%2Fx&limit=1"),
            // PHX-123 (SR-14): refused for the encoded separators alone, no extra key.
            ("POST", "/files/upload?filename=..%2F..%2Fx.md"),
            ("POST", "/files/upload?filename=..%5Cx.md"),
            ("POST", "/files/upload?filename=C%3Ax.md"),
            ("POST", "/files/upload?filename=x%00.md"),
            ("POST", "/files/upload?filename=x%1F.md"),
            ("POST", "/files/upload?filename=x%7F.md"),
            ("POST", "/files/upload?filename=x%2f.md"),
            ("POST", "/files/upload/x?filename=a"),
        ] {
            assert_eq!(
                admit(method, path, Some(json!({"content_base64": "aGVsbG8="}))),
                Err(ProxyError::RouteNotAllowed),
                "{method} {path}"
            );
        }
    }

    #[test]
    fn base64_is_strict_and_canonical() {
        assert_eq!(decode_base64("").unwrap(), b"");
        assert_eq!(decode_base64("Zg==").unwrap(), b"f");
        assert_eq!(decode_base64("Zm8=").unwrap(), b"fo");
        assert_eq!(decode_base64("Zm9v").unwrap(), b"foo");
        assert_eq!(decode_base64("+/+/").unwrap(), [0xfb, 0xff, 0xbf]);
        for bad in [
            "Zg", "Zg=", "Zg===", "Z===", "Zg==Zg==", "Zm9v\n", "Zm-v", "Zm_v", "Zh==", "Zm9=",
        ] {
            assert_eq!(decode_base64(bad), None, "{bad}");
        }
    }

    #[test]
    fn token_is_added_in_rust_and_never_returned() {
        let token = Token::generate().unwrap();
        let endpoint = Endpoint {
            port: 4242,
            token: token.clone(),
        };
        assert_eq!(url(4242, "/ready"), "http://127.0.0.1:4242/ready");
        assert_eq!(bearer(&endpoint), format!("Bearer {}", token.expose()));
        let response = ApiResponse {
            status: 200,
            body: Some(json!({"status": "ready"})),
        };
        let json = serde_json::to_string(&response).unwrap();
        assert!(!json.contains(token.expose()));
        assert_eq!(
            serde_json::to_string(&ProxyError::RouteNotAllowed).unwrap(),
            r#""route_not_allowed""#
        );
    }

    #[test]
    fn unreachable_backend_is_a_closed_transport_code() {
        // Port 1 on loopback is closed; the error carries no text, URL or token.
        let endpoint = Endpoint {
            port: 1,
            token: Token::generate().unwrap(),
        };
        let request = admit("GET", "/health", None).unwrap();
        assert_eq!(
            forward(&endpoint, &request),
            Err(ProxyError::TransportFailed)
        );
    }
}
