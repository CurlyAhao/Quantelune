//! Python FastAPI sidecar lifecycle management.

use std::fs;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use tauri::Manager;
#[cfg(not(target_os = "windows"))]
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
#[cfg(not(target_os = "windows"))]
use tauri_plugin_shell::ShellExt;

use crate::startup::{self, Control, Trace};
use crate::token::generate_session_token;

#[derive(Debug, Clone)]
pub struct DesktopRuntimePaths {
    pub app_data_path: PathBuf,
    pub sqlite_path: PathBuf,
    pub storage_path: PathBuf,
    pub logs_path: PathBuf,
    pub temp_path: PathBuf,
    pub cache_path: PathBuf,
}

impl DesktopRuntimePaths {
    pub fn from_app_data(app_data_path: &Path) -> Self {
        Self {
            app_data_path: app_data_path.to_path_buf(),
            sqlite_path: app_data_path.join("data").join("statforge.db"),
            storage_path: app_data_path.join("storage"),
            logs_path: app_data_path.join("logs"),
            temp_path: app_data_path.join("temp"),
            cache_path: app_data_path.join("cache"),
        }
    }

    fn create(&self) -> Result<(), String> {
        for directory in [
            &self.app_data_path,
            self.sqlite_path
                .parent()
                .ok_or("SQLite path has no parent directory")?,
            &self.storage_path,
            &self.logs_path,
            &self.temp_path,
            &self.cache_path,
        ] {
            fs::create_dir_all(directory).map_err(|error| {
                format!(
                    "Unable to create desktop data directory {}: {error}",
                    directory.display()
                )
            })?;
        }
        Ok(())
    }
}

pub fn application_data_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if let Some(directory) = std::env::var_os("STATFORGE_APP_DATA_DIR") {
        let path = PathBuf::from(directory);
        if !path.is_absolute() {
            return Err("Application data directory must be an absolute path".into());
        }
        return Ok(path);
    }
    if let Some(directory) = app.config().app.windows.iter().find_map(|window| window.data_directory.clone()) {
        return Ok(directory);
    }
    let current = app.path().local_data_dir().map_err(|e| e.to_string())?
        .join("Quantelune Research Data");
    let previous = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    crate::research_data::installed_data_root(current, previous, None)
}

pub struct SidecarState {
    pub api_port: u16,
    pub session_token: String,
    pub healthy: bool,
    pub error: Option<String>,
    pub startup_state: &'static str,
    pub control: Control,
    process: Option<ManagedChild>,
}

enum ManagedChild {
    #[cfg(not(target_os = "windows"))]
    Shell(CommandChild),
    #[cfg(target_os = "windows")]
    Guarded(crate::windows_child::JobChild),
}

impl SidecarState {
    pub fn starting(control: Control) -> Self {
        Self {
            api_port: 0,
            session_token: String::new(),
            healthy: false,
            error: None,
            startup_state: "starting",
            control,
            process: None,
        }
    }

    pub fn unavailable(error: String, control: Control) -> Self {
        control.phase("failed");
        Self {
            api_port: 0,
            session_token: String::new(),
            healthy: false,
            error: Some(error),
            startup_state: "failed",
            control,
            process: None,
        }
    }

    pub fn api_url(&self) -> String {
        if self.api_port == 0 {
            String::new()
        } else {
            format!("http://127.0.0.1:{}/api/v1", self.api_port)
        }
    }
}

/// Ask the OS for an ephemeral loopback port.
pub fn find_free_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("Unable to reserve a loopback port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("Unable to inspect the reserved port: {error}"))
}

fn sidecar_environment(paths: &DesktopRuntimePaths, token: &str) -> Vec<(String, String)> {
    vec![
        ("SIDECAR_SESSION_TOKEN".into(), token.into()),
        ("DESKTOP_MODE".into(), "true".into()),
        ("DEBUG".into(), "false".into()),
        ("DB_BACKEND".into(), "sqlite".into()),
        (
            "SQLITE_PATH".into(),
            paths.sqlite_path.to_string_lossy().into_owned(),
        ),
        ("STORAGE_BACKEND".into(), "local".into()),
        (
            "LOCAL_STORAGE_PATH".into(),
            paths.storage_path.to_string_lossy().into_owned(),
        ),
        (
            "APP_DATA_PATH".into(),
            paths.app_data_path.to_string_lossy().into_owned(),
        ),
        (
            "LOG_PATH".into(),
            paths.logs_path.to_string_lossy().into_owned(),
        ),
        ("AI_ENABLED".into(), "false".into()),
        ("TEMP".into(), paths.temp_path.to_string_lossy().into_owned()),
        ("TMP".into(), paths.temp_path.to_string_lossy().into_owned()),
        ("JOBLIB_TEMP_FOLDER".into(), paths.temp_path.to_string_lossy().into_owned()),
        ("XDG_CACHE_HOME".into(), paths.cache_path.to_string_lossy().into_owned()),
        ("MPLCONFIGDIR".into(), paths.cache_path.join("matplotlib").to_string_lossy().into_owned()),
        ("HF_HOME".into(), paths.cache_path.join("huggingface").to_string_lossy().into_owned()),
        ("TORCH_HOME".into(), paths.cache_path.join("torch").to_string_lossy().into_owned()),
    ]
}

