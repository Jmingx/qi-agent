# Hermes 工具系统参考手册（对照 qi-agent）

> 来源：阅读本地 Hermes 源码（C:\Users\xie\PycharmProjects\hermes-agent\tools\，98 个文件）
> 日期：2026-08-14
> 用途：qi-agent 工具系统打磨与架构升级的参考地图

## 1. Hermes 工具全景（98 个文件，按功能分类）

| 类别 | 工具文件 | 说明 | qi-agent 对应 |
|------|---------|------|--------------|
| **核心执行** | terminal_tool.py | 终端命令执行（完整版：任意命令+审批） | ✅ shell（只读版） |
| | file_tools.py / file_operations.py | 文件读写删改全套 | ✅ read_file |
| | code_execution_tool.py | **沙箱执行 Python 代码** | ⬜ 未实现（高价值） |
| | web_tools.py | 网页抓取/搜索 | ⬜ |
| | browser_tool.py / browser_cdp_tool.py | 浏览器控制（CDP：开网页/点击/截图） | ⬜ |
| **感知** | vision_tools.py | 图片分析 | ⬜ |
| | transcription_tools.py | 语音转文字 | ⬜ |
| | tts_tool.py | 文字转语音 | ⬜ |
| | image_generation_tool.py / video_generation_tool.py | 生成图片/视频 | ⬜ |
| | x_search_tool.py | Twitter/X 搜索 | ⬜ |
| **记忆与状态** | memory_tool.py | **长期记忆（跨会话）** | ⬜ 阶段 3 方向 |
| | session_search_tool.py | 搜索历史会话 | ⬜ |
| | todo_tool.py | 任务清单管理 | ⬜ |
| | checkpoint_manager.py | 进度检查点 | ⬜ |
| | file_state.py | 文件状态追踪 | ⬜ |
| **协作与调度** | delegate_tool.py | **委派子任务给子 agent** | ⬜ |
| | async_delegation.py | 异步委派 | ⬜ |
| | cronjob_tools.py | **定时任务** | ⬜ 阶段 5c 方向 |
| | send_message_tool.py | 跨平台发消息 | ⬜ |
| | kanban_tools.py | 看板管理 | ⬜ |
| **学习与自改进** | skill_manager_tool.py / skills_tool.py | **技能系统** | ⬜ 阶段 5a 方向 |
| | skills_hub.py / skills_guard.py / skills_sync.py | 技能中心/安全/同步 | ⬜ |
| | memory_tool.py | 记忆读写 | ⬜ |
| **平台接入** | discord_tool.py、feishu_doc_tool.py、homeassistant_tool.py、yuanbao_tools.py、mcp_tool.py | 各平台/MCP 协议 | ⬜ |
| **基础设施** | registry.py | 工具注册表（工业版） | ✅ registry.py（简化版） |
| | path_security.py | 路径安全（防读敏感文件） | ⬜（高价值） |
| | approval.py | 命令审批 | ⬜ |
| | tool_output_limits.py | 输出限制 | ✅ 部分（截断） |
| | schema_sanitizer.py | schema 清洗 | ⬜ |

## 2. 借鉴优先级建议（学习价值 × 实现难度）

| 优先级 | 借鉴项 | 对应 Hermes 文件 | 学习点 |
|--------|--------|----------------|--------|
| ⭐⭐⭐ | code_execution（执行 Python 代码） | code_execution_tool.py | 沙箱安全、动态执行 |
| ⭐⭐⭐ | 参数校验 + schema 清洗 | schema_sanitizer.py | 校验器设计 |
| ⭐⭐⭐ | memory_tool | memory_tool.py | 阶段 3 本来要做 |
| ⭐⭐ | tool_output_limits（输出限制） | tool_output_limits.py | 截断完善 |
| ⭐⭐ | path_security（路径安全） | path_security.py | 防读 .env 等敏感文件 |
| ⭐⭐ | todo_tool | todo_tool.py | 简单实用 |
| ⭐ | cronjob / delegate | 各文件 | 阶段 5 再考虑 |

## 3. 注册机制对比（qi-agent vs Hermes）

| 维度 | qi-agent（装饰器） | Hermes（显式注册） |
|------|------------------|-------------------|
| 工具载体 | 函数 + @tool 装饰器 | **文件** + registry.register() |
| 注册信息量 | 1 个（description） | 12 个字段（name/toolset/schema/handler/check_fn/requires_env/is_async/emoji/max_result_size/...） |
| schema 来源 | 签名自动生成 | 手写 dict（精细控制） |
| 组织粒度 | 所有工具混在 1-2 个文件 | **1 个工具 = 1 个文件** |
| 附加能力 | 无 | 环境检查、异步、输出限制、插件覆盖保护 |
| 可扩展性 | 简单直接（小项目友好） | 模块化 + 可插拔（大系统必需） |

## 4. 业界工具组织模式

| 模式 | 代表 | 特点 |
|------|------|------|
| 装饰器模式 | LangChain @tool、CrewAI | 快速、小项目/原型友好 |
| 文件 + 显式注册 | Hermes、OpenAI Plugins | 工程化、可扩展、大型系统 |
| MCP 协议 | Claude、Cursor 生态 | 工具服务化、跨进程/跨语言、动态发现 |

## 5. 关键洞察

1. **handler 本质都是普通函数**——"工具=函数"没错，区别在注册机制和文件组织
2. **工程化 = 文件粒度 + 显式注册**：一个工具一个文件（可含私有状态/辅助类/环境检查），注册表集中管理
3. **schema 手写 vs 自动生成**：小项目自动生成够用；大系统手写 schema 获得精细控制（描述、枚举、默认值、嵌套类型）
4. **qi-agent 演进路线**：装饰器（现在）→ 文件粒度 + register()（对齐 Hermes）→ 可选 MCP（阶段 5e）
