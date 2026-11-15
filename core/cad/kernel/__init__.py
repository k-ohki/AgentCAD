from .base import *  # noqa: F401,F403


def available_kernels() -> dict:
    """使えるカーネルの {名前: 生成関数}。実カーネルのアダプタは import できたものだけ載る。
    新しいアダプタは cad/kernel/<name>.py に Kernel サブクラスを置き、ここに1行足す。
    受け入れ条件は tests/test_kernel_conformance.py（全カーネル共通）。"""
    import importlib
    from .mock import MockKernel
    out = {"mock": MockKernel}
    for name, module, cls in (("usd_brep", ".usd_brep", "UsdBrepKernel"), ("occt", ".occt", "OcctKernel")):
        try:
            out[name] = getattr(importlib.import_module(module, __package__), cls)
        except ImportError:
            pass
    return out
