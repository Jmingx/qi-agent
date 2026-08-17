# 2026-08-17 run_python 软沙箱工具（v0.4.2）

## 做了什么
- 新增 run_python 工具（tools/run_python.py）：软沙箱执行 Python 代码
- 四锁设计：权限锁（静态黑名单扫描）+ 隔离锁（子进程）+ 时间锁（10s 超时）+ 安全锁（环境变量白名单）
- 11 个测试（tests/test_run_python.py）
- 注册到工具表（cli 导入触发，初始化日志显示）

## 怎么做的
1. TDD：写 test_run_python.py（11 用例）→ RED → 实现 run_python.py → GREEN
2. 更新 tools/__init__.py + cli.py 导入
3. 全量验证：67 passed + ruff 无错误
4. 链路验收：execute_tool 调用（正常计算 391 / 拦截 import os / 超时提示）
5. commit → tag v0.4.2

## 遇到的问题与解决
1. **test_safe_env_no_api_key 自相矛盾**：测试代码里 `import os` 读环境变量，但 import os 本身被权限锁拦截（先撞权限锁）。解决：改为直接单元测试 `_build_safe_env()` 函数（验证白名单构建，不经过沙箱执行路径）——更干净、更精准
2. **模型不主动调工具**：手工验收时模型选择心算而非调 run_python（对简单算术自信）。解决：改用 execute_tool 直接验证注册-执行链路（单元层面已覆盖 11 个用例）

## 验证结果
- `uv run pytest` → 67 passed ✅（+11 run_python）
- `uv run ruff check .` → All checks passed ✅
- 链路验收：正常执行 391 / 拦截 import os / 超时 10s ✅
- 工具注册：run_python (toolset=builtin, 参数=['code']) ✅
- git tag v0.4.2 ✅

## 下一步
- TODO 沙箱 v2（RestrictedPython）——解决 v1 黑名单可绕过问题
- TODO 进程沙箱：干净环境变量（run_python 已内置 env 白名单，可推广到 shell）
- 或推进其他 P0（path_security / 参数校验 / shell 审批）
