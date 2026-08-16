# 03-Python装饰器与property原理（含作用域与源码阅读）

> 归档来源：qi-agent 开发会话问答（2026-08-14）
> 面向读者：agent 开发小白
> 配套代码：`qi_agent/agent.py` 中的 `@property history`

## 1. 装饰器是什么：给函数穿衣服

**装饰器（decorator）的本质：一个"接收函数、返回新函数"的函数。** 它给原函数外面包一层，在调用前后插入额外逻辑，但不改原函数的代码。

### 类比：给外卖加保温袋

- 原函数 = 一份饭菜
- 装饰器 = 保温袋
- `@装饰器` 写在函数上面 = 把饭菜装进保温袋
- 你吃到的还是那份饭（功能不变），但它多了保温功能（额外行为）

### 语法糖的真相：@ 展开后是什么

```python
# 写法 A：用 @ 语法糖
@my_decorator
def hello():
    return "hi"

# 写法 B：不用 @，完全等价
def hello():
    return "hi"
hello = my_decorator(hello)   # ← @ 的真相：把函数传给装饰器，结果再赋回原名字
```

### 一个装饰器的完整生命周期

```python
def my_decorator(func):            # ① 接收原函数
    def wrapper(*args, **kwargs):  # ② 造一个新函数（包装层）
        print("调用前")             # ③ 调用前的额外逻辑
        result = func(*args, **kwargs)  # ④ 调用原函数
        print("调用后")             # ⑤ 调用后的额外逻辑
        return result
    return wrapper                 # ⑥ 返回新函数

@my_decorator
def hello():
    return "hi"

hello()   # 实际执行的是 wrapper()
```

## 2. @property：把"方法"伪装成"属性"

```python
# 无 @property 时：history 是普通方法，必须加括号调用
agent.history()

# 有 @property 后：像属性一样访问，不用加括号
agent.history
```

**本质上 property 是 Python 的一个内建类，实现了"描述符协议"（`__get__`/`__set__`/`__delete__`）。** 当你访问 `agent.history` 时，Python 发现 history 是 property 对象，就调用它的 `__get__` 方法，`__get__` 再去执行你写的函数。

**大白话：property 是一个"拦截器"** —— 拦截"属性读取"这个动作，转而去执行你的函数。

### property 内部存了 3 个函数（PyCharm 跳转看到的本质）

```
fget ──► 当你读  obj.x     时被调用
fset ──► 当你写  obj.x = 5 时被调用
fdel ──► 当你删  del obj.x 时被调用
```

**`@property` 单独用 = 只填了 fget（读），没填 fset（写）→ 这就是"只读"的原理：**

```python
@property
def history(self):
    return self.messages

# 等价于：history = property(fget=读取函数, fset=None, fdel=None)
# fset 是 None → 不能写 → 只读
```

### @property 全家桶（读写删完整定义）

```python
class C:
    @property          # 定义读：x = property(fget=getx)
    def x(self):
        return self._x

    @x.setter          # 定义写：把 x 的 fset 补上
    def x(self, value):
        self._x = value

    @x.deleter         # 定义删：把 x 的 fdel 补上
    def x(self):
        del self._x
```

### @property 的核心价值

> **属性访问能触发函数逻辑** —— 调用方写 `obj.x = 5` 就能触发校验/副作用代码，这是裸属性做不到的。

- 语义清晰：`agent.history` 读起来是"数据"，`agent.history()` 读起来是"动作"
- 只读约定：对外声明"这个值只能看不能改"
- 未来可扩展：接口与实现之间的缓冲层——内部实现随便改（如从内存换 SQLite），外部代码零改动

**诚实结论：** 只用裸属性 `self.messages` 也完全能实现同样的功能。@property 在简单场景是"设计习惯"，在"读写时需插入逻辑"的场景才是必须。

## 3. Python 成员变量与作用域

### 三种变量，三种作用域规则

| 变量类型 | 定义位置 | 生命周期 | 谁能访问 |
|---------|---------|---------|---------|
| **局部变量** | 函数内部 | 函数执行期间 | 只有函数内部 |
| **全局变量** | 模块顶层 | 程序运行期间 | 模块内所有代码 |
| **成员变量**（实例属性） | `self.xxx` 赋值 | 跟实例同生共死 | 拿到实例引用的人都能访问 |

### 成员变量的本质：实例的 __dict__ 字典

```python
print(agent.__dict__)
# {'client': <FakeClient object>, 'messages': [{'role': 'system', ...}, ...]}
```

**成员变量就是挂在实例身上的一个字典。** 所谓"作用域"，对成员变量来说就是：**挂在哪个实例上，谁持有引用，谁就能读写** —— 没有编译期的可见性限制。

