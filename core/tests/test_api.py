import base64
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

from cad.api.rpc import Api, RpcError
from cad.api.server import make_handler
from cad.doc.workspace import Workspace


@pytest.fixture
def api():
    return Api(Workspace("prism"))


def arr(s, dt):
    return np.frombuffer(base64.b64decode(s), dtype=np.dtype(dt).newbyteorder("<"))


def test_demo_state_tree_and_mesh(api):
    st = api.call("doc.demo")
    states = {t["name"]: t["state"] for t in st["tree"]}
    assert states["Extrude1"] == "ok" and states["Extrude2"] == "ok"
    assert states["Extrude3"] == "error" and states["Fillet1"] == "error"     # 簡易カーネルは差・フィレット非対応
    m = st["mesh"]
    pos, idx, fot = arr(m["positions"], np.float32), arr(m["indices"], np.uint32), arr(m["face_of_triangle"], np.uint32)
    assert len(idx) % 3 == 0 and idx.max() < len(pos) // 3 and len(fot) == len(idx) // 3
    assert "Extrude1/end" in m["face_names"] and "Extrude2/end" in m["face_names"]
    seg = arr(m["edges"], np.float32).reshape(-1, 2, 3)
    assert len(seg) == len(arr(m["edge_of_segment"], np.uint32)) and m["edge_names"]
    assert m["bbox"][1] == pytest.approx([100, 60, 35], abs=1e-4)
    assert st["undo"] is None and st["variables"][0]["value"] == 100


def test_edit_param_expr_variable_undo_redo(api):
    did = api.call("doc.demo")["doc"]
    p = api.call("feature.params", {"doc": did, "id": "Sketch1"})
    width = next(x for x in p["params"] if x["expr"] == "width")
    st = api.call("variable.set", {"doc": did, "name": "width", "expr": "150"})
    assert st["mesh"]["bbox"][1][0] == pytest.approx(150, abs=1e-4)
    st = api.call("feature.edit", {"doc": did, "id": "Extrude1", "key": "distance", "expr": "width / 10"})
    assert st["mesh"]["bbox"][1][2] == pytest.approx(15 + 15, abs=1e-4)
    st = api.call("feature.edit", {"doc": did, "id": "Sketch1", "key": width["key"], "value": 80})
    assert st["mesh"]["bbox"][1][0] == pytest.approx(80, abs=1e-4)
    assert st["undo"].startswith("Sketch1")
    st = api.call("edit.undo", {"doc": did})
    assert st["mesh"]["bbox"][1][0] == pytest.approx(150, abs=1e-4) and st["redo"]
    st = api.call("edit.redo", {"doc": did})
    assert st["mesh"]["bbox"][1][0] == pytest.approx(80, abs=1e-4)


def test_suppress_rollback_and_errors(api):
    did = api.call("doc.demo")["doc"]
    st = api.call("feature.suppress", {"doc": did, "id": "Extrude2", "value": True})
    states = {t["name"]: t["state"] for t in st["tree"]}
    assert states["Extrude2"] == "suppressed" and states["Sketch3"] == "error"   # ボスの上面が無い
    st = api.call("rollback.set", {"doc": did, "index": 2})
    assert [t["state"] for t in st["tree"]][2:] == ["rolled_back"] * 5 and st["rollback"] == 2
    st = api.call("rollback.set", {"doc": did, "index": 7})
    assert st["rollback"] is None
    with pytest.raises(RpcError):
        api.call("feature.edit", {"doc": did, "id": "Extrude1", "key": "nope", "value": 1})
    with pytest.raises(RpcError):
        api.call("feature.edit", {"doc": did, "id": "Extrude1", "key": "distance", "expr": "1 +"})
    with pytest.raises(RpcError):
        api.call("doc.state", {"doc": "nope"})
    with pytest.raises(RpcError):
        api.call("no.such")


