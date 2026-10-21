//! PHX-106 (ADR-091): the desktop shell owns the PHOENIX backend process.
//!
//! On launch the shell:
//! - takes an exclusive lock in the backend data directory, so a second PHOENIX desktop
//!   cannot start a second backend on the same data;
//! - generates a per-launch API token (32 random bytes, URL-safe base64);
//! - starts `python -m phoenix.api --host 127.0.0.1 --port 0 --data-dir <dir>` with an
//!   argument array (no shell), writes the token as one line on the child's stdin and waits,
//!   bounded, for the `phoenix.backend.ready` line on stdout to learn the port;
//! - watches the child and reports `degraded` if it exits;
//! - kills it (and, on Windows, its whole process tree through a kill-on-close job object)
//!   when the app exits.
//!
//! The token never leaves Rust: it is not on any command line, not in any environment, not
//! logged, and no Tauri command or event returns it. `Token`'s Debug output is redacted.
//!
//! Installed mode (PHX-092, ADR-125): the installer puts a self-contained backend next to the
//! executable (`backend/python/python.exe`, the embeddable CPython with the locked wheels, and
//! `backend/app/`, the PHOENIX package and migrations). Demo/dev launch mode: without that
//! directory the backend runs from the repository's own virtual environment (`<root>/.venv`).

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::Duration;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use serde::{Deserialize, Serialize};

pub const READY_EVENT: &str = "phoenix.backend.ready";
pub const LOOPBACK: &str = "127.0.0.1";
/// Startup runs migrations and bounded provider probes; the ready line normally arrives in
/// seconds. Past this bound the child is killed and the app reports `degraded`.
pub const READY_TIMEOUT: Duration = Duration::from_secs(90);
pub const LOCK_FILE: &str = "phoenix.desktop.lock";
pub const LOG_FILE: &str = "phoenix.desktop-backend.log";
const TOKEN_BYTES: usize = 32;
const MAX_READY_LINE: usize = 4096;
const WATCH_INTERVAL: Duration = Duration::from_millis(500);

/// The per-launch API token. It can only be read inside this crate, and Debug is redacted.
#[derive(Clone)]
pub struct Token(Arc<str>);

impl Token {
    pub fn generate() -> Result<Self, &'static str> {
        let mut bytes = [0u8; TOKEN_BYTES];
        getrandom::fill(&mut bytes).map_err(|_| "credential_failed")?;
        Ok(Self(URL_SAFE_NO_PAD.encode(bytes).into()))
    }

    pub(crate) fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Token {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Token(<redacted>)")
    }
}

/// Where the ready backend listens. Only Rust holds this.
#[derive(Clone, Debug)]
pub struct Endpoint {
    pub port: u16,
    pub token: Token,
}

/// What the webview may know about the backend: a state and a closed code. No port, no token.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum BackendStatus {
    Starting,
    Ready,
    Degraded { code: &'static str },
}

#[derive(Deserialize)]
struct ReadyLine {
    event: String,
    host: String,
    port: u16,
}

/// The launcher's one JSON ready line (`phoenix.api.launcher.ready_line`); other fields
/// (pid, version) are ignored. Only a loopback, non-zero port is accepted.
pub fn parse_ready_line(line: &str) -> Option<u16> {
    if line.len() > MAX_READY_LINE {
        return None;
    }
    let value: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    if !value.is_object() {
        return None;
    }
    let ready: ReadyLine = serde_json::from_value(value).ok()?;
    (ready.event == READY_EVENT && ready.host == LOOPBACK && ready.port != 0).then_some(ready.port)
}

/// How the backend is started (installed or demo/dev launch mode).
#[derive(Clone, Debug)]
pub struct LaunchConfig {
    pub root: PathBuf,
    pub python: PathBuf,
    pub data_dir: PathBuf,
    /// The bundled interpreter runs isolated (`-I`): no environment variables, no user
    /// site-packages; its import path comes only from its `python311._pth` file.
    pub isolated: bool,
}

/// The installer's backend directory, next to the executable (PHX-092).
pub const BUNDLED_BACKEND_DIR: &str = "backend";

fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

