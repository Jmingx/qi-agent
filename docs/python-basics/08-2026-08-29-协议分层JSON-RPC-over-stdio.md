# 协议分层：JSON-RPC over stdio 是什么意思

日期：2026-08-29
来源：内核/外壳分离方案讨论中的知识问答（用户问"JSON-RPC（应用）over stdio（传输），是什么意思"、"为什么不用 HTTP"）

## 一、一句话

```
JSON-RPC = 消息的【格式规则】（说什么）
stdio = 消息传输的【管道】（怎么传）
"JSON-RPC over stdio" = 用 stdin/stdout 管道传 JSON-RPC 消息
```

## 二、分层思想（核心概念）

```
任何网络/进程通信都分【层】——每层管一件事，可独立替换：

  ┌────────────────────────────────┐
  │ 应用层：JSON-RPC 方法          │  ← 说什么（格式/语义）
  │   {"method":"message/send",...}│
  ├────────────────────────────────┤
  │ 传输层：stdio / socket / WebSocket │  ← 怎么传（管道）
  ├────────────────────────────────┤
  │ 编码层：JSON                    │  ← 怎么表示（序列化）
  └────────────────────────────────┘

关键：分层让"换管道不换格式"——
  同一份 JSON-RPC 消息，stdio 传（本地）/ socket 传（局域网）/
  WebSocket 传（浏览器）——格式完全不变，只换传输
```

## 三、具体到 stdio

```
stdio = Standard Input/Output（标准输入/输出）：
  stdin（程序 A 的输入）= 程序 B 写进来
  stdout（程序 A 的输出）= 程序 B 读走

两个进程用 stdio 连接（管道）：
  进程 A（CLI 外壳）←→ 进程 B（内核服务）
    A 写 JSON-RPC 到 stdout → B 从 stdin 读
    B 写响应到 stdout → A 从 stdin 读
  → 一条管道双向传（newline-delimited JSON：一行一条消息）
```

## 四、为什么不用 HTTP（核心对比）

```
HTTP = 单向请求-响应（客户端发起，服务器被动答，一问一答）
  → agent 交互需要【双向】：内核问用户（审批）→ 用户答 → 再回来
  → HTTP 请求已结束，状态丢了（要轮询/回调，别扭）

JSON-RPC over stdio = 双向长连接（一条管道持续传）
  → 审批是会话内往返（request → respond）
  → 流式输出是通知（无响应，持续推）

类比（Java）：
  HTTP ≈ 打电话（拨号-通话-挂断，每次重新建立）
  JSON-RPC over stdio ≈ 对讲机（一条线持续，双向随时说）
```

## 五、业界实证（分层用 HTTP 的场合）

```
不是"不用 HTTP"——是【分层用】：
  内核 ↔ 外壳（本机）：JSON-RPC over stdio（双向/流式/轻量）
  外壳 ↔ 远端用户（浏览器/公网）：HTTP/WebSocket（浏览器兼容/鉴权）

Codex 实证：
  codex app-server --listen stdio://   ← 本机（默认）
  codex app-server --listen socket://  ← 远程（同协议换传输）
```

## 六、要点速记

```
1. 分层 = 格式（应用层）与管道（传输层）独立，可替换
2. JSON-RPC over stdio = 用 stdin/stdout 传 JSON-RPC（双向长连接）
3. HTTP 单向请求-响应 → 做不了审批双向流；用在内核-外壳之间别扭
4. HTTP 用在外壳↔用户（浏览器/公网）——分层各司其职
5. 换传输不换协议 = 分层设计的价值（本地 stdio → 远程 socket 无缝）
```
