"""patch 精确编辑工具：小改动精确替换（替代整文件覆盖）。

设计（方案 2026-08-22-工具三件套，参考 Hermes patch/fuzzy_match）：
- 匹配策略链（Hermes 9 策略的保守子集 3 个，正则实现）：
  exact（精确）→ whitespace_flexible（空白序列/换行容错）→
  indentation_flexible（行首缩进漂移）——在原文上搜索，匹配区间
  天然准确（无归一化位置映射问题）；宁可报错不误改（保守哲学）
- 原子性：匹配失败 → 不写文件（零部分修改）；成功后单次/全部替换
- 安全：敏感路径红线（approved 也拒）+ 编辑已有文件 = 覆盖语义 →
  审批档（声明式 approval 模板）；approved 内部参数防绕过
- diff 展示：difflib unified_diff——模型/用户看到改了什么
- 边界：新建/整文件重写 → write_file；小改动精确替换 → 本工具
"""

import difflib
import re

from qi_agent.security.path_security import is_sensitive_path
from qi_agent.tools.registry import register

# 策略链（按顺序尝试；命中即用原文替换）
_STRATEGIES = ("exact", "whitespace_flexible", "indentation_flexible")


def _build_pattern(old: str, strategy: str) -> re.Pattern:
    """按策略把 old_string 转成匹配正则（DOTALL 跨行）。

    在原文上搜索——匹配区间直接是原文位置，无需归一化偏移映射
    （映射是 fuzzy 匹配实现中最容易出错的点）。
    """
    if strategy == "exact":
        return re.compile(re.escape(old), re.DOTALL)
    if strategy == "whitespace_flexible":
        # 空白序列（含换行/制表符/行尾）→ \s+：行尾、空白、换行容错合一
        parts = [re.escape(p) for p in re.split(r"\s+", old) if p]
        return re.compile(r"\s+".join(parts), re.DOTALL)
    if strategy == "indentation_flexible":
        # 每行行首缩进 → [ \t]*，行间换行 → \s*：缩进漂移 + 行尾容错
        lines = [re.escape(line.lstrip()) for line in old.split("\n")]
        return re.compile(r"[ \t]*" + r"\s*".join(lines), re.DOTALL)
    raise ValueError(f"未知策略: {strategy}")


def _render_diff(original: str, modified: str, path: str) -> str:
    """生成 unified diff 展示（截断防撑爆上下文）。"""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile=path, tofile=path,
    )
    text = "".join(diff)
    if len(text) > 2000:
        text = text[:2000] + "\n...[diff 过长已截断]"
    return text


def patch(path: str, old_string: str, new_string: str,
          replace_all: bool = False, approved: bool = False) -> str:
    """精确编辑文件：old_string → new_string（原子替换）。

    Args:
        path: 目标文件路径
        old_string: 待替换的原文片段（可跨行）
        new_string: 替换后的新文
        replace_all: True = 替换全部匹配；False = 只第一处
        approved: 内部参数（agent 审批注入）——编辑已有文件需 True

    Returns:
        成功（含 diff 展示）或 [安全拦截]/[错误] 提示
    """
    # ① 敏感路径红线（工具层兜底，approved 也拒）
    if is_sensitive_path(path):
        return f"[安全拦截] 路径敏感，禁止编辑: {path}"
    # ② 读文件
    try:
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
    except FileNotFoundError:
        return f"[错误] 文件不存在: {path}"
    except IsADirectoryError:
        return f"[错误] 路径是目录: {path}"
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"
    # ③ 审批语义：编辑已有文件需 approved（工具层兜底，fail-closed）
    if not approved:
        return f"[审批拒绝] 编辑文件需要用户审批: {path}（未获得批准）"
    # ④ 参数校验
    if not old_string:
        return "[参数错误] old_string 不能为空"

    # ⑤ 匹配策略链（原子：全部策略失败 → 不写文件）
    modified = None
    strategy_used = None
    for strategy in _STRATEGIES:
        pattern = _build_pattern(old_string, strategy)
        if pattern.search(original):
            # lambda 替换：new_string 含反斜杠（Windows 路径）时 sub 的
            # 替换串会解释 \1 组引用——lambda 返回字面值，杜绝转义 bug
            count = 0 if replace_all else 1
            modified = pattern.sub(lambda _m: new_string, original, count=count)
            strategy_used = strategy
            break
    if modified is None:
        # 可行动错误（对齐 Hermes format_no_match_hint）：给上下文提示
        preview = " | ".join(line.strip() for line in original.splitlines()[:5])
        return (
            f"[错误] 未找到匹配的 old_string（已尝试策略: {', '.join(_STRATEGIES)}）。"
            f"文件前几行: {preview[:120]}。请检查 old_string 与文件内容是否一致"
            "（注意缩进/空白/换行）"
        )
    if modified == original:
        return "[错误] 替换结果与原文相同（未产生变化）"

    # ⑥ 写回（原子：此刻才落盘）
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(modified)
    except OSError as exc:
        return f"[错误] 写入失败: {exc}"

    # ⑦ diff 展示
    diff = _render_diff(original, modified, path)
    count_desc = "全部" if replace_all else "1"
    return f"[已修改] {path}（替换 {count_desc} 处，策略: {strategy_used}）\n{diff}"


register(
    name="patch",
    toolset="builtin",
    handler=patch,
    description=(
        "精确编辑文件（小改动替换，非整文件重写）：old_string → new_string。"
        "支持行尾/空白/缩进容错匹配；返回 diff。"
        "【边界】新建/整文件重写用 write_file；本工具只改已有文件的局部内容"
    ),
    # 审批声明（v0.4.26 声明式）：编辑已有文件 = 覆盖语义 → 无条件审批
    approval="patch 编辑 {path}",
    # 手写 schema：只暴露 path/old_string/new_string/replace_all——
    # approved 是内部参数（agent 审批注入，防绕过）
    schema={
        "type": "function",
        "function": {
            "name": "patch",
            "description": (
                "精确编辑文件（局部替换）：把 old_string 替换为 new_string。"
                "匹配支持换行/行尾空白/缩进漂移容错；成功后返回 diff。"
                "编辑会弹出审批请求，用户同意后执行；若被拒绝"
                "（[审批拒绝]）不要反复尝试"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目标文件路径",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "待替换的原文片段（需与文件内容匹配）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "替换后的新文",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "True=替换全部匹配；False=只第一处（默认）",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
)
