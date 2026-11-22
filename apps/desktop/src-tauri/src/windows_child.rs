//! Windows sidecar containment, assigned atomically at process creation.
//! The non-inherited Job handle lives only in Desktop: even hard Desktop exit
//! closes its last handle and terminates the PyInstaller/worker family.

use std::collections::BTreeMap;
use std::ffi::{c_void, OsStr, OsString};
use std::fs::File;
use std::io::{self, Read};
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::path::Path;
use std::ptr::{null, null_mut};

use windows_sys::Win32::Foundation::{SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT};
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::System::JobObjects::{
    CreateJobObjectW, IsProcessInJob, JobObjectExtendedLimitInformation, SetInformationJobObject,
    TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::{
    CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
    InitializeProcThreadAttributeList, OpenProcess, UpdateProcThreadAttribute, WaitForSingleObject,
    CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT,
    LPPROC_THREAD_ATTRIBUTE_LIST, PROCESS_INFORMATION, PROCESS_QUERY_LIMITED_INFORMATION,
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_JOB_LIST, STARTF_USESTDHANDLES,
    STARTUPINFOEXW,
};

// SDK ABI: IPv4 TCP_TABLE_OWNER_PID_LISTENER, without changing the locked
// windows-sys feature set or the preserved build-input Cargo.toml.
#[link(name = "iphlpapi")]
extern "system" {
    fn GetExtendedTcpTable(
        table: *mut c_void,
        bytes: *mut u32,
        ordered: i32,
        family: u32,
        class: i32,
        reserved: u32,
    ) -> u32;
}

fn loopback_listener_pid(port: u16) -> io::Result<Option<u32>> {
    let mut bytes = 0;
    let first = unsafe { GetExtendedTcpTable(null_mut(), &mut bytes, 0, 2, 3, 0) };
    if first != 122 || !(4..=1024 * 1024).contains(&bytes) {
        return Err(io::Error::other("Listener table size unavailable"));
    }
    // DWORD alignment covers the count and the six-DWORD owner-PID rows.
    let mut table = vec![0u32; (bytes as usize).div_ceil(4)];
    let capacity = table.len() * 4;
    let result = unsafe { GetExtendedTcpTable(table.as_mut_ptr().cast(), &mut bytes, 0, 2, 3, 0) };
    if result != 0 || bytes as usize > capacity || bytes < 4 {
        return Err(io::Error::other("Listener table unavailable"));
    }
    let count = table[0] as usize;
    if count > (bytes as usize - 4) / 24 {
        return Err(io::Error::other("Listener table incomplete"));
    }
    let mut owner = None;
    for row in table[1..1 + count * 6].chunks_exact(6) {
        if row[0] == 2
            && row[1] == u32::from_ne_bytes([127, 0, 0, 1])
            && u16::from_be(row[2] as u16) == port
        {
            if owner.is_some() || row[5] == 0 {
                return Err(io::Error::other("Listener identity ambiguous"));
            }
            owner = Some(row[5]);
        }
    }
    Ok(owner)
}

pub struct JobChild {
    job: OwnedHandle,
    process: OwnedHandle,
    pid: u32,
}

impl JobChild {
    #[cfg(test)]
    pub fn observer(&self) -> io::Result<OwnedHandle> {
        self.process.try_clone()
    }

    pub fn exit_code(&self) -> io::Result<Option<u32>> {
        match unsafe { WaitForSingleObject(self.process.as_raw_handle(), 0) } {
            0x00000102 => Ok(None), // WAIT_TIMEOUT: this exact process is still running.
            0 => {
                let mut code = 0;
                if unsafe { GetExitCodeProcess(self.process.as_raw_handle(), &mut code) } == 0 {
                    Err(io::Error::last_os_error())
                } else {
                    Ok(Some(code))
                }
            }
            _ => Err(io::Error::last_os_error()),
        }
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    pub fn owns_loopback_listener(&self, port: u16) -> io::Result<bool> {
        let Some(pid) = loopback_listener_pid(port)? else {
            return Ok(false);
        };
        let raw = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if raw.is_null() {
            return Err(io::Error::last_os_error());
        }
        let listener = unsafe { OwnedHandle::from_raw_handle(raw) };
        let mut belongs = 0;
        if unsafe {
            IsProcessInJob(
                listener.as_raw_handle(),
                self.job.as_raw_handle(),
                &mut belongs,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        // PyInstaller onefile's listener may be a descendant of the bootstrap
        // process. Job membership, rather than equality to the root PID, matters.
        Ok(belongs != 0)
    }

    pub fn terminate(self) -> io::Result<()> {
        // Only this owned job, never a name search or unrelated PID.
        if unsafe { TerminateJobObject(self.job.as_raw_handle(), 1) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let waited = unsafe { WaitForSingleObject(self.process.as_raw_handle(), 15_000) };
        if waited != 0 {
            return Err(io::Error::other(
                "Owned sidecar process did not exit after Job stop",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod process_tests {
    use super::*;
    use std::io::Write;
    use std::time::{Duration, Instant};

    #[test]
    fn native_owned_fixture_entry() {
        match std::env::var("STATFORGE_NATIVE_FIXTURE_MODE")
            .ok()
            .as_deref()
        {
            Some("exit23") => std::process::exit(23),
            Some("noisy-exit23") => {
                // Invalid UTF-8 and output larger than either pipe's capacity:
                // draining must not depend on lines, decoding or Desktop stderr.
                for _ in 0..128 {
                    std::io::stdout().write_all(&[0xff; 8192]).unwrap();
                    std::io::stderr().write_all(&[0xfe; 8192]).unwrap();
                }
                std::process::exit(23);
            }
            Some("sleep") => std::thread::sleep(Duration::from_secs(60)),
            Some("listen") => {
                let port: u16 = std::env::var("STATFORGE_NATIVE_FIXTURE_PORT")
                    .unwrap()
                    .parse()
                    .unwrap();
                let _listener = std::net::TcpListener::bind(("127.0.0.1", port)).unwrap();
                std::thread::sleep(Duration::from_secs(60));
            }
            Some("listen-child") => {
                let mut child = std::process::Command::new(std::env::current_exe().unwrap())
                    .args(fixture_arguments())
                    .env("STATFORGE_NATIVE_FIXTURE_MODE", "listen")
                    .spawn()
                    .unwrap();
                let _ = child.wait();
            }
            _ => {} // Ordinary suite: no child mode and no side effects.
        }
    }

    #[test]
    fn exact_owned_handle_reports_the_actual_early_exit() {
        for mode in ["exit23", "noisy-exit23"] {
            let executable = std::env::current_exe().unwrap();
            let child = spawn(
                &executable,
                &fixture_arguments(),
                &[("STATFORGE_NATIVE_FIXTURE_MODE".into(), mode.into())],
                &std::env::temp_dir(),
            )
            .unwrap();
            let started = Instant::now();
            loop {
                if let Some(code) = child.exit_code().unwrap() {
                    assert_eq!(code, 23);
                    break;
                }
                assert!(started.elapsed() < Duration::from_secs(5));
                std::thread::sleep(Duration::from_millis(10));
            }
            child.terminate().unwrap();
        }
    }

    #[test]
    fn listener_must_belong_to_the_owned_job_including_descendants() {
        for mode in ["listen", "listen-child"] {
            let reserved = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
            let port = reserved.local_addr().unwrap().port();
            drop(reserved);
            let child = spawn(
                &std::env::current_exe().unwrap(),
                &fixture_arguments(),
                &[
                    ("STATFORGE_NATIVE_FIXTURE_MODE".into(), mode.into()),
                    ("STATFORGE_NATIVE_FIXTURE_PORT".into(), port.to_string()),
                ],
                &std::env::temp_dir(),
            )
            .unwrap();
            let started = Instant::now();
            while !child.owns_loopback_listener(port).unwrap() {
                assert!(started.elapsed() < Duration::from_secs(5));
                std::thread::sleep(Duration::from_millis(10));
            }
            let outside = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
            assert!(!child
                .owns_loopback_listener(outside.local_addr().unwrap().port())
                .unwrap());
            child.terminate().unwrap();
            // Neither the root nor the descendant may retain this listening port.
            let stopped = Instant::now();
            while std::net::TcpListener::bind(("127.0.0.1", port)).is_err() {
                assert!(stopped.elapsed() < Duration::from_secs(2));
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }
}

#[cfg(test)]
pub fn fixture_arguments() -> Vec<OsString> {
    [
        "--exact",
        "windows_child::process_tests::native_owned_fixture_entry",
        "--test-threads=1",
    ]
    .into_iter()
    .map(Into::into)
    .collect()
}

struct Attributes {
    // Native attribute memory is pointer-aligned (u128 also meets 16-byte alignment).
    storage: Vec<u128>,
}

impl Attributes {
    fn new() -> io::Result<Self> {
        let mut bytes = 0;
        unsafe { InitializeProcThreadAttributeList(null_mut(), 2, 0, &mut bytes) };
        if bytes == 0 || bytes > 65_536 {
            return Err(io::Error::other("Invalid process attribute size"));
        }
        let mut storage = vec![0; bytes.div_ceil(size_of::<u128>())];
        if unsafe {
            InitializeProcThreadAttributeList(storage.as_mut_ptr().cast(), 2, 0, &mut bytes)
        } == 0
        {
            // Do not call Delete on an uninitialized attribute list.
            return Err(io::Error::last_os_error());
        }
        Ok(Self { storage })
    }

    fn pointer(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.storage.as_mut_ptr().cast()
    }

    fn set_handles(&mut self, kind: u32, handles: &[HANDLE]) -> io::Result<()> {
        if unsafe {
            UpdateProcThreadAttribute(
                self.pointer(),
                0,
                kind as usize,
                handles.as_ptr().cast(),
                std::mem::size_of_val(handles),
                null_mut(),
                null(),
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

impl Drop for Attributes {
    fn drop(&mut self) {
        unsafe { DeleteProcThreadAttributeList(self.pointer()) };
    }
}

fn pipe(child_reads: bool) -> io::Result<(OwnedHandle, OwnedHandle)> {
    let mut read = null_mut();
    let mut write = null_mut();
    let security = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: null_mut(),
        bInheritHandle: 1,
    };
    if unsafe { CreatePipe(&mut read, &mut write, &security, 0) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let read = unsafe { OwnedHandle::from_raw_handle(read) };
    let write = unsafe { OwnedHandle::from_raw_handle(write) };
    let parent_end = if child_reads {
        write.as_raw_handle()
    } else {
        read.as_raw_handle()
    };
    if unsafe { SetHandleInformation(parent_end, HANDLE_FLAG_INHERIT, 0) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok((read, write))
}

fn wide(value: &OsStr) -> io::Result<Vec<u16>> {
    let mut result: Vec<u16> = value.encode_wide().collect();
    if result.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Embedded NUL in process input",
        ));
    }
    result.push(0);
    Ok(result)
}

fn quoted(value: &OsStr) -> io::Result<Vec<u16>> {
    let units = wide(value)?;
    let mut result = vec![b'"' as u16];
    let mut slashes = 0;
    for &unit in &units[..units.len() - 1] {
        if unit == b'\\' as u16 {
            slashes += 1;
            continue;
        }
        result.extend(std::iter::repeat_n(
            b'\\' as u16,
            if unit == b'"' as u16 {
                slashes * 2 + 1
            } else {
                slashes
            },
        ));
        slashes = 0;
        result.push(unit);
    }
    result.extend(std::iter::repeat_n(b'\\' as u16, slashes * 2));
    result.push(b'"' as u16);
    Ok(result)
}

fn environment(overrides: &[(String, String)]) -> io::Result<Vec<u16>> {
    let mut values: BTreeMap<OsString, OsString> = BTreeMap::new();
    // Runtime essentials only; no inherited credentials, tracing, or user RC.
    for key in [
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "PROGRAMDATA",
        "PROGRAMFILES",
    ] {
        if let Some(value) = std::env::var_os(key) {
            values.insert(key.into(), value);
        }
    }
    for (key, value) in overrides {
        values.insert(key.into(), value.into());
    }
    for (key, value) in [
        ("PYTHONNOUSERSITE", "1"),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("LANGSMITH_TRACING", "false"),
        ("LANGCHAIN_TRACING_V2", "false"),
        ("OTEL_SDK_DISABLED", "true"),
    ] {
        values.insert(key.into(), value.into());
    }
    let mut result = Vec::new();
    for (key, value) in values {
        let mut row = key;
        row.push("=");
        row.push(value);
        result.extend(wide(&row)?);
    }
    result.push(0);
    Ok(result)
}

fn drain_pipe(handle: OwnedHandle) {
    std::thread::spawn(move || {
        let mut input = File::from(handle);
        let mut bytes = [0; 8192];
        loop {
            match input.read(&mut bytes) {
                Ok(0) => break,
                Ok(_) => {} // The sidecar owns its file log; never forward raw content.
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
    });
}

pub fn spawn(
    executable: &Path,
    arguments: &[OsString],
    overrides: &[(String, String)],
    cwd: &Path,
) -> io::Result<JobChild> {
    if !executable.is_file() || !executable.is_absolute() || !cwd.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Missing approved sidecar path",
        ));
    }
    let raw_job = unsafe { CreateJobObjectW(null(), null()) };
    if raw_job.is_null() {
        return Err(io::Error::last_os_error());
    }
    let job = unsafe { OwnedHandle::from_raw_handle(raw_job) };
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if unsafe {
        SetInformationJobObject(
            raw_job,
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let (stdin_read, stdin_write) = pipe(true)?;
    let (stdout_read, stdout_write) = pipe(false)?;
    let (stderr_read, stderr_write) = pipe(false)?;
    let child_handles = [
        stdin_read.as_raw_handle(),
        stdout_write.as_raw_handle(),
        stderr_write.as_raw_handle(),
    ];
    let job_handles = [raw_job];
    let mut attributes = Attributes::new()?;
    attributes.set_handles(PROC_THREAD_ATTRIBUTE_HANDLE_LIST, &child_handles)?;
    attributes.set_handles(PROC_THREAD_ATTRIBUTE_JOB_LIST, &job_handles)?;
    let application = wide(executable.as_os_str())?;
    let directory = wide(cwd.as_os_str())?;
    let mut command_line = quoted(executable.as_os_str())?;
    for argument in arguments {
        command_line.push(b' ' as u16);
        command_line.extend(quoted(argument)?);
    }
    command_line.push(0);
    let environment = environment(overrides)?;
    let mut startup: STARTUPINFOEXW = unsafe { zeroed() };
    startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = child_handles[0];
    startup.StartupInfo.hStdOutput = child_handles[1];
    startup.StartupInfo.hStdError = child_handles[2];
    startup.lpAttributeList = attributes.pointer();
    let mut information: PROCESS_INFORMATION = unsafe { zeroed() };
    // Windows attaches JOB_LIST before starting the process, removing the
    // spawn/assign crash gap. The Job itself is excluded from HANDLE_LIST.
    if unsafe {
        CreateProcessW(
            application.as_ptr(),
            command_line.as_mut_ptr(),
            null(),
            null(),
            1,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            environment.as_ptr().cast::<c_void>(),
            directory.as_ptr(),
            &startup.StartupInfo,
            &mut information,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let process = unsafe { OwnedHandle::from_raw_handle(information.hProcess) };
    let _thread = unsafe { OwnedHandle::from_raw_handle(information.hThread) };
    drop(stdin_read);
    drop(stdin_write);
    drop(stdout_write);
    drop(stderr_write);
    drain_pipe(stdout_read);
    drain_pipe(stderr_read);
    Ok(JobChild {
        job,
        process,
        pid: information.dwProcessId,
    })
}
