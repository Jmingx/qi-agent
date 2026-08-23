# 04-异常处理与BaseException层级（Ctrl+C中断处理）

> 归档来源：qi-agent 开发会话 bug 修复问答（2026-08-14）
> 起因：用户运行 CLI 时等待 API 响应按 Ctrl+C，程序打印完整 Traceback 崩溃

## 1. 问题现象

```
File "...ssl.py", line 1140, in read
    return self._sslobj.read(len)
KeyboardInterrupt
```

用户按 Ctrl+C 中断等待中的 API 请求 → 程序打印完整 Traceback 崩溃。
**根因：`except Exception` 捕获不到 `KeyboardInterrupt`。**

## 2. Python 异常层级（关键知识点）

```
BaseException                          ← 祖宗
├── Exception                          ← 常规错误（文件/网络/类型...）
│   ├── OSError / ValueError / TypeError / ...
└── KeyboardInterrupt                  ← Ctrl+C 中断 ⚠️
└── SystemExit                         ← 程序退出
```

**`except Exception` 只接 Exception 及其子类；KeyboardInterrupt 直接继承 BaseException，所以漏网。**

## 3. 为什么 Python 这样设计（刻意为之）

- `Exception` = "程序可以处理并继续的错误"（文件不存在、网络失败、类型错误）
- `KeyboardInterrupt` = "用户强烈要求停止"的信号
- 默认**不该**被普通 except 吞掉——否则用户按 Ctrl+C 程序无响应
- 所以它被放在 BaseException 层级，普通 `except Exception` 接不住

## 4. 修复方式

```python
try:
    reply = agent.chat(user_input)
    print(f"agent> {reply}")
except KeyboardInterrupt:        # 必须显式捕获（Ctrl+C 优雅退出）
    print("\n[已中断] 再见！")
    break
except Exception as exc:         # 常规错误继续对话
    print(f"[错误] 调用失败: {exc}")
```

**规则：如果程序需要响应 Ctrl+C（优雅退出/清理资源），必须显式写 `except KeyboardInterrupt`；如果只是想捕获"可恢复的错误"，用 `except Exception` 即可。**

## 5. 何时需要捕获 KeyboardInterrupt

| 场景 | 建议 |
|------|------|
| CLI 交互程序 | ✅ 必须捕获（用户按 Ctrl+C 应优雅退出） |
| 长任务/等待网络 | ✅ 必须捕获（清理资源、保存进度） |
| 库函数 | ❌ 一般不捕获（让上层决定） |
| 后台服务 | 视情况（常配合 signal 处理） |

## 6. 调试技巧

- **看 Traceback 最后一行**：异常类型 + 消息（本案例是 KeyboardInterrupt，不是 OSError）
- **别被中间帧迷惑**：SSL 读取只是"中断发生的地点"，不是"错误的原因"
- **区分"错误"和"中断"**：文件/网络错误 → Exception；用户主动中断 → KeyboardInterrupt
