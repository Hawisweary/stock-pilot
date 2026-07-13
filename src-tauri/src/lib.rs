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
            // prod 模式：dev 热更新易崩且曾致旧构建/WebKit报错问题
            let child = Command::new("bash")
                .args([&launch_sh, "daemon", "prod"])
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
                // 关窗口只关窗口：后端/前端/保活留在后台继续运行，
                // 否则 02:00/15:30 的调度任务在窗口关闭期间全部丢失
                // （曾是"调度器没跑/服务神秘停机"的总根源）。
                // 需要彻底停止服务时手动执行 ./launch.sh stop。
                let state = window.state::<ServerProcesses>();
                for child in state.0.lock().unwrap().iter_mut() {
                    let _ = child.kill(); // 仅回收启动用的bash子进程
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
