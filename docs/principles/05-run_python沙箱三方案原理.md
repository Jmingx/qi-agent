# 05-run_python沙箱三方案原理（v1白名单 / v2 RestrictedPython / v3 psutil）

> 归档来源：qi-agent 开发会话问答（2026-08-17）
> 对应 TODO：tool-calling.md 沙箱 v1/v2/v3 条目
> 阅读前置：principles/04（隔离三维度）、02（工具调用原理）

## 1. 沙箱三方案总览

| | v1 手写白名单 | v2 RestrictedPython | v3 psutil 监控 |
|---|---|---|---|
| 哲学 | 黑名单：列危险特征 | 白名单+代码重写 | 配额：限制用量 |
| 防什么 | 明显的危险操作 | 一切受限操作（含混淆） | 资源耗尽（内存/CPU/时间） |
| 检查层级 | 源码文本 | 编译后 AST/字节码 | 运行时进程状态 |
| Java 类比 | 正则检查输入 | ASM 字节码插桩 | JVM -Xmx + 监控线程 |
| 可绕过性 | 易（拼接就绕） | 难（需找解释器漏洞） | 不可绕（只管用量） |

**三者互补：v2 管"能不能做坏事"（安检门），v3 管"能用多少"（限流阀）。**

## 2. v1 手写白名单：安检门模式

### 原理：不信任代码，在入口扫描

```python
def run_python(code: str) -> str:
    forbidden = ["import os", "import sys", "import subprocess",
                 "open(", "__import__", "eval(", "exec("]
    for bad in forbidden:
        if bad in code:
            return f"[安全拦截] {bad}"
    result = subprocess.run([sys.executable, "-c", code],
                            capture_output=True, text=True,
                            timeout=10, env=CLEAN_ENV)
    return result.stdout
```

### 三环节：文本扫描（危险特征）/ 子进程（崩溃隔离）/ 超时（防死循环）

### 致命弱点（黑名单哲学缺陷）

```python
code = "impo" + "rt os"                 # 拼接绕过
code = "import builtins; builtins.__import__('os')"   # 间接路径
code = "().__class__.__bases__[0].__subclasses__()"   # 反射链逃逸
```

**你永远列不全"所有危险写法"**——攻击者找到一个漏网就赢。特征匹配追不上想象力。

## 3. v2 RestrictedPython：解释器内嵌守卫模式

### 原理：把代码"翻译"成受限版本再执行（不是扫描！）

```python
from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem

byte_code = compile_restricted(code, "<sandbox>", "exec")   # ① AST重写
safe_globals = {"__builtins__": safe_builtins, "_getitem_": default_guarded_getitem}
exec(byte_code, safe_globals)                                # ② 受限执行
```

### 重写拦截点

| 拦截点 | 机制 |
|--------|------|
| import | 只能用 safe_builtins 白名单（无 os/sys） |
| 属性访问 | `obj.attr` → `_getattr_(obj, "attr")` 守卫 |
| 下标访问 | `a[b]` → `_getitem_(a, b)` 守卫 |
| 魔术方法逃逸 | 拦截 `__class__`/`__bases__` 反射链 |

### 为什么比 v1 强

- 检查对象是**编译后代码结构**（AST），不是文本——混淆后 AST 结构不变
- 安全内建到代码本身（"把每个人改造成合规者再放行"）
- Java 类比：Java Agent + ASM 字节码插桩

## 4. v3 psutil 资源监控：交警限流模式

### 原理：不阻止危险，限制"用多少"

```python
proc = subprocess.Popen([sys.executable, "-c", code], ...)
p = psutil.Process(proc.pid)
while proc.poll() is None:
    if time.time() - start > timeout: proc.kill(); return "[超时]"
    mem = p.memory_info().rss / 1024 / 1024
    if mem > max_memory_mb: proc.kill(); return f"[内存超限]"
    time.sleep(0.05)   # 50ms 轮询
```

### 三资源维度：时间（time 差值）/ 内存（memory_info().rss）/ CPU（cpu_percent）

### 为什么必须

**v1/v2 防"坏代码"，v3 防"耗资源的好代码"：**
```python
while True: x = [1] * 1000000   # 合法代码，但无限吃内存——只有 v3 能拦
```

Java 类比：JVM `-Xmx256m` + 外部监控线程查 ThreadMXBean。

## 5. 实施路径（TODO 顺序）

```
v1 先跑通（教学：理解黑名单的局限）
→ v2 换 RestrictedPython（教学：理解"安全内建到代码"）
→ v3 加 psutil（教学：理解"隔离的另一半是资源"）
```

依赖：RestrictedPython + psutil（纯 Python，轻量，符合 principles/04 三轴组合）
