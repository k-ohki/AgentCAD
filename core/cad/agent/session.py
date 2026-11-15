"""AI エージェントの会話（D67）。裏のスレッドでモデルと道具のやり取りを回し、経過をイベントとして貯める。画面は agent.poll で取りに来る。

イベント（画面へ）:
- {type: "user", text, images}: 依頼
- {type: "text", text}: モデルの文（少しずつ届く。続く text は画面でつなげる）/ {type: "thinking", text}: 思考の要約（同じく）
- {type: "tool", name, input}: 道具を呼ぶ / {type: "tool_result", name, text, is_error, image?}: 結果（画像は data URL）
- {type: "doc", doc}: 今の文書が変わった（画面はその文書を出す）/ {type: "changed", doc}: 文書が変わった（画面は描き直す）
- {type: "ui", action, args}: 画面だけの操作（断面など）
- {type: "error", message} / {type: "done", stop}
"""
from __future__ import annotations

import base64
import json
import threading
import traceback
from typing import Any, Callable

from .config import AgentConfig, load_config
from .prompt import SYSTEM_PROMPT
from .providers import ProviderError, ToolOutput, make_provider
from .tools import TOOLS, CadTools


class AgentSession:
    def __init__(self, api, cfg: AgentConfig | None = None, provider_factory: Callable[[AgentConfig], Any] = make_provider):
        self.api = api
        self.cfg = cfg or load_config()
        self.provider_factory = provider_factory
        self.provider = None
        self.tools = CadTools(api, emit=self._emit)
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.thread: threading.Thread | None = None
        self.stop_flag = False

    # ------------------------------------------------------------ 画面から
    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def reset(self):
        """新しい会話にする（実行中なら止まるまで待たない。止める旗だけ立てる）。"""
        self.stop_flag = True
        self.provider = None
        with self.cond:
            self.events = []

    def chat(self, text: str, images: list[tuple[str, bytes]] = (), doc: str | None = None):
        if self.running:
            raise ValueError("AI はまだ作業中です（止めるか、終わるのを待ってください）")
        if not text.strip() and not images:
            raise ValueError("依頼の文を入れてください")
        if self.provider is None:
            self.provider = self.provider_factory(self.cfg)          # 設定の誤り（キー無しなど）はここで分かる
        if doc and doc in self.api.ws.docs:
            self.tools.doc = doc
        self.stop_flag = False
        self._emit({"type": "user", "text": text,
                    "images": [f"data:{mt};base64,{base64.b64encode(b).decode()}" for mt, b in images]})
        self.thread = threading.Thread(target=self._run, args=(text, list(images)), daemon=True, name="agent")
        self.thread.start()

    def stop(self):
        self.stop_flag = True

    def poll(self, since: int = 0, wait: float = 0.0) -> dict:
        """since 番目より後のイベント。wait 秒まで新しいイベントを待つ。"""
        with self.cond:
            if wait and len(self.events) <= since and self.running:
                self.cond.wait(timeout=wait)
            return {"events": self.events[since:], "next": len(self.events), "running": self.running}

    # ------------------------------------------------------------ 裏のスレッド
    def _emit(self, ev: dict):
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    def _context(self, text: str) -> str:
        """依頼の文の最後に、今の文書を添える（システムプロンプトは変えない = キャッシュが効く）。"""
        ws = self.api.ws
        docs = [f"{d}（{ws.names.get(d, '?')}、{ws.kind(d)}）" for d in ws.docs]
        cur = self.tools.doc
        lines = [text, "", "---", f"今の文書: {cur}（{ws.names.get(cur, '?')}、{ws.kind(cur)}）" if cur in ws.docs else "今の文書: なし",
                 f"開いている文書: {', '.join(docs) if docs else 'なし'}", f"カーネル: {ws.kernel_name}"]
        return "\n".join(lines)

    def _run(self, text: str, images: list):
        p = self.provider
        try:
            p.add_user(self._context(text), images)
            for step in range(self.cfg.max_steps):
                if self.stop_flag:
                    self._emit({"type": "done", "stop": "stopped"})
                    return
                turn = p.step(SYSTEM_PROMPT, TOOLS, on_text=lambda s: self._emit({"type": "text", "text": s}),
                              on_thinking=lambda s: self._emit({"type": "thinking", "text": s}))
                if turn.stop == "refusal":
                    self._emit({"type": "error", "message": f"AI が応答を断りました: {turn.detail}"})
                    self._emit({"type": "done", "stop": "refusal"})
                    return
                if turn.stop == "pause_turn":                       # サーバ側の道具（ウェブ検索）の途中: そのまま続ける
                    continue
                if not turn.calls:
                    self._emit({"type": "done", "stop": turn.stop})
                    return
                if turn.stop == "max_tokens":                       # 道具の引数が途中で切れたかもしれない: 実行しない
                    p.add_tool_results([ToolOutput(c.id, "応答が長すぎて途中で切れました。もっと小さく分けて呼んでください",
                                                   is_error=True) for c in turn.calls])
                    continue
                outs = []
                for c in turn.calls:
                    if self.stop_flag:
                        outs.append(ToolOutput(c.id, "利用者が止めました", is_error=True))
                        continue
                    self._emit({"type": "tool", "name": c.name, "input": _short(c.input)})
                    o = self.tools.run(c.name, c.input, c.id)
                    ev = {"type": "tool_result", "name": c.name, "text": o.text[:2000], "is_error": o.is_error}
                    if o.images:
                        ev["image"] = "data:image/png;base64," + base64.b64encode(o.images[0]).decode()
                    self._emit(ev)
                    outs.append(o)
                p.add_tool_results(outs)
            self._emit({"type": "error", "message": f"手順の上限（{self.cfg.max_steps} 回）に達したので止めました"})
            self._emit({"type": "done", "stop": "max_steps"})
        except ProviderError as e:
            self._emit({"type": "error", "message": str(e)})
            self._emit({"type": "done", "stop": "error"})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._emit({"type": "error", "message": f"内部エラー: {type(e).__name__}: {e}"})
            self._emit({"type": "done", "stop": "error"})


def _short(x: Any, n: int = 600) -> str:
    s = json.dumps(x, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"
