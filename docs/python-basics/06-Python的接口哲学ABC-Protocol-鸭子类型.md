# 06 Python 的"接口"哲学：ABC / Protocol / 鸭子类型（Java interface 对照）

> 归档：2026-08-19 · 来源：安全审核插件设计问答（"插件为什么不按接口规范"）
> 读者：Java 背景的 Python 学习者
> 场景：理解 Python 项目里"没有 interface 关键字"时，接口/契约如何表达

---

## 1. 核心结论

**Python 有接口，只是形态不是 Java 的 `interface`**。接口的本质是**契约**（"你只要保证有这些方法/行为，我就能用你"）——Java 用类型强制契约（编译期检查），Python 用约定 + 文档表达契约（运行时检查）。

## 2. Python 接口的三种形态

| 形态 | 是什么 | 强制力 | Java 对照 |
|------|--------|--------|-----------|
| **ABC**（`abc.ABC`） | 抽象基类，必须**继承**才算实现 | 运行时强制（实例化报错） | `interface` + `implements` |
| **Protocol**（`typing.Protocol`） | 结构化接口：**只要有对应方法就满足**，无需继承 | 类型检查器提示（如 mypy） | 无直接对应（更接近 Go 的 interface） |
| **鸭子类型/约定** | 文档约定："你提供 `install(bus)` 就行" | 无（装错运行时报错） | 无（全靠反射检查） |

### 三者的代码形态

```python
# ABC：必须继承
class Plugin(ABC):
    @abstractmethod
    def install(self, bus): ...

class MyPlugin(Plugin):        # 必须 extends
    def install(self, bus): ...

# Protocol：有方法即满足（不用继承）
class ChatClient(Protocol):
    def chat(self, messages, tools=None) -> str: ...

class RealClient:              # 没写 implements，照样是 ChatClient
    def chat(self, messages, tools=None): ...

# 鸭子类型/约定：纯文档约束
# "插件必须提供 install(bus) 方法"——没有类型检查，全靠约定
```

## 3. 为什么函数可以"代替接口"（工具场景）

Java：定义接口 + 每个工具一个实现类，靠类实现多态：

```java
interface Tool { String execute(Map<String, Object> args); }
class GetTimeTool implements Tool { ... }
```

Python：**函数是一等公民**，函数签名本身就是接口：

```python
def get_time() -> str: ...
def read_file(path: str) -> str: ...

# 调用方只依赖约定：handler(**arguments) -> str
result = entry.handler(**arguments)
```

为一个方法定义接口 + 类，是 Java 的类型系统负担；Python 直接传函数，约定即接口。

## 4. 什么时候用哪种？（决策框架）

| 场景 | 选型 | 项目实例 |
|------|------|---------|
| **多态调用点**（多个实现替换，协议 ≥2 方法） | `Protocol` | `ChatClient`（真实 LLMClient / 测试 FakeClient） |
| 单方法协议（install/report） | 鸭子类型约定 | 插件系统（install(bus) 约定） |
| 复杂协议（>3 方法、强约束） | `ABC` 或 `Protocol` | 将来档位 B 服务化（llm/tools 可替换时） |
| 纯数据形状 | dataclass / TypedDict | ToolCall / ChatResult |

**关键判断**：协议的方法数 + 是否需要多态替换。1 个方法用约定（过度设计警告），≥2 个方法且多实现用 Protocol，强约束复杂协议用 ABC。

## 5. 约定式接口的代价（诚实评估）

- 没有编译期检查：插件忘了 `install`，装配时才 `AttributeError`（运行时炸 vs 编译期炸）
- 文档必须详尽：约定靠文档维护，团队要遵守
- 好处：灵活（任何形态都能当插件）、轻量（不建继承树）、Python 惯例（pytest 插件、Flask 扩展全是约定式）

**取舍本质**：Java 用类型换安全（编译期），Python 用约定换灵活（运行时）——没有对错，是语言哲学。

## 6. 相关链接

- 项目实例：`qi_agent/agent.py`（ChatClient Protocol）、`qi_agent/plugins/`（install 约定）
- 概念延伸：`docs/principles/07-事件驱动与钩子原理.md`（事件总线依赖约定而非接口的原因）
