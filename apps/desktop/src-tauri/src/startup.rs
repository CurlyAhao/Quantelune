//! One startup budget, cancellation and content-free diagnostic metadata.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const STARTUP_LIMIT: Duration = Duration::from_secs(120);

#[derive(Clone, Debug)]
pub struct Progress {
    pub phase: &'static str,
    pub diagnostic_file: Option<String>,
    pub diagnostic_available: bool,
}

#[derive(Clone)]
pub struct Control {
    started: Instant,
    limit: Duration,
    cancelled: Arc<AtomicBool>,
    progress: Arc<Mutex<Progress>>,
}

impl Control {
    pub fn new(limit: Duration) -> Self {
        Self {
            started: Instant::now(),
            limit,
            cancelled: Arc::new(AtomicBool::new(false)),
            progress: Arc::new(Mutex::new(Progress {
                phase: "starting",
                diagnostic_file: None,
                diagnostic_available: false,
            })),
        }
    }

    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }

    pub fn check(&self) -> Result<(), &'static str> {
        if self.cancelled.load(Ordering::SeqCst) {
            Err("STARTUP_CANCELLED")
        } else if self.started.elapsed() >= self.limit {
            Err("STARTUP_TIMEOUT")
        } else {
            Ok(())
        }
    }

    pub fn remaining(&self) -> Duration {
        self.limit.saturating_sub(self.started.elapsed())
    }

    pub fn elapsed_ms(&self) -> u64 {
        self.started.elapsed().as_millis().min(u64::MAX as u128) as u64
    }

    pub fn progress(&self) -> Progress {
        self.progress.lock().map(|x| x.clone()).unwrap_or(Progress {
            phase: "diagnostic_unavailable",
            diagnostic_file: None,
            diagnostic_available: false,
        })
    }

    pub fn phase(&self, phase: &'static str) {
        if let Ok(mut value) = self.progress.lock() {
            value.phase = phase;
        }
    }
}

pub struct Trace {
    control: Control,
    file: Option<File>,
    bytes: usize,
    events: usize,
    attempt: String,
}

fn utc_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}

impl Trace {
    pub fn create(directory: &Path, control: &Control) -> Self {
        let attempt = format!("{}-{}", utc_ms(), std::process::id());
        let name = format!("desktop-startup-{attempt}.jsonl");
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(directory.join(&name))
            .ok();
        if let Ok(mut value) = control.progress.lock() {
            value.diagnostic_available = file.is_some();
            value.diagnostic_file = file.as_ref().map(|_| name);
        }
        Self {
            control: control.clone(),
            file,
            bytes: 0,
            events: 0,
            attempt,
        }
    }

    pub fn note(
        &mut self,
        phase: &'static str,
        pid: Option<u32>,
        exit: Option<u32>,
        reason: Option<&'static str>,
    ) {
        self.control.phase(phase);
        let value = serde_json::json!({"attempt":self.attempt,"utc_ms":utc_ms(),
            "elapsed_ms":self.control.elapsed_ms(),"phase":phase,"pid":pid,"exit_code":exit,"reason":reason});
        let mut bytes = serde_json::to_vec(&value).unwrap_or_default();
        bytes.push(b'\n');
        let written = self.events < 32
            && self.bytes + bytes.len() <= 16 * 1024
            && self
                .file
                .as_mut()
                .is_some_and(|file| file.write_all(&bytes).and_then(|_| file.flush()).is_ok());
        if written {
            self.bytes += bytes.len();
            self.events += 1;
        } else {
            self.file = None;
            if let Ok(mut value) = self.control.progress.lock() {
                value.diagnostic_available = false;
            }
        }
    }
}

#[derive(Debug, PartialEq)]
pub enum Failure {
    Code(&'static str),
    ChildExited(u32),
}

impl Failure {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Code(code) => code,
            Self::ChildExited(_) => "CHILD_EXITED",
        }
    }
}

