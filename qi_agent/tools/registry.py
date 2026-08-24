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

# 工具输出统一截断上限（阶段 B2，方案 2026-08-22）：registry 出口兜底
_TOOL_OUTPUT_LIMIT = 2000

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
    approval: str | Callable[[dict], str | None] | None = None
    # 审批声明（v0.4.26 声明式判档）：工具在注册时自声明权限策略——
    # str     = 无条件审批模板（"删除文件 {path}"，{param} 从参数填充，
    #           缺参/不匹配回退模板本身）
    # callable = 条件审批函数：接收 arguments，返回审批描述 str（需审批）
    #           或 None（放行）
    # None    = 默认放行（不产生审批）
    # 由 security_guard 插件查 registry 执行；插件本身零工具名分支。
    output_limit: int = _TOOL_OUTPUT_LIMIT
    # 输出截断上限（阶段 B2，方案 2026-08-22）：registry 出口统一截断
    # 兜底（默认 2000 字符）——各工具不再各自为政，截断策略一处改。
    # 例外：read_file 注册 50_000（行级分页语义——一次可返回大块，
    # 模型用 offset 续读；统一 2000 会破坏分页设计）


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
    approval: str | Callable[[dict], str | None] | None = None,
    output_limit: int | None = None,
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
        approval: 审批声明（v0.4.26）——无条件模板 str / 条件函数 callable /
            None 放行。security_guard 插件查 registry 判档，插件零改动
        output_limit: 输出截断上限（阶段 B2）——默认 2000；read_file 等
            分页工具可调大（50_000）

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
        approval=approval,
        output_limit=output_limit or _TOOL_OUTPUT_LIMIT,
    )
    _TOOL_REGISTRY[name] = entry
    _log_registered(entry)


def tool(description: str = "", toolset: str = "builtin",
         approval: str | Callable[[dict], str | None] | None = None) -> Callable:
    """装饰器：register() 的便捷封装（向后兼容，现有用法零改动）。

    用法:
        @tool(description="获取当前时间")
        def get_time() -> str: ...
    """

    def decorator(fn: Callable) -> Callable:
        # 转调 register()：schema 自动从签名生成
        register(name=fn.__name__, toolset=toolset, handler=fn,
                 description=description, approval=approval)
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


def get_tool(name: str) -> "ToolEntry | None":
    """按名字取工具条目（未注册返回 None）。

    供插件/内部逻辑读取工具元信息（如审批声明 approval，v0.4.26）。
    """
    return _TOOL_REGISTRY.get(name)


def get_tool_schemas(allowlist: list[str] | None = None) -> list[dict]:
    """返回工具定义（作为给 LLM 的 tools 参数）。

    Args:
        allowlist: 工具白名单（subagent 受限子集，方案 2026-08-23）——
            None = 全部工具（默认，向后兼容）；非空列表 = 只返回白名单内
            工具——LLM 只知道这些工具存在，其他工具【看都看不到】
            （层 1 模型可见过滤；执行端硬校验见 executor）

    Returns:
        schema 列表（过滤后）
    """
    if allowlist is None:
        return [entry.schema for entry in _TOOL_REGISTRY.values()]
    return [
        entry.schema for entry in _TOOL_REGISTRY.values()
        if entry.name in allowlist
    ]


def get_tools_by_toolset(toolset: str) -> list[str]:
    """返回指定分组下的所有工具名（toolset 分组查询）。"""
    return [entry.name for entry in _TOOL_REGISTRY.values() if entry.toolset == toolset]


def validate_arguments(schema: dict, arguments: dict,
                       internal: set[str] | None = None) -> str | None:
    """校验工具参数（执行前），返回 None=通过，否则返回可行动的错误信息。

    Args:
        schema: 工具 schema（模型可见）
        arguments: 待校验参数
        internal: 本次调用中【agent 内部注入】的参数名集合（模型路径不传）——
            这些参数跳过校验（schema 未声明也不报错）。
            防绕过关键（v0.4.18）：模型直接传 approved 时 internal=None →
            approved 是多余参数 → 拒绝；只有 agent 审批路径显式传入 internal。

    校验项（方案 docs/plans/2026-08-17-参数校验方案.md）：
    1. 必填检查：schema.required 中缺失的参数
    2. 类型检查：参数类型与 schema.properties 声明的类型不符
       （bool 严格化：bool 是 int 子类，integer 参数不接受 bool）
    3. 多余参数：传入 schema 未声明的参数

    错误信息"可行动"：告诉模型缺什么、该传什么类型，一轮纠错到位，
    避免模型盲目重试浪费 API 轮次。
    """
    func = schema["function"]
    params = func["parameters"]
    properties = params.get("properties", {})
    required = params.get("required", [])
    internal = internal or set()  # 本次调用的内部注入参数（agent 审批等）

    # 1. 必填检查：缺失的参数逐个列出
    missing = [r for r in required if r not in arguments]
    if missing:
        return f"缺少必填参数: {', '.join(missing)}"

    # 2. 类型检查 + 3. 多余参数（internal 放行——agent 内部注入，模型 schema 不可见）
    type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    for name, value in arguments.items():
        if name in internal:
            continue  # 内部参数（agent 注入）跳过校验
        if name not in properties:
            return f"未知参数: {name}（可用参数: {', '.join(properties)}）"
        expected = properties[name].get("type")
        # bool 严格化：bool 是 int 的子类，声明 integer 的参数不接受 bool
        if expected == "integer" and isinstance(value, bool):
            return f"参数 {name} 类型错误: 期望 integer, 实际 boolean"
        if expected in type_map and not isinstance(value, type_map[expected]):
            return f"参数 {name} 类型错误: 期望 {expected}, 实际 {type(value).__name__}"

    return None


def execute_tool(name: str, arguments: dict,
                 internal: set[str] | None = None) -> str:
    """按名字执行工具，返回字符串结果。

    未知工具/执行异常都返回错误提示字符串，不抛出异常——
    agent 循环中工具失败不应中断整个对话。

    参数校验（执行前）：失败返回 [参数错误] 前缀的可行动错误，
    让模型区分"我传参错了"（可修正重试）vs"工具本身失败"（换工具/放弃）。

    Args:
        name: 工具名
        arguments: 参数
        internal: agent 内部注入参数名集合（审批等；模型路径不传）
    """
    entry = _TOOL_REGISTRY.get(name)
    if entry is None:
        return f"[工具错误] 未知工具: {name}"

    # 参数校验（执行前，返回可行动错误）
    error = validate_arguments(entry.schema, arguments, internal)
    if error:
        return f"[参数错误] {error}"

    try:
        result = entry.handler(**arguments)
        # 统一转成字符串返回（LLM 只能读文本）
        if isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, ensure_ascii=False)
        # 出口统一截断（阶段 B2，方案 2026-08-22）：工具各自内部截断
        # 保留（兜底双保险），registry 是最终闸门——截断策略一处改
        if len(text) > entry.output_limit:
            text = (
                text[:entry.output_limit]
                + f"\n...[输出过长已截断（{len(text)} 字符，上限 {entry.output_limit}）]"
            )
        return text
    except Exception as exc:  # 工具内部异常 → 转为错误消息回填给 LLM
        return f"[工具错误] {name} 执行失败: {exc}"
