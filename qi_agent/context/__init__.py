"""上下文管理子包：估算 / 裁剪 / 压缩 / 构成分解 / 注入。

方案 docs/plans/2026-08-22-上下文管理方案.md（四阶段）：
- 阶段 A：estimator（token 估算）+ breakdown（构成分解）
- 阶段 B：window（滑动窗口裁剪）+ sticky（关键信息保留）
- 阶段 C：compressor（触发 + 摘要压缩）
- 阶段 D：inject（todo/sticky 统一注入层）
"""
