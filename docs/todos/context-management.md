# 上下文管理 TODO（docs/todos/context-management.md）

> 创建：2026-08-18 · 基线：v0.4.6（工具调用深化中，阶段 3 记忆会话未开始）
> 目标：让 agent 能长时间对话——上下文窗口有限（DeepSeek 约 64K-128K tokens），历史只增不减必然撑爆/费用暴涨/质量下降
> 参考：Hermes agent/ 目录（context_engine.py 可插拔引擎 + context_compressor.py 压缩器 + conversation_compression.py + context_breakdown.py 构成分析 + prompt_caching.py）
> 约定：⬜ 未开始 / 🚧 进行中 / ✅ 已完成；P0 核心 / P1 重要 / P2 增强

---

## 先理解：上下文管理的三大手段（Hermes 对照）

| 手段 | 原理 | Hermes 对应 |
|------|------|-------------|
| **估算**（感知） | 知道每轮请求占多少 token——没有度量就没有管理 | context_breakdown.py（构成分解）+ update_from_response（usage 跟踪） |
| **预防**（裁剪） | 超过上限就丢旧消息（滑动窗口） | 会话窗口裁剪 + 工具结果截断 |
| **压缩**（摘要） | 把早期对话用 LLM 压成摘要，保住关键信息 | ContextEngine 抽象基类 + compress_context（摘要生成、分割 SQLite 会话、轮换 session_id） |

**关键认知（Hermes AGENTS.md 原话）：prompt caching 是神圣的**——长对话每轮复用缓存前缀。任何"中途改动过去上下文"的操作都会打爆缓存翻倍费用；压缩是 Hermes 唯一允许的例外。我们做裁剪/压缩策略时，把"前缀尽量稳定"当成设计约束（DeepSeek 有上下文缓存，命中后便宜很多）。

---

## 一、上下文感知基础（地基：没有度量就没有管理）

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **token 估算器** | P0 | ⭐⭐ | `estimate_tokens(text) -> int`：先用 char/4 粗略估算（Hermes estimate_request_tokens_rough 同款启发式，对齐压缩阈值用，不追求精确）；可选升级 tiktoken 精确计数 |
| ⬜ | **API usage 跟踪** | P1 | ⭐⭐ | llm.py 从响应 usage 字段透出 prompt/completion_tokens，agent 累计；会话结束汇总打印（与 debugger 联动） |
| ⬜ | **上下文构成分解** | P1 | ⭐⭐ | 估算 system prompt / 工具 schema / 对话历史各自的 token 占比（Hermes context_breakdown 思路）；--debug 模式展示"当前上下文占用 X%"，压缩触发前用户可见 |

## 二、历史裁剪（预防：简单可靠，先跑通）

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **滑动窗口裁剪（history_limit）** | P0 | ⭐⭐⭐ | 超过 N 条消息时裁剪最旧的非 system 消息。**两大坑必须处理**：① tool_calls 消息与其 tool 结果消息必须成对裁剪/保留（拆散 = 协议错误）；② 删中间消息可能造成连续两条同 role（OpenAI 协议拒绝，Hermes 规范明令禁止）——裁剪后需校验 role 交替，必要时合并/丢弃 |
| ⬜ | **工具结果截断统一收口** | P0 | ⭐ | 现各工具各自截断 2000 字符（read_file/shell/run_python 各自为政）——升级为 registry 层集中截断（execute_tool 出口统一处理），工具只管返回、截断策略一处改 |
| ⬜ | **关键信息保留（sticky notes）** | P1 | ⭐⭐ | 用户显式要求记住的信息（"我叫小明"）不能被裁剪掉——简单版：system prompt 维护 sticky 区，用户/agent 可写入，裁剪永不碰它 |

## 三、上下文压缩（进阶：保住关键信息的裁剪）

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **触发条件（should_compress）** | P0 | ⭐⭐⭐ | 每轮后估算 token 总量 > 阈值（默认窗口 70%，可配置）触发。参考 Hermes ContextEngine 生命周期：update_from_response（每轮收 usage）→ should_compress（每轮检查）→ compress |
| ⬜ | **摘要压缩（summarize）** | P0 | ⭐⭐⭐ | 用 LLM 把早期对话压成摘要（独立对话调用，不污染主对话 token）：保留关键事实/用户要求/工具结果结论。压缩后历史 = system + 摘要 + 最近未压缩消息。UI 提示"正在压缩上下文以继续…"（Hermes COMPACTION_STATUS） |
| ⬜ | **压缩模型独立配置** | P1 | ⭐⭐ | 压缩可走独立模型/独立对话（Hermes 启动时探测辅助模型上下文能否容纳压缩阈值，不能则自动降阈值或硬拒绝） |
| ⬜ | **手动压缩与占用查看** | P2 | ⭐⭐ | CLI 命令 `/context`（看占用构成）+ `/compact`（手动触发压缩）——与 debug 联动 |

## 四、与现有/规划系统的集成

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **todo 状态注入上下文（自我跟踪）** | P1 | ⭐⭐ | **2026-08-22 真实暴露**：todo 工具实际只建 2 条任务，模型却脑补 9 步清单展示给用户——todo 是"被动工具"，模型靠对话记忆脑补，从不主动 list。修复：每次 LLM 请求前把活动任务（pending/in_progress）注入**发送消息副本**（对齐 Hermes format_for_injection：只注入活动任务防模型重做已完成工作；注入副本不污染 self.messages）。注入口：agent.py chat 循环 client.chat 前（pre-step 事件后）。与 sticky notes 的关系：todo 注入 = 自动注入、sticky = 用户显式保留——统一收敛为"上下文注入层" |
| ⬜ | **与阶段 3 记忆会话联动** | P1 | ⭐⭐⭐ | SQLite 存全量历史（永不丢），内存只留窗口内消息；压缩后旧会话归档/轮换 session_id（Hermes 分割会话思路）。**依赖：阶段 3 先落地** |
| ⬜ | **prompt caching 友好设计** | P2 | ⭐⭐⭐ | 裁剪/压缩策略以"前缀稳定"为约束：system+早期固定部分放最前（缓存命中区），变动频繁的最近对话放最后；压缩是唯一允许破坏缓存的例外 |
| ⬜ | **长对话评测** | P2 | ⭐⭐ | 评测集加长对话用例：连跑 50 轮后回答质量不下降、token 不超限、压缩后关键事实仍记得（与 evaluation.md 联动） |

---

## 完成记录

| 日期 | 条目 | commit/tag | 备注 |
|------|------|-----------|------|
|      |      |           |      |

## 备注（演进思路）

- **顺序建议**：一（估算器）→ 二（裁剪）→ 三（压缩）→ 四（集成）。估算器是裁剪/压缩的公共地基，必须先有
- **裁剪 vs 压缩的取舍**：裁剪简单可靠但丢信息；压缩保信息但费一次 API 调用。生产级做法是"裁剪保底 + 压缩保质量"——先裁剪跑通，再上压缩
- **role 交替与成对性是裁剪的死线**：实现时必须有专项测试（裁剪后历史能正常发给 API 不 400）
- **与路线图的关系**：阶段 4 配置里有 history_limit 字段（YAML 外置裁剪参数）——裁剪实现时把参数做成可配置，阶段 4 自然衔接
- **与 TODO 其他清单联动**：evaluation.md 的"长对话评测"依赖本清单；tool-calling.md 的"工具调用成本追踪"与本清单的 usage 跟踪共用数据
