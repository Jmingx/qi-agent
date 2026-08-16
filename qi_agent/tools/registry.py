"""工具注册机制：@tool 装饰器把函数登记进注册表，供 LLM 调用。

核心思想（回顾 python-basics/02）：
- 装饰器是"包装"，但 @tool 不做包装，只做"登记"——把函数信息
  存入全局注册表，让 LLM 通过 JSON Schema 知道有哪些工具可用。
- 工具名 = 函数名；参数 schema 从函数签名 + 类型注解自动推导，
  写工具的人不用手动维护 schema。
"""

import inspect
import json
from typing import Callable, get_type_hints

# 注册表：工具名 -> {"fn": 函数, "description": 描述, "schema": JSON Schema}
_TOOL_REGISTRY: dict[str, dict] = {}

# Python 类型 -> JSON Schema 类型映射（本阶段支持的基础类型）
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def tool(description: str = "") -> Callable:
    """装饰器：把函数注册为可被 LLM 调用的工具。

    用法:
        @tool(description="获取当前时间")
        def get_time() -> str: ...

    函数名即工具名；参数 schema 从函数签名自动推导。
    原样返回函数（不包装）——LLM 不需要被包装的版本，只需要注册信息。
    """

    def decorator(fn: Callable) -> Callable:
        name = fn.__name__
        _TOOL_REGISTRY[name] = {
            "fn": fn,
            "description": description,
            "schema": _build_schema(fn, description),
        }
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
    return [entry["schema"] for entry in _TOOL_REGISTRY.values()]


def execute_tool(name: str, arguments: dict) -> str:
    """按名字执行工具，返回字符串结果。

    未知工具/执行异常都返回错误提示字符串，不抛出异常——
    agent 循环中工具失败不应中断整个对话。
    """
    entry = _TOOL_REGISTRY.get(name)
    if entry is None:
        return f"[工具错误] 未知工具: {name}"

    try:
        result = entry["fn"](**arguments)
        # 统一转成字符串返回（LLM 只能读文本）
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:  # 工具内部异常 → 转为错误消息回填给 LLM
        return f"[工具错误] {name} 执行失败: {exc}"
