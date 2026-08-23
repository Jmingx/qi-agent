# 2026-08-20 write_file 工具：agent 读写能力（v0.4.19）

## 做了什么
- **write_file 工具**（agent 从只读走向读写）：
  - 四档路径判定：①敏感路径（.env/.git/密钥，全局）→ 拒绝（红线，不可审批）
    ②项目内新增 → 自动放行 ③项目内覆盖已有 → 审批 ④项目外普通路径 → 审批
  - 复用现有机制（零新架构）：is_sensitive_path（红线）+ approval_gate/agent/tool-approval
    （审批）+ approved 内部参数（防绕过）
  - 工具层兜底：无插件时覆盖/越界 fail-closed 拒绝（"安全底线硬编码"哲学）
  - UTF-8 写入 + 目录自动创建 + 手写 schema（只暴露 path/content）
- **security_guard 扩展**：_ARG_PARAM_MAP +write_file→path；_on_tool_call 加覆盖/越界
  审批分支；**_check_sensitive_path 扩展**（原来只处理 shell，现支持带 path 参数的
  工具——write/read 双保险）
- 11 个新测试（211 passed）

## 怎么做的（TDD）
1. 方案评审：5 决策点全批准（用户定：项目内新增自动/覆盖审批/越界审批/敏感拒）
2. RED：write_file 模块不存在，测试全失败
3. GREEN：write_file.py + security_guard 扩展
4. 全量验证：211 passed + ruff + 手工 8 项验收

## 遇到的问题与解决
1. **_check_sensitive_path 只处理 shell**（`if name != "shell": return None`）——
   write_file 的 .env 红线没走到（测试抓出）。修复：扩展为支持带 path 参数的
   工具（read_file/write_file 直接检查路径，shell 保持 token 化）
2. **security_guard import 补丁两次失败**（docstring 内容与预期不符 + JSON 引号转义）——
   教训：patch 前先 read_file 确认实际内容，锚点用唯一短行
3. **测试误删 mock import**（清理 ruff F401 时删掉了还在用的 mock）——教训：
   清理未使用 import 前先确认文件内其他引用

## 验证结果
- `uv run pytest` → 211 passed ✅（+11）
- `uv run ruff check .` → All checks passed ✅
- 手工：项目内新增写入 ✅ / 覆盖无 approved 拒绝 ✅ / 覆盖+approved 写入 ✅ /
  敏感 .env（approved 也拒）✅ / 项目外拒绝 ✅ / 插件层四档判定 ✅

## 下一步
- TODO 打勾：write_file ✅（工具调用主线：读写能力就位）
- read_file 分页升级（方案已写，待评审：offset/limit 行级分页，修 snake.py 截断）
- 沙箱降级审批档（P0）/ LLM 调用超时（P1）/ 权限规则统一（P1）
