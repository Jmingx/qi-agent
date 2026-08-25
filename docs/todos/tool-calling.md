# 工具调用模块 TODO（深入、透彻）

> 创建：2026-08-17 · 基线：v0.4.0（文件粒度 + register + 初始化日志）
> 目标：把工具调用这一块做深做透，覆盖沙箱/安全/异步/可靠性/可观测性
> 约定：⬜ 未开始 / 🚧 进行中 / ✅ 已完成；P0 核心 / P1 重要 / P2 增强

---

## 一、沙箱能力（按强度逐条，每条独立）

沙箱 = 限制代码"能碰什么、能用多少、能留什么"。从软到硬逐级实现。

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ✅ | **软沙箱：run_python 工具（v1）** | P0 | ⭐⭐ | `run_python(code)`：子进程执行 + 静态白名单（禁止 import os/sys/subprocess）+ 10s 超时 + 干净环境。参考 principles 沙箱三件套。**完成：v0.4.2** |
| ✅ | **软沙箱升级（v2）：RestrictedPython** | P1 | ⭐⭐⭐ | 用 Python 官方受限执行库（解释器层拦截 import/属性访问，防 `().__class__...` 逃逸），替代手写白名单。纯 Python 无重依赖——**轻量但认真**的沙箱。**完成：v0.4.13**（受限执行器+模式开关+内建集/模块白名单配置化） |
| ✅ | **软沙箱升级（v3）：资源限制** | P1 | ⭐⭐⭐ | 内存限制（psutil 轮询，双阈值 192/256MB + 进程树 RSS）、输出字节上限（已有）、错误隔离（已有）。Popen 轮询 + drain 线程防死锁。**完成：v0.4.17** |
| ✅ | **进程沙箱：干净环境变量（v1.1 升级）** | P0 | ⭐⭐ | 双重过滤：①密钥名子串拦截（KEY/TOKEN/SECRET/PASSWORD/AUTH 等，无论前缀都丢）②安全名单保留。对齐 Hermes _scrub_child_env（参考 Hermes code_execution _scrub_child_env，三道规则）。**完成：v0.4.12** |
| ✅ | **进程沙箱：工作目录隔离** | P1 | ⭐⭐ | 临时目录（tempfile.mkdtemp）执行——cwd=临时目录，脚本碰不到项目文件。restricted 纵深 + legacy 真实防线（拼接绕过写文件只能写临时目录）。**完成：v0.4.16** |
| ⬜ | **Windows 原生隔离（Job Objects）** | P2 | ⭐⭐⭐ | Windows Job Objects / AppContainer 限制子进程内存/CPU/句柄——轻量的系统级资源限制，无需 Docker |
| ⬜ | **容器沙箱：Docker 后端（远期可选）** | P2 | ⭐⭐⭐⭐ | **注意：依赖 Docker 较重**（WSL2 虚拟机、秒级启动、镜像管理）。做成"检测到 Docker 才启用，无则自动降级进程沙箱"。仅当未来需要跑**不可信代码**（RL 评测/第三方脚本/多租户）时才值得——单用户本地 agent 软沙箱已覆盖 95% 需求（参考 Hermes environments/docker.py） |
| ⬜ | **远程沙箱：远程执行后端（远期）** | P2 | ⭐⭐⭐⭐⭐ | SSH/Modal 等远程执行（参考 Hermes environments/ssh.py、modal.py）——先留接口，不急于实现 |

