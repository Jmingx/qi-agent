"""独立评测 Gateway 入口，不修改生产 qi_agent.serve。"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import websockets

from qi_agent.evaluation_gateway import EvaluationGateway
from qi_agent.serve import ServeTransport


def _merge_dicts(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


class EvaluationServeTransport(ServeTransport):
    """评测 transport：按 Session 注入白名单 plugin override。"""

    def _attach_context(self, context) -> None:
        original = self._plugin_config
        overrides = getattr(context, "_qi_eval_plugin_overrides", {})
        self._plugin_config = _merge_dicts(original, overrides)
        try:
            super()._attach_context(context)
        finally:
            self._plugin_config = original


def run_ws(host: str, port: int) -> None:
    gateway = EvaluationGateway()
    transport = EvaluationServeTransport(gateway)  # type: ignore[arg-type]

    async def _handler(ws: Any) -> None:
        await transport.handle_connection(ws)

    async def _main() -> None:
        transport.set_loop(asyncio.get_running_loop())
        async with websockets.serve(_handler, host, port):
            print(f"[evaluation-serve] WS: ws://{host}:{port}")
            await asyncio.Future()

    asyncio.run(_main())


def main() -> None:
    parser = argparse.ArgumentParser(description="qi-agent evaluation Gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()
    run_ws(args.host, args.port)


if __name__ == "__main__":
    main()
