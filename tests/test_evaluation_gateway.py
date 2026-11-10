"""评测 Gateway 旁路控制面的协议和 Fixture 测试。"""

from __future__ import annotations

import json

from qi_agent.evaluation_gateway import EvaluationGateway
from qi_agent.gateway.protocol import RpcDispatcher
from evaluation.gateway_runner import _preconditions
from evaluation.tasks import EvalTask


class FakeManager:
    def __init__(self) -> None:
        self.contexts = {}

    def get_context(self, context_id: str):
        return self.contexts.get(context_id)

    def register(self, context, role="main"):
        self.contexts[context.id] = context
        return context.id


class FakeGateway:
    def __init__(self) -> None:
        self.manager = FakeManager()
        self.dispatcher = RpcDispatcher()
        self.shell_callback = None

    class _Storage:
        def create_session(self, session_id, title=""):
            return None

        def delete_session(self, session_id):
            return None

    def _storage(self):
        return self._Storage()

    def _create_session(self, *, goal: str, metadata: dict[str, str]) -> dict[str, str]:
        from qi_agent.context.context import AgentContext

        context = AgentContext(goal=goal, metadata=metadata)
        self.manager.contexts[context.id] = context
        return {"session_id": context.id}

    def _session_delete(self, session_id: str) -> dict[str, bool]:
        self.manager.contexts.pop(session_id, None)
        return {"ok": True}


def test_eval_prepare_injects_history_without_changing_normal_gateway(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "qi_agent.evaluation_gateway._snapshot_memory",
        lambda: {},
    )
    monkeypatch.setattr(
        "qi_agent.evaluation_gateway._restore_memory",
        lambda snapshot: None,
    )
    gateway = EvaluationGateway(FakeGateway())

    prepared = gateway._prepare(
        case_id="history_seed",
        run_id="run-1",
        preconditions={
            "history": [
                {"role": "user", "content": "我叫张三"},
                {"role": "assistant", "content": "你好，张三"},
            ]
        },
    )

    context = gateway.manager.get_context(prepared["session_id"])
    assert context is not None
    messages = context.events.waterfall(
        "agent/pre-step",
        [{"role": "system", "content": "system"}, {"role": "user", "content": "问题"}],
        turn=1,
        step=0,
    )
    assert [message["content"] for message in messages] == [
        "system",
        "我叫张三",
        "你好，张三",
        "问题",
    ]

    cleaned = gateway._cleanup(prepared["session_id"])
    assert cleaned["ok"] is True
    assert gateway.manager.get_context(prepared["session_id"]) is None


def test_eval_dispatcher_keeps_normal_methods_on_base_gateway() -> None:
    base = FakeGateway()
    base.dispatcher.register("normal/ping", lambda: {"ok": True})
    gateway = EvaluationGateway(base)

    response = gateway.dispatcher.dispatch(
        '{"jsonrpc":"2.0","id":1,"method":"normal/ping","params":{}}'
    )

    assert json.loads(response)["result"] == {"ok": True}


def test_task_preconditions_are_translated_to_eval_fixture() -> None:
    task = EvalTask(
        "c-long-3", "context", "todo", ["查看任务"],
        plugin_overrides={"context_manager": {"compress": {"window": 6000}}},
    )

    prepared = _preconditions(task)

    assert prepared["context"]["workspace"]["todos"] == [
        {"title": "写周报", "status": "pending"}
    ]
    assert prepared["context"]["plugin_overrides"]["context_manager"]
