"""開発用 HTTP サーバ: UI の静的ファイルと JSON-RPC（POST /rpc）。標準ライブラリだけで動く。

    python -m cad.api [--port 8710] [--kernel auto|usd_brep|prism|mock] [--demo [plate|car]] [--no-browser] [--exit-with-stdin] [--shell-windows]

--exit-with-stdin: 標準入力が閉じたら終了する（Tauri の殻から起動されたとき、殻が落ちても残らないように）。
--shell-windows: 文書を別ウィンドウで開くとき、標準出力に "@@agentcad:window <文書ID> <名前>" を出す（殻がウィンドウを作る）。

1 人で使う前提なので、リクエストは 1 本ずつ処理する（文書をロックで守る）。localhost のみで待ち受ける。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..doc.workspace import Workspace
from .rpc import UNLOCKED, Api, RpcError

WEB_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "app", "web"))


def make_handler(api: Api, lock: threading.Lock, web_root: str = WEB_ROOT):
    api.lock = lock                                    # AI エージェントも同じロックで API を呼ぶ（D67）
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):      # 静的ファイルのログは出さない
            if self.command == "POST":
                super().log_message(fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            full = os.path.normpath(os.path.join(web_root, rel))
            if not full.startswith(web_root + os.sep) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if full.endswith(".js"):
                ctype = "text/javascript"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))

        def do_POST(self):
            if self.path != "/rpc":
                return self._send(404, b"not found", "text/plain")
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"error": {"message": "JSON が読めません"}}, ensure_ascii=False).encode("utf-8"),
                                  "application/json; charset=utf-8")
            rid = req.get("id")
            method = req.get("method", "")
            try:
                if method in UNLOCKED:                 # AI の経過を待つ呼び出しはロックの外で（D67）
                    res = {"id": rid, "result": api.call(method, req.get("params"))}
                else:
                    with lock:
                        res = {"id": rid, "result": api.call(method, req.get("params"))}
            except RpcError as e:
                res = {"id": rid, "error": {"message": str(e)}}
            except Exception as e:
                traceback.print_exc()
                res = {"id": rid, "error": {"message": f"内部エラー: {type(e).__name__}: {e}"}}
            self._send(200, json.dumps(res, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    return Handler


def watch_stdin(stop, stream=None):
    """標準入力が閉じる（親プロセスが終わる）まで待って stop() を呼ぶスレッドを始める。"""
    import sys
    stream = stream if stream is not None else sys.stdin.buffer

    def run():
        try:
            while stream.read(1):
                pass
        except (OSError, ValueError):
            pass
        stop()
    t = threading.Thread(target=run, daemon=True, name="stdin-watch")
    t.start()
    return t


def serve(port: int = 8710, kernel: str = "auto", demo: bool | str | None = None, browser: bool = True, exit_with_stdin: bool = False,
          shell_windows: bool = False):
    # パイプに出すとき（殻・テスト）は Windows でも UTF-8 にする。端末に出すときは Python が端末に合わせる
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    ws = Workspace(kernel)
    api = Api(ws)
    if shell_windows:      # Tauri の殻が標準出力のこの行を読んでウィンドウを作る（D50）
        api.window_opener = lambda doc, name: print(f"@@agentcad:window {doc} {name}", flush=True)
    if demo:
        api.call("doc.demo", {"kind": "plate" if demo is True else demo})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api, threading.Lock()))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"agentcad: {url}  （カーネル: {ws.kernel_name}、終了は Ctrl+C）", flush=True)
    if exit_with_stdin:
        watch_stdin(httpd.shutdown)
    if browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m cad.api")
    p.add_argument("--port", type=int, default=8710)
    p.add_argument("--kernel", default="auto")
    p.add_argument("--demo", nargs="?", const="plate", default=None, help="起動時にデモ部品を開く（--demo car で車）")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--exit-with-stdin", action="store_true", help="標準入力が閉じたら終了する（Tauri 用）")
    p.add_argument("--shell-windows", action="store_true", help="新しいウィンドウを殻に作らせる（Tauri 用）")
    a = p.parse_args(argv)
    serve(a.port, a.kernel, a.demo, not a.no_browser, a.exit_with_stdin, a.shell_windows)
