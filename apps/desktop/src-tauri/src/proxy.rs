//! PHX-106 (ADR-091): the only way webview JavaScript reaches the backend.
//!
//! `api_request(method, path, body)` forwards one request to `http://127.0.0.1:<port>`
//! only if the method and path match the exact allowlist below, which is drawn from the
//! REST API v1 contract (`docs/13_API_CONTRACTS.md`). IDs must be canonical lowercase UUIDs
//! and only listed query keys with plain values pass. Rust adds the bearer token; the webview
//! never sees it. Responses carry only the status and the backend's JSON body; failures are
//! closed codes with no free text. No proxy from the environment, no redirects.

use std::sync::{Arc, OnceLock};
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;

use crate::backend::{Backend, Endpoint, LOOPBACK};

pub const MAX_PATH: usize = 512;
pub const MAX_BODY: usize = 64 * 1024;
pub const MAX_RESPONSE: u64 = 4 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Method {
    Get,
    Post,
    Patch,
}

/// Exact (method, route) pairs; `{id}` is one canonical lowercase UUID segment. Destructive
/// routes no adapter uses (grant revoke, conversation deletes) are deliberately absent.
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
    // PHX-115 (ADR-147): read-only runtime metrics, no query.
    (Method::Get, "/metrics", &[]),
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
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Admitted {
    pub method: Method,
    pub path: String,
    pub body: Option<Vec<u8>>,
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

fn query_ok(query: &str, keys: &[&str]) -> bool {
    let mut seen: Vec<&str> = Vec::new();
    query.split('&').all(|pair| {
        let Some((key, value)) = pair.split_once('=') else {
            return false;
        };
        let fresh = !seen.contains(&key);
        seen.push(key);
        fresh
            && keys.contains(&key)
            && !value.is_empty()
            && value.len() <= 64
            && value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
    })
}

/// The allowlist decision. Anything not exactly listed is refused before any I/O.
pub fn admit(method: &str, path: &str, body: Option<Value>) -> Result<Admitted, ProxyError> {
    let method = match method {
        "GET" => Method::Get,
        "POST" => Method::Post,
        "PATCH" => Method::Patch,
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
    let body = match (method, body) {
        (Method::Get, None) => None,
        (Method::Get, Some(_)) => return Err(ProxyError::InvalidBody),
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
            ("GET", "/metrics".to_owned()),
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
        assert_eq!(ROUTES.len(), 19);
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
            ("GET", "/metrics?verbose=1".to_owned()),
            ("GET", "/metrics/".to_owned()),
            ("GET", format!("/metrics/{ID}")),
            ("POST", "/metrics".to_owned()),
            ("PATCH", "/metrics".to_owned()),
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
