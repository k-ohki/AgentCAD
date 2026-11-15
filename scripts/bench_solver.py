"""ソルバのベンチマーク。ソルバを触ったら実行して docs/PLAN.md の実測値と比べる。

    cd core && python ../scripts/bench_solver.py
"""
import time, math, random, sys
sys.path.insert(0,'.')
import numpy as np
from cad.sketch import Sketch

def chain(n):
    """n 個の矩形を横に連結し完全定義する"""
    s=Sketch(); prev=None
    for i in range(n):
        ls=s.rect(i*12, 0, i*12+10.3, 5.2)
        s.constrain("length", ls[0], value=10); s.constrain("length", ls[1], value=5)
        p0=s.entities[ls[0]].p1
        if prev is None: s.constrain("fix", p0)
        else:
            s.constrain("hdistance", prev, p0, value=12); s.constrain("vdistance", prev, p0, value=0)
        prev=p0
    return s
for n in (5,20,50):
    s=chain(n)
    t=time.perf_counter(); b=s._build(); sys_=b.sys; r=sys_.solve(); t1=time.perf_counter()
    d=sys_.diagnose(); t2=time.perf_counter()
    print(n,"rects params",len(sys_.x),"cons",sys_.n_constraints,"solve %.0fms it=%d %s"%((t1-t)*1e3,r.iterations,r.status.value),"diagnose %.0fms dof=%d"%((t2-t1)*1e3,d.dof), d.state, len(d.redundant), len(d.conflicting))
    p=s.entities[list(s.entities)[-1]]
    t=time.perf_counter(); 
    for k in range(5): s.drag(list(s.entities)[2], 10+k, 5+k, diagnose=False)
    print("   drag(診断なし) avg %.0fms"%((time.perf_counter()-t)/5*1e3))
