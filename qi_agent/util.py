"""统一 ID 生成（2026-08-29 用户拍板：message_id/agent_id/context_id
都走同一规则——<类型>_<时间戳>_<uuid>）。

格式：<prefix>_<YYYYMMDD>_<HHMMSS>_<uuid6>
  例：msg_20260829_013000_3f2a9c
      agt_20260829_013000_8b1e4d
      ctx_20260829_013000_c7d2f1

用途：所有实体 ID（会话/执行者/消息/子任务）统一可读性——
  一眼看出类型 + 创建时间 + 唯一性（uuid 防同秒冲突）。
"""

import uuid
from datetime import datetime


def generate_id(prefix: str) -> str:
    """生成统一 ID：<prefix>_<YYYYMMDD>_<HHMMSS>_<uuid6>。

    Args:
        prefix: 类型前缀（ctx 会话 / agt 执行者 / msg 消息 / ...）

    Returns:
        形如 "ctx_20260829_013000_3f2a9c" 的 ID 字符串。
    """
    now = datetime.now()
    rand = uuid.uuid4().hex[:6]
    return f"{prefix}_{now:%Y%m%d}_{now:%H%M%S}_{rand}"
