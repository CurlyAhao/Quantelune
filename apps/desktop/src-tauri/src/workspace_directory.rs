//! Directory browsing for the user-selected local workspace. Reads names, not data.
use std::path::{Path, PathBuf};

#[derive(serde::Serialize)]
pub struct DirectoryEntry {
    name: String,
    path: String,
    directory: bool,
    bytes: u64,
}

#[derive(serde::Serialize)]
pub struct DirectoryListing {
    path: String,
    parent: Option<String>,
    drives: Vec<String>,
    entries: Vec<DirectoryEntry>,
}

fn display(path: &Path) -> String {
    let value = path.to_string_lossy();
    if let Some(unc) = value.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{}", unc)
    } else {
        value.strip_prefix(r"\\?\").unwrap_or(&value).to_string()
    }
}

fn default_directory(app: &tauri::AppHandle, drives: &[String]) -> PathBuf {
    if let Some(data) = app.config().app.windows.iter().find_map(|window| window.data_directory.as_ref()) {
        if let Ok(bytes) = std::fs::read(data.join("local-workspaces.json")) {
            if let Ok(serde_json::Value::Object(entries)) = serde_json::from_slice::<serde_json::Value>(&bytes) {
                for entry in entries.values() {
                    if let Some(root) = entry.get("root").and_then(|root| root.as_str()) {
                        if Path::new(root).is_dir() {
                            return PathBuf::from(root);
                        }
                    }
                }
            }
        }
    }
    PathBuf::from(drives.iter().find(|drive| !drive.starts_with('C')).or_else(|| drives.first()).map(String::as_str).unwrap_or("/"))
}

#[tauri::command]
pub fn browse_workspace_directory(app: tauri::AppHandle, path: Option<String>) -> Result<DirectoryListing, String> {
    #[cfg(target_os = "windows")]
    let drives: Vec<String> = ('A'..='Z').map(|letter| format!("{letter}:\\")).filter(|path| Path::new(path).is_dir()).collect();
    #[cfg(not(target_os = "windows"))]
    let drives: Vec<String> = vec!["/".into()];
    let requested = path.filter(|value| !value.trim().is_empty()).map(PathBuf::from).unwrap_or_else(|| default_directory(&app, &drives));
    if !requested.is_absolute() {
        return Err("请输入完整文件夹路径，例如 F:\\CMU-MOSI。".into());
    }
    let root = requested.canonicalize().map_err(|_| "无法打开这个路径，请核对文件夹位置和访问权限。")?;
    if !root.is_dir() {
        return Err("这个路径是文件，请打开它所在的文件夹。".into());
    }
    let mut entries = Vec::new();
    for item in std::fs::read_dir(&root).map_err(|_| "无法读取文件夹，请选择有访问权限的目录。")? {
        let item = item.map_err(|_| "读取目录时发生错误，请刷新重试。")?;
        let metadata = item.metadata().map_err(|_| "无法读取其中一个文件，请刷新或核对文件权限。")?;
        let kind = item.file_type().map_err(|_| "无法读取文件类型，请刷新重试。")?;
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 { continue; }
        }
        if kind.is_symlink() || item.file_name() == ".statforge" { continue; }
        entries.push(DirectoryEntry { name: item.file_name().to_string_lossy().into_owned(), path: display(&item.path()), directory: kind.is_dir(), bytes: metadata.len() });
    }
    entries.sort_by(|a, b| b.directory.cmp(&a.directory).then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase())));
    Ok(DirectoryListing { path: display(&root), parent: root.parent().map(display), drives, entries })
}
