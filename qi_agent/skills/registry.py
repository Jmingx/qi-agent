"""扁平 Skill 包的扫描、检索和受控读取。

Skill 包只保存可分发知识；安装作用域、启用状态和使用统计属于运行时注册表，
不写回用户的 SKILL.md。
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_TEXT_SUFFIXES = frozenset({".md", ".txt"})
_MAX_CONTENT_CHARS = 20_000
_DEFAULT_USER_ROOT = Path.home() / ".qi-agent" / "skills"


@dataclass(frozen=True)
class SkillRecord:
    """注册表中的可用 Skill。"""

    name: str
    description: str
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    version: str
    root: Path
    scope: str


class SkillRegistry:
    """扫描扁平目录，提供可解释的关键词召回与受控文本读取。"""

    def __init__(
        self,
        user_root: Path | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.user_root = user_root or Path(
            os.getenv("QI_AGENT_SKILLS_DIR", str(_DEFAULT_USER_ROOT))
        )
        self.project_root = project_root or (Path.cwd() / "skills")
        self._records: dict[str, SkillRecord] = {}
        self._cache: OrderedDict[tuple[str, int], str] = OrderedDict()
        self.refresh()

    def refresh(self) -> None:
        """原子替换扫描结果；项目级同名 Skill 覆盖用户级。"""
        records: dict[str, SkillRecord] = {}
        for root, scope in ((self.user_root, "user"), (self.project_root, "project")):
            for record in self._scan_root(root, scope):
                records[record.name] = record
        self._records = records

    def list(self) -> list[SkillRecord]:
        return sorted(self._records.values(), key=lambda item: item.name)

    def get(self, name: str) -> SkillRecord | None:
        return self._records.get(name)

    def search(self, query: str, limit: int = 3) -> list[SkillRecord]:
        """按名称、别名、标签、分类和描述进行确定性召回。"""
        normalized_query = query.lower()
        tokens = {token.lower() for token in re.findall(r"[\w\u4e00-\u9fff-]+", query)}
        scored: list[tuple[int, SkillRecord]] = []
        for record in self._records.values():
            fields = (
                (record.name, 8),
                *[(value, 6) for value in record.aliases],
                *[(value, 4) for value in record.tags],
                *[(value, 3) for value in record.categories],
                (record.description, 2),
            )
            score = sum(
                weight
                for text, weight in fields
                if text.lower() in normalized_query
                or any(token in text.lower() for token in tokens)
            )
            if score:
                scored.append((score, record))
        ranked = sorted(scored, key=lambda pair: (-pair[0], pair[1].name))
        return [item for _, item in ranked[:limit]]

    def manifest(self, limit: int = 20) -> str:
        """给模型的 L0 极简 manifest；调用方可按 token 预算截断。"""
        records = self.list()[:limit]
        if not records:
            return "当前没有可用 Skill。"
        return "\n".join(f"- {item.name}: {item.description}" for item in records)

    def view(self, name: str, resource_path: str = "") -> str:
        """只读取已注册 Skill 根目录内的受限文本资源。"""
        record = self.get(name)
        if record is None:
            return f"[Skill错误] 未注册的 Skill: {name}"
        relative = Path(resource_path) if resource_path else Path("SKILL.md")
        if relative.is_absolute() or ".." in relative.parts:
            return "[Skill安全拦截] resource_path 必须是 Skill 内的相对路径。"
        target = (record.root / relative).resolve()
        try:
            target.relative_to(record.root.resolve())
        except ValueError:
            return "[Skill安全拦截] 资源路径越出 Skill 目录。"
        if target.suffix.lower() not in _TEXT_SUFFIXES:
            return "[Skill安全拦截] 仅允许读取 Markdown 或文本资源。"
        if not target.is_file():
            return f"[Skill错误] 资源不存在: {relative.as_posix()}"
        key = (str(target), target.stat().st_mtime_ns)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        text = target.read_text(encoding="utf-8")
        if len(text) > _MAX_CONTENT_CHARS:
            text = text[:_MAX_CONTENT_CHARS] + "\n...[Skill 内容过长已截断]"
        self._cache[key] = text
        self._cache.move_to_end(key)
        while len(self._cache) > 32:
            self._cache.popitem(last=False)
        return text

    def _scan_root(self, root: Path, scope: str) -> list[SkillRecord]:
        if not root.is_dir():
            return []
        records: list[SkillRecord] = []
        for item in root.iterdir():
            skill_file = item / "SKILL.md"
            if not item.is_dir() or not skill_file.is_file():
                continue
            try:
                metadata = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
                name = metadata["name"]
                description = metadata["description"]
                if not _NAME_RE.fullmatch(name):
                    continue
                records.append(SkillRecord(
                    name=name,
                    description=description,
                    categories=tuple(_as_list(metadata.get("categories", ""))),
                    tags=tuple(_as_list(metadata.get("tags", ""))),
                    aliases=tuple(_as_list(metadata.get("aliases", ""))),
                    version=metadata.get("version", "0.0.0"),
                    root=item,
                    scope=scope,
                ))
            except (OSError, ValueError):
                continue
        return records


def _parse_frontmatter(content: str) -> dict[str, str]:
    if not content.startswith("---\n"):
        raise ValueError("缺少 YAML frontmatter")
    _, raw, _ = content.split("---", 2)
    metadata: dict[str, str] = {}
    for line in raw.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    if not metadata.get("name") or not metadata.get("description"):
        raise ValueError("name/description 为必填")
    return metadata


def _as_list(value: str) -> list[str]:
    return [
        item.strip().strip('"').strip("'")
        for item in value.strip("[]").split(",")
        if item.strip()
    ]


_REGISTRY: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SkillRegistry()
    return _REGISTRY
