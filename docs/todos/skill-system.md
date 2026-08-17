# Skill 系统 TODO（docs/todos/skill-system.md）

> 创建：2026-08-17 · 基线：v0.4.1（工具调用已成型，run_python 沙箱推进中）
> 目标：实现 skill 系统的完整能力——让 agent 能加载"技能说明书"并按需使用
> 参考：Hermes skills/ 目录（SKILL.md + frontmatter + 附属文件）、tools/skills_tool.py、skills_hub.py、skills_guard.py
> 约定：⬜ 未开始 / 🚧 进行中 / ✅ 已完成；P0 核心 / P1 重要 / P2 增强

---

## 先理解：skill 是什么（Hermes 的真实结构）

```
skills/
└── github/
    └── github-pr-workflow/
        ├── SKILL.md          # 技能说明书（frontmatter 元数据 + markdown 正文）
        └── references/       # 附属文件（可选：详细文档/模板/脚本）
            └── api.md
```

**SKILL.md 的 frontmatter（YAML 元数据）：**

```yaml
---
name: github-pr-workflow          # 技能名
description: "GitHub PR lifecycle..."   # 一句话描述（触发匹配用）
version: 1.1.0
tags: [GitHub, PR, CI/CD]         # 标签
related_skills: [github-auth]     # 关联技能
---
```

**skill 的本质：一段"如何做某类任务"的结构化知识**，agent 遇到匹配任务时加载它，把里面的步骤/命令/注意事项作为上下文注入。

---

## 一、Skill 核心机制（用户要求 + 必要补充）

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **Skill 文件格式与解析（SKILL.md + frontmatter）** | P0 | ⭐⭐ | 定义 skill 格式：SKILL.md + YAML frontmatter（name/description/version/tags）。解析器读取元数据 + 正文。参考 Hermes SKILL.md 结构 |
| ⬜ | **Skill 注册表与目录扫描** | P0 | ⭐⭐ | 启动时扫描 skills/ 目录，建立 skill 索引（name→文件路径+元数据）。参考 Hermes skills_hub.py |
| ⬜ | **Skill 动态加载** | P0 | ⭐⭐⭐ | agent 运行中按需加载 skill 内容注入上下文（不是启动时全加载）。核心：匹配→读取→注入 |
| ⬜ | **Skill 触发匹配（description 关键词匹配）** | P0 | ⭐⭐⭐ | 根据用户输入/任务描述，用 description+tags 匹配最相关 skill。简单版：关键词打分；进阶版：LLM 判断 |
| ⬜ | **Skill 脚本执行** | P0 | ⭐⭐⭐ | skill 可附带可执行脚本（scripts/ 目录），agent 加载 skill 后能运行其脚本。⚠️ 需要沙箱安全（复用 run_python 沙箱） |
| ⬜ | **渐进式加载（lazy loading）** | P1 | ⭐⭐⭐ | 只在 skill 被匹配时才加载内容（避免启动加载全部 skill 浪费 token/内存）。skill 索引常驻、内容按需读 |
| ⬜ | **LRU 缓存 skill** | P1 | ⭐⭐⭐ | 常用 skill 缓存内容（LRU 淘汰），减少重复读盘；缓存失效策略（文件 mtime 变化时重新读） |
| ⬜ | **Skill 管理命令（list/show/load）** | P1 | ⭐⭐ | CLI 命令：查看已加载 skill、查看某个 skill 内容、手动加载。参考 Hermes skills_tool.py |

## 二、Skill 进阶能力（补充，从 Hermes 看到的机制）

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **Skill 层级组织（分类目录）** | P1 | ⭐⭐ | skills/<category>/<skill-name>/ 两级目录（如 github/github-pr-workflow/），参考 Hermes 结构 |
| ⬜ | **Skill 附属文件（references/templates/scripts）** | P1 | ⭐⭐⭐ | skill 可带 references/（详细文档）、templates/（模板）、scripts/（脚本）。加载时按需访问附属文件 |
| ⬜ | **Skill 编写与自改进（agent 自己写 skill）** | P1 | ⭐⭐⭐ | agent 完成任务后能把自己的经验沉淀为 skill（自进化能力）——Hermes 的核心特色 |
| ⬜ | **Skill 版本与兼容性检查** | P2 | ⭐⭐ | frontmatter 的 version 字段：加载时检查兼容性（如要求的平台/依赖） |
| ⬜ | **Skill 安全守卫（skills_guard）** | P2 | ⭐⭐⭐ | 参考 Hermes skills_guard.py：防恶意 skill（脚本注入、危险指令），脚本执行走沙箱 |
| ⬜ | **Skill 热更新（运行时 reload）** | P2 | ⭐⭐⭐ | 文件变化时自动重新加载（LRU 缓存失效 + 索引刷新联动） |

## 三、Skill 与现有系统的集成

| 状态 | 条目 | 价值 | 难度 | 说明 |
|------|------|------|------|------|
| ⬜ | **Skill 内容注入 system prompt** | P0 | ⭐⭐⭐ | 匹配到的 skill 正文拼接到 system prompt（参考 Hermes：skill 内容进上下文） |
| ⬜ | **Skill 触发时机设计（何时加载）** | P0 | ⭐⭐⭐ | 决策：①对话开始时按系统提示匹配 ②用户输入后匹配 ③LLM 主动请求加载（工具调用）——需设计触发策略 |
| ⬜ | **Skill 与工具联动** | P2 | ⭐⭐⭐ | skill 描述里可声明"需要哪些工具"，加载时确保工具已注册（如 git skill 需要 shell 工具） |

---

## 完成记录

| 日期 | 条目 | commit/tag | 备注 |
|------|------|-----------|------|
|      |      |           |      |

## 备注（演进思路）

- **渐进式加载是核心体验**：skill 多起来后，启动全加载会拖慢 agent 且烧 token——索引常驻 + 内容按需 = 正确姿势
- **LRU 缓存是性能关键**：常用 skill（如 git 流程）反复加载不该重复读盘
- **脚本执行是危险面**：skill 脚本 = 不可信代码（可能来自社区），必须走 run_python 沙箱（v1→v2 安全升级后更稳）
- **skill 来源优先级**：本地手写（安全）→ 后续可考虑社区下载（需安全审查，对应 skills_guard）
