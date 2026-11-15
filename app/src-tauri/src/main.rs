// AgentCAD のデスクトップ用の殻（D41）。
//
// やることは 3 つだけ:
//   1. Python サーバ（python -m cad.api）を空いているポートで起動する
//   2. ウィンドウを出し、サーバが応答したら UI（http://127.0.0.1:<port>/）へ移る
//   3. 終了時にサーバを止める（殻が異常終了しても、サーバは標準入力が閉じたことで終了する）
// ほかに、サーバが標準出力に "@@agentcad:window <文書ID> <名前>" を出したら、その文書のウィンドウを作る
// （部品・アセンブリごとのウィンドウ。D50）。標準出力のほかの行はログへ書く。
// UI と Python の間は HTTP のまま（ブラウザで開くときと同じ）。Tauri の IPC は使わない。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

/// 起動した Python サーバ。
struct Server(Mutex<Option<Child>>);

/// リポジトリのルート（core/cad/api/server.py があるフォルダ）を探す。
/// 環境変数 AGENTCAD_ROOT → 実行ファイルの場所から上へ → 作業フォルダから上へ。
fn find_root() -> Option<PathBuf> {
    let has_core = |dir: &Path| dir.join("core").join("cad").join("api").join("server.py").is_file();
    if let Ok(dir) = std::env::var("AGENTCAD_ROOT") {
        let dir = PathBuf::from(dir);
        if has_core(&dir) {
            return Some(dir);
        }
    }
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    for start in starts {
        for dir in start.ancestors() {
            if has_core(dir) {
                return Some(dir.to_path_buf());
            }
        }
    }
    None
}

/// 設定ファイル <ルート>/agentcad.config.json（無くてもよい）。{"python": "...", "kernel": "auto"}
fn read_config(root: &Path) -> serde_json::Value {
    std::fs::read_to_string(root.join("agentcad.config.json"))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or(serde_json::Value::Null)
}

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

/// Python サーバを起動する。戻り値: (子プロセス, ポート)。失敗したら利用者に見せる説明。
fn start_server(log_path: &Path) -> Result<(Child, u16), String> {
    let root = find_root().ok_or_else(|| {
        "AgentCAD のフォルダ（core\\cad\\api がある場所）が見つかりません。\n\
         リポジトリの中でビルドした agentcad.exe を使うか、環境変数 AGENTCAD_ROOT にリポジトリの場所を設定してください。"
            .to_string()
    })?;
    let config = read_config(&root);
    let python = std::env::var("AGENTCAD_PYTHON")
        .ok()
        .or_else(|| config["python"].as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| "python".to_string());
    let kernel = config["kernel"].as_str().unwrap_or("auto").to_string();
    let port = free_port().map_err(|e| format!("空いているポートを取れません: {e}"))?;
    let port_text = port.to_string();
    // ログは追記で開く（標準エラーはここへ直接、標準出力は read_stdout が書く）
    File::create(log_path).map_err(|e| format!("ログ {} を作れません: {e}", log_path.display()))?;
    let log_err = std::fs::OpenOptions::new().append(true).open(log_path).map_err(|e| format!("ログを開けません: {e}"))?;

    let mut cmd = Command::new(&python);
    cmd.current_dir(root.join("core"))
        .args(["-m", "cad.api", "--no-browser", "--exit-with-stdin", "--shell-windows", "--port", port_text.as_str(), "--kernel", kernel.as_str()])
        .args(std::env::args().skip(1)) // agentcad.exe --demo など、残りの引数はサーバに渡す
        .env("PYTHONUTF8", "1")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log_err));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: コンソールの窓を出さない
    }
    let child = cmd.spawn().map_err(|e| {
        format!(
            "Python を起動できません（{python}）: {e}\n\
             {} に {{\"python\": \"C:\\\\...\\\\python.exe\"}} を書くか、環境変数 AGENTCAD_PYTHON を設定してください。",
            root.join("agentcad.config.json").display()
        )
    })?;
    Ok((child, port))
}

