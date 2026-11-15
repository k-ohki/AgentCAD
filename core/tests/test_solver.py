import numpy as np
import pytest

from cad.solver import FuncConstraint, Kind, SolveStatus, System
from cad.solver.ad import D


def test_solve_simple_distance():
    s = System()
    a = s.add_param(0, fixed=True); b = s.add_param(3)
    s.add_constraint(FuncConstraint([a, b], lambda v: v[1] - v[0] - 5))
    r = s.solve()
    assert r.status == SolveStatus.OK and s.x[b] == pytest.approx(5)
    d = s.diagnose()
    assert d.dof == 0 and d.state == "full"


def test_inconsistent_blames_latest_and_keeps_x():
    s = System()
    a = s.add_param(1)
    s.add_constraint(FuncConstraint([a], lambda v: v[0] - 1))
    s.add_constraint(FuncConstraint([a], lambda v: v[0] - 2))
    assert s.solve().status == SolveStatus.INCONSISTENT
    x = s.x.copy()
    assert s.diagnose().conflicting == [1]
    assert s.x == pytest.approx(x)            # diagnose は x を変えない


def test_fd_jacobian_matches_analytic():
    c = FuncConstraint([0, 1], lambda v: [np.hypot(v[0], v[1])])
    assert c.jacobian(np.array([3.0, 4.0])) == pytest.approx(np.array([[0.6, 0.8]]), abs=1e-6)


def check_kind_jacobian(kind: Kind, n_params: int, n_consts: int, rng, B=4):
    """自動微分のヤコビアンを中心差分と比較する。"""
    x = rng.uniform(-3, 3, (B, n_params)); x[:, 3:7] += 2.0
    c = rng.uniform(0.5, 2, (B, n_consts))

    def val(xx):
        seeds = [np.zeros((B, n_params)) for _ in range(n_params)]
        return np.stack([e.v for e in kind.fn([D(xx[:, j], seeds[j]) for j in range(n_params)], c)], 1)
    eye = np.eye(n_params)
    res = kind.fn([D(x[:, j], np.broadcast_to(eye[j], (B, n_params))) for j in range(n_params)], c)
    assert len(res) == kind.n_res
    J = np.stack([e.g for e in res], 1)                     # (B, n_res, P)
    h = 1e-6
    for j in range(n_params):
        d = np.zeros_like(x); d[:, j] = h
        fd = (val(x + d) - val(x - d)) / (2 * h)
        assert J[:, :, j] == pytest.approx(fd, abs=1e-5), f"{kind.name} param {j}"


def test_sketch_kinds_autodiff():
    from cad.sketch import kinds as K
    rng = np.random.default_rng(0)
    arity = {"coincident": 4, "horizontal": 4, "vertical": 4, "parallel": 8, "perpendicular": 8, "angle": 8,
             "equal_len": 8, "equal_param": 2, "point_on_line": 6, "point_on_circle": 5, "midpoint": 6,
             "tangent": 7, "tangent_at": 8, "tangent_cc": 6, "tangent_cc_at": 6, "symmetric": 8, "fix": 2, "dist_pp": 4, "dist_pl": 6,
             "hdist": 4, "vdist": 4, "length": 4, "param_value": 1,
             "fix_line": 4, "fix_circle": 3, "fix_arc": 6, "param_diff": 2, "collinear3": 6, "on_fixed_line": 2}
    kinds = [k for k in vars(K).values() if isinstance(k, Kind)]
    assert {k.name for k in kinds} == set(arity)
    for k in kinds:
        check_kind_jacobian(k, arity[k.name], 6, rng)


def test_mate_kinds_autodiff():
    from cad.assembly import kinds as M
    rng = np.random.default_rng(1)
    for k in (v for v in vars(M).values() if isinstance(v, Kind)):
        check_kind_jacobian(k, 4 if k.name == "quat_norm" else 14, M.N_CONSTS, rng)


LINK = Kind("link", lambda p, c: [(p[2] - p[0]) * (p[2] - p[0]) + (p[3] - p[1]) * (p[3] - p[1]) - c[:, 0]], 1)


def test_drag_prefers_dragged_param_and_stays_feasible():
    s = System()
    a = [s.add_param(v) for v in (0, 0)]; b = [s.add_param(v) for v in (5, 0)]
    s.add(LINK, a + b, [25.0])                       # |ab| = 5、どちらも自由
    assert s.drag({b[0]: 8.0, b[1]: 4.0}).status == SolveStatus.OK
    assert s.x[b] == pytest.approx([8, 4], abs=0.05)   # 掴んだ点はほぼカーソル位置
    assert np.hypot(*(s.x[b] - s.x[a])) == pytest.approx(5)
    # 相手を固定すると届かない → 円上の近い点で止まる
    s.fixed[a] = True
    ax, ay = s.x[a]
    s.drag({b[0]: ax + 30.0, b[1]: ay})
    assert s.x[b] == pytest.approx([ax + 5, ay], abs=1e-6)


def test_partial_dependency_is_not_redundant():
    s = System()
    p = [s.add_param(v) for v in (1.0, 2.0)]
    s.add(Kind("xy", lambda p, c: [p[0] - 1.0, p[1] - 2.0], 2), p)
    i = s.add(Kind("x", lambda p, c: [p[0] - 1.0], 1), p[:1])        # 完全に従属 → 冗長
    j = s.add(Kind("xz", lambda p, c: [p[0] - 1.0, p[1] - 5.0], 2), [p[0], s.add_param(5.0)])  # 一部だけ従属
    d = s.diagnose()
    assert d.redundant == [i] and j not in d.redundant and d.dof == 0
