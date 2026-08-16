# 02-工具调用原理与Agent循环

> 对应阶段：阶段 2（工具调用）
> 日期：2026-08-14
> 配套代码：qi_agent/tools/、agent.py 的 Agent.chat()

## 1. 工具调用不是魔法：是"提示词 + 结构化输出"

### 1.1 模型怎么知道有工具可用？

在请求里多传一个 `tools` 参数——用 **JSON Schema** 描述每个工具的签名（名字、描述、参数类型、必填项）：

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "读取指定路径的文件内容",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {"type": "string", "description": "文件路径"}
      },
      "required": ["path"]
    }
  }
}
```

模型看到这些"工具说明书"后，如果判断需要调用，就不再返回普通文本，而是返回结构化 `tool_calls` 对象：**工具名 + 参数（JSON 字符串）+ 调用 id**。

**训练原理：** 模型在训练时见过大量"工具使用"示例，学会了"什么时候该调工具、参数怎么填"。我们只需按协议传对格式。

### 1.2 关键协议细节：arguments 是字符串

模型返回的 `tool_calls[].function.arguments` 是 **JSON 字符串**（如 `"{}"` 或 `"{\"path\": \"/tmp/a.txt\"}"`），不是对象！

我们解析成 dict 执行工具后，**回填历史时必须还原成 JSON 字符串**——这是踩过的真实坑（API 报 `invalid type: map, expected a string`）。

```python
# 解析（执行前）：字符串 → dict
args = json.loads(call.function.arguments or "{}")

# 回填（执行后）：dict → 字符串（协议要求）
"arguments": json.dumps(tc.arguments, ensure_ascii=False)
```

## 2. Agent Loop：循环直到"想好为止"

### 2.1 循环结构

```
用户提问
   │
   ▼
调 LLM（带工具清单） ──► 模型直接回答 → 这是最终答案，结束 ✅
   │
   ▼
模型返回 tool_calls
   │
   ▼
逐个执行工具 → 结果以 role="tool" 回填历史
   │
   └──► 回到"调 LLM"（循环）
```

**大白话：** agent 先"想"（调 LLM），想调工具就"动手"（执行函数），把结果告诉它，让它继续想……直到它觉得"想清楚了"。

### 2.2 一轮工具调用的完整消息序列

```
[system]   你是一个有用的助手，可以调用工具。
[user]     现在几点了？
[assistant] tool_calls: [{name: "get_time", arguments: "{}", id: "call_1"}]
[tool]     2026-08-14 21:30:00          ← 工具执行结果，tool_call_id 对应 call_1
[assistant] 现在是晚上 9 点半。          ← 最终答案
```

**tool 消息的三个要素：**
- `role: "tool"` — 标记这是工具结果
- `tool_call_id` — 与模型发起的调用一一对应（协议强制要求）
- `content` — 结果内容（必须是字符串）

### 2.3 为什么 assistant 的 tool_calls 消息要原样进历史？

模型发起调用后，**那条带 tool_calls 的 assistant 消息必须完整保留**在历史里（而不是只存"模型说了啥"）。因为：
1. 后续请求中模型需要看到自己之前发起过哪些调用
2. 协议要求：tool 消息必须跟在对应的 assistant tool_calls 消息之后

### 2.4 max_turns：防死循环的保险丝

**风险：** 工具逻辑 bug 或模型陷入"调工具→失败→再调"死循环，每次循环都是一次 API 计费。

**对策：** 限制循环次数（max_turns=8），超限即停止并返回"已达最大轮数"。这是所有 agent 框架的标准防护。

## 3. @tool 装饰器：工具注册机制

### 3.1 设计

```python
_TOOL_REGISTRY: dict[str, dict] = {}   # name -> {fn, description, schema}

def tool(description: str = ""):
    def decorator(fn):
        name = fn.__name__                          # 函数名即工具名
        _TOOL_REGISTRY[name] = {
            "fn": fn,
            "description": description,
            "schema": build_schema(fn, description),  # 签名+注解自动生成
        }
        return fn                                    # 原样返回，不包装
    return decorator
```

### 3.2 三个关键设计决策

| 决策 | 理由 |
|------|------|
| **函数名即工具名** | 零配置，写工具的人不用想名字 |
| **schema 自动生成** | 用 inspect.signature + get_type_hints 从函数签名推导 JSON Schema，不用手动维护 |
| **原样返回函数** | LLM 不需要被包装的版本，只需要注册信息；工具函数仍可被普通调用 |

### 3.3 schema 自动生成的原理

```python
def _build_schema(fn, description):
    sig = inspect.signature(fn)        # 拿到参数列表
    hints = get_type_hints(fn)         # 拿到类型注解
    for name, param in sig.parameters.items():
        param_type = hints.get(name, str)
        # str→"string", int→"integer", bool→"boolean"
        if param.default is not empty: required 不包含它
        else: required 包含它
```

**类型映射表：** `str→string, int→integer, float→number, bool→boolean`。无注解参数默认按 string 处理。本阶段只支持基础类型，复杂嵌套类型（list/dict 参数）暂不支持（YAGNI）。

## 4. ChatResult/ToolCall：为什么返回对象而非字符串

agent 需要区分"模型给了文本"还是"模型要调工具"——裸字符串装不下这个信息。用 @dataclass 定义：

```python
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict          # 解析后的参数字典