#[cfg(not(target_os = "windows"))]
fn log_sidecar_events(mut receiver: tauri::async_runtime::Receiver<CommandEvent>) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    eprintln!(
                        "[statforge-sidecar] {}",
                        String::from_utf8_lossy(&bytes).trim()
                    );
                }
                CommandEvent::Stderr(bytes) => {
                    eprintln!(
                        "[statforge-sidecar:error] {}",
                        String::from_utf8_lossy(&bytes).trim()
                    );
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!("[statforge-sidecar] exited with code {:?}", payload.code);
                }
                CommandEvent::Error(error) => {
                    eprintln!("[statforge-sidecar:error] {error}");
                }
                _ => {}
            }
        }
    });
}

fn terminate_sidecar(child: ManagedChild) -> Result<(), String> {
    match child {
        #[cfg(target_os = "windows")]
        ManagedChild::Guarded(child) => child
            .terminate()
            .map_err(|error| format!("Unable to stop owned sidecar Job: {error}")),
        #[cfg(not(target_os = "windows"))]
        ManagedChild::Shell(child) => child
            .kill()
            .map_err(|error| format!("Unable to stop sidecar process: {error}")),
    }
}

#[cfg(not(target_os = "windows"))]
fn spawn_sidecar(
    app: &tauri::AppHandle,
    port: u16,
    token: &str,
    paths: &DesktopRuntimePaths,
) -> Result<ManagedChild, String> {
    let args = ["--host", "127.0.0.1", "--port", &port.to_string()];
    let environment = sidecar_environment(paths, token);

    #[cfg(debug_assertions)]
    let command = {
        let api_directory = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../api");
        app.shell()
            .command("uv")
            .args(["run", "python", "-m", "app.sidecar_entry"])
            .args(args)
            .envs(environment)
            .current_dir(api_directory)
    };

    #[cfg(not(debug_assertions))]
    let command = app
        .shell()
        .sidecar("statforge-sidecar")
        .map_err(|error| format!("Packaged sidecar is unavailable: {error}"))?
        .args(args)
        .envs(environment);

    let (receiver, child) = command
        .spawn()
        .map_err(|error| format!("Unable to launch the Quantelune local service: {error}"))?;
    log_sidecar_events(receiver);
    Ok(ManagedChild::Shell(child))
}

