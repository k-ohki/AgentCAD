"""フィーチャーの登録と API の対応（足し忘れを見つける）。"""
import pytest

from cad.api.feature_api import MAKERS, OPTION_LABELS, REF_KEYS
from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace
from cad.part.features import REGISTRY


def test_every_feature_has_metadata_and_maker():
    for t, cls in REGISTRY.items():
        assert cls.label and cls.default_name, t
        for key, kind in cls.options.items():
            assert key in cls.__dataclass_fields__, (t, key)
            assert key in OPTION_LABELS or key in cls.option_labels, (t, key)
            assert kind is bool or isinstance(kind, tuple) or kind in REF_KEYS or kind in ("seeds", "faces", "sketch"), (t, key)
    assert set(MAKERS) == set(REGISTRY) - {"sketch", "import"}   # スケッチは sketch.new、インポートは doc.open で作る
    assert len({c.default_name for c in REGISTRY.values()}) == len(REGISTRY)


def test_feature_add_arguments_and_missing_ids():
    api = Api(Workspace("mock"))
    d = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[0, 0], [10, 5]]})
    # ほかの種類の引数（distance）は無視する。どの種類にも無い引数はエラー
    st = api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "radius": 3})
    assert st["created"] == ["Extrude1"]
    with pytest.raises(RpcError):
        api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "nope": 1})
    with pytest.raises(RpcError):
        api.call("feature.add", {"doc": d, "type": "nope"})
    with pytest.raises(RpcError, match="Nope1"):
        api.call("feature.params", {"doc": d, "id": "Nope1"})
