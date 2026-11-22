use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

fn has_database(root: &Path) -> Result<bool, String> {
    let database = root.join("data").join("statforge.db");
    match fs::metadata(&database) {
        Ok(metadata) if metadata.is_file() => Ok(true),
        Ok(_) => Err(format!("项目数据库路径不是文件：{}", database.display())),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!("无法读取项目库 {}：{error}", database.display())),
    }
}

pub(crate) fn installed_data_root(
    current: PathBuf,
    previous: PathBuf,
    workspace: Option<&Path>,
) -> Result<PathBuf, String> {
    let mut libraries = Vec::new();
    for root in workspace
        .into_iter()
        .chain([current.as_path(), previous.as_path()])
    {
        if has_database(root)? {
            let resolved = root
                .canonicalize()
                .map_err(|error| format!("无法打开项目库 {}：{error}", root.display()))?;
            if !libraries.iter().any(|(identity, _)| *identity == resolved) {
                libraries.push((resolved, root));
            }
        }
    }
    match libraries.as_slice() {
        [] => Ok(current),
        [(_, path)] => Ok(path.to_path_buf()),
        _ => {
            let paths = libraries
                .iter()
                .map(|(_, path)| path.display().to_string())
                .collect::<Vec<_>>()
                .join("\n");
            Err(format!("找到多个项目库：\n{paths}\n\n请将环境变量 STATFORGE_APP_DATA_DIR 设为要使用的目录，然后重新启动 Quantelune。"))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn upgrade_reuses_the_existing_database_and_preserves_distinct_libraries() {
        let root = std::env::temp_dir().join(format!("quantelune-library-{}", std::process::id()));
        fs::create_dir(&root).unwrap();
        let current = root.join("new");
        let old = root.join("old");
        let workspace = root.join("workspace");
        fs::create_dir(&workspace).unwrap();
        let resolve = || installed_data_root(current.clone(), old.clone(), Some(&workspace));
        let create_db = |path: &Path| {
            fs::create_dir_all(path.join("data")).unwrap();
            fs::write(path.join("data/statforge.db"), b"existing library").unwrap();
        };
        assert_eq!(resolve().unwrap(), current);
        create_db(&old);
        assert_eq!(resolve().unwrap(), old);
        create_db(&current);
        assert!(resolve().unwrap_err().contains("STATFORGE_APP_DATA_DIR"));
        assert_eq!(
            installed_data_root(old.clone(), old.clone(), None).unwrap(),
            old
        );
        create_db(&workspace);
        let conflict = resolve().unwrap_err();
        for path in [&current, &old, &workspace] {
            assert!(conflict.contains(&path.display().to_string()));
        }
        assert_eq!(
            fs::read(current.join("data/statforge.db")).unwrap(),
            b"existing library"
        );
        assert_eq!(
            fs::read(old.join("data/statforge.db")).unwrap(),
            b"existing library"
        );
        fs::remove_file(old.join("data/statforge.db")).unwrap();
        fs::remove_file(workspace.join("data/statforge.db")).unwrap();
        assert_eq!(resolve().unwrap(), current);
        fs::rename(
            current.join("data/statforge.db"),
            workspace.join("data/statforge.db"),
        )
        .unwrap();
        assert_eq!(resolve().unwrap(), workspace);
        fs::remove_dir_all(root).unwrap();
    }
}
