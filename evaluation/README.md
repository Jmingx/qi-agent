# 统一评测入口

评测代码和用例的规范位置是本目录：

```bash
python -m evaluation.run --suite <evaluation/suites 下的 JSON 文件名>
python -m evaluation.run --suite all
```

例如：

```bash
python -m evaluation.run --suite smoke
python -m evaluation.run --suite regression
python -m evaluation.run --suite subagent
```

调试单个用例时可以按 `case_id` 过滤：

```bash
python -m evaluation.run --suite smoke --case-id time_tool
python -m evaluation.run --case-id time_tool
```

第二种写法会在全部 JSONL 套件中查找该 ID；ID 不存在或重复时会直接报错，
不会启动评测 Gateway。

`--suite` 的可选值会在启动时从 `evaluation/suites/*.jsonl` 自动发现；新增
套件只需新增同名 JSONL 文件。不传参数时只打印帮助，不会启动评测。

所有套件都使用同一个 Gateway runner。
所有 Agent 执行都连接独立的
`qi_agent.evaluation_serve`，并在每个 Case 周围调用 `eval/prepare` 与
`eval/cleanup`。

用例唯一真源是 `evaluation/suites/*.jsonl`，每个 JSONL 文件名就是一个套件名。
每行必须是一个 object，`id` 在文件内及本次加载的套件集合内唯一，
错误会报告文件名和行号。`tasks.py` 只保留旧 `EvalTask` 和常量别名，不能
再在 Python 中新增任务。

长任务、多步对话、工具 AND/ANY、关键词最少次数、禁止工具、安全拦截、
记忆断言和 rubric 都使用同一声明式字段；memory、sticky、Todo、history
以及 plugin override 通过 `preconditions` 传给评测 Gateway。

每次运行还会把结果写入统一 Opik project `qi-agent-evaluation`：每个 suite
对应一个 Dataset，每次运行对应一个 Experiment，每个 case 的 Trace 会通过
Experiment Item 同时关联 Dataset Item。CLI 会打印 Dataset 和 Experiment 名称，
用于从 Opik 页面反向定位本次运行。

评测执行、结果对比和数据模型统一位于 `evaluation/`，新代码应导入
`evaluation.case_models`、`evaluation.serve_control` 和
`evaluation.gateway_runner`。
