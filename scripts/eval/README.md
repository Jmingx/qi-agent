# Opik 评测接入 POC

这组脚本只做一件事: 把 `qi-agent serve` 的真实 WS 闭环接到本地自托管 Opik，跑 3 条低 token 验证用例，并把规则结果写进 dataset / experiment。

## 需要什么

先准备本地 Opik 自托管和 Python 依赖。

### Python 依赖

```bash
pip install opik websockets
```

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPIK_URL_OVERRIDE` | `http://127.0.0.1:5173/api` | 本地 Opik 后端地址 |
| `OPIK_WORKSPACE` | `default` | 本地默认 workspace |
| `OPIK_PROJECT_NAME` | `qi-agent-smoke` | 这次 POC 的 project 名称 |
| `QI_AGENT_SERVE_URL` | `ws://127.0.0.1:8771` | `qi-agent serve` 的 WS 地址 |
| `QI_AGENT_SERVE_PORT` | `8771` | 如果脚本需要自启 serve，就用这个端口 |
| `QI_AGENT_DATASET_NAME` | `qi-agent-smoke` | Opik dataset 名称 |
| `QI_AGENT_EXPERIMENT_NAME` | `2026-09-03-qi-agent-smoke` | Opik experiment 名称 |

## 怎么跑

1. 先启动 Docker Desktop。
2. 在官方 `comet-ml/opik` 仓库的 `deployment/docker-compose/` 下起本地 Opik:

```powershell
cd C:\Users\xie\AppData\Local\Temp\opik-selfhost\deployment\docker-compose
docker compose -f docker-compose.yaml -f docker-compose.override.yaml --profile opik up -d
```

3. 跑这次的 3 条验证用例:

```bash
python scripts/eval/run_eval.py
```

如果你已经手动起了 `qi-agent serve ws://127.0.0.1:8771`，可以加 `--no-start-serve`。

## 省钱红线

- 只跑 `cases.jsonl` 里的 3 条，不加量
- 不跑 pass@k
- 不跑 benchmark 全量集
- 不启用 LLM judge
- 调试时不要反复重跑同一条 case

## 这脚本怎么工作

1. 读取 `cases.jsonl` 里的 3 条低 token 用例。
2. 如果本地 8771 端口没起，就自动启动 `python -m qi_agent.serve --port 8771`。
3. 每条 case 通过 WS JSON-RPC 走 `session/create` 和 `message/send`。
4. 脚本收集 tool call、回复文本、turn、token 和耗时。
5. 用纯规则断言是否通过，然后把结果写进 Opik dataset / experiment。
6. 最后再用 Opik API 复查 dataset / experiment 里是否能看到 3 条结果。
