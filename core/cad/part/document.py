"""パートドキュメント: フィーチャーツリーと再生成エンジン。

再生成の規則（一般的な 3D CAD に準拠）
- フィーチャーは上から順に評価。ロールバックバーより下は評価しない
- 抑制されたフィーチャーは飛ばす（ボディはそのまま次へ）
- 失敗したフィーチャーはエラーを記録し、ボディは素通しにして続行する（ツリー全体は壊れない）
- 差分再生成: 各フィーチャーの「定義」と「依存先の結果バージョン」が前回と同じなら結果を再利用
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import logging

from ..doc.expr import ExprError, Variables
from ..kernel.base import KernelError
from ..doc.history import History
from ..naming import DanglingReference
from . import appearance
from .features import REGISTRY, BuildContext, Feature, FeatureError, FeatureResult

log = logging.getLogger(__name__)

@dataclass
class _Cache:
    key: tuple
    result: FeatureResult
    version: int


@dataclass
class FeatureStatus:
    state: str                    # ok / warning / error / suppressed / rolled_back
    message: str | None = None
    rebuilt: bool = False


class PartDocument(History):
    def __init__(self, kernel):
        super().__init__()
        self.kernel = kernel
        self.features: list[Feature] = []
        self.rollback: int | None = None          # None = 末尾（全評価）
        #: 表示用の一時ロールバック（スケッチ編集中など）。保存・Undo の対象外
        self.view_rollback: int | None = None
        self._cache: dict[str, _Cache] = {}
        self._version = 0
        self.status: dict[str, FeatureStatus] = {}
        self.body = None
        self.names = None
        self.results: dict[str, FeatureResult] = {}
        self.variables = Variables()              # グローバル変数（名前 → 式）
        self.var_values: dict[str, float] = {}
        self.var_errors: dict[str, str] = {}
        self.appearance = appearance.empty()      # 外観（色。D69）
        #: 再生成の進み具合を知らせる関数 (済んだ数, 全体, いまのフィーチャーの名前)。ファイルを開くときに Workspace が入れる（D69）
        self.progress = None

    # ------------------------------------------------------------ 編集
    def _new_id(self, type_):
        n = 1
        ids = {f.id for f in self.features}
        base = REGISTRY[type_].default_name
        while f"{base}{n}" in ids:
            n += 1
        return f"{base}{n}"

    def add(self, feature: Feature) -> str:
        """ロールバックバーの位置に挿入する。"""
        if not feature.id:
            feature.id = self._new_id(feature.type)
        if not feature.name:
            feature.name = feature.id
        pos = len(self.features) if self.rollback is None else self.rollback
        self.features.insert(pos, feature)
        if self.rollback is not None:
            self.rollback += 1
        return feature.id

    def index(self, fid: str) -> int:
        """ツリーでの位置。無ければ KeyError。"""
        for i, f in enumerate(self.features):
            if f.id == fid:
                return i
        raise KeyError(f"フィーチャー {fid} がありません")

    def get(self, fid: str) -> Feature:
        return self.features[self.index(fid)]

    def context(self) -> BuildContext:
        """今の再生成の結果（最後のボディ・名前・各フィーチャーの結果）を見る文脈。再生成の外で参照を解決するのに使う
        （スケッチ平面の解決、エンティティ変換、外部参照など）。"""
        return BuildContext(self.kernel, body=self.body, names=self.names, results=self.results,
                            features={f.id: f for f in self.features})

    def delete(self, fid: str):
        i = self.index(fid)
        self.features.pop(i)
        if self.rollback is not None and i < self.rollback:
            self.rollback -= 1

    def move(self, fid: str, new_index: int):
        """フィーチャーを new_index の位置へ移す（依存関係が逆転したものは再生成でエラー表示になる）。"""
        f = self.get(fid)
        if not 0 <= new_index < len(self.features):
            raise ValueError("移動先が範囲外です")
        self.features.remove(f)
        self.features.insert(new_index, f)

    def set_rollback(self, index: int | None):
        self.rollback = index

    # ------------------------------------------------------------ 再生成
    def regenerate(self) -> dict[str, FeatureStatus]:
        ctx = BuildContext(self.kernel)
        ctx.features = {f.id: f for f in self.features}
        prev_body_fid = None
        versions: dict[str, int] = {}
        status: dict[str, FeatureStatus] = {}
        self.var_values, self.var_errors = self.variables.evaluate()
        dims: dict[str, float] = {}               # "<フィーチャーID>.<パラメータ>" → 値（上から順に確定）

        def lookup(name: str) -> float:
            if name in self.var_values:
                return self.var_values[name]
            if name in self.var_errors:
                raise ExprError(f"変数 {name} がエラーです: {self.var_errors[name]}")
            if name in dims:
                return dims[name]
            raise ExprError(f"{name} は未定義です（寸法は自分より上のフィーチャーのものだけ参照できます）")
        for i, f in enumerate(self.features):
            if (self.rollback is not None and i >= self.rollback) or \
                    (self.view_rollback is not None and i >= self.view_rollback):
                status[f.id] = FeatureStatus("rolled_back"); continue
            expr_error = None
            try:
                f.apply_exprs(lookup)
            except ExprError as e:
                expr_error = str(e)
            dims.update({f"{f.id}.{k}": v for k, v in f.dims().items()})
            if f.suppressed:
                status[f.id] = FeatureStatus("suppressed"); continue
            if expr_error:
                self._cache.pop(f.id, None)
                ctx.results[f.id] = FeatureResult(error=f"式エラー: {expr_error}")
                status[f.id] = FeatureStatus("error", ctx.results[f.id].error, True)
                continue
            deps = set(f.deps())
            if f.uses_body and prev_body_fid:
                deps.add(prev_body_fid)
            missing = [d for d in deps if d not in versions]
            key = (self._def_key(f), tuple(sorted((d, versions.get(d, -1)) for d in deps)))
            cached = self._cache.get(f.id)
            rebuilt = False
            if cached and cached.key == key:
                res = cached.result
                ver = cached.version
            else:
                if self.progress:
                    self.progress(i, len(self.features), f.name)
                if missing:
                    res = FeatureResult(error=f"参照先が無効です: {', '.join(sorted(missing))}")
                else:
                    ctx.notes = []
                    try:
                        res = f.build(ctx)
                        if ctx.notes:
                            res.warning = " / ".join(filter(None, [res.warning, *ctx.notes]))
                    except (FeatureError, DanglingReference, KernelError) as e:
                        res = FeatureResult(error=str(e))
                    except Exception as e:          # 想定外の例外でもツリー全体は止めない（原因はログに残す）
                        log.exception("フィーチャー %s の再生成で想定外の例外", f.id)
                        res = FeatureResult(error=f"内部エラー: {type(e).__name__}: {e}")
                self._version += 1
                ver = self._version
                # スケッチは build で点座標が解に更新されるので、build 後の定義をキーにする
                key = (self._def_key(f), key[1])
                self._cache[f.id] = _Cache(key, res, ver)
                rebuilt = True
            ctx.results[f.id] = res
            if res.error:
                status[f.id] = FeatureStatus("error", res.error, rebuilt)
                continue
            versions[f.id] = ver
            status[f.id] = FeatureStatus("warning" if res.warning else "ok", res.warning, rebuilt)
            if f.makes_body:
                ctx.body, ctx.names = res.body, res.names
                prev_body_fid = f.id
        # 存在しないフィーチャーのキャッシュを捨てる
        live = {f.id for f in self.features}
        self._cache = {k: v for k, v in self._cache.items() if k in live}
        self.body, self.names, self.status = ctx.body, ctx.names, status
        self.results = ctx.results
        return status

    @staticmethod
    def _def_key(f: Feature) -> str:
        d = f.to_dict(); d.pop("name", None)
        return json.dumps(d, sort_keys=True, default=str)

    def _changed(self):
        self.regenerate()

    # ------------------------------------------------------------ 保存
    def to_dict(self) -> dict:
        return {"format": "agentcad.part/1", "rollback": self.rollback, "variables": dict(self.variables.exprs),
                "features": [f.to_dict() for f in self.features],
                "appearance": json.loads(json.dumps(self.appearance))}

    def _load(self, d: dict):
        self.features = [Feature.from_dict(json.loads(json.dumps(x))) for x in d["features"]]
        self.rollback = d["rollback"]
        self.variables = Variables(d.get("variables"))
        self.appearance = {**appearance.empty(), **json.loads(json.dumps(d.get("appearance") or {}))}

    @classmethod
    def from_dict(cls, kernel, d: dict) -> "PartDocument":
        doc = cls(kernel); doc._load(d); return doc

    def tree(self) -> list[dict]:
        """UI 用のツリー表示データ。"""
        out = []
        for i, f in enumerate(self.features):
            st = self.status.get(f.id, FeatureStatus("ok"))
            out.append({"id": f.id, "name": f.name, "type": f.type, "state": st.state,
                        "message": st.message, "index": i})
        return out

