"""Skill 注册表、渐进注入与 Gateway 显式激活测试。"""

from pathlib import Path

from qi_agent.events import EventBus
from qi_agent.gateway.gateway import Gateway
from qi_agent.plugins.builtin.skill_index import SkillIndexPlugin
from qi_agent.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, description: str, aliases: str = "") -> None:
    target = root / name
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "categories: [testing]\n"
        "tags: [pytest]\n"
        f"aliases: [{aliases}]\n"
        "version: 1.0.0\n"
        "---\n"
        f"# {name}\n正文",
        encoding="utf-8",
    )


def test_registry_project_overrides_user_and_searches_alias(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    project_root = tmp_path / "project"
    _write_skill(user_root, "evaluation-run", "旧描述", "跑评测")
    _write_skill(project_root, "evaluation-run", "项目评测流程", "验证评测")
    registry = SkillRegistry(user_root=user_root, project_root=project_root)

    item = registry.get("evaluation-run")
    assert item is not None
    assert item.scope == "project"
    assert item.description == "项目评测流程"
    assert registry.search("请帮我验证评测")[0].name == "evaluation-run"


def test_view_rejects_path_escape_and_invalid_type(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "evaluation-run", "评测")
    registry = SkillRegistry(user_root=root, project_root=tmp_path / "none")

    assert "正文" in registry.view("evaluation-run")
    assert "安全拦截" in registry.view("evaluation-run", "../secret.md")
    assert "安全拦截" in registry.view("evaluation-run", "script.py")


def test_plugin_keeps_candidates_and_activation_only_for_current_turn(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "evaluation-run", "评测", "跑评测")
    registry = SkillRegistry(user_root=root, project_root=tmp_path / "none")
    monkeypatch.setattr("qi_agent.plugins.builtin.skill_index.get_skill_registry", lambda: registry)
    bus = EventBus()
    plugin = SkillIndexPlugin()
    plugin.install(bus)
    bus._qi_active_skill = {"name": "evaluation-run", "content": "正文", "turn": 1}
    messages = [{"role": "user", "content": "跑评测"}]

    first = plugin._inject(messages, turn=1)
    assert any("[Skill Manifest]" in item["content"] for item in first)
    assert any("[Skill Candidates]" in item["content"] for item in first)
    assert any("[Active Skill]" in item["content"] for item in first)

    next_turn = plugin._inject(first, turn=2)
    assert not any("[Active Skill]" in item["content"] for item in next_turn)


def test_gateway_skill_activate_runs_with_selected_skill(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "evaluation-run", "评测", "跑评测")
    registry = SkillRegistry(user_root=root, project_root=tmp_path / "none")
    monkeypatch.setattr("qi_agent.skills.registry._REGISTRY", registry)

    import qi_agent.agents.factory as factory
    from qi_agent.llm import ChatResult

    class FastClient:
        def chat(self, messages, tools=None):
            return ChatResult(
                content="ok",
                tool_calls=[],
                assistant_message={"role": "assistant", "content": "ok"},
                usage=None,
            )

        def chat_stream(self, messages, tools=None, on_delta=None):
            return self.chat(messages, tools)

    monkeypatch.setattr(factory, "load_api_key", lambda: "sk-test")
    monkeypatch.setattr(factory, "LLMClient", lambda _: FastClient())
    gateway = Gateway()
    session_id = gateway._create_session()["session_id"]
    result = gateway._skill_activate(session_id, "evaluation-run", "请跑评测")

    assert result["reply"] == "ok"
    assert result["skill_id"] == "evaluation-run"
