mod backend;

use backend::{Backend, Info};
use tauri::{Manager, RunEvent, State};

/// State of the Python backend: the interface polls it until "ready", then
/// talks to the backend's HTTP API with the URL and token.
#[tauri::command]
fn backend_info(backend: State<'_, Backend>) -> Info {
    backend.info.lock().unwrap().clone()
}

/// Stop and start the backend again (after an error, or a settings change).
#[tauri::command]
fn restart_backend(backend: State<'_, Backend>, lang: String) -> Info {
    backend.stop();
    backend.start(if lang == "en" { "en" } else { "fr" });
    backend.info.lock().unwrap().clone()
}

/// Choose the library folder (None: the default data/ folder), then restart
/// the backend on it.
#[tauri::command]
fn set_library(backend: State<'_, Backend>, library: Option<String>, lang: String)
    -> Result<Info, String> {
    backend::save_library(library.as_deref())?;
    backend.stop();
    backend.start(if lang == "en" { "en" } else { "fr" });
    Ok(backend.info.lock().unwrap().clone())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![backend_info, restart_backend, set_library])
        .setup(|app| {
            let lang = std::env::var("IAK_LANG").unwrap_or_else(|_| "fr".into());
            app.state::<Backend>().start(&lang);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building IA-K");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<Backend>().stop();
        }
    });
}
