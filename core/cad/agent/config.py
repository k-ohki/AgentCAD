"""AI エージェントの設定（D67）。個人用なので、API キーもそのまま JSON に保存する（利用者の指示: セキュリティは気にしない）。

保存先: 環境変数 AGENTCAD_AGENT_CONFIG があればそのファイル、なければ ~/.agentcad/agent.json。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

#: 接続先の種類 → 既定のモデル・接続先 URL
PROVIDERS = {
    "anthropic": {"label": "Claude（Anthropic）", "model": "claude-opus-5-5", "base_url": ""},
    "openai": {"label": "OpenAI 互換（ChatGPT・Ollama・LM Studio など）", "model": "gpt-5", "base_url": "https://api.openai.com/v1"},
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass
class AgentConfig:
    provider: str = "anthropic"
    api_key: str = ""
    model: str = ""                      # 空なら PROVIDERS の既定
    base_url: str = ""                   # 空なら PROVIDERS の既定（anthropic は SDK の既定）
    effort: str = "high"                 # Claude の output_config.effort（空なら送らない）
    web_search: bool = True              # Claude のウェブ検索（サーバ側のツール）を使えるようにする
    max_steps: int = 60                  # 1 回の依頼でモデルを呼ぶ回数の上限
    extra: dict = field(default_factory=dict)

    @property
    def model_name(self) -> str:
        return self.model or PROVIDERS.get(self.provider, PROVIDERS["anthropic"])["model"]

    @property
    def url(self) -> str:
        return self.base_url or PROVIDERS.get(self.provider, PROVIDERS["anthropic"])["base_url"]

    def public(self) -> dict:
        """画面に返す形。API キーは末尾 4 文字だけ見せる（表示のため。保存はそのまま）。"""
        d = asdict(self)
        k = d.pop("api_key")
        d["has_key"] = bool(k)
        d["key_hint"] = ("…" + k[-4:]) if len(k) >= 8 else ("設定済み" if k else "")
        d["model_name"] = self.model_name
        d["providers"] = {p: v["label"] for p, v in PROVIDERS.items()}
        d["efforts"] = list(EFFORTS)
        return d


def config_path() -> str:
    return os.environ.get("AGENTCAD_AGENT_CONFIG") or os.path.join(os.path.expanduser("~"), ".agentcad", "agent.json")


def load_config() -> AgentConfig:
    try:
        with open(config_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    cfg = AgentConfig()
    for k, v in raw.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    if not cfg.api_key:                  # 環境変数のキーでもよい
        cfg.api_key = os.environ.get("ANTHROPIC_API_KEY" if cfg.provider == "anthropic" else "OPENAI_API_KEY", "")
    return cfg


def save_config(cfg: AgentConfig) -> None:
    p = config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=1)


def update_config(cfg: AgentConfig, values: dict) -> AgentConfig:
    """画面からの変更を当てる。api_key は空文字なら変えない（"-" で消す）。"""
    for k, v in values.items():
        if k == "api_key":
            if v == "-":
                cfg.api_key = ""
            elif v:
                cfg.api_key = str(v).strip()
            continue
        if k == "provider" and v not in PROVIDERS:
            raise ValueError(f"未知の接続先: {v}")
        if k == "effort" and v not in ("",) + EFFORTS:
            raise ValueError(f"未知の effort: {v}")
        if k in ("max_steps",):
            v = max(1, int(v))
        if k == "web_search":
            v = bool(v)
        if not hasattr(cfg, k) or k in ("extra",):
            raise ValueError(f"未知の設定: {k}")
        setattr(cfg, k, v)
    return cfg
