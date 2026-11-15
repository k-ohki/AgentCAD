"""汎用の非線形拘束ソルバ（スケッチ 2D とアセンブリ合致 3D で共用）。

設計（docs/SPEC.md 2章）
- 変数は1本のベクトル x。拘束は「種類(Kind) + 変数インデックス + 定数」
- 同じ Kind の拘束はまとめてバッチ評価し、自動微分(ad.py)で正確な疎ヤコビアンを得る
- 解法: 双対形式の Levenberg-Marquardt
      dx = W⁻¹Jᵀ (J W⁻¹ Jᵀ + λI)⁻¹ (−r)
  λ→0 で「重み付き最小ノルム Newton」になる。未定義（自由度あり）の系では
  今の形からの移動量が最小の解へ動くので、触っていない部分はなるべく動かない。
- ドラッグ: 掴んだ変数を目標値へ置き、その変数の移動コスト W を大きくして解く。
  掴んだ点はカーソルに留まろうとし、他が追従する。届かない場合は拘束を満たす近い点で止まる。
- 診断: 自由度 / 冗長 / 矛盾の原因拘束 / まだ動ける変数（零空間）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

import numpy as np
import scipy.linalg
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .ad import D


@dataclass(frozen=True, eq=False)
class Kind:
    """拘束の種類。fn(p, c) は残差のリストを返す。
    p: 局所変数（D のリスト、各 (B,)）、c: 定数 (B, nc)。B 個を同時に評価する。"""
    name: str
    fn: Callable[[list[D], np.ndarray], list]
    n_res: int


class Constraint:
    """バッチ化しない汎用拘束（差分ヤコビアン）。試作・テスト用の遅い経路。"""
    params: list[int]
    name: str = ""

    def residual(self, v: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def jacobian(self, v: np.ndarray) -> np.ndarray:
        r0 = self.residual(v)
        J = np.zeros((len(r0), len(v)))
        for i in range(len(v)):
            h = 1e-7 * max(1.0, abs(v[i]))
            vp = v.copy(); vp[i] += h
            vm = v.copy(); vm[i] -= h
            J[:, i] = (self.residual(vp) - self.residual(vm)) / (2 * h)
        return J


class FuncConstraint(Constraint):
    def __init__(self, params: Sequence[int], fn: Callable[[np.ndarray], np.ndarray], name: str = ""):
        self.params = list(params)
        self.fn = fn
        self.name = name

    def residual(self, v):
        return np.atleast_1d(np.asarray(self.fn(v), dtype=float))


class SolveStatus(str, Enum):
    OK = "ok"
    INCONSISTENT = "inconsistent"


@dataclass
class SolveResult:
    status: SolveStatus
    iterations: int
    residual: float


@dataclass
class Diagnosis:
    dof: int
    redundant: list[int] = field(default_factory=list)     # 自由度を1つも奪わない拘束
    conflicting: list[int] = field(default_factory=list)   # 矛盾の原因（後から追加した方を優先）
    free_params: list[int] = field(default_factory=list)   # まだ動ける変数
    null_basis: np.ndarray | None = None                   # 行=動き方、列=free_index
    free_index: list[int] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.conflicting or self.redundant:
            return "over"
        return "full" if self.dof == 0 else "under"

    def dof_of(self, params: Sequence[int]) -> int:
        """指定した変数群だけを見たときの自由度（部品ごとの DOF 表示用）。"""
        if self.null_basis is None or len(self.null_basis) == 0:
            return 0
        pos = {p: i for i, p in enumerate(self.free_index)}
        cols = [pos[p] for p in params if p in pos]
        if not cols:
            return 0
        return int(np.linalg.matrix_rank(self.null_basis[:, cols], tol=1e-6))


class System:
    #: これ以下の規模は密行列で解く（小さい系では疎行列のオーバーヘッドの方が大きい）
    DENSE_LIMIT = 20_000

    def __init__(self):
        self.x = np.zeros(0)
        self.fixed = np.zeros(0, dtype=bool)
        self._cons: list[tuple] = []      # ("k", kind, params, consts) | ("f", Constraint)
        self._compiled = None
        self._off: set[int] = set()       # 一時的に無効化した拘束（矛盾の原因探索用）
        #: 長さの目安（許容誤差の基準）。合致のように形の大きさが定数側にあるときは、使う側が入れる（D70）
        self.length_scale = 1.0
        #: 変数ごとの大きさの目安（None = すべて 1）。長さと回転（四元数）が混ざるとき、長さの変数を length_scale で
        #: 割った単位で解く（変数のスケーリング）。部品の原点から遠い軸のまわりの回転（扉のヒンジ）が解けるように（D70）
        self.param_scale: np.ndarray | None = None

    # ------------------------------------------------------------ 構築
    def add_param(self, value: float, fixed: bool = False) -> int:
        self.x = np.append(self.x, float(value))
        self.fixed = np.append(self.fixed, fixed)
        return len(self.x) - 1

    def add(self, kind: Kind, params: Sequence[int], consts: Sequence[float] = ()) -> int:
        self._cons.append(("k", kind, tuple(params), tuple(float(c) for c in consts)))
        self._compiled = None
        return len(self._cons) - 1

    def add_constraint(self, c: Constraint) -> int:
        self._cons.append(("f", c))
        self._compiled = None
        return len(self._cons) - 1

    @property
    def n_constraints(self) -> int:
        return len(self._cons)

    def _compile(self):
        if self._compiled is not None:
            return self._compiled
        row0 = np.zeros(len(self._cons) + 1, dtype=int)
        groups: dict[Kind, list[int]] = {}
        generic = []
        for ci, c in enumerate(self._cons):
            if c[0] == "k":
                n = c[1].n_res
                groups.setdefault(c[1], []).append(ci)
            else:
                n = len(c[1].residual(self.x[c[1].params]))
                generic.append(ci)
            row0[ci + 1] = row0[ci] + n
        batches = []
        for kind, cis in groups.items():
            P = np.array([self._cons[ci][2] for ci in cis], dtype=int)
            C = np.array([self._cons[ci][3] for ci in cis], dtype=float).reshape(len(cis), -1)
            p = P.shape[1]
            seeds = [np.broadcast_to(np.eye(p)[j], (len(cis), p)) for j in range(p)]
            batches.append((kind, np.array(cis), P, C, row0[cis], seeds))
        self._compiled = (row0, batches, generic)
        return self._compiled

    # ------------------------------------------------------------ 評価
    def _eval(self, x: np.ndarray):
        """残差 r (m,) と疎ヤコビアン J (m×n) を返す。"""
        row0, batches, generic = self._compile()
        m, n = int(row0[-1]), len(x)
        r = np.zeros(m)
        rows, cols, vals = [], [], []
        for kind, cis, P, C, r0, seeds in batches:
            xv = x[P]
            res = kind.fn([D(xv[:, j], seeds[j]) for j in range(P.shape[1])], C)
            for k, e in enumerate(res):
                if not isinstance(e, D):          # 変数に依存しない残差
                    r[r0 + k] = e
                    continue
                r[r0 + k] = e.v
                rows.append(np.repeat(r0 + k, P.shape[1])); cols.append(P.ravel()); vals.append(e.g.ravel())
        for ci in generic:
            c = self._cons[ci][1]
            v = x[c.params]
            rr = c.residual(v); jj = c.jacobian(v)
            a = row0[ci]
            r[a:a + len(rr)] = rr
            ri, pi = np.meshgrid(np.arange(a, a + len(rr)), c.params, indexing="ij")
            rows.append(ri.ravel()); cols.append(pi.ravel()); vals.append(jj.ravel())
        if rows:
            vals_ = np.nan_to_num(np.concatenate(vals), nan=0.0, posinf=0.0, neginf=0.0)
            J = sp.coo_matrix((vals_, (np.concatenate(rows), np.concatenate(cols))), shape=(m, n)).tocsr()
        else:
            J = sp.csr_matrix((m, n))
        r = np.nan_to_num(r, nan=0.0, posinf=1e30, neginf=-1e30)
        if self._off:
            mask = np.ones(m)
            for ci in self._off:
                mask[row0[ci]:row0[ci + 1]] = 0.0
            r = r * mask
            J = sp.diags(mask) @ J
        return r, J

    def _scale(self, x) -> float:
        return max(1.0, self.length_scale, float(np.abs(x).max(initial=0.0)))

    def residuals(self) -> list[float]:
        """拘束ごとの最大残差（絶対値）。"""
        row0 = self._compile()[0]
        r, _ = self._eval(self.x)
        return [float(np.abs(r[row0[i]:row0[i + 1]]).max(initial=0.0)) for i in range(len(self._cons))]

    # ------------------------------------------------------------ 求解
    def solve(self, max_iter: int = 80, drag: dict[int, float] | None = None,
              drag_weight: float = 1e3) -> SolveResult:
        x = self.x.copy()
        free = ~self.fixed
        w = np.ones(len(x)) if self.param_scale is None else 1.0 / np.asarray(self.param_scale, float) ** 2
        for p, t in (drag or {}).items():
            if free[p]:
                x[p] = t
                w[p] = drag_weight
        fidx = np.where(free)[0]
        winv = 1.0 / w[fidx]
        r, J = self._eval(x)
        S = self._scale(x)
        ctol, oktol = 1e-11 * S, 1e-7 * S
        err = float(np.linalg.norm(r))
        lam, it = 1e-10, 0
        dense = J.shape[0] * max(1, len(fidx)) <= self.DENSE_LIMIT
        while it < max_iter and len(r) and len(fidx) and np.abs(r).max() > ctol:
            it += 1
            if dense:
                Jf = J.toarray()[:, fidx]
                JW = Jf * winv
                A = JW @ Jf.T
                diag = A.diagonal()
            else:
                Jf = J[:, fidx].tocsr()
                JW = Jf.multiply(winv).tocsr()
                A = (JW @ Jf.T).tocsc()
                diag = A.diagonal()
            scale = max(float(diag.mean()), 1e-30)
            if np.linalg.norm(JW.T @ r) < 1e-12 * max(err, 1e-300) * np.sqrt(scale):
                break                                   # 最小二乗の停留点（矛盾）
            accepted = False
            while lam < 1e9:
                y = self._lin_solve(A, lam * scale, -r, dense)
                xn = x.copy()
                xn[fidx] += JW.T @ y
                rn, Jn = self._eval(xn)
                en = float(np.linalg.norm(rn))
                if np.isfinite(en) and en < err:
                    accepted = True
                    break
                lam = max(lam * 10, 1e-6)
            if not accepted:
                break
            stalled = err - en < 1e-13 * err
            x, r, J, err = xn, rn, Jn, en
            lam = max(lam * 0.1, 1e-10)
            if stalled:
                break
        self.x = x
        maxr = float(np.abs(r).max(initial=0.0))
        return SolveResult(SolveStatus.OK if maxr < oktol else SolveStatus.INCONSISTENT, it, maxr)

    @staticmethod
    def _lin_solve(A, mu, b, dense):
        if dense:
            M = A + mu * np.eye(len(b))
            try:
                return scipy.linalg.solve(M, b, assume_a="pos", check_finite=False)
            except (np.linalg.LinAlgError, scipy.linalg.LinAlgError, ValueError):
                return np.linalg.lstsq(M, b, rcond=None)[0]
        M = (A + mu * sp.identity(len(b), format="csc")).tocsc()
        return spla.splu(M).solve(b)

    def drag(self, targets: dict[int, float], max_iter: int = 80) -> SolveResult:
        """変数を目標値へ引っ張って解く。解けなければ元の状態に戻す。"""
        backup = self.x.copy()
        res = self.solve(max_iter, drag=targets)
        if res.status != SolveStatus.OK:
            self.x = backup
        return res

    # ------------------------------------------------------------ 診断
    def diagnose(self) -> Diagnosis:
        """自由度・冗長・矛盾を判定する。self.x は変更しない。"""
        saved = self.x.copy()
        culprits: list[int] = []
        try:
            r, _ = self._eval(self.x)
            if len(r) and np.abs(r).max() >= 1e-7 * self._scale(self.x):
                if self.solve().status != SolveStatus.OK:
                    culprits = self._find_culprits(saved)
            d = self._rank_analysis()
        finally:
            self.x = saved
            self._off = set()
        d.conflicting = sorted(culprits)
        return d

    def _find_culprits(self, start: np.ndarray) -> list[int]:
        """矛盾の原因を探す。残差が残った拘束の中から、後に追加したものを優先して外し、
        解けるようになる最小の組を返す。終了時 self._off に原因が入り、self.x は残りを解いた状態。"""
        off: list[int] = []
        ok = False
        for _ in range(4):
            res = self.residuals()
            tol = 1e-7 * self._scale(self.x)
            group = sorted((i for i, e in enumerate(res) if e >= tol and i not in self._off), reverse=True)
            if not group:
                break
            for c in group:
                self._off.add(c); off.append(c)
                self.x = start.copy()
                if self.solve().status == SolveStatus.OK:
                    ok = True
                    break
            if ok:
                break
        if ok:
            for c in off[:-1]:                 # 外しすぎた分を戻す
                self._off.discard(c)
                self.x = start.copy()
                if self.solve().status != SolveStatus.OK:
                    self._off.add(c)
            self.x = start.copy()
            self.solve()
        return sorted(self._off)

    def _rank_analysis(self) -> Diagnosis:
        row0 = self._compile()[0]
        r, J = self._eval(self.x)
        fidx = np.where(~self.fixed)[0]
        nf = len(fidx)
        Jf = J.toarray()[:, fidx] if nf else np.zeros((len(r), 0))
        norms = np.linalg.norm(Jf, axis=1) if nf else np.zeros(len(r))
        # 行を追加順にグラム・シュミット直交化し、ランクを増やす行かどうかを調べる
        Q = np.zeros((min(len(r), nf), nf))
        k = 0
        indep = np.zeros(len(r), dtype=bool)
        for i in range(len(r)):
            if norms[i] < 1e-12 or k >= nf:
                continue
            v = Jf[i] / norms[i]
            for _ in range(2):
                v = v - Q[:k].T @ (Q[:k] @ v)
            nv = np.linalg.norm(v)
            if nv > 1e-7:
                Q[k] = v / nv
                k += 1
                indep[i] = True
        tol = 1e-7 * self._scale(self.x)
        redundant = []
        for ci in range(len(self._cons)):
            a, b = row0[ci], row0[ci + 1]
            if ci in self._off or a == b:
                continue
            if not indep[a:b].any() and np.abs(r[a:b]).max() < tol:
                redundant.append(ci)
        dof = nf - k
        null, free_params = None, []
        if dof > 0:
            null = np.eye(nf) if k == 0 else scipy.linalg.qr(Q[:k].T, mode="full")[0][:, k:].T
            mag = np.abs(null).max(axis=0)
            free_params = [int(fidx[i]) for i in np.where(mag > 1e-6)[0]]
        return Diagnosis(dof, redundant, [], free_params, null, [int(i) for i in fidx])
