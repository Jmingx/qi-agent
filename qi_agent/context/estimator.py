"""token 估算器（阶段 A1）：裁剪/压缩的公共地基。

设计（方案 2026-08-22-上下文管理，对齐 Hermes _chars_to_tokens）：
- char/4 启发式：精度足够阈值判断（should_compress 70% 线），零依赖
- 中文 char/4 偏高（中文字符信息密度高）——已知局限，阈值判断
  受影响极小（趋势正确即可）；tiktoken 精确计数留可选升级
- 工具 schema 走 JSON 序列化后同样 char/4（对齐 Hermes _json_tokens）
"""

import json

# char -> token 估算系数（对齐 Hermes _chars_to_tokens 的 4）
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """估算文本 token 数（char/4，空串保底 1——防除零/零值误判）。"""
    if not text:
        return 1
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_tool_schemas_tokens(schemas: list[dict]) -> int:
    """估算工具 schema 总 token（JSON 序列化后 char/4）。"""
    if not schemas:
        return 0
    return estimate_tokens(json.dumps(schemas, ensure_ascii=False))


def estimate_token_usage(usages: list[dict]) -> dict:
    """多个 usage 记录累计（prompt/completion/total 各自加总）。"""
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for u in usages:
        if not u:
            continue
        for key in total:
            total[key] += int(u.get(key, 0) or 0)
    return total