pub async fn wait_healthy<F, G>(
    control: &Control,
    port: u16,
    mut exit_code: F,
    mut owns_listener: G,
) -> Result<(), Failure>
where
    F: FnMut() -> Result<Option<u32>, &'static str>,
    G: FnMut() -> bool,
{
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(1))
        .build()
        .map_err(|_| Failure::Code("HEALTH_CLIENT_UNAVAILABLE"))?;
    let url = format!("http://127.0.0.1:{port}/health");
    loop {
        control.check().map_err(Failure::Code)?;
        if let Some(code) = exit_code().map_err(Failure::Code)? {
            return Err(Failure::ChildExited(code));
        }
        let remaining = control.remaining();
        let response = tokio::time::timeout(remaining, client.get(&url).send()).await;
        control.check().map_err(Failure::Code)?;
        if matches!(response, Ok(Ok(response)) if response.status().is_success()) {
            let owned = owns_listener();
            control.check().map_err(Failure::Code)?;
            if owned {
                let exit = exit_code().map_err(Failure::Code)?;
                control.check().map_err(Failure::Code)?;
                return exit.map_or(Ok(()), |code| Err(Failure::ChildExited(code)));
            }
        }
        tokio::time::sleep(control.remaining().min(Duration::from_millis(250))).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn successful_response_from_unowned_listener_is_not_ready() {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = tokio::spawn(async move {
            loop {
                let (mut stream, _) = listener.accept().await.unwrap();
                let mut request = [0; 512];
                let _ = stream.read(&mut request).await.unwrap();
                stream
                    .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    .await
                    .unwrap();
            }
        });
        let result = wait_healthy(
            &Control::new(Duration::from_millis(300)),
            port,
            || Ok(None),
            || false,
        )
        .await;
        server.abort();
        let _ = server.await;
        assert_eq!(result, Err(Failure::Code("STARTUP_TIMEOUT")));
    }

    #[tokio::test]
    async fn loopback_success_and_failure_keep_the_same_deadline() {
        for status in ["200 OK", "503 Service Unavailable"] {
            let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
                .await
                .unwrap();
            let port = listener.local_addr().unwrap().port();
            let mut server = tokio::spawn(async move {
                let (mut stream, _) = listener.accept().await.unwrap();
                let mut request = [0; 512];
                let count = stream.read(&mut request).await.unwrap();
                assert!(request[..count].starts_with(b"GET /health "));
                stream
                    .write_all(
                        format!(
                            "HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        )
                        .as_bytes(),
                    )
                    .await
                    .unwrap();
            });
            let result = wait_healthy(
                &Control::new(Duration::from_millis(200)),
                port,
                || Ok(None),
                || true,
            )
            .await;
            assert_eq!(
                result,
                if status.starts_with("200") {
                    Ok(())
                } else {
                    Err(Failure::Code("STARTUP_TIMEOUT"))
                }
            );
            match tokio::time::timeout(Duration::from_secs(2), &mut server).await {
                Ok(done) => done.unwrap(),
                Err(_) => {
                    server.abort();
                    let _ = server.await;
                    panic!("Loopback fixture never completed its request");
                }
            }
        }
    }

    #[test]
    fn diagnostic_records_are_bounded_and_have_only_fixed_metadata_fields() {
        let directory = std::env::temp_dir().join(format!(
            "sf-startup-trace-{}-{}",
            std::process::id(),
            utc_ms()
        ));
        std::fs::create_dir(&directory).unwrap();
        let control = Control::new(STARTUP_LIMIT);
        let mut trace = Trace::create(&directory, &control);
        for _ in 0..40 {
            trace.note("test_phase", Some(17), Some(23), Some("SYNTHETIC"));
        }
        let name = control.progress().diagnostic_file.unwrap();
        let path = directory.join(name);
        drop(trace);
        let bytes = std::fs::read(&path).unwrap();
        assert!(bytes.len() <= 16 * 1024);
        let lines: Vec<_> = std::str::from_utf8(&bytes).unwrap().lines().collect();
        assert_eq!(lines.len(), 32);
        for line in lines {
            let value: serde_json::Value = serde_json::from_str(line).unwrap();
            let keys: std::collections::BTreeSet<_> = value
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect();
            assert_eq!(
                keys,
                [
                    "attempt",
                    "utc_ms",
                    "elapsed_ms",
                    "phase",
                    "pid",
                    "exit_code",
                    "reason"
                ]
                .into_iter()
                .collect()
            );
        }
        assert!(!control.progress().diagnostic_available); // Partial/bounded output is not a complete collection.
        std::fs::remove_file(path).unwrap();
        std::fs::remove_dir(directory).unwrap();
    }

    #[tokio::test]
    async fn cancelled_and_expired_never_probe_or_send() {
        let cancelled = Control::new(STARTUP_LIMIT);
        cancelled.cancel();
        assert_eq!(
            wait_healthy(
                &cancelled,
                1,
                || panic!("no child probe"),
                || panic!("no listener probe")
            )
            .await,
            Err(Failure::Code("STARTUP_CANCELLED"))
        );
        let expired = Control::new(Duration::ZERO);
        assert_eq!(
            wait_healthy(
                &expired,
                1,
                || panic!("no child probe"),
                || panic!("no listener probe")
            )
            .await,
            Err(Failure::Code("STARTUP_TIMEOUT"))
        );
    }

    #[tokio::test]
    async fn owned_child_exit_is_immediate_and_keeps_code() {
        assert_eq!(
            wait_healthy(
                &Control::new(STARTUP_LIMIT),
                1,
                || Ok(Some(23)),
                || panic!("no listener probe")
            )
            .await,
            Err(Failure::ChildExited(23))
        );
    }

    #[tokio::test]
    async fn slow_http_request_uses_remaining_total_budget() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        listener.set_nonblocking(true).unwrap();
        let listener = tokio::net::TcpListener::from_std(listener).unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            tokio::time::sleep(Duration::from_secs(2)).await;
            drop(stream);
        });
        let control = Control::new(Duration::from_millis(80));
        let result = tokio::time::timeout(
            Duration::from_secs(4),
            wait_healthy(&control, port, || Ok(None), || true),
        )
        .await
        .unwrap();
        assert_eq!(result, Err(Failure::Code("STARTUP_TIMEOUT")));
        assert!(control.elapsed_ms() < 1000);
        server.abort();
        let _ = server.await;
    }
}