impl LaunchConfig {
    /// Backend root and interpreter, in order:
    /// 1. `PHOENIX_BACKEND_ROOT` at run time (explicit developer override), with
    ///    `PHOENIX_BACKEND_PYTHON` or the root's `.venv`;
    /// 2. installed mode: `<exe dir>/backend` exists, so the root is `backend/app` and the
    ///    interpreter `backend/python/python.exe` (or `PHOENIX_BACKEND_PYTHON`). An incomplete
    ///    installation is an error; it never falls back to a checkout;
    /// 3. demo/dev mode: `PHOENIX_BACKEND_ROOT` at build time, else the checkout this binary was
    ///    built from, with `PHOENIX_BACKEND_PYTHON` or the root's `.venv`.
    ///
    /// Data: `PHOENIX_DATA_DIR` (the backend's own setting), else the backend's default
    /// `~/.phoenix/data`, in every mode (ADR-126).
    pub fn resolve() -> Result<Self, &'static str> {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|exe| exe.parent().map(Path::to_path_buf));
        Self::resolve_with(
            &env_path,
            exe_dir.as_deref(),
            option_env!("PHOENIX_BACKEND_ROOT").map(PathBuf::from),
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."),
        )
    }

    fn resolve_with(
        env: &dyn Fn(&str) -> Option<PathBuf>,
        exe_dir: Option<&Path>,
        build_root: Option<PathBuf>,
        checkout: PathBuf,
    ) -> Result<Self, &'static str> {
        let bundle = exe_dir
            .map(|dir| dir.join(BUNDLED_BACKEND_DIR))
            .filter(|dir| dir.is_dir());
        let (root, bundled_python) = match (env("PHOENIX_BACKEND_ROOT"), bundle) {
            (Some(root), _) => (root, None),
            (None, Some(bundle)) => (bundle.join("app"), Some(bundled_python(&bundle))),
            (None, None) => (build_root.unwrap_or(checkout), None),
        };
        let root = root.canonicalize().map_err(|_| "backend_not_found")?;
        if !root
            .join("phoenix")
            .join("api")
            .join("__main__.py")
            .is_file()
        {
            return Err("backend_not_found");
        }
        let override_python = env("PHOENIX_BACKEND_PYTHON");
        let isolated = override_python.is_none() && bundled_python.is_some();
        let python = override_python
            .or(bundled_python)
            .unwrap_or_else(|| venv_python(&root));
        if !python.is_file() {
            return Err("python_not_found");
        }
        let data_dir = match env("PHOENIX_DATA_DIR") {
            Some(dir) => dir,
            None => home_dir(env)
                .ok_or("data_dir_unavailable")?
                .join(".phoenix")
                .join("data"),
        };
        Ok(Self {
            root,
            python,
            data_dir,
            isolated,
        })
    }

    /// The exact argument array; never a shell string.
    pub fn args(&self) -> Vec<std::ffi::OsString> {
        let isolation: &[&str] = if self.isolated { &["-I"] } else { &[] };
        let mut args: Vec<std::ffi::OsString> = isolation
            .iter()
            .chain(&[
                "-m",
                "phoenix.api",
                "--host",
                LOOPBACK,
                "--port",
                "0",
                "--data-dir",
            ])
            .map(Into::into)
            .collect();
        args.push(self.data_dir.clone().into_os_string());
        args
    }
}

fn bundled_python(bundle: &Path) -> PathBuf {
    if cfg!(windows) {
        bundle.join("python").join("python.exe")
    } else {
        bundle.join("python").join("bin").join("python3")
    }
}

fn venv_python(root: &Path) -> PathBuf {
    if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    }
}

fn home_dir(env: &dyn Fn(&str) -> Option<PathBuf>) -> Option<PathBuf> {
    if cfg!(windows) {
        env("USERPROFILE")
    } else {
        env("HOME")
    }
}

struct Process {
    child: Child,
    #[cfg(windows)]
    _job: Option<job::Job>,
}

struct Inner {
    status: BackendStatus,
    endpoint: Option<Endpoint>,
    process: Option<Process>,
    lock: Option<File>,
    stopping: bool,
}

