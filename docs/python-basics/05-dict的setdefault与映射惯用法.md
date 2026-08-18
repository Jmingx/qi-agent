# 05 dict.setdefault 与映射惯用法（"取桶，没有就建"）

> 归档：2026-08-18 · 来源：事件总线（events.py）代码讲解问答
> 读者：agent 开发小白（有 Java 背景）
> 场景：`EventBus._listeners` 用事件名（key）分组保存监听者列表——"取名单，没有就建一个空名单"的高频模式

---

## 1. 从一句真实代码开始

`qi_agent/events.py` 里注册监听器的核心一行：

```python
self._listeners.setdefault(event, []).append(_Listener(handler, priority))
```

拆开看：

```python
self._listeners.setdefault(event, []).append(_Listener(handler, priority))
#         └─┬─┘    └──┬──┘  └┬┘ └───────────┬────────────────┘
#       字典对象   setdefault  默认值      append 的参数（新监听者）
```

整句 = **"取出 `event` 对应的列表，然后把新监听者追加进去"**。核心魔法在 `setdefault`。

## 2. `dict.setdefault(key, default)` 语义

```
key 存在  → 返回 key 对应的值（不碰 default）
key 不存在 → 先把 default 插入字典，再返回它
```

| 场景 | 调用前 | `setdefault` 之后 | 返回值 |
|------|--------|------------------|--------|
| `event` 已有名单 | `{"a": [A]}` | `{"a": [A]}` | `[A]` |
| `event` 没名单 | `{}` | `{"a": []}` | `[]` |

**关键**：无论哪种情况，**返回值都是一个可用的列表**——所以可以直接 `.append()`，不用先判断 key 存不存在。

## 3. 与 `dict.get` 的区别（易混点）

| 方法 | key 不存在时 | 副作用 |
|------|-------------|--------|
| `d.get(key, default)` | 返回 default，**不修改字典** | 无 |
| `d.setdefault(key, default)` | 把 default **插入字典**，再返回 | **有**（写入字典） |

`get` 是"只读查询"，`setdefault` 是"查询 + 没有就写入"。本场景必须用 `setdefault`——因为注册后名单必须持久存在，不能每次查询都新建临时列表。

## 4. Java 类比：就是 `computeIfAbsent`（一一对应）

```java
// Java 8+
List<Listener> list = map.computeIfAbsent(event, k -> new ArrayList<>());
list.add(new Listener(handler, priority));
```

```python
# Python（qi-agent events.py）
self._listeners.setdefault(event, []).append(_Listener(handler, priority));
```

| 概念 | Java | Python |
|------|------|--------|
| 取桶，没有就建 | `computeIfAbsent(key, k -> new ArrayList<>())` | `setdefault(key, [])` |
| 追加元素 | `list.add(x)` | `list.append(x)` |

两者语义等价：**"没有就建、有就取"，返回值都是可用的容器**。区别仅在 Java 用 lambda 惰性创建 default，Python 的 default 是立即求值的普通参数（见第 7 节坑）。

## 5. 不这样写会怎样（等价展开）

```python
# 展开写法 1：if 判断（最直白）
if event not in self._listeners:
    self._listeners[event] = []
self._listeners[event].append(_Listener(handler, priority))

# 展开写法 2：get + 新列表（性能差）
self._listeners[event] = self._listeners.get(event, []) + [_Listener(handler, priority)]
# 每次新建列表 + 复制全部旧元素，O(n)，n 大时浪费
```

`setdefault` 一行搞定——**"取名单，没有就建一个"** 是高频模式，字典为此专门提供了这个原子操作。

## 6. 实际流程走一遍（events.py 的注册场景）

```python
# 第一次：_listeners 里没有 "agent/tool-call" 这个 key
# setdefault 插入 {"agent/tool-call": []} 并返回 []（空列表）
# append 后 → {"agent/tool-call": [_Listener(A)]}

# 第二次（另一个插件也监听同一事件）：
# setdefault 发现 key 已存在 → 返回现有列表 [_Listener(A)]
# append 后 → {"agent/tool-call": [_Listener(A), _Listener(B)]}

# 第三次注册不同事件名（"agent/tool-result"）：
# 又是"没有就建" → 新建独立列表，与前面的互不干扰
# → {"agent/tool-call": [A, B], "agent/tool-result": [C]}
```

事件名（key）是**分类的桶**，`setdefault` 保证：第一次注册时桶存在，后续注册直接往桶里加。

## 7. 值得知道的坑：default 参数立即求值

```python
self._listeners.setdefault(event, [])  # ← default 参数会被【立即求值】
```

Python 函数参数**先算完再传**——即使 key 已存在、default 用不上，`[]` 也会先被创建（然后被丢弃）。本例无害（空列表很便宜），但如果 default 是昂贵对象（大计算、数据库连接、加载文件）就白花钱了。

- 惰性替代方案：`collections.defaultdict(list)`——访问不存在的 key 自动建桶，但语义不同（任何读取都会建桶，且没有"取一次"的返回值）
- 或干脆 `if key not in d:` 判断（default 昂贵时的首选）

```python
from collections import defaultdict

# defaultdict 写法（惰性建桶，但只对"追加"模式友好）
self._listeners = defaultdict(list)
self._listeners[event].append(_Listener(handler, priority))
```

对比：`setdefault` 适合"**取一次就用**"（要返回值），`defaultdict` 适合"**反复读写**"（靠下标访问）。

## 8. 小结

| 要点 | 内容 |
|------|------|
| `setdefault` 语义 | key 存在返回原值，不存在插入 default 并返回 |
| 与 `get` 的区别 | `get` 只读不写，`setdefault` 会写入字典 |
| Java 对照 | `Map.computeIfAbsent`（但 Java 惰性、Python 立即求值） |
| 适用场景 | "取桶，没有就建"——事件注册表、按 key 分组的计数器、缓存桶 |
| 坑 | default 立即求值；default 昂贵时改用 `if` 判断 |

## 9. 相关链接

- 实际使用处：`qi_agent/events.py`（EventBus.on 注册监听器）
- 概念背景：`docs/principles/07-事件驱动与钩子原理.md`（事件总线是插件化地基）
