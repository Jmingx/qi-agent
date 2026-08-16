"""工具注册机制：register() 显式注册 + @tool 便捷封装。

架构设计（参考 Hermes tools/registry.py）：
- 工具是"文件 + 注册"的完整单元：handler（函数）+ schema + toolset + 元信息
- register() 显式注册：信息量大（分组/环境检查/手写schema），工程化
- @tool 装饰器保留为 register() 的便捷封装（向后兼容）

初始化日志（用户要求）：注册/跳过工具时打印信息，方便定位与学习。
"""

import inspect
import json
import os
from dataclasses import dataclass, field
from typing import Callable, get_type_hints

# 注册表：工具名 -> ToolEntry（改用结构化条目，对齐 Hermes ToolEntry 思想）
_TOOL_REGISTRY: dict[str, "ToolEntry"] = {}

# Python 类型 -> JSON Schema 类型映射（自动生成 schema 用）
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


@dataclass
class ToolEntry:
    """注册表中的一个工具条目（对齐 Hermes 的 ToolEntry 思想）。"""

    name: str                       # 工具名（唯一）
    toolset: str                    # 归属分组（默认 builtin）
    schema: dict                    # 完整 JSON Schema
    handler: Callable               # 处理函数（接收 **arguments）
    description: str = ""           # 一句话描述
    check_fn: Callable | None = None        # 环境检查（返回 False 不注册）
    requires_env: list[str] = field(default_factory=list)  # 需要的环境变量


def _log_registered(entry: "ToolEntry") -> None:
    """打印工具注册成功的日志（学习/定位用）。"""
    params = entry.schema["function"]["parameters"]["properties"]
    print(
        f"[工具注册] ✓ {entry.name} "
        f"(toolset={entry.toolset}, 参数={list(params.keys())})"
    )


def _log_skipped(name: str, reason: str) -> None:
    """打印工具被跳过注册的日志（环境检查/依赖缺失时）。"""
    print(f"[工具注册] ⚠ 跳过 {name}: {reason}")


def register(
    name: str,
    toolset: str = "builtin",
    schema: dict | None = None,
    handler: Callable | None = None,
    description: str = "",
    check_fn: Callable | None = None,
    requires_env: list[str] | None = None,
) -> None:
    """显式注册一个工具。

    Args:
        name: 工具名（唯一，重复注册抛错）
        toolset: 归属分组（默认 builtin）
        schema: 手写 JSON Schema；None 则从 handler 签名自动生成
        handler: 处理函数（接收关键字参数）
        description: 工具描述
        check_fn: 环境检查函数，返回 False 时跳过注册（优雅降级）
        requires_env: 需要的环境变量列表，缺失时跳过注册

    Raises:
        ValueError: 工具名重复且未显式 override
    """
    if handler is None:
        raise ValueError(f"register({name}): handler 不能为空")

    # 1. 环境检查：check_fn 返回 False → 跳过（记录日志）
    if check_fn is not None and not check_fn():
        _log_skipped(name, f"check_fn 返回 False（{description}）")
        return

    # 2. 环境变量检查：缺失 → 跳过（记录日志）
    envs = requires_env or []
    missing = [e for e in envs if not os.getenv(e)]
    if missing:
        _log_skipped(name, f"缺少环境变量: {missing}")
        return

    # 3. 重复注册防护
    if name in _TOOL_REGISTRY:
        existing = _TOOL_REGISTRY[name]
        raise ValueError(
            f"工具 '{name}' 已存在（toolset={existing.toolset}），如需覆盖请先注销"
        )

    # 4. schema：手写优先，否则自动生成
    if schema is None:
        schema = _build_schema(handler, description)
        # 关键：自动生成的 schema 里 function.name 必须用注册名，
        # 不能用函数名（注册名和函数名可能不一致，如 register(name="schema_x", handler=tool_x)）
        schema["function"]["name"] = name

    entry = ToolEntry(
        name=name,
        toolset=toolset,
        schema=schema,
        handler=handler,
        description=description,
        check_fn=check_fn,
        requires_env=envs,
    )
    _TOOL_REGISTRY[name] = entry
    _log_registered(entry)


def tool(description: str = "", toolset: str = "builtin") -> Callable:
    """装饰器：register() 的便捷封装（向后兼容，现有用法零改动）。

    用法:
        @tool(description="获取当前时间")
        def get_time() -> str: ...
    """

    def decorator(fn: Callable) -> Callable:
        # 转调 register()：schema 自动从签名生成
        register(name=fn.__name__, toolset=toolset, handler=fn, description=description)
        return fn  # 原样返回，不改变函数本身

    return decorator


def _build_schema(fn: Callable, description: str) -> dict:
    """从函数签名和类型注解生成 JSON Schema（OpenAI tools 格式）。"""
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)

    properties: dict[str, dict] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        # 跳过 self（工具都是模块级函数，理论不会出现，防御性跳过）
        if param_name == "self":
            continue

        param_type = hints.get(param_name, str)  # 无注解默认按字符串处理
        prop = {"type": _TYPE_MAP.get(param_type, "string")}

        # 有默认值的参数：非必填，可带 description 说明默认值
        if param.default is not inspect.Parameter.empty:
            prop["description"] = f"默认值: {param.default}"
        else:
            required.append(param_name)

        properties[param_name] = prop

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def get_tool_schemas() -> list[dict]:
    """返回所有已注册工具的定义（作为给 LLM 的 tools 参数）。"""
    return [entry.schema for entry in _TOOL_REGISTRY.values()]


def get_tools_by_toolset(toolset: str) -> list[str]:
    """返回指定分组下的所有工具名（toolset 分组查询）。"""
    return [entry.name for entry in _TOOL_REGISTRY.values() if entry.toolset == toolset]


def execute_tool(name: str, arguments: dict) -> str:
    """按名字执行工具，返回字符串结果。

    未知工具/执行异常都返回错误提示字符串，不抛出异常——
    agent 循环中工具失败不应中断整个对话。
    """
    entry = _TOOL_REGISTRY.get(name)
    if entry is None:
        return f"[工具错误] 未知工具: {name}"

    try:
        result = entry.handler(**arguments)
        # 统一转成字符串返回（LLM 只能读文本）
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:  # 工具内部异常 → 转为错误消息回填给 LLM
        return f"[工具错误] {name} 执行失败: {exc}"