@dataclass
class ChatResult:
    content: str | None                    # 文本（无工具调用时非空）
    tool_calls: list[ToolCall] | None      # 调用列表（有调用时非空）
    assistant_message: dict                # 完整 assistant 消息（原样进历史）
```

**判别逻辑：** `content` 非空 = 直接回答；`tool_calls` 非空 = 要调工具。

## 5. 工具执行的安全设计（shell）

shell 工具是双刃剑：能执行命令 = 能破坏系统。本阶段的安全策略：

| 策略 | 实现 |
|------|------|
| **只读白名单** | 只允许 pwd/ls/dir/echo/cat/type 等前缀 |
| **危险关键词拦截** | rm/del/format/shutdown/>/管道/&&/curl 等直接拒绝 |
| **超时限制** | 10 秒超时，防止挂起 |
| **输出截断** | 2000 字符上限，防止撑爆上下文 |
| **错误不崩溃** | 工具异常转为错误消息回填，agent 继续工作 |

**为什么不让 agent 随便执行命令？** LLM 可能被 prompt injection（提示词注入）诱导执行危险命令——"帮我删除所有文件"这类请求模型可能照做。安全边界必须在代码层强制，不能依赖模型自觉。

## 6. 踩过的坑

1. **模块命名冲突 + 循环导入**：同时创建 `tools.py`（模块）和 `tools/`（包目录）导致同名冲突和循环导入。解决：注册机制移入 `tools/registry.py`。
2. **arguments 格式错误**：回填 assistant_message 时把解析后的 dict 直接放进去，API 报 `invalid type: map, expected a string`。解决：`json.dumps` 还原成字符串。
3. **PYTHONPATH 污染影响 pytest**：阶段 2 起测试导入链经过 openai（llm.py），Hermes 终端注入的 PYTHONPATH 让 pytest 也中招。解决：`PYTHONPATH= uv run pytest`。

## 7. 模型不支持工具调用怎么办（降级方案）

### 7.1 问题

不是所有模型都支持原生 tool_calls（阶段 4 换 provider 时必遇）。不支持时传 `tools` 字段要么被忽略、要么报错。

### 7.2 方案 A：能力检测（先问再打）

```python
# 1. 查模型列表/能力元数据（如果有）
# 2. 发探测请求：给 tools 参数，看是否报错/是否返回 tool_calls
```

支持 → 原生 tool_calls；不支持 → 方案 B。缺点：探测花钱、文档可能过时。

### 7.3 方案 B：Prompt-based tool calling（提示词式工具调用）★核心降级

**原理：** 工具调用本质是"模型输出结构化文本"。原生协议是输出 `tool_calls` 字段；降级方案是在 system prompt 里描述工具格式，让模型输出 JSON，客户端解析执行。

```python
# 1. system prompt 里描述工具格式（关键）
TOOL_PROMPT = '''
需要调用工具时，只输出一个 JSON 对象（不要输出其他内容）:
{"tool": "工具名", "arguments": {"参数名": "值"}}
可用工具: get_time(无参数), read_file(参数path)...
'''

# 2. 解析模型输出的 JSON（容错）
def parse_tool_call(text):
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match: return None
    try: return json.loads(match.group(0))
    except json.JSONDecodeError: return None

# 3. agent loop 完全一样：解析→执行→回填→继续
resp = model.chat(messages)
call = parse_tool_call(resp)
result = execute_tool(call["tool"], call["arguments"])
messages.append({"role": "tool", "content": result})
```

**关键：降级方案只依赖模型"会输出 JSON"**（几乎所有模型都会），不依赖原生 tool_calls 能力。

### 7.4 方案对比

| | 原生 tool_calls | Prompt-based 降级 |
|---|---|---|
| 依赖模型能力 | 需要原生支持 | 只要能输出 JSON |
| 可靠性 | 高（协议保证格式） | 中（需容错解析） |
| 实现成本 | 低（SDK 支持） | 中（解析器+容错+提示词设计） |
| 适用 | 主流模型 | 老模型/本地小模型/特殊 provider |

### 7.5 工程实践：自适应双通道

```python
def chat(self, messages, tools=None, force_prompt_mode=False):
    if tools and not self.supports_tool_calls:   # 不支持原生
        return self._prompt_mode_chat(messages, tools)  # 提示词降级
    return self._native_chat(messages, tools)     # 原生
```

**Hermes 能兼容几十家 provider 的秘诀之一就是：能力检测 + 降级通道**，让同一个 agent 在最强和最弱的模型上都能跑。

## 8. 与 Hermes 的对照

Hermes 的工具系统（`tools/` 目录）就是这套机制的工业级实现：
- 它的 `@tool` 装饰器支持更丰富的参数定义（pydantic 模型驱动 schema）
- 它有完整的工具安全体系（approvals、权限、命令白名单）
- 它的 agent 循环有更细的容错（重试、流式、中断）

**我们写的 80 行 tools.py 是它的最小原理实现**——先理解原理，再看工业实现就能看懂。

## 8. 本阶段的产物

```
qi_agent/tools/registry.py   @tool 装饰器 + 注册表 + schema 生成（~110行）
qi_agent/tools/builtin.py    get_time / read_file / shell（~90行）
qi_agent/llm.py              ChatResult/ToolCall + tools 参数支持（~110行）
qi_agent/agent.py            Agent Loop（~70行）
tests/test_tools.py          11 个测试
tests/test_agent_loop.py     7 个测试
```
