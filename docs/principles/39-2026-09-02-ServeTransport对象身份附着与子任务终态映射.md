# ServeTransport 对象身份附着与子任务终态映射

> 日期：2026-09-02  
> 场景：WebShell / 子任务进度推送 / 终态展示修复

## 这次解决了什么

这次修复表面上是两个 UI 问题，底层其实是两条很关键的链路：

1. `ServeTransport` 给 context 挂事件监听器时，不能把“业务 session_id”当成“对象是否已经挂过监听器”的依据。
2. 子任务轮询时，前端不能只看顶层 `context.status`，而要区分 `result.status` 里真正的业务终态。

这两个点如果混在一起看，就会出现很典型的错觉：

- 后端明明创建了新 context，但事件不再转发
- 子任务已经返回 `need_more_info` 或 `stopped`，卡片却还是显示“已完成”

## 一、为什么不能用 `context.id` 做 attach 去重

`context.id` 是业务身份，不是 Python 对象身份。

在 Web 场景里，`session/create` 和 `session/resume` 可能在很短时间内出现“同一个 session id，但对应了不同的 context 对象”的情况。原因是：

- 会话语义上还是同一个 session
- 但实现层可能已经 new 了一个新的 `AgentContext`
- 新对象必须重新挂监听器，否则新对象产生的事件不会进入 `ServeTransport`

如果我们只写：

```python
if context.id in self._attached_contexts:
    return
```

那么就会把“同 session id 的新对象”误判成“已经 attach 过”，结果就是：

- 新对象的 `agent/tool-call`
- 新对象的 `agent/tool-result`
- 新对象的 `agent/turn-end`

都不会转成 WebSocket 通知。

## 二、为什么 `id(context)` 才是正确的去重键

`id(context)` 是 Python 运行时给对象的身份标识。

它的含义是：

- 这个对象实例有没有被处理过
- 而不是这个 session 的业务 id 有没有出现过

这正好符合 attach 的真实目的：

- 同一个对象重复注册，应该只 attach 一次
- 不同对象即便 session_id 一样，也必须各自 attach

所以这次把 `_attached_contexts` 从 `set[str]` 改成了 `set[int]`，并用 `id(context)` 记录。

这和业界常见做法一致：

- 事件监听器通常按“实例生命周期”管理
- 业务 id 用来寻址数据，不用来判断对象是否已经装配

## 三、子任务终态为什么要看 `result.status`

这个问题容易和 `context.status` 搞混。

### 顶层状态 `context.status`

这是“这个上下文线程是否还在运行”的状态。

常见值大概是：

- `running`
- `completed`
- `failed`
- `stopped`

它表达的是流程有没有结束，不等于业务答案是什么。

### 业务终态 `result.status`

子任务返回时，真正有业务含义的是 `result.status`，例如：

- `completed`
- `failed`
- `need_more_info`
- `stopped`

这里面最容易漏的是：

- `need_more_info`
- `stopped`

它们不是“异常”，而是正常业务终止方式。前端如果只认顶层状态，就会把这些结果误显示成“已完成”。

## 四、这次前端做了什么

这次前端围绕 `useSubtask` 和 `SubTaskCard` 做了三件事：

1. `session/status` 的结果里，只要 `response.result.status` 是终态，就用它作为卡片终态。
2. `need_more_info` 和 `stopped` 单独映射为各自的标签、提示文案和卡片样式。
3. `completed` 的长结果默认全文展开，短结果直接展示，避免用户只看到 300 字预览误以为是摘要。

这其实是在做“业务语义还原”：

- 轮询接口拿到的是原始协议数据
- `useSubtask` 把协议数据翻译成 UI 状态
- `SubTaskCard` 再把状态翻译成用户能读懂的卡片

## 五、为什么这类问题在 Hermes 里也很常见

Hermes / 业界主流 agent 系统一般都会遇到同一类坑：

1. **会话标识和实例标识混淆**
   - 业务 id 看起来稳定
   - 但实际监听对象可能已经换了

2. **协议状态和 UI 状态混淆**
   - API 返回的是协议字段
   - UI 需要的是“用户语义”

3. **终态分类不完整**
   - 只做 completed/failed 两类太粗
   - 真正落地时，`need_more_info` 这种状态必须单独处理

4. **轮询生命周期管理不严**
   - timer 清了，meta 没清
   - 或者 meta 清了，timer 还在跑
   - 最后就会出现“卡片一直转、但实际上已经结束”

## 六、这次的取舍

这次没有引入额外状态库，也没有把轮询改成复杂的事件流订阅系统，原因很直接：

- 当前问题的根因是“attach 键错了”和“终态映射不完整”
- 先修正这两个地方，收益最大，风险最小
- 额外抽象只会把调试面扩大

也就是说，这次是典型的“先修语义，再谈架构”的修法。

## 七、这次改动带来的直接收益

- 新 context 即使 session_id 相同，也会重新挂监听器
- 主会话的工具调用、结果、结束事件能正常转发到 WebSocket
- 子任务 `need_more_info` 和 `stopped` 不再被 UI 误判成已完成
- 卡片状态、标签、说明文字和实际协议状态保持一致

## 八、回头再看，最容易踩的坑

1. 把 `context.id` 当成对象身份。
2. 只看 `context.status`，忽略 `result.status`。
3. 认为终态只有 `completed` 和 `failed`。
4. 轮询里只清 timer，不清元数据，或者反过来。

这四个坑都属于 agent 开发里很常见的“状态语义错位”问题。
真正的解决方式不是多写几个 if，而是先把“哪个字段负责什么语义”定义清楚。