pub type Notify = Arc<dyn Fn(BackendStatus) + Send + Sync>;

/// The backend lifecycle, shared by the Tauri commands, the event bridge and the watcher.
pub struct Backend {
    inner: Mutex<Inner>,
}

impl Default for Backend {
    fn default() -> Self {
        Self::new()
    }
}

impl Backend {
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(Inner {
                status: BackendStatus::Starting,
                endpoint: None,
                process: None,
                lock: None,
                stopping: false,
            }),
        }
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub fn status(&self) -> BackendStatus {
        self.lock().status.clone()
    }

    pub fn endpoint(&self) -> Option<Endpoint> {
        self.lock().endpoint.clone()
    }

    pub fn is_stopping(&self) -> bool {
        self.lock().stopping
    }

    fn set_status(&self, status: BackendStatus, notify: &Notify) {
        {
            let mut inner = self.lock();
            if inner.stopping || inner.status == status {
                return;
            }
            if status != BackendStatus::Ready {
                inner.endpoint = None;
            }
            inner.status = status.clone();
        }
        notify(status);
    }

    /// Starts the backend on a background thread; progress is reported through `notify`.
    pub fn start(self: &Arc<Self>, notify: Notify) {
        let backend = Arc::clone(self);
        thread::Builder::new()
            .name("phoenix-backend-launch".into())
            .spawn(move || {
                if let Err(code) = backend.launch(&notify) {
                    backend.kill_child();
                    backend.set_status(BackendStatus::Degraded { code }, &notify);
                }
            })
            .expect("failed to start the backend launch thread");
    }

    fn launch(self: &Arc<Self>, notify: &Notify) -> Result<(), &'static str> {
        let config = LaunchConfig::resolve()?;
        fs::create_dir_all(&config.data_dir).map_err(|_| "data_dir_unavailable")?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .write(true)
            .open(config.data_dir.join(LOCK_FILE))
            .map_err(|_| "data_dir_unavailable")?;
        lock.try_lock().map_err(|_| "already_running")?;
        self.lock().lock = Some(lock);

        let token = Token::generate()?;
        let log =
            File::create(config.data_dir.join(LOG_FILE)).map_err(|_| "data_dir_unavailable")?;
        let mut command = Command::new(&config.python);
        command
            .args(config.args())
            .current_dir(&config.root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(log));
        let (mut child, job) = spawn(command)?;
        let mut stdin = child.stdin.take().ok_or("spawn_failed")?;
        let stdout = child.stdout.take().ok_or("spawn_failed")?;
        {
            let mut inner = self.lock();
            if inner.stopping {
                let _ = child.kill();
                return Err("stopped");
            }
            inner.process = Some(Process {
                child,
                #[cfg(windows)]
                _job: job,
            });
        }
        #[cfg(not(windows))]
        let _ = job;

        // One line, then EOF. A failed write means the child already exited.
        let mut line = String::with_capacity(token.expose().len() + 1);
        line.push_str(token.expose());
        line.push('\n');
        let written = stdin
            .write_all(line.as_bytes())
            .and_then(|()| stdin.flush());
        drop(stdin);
        drop(line);
        written.map_err(|_| "backend_exited")?;

        let port = wait_ready(stdout, READY_TIMEOUT)?;
        {
            let mut inner = self.lock();
            if inner.stopping {
                return Err("stopped");
            }
            inner.endpoint = Some(Endpoint { port, token });
        }
        self.set_status(BackendStatus::Ready, notify);
        self.watch(notify.clone());
        Ok(())
    }

    /// Reports `degraded` once if the child exits while the app is still running.
    fn watch(self: &Arc<Self>, notify: Notify) {
        let backend = Arc::clone(self);
        let _ = thread::Builder::new()
            .name("phoenix-backend-watch".into())
            .spawn(move || loop {
                thread::sleep(WATCH_INTERVAL);
                let exited = {
                    let mut inner = backend.lock();
                    if inner.stopping {
                        return;
                    }
                    let running = inner
                        .process
                        .as_mut()
                        .map(|process| process.child.try_wait());
                    !matches!(running, Some(Ok(None)))
                };
                if exited {
                    backend.kill_child();
                    backend.set_status(
                        BackendStatus::Degraded {
                            code: "backend_exited",
                        },
                        &notify,
                    );
                    return;
                }
            });
    }

    fn kill_child(&self) {
        let process = self.lock().process.take();
        if let Some(mut process) = process {
            let _ = process.child.kill();
            let _ = process.child.wait();
            // Dropping the process drops the job handle, which kills any remaining tree.
        }
    }

    /// App exit: stop reporting, kill the backend tree and release the data-dir lock.
    pub fn shutdown(&self) {
        {
            let mut inner = self.lock();
            inner.stopping = true;
            inner.endpoint = None;
        }
        self.kill_child();
        self.lock().lock = None;
    }
}

