fn main() {
    // PHX-106 (ADR-091): declaring the app commands makes each one an ACL permission
    // (allow-api-request, allow-backend-status) that a capability must grant explicitly.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["api_request", "backend_status"]),
    ))
    .expect("failed to run tauri-build");
}
