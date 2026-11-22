//! Open a prepared script through an editor from the desktop process, outside the API Job.
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

fn hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|c| c.is_ascii_hexdigit())
}
fn uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, c)| {
            if [8, 13, 18, 23].contains(&i) {
                c == b'-'
            } else {
                c.is_ascii_hexdigit()
            }
        })
}
fn editor_path(id: &str) -> Option<PathBuf> {
    let (binary, label, command) = match id {
        "vscode" => ("Code.exe", "Microsoft VS Code", "code"),
        "cursor" => ("Cursor.exe", "cursor", "cursor"),
        "vscode-insiders" => (
            "Code - Insiders.exe",
            "Microsoft VS Code Insiders",
            "code-insiders",
        ),
        "notepadpp" => ("notepad++.exe", "Notepad++", "notepad++"),
        _ => return None,
    };
    let mut paths = Vec::new();
    if let Some(raw) = std::env::var_os("PATH") {
        for parent in std::env::split_paths(&raw) {
            paths.push(parent.join(binary));
            if parent.join(format!("{command}.cmd")).is_file() {
                if let Some(root) = parent.parent() {
                    paths.push(root.join(binary));
                }
            }
        }
    }
    for name in ["LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"] {
        if let Some(root) = std::env::var_os(name) {
            paths.push(Path::new(&root).join("Programs").join(label).join(binary));
            paths.push(Path::new(&root).join(label).join(binary));
        }
    }
    paths
        .into_iter()
        .find(|path| path.is_file() && !path.is_symlink())
}

#[cfg(target_os = "windows")]
fn launch(path: &Path, editor: &str) -> Result<serde_json::Value, String> {
    use std::os::windows::ffi::OsStrExt;
    use std::process::{Command, Stdio};
    if editor == "system" {
        #[link(name = "shell32")]
        extern "system" {
            fn ShellExecuteW(
                window: *mut std::ffi::c_void,
                operation: *const u16,
                file: *const u16,
                parameters: *const u16,
                directory: *const u16,
                show: i32,
            ) -> isize;
        }
        let verb: Vec<u16> = "edit".encode_utf16().chain(Some(0)).collect();
        let file: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        // The edit verb can open an IDE; the .py open verb can execute Python.
        let result = unsafe {
            ShellExecuteW(
                std::ptr::null_mut(),
                verb.as_ptr(),
                file.as_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
            )
        };
        return if result > 32 {
            Ok(serde_json::json!({"launched": true, "editor": editor}))
        } else {
            Err("CODE_EDITOR_UNAVAILABLE".into())
        };
    }
    let executable = editor_path(editor).ok_or("CODE_EDITOR_UNAVAILABLE")?;
    let environment: Vec<_> = [
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "TEMP",
        "TMP",
        "HOMEDRIVE",
        "HOMEPATH",
    ]
    .into_iter()
    .filter_map(|key| std::env::var_os(key).map(|value| (key, value)))
    .collect();
    let child = Command::new(executable)
        .arg(path)
        .env_clear()
        .envs(environment)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "CODE_EDITOR_UNAVAILABLE")?;
    Ok(serde_json::json!({"launched": true, "editor": editor, "launcher_pid": child.id()}))
}
#[cfg(not(target_os = "windows"))]
fn launch(_path: &Path, _editor: &str) -> Result<serde_json::Value, String> {
    Err("CODE_EDITOR_UNAVAILABLE".into())
}

#[tauri::command]
pub async fn open_plot_editor(
    app: tauri::AppHandle,
    state: tauri::State<'_, Mutex<crate::sidecar::SidecarState>>,
    project_id: String,
    draft_id: String,
    file_sha256: String,
    editor: String,
) -> Result<serde_json::Value, String> {
    if !uuid(&project_id)
        || !uuid(&draft_id)
        || !hex(&file_sha256, 64)
        || !["vscode", "cursor", "vscode-insiders", "notepadpp", "system"]
            .contains(&editor.as_str())
    {
        return Err("CODE_FILE_INPUT_INVALID".into());
    }
    let (port, token) = {
        let guard = state.lock().map_err(|_| "CODE_RUNTIME_UNAVAILABLE")?;
        if !guard.healthy {
            return Err("CODE_RUNTIME_UNAVAILABLE".into());
        }
        (guard.api_port, guard.session_token.clone())
    };
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "CODE_RUNTIME_UNAVAILABLE")?;
    let response = client
        .get(format!(
            "http://127.0.0.1:{port}/api/v1/projects/{project_id}/plots/{draft_id}/code-file"
        ))
        .header("X-Session-Token", token)
        .send()
        .await
        .map_err(|_| "CODE_RUNTIME_UNAVAILABLE")?;
    if !response.status().is_success() || response.content_length().unwrap_or(0) > 200_000 {
        return Err("CODE_FILE_UNAVAILABLE".into());
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|_| "CODE_FILE_UNAVAILABLE")?;
    if bytes.len() > 200_000 {
        return Err("CODE_FILE_UNAVAILABLE".into());
    }
    let value: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|_| "CODE_FILE_UNAVAILABLE")?;
    if value["file_sha256"].as_str() != Some(file_sha256.as_str()) {
        return Err("CODE_FILE_CHANGED".into());
    }
    let file = PathBuf::from(value["file_path"].as_str().ok_or("CODE_FILE_UNAVAILABLE")?);
    let root = crate::sidecar::application_data_path(&app)
        .map_err(|_| "CODE_FILE_UNAVAILABLE")?
        .join("code")
        .canonicalize()
        .map_err(|_| "CODE_FILE_UNAVAILABLE")?;
    let canonical = file.canonicalize().map_err(|_| "CODE_FILE_UNAVAILABLE")?;
    let relative = canonical
        .strip_prefix(&root)
        .map_err(|_| "CODE_FILE_UNAVAILABLE")?;
    let parts: Vec<_> = relative.components().collect();
    let name = canonical
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("CODE_FILE_UNAVAILABLE")?;
    if parts.len() != 3
        || !parts[..2]
            .iter()
            .all(|part| part.as_os_str().to_str().is_some_and(|s| hex(s, 32)))
        || !name.starts_with("plot-")
        || !name.ends_with(".py")
        || !hex(&name[5..name.len() - 3], 32)
        || !canonical.is_file()
    {
        return Err("CODE_FILE_UNAVAILABLE".into());
    }
    launch(&file, &editor)
}
