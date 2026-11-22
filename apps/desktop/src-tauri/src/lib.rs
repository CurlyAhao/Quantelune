pub mod locale;
mod research_data;
mod code_editor;
mod workspace_directory;
#[cfg(test)]
mod runtime_tests;
/// StatForge Desktop — Tauri 2 application library.
///
/// Initializes the Python sidecar on app startup, exposes its API URL and
/// session token to the webview via a Tauri command, and kills the sidecar
/// when the window is destroyed. Windows additionally owns a non-inherited
/// kill-on-close Job that contains the entire family after a Desktop crash.
pub mod sidecar;
pub mod startup;
pub mod token;
#[cfg(target_os = "windows")]
mod windows_child;

use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

use crate::sidecar::{publish_startup, setup_sidecar, shutdown_sidecar, SidecarState};

/// Tauri command: get the sidecar API URL and session token.
#[tauri::command]
fn get_sidecar_info(
    state: tauri::State<'_, Mutex<SidecarState>>,
) -> Result<serde_json::Value, String> {
    let guard = state.lock().map_err(|e| format!("Lock error: {}", e))?;
    let progress = guard.control.progress();
    Ok(serde_json::json!({
        "apiUrl": if guard.healthy {guard.api_url()} else {String::new()},
        "sessionToken": if guard.healthy {guard.session_token.clone()} else {String::new()},
        "healthy": guard.healthy,
        "error": guard.error,
        "startupState": guard.startup_state,
        "phase": progress.phase,
        "diagnosticFile": progress.diagnostic_file,
        "diagnosticAvailable": progress.diagnostic_available,
        "elapsedMs": guard.control.elapsed_ms(),
        "preferredLocale": crate::locale::read_installer_locale(),
    }))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            // The deadline starts before queuing any filesystem/process work.
            let control = startup::Control::new(startup::STARTUP_LIMIT);
            app.manage(Mutex::new(SidecarState::starting(control.clone())));
            // The runtime's WindowConfig conversion drops data_directory in
            // this pinned Tauri version. Apply it on the builder explicitly.
            for window in app.config().app.windows.iter().filter(|window| !window.create) {
                let directory = match sidecar::application_data_path(app.handle()) {
                    Ok(path) => path,
                    Err(message) => {
                        let handle = app.handle().clone();
                        app.dialog().message(message).title("Quantelune · 项目库")
                            .kind(MessageDialogKind::Error).show(move |_| handle.exit(1));
                        return Ok(());
                    }
                };
                std::fs::create_dir_all(&directory)?;
                tauri::WebviewWindowBuilder::from_config(app.handle(), window)?
                    .data_directory(directory)
                    .build()?;
            }
            let handle = app.handle().clone();
            tauri::async_runtime::spawn_blocking(move || {
                let result = setup_sidecar(&handle, control.clone())
                    .unwrap_or_else(|error| SidecarState::unavailable(error, control));
                publish_startup(&handle.state::<Mutex<SidecarState>>(), result);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let app = window.app_handle();
                if let Some(state) = app.try_state::<Mutex<SidecarState>>() {
                    shutdown_sidecar(&state);
                }
            }
        })
        .invoke_handler(tauri::generate_handler![get_sidecar_info, code_editor::open_plot_editor, workspace_directory::browse_workspace_directory])
        .build(tauri::generate_context!())
        .expect("Quantelune Desktop failed to start")
        .run(|handle, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Some(state) = handle.try_state::<Mutex<SidecarState>>() {
                    shutdown_sidecar(&state);
                }
            }
        });
}
