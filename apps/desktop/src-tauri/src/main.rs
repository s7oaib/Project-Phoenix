#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod events;
mod proxy;

use std::sync::Arc;

use tauri::{Emitter, Manager, RunEvent};

/// Tauri events the webview may listen to (capability `core:event:allow-listen`).
const BACKEND_STATUS_EVENT: &str = "phoenix:backend-status";
const EVENT: &str = "phoenix:event";
const EVENTS_STATUS_EVENT: &str = "phoenix:events-status";

fn main() {
    let backend = Arc::new(backend::Backend::new());
    let app = tauri::Builder::default()
        .manage(Arc::clone(&backend))
        .invoke_handler(tauri::generate_handler![
            proxy::api_request,
            backend::backend_status
        ])
        .setup(move |app| {
            #[cfg(windows)]
            focus_wrap::install(app);
            // PHX-106 (ADR-091): the shell starts and owns the backend; only Rust holds the token.
            let handle = app.handle().clone();
            backend.start(Arc::new(move |status| {
                let _ = handle.emit_to("main", BACKEND_STATUS_EVENT, status);
            }));
            let handle = app.handle().clone();
            events::spawn_bridge(
                Arc::clone(&backend),
                Arc::new(move |bridged| {
                    let _ = match bridged {
                        events::Bridged::Event(event) => handle.emit_to("main", EVENT, event),
                        events::Bridged::Status(status) => {
                            handle.emit_to("main", EVENTS_STATUS_EVENT, status)
                        }
                    };
                }),
            );
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Failed to run the PHOENIX desktop application");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<Arc<backend::Backend>>().shutdown();
        }
    });
}

/// Keeps keyboard focus inside the web content when Tab/Shift+Tab leaves its first or last
/// focusable element (PHX-088). WebView2 raises MoveFocusRequested at those edges; left unhandled,
/// focus rests for one keypress on the unnamed, unoutlined WebView host. The window has no other
/// tab stops, so focus wraps straight back into the page instead.
#[cfg(windows)]
mod focus_wrap {
    use tauri::Manager;
    use webview2_com::{
        Microsoft::Web::WebView2::Win32::{
            COREWEBVIEW2_MOVE_FOCUS_REASON, COREWEBVIEW2_MOVE_FOCUS_REASON_NEXT,
            COREWEBVIEW2_MOVE_FOCUS_REASON_PREVIOUS,
        },
        MoveFocusRequestedEventHandler,
    };

    pub fn install(app: &tauri::App) {
        let Some(window) = app.get_webview_window("main") else {
            return;
        };
        // Best effort: if registration fails, WebView2's default tab-out behaviour remains.
        let _ = window.with_webview(|webview| {
            let handler = MoveFocusRequestedEventHandler::create(Box::new(|controller, args| {
                let (Some(controller), Some(args)) = (controller, args) else {
                    return Ok(());
                };
                let mut reason = COREWEBVIEW2_MOVE_FOCUS_REASON::default();
                unsafe { args.Reason(&mut reason)? };
                if wraps(reason) && unsafe { controller.MoveFocus(reason) }.is_ok() {
                    unsafe { args.SetHandled(true)? };
                }
                Ok(())
            }));
            let mut token = 0i64;
            let _ = unsafe {
                webview
                    .controller()
                    .add_MoveFocusRequested(&handler, &mut token)
            };
        });
    }

    /// Only keyboard tab-out wraps; programmatic focus requests keep WebView2's default.
    pub(super) fn wraps(reason: COREWEBVIEW2_MOVE_FOCUS_REASON) -> bool {
        reason == COREWEBVIEW2_MOVE_FOCUS_REASON_NEXT
            || reason == COREWEBVIEW2_MOVE_FOCUS_REASON_PREVIOUS
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::focus_wrap::wraps;
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_MOVE_FOCUS_REASON_NEXT, COREWEBVIEW2_MOVE_FOCUS_REASON_PREVIOUS,
        COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC,
    };

    #[test]
    fn keyboard_tab_out_wraps_in_both_directions() {
        assert!(wraps(COREWEBVIEW2_MOVE_FOCUS_REASON_NEXT));
        assert!(wraps(COREWEBVIEW2_MOVE_FOCUS_REASON_PREVIOUS));
    }

    #[test]
    fn programmatic_focus_keeps_default_behaviour() {
        assert!(!wraps(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC));
    }
}
