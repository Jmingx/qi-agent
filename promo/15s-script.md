# qi-agent 15 秒视频稿件

日期：2026-09-08。文件：qi-agent-event-driven-15s-v2.mp4。

0–4 秒：核心推进任务，关键节点发出事件，插件在事件点响应。

4–10 秒：emit 按序通知；waterfall 逐层改写；bail 首个非 None 返回即停止。

10–15 秒：Agent A send → 中央队列 → Dispatcher → Agent B inbox → drain。

交付：1080×1920，30fps，450 帧，15 秒，H.264，无音轨。中文文字直接渲染进画面。

准确性：EventBus 的同步分发不代表异步并发；Mailbox 图只展示中央队列路径，源码另有 send_direct。
