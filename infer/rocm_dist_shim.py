# -*- coding: utf-8 -*-
"""
rocm_dist_shim.py —— ROCm on Windows 的 torch 分布式模块补丁

问题：ROCm 7.2.1 的 Windows 版 PyTorch **未编译分布式功能**——
      torch/_C.cp312-win_amd64.pyd 内不含任何 c10d 符号（实测全部缺失），
      导致 `import torch.distributed.tensor` 失败：
        ModuleNotFoundError: No module named 'torch._C._distributed_c10d'
      而 transformers 4.52.1 的 modeling_utils.py 第 40 行**无条件** import 它，
      于是模型根本装不进来。

方案：注入一个惰性替身模块 torch._C._distributed_c10d，
      所有未知属性按需生成占位类/函数，只为满足 import 期的类型检查，
      **不实现任何真实分布式功能**（我们跑单卡推理，用不到）。

安全性：transformers 第 176 行有守卫
      `if _torch_distributed_available and is_torch_greater_or_equal("2.5"):`
      而 ROCm 版 torch.distributed.is_available() == False，
      故 DTensor 分支根本不会进入，替身不参与任何计算路径。

用法：必须在 import transformers 之前调用
      import rocm_dist_shim; rocm_dist_shim.install()
"""
import sys
import types


def _make_stub():
    """构造惰性替身模块：任意属性访问都返回一个占位对象。"""
    mod = types.ModuleType("torch._C._distributed_c10d")

    class _StubMeta(type):
        """让占位类支持任意嵌套属性访问（如 ProcessGroup.BackendType.UNDEFINED）。"""
        def __getattr__(cls, name):
            if name.startswith("__"):
                raise AttributeError(name)
            sub = _StubMeta(name, (object,), {})
            setattr(cls, name, sub)
            return sub

    class _StubBase(metaclass=_StubMeta):
        def __init__(self, *a, **k):
            raise RuntimeError(
                "torch 分布式功能在 ROCm Windows 构建中不可用"
                "（本项目仅做单卡推理，不需要分布式）")

    def __getattr__(name):
        if name.startswith("__"):
            raise AttributeError(name)
        cls = _StubMeta(name, (_StubBase,), {
            "__module__": "torch._C._distributed_c10d",
            "__qualname__": name,
        })
        setattr(mod, name, cls)
        return cls

    mod.__getattr__ = __getattr__
    mod.__all__ = []
    return mod


def install():
    """注入替身。必须在 import transformers 之前调用。

    做法：直接把 `torch.distributed.tensor` 注册成一个空壳模块，
    绕过它整条依赖链（torch._C._distributed_c10d → distributed_c10d →
    _functional_collectives → placement_types → tensor）。
    理由：transformers 只在 modeling_utils.py:176 用它取 DTensor，
    而该行有守卫 `if _torch_distributed_available and ...`，
    ROCm 版 torch.distributed.is_available() == False ⇒ 该分支不执行。
    我们跑单卡推理，分布式完全不参与计算。
    """
    import torch
    C = torch._C
    if hasattr(C, "_distributed_c10d"):
        return "already-present"

    # 1) torch._C._distributed_c10d 替身（满足 distributed_c10d.py 的导入期需求）
    stub = _make_stub()
    sys.modules["torch._C._distributed_c10d"] = stub
    C._distributed_c10d = stub

    # 2) 直接短路整个 torch.distributed 包（tensor / fsdp / _functional_collectives …）
    #    触发链：transformers.modeling_utils:40 → torch.distributed.tensor
    #             torch._dynamo → torch.distributed.fsdp → torch.testing._internal.distributed.fake_pg
    #    这些全是"分布式训练"路径，单卡推理一律不执行。
    class _StubMeta(type):
        """让占位类支持任意嵌套属性访问（如 ProcessGroup.BackendType.UNDEFINED）。

        类级调用（`rpc.is_available()`）直接返回 False 而不实例化——
        torch.jit 的注解解析会直接调用这些「可用性查询」函数来决定分支。
        """
        def __getattr__(cls, name):
            if name.startswith("__"):
                raise AttributeError(name)
            sub = _StubMeta(name, (object,), {})
            setattr(cls, name, sub)
            return sub

        def __call__(cls, *a, **k):
            return False

    class _StubBase(metaclass=_StubMeta):
        def __init__(self, *a, **k):
            raise RuntimeError("torch 分布式功能在 ROCm Windows 构建中不可用（单卡推理无需）")

        def __call__(self, *a, **k):
            return False

    def _mk(name):
        return _StubMeta(name, (_StubBase,), {"__module__": name})

    # torch.distributed 顶层
    dist = types.ModuleType("torch.distributed")
    dist.__path__ = []  # 标记为包，便于子模块注册
    for _n in ("Store", "FileStore", "TCPStore", "PrefixStore", "ProcessGroup",
               "Work", "ReduceOp", "Barrier", "Backend", "DebugLevel",
               "all_reduce", "all_gather", "broadcast", "barrier",
               "init_process_group", "is_available", "is_initialized",
               "get_rank", "get_world_size"):
        setattr(dist, _n, _mk(_n))
    dist.is_available = lambda: False   # 关键：False ⇒ transformers 的 DTensor 分支不执行
    dist.is_initialized = lambda: False
    sys.modules["torch.distributed"] = dist
    setattr(torch, "distributed", dist)

    # 子包：tensor / fsdp / _functional_collectives / rendezvous / rpc …
    for sub in ("tensor", "fsdp", "_functional_collectives", "rendezvous",
                "distributed_c10d", "_tensor", "device_mesh", "eligibility",
                "algorithms", "graph", "sharding", "_shard.sharding_spec",
                "remote_device", "constants", "utils", "_coalescing_manager",
                "_composable_state", "_functional_collectives_impl",
                "rpc", "rpc._tensor", "distributed_c10d_", "p2p",
                "composable", "checkpoint", "_shard", "_shard.sharded_tensor",
                "_shard.sharded_tensor._utils", "_shard.sharded_tensor.api",
                "_shard.sharded_tensor.sharding_spec", "_shard.sharding_spec._internals"):
        m = types.ModuleType("torch.distributed." + sub)
        m.__path__ = []
        m.__getattr__ = lambda name, _m=m: (
            _mk(name) if not (name.startswith("__") and name.endswith("__"))
            else (_ for _ in ()).throw(AttributeError(name)))
        sys.modules["torch.distributed." + sub] = m
    for sub in ("tensor", "fsdp", "_functional_collectives", "rpc"):
        setattr(dist, sub, sys.modules["torch.distributed." + sub])
    return "installed"


if __name__ == "__main__":
    import torch
    print("torch:", torch.__version__)
    print("install ->", install())
    try:
        import torch.distributed.tensor as DT
        print("torch.distributed.tensor 导入: OK")
    except Exception as e:
        print("仍失败:", type(e).__name__, str(e)[:200])
        raise
    try:
        import transformers
        print("transformers 导入: OK  (%s)" % transformers.__version__)
    except Exception as e:
        print("transformers 仍失败:", type(e).__name__, str(e)[:200])
        raise
    # 关键校验：DTensor 分支不应进入
    print("torch.distributed.is_available():", torch.distributed.is_available())
    print(">>> 补丁有效")
