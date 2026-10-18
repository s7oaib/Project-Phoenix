//! PHX-106 (ADR-091): one Rust WebSocket client to `WS /events`, forwarded as Tauri events.
//!
//! The client authenticates with the subprotocols `phoenix.v1, phoenix.auth.<token>` (PHX-101)
//! and requires the server to echo `phoenix.v1`. Each text frame is re-checked as a bounded
//! JSON event envelope, reduced to the eight envelope keys and emitted to the main window as
//! `phoenix:event`. Connection changes are emitted as `phoenix:events-status`; after any
//! close (1013 overflow, 1001/1012 shutdown, errors) the client reconnects with a bounded
//! backoff and the webview refetches over REST, which stays the source of truth.

use std::net::{SocketAddr, TcpStream};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use serde::Serialize;
use serde_json::{Map, Value};
use tungstenite::client::IntoClientRequest;
use tungstenite::http::HeaderValue;
use tungstenite::{Error as WsError, Message};

use crate::backend::{Backend, Endpoint, LOOPBACK};

pub const SUBPROTOCOL: &str = "phoenix.v1";
pub const TOKEN_PREFIX: &str = "phoenix.auth.";
pub const MAX_FRAME: usize = 64 * 1024;
pub const ENVELOPE_KEYS: [&str; 8] = [
    "event_id",
    "event_type",
    "timestamp",
    "task_id",
    "agent_id",
    "payload",
    "severity",
    "correlation_id",
];
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TICK: Duration = Duration::from_secs(1);
const IDLE_TICK: Duration = Duration::from_millis(500);
const MAX_BACKOFF: Duration = Duration::from_secs(5);

/// What the webview learns about the stream: connected or not, and the close code if any.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct StreamStatus {
    pub connected: bool,
    pub code: Option<u16>,
}

pub type Emit = Arc<dyn Fn(Bridged) + Send + Sync>;

pub enum Bridged {
    Event(Value),
    Status(StreamStatus),
}

/// Keeps only a bounded JSON object with a string `event_type` and the envelope keys.
pub fn sanitize(text: &str) -> Option<Value> {
    if text.len() > MAX_FRAME {
        return None;
    }
    let Value::Object(object) = serde_json::from_str::<Value>(text).ok()? else {
        return None;
    };
    let event_type = object.get("event_type")?.as_str()?;
    if event_type.is_empty() || event_type.len() > 64 {
        return None;
    }
    let kept: Map<String, Value> = object
        .into_iter()
        .filter(|(key, _)| ENVELOPE_KEYS.contains(&key.as_str()))
        .collect();
    Some(Value::Object(kept))
}

pub fn protocols_header(endpoint: &Endpoint) -> Option<HeaderValue> {
    let mut value = HeaderValue::from_str(&format!(
        "{SUBPROTOCOL}, {TOKEN_PREFIX}{}",
        endpoint.token.expose()
    ))
    .ok()?;
    value.set_sensitive(true);
    Some(value)
}

/// Starts the bridge thread; it lives until the backend is shutting down.
pub fn spawn_bridge(backend: Arc<Backend>, emit: Emit) {
    thread::Builder::new()
        .name("phoenix-events".into())
        .spawn(move || {
            let mut backoff = Duration::from_millis(250);
            while !backend.is_stopping() {
                let Some(endpoint) = backend.endpoint() else {
                    thread::sleep(IDLE_TICK);
                    continue;
                };
                match stream(&backend, &endpoint, &emit) {
                    Ok(code) => {
                        backoff = Duration::from_millis(250);
                        emit(Bridged::Status(StreamStatus {
                            connected: false,
                            code,
                        }));
                    }
                    Err(()) => backoff = (backoff * 2).min(MAX_BACKOFF),
                }
                thread::sleep(backoff);
            }
        })
        .expect("failed to start the event bridge thread");
}