/// Reads stdout lines on a helper thread until the ready line, EOF or the deadline.
fn wait_ready(
    stdout: impl std::io::Read + Send + 'static,
    timeout: Duration,
) -> Result<u16, &'static str> {
    let (sender, receiver) = mpsc::channel::<Option<u16>>();
    thread::Builder::new()
        .name("phoenix-backend-stdout".into())
        .spawn(move || {
            let mut announced = false;
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if !announced {
                    if let Some(port) = parse_ready_line(&line) {
                        announced = true;
                        let _ = sender.send(Some(port));
                    }
                }
                // Later stdout lines are drained and ignored, so the pipe never fills.
            }
            let _ = sender.send(None);
        })
        .map_err(|_| "spawn_failed")?;
    match receiver.recv_timeout(timeout) {
        Ok(Some(port)) => Ok(port),
        Ok(None) | Err(RecvTimeoutError::Disconnected) => Err("backend_exited"),
        Err(RecvTimeoutError::Timeout) => Err("ready_timeout"),
    }
}

#[cfg(windows)]
fn spawn(mut command: Command) -> Result<(Child, Option<job::Job>), &'static str> {
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};
    // Suspended until it is inside the kill-on-close job, so even a venv launcher's child
    // (the real interpreter) is in the job. No console window for the GUI app's child.
    command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
    let child = command.spawn().map_err(|_| "spawn_failed")?;
    let job = job::Job::kill_on_close().ok();
    if let Some(job) = &job {
        let _ = job.assign(&child); // best effort: without it, kill() still stops the launcher
    }
    if job::resume(child.id()).is_err() {
        let mut child = child;
        let _ = child.kill();
        return Err("spawn_failed");
    }
    Ok((child, job))
}

#[cfg(not(windows))]
fn spawn(mut command: Command) -> Result<(Child, Option<()>), &'static str> {
    let child = command.spawn().map_err(|_| "spawn_failed")?;
    Ok((child, None))
}

#[cfg(windows)]
mod job {
    use std::ffi::c_void;
    use std::io;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    /// A job object whose processes are all killed when its last handle closes, including
    /// when the desktop app itself dies.
    pub struct Job(HANDLE);

    // The handle is only used for assignment and closed once on drop.
    unsafe impl Send for Job {}

    impl Job {
        pub fn kill_on_close() -> io::Result<Self> {
            // SAFETY: null attributes and name create an anonymous job.
            let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            let job = Self(handle);
            // SAFETY: a zeroed limit structure is valid; only the kill-on-close flag is set.
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            // SAFETY: the pointer and size describe `info`, which outlives the call.
            let ok = unsafe {
                SetInformationJobObject(
                    job.0,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )
            };
            if ok == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(job)
        }

