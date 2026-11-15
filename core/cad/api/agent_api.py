"""AI エージェントの API（D67）。会話は 1 つ（個人用なので、サーバに 1 つの会話を持つ）。"""
from __future__ import annotations

import base64

from ..agent.config import save_config, update_config


class AgentApi:
    """Api に混ぜて使う。"""

    def _register_agent(self):
        self._agent = None
        self.methods.update({
            "agent.settings": self.agent_settings, "agent.chat": self.agent_chat, "agent.poll": self.agent_poll,
            "agent.stop": self.agent_stop, "agent.reset": self.agent_reset,
        })

    @property
    def agent(self):
        if self._agent is None:
            from ..agent.session import AgentSession
            self._agent = AgentSession(self)
        return self._agent

    def agent_settings(self, **values):
        """AI の設定を変えて（引数なしなら読むだけ）返す。provider / api_key（空文字は変えない、"-" で消す）/ model / base_url /
        effort / web_search / max_steps。保存先は ~/.agentcad/agent.json。接続先・キー・モデルを変えると会話は新しくなる。"""
        ag = self.agent
        if values:
            renew = any(k in values for k in ("provider", "api_key", "model", "base_url"))
            update_config(ag.cfg, values)
            save_config(ag.cfg)
            if renew:
                ag.reset()
        return {**ag.cfg.public(), "running": ag.running}

    def agent_chat(self, text: str, images: list | None = None, doc: str | None = None):
        """依頼を送る（裏で動き始めてすぐ返る。経過は agent.poll）。images: [{media_type, data（base64）}]。doc: 今の文書。"""
        imgs = []
        for im in images or []:
            mt = im.get("media_type", "image/png")
            if mt not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                raise ValueError(f"使えない画像の形式: {mt}")
            imgs.append((mt, base64.b64decode(im["data"])))
        self.agent.chat(text, imgs, doc)
        return {"running": True}

    def agent_poll(self, since: int = 0, wait: float = 0.0):
        """since 番目より後のイベントと、次の番号・実行中か。wait 秒まで新しいイベントを待つ（ロックの外で呼ばれる）。"""
        return self.agent.poll(int(since), min(float(wait), 25.0))

    def agent_stop(self):
        self.agent.stop()
        return {"running": self.agent.running}

    def agent_reset(self):
        """新しい会話にする。"""
        self.agent.reset()
        return {"running": self.agent.running}