/// One connection. `Ok(code)` after it was established and then closed; `Err` if it never was.
fn stream(backend: &Backend, endpoint: &Endpoint, emit: &Emit) -> Result<Option<u16>, ()> {
    let address = SocketAddr::from(([127, 0, 0, 1], endpoint.port));
    let socket = TcpStream::connect_timeout(&address, CONNECT_TIMEOUT).map_err(|_| ())?;
    socket
        .set_read_timeout(Some(HANDSHAKE_TIMEOUT))
        .map_err(|_| ())?;
    socket
        .set_write_timeout(Some(HANDSHAKE_TIMEOUT))
        .map_err(|_| ())?;
    let mut request = format!("ws://{LOOPBACK}:{}/events", endpoint.port)
        .into_client_request()
        .map_err(|_| ())?;
    request.headers_mut().insert(
        "Sec-WebSocket-Protocol",
        protocols_header(endpoint).ok_or(())?,
    );
    let (mut socket, response) = tungstenite::client(request, socket).map_err(|_| ())?;
    let echoed = response
        .headers()
        .get("Sec-WebSocket-Protocol")
        .and_then(|value| value.to_str().ok());
    if echoed != Some(SUBPROTOCOL) {
        let _ = socket.close(None);
        return Err(());
    }
    socket
        .get_mut()
        .set_read_timeout(Some(READ_TICK))
        .map_err(|_| ())?;
    emit(Bridged::Status(StreamStatus {
        connected: true,
        code: None,
    }));
    loop {
        if backend.is_stopping() || backend.endpoint().map(|e| e.port) != Some(endpoint.port) {
            let _ = socket.close(None);
            return Ok(None);
        }
        // read() also flushes queued pong and close replies (uvicorn pings idle clients).
        match socket.read() {
            Ok(Message::Text(text)) => {
                if let Some(event) = sanitize(text.as_str()) {
                    emit(Bridged::Event(event));
                }
            }
            Ok(Message::Close(frame)) => {
                let code = frame.map(|frame| u16::from(frame.code));
                let _ = socket.flush();
                return Ok(code);
            }
            Ok(_) => {}
            Err(WsError::Io(error))
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) => {}
            Err(_) => return Ok(None),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::Token;

    #[test]
    fn envelopes_are_bounded_objects_reduced_to_their_keys() {
        let event = sanitize(
            r#"{"event_id":"e","event_type":"step.started","timestamp":"t","task_id":null,
                "agent_id":"verifier","payload":{"step_id":"s1"},"severity":"info",
                "correlation_id":"c","extra":"dropped"}"#,
        )
        .unwrap();
        let object = event.as_object().unwrap();
        assert_eq!(object.len(), 8);
        assert!(!object.contains_key("extra"));
        for bad in [
            "",
            "[]",
            "\"step.started\"",
            r#"{"event_type":""}"#,
            r#"{"event_type":7}"#,
            r#"{"payload":{}}"#,
        ] {
            assert_eq!(sanitize(bad), None, "{bad}");
        }
        let large = format!(
            r#"{{"event_type":"x","payload":"{}"}}"#,
            "y".repeat(MAX_FRAME)
        );
        assert_eq!(sanitize(&large), None);
    }

    #[test]
    fn subprotocol_header_carries_the_token_but_is_sensitive() {
        let token = Token::generate().unwrap();
        let endpoint = Endpoint {
            port: 1,
            token: token.clone(),
        };
        let header = protocols_header(&endpoint).unwrap();
        assert!(header.is_sensitive());
        assert_eq!(
            header.to_str().unwrap(),
            format!("phoenix.v1, phoenix.auth.{}", token.expose())
        );
        assert!(!format!("{header:?}").contains(token.expose()));
        let status = serde_json::to_string(&StreamStatus {
            connected: false,
            code: Some(1013),
        })
        .unwrap();
        assert_eq!(status, r#"{"connected":false,"code":1013}"#);
    }
}
