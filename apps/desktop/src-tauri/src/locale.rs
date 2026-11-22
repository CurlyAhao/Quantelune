#[cfg(target_os = "windows")]
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

const SIMPLIFIED_CHINESE_LANGUAGE_ID: u32 = 2052;
const ENGLISH_LANGUAGE_ID: u32 = 1033;

pub fn windows_language_id_to_locale(language_id: u32) -> Option<&'static str> {
    match language_id {
        SIMPLIFIED_CHINESE_LANGUAGE_ID => Some("zh-CN"),
        ENGLISH_LANGUAGE_ID => Some("en"),
        _ => None,
    }
}

#[cfg(target_os = "windows")]
pub fn read_installer_locale() -> Option<&'static str> {
    let current_user = RegKey::predef(HKEY_CURRENT_USER);
    let installer_key = current_user
        .open_subkey(r"Software\statforge\StatForge")
        .ok()?;
    let language_id: u32 = installer_key.get_value("Installer Language").ok()?;
    windows_language_id_to_locale(language_id)
}

#[cfg(not(target_os = "windows"))]
pub fn read_installer_locale() -> Option<&'static str> {
    None
}
