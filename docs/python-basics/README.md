# Python 基础知识归档（docs/python-basics/）

存放**通用 Python 语言/工程知识**（与 qi-agent 项目无关的通用技能）。
agent / LLM 相关的项目技术原理请归档到 `docs/principles/`。

## 命名规范
- `NN-<主题>.md`，NN 为两位数序号（01, 02, ...）
- 文档面向 Python 初学者，中文

## 索引

| 序号 | 主题 | 日期 |
|------|------|------|
| 01 | Python项目管理与打包原理（pyproject/uv/pytest/ruff） | 2026-08-14 |
| 02 | Python装饰器与property原理（含作用域与源码阅读） | 2026-08-14 |
| 03 | 闭包与装饰器本质、dataclass详解 | 2026-08-14 |
| 04 | 异常处理与BaseException层级（Ctrl+C中断处理） | 2026-08-14 |
| 05 | dict的setdefault与映射惯用法（"取桶没有就建"+Java computeIfAbsent对照） | 2026-08-18 |
| 06 | Python的接口哲学（ABC/Protocol/鸭子类型 vs Java interface） | 2026-08-19 |
| 07 | queue.Queue线程安全队列（阻塞/非阻塞/有界背压）+ str-Enum陷阱 | 2026-08-29 |
| 08 | 协议分层：JSON-RPC over stdio 是什么意思（应用层/传输层/为什么不用HTTP） | 2026-08-29 |
| 09 | async/await与事件循环（单线程协作式并发、vs线程、asyncio内部、场景账） | 2026-08-29 |
