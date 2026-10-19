//! The Python backend (`python -m iak serve`), started with the application
//! and stopped with it.
//!
//! IA-K is portable: the executable sits at the root of the key (or of a
//! disk folder) next to `backend/`, `build/python/<os>/` and `data/`. The
//! root is found from the executable's location, or from `IAK_ROOT`.
//! The backend announces itself on its first stdout line:
//!     IAK_API {"url": "http://127.0.0.1:PORT", "token": "...", "version": "..."}
//! Its stderr goes to `data/iak-backend.log`.

use serde::Serialize;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Clone, Serialize, Default)]
pub struct Info {
    /// starting | ready | error | stopped
    pub status: String,
    pub url: String,
    pub token: String,
    pub version: String,
    pub error: String,
    pub root: String,
    pub library: String,
    pub log: String,
}

#[derive(Default)]
pub struct Backend {
    pub info: Arc<Mutex<Info>>,
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
}

/// Folder holding `backend/iak/__main__.py`: IAK_ROOT, else the folder of the
/// executable or one of its parents (development builds live in
/// src-tauri/target/debug), else the current folder.
pub fn find_root() -> Option<PathBuf> {
    let is_root = |p: &Path| p.join("backend").join("iak").join("__main__.py").is_file();
    if let Ok(r) = std::env::var("IAK_ROOT") {
        let p = PathBuf::from(r);
        if is_root(&p) {
            return Some(p);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        // an AppImage runs from a temporary mount: its own location is in APPIMAGE
        let exe = std::env::var("APPIMAGE").map(PathBuf::from).unwrap_or(exe);
        for dir in exe.ancestors().skip(1).take(6) {
            if is_root(dir) {
                return Some(dir.to_path_buf());
            }
        }
    }
    std::env::current_dir().ok().filter(|p| is_root(p))
}

/// Library folder chosen in the settings (config/app.json, "library"):
/// absolute, or relative to the IA-K folder so that it follows the key.
fn configured_library(root: &Path) -> Option<PathBuf> {
    let text = fs::read_to_string(root.join("config").join("app.json")).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let lib = value.get("library")?.as_str()?.trim();
    if lib.is_empty() {
        return None;
    }
    let p = PathBuf::from(lib);
    Some(if p.is_absolute() { p } else { root.join(p) })
}

/// Save the library folder in config/app.json (None: back to data/). A path
/// inside the IA-K folder is stored relative to it.
pub fn save_library(library: Option<&str>) -> Result<(), String> {
    let root = find_root().ok_or("IA-K folder not found")?;
    let path = root.join("config").join("app.json");
    let mut value: serde_json::Value = fs::read_to_string(&path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| serde_json::json!({}));
    match library.map(str::trim).filter(|l| !l.is_empty()) {
        Some(l) => {
            let p = PathBuf::from(l);
            if !p.is_absolute() {
                return Err(format!("not an absolute path: {l}"));
            }
            fs::create_dir_all(&p).map_err(|e| format!("cannot create {l}: {e}"))?;
            let stored = p
                .strip_prefix(&root)
                .map(|r| r.to_string_lossy().replace('\\', "/"))
                .unwrap_or_else(|_| p.to_string_lossy().into_owned());
            value["library"] = serde_json::Value::String(stored);
        }
        None => {
            if let Some(obj) = value.as_object_mut() {
                obj.remove("library");
            }
        }
    }
    fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let text = serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?;
    fs::write(&path, text + "\n").map_err(|e| format!("cannot write {}: {e}", path.display()))
}

/// Portable Python of the key, else a Python of the computer (development).
fn find_python(root: &Path) -> PathBuf {
    let portable = if cfg!(windows) {
        root.join("build").join("python").join("windows").join("python.exe")
    } else {
        root.join("build").join("python").join("linux").join("bin").join("python3.12")
    };
    if portable.is_file() {
        return portable;
    }
    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

impl Backend {
    pub fn start(&self, lang: &str) {
        let info = self.info.clone();
        let fail = |msg: String| {
            let mut i = info.lock().unwrap();
            i.status = "error".into();
            i.error = msg;
        };
        {
            let mut i = info.lock().unwrap();
            *i = Info { status: "starting".into(), ..Default::default() };
        }
        let Some(root) = find_root() else {
            fail("IA-K files not found: put the program at the root of the IA-K folder \
                  (next to backend/), or set IAK_ROOT"
                .into());
            return;
        };
        let library = std::env::var("IAK_LIBRARY")
            .map(PathBuf::from)
            .ok()
            .or_else(|| configured_library(&root))
            .unwrap_or_else(|| root.join("data"));
        if let Err(e) = fs::create_dir_all(&library) {
            fail(format!("cannot create the library folder {}: {e}", library.display()));
            return;
        }
        let log_path = library.join("iak-backend.log");
        let log = match File::create(&log_path) {
            Ok(f) => f,
            Err(e) => {
                fail(format!("cannot write {}: {e}", log_path.display()));
                return;
            }
        };
        let python = find_python(&root);
        let mut cmd = Command::new(&python);
        cmd.args(["-m", "iak", "--lang", lang, "serve", "--watch-stdin"])
            .current_dir(&root)
            .env("PYTHONPATH", root.join("backend"))
            .env("PYTHONUTF8", "1")
            .env("PYTHONUNBUFFERED", "1")
            .env("IAK_LIBRARY", &library)
            // kept open while the application lives: the backend stops when
            // it closes, even if the application is killed or crashes
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(log));
        let tess = root.join("build").join("tesseract");
        let tess_exe = tess.join(if cfg!(windows) { "tesseract.exe" } else { "tesseract" });
        if tess_exe.is_file() {
            cmd.env("IAK_TESSERACT", &tess_exe);
            if tess.join("tessdata").is_dir() {
                cmd.env("TESSDATA_PREFIX", tess.join("tessdata"));
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        {
            let mut i = info.lock().unwrap();
            i.root = root.display().to_string();
            i.library = library.display().to_string();
            i.log = log_path.display().to_string();
        }
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                fail(format!(
                    "cannot start {}: {e}. Run setup_python first (see README).",
                    python.display()
                ));
                return;
            }
        };
        let stdout = child.stdout.take().expect("piped stdout");
        *self.stdin.lock().unwrap() = child.stdin.take();
        *self.child.lock().unwrap() = Some(child);

        // Read the announce line, then keep draining stdout.
        let reader_info = info.clone();
        thread::spawn(move || {
            let mut lines = BufReader::new(stdout).lines();
            let mut announced = false;
            while let Some(Ok(line)) = lines.next() {
                if announced {
                    continue;
                }
                if let Some(json) = line.strip_prefix("IAK_API ") {
                    let mut i = reader_info.lock().unwrap();
                    match serde_json::from_str::<serde_json::Value>(json) {
                        Ok(v) => {
                            i.url = v["url"].as_str().unwrap_or_default().into();
                            i.token = v["token"].as_str().unwrap_or_default().into();
                            i.version = v["version"].as_str().unwrap_or_default().into();
                            i.status = "ready".into();
                        }
                        Err(e) => {
                            i.status = "error".into();
                            i.error = format!("unreadable backend announce: {e}");
                        }
                    }
                    announced = true;
                }
            }
            // stdout closed: the backend has stopped
            let mut i = reader_info.lock().unwrap();
            if i.status != "error" {
                i.status = if announced { "stopped".into() } else { "error".into() };
                if !announced {
                    i.error = format!("the backend stopped while starting; see {}", i.log);
                }
            }
        });

        // Watchdog: no announce within 90 s (slow USB key) is an error.
        let wd = info.clone();
        thread::spawn(move || {
            let t0 = Instant::now();
            while t0.elapsed() < Duration::from_secs(90) {
                if wd.lock().unwrap().status != "starting" {
                    return;
                }
                thread::sleep(Duration::from_millis(250));
            }
            let mut i = wd.lock().unwrap();
            if i.status == "starting" {
                i.status = "error".into();
                i.error = format!("the backend did not start within 90 s; see {}", i.log);
            }
        });
    }

    pub fn stop(&self) {
        // closing stdin asks the backend to stop; kill makes sure of it
        self.stdin.lock().unwrap().take();
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        let mut i = self.info.lock().unwrap();
        if i.status == "ready" || i.status == "starting" {
            i.status = "stopped".into();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn library_setting_round_trip() {
        let root = std::env::temp_dir().join(format!("iak-root-{}", std::process::id()));
        fs::create_dir_all(root.join("backend").join("iak")).unwrap();
        fs::write(root.join("backend").join("iak").join("__main__.py"), "").unwrap();
        std::env::set_var("IAK_ROOT", &root);
        assert_eq!(find_root().unwrap(), root);
        assert_eq!(configured_library(&root), None);

        // inside the IA-K folder: stored relative, so it follows the key
        let inside = root.join("bibliotheque");
        save_library(Some(inside.to_str().unwrap())).unwrap();
        let text = fs::read_to_string(root.join("config").join("app.json")).unwrap();
        assert!(text.contains("\"library\": \"bibliotheque\""), "{text}");
        assert_eq!(configured_library(&root).unwrap(), inside);
        assert!(inside.is_dir());

        // elsewhere: stored absolute
        let outside = std::env::temp_dir().join(format!("iak-lib-{}", std::process::id()));
        save_library(Some(outside.to_str().unwrap())).unwrap();
        assert_eq!(configured_library(&root).unwrap(), outside);

        // relative input refused; None goes back to data/
        assert!(save_library(Some("relative/path")).is_err());
        save_library(None).unwrap();
        assert_eq!(configured_library(&root), None);

        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&outside);
    }
}