## 二、安全环境

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ✅ | **path_security：路径安全** | P0 | ⭐⭐ | read_file 等工具禁止读取敏感路径：.env、.git/、__pycache__、node_modules、密钥文件（参考 Hermes tools/path_security.py）。**完成：v0.4.3** |
| ✅ | **shell 权限模型升级 + 审批机制** | P0 | ⭐⭐⭐ | 三档决策：①自动放行（只读白名单）②需审批（危险命令弹窗 y/n/a=总是允许，approval_gate 插件+agent/tool-approval 事件点，fail-closed 无监听器拒绝）③硬拒绝（红线不进审批）。approved 内部参数防绕过（schema 不可见+调用级 internal）。**完成：v0.4.18** |
| ✅ | **并行工具调用** | P1 | ⭐⭐⭐ | 模型一次返回多个 tool_calls → agent 并行执行（省 N-1 次 LLM 往返，秒级×N 数量级收益）。**完成 v0.4.25（2026-08-22）**：三阶段（主线程判档/审批 → 线程池 max_workers=10 并行执行 → 主线程按序 emit/回填）；DeepSeek 并行返回实测支持（偶发不稳定）；tool-result 主线程 emit 因监听器状态非线程安全；shell/run_python 进程复用经性能账分析放弃（启动开销 ms 级 vs LLM 秒级） |
| ❌ | **命令执行并发控制（全局信号量）** | P1 | ⭐⭐ | **已丢弃（2026-08-22 用户决策）**：并行工具调用（v0.4.25）的 max_workers=10 已是天然并发边界；同步串行架构下并发场景不存在（单 agent 一次 1 个工具、评测 Semaphore(3) 限 3 agent、background 不计入）——全局信号量无触发场景，方案文档已删。若未来评测大幅加并发或出现真实进程风暴再评估 |
| ✅ | **LLM 调用超时** | P1 | ⭐⭐⭐ | LLMClient 加请求超时（openai SDK timeout，如 60s）——根治评测超时后线程残留（wait_for 无法终止线程，asyncio.run join 卡 300s 的 RuntimeWarning）。联动"命令执行超时与并发控制" TODO。**完成 v0.4.24**：timeout=60 默认（客户端 + chat/chat_stream 显式传递）；评测/CLI 异常兜底已存在（验证固化）；挂起调用最多 60s 返回，线程不残留 |
| ✅ | **沙箱降级需用户审核（run_python 补审批档）** | P0 | ⭐⭐⭐ | 软沙箱 legacy 降级（QI_SANDBOX_MODE）当前是"环境变量显式开关"——**过渡方案**。审批机制已就绪（v0.4.18 agent/tool-approval + approval_gate）：降级操作改走审批（弹窗"确认降级沙箱安全等级？"），环境变量开关退役。即 run_python 补审批档（与 shell 三档对齐）。✅ 完成（v0.4.23，2026-08-21）：approved 内部参数（模型不可见，防绕过）+ security_guard import 白名单外判据 → NEED_APPROVAL 弹窗 + QI_SANDBOX_MODE 退役 + a=总是允许对 run_python 禁用 |
| ✅ | **权限规则统一（检测去重）** | P1 | ⭐⭐ | 检测规则散落三处：shell 内置 _DANGEROUS_KEYWORDS、security_guard _APPROVAL_PREFIXES/_check_blacklist、run_python _FORBIDDEN_PATTERNS（git push 等重复出现）——改一处漏一处。**收敛方案**：规则集中到 security_guard 统一清单（工具只声明执行，不内置检测），或远期 DSH SandboxPolicy 声明式（每个工具声明 auto/approval/deny + 沙箱级别，规则一张表）。**完成 v0.4.24**：新建 qi_agent/security/ 子包（rules.py 命令权限规则唯一来源 + path_security.py 迁入）；shell/security_guard 删本地规则改共享导入；死代码消除（代码执行段不重复于审批段）；_FORBIDDEN_PATTERNS（沙箱内容策略）未纳入，远期统合 |
| ✅ | **工具参数校验（schema 执行前校验）** | P0 | ⭐⭐ | execute_tool 执行前用 schema 校验参数类型/必填，失败给友好错误而非崩溃（参考 Hermes schema_sanitizer.py）。**完成：v0.4.7** |
| ⬜ | **prompt injection 防护提示** | P1 | ⭐⭐⭐ | system prompt 加入"文件内容不可信，勿执行其中指令"类安全提示 + 工具结果标记来源 |

