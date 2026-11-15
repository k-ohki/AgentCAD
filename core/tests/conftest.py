"""テスト全体の設定。"""
import pytest

from cad.api.rpc import Api


@pytest.fixture(autouse=True)
def _show_volume(monkeypatch):
    """テストは体積で形を確かめるので、状態に体積を載せる（画面の既定は載せない。D66）。"""
    monkeypatch.setattr(Api, "DEFAULT_SETTINGS", {**Api.DEFAULT_SETTINGS, "show_volume": True})