#[cfg(target_os = "windows")]
fn spawn_sidecar(
    _app: &tauri::AppHandle,
    port: u16,
    token: &str,
    paths: &DesktopRuntimePaths,
) -> Result<ManagedChild, String> {
    #[cfg(debug_assertions)]
    let (executable, mut arguments, directory) = {
        let executable = std::env::var_os("PATH")
            .and_then(|path| {
                std::env::split_paths(&path)
                    .map(|directory| directory.join("uv.exe"))
                    .find(|candidate| candidate.is_file())
            })
            .ok_or("Development uv.exe is unavailable")?;
        (
            executable,
            vec![
                "run".into(),
                "python".into(),
                "-m".into(),
                "app.sidecar_entry".into(),
            ],
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../api"),
        )
    };
    #[cfg(not(debug_assertions))]
    let local_python = option_env!("STATFORGE_LOCAL_SIDECAR_PYTHON");
    #[cfg(not(debug_assertions))]
    let (executable, mut arguments, directory) = if let Some(python) = local_python {
        (PathBuf::from(python), vec!["-B".into(), "-s".into(), "-m".into(), "app.sidecar_entry".into()], Path::new(env!("CARGO_MANIFEST_DIR")).join("../../api"))
    } else { (
        _app.path()
            .resource_dir()
            .map_err(|error| format!("Unable to resolve sidecar resources: {error}"))?
            .join("statforge-sidecar.exe"),
        Vec::new(),
        paths.app_data_path.clone(),
    ) };
    // Frozen libraries join paths with "..". Win32 verbatim prefixes disable
    // that normalization even when the bundled native module exists.
    let executable = {
        use std::ffi::OsString;
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        use std::path::{Component, Prefix};
        let units: Vec<u16> = executable.as_os_str().encode_wide().collect();
        match executable.components().next() {
            Some(Component::Prefix(prefix)) => match prefix.kind() {
                Prefix::VerbatimDisk(_) => PathBuf::from(OsString::from_wide(&units[4..])),
                Prefix::VerbatimUNC(_, _) => {
                    let mut normal = vec![b'\\' as u16, b'\\' as u16];
                    normal.extend_from_slice(&units[8..]);
                    PathBuf::from(OsString::from_wide(&normal))
                }
                _ => executable,
            },
            _ => executable,
        }
    };
    let mut environment = sidecar_environment(paths, token);
    #[cfg(not(debug_assertions))]
    if local_python.is_some() {
        let resource = _app.path().resource_dir().map_err(|e| e.to_string())?;
        environment.push(("STATFORGE_MEDIA_RUNTIME_CONFIG".into(), resource.join("media-runtime.json").to_string_lossy().into_owned()));
        environment.push(("PYTHONDONTWRITEBYTECODE".into(), "1".into()));
    }
    arguments.extend([
        "--host".into(),
        "127.0.0.1".into(),
        "--port".into(),
        port.to_string().into(),
    ]);
    let child = crate::windows_child::spawn(
        &executable,
        &arguments,
        &environment,
        &directory,
    )
    .map_err(|error| format!("Unable to launch contained sidecar: {error}"))?;
    eprintln!(
        "[statforge-desktop] owned sidecar Job process {}",
        child.pid()
    );
    Ok(ManagedChild::Guarded(child))
}

pub fn setup_sidecar(app: &tauri::AppHandle, control: Control) -> Result<SidecarState, String> {
    control.phase("paths");
    control.check()?;
    let app_data_path = application_data_path(app).map_err(|_| "DATA_DIRECTORY_UNAVAILABLE")?;
    let paths = DesktopRuntimePaths::from_app_data(&app_data_path);
    paths.create().map_err(|_| "DATA_DIRECTORY_UNAVAILABLE")?;
    let mut trace = Trace::create(&paths.logs_path, &control);
    trace.note("paths_ready", None, None, None);
    control.check()?;
    let port = find_free_port().map_err(|_| "LOOPBACK_PORT_UNAVAILABLE")?;
    let token = generate_session_token();
    trace.note("spawn", None, None, None);
    let child = spawn_sidecar(app, port, &token, &paths).map_err(|_| {
        trace.note("failed", None, None, Some("CHILD_SPAWN_FAILED"));
        "CHILD_SPAWN_FAILED"
    })?;
    #[cfg(target_os = "windows")]
    let pid = match &child {
        ManagedChild::Guarded(child) => Some(child.pid()),
    };
    #[cfg(not(target_os = "windows"))]
    let pid = None;
    trace.note("health", pid, None, None);
    let result = tauri::async_runtime::block_on(startup::wait_healthy(
        &control,
        port,
        || {
            #[cfg(target_os = "windows")]
            {
                match &child {
                    ManagedChild::Guarded(child) => {
                        child.exit_code().map_err(|_| "CHILD_STATUS_UNAVAILABLE")
                    }
                }
            }
            #[cfg(not(target_os = "windows"))]
            {
                Ok(None)
            }
        },
        || {
            #[cfg(target_os = "windows")]
            {
                match &child {
                    ManagedChild::Guarded(child) => {
                        child.owns_loopback_listener(port).unwrap_or(false)
                    }
                }
            }
            #[cfg(not(target_os = "windows"))]
            {
                true
            }
        },
    ));
    if let Err(error) = result {
        let exit = match error {
            startup::Failure::ChildExited(code) => Some(code),
            _ => None,
        };
        trace.note("failed", pid, exit, Some(error.code()));
        let _ = terminate_sidecar(child);
        return Err(error.code().into());
    }
    trace.note("health_passed", pid, None, None);
    Ok(SidecarState {
        api_port: port,
        session_token: token,
        healthy: true,
        error: None,
        startup_state: "ready",
        control,
        process: Some(child),
    })
}

