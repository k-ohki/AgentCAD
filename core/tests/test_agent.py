"""AI エージェント（D67）。実際の API は呼ばない（台本どおりに答える偽のモデル・偽のクライアントで確かめる）。"""
import json
import threading
import time
import types

import pytest

from cad.agent.config import AgentConfig, load_config, save_config, update_config
from cad.agent.providers import AnthropicProvider, OpenAICompatProvider, ToolCall, ToolOutput, Turn
from cad.agent.session import AgentSession
from cad.agent.tools import TOOLS, CadTools
from cad.api.rpc import Api
from cad.doc.workspace import Workspace


class ScriptedProvider:
    """台本（Turn の列）どおりに答える。受け取った道具の結果を記録する。"""

    def __init__(self, turns):
        self.turns = list(turns)
        self.user = []
        self.results = []

    def add_user(self, text, images=()):
        self.user.append((text, list(images)))

    def add_tool_results(self, outs):
        self.results.append(outs)

    def step(self, system, tools, on_text=lambda s: None, on_thinking=lambda s: None):
        t = self.turns.pop(0)
        if t.text:
            on_text(t.text)
        return t


def run_session(api, turns, text="箱を作って", images=()):
    prov = ScriptedProvider(turns)
    s = AgentSession(api, AgentConfig(api_key="x"), provider_factory=lambda cfg: prov)
    s.chat(text, images)
    s.thread.join(10)
    assert not s.running
    return s, prov, s.poll(0)["events"]


def test_session_builds_a_part_and_drives_the_ui():
    api = Api(Workspace("prism"))
    turns = [
        Turn(calls=[ToolCall("1", "cad_call", {"method": "doc.new", "params": {"name": "Box"}})], stop="tool_use"),
        Turn(text="箱を作ります。", calls=[ToolCall("2", "cad_batch", {"calls": [
            {"method": "sketch.new", "params": {"plane": {"plane": "Front"}}},
            {"method": "sketch.add", "params": {"kind": "polyline", "points": [[0, 0], [40, 0], [40, 20], [0, 20]]}},
            {"method": "sketch.end"},
            {"method": "feature.add", "params": {"type": "extrude", "sketch": "Sketch1", "distance": 10}}]})], stop="tool_use"),
        Turn(calls=[ToolCall("3", "render_view", {"view": "iso", "width": 320, "height": 240}),
                    ToolCall("4", "ui_action", {"action": "section", "args": {"plane": "Front", "offset": 5}})], stop="tool_use"),
        Turn(text="40×20×10 の箱を作りました。", stop="end_turn"),
    ]
    s, prov, ev = run_session(api, turns)
    types_ = [e["type"] for e in ev]
    assert types_[0] == "user" and types_[-1] == "done" and "error" not in types_, ev
    doc = next(e["doc"] for e in ev if e["type"] == "doc")
    st = api.call("doc.state", {"doc": doc, "mesh": False})
    assert st["name"] == "Box" and st["volume"] == pytest.approx(40 * 20 * 10)
    batch = prov.results[1][0]
    assert not batch.is_error and json.loads(batch.text)["ok"] == 4
    assert json.loads(batch.text)["last_state"]["volume"] == pytest.approx(8000)       # 要約に体積が入る
    img = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "render_view")
    assert img["image"].startswith("data:image/png;base64,") and prov.results[2][0].images[0][:8] == b"\x89PNG\r\n\x1a\n"
    assert {"type": "ui", "action": "section", "args": {"plane": "Front", "offset": 5}} in ev
    assert "".join(e["text"] for e in ev if e["type"] == "text").endswith("作りました。")
    assert "今の文書: なし" in prov.user[0][0]                         # 依頼の文に今の文書を添える


def test_tool_errors_are_returned_to_the_model():
    api = Api(Workspace("prism"))
    turns = [Turn(calls=[ToolCall("1", "cad_call", {"method": "no.such"}),
                         ToolCall("2", "cad_batch", {"calls": [{"method": "doc.new"}, {"method": "sketch.add", "params": {"kind": "x"}}]}),
                         ToolCall("3", "run_python", {"code": "print(call('doc.list'))\nresult = 1 + 1"}),
                         ToolCall("4", "bogus", {})], stop="tool_use"),
             Turn(text="完了", stop="end_turn")]
    s, prov, ev = run_session(api, turns)
    r = prov.results[0]
    assert r[0].is_error and "未知のメソッド" in r[0].text
    b = json.loads(r[1].text)
    assert b["stopped_at"] == 1 and "error" in b["results"][1]
    assert not r[2].is_error and "result = 2" in r[2].text
    assert r[3].is_error


def test_stop_and_max_steps():
    api = Api(Workspace("prism"))
    loop = [Turn(calls=[ToolCall(str(i), "api_help", {"method": "doc."})], stop="tool_use") for i in range(5)]
    prov = ScriptedProvider(loop)
    s = AgentSession(api, AgentConfig(api_key="x", max_steps=3), provider_factory=lambda cfg: prov)
    s.chat("x")
    s.thread.join(10)
    ev = s.poll(0)["events"]
    assert any(e["type"] == "error" and "上限" in e["message"] for e in ev)
    with pytest.raises(ValueError):
        s.chat("  ")