        pub fn assign(&self, child: &Child) -> io::Result<()> {
            // SAFETY: both handles are valid for the duration of the call.
            let ok = unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) };
            if ok == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            // SAFETY: the handle was created by CreateJobObjectW and is closed exactly once.
            unsafe { CloseHandle(self.0) };
        }
    }

    /// Resumes the suspended threads of a process created with CREATE_SUSPENDED.
    pub fn resume(pid: u32) -> io::Result<()> {
        // SAFETY: a thread snapshot of the whole system; closed below.
        let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        if snapshot == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: THREADENTRY32 is plain data; dwSize is set before use.
        let mut entry: THREADENTRY32 = unsafe { std::mem::zeroed() };
        entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;
        let mut resumed = 0;
        // SAFETY: snapshot and entry are valid for these calls.
        let mut more = unsafe { Thread32First(snapshot, &mut entry) } != 0;
        while more {
            if entry.th32OwnerProcessID == pid {
                // SAFETY: the thread handle is closed right after resuming.
                let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
                if !thread.is_null() {
                    unsafe {
                        ResumeThread(thread);
                        CloseHandle(thread);
                    }
                    resumed += 1;
                }
            }
            more = unsafe { Thread32Next(snapshot, &mut entry) } != 0;
        }
        // SAFETY: closes the snapshot handle exactly once.
        unsafe { CloseHandle(snapshot) };
        if resumed == 0 {
            return Err(io::Error::other("no thread resumed"));
        }
        Ok(())
    }
}

