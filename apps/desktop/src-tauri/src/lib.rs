use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const HEALTH_TIMEOUT: Duration = Duration::from_millis(500);

/// Owns the backend process only when Mochi started it itself. A backend that
/// was already running (for development, or started by the user) is never
/// adopted and never killed by the desktop shell.
#[derive(Default)]
struct BackendState {
    child: Mutex<Option<Child>>,
}

fn backend_address() -> SocketAddr {
    format!("{BACKEND_HOST}:{BACKEND_PORT}")
        .parse()
        .expect("static backend address is valid")
}

/// Dependency-free health probe: the backend is healthy only when it answers
/// `GET /api/health` with HTTP 200. A bare open port is not enough.
fn backend_is_healthy() -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&backend_address(), HEALTH_TIMEOUT) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(900)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(900)));
    let request = b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }
    let mut buffer = [0_u8; 64];
    match stream.read(&mut buffer) {
        Ok(count) if count > 0 => {
            let head = String::from_utf8_lossy(&buffer[..count]);
            head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
        }
        _ => false,
    }
}

fn is_backend_root(path: &Path) -> bool {
    path.join("app").is_dir() && path.join("app").join("main.py").is_file()
}

fn find_backend_root(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("MOCHI_BACKEND_DIR") {
        let candidate = PathBuf::from(explicit);
        if is_backend_root(&candidate) {
            return Some(candidate);
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        for candidate in [resource_dir.join("backend"), resource_dir.clone()] {
            if is_backend_root(&candidate) {
                return Some(candidate);
            }
        }
    }

    let mut roots: Vec<PathBuf> = Vec::new();
    if let Ok(executable) = std::env::current_exe() {
        roots.extend(executable.ancestors().map(Path::to_path_buf));
    }
    roots.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")));

    for root in roots {
        let mut cursor = Some(root.as_path());
        // Walk a few levels up to cover both `target/debug` and a packaged
        // installation while keeping the search bounded.
        for _ in 0..6 {
            let Some(current) = cursor else { break };
            let candidate = current.join("backend");
            if is_backend_root(&candidate) {
                return Some(candidate);
            }
            cursor = current.parent();
        }
    }

    None
}

fn find_venv_python(backend_root: &Path) -> Option<PathBuf> {
    let candidates = [
        backend_root.join(".venv").join("Scripts").join("python.exe"),
        backend_root.join(".venv").join("bin").join("python"),
    ];
    candidates.into_iter().find(|candidate| candidate.is_file())
}

fn spawn_backend(app: &tauri::AppHandle, backend_root: &Path) -> Option<Child> {
    let python = find_venv_python(backend_root)?;

    let log_dir = app
        .path()
        .app_log_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("mochi-logs"));
    let _ = fs::create_dir_all(&log_dir);
    let stdout_path = log_dir.join("backend.out.log");
    let stderr_path = log_dir.join("backend.err.log");
    let stdout = File::create(&stdout_path).ok()?;
    let stderr = File::create(&stderr_path).ok()?;

    let mut command = Command::new(python);
    command
        .arg("-m")
        .arg("app.main")
        .current_dir(backend_root)
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    match command.spawn() {
        Ok(child) => Some(child),
        Err(error) => {
            let _ = fs::write(
                log_dir.join("backend.spawn.log"),
                format!("Could not start backend: {error}\n"),
            );
            None
        }
    }
}

fn wait_until_healthy(attempts: u32, delay: Duration) -> bool {
    for _ in 0..attempts {
        if backend_is_healthy() {
            return true;
        }
        std::thread::sleep(delay);
    }
    false
}

fn shutdown_backend(state: &BackendState) {
    let Ok(mut guard) = state.child.lock() else {
        return;
    };
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

pub fn run() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .setup(|app| {
            // Do not start a second server: an existing healthy backend is
            // treated as the source of truth and left untouched.
            if backend_is_healthy() {
                return Ok(());
            }

            let Some(backend_root) = find_backend_root(app.handle()) else {
                // The UI already reports an offline backend honestly. Do not
                // invent a running service or spawn an arbitrary Python.
                return Ok(());
            };

            if find_venv_python(&backend_root).is_none() {
                return Ok(());
            }

            let Some(child) = spawn_backend(app.handle(), &backend_root) else {
                return Ok(());
            };

            if let Some(state) = app.try_state::<BackendState>() {
                if let Ok(mut guard) = state.child.lock() {
                    *guard = Some(child);
                }
            }

            // Wait for readiness off the UI thread. The frontend polls health
            // and the WebSocket reconnects automatically when it is ready.
            std::thread::spawn(|| {
                let _ = wait_until_healthy(30, Duration::from_millis(400));
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Mochi")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<BackendState>() {
                    shutdown_backend(&state);
                }
            }
        });
}
