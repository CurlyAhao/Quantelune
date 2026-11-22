use std::path::Path;

use crate::locale::windows_language_id_to_locale;
use crate::sidecar::DesktopRuntimePaths;

#[test]
fn desktop_runtime_paths_live_under_the_user_writable_app_data_directory() {
    let paths = DesktopRuntimePaths::from_app_data(Path::new(
        r"C:\Users\Researcher\AppData\Local\StatForge",
    ));

    assert_eq!(
        paths.sqlite_path,
        Path::new(r"C:\Users\Researcher\AppData\Local\StatForge\data\statforge.db")
    );
    assert_eq!(
        paths.storage_path,
        Path::new(r"C:\Users\Researcher\AppData\Local\StatForge\storage")
    );
    assert_eq!(
        paths.logs_path,
        Path::new(r"C:\Users\Researcher\AppData\Local\StatForge\logs")
    );
}

#[test]
fn installer_language_ids_map_to_application_locales() {
    assert_eq!(windows_language_id_to_locale(2052), Some("zh-CN"));
    assert_eq!(windows_language_id_to_locale(1033), Some("en"));
    assert_eq!(windows_language_id_to_locale(1036), None);
}