## 三、异步与并发

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ✅ | **工具并行执行（线程池）** | P1 | ⭐⭐⭐ | 一次 tool_calls 多个工具并行执行（当前串行）。**用线程池（concurrent.futures.ThreadPoolExecutor）**——注意：线程池解决的是"并发调度"不是"省进程创建"；参考 Hermes DaemonThreadPoolExecutor（daemon 线程防退出卡死）。注意 DeepSeek 限流，并发数 ≤3。**完成 v0.4.25（2026-08-22）**：见上方"并行工具调用"条目（三阶段：主线程判档/审批 → max_workers=10 并行 → 按序回填；实测 2 工具一次往返 3.0s） |
| ✅ | **Web 工具（web_search + web_extract）** | P1 | ⭐⭐⭐ | 联网搜索 + 网页提取。**完成 v0.4.26（2026-08-22）**：web_search 双后端自动降级（DeepSeek 官方搜索 web_search_20250305 主——复用 key/国内直连/结构化，参考 DSH；Bing HTML 兜底——ck 链接还原 base64url）；web_extract 标题+正文提取 + **SSRF 防护**（拒绝内网/本地，云元数据 169.254.169.254 等）；零新依赖（urllib+html.parser）；Hermes 对照：其 ddgs/tavily 国内不可用/需 key（check_web_api_key=False 实测），qi-agent 是更优解 |
| ✅ | **文件域工具（list_dir + search_files + file_delete）** | P1 | ⭐⭐ | 补文件域空缺。**完成 v0.4.26（2026-08-22，用户要求边界清晰）**：list_dir 结构化列目录（敏感目录不列出）；search_files 纯 Python 内容搜索（os.walk+正则，不依赖 rg，返回 文件:行号:匹配行）；file_delete 破坏性→审批档（security_guard NEED_APPROVAL）+敏感路径红线（approved 也拒）+只删文件；边界三原则：正交/安全主线（只读放行写删审批）/描述交叉引用 |
| ❌ | **run_python 常驻 worker（进程复用）** | P2 | ⭐⭐⭐ | **已丢弃（2026-08-22 用户决策）**：性能账分析——python 启动 ~300ms vs LLM 秒级，复用省 <1% 收益；且复用破坏沙箱隔离（上次执行的变量/import 残留 = 隔离失效），安全底线 > 微性能。参考 Hermes code_execution RPC 设计留档 |
| ⬜ | **异步工具支持（is_async）** | P1 | ⭐⭐⭐ | register() 增加 is_async 字段（对齐 Hermes），execute_tool 区分同步/异步 handler（asyncio.run 包装） |
| ⬜ | **工具调用去重/幂等** | P2 | ⭐⭐ | 相同参数重复调用时（如 get_time 被调两次）可缓存或提示，避免浪费 API 轮次 |

## 四、可靠性

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **工具错误重试与降级** | P1 | ⭐⭐ | 工具失败时错误信息更友好（含"可尝试的替代方案"），让模型自行决定重试/换工具；可加失败统计 |
| ⬜ | **工具边界与选择优化（description 语义）** | P1 | ⭐⭐ | 工具能力重叠时模型可能选错（如 shell vs run_python 都能执行代码）。优化：①description 写清"什么时候用我/什么时候别用我"（交叉引用边界）②工具正交性设计（能力不重叠）③按场景只加载相关 toolset（减少候选=减少选错）。验证：评测任务集加"工具选择正确性"断言 |
| ⬜ | **prompt-based 降级通道（双通道）** | P0 | ⭐⭐⭐ | 模型不支持原生 tool_calls 时走提示词式工具调用（system prompt 描述工具 + JSON 解析）。之前已讲原理（principles/02 第 7 节），落地为 llm.py 的自适应分支 |
| ⬜ | **复杂参数类型支持** | P1 | ⭐⭐⭐ | schema 自动生成支持 list/dict/嵌套对象/枚举（enum），当前只支持 str/int/float/bool |