def test_api_methods_and_unlocked_poll(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_AGENT_CONFIG", str(tmp_path / "agent.json"))
    api = Api(Workspace("prism"))
    st = api.call("agent.settings", {"api_key": "sk-test-123456", "model": "claude-opus-5-5", "effort": "high"})
    assert st["has_key"] and st["key_hint"] == "…3456" and "api_key" not in st
    assert json.load(open(tmp_path / "agent.json", encoding="utf-8"))["api_key"] == "sk-test-123456"
    prov = ScriptedProvider([Turn(text="こんにちは", stop="end_turn")])
    api.agent.provider_factory = lambda cfg: prov
    api.call("agent.chat", {"text": "hi", "images": [{"media_type": "image/png", "data": "iVBORw0KGgo="}]})
    out = {"events": []}
    for _ in range(50):
        out = api.call("agent.poll", {"since": 0, "wait": 0.2})
        if not out["running"]:
            break
    assert [e["type"] for e in out["events"]][-1] == "done"
    assert prov.user[0][1][0][0] == "image/png"
    with pytest.raises(Exception):
        api.call("agent.settings", {"provider": "nope"})
    # ロックの外: ほかのスレッドがロックを持っていても agent.poll は返る
    from cad.api.rpc import UNLOCKED
    assert "agent.poll" in UNLOCKED


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCAD_AGENT_CONFIG", str(tmp_path / "a.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = load_config()
    assert c.provider == "anthropic" and c.model_name == "claude-opus-5-5" and c.api_key == ""
    update_config(c, {"provider": "openai", "api_key": "k", "base_url": "http://localhost:11434/v1", "model": "llama3"})
    save_config(c)
    c2 = load_config()
    assert (c2.provider, c2.api_key, c2.url, c2.model_name) == ("openai", "k", "http://localhost:11434/v1", "llama3")
    update_config(c2, {"api_key": ""})
    assert c2.api_key == "k"                                  # 空文字は変えない
    update_config(c2, {"api_key": "-"})
    assert c2.api_key == ""


# ---------------------------------------------------------------- 接続先（偽のクライアント）


class FakeStream:
    def __init__(self, events, final):
        self.events, self.final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


def test_anthropic_request_and_parsing():
    seen = {}
    final = types.SimpleNamespace(stop_reason="tool_use", stop_details=None, content=[
        types.SimpleNamespace(type="thinking", thinking="…"),
        types.SimpleNamespace(type="text", text="作ります"),
        types.SimpleNamespace(type="tool_use", id="tu1", name="cad_call", input={"method": "doc.new"})])

    def stream(**params):
        seen.update(params)
        return FakeStream([types.SimpleNamespace(type="text", text="作り"), types.SimpleNamespace(type="text", text="ます"),
                           types.SimpleNamespace(type="thinking", thinking="考え中")], final)
    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream)))
    p = AnthropicProvider(AgentConfig(api_key="k", effort="high"), client=client)
    p.add_user("箱", [("image/png", b"\x89PNG")])
    texts, thoughts = [], []
    t = p.step("SYS", TOOLS, on_text=texts.append, on_thinking=thoughts.append)
    assert seen["model"] == "claude-opus-5-5" and seen["max_tokens"] == 64000
    assert seen["thinking"] == {"type": "adaptive", "display": "summarized"} and seen["output_config"] == {"effort": "high"}
    assert seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"]
    assert seen["cache_control"] == {"type": "ephemeral"} and seen["system"][0]["text"] == "SYS"
    assert all(x.get("eager_input_streaming") for x in seen["tools"] if "input_schema" in x)
    assert any(x.get("type") == "web_search_20260209" for x in seen["tools"])
    assert seen["messages"][0]["content"][0]["type"] == "image"
    assert texts == ["作り", "ます"] and thoughts == ["考え中"]
    assert t.stop == "tool_use" and t.text == "作ります" and t.calls[0].name == "cad_call"
    assert p.messages[-1] == {"role": "assistant", "content": final.content}      # 思考のブロックもそのまま返す
    p.add_tool_results([ToolOutput("tu1", "ok", [b"\x89PNG"])])
    blk = p.messages[-1]["content"][0]
    assert blk["type"] == "tool_result" and blk["tool_use_id"] == "tu1" and blk["content"][0]["type"] == "image"
    # 代替の無いモデル・effort なしでは送らない
    q = AnthropicProvider(AgentConfig(api_key="k", model="claude-haiku-4-5", effort="", web_search=False), client=client)
    prm = q.params("S", TOOLS)
    assert "fallbacks" not in prm and "output_config" not in prm and not any(x.get("type") == "web_search_20260209" for x in prm["tools"])


