"""T（Tauri の殻）のための Python 側: ファイルダイアログの RPC、標準入力が閉じたら終了。"""
import io
import os
import subprocess
import sys
import types
import urllib.request

import pytest

from cad.api import dialogs
from cad.api.rpc import Api
from cad.api.server import watch_stdin
from cad.doc.workspace import Workspace


def fake_run(out=b"", code=0, err=b""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        assert kw.get("stdin") is dialogs.subprocess.DEVNULL     # 殻とのパイプを引き継ぐと Windows で起動が止まる
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)
    return run, calls


def test_pick_path(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTCAD_NO_DIALOG", raising=False)
    run, calls = fake_run("C:/作業/部品.usda".encode("utf-8"))
    monkeypatch.setattr(dialogs.subprocess, "run", run)
    assert dialogs.pick_path("save", str(tmp_path / "a.usda")) == os.path.normpath("C:/作業/部品.usda")
    assert calls[0][0] == sys.executable and calls[0][3:] == ["save", str(tmp_path), "a.usda", "usda"]
    dialogs.pick_path("open", "/no/such/dir/x.usda")
    assert calls[1][3:] == ["open", "", "x.usda", "usda"]                       # 無いフォルダは渡さない
    monkeypatch.setattr(dialogs.subprocess, "run", fake_run(b"")[0])
    assert dialogs.pick_path("open") is None                             # 取り消し
    monkeypatch.setattr(dialogs.subprocess, "run", fake_run(code=1, err=b"x\nModuleNotFoundError: tkinter")[0])
    with pytest.raises(dialogs.DialogUnavailable, match="tkinter"):
        dialogs.pick_path("open")
    with pytest.raises(ValueError):
        dialogs.pick_path("delete")
    compile(dialogs._SCRIPT, "dialog", "exec")                           # 子プロセスのスクリプトが構文として正しい


def test_ui_pick_path_rpc(monkeypatch, tmp_path):
    api = Api(Workspace("prism"))
    did = api.call("doc.new", {"name": "Bracket"})["doc"]
    seen = []
    monkeypatch.setattr(dialogs, "pick_path", lambda mode, initial=None, ext="usda": seen.append((mode, initial)) or str(tmp_path / "b.usda"))
    r = api.call("ui.pick_path", {"mode": "save", "doc": did})
    assert r == {"path": str(tmp_path / "b.usda"), "available": True} and seen[-1] == ("save", "Bracket.usda")
    api.call("ui.pick_path", {"mode": "open"})
    assert seen[-1] == ("open", os.path.join(str(tmp_path), ""))         # 前に選んだフォルダから始める
    api.call("doc.save", {"doc": did, "path": r["path"]})
    api.call("ui.pick_path", {"mode": "save", "doc": did})
    assert seen[-1][1] == str(tmp_path / "b.usda")

    def unavailable(mode, initial=None, ext="usda"):
        raise dialogs.DialogUnavailable("画面がありません")
    monkeypatch.setattr(dialogs, "pick_path", unavailable)
    assert api.call("ui.pick_path", {"mode": "open"}) == {"path": None, "available": False, "reason": "画面がありません"}


def test_watch_stdin_calls_stop_on_eof():
    hit = []
    watch_stdin(lambda: hit.append(1), io.BytesIO(b"abc")).join(2)
    assert hit == [1]


def test_server_exits_when_stdin_closes():
    core = os.path.join(os.path.dirname(__file__), "..")
    p = subprocess.Popen([sys.executable, "-m", "cad.api", "--no-browser", "--exit-with-stdin", "--port", "0", "--kernel", "prism"],
                         cwd=core, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        line = p.stdout.readline().decode("utf-8")
        url = line.split()[1]
        assert b"AgentCAD" in urllib.request.urlopen(url, timeout=5).read()
        p.stdin.close()
        assert p.wait(timeout=10) == 0
    finally:
        if p.poll() is None:
            p.kill()
