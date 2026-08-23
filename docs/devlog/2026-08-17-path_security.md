# 2026-08-17 path_security 路径安全（v0.4.3）

## 做了什么
- 新建 path_security.py：敏感路径黑名单 + 路径规范化（abspath 防 .. 绕过）
- 三段检查：文件名（.env/id_rsa/.netrc）+ 目录段（.git/.ssh/node_modules）+ 扩展名（.pem/.key）
- 接入 read_file：敏感路径返回安全拦截
- 12 个测试（tests/test_path_security.py）

## 怎么做的
1. TDD：写 test_path_security.py（12 用例）→ RED → 实现 path_security.py → GREEN
2. 接入 read_file.py（3 行）
3. 全量验证：79 passed + ruff 无错误
4. 手工验收：读 .env 拦截 / 读 README.md 放行 / ../.env 规范化拦截
5. commit → tag v0.4.3

## 遇到的问题与解决
1. **配置化讨论（用户提问）**：黑白名单该不该配置化？查 Hermes 源码发现——它的危险命令模式（DANGEROUS_PATTERNS）和路径校验也是硬编码！只有"用户决策类"（审批白名单/审批模式）配置化。结论：**安全底线硬编码，用户决策配置化**——敏感路径是安全底线，保持硬编码正确
2. **.env.example 误伤**：测试时发现 .env.example 含 .env 前缀但它是模板（无密钥）——放行（不在敏感名单，且非目录段）

## 验证结果
- `uv run pytest` → 79 passed ✅（+12 path_security）
- `uv run ruff check .` → All checks passed ✅
- 手工验收：.env 拦截 / README.md 放行 / ../.env 规范化拦截 ✅
- git tag v0.4.3 ✅

## 下一步
- TODO 参数校验（execute_tool schema 校验）——独立 P0
- 或 shell 权限升级 + 审批（依赖参数校验）
- path_security 后续可扩展到 write_file/shell 的路径操作
