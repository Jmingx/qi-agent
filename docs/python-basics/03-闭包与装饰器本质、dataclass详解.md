# 03-闭包与装饰器本质、dataclass详解

> 归档来源：qi-agent 开发会话问答（2026-08-14）
> 面向读者：Python 初学者
> 前置知识：装饰器基本用法（见 02-Python装饰器与property原理）

## 1. 闭包（Closure）：函数"记住"外部变量

### 1.1 什么是闭包

**闭包 = 内层函数 + 它"记住"的外层变量。**

```python
def outer(x):
    def inner(y):
        return x + y    # inner 用到了 outer 的变量 x
    return inner

add5 = outer(5)         # outer(5) 执行完了，x=5 本该销毁……
add5(3)                 # → 8 ？！
```

外层函数 `outer` 已经返回，但 `inner` 依然记得 `x=5`。检查 `add5.__closure__` 可以看到它"记忆背包"里装着的变量。这就是闭包：**函数 + 它捕获的环境**。

### 1.2 为什么需要闭包

Python 中函数返回后，其局部变量按道理应该销毁。但内层函数引用了外层变量时，Python 会把这些变量"打包"进内层函数的记忆（closure cell），使它们在函数返回后依然存活。**闭包是"函数作为一等公民"的必然结果。**

## 2. 装饰器的本质就是闭包

### 2.1 结构对比

```python
# 闭包：外层函数包内层，内层引用外层变量，外层返回内层
def outer(x):
    def inner(y):
        return x + y        # 引用外层变量 x
    return inner

# 装饰器：同一个结构，只是被包装的是"函数"
def my_decorator(func):
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)   # 引用外层参数 func
        return result
    return wrapper
```

**完全一样的三明治结构。** `wrapper` 是闭包，它"记住"了 `func`。`@my_decorator` 只是把 `func` 传进闭包工厂，拿到包好的 wrapper 替换原名。

### 2.2 为什么必须用闭包

如果不用闭包，`wrapper` 在**之后**被调用时无法访问 `func`——`func` 是 `my_decorator` 的局部变量，函数返回就该销毁。**闭包让这些变量存活下来**，这是装饰器能工作的根基。

> **一句话：闭包是"函数记住外部变量"的机制；装饰器是这种机制最经典的应用。** 掌握了闭包就掌握了装饰器的底层原理。

## 3. @dataclass：数据类样板代码生成器

### 3.1 痛点：数据类的手写样板

```python
# 不用 dataclass 手写 ToolCall
class ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.name = name
        self.arguments = arguments

    def __repr__(self):
        return f"ToolCall(id={self.id!r}, name={self.name!r}, arguments={self.arguments!r})"

    def __eq__(self, other):
        if not isinstance(other, ToolCall):
            return NotImplemented
        return (self.id, self.name, self.arguments) == (other.id, other.name, other.arguments)
```

三个方法全是模板，每个数据类都要重写，还容易抄错。

### 3.2 @dataclass：声明即所得

```python
from dataclasses import dataclass

@dataclass
class ToolCall:
    id: str          # 类型注解即字段声明
    name: str
    arguments: dict
```

**一行 @dataclass 自动生成** `__init__` / `__repr__` / `__eq__`（还有可选的 `__hash__`）：

```python
tc = ToolCall(id='call_1', name='get_time', arguments={})
print(tc)    # ToolCall(id='call_1', name='get_time', arguments={})  ← __repr__ 自动有
tc == ToolCall('call_1', 'get_time', {})  # True  ← __eq__ 自动有
```

### 3.3 常用选项

```python
@dataclass(frozen=True)     # 不可变：创建后字段不能修改（像元组）
class Point:
    x: int
    y: int

@dataclass
class Config:
    name: str = "默认值"     # 字段默认值
    retries: int = 3
```

### 3.4 适用场景

- 纯数据容器（DTO）：装数据的类，没有复杂行为
- 返回结构：函数需要返回多个值时，用 dataclass 比元组/字典可读性好得多
- 配置对象、API 响应结构等

### 3.5 与装饰器家族的关系

@dataclass 也是装饰器，但和 @property/@tool 不同：**它不包装函数而是转换类**——读取类的字段注解，动态生成方法，替换类的定义。这是"装饰器作用于类"的典型例子。

## 4. 知识点关联

| 概念 | 关联 |
|------|------|
| 闭包 | 装饰器的底层实现机制 |
| 装饰器 | 闭包的应用场景；@property/@tool/@dataclass 都是装饰器 |
| @dataclass | 阶段 2 的 ChatResult/ToolCall 数据结构使用 |
| 数据类 | 函数返回多值的首选形态（比元组可读、比字典有类型） |