## 五、可观测性（结合 --debug）

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ✅ | **工具调用统计** | P1 | ⭐⭐ | 记录每个工具调用次数/耗时/成功失败率，会话结束汇总打印（debugger.py 扩展）。**完成：v0.4.8**（ToolStatsPlugin 监听 agent/* 事件，--stats 启用） |
| ⬜ | **工具调用成本追踪** | P2 | ⭐⭐ | 估算每次工具调用轮次的 token/成本（从 API usage 字段） |

## 六、更多工具（利用新架构，1 文件 1 工具）

> 调研基线（2026-08-22）：对照 Hermes（70+ 工具）与 DSH（能力包制）全量
> 清单，按"实用性 × 学习价值 × qi-agent 阶段"筛选。第一梯队（todo /
> clarify / patch）是纯逻辑工具无外部依赖，建议优先；第二梯队（子代理/
> 定时/会话搜索/看板）复杂度高，后续。

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ✅ | **list_dir 工具** | P1 | ⭐ | 列出目录内容（读路径信息，配合 read_file）。**完成 v0.4.26**：结构化返回（名称/类型/大小），敏感目录不列出 |
| ✅ | **write_file 工具（读写能力）** | P0 | ⭐⭐⭐ | 四档路径判定：敏感拒（红线不可审批）/项目内新增自动/覆盖审批/越界审批。复用 is_sensitive_path + approval_gate + approved 内部参数；工具层兜底 fail-closed。**完成：v0.4.19** |
| ✅ | **read_file 分页升级** | P1 | ⭐⭐ | 大文件读取：offset/limit 行级分页（对齐 Hermes 75 行/offset 分段读）+ header 元信息（"第 X-Y 行（共 N 行）"）+ tail 续读提示（可行动信息设计）+ 50K 字符双保险。**完成：v0.4.20**（方案 docs/plans/2026-08-20-read_file分页升级方案.md） |
| ⬜ | **calc 计算器工具** | P2 | ⭐ | 安全表达式求值（ast 解析，不用 eval——教学点：eval 危险 vs ast 安全） |
| ✅ | **todo 任务清单** | P1 | ⭐⭐ | 任务清单管理（参考 Hermes todo_tool / DSH tool-todo 的 todo_write）。agent 长任务自我追踪——"计划 5 件做完 2 件"，不再靠对话记忆；状态管理练手（CRUD + 完成判定 + 持久化）。**完成 v0.4.26**：单工具 action 分发（create/list/update/complete/delete）+ TodoStore 内存会话级 + 状态机（pending→in_progress→completed 终态不可回退）+ id 不重用 + 纯内存放行不审批 + **线程安全（并行 create 加锁）**。**⚠ 遗留**：长任务自动跟踪未实现——模型不会主动 list，靠对话记忆脑补（真实暴露：建 2 条任务脑补 9 步清单）。修复 = 状态注入上下文（对齐 Hermes format_for_injection），已记 context-management.md"todo 状态注入上下文"条目，随上下文管理整块实施 |
| ✅ | **clarify 澄清提问** | P1 | ⭐⭐ | agent 主动问用户（参考 Hermes clarify_tool / DSH interaction ask-user）——"你要删的是 A 还是 B？"。补全交互能力：qi-agent 已有审批弹窗（安全交互），缺信息澄清（信息交互）。**完成 v0.4.26**：交互抽象层 InteractionProvider（工具与交互分离——CLI 注入 TerminalInteraction，未来 Web/GUI 换实现工具零改动）+ 薄分发器 + fail-safe（未注册/非 tty 返回 [交互不可用] 不挂死）+ 选项 ≤4。**⚠ 遗留**：真实对话交互未验证——单测覆盖 provider 委托/选项/超时/fail-safe，但 CLI 注入 + 真实提问流程未做端到端手工验收（须在 cli.py 注入 TerminalInteraction 后跑一次真实对话确认） |
| ✅ | **patch 精确编辑** | P1 | ⭐⭐⭐ | 小改动精确替换（参考 Hermes patch_parser / DSH str_replace_editor）——write_file 整文件覆盖 vs patch 单行替换（省 token + 不覆盖别处）。**完成 v0.4.26**：正则策略链 3 个保守策略（exact/whitespace_flexible/indentation_flexible，Hermes 9 策略保守子集）+ 原子性（匹配失败不写文件）+ diff 展示 + 编辑审批档（声明式 approval）+ 敏感路径红线 |
| ⬜ | **subagent 子代理（delegate_task → agent pool 形态）** | P2 | ⭐⭐⭐⭐ | 子代理并行跑子任务（参考 Hermes delegate_tool / DSH subagent 包：codex/claude-code/dsh-sdk providers）——并行工具调用的下一级（并行 agent）。**2026-08-23 用户架构想法（先记 TODO 缓一缓，方案未写）**：主进程 = agent 集群管理系统（只管理基础模块：记忆/上下文等），agent 下沉为 tool（agent-as-tool），形态 = agent pool（常驻 worker 随时派活），**核心上下文依然在主进程**（context-centric 分解，对齐 Anthropic 2026-01 共识——worker 只拿"一页纸任务卡"：目标+输出格式+工具清单+完成边界，结束只回摘要）。**演进路径**：① 一次性 subagent 工具（无状态，验证 打包→执行→摘要回填→核心上下文更新 链路）→ ② pool 化 + 记忆/上下文管理器服务化（设计里最有价值部分，不依赖 pool 可先行）→ ③ 跨进程协议化（A2A/ANP）。**评审决策点（未拍板）**：worker 同进程线程 vs 子进程（Python 线程不可杀、worker 崩溃带崩主进程 vs IPC 序列化复杂度）；派活上下文打包量（隔离 vs 够用）；审批链跨 worker（worker 工具集受限为主 + NEED_APPROVAL 回传主进程为辅）；并发上限与成本账（multi-agent 15x token、DeepSeek 限流——单用户 CLI 场景 pool 化疑似伪需求）。注意：嵌套限制、结果汇总、隔离上下文 |
| ⬜ | **AgentManager 通用化 + CLI 控制面（v2）** | P2 | ⭐⭐⭐ | **2026-08-24 用户架构想法（V2 先记 TODO 缓一缓）**：控制面统一——"CLI 控制主 agent = 主 agent 控制 subagent"（同一套 AgentContext.steer/stop/poll 协议）。现状：SubagentManager 已操作统一 Context（AgentContext 合并方案 D5）；CLI 目前只有对话入口（无 steer/stop 主 agent 能力）。**V2 内容**：① SubagentManager → AgentManager（register 主 agent context，通用控制台）② CLI /stop /steer → manager（用户 = 主 agent 的"父"）③ 可插拔 agent（换执行者/存储，context 不变——数据载体与执行者解耦的自然结果）。**场景账（缓一缓的理由）**：用户打断 CLI 对话用 Ctrl+C 足够，steer/stop 完整价值在 subagent 后台模式（background=true，v2）才体现——机制就绪，调用者按需接。关联：AgentContext 统一合并方案（已实施） |
| ⬜ | **cronjob 定时任务** | P2 | ⭐⭐⭐ | 定时后台执行（参考 Hermes cronjob_tools）——"每天 9 点检查 X"。需要后台调度器 + 持久化任务表 |
| ⬜ | **session_search 会话搜索** | P2 | ⭐⭐⭐ | 历史会话检索（参考 Hermes session_search_tool，FTS5）——前置：会话持久化存储 |
| ⬜ | **kanban 看板** | P2 | ⭐⭐⭐ | 任务可视化（列/卡片，参考 Hermes kanban_tools×12：create/block/unblock/comment/heartbeat 等）——todo 的升级版，先做 todo |
| ❌ | **浏览器自动化全家 / 媒体生成 / 平台生态** | — | — | **调研排除（2026-08-22）**：浏览器全家（CDP 驱动重，qi-agent 阶段过早）；媒体生成（image/video/tts 需外部 API + RX6500 4GB 跑不动）；平台生态（discord/feishu/yuanbao/homeassistant 平台绑定）；read_image（deepseek-v4-flash 无 vision，做了模型也看不懂） |

---

## 完成记录

| 日期 | 条目 | commit/tag | 备注 |
|------|------|-----------|------|
| 2026-08-17 | path_security 路径安全 | v0.4.3 | 三段检查+规范化防绕过，12 测试 |
| 2026-08-17 | run_python 软沙箱 v1 | v0.4.2 | 四锁设计（白名单/子进程/超时/干净环境），11 测试 |
| 2026-08-18 | 工具参数校验 | v0.4.7 | 三检查+bool严格化+[参数错误]前缀，11 测试 |
| 2026-08-18 | Agent 循环事件化 | v0.4.8 | 事件总线+7事件点+统计插件，19 测试 |