def test_openai_compatible_tool_roundtrip():
    calls = []

    def post(url, body, headers):
        calls.append((url, body, headers))
        if len(calls) == 1:
            return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "cad_call", "arguments": json.dumps({"method": "doc.list"})}}]}}]}
        return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "完了"}}]}
    p = OpenAICompatProvider(AgentConfig(provider="openai", api_key="k", model="gpt-x"), post=post)
    p.add_user("やって", [("image/png", b"\x89PNG")])
    t = p.step("SYS", TOOLS)
    url, body, headers = calls[0]
    assert url == "https://api.openai.com/v1/chat/completions" and headers["Authorization"] == "Bearer k"
    assert body["model"] == "gpt-x" and body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["tools"][0]["function"]["name"] == "cad_call" and body["messages"][1]["content"][0]["type"] == "image_url"
    assert t.stop == "tool_use" and t.calls[0].input == {"method": "doc.list"}
    p.add_tool_results([ToolOutput("c1", "[]", [b"\x89PNG"])])
    assert p.messages[-2]["role"] == "tool" and p.messages[-1]["content"][0]["type"] == "image_url"
    t = p.step("SYS", TOOLS)
    assert t.stop == "end_turn" and t.text == "完了"


def test_cad_tools_defaults_to_current_doc():
    api = Api(Workspace("prism"))
    events = []
    t = CadTools(api, emit=events.append)
    t.call("doc.new", {"name": "A"})
    assert t.doc and events[-1] == {"type": "doc", "doc": t.doc}
    st = t.call("doc.state", {"mesh": False})                    # doc を省くと今の文書
    assert st["name"] == "A"
    out = t.run("api_help", {"method": "sketch.add"}, "x")
    assert "polyline" in out.text and not out.is_error


def test_anthropic_sdk_against_local_fake_server(tmp_path):
    """本物の SDK（anthropic）で、手元の偽の API サーバへ要求を送る。送る JSON・ヘッダと、ストリーミング（SSE）の読み取りを確かめる。"""
    pytest.importorskip("anthropic")
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = {}

    def sse(name, data):
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"
    body = "".join([
        sse("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0}}}),
        sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "作り"}}),
        sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ます"}}),
        sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse("content_block_start", {"type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "cad_call", "input": {}}}),
        sse("content_block_delta", {"type": "content_block_delta", "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": "{\"method\": \"doc.new\", \"params\": {\"name\": \"A\"}}"}}),
        sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
        sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 20}}),
        sse("message_stop", {"type": "message_stop"}),
    ]).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["path"] = self.path
            seen["headers"] = dict(self.headers)
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = AgentConfig(api_key="sk-test", base_url=f"http://127.0.0.1:{srv.server_address[1]}", effort="high")
        p = AnthropicProvider(cfg)
        p.add_user("作って")
        texts = []
        t = p.step("SYS", TOOLS, on_text=texts.append)
    finally:
        srv.shutdown()
    b, h = seen["body"], {k.lower(): v for k, v in seen["headers"].items()}
    assert seen["path"].startswith("/v1/messages"), seen["path"]
    assert h.get("x-api-key") == "sk-test" or h.get("authorization") == "Bearer sk-test", sorted(h)
    assert "server-side-fallback-2026-07-01" in h.get("anthropic-beta", "")
    assert b["model"] == "claude-opus-5-5" and b["stream"] is True and b["fallbacks"] == "default"
    assert b["thinking"] == {"type": "adaptive", "display": "summarized"} and b["output_config"] == {"effort": "high"}
    assert b["cache_control"] == {"type": "ephemeral"} and b["tools"][0]["eager_input_streaming"] is True
    assert texts == ["作り", "ます"] and t.stop == "tool_use" and t.text == "作ります"
    assert t.calls[0].name == "cad_call" and t.calls[0].input == {"method": "doc.new", "params": {"name": "A"}}
    p.add_tool_results([ToolOutput("toolu_1", "ok")])
    assert p.messages[1]["role"] == "assistant" and p.messages[2]["content"][0]["tool_use_id"] == "toolu_1"


def test_clip_triangles_keeps_far_side():
    """render_view の section（D68）: n·p <= offset の側だけを残し、またぐ三角形は切る。"""
    import numpy as np
    from cad.agent.render import clip_triangles
    tris = np.array([[[0, 0, -1], [1, 0, -1], [0, 1, -2]],        # 全部奥 → そのまま
                     [[0, 0, 1], [1, 0, 1], [0, 1, 2]],           # 全部手前 → 消える
                     [[0, 0, -1], [1, 0, -1], [0, 1, 1]]], float)  # またぐ → 切る（頂点 2 つが奥: 四角形 → 2 枚）
    t, f = clip_triangles(tris, np.array([7, 8, 9]), (0, 0, 1), 0.0)
    assert sorted(f.tolist()) == [7, 9, 9] and (t[..., 2] <= 1e-12).all()
    t2, _ = clip_triangles(tris, None, (0, 0, -1), 0.0)            # 反対側（flip）
    assert (t2[..., 2] >= -1e-12).all() and len(t2) == 2
