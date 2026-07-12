use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::Manager;

pub struct ServerProcesses(pub Mutex<Vec<Child>>);

fn port_ready(port: u16) -> bool {
    TcpStream::connect(("127.0.0.1", port)).is_ok()
}

fn wait_and_navigate(handle: tauri::AppHandle, port: u16, label: &'static str) {
    thread::spawn(move || {
        let start = Instant::now();
        let timeout = Duration::from_secs(90);
        while start.elapsed() < timeout {
            if port_ready(port) {
                if let Some(w) = handle.get_webview_window("main") {
                    let url = format!("http://localhost:{}", port);
                    let _ = w.eval(&format!("window.location.href = '{}'", url));
                }
                return;
            }
            thread::sleep(Duration::from_millis(400));
        }
        if let Some(w) = handle.get_webview_window("main") {
            let _ = w.eval(&format!(
                "document.getElementById('status').textContent = '{} failed to start'",
                label
            ));
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/harrywang".to_string());
    let project_dir = format!("{}/WorkBuddy/2026-05-18-task-7/ai-fundamental-researcher", home);
    let launch_sh = format!("{}/launch.sh", project_dir);

    tauri::Builder::default()
        .manage(ServerProcesses(Mutex::new(vec![])))
        .setup(move |app| {
            // launch.sh daemon starts both FastAPI backend (:8800) and Next.js frontend (:3002)
            let child = Command::new("bash")
                .args([&launch_sh, "daemon", "dev"])
                .current_dir(&project_dir)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("failed to start launch.sh");

            app.state::<ServerProcesses>().0.lock().unwrap().push(child);
            // Wait for the Next.js frontend — it comes up last
            wait_and_navigate(app.handle().clone(), 3002, "AFR");
            Ok(())
        })
        .on_window_event(move |window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/harrywang".to_string());
                let project_dir = format!("{}/WorkBuddy/2026-05-18-task-7/ai-fundamental-researcher", home);
                let launch_sh = format!("{}/launch.sh", project_dir);

                // kill child pids tracked by launch.sh
                let _ = Command::new("bash")
                    .args([&launch_sh, "stop"])
                    .current_dir(&project_dir)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn();

                // also kill the direct child
                let state = window.state::<ServerProcesses>();
                for child in state.0.lock().unwrap().iter_mut() {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
