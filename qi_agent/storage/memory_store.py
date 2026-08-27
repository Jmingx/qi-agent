"""记忆存储（§ 分隔条目——完全对齐 Hermes MemoryStore，方案 2026-08-26）。

业界实证（2026-08-26 源码调研）：
  Hermes：MEMORY.md + USER.md，条目用 "§" 分隔——
    entries = raw.split("§")（§ 是条目分隔符，不是分节头）
    去重 list(dict.fromkeys)；字符上限 memory_char_limit/user_char_limit
  DSH：skill 系统原文给 LLM（零解析）
  → 我们完全对齐 Hermes：§ 分隔条目 + 去重 + 字符上限，零 Markdown 解析

分层（按内容范围）：
  MEMORY.md   长期知识（事实/项目约定/经验——agent 维护）
  USER.md     用户画像（偏好/身份/关系——用户相关）

文件格式（~/.qi-agent/）：
  MEMORY.md:
    § qi-agent 是 Python agent 框架
    § 用户喜欢简洁回答
    § 项目：qi-agent

API：
  read_memory() -> str         读 MEMORY.md + USER.md（合并注入）
  add_memory(text)             § 追加条目（去重——重复不写）
  remove_memory(text)          删除条目
  list_entries()               列出全部条目
"""

import os
import threading

# 条目分隔符（Hermes 同款）
ENTRY_DELIMITER = "§"

# 记忆字符上限（Hermes 同款——防无限膨胀）
MEMORY_CHAR_LIMIT = 2200
USER_CHAR_LIMIT = 1375

# 记忆文件默认目录（~/.qi-agent/——对齐 SQLite 数据目录）
_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".qi-agent")


class MemoryStore:
    """§ 分隔条目记忆（MEMORY.md + USER.md——零 Markdown 解析）。"""

    def __init__(self, dir_path: str | None = None) -> None:
        self.dir_path = dir_path or _DEFAULT_DIR
        os.makedirs(self.dir_path, exist_ok=True)
        self.memory_path = os.path.join(self.dir_path, "MEMORY.md")
        self.user_path = os.path.join(self.dir_path, "USER.md")
        self._lock = threading.Lock()  # 写锁（防并发写文件交错）

    # ── 内部 ─────────────────────────────────────────────────────────────
    def _read_file(self, path: str) -> str:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return ""

    def _write_file(self, path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _parse_entries(self, content: str) -> list[str]:
        """按 § 切分条目（Hermes 同款——§ 是条目分隔符）。"""
        return [e.strip() for e in content.split(ENTRY_DELIMITER) if e.strip()]

    def _char_limit(self, target: str) -> int:
        return USER_CHAR_LIMIT if target == "user" else MEMORY_CHAR_LIMIT

    # ── 读写 ─────────────────────────────────────────────────────────────
    def read_memory(self) -> str:
        """读全部记忆（MEMORY.md + USER.md 合并）——注入 system 用。

        返回原文（LLM 理解 § 分隔——不程序解析，DSH 哲学）。
        """
        memory = self._read_file(self.memory_path)
        user = self._read_file(self.user_path)
        if memory and user:
            return f"{memory}\n\n{user}"
        return memory or user

    def add_memory(self, text: str, target: str = "memory") -> None:
        """§ 追加一条记忆（去重——重复不写，Hermes 同款）。

        Args:
            text: 记忆内容（可多行）
            target: "memory"（MEMORY.md）/"user"（USER.md）
        """
        text = text.strip()
        if not text:
            return
        path = self.user_path if target == "user" else self.memory_path
        with self._lock:
            content = self._read_file(path)
            entries = self._parse_entries(content)
            # 去重（保序——重复条目不写）
            if text in entries:
                return
            entries.append(text)
            # 渲染：每条记忆 § 开头（第一条也要——split 依赖 §）
            rendered = f"{ENTRY_DELIMITER} " + f"\n{ENTRY_DELIMITER} ".join(entries)
            # 字符上限（Hermes 同款——超限截断）
            limit = self._char_limit(target)
            if len(rendered) > limit:
                rendered = rendered[:limit]
            self._write_file(path, rendered + "\n")

    def remove_memory(self, text: str, target: str = "memory") -> None:
        """删除条目（精确匹配）。"""
        path = self.user_path if target == "user" else self.memory_path
        with self._lock:
            content = self._read_file(path)
            entries = self._parse_entries(content)
            entries = [e for e in entries if e != text.strip()]
            rendered = (f"{ENTRY_DELIMITER} " + f"\n{ENTRY_DELIMITER} ".join(entries)
                        if entries else "")
            self._write_file(path, rendered + "\n" if rendered else "")

    def list_entries(self, target: str = "memory") -> list[str]:
        """列出全部条目。"""
        path = self.user_path if target == "user" else self.memory_path
        return self._parse_entries(self._read_file(path))