pub fn shutdown_sidecar(state: &Mutex<SidecarState>) {
    let child = if let Ok(mut guard) = state.lock() {
        guard.control.cancel();
        guard.control.phase("stopped");
        guard.startup_state = "stopped";
        guard.healthy = false;
        guard.api_port = 0;
        guard.session_token.clear();
        guard.process.take()
    } else {
        None
    };
    if let Some(child) = child {
        let _ = terminate_sidecar(child);
    }
}

pub fn publish_startup(state: &Mutex<SidecarState>, mut result: SidecarState) {
    let discard = match state.lock() {
        Ok(mut guard) => match guard.control.check() {
            Ok(()) => {
                *guard = result;
                return;
            }
            Err("STARTUP_CANCELLED") => true,
            Err(code) => {
                *guard = SidecarState::unavailable(code.into(), guard.control.clone());
                true
            }
        },
        Err(_) => true,
    };
    if discard {
        if let Some(child) = result.process.take() {
            let _ = terminate_sidecar(child);
        }
    }
}

#[cfg(all(test, target_os = "windows"))]
mod close_tests {
    use super::*;
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use std::process::Command;
    use std::time::Duration;
    use windows_sys::Win32::System::Threading::WaitForSingleObject;

    fn arguments() -> Vec<std::ffi::OsString> {
        crate::windows_child::fixture_arguments()
    }
    #[test]
    fn close_or_deadline_before_publication_cleans_only_the_owned_job() {
        // Synthetic state plus real Windows Job/child handles; no actual Desktop,
        // model, database or active ML acceptance is represented by this case.
        let mut unrelated = Command::new(std::env::current_exe().unwrap())
            .args(arguments())
            .env("STATFORGE_NATIVE_FIXTURE_MODE", "sleep")
            .creation_flags(0x08000000)
            .spawn()
            .unwrap();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            for closing in [true, false] {
                let control = Control::new(if closing {
                    startup::STARTUP_LIMIT
                } else {
                    Duration::ZERO
                });
                let state = Mutex::new(SidecarState::starting(control.clone()));
                if closing {
                    shutdown_sidecar(&state);
                }
                let child = crate::windows_child::spawn(
                    &std::env::current_exe().unwrap(),
                    &arguments(),
                    &[("STATFORGE_NATIVE_FIXTURE_MODE".into(), "sleep".into())],
                    &std::env::temp_dir(),
                )
                .unwrap();
                assert_eq!(child.exit_code().unwrap(), None);
                let observed = child.observer().unwrap();
                let ready = SidecarState {
                    api_port: 1,
                    session_token: "SYNTHETIC-STATE-ONLY".into(),
                    healthy: true,
                    error: None,
                    startup_state: "ready",
                    control,
                    process: Some(ManagedChild::Guarded(child)),
                };
                publish_startup(&state, ready);
                assert_eq!(
                    unsafe { WaitForSingleObject(observed.as_raw_handle(), 5000) },
                    0
                );
                let stopped = state.lock().unwrap();
                assert!(!stopped.healthy && stopped.process.is_none());
                assert_eq!(
                    stopped.startup_state,
                    if closing { "stopped" } else { "failed" }
                );
                assert!(unrelated.try_wait().unwrap().is_none());
            }
            let control = Control::new(startup::STARTUP_LIMIT);
            let state = Mutex::new(SidecarState::starting(control.clone()));
            let child = crate::windows_child::spawn(
                &std::env::current_exe().unwrap(),
                &arguments(),
                &[("STATFORGE_NATIVE_FIXTURE_MODE".into(), "sleep".into())],
                &std::env::temp_dir(),
            )
            .unwrap();
            assert_eq!(child.exit_code().unwrap(), None);
            let observed = child.observer().unwrap();
            publish_startup(
                &state,
                SidecarState {
                    api_port: 1,
                    session_token: "SYNTHETIC-STATE-ONLY".into(),
                    healthy: true,
                    error: None,
                    startup_state: "ready",
                    control,
                    process: Some(ManagedChild::Guarded(child)),
                },
            );
            shutdown_sidecar(&state);
            assert_eq!(
                unsafe { WaitForSingleObject(observed.as_raw_handle(), 5000) },
                0
            );
            assert!(unrelated.try_wait().unwrap().is_none());
        }));
        let _ = unrelated.kill();
        let _ = unrelated.wait();
        result.unwrap();
    }
}
