"""AI エージェントの接続先（D67）。会話の履歴は接続先ごとの形で持ち、共通の口（add_user・step・add_tool_results）で使う。

- AnthropicProvider: 公式 SDK（anthropic）。既定は claude-opus-5-5。ストリーミングで文字を少しずつ画面へ流し、
  思考は要約を表示（display: summarized）、プロンプトキャッシュ（cache_control）、拒否されたときのサーバ側の
  代替（fallbacks: "default"）を使う
- OpenAICompatProvider: Chat Completions 互換の HTTP API（OpenAI・Ollama・LM Studio・OpenRouter など）。標準ライブラリの
  urllib で呼ぶ（追加の依存なし）。関数呼び出し（tools）を使う
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import AgentConfig

#: サーバ側の代替（fallbacks: "default"）が使える Claude のモデル
FALLBACK_MODELS = ("claude-opus-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5-5")


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    """モデルの 1 回の応答。"""
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end_turn"               # end_turn / tool_use / max_tokens / refusal / pause_turn
    detail: str = ""                     # 拒否の理由など


@dataclass
class ToolOutput:
    """道具の結果。images は PNG のバイト列（モデルに見せる画像）。"""
    id: str
    text: str
    images: list[bytes] = field(default_factory=list)
    is_error: bool = False


class ProviderError(Exception):
    pass


def make_provider(cfg: AgentConfig):
    if cfg.provider == "openai":
        return OpenAICompatProvider(cfg)
    return AnthropicProvider(cfg)


# ---------------------------------------------------------------- Claude（公式 SDK）


class AnthropicProvider:
    def __init__(self, cfg: AgentConfig, client: Any = None):
        self.cfg = cfg
        if client is None:
            import anthropic
            if not cfg.api_key:
                raise ProviderError("Claude の API キーが設定されていません（AI パネルの ⚙ から入力してください）")
            client = anthropic.Anthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)
        self.client = client
        self.messages: list[dict] = []

    def add_user(self, text: str, images: list[tuple[str, bytes]] = ()):
        content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(b).decode()}}
                   for mt, b in images]
        content.append({"type": "text", "text": text})
        self.messages.append({"role": "user", "content": content})

    def add_tool_results(self, outs: list[ToolOutput]):
        blocks = []
        for o in outs:
            content: list[dict] = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                 "data": base64.b64encode(b).decode()}} for b in o.images]
            content.append({"type": "text", "text": o.text or "(結果なし)"})
            blocks.append({"type": "tool_result", "tool_use_id": o.id, "content": content, "is_error": o.is_error})
        self.messages.append({"role": "user", "content": blocks})        # 全部の結果を 1 つの user にまとめる

    def params(self, system: str, tools: list[dict]) -> dict:
        """1 回分の要求（テストで中身を確かめられるように分けておく）。"""
        cfg, model = self.cfg, self.cfg.model_name
        tool_defs = [{**t, "eager_input_streaming": True} for t in tools]
        if cfg.web_search:
            tool_defs.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 5})
        p: dict[str, Any] = {
            "model": model, "max_tokens": 64000,
            "system": [{"type": "text", "text": system}],
            "messages": self.messages, "tools": tool_defs,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "cache_control": {"type": "ephemeral"},                     # 道具・システム・履歴の前の方をキャッシュ
        }
        if cfg.effort:
            p["output_config"] = {"effort": cfg.effort}
        if model in FALLBACK_MODELS:                                   # 拒否されたら別のモデルで続ける（サーバ側）
            p["betas"] = ["server-side-fallback-2026-07-01"]
            p["fallbacks"] = "default"
        return p

    def step(self, system: str, tools: list[dict], on_text: Callable[[str], None] = lambda s: None,
             on_thinking: Callable[[str], None] = lambda s: None) -> Turn:
        import anthropic
        params = self.params(system, tools)
        for attempt in range(2):
            try:
                with self.client.beta.messages.stream(**params) as stream:
                    for ev in stream:
                        if ev.type == "text":
                            on_text(ev.text)
                        elif ev.type == "thinking":
                            on_thinking(ev.thinking)
                    msg = stream.get_final_message()
                break
            except ValueError:            # 道具の引数の JSON が壊れていた（eager_input_streaming）。もう一度だけ頼む
                if attempt:
                    raise ProviderError("道具の引数を読めませんでした（2 回）")
            except anthropic.AuthenticationError:
                raise ProviderError("API キーが正しくありません")
            except anthropic.NotFoundError as e:
                raise ProviderError(f"モデルか接続先が見つかりません: {e.message}")
            except anthropic.RateLimitError:
                raise ProviderError("利用の上限に達しました。少し待ってからもう一度試してください")
            except anthropic.APIStatusError as e:
                raise ProviderError(f"API のエラー（{e.status_code}）: {e.message}")
            except anthropic.APIConnectionError:
                raise ProviderError("API に接続できません（ネットワークを確かめてください）")
        self.messages.append({"role": "assistant", "content": msg.content})   # 思考・代替のブロックも含めてそのまま返す
        turn = Turn(stop=msg.stop_reason or "end_turn")
        for b in msg.content:
            if b.type == "text":
                turn.text += b.text
            elif b.type == "tool_use":
                turn.calls.append(ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else {}))
        if turn.stop == "refusal":
            d = getattr(msg, "stop_details", None)
            turn.detail = getattr(d, "explanation", "") or "安全のため応答を断りました"
        return turn


# ---------------------------------------------------------------- OpenAI 互換（Chat Completions）


class OpenAICompatProvider:
    def __init__(self, cfg: AgentConfig, post: Callable[[str, dict, dict], dict] | None = None):
        self.cfg = cfg
        self.messages: list[dict] = []
        self._post = post or self._http_post

    def add_user(self, text: str, images: list[tuple[str, bytes]] = ()):
        if images:
            parts: Any = [{"type": "image_url", "image_url": {"url": f"data:{mt};base64,{base64.b64encode(b).decode()}"}}
                          for mt, b in images] + [{"type": "text", "text": text}]
        else:
            parts = text
        self.messages.append({"role": "user", "content": parts})

    def add_tool_results(self, outs: list[ToolOutput]):
        imgs = []
        for o in outs:
            self.messages.append({"role": "tool", "tool_call_id": o.id,
                                  "content": ("エラー: " if o.is_error else "") + (o.text or "(結果なし)")})
            imgs += o.images
        if imgs:                          # 道具の結果に画像を入れられないので、続く user に入れる
            self.messages.append({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b).decode()}}
                for b in imgs] + [{"type": "text", "text": "（上の道具の結果の画像）"}]})

    def body(self, system: str, tools: list[dict]) -> dict:
        return {"model": self.cfg.model_name,
                "messages": [{"role": "system", "content": system}] + self.messages,
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["input_schema"]}} for t in tools]}

    def step(self, system: str, tools: list[dict], on_text: Callable[[str], None] = lambda s: None,
             on_thinking: Callable[[str], None] = lambda s: None) -> Turn:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        res = self._post(self.cfg.url.rstrip("/") + "/chat/completions", self.body(system, tools), headers)
        try:
            ch = res["choices"][0]
            m = ch["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"応答を読めません: {str(res)[:300]}")
        self.messages.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls")})
        turn = Turn(text=m.get("content") or "", stop=ch.get("finish_reason") or "stop")
        if turn.text:
            on_text(turn.text)
        for c in m.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {"__invalid_json__": c["function"].get("arguments")}
            turn.calls.append(ToolCall(c["id"], c["function"]["name"], args if isinstance(args, dict) else {}))
        turn.stop = "tool_use" if turn.calls else ("max_tokens" if turn.stop == "length" else "end_turn")
        return turn

    @staticmethod
    def _http_post(url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"API のエラー（{e.code}）: {e.read().decode('utf-8', 'replace')[:500]}")
        except urllib.error.URLError as e:
            raise ProviderError(f"API に接続できません: {e.reason}")