#[tauri::command]
pub fn backend_status(backend: tauri::State<'_, Arc<Backend>>) -> BackendStatus {
    backend.status()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_line_yields_the_loopback_port() {
        let line = r#"{"event":"phoenix.backend.ready","host":"127.0.0.1","port":51234,"pid":42,"version":"0.1.0"}"#;
        assert_eq!(parse_ready_line(line), Some(51234));
        assert_eq!(parse_ready_line(&format!("  {line}\r\n")), Some(51234));
    }

    #[test]
    fn other_lines_are_not_ready() {
        for line in [
            "",
            "not json",
            "INFO uvicorn: started",
            r#"{"event":"phoenix.backend.started","host":"127.0.0.1","port":5000}"#,
            r#"{"event":"phoenix.backend.ready","host":"0.0.0.0","port":5000}"#,
            r#"{"event":"phoenix.backend.ready","host":"::1","port":5000}"#,
            r#"{"event":"phoenix.backend.ready","host":"127.0.0.1","port":0}"#,
            r#"{"event":"phoenix.backend.ready","host":"127.0.0.1","port":70000}"#,
            r#"{"event":"phoenix.backend.ready","host":"127.0.0.1","port":"5000"}"#,
            r#"{"event":"phoenix.backend.ready","host":"127.0.0.1"}"#,
            r#"["phoenix.backend.ready","127.0.0.1",5000]"#,
        ] {
            assert_eq!(parse_ready_line(line), None, "{line}");
        }
        let padded = format!(
            r#"{{"event":"phoenix.backend.ready","host":"127.0.0.1","port":5000,"pad":"{}"}}"#,
            "x".repeat(MAX_READY_LINE)
        );
        assert_eq!(parse_ready_line(&padded), None);
    }

    #[test]
    fn token_is_32_random_bytes_in_url_safe_base64() {
        let token = Token::generate().unwrap();
        assert_eq!(token.expose().len(), 43);
        assert!(token
            .expose()
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'));
        assert_eq!(URL_SAFE_NO_PAD.decode(token.expose()).unwrap().len(), 32);
        assert_ne!(token.expose(), Token::generate().unwrap().expose());
    }

    #[test]
    fn token_is_redacted_from_debug_and_status() {
        let token = Token::generate().unwrap();
        let endpoint = Endpoint {
            port: 1,
            token: token.clone(),
        };
        let debug = format!("{token:?} {endpoint:?} {:?}", Some(&endpoint));
        assert!(!debug.contains(token.expose()));
        assert!(debug.contains("Token(<redacted>)"));
        for status in [
            BackendStatus::Starting,
            BackendStatus::Ready,
            BackendStatus::Degraded {
                code: "backend_exited",
            },
        ] {
            let json = serde_json::to_string(&status).unwrap();
            assert!(!json.contains(token.expose()));
            assert!(!json.contains("port"));
        }
        assert_eq!(
            serde_json::to_string(&BackendStatus::Degraded {
                code: "ready_timeout"
            })
            .unwrap(),
            r#"{"state":"degraded","code":"ready_timeout"}"#
        );
    }

    #[test]
    fn launch_arguments_are_an_explicit_loopback_array() {
        let mut config = LaunchConfig {
            root: PathBuf::from("root"),
            python: PathBuf::from("python"),
            data_dir: PathBuf::from("data dir; rm -rf /"),
            isolated: false,
        };
        let args = |config: &LaunchConfig| -> Vec<String> {
            config
                .args()
                .into_iter()
                .map(|a| a.into_string().unwrap())
                .collect()
        };
        let expected = [
            "-m",
            "phoenix.api",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--data-dir",
            "data dir; rm -rf /",
        ];
        assert_eq!(args(&config), expected);
        // PHX-092: the bundled interpreter is started isolated, before the module.
        config.isolated = true;
        assert_eq!(args(&config)[0], "-I");
        assert_eq!(args(&config)[1..], expected);
    }

    #[test]
    fn ready_wait_is_bounded_and_ends_on_eof() {
        let ready = b"INFO starting\n{\"event\":\"phoenix.backend.ready\",\"host\":\"127.0.0.1\",\"port\":4321}\n";
        assert_eq!(
            wait_ready(std::io::Cursor::new(ready.to_vec()), Duration::from_secs(5)),
            Ok(4321)
        );
        assert_eq!(
            wait_ready(
                std::io::Cursor::new(b"no ready line\n".to_vec()),
                Duration::from_secs(5)
            ),
            Err("backend_exited")
        );
        struct Silent;
        impl std::io::Read for Silent {
            fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
                thread::sleep(Duration::from_secs(2));
                Ok(0)
            }
        }
        assert_eq!(
            wait_ready(Silent, Duration::from_millis(100)),
            Err("ready_timeout")
        );
    }

    /// A scratch tree under the temp directory, removed on drop.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(name: &str) -> Self {
            let dir =
                std::env::temp_dir().join(format!("phoenix-desktop-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            Self(dir)
        }

        fn file(&self, relative: &str) -> PathBuf {
            let path = self.0.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, b"").unwrap();
            path
        }

        /// A backend root (`<root>/phoenix/api/__main__.py`), canonical as resolve returns it.
        fn backend(&self, root: &str) -> PathBuf {
            self.file(&format!("{root}/phoenix/api/__main__.py"));
            self.0.join(root).canonicalize().unwrap()
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn env_of(pairs: &[(&str, PathBuf)]) -> impl Fn(&str) -> Option<PathBuf> {
        let pairs: Vec<(String, PathBuf)> = pairs
            .iter()
            .map(|(key, value)| (key.to_string(), value.clone()))
            .collect();
        move |name| {
            pairs
                .iter()
                .find(|(key, _)| key == name)
                .map(|(_, value)| value.clone())
        }
    }

    const PYTHON_IN_BUNDLE: &str = if cfg!(windows) {
        "app-dir/backend/python/python.exe"
    } else {
        "app-dir/backend/python/bin/python3"
    };
    const PYTHON_IN_VENV: &str = if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    };
    const HOME: &str = if cfg!(windows) { "USERPROFILE" } else { "HOME" };

    #[test]
    fn installed_mode_uses_the_bundled_runtime_next_to_the_executable() {
        let scratch = Scratch::new("bundled");
        let app = scratch.backend("app-dir/backend/app");
        let python = scratch.file(PYTHON_IN_BUNDLE);
        let checkout = scratch.backend("checkout");
        scratch.file(&format!("checkout/{PYTHON_IN_VENV}"));
        let env = env_of(&[(HOME, scratch.0.join("home"))]);
        let exe_dir = scratch.0.join("app-dir");
        let config = LaunchConfig::resolve_with(&env, Some(&exe_dir), None, checkout).unwrap();
        assert_eq!(config.root, app);
        assert_eq!(config.python, python);
        assert!(config.isolated);
        assert_eq!(
            config.data_dir,
            scratch.0.join("home").join(".phoenix").join("data")
        );
    }

    #[test]
    fn an_incomplete_installation_never_falls_back_to_a_checkout() {
        let scratch = Scratch::new("incomplete");
        let checkout = scratch.backend("checkout");
        scratch.file(&format!("checkout/{PYTHON_IN_VENV}"));
        let env = env_of(&[(HOME, scratch.0.join("home"))]);
        let exe_dir = scratch.0.join("app-dir");
        fs::create_dir_all(exe_dir.join(BUNDLED_BACKEND_DIR)).unwrap();
        let resolve = || LaunchConfig::resolve_with(&env, Some(&exe_dir), None, checkout.clone());
        assert_eq!(resolve().map(|c| c.root), Err("backend_not_found"));
        let app = scratch.backend("app-dir/backend/app");
        assert_eq!(resolve().map(|c| c.root), Err("python_not_found"));
        let python = scratch.file(PYTHON_IN_BUNDLE);
        assert_eq!(resolve().map(|c| (c.root, c.python)), Ok((app, python)));
    }

    #[test]
    fn explicit_overrides_win_over_the_installation() {
        let scratch = Scratch::new("overrides");
        scratch.backend("app-dir/backend/app");
        scratch.file(PYTHON_IN_BUNDLE);
        let dev = scratch.backend("dev");
        scratch.file(&format!("dev/{PYTHON_IN_VENV}"));
        let dev_python = venv_python(&dev);
        let custom_python = scratch.file("custom/python.exe");
        let data = scratch.0.join("D Phoenix data");
        let exe_dir = scratch.0.join("app-dir");
        let env = env_of(&[
            ("PHOENIX_BACKEND_ROOT", scratch.0.join("dev")),
            ("PHOENIX_DATA_DIR", data.clone()),
        ]);
        let config =
            LaunchConfig::resolve_with(&env, Some(&exe_dir), None, scratch.0.clone()).unwrap();
        assert_eq!(
            (config.root, config.python, config.data_dir, config.isolated),
            (dev, dev_python, data, false)
        );
        // The interpreter override also applies to the bundled root.
        let env = env_of(&[
            ("PHOENIX_BACKEND_PYTHON", custom_python.clone()),
            (HOME, scratch.0.join("home")),
        ]);
        let config =
            LaunchConfig::resolve_with(&env, Some(&exe_dir), None, scratch.0.clone()).unwrap();
        assert!(config.root.ends_with("app"));
        assert_eq!(config.python, custom_python);
        assert!(!config.isolated);
    }

    #[test]
    fn without_an_installation_the_dev_checkout_and_venv_are_used() {
        let scratch = Scratch::new("dev");
        let checkout = scratch.backend("checkout");
        scratch.file(&format!("checkout/{PYTHON_IN_VENV}"));
        let venv = venv_python(&checkout);
        let built = scratch.backend("built");
        scratch.file(&format!("built/{PYTHON_IN_VENV}"));
        let built_venv = venv_python(&built);
        let exe_dir = scratch.0.join("target");
        fs::create_dir_all(&exe_dir).unwrap();
        let env = env_of(&[(HOME, scratch.0.join("home"))]);
        let config =
            LaunchConfig::resolve_with(&env, Some(&exe_dir), None, checkout.clone()).unwrap();
        assert_eq!((config.root, config.python), (checkout.clone(), venv));
        let built_root = Some(scratch.0.join("built"));
        let config =
            LaunchConfig::resolve_with(&env, Some(&exe_dir), built_root, checkout.clone()).unwrap();
        assert_eq!((config.root, config.python), (built, built_venv));
        // No home directory and no PHOENIX_DATA_DIR: an explicit, closed error.
        assert_eq!(
            LaunchConfig::resolve_with(&env_of(&[]), None, None, checkout).map(|c| c.data_dir),
            Err("data_dir_unavailable")
        );
    }

    #[test]
    fn second_lock_on_the_same_data_dir_is_refused() {
        let dir = std::env::temp_dir().join(format!("phoenix-desktop-lock-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let open = || {
            OpenOptions::new()
                .create(true)
                .truncate(false)
                .write(true)
                .open(dir.join(LOCK_FILE))
                .unwrap()
        };
        let first = open();
        first.try_lock().unwrap();
        assert!(open().try_lock().is_err());
        drop(first);
        assert!(open().try_lock().is_ok());
        let _ = fs::remove_dir_all(&dir);
    }
}
