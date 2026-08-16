# 02-LLM对话原理与消息历史管理

> 对应阶段：阶段 1（最小对话闭环）
> 日期：2026-08-14

## 1. 最核心的认知：模型没有记忆

很多人误以为 LLM"记得"之前的对话。真相是：

> **Chat Completion API 是一个无状态函数。** 输入消息列表 → 输出一条回复。模型每次请求都是"第一次见到这些消息"。

"多轮对话的上下文"完全靠**客户端**实现：每次请求把**全部历史消息**重新发给 API。模型只是"看到"了你发给它的历史，从而表现得像记得。

```
第1轮请求: [system, user:"我叫小明"]                    → 回复"你好小明"
第2轮请求: [system, user:"我叫小明", assistant:"你好小明",
            user:"我叫什么名字？"]                        → 回复"你叫小明"
```

第 2 轮请求携带了第 1 轮的全部消息——这就是"记忆"的全部秘密。

### 类比

想象一个**每次都会失忆的图书管理员**。你每次问他问题，都要把之前的对话记录塞给他看。他看了记录就能"接上话"，但不看就什么都不记得。API 就是这个失忆的管理员，消息列表就是塞给他的记录。

## 2. 消息结构：role 与 content

每条消息是 `{"role": ..., "content": ...}`，role 有三种：

| role | 含义 | 谁来写 |
|------|------|--------|
| `system` | 系统指令：设定人设、行为边界、输出格式 | 开发者（永远第一条） |
| `user` | 用户说的话 | 客户端 |
| `assistant` | 模型的回复 | 客户端收到后原样存回 |

### 硬性规则：role 交替

**user 和 assistant 必须严格交替出现**，不能连续两条 user 或两条 assistant。原因：
- API 层面会报错或行为异常
- 逻辑上：user 消息是"输入"，assistant 消息是"输出"，必须成对出现才构成完整的一轮

这要求消息历史维护代码必须按"请求-响应"严格配对追加。我们的 `Agent.chat()` 就是这么做的：

```python
def chat(self, user_input: str) -> str:
    self.messages.append({"role": "user", "content": user_input})       # 请求
    reply = self.client.chat(self.messages)                              # 发全部历史
    self.messages.append({"role": "assistant", "content": reply})        # 响应
    return reply
```

## 3. 架构设计：为什么分三层

```
cli.py（交互）→ agent.py（状态）→ llm.py（通信）
```

| 层 | 职责 | 它不知道的事 |
|----|------|-------------|
| `cli.py` | 读输入、打印回复、处理退出 | 模型是谁、历史怎么存 |
| `agent.py` | 持有 messages 列表，实现对话逻辑 | 网络细节、HTTP 协议 |
| `llm.py` | 调 API：发消息列表、拿回复文本 | 对话是什么、界面长什么样 |

**核心思想：职责单一 + 依赖方向单向。** 上层依赖下层，下层不知道上层的存在。好处：
- 换模型：只改 llm.py 内部
- 加工具：只改 agent.py
- 换界面（Web/桌面/微信）：只换 cli.py

## 4. 为什么用 OpenAI SDK 调 DeepSeek

DeepSeek API 与 OpenAI 协议完全兼容（同样的 `/chat/completions` 端点、同样的 JSON 格式），所以：

```python
OpenAI(api_key=key, base_url="https://api.deepseek.com")
```

**一个 SDK 走天下**——SDK 帮你处理了 HTTP 重试、超时、错误解析、类型提示。更重要的是，阶段 2 的**工具调用**（tool calling）和阶段 5 的**流式输出**都是 SDK 原生能力，现在选对，后面免费。

## 5. 测试策略：mock 让测试不花钱不联网

单元测试绝不能真实调用 API（花钱、依赖网络、慢）。做法：**测试替身（Fake）**。

```python
class FakeClient:
    def __init__(self, reply="fake reply"):
        self.reply = reply
        self.received = []          # 记录每次收到的消息

    def chat(self, messages):
        self.received.append(messages)
        return self.reply
```

FakeClient 的接口和真 LLMClient 完全一样（鸭子类型），但立即返回固定字符串。测试就能断言：
- 用户消息有没有进历史
- 第二次请求有没有带上第一轮的历史（**多轮上下文的核心验证**）

```python
def test_two_turns_context():
    agent, fake = make_agent()
    agent.chat("我叫小明")
    agent.chat("我叫什么名字？")
    assert len(fake.received[1]) == 5   # system+user+assistant+user+assistant
```

`test_two_turns_context` 是整个阶段最重要的测试——它用断言把"多轮上下文"这个需求钉死在代码里，以后任何重构只要破坏了这个行为，测试立刻红。

## 6. 踩过的坑

### 6.1 测试断言设计失误

`test_user_message_appended` 最初断言"chat() 后历史最后一条是 user"——这是错的：chat() 完成后最后一条必然是 assistant（一轮对话以模型回复结束）。修正为断言 user 消息位于 `history[1]`（system 之后）。

**教训：测试断言必须符合真实设计语义**，不能凭直觉写。

### 6.2 PYTHONPATH 环境污染（环境级坑）

本机 Hermes 终端会注入 `PYTHONPATH` 指向 Hermes 自己的 venv，导致 `uv run python` 导入 openai/pydantic 时命中了**损坏的 Hermes venv 副本**而不是项目自己的 .venv。现象：pytest 正常但 `python -c "import openai"` 报 `ModuleNotFoundError: pydantic_core._pydantic_core`。

解决：运行命令加前缀 `PYTHONPATH= uv run python ...`。pytest 不受影响（它的启动机制不同）。

## 7. 与 Hermes 的对照

Hermes 的对话核心（`agent/` 目录）遵循完全相同的原理：维护消息历史、循环调用 LLM、工具结果回填。只是规模大得多——它还有多轮工具调用循环、流式输出、多平台适配。**你现在的 Agent 类就是 Hermes agent 内核的最小雏形。**

## 8. 本阶段的产物

```
qi_agent/llm.py     LLMClient：DeepSeek 客户端封装（~30行）
qi_agent/agent.py   Agent：消息历史管理 + 多轮对话（~50行）
qi_agent/cli.py     REPL 入口（~50行）
tests/test_agent.py 6 个单元测试（FakeClient mock）
```

全部代码 ≤50 行/文件，符合 AGENTS.md 规范（单文件 ≤300 行、单方法 ≤50 行）。
