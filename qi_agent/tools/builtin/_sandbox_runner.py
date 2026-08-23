"""子进程受限执行器：RestrictedPython 编译 + 受限环境 exec。

由 run_python 以子进程方式调用（sys.stdin 传用户代码），与主进程隔离——
不可信代码只在受限子进程内编译执行（双保险：受限解释器 + 进程隔离）。

设计（方案 docs/plans/2026-08-19-软沙箱v2方案.md + API 实况调整）：
- AST 重写：属性访问/下标/迭代/print 强制走守卫（RestrictedPython 8.5）
- import 拦截：受限 builtins 无 __import__ → 默认全禁；配置放行时注入
  受限 __import__ 守卫（白名单检查）——这是 8.5 的实际机制（不改写 import 语句）
- 受限内建 + import 白名单：默认最严格，环境变量按需放行（受可放行上限约束）

守卫机制（8.5 实况，对照方案文档的差异）：
- print(x)  → _print._call_print(x)（_print = _print_(_getattr_) 注入初始化）
- obj.attr → _getattr_(obj, 'attr')
- obj[k]   → _getitem_(obj, k)
- for x in it → _getiter_(it)
"""

import builtins
import operator
import os
import sys

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getattr
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
)

# 原地运算守卫映射（n += 1 → n = _inplacevar_("+=", n, 1)）
_INPLACE_OPS = {
    "+=": operator.iadd, "-=": operator.isub, "*=": operator.imul,
    "/=": operator.itruediv, "//=": operator.ifloordiv, "%=": operator.imod,
    "**=": operator.ipow, "&=": operator.iand, "|=": operator.ior,
    "^=": operator.ixor, "<<=": operator.ilshift, ">>=": operator.irshift,
}

# ── 可放行清单（用户能扩展的"上限"——只含无副作用的纯计算能力）──
# 无论配置什么，open/eval/exec/__import__/os/sys 都不在此清单 → 永远进不来
_ALLOWED_EXTRA_BUILTINS = ("round", "pow", "divmod", "isinstance")
_ALLOWED_EXTRA_MODULES = ("math", "random", "json", "statistics", "fractions", "decimal")

# ── 默认安全内建（硬编码底线）：计算结果必需，无系统能力 ──
_SAFE_BUILTINS = dict(safe_builtins)
_SAFE_BUILTINS.update({
    "print": print,
    "len": len,
    "range": range,
    "str": str,
    "int": int,
    "float": float,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
})

# ── import 白名单（默认空 = import 全禁，安全底线）──
_SAFE_MODULES: tuple[str, ...] = ()


class _StdoutPrint:
    """print 守卫（CLI 版）。

    RestrictedPython 8.5 把 print(x) 改写为 `_print._call_print(x)`，
    并在模块开头注入 `_print = _print_(_getattr_)`。Zope 原版 PrintCollector
    收集文本供宿主读取；CLI 场景直接输出 stdout 即可。
    """

    def __init__(self, _getattr_=None) -> None:
        self._getattr_ = _getattr_  # 注入初始化传入，本实现不使用

    def _call_print(self, *objects, **kwargs) -> None:
        print(*objects, **kwargs)  # 直接输出（沙箱子进程 stdout 由父进程捕获）


def _load_config() -> None:
    """从环境变量读取用户扩展（按需放行，默认保持最严格）。

    配置源：QI_SANDBOX_EXTRA_BUILTINS / QI_SANDBOX_EXTRA_MODULES（逗号分隔）
    将来阶段 4 config.yaml 接入时，环境变量源换成配置文件（接口不变）。
    """
    global _SAFE_MODULES
    for name in os.getenv("QI_SANDBOX_EXTRA_BUILTINS", "").split(","):
        name = name.strip()
        if name in _ALLOWED_EXTRA_BUILTINS:
            # 从真实 builtins 取函数（globals() 取不到内建，会得到 None）
            _SAFE_BUILTINS[name] = getattr(builtins, name)
    _SAFE_MODULES = tuple(
        m.strip()
        for m in os.getenv("QI_SANDBOX_EXTRA_MODULES", "").split(",")
        if m.strip() in _ALLOWED_EXTRA_MODULES
    )


def _guarded_import(name, *args, **kwargs):
    """受限 __import__ 守卫：只放行白名单模块。

    8.5 实况：import 语句不改写（保留 IMPORT_NAME 字节码），默认靠
    "受限 builtins 无 __import__" 全禁；配置放行时注入本守卫做白名单检查。
    """
    if name not in _SAFE_MODULES:
        raise ImportError(f"模块 {name} 不在沙箱白名单")
    return __import__(name, *args, **kwargs)


def _inplacevar_(op: str, left, right):
    """原地运算守卫（RestrictedPython 改写 n += 1 → _inplacevar_("+=", n, 1)）。"""
    return _INPLACE_OPS[op](left, right)


def main() -> None:
    """入口：读 stdin 用户代码 → 受限编译 → 受限执行。"""
    _load_config()
    if _SAFE_MODULES:
        # 配置放行了模块 → 注入受限 __import__ 守卫（白名单检查）
        _SAFE_BUILTINS["__import__"] = _guarded_import
    code = sys.stdin.read()
    try:
        bytecode = compile_restricted(code, "<sandbox>", "exec")
    except SyntaxError as exc:
        print(f"[错误] 代码编译失败: {exc}")
        return
    # 受限环境：受限内建 + 守卫函数（AST 改写后的代码调用它们）
    safe_globals = {
        "__builtins__": _SAFE_BUILTINS,
        "_getattr_": default_guarded_getattr,
        "_getitem_": lambda ob, key: ob[key],
        "_getiter_": lambda ob: iter(ob),
        "_inplacevar_": _inplacevar_,
        "_print_": _StdoutPrint,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
    }
    try:
        exec(bytecode, safe_globals)  # noqa: S102 受限环境 exec——这正是沙箱目的
    except Exception as exc:  # 用户代码异常 → 返回错误信息（不中断）
        print(f"[错误] 执行失败: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