/// サーバの標準出力を読む。"@@agentcad:window" の行でウィンドウを作り、ほかの行はログへ書く。
fn read_stdout(handle: AppHandle, stdout: ChildStdout, log_path: PathBuf, port: u16) {
    let mut log = std::fs::OpenOptions::new().append(true).open(&log_path).ok();
    for line in BufReader::new(stdout).lines() {
        let Ok(line) = line else { break };
        if let Some(rest) = line.strip_prefix("@@agentcad:window ") {
            let (doc, name) = rest.split_once(' ').unwrap_or((rest, rest));
            open_doc_window(&handle, doc, name, port);
        } else if let Some(f) = log.as_mut() {
            let _ = writeln!(f, "{line}");
        }
    }
}

/// 文書のウィンドウを出す。すでにあれば前に出す。
fn open_doc_window(handle: &AppHandle, doc: &str, name: &str, port: u16) {
    let safe: String = doc.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-').collect();
    let label = format!("doc-{safe}");
    if let Some(w) = handle.get_webview_window(&label) {
        let _ = w.unminimize();
        let _ = w.set_focus();
        return;
    }
    let Ok(url) = format!("http://127.0.0.1:{port}/?doc={safe}").parse() else { return };
    let built = WebviewWindowBuilder::new(handle, label, WebviewUrl::External(url))
        .title(format!("AgentCAD - {name}"))
        .inner_size(1280.0, 820.0)
        .min_inner_size(900.0, 600.0)
        .build();
    if let Err(e) = built {
        eprintln!("ウィンドウを作れません: {e}");
    }
}

fn main() {
    let log_path = std::env::temp_dir().join("agentcad-server.log");
    let started = start_server(&log_path);
    let (mut child, port, error) = match started {
        Ok((child, port)) => (Some(child), port, None),
        Err(message) => (None, 0, Some(message)),
    };
    let stdout = child.as_mut().and_then(|c| c.stdout.take());
    let stdout_log = log_path.clone();
    // 起動画面（dist/index.html）に渡す情報。UI のページでは使わない。
    let init = format!(
        "window.__AGENTCAD_SHELL__ = {};",
        serde_json::json!({ "port": port, "error": error, "log": log_path.display().to_string() })
    );

    let app = tauri::Builder::default()
        .manage(Server(Mutex::new(child)))
        .setup(move |app| {
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("AgentCAD")
                .inner_size(1440.0, 900.0)
                .min_inner_size(900.0, 600.0)
                .initialization_script(&init)
                .build()?;
            if port == 0 {
                return Ok(()); // 起動に失敗: 起動画面が説明を出す
            }
            if let Some(out) = stdout {
                let h = app.handle().clone();
                std::thread::spawn(move || read_stdout(h, out, stdout_log, port));
            }
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let addr = SocketAddr::from(([127, 0, 0, 1], port));
                let begun = Instant::now();
                loop {
                    std::thread::sleep(Duration::from_millis(150));
                    if TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
                        let _ = window.eval(&format!("window.location.replace('http://127.0.0.1:{port}/')"));
                        return;
                    }
                    // サーバが応答する前に終わった（Python のエラーなど）か、時間切れ
                    let exited = {
                        let state = handle.state::<Server>();
                        let mut guard = state.inner().0.lock().unwrap();
                        match guard.as_mut() {
                            Some(child) => child.try_wait().ok().flatten().map(|status| status.to_string()),
                            None => return,
                        }
                    };
                    let problem = match exited {
                        Some(status) => Some(format!("サーバが起動せずに終了しました（{status}）")),
                        None if begun.elapsed() > Duration::from_secs(90) => {
                            Some("サーバが 90 秒たっても応答しません".to_string())
                        }
                        None => None,
                    };
                    if let Some(message) = problem {
                        // 起動画面の読み込みを待ってから知らせる
                        std::thread::sleep(Duration::from_millis(800).saturating_sub(begun.elapsed()));
                        let text = serde_json::Value::String(message).to_string();
                        let _ = window.eval(&format!("window.agentcadShellError && window.agentcadShellError({text})"));
                        return;
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("AgentCAD を起動できません");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<Server>();
            let child = state.inner().0.lock().unwrap().take();
            if let Some(mut child) = child {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