def test_expression_error_shows_in_tree_not_exception(api):
    did = api.call("doc.demo")["doc"]
    st = api.call("feature.edit", {"doc": did, "id": "Extrude1", "key": "distance", "expr": "undefined_var"})
    e1 = next(t for t in st["tree"] if t["name"] == "Extrude1")
    assert e1["state"] == "error" and "式エラー" in e1["message"]


def test_save_and_open(api, tmp_path):
    did = api.call("doc.demo")["doc"]
    path = api.call("doc.save", {"doc": did, "path": str(tmp_path / "demo")})["path"]
    assert path.endswith(".usda")
    api.call("doc.close", {"doc": did})
    st = api.call("doc.open", {"path": path})
    assert st["name"] == "demo" and st["modified"] is False and st["mesh"]["bbox"][1] == pytest.approx([100, 60, 35], abs=1e-4)


def test_http_roundtrip_and_static_files(tmp_path):
    (tmp_path / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    api = Api(Workspace("prism"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, threading.Lock(), str(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert urllib.request.urlopen(base + "/").read() == b"<h1>ok</h1>"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/../../etc/passwd")

        def rpc(method, params=None):
            req = urllib.request.Request(base + "/rpc", json.dumps({"id": 1, "method": method, "params": params}).encode(),
                                         {"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req).read())
        r = rpc("doc.demo")
        assert r["result"]["tree"][0]["name"] == "Sketch1"
        assert "未知のメソッド" in rpc("x.y")["error"]["message"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_feature_options_delete_and_edge_faces(api):
    did = api.call("doc.demo")["doc"]
    st = api.call("doc.state", {"doc": did})
    m = st["mesh"]
    assert len(m["edge_faces"]) == len(m["edge_names"]) and all(0 <= i < len(m["face_names"]) for p in m["edge_faces"] for i in p)
    p = api.call("feature.params", {"doc": did, "id": "Extrude2"})
    assert p["options"] == {"end": "blind", "op": "add", "reverse": False}
    st = api.call("feature.set", {"doc": did, "id": "Extrude2", "key": "reverse", "value": True})
    assert st["mesh"]["bbox"][1][2] == pytest.approx(20, abs=1e-4)          # ボスが板の中へ向く
    with pytest.raises(RpcError):
        api.call("feature.set", {"doc": did, "id": "Extrude2", "key": "op", "value": "nope"})
    with pytest.raises(RpcError):
        api.call("feature.set", {"doc": did, "id": "Extrude2", "key": "distance", "value": 3})
    st = api.call("feature.delete", {"doc": did, "id": "Fillet1"})
    assert [t["name"] for t in st["tree"]][-1] == "Extrude3" and st["undo"] == "Fillet1 を削除"
    st = api.call("edit.undo", {"doc": did})
    assert [t["name"] for t in st["tree"]][-1] == "Fillet1"


def test_fillet_from_selected_edge_names():
    api = Api(Workspace("mock"))
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [10, 5]]})
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1"})     # 編集中のまま押し出せる
    edge = next(iter(api.ws.get(did).names.edges))
    st = api.call("feature.add", {"doc": did, "type": "fillet", "edges": [edge], "radius": 1})
    assert st["sketch"] is None and [t["state"] for t in st["tree"]] == ["warning", "ok", "ok"]
    assert api.call("feature.params", {"doc": did, "id": "Fillet1"})["options"]["edges"] == [edge]


def test_volume_only_when_shown(monkeypatch):
    """D66: 体積は既定では計算しない（has_body だけ）。ui.settings で切り替える。"""
    monkeypatch.setattr(Api, "DEFAULT_SETTINGS", {"show_volume": False})
    api = Api(Workspace("prism"))
    d = api.call("doc.demo")["doc"]
    st = api.call("doc.state", {"doc": d, "mesh": False})
    assert st["volume"] is None and st["has_body"] is True
    assert api.call("ui.settings", {"show_volume": True}) == {"show_volume": True}
    assert api.call("doc.state", {"doc": d, "mesh": False})["volume"] > 0
    with pytest.raises(RpcError):
        api.call("ui.settings", {"bogus": 1})
