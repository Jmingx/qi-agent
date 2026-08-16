# 01-现代Python项目管理与打包原理

> 对应阶段：阶段 0（项目脚手架）
> 日期：2026-08-14

## 1. 为什么要了解这些

搭建一个 Python 项目，第一件事不是写代码，而是把"项目怎么管理"这件事搞清楚。本章解释脚手架里每个配置文件为什么存在、每个命令在干什么。看懂这些，后面所有阶段写代码时就不会被工具链绊住。

## 2. pyproject.toml：现代 Python 项目的"身份证+说明书"

### 2.1 它是什么

`pyproject.toml` 是 Python 官方钦定的项目配置文件（PEP 518 / PEP 621 标准）。一个文件同时承担三个职责：

| 配置段 | 职责 | 类比 |
|--------|------|------|
| `[project]` | 项目元数据（名字、版本、依赖） | 身份证 |
| `[build-system]` | 声明用什么工具把源码"打包"成可安装的包 | 建筑队 |
| `[tool.xxx]` | 各种工具（pytest/ruff）的专属配置 | 各工种的施工图 |

### 2.2 为什么需要 build-system

**关键概念：一个 Python 项目必须"安装"后才能被 import。**

你写了一个 `qi_agent/` 目录，里面有 `.py` 文件。如果你只是"运行"它（比如 `python -c "import qi_agent"`），Python 能在**当前目录**找到它——但一旦你在别的目录、别的脚本里 import，Python 就找不到了。

```python
# 为什么会报 ModuleNotFoundError？
# pytest 运行时的工作目录是项目根，但 Python 的模块搜索路径
# （sys.path）并不自动包含项目根目录！
```

**editable install（可编辑安装）** 解决这个问题：`uv sync` 会把项目本身"安装"进虚拟环境，装的是一个"链接"（指向源码目录），而不是一份拷贝。这样：
- 任何地方都能 `import qi_agent`
- 改源码立即生效，不用重新安装

我们脚手架里 `pyproject.toml` 的 `[build-system]` 就是告诉 uv："请用 hatchling 这个打包工具，把我这个项目安装成可编辑包"。`uv sync` 输出里的 `+ qi-agent==0.1.0 (from file:///...)` 就是安装成功的证据。

### 2.3 为什么选 hatchling

打包工具（build backend）有很多：setuptools（最老牌）、hatchling（现代轻量）、poetry-core、flit。选 hatchling 因为：
- 零配置：对"src 目录下放包"这种标准布局开箱即用
- 纯 Python 实现，依赖少
- 是 uv / ruff 作者们的默认推荐

## 3. uv：Rust 写的新一代包管理器

### 3.1 它解决什么问题

传统流程是 `pip install` + `venv` 手工管理，慢且容易乱。uv 用 Rust 重写了依赖解析和安装，速度是 pip 的 10-100 倍，并且：

| 功能 | 说明 |
|------|------|
| `uv sync` | 一条命令：创建虚拟环境 + 安装全部依赖 + 生成/更新锁文件 |
| `uv add <pkg>` | 安装依赖 + 自动写进 pyproject.toml + 更新锁文件 |
| `uv.lock` | 锁文件：记录所有依赖的**精确版本**，保证任何人任何机器装出来一模一样 |
| `uv run <cmd>` | 在项目虚拟环境里运行命令（不用手动 activate） |

### 3.2 锁文件为什么重要

`pyproject.toml` 里写的是 `openai>=1.40`（宽松约束），但 `uv.lock` 里锁的是 `openai==3.0.0`（精确版本）。这叫 **reproducible builds（可复现构建）**：

> 你机器上跑通的东西，换台机器、过三个月，`uv sync` 之后跑出来的结果完全一致。

没有锁文件的话，"在我机器上能用"会成为项目最大的坑——依赖悄悄升级就可能破坏行为。

## 4. .gitignore：什么不该进版本控制

git 管的是"代码"，不是"机器状态"。必须排除的：

| 条目 | 为什么排除 |
|------|-----------|
| `.venv/` | 虚拟环境是机器专属的，几百 MB 垃圾 |
| `.env` | 里面有 API key，泄露=钱包出血 |
| `.idea/` | PyCharm 的 IDE 配置，每个人偏好不同 |
| `__pycache__/` | Python 字节码缓存，垃圾 |
| `*.db` | 数据库文件，数据不是代码 |

**教训：** 我们初始提交时不小心把 `.idea/` 提交进去了，后面用 `git rm -r --cached .idea` 移出——`--cached` 的意思是"从 git 的追踪记录里删掉，但保留磁盘上的文件"。

## 5. pytest：测试框架怎么找到你的测试

`pytest` 的约定：`test_*.py` 文件、`test_*` 函数。运行 `pytest` 时它自动：
1. 从当前目录向下扫描所有 `test_*.py`
2. 收集所有 `test_` 开头的函数
3. 逐个执行，`assert` 失败就报错

我们加了 `[tool.pytest.ini_options] pythonpath = ["."]`——把项目根目录加进模块搜索路径，这样测试里 `import qi_agent` 能找到包。（其实 editable install 已经解决了，这个配置是双保险。）

## 6. ruff：让机器管代码风格

lint（静态检查）工具的作用：**在运行前发现代码问题**。ruff 一个工具顶三个（flake8 + black + isort）：
- E 类规则：语法/风格错误（如 E501 行太长）
- F 类规则：逻辑问题（如 F401 导入了没用的名字）

我们的规范（AGENTS.md P1-5）要求代码风格统一——靠人自觉不可靠，让 `uv run ruff check` 在提交前强制执行。

## 7. 踩过的坑

1. **ModuleNotFoundError**：包没安装，pytest 找不到 `qi_agent`。解决：加 `[build-system]` + `uv sync` 重装。
2. **.idea/ 误提交**：初始化 git 时把 IDE 配置提交了。解决：`git rm -r --cached` + 补进 .gitignore。
3. **CRLF 警告**：Windows 下 git 提示 "LF will be replaced by CRLF"——这是行尾符差异的正常提示，不影响功能（后续可配置 `.gitattributes` 统一）。

## 8. 与 Hermes 的对照

Hermes 本身就是一个标准 Python 项目（pyproject.toml + uv），它的依赖管理方式和我们完全一样。你在 `C:\Users\xie\PycharmProjects\hermes-agent\pyproject.toml` 能看到同样的结构——只是规模大得多。