### Python 没有强制作用域

- Python 没有 C++/Java 那种 private/protected/public
- "私有"靠约定：下划线开头 `_xxx` 表示"请别动我"，但技术上照样能访问
- 设计哲学：**我们都是成年人，靠自觉**

## 4. 装饰器 vs 钩子函数（重要辨析）

**有相似之处，但不是一回事。**

### 相似点（都"把函数交出去"）

- 函数是一等公民：都把函数当参数传递
- 不改原代码：都在原函数之外附加逻辑
- 都能插桩：在某个时机插入代码

### 本质区别：谁控制执行时机

| 维度 | 装饰器 | 钩子函数 |
|------|--------|---------|
| 执行时机 | **定义时**立即包装 | **事件发生时**才被调用 |
| 谁控制 | 装饰器主动包装 | 框架/系统主动回调 |
| 结果 | **必须返回**一个新函数（替换原函数） | 通常无返回值（副作用为主） |
| 确定性 | 同步、每次调用都经过包装 | 异步、触发时机由外部决定 |
| 典型场景 | 加缓存、加日志、属性托管 | 事件监听、生命周期回调、插件机制 |

### 类比

- 装饰器 = 给手机装手机壳：装上那一刻就完成，之后用的就是"带壳手机"
- 钩子 = 鱼钩：挂上去等鱼咬，何时触发由事件决定，可能永不触发

### 但它们可以组合：装饰器作为"注册钩子"的语法手段

```python
@app.route("/hello")   # Web框架：装饰器把 hello 注册到路由表
def hello(): ...       # 请求到达时框架才回调它（此时像钩子）

@tool(description="获取当前时间")   # 阶段2：把函数登记进工具注册表
def get_time() -> str: ...         # LLM 决定调用时才执行（此时像钩子）
```

**一句话：装饰器是"包装"，钩子是"回调"。** 它们共享"函数作为对象传递"的思想；区分的关键是看**执行时机由谁控制**。

## 5. 如何阅读 Python 源码

### 5.1 先判断"能不能读"：按来源分类

| 来源 | 能否读到 Python 源码 | 例子 |
|------|---------------------|------|
| **内建类型**（built-in） | ❌ C 实现，只有 docstring | `property`、`list`、`dict`、`len` |
| **标准库模块** | ✅ 大多数是纯 Python | `functools`、`os`、`json` |
| **第三方库** | ✅ 纯 Python（多数） | `openai`、`pydantic` |
| **你自己/项目代码** | ✅ | `agent.py` |

**判断方法：** PyCharm 跳转后看文件路径——如果显示 `types.py`、`builtins.py` 且内容只有注释没有逻辑，就是 C 实现的壳（stub），别在里面找答案。

### 5.2 内建类型怎么看：看文档，不看源码

```python
import inspect
inspect.signature(func)   # 只看签名：参数是什么
inspect.getsource(func)   # 看源码（纯 Python 才有；内建类型报 "is a built-in class"）
inspect.getdoc(func)      # 看 docstring
help(property)            # 内建类型的文档入口（比 stub 好懂 100 倍）
```

### 5.3 纯 Python 源码怎么读：从外到内

```
① 先看函数签名   → 它接收什么参数
② 再看 docstring → 它是干嘛的（先懂意图）
③ 看主流程      → 找 def wrapper(...) 和 return（装饰器都返回 wrapper）
④ 跳过细节      → 看不懂的辅助函数先跳过
```

**读源码三字诀：签名 → 意图 → 主线。** 不要逐行读，先建立"这个函数做什么、输入输出是什么"的框架，再钻细节。

## 6. 踩过的坑

1. **测试断言设计失误**：test_user_message_appended 原断言"chat() 后历史最后一条是 user"——错，chat() 完成后最后一条必然是 assistant（一轮以模型回复结束）。修正为断言 user 位于 history[1]（system 之后）。
2. **PYTHONPATH 环境污染**：Hermes 终端注入 PYTHONPATH 指向 Hermes venv，导致 `uv run python` 导入损坏的 openai/pydantic_core。解决：命令加 `PYTHONPATH=` 前缀。pytest 不受影响。

## 7. 知识点地图（后续阶段的落点）

| 概念 | 将在哪用到 |
|------|-----------|
| 装饰器实现 | 阶段 2 亲手写 `@tool` 装饰器（工具注册机制） |
| 注册表/钩子思想 | 阶段 2 工具注册表、阶段 5 技能系统 |
| @property 缓冲层 | 阶段 3 存储层换实现（内存→SQLite）时接口不变 |
| 读源码方法 | 阅读 Hermes 源码（agent 循环、工具系统）时使用 |
