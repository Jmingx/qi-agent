"""安全基础设施子包：命令权限规则 + 路径安全检测。

设计（方案 2026-08-22-权限规则统一方案，用户拍板"安全域收敛"）：
- 被 tools（shell/read_file/write_file）与 plugins（security_guard）共享的
  安全数据/检测收敛于此——中立子包，不偏任何一方（工具层 import 插件 = 反向依赖）
- rules.py：命令权限规则单一来源（红线/审批/代码执行档）
- path_security.py：敏感路径检测（从 tools/ 迁入）
"""
