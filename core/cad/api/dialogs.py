"""OS のファイルダイアログ（開く / 名前を付けて保存）。

別プロセスの Python で tkinter のダイアログを出す（D40）。ブラウザでも Tauri でも同じに動き、
サーバのスレッドで GUI を扱わずに済む。tkinter や画面が無い環境では DialogUnavailable（UI はパスの入力欄に切り替える）。
"""
from __future__ import annotations

import os
import subprocess
import sys

_SCRIPT = r"""
import sys, tkinter
from tkinter import filedialog
mode, folder, name, ext = sys.argv[1:5]
root = tkinter.Tk(); root.withdraw(); root.attributes("-topmost", True); root.update()
types = [("AgentCAD (USD)", "*.usda"), ("メッシュ（STL / 3MF / OBJ）", "*.stl *.3mf *.obj"), ("旧形式", "*.ucpart *.ucasm"),
         ("すべて", "*.*")]
if mode == "open":
    types.insert(0, ("開けるファイル", "*.usda *.stl *.3mf *.obj *.ucpart *.ucasm"))
kw = dict(parent=root, initialdir=folder or None)
if mode == "open":
    p = filedialog.askopenfilename(title="開く", filetypes=types, **kw)
elif ext != "usda":
    p = filedialog.asksaveasfilename(title="書き出し", filetypes=[(ext.upper(), "*." + ext)], defaultextension="." + ext,
                                     initialfile=name or None, **kw)
else:
    p = filedialog.asksaveasfilename(title="名前を付けて保存", filetypes=types[:1], defaultextension=".usda",
                                     initialfile=name or None, **kw)
sys.stdout.buffer.write((p or "").encode("utf-8"))
"""


class DialogUnavailable(Exception):
    pass


def pick_path(mode: str, initial: str | None = None, ext: str = "usda", timeout: float = 900) -> str | None:
    """mode: "open" / "save"。initial: 最初に見せるパス（フォルダとファイル名）。取り消したら None。"""
    if mode not in ("open", "save"):
        raise ValueError(f"未知の mode: {mode}")
    if ext not in ("usda", "pdf", "dxf", "stl", "3mf", "obj"):
        raise ValueError(f"未知の拡張子: {ext}")
    if os.environ.get("AGENTCAD_NO_DIALOG"):
        raise DialogUnavailable("ファイルダイアログは無効です（AGENTCAD_NO_DIALOG）")
    folder, name = os.path.split(initial) if initial else ("", "")
    if folder and not os.path.isdir(folder):
        folder = ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)       # Windows: コンソールの窓を出さない
    try:
        # stdin は渡さない: デスクトップ版ではサーバの stdin は殻とのパイプで、終了の合図を待つスレッドが読み続けている。
        # Windows では読み込み中のパイプを引き継いだ子の Python が起動の途中で待たされ、本体を閉じるまでダイアログが出ない
        r = subprocess.run([sys.executable, "-c", _SCRIPT, mode, folder, name, ext], capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DialogUnavailable(f"ファイルダイアログを開けません: {e}")
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
        raise DialogUnavailable(f"ファイルダイアログを開けません: {tail[0]}")
    path = r.stdout.decode("utf-8", "replace").strip()
    return os.path.normpath(path) if path else None
